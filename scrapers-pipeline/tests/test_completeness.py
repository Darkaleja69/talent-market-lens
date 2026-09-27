"""Tests for structural Parquet reading/validation (T-25; RF-3, RF-5)."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import completeness, field_contract, sources

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


# --- T-27: per-field validity and completeness (RF-4, RF-5) ------------------

# Expected per-field outcome for `_indeed_table`: (required, valid, absent,
# invalid, completeness_pct) over its 4 unique offers.
INDEED_EXPECTED = {
    "id": (False, 4, 0, 0, 100.0),
    "title": (True, 2, 1, 1, 50.0),
    "company": (True, 2, 1, 1, 50.0),
    "description": (True, 3, 1, 0, 75.0),
    "salary": (False, 2, 1, 1, 50.0),
    "skills": (False, 0, 4, 0, 0.0),
    "work_mode": (False, 2, 1, 1, 50.0),
    "location": (False, 3, 1, 0, 75.0),
    "posted_date": (False, 3, 0, 1, 75.0),
}


def _indeed_table() -> pa.Table:
    """Four unique Indeed offers plus one exact duplicate on the dedup key.

    It mixes valid values, genuinely absent ones (``None``/blank) and values
    that violate the contract, across every measured field.
    """
    return pa.table(
        {
            "job_key": ["i1", "i1", "i2", "i3", "i4"],
            "title": [
                "Data Engineer",
                "duplicate of i1",
                "https://example.com/job",  # invalid: URL
                "Analyst",
                None,  # absent
            ],
            "company": ["ACME Corp", "duplicate", "4,5", "Globex", "   "],
            "description_text": [
                "Long description",
                "duplicate",
                "",  # absent
                "Another description",
                "Third description",
            ],
            "salary_text": [
                "40.000-55.000 EUR",
                "duplicate",
                None,  # absent
                "competitive",  # invalid, but salary_min below rescues it
                "competitive",  # invalid and nothing else present
            ],
            "salary_min": [None, None, None, 30000.0, None],
            "salary_max": [None, None, None, 45000.0, None],
            "workplace_type": ["Remote", "duplicate", "Flexible", "Híbrido", None],
            "location": ["Madrid", "duplicate", None, "Barcelona", "Valencia"],
            "posted_date": [
                "2026-09-26",
                "duplicate",
                "not-a-date",
                "2026-09-20",
                "2026-09-21",
            ],
        }
    )


def test_measure_offers_completeness_reports_every_measured_field():
    offers = completeness.unique_offers(_indeed_table(), "indeed")

    result = completeness.measure_offers_completeness(offers, "indeed")

    assert result.source == "indeed"
    assert result.total_offers == 4
    assert tuple(result.fields) == field_contract.MEASURED_FIELDS
    for field, (required, valid, absent, invalid, pct) in INDEED_EXPECTED.items():
        stats = result.fields[field]
        assert stats.field == field
        assert stats.total == 4
        assert stats.required is required, field
        assert stats.valid == valid, field
        assert stats.absent == absent, field
        assert stats.invalid == invalid, field
        assert stats.completeness_pct == pct, field
        # Counters are exhaustive: absent + invalid + valid == total (RF-4).
        assert stats.valid + stats.absent + stats.invalid == stats.total


def test_measure_completeness_deduplicates_before_counting_the_denominator():
    table = _indeed_table()  # 5 rows, but `i1` repeats: 4 unique offers.

    result = completeness.measure_completeness(table, "indeed")

    assert result.total_offers == 4
    assert table.num_rows == 5
    for stats in result.fields.values():
        assert stats.total == 4


def test_measure_completeness_empty_table_is_zero_not_a_division_error():
    table = pa.table({"job_key": pa.array([], type=pa.string())})

    result = completeness.measure_completeness(table, "indeed")

    assert result.total_offers == 0
    assert tuple(result.fields) == field_contract.MEASURED_FIELDS
    for stats in result.fields.values():
        assert stats.total == 0
        assert stats.valid == 0
        assert stats.absent == 0
        assert stats.invalid == 0
        assert stats.completeness_pct == 0.0


def test_required_flags_come_from_the_contract():
    result = completeness.measure_completeness(_indeed_table(), "indeed")

    for field in ("title", "company", "description"):
        assert result.fields[field].required is True
    for field in set(field_contract.MEASURED_FIELDS) - {
        "title",
        "company",
        "description",
    }:
        assert result.fields[field].required is False


def test_absent_and_invalid_are_counted_separately():
    table = pa.table(
        {
            "job_key": ["a", "b"],
            # Row `a` is genuinely absent everywhere; row `b` is present but
            # breaks the contract in every field.
            "title": [None, "https://example.com/job"],
            "company": ["", "4,5"],
            "description_text": ["   ", "https://example.com/job"],
            "salary_text": [None, "competitive"],
            "workplace_type": [None, "Flexible"],
            "location": [None, "https://example.com/job"],
            "posted_date": [None, "not-a-date"],
        }
    )

    result = completeness.measure_completeness(table, "indeed")

    for field in (
        "title",
        "company",
        "description",
        "salary",
        "work_mode",
        "location",
        "posted_date",
    ):
        stats = result.fields[field]
        assert stats.total == 2, field
        assert stats.valid == 0, field
        assert stats.absent == 1, field
        assert stats.invalid == 1, field
        assert stats.completeness_pct == 0.0, field


def test_field_without_aliases_is_absent_and_never_invalid():
    # Indeed and InfoJobs do not publish `skills`; a stray column must not be
    # mistaken for a real, contract-violating value.
    assert sources.field_aliases("indeed", "skills") == ()
    assert sources.field_aliases("infojobs", "skills") == ()

    offer = {"job_key": "a", "skills": "Python|SQL"}

    assert (
        completeness.classify_offer_field(
            "skills", offer, sources.field_aliases("indeed", "skills")
        )
        is field_contract.ABSENT
    )

    table = pa.table(
        {
            "job_key": ["a", "b"],
            "title": ["t", "t"],
            "company": ["c", "c"],
            "description_text": ["d", "d"],
            "skills": ["Python|SQL", "||"],
        }
    )
    stats = completeness.measure_completeness(table, "indeed").fields["skills"]
    assert stats.valid == 0
    assert stats.absent == 2
    assert stats.invalid == 0


def test_classify_offer_field_alias_preference_valid_beats_invalid():
    aliases = ("description_full", "description_snippet")

    valid_fallback = {
        "description_full": "https://example.com/job",  # invalid URL
        "description_snippet": "Real snippet",  # valid
    }
    assert (
        completeness.classify_offer_field("description", valid_fallback, aliases)
        is field_contract.VALID
    )

    invalid_only = {
        "description_full": None,  # absent
        "description_snippet": "https://example.com/job",  # invalid
    }
    assert (
        completeness.classify_offer_field("description", invalid_only, aliases)
        is field_contract.INVALID
    )

    absent_only = {"other": "x"}
    assert (
        completeness.classify_offer_field("description", absent_only, aliases)
        is field_contract.ABSENT
    )


def test_description_alias_fallback_prefers_a_valid_snippet():
    table = pa.table(
        {
            "job_id": ["d1", "d2", "d3", "d4"],
            "description_full": [
                "https://example.com/job",  # invalid
                None,  # absent
                "https://example.com/job",  # invalid
                None,  # absent
            ],
            "description_snippet": ["Real snippet", "Snippet only", "", None],
        }
    )

    stats = completeness.measure_completeness(table, "irishjobs").fields["description"]

    assert stats.total == 4
    assert stats.valid == 2
    assert stats.invalid == 1
    assert stats.absent == 1
    assert stats.completeness_pct == 50.0


def test_field_completeness_is_frozen():
    stats = completeness.FieldCompleteness(
        field="title",
        required=True,
        total=1,
        valid=1,
        absent=0,
        invalid=0,
        completeness_pct=100.0,
    )
    with pytest.raises(Exception):
        stats.valid = 0  # type: ignore[misc]


def test_source_completeness_is_frozen():
    result = completeness.SourceCompleteness(source="indeed", total_offers=0, fields={})
    with pytest.raises(Exception):
        result.total_offers = 1  # type: ignore[misc]
