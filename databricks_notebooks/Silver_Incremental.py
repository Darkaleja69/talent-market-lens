"""Incremental (MERGE) writes for the four silver offer tables.

The module keeps the logic shared by ``silver_indeed``, ``silver_linkedin``,
``silver_infojobs`` and ``silver_multisite`` testable outside the notebooks:

* :data:`SOURCE_SPECS` maps each source to the business key of its silver.
* :func:`key_from_columns` / :func:`key_expression` / :func:`key_sql` define
  the semantic group ``_key`` (title | company_name | location_city) once, as
  a ``Column`` and as SQL text for the ``MERGE`` ``ON`` clause.
* :func:`window_bounds` resolves the watermark window (``max(_ingest_date)``
  minus ``lookback_days``) and the only three reasons that force a rebuild.
* :func:`changed_since` filters bronze by ``_ingest_date``; the window is open
  when ``from_date`` is null (empty target with the column).
* :func:`select_candidates` selects the rows of the affected groups by exact
  key equality; :func:`recompute_groups` applies the notebook transform.
* :func:`orphan_markers` builds one row per affected group that has no rows
  left in bronze; the ``MERGE`` deletes it (``_ingest_date IS NULL``).
* :func:`merge_source` projects the source to the destination contract plus
  the new columns of the transform and validates it before writing.
* :func:`merge_sql` / :func:`merge_into_silver` build and run the single
  ``MERGE WITH SCHEMA EVOLUTION`` statement.
* :func:`run` is the only entry point that writes: it resolves the window,
  integrates the affected groups and returns the summary of the run.

The module does not know the business rules: the transform, the company
normalizer and the city column expression are callables supplied by each
notebook. ``_ingest_date`` is the only control column kept in silver.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

#: Name of the group key, computed on the fly (never stored).
KEY = "_key"
#: Columns that make up the semantic group of an offer.
KEY_COLUMNS = ("title", "company_name", "location_city")
#: Control column of silver: watermark of the run and of the window.
INGEST_DATE_COLUMN = "_ingest_date"
#: Working columns that must never reach the MERGE source.
RANK_COLUMN = "_rn"
#: Default window (one weekly cadence) to tolerate reruns and gaps.
DEFAULT_LOOKBACK_DAYS = 7

MODE_FULL = "full"
MODE_INCREMENTAL = "incremental"

REASON_FORCE_FULL = "force_full"
REASON_TABLE_MISSING = "table_missing"
REASON_MISSING_INGEST_DATE = "missing_ingest_date"

#: Fields of the summary returned by :func:`run` (design-incremental.md §5.5).
SUMMARY_FIELDS = (
    "source",
    "mode",
    "mode_reason",
    "watermark",
    "from_date",
    "to_date",
    "lookback_days",
    "changed_rows",
    "affected_groups",
    "recomputed_groups",
    "deleted_groups",
    "merge_rows",
    "new_columns",
    "written",
    "target_rows",
    "elapsed_s",
)


@dataclass(frozen=True)
class SourceSpec:
    """Business key of one source in silver (``job_id`` + ``site``)."""

    silver_keys: Tuple[str, ...]


#: Silver key per source (``multi_site`` adds the site to the offer id).
SOURCE_SPECS: Mapping[str, SourceSpec] = {
    "indeed": SourceSpec(("job_id",)),
    "linkedin": SourceSpec(("job_id",)),
    "infojobs": SourceSpec(("job_id",)),
    "multi_site": SourceSpec(("source_scraper", "job_id")),
}


@dataclass(frozen=True)
class Window:
    """Resolved window of one run (design-incremental.md §4).

    ``mode`` is ``full`` only for the three documented reasons; ``reason`` is
    ``None`` for an incremental run. ``mode_reason`` is the name of the same
    field in the run summary.
    """

    mode: str
    reason: Optional[str]
    watermark: Optional[dt.date]
    from_date: Optional[dt.date]
    to_date: Optional[dt.date]
    lookback_days: int

    @property
    def mode_reason(self) -> Optional[str]:
        """Alias of :attr:`reason` (name used by the run summary)."""
        return self.reason


def get_source_spec(source: str) -> SourceSpec:
    """Return the silver spec of ``source``, or fail with a clear message."""
    try:
        return SOURCE_SPECS[source]
    except KeyError:
        valid = ", ".join(sorted(SOURCE_SPECS))
        raise ValueError(
            f"Fuente desconocida: {source!r}. Fuentes validas: {valid}."
        ) from None


# ---------------------------------------------------------------------------
# Semantic key (one single definition, as Column and as SQL text)
# ---------------------------------------------------------------------------

def key_from_columns(title: Column, company: Column, city: Column) -> Column:
    """Semantic group key of a triple of columns (same rule as the dedup).

    ``concat_ws`` skips NULLs, the whitespace collapse goes over the joined
    string, ``trim`` only trims the ends and ``lower`` goes last. The function
    is applied to bronze (company and city already normalized) and to silver,
    so two different triples that produce the same key are the same dedup
    group on both sides.
    """
    return F.lower(F.trim(F.regexp_replace(
        F.concat_ws("|", title, company, city), r"\s+", " ")))


def key_expression(alias: Optional[str] = None) -> Column:
    """Key expression over the three standard columns, optionally aliased."""
    def col(name: str) -> Column:
        return F.col(f"{alias}.{name}") if alias else F.col(name)

    return key_from_columns(
        col(KEY_COLUMNS[0]), col(KEY_COLUMNS[1]), col(KEY_COLUMNS[2])
    )


def key_sql(alias: str) -> str:
    """Same key expression as SQL text, for the ``ON`` of the MERGE.

    The pattern needs a double backslash in SQL: Spark turns ``'\\s+'`` into
    ``s+`` and the collapse would not happen (P11 fixes both).
    """
    return (
        "lower(trim(regexp_replace(concat_ws('|', "
        f"{alias}.title, {alias}.company_name, {alias}.location_city), "
        r"'\\s+', ' ')))"
    )


# ---------------------------------------------------------------------------
# Window (watermark, lookback and force_full)
# ---------------------------------------------------------------------------

def as_date(value, *, name: str) -> Optional[dt.date]:
    """Parse a widget date: ``""``/``None`` -> ``None``, ISO or ValueError."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{name} debe ser una fecha ISO con formato YYYY-MM-DD: {value!r}."
        ) from None


def as_bool(value) -> bool:
    """Parse a widget boolean: ``""``/``"false"``/``"0"`` -> False (design §4).

    ``bool("false")`` is True, so the widget text must never be used as a
    Python boolean. ``None`` behaves like ``""``; any other value fails.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("", "false", "0"):
        return False
    if text in ("true", "1"):
        return True
    raise ValueError(
        f"Valor booleano no valido: {value!r}. Usa true/false (o 1/0)."
    )


def _parse_lookback_days(value) -> int:
    """Parse ``lookback_days``: ``""`` -> 7; negative or non-integer fails."""
    if value is None:
        return DEFAULT_LOOKBACK_DAYS
    if isinstance(value, bool):
        raise ValueError("lookback_days debe ser un entero no negativo.")
    text = str(value).strip()
    if not text:
        return DEFAULT_LOOKBACK_DAYS
    try:
        days = int(text)
    except ValueError:
        raise ValueError(
            f"lookback_days debe ser un entero no negativo: {value!r}."
        ) from None
    if days < 0:
        raise ValueError(
            f"lookback_days debe ser un entero no negativo: {value!r}."
        )
    return days


def _watermark(spark, target_table: str) -> Optional[dt.date]:
    """``max(_ingest_date)`` of an existing target, else ``None``."""
    if not spark.catalog.tableExists(target_table):
        return None
    target = spark.read.table(target_table)
    if INGEST_DATE_COLUMN not in target.columns:
        return None
    return target.agg(F.max(F.col(INGEST_DATE_COLUMN))).first()[0]


def window_bounds(
    spark,
    target_table: str,
    *,
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    from_date=None,
    to_date=None,
    force_full=False,
) -> Window:
    """Resolve the window of a run (design-incremental.md §4 D-B4).

    ``full`` only for ``force_full``, a missing table or a table without
    ``_ingest_date``. An empty table with the column is incremental with an
    open window (``from_date`` null). ``to_date`` requires ``from_date``
    unless ``force_full`` is set; ``from_date > to_date`` fails.
    """
    days = _parse_lookback_days(lookback_days)
    start = as_date(from_date, name="from_date")
    end = as_date(to_date, name="to_date")
    forced = as_bool(force_full)

    if end is not None and start is None and not forced:
        raise ValueError(
            "to_date requiere from_date: no hay tope superior sin inicio de "
            "ventana."
        )
    if start is not None and end is not None and start > end:
        raise ValueError("from_date no puede ser posterior a to_date.")

    if forced:
        return Window(
            mode=MODE_FULL,
            reason=REASON_FORCE_FULL,
            watermark=_watermark(spark, target_table),
            from_date=None,
            to_date=None,
            lookback_days=days,
        )
    if not spark.catalog.tableExists(target_table):
        return Window(
            mode=MODE_FULL,
            reason=REASON_TABLE_MISSING,
            watermark=None,
            from_date=None,
            to_date=None,
            lookback_days=days,
        )

    target = spark.read.table(target_table)
    if INGEST_DATE_COLUMN not in target.columns:
        return Window(
            mode=MODE_FULL,
            reason=REASON_MISSING_INGEST_DATE,
            watermark=None,
            from_date=None,
            to_date=None,
            lookback_days=days,
        )

    watermark = target.agg(F.max(F.col(INGEST_DATE_COLUMN))).first()[0]
    if start is None and watermark is not None:
        start = watermark - dt.timedelta(days=days)
    return Window(
        mode=MODE_INCREMENTAL,
        reason=None,
        watermark=watermark,
        from_date=start,
        to_date=end,
        lookback_days=days,
    )


# ---------------------------------------------------------------------------
# Affected groups and recompute
# ---------------------------------------------------------------------------

def changed_since(prepared: DataFrame, window: Window) -> DataFrame:
    """Rows of ``prepared`` whose ``_ingest_date`` falls in the window.

    The window is open (no filter) when ``window.from_date`` is null, which
    is the empty-target case; ``to_date`` only applies when requested.
    """
    if INGEST_DATE_COLUMN not in prepared.columns:
        raise ValueError("Falta _ingest_date en el bronce preparado.")
    if dict(prepared.dtypes)[INGEST_DATE_COLUMN] != "date":
        raise ValueError("_ingest_date debe ser de tipo date.")
    condition = F.lit(True)
    if window.from_date is not None:
        condition = condition & (
            F.col(INGEST_DATE_COLUMN) >= F.lit(window.from_date)
        )
    if window.to_date is not None:
        condition = condition & (
            F.col(INGEST_DATE_COLUMN) <= F.lit(window.to_date)
        )
    return prepared.where(condition)


def select_candidates(keyed: DataFrame, affected: DataFrame) -> DataFrame:
    """Rows of ``keyed`` whose exact key is in the affected set."""
    return keyed.join(affected, KEY, "left_semi").drop(KEY)


def recompute_groups(
    candidates: DataFrame, *, transform: Callable[[DataFrame], DataFrame]
) -> DataFrame:
    """Recompute the affected groups with the notebook's own chain."""
    return transform(candidates)


def require_no_null_ingest_date(recompute: DataFrame) -> None:
    """Fail before the MERGE when a recomputed row has no ``_ingest_date``.

    A NULL is the marker discriminator of the MERGE: a real row with a NULL
    would delete its group instead of updating it.
    """
    if INGEST_DATE_COLUMN not in recompute.columns:
        raise ValueError("El recalculo no tiene _ingest_date.")
    nulls = recompute.where(F.col(INGEST_DATE_COLUMN).isNull()).limit(1)
    if not nulls.isEmpty():
        raise ValueError(
            "El recalculo tiene filas con _ingest_date nulo: no se puede "
            "distinguir una fila real de un marcador de borrado."
        )


def require_unique_groups(recompute: DataFrame) -> None:
    """Fail before the MERGE when a group has more than one source row."""
    duplicated = (
        recompute.select(key_expression().alias(KEY))
        .groupBy(KEY)
        .count()
        .where(F.col("count") > 1)
        .limit(1)
    )
    if not duplicated.isEmpty():
        raise ValueError(
            "El recalculo devuelve mas de una fila por grupo (_key): el MERGE "
            "fallaria por multiples filas del origen para una fila del "
            "destino."
        )


def orphan_markers(
    target: DataFrame, affected: DataFrame, recompute_keys: DataFrame
) -> DataFrame:
    """One marker per affected group that has no rows left in bronze.

    The marker is the target row with ``_ingest_date`` forced to NULL, which
    is the only discriminator the MERGE needs to delete it.
    """
    return (
        target.withColumn(KEY, key_expression())
        .join(affected, KEY, "left_semi")
        .join(recompute_keys, KEY, "left_anti")
        .drop(KEY)
        .withColumn(INGEST_DATE_COLUMN, F.lit(None).cast("date"))
    )


# ---------------------------------------------------------------------------
# MERGE source and statement
# ---------------------------------------------------------------------------

def _internal_columns(df: DataFrame) -> List[str]:
    """Working columns of ``df``: every ``_`` column but the control one."""
    return [
        name
        for name in df.columns
        if name.startswith("_") and name != INGEST_DATE_COLUMN
    ]


def merge_source(
    recompute: DataFrame,
    markers: DataFrame,
    target_columns: Sequence[str],
) -> DataFrame:
    """Project the MERGE source to the contract plus the transform's columns.

    The source has the destination columns in destination order and, at the
    end, the columns the transform added (schema evolution). The markers take
    those new columns as typed NULLs. A missing contract column or an
    internal column (``_key``, ``_rn``, ``_source_file``, ...) fails before
    writing.
    """
    target_columns = list(target_columns)
    internal = _internal_columns(recompute)
    if internal:
        raise ValueError(
            "El recalculo conserva columnas internas ("
            + ", ".join(internal)
            + "); no pueden llegar al MERGE."
        )
    internal = _internal_columns(markers)
    if internal:
        raise ValueError(
            "Los marcadores conservan columnas internas ("
            + ", ".join(internal)
            + "); no pueden llegar al MERGE."
        )
    missing = [
        name for name in target_columns if name not in recompute.columns
    ]
    if missing:
        raise ValueError(
            "Al recalculo le faltan columnas del contrato de la silver: "
            + ", ".join(missing)
            + "."
        )
    missing_markers = [
        name for name in target_columns if name not in markers.columns
    ]
    if missing_markers:
        raise ValueError(
            "A los marcadores les faltan columnas del contrato de la silver: "
            + ", ".join(missing_markers)
            + "."
        )

    new_columns = [
        name for name in recompute.columns if name not in target_columns
    ]
    ordered = target_columns + new_columns
    projected = recompute.select(*ordered)
    marker_rows = markers.select(*target_columns)
    for name in new_columns:
        marker_rows = marker_rows.withColumn(
            name, F.lit(None).cast(recompute.schema[name].dataType)
        )
    return projected.unionByName(marker_rows.select(*ordered))


def merge_sql(target_table: str, view_name: str) -> str:
    """Build the incremental statement of one source (design §3 D-B6).

    Order of the ``WHEN`` clauses: DELETE (with condition) before UPDATE
    (unconditional) before INSERT (with condition). ``UPDATE/INSERT *`` need
    ``WITH SCHEMA EVOLUTION`` to add the new columns of the transform.
    """
    return (
        f"MERGE WITH SCHEMA EVOLUTION INTO {target_table} AS t\n"
        f"USING {view_name} AS s\n"
        f"ON  {key_sql('t')}\n"
        f"  = {key_sql('s')}\n"
        f"WHEN MATCHED AND s.{INGEST_DATE_COLUMN} IS NULL THEN DELETE\n"
        "WHEN MATCHED THEN UPDATE SET *\n"
        f"WHEN NOT MATCHED AND s.{INGEST_DATE_COLUMN} IS NOT NULL THEN "
        "INSERT *"
    )


def merge_into_silver(
    spark, target_table: str, merge_src: DataFrame, *, view_name: str
) -> None:
    """Publish the source as a unique temporary view and run the MERGE.

    The view name is generated per source and run, and it is always dropped,
    also when the statement fails. MERGE only restricts a view as the
    destination, not as the source.
    """
    if not spark.catalog.tableExists(target_table):
        raise ValueError(
            "La tabla destino no existe: ejecuta con force_full o revisa la "
            "migracion."
        )
    merge_src.createOrReplaceTempView(view_name)
    try:
        spark.sql(merge_sql(target_table, view_name))
    finally:
        spark.catalog.dropTempView(view_name)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(
    spark,
    target_table: str,
    prepared: DataFrame,
    *,
    source: str,
    transform: Callable[[DataFrame], DataFrame],
    normalize_company: Callable[[DataFrame], DataFrame],
    normalize_city_column: Callable[[Column], Column],
    lookback_days=DEFAULT_LOOKBACK_DAYS,
    from_date=None,
    to_date=None,
    force_full=False,
    repeat_window=False,
) -> dict:
    """One incremental run, or two plus the repeat envelope.

    ``prepared`` is the bronze frame after the rename and the cheap
    derivations of the notebook (read once there). ``transform`` is the
    notebook chain; ``normalize_company`` and ``normalize_city_column`` are
    the same callables the chain uses, applied to bronze to compute the exact
    key of the affected groups. ``repeat_window`` parses its own value and
    wraps two runs with the same arguments (T-10 run B); the notebook never
    builds the envelope.
    """
    source_spec = get_source_spec(source)  # fail fast, before any work
    repeat = as_bool(repeat_window)
    arguments = dict(
        spark=spark,
        target_table=target_table,
        prepared=prepared,
        source=source,
        source_spec=source_spec,
        transform=transform,
        normalize_company=normalize_company,
        normalize_city_column=normalize_city_column,
        lookback_days=lookback_days,
        from_date=from_date,
        to_date=to_date,
        force_full=force_full,
    )
    first = _run_once(**arguments)
    if not repeat:
        return first
    second = _run_once(**arguments)
    return {"repeat_window": True, "runs": [first, second]}


def _run_once(
    spark,
    target_table: str,
    prepared: DataFrame,
    *,
    source: str,
    source_spec: SourceSpec,
    transform: Callable[[DataFrame], DataFrame],
    normalize_company: Callable[[DataFrame], DataFrame],
    normalize_city_column: Callable[[Column], Column],
    lookback_days,
    from_date,
    to_date,
    force_full,
) -> dict:
    """One run: window, affected groups, recompute and a single commit."""
    started = time.monotonic()
    bounds = window_bounds(
        spark,
        target_table,
        lookback_days=lookback_days,
        from_date=from_date,
        to_date=to_date,
        force_full=force_full,
    )

    if bounds.mode == MODE_FULL:
        offers = transform(prepared).cache()
        try:
            rows = offers.count()
            offers.write.mode("overwrite") \
                .option("overwriteSchema", "true") \
                .saveAsTable(target_table)
        finally:
            offers.unpersist()
        return _summary(
            source,
            bounds,
            changed_rows=0,
            affected_groups=0,
            recomputed_groups=rows,
            deleted_groups=0,
            merge_rows=0,
            new_columns=[],
            written=True,
            target_rows=spark.read.table(target_table).count(),
            elapsed_s=time.monotonic() - started,
        )

    target = spark.read.table(target_table)  # window_bounds checked it exists
    keys = list(source_spec.silver_keys)
    cached: List[DataFrame] = []

    def keep(df: DataFrame) -> DataFrame:
        df = df.cache()
        cached.append(df)
        return df

    try:
        # 1. Exact key over bronze: same normalization the silver will use.
        keyed = keep(
            normalize_company(prepared).withColumn(
                KEY,
                key_from_columns(
                    F.col(KEY_COLUMNS[0]),
                    F.col(KEY_COLUMNS[1]),
                    normalize_city_column(F.col(KEY_COLUMNS[2])),
                ),
            )
        )

        # 2. Window.
        changed = keep(changed_since(keyed, bounds))
        if changed.isEmpty():
            return _summary(
                source,
                bounds,
                changed_rows=0,
                affected_groups=0,
                recomputed_groups=0,
                deleted_groups=0,
                merge_rows=0,
                new_columns=[],
                written=False,
                target_rows=target.count(),
                elapsed_s=time.monotonic() - started,
            )

        # 3. Affected groups: new keys (bronze) and old keys (silver by id).
        keys_new = changed.select(F.col(KEY)).distinct()
        changed_ids = changed.select(*keys)
        old_groups = target.join(changed_ids, keys, "left_semi")
        keys_old = old_groups.select(key_expression().alias(KEY))
        affected = keep(keys_new.unionByName(keys_old).distinct())

        # 4. Candidates: exact equality of the key, no superset.
        candidates = select_candidates(keyed, affected)

        # 5. Full group recompute with the notebook chain.
        recompute = keep(recompute_groups(candidates, transform=transform))
        require_no_null_ingest_date(recompute)
        require_unique_groups(recompute)

        # The key is derived here: the chain already dropped it from recompute.
        recompute_keys = keep(
            recompute.select(key_expression().alias(KEY)).distinct()
        )

        # 6. Affected groups that have no rows left in bronze.
        markers = keep(orphan_markers(target, affected, recompute_keys))

        # 7. Source of the MERGE: contract + new columns, no internal ones.
        merge_src = keep(merge_source(recompute, markers, target.columns))

        # 8. Inspect the cached frames while the target is still the old one,
        #    then a single commit with a view name unique per run.
        changed_rows = changed.count()
        affected_groups = affected.count()
        recomputed_groups = recompute.count()
        deleted_groups = markers.count()
        merge_rows = merge_src.count()
        new_columns = [
            name for name in recompute.columns if name not in target.columns
        ]
        view_name = f"__silver_batch_{source}_{uuid4().hex[:8]}"
        merge_into_silver(
            spark, target_table, merge_src, view_name=view_name
        )

        return _summary(
            source,
            bounds,
            changed_rows=changed_rows,
            affected_groups=affected_groups,
            recomputed_groups=recomputed_groups,
            deleted_groups=deleted_groups,
            merge_rows=merge_rows,
            new_columns=new_columns,
            written=True,
            target_rows=spark.read.table(target_table).count(),
            elapsed_s=time.monotonic() - started,
        )
    finally:
        for df in cached:
            df.unpersist()


def _iso_date(value: Optional[dt.date]) -> Optional[str]:
    """Serialize a date as ``YYYY-MM-DD`` (``None`` stays ``None``)."""
    return value.isoformat() if value is not None else None


def _summary(
    source: str,
    bounds: Window,
    *,
    changed_rows: int,
    affected_groups: int,
    recomputed_groups: int,
    deleted_groups: int,
    merge_rows: int,
    new_columns: Iterable[str],
    written: bool,
    target_rows: int,
    elapsed_s: float,
) -> dict:
    """16-field summary of a run (design-incremental.md §5.5)."""
    return {
        "source": source,
        "mode": bounds.mode,
        "mode_reason": bounds.reason,
        "watermark": _iso_date(bounds.watermark),
        "from_date": _iso_date(bounds.from_date),
        "to_date": _iso_date(bounds.to_date),
        "lookback_days": bounds.lookback_days,
        "changed_rows": int(changed_rows),
        "affected_groups": int(affected_groups),
        "recomputed_groups": int(recomputed_groups),
        "deleted_groups": int(deleted_groups),
        "merge_rows": int(merge_rows),
        "new_columns": list(new_columns),
        "written": bool(written),
        "target_rows": int(target_rows),
        "elapsed_s": round(float(elapsed_s), 1),
    }
