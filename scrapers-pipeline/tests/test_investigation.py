"""Tests for the investigation context (T-35; RF-9, RF-10, RF-11).

The module under test is pure: these tests use only the ``verification.*``
business modules, with no network, Azure, PyArrow or browser involved.
"""
from __future__ import annotations

import dataclasses

import pytest

from verification import (
    completeness,
    fingerprint,
    investigation,
    run_evidence,
    sources,
    status,
)

ALL_SOURCES = sources.source_ids()

EXPECTED_URL_COLUMNS = {
    "indeed": "viewjob_url",
    "linkedin": "job_url",
    "infojobs": "url_oferta",
    "irishjobs": "job_url",
    "stepstone_nl": "job_url",
    "devitjobs": "job_url",
    "nvb": "job_url",
    "jobs_ch": "job_url",
    "glassdoor": "job_url",
}


def _field(
    field: str, required: bool, valid: int, total: int = 1000
) -> completeness.FieldCompleteness:
    """Build a FieldCompleteness with an exact completeness percentage."""
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
    source_id: str,
    fields: dict[str, completeness.FieldCompleteness],
    total_offers: int | None = None,
) -> completeness.SourceCompleteness:
    if total_offers is None:
        total_offers = max((stats.total for stats in fields.values()), default=0)
    return completeness.SourceCompleteness(
        source=source_id, total_offers=total_offers, fields=fields
    )


def _indeed_offers(count: int) -> list[dict]:
    """Build valid, unique Indeed offers (all fields present)."""
    return [
        {
            "job_key": f"i{index}",
            "title": f"Title {index}",
            "company": "ACME Corp",
            "description_text": "A real description.",
            "viewjob_url": f"https://indeed.example/{index}",
        }
        for index in range(count)
    ]


# --- Trigger constants -------------------------------------------------------


def test_trigger_constants_match_the_design():
    assert investigation.TRIGGER_SOURCE_FAILED == "source_failed"
    assert (
        investigation.TRIGGER_REQUIRED_FIELD == "required_field_below_target"
    )
    assert (
        investigation.TRIGGER_OPTIONAL_FIELD
        == "optional_field_at_or_below_threshold"
    )


# --- Triggering rules (RF-9, RF-10) ------------------------------------------


def test_correct_source_without_investigation_fields_has_no_contexts():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 1000),
            "description": _field("description", True, 1000),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert investigation.needs_investigation(result) is False
    assert (
        investigation.build_contexts(
            "indeed", result, completeness=source, offers=_indeed_offers(3)
        )
        == ()
    )
    assert (
        investigation.build_context(
            "indeed", result, completeness=source
        )
        is None
    )


def test_needs_investigation_for_correct_source_with_flagged_fields():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.investigation_fields == ("salary",)
    assert investigation.needs_investigation(result) is True


# --- Failed sources: zero offers, missing evidence, structural (RF-9) --------


def test_zero_offers_builds_a_single_source_failed_context():
    source = _source(
        "indeed", {"title": _field("title", True, 0, total=0)}, total_offers=0
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    contexts = investigation.build_contexts(
        "indeed", result, completeness=source, offers=[]
    )

    assert len(contexts) == 1
    context = contexts[0]
    assert context.trigger == investigation.TRIGGER_SOURCE_FAILED
    assert context.field is None
    assert context.source == "indeed"
    assert context.display_name == "Indeed"
    assert context.group == "direct"
    assert context.metric is None
    assert context.rule_reference is None
    assert any("cero" in item.lower() for item in context.evidence)


def test_missing_evidence_builds_a_source_failed_context():
    result = status.classify_source("linkedin", None)

    assert result.state == status.SOURCE_FAILED
    contexts = investigation.build_contexts("linkedin", result)

    assert len(contexts) == 1
    assert contexts[0].trigger == investigation.TRIGGER_SOURCE_FAILED
    assert any(
        "evidencia" in item.lower() for item in contexts[0].evidence
    )


def test_structural_failure_reports_the_multi_site_group():
    source = _source("devitjobs", {"title": _field("title", True, 1000)})
    result = status.classify_source(
        "devitjobs", source, structural_failure=True
    )

    (context,) = investigation.build_contexts("devitjobs", result)

    assert context.display_name == "DevITjobs"
    assert context.group == "multi_site"
    assert any(
        "estructural" in item.lower() for item in context.evidence
    )


# --- Field thresholds (RF-10) ------------------------------------------------


def test_required_field_below_target_builds_a_required_context():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
            "description": _field("description", True, 1000),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    contexts = investigation.build_contexts(
        "indeed", result, completeness=source, offers=_indeed_offers(3)
    )

    assert len(contexts) == 1
    context = contexts[0]
    assert context.trigger == investigation.TRIGGER_REQUIRED_FIELD
    assert context.field == "company"
    assert context.metric == "válidos 850/1000 (85.0%)"
    assert context.rule_reference is not None
    assert "company" in context.rule_reference
    assert context.example_offer is not None


def test_optional_field_at_threshold_builds_an_optional_context():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 600),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    contexts = investigation.build_contexts(
        "indeed", result, completeness=source, offers=_indeed_offers(3)
    )

    assert len(contexts) == 1
    context = contexts[0]
    assert context.trigger == investigation.TRIGGER_OPTIONAL_FIELD
    assert context.field == "salary"
    assert context.metric == "válidos 600/1000 (60.0%)"
    assert "salary_text" in context.rule_reference


def test_optional_field_above_threshold_has_no_context():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 601),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.investigation_fields == ()
    assert (
        investigation.build_contexts("indeed", result, completeness=source)
        == ()
    )


def test_required_between_90_and_100_is_an_incident_in_the_evidence():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 950),
        },
    )
    result = status.classify_source("indeed", source)

    (context,) = investigation.build_contexts(
        "indeed", result, completeness=source
    )

    assert context.trigger == investigation.TRIGGER_REQUIRED_FIELD
    assert any(
        "incidencia" in item.lower() for item in context.evidence
    )


def test_several_flagged_fields_build_one_context_per_field():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    assert set(result.investigation_fields) == {"company", "salary"}

    contexts = investigation.build_contexts(
        "indeed", result, completeness=source, offers=_indeed_offers(3)
    )

    by_field = {context.field: context for context in contexts}
    assert set(by_field) == {"company", "salary"}
    assert by_field["company"].trigger == investigation.TRIGGER_REQUIRED_FIELD
    assert by_field["salary"].trigger == investigation.TRIGGER_OPTIONAL_FIELD


def test_build_context_with_unflagged_field_returns_none():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    assert result.investigation_fields == ("salary",)
    assert (
        investigation.build_context(
            "indeed", result, completeness=source, field="company"
        )
        is None
    )


def test_unflagged_canonical_field_is_not_investigated():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("indeed", source)

    contexts = investigation.build_contexts(
        "indeed", result, completeness=source
    )

    assert [context.field for context in contexts] == ["salary"]


def test_failed_source_with_a_concrete_field_has_no_general_context():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
        },
    )
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_FAILED
    assert result.investigation_fields == ("company",)
    assert (
        investigation.build_context("indeed", result, completeness=source)
        is None
    )


def test_unknown_field_raises_value_error():
    result = status.classify_source("linkedin", None)

    with pytest.raises(ValueError):
        investigation.build_context("linkedin", result, field="not_a_field")


def test_unknown_field_raises_value_error_without_investigation():
    source = _source("indeed", {"title": _field("title", True, 1000)})
    result = status.classify_source("indeed", source)

    assert result.state == status.SOURCE_CORRECT
    with pytest.raises(ValueError):
        investigation.build_context(
            "indeed", result, completeness=source, field="not_a_field"
        )



# --- Example URL per source (RF-9) -------------------------------------------


@pytest.mark.parametrize("source_id", ALL_SOURCES)
def test_example_url_column_per_source(source_id):
    assert (
        investigation.example_url_column(source_id)
        == EXPECTED_URL_COLUMNS[source_id]
    )


@pytest.mark.parametrize("source_id", ALL_SOURCES)
def test_source_url_reads_the_example_url(source_id):
    column = EXPECTED_URL_COLUMNS[source_id]
    url = f"https://example.test/{source_id}"
    assert investigation.source_url({column: url}, source_id) == url


@pytest.mark.parametrize("source_id", ALL_SOURCES)
def test_source_url_blank_or_missing_is_none(source_id):
    column = EXPECTED_URL_COLUMNS[source_id]
    assert investigation.source_url({}, source_id) is None
    assert investigation.source_url({column: None}, source_id) is None
    assert investigation.source_url({column: "   "}, source_id) is None


def test_example_url_column_none_when_no_url_column(monkeypatch):
    monkeypatch.setattr(
        sources, "required_columns", lambda source_id: ("job_key", "title")
    )
    assert investigation.example_url_column("indeed") is None
    assert investigation.source_url({"job_key": "1"}, "indeed") is None


# --- Representative offer ----------------------------------------------------


def test_select_example_offer_prefers_absent_or_invalid_and_copies():
    offers = [
        {
            "job_key": "1",
            "title": "First",
            "company": "ACME",
            "salary_text": "N/A",
        },
        {
            "job_key": "2",
            "title": "Second",
            "company": None,
            "salary_text": "50000",
        },
    ]

    # company is absent on the second offer.
    example = investigation.select_example_offer(
        offers, "indeed", field="company"
    )
    assert example is not None
    assert example["job_key"] == "2"

    # The result is a copy: mutating it does not touch the input.
    example["title"] = "MUTATED"
    assert offers[1]["title"] == "Second"

    # An invalid value also qualifies (salary text without an amount).
    invalid = investigation.select_example_offer(
        offers, "indeed", field="salary"
    )
    assert invalid["job_key"] == "1"

    # No absent/invalid value: the first offer is used.
    fallback = investigation.select_example_offer(
        offers, "indeed", field="title"
    )
    assert fallback["job_key"] == "1"

    assert investigation.select_example_offer([], "indeed") is None


def test_build_context_exposes_the_example_offer_and_url():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
        },
    )
    result = status.classify_source("indeed", source)

    context = investigation.build_context(
        "indeed",
        result,
        completeness=source,
        offers=_indeed_offers(2),
        field="company",
    )

    assert context is not None
    assert context.example_offer is not None
    assert context.example_url is not None
    assert context.example_url.startswith("https://indeed.example/")


# --- Search dimensions (RF-2, RF-7) ------------------------------------------


def test_build_contexts_fills_search_region_and_city():
    source = _source(
        "linkedin",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("linkedin", source)
    searches = (
        fingerprint.SearchDimension(
            source="indeed", search="other", region="España", city="Madrid"
        ),
        fingerprint.SearchDimension(
            source="linkedin",
            search="data engineer",
            region="España",
            city="Barcelona",
        ),
    )

    (context,) = investigation.build_contexts(
        "linkedin", result, completeness=source, searches=searches
    )

    assert context.search == "data engineer"
    assert context.region == "España"
    assert context.city == "Barcelona"


def test_build_contexts_falls_back_to_the_first_dimension():
    source = _source(
        "devitjobs",
        {
            "title": _field("title", True, 1000),
            "salary": _field("salary", False, 500),
        },
    )
    result = status.classify_source("devitjobs", source)
    searches = (
        fingerprint.SearchDimension(source="other", search="fallback"),
    )

    (context,) = investigation.build_contexts(
        "devitjobs", result, completeness=source, searches=searches
    )

    assert context.search == "fallback"
    assert context.region is None
    assert context.city is None


# --- Local run evidence (RF-9, RF-11) ----------------------------------------


def test_run_evidence_appears_in_the_context():
    result = status.classify_source("linkedin", None)
    run = run_evidence.RunEvidence(
        source="linkedin",
        outcome=run_evidence.OUTCOME_ERROR,
        offers_current_run=None,
        offers_snapshot=None,
        snapshot_stale=False,
        attempt=2,
        attempts=3,
        error="captcha/challenge detected",
        detail="exit=-1",
    )

    (context,) = investigation.build_contexts("linkedin", result, run=run)

    joined = " | ".join(context.evidence)
    assert run.outcome in joined
    assert run.error in joined
    assert run.detail in joined
    assert "intento 2/3" in joined


def test_run_evidence_reports_the_current_run_offers():
    result = status.classify_source("linkedin", None)
    run = run_evidence.RunEvidence(
        source="linkedin",
        outcome=run_evidence.OUTCOME_OK,
        offers_current_run=7,
        offers_snapshot=100,
        snapshot_stale=False,
        attempt=None,
        attempts=None,
        error=None,
        detail=None,
    )

    (context,) = investigation.build_contexts("linkedin", result, run=run)

    assert any(
        "7" in item and "ofertas" in item for item in context.evidence
    )


def test_extra_evidence_is_kept_and_deduplicated():
    result = status.classify_source("linkedin", None)

    (context,) = investigation.build_contexts(
        "linkedin", result, evidence=("marcador ausente", "marcador ausente")
    )

    assert context.evidence == (
        "sin evidencia suficiente para confirmar la fuente",
        "marcador ausente",
    )


# --- Purity and immutability (RF-12) -----------------------------------------


def test_build_contexts_does_not_change_status_completeness_or_offers():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
        },
    )
    result = status.classify_source("indeed", source)
    status_snapshot = dataclasses.asdict(result)
    completeness_snapshot = dataclasses.asdict(source)
    offers = _indeed_offers(2)
    offers_snapshot = [dict(offer) for offer in offers]

    investigation.build_contexts(
        "indeed", result, completeness=source, offers=offers
    )

    assert dataclasses.asdict(result) == status_snapshot
    assert dataclasses.asdict(source) == completeness_snapshot
    assert offers == offers_snapshot


def test_investigation_context_is_frozen():
    context = investigation.InvestigationContext(
        source="indeed",
        display_name="Indeed",
        group="direct",
        trigger=investigation.TRIGGER_SOURCE_FAILED,
        field=None,
        search=None,
        region=None,
        city=None,
        example_url=None,
        example_offer=None,
        metric=None,
        evidence=(),
        rule_reference=None,
    )
    with pytest.raises(Exception):
        context.trigger = investigation.TRIGGER_REQUIRED_FIELD  # type: ignore[misc]


# --- Unconfirmed web investigation (T-36; RF-9, RF-10, RF-11) ----------------


def _context(source_id: str = "indeed") -> investigation.InvestigationContext:
    """Build a minimal investigation context for the web-check tests."""
    source = sources.get_source(source_id)
    return investigation.InvestigationContext(
        source=source_id,
        display_name=source.display_name,
        group=source.group,
        trigger=investigation.TRIGGER_SOURCE_FAILED,
        field=None,
        search=None,
        region=None,
        city=None,
        example_url=None,
        example_offer=None,
        metric=None,
        evidence=(),
        rule_reference=None,
    )


def test_unconfirmed_web_constants_match_the_design():
    assert investigation.INVESTIGATION_CONFIRMED == "confirmed"
    assert investigation.INVESTIGATION_UNCONFIRMED == "unconfirmed"


def test_inaccessible_web_is_unconfirmed():
    outcome = investigation.record_web_check(_context(), accessible=False)
    assert outcome.state == investigation.INVESTIGATION_UNCONFIRMED


def test_inaccessible_web_never_asserts_a_cause():
    outcome = investigation.record_web_check(
        _context(), accessible=False, probable_cause="el selector cambió"
    )
    assert outcome.probable_cause is None


def test_inaccessible_web_has_no_verified_observations():
    outcome = investigation.record_web_check(
        _context(), accessible=False, observations=("parecía otro selector",)
    )
    assert outcome.observations == ()


def test_unconfirmed_web_shortcut_keeps_guidance():
    context = _context()
    outcome = investigation.unconfirmed_web(
        context,
        detail="la web no responde",
        recommendation="actualizar el selector de empresa",
        manual_check="abrir la URL de ejemplo y revisar el bloque de empresa",
    )

    assert outcome.state == investigation.INVESTIGATION_UNCONFIRMED
    assert outcome.context is context
    assert outcome.observations == ()
    assert outcome.probable_cause is None
    assert outcome.detail == "la web no responde"
    assert outcome.recommendation == "actualizar el selector de empresa"
    assert (
        outcome.manual_check
        == "abrir la URL de ejemplo y revisar el bloque de empresa"
    )


def test_accessible_web_keeps_facts_hypothesis_and_recommendation():
    outcome = investigation.record_web_check(
        _context(),
        accessible=True,
        observations=("el título aparece en el h2",),
        probable_cause="el selector cambió de ubicación",
        recommendation="actualizar el parser del título",
        manual_check="comprobar el h2 de la oferta de ejemplo",
    )

    assert outcome.state == investigation.INVESTIGATION_CONFIRMED
    assert outcome.observations == ("el título aparece en el h2",)
    assert outcome.probable_cause == "el selector cambió de ubicación"
    assert outcome.recommendation == "actualizar el parser del título"
    assert outcome.manual_check == "comprobar el h2 de la oferta de ejemplo"


def test_is_unconfirmed_distinguishes_both_states():
    context = _context()
    assert (
        investigation.is_unconfirmed(
            investigation.record_web_check(context, accessible=False)
        )
        is True
    )
    assert (
        investigation.is_unconfirmed(
            investigation.record_web_check(context, accessible=True)
        )
        is False
    )


def test_accessible_web_without_facts_stays_confirmed_with_defaults():
    outcome = investigation.record_web_check(_context(), accessible=True)

    assert outcome.state == investigation.INVESTIGATION_CONFIRMED
    assert outcome.observations == ()
    assert outcome.probable_cause is None
    assert outcome.detail is None
    assert outcome.recommendation is None
    assert outcome.manual_check is None
    assert investigation.is_unconfirmed(outcome) is False


def test_source_state_preserved_true_for_identical_mapping():
    source = _source("indeed", {"title": _field("title", True, 1000)})
    correct = status.classify_source("indeed", source)
    mapping = {"indeed": correct}
    snapshot = dataclasses.asdict(correct)

    assert investigation.source_state_preserved(mapping, mapping) is True
    # A distinct mapping with equal values is also preserved.
    assert investigation.source_state_preserved(mapping, {"indeed": correct}) is True
    # The helper is read-only: it never mutates the status it compares.
    assert dataclasses.asdict(correct) == snapshot


def test_source_state_preserved_false_on_artificial_changes():
    source = _source("indeed", {"title": _field("title", True, 1000)})
    correct = status.classify_source("indeed", source)
    before = {"indeed": correct}

    changed_state = dataclasses.replace(correct, state=status.SOURCE_FAILED)
    assert (
        investigation.source_state_preserved(before, {"indeed": changed_state})
        is False
    )

    changed_failures = dataclasses.replace(correct, failures=("zero offers",))
    assert (
        investigation.source_state_preserved(before, {"indeed": changed_failures})
        is False
    )

    changed_incidents = dataclasses.replace(correct, incidents=("title",))
    assert (
        investigation.source_state_preserved(before, {"indeed": changed_incidents})
        is False
    )

    changed_fields = dataclasses.replace(
        correct, investigation_fields=("salary",)
    )
    assert (
        investigation.source_state_preserved(before, {"indeed": changed_fields})
        is False
    )

    # A different set of source keys is also a change.
    assert investigation.source_state_preserved(before, {}) is False
    assert (
        investigation.source_state_preserved(before, {"linkedin": correct})
        is False
    )


def test_web_check_does_not_change_the_ingestion_state():
    source = _source(
        "indeed",
        {
            "title": _field("title", True, 1000),
            "company": _field("company", True, 850),
        },
    )
    result = status.classify_source("indeed", source)
    offers = _indeed_offers(3)

    before = {"indeed": result}
    status_snapshot = dataclasses.asdict(result)
    completeness_snapshot = dataclasses.asdict(source)
    offers_snapshot = [dict(offer) for offer in offers]

    context = _context()
    investigation.record_web_check(
        context, accessible=False, probable_cause="hipótesis descartada"
    )
    investigation.record_web_check(
        context,
        accessible=True,
        observations=("hecho verificado",),
        probable_cause="causa probable",
    )
    after = {"indeed": result}

    # If any of these had been mutated by the web check, the snapshots captured
    # before it would no longer match.
    assert dataclasses.asdict(result) == status_snapshot
    assert dataclasses.asdict(source) == completeness_snapshot
    assert offers == offers_snapshot
    # The helper corroborates that the ingestion state did not change.
    assert investigation.source_state_preserved(before, after) is True


def test_investigation_outcome_is_frozen():
    outcome = investigation.record_web_check(_context(), accessible=False)
    with pytest.raises(Exception):
        outcome.state = investigation.INVESTIGATION_CONFIRMED  # type: ignore[misc]


# --- Actionable guidance (T-40, RF-11) ---------------------------------------


def _guidance_context(
    trigger: str,
    *,
    source_id: str = "indeed",
    field: str | None = None,
    example_url: str | None = None,
) -> investigation.InvestigationContext:
    """Build a context with a chosen trigger for the guidance tests."""
    source = sources.get_source(source_id)
    return investigation.InvestigationContext(
        source=source_id,
        display_name=source.display_name,
        group=source.group,
        trigger=trigger,
        field=field,
        search="data engineer",
        region="España",
        city="Madrid",
        example_url=example_url,
        example_offer=None,
        metric=None,
        evidence=(),
        rule_reference=None,
    )


_GUIDANCE_CASES = [
    (investigation.TRIGGER_SOURCE_FAILED, None),
    (investigation.TRIGGER_REQUIRED_FIELD, "company"),
    (investigation.TRIGGER_OPTIONAL_FIELD, "salary"),
]


@pytest.mark.parametrize(("trigger", "field"), _GUIDANCE_CASES)
def test_suggest_recommendation_is_non_empty_names_source_and_is_deterministic(
    trigger, field
):
    context = _guidance_context(trigger, field=field)

    text = investigation.suggest_recommendation(context)

    assert text.strip()
    assert "Indeed" in text
    assert text == investigation.suggest_recommendation(context)


@pytest.mark.parametrize(("trigger", "field"), _GUIDANCE_CASES)
def test_suggest_manual_check_with_example_url_mentions_it(trigger, field):
    context = _guidance_context(
        trigger, field=field, example_url="https://indeed.example/1"
    )

    text = investigation.suggest_manual_check(context)

    assert text.strip()
    assert "https://indeed.example/1" in text
    assert text == investigation.suggest_manual_check(context)


def test_suggest_manual_check_without_example_url_stays_actionable():
    context = _guidance_context(investigation.TRIGGER_SOURCE_FAILED)

    text = investigation.suggest_manual_check(context)

    assert text.strip()
    assert "Volver a ejecutar Indeed" in text
    assert "https://" not in text


def test_field_guidance_names_the_affected_field():
    required = _guidance_context(
        investigation.TRIGGER_REQUIRED_FIELD, field="company"
    )
    optional = _guidance_context(
        investigation.TRIGGER_OPTIONAL_FIELD,
        field="salary",
        example_url="https://indeed.example/2",
    )

    assert "empresa (company)" in investigation.suggest_recommendation(required)
    assert "empresa (company)" in investigation.suggest_manual_check(required)
    assert "salario (salary)" in investigation.suggest_recommendation(optional)
    assert "salario (salary)" in investigation.suggest_manual_check(optional)


def test_suggestions_do_not_assert_a_confirmed_cause():
    # The page was never verified, so the guidance must stay as a hypothesis.
    context = _guidance_context(
        investigation.TRIGGER_REQUIRED_FIELD, field="title"
    )

    text = investigation.suggest_recommendation(context)

    assert "causa confirmada" not in text.lower()
