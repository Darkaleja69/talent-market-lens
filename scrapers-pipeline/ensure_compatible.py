#!/usr/bin/env python3
"""ensure_compatible.py <directorio> [opciones]

Valida los parquet de un directorio ANTES de subirlos a ADLS y, si hace
falta, los reescribe para que Spark/Databricks pueda leerlos:

  - Lee cada fichero con PyArrow (detecta parquet corrupto/incompleto).
  - Convierte columnas timestamp[ns] -> timestamp[us] (TIMESTAMP_MICROS,
    el unico que Spark acepta en parquet). Sin esto Databricks falla con
    FAILED_READ_FILE / PARQUET_TYPE_ILLEGAL.
  - Comprueba que esten presentes las columnas obligatorias (--required-cols).
  - Comprueba un minimo de filas (--min-rows, default 1).
  - (--coherence <site>) comprueba la coherencia de cada fila (job_id no
    vacio, company_name sin ratings/URLs, URLs rotas, fechas no validas)
    usando las reglas de coherence.py. Los ficheros con filas incoherentes
    se marcan como invalidos y se mueven a cuarentena: NUNCA se suben.

Opciones:
  --required-cols a,b,c   columnas obligatorias (separadas por coma)
  --min-rows N            minimo de filas por fichero (default: 1)
  --coherence site        validar coherencia de filas (indeed|linkedin|
                          infojobs|multi_site)
  --manifest path.json    escribe un manifest JSON con estado por fichero
  --quarantine-dir dir    mueve los ficheros INVALIDOS a esta carpeta (asi
                          no se suben a ADLS)

Exit code: 0 si todos los ficheros son validos, 1 si alguno es invalido.
"""
import argparse
import glob
import hashlib
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq

from coherence import coherence_issues


def read_and_coerce_micros(path):
    """Lee el fichero y devuelve (table, converted).

    Si hay columnas timestamp[ns] las reescribe en el fichero a [us].
    Lanza excepcion si el parquet no se puede leer.
    """
    t = pq.read_table(path)
    schema = t.schema
    nanos = [
        c
        for c in schema.names
        if pa.types.is_timestamp(schema.field(c).type)
        and schema.field(c).type.unit == "ns"
    ]
    if not nanos:
        return t, False
    arrays = []
    for c in schema.names:
        col = t.column(c)
        if c in nanos:
            tz = schema.field(c).type.tz
            # safe=False: trunca a micros. Los nanosegundos sobrantes son
            # irrelevantes para fechas de ofertas y sin esto el cast falla
            # con ArrowInvalid si el valor no esta alineado a micros.
            arrays.append(col.cast(pa.timestamp("us", tz=tz), safe=False))
        else:
            arrays.append(col)
    new_t = pa.Table.from_arrays(arrays, names=schema.names)
    pq.write_table(new_t, path, compression="snappy")
    return new_t, True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(
        description="Valida y reescribe parquet para compatibilidad con Databricks."
    )
    ap.add_argument("directorio")
    ap.add_argument("--required-cols", default="",
                    help="columnas obligatorias, separadas por coma")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="minimo de filas por fichero (default: 1)")
    ap.add_argument("--coherence", default="",
                    help="validar coherencia de filas: indeed|linkedin|"
                         "infojobs|multi_site")
    ap.add_argument("--manifest", default=None,
                    help="path del manifest JSON a escribir")
    ap.add_argument("--quarantine-dir", default=None,
                    help="mover ficheros invalidos a esta carpeta")
    args = ap.parse_args()

    if not os.path.isdir(args.directorio):
        print("uso: ensure_compatible.py <directorio> [--required-cols ...]",
              file=sys.stderr)
        return 1

    required = [c.strip() for c in args.required_cols.split(",") if c.strip()]
    coherence_site = args.coherence.strip() or None
    files = sorted(glob.glob(os.path.join(args.directorio, "*.parquet")))
    if not files:
        print("sin parquets en", args.directorio)
        if args.manifest:
            _write_manifest(args.manifest, [], 0)
        return 0

    entries = []
    errors = 0
    for f in files:
        entry = {
            "file": os.path.basename(f),
            "status": "ok",
            "rows": 0,
            "bytes": 0,
            "sha256": "",
            "converted": False,
            "error": None,
        }
        try:
            t, converted = read_and_coerce_micros(f)
            missing = [c for c in required if c not in t.schema.names]
            if missing:
                raise ValueError(
                    "faltan columnas obligatorias: " + ", ".join(missing)
                )
            if t.num_rows < args.min_rows:
                raise ValueError(
                    "filas %d < minimo %d" % (t.num_rows, args.min_rows)
                )
            if coherence_site:
                bad_rows = 0
                for row in zip(*(t.column(c).to_pylist()
                                 for c in t.schema.names)):
                    rec = dict(zip(t.schema.names, row))
                    if coherence_issues(rec, coherence_site):
                        bad_rows += 1
                if bad_rows:
                    raise ValueError(
                        "%d fila(s) incoherente(s) (coherence=%s)"
                        % (bad_rows, coherence_site)
                    )
            entry["rows"] = t.num_rows
            entry["converted"] = converted
            entry["bytes"] = os.path.getsize(f)
            entry["sha256"] = sha256_file(f)
            note = " [ns->us convertido]" if converted else ""
            print("%s: OK (%d filas)%s" % (os.path.basename(f), t.num_rows, note))
        except Exception as e:
            errors += 1
            entry["status"] = "bad"
            entry["error"] = repr(e)
            print("%s: INVALIDO %r" % (os.path.basename(f), e), file=sys.stderr)
            if args.quarantine_dir:
                try:
                    os.makedirs(args.quarantine_dir, exist_ok=True)
                    dest = os.path.join(args.quarantine_dir, os.path.basename(f))
                    os.replace(f, dest)
                    entry["moved_to"] = dest
                    print("  -> movido a cuarentena: %s" % dest, file=sys.stderr)
                except OSError as me:
                    print("  -> no se pudo mover a cuarentena: %r" % me,
                          file=sys.stderr)
        entries.append(entry)

    if args.manifest:
        _write_manifest(args.manifest, entries, errors)
    return 1 if errors else 0


def _write_manifest(path, entries, errors):
    summary = {
        "schema_version": 1,
        "total_files": len(entries),
        "bad_files": errors,
        "files": entries,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())