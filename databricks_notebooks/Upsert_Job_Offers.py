"""Batch dedup, quarantine routing and MERGE SQL for the bronze upsert.

The bronze ingestion (``bronze_ingest``) processes one microbatch per
``foreachBatch``. This module keeps that logic testable outside the notebook:

* :data:`SOURCE_SPECS` maps each source to its key column(s) and capture-time
  column in bronze (the four tables do not share names or types).
* :func:`split_quarantine` separates the rows that cannot be merged (missing
  key or capture time) and adds the quarantine metadata.
* :func:`dedupe_batch` keeps one row per key inside the batch: the latest
  capture, with ``_ingest_date`` as tie-breaker, reusing ``keep_latest_by``.
* :func:`merge_sql` builds the ``MERGE WITH SCHEMA EVOLUTION`` statement.
* :func:`new_quarantine_rows` removes the rows already stored or repeated in
  the batch before appending to the quarantine table.

Reserved control columns: ``_ingest_date``, ``_source_file``, ``_row_hash``,
``_scraped_ts`` and any column starting with ``_quarantine_``. They never take
part in the row hash.
"""
from dataclasses import dataclass
from functools import reduce
from typing import List, Mapping, Sequence, Tuple

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

import Deduplicate_Job_Offers as dedup

#: Name of the temporary view that exposes the microbatch to the MERGE.
BATCH_VIEW_NAME = "__batch"

INGEST_DATE_COLUMN = "_ingest_date"
SOURCE_FILE_COLUMN = "_source_file"
ROW_HASH_COLUMN = "_row_hash"
QUARANTINE_REASON_COLUMN = "_quarantine_reason"
QUARANTINE_AT_COLUMN = "_quarantine_at"
SCRAPED_TS_COLUMN = "_scraped_ts"

MISSING_KEY = "missing_key"
MISSING_SCRAPED_AT = "missing_scraped_at"

#: Columns that identify a quarantine row when writing the table.
QUARANTINE_IDENTITY_COLUMNS = (
    ROW_HASH_COLUMN,
    QUARANTINE_REASON_COLUMN,
    SOURCE_FILE_COLUMN,
)

_QUARANTINE_PREFIX = "_quarantine_"
_RESERVED_COLUMNS = frozenset(
    {
        INGEST_DATE_COLUMN,
        SOURCE_FILE_COLUMN,
        ROW_HASH_COLUMN,
        SCRAPED_TS_COLUMN,
    }
)


@dataclass(frozen=True)
class SourceSpec:
    """Bronze key column(s) and capture-time column of one source."""

    keys: Tuple[str, ...]
    scraped_at: str


#: Bronze layout per source (see ``design-upsert.md`` D0/D1).
SOURCE_SPECS: Mapping[str, SourceSpec] = {
    "indeed": SourceSpec(keys=("job_key",), scraped_at="scraped_at"),
    "linkedin": SourceSpec(keys=("job_id",), scraped_at="scraped_at"),
    "infojobs": SourceSpec(keys=("id_oferta",), scraped_at="fecha_scraped"),
    "multi_site": SourceSpec(keys=("site", "job_id"), scraped_at="scraped_at"),
}


def get_source_spec(source: str) -> SourceSpec:
    """Return the bronze spec of ``source``, or fail with a clear message."""
    try:
        return SOURCE_SPECS[source]
    except KeyError:
        valid = ", ".join(sorted(SOURCE_SPECS))
        raise ValueError(
            f"Fuente desconocida: {source!r}. Fuentes validas: {valid}."
        ) from None


def _source_columns(df: DataFrame) -> List[str]:
    """Payload columns of ``df``: everything except the reserved controls.

    The list comes from ``df.columns`` so a new source column (schema
    evolution) is part of the fingerprint without touching this module.
    """
    return [
        name
        for name in df.columns
        if name not in _RESERVED_COLUMNS
        and not name.startswith(_QUARANTINE_PREFIX)
    ]


def _missing_key_expr(keys: Sequence[str]) -> Column:
    """True when any key is NULL or blank (empty or spaces only).

    The key is cast to string and trimmed, so NULL, ``""`` and space-only
    values share the same semantics. Spark's ``trim`` removes ASCII spaces
    only; tabs and other Unicode whitespace are not covered.
    """
    expr = F.lit(False)
    for key in keys:
        expr = (
            expr
            | F.col(key).isNull()
            | (F.trim(F.col(key).cast("string")) == F.lit(""))
        )
    return expr


def row_hash(df: DataFrame) -> Column:
    """Content fingerprint (``_row_hash``) of the source columns of ``df``.

    ``sha2(to_json(struct(<source columns>)), 256)``: unlike ``concat_ws`` it
    distinguishes ``NULL`` from ``""`` and cannot collide when a value contains
    the separator. Timestamps are serialized in the session time zone, so the
    session must be fixed to UTC (the notebook and the tests do) for a stable
    fingerprint across environments.
    """
    source_columns = _source_columns(df)
    if not source_columns:
        raise ValueError(
            "El DataFrame no tiene columnas de fuente para calcular _row_hash."
        )
    struct = F.struct(*[F.col(name) for name in source_columns])
    return F.sha2(F.to_json(struct), 256)


def split_quarantine(df: DataFrame, source: str) -> Tuple[DataFrame, DataFrame]:
    """Split a bronze batch into ``(valid_df, quarantine_df)`` for ``source``.

    A row goes to quarantine when any key is NULL or blank
    (``missing_key``) or when its capture-time column is NULL, blank or
    unparseable (``try_to_timestamp(...) IS NULL``, ``missing_scraped_at``).
    When both conditions hold, ``missing_key`` wins.

    The valid frame keeps the input columns untouched. The quarantine frame
    adds ``_quarantine_reason``, ``_quarantine_at`` and ``_row_hash``, and
    keeps ``_source_file`` (NULL when the input does not carry it), so it can
    be appended to ``bronze.<source>_quarantine``.
    """
    spec = get_source_spec(source)
    hash_expr = row_hash(df)
    missing_key = _missing_key_expr(spec.keys)
    missing_scraped_at = F.try_to_timestamp(F.col(spec.scraped_at)).isNull()
    invalid = missing_key | missing_scraped_at

    valid = df.filter(~invalid)

    quarantine = df.filter(invalid)
    if SOURCE_FILE_COLUMN not in df.columns:
        quarantine = quarantine.withColumn(
            SOURCE_FILE_COLUMN, F.lit(None).cast("string")
        )
    quarantine = (
        quarantine
        .withColumn(ROW_HASH_COLUMN, hash_expr)
        .withColumn(
            QUARANTINE_REASON_COLUMN,
            F.when(missing_key, F.lit(MISSING_KEY)).otherwise(
                F.lit(MISSING_SCRAPED_AT)
            ),
        )
        .withColumn(QUARANTINE_AT_COLUMN, F.current_timestamp())
    )
    return valid, quarantine


def dedupe_batch(df: DataFrame, source: str) -> DataFrame:
    """Keep one row per key inside a batch: the latest capture.

    Adds an auxiliary ``_scraped_ts = try_to_timestamp(<capture column>)`` so
    ISO strings (LinkedIn, multi_site) and timestamps (Indeed, InfoJobs) sort
    by instant and not by date, reuses ``keep_latest_by`` with
    ``order_by=("_scraped_ts", "_ingest_date")`` and drops the auxiliary
    column: the output keeps the input column contract.

    Precondition: ``_ingest_date`` is present and the batch was split with
    :func:`split_quarantine` first; invalid rows are not filtered here.
    """
    spec = get_source_spec(source)
    with_instant = df.withColumn(
        SCRAPED_TS_COLUMN, F.try_to_timestamp(F.col(spec.scraped_at))
    )
    latest = dedup.keep_latest_by(
        with_instant,
        keys=list(spec.keys),
        order_by=(SCRAPED_TS_COLUMN, INGEST_DATE_COLUMN),
    )
    return latest.drop(SCRAPED_TS_COLUMN)


def merge_sql(target_table: str, source: str) -> str:
    """Build the bronze upsert statement for one source.

    ``target_table`` is the fully qualified bronze table and ``source`` the
    key in :data:`SOURCE_SPECS`; the microbatch is exposed as
    :data:`BATCH_VIEW_NAME`. The incoming row wins only when its capture
    instant is strictly newer, with ``_ingest_date`` as tie-breaker; a stored
    row without a valid capture time is repaired by the first valid capture.
    ``try_to_timestamp`` (never ``to_timestamp``) keeps ANSI mode from
    aborting the batch on blank or malformed values.
    """
    spec = get_source_spec(source)
    temporal = spec.scraped_at
    key_match = " AND ".join(f"t.{key} = s.{key}" for key in spec.keys)
    return (
        f"MERGE WITH SCHEMA EVOLUTION INTO {target_table} AS t\n"
        f"USING {BATCH_VIEW_NAME} AS s\n"
        f"ON {key_match}\n"
        "WHEN MATCHED AND (\n"
        f"    try_to_timestamp(t.{temporal}) IS NULL\n"
        f"    OR try_to_timestamp(s.{temporal}) > try_to_timestamp(t.{temporal})\n"
        f"    OR (try_to_timestamp(s.{temporal}) = try_to_timestamp(t.{temporal})\n"
        f"        AND s.{INGEST_DATE_COLUMN} > t.{INGEST_DATE_COLUMN})\n"
        "  ) THEN UPDATE SET *\n"
        "WHEN NOT MATCHED THEN INSERT *"
    )


def new_quarantine_rows(
    quarantine_df: DataFrame, existing_df: DataFrame
) -> DataFrame:
    """Return the quarantine rows that are not stored yet.

    Deduplicates the batch by the row identity (``_row_hash``,
    ``_quarantine_reason``, ``_source_file``) and drops the rows already
    present in ``existing_df`` with a left anti join on the same trio: a
    reprocessed batch does not grow the quarantine table and a row repeated in
    one batch is written once.
    """
    candidates = quarantine_df.dropDuplicates(list(QUARANTINE_IDENTITY_COLUMNS))
    stored = existing_df.select(*QUARANTINE_IDENTITY_COLUMNS)
    condition = reduce(
        lambda left, right: left & right,
        (
            F.col(f"new.{name}").eqNullSafe(F.col(f"old.{name}"))
            for name in QUARANTINE_IDENTITY_COLUMNS
        ),
    )
    return candidates.alias("new").join(stored.alias("old"), condition, "left_anti")
