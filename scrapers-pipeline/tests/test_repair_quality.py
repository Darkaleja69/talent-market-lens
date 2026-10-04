"""Tests for the quality gate of the live repair test (T-11; RF-7, RF-8, RF-16).

Offline tests for ``repair.quality`` with synthetic Parquet files written to
``tmp_path``: improvement, regression beyond the tolerance and the exact
tolerance boundary, an unpublished optional that does not block, a degraded
optional that does, an incomplete required field, an unmet target, the
description priority, the empty sample and the unreadable file, plus the
Spanish table, the English machine result and determinism.
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from repair import quality, targets
from verification import field_contract, sources

_SOURCE = "nvb"


def _write_live_parquet(
    path: Path,
    *,
    total: int,
    valid_counts: dict[str, int] | None = None,
    omit_column: str | None = None,
) -> None:
    """Write a Multi-site Parquet with the given per-field valid counts.

    The first ``valid_counts[field]`` offers carry a valid value and the rest
    ``None``; a field absent from the map is valid in every offer.
    """
    counts = valid_counts or {}

    def values(field: str, valid_value) -> list:
        count = counts.get(field, total)
        return [valid_value] * count + [None] * (total - count)

    columns = {
        "job_id": [f"job-{index}" for index in range(total)],
        "job_url": [f"https://example.com/{index}" for index in range(total)],
        "title": values("title", "Data Engineer"),
        "company_name": values("company", "ACME"),
        "scraped_at": ["2026-10-03"] * total,
        "description_full": values("description", "Full description"),
        "salary_raw": values("salary", "40.000 EUR"),
        "skills": values("skills", "Python|SQL"),
        "work_mode": values("work_mode", "Remote"),
        "location_raw": values("location", "Amsterdam"),
        "posted_datetime": values("posted_date", "2026-09-20"),
    }
    if omit_column is not None:
        columns.pop(omit_column)
    pq.write_table(pa.table(columns), str(path))


def _profile(
    *,
    before: dict[str, float | None] | None = None,
    goals: dict[str, float | None] | None = None,
) -> tuple[targets.QualityProfile, ...]:
    """Build the T-04-style profile of the source with test overrides."""
    before = before or {}
    target_overrides = goals or {}
    profile = []
    for field in field_contract.MEASURED_FIELDS:
        required = field_contract.is_required(field)
        published = bool(sources.field_aliases(_SOURCE, field))
        profile.append(
            targets.QualityProfile(
                field=field,
                required=required,
                current_pct=before.get(field),
                target_pct=target_overrides.get(
                    field, 100.0 if required or published else None
                ),
                is_focus=False,
            )
        )
    return tuple(profile)


def _row(verdict: quality.QualityVerdict, field: str) -> quality.QualityRow:
    return next(row for row in verdict.rows if row.field == field)


# --- Passing gates -----------------------------------------------------------


def test_improvement_passes_with_required_complete_and_targets_met(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4)
    profile = _profile(
        before={
            "title": 100.0,
            "company": 100.0,
            "description": 60.0,
            "salary": 20.0,
            "skills": 0.0,
            "work_mode": 0.0,
        }
    )

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is True
    assert verdict.total_offers == 4
    assert verdict.blockers == ()
    assert all(row.status == quality.STATUS_PASS for row in verdict.rows)
    description = _row(verdict, "description")
    assert description.before_pct == 60.0
    assert description.after_pct == 100.0
    assert description.delta_pp == 40.0


def test_exact_tolerance_drop_does_not_block(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=20, valid_counts={"skills": 19})
    profile = _profile(before={"skills": 100.0}, goals={"skills": None})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    skills = _row(verdict, "skills")
    assert skills.after_pct == 95.0
    assert skills.delta_pp == -5.0
    assert skills.reasons == ()
    assert skills.status == quality.STATUS_NOT_APPLICABLE
    assert verdict.ok is True


@pytest.mark.parametrize("before", [None, 0.0])
def test_unpublished_optional_absent_does_not_block(tmp_path, before):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"skills": 0})
    profile = _profile(before={"skills": before}, goals={"skills": None})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    skills = _row(verdict, "skills")
    assert skills.after_pct == 0.0
    assert skills.reasons == ()
    assert skills.status == quality.STATUS_NOT_APPLICABLE
    assert verdict.ok is True


# --- Blocking gates ----------------------------------------------------------


def test_regression_beyond_tolerance_blocks(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=10, valid_counts={"salary": 4})
    profile = _profile(before={"salary": 50.0})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    salary = _row(verdict, "salary")
    assert salary.before_pct == 50.0
    assert salary.after_pct == 40.0
    assert salary.delta_pp == -10.0
    assert quality.REASON_REGRESSION in salary.reasons
    assert quality.REASON_REGRESSION in verdict.blockers
    assert salary.status == quality.STATUS_FAIL


def test_degraded_unpublished_optional_blocks(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=10, valid_counts={"skills": 9})
    profile = _profile(before={"skills": 100.0}, goals={"skills": None})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    skills = _row(verdict, "skills")
    assert skills.after_pct == 90.0
    assert quality.REASON_REGRESSION in skills.reasons
    assert quality.REASON_REGRESSION in verdict.blockers


def test_incomplete_required_field_blocks(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"title": 3})
    profile = _profile()

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    title = _row(verdict, "title")
    assert title.after_pct == 75.0
    assert quality.REASON_REQUIRED in title.reasons
    assert quality.REASON_REQUIRED in verdict.blockers


def test_missing_description_comes_first_in_the_blockers(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(
        path, total=4, valid_counts={"description": 2, "title": 3}
    )
    profile = _profile()

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    assert verdict.blockers[0] == quality.BLOCKER_DESCRIPTION_MISSING
    assert quality.REASON_REQUIRED in verdict.blockers


def test_unmet_target_blocks_when_the_portal_exposes_the_field(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"salary": 2})
    profile = _profile(before={"salary": 0.0})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    salary = _row(verdict, "salary")
    assert salary.after_pct == 50.0
    assert quality.REASON_TARGET in salary.reasons
    assert quality.REASON_REGRESSION not in salary.reasons
    assert quality.REASON_TARGET in verdict.blockers


def test_empty_sample_blocks(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=0)
    profile = _profile()

    verdict = quality.evaluate_quality(path, _SOURCE, profile)

    assert verdict.ok is False
    assert verdict.total_offers == 0
    assert quality.BLOCKER_EMPTY_SAMPLE in verdict.blockers
    assert all(row.after_pct == 0.0 for row in verdict.rows)


def test_unreadable_parquet_does_not_raise(tmp_path):
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"not a parquet")

    verdict = quality.evaluate_quality(path, _SOURCE, _profile())

    assert verdict.ok is False
    assert verdict.total_offers is None
    assert verdict.rows == ()
    assert verdict.blockers == (quality.BLOCKER_UNREADABLE,)
    assert verdict.read_error is not None


def test_missing_mandatory_columns_block_without_measuring(tmp_path):
    path = tmp_path / "partial.parquet"
    _write_live_parquet(path, total=2, omit_column="company_name")

    verdict = quality.evaluate_quality(path, _SOURCE, _profile())

    assert verdict.ok is False
    assert verdict.rows == ()
    assert verdict.blockers == (quality.BLOCKER_MISSING_COLUMNS,)
    assert verdict.missing_columns == ("company_name",)


# --- Machine result and Spanish table ----------------------------------------


def test_verdict_to_dict_is_english_and_json_serializable(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"title": 3})
    profile = _profile()

    verdict = quality.evaluate_quality(path, _SOURCE, profile)
    result = quality.verdict_to_dict(verdict)

    assert set(result) == {
        "source",
        "ok",
        "total_offers",
        "blockers",
        "missing_columns",
        "read_error",
        "regression_tolerance_pp",
        "fields",
    }
    assert result["source"] == "nvb"
    assert result["ok"] is False
    assert result["regression_tolerance_pp"] == 5.0
    title = next(
        row for row in result["fields"] if row["field"] == "title"
    )
    assert title["status"] == quality.STATUS_FAIL
    assert quality.REASON_REQUIRED in title["reasons"]
    assert quality.REASON_TARGET in title["reasons"]
    assert json.loads(json.dumps(result)) == result


def test_spanish_table_shows_the_verdict_and_the_before_after_rows(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"description": 2})
    profile = _profile(before={"description": 60.0})

    verdict = quality.evaluate_quality(path, _SOURCE, profile)
    text = quality.render_quality_table(verdict)

    assert "Calidad de la prueba en vivo — fuente: nvb" in text
    assert "Veredicto: NO OK" in text
    assert "Ofertas únicas medidas: 4" in text
    assert "falta la descripción (obligatoria y prioritaria)" in text
    assert "Campo" in text and "Después" in text
    assert "description" in text
    assert "60.0 %" in text
    assert "50.0 %" in text
    assert "no cumple" in text


def test_spanish_table_reports_ok_and_unpublished_optionals(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"skills": 0})
    profile = _profile(goals={"skills": None})

    text = quality.render_quality_table(
        quality.evaluate_quality(path, _SOURCE, profile)
    )

    assert "Veredicto: OK" in text
    assert "Bloqueos: ninguno" in text
    assert "no aplica" in text
    assert "sin meta" in text


def test_unreadable_spanish_table_shows_the_error(tmp_path):
    path = tmp_path / "broken.parquet"
    path.write_bytes(b"not a parquet")

    text = quality.render_quality_table(
        quality.evaluate_quality(path, _SOURCE, _profile())
    )

    assert "Veredicto: NO OK" in text
    assert "Ofertas únicas medidas: no se pudieron medir" in text
    assert "parquet ilegible" in text
    assert "Campos: sin medición" in text


# --- Determinism and immutability --------------------------------------------


def test_verdict_and_rendering_are_deterministic(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=4, valid_counts={"description": 3})
    profile = _profile(before={"description": 80.0})

    first = quality.evaluate_quality(path, _SOURCE, profile)
    second = quality.evaluate_quality(path, _SOURCE, profile)

    assert first == second
    assert quality.verdict_to_dict(first) == quality.verdict_to_dict(second)
    assert quality.render_quality_table(
        first
    ) == quality.render_quality_table(second)


def test_verdict_and_rows_are_frozen(tmp_path):
    path = tmp_path / "nvb.parquet"
    _write_live_parquet(path, total=2)
    verdict = quality.evaluate_quality(path, _SOURCE, _profile())

    with pytest.raises(FrozenInstanceError):
        verdict.ok = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        verdict.rows[0].after_pct = 0.0  # type: ignore[misc]
