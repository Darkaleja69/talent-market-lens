"""Console report of the daily-run diagnostic in Spanish (T-38).

This module turns the pieces already computed by the other verification
modules into a plain-text report a junior data engineer can read (RF-2, RF-4,
RF-6, RF-7, RF-14, RF-15):

- the per-source state and the global state (:mod:`verification.status`);
- the per-field completeness with counts and percentages
  (:mod:`verification.completeness`);
- the obtained vs. published comparison (:mod:`verification.publication`);
- the completeness trend (:mod:`verification.trends`);
- the run evidence and the watchdog progress/stop (:mod:`verification.run_evidence`,
  :mod:`verification.progress`).

Design rules:

- The module is **pure**: it only combines already-computed dataclasses and
  renders text. It does no network, Azure, Parquet or subprocess work, and it
  never mutates the pieces it receives (RF-12).
- Missing pieces (``None`` or empty) never raise and simply add no line.
- Every completeness percentage is rendered next to its counts, so the numbers
  stay verifiable (NFR).
- Identifiers and comments are in English; the rendered text is in Spanish
  (constitution #6, NFR).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from verification import (
    publication as publication_module,
    run_evidence,
    sources,
    status as status_module,
    trends,
)

# Spanish labels for the per-source state (RF-14). Public so a later report
# layer (T-39) can reuse them without re-deriving the translation.
SOURCE_STATE_ES: dict[str, str] = {
    status_module.SOURCE_CORRECT: "correcta",
    status_module.SOURCE_FAILED: "fallida",
}

# Spanish labels for the global state (RF-14).
GLOBAL_STATE_ES: dict[str, str] = {
    status_module.GLOBAL_CORRECT: "correcto",
    status_module.GLOBAL_PARTIAL: "parcial",
    status_module.GLOBAL_FAILED: "fallido",
    status_module.GLOBAL_INCONCLUSIVE: "inconcluso",
}

# Spanish labels for the publication stage (RF-6, RF-8).
PUBLICATION_STATE_ES: dict[str, str] = {
    publication_module.PUBLICATION_OK: "correcta",
    publication_module.PUBLICATION_NO_NEW_OFFERS: "sin ofertas nuevas",
    publication_module.PUBLICATION_EMPTY: "sin ofertas",
    publication_module.PUBLICATION_PENDING: "pendiente de publicar",
    publication_module.PUBLICATION_MISMATCH: "discrepancia",
    publication_module.PUBLICATION_REJECTED: "rechazada",
    publication_module.PUBLICATION_NOT_CHECKED: "no comprobada",
}

# Spanish labels for the trend direction (RF-7).
TREND_DIRECTION_ES: dict[str, str] = {
    trends.DIRECTION_IMPROVED: "mejora",
    trends.DIRECTION_REGRESSED: "retroceso",
    trends.DIRECTION_STABLE: "estable",
}

# Spanish names of the canonical fields, so the report reads naturally (RF-4).
FIELD_ES: dict[str, str] = {
    "id": "identificador",
    "title": "título",
    "company": "empresa",
    "description": "descripción",
    "salary": "salario",
    "skills": "skills",
    "work_mode": "modalidad",
    "location": "ubicación",
    "posted_date": "fecha de publicación",
}

# Spanish names of the source groups.
GROUP_ES: dict[str, str] = {
    "direct": "directa",
    "multi_site": "Multi-site",
}

# Spanish labels for the local run outcomes (RF-15).
RUN_OUTCOME_ES: dict[str, str] = {
    run_evidence.OUTCOME_OK: "correcto",
    run_evidence.OUTCOME_EMPTY: "sin ofertas",
    run_evidence.OUTCOME_ERROR: "error técnico",
    run_evidence.OUTCOME_BLOCKED: "bloqueado",
    run_evidence.OUTCOME_NO_EVIDENCE: "sin evidencia",
}

# Spanish explanation of a watchdog stop (RF-15). The reason code itself is
# defined by the supervisor (``verification.supervisor.WATCHDOG_REASON``).
STOP_REASON_ES: dict[str, str] = {
    "watchdog_no_progress": "bloqueo por falta de progreso",
}

# status.py failure reasons (code-facing English) matched exactly to translate
# them for the person (RF-11). Their own numbers are intentionally dropped: the
# affected field is rendered with its counts and percentage in the field block.
_ZERO_OFFERS = "zero offers"
_MISSING_EVIDENCE = "missing evidence"
_STRUCTURAL_FAILURE = "structural failure"
_REQUIRED_REASON_RE = re.compile(
    r"required field '(?P<field>.+)' at [\d.]+% \(<90%\)"
)


@dataclass(frozen=True)
class FieldReport:
    """One canonical field as shown in the report.

    ``valid``/``total`` are the counts behind ``completeness_pct`` of the
    obtained stage. ``published_valid``/``published_total`` and
    ``published_pct`` are only set when the published stage was measured and
    shared the field (RF-6); ``difference`` is ``published_pct - obtained_pct``
    in percentage points. The published counts stay ``None`` when the stage did
    not measure them, so the renderer never invents them.
    """

    field: str
    required: bool
    total: int
    valid: int
    absent: int
    invalid: int
    completeness_pct: float
    published_pct: float | None
    difference: float | None
    published_valid: int | None = None
    published_total: int | None = None


@dataclass(frozen=True)
class SourceReport:
    """Everything the report shows about one independent source (RF-2).

    ``state`` is the per-source state code (correct/failed); ``failures`` and
    ``incidents`` come from :class:`verification.status.SourceStatus` and stay
    in their code-facing English form so the renderer translates them.
    ``trend`` is the full per-field trend of the source, if any.
    """

    source: str
    display_name: str
    group: str
    state: str
    failures: tuple[str, ...]
    incidents: tuple[str, ...]
    offers_current_run: int | None
    total_offers: int | None
    fields: tuple[FieldReport, ...]
    publication_state: str | None
    obtained_offers: int | None
    delta_offers: int | None
    trend: trends.SourceTrend | None
    progress: int | None
    stop_reason: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class DiagnosticReport:
    """Whole-run report: global state plus one block per source (RF-14).

    ``global_detail`` carries the inconclusive reason (Spanish) when the run
    could not be analysed (RF-13); ``trend_note`` explains an absent or partial
    trend and ``runs_used`` is how many comparable runs backed it (RF-7).
    """

    global_state: str
    global_detail: str | None
    sources: tuple[SourceReport, ...]
    trend_note: str | None
    runs_used: int


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate strings preserving their first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _build_field_reports(
    publication: publication_module.SourcePublication,
) -> tuple[FieldReport, ...]:
    """Build the per-field report from the obtained/published stages (RF-4, RF-6).

    Only the fields measured in the obtained stage are listed, in the order the
    stage measured them. A field also measured in the published stage is
    enriched with its published counts, percentage and difference; the rest
    keep ``None`` so no comparison is invented.
    """
    obtained = publication.obtained
    if obtained is None:
        return ()

    comparisons: dict[str, publication_module.FieldComparison] = {}
    if publication.published is not None:
        for field_comparison in publication_module.compare_completeness(
            obtained, publication.published
        ):
            comparisons[field_comparison.field] = field_comparison

    published_fields = (
        publication.published.fields
        if publication.published is not None
        else {}
    )
    reports: list[FieldReport] = []
    for field, stats in obtained.fields.items():
        shared_comparison = comparisons.get(field)
        published_stats = published_fields.get(field)
        reports.append(
            FieldReport(
                field=field,
                required=stats.required,
                total=stats.total,
                valid=stats.valid,
                absent=stats.absent,
                invalid=stats.invalid,
                completeness_pct=stats.completeness_pct,
                published_pct=(
                    shared_comparison.published_pct
                    if shared_comparison is not None
                    else None
                ),
                difference=(
                    shared_comparison.difference
                    if shared_comparison is not None
                    else None
                ),
                published_valid=(
                    published_stats.valid
                    if published_stats is not None
                    else None
                ),
                published_total=(
                    published_stats.total
                    if published_stats is not None
                    else None
                ),
            )
        )
    return tuple(reports)


def _run_evidence_items(
    run: run_evidence.RunEvidence | None,
) -> tuple[str, ...]:
    """Translate the run outcome/error/detail into Spanish evidence lines."""
    if run is None:
        return ()
    outcome = RUN_OUTCOME_ES.get(run.outcome, run.outcome)
    items = [f"resultado de la ejecución: {outcome} ({run.outcome})"]
    if run.error:
        items.append(f"error registrado: {run.error}")
    if run.detail:
        items.append(f"detalle: {run.detail}")
    if run.attempt is not None and run.attempts is not None:
        items.append(f"intento {run.attempt}/{run.attempts}")
    return tuple(items)


def build_source_report(
    source_id: str,
    *,
    status: status_module.SourceStatus,
    run: run_evidence.RunEvidence | None = None,
    publication: publication_module.SourcePublication | None = None,
    trend: trends.SourceTrend | None = None,
    progress: int | None = None,
    stop_reason: str | None = None,
    evidence: Sequence[str] = (),
) -> SourceReport:
    """Combine the already-computed pieces of one source into a report (RF-2).

    ``fields`` comes from ``publication.obtained`` when present, enriched with
    ``publication.published``/``compare_completeness``; without a publication
    there is no field measurement, so ``fields`` is empty. ``progress`` is the
    accumulated offers counter of the run; when not given it falls back to the
    run's own counter, so the progress evidence is never lost. The inputs are
    only read, never mutated.
    """
    source = sources.get_source(source_id)
    fields = (
        _build_field_reports(publication)
        if publication is not None
        else ()
    )
    total_offers = (
        publication.obtained.total_offers
        if publication is not None and publication.obtained is not None
        else None
    )
    if progress is None and run is not None:
        progress = run.offers_current_run

    extra = [str(item) for item in evidence if str(item).strip()]
    evidence_items = _dedupe([*extra, *_run_evidence_items(run)])

    return SourceReport(
        source=source_id,
        display_name=source.display_name,
        group=source.group,
        state=status.state,
        failures=tuple(status.failures),
        incidents=tuple(status.incidents),
        offers_current_run=(
            run.offers_current_run if run is not None else None
        ),
        total_offers=total_offers,
        fields=fields,
        publication_state=(
            publication.stage.state if publication is not None else None
        ),
        obtained_offers=(
            publication.stage.obtained_offers
            if publication is not None
            else None
        ),
        delta_offers=(
            publication.stage.delta_offers
            if publication is not None
            else None
        ),
        trend=trend,
        progress=progress,
        stop_reason=stop_reason,
        evidence=evidence_items,
    )


def build_report(
    *,
    sources: Sequence[SourceReport],
    global_status: status_module.GlobalStatus,
    trend_note: str | None = None,
    runs_used: int = 0,
) -> DiagnosticReport:
    """Wrap the source reports and the global outcome into one report (RF-14).

    The received source order is preserved. ``global_detail`` is the
    inconclusive reason when the run could not be analysed (RF-13).
    """
    return DiagnosticReport(
        global_state=global_status.state,
        global_detail=global_status.inconclusive_reason,
        sources=tuple(sources),
        trend_note=trend_note,
        runs_used=runs_used,
    )


def _failure_reason_es(reason: str) -> str:
    """Translate a ``status`` failure reason into a Spanish sentence."""
    if reason == _ZERO_OFFERS:
        return "cero ofertas capturadas en la ejecución"
    if reason == _MISSING_EVIDENCE:
        return "sin evidencia suficiente para confirmar la fuente"
    if reason.startswith(_STRUCTURAL_FAILURE):
        detail = reason[len(_STRUCTURAL_FAILURE):].lstrip(": ").strip()
        return f"fallo estructural: {detail}" if detail else "fallo estructural"
    match = _REQUIRED_REASON_RE.match(reason)
    if match:
        return (
            f"campo obligatorio '{match.group('field')}' por debajo del umbral"
        )
    return reason


def _counts_and_pct(valid: int, total: int, pct: float) -> str:
    """Render a percentage *with* its counts, so it is verifiable (NFR)."""
    return f"{valid}/{total} ({pct:.1f}%)"


def _render_field(field_report: FieldReport) -> str:
    """Render one field line: counts, percentage and optional comparison."""
    name = FIELD_ES.get(field_report.field, field_report.field)
    marker = " [obligatorio]" if field_report.required else ""
    parts = [
        f"{name}{marker}: "
        f"{_counts_and_pct(field_report.valid, field_report.total, field_report.completeness_pct)}"
    ]
    if (
        field_report.published_pct is not None
        and field_report.published_valid is not None
        and field_report.published_total is not None
    ):
        parts.append(
            "publicado "
            + _counts_and_pct(
                field_report.published_valid,
                field_report.published_total,
                field_report.published_pct,
            )
        )
    if field_report.difference is not None:
        parts.append(f"diferencia {field_report.difference:+.1f} pp")
    return "    - " + " | ".join(parts)


def _trend_series(field_trend: trends.FieldTrend) -> str:
    """Render the trend values, with their counts when the series carries them.

    Each percentage is shown as ``valid/total (pct%)`` so it stays verifiable
    (NFR). When the trend has no counts (``counts`` empty or not aligned with
    ``values``), it falls back to plain percentages without the ``%`` sign, as
    those historical counts are not available.
    """
    has_counts = (
        bool(field_trend.counts)
        and len(field_trend.counts) == len(field_trend.values)
    )
    if has_counts:
        return " -> ".join(
            _counts_and_pct(valid, total, pct)
            for (valid, total), pct in zip(
                field_trend.counts, field_trend.values
            )
        )
    return " -> ".join(f"{value:.1f}" for value in field_trend.values)


def _render_trend(trend: trends.SourceTrend) -> list[str]:
    """Render the per-field trend of one source (RF-7)."""
    lines = [f"  Tendencia ({trend.runs_used} ejecuciones):"]
    if not trend.fields:
        lines.append("    - sin campos comparables")
        return lines
    for field, field_trend in trend.fields.items():
        name = FIELD_ES.get(field, field)
        direction = TREND_DIRECTION_ES.get(
            field_trend.direction, field_trend.direction
        )
        series = _trend_series(field_trend)
        lines.append(
            f"    - {name}: {direction} "
            f"({field_trend.delta:+.1f} pp) [{series}]"
        )
    return lines


def render_source_report(source_report: SourceReport) -> str:
    """Render one source block as Spanish console text.

    The block always shows the visible name and group, the state, the run
    evidence, the counts/percentages of every measured field, the publication
    state, the trend when available, and the progress counter or the stop
    reason. Missing pieces simply add no line.
    """
    lines: list[str] = []
    group = GROUP_ES.get(source_report.group, source_report.group)
    lines.append(f"{source_report.display_name} ({group})")
    state = SOURCE_STATE_ES.get(source_report.state, source_report.state)
    lines.append(f"  Estado: {state}")

    for item in source_report.evidence:
        lines.append(f"  Evidencia: {item}")
    if source_report.offers_current_run is not None:
        lines.append(
            "  Ofertas capturadas en la ejecución: "
            f"{source_report.offers_current_run}"
        )
    if source_report.total_offers is not None:
        lines.append(
            "  Ofertas únicas (snapshot obtenido): "
            f"{source_report.total_offers}"
        )
    if source_report.progress is not None:
        lines.append(
            f"  Progreso (ofertas parseadas): {source_report.progress}"
        )
    if source_report.stop_reason:
        reason_es = STOP_REASON_ES.get(
            source_report.stop_reason, source_report.stop_reason
        )
        lines.append(
            f"  Detención: {reason_es} ({source_report.stop_reason})"
        )

    if source_report.fields:
        lines.append("  Completitud por campo:")
        for field_report in source_report.fields:
            lines.append(_render_field(field_report))
    else:
        lines.append("  Completitud por campo: sin medición")

    if source_report.publication_state is not None:
        state_es = PUBLICATION_STATE_ES.get(
            source_report.publication_state, source_report.publication_state
        )
        extra: list[str] = []
        if source_report.delta_offers is not None:
            extra.append(f"delta {source_report.delta_offers}")
        if source_report.obtained_offers is not None:
            extra.append(
                f"snapshot obtenido {source_report.obtained_offers}"
            )
        suffix = f" | {' | '.join(extra)}" if extra else ""
        lines.append(
            f"  Publicación: {state_es} "
            f"({source_report.publication_state}){suffix}"
        )

    if source_report.trend is not None:
        lines.extend(_render_trend(source_report.trend))

    if source_report.failures:
        lines.append("  Motivos de fallo:")
        for failure in source_report.failures:
            lines.append(f"    - {_failure_reason_es(failure)}")
    if source_report.incidents:
        lines.append("  Incidencias:")
        for incident in source_report.incidents:
            name = FIELD_ES.get(incident, incident)
            lines.append(
                f"    - campo obligatorio '{name}' sin alcanzar el objetivo"
            )
    return "\n".join(lines)


def render_report(report: DiagnosticReport) -> str:
    """Render the whole report as Spanish console text (RF-14).

    The header shows the Spanish global state with the correct/failed counts
    and, when available, the trend note and how many runs were used. When the
    run could not be analysed (``GLOBAL_INCONCLUSIVE``) the report says so and
    shows the reason, and it does not list sources as correct/failed because
    none were analysed (RF-13). The output is deterministic and ends in a
    newline.
    """
    lines: list[str] = ["=== Diagnóstico de la ejecución diaria ==="]
    global_state = GLOBAL_STATE_ES.get(
        report.global_state, report.global_state
    )

    if report.global_state == status_module.GLOBAL_INCONCLUSIVE:
        lines.append(f"Estado global: {global_state}")
        if report.global_detail:
            lines.append(f"Detalle: {report.global_detail}")
        lines.append(
            "No se analizaron las fuentes: no hay resultados por fuente que "
            "presentar."
        )
        return "\n".join(lines) + "\n"

    correct = sum(
        1
        for source_report in report.sources
        if source_report.state == status_module.SOURCE_CORRECT
    )
    failed = len(report.sources) - correct
    lines.append(
        f"Estado global: {global_state} "
        f"(correctas {correct}/{len(report.sources)}, "
        f"fallidas {failed}/{len(report.sources)})"
    )
    if report.trend_note:
        lines.append(f"Tendencia: {report.trend_note}")
    lines.append(f"Ejecuciones usadas para la tendencia: {report.runs_used}")

    for source_report in report.sources:
        lines.append("")
        lines.append("----------------------------------------")
        lines.extend(render_source_report(source_report).splitlines())

    return "\n".join(lines) + "\n"
