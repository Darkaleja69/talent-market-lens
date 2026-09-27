"""Tests for per-source threshold classification (T-28; RF-3, RF-10, RF-14)."""
from __future__ import annotations

import pytest

from verification import completeness, field_contract, status


def _field(
    field: str, required: bool, valid: int, total: int = 1000
) -> completeness.FieldCompleteness:
    """Build a FieldCompleteness with an exact completeness percentage.

    Using the same formula as ``completeness`` (``100 * valid / total``, and
    ``0.0`` when there are no offers) lets the tests pin the borderline values
    89.9/90/99/100 % and 60/60.1 % on integer counts over 1000 offers.
    """
    pct = 0.0 if total == 0 else 100.0 * valid / total
    return completeness.FieldCompleteness(
        field=field,
        required=required,
        total=total,
        valid=valid,
        absent=total - valid,
        invalid=0,
        completeness_pct=pct,
    )


def _source(
    fields: dict[str, completeness.FieldCompleteness],
    source: str = "indeed",
    total_offers: int | None = None,
) -> completeness.SourceCompleteness:
    if total_offers is None:
        total_offers = max((stats.total for stats in fields.values()), default=0)
    return completeness.SourceCompleteness(
        source=source, total_offers=total_offers, fields=fields
    )


# --- Constants ---------------------------------------------------------------


def test_threshold_constants_match_the_spec():
    assert status.REQUIRED_FAIL_PCT == 90.0
    assert status.REQUIRED_TARGET_PCT == 100.0
    assert status.OPTIONAL_INVESTIGATION_PCT == 60.0
    assert status.SOURCE_CORRECT == "correct"
    assert status.SOURCE_FAILED == "failed"


# --- Mandatory field thresholds (RF-3, RF-10) --------------------------------


def test_required_89_9_percent_fails_the_source():
    source = _source({"title": _field("title", True, 899)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("required field 'title' at 89.9% (<90%)",)
    assert result.incidents == ()
    assert result.investigation_fields == ("title",)


def test_required_90_percent_is_correct_with_incident():
    source = _source({"title": _field("title", True, 900)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ("title",)
    assert result.investigation_fields == ("title",)


def test_required_99_percent_is_correct_with_incident():
    source = _source({"company": _field("company", True, 990)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.incidents == ("company",)
    assert result.investigation_fields == ("company",)


def test_required_100_percent_is_correct_without_incident_or_investigation():
    source = _source({"description": _field("description", True, 1000)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ()
    assert result.investigation_fields == ()


# --- Non-mandatory field thresholds (RF-10) ----------------------------------


def test_optional_60_percent_is_correct_and_triggers_investigation():
    source = _source({"salary": _field("salary", False, 600)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ()
    assert result.investigation_fields == ("salary",)


def test_optional_60_1_percent_is_correct_without_investigation():
    source = _source({"salary": _field("salary", False, 601)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ()
    assert result.investigation_fields == ()


# --- Zero offers and missing/structural evidence (RF-3) ----------------------


def test_zero_offers_fails_the_source():
    source = _source(
        {"title": _field("title", True, 0, total=0)},
        total_offers=0,
    )

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("zero offers",)


def test_missing_completeness_fails_as_missing_evidence():
    result = status.classify_source("indeed", None)

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("missing evidence",)


def test_unavailable_evidence_fails_even_with_completeness():
    source = _source({"title": _field("title", True, 1000)})

    result = status.classify_source("indeed", source, evidence_available=False)

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("missing evidence",)


def test_structural_failure_fails_and_includes_the_detail():
    source = _source({"title": _field("title", True, 1000)})

    result = status.classify_source(
        "indeed",
        source,
        structural_failure=True,
        structural_error="corrupt parquet",
    )

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("structural failure: corrupt parquet",)


def test_structural_failure_without_detail_has_a_plain_reason():
    source = _source({"title": _field("title", True, 1000)})

    result = status.classify_source("indeed", source, structural_failure=True)

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("structural failure",)


# --- Contract-driven required flags and scope --------------------------------


def test_required_below_90_also_appears_in_investigation_fields():
    source = _source({"company": _field("company", True, 750)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    assert result.investigation_fields == ("company",)


def test_required_flag_comes_from_field_contract():
    offers = [
        {
            "job_key": f"i{index}",
            "title": f"Title {index}",
            "company": None if index == 0 else "ACME Corp",
            "description_text": f"Description {index}",
            # salary is never published: absent in every offer.
        }
        for index in range(20)
    ]
    source = completeness.measure_offers_completeness(offers, "indeed")

    assert field_contract.is_required("company") is True
    assert field_contract.is_required("salary") is False

    result = status.classify_source("indeed", source)

    # 19/20 = 95 % mandatory -> incident, no failure; salary 0 % optional ->
    # investigation only. This mirrors field_contract's required flags.
    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ("company",)
    assert "salary" in result.investigation_fields


def test_only_present_fields_are_evaluated_no_invented_thresholds():
    # The contract marks company/description as mandatory too, but they were
    # not measured for this source, so no threshold is invented for them.
    source = _source(
        {"title": _field("title", True, 5, total=5)}, total_offers=5
    )

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()


def test_unknown_optional_field_only_triggers_investigation():
    source = _source({"weird_metric": _field("weird_metric", False, 0)})

    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.investigation_fields == ("weird_metric",)


# --- Realistic combinations --------------------------------------------------


def test_realistic_correct_source_with_incidents_and_investigations():
    fields = {
        "id": _field("id", False, 1000),
        "title": _field("title", True, 1000),
        "company": _field("company", True, 950),  # 95 % -> incident
        "description": _field("description", True, 990),  # 99 % -> incident
        "salary": _field("salary", False, 500),  # 50 % -> investigation
        "skills": _field("skills", False, 0),  # 0 % -> investigation
        "work_mode": _field("work_mode", False, 700),  # above threshold
        "location": _field("location", False, 650),  # above threshold
        "posted_date": _field("posted_date", False, 610),  # 61 % -> nothing
    }

    result = status.classify_source("indeed", _source(fields))

    assert result.state == status.SOURCE_CORRECT
    assert result.failures == ()
    assert result.incidents == ("company", "description")
    assert result.investigation_fields == (
        "company",
        "description",
        "salary",
        "skills",
    )


def test_realistic_failed_source_with_a_mandatory_field_below_90():
    fields = {
        "title": _field("title", True, 1000),
        "company": _field("company", True, 899),  # 89.9 % -> failure
        "description": _field("description", True, 1000),
        "salary": _field("salary", False, 600),  # 60 % -> investigation
    }

    result = status.classify_source("indeed", _source(fields))

    assert result.state == status.SOURCE_FAILED
    assert result.failures == ("required field 'company' at 89.9% (<90%)",)
    assert result.incidents == ()
    assert result.investigation_fields == ("company", "salary")


# --- Bulk helper and immutability --------------------------------------------


def test_classify_sources_maps_every_source():
    okay = _source({"title": _field("title", True, 1000)})
    mapping = {"indeed": okay, "linkedin": None}

    result = status.classify_sources(mapping)

    assert tuple(result) == ("indeed", "linkedin")
    assert result["indeed"].state == status.SOURCE_CORRECT
    assert result["linkedin"].state == status.SOURCE_FAILED
    assert result["linkedin"].failures == ("missing evidence",)


def test_source_status_is_frozen():
    result = status.SourceStatus(
        source="indeed",
        state=status.SOURCE_CORRECT,
        failures=(),
        incidents=(),
        investigation_fields=(),
    )
    with pytest.raises(Exception):
        result.state = status.SOURCE_FAILED  # type: ignore[misc]
