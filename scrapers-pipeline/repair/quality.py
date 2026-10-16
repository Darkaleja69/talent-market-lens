"""Quality gate of a live repair test (T-11; RF-7, RF-8, RF-11, RF-16).

The live test of a repair is measured with ``verification.completeness`` (the
same metrics as the 001 process, no new ones) and compared with the per-field
profile of the brief (T-04/T-05). The gate fails when:

- the Parquet is unreadable or lacks the mandatory columns of the source;
- the sample has zero offers (a test of 0 offers proves nothing);
- a required field (``title``, ``company``, ``description``) is below 100 %;
- a field already covered (``before_pct > 0``) drops more than
  ``QUALITY_REGRESSION_TOLERANCE_PP`` percentage points;
- a field whose target is not ``None`` (the web/API exposes it) does not
  reach its target.

An optional field the portal does not publish (``target_pct is None``) with no
previous coverage never blocks and never counts as a regression; it only
blocks if it was already covered and degrades beyond the tolerance. The
``description`` is required and prioritized: when it fails, its specific
reason comes first.

The result is machine-readable in English (:func:`verdict_to_dict`, stored as
``quality_after.json``) and has a Spanish table for the person
(:func:`render_quality_table`). The module only reads the Parquet: it never
writes the record and never uploads anything (RF-15).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from repair import targets
from verification import completeness

# Percentage-point drop a covered field may suffer before the gate fails.
QUALITY_REGRESSION_TOLERANCE_PP = 5.0

# Machine statuses of a field row.
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_APPLICABLE = "not_applicable"

# Blocking reason codes: the structural ones plus the field-level reasons.
BLOCKER_UNREADABLE = "unreadable_parquet"
BLOCKER_MISSING_COLUMNS = "missing_columns"
BLOCKER_EMPTY_SAMPLE = "empty_sample"
BLOCKER_DESCRIPTION_MISSING = "description_missing"
REASON_REQUIRED = "required_below_100"
REASON_REGRESSION = "regression_beyond_tolerance"
REASON_TARGET = "target_not_reached"

# Spanish labels of the codes, for the person's table.
_CODE_ES: dict[str, str] = {
    BLOCKER_UNREADABLE: "parquet ilegible",
    BLOCKER_MISSING_COLUMNS: "faltan columnas obligatorias",
    BLOCKER_EMPTY_SAMPLE: "muestra vacía (0 ofertas)",
    BLOCKER_DESCRIPTION_MISSING: (
        "falta la descripción (obligatoria y prioritaria)"
    ),
    REASON_REQUIRED: "campo obligatorio por debajo del 100 %",
    REASON_REGRESSION: (
        "regresión superior a "
        f"{QUALITY_REGRESSION_TOLERANCE_PP:.1f} pp"
    ),
    REASON_TARGET: "meta no alcanzada",
}
_STATUS_ES: dict[str, str] = {
    STATUS_PASS: "cumple",
    STATUS_FAIL: "no cumple",
    STATUS_NOT_APPLICABLE: "no aplica",
}
_TABLE_HEADERS = (
    "Campo",
    "Obligatorio",
    "Antes",
    "Después",
    "Delta",
    "Meta",
    "Estado",
)


@dataclass(frozen=True)
class QualityRow:
    """Before/after quality of one canonical field (T-11; RF-16).

    ``before_pct`` comes from the diagnostic profile of the brief and
    ``after_pct`` from the live test; ``delta_pp`` is their difference when
    both are known. ``target_pct`` is the goal (``None`` when the portal does
    not publish the field) and ``status`` is a machine code (``pass``,
    ``fail`` or ``not_applicable``). ``reasons`` lists the blocking codes of
    the row (``required_below_100``, ``regression_beyond_tolerance``,
    ``target_not_reached``), empty when the row passes.
    """

    field: str
    required: bool
    before_pct: float | None
    after_pct: float | None
    delta_pp: float | None
    target_pct: float | None
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class QualityVerdict:
    """Result of the quality gate for one live test (T-11; RF-7, RF-8).

    ``ok`` is the gate verdict; ``blockers`` lists the machine codes that
    blocked it (empty when ``ok``), with the ``description`` reason first when
    that field failed. ``total_offers`` is the measured unique-offer count
    (``None`` when the Parquet could not be measured) and ``rows`` the
    before/after table in canonical field order (empty when unreadable or
    missing mandatory columns). ``missing_columns`` and ``read_error`` keep the
    structural detail for the person and the record.
    """

    source: str
    ok: bool
    total_offers: int | None
    rows: tuple[QualityRow, ...]
    blockers: tuple[str, ...]
    missing_columns: tuple[str, ...] = ()
    read_error: str | None = None


def evaluate_quality(
    parquet_path: str | Path,
    source_id: str,
    profile: Sequence[targets.QualityProfile],
) -> QualityVerdict:
    """Measure a live-test Parquet and apply the quality gate (RF-7, RF-16).

    ``profile`` is the per-field profile built by T-04 (through the brief):
    ``current_pct`` is the before value, ``target_pct`` the goal (``None`` when
    the portal does not publish the field) and ``required`` the contract flag.
    The measurement reuses ``verification.completeness``: the Parquet is read
    with the source's mandatory columns and, when structurally sound, its
    unique offers are measured per canonical field. Unreadable files, missing
    mandatory columns and empty samples return a not-ok verdict with their
    blocker instead of raising.
    """
    path = str(parquet_path)
    read = completeness.read_source_parquet(path, source_id)
    if not read.readable:
        return QualityVerdict(
            source=source_id,
            ok=False,
            total_offers=None,
            rows=(),
            blockers=(BLOCKER_UNREADABLE,),
            read_error=read.error,
        )
    if read.missing_columns:
        return QualityVerdict(
            source=source_id,
            ok=False,
            total_offers=None,
            rows=(),
            blockers=(BLOCKER_MISSING_COLUMNS,),
            missing_columns=read.missing_columns,
        )
    try:
        table = pq.read_table(path)
    except Exception as exc:  # defensive: the file changed after the check
        return QualityVerdict(
            source=source_id,
            ok=False,
            total_offers=None,
            rows=(),
            blockers=(BLOCKER_UNREADABLE,),
            read_error=repr(exc),
        )
    measured = completeness.measure_completeness(table, source_id)
    rows = tuple(
        _quality_row(entry, measured.fields[entry.field].completeness_pct)
        for entry in profile
    )
    blockers = _aggregate_blockers(rows, measured.total_offers == 0)
    return QualityVerdict(
        source=source_id,
        ok=not blockers,
        total_offers=measured.total_offers,
        rows=rows,
        blockers=blockers,
    )


def verdict_to_dict(verdict: QualityVerdict) -> dict:
    """Convert a verdict into the English JSON-ready result (RF-16).

    Keys and status codes are in English and the result is directly
    serializable with ``json.dumps`` for ``quality_after.json`` (plan §3.4).
    """
    return {
        "source": verdict.source,
        "ok": verdict.ok,
        "total_offers": verdict.total_offers,
        "blockers": list(verdict.blockers),
        "missing_columns": list(verdict.missing_columns),
        "read_error": verdict.read_error,
        "regression_tolerance_pp": QUALITY_REGRESSION_TOLERANCE_PP,
        "fields": [
            {
                "field": row.field,
                "required": row.required,
                "before_pct": row.before_pct,
                "after_pct": row.after_pct,
                "delta_pp": row.delta_pp,
                "target_pct": row.target_pct,
                "status": row.status,
                "reasons": list(row.reasons),
            }
            for row in verdict.rows
        ],
    }


def render_quality_table(verdict: QualityVerdict) -> str:
    """Render the verdict and its before/after table in Spanish (T-11).

    The output is plain console text without emojis: the header states the
    source, the verdict, the measured offers and the Spanish blockers, and the
    table lists every field with its before/after percentages, delta, target
    and status. Field names and machine codes stay in English.
    """
    lines = [
        f"Calidad de la prueba en vivo — fuente: {verdict.source}",
        f"Veredicto: {'OK' if verdict.ok else 'NO OK'}",
        (
            "Ofertas únicas medidas: no se pudieron medir"
            if verdict.total_offers is None
            else f"Ofertas únicas medidas: {verdict.total_offers}"
        ),
    ]
    if verdict.missing_columns:
        lines.append(
            "Columnas obligatorias ausentes: "
            + ", ".join(verdict.missing_columns)
        )
    if verdict.read_error is not None:
        lines.append(f"Error de lectura: {verdict.read_error}")
    if verdict.blockers:
        lines.append(
            "Bloqueos: "
            + "; ".join(_CODE_ES.get(code, code) for code in verdict.blockers)
        )
    else:
        lines.append("Bloqueos: ninguno")
    if verdict.rows:
        lines.append("")
        lines.extend(_render_rows(verdict.rows))
    else:
        lines.append("Campos: sin medición")
    return "\n".join(lines) + "\n"


def _quality_row(
    entry: targets.QualityProfile,
    after_pct: float,
) -> QualityRow:
    """Build one row by applying the gate rules to a measured field (T-11)."""
    reasons: list[str] = []
    if entry.required and after_pct < 100.0:
        reasons.append(REASON_REQUIRED)
    if (
        entry.current_pct is not None
        and entry.current_pct > 0.0
        and after_pct
        < entry.current_pct - QUALITY_REGRESSION_TOLERANCE_PP
    ):
        reasons.append(REASON_REGRESSION)
    if entry.target_pct is not None and after_pct < entry.target_pct:
        reasons.append(REASON_TARGET)
    if reasons:
        status = STATUS_FAIL
    elif entry.target_pct is None:
        status = STATUS_NOT_APPLICABLE
    else:
        status = STATUS_PASS
    delta_pp = (
        None if entry.current_pct is None else after_pct - entry.current_pct
    )
    return QualityRow(
        field=entry.field,
        required=entry.required,
        before_pct=entry.current_pct,
        after_pct=after_pct,
        delta_pp=delta_pp,
        target_pct=entry.target_pct,
        status=status,
        reasons=tuple(reasons),
    )


def _aggregate_blockers(
    rows: tuple[QualityRow, ...],
    empty_sample: bool,
) -> tuple[str, ...]:
    """Order the blocking codes: description first, then the rest (RF-16)."""
    blockers: list[str] = []
    description_failed = any(
        row.field == "description" and REASON_REQUIRED in row.reasons
        for row in rows
    )
    if description_failed:
        blockers.append(BLOCKER_DESCRIPTION_MISSING)
    if empty_sample:
        blockers.append(BLOCKER_EMPTY_SAMPLE)
    ordered = sorted(
        rows, key=lambda row: 0 if row.field == "description" else 1
    )
    for row in ordered:
        for reason in row.reasons:
            if reason not in blockers:
                blockers.append(reason)
    return tuple(blockers)


def _render_rows(rows: tuple[QualityRow, ...]) -> list[str]:
    """Render the before/after table as aligned Spanish text."""
    cells = [
        (
            row.field,
            "sí" if row.required else "no",
            _pct_text(row.before_pct),
            _pct_text(row.after_pct),
            _delta_text(row.delta_pp),
            "sin meta" if row.target_pct is None else _pct_text(row.target_pct),
            _status_text(row),
        )
        for row in rows
    ]
    widths = [len(header) for header in _TABLE_HEADERS]
    for row_cells in cells:
        for index, cell in enumerate(row_cells):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  "
        + " | ".join(
            header.ljust(width)
            for header, width in zip(_TABLE_HEADERS, widths)
        )
    ]
    lines.append("  " + "-+-".join("-" * width for width in widths))
    for row_cells in cells:
        lines.append(
            "  "
            + " | ".join(
                cell.ljust(width)
                for cell, width in zip(row_cells, widths)
            )
        )
    return lines


def _pct_text(value: float | None) -> str:
    """Format a percentage with one decimal, or a Spanish missing note."""
    if value is None:
        return "sin dato"
    return f"{value:.1f} %"


def _delta_text(value: float | None) -> str:
    """Format a percentage-point delta with its sign, or a missing note."""
    if value is None:
        return "sin dato"
    return f"{value:+.1f} pp"


def _status_text(row: QualityRow) -> str:
    """Render the Spanish status of a row, with its Spanish reasons."""
    label = _STATUS_ES.get(row.status, row.status)
    if not row.reasons:
        return label
    reasons = "; ".join(_CODE_ES.get(code, code) for code in row.reasons)
    return f"{label} ({reasons})"
