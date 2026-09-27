"""Structural readability checks for the obtained Parquet files.

T-25 (RF-3, RF-5): a Parquet file that cannot be read (corrupt or incomplete)
or that lacks any of its source's mandatory columns is a *structural failure*
of the source. This module only inspects readability and schema: per-offer
value validity and per-field completeness are computed elsewhere
(``field_contract.py`` and later tasks).

T-26 (RF-4, RF-5): completeness is measured over *unique* offers, so this
module also deduplicates rows by each source's own key (``job_key``,
``job_id`` or ``id_oferta``). The deduplication is pure and independent of
PyArrow: a table is only converted to plain dicts first.

PyArrow is reused (already justified in the plan, section 6.3) and no new
dependency is added. The reading is intentionally side-effect free and never
raises on a bad file, so read failures stay data instead of crashes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

import pyarrow as pa
import pyarrow.parquet as pq

from verification import field_contract, sources


@dataclass(frozen=True)
class ObtainedParquet:
    """Result of reading one obtained Parquet file."""

    source: str | None
    path: str
    readable: bool
    rows: int | None
    columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    error: str | None


def read_obtained_parquet(
    path: str, required_columns: tuple[str, ...] = ()
) -> ObtainedParquet:
    """Read one Parquet file and report its structure without raising.

    A corrupt or incomplete file yields ``readable=False``, ``rows=None`` and
    the ``repr`` of the exception in ``error``. A readable file reports its
    row count, the schema columns and which required columns are missing.
    """
    try:
        table = pq.read_table(path)
    except Exception as exc:
        return ObtainedParquet(
            source=None,
            path=str(path),
            readable=False,
            rows=None,
            columns=(),
            missing_columns=(),
            error=repr(exc),
        )
    columns = tuple(table.schema.names)
    missing = tuple(col for col in required_columns if col not in columns)
    return ObtainedParquet(
        source=None,
        path=str(path),
        readable=True,
        rows=table.num_rows,
        columns=columns,
        missing_columns=missing,
        error=None,
    )


def is_structural_failure(result: ObtainedParquet) -> bool:
    """Return True when a file is unreadable or lacks mandatory columns.

    This is the "structural non-compliance" signal RF-3 uses to mark a source
    as failed; it does not judge per-offer field values.
    """
    return (not result.readable) or bool(result.missing_columns)


def read_source_parquet(path: str, source_id: str) -> ObtainedParquet:
    """Read a source's Parquet using its catalog mandatory columns."""
    result = read_obtained_parquet(path, sources.required_columns(source_id))
    return replace(result, source=source_id)


def deduplicate_offers(
    rows: Iterable[Mapping[str, object]], dedup_key: str
) -> tuple[dict, ...]:
    """Return the unique offers keyed by ``dedup_key``, in first-seen order.

    The key of each row is read from ``dedup_key`` and normalized with
    ``str(value).strip()``. The comparison is exact and case-sensitive after
    that ``strip`` because job identifiers are opaque strings.

    A row whose key is absent (no such column, ``None``), empty or
    whitespace-only cannot be *proven* to duplicate another row, so each such
    row counts as a unique offer of its own (positional identity). This keeps
    the offer count honest and lets the field completeness of ``id`` later
    count those missing keys as ``ABSENT``. For present keys, the first
    appearance wins and its dict is the one preserved.
    """
    unique: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        value = row.get(dedup_key)
        key = "" if value is None else str(value).strip()
        if key == "":
            # Absent/empty key: cannot be proven a duplicate, keep it as-is.
            unique.append(dict(row))
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(row))
    return tuple(unique)


def unique_offers(table: pa.Table, source_id: str) -> tuple[dict, ...]:
    """Return the unique offers of a PyArrow table for a source.

    Uses the source's own deduplication key from the catalog
    (``sources.dedup_key``), i.e. ``job_key``, ``job_id`` or ``id_oferta``.
    """
    return deduplicate_offers(table.to_pylist(), sources.dedup_key(source_id))


def count_unique_offers(table: pa.Table, source_id: str) -> int:
    """Return the number of unique offers of a PyArrow table for a source."""
    return len(unique_offers(table, source_id))


# --- T-27: per-field validity and completeness (RF-4, RF-5) ------------------


@dataclass(frozen=True)
class FieldCompleteness:
    """Validity counts and completeness percentage of one canonical field.

    ``valid``, ``absent`` and ``invalid`` are mutually exclusive per offer and
    always add up to ``total``. ``completeness_pct`` is the share of valid
    values (0.0 when there are no offers, avoiding a division by zero).
    """

    field: str
    required: bool
    total: int
    valid: int
    absent: int
    invalid: int
    completeness_pct: float


@dataclass(frozen=True)
class SourceCompleteness:
    """Per-field completeness of one source, over its unique offers.

    ``fields`` holds exactly one entry per ``field_contract.MEASURED_FIELDS``,
    in that order. ``total_offers`` is the shared denominator of every field.
    """

    source: str
    total_offers: int
    fields: dict[str, FieldCompleteness]


def classify_offer_field(
    field: str, offer: Mapping[str, object], aliases: tuple[str, ...]
) -> field_contract.FieldStatus:
    """Classify one offer's value for a canonical field through its aliases.

    ``aliases`` are the origin columns a source may publish the data under, in
    preference order (e.g. ``description_full`` before ``description_snippet``).
    A field with no aliases (Indeed/InfoJobs ``skills``) is not published by
    that source at all, so it is ``ABSENT`` and never ``INVALID``.

    The combination rule over the aliases present in the offer is
    "valid beats invalid beats absent": the first ``VALID`` alias wins;
    otherwise any ``INVALID`` alias makes the field ``INVALID``; otherwise the
    field is ``ABSENT``.
    """
    if not aliases:
        return field_contract.ABSENT
    saw_invalid = False
    for alias in aliases:
        if alias not in offer:
            continue
        status = field_contract.classify(field, offer[alias])
        if status is field_contract.VALID:
            return field_contract.VALID
        if status is field_contract.INVALID:
            saw_invalid = True
    return field_contract.INVALID if saw_invalid else field_contract.ABSENT


def _completeness_pct(valid: int, total: int) -> float:
    """Return the valid share in percent (0.0 when there are no offers)."""
    if total == 0:
        return 0.0
    return 100.0 * valid / total


def measure_offers_completeness(
    offers: Iterable[Mapping[str, object]], source_id: str
) -> SourceCompleteness:
    """Measure per-field completeness over already-unique offers.

    This is the pure core: ``offers`` is any iterable of dicts and no PyArrow
    is involved. It is materialised once so every canonical field
    (``field_contract.MEASURED_FIELDS``) is counted on the same population.
    ``total`` is the number of offers and the denominator shared by all fields.
    """
    unique = tuple(offers)
    total = len(unique)
    fields: dict[str, FieldCompleteness] = {}
    for field in field_contract.MEASURED_FIELDS:
        aliases = sources.field_aliases(source_id, field)
        valid = absent = invalid = 0
        for offer in unique:
            status = classify_offer_field(field, offer, aliases)
            if status is field_contract.VALID:
                valid += 1
            elif status is field_contract.INVALID:
                invalid += 1
            else:
                absent += 1
        fields[field] = FieldCompleteness(
            field=field,
            required=field_contract.is_required(field),
            total=total,
            valid=valid,
            absent=absent,
            invalid=invalid,
            completeness_pct=_completeness_pct(valid, total),
        )
    return SourceCompleteness(source=source_id, total_offers=total, fields=fields)


def measure_completeness(table: pa.Table, source_id: str) -> SourceCompleteness:
    """Measure per-field completeness of a source's unique offers.

    Convenience entry point: the table is deduplicated first (RF-4) with
    ``unique_offers`` so duplicates never inflate the denominator, then the
    pure core does the counting.
    """
    return measure_offers_completeness(unique_offers(table, source_id), source_id)
