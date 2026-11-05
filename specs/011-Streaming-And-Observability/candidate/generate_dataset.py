"""Generate the Free Edition demo extract (CSV + schema + manifest).

The demo compares two ingestion mechanisms on the same data without touching the
paid workspace. This script reads the latest Parquet of each landing source from
``--input-dir/<source>/``, samples a bounded number of rows, truncates long
texts, injects two invalid rows per source and writes:

* ``data/<source>.csv``: UTF-8, header, empty values for NULL, deterministic.
* ``schema.json``: Spark type of every column, derived from the Arrow schema.
* ``manifest.json``: counts, hashes and the sampling/truncation strategy. It
  never contains row values.

Only the standard library and ``pyarrow`` are used. The CSV files are
regenerable and therefore not versioned.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

#: Bronze keys and capture column per source. Mirrors
#: ``databricks_notebooks/Upsert_Job_Offers.py`` (SOURCE_SPECS); the kit is
#: standalone and must not import PySpark to build the dataset.
SOURCE_SPECS: Mapping[str, Mapping[str, Any]] = {
    "indeed": {"keys": ("job_key",), "capture_column": "scraped_at"},
    "infojobs": {"keys": ("id_oferta",), "capture_column": "fecha_scraped"},
    "linkedin": {"keys": ("job_id",), "capture_column": "scraped_at"},
    "multi_site": {"keys": ("site", "job_id"), "capture_column": "scraped_at"},
}

SOURCE_ORDER: Tuple[str, ...] = ("indeed", "infojobs", "linkedin", "multi_site")

DESCRIPTION_MARKERS = ("description", "descripcion")
DESCRIPTION_CHARS = 600
TEXT_CHARS = 300
MAX_TOTAL_BYTES = 1_000_000

MISSING_KEY = "missing_key"
MISSING_SCRAPED_AT = "missing_scraped_at"
INVALID_CAPTURE_SENTINEL = "not-a-timestamp"


def _is_description_column(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in DESCRIPTION_MARKERS)


def spark_type_for_arrow(arrow_type: pa.DataType) -> str:
    """Map an Arrow type to the Spark type used by the CSV readers."""
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        return "string"
    if pa.types.is_boolean(arrow_type):
        return "boolean"
    if pa.types.is_integer(arrow_type):
        return "long"
    if pa.types.is_floating(arrow_type):
        return "double"
    if pa.types.is_timestamp(arrow_type):
        return "timestamp"
    if pa.types.is_date(arrow_type):
        return "date"
    if (
        pa.types.is_list(arrow_type)
        or pa.types.is_large_list(arrow_type)
        or pa.types.is_fixed_size_list(arrow_type)
    ):
        # CSV cannot carry arrays: the value is serialized as a JSON string and
        # declared as such in the manifest.
        return "string"
    if pa.types.is_decimal(arrow_type):
        return "double"
    raise ValueError(f"Tipo Arrow no soportado en el kit: {arrow_type}")


def _format_timestamp(value: datetime) -> str:
    """Format a timestamp as a naive UTC string with microseconds."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _clean_text(value: str) -> str:
    """Keep a value on one CSV line; the Spark reader uses multiLine=false."""
    return (
        value.replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\x00", "")
    )


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def _format_value(
    value: Any, arrow_type: pa.DataType, column_name: str
) -> Tuple[str, bool]:
    """Convert an Arrow value to ``(csv_text, was_truncated)``.

    An empty string represents NULL; the flag is true when the text was cut to
    the declared limit, so the manifest lists only columns actually truncated.
    """
    if value is None:
        return "", False
    if isinstance(value, bool):
        return ("true" if value else "false"), False
    if isinstance(value, datetime):
        return _format_timestamp(value), False
    if isinstance(value, date):
        return value.isoformat(), False
    if pa.types.is_floating(arrow_type):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "", False
        return format(float(value), ".15g"), False
    if pa.types.is_integer(arrow_type):
        return str(int(value)), False
    if pa.types.is_boolean(arrow_type):
        return ("true" if value else "false"), False
    if pa.types.is_timestamp(arrow_type):
        return _format_timestamp(value), False
    if isinstance(value, (list, dict)):
        return (
            _clean_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"))),
            False,
        )
    text = _clean_text(str(value))
    limit = DESCRIPTION_CHARS if _is_description_column(column_name) else TEXT_CHARS
    return _truncate(text, limit), len(text) > limit


def _parquet_sort_key(path: Path) -> Tuple[date, str, int]:
    """Latest landing file first: ``dia=`` date, then name, then mtime."""
    day = date.min
    for part in path.parts:
        if part.startswith("dia="):
            try:
                day = date.fromisoformat(part[4:])
            except ValueError:
                pass
            break
    return (day, path.name, path.stat().st_mtime_ns)


def find_latest_parquet(source_dir: Path) -> Path:
    """Return the most recent Parquet under ``source_dir`` (recursive)."""
    candidates = sorted(source_dir.rglob("*.parquet"), key=_parquet_sort_key)
    if not candidates:
        raise FileNotFoundError(
            f"No hay ficheros Parquet en {source_dir}."
        )
    return candidates[-1]


def _input_date(path: Path) -> str:
    for part in path.parts:
        if part.startswith("dia="):
            try:
                return date.fromisoformat(part[4:]).isoformat()
            except ValueError:
                break
    return date.fromtimestamp(path.stat().st_mtime).isoformat()


def _relative_input_file(path: Path, source_dir: Path) -> str:
    try:
        return path.relative_to(source_dir).as_posix()
    except ValueError:
        return path.name


def _column_metadata(table: pa.Table) -> List[Dict[str, Any]]:
    columns = []
    for field in table.schema:
        columns.append(
            {
                "name": field.name,
                "spark_type": spark_type_for_arrow(field.type),
                "nullable": bool(field.nullable),
                "arrow_type": str(field.type),
            }
        )
    return columns


def _stable_source_seed(seed: int, source: str) -> int:
    digest = hashlib.sha256(f"{seed}:{source}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _sample_indices(count: int, rows: int, seed: int, source: str) -> Tuple[List[int], bool]:
    if count <= rows:
        return list(range(count)), False
    rng = random.Random(_stable_source_seed(seed, source))
    return sorted(rng.sample(range(count), rows)), True


def _is_parseable_timestamp(text: str) -> bool:
    if not text.strip():
        return False
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _missing_key(row: Mapping[str, str], keys: Sequence[str]) -> bool:
    return any(not (row.get(key) or "").strip() for key in keys)


def _quarantine_reason(
    row: Mapping[str, str], keys: Sequence[str], capture_column: str
) -> Optional[str]:
    if _missing_key(row, keys):
        return MISSING_KEY
    if not _is_parseable_timestamp(row.get(capture_column, "")):
        return MISSING_SCRAPED_AT
    return None


def _format_rows(
    table: pa.Table, indices: Iterable[int]
) -> Tuple[List[Dict[str, str]], List[str]]:
    records = table.to_pylist()
    columns = list(table.schema.names)
    types = [field.type for field in table.schema]
    rows: List[Dict[str, str]] = []
    truncated: set = set()
    for index in indices:
        record = records[index]
        formatted: Dict[str, str] = {}
        for name, arrow_type in zip(columns, types):
            text, was_truncated = _format_value(
                record.get(name), arrow_type, name
            )
            formatted[name] = text
            if was_truncated:
                truncated.add(name)
        rows.append(formatted)
    return rows, [name for name in columns if name in truncated]


def _inject_invalid_rows(
    rows: List[Dict[str, str]],
    keys: Sequence[str],
    capture_column: str,
) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    """Append one ``missing_key`` row and one ``missing_scraped_at`` row.

    The injected rows are copies of real rows with a blank key or an
    unparseable capture value; the manifest declares them as synthetic.
    """
    valid_base = next(
        (
            row
            for row in rows
            if not _missing_key(row, keys)
            and _is_parseable_timestamp(row.get(capture_column, ""))
        ),
        None,
    )
    if valid_base is None:
        raise ValueError(
            "No hay filas validas para inyectar los casos de cuarentena."
        )

    missing_key_row = dict(valid_base)
    missing_key_row[keys[0]] = ""

    missing_scraped_at_row = dict(valid_base)
    missing_scraped_at_row[capture_column] = INVALID_CAPTURE_SENTINEL

    injected = rows + [missing_key_row, missing_scraped_at_row]
    counts = {MISSING_KEY: 0, MISSING_SCRAPED_AT: 0}
    for row in injected:
        reason = _quarantine_reason(row, keys, capture_column)
        if reason is not None:
            counts[reason] += 1
    return injected, counts


def _distinct_valid_keys(
    rows: Sequence[Mapping[str, str]], keys: Sequence[str], capture_column: str
) -> int:
    valid_keys = {
        tuple(row.get(key, "") for key in keys)
        for row in rows
        if _quarantine_reason(row, keys, capture_column) is None
    }
    return len(valid_keys)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def generate(
    input_dir: Path,
    output_dir: Path,
    rows: int = 200,
    seed: int = 20261102,
) -> Dict[str, Any]:
    """Generate the CSV files, ``schema.json`` and ``manifest.json``."""
    if rows < 1:
        raise ValueError("--rows debe ser mayor que 0.")
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    data_dir = output_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    schema_sources: Dict[str, Any] = {}
    manifest_sources: List[Dict[str, Any]] = []
    total_bytes = 0
    total_rows = 0
    total_valid = 0
    total_invalid = 0
    total_invalid_by_reason = {MISSING_KEY: 0, MISSING_SCRAPED_AT: 0}

    for source in SOURCE_ORDER:
        spec = SOURCE_SPECS[source]
        keys = list(spec["keys"])
        capture_column = spec["capture_column"]
        source_dir = input_dir / source
        parquet_path = find_latest_parquet(source_dir)

        # ``partitioning=None`` keeps the landing folder layout (``dia=...``)
        # out of the data: the CSV carries the stored columns only, not the
        # partition key inferred from the path.
        table = pq.read_table(parquet_path, partitioning=None)
        indices, sampled = _sample_indices(table.num_rows, rows, seed, source)
        sampled_rows, truncated_columns = _format_rows(table, indices)
        all_rows, invalid_counts = _inject_invalid_rows(
            sampled_rows, keys, capture_column
        )
        columns = list(table.schema.names)
        header = columns

        csv_path = data_dir / f"{source}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            for row in all_rows:
                writer.writerow([row.get(name, "") for name in header])

        payload = csv_path.read_bytes()
        sha256 = hashlib.sha256(payload).hexdigest()
        csv_bytes = len(payload)
        total_bytes += csv_bytes
        total_rows += len(all_rows)
        invalid_rows = sum(invalid_counts.values())
        ordinal_valid = len(all_rows) - invalid_rows
        total_valid += ordinal_valid
        total_invalid += invalid_rows
        for reason, count in invalid_counts.items():
            total_invalid_by_reason[reason] += count

        schema_sources[source] = {
            "csv": f"data/{source}.csv",
            "keys": keys,
            "capture_column": capture_column,
            "columns": _column_metadata(table),
        }
        manifest_sources.append(
            {
                "source": source,
                "input_file": _relative_input_file(parquet_path, source_dir),
                "input_date": _input_date(parquet_path),
                "input_rows": table.num_rows,
                "input_bytes": parquet_path.stat().st_size,
                "sampled": sampled,
                "sampled_rows": len(sampled_rows),
                "csv": f"data/{source}.csv",
                "csv_bytes": csv_bytes,
                "sha256": sha256,
                "rows": len(all_rows),
                "columns": len(columns),
                "valid_rows": ordinal_valid,
                "invalid_rows": invalid_rows,
                "invalid_by_reason": {
                    reason: invalid_counts.get(reason, 0)
                    for reason in (MISSING_KEY, MISSING_SCRAPED_AT)
                },
                "distinct_valid_keys": _distinct_valid_keys(
                    all_rows, keys, capture_column
                ),
                "columns_truncated": truncated_columns,
                "list_columns_as_json": [
                    field.name
                    for field in table.schema
                    if pa.types.is_list(field.type)
                    or pa.types.is_large_list(field.type)
                    or pa.types.is_fixed_size_list(field.type)
                ],
            }
        )

    schema_payload = {
        "format_version": 1,
        "description": (
            "Spark schema of the demo CSV files. Derived from the Arrow schema "
            "of the latest landing Parquet per source."
        ),
        "sources": schema_sources,
    }
    _write_json(output_dir / "schema.json", schema_payload)

    manifest_payload = {
        "kit": "spec-011 free edition demo",
        "generator": "generate_dataset.py",
        "input_selection": (
            "Latest Parquet per source (recursive): dia= partition date first, "
            "then file name, then modification time."
        ),
        "seed": seed,
        "rows_requested_per_source": rows,
        "sampling": (
            "Deterministic sample without replacement per source: "
            "random.Random(sha256('<seed>:<source>')), selected indices sorted "
            "to keep the source row order. All rows are used when the input has "
            "fewer rows than requested."
        ),
        "truncation": {
            "description_columns": DESCRIPTION_MARKERS,
            "description_chars": DESCRIPTION_CHARS,
            "other_text_chars": TEXT_CHARS,
            "line_breaks": "replaced by spaces (Spark CSV multiLine=false)",
            "timestamps": (
                "naive UTC strings with microseconds: "
                "yyyy-MM-dd HH:mm:ss.SSSSSS"
            ),
            "list_columns": "serialized as JSON strings; not truncated",
        },
        "injected_rows_are_synthetic": True,
        "injected_rows": [
            {
                "reason": MISSING_KEY,
                "detail": "one row per source with a blank bronze key",
            },
            {
                "reason": MISSING_SCRAPED_AT,
                "detail": (
                    "one row per source with the capture column set to "
                    f"'{INVALID_CAPTURE_SENTINEL}'"
                ),
            },
        ],
        "total_csv_bytes": total_bytes,
        "total_bytes_warning": (
            None if total_bytes <= MAX_TOTAL_BYTES else
            f"Los CSV superan {MAX_TOTAL_BYTES} bytes: baja --rows o el truncado."
        ),
        "totals": {
            "rows": total_rows,
            "valid_rows": total_valid,
            "invalid_rows": total_invalid,
            "invalid_by_reason": total_invalid_by_reason,
        },
        "sources": manifest_sources,
    }
    _write_json(output_dir / "manifest.json", manifest_payload)
    return manifest_payload


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Genera el extracto CSV del kit de la demo (T-09) desde el Parquet "
            "mas reciente de cada fuente."
        )
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help=(
            "Directorio con una subcarpeta por fuente "
            "(indeed/, infojobs/, linkedin/, multi_site/)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent),
        help="Directorio de salida (por defecto, la carpeta del script).",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=200,
        help="Filas maximas por fuente antes de inyectar las invalidas (200).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20261102,
        help="Semilla del muestreo determinista (20261102).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    manifest = generate(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        rows=args.rows,
        seed=args.seed,
    )
    print("Fuente      filas  validas  missing_key  missing_scraped_at  bytes")
    for item in manifest["sources"]:
        print(
            f"{item['source']:<11} {item['rows']:>6} {item['valid_rows']:>8} "
            f"{item['invalid_by_reason'][MISSING_KEY]:>12} "
            f"{item['invalid_by_reason'][MISSING_SCRAPED_AT]:>19} "
            f"{item['csv_bytes']:>6}"
        )
    totals = manifest["totals"]
    print(
        f"Total: {totals['rows']} filas, {totals['invalid_rows']} invalidas, "
        f"{manifest['total_csv_bytes']} bytes de CSV."
    )
    print(f"Manifiesto: {Path(args.output_dir) / 'manifest.json'}")
    print(f"Esquema:    {Path(args.output_dir) / 'schema.json'}")
    if manifest["total_bytes_warning"]:
        print(f"AVISO: {manifest['total_bytes_warning']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
