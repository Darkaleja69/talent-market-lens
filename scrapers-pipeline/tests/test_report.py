"""Tests for the Spanish console report (T-38; RF-2, RF-4, RF-6, RF-7, RF-14, RF-15).

No network, Azure or Parquet is involved: the module under test only combines
already-computed dataclasses. The fixtures build those dataclasses directly
(``completeness``, ``publication``, ``trends``, ``status``), mirroring the
existing tests of those modules.
"""
from __future__ import annotations

import re

import pytest

from verification import (
    completeness,
    field_contract,
    investigation,
    landing,
    publication,
    report,
    run_evidence,
    sources,
    status,
    trends,
)


# --- Fixtures ----------------------------------------------------------------


def _field(
    field: str, valid: int, total: int = 100, *, required: bool | None = None
) -> completeness.FieldCompleteness:
    """Build a FieldCompleteness with an exact completeness percentage."""
    if required is None:
        required = field_contract.is_required(field)
    return completeness.FieldCompleteness(
        field=field,
        required=required,
        total=total,
        valid=valid,
        absent=total - valid,
        invalid=0,
        completeness_pct=0.0 if total == 0 else 100.0 * valid / total,
    )


def _source_completeness(
    source: str, fields: dict[str, completeness.FieldCompleteness]
) -> completeness.SourceCompleteness:
    total = max((stats.total for stats in fields.values()), default=0)
    return completeness.SourceCompleteness(
        source=source, total_offers=total, fields=fields
    )


_FIELD_VALUES: dict[str, object] = {
    "title": "Data Engineer",
    "company": "ACME Corp",
    "description": "A real description.",
    "salary": "30000",
    "skills": ["Python"],
    "work_mode": "Remote",
    "location": "Madrid",
    "posted_date": "2026-09-01",
}


def _offer(source_id: str, index: int) -> dict:
    """Build one valid offer using the source's own first alias per field."""
    offer: dict[str, object] = {}
    for field, aliases in sources.field_map(source_id).items():
        if not aliases:
            continue
        if field == "id":
            offer[aliases[0]] = f"{source_id}-{index}"
        else:
            offer[aliases[0]] = _FIELD_VALUES[field]
    return offer


def _measured(source_id: str, count: int = 2) -> completeness.SourceCompleteness:
    return completeness.measure_offers_completeness(
        [_offer(source_id, index) for index in range(count)], source_id
    )


def _correct_status(source_id: str) -> status.SourceStatus:
    return status.classify_source(
        source_id, _source_completeness(source_id, {"title": _field("title", 100)})
    )


def _failed_status(source_id: str) -> status.SourceStatus:
    return status.classify_source(source_id, None)


def _publication(
    source_id: str,
    *,
    obtained: completeness.SourceCompleteness | None,
    published: completeness.SourceCompleteness | None,
    manifest_state: str | None = landing.STATE_OK,
) -> publication.SourcePublication:
    return publication.build_source_publication(
        source=source_id,
        obtained=obtained,
        published=published,
        manifest_state=manifest_state,
    )


# --- 1. Label dictionaries ---------------------------------------------------


def test_state_label_dicts_translate_the_codes():
    assert report.SOURCE_STATE_ES == {"correct": "correcta", "failed": "fallida"}
    assert report.GLOBAL_STATE_ES == {
        "correct": "correcto",
        "partial": "parcial",
        "failed": "fallido",
        "inconclusive": "inconcluso",
    }
    assert report.PUBLICATION_STATE_ES == {
        "ok": "correcta",
        "no_new_offers": "sin ofertas nuevas",
        "empty": "sin ofertas",
        "pending": "pendiente de publicar",
        "mismatch": "discrepancia",
        "rejected": "rechazada",
        "not_checked": "no comprobada",
        "not_applicable": "sin datos que publicar",
    }
    assert report.TREND_DIRECTION_ES == {
        "improved": "mejora",
        "regressed": "retroceso",
        "stable": "estable",
    }


# --- 2. Correct source with obtained/published and trend ---------------------


def test_build_source_report_enriches_fields_with_publication_and_trend():
    obtained = _source_completeness(
        "linkedin",
        {
            "title": _field("title", 100),
            "salary": _field("salary", 50),
        },
    )
    published = _source_completeness(
        "linkedin",
        {
            "title": _field("title", 1, total=1),
            "salary": _field("salary", 0, total=1),
        },
    )
    pub = _publication(
        "linkedin", obtained=obtained, published=published
    )
    trend = trends.SourceTrend(
        source="linkedin",
        runs_used=5,
        fields={
            "title": trends.FieldTrend(
                field="title",
                required=True,
                values=(90.0, 95.0, 100.0),
                direction=trends.DIRECTION_IMPROVED,
                delta=5.0,
            )
        },
    )

    source_report = report.build_source_report(
        "linkedin",
        status=_correct_status("linkedin"),
        publication=pub,
        trend=trend,
        progress=100,
    )

    assert source_report.publication_state == publication.PUBLICATION_OK
    assert source_report.obtained_offers == 100
    assert source_report.delta_offers == 1
    title = next(f for f in source_report.fields if f.field == "title")
    assert title.published_pct == 100.0
    assert title.difference == 0.0
    assert title.published_valid == 1
    assert title.published_total == 1
    salary = next(f for f in source_report.fields if f.field == "salary")
    assert salary.published_pct == 0.0
    assert salary.difference == -50.0
    assert salary.published_valid == 0
    assert salary.published_total == 1

    text = report.render_source_report(source_report)

    assert "LinkedIn" in text
    assert "Estado: correcta" in text
    assert "100/100" in text
    assert "100.0%" in text
    assert "publicado 1/1 (100.0%)" in text
    assert "publicado 0/1 (0.0%)" in text
    assert "mejora" in text
    assert "5 ejecuciones" in text
    assert "Progreso (ofertas parseadas): 100" in text


# --- 3. Failed source with a watchdog stop and progress ----------------------


def test_render_source_report_shows_watchdog_stop_progress_and_failures():
    source_report = report.build_source_report(
        "indeed",
        status=_failed_status("indeed"),
        run=run_evidence.RunEvidence(
            source="indeed",
            outcome=run_evidence.OUTCOME_ERROR,
            offers_current_run=120,
            offers_snapshot=None,
            snapshot_stale=False,
            attempt=1,
            attempts=2,
            error="timeout",
            detail="last attempt stopped",
        ),
        progress=120,
        stop_reason="watchdog_no_progress",
    )

    text = report.render_source_report(source_report)

    assert "Detención: bloqueo por falta de progreso (watchdog_no_progress)" in text
    assert "Progreso (ofertas parseadas): 120" in text
    assert "Ofertas capturadas en la ejecución: 120" in text
    assert "Motivos de fallo:" in text
    assert "sin evidencia suficiente para confirmar la fuente" in text
    assert "error registrado: timeout" in text


# --- 4. All nine sources through render_report -------------------------------


def test_render_report_includes_every_source_with_counts_state_and_progress():
    source_reports: list[report.SourceReport] = []
    statuses: dict[str, status.SourceStatus] = {}
    for index, source in enumerate(sources.all_sources()):
        obtained = _measured(source.id)
        pub = _publication(source.id, obtained=obtained, published=obtained)
        source_status = status.classify_source(source.id, obtained)
        statuses[source.id] = source_status
        source_reports.append(
            report.build_source_report(
                source.id,
                status=source_status,
                publication=pub,
                progress=index + 1,
            )
        )

    diagnostic = report.build_report(
        sources=source_reports,
        global_status=status.classify_global(statuses),
    )
    text = report.render_report(diagnostic)

    names = [source.display_name for source in sources.all_sources()]
    for name in names:
        assert name in text

    blocks: dict[str, str] = {}
    for chunk in text.split("----------------------------------------"):
        stripped = chunk.strip()
        for source in sources.all_sources():
            if stripped.startswith(source.display_name + " ("):
                blocks[source.display_name] = stripped

    assert set(blocks) == set(names)
    for block in blocks.values():
        assert "Estado: correcta" in block
        assert "%" in block
        assert re.search(r"\d+/\d+", block)
        assert "Progreso (ofertas parseadas):" in block


# --- 5. Global outcomes ------------------------------------------------------


def _sources_for_correct(count: int = 2) -> list[report.SourceReport]:
    return [
        report.build_source_report(source.id, status=_correct_status(source.id))
        for source in sources.all_sources()[:count]
    ]


def test_global_correct_header_shows_correcto():
    mapped = {
        source.id: _correct_status(source.id)
        for source in sources.all_sources()[:3]
    }
    diagnostic = report.build_report(
        sources=_sources_for_correct(3),
        global_status=status.classify_global(mapped),
    )

    text = report.render_report(diagnostic)

    assert "Estado global: correcto" in text
    assert "correctas 3/3" in text
    assert "fallidas 0/3" in text


def test_global_partial_header_shows_parcial():
    mapped = {
        "indeed": _correct_status("indeed"),
        "linkedin": _failed_status("linkedin"),
    }
    diagnostic = report.build_report(
        sources=[
            report.build_source_report("indeed", status=mapped["indeed"]),
            report.build_source_report("linkedin", status=mapped["linkedin"]),
        ],
        global_status=status.classify_global(mapped),
    )

    text = report.render_report(diagnostic)

    assert "Estado global: parcial" in text
    assert "correctas 1/2" in text
    assert "fallidas 1/2" in text


def test_global_failed_header_shows_fallido():
    mapped = {
        "indeed": _failed_status("indeed"),
        "glassdoor": _failed_status("glassdoor"),
    }
    diagnostic = report.build_report(
        sources=[
            report.build_source_report("indeed", status=mapped["indeed"]),
            report.build_source_report("glassdoor", status=mapped["glassdoor"]),
        ],
        global_status=status.classify_global(mapped),
    )

    text = report.render_report(diagnostic)

    assert "Estado global: fallido" in text
    assert "correctas 0/2" in text
    assert "fallidas 2/2" in text


def test_global_inconclusive_shows_reason_and_no_source_blocks():
    diagnostic = report.build_report(
        sources=(),
        global_status=status.classify_global(
            {}, analyzable=False, inconclusive_reason="la ejecución no ha terminado"
        ),
    )

    text = report.render_report(diagnostic)

    assert "Estado global: inconcluso" in text
    assert "la ejecución no ha terminado" in text
    for source in sources.all_sources():
        assert source.display_name not in text
    assert "correcta" not in text
    assert "fallida" not in text


# --- 6. Publication state translations ---------------------------------------


@pytest.mark.parametrize(
    ("manifest_state", "published", "expected_state", "expected_text"),
    [
        (
            landing.STATE_PENDING,
            None,
            publication.PUBLICATION_PENDING,
            "pendiente de publicar",
        ),
        (
            landing.STATE_OK,
            "empty",
            publication.PUBLICATION_NO_NEW_OFFERS,
            "sin ofertas nuevas",
        ),
        (
            landing.STATE_OK,
            "empty_obtained",
            publication.PUBLICATION_EMPTY,
            "sin ofertas",
        ),
    ],
)
def test_publication_states_are_translated(
    manifest_state, published, expected_state, expected_text
):
    obtained = _source_completeness("indeed", {"title": _field("title", 100)})
    if published == "empty":
        published_stage = completeness.measure_offers_completeness([], "indeed")
    elif published == "empty_obtained":
        obtained = completeness.measure_offers_completeness([], "indeed")
        published_stage = completeness.measure_offers_completeness([], "indeed")
    else:
        published_stage = None

    pub = _publication(
        "indeed",
        obtained=obtained,
        published=published_stage,
        manifest_state=manifest_state,
    )
    assert pub.stage.state == expected_state

    source_report = report.build_source_report(
        "indeed", status=_correct_status("indeed"), publication=pub
    )
    text = report.render_source_report(source_report)

    assert expected_text in text


def test_not_applicable_publication_is_rendered_in_spanish():
    # A source that prepared nothing to publish is reported as "sin datos que
    # publicar", never as a pending upload (T-55, RF-8).
    pub = publication.build_source_publication(
        source="infojobs",
        obtained=None,
        published=None,
        not_applicable=True,
    )

    source_report = report.build_source_report(
        "infojobs", status=_correct_status("infojobs"), publication=pub
    )
    text = report.render_source_report(source_report)

    assert source_report.publication_state == publication.PUBLICATION_NOT_APPLICABLE
    assert "sin datos que publicar" in text
    assert "pendiente de publicar" not in text


# --- 7. Trend note and direction ---------------------------------------------


def test_render_report_includes_the_no_history_note():
    mapped = {"indeed": _correct_status("indeed")}
    diagnostic = report.build_report(
        sources=[report.build_source_report("indeed", status=mapped["indeed"])],
        global_status=status.classify_global(mapped),
        trend_note=trends.NO_HISTORY_NOTE,
        runs_used=1,
    )

    text = report.render_report(diagnostic)

    assert trends.NO_HISTORY_NOTE in text
    assert "Ejecuciones usadas para la tendencia: 1" in text


def test_render_source_report_shows_the_spanish_trend_direction():
    trend = trends.SourceTrend(
        source="indeed",
        runs_used=3,
        fields={
            "salary": trends.FieldTrend(
                field="salary",
                required=False,
                values=(60.0, 55.0, 50.0),
                direction=trends.DIRECTION_REGRESSED,
                delta=-5.0,
            )
        },
    )
    source_report = report.build_source_report(
        "indeed", status=_correct_status("indeed"), trend=trend
    )

    text = report.render_source_report(source_report)

    assert "retroceso" in text
    assert "3 ejecuciones" in text


# --- 8. Missing pieces never break the report --------------------------------


def test_build_and_render_tolerate_missing_pieces():
    source_report = report.build_source_report(
        "indeed",
        status=_failed_status("indeed"),
        run=None,
        publication=None,
        trend=None,
    )

    text = report.render_source_report(source_report)

    assert "Indeed" in text
    assert "Estado: fallida" in text
    assert "Completitud por campo: sin medición" in text
    assert "Publicación:" not in text
    assert "Tendencia" not in text


# --- 9. Percentages always come with their counts ----------------------------


def test_every_percentage_in_the_report_has_its_counts():
    source_reports: list[report.SourceReport] = []
    mapped: dict[str, status.SourceStatus] = {}
    for source in sources.all_sources()[:3]:
        obtained = _source_completeness(
            source.id,
            {
                "title": _field("title", 90),
                "salary": _field("salary", 50),
            },
        )
        pub = _publication(source.id, obtained=obtained, published=obtained)
        source_status = status.classify_source(source.id, obtained)
        mapped[source.id] = source_status
        trend = trends.SourceTrend(
            source=source.id,
            runs_used=3,
            fields={
                "title": trends.FieldTrend(
                    field="title",
                    required=True,
                    values=(80.0, 90.0, 90.0),
                    direction=trends.DIRECTION_IMPROVED,
                    delta=0.0,
                    counts=((80, 100), (90, 100), (90, 100)),
                )
            },
        )
        source_reports.append(
            report.build_source_report(
                source.id,
                status=source_status,
                publication=pub,
                trend=trend,
            )
        )

    diagnostic = report.build_report(
        sources=source_reports,
        global_status=status.classify_global(mapped),
    )
    text = report.render_report(diagnostic)

    lines_with_pct = [line for line in text.splitlines() if "%" in line]
    assert lines_with_pct, "el informe debía mostrar algún porcentaje"
    for line in lines_with_pct:
        assert re.search(r"\d+/\d+", line), line

    # The published comparison and the trend carry their counts too.
    assert "publicado 90/100 (90.0%)" in text
    assert "publicado 50/100 (50.0%)" in text
    assert "80/100 (80.0%) -> 90/100 (90.0%) -> 90/100 (90.0%)" in text


def test_published_counts_absent_are_not_invented():
    field_report = report.FieldReport(
        field="salary",
        required=False,
        total=100,
        valid=50,
        absent=50,
        invalid=0,
        completeness_pct=50.0,
        published_pct=40.0,
        difference=-10.0,
        published_valid=None,
        published_total=None,
    )

    text = report._render_field(field_report)

    assert "50/100 (50.0%)" in text
    assert "publicado" not in text
    assert "diferencia -10.0 pp" in text


def test_trend_without_counts_falls_back_to_plain_percentages():
    field_trend = trends.FieldTrend(
        field="title",
        required=True,
        values=(90.0, 100.0),
        direction=trends.DIRECTION_IMPROVED,
        delta=10.0,
    )

    series = report._trend_series(field_trend)

    assert series == "90.0 -> 100.0"
    assert "%" not in series


def test_trend_positional_construction_keeps_empty_counts():
    # Backward compatibility: a FieldTrend built with five arguments still
    # works and simply carries no counts.
    field_trend = trends.FieldTrend(
        "title", True, (90.0, 100.0), trends.DIRECTION_IMPROVED, 10.0
    )

    assert field_trend.counts == ()
    assert report._trend_series(field_trend) == "90.0 -> 100.0"


# --- 10. Immutability --------------------------------------------------------


def test_source_report_is_frozen():
    source_report = report.build_source_report(
        "indeed", status=_correct_status("indeed")
    )
    with pytest.raises(Exception):
        source_report.state = status.SOURCE_FAILED  # type: ignore[misc]


def test_diagnostic_report_is_frozen():
    mapped = {"indeed": _correct_status("indeed")}
    diagnostic = report.build_report(
        sources=[report.build_source_report("indeed", status=mapped["indeed"])],
        global_status=status.classify_global(mapped),
    )
    with pytest.raises(Exception):
        diagnostic.global_state = status.GLOBAL_FAILED  # type: ignore[misc]


def test_field_report_is_frozen():
    field_report = report.FieldReport(
        field="title",
        required=True,
        total=1,
        valid=1,
        absent=0,
        invalid=0,
        completeness_pct=100.0,
        published_pct=None,
        difference=None,
    )
    with pytest.raises(Exception):
        field_report.valid = 0  # type: ignore[misc]


# --- 11. Web investigation in the report (T-39; RF-9, RF-10, RF-11) ----------


def _investigation_context(
    source_id: str = "indeed",
    *,
    field: str | None = "company",
    search: str | None = "data engineer",
    region: str | None = "España",
    city: str | None = "Madrid",
    metric: str | None = "válidos 850/1000 (85.0%)",
    example_url: str | None = "https://indeed.example/1",
    rule_reference: str | None = "campo 'company'; alias de origen: company",
    evidence: tuple[str, ...] = ("cero ofertas capturadas en la ejecución",),
) -> investigation.InvestigationContext:
    """Build a fully-populated investigation context for the report tests."""
    source = sources.get_source(source_id)
    return investigation.InvestigationContext(
        source=source_id,
        display_name=source.display_name,
        group=source.group,
        trigger=investigation.TRIGGER_REQUIRED_FIELD,
        field=field,
        search=search,
        region=region,
        city=city,
        example_url=example_url,
        example_offer=None,
        metric=metric,
        evidence=evidence,
        rule_reference=rule_reference,
    )


def _section(text: str, start: str, end: str | None = None) -> str:
    """Return the text between two section headers (exclusive)."""
    segment = text.split(start, 1)[1]
    if end is not None:
        segment = segment.split(end, 1)[0]
    return segment


def test_investigation_state_labels_are_in_spanish():
    assert report.INVESTIGATION_STATE_ES == {
        "confirmed": "confirmada",
        "unconfirmed": "no concluida",
    }


def test_unconfirmed_investigation_declares_no_verification_and_keeps_state():
    outcome = investigation.unconfirmed_web(
        _investigation_context(),
        detail="la web no responde",
        recommendation="actualizar el selector de empresa",
        manual_check="abrir la URL de ejemplo y revisar el bloque de empresa",
    )

    text = report.render_investigation(outcome)

    assert "no concluida" in text
    assert "no se pudo verificar" in text
    assert "sin concluir" in text
    assert "conserva su estado" in text
    assert "Detalle: la web no responde" in text
    # No fact and no cause are asserted.
    assert "sin hechos verificados" in text
    assert "no se afirma ninguna causa" in text


def test_unconfirmed_investigation_discards_a_supplied_cause():
    # Even if a cause is handed in, an inaccessible page discards it and it
    # must never appear as if it had been verified.
    outcome = investigation.record_web_check(
        _investigation_context(),
        accessible=False,
        probable_cause="el selector de empresa cambió",
    )

    text = report.render_investigation(outcome)

    assert "el selector de empresa cambió" not in text


def test_hand_built_unconfirmed_outcome_never_shows_facts_or_cause():
    # Defense in depth: an outcome built by hand (skipping record_web_check)
    # with facts and a cause must still render them as unverified, never as
    # observed (RF-9, RF-10, RF-11).
    outcome = investigation.InvestigationOutcome(
        context=_investigation_context(),
        state=investigation.INVESTIGATION_UNCONFIRMED,
        observations=("hecho inventado",),
        probable_cause="causa inventada",
        recommendation="actualizar el parser de empresa",
        manual_check="abrir la URL de ejemplo",
        detail="la web no responde",
    )

    text = report.render_investigation(outcome)

    assert "hecho inventado" not in text
    assert "causa inventada" not in text
    assert "sin hechos verificados: la web no se pudo verificar" in text
    assert "no se afirma ninguna causa: la web no se pudo verificar" in text
    # The guidance and the detail are still shown.
    assert "actualizar el parser de empresa" in text
    assert "abrir la URL de ejemplo" in text
    assert "Detalle: la web no responde" in text


def test_record_web_check_inaccessible_drops_supplied_observations():
    # T-36 guard: observations passed to an inaccessible check never surface.
    outcome = investigation.record_web_check(
        _investigation_context(),
        accessible=False,
        observations=("parecía otro selector",),
    )

    text = report.render_investigation(outcome)

    assert "parecía otro selector" not in text
    assert "sin hechos verificados: la web no se pudo verificar" in text


def test_confirmed_investigation_separates_facts_from_hypothesis():
    outcome = investigation.record_web_check(
        _investigation_context(),
        accessible=True,
        observations=("el nombre de la empresa aparece en un div distinto",),
        probable_cause="el selector de empresa cambió de ubicación",
        recommendation="actualizar el parser de empresa",
        manual_check="abrir la URL de ejemplo y comprobar el div de empresa",
    )

    text = report.render_investigation(outcome)

    assert "Hechos observados:" in text
    assert "Causa probable (hipótesis):" in text
    assert "Cambio recomendado:" in text
    assert "Comprobación manual:" in text

    facts = _section(
        text, "Hechos observados:", "Causa probable (hipótesis):"
    )
    assert "el nombre de la empresa aparece en un div distinto" in facts
    assert "el selector de empresa cambió de ubicación" not in facts

    cause = _section(
        text, "Causa probable (hipótesis):", "Cambio recomendado:"
    )
    assert "el selector de empresa cambió de ubicación" in cause
    assert "actualizar el parser de empresa" not in cause

    recommendation = _section(
        text, "Cambio recomendado:", "Comprobación manual:"
    )
    assert "actualizar el parser de empresa" in recommendation
    assert "abrir la URL de ejemplo y comprobar el div de empresa" not in recommendation

    manual = _section(text, "Comprobación manual:")
    assert "abrir la URL de ejemplo y comprobar el div de empresa" in manual


def test_confirmed_investigation_with_missing_pieces_says_so():
    outcome = investigation.record_web_check(
        _investigation_context(field=None, metric=None, rule_reference=None),
        accessible=True,
    )

    text = report.render_investigation(outcome)

    assert "Estado de la investigación: confirmada (confirmed)" in text
    assert "no se registraron hechos observados" in text
    assert "no se afirma ninguna causa" in text
    assert "sin cambio recomendado" in text
    assert "sin comprobación manual indicada" in text


def test_investigation_context_shows_source_search_region_field_metric_url_and_evidence():
    text = report.render_investigation(
        investigation.record_web_check(_investigation_context(), accessible=True)
    )

    assert "Fuente: Indeed (directa)" in text
    assert "Búsqueda: data engineer" in text
    assert "Región: España" in text
    assert "Ciudad: Madrid" in text
    assert "Campo afectado: empresa (company)" in text
    assert "Métrica: válidos 850/1000 (85.0%)" in text
    assert "URL de ejemplo: https://indeed.example/1" in text
    assert "Regla/parser: campo 'company'; alias de origen: company" in text
    assert "Evidencia local:" in text
    assert "cero ofertas capturadas en la ejecución" in text


def test_render_investigations_produces_one_block_per_outcome_in_order():
    first = investigation.record_web_check(
        _investigation_context("indeed"), accessible=True
    )
    second = investigation.record_web_check(
        _investigation_context("linkedin", field="title"), accessible=True
    )

    text = report.render_investigations((first, second))

    assert text.count("Fuente:") == 2
    assert text.index("Fuente: Indeed") < text.index("Fuente: LinkedIn")

    assert report.render_investigations(()) == ""


def test_render_report_includes_investigation_section_only_when_present():
    mapped = {"indeed": _failed_status("indeed")}
    source_reports = [
        report.build_source_report("indeed", status=mapped["indeed"])
    ]
    outcome = investigation.record_web_check(
        _investigation_context("indeed"), accessible=True
    )

    without = report.build_report(
        sources=source_reports,
        global_status=status.classify_global(mapped),
    )
    assert "Investigación de la web real" not in report.render_report(without)

    with_investigations = report.build_report(
        sources=source_reports,
        global_status=status.classify_global(mapped),
        investigations=(outcome,),
    )
    text = report.render_report(with_investigations)

    assert "=== Investigación de la web real ===" in text
    assert "Fuente: Indeed (directa)" in text
    # The rest of the report format is intact.
    assert "Estado global: fallido" in text
    assert "Motivos de fallo:" in text


def test_render_report_includes_investigations_when_inconclusive():
    outcome = investigation.record_web_check(
        _investigation_context("indeed"), accessible=True
    )
    diagnostic = report.build_report(
        sources=(),
        global_status=status.classify_global(
            {}, analyzable=False, inconclusive_reason="no hay ejecución"
        ),
        investigations=(outcome,),
    )

    text = report.render_report(diagnostic)

    assert "Estado global: inconcluso" in text
    assert "=== Investigación de la web real ===" in text


def test_diagnostic_report_without_investigations_stays_compatible():
    mapped = {"indeed": _correct_status("indeed")}
    diagnostic = report.DiagnosticReport(
        global_state=status.GLOBAL_CORRECT,
        global_detail=None,
        sources=(
            report.build_source_report("indeed", status=mapped["indeed"]),
        ),
        trend_note=None,
        runs_used=0,
    )

    assert diagnostic.investigations == ()
    with pytest.raises(Exception):
        diagnostic.investigations = ()  # type: ignore[misc]


def test_build_report_defaults_investigations_to_empty_tuple():
    mapped = {"indeed": _correct_status("indeed")}
    diagnostic = report.build_report(
        sources=[report.build_source_report("indeed", status=mapped["indeed"])],
        global_status=status.classify_global(mapped),
    )

    assert diagnostic.investigations == ()
