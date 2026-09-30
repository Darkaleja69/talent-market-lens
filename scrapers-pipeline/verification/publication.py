"""Distinguish the obtained snapshot from the published delta (T-31).

The diagnostic measures two different populations (plan section 4.2):

- **Obtained** is the scraper's local snapshot (the "obtained" stage already
  measured by :mod:`verification.completeness`).
- **Published** is what the pipeline uploaded to the landing. For LinkedIn and
  Multi-site the pipeline uploads *only* the delta of new offers
  (``OnlyNewOffers`` / ``filter_new_offers.py``).

Because of that, an empty published delta (or the absence of a delta file
because there was nothing new) does **not** mean the scraper captured zero
offers: the snapshot may still contain offers. This module encodes that rule so
no other layer has to re-derive it:

- ``PUBLICATION_NO_NEW_OFFERS``: a visible, empty delta with a non-empty
  obtained snapshot. Everything was already uploaded.
- ``PUBLICATION_EMPTY``: an empty delta with an *explicitly measured* empty
  snapshot (``obtained_offers == 0``). This is the only state that means "zero
  offers captured" (RF-3).
- ``PUBLICATION_PENDING``: the expected object is not visible yet. Per RF-8
  this is reported as pending, never as an error.
- ``PUBLICATION_NOT_APPLICABLE``: the run prepared no data for the source, so
  there is nothing to publish. The absence of a manifest/object is not a
  pending upload; it is not an error and it never fails the source (RF-8).
- ``PUBLICATION_NOT_CHECKED``: the publication could not be confirmed (an
  unverifiable object, a missing published delta metric, or a snapshot that was
  not measured). "Not confirmed" is not the same as "not there yet" and it is
  never proof of zero captures.

Precedence in :func:`classify_publication` follows evidence strength: remote
manifest states first, then the offer counts. An object that is *visible but
unconfirmable* (``STATE_UNVERIFIED``) is ``NOT_CHECKED``, not ``PENDING``; an
absent snapshot (``obtained_offers is None``) is missing evidence, so it can
never make ``is_zero_offers_captured`` true (RF-3 handles missing evidence in
:mod:`verification.status`).

The module is pure: it imports :mod:`verification.landing` only for its state
constants and never lists or downloads anything. Publication checks that need
the landing live in :mod:`verification.landing`; the caller (T-38) combines
both.
"""
from __future__ import annotations

from dataclasses import dataclass

from verification import completeness, field_contract, landing

# Published-stage states (RF-6, RF-8). Plain strings so the report layer can
# render them without an extra enum import.
PUBLICATION_OK = "ok"  # the published delta is visible and carries offers
PUBLICATION_NO_NEW_OFFERS = "no_new_offers"  # empty delta, non-empty snapshot
PUBLICATION_EMPTY = "empty"  # empty/absent delta and snapshot: zero captured
PUBLICATION_PENDING = "pending"  # the expected object is not visible yet
PUBLICATION_MISMATCH = "mismatch"  # visible but inconsistent (rows/checksum)
PUBLICATION_REJECTED = "rejected"  # the manifest/object was rejected
PUBLICATION_NOT_CHECKED = "not_checked"  # the publication could not be checked
PUBLICATION_NOT_APPLICABLE = "not_applicable"  # no data to publish from the run


@dataclass(frozen=True)
class PublishedStage:
    """Publication outcome of one source.

    ``obtained_offers`` is the unique-offer count of the local snapshot (or
    ``None`` when it was not measured); ``delta_offers`` is the count of the
    published ``OnlyNewOffers`` delta (or ``None`` when it was not measured).
    """

    source: str
    state: str
    obtained_offers: int | None
    delta_offers: int | None
    detail: str | None = None


def classify_publication(
    *,
    obtained_offers: int | None,
    delta_offers: int | None,
    manifest_state: str | None = None,
    rejected: bool = False,
    not_checked: bool = False,
    not_applicable: bool = False,
) -> str:
    """Classify the publication stage of a source (RF-3, RF-6, RF-8).

    The remote/manifest states take precedence over the offer counts: a
    rejected manifest is a rejection even if the delta had offers, and an
    object that is not visible yet is pending. A visible-but-unconfirmable
    object (``STATE_UNVERIFIED``) is *not checked*, because RF-8 forbids
    declaring a publication correct without confirming it and forbids calling
    an unconfirmed object an error either.

    ``not_applicable`` marks a source whose run prepared nothing to publish:
    the absent manifest/object is neither pending nor an error and the source
    is never failed because of it (T-55, RF-8). It only applies when the check
    could actually run; ``not_checked`` takes precedence.

    A snapshot that was not measured (``obtained_offers is None``) is missing
    evidence, so it can never yield ``PUBLICATION_EMPTY``: only an explicit
    zero (``obtained_offers == 0``) proves zero captured offers (RF-3).
    """
    if not_checked:
        return PUBLICATION_NOT_CHECKED
    if not_applicable:
        return PUBLICATION_NOT_APPLICABLE
    if rejected or manifest_state == landing.STATE_REJECTED:
        return PUBLICATION_REJECTED
    if manifest_state == landing.STATE_MISMATCH:
        return PUBLICATION_MISMATCH
    if manifest_state == landing.STATE_PENDING:
        return PUBLICATION_PENDING
    # Visible but unconfirmable (unreadable, or no rows/sha256 to compare):
    # the publication cannot be declared correct, so it is not checked. It is
    # not "pending" (the object is visible) and not a mismatch (no evidence).
    if manifest_state == landing.STATE_UNVERIFIED:
        return PUBLICATION_NOT_CHECKED
    # No manifest/state at all (or an unexpected state) while a publication was
    # expected: the object is not visible yet -> pending, never an error.
    if manifest_state != landing.STATE_OK:
        return PUBLICATION_PENDING

    # The publication is visible; the delta metric alone decides the state.
    if delta_offers is None:
        # Visible object but no published metric: the delta cannot be judged.
        return PUBLICATION_NOT_CHECKED
    if delta_offers > 0:
        return PUBLICATION_OK
    # Empty delta: only an explicitly measured zero snapshot proves that zero
    # offers were captured. A snapshot that was not measured (None) is missing
    # evidence, not a zero, so it is not checked rather than empty.
    if obtained_offers is None:
        return PUBLICATION_NOT_CHECKED
    if obtained_offers > 0:
        return PUBLICATION_NO_NEW_OFFERS
    return PUBLICATION_EMPTY


def is_zero_offers_captured(state: str) -> bool:
    """Return True only when the state proves zero offers were captured.

    ``PUBLICATION_EMPTY`` is the single such state: it requires an explicitly
    measured empty snapshot (``obtained_offers == 0``) alongside an empty
    delta. An empty published delta is *not* proof of zero captures when the
    obtained snapshot has offers (``PUBLICATION_NO_NEW_OFFERS``), was not
    measured (``PUBLICATION_NOT_CHECKED``), is not visible yet
    (``PUBLICATION_PENDING``) or there was nothing to publish
    (``PUBLICATION_NOT_APPLICABLE``). This is the key rule of T-31 (RF-3,
    RF-8).
    """
    return state == PUBLICATION_EMPTY


@dataclass(frozen=True)
class FieldComparison:
    """Completeness difference of one field between obtained and published.

    ``difference`` is ``published_pct - obtained_pct`` in percentage points.
    The comparison is by percentage, never by absolute count: the obtained
    snapshot and the published ``OnlyNewOffers`` delta are different
    populations and are routinely different sizes (RF-6).
    """

    field: str
    required: bool
    obtained_pct: float
    published_pct: float
    difference: float


def _comparable_field_order(
    obtained: completeness.SourceCompleteness,
    published: completeness.SourceCompleteness,
) -> list[str]:
    """Return the measured fields present in both stages, in contract order.

    ``field_contract.MEASURED_FIELDS`` fixes the order of the shared fields;
    any extra field measured in both stages (not part of the canonical
    contract) is appended in ``published`` then ``obtained`` order. A field
    measured in only one stage is never invented in the other.
    """
    order: list[str] = []
    for field in field_contract.MEASURED_FIELDS:
        if field in obtained.fields and field in published.fields:
            order.append(field)
    for field in published.fields:
        if field in obtained.fields and field not in order:
            order.append(field)
    for field in obtained.fields:
        if field in published.fields and field not in order:
            order.append(field)
    return order


def compare_completeness(
    obtained: completeness.SourceCompleteness,
    published: completeness.SourceCompleteness,
) -> tuple[FieldComparison, ...]:
    """Compare the per-field completeness of the two stages (RF-6).

    Only fields measured in **both** stages are reported, so a field the
    published delta does not carry is not turned into a false difference.
    Percentages are compared, not raw counts, because the two populations
    (snapshot vs. published delta) can differ in size.
    """
    comparisons: list[FieldComparison] = []
    for field in _comparable_field_order(obtained, published):
        obtained_field = obtained.fields[field]
        published_field = published.fields[field]
        comparisons.append(
            FieldComparison(
                field=field,
                required=obtained_field.required,
                obtained_pct=obtained_field.completeness_pct,
                published_pct=published_field.completeness_pct,
                difference=(
                    published_field.completeness_pct
                    - obtained_field.completeness_pct
                ),
            )
        )
    return tuple(comparisons)


@dataclass(frozen=True)
class SourcePublication:
    """A source's obtained snapshot, published delta and derived stage."""

    source: str
    obtained: completeness.SourceCompleteness | None
    published: completeness.SourceCompleteness | None
    stage: PublishedStage


def build_source_publication(
    *,
    source: str,
    obtained: completeness.SourceCompleteness | None,
    published: completeness.SourceCompleteness | None,
    manifest_state: str | None = None,
    rejected: bool = False,
    not_checked: bool = False,
    not_applicable: bool = False,
) -> SourcePublication:
    """Combine both completeness stages into one classification (T-38).

    The offer counts are derived from the stages' ``total_offers`` (``None``
    when a stage was not measured) and handed to
    :func:`classify_publication`. Passing ``published=None`` models an absent
    delta; it can never yield ``PUBLICATION_EMPTY`` unless the obtained
    snapshot is also empty/absent. ``not_applicable`` marks a source with
    nothing prepared to publish (T-55).
    """
    obtained_offers = obtained.total_offers if obtained is not None else None
    delta_offers = published.total_offers if published is not None else None
    state = classify_publication(
        obtained_offers=obtained_offers,
        delta_offers=delta_offers,
        manifest_state=manifest_state,
        rejected=rejected,
        not_checked=not_checked,
        not_applicable=not_applicable,
    )
    stage = PublishedStage(
        source=source,
        state=state,
        obtained_offers=obtained_offers,
        delta_offers=delta_offers,
    )
    return SourcePublication(
        source=source,
        obtained=obtained,
        published=published,
        stage=stage,
    )
