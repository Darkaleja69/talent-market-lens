"""Per-source state classification from completeness thresholds.

T-28 (RF-3, RF-10, RF-14): given a source's measured completeness this module
decides whether the source is ``correct`` or ``failed`` and records why. It
implements the business rules behind:

- RF-3: a mandatory field below 90 % fails its source; zero offers, missing
  evidence and structural non-compliance also fail it;
- RF-10: which fields must be investigated with the real website (mandatory
  fields below 100 % and non-mandatory fields at 60 % or less);
- RF-14: each independent source is classified only as ``correct`` or
  ``failed``.

Only **per-source** classification lives here. The global outcome
(``correct``/``partial``/``failed``/``inconclusive``) is a separate concern and
is deliberately not implemented in this module.
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

# Readable reason fragments (code-facing text stays in English).
_MISSING_EVIDENCE = "missing evidence"
_ZERO_OFFERS = "zero offers"
_STRUCTURAL_FAILURE = "structural failure"


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

    1. missing evidence (``evidence_available`` false or ``completeness`` None);
    2. structural failure (unreadable file or missing mandatory columns);
    3. zero offers;
    4. per-field thresholds over ``completeness.fields``.

    For step 4, a mandatory field below 90 % fails the source; a mandatory
    field between 90 % (inclusive) and 100 % is an incident that does not fail
    it; a non-mandatory field at 60 % or less only triggers investigation.
    Mandatory fields below 100 % and non-mandatory fields at 60 % or less are
    always listed in ``investigation_fields`` (RF-10). Only the fields present
    in ``completeness`` are evaluated; no thresholds are invented for fields
    that were not measured.
    """
    if not evidence_available or completeness is None:
        return _failed(source_id, _MISSING_EVIDENCE)

    if structural_failure:
        reason = (
            f"{_STRUCTURAL_FAILURE}: {structural_error}"
            if structural_error
            else _STRUCTURAL_FAILURE
        )
        return _failed(source_id, reason)

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
