#!/usr/bin/env python3
"""filter_new_offers.py <parquet> <key_col> <state_file> <out_parquet> [--new-keys-file PATH]

Filtra un parquet SNAPSHOT dejando SOLO las ofertas nuevas: elimina las filas
cuya clave (key_col) ya esta registrada en state_file (estado de claves ya
subidas a ADLS) y escribe el resultado en out_parquet.

- state_file: JSON con {"keys": [...]}. Si no existe se trata como vacio
  (primera ejecucion: se sube el snapshot completo y se registra el estado).
- --new-keys-file PATH: escribe un JSON con las claves nuevas del snapshot
  para que el pipeline las anada al estado SOLO tras el exito de azcopy.

Exit codes: 0 OK; 2 si falta la columna clave o el parquet no se puede leer.
"""
import argparse
import json
import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq


def load_keys(path: str) -> set:
    if not path or not os.path.exists(path):
        return set()
    # utf-8-sig: tolera el BOM que anade PowerShell 5.1 al escribir el estado
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    keys = data.get("keys") or []
    return {str(k).strip() for k in keys if str(k).strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("key_col")
    ap.add_argument("state_file")
    ap.add_argument("out_parquet")
    ap.add_argument("--new-keys-file", default=None)
    args = ap.parse_args()

    try:
        table = pq.read_table(args.parquet)
    except Exception as e:  # noqa: BLE001
        print(f"filter_new_offers: ERROR leyendo {args.parquet}: {e!r}",
              file=sys.stderr)
        return 2

    if args.key_col not in table.schema.names:
        print(f"filter_new_offers: ERROR: la columna clave '{args.key_col}' "
              f"no existe en {args.parquet}", file=sys.stderr)
        return 2

    known = load_keys(args.state_file)
    keys = table.column(args.key_col).to_pylist()
    mask = []
    new_keys = []
    for k in keys:
        s = str(k).strip() if k is not None else ""
        if s and s not in known:
            mask.append(True)
            new_keys.append(s)
        else:
            mask.append(False)

    new_table = table.filter(pa.array(mask))
    pq.write_table(new_table, args.out_parquet, compression="snappy")

    print(f"filter_new_offers: {len(keys)} filas | {new_table.num_rows} "
          f"nuevas | {len(keys) - new_table.num_rows} ya subidas")

    if args.new_keys_file:
        with open(args.new_keys_file, "w", encoding="utf-8") as fh:
            json.dump({"total": new_table.num_rows, "keys": new_keys}, fh,
                      ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())