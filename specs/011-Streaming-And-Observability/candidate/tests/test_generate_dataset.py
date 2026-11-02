"""Local tests for the T-09 dataset generator (no Databricks, no Spark)."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_dataset as gd  # noqa: E402


def _schema_for(source: str) -> pa.Schema:
    if source == "indeed":
        return pa.schema(
            [
                ("job_key", pa.string()),
                ("title", pa.string()),
                ("description_text", pa.string()),
                ("salary_min", pa.int64()),
                ("rating", pa.float64()),
                ("is_remote", pa.bool_()),
                ("posted_at", pa.timestamp("us", tz="UTC")),
                ("scraped_at", pa.timestamp("us", tz="UTC")),
            ]
        )
    if source == "infojobs":
        return pa.schema(
            [
                ("id_oferta", pa.string()),
                ("titulo", pa.string()),
                ("descripcion_snippet", pa.string()),
                ("salario_min", pa.float64()),
                ("pagina", pa.int64()),
                ("fecha_scraped", pa.timestamp("us")),
            ]
        )
    if source == "linkedin":
        return pa.schema(
            [
                ("job_id", pa.string()),
                ("title", pa.string()),
                ("description_full", pa.string()),
                ("salary_min", pa.float64()),
                ("is_new", pa.bool_()),
                ("skills", pa.list_(pa.string())),
                ("scraped_at", pa.string()),
            ]
        )
    if source == "multi_site":
        return pa.schema(
            [
                ("site", pa.string()),
                ("job_id", pa.string()),
                ("title", pa.string()),
                ("description_full", pa.string()),
                ("skills", pa.string()),
                ("salary_disclosed", pa.bool_()),
                ("scraped_at", pa.string()),
            ]
        )
    raise AssertionError(source)


def _rows_for(source: str, count: int = 6) -> list:
    rows = []
    for index in range(count):
        key = f"{source}-key-{index}"
        long_description = (
            f"UNIQUE-TITLE-{source}-{index} "
            + ("descripcion larga con comas, \"comillas\" y salto\n" * 40)
        )
        long_title = "titulo-" + ("x" * 400)
        if source == "indeed":
            rows.append(
                {
                    "job_key": key,
                    "title": long_title,
                    "description_text": long_description,
                    "salary_min": 30000 + index,
                    "rating": 4.25 + index / 10,
                    "is_remote": index % 2 == 0,
                    "posted_at": datetime(
                        2026, 11, 1, 10, index, tzinfo=timezone.utc
                    ),
                    "scraped_at": datetime(
                        2026, 11, 1, 12, index, tzinfo=timezone.utc
                    ),
                }
            )
        elif source == "infojobs":
            rows.append(
                {
                    "id_oferta": key,
                    "titulo": long_title,
                    "descripcion_snippet": long_description,
                    "salario_min": 25000.5 + index,
                    "pagina": index + 1,
                    "fecha_scraped": datetime(2026, 11, 1, 13, index),
                }
            )
        elif source == "linkedin":
            rows.append(
                {
                    "job_id": key,
                    "title": long_title,
                    "description_full": long_description,
                    "salary_min": 40000.0 + index,
                    "is_new": index % 2 == 0,
                    "skills": ["Python", "SQL", "Spark"][: (index % 3) + 1],
                    "scraped_at": f"2026-11-01T{14 + index // 60:02d}:{index % 60:02d}:00+00:00",
                }
            )
        else:
            rows.append(
                {
                    "site": "irishjobs" if index % 2 == 0 else "stepstone",
                    "job_id": key,
                    "title": long_title,
                    "description_full": long_description,
                    "skills": "Python|SQL",
                    "salary_disclosed": index % 2 == 0,
                    "scraped_at": f"2026-11-01T{15 + index // 60:02d}:{index % 60:02d}:00+00:00",
                }
            )
    return rows


def _write_source(
    input_dir: Path, source: str, day: str = "2026-11-02", count: int = 6
) -> Path:
    schema = _schema_for(source)
    table = pa.Table.from_pylist(_rows_for(source, count), schema=schema)
    target = input_dir / source / f"dia={day}" / f"{source}_{day}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, target)
    return target


def test_generates_deterministic_csv_schema_and_manifest(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    for source in gd.SOURCE_ORDER:
        _write_source(input_dir, source)

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    manifest1 = gd.generate(input_dir, out1, rows=3, seed=123)
    manifest2 = gd.generate(input_dir, out2, rows=3, seed=123)

    assert (out1 / "manifest.json").read_bytes() == (out2 / "manifest.json").read_bytes()
    assert manifest1 == manifest2

    total_bytes = 0
    for item in manifest1["sources"]:
        source = item["source"]
        assert item["input_rows"] == 6
        assert item["sampled"] is True
        assert item["sampled_rows"] == 3
        assert item["rows"] == 5
        assert item["valid_rows"] == 3
        assert item["invalid_rows"] == 2
        assert item["invalid_by_reason"] == {
            gd.MISSING_KEY: 1,
            gd.MISSING_SCRAPED_AT: 1,
        }
        assert item["distinct_valid_keys"] == 3
        assert "description" in " ".join(item["columns_truncated"]) or (
            source == "infojobs" and "descripcion_snippet" in item["columns_truncated"]
        )

        csv_path = out1 / item["csv"]
        assert (out1 / "data" / f"{source}.csv").read_bytes() == (
            out2 / "data" / f"{source}.csv"
        ).read_bytes()
        payload = csv_path.read_bytes()
        assert item["csv_bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
        total_bytes += len(payload)

        with csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        assert len(rows) == item["rows"]
        assert item["valid_rows"] + item["invalid_rows"] == len(rows)

        keys = list(gd.SOURCE_SPECS[source]["keys"])
        capture = gd.SOURCE_SPECS[source]["capture_column"]
        blank_keys = [row for row in rows if not (row[keys[0]] or "").strip()]
        bad_captures = [
            row
            for row in rows
            if row[capture] and not _parseable(row[capture])
        ]
        assert len(blank_keys) == 1
        assert len(bad_captures) == 1
        if source == "multi_site":
            assert blank_keys[0]["site"] == ""
            assert blank_keys[0]["job_id"] != ""
        else:
            assert blank_keys[0][keys[0]] == ""

        schema = json.loads((out1 / "schema.json").read_text(encoding="utf-8"))
        columns = schema["sources"][source]["columns"]
        assert len(columns) == item["columns"]
        types = {column["name"]: column["spark_type"] for column in columns}
        if source in ("indeed", "infojobs", "linkedin", "multi_site"):
            assert types[keys[0]] == "string"
        if source == "indeed":
            assert types["salary_min"] == "long"
            assert types["rating"] == "double"
            assert types["is_remote"] == "boolean"
            assert types["posted_at"] == "timestamp"
            assert types["scraped_at"] == "timestamp"
        if source == "linkedin":
            assert types["skills"] == "string"
            assert item["list_columns_as_json"] == ["skills"]

        manifest_text = json.dumps(manifest1, ensure_ascii=False)
        assert "UNIQUE-TITLE" not in manifest_text
        assert f"{source}-key-0" not in manifest_text
        for row in rows:
            for name, value in row.items():
                if name in item["list_columns_as_json"]:
                    continue
                assert "\n" not in value
                limit = (
                    gd.DESCRIPTION_CHARS
                    if gd._is_description_column(name)
                    else gd.TEXT_CHARS
                )
                assert len(value) <= limit

    assert manifest1["total_csv_bytes"] == total_bytes
    assert total_bytes <= gd.MAX_TOTAL_BYTES
    assert manifest1["totals"]["valid_rows"] == 12
    assert manifest1["totals"]["invalid_rows"] == 8


def _parseable(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def test_picks_the_latest_dia_partition(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    for source in gd.SOURCE_ORDER:
        _write_source(input_dir, source, day="2026-11-01", count=2)
        _write_source(input_dir, source, day="2026-11-02", count=2)

    manifest = gd.generate(input_dir, tmp_path / "out", rows=10, seed=1)
    for item in manifest["sources"]:
        assert item["input_file"].startswith("dia=2026-11-02/")
        assert item["input_date"] == date(2026, 11, 2).isoformat()
        assert item["rows"] == 4
