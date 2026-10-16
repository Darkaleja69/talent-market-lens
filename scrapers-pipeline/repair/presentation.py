"""Render the repair queue as Spanish console text (T-06; RF-1).

:func:`describe_targets` turns the prioritized queue of :mod:`repair.targets`
(primary targets first, secondary completeness targets after) into plain
Spanish text for the person: a short header with the run of origin and the
target counts, then one block per target with its position and role, source,
kind, state, reason, quality gap and plan §6 playbook.

Machine identifiers (source ids, kinds, statuses, outcomes, field names,
triggers and section numbers) stay in English; only the generated labels and
explanations are Spanish. No emoji, path or credential is emitted: the summary
only uses the fields of the target. T-16's ``targets`` subcommand prints the
result of this function and adds no rules of its own.

The text uses the arrow ``→`` of the quality gap; a console that cannot encode
it (for example a cp1252 Windows console) needs UTF-8 output, which T-16
configures when printing.
"""
from __future__ import annotations

from collections.abc import Iterable

from repair import targets

# Spanish descriptions of the run outcome (the code itself stays English).
_OUTCOME_ES: dict[str, str] = {
    "ok": "ejecución correcta",
    "empty": "sin ofertas capturadas",
    "error": "fallo técnico o watchdog",
    "blocked": "bloqueo por CAPTCHA/anti-bot",
    "no_evidence": "sin evidencia suficiente",
}

# Spanish descriptions of the completeness trigger (the code stays English).
_TRIGGER_ES: dict[str, str] = {
    targets.TRIGGER_REQUIRED_FIELD: "campo obligatorio por debajo del objetivo",
    targets.TRIGGER_OPTIONAL_FIELD: "campo opcional en el umbral o por debajo",
}

# Spanish short title of the applicable plan §6 playbook, per source. An
# unknown source falls back to the generic playbook §6.7. The section numbers
# are cross-checked against `repair.brief` by the tests.
_PLAYBOOK_ES: dict[str, tuple[str, str]] = {
    "infojobs": ("6.1", "InfoJobs — bloqueo por CAPTCHA"),
    "indeed": ("6.2", "Indeed — descripción al 0 % y segunda página"),
    "linkedin": (
        "6.3",
        "LinkedIn — descripción histórica y campos opcionales",
    ),
    "irishjobs": ("6.4", "IrishJobs — 0 tarjetas y watchdog"),
    "glassdoor": ("6.5", "Glassdoor — watchdog por descripciones"),
    "stepstone_nl": ("6.6", "StepStone NL — completitud de campos opcionales"),
    "nvb": ("6.6", "NVB — completitud de campos opcionales"),
    "jobs_ch": ("6.6", "Jobs.ch — completitud de campos opcionales"),
}
_GENERIC_PLAYBOOK_ES: tuple[str, str] = (
    "6.7",
    "genérico para fuentes nuevas o fallos futuros",
)

_ROLE_ES: dict[str, str] = {
    targets.ROLE_PRIMARY: "primario",
    targets.ROLE_SECONDARY: "secundario",
}


def describe_targets(targets_queue: Iterable[targets.RepairTarget]) -> str:
    """Render a prioritized repair queue as Spanish console text (T-06).

    ``targets_queue`` is normally ``select_all_targets(loaded)``: primary
    targets first and secondary completeness targets after. The text starts
    with a header (run of origin and counts) and continues with one numbered
    block per target: role, source, kind, state (machine ``status``/``outcome``
    plus a Spanish description), reason (the diagnostic failures verbatim, or
    the secondary trigger, field and current percentage), quality gap (fields
    whose target is above the current value, plus the optionals the portal
    does not publish) and the applicable plan §6 playbook. Machine identifiers
    stay in English; the returned text ends in a newline and can be printed
    directly.
    """
    items = tuple(targets_queue)
    lines = _header(items)
    if not items:
        lines.append("No hay objetivos que reparar.")
    for position, target in enumerate(items, start=1):
        lines.extend(_describe_target(position, target))
    return "\n".join(lines) + "\n"


def _header(items: tuple[targets.RepairTarget, ...]) -> list[str]:
    """Return the Spanish header with the run of origin and the counts."""
    run_dates: list[str] = []
    for target in items:
        label = (
            target.run_date.isoformat()
            if target.run_date is not None
            else "desconocido"
        )
        if label not in run_dates:
            run_dates.append(label)
    if not run_dates:
        run_line = "run de origen: desconocido"
    elif len(run_dates) == 1:
        run_line = f"run de origen: {run_dates[0]}"
    else:
        run_line = f"runs de origen: {', '.join(run_dates)}"

    primaries = sum(1 for item in items if item.role == targets.ROLE_PRIMARY)
    secondaries = len(items) - primaries
    return [
        f"Objetivos de reparación — {run_line}",
        "Total: "
        f"{len(items)} {_plural(len(items), 'objetivo', 'objetivos')} "
        f"({primaries} {_plural(primaries, 'primario', 'primarios')}, "
        f"{secondaries} {_plural(secondaries, 'secundario', 'secundarios')})",
        "",
    ]


def _describe_target(
    position: int,
    target: targets.RepairTarget,
) -> list[str]:
    """Return the Spanish block of one numbered repair target."""
    role_es = _ROLE_ES.get(target.role, target.role)
    kind = target.kind if target.kind is not None else "desconocido"
    return [
        f"{position}. {target.source} — {role_es} ({target.role}) | "
        f"kind: {kind}",
        f"   Estado: {target.status} | outcome: {_outcome_text(target.outcome)}",
        f"   Motivo: {_reason_text(target)}",
        f"   Brecha de calidad: {_quality_text(target)}",
        f"   Playbook: {_playbook_text(target.source)}",
    ]


def _outcome_text(outcome: str | None) -> str:
    """Return the machine outcome plus its Spanish description when known."""
    if outcome is None:
        return "sin resultado registrado"
    label = _OUTCOME_ES.get(outcome)
    return outcome if label is None else f"{outcome} ({label})"


def _reason_text(target: targets.RepairTarget) -> str:
    """Return the reason: diagnostic failures or the secondary field gap."""
    if target.role == targets.ROLE_SECONDARY:
        trigger = target.trigger if target.trigger is not None else "sin trigger"
        label = _TRIGGER_ES.get(target.trigger or "")
        trigger_text = trigger if label is None else f"{trigger} ({label})"
        field = target.field if target.field is not None else "sin campo"
        current = (
            "sin dato"
            if target.current_pct is None
            else _pct_current(target.current_pct)
        )
        return f"{trigger_text} | campo: {field} | actual: {current}"
    if target.failures:
        return "; ".join(target.failures)
    return "sin motivos registrados en el diagnóstico"


def _quality_text(target: targets.RepairTarget) -> str:
    """Return the quality gap: fields below target and unpublished optionals.

    A field is a gap when its target is not ``None`` and the current value is
    unknown or below the target; an optional without target is the portal not
    publishing it, so it is reported as such and never as a failure.
    """
    parts: list[str] = []
    unpublished: list[str] = []
    for entry in target.quality:
        if entry.target_pct is None:
            unpublished.append(
                f"{entry.field}: sin meta (el portal no lo publica)"
            )
            continue
        if entry.current_pct is None:
            parts.append(
                f"{entry.field} sin dato → {_pct_target(entry.target_pct)}"
            )
        elif entry.current_pct < entry.target_pct:
            parts.append(
                f"{entry.field} {_pct_current(entry.current_pct)} → "
                f"{_pct_target(entry.target_pct)}"
            )
    parts.extend(unpublished)
    return "; ".join(parts) if parts else "sin brechas"


def _playbook_text(source_id: str) -> str:
    """Return the plan §6 playbook reference of a source (generic if unknown)."""
    section, title = _PLAYBOOK_ES.get(source_id, _GENERIC_PLAYBOOK_ES)
    return f"§{section} {title}"


def _pct_current(value: float) -> str:
    """Format a measured percentage with one decimal (for example 0.0 %)."""
    return f"{value:.1f} %"


def _pct_target(value: float) -> str:
    """Format a target percentage, dropping the decimal when it is integral."""
    if float(value).is_integer():
        return f"{int(value)} %"
    return f"{value:.1f} %"


def _plural(count: int, singular: str, plural: str) -> str:
    """Return the singular noun for one unit and the plural otherwise."""
    return singular if count == 1 else plural
