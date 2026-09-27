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


# --- T-26: deduplication by each source's own key (RF-4, RF-5) ---------------


def test_deduplicate_offers_collapses_exact_duplicate_keys():
    rows = [
        {"job_key": "a", "title": "first"},
        {"job_key": "a", "title": "duplicate of first"},
        {"job_key": "b", "title": "second"},
    ]

    unique = completeness.deduplicate_offers(rows, "job_key")

    assert unique == (
        {"job_key": "a", "title": "first"},
        {"job_key": "b", "title": "second"},
    )


def test_deduplicate_offers_keeps_first_appearance_order():
    rows = [
        {"job_key": "b", "title": "b first"},
        {"job_key": "a", "title": "a"},
        {"job_key": "b", "title": "b duplicate"},
        {"job_key": "c", "title": "c"},
    ]

    unique = completeness.deduplicate_offers(rows, "job_key")

    assert tuple(row["title"] for row in unique) == ("b first", "a", "c")


def test_deduplicate_offers_strips_keys_but_is_case_sensitive():
    rows = [
        {"job_key": " A ", "title": "first"},
        {"job_key": "A", "title": "duplicate of first"},
        {"job_key": "a", "title": "opaque lower-case id"},
    ]

    unique = completeness.deduplicate_offers(rows, "job_key")

    assert tuple(row["title"] for row in unique) == ("first", "opaque lower-case id")


def test_deduplicate_offers_absent_or_empty_keys_do_not_collapse():
    rows = [
        {"job_key": None, "title": "none"},
        {"job_key": "", "title": "empty"},
        {"job_key": "   ", "title": "spaces"},
        {"job_key": "dup", "title": "first dup"},
        {"job_key": "dup", "title": "second dup"},
    ]

    unique = completeness.deduplicate_offers(rows, "job_key")

    # Three unprovable keys each stay unique; the real duplicate collapses.
    assert tuple(row["title"] for row in unique) == (
        "none",
        "empty",
        "spaces",
        "first dup",
    )


def test_deduplicate_offers_rows_missing_the_key_column_do_not_collapse():
    rows = [{"title": "missing"}, {"title": "also missing"}]

    unique = completeness.deduplicate_offers(rows, "job_key")

    assert unique == ({"title": "missing"}, {"title": "also missing"})


def test_unique_offers_totals_per_source():
    indeed = pa.table(
        {
            "job_key": ["i1", "i1", "i2", "i3", "i3"],
            "title": ["a", "b", "c", "d", "e"],
        }
    )
    linkedin = pa.table(
        {
            "job_id": ["l1", "l2", "l2", "l3"],
            "title": ["a", "b", "c", "d"],
        }
    )
    irishjobs = pa.table(
        {
            "job_id": ["m1", "m1", "m2", "m3", "m3", "m3"],
            "title": ["a", "b", "c", "d", "e", "f"],
        }
    )
    infojobs = pa.table(
        {
            "id_oferta": ["o1", "o2", "o2", "o3", "o4", "o4"],
            "title": ["a", "b", "c", "d", "e", "f"],
        }
    )

    assert completeness.count_unique_offers(indeed, "indeed") == 3
    assert completeness.unique_offers(indeed, "indeed")[0]["job_key"] == "i1"
    assert completeness.count_unique_offers(linkedin, "linkedin") == 3
    assert completeness.count_unique_offers(irishjobs, "irishjobs") == 3
    assert completeness.count_unique_offers(infojobs, "infojobs") == 4


def test_unique_offers_uses_the_dedup_key_of_each_of_the_nine_sources():
    for source_id in sources.source_ids():
        key = sources.dedup_key(source_id)
        # The dedup column repeats; every other column is unique per row, so a
        # count of 2 only holds if the source's own dedup key is the one used.
        table = pa.table(
            {
                key: ["dup", "dup", "only"],
                "other": ["x", "y", "z"],
            }
        )

        unique = completeness.unique_offers(table, source_id)

        assert len(unique) == 2, source_id
        assert unique == (
            {key: "dup", "other": "x"},
            {key: "only", "other": "z"},
        ), source_id
        assert completeness.count_unique_offers(table, source_id) == 2, source_id


def test_unique_offers_reads_a_written_parquet(tmp_path):
    path = tmp_path / "infojobs.parquet"
    table = pa.table(
        {
            "id_oferta": ["o1", "o1", "o2"],
            "titulo": ["a", "b", "c"],
        }
    )
    pq.write_table(table, str(path))

    read_back = pq.read_table(str(path))

    assert completeness.count_unique_offers(read_back, "infojobs") == 2
