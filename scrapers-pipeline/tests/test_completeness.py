"""Tests for structural Parquet reading/validation (T-25; RF-3, RF-5)."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import completeness, sources

# Mandatory columns per source, mirroring `RequiredColumns` in config.ps1.
EXPECTED_REQUIRED_COLUMNS = {
    "indeed": ("job_key", "title", "company", "viewjob_url", "scraped_at"),
    "linkedin": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "infojobs": ("id_oferta", "titulo", "empresa", "url_oferta", "fecha_scraped"),
    "irishjobs": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "stepstone_nl": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "devitjobs": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "nvb": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "jobs_ch": ("job_id", "job_url", "title", "company_name", "scraped_at"),
    "glassdoor": ("job_id", "job_url", "title", "company_name", "scraped_at"),
}


def _write_parquet(path: Path, columns: tuple[str, ...], rows: int = 2) -> None:
    table = pa.table({name: [f"{name}_{i}" for i in range(rows)] for name in columns})
    pq.write_table(table, str(path))


def test_readable_parquet_with_all_required_columns_is_not_a_failure(tmp_path):
    path = tmp_path / "indeed.parquet"
    _write_parquet(path, EXPECTED_REQUIRED_COLUMNS["indeed"], rows=3)

    result = completeness.read_obtained_parquet(
        str(path), sources.required_columns("indeed")
    )

    assert result.readable is True
    assert result.rows == 3
    assert result.missing_columns == ()
    assert result.error is None
    assert completeness.is_structural_failure(result) is False


def test_corrupt_parquet_is_a_structural_failure(tmp_path):
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"not a parquet")

    result = completeness.read_obtained_parquet(str(path))

    assert result.readable is False
    assert result.rows is None
    assert result.error is not None
    assert completeness.is_structural_failure(result) is True


def test_parquet_missing_required_columns_is_a_structural_failure(tmp_path):
    path = tmp_path / "partial.parquet"
    _write_parquet(path, ("job_key", "title"))

    result = completeness.read_obtained_parquet(
        str(path), sources.required_columns("indeed")
    )

    assert result.readable is True
    assert result.missing_columns == ("company", "viewjob_url", "scraped_at")
    assert completeness.is_structural_failure(result) is True


def test_read_source_parquet_uses_catalog_required_columns(tmp_path):
    indeed_path = tmp_path / "indeed.parquet"
    _write_parquet(indeed_path, sources.required_columns("indeed"))

    indeed = completeness.read_source_parquet(str(indeed_path), "indeed")

    assert indeed.source == "indeed"
    assert indeed.readable is True
    assert indeed.missing_columns == ()
    assert completeness.is_structural_failure(indeed) is False

    # A Multi-site portal output that omits `scraped_at` must be flagged.
    glassdoor_path = tmp_path / "glassdoor.parquet"
    _write_parquet(
        glassdoor_path, ("job_id", "job_url", "title", "company_name")
    )

    glassdoor = completeness.read_source_parquet(str(glassdoor_path), "glassdoor")

    assert glassdoor.source == "glassdoor"
    assert glassdoor.readable is True
    assert glassdoor.missing_columns == ("scraped_at",)
    assert completeness.is_structural_failure(glassdoor) is True


def test_corrupt_source_parquet_sets_source_and_fails(tmp_path):
    path = tmp_path / "linkedin_broken.parquet"
    path.write_bytes(b"not a parquet")

    result = completeness.read_source_parquet(str(path), "linkedin")

    assert result.source == "linkedin"
    assert result.readable is False
    assert result.error is not None
    assert completeness.is_structural_failure(result) is True


def test_required_columns_catalog_matches_config_ps1():
    assert set(EXPECTED_REQUIRED_COLUMNS) == set(sources.source_ids())
    for source_id, expected in EXPECTED_REQUIRED_COLUMNS.items():
        assert sources.required_columns(source_id) == expected


def test_obtained_parquet_is_frozen():
    result = completeness.ObtainedParquet(
        source=None,
        path="x.parquet",
        readable=True,
        rows=0,
        columns=(),
        missing_columns=(),
        error=None,
    )
    with pytest.raises(Exception):
        result.rows = 1  # type: ignore[misc]
