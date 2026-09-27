"""Bounded context to investigate an anomaly on the real website (T-35, T-36).

The diagnostic only needs to contrast a handful of cases with the real site
(plan section 5, technical decision 7): a **failed source** (RF-9) or a
**completeness threshold** not met (RF-10). This module prepares, for each of
those cases, a small, read-only context the person can follow by hand:

- which source, its readable name and group;
- the effective search, region and city;
- an example offer and its URL;
- the affected canonical field and a verifiable metric with counts;
- the local evidence the run left;
- a hint of the rule/parser to look at.

Design rules:

- The module is **pure**: standard library plus the existing ``verification.*``
  modules, no network, no Azure, no direct PyArrow use (it never reads Parquet)
  and no side effects. :func:`record_web_check` also represents a manual check
  (T-36), keeping facts and hypotheses apart and never asserting a cause when
  the page could not be verified.
- It **never changes the source state** (RF-12): it only reads a
  :class:`verification.status.SourceStatus` and returns data.
- It **never invents data**: a missing ``completeness`` or absent offers simply
  leave those context fields empty (``None``/empty tuple).

User-facing text (evidence and metric) is in Spanish; identifiers and comments
stay in English.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from verification import completeness, field_contract, run_evidence, sources
from verification.fingerprint import SearchDimension
from verification.status import SOURCE_FAILED, SourceStatus

# Trigger reasons (RF-9, RF-10). Plain strings so the report layer can render
# them without importing an enum.
TRIGGER_SOURCE_FAILED = "source_failed"
TRIGGER_REQUIRED_FIELD = "required_field_below_target"
TRIGGER_OPTIONAL_FIELD = "optional_field_at_or_below_threshold"

# Web-check outcome (T-36; RF-9, RF-10, RF-11). The person contrasts the
# anomaly with the real website by hand; the system only represents the result.
INVESTIGATION_CONFIRMED = "confirmed"
INVESTIGATION_UNCONFIRMED = "unconfirmed"

# status.py failure reasons, translated for the person (RF-11). The strings are
# produced by verification.status, so they are matched exactly.
_MISSING_EVIDENCE = "missing evidence"
_ZERO_OFFERS = "zero offers"
_STRUCTURAL_FAILURE = "structural failure"
_REQUIRED_REASON_RE = re.compile(
    r"required field '(?P<field>.+)' at (?P<pct>[\d.]+)% \(<90%\)"
)

# Readable Spanish for the per-source run outcome codes.
_OUTCOME_ES = {
    run_evidence.OUTCOME_OK: "correcto",
    run_evidence.OUTCOME_EMPTY: "sin ofertas",
    run_evidence.OUTCOME_ERROR: "error técnico",
    run_evidence.OUTCOME_BLOCKED: "bloqueado",
    run_evidence.OUTCOME_NO_EVIDENCE: "sin evidencia",
}


@dataclass(frozen=True)
class InvestigationContext:
    """Bounded, read-only context to contrast one anomaly with the real site.

    ``field`` is ``None`` for a general source failure without a concrete
    field; ``example_offer`` is a *copy* so callers can never mutate the offers
    the context was built from (RF-12).
    """

    source: str
    display_name: str
    group: str
    trigger: str
    field: str | None
    search: str | None
    region: str | None
    city: str | None
    example_url: str | None
    example_offer: dict | None
    metric: str | None
    evidence: tuple[str, ...]
    rule_reference: str | None


@dataclass(frozen=True)
class InvestigationOutcome:
    """Result of contrasting one anomaly with the real website (T-36).

    Facts (``observations``) and hypotheses (``probable_cause``) are kept
    separate: an unconfirmed check stores no cause, because RF-9/RF-10 forbid
    presenting a cause as confirmed when the page could not be verified. The
    outcome never carries nor alters the ingestion state (RF-12).
    """

    context: InvestigationContext
    state: str
    observations: tuple[str, ...]
    probable_cause: str | None
    recommendation: str | None
    manual_check: str | None
    detail: str | None


def needs_investigation(status: SourceStatus) -> bool:
    """Return True when a source must be investigated on the real site.

    A source qualifies when it is failed (RF-9) or when at least one field fell
    below its threshold and was listed in ``investigation_fields`` (RF-10).
    """
    return status.state == SOURCE_FAILED or bool(status.investigation_fields)


def example_url_column(source_id: str) -> str | None:
    """Return the example-URL column of a source, derived from the catalog.

    The column is the first required column whose name contains ``"url"``
    (Indeed ``viewjob_url``; LinkedIn and the six portals ``job_url``; InfoJobs
    ``url_oferta``), or ``None`` when the source has none.
    """
    for column in sources.required_columns(source_id):
        if "url" in column.lower():
            return column
    return None


def source_url(offer: Mapping, source_id: str) -> str | None:
    """Return the example URL of an offer, or ``None`` when absent/blank."""
    column = example_url_column(source_id)
    if column is None:
        return None
    value = offer.get(column)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def select_example_offer(
    offers: Iterable[Mapping], source_id: str, field: str | None = None
) -> dict | None:
    """Pick a representative offer and return a copy (never the input).

    With ``field``, an offer whose value for that field is ``ABSENT`` or
    ``INVALID`` (through the source's aliases) is preferred, because it shows
    the anomaly best; if none qualifies, the first offer is used. Returns
    ``None`` when there are no offers.
    """
    materialized = list(offers)
    if not materialized:
        return None
    if field is not None:
        aliases = sources.field_aliases(source_id, field)
        for offer in materialized:
            state = completeness.classify_offer_field(field, offer, aliases)
            if state in (field_contract.ABSENT, field_contract.INVALID):
                return dict(offer)
    return dict(materialized[0])


def _clean(value: object) -> str | None:
    """Trim a value to a non-empty string, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_flag(
    field: str, source_completeness: completeness.SourceCompleteness | None
) -> bool:
    """Return whether ``field`` is mandatory, using the contract as fallback."""
    if source_completeness is not None:
        stats = source_completeness.fields.get(field)
        if stats is not None:
            return stats.required
    return field_contract.is_required(field)


def _trigger(field: str | None, required: bool) -> str:
    if field is None:
        return TRIGGER_SOURCE_FAILED
    return TRIGGER_REQUIRED_FIELD if required else TRIGGER_OPTIONAL_FIELD


def _metric(
    source_completeness: completeness.SourceCompleteness | None, field: str | None
) -> str | None:
    """Return a verifiable metric with counts and percentage, or ``None``."""
    if source_completeness is None or field is None:
        return None
    stats = source_completeness.fields.get(field)
    if stats is None:
        return None
    return f"válidos {stats.valid}/{stats.total} ({stats.completeness_pct:.1f}%)"


def _rule_reference(source_id: str, field: str | None) -> str | None:
    """Return a hint of the field/parser to look at, or ``None``."""
    if field is None:
        return None
    aliases = sources.field_aliases(source_id, field)
    if aliases:
        return f"campo '{field}'; alias de origen: {', '.join(aliases)}"
    return f"campo '{field}'; la fuente no publica este campo"


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
            f"campo obligatorio '{match.group('field')}' en "
            f"{match.group('pct')}% (<90%)"
        )
    return reason


def _status_evidence(source_status: SourceStatus) -> tuple[str, ...]:
    """Return the Spanish motives recorded by the source classification."""
    items = [_failure_reason_es(reason) for reason in source_status.failures]
    for field in source_status.incidents:
        items.append(f"incidencia: campo obligatorio '{field}' entre 90% y 100%")
    return tuple(items)


def _run_evidence(run: run_evidence.RunEvidence | None) -> tuple[str, ...]:
    """Return the local run evidence translated/annotated for the person."""
    if run is None:
        return ()
    outcome = _OUTCOME_ES.get(run.outcome, run.outcome)
    items = [f"resultado de la ejecución: {outcome} ({run.outcome})"]
    if run.offers_current_run is not None:
        items.append(
            f"ofertas capturadas en la ejecución: {run.offers_current_run}"
        )
    if run.error:
        items.append(f"error registrado: {run.error}")
    if run.detail:
        items.append(f"detalle: {run.detail}")
    if run.attempt is not None and run.attempts is not None:
        items.append(f"intento {run.attempt}/{run.attempts}")
    return tuple(items)


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


def _context_evidence(
    source_status: SourceStatus, extra: Iterable[str]
) -> tuple[str, ...]:
    """Combine the status motives with extra evidence, in Spanish and deduped."""
    items = list(_status_evidence(source_status))
    for item in extra:
        if item:
            text = str(item).strip()
            if text:
                items.append(text)
    return _dedupe(items)


def build_context(
    source_id: str,
    status: SourceStatus,
    *,
    completeness: completeness.SourceCompleteness | None = None,
    offers: Iterable[Mapping] = (),
    search: str | None = None,
    region: str | None = None,
    city: str | None = None,
    evidence: Sequence[str] = (),
    field: str | None = None,
) -> InvestigationContext | None:
    """Build one investigation context for a source/field (RF-9, RF-10, RF-11).

    Returns ``None`` when the source does not require investigation **or** when
    the requested field was not flagged by RF-10. An unknown, non-canonical
    ``field`` raises :class:`ValueError` (a domain error) instead of leaking a
    ``KeyError`` from the catalog.

    ``field=None`` requests the general source-failure trigger, which only
    applies to a failed source whose failure is not tied to a concrete field
    (zero offers, missing evidence, structural failure). A failed source that
    has affected fields yields one context per field instead, so ``field=None``
    never invents a general context for it. A field is classified as
    mandatory/optional from the measured completeness when available, otherwise
    from the data contract.
    """
    if field is not None and field not in sources.CANONICAL_FIELDS:
        raise ValueError(
            f"unknown canonical field '{field}'; expected one of: "
            f"{', '.join(sources.CANONICAL_FIELDS)}"
        )
    if not needs_investigation(status):
        return None
    if field is None:
        # Only the general failure (no concrete field) uses the field-less
        # trigger (RF-9). A source failed for a field is investigated per
        # field, so a field-less request must not fabricate a context.
        if status.state != SOURCE_FAILED or status.investigation_fields:
            return None
    elif field not in status.investigation_fields:
        # Never invent an investigation for a field RF-10 did not flag: a
        # source marked only for another field must not yield a context here.
        return None

    source = sources.get_source(source_id)
    example_offer = select_example_offer(offers, source_id, field)
    example_url = (
        source_url(example_offer, source_id) if example_offer is not None else None
    )
    required = _required_flag(field, completeness) if field is not None else False
    return InvestigationContext(
        source=source_id,
        display_name=source.display_name,
        group=source.group,
        trigger=_trigger(field, required),
        field=field,
        search=_clean(search),
        region=_clean(region),
        city=_clean(city),
        example_url=example_url,
        example_offer=example_offer,
        metric=_metric(completeness, field),
        evidence=_context_evidence(status, evidence),
        rule_reference=_rule_reference(source_id, field),
    )


def _dimensions(
    source_id: str, searches: Sequence[SearchDimension]
) -> tuple[str | None, str | None, str | None]:
    """Return (search, region, city) from the matching/first SearchDimension."""
    materialized = list(searches)
    if not materialized:
        return None, None, None
    chosen = next(
        (dimension for dimension in materialized if dimension.source == source_id),
        materialized[0],
    )
    return _clean(chosen.search), _clean(chosen.region), _clean(chosen.city)


def build_contexts(
    source_id: str,
    status: SourceStatus,
    *,
    completeness: completeness.SourceCompleteness | None = None,
    offers: Iterable[Mapping] = (),
    searches: Sequence[SearchDimension] = (),
    evidence: Sequence[str] = (),
    run: run_evidence.RunEvidence | None = None,
) -> tuple[InvestigationContext, ...]:
    """Build every investigation context required for a source (T-35).

    One context is generated per affected field in
    ``status.investigation_fields``. When the source is failed but no field was
    flagged (zero offers, missing evidence, structural failure), a single
    context with ``field=None`` and the source-failure trigger is generated. A
    source that does not require investigation yields ``()``.

    ``searches`` fills the search/region/city trace and ``run`` adds the local
    evidence of the execution. Contexts are deduplicated by
    ``(source, trigger, field)`` without losing order. Entries are never
    mutated.
    """
    if not needs_investigation(status):
        return ()

    offer_list = list(offers)
    search, region, city = _dimensions(source_id, searches)
    extra_evidence = tuple(evidence) + _run_evidence(run)

    fields: Sequence[str | None]
    if status.investigation_fields:
        fields = list(status.investigation_fields)
    elif status.state == SOURCE_FAILED:
        fields = [None]
    else:
        return ()

    contexts: list[InvestigationContext] = []
    seen: set[tuple[str, str, str | None]] = set()
    for field in fields:
        context = build_context(
            source_id,
            status,
            completeness=completeness,
            offers=offer_list,
            search=search,
            region=region,
            city=city,
            evidence=extra_evidence,
            field=field,
        )
        if context is None:
            continue
        key = (context.source, context.trigger, context.field)
        if key in seen:
            continue
        seen.add(key)
        contexts.append(context)
    return tuple(contexts)


# Readable Spanish names of the canonical fields, so the guidance reads
# naturally. Kept local (and small) to avoid a dependency on the report layer.
_FIELD_ES: dict[str, str] = {
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


def _field_label(field: str | None) -> str:
    """Return a field with its Spanish name, e.g. ``salario (salary)``."""
    if field is None:
        return "el campo afectado"
    return f"{_FIELD_ES.get(field, field)} ({field})"


def suggest_recommendation(context: InvestigationContext) -> str:
    """Return an actionable code change for an affected source (RF-11).

    Pure and deterministic: it only reads the context. It covers the three
    investigation triggers and never asserts a confirmed cause, because the
    real website has not been verified (RF-9, RF-10). The text names the
    source and, when there is one, the affected field.
    """
    source = context.display_name
    if context.trigger == TRIGGER_SOURCE_FAILED:
        return (
            f"Revisar el scraper de {source}: comprobar que arranca, que las "
            "búsquedas y regiones configuradas siguen vigentes y que el portal "
            "es accesible. Si la evidencia apunta a un fallo estructural, "
            "corregir el esquema o las columnas obligatorias del contrato de "
            "datos antes de volver a ejecutarlo."
        )
    label = _field_label(context.field)
    if context.trigger == TRIGGER_REQUIRED_FIELD:
        return (
            f"Revisar el parser de {source} para el campo obligatorio {label}: "
            "actualizar el selector/expresión que lo extrae para que se recoja "
            "en el 100 % de las ofertas y confirmar que no se descarta por un "
            "valor que incumple el contrato de datos."
        )
    return (
        f"Revisar el parser de {source} para el campo {label}: comprobar si el "
        "dato cambió de componente o de ubicación en la web y ajustar el "
        "selector/expresión. Es un campo no obligatorio, así que su ausencia "
        "no invalida la fuente."
    )


def suggest_manual_check(context: InvestigationContext) -> str:
    """Return how to verify a fix by hand for an affected source (RF-11).

    Pure and deterministic. It names the source, the affected field and the
    example URL when available, so the person can reproduce the check on the
    real website without running a second full scrape (RF-9, RF-12).
    """
    source = context.display_name
    if context.trigger == TRIGGER_SOURCE_FAILED:
        if context.example_url is not None:
            return (
                f"Abrir {context.example_url} y confirmar que {source} muestra "
                "ofertas para la búsqueda indicada; después volver a ejecutar "
                "el scraper y comprobar en el informe que la fuente deja de "
                "figurar como fallida."
            )
        return (
            f"Volver a ejecutar {source} con la búsqueda y región indicadas y "
            "comprobar en el log que se capturan ofertas y que el Parquet "
            "resultante incluye las columnas obligatorias del contrato."
        )
    label = _field_label(context.field)
    if context.example_url is not None:
        return (
            f"Abrir {context.example_url} y confirmar que {label} aparece y se "
            f"recoge; después volver a ejecutar {source} y comprobar en el "
            "informe que su completitud alcanza el objetivo."
        )
    return (
        f"Volver a ejecutar {source} y comprobar en el informe que la "
        f"completitud de {label} alcanza el objetivo."
    )


def record_web_check(
    context: InvestigationContext,
    *,
    accessible: bool,
    observations: Sequence[str] = (),
    probable_cause: str | None = None,
    recommendation: str | None = None,
    manual_check: str | None = None,
    detail: str | None = None,
) -> InvestigationOutcome:
    """Represent a manual check on the real website (RF-9, RF-10, RF-11).

    The check is person-assisted: this only stores what happened, it never
    navigates or launches a scraper (RF-9, RF-10, RF-12). When the page is not
    accessible or cannot be verified (``accessible`` false) the investigation is
    left **unconfirmed**: no fact is asserted (``observations`` empty) and the
    ``probable_cause`` is discarded, because no cause can be confirmed without
    verifying the page. The recommendation, the manual check and the note are
    still kept as guidance. With ``accessible`` true the state is confirmed and
    the facts and hypothesis are preserved as given.
    """
    if not accessible:
        return InvestigationOutcome(
            context=context,
            state=INVESTIGATION_UNCONFIRMED,
            observations=(),
            probable_cause=None,
            recommendation=recommendation,
            manual_check=manual_check,
            detail=detail,
        )
    return InvestigationOutcome(
        context=context,
        state=INVESTIGATION_CONFIRMED,
        observations=tuple(observations),
        probable_cause=probable_cause,
        recommendation=recommendation,
        manual_check=manual_check,
        detail=detail,
    )


def unconfirmed_web(
    context: InvestigationContext,
    *,
    detail: str | None = None,
    recommendation: str | None = None,
    manual_check: str | None = None,
) -> InvestigationOutcome:
    """Shortcut for a website that is not accessible or not verifiable (T-36)."""
    return record_web_check(
        context,
        accessible=False,
        detail=detail,
        recommendation=recommendation,
        manual_check=manual_check,
    )


def is_unconfirmed(outcome: InvestigationOutcome) -> bool:
    """Return whether the web check could not be confirmed (RF-9, RF-10)."""
    return outcome.state == INVESTIGATION_UNCONFIRMED


def source_state_preserved(
    before: Mapping[str, SourceStatus],
    after: Mapping[str, SourceStatus],
) -> bool:
    """Return whether the ingestion state did not change (RF-12).

    Pure read-only helper: it compares the state and motives (failures,
    incidents and fields flagged for investigation) of every source without
    mutating either mapping. It lets tests assert that representing a web check
    never touches the source classification.
    """
    if set(before) != set(after):
        return False
    for source_id, before_status in before.items():
        after_status = after[source_id]
        if (
            before_status.state != after_status.state
            or before_status.failures != after_status.failures
            or before_status.incidents != after_status.incidents
            or before_status.investigation_fields
            != after_status.investigation_fields
        ):
            return False
    return True
