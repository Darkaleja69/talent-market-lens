"""Per-source state classification from completeness thresholds.

T-28 (RF-3, RF-10, RF-14): given a source's measured completeness this module
decides whether the source is ``correct`` or ``failed`` and records why. It
implements the business rules behind:

- RF-3: a mandatory field below 90 % fails its source; zero offers, missing
  evidence and structural non-compliance also fail it;
- RF-10: which fields must be investigated with the real website (mandatory
  fields below 100 % and non-mandatory fields at 60 % or less);
- RF-13: an unanalysable run yields an ``inconclusive`` global outcome;
- RF-14: each independent source is classified only as ``correct`` or
  ``failed``, and the set of sources yields the global outcome
  (``correct``/``partial``/``failed``/``inconclusive``).

Both the **per-source** classification (``classify_source``/``classify_sources``)
and the **global** outcome (``classify_global``) live here. The module is pure:
it performs no network or I/O and depends only on the standard library and the
completeness model.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from verification.completeness import SourceCompleteness

# RF-3: a mandatory field below 90 % completeness fails its source.
REQUIRED_FAIL_PCT = 90.0
# RF-4/RF-10: the target completeness of a mandatory field is 100 %.
REQUIRED_TARGET_PCT = 100.0
# RF-10: a non-mandatory field at 60 % or less triggers investigation.
OPTIONAL_INVESTIGATION_PCT = 60.0

# RF-14: a source can only end up correct or failed.
SOURCE_CORRECT = "correct"
SOURCE_FAILED = "failed"

# RF-14: global outcome of the analysed run.
GLOBAL_CORRECT = "correct"
GLOBAL_PARTIAL = "partial"
GLOBAL_FAILED = "failed"
GLOBAL_INCONCLUSIVE = "inconclusive"

# Readable reason fragments (code-facing text stays in English).
_MISSING_EVIDENCE = "missing evidence"
_ZERO_OFFERS = "zero offers"
_STRUCTURAL_FAILURE = "structural failure"

# User-facing (Spanish) reasons why the run cannot be classified (RF-13).
_INCONCLUSIVE_DEFAULT = "no se pudo analizar la ejecución"
_INCONCLUSIVE_NO_SOURCES = "no hay fuentes para clasificar la ejecución"


@dataclass(frozen=True)
class SourceStatus:
    """Classification of one source against the completeness thresholds.

    ``failures`` are the reasons the source is failed (empty when correct);
    ``incidents`` are mandatory fields between 90 % (inclusive) and 100 % that
    do not fail the source; ``investigation_fields`` are the fields RF-10 asks
    to contrast with the real website (mandatory below 100 % and
    non-mandatory at 60 % or less).
    """

    source: str
    state: str
    failures: tuple[str, ...]
    incidents: tuple[str, ...]
    investigation_fields: tuple[str, ...]


def _required_failure_reason(field: str, pct: float) -> str:
    """Return a readable reason for a mandatory field below 90 %."""
    return f"required field '{field}' at {pct:.1f}% (<90%)"


def _failed(source_id: str, reason: str) -> SourceStatus:
    return SourceStatus(
        source=source_id,
        state=SOURCE_FAILED,
        failures=(reason,),
        incidents=(),
        investigation_fields=(),
    )


def classify_source(
    source_id: str,
    completeness: SourceCompleteness | None,
    *,
    evidence_available: bool = True,
    structural_failure: bool = False,
    structural_error: str | None = None,
) -> SourceStatus:
    """Classify one source as ``correct`` or ``failed`` (RF-3, RF-10, RF-14).

    The checks are applied in order and short-circuit on the first failure:

    1. structural failure (unreadable file or missing mandatory columns);
    2. missing evidence (``evidence_available`` false or ``completeness`` None);
    3. zero offers;
    4. per-field thresholds over ``completeness.fields``.

    A structural failure is checked **before** the missing-completeness
    fallback because it is the more specific, actionable finding (RF-3,
    RF-11): a corrupt file or one without the mandatory columns yields no
    ``completeness`` and would otherwise be reported as generic "missing
    evidence", hiding the real reason. It also takes precedence over an
    explicit ``evidence_available=False`` for the same reason.

    For step 4, a mandatory field below 90 % fails the source; a mandatory
    field between 90 % (inclusive) and 100 % is an incident that does not fail
    it; a non-mandatory field at 60 % or less only triggers investigation.
    Mandatory fields below 100 % and non-mandatory fields at 60 % or less are
    always listed in ``investigation_fields`` (RF-10). Only the fields present
    in ``completeness`` are evaluated; no thresholds are invented for fields
    that were not measured.
    """
    if structural_failure:
        reason = (
            f"{_STRUCTURAL_FAILURE}: {structural_error}"
            if structural_error
            else _STRUCTURAL_FAILURE
        )
        return _failed(source_id, reason)

    if not evidence_available or completeness is None:
        return _failed(source_id, _MISSING_EVIDENCE)

    if completeness.total_offers == 0:
        return _failed(source_id, _ZERO_OFFERS)

    failures: list[str] = []
    incidents: list[str] = []
    investigation: list[str] = []
    for field, stats in completeness.fields.items():
        pct = stats.completeness_pct
        if stats.required:
            if pct < REQUIRED_FAIL_PCT:
                failures.append(_required_failure_reason(field, pct))
                investigation.append(field)
            elif pct < REQUIRED_TARGET_PCT:
                incidents.append(field)
                investigation.append(field)
        elif pct <= OPTIONAL_INVESTIGATION_PCT:
            investigation.append(field)

    return SourceStatus(
        source=source_id,
        state=SOURCE_FAILED if failures else SOURCE_CORRECT,
        failures=tuple(failures),
        incidents=tuple(incidents),
        investigation_fields=tuple(investigation),
    )


def classify_sources(
    completeness_by_source: Mapping[str, SourceCompleteness | None],
) -> dict[str, SourceStatus]:
    """Classify several sources from their completeness, preserving order."""
    return {
        source_id: classify_source(source_id, source_completeness)
        for source_id, source_completeness in completeness_by_source.items()
    }


@dataclass(frozen=True)
class GlobalStatus:
    """Global outcome of a run plus the per-source split (RF-13, RF-14).

    ``state`` is one of ``GLOBAL_CORRECT``, ``GLOBAL_PARTIAL``, ``GLOBAL_FAILED``
    or ``GLOBAL_INCONCLUSIVE``; ``correct`` and ``failed`` hold the source ids in
    the order they were received; ``inconclusive_reason`` is set (in Spanish)
    only when the run could not be classified; ``source_count`` is the number of
    sources that were classified.
    """

    state: str
    correct: tuple[str, ...]
    failed: tuple[str, ...]
    inconclusive_reason: str | None
    source_count: int


def classify_global(
    statuses: Mapping[str, SourceStatus],
    *,
    analyzable: bool = True,
    inconclusive_reason: str | None = None,
) -> GlobalStatus:
    """Classify the global outcome from the per-source statuses (RF-13, RF-14).

    RF-13 takes precedence: when ``analyzable`` is false the run itself could
    not be analysed (no identifiable/readable run, or a run still in progress),
    so the outcome is ``GLOBAL_INCONCLUSIVE`` with ``inconclusive_reason`` — the
    reason received, or a default Spanish text when none is given — and the
    per-source lists stay empty. An analysable run with no sources at all cannot
    be classified either and is also ``GLOBAL_INCONCLUSIVE``.

    Otherwise the outcome is ``GLOBAL_CORRECT`` when every source is correct,
    ``GLOBAL_FAILED`` when every source is failed and ``GLOBAL_PARTIAL`` when
    correct and failed sources are mixed. Any source state other than
    ``SOURCE_CORRECT``/``SOURCE_FAILED`` is treated as failed (defensive: this
    module only ever produces those two states).

    The mapping order is preserved in ``correct`` and ``failed``.
    """
    source_count = len(statuses)
    if not analyzable:
        # Nothing is classified when the run cannot be analysed (RF-13).
        return GlobalStatus(
            state=GLOBAL_INCONCLUSIVE,
            correct=(),
            failed=(),
            inconclusive_reason=inconclusive_reason or _INCONCLUSIVE_DEFAULT,
            source_count=0,
        )
    if source_count == 0:
        return GlobalStatus(
            state=GLOBAL_INCONCLUSIVE,
            correct=(),
            failed=(),
            inconclusive_reason=_INCONCLUSIVE_NO_SOURCES,
            source_count=0,
        )

    correct = tuple(
        source_id
        for source_id, status_ in statuses.items()
        if status_.state == SOURCE_CORRECT
    )
    failed = tuple(
        source_id
        for source_id, status_ in statuses.items()
        if status_.state != SOURCE_CORRECT
    )

    if not failed:
        state = GLOBAL_CORRECT
    elif not correct:
        state = GLOBAL_FAILED
    else:
        state = GLOBAL_PARTIAL

    return GlobalStatus(
        state=state,
        correct=correct,
        failed=failed,
        inconclusive_reason=None,
        source_count=source_count,
    )


def is_inconclusive(global_status: GlobalStatus) -> bool:
    """Return whether a global outcome is inconclusive (RF-13)."""
    return global_status.state == GLOBAL_INCONCLUSIVE
