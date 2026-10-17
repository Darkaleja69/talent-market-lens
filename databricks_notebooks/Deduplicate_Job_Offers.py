"""Early dedup of job offers: keep only the latest capture per key.

Shared by the silver notebooks (LinkedIn and multisite) to reduce the
accumulated bronze snapshots to one row per offer before ``enrich()`` and the
company tables run, without changing the output column contract.
"""
from typing import Sequence

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

_KEEP_LATEST_RN_COLUMN = "_keep_latest_rn"


def _missing_key_expr(keys: Sequence[str]):
    """Boolean expression that is true for rows with a missing key.

    A key is missing when it is NULL or blank after casting to string and
    trimming, so NULL, ``""`` and space-only values share the same semantics.
    ``blank`` here means empty or spaces only: Spark's ``trim`` removes ASCII
    spaces, so tabs and other Unicode whitespace are not covered.
    """
    expr = None
    for key in keys:
        missing = (
            F.col(key).isNull()
            | (F.trim(F.col(key).cast("string")) == F.lit(""))
        )
        expr = missing if expr is None else (expr | missing)
    return expr


def keep_latest_by(
    df: DataFrame,
    keys: Sequence[str],
    order_by: Sequence[str] = ("scraped_at", "_ingest_date"),
) -> DataFrame:
    """Return one row per key combination: the latest capture.

    A window (``partitionBy(keys).orderBy(order_by DESC NULLS LAST)``) plus
    ``row_number()`` is used instead of ``dropDuplicates``: the latter keeps
    an arbitrary row per key and would drop the most recent capture.

    Rows whose key is NULL or blank (empty or spaces only) are a preventive
    guard: they cannot be grouped safely, so all of them are passed through
    untouched.

    Order precondition: the ``order_by`` columns must sort equivalently to
    the chronological order. In this project ``scraped_at`` is an ISO-8601
    string with the single ``+00:00`` offset, so its lexicographic order
    matches the chronological one; ``_ingest_date`` is the tie-breaker. On
    exact ties any of the tied rows is a valid latest capture.

    It runs a single window pass, adds no columns and changes no types: the
    output column contract is identical to the input one.
    """
    key_columns = list(keys)
    order_columns = list(order_by)
    if not key_columns:
        raise ValueError("keys must contain at least one column")
    if not order_columns:
        raise ValueError("order_by must contain at least one column")

    missing_expr = _missing_key_expr(key_columns)
    window = Window.partitionBy(*key_columns).orderBy(
        *[F.col(name).desc_nulls_last() for name in order_columns]
    )

    return (
        df.withColumn(_KEEP_LATEST_RN_COLUMN, F.row_number().over(window))
        .filter((F.col(_KEEP_LATEST_RN_COLUMN) == 1) | missing_expr)
        .drop(_KEEP_LATEST_RN_COLUMN)
    )
