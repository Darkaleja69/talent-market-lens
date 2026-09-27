"""Tests for the data contract field validity rules (T-04, T-05; RF-3, RF-5)."""
from __future__ import annotations

import pytest

from verification import field_contract as fc

MEASURED_FIELDS = (
    "id",
    "title",
    "company",
    "description",
    "salary",
    "skills",
    "work_mode",
    "location",
    "posted_date",
)

REQUIRED = ("title", "company", "description")


def test_all_measured_fields_have_a_rule():
    assert set(fc.MEASURED_FIELDS) == set(MEASURED_FIELDS)
    for field in MEASURED_FIELDS:
        assert fc.field_rule(field).field == field


def test_required_fields_are_marked_as_such():
    for field in REQUIRED:
        assert fc.is_required(field) is True
    for field in set(MEASURED_FIELDS) - set(REQUIRED):
        assert fc.is_required(field) is False


def test_status_constants_are_distinct():
    assert fc.VALID != fc.ABSENT
    assert fc.ABSENT != fc.INVALID
    assert fc.VALID != fc.INVALID


def test_unknown_field_raises():
    with pytest.raises(KeyError):
        fc.classify("not_a_field", "value")
    with pytest.raises(KeyError):
        fc.is_required("not_a_field")


def test_absent_values_are_absent_not_invalid():
    for field in MEASURED_FIELDS:
        for empty in (None, "", "   "):
            assert fc.classify(field, empty) is fc.ABSENT, (field, empty)


@pytest.mark.parametrize("field", ["title", "company", "description", "location"])
def test_text_fields_valid_examples(field):
    assert fc.classify(field, "Data Engineer") is fc.VALID
    # No minimum text length is enforced by the contract.
    assert fc.classify(field, ".") is fc.VALID


@pytest.mark.parametrize("field", ["title", "company", "description", "location"])
def test_text_fields_reject_urls(field):
    assert fc.classify(field, "https://example.com/job") is fc.INVALID


def test_company_rejects_ratings_and_urls_but_keeps_names():
    assert fc.classify("company", "4,5") is fc.INVALID
    assert fc.classify("company", "4.0") is fc.INVALID
    assert fc.classify("company", "https://www.glassdoor.com/Overview") is fc.INVALID
    assert fc.classify("company", "ACME Corp") is fc.VALID


def test_title_and_description_are_text_and_only_reject_urls():
    assert fc.classify("title", "https://example.com/job") is fc.INVALID
    assert fc.classify("description", "https://example.com/job") is fc.INVALID
    # A rating-like string is still plain text for these fields.
    assert fc.classify("title", "4,5") is fc.VALID


def test_id_validity():
    assert fc.classify("id", "8421-abcd") is fc.VALID
    assert fc.classify("id", "x" * 200) is fc.INVALID


def test_posted_date_validity():
    assert fc.classify("posted_date", "2026-09-26") is fc.VALID
    assert fc.classify("posted_date", "2026-09-26T08:30:00Z") is fc.VALID
    assert fc.classify("posted_date", "not-a-date") is fc.INVALID


def test_salary_accepts_numbers_and_salary_text():
    assert fc.classify("salary", 30000) is fc.VALID
    assert fc.classify("salary", 30000.5) is fc.VALID
    assert fc.classify("salary", "30000") is fc.VALID
    assert fc.classify("salary", "40.000-55.000 euros/ano") is fc.VALID
    assert fc.classify("salary", "EUR 30k") is fc.VALID


def test_salary_rejects_non_numeric_without_salary_text():
    assert fc.classify("salary", "competitive") is fc.INVALID
    assert fc.classify("salary", "negotiable") is fc.INVALID


def test_work_mode_accepts_canonical_values_case_insensitively():
    for value in ("Remote", "remote", "Hybrid", "On-site", "ON-SITE", " on-site "):
        assert fc.classify("work_mode", value) is fc.VALID, value


def test_work_mode_accepts_real_spanish_and_english_variants():
    valid = (
        "Remoto",
        "En remoto",
        "a distancia",
        "Teletrabajo",
        "Híbrido",
        "hibrido",
        "Presencial",
        "onsite",
    )
    for value in valid:
        assert fc.classify("work_mode", value) is fc.VALID, value


def test_work_mode_rejects_values_outside_the_set():
    for value in ("Flexible", "Mixto", "Presencial remoto", "N/A"):
        assert fc.classify("work_mode", value) is fc.INVALID, value


def test_normalize_work_mode():
    assert fc.normalize_work_mode("Híbrido") == "Hybrid"
    assert fc.normalize_work_mode("hibrido") == "Hybrid"
    assert fc.normalize_work_mode("En remoto") == "Remote"
    assert fc.normalize_work_mode("a distancia") == "Remote"
    assert fc.normalize_work_mode("Teletrabajo") == "Remote"
    assert fc.normalize_work_mode("Presencial") == "On-site"
    assert fc.normalize_work_mode("REMOTO") == "Remote"
    assert fc.normalize_work_mode("onsite") == "On-site"
    assert fc.normalize_work_mode("Flexible") is None
    assert fc.normalize_work_mode("Mixto") is None
    assert fc.normalize_work_mode(None) is None
    assert fc.normalize_work_mode("") is None


def test_skills_accepts_lists_and_pipe_or_comma_strings():
    assert fc.classify("skills", ["Python", "SQL"]) is fc.VALID
    assert fc.classify("skills", "Python|SQL") is fc.VALID
    assert fc.classify("skills", "Python, SQL") is fc.VALID
    assert fc.classify("skills", "Python") is fc.VALID


def test_skills_absent_when_empty_container_or_none():
    for empty in (None, "", "   ", [], ()):
        assert fc.classify("skills", empty) is fc.ABSENT, empty


def test_skills_invalid_when_present_but_without_a_real_skill():
    assert fc.classify("skills", "||") is fc.INVALID
    assert fc.classify("skills", " , ") is fc.INVALID
    assert fc.classify("skills", ["", "  "]) is fc.INVALID


def test_field_rule_required_flags():
    for field in REQUIRED:
        assert fc.field_rule(field).required is True
    assert fc.field_rule("salary").required is False
    assert fc.field_rule("skills").required is False
    assert fc.field_rule("work_mode").required is False
    assert fc.field_rule("posted_date").required is False


def test_reuses_coherence_validators():
    # The contract must not duplicate structural validation.
    import coherence

    assert fc.coherence_validators()["text"] is coherence._v_text
    assert fc.coherence_validators()["date"] is coherence._v_date
    assert fc.coherence_validators()["num"] is coherence._v_num
    assert fc.coherence_validators()["id"] is coherence._v_id
