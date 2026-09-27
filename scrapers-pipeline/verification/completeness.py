"""Structural readability checks for the obtained Parquet files.

T-25 (RF-3, RF-5): a Parquet file that cannot be read (corrupt or incomplete)
or that lacks any of its source's mandatory columns is a *structural failure*
of the source. This module only inspects readability and schema: per-offer
value validity and per-field completeness are computed elsewhere
(``field_contract.py`` and later tasks).

PyArrow is reused (already justified in the plan, section 6.3) and no new
dependency is added. The reading is intentionally side-effect free and never
raises on a bad file, so read failures stay data instead of crashes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import pyarrow.parquet as pq

from verification import sources


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
