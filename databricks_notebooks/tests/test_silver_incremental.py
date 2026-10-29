"""Tests for Silver_Incremental (real Spark DataFrames, spec 008 T-08).

No ``delta-spark`` locally, so the MERGE semantics are exercised with a
reference implementation of the statement of ``design-incremental.md`` §3
D-B6 (upsert by ``_key`` of the rows with ``_ingest_date`` plus deletion of
the marker keys), published again as the target temporary view. ``run`` is
exercised end to end with ``merge_into_silver`` monkeypatched, so the window,
the affected groups, the recompute, the markers and the summary all run. The
real Delta MERGE, ``MERGE WITH SCHEMA EVOLUTION`` and the ``USING`` over a
temporary view are validated in the dev run (T-10) and in production (T-11).

The Spark session is the same kind used by ``test_upsert_job_offers.py``
(local, one core, UTC).
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import DataFrameWriter  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import Window as SparkWindow  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Deduplicate_Job_Offers as dedup  # noqa: E402
import Enrich_Job_Offers_Dataframes as de  # noqa: E402
import Prepare_Gold as gold  # noqa: E402
import Prepare_PBI_Export as pbi  # noqa: E402
import Prepare_Web_Export as web  # noqa: E402
import Silver_Incremental as inc  # noqa: E402


# ---------------------------------------------------------------------------
# SparkSession for tests (same Windows config and UTC zone as the other
# integration modules, so timestamps and dates do not depend on the machine).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def spark():
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("silver-incremental-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.extraJavaOptions", ipv4)
        .config("spark.executor.extraJavaOptions", ipv4)
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .config("spark.executorEnv.PYSPARK_PYTHON", sys.executable)
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    previous_time_zone = session.conf.get("spark.sql.session.timeZone", None)
    session.conf.set("spark.sql.session.timeZone", "UTC")
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 1).map(lambda x: x).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    yield session
    if previous_time_zone is not None:
        session.conf.set("spark.sql.session.timeZone", previous_time_zone)
    session.stop()


# ---------------------------------------------------------------------------
# Silver contract of the notebooks (copied from df_structure_Schema of
# silver_indeed / offers_df_structure_Schema of silver_linkedin) and the
# per-source drop/rename of the offers chain.
# ---------------------------------------------------------------------------

CONTRACT = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("job_url", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("company_name", T.StringType(), True),
    T.StructField("location_city", T.StringType(), True),
    T.StructField("location_region", T.StringType(), True),
    T.StructField("location_country", T.StringType(), True),
    T.StructField("posted_date", T.DateType(), True),
    T.StructField("work_mode", T.StringType(), True),
    T.StructField("is_salary_available", T.BooleanType(), True),
    T.StructField("salary_currency", T.StringType(), True),
    T.StructField("salary_min", T.DoubleType(), True),
    T.StructField("salary_max", T.DoubleType(), True),
    T.StructField("salary_period", T.StringType(), True),
    T.StructField("salary_raw", T.StringType(), True),
    T.StructField("experience_level", T.StringType(), True),
    T.StructField("description_clean", T.StringType(), True),
    T.StructField("skills", T.StringType(), True),
    T.StructField("source_scraper", T.StringType(), True),
    T.StructField("search_role", T.StringType(), True),
    T.StructField("scraped_at", T.TimestampType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
])
CONTRACT_DEFAULTS = {field.name: None for field in CONTRACT.fields}

#: ``drop`` of the offers cell of each silver (``_ingest_date`` is kept: it is
#: the control column of the incremental).
SILVER_DROPS = {
    "indeed": ["salary_min", "salary_max", "scraped_at", "salary_period",
               "work_mode"],
    "linkedin": ["scraped_at", "salary_period", "work_mode"],
    "infojobs": ["scraped_at", "salary_period", "work_mode"],
    "multi_site": ["scraped_at", "salary_period", "work_mode"],
}

#: Measured contract size per source (design §1.3), never a shared list.
TARGET_SIZES = {
    "indeed": 31,
    "linkedin": 33,
    "infojobs": 33,
    "multi_site": 33,
}

_NORM_RENAMES = {"salary_period_norm": "salary_period",
                 "work_mode_norm": "work_mode"}


def _enriched_contract(spark):
    """Empty frame with the 22 contract columns already enriched (36)."""
    empty = spark.createDataFrame([], schema=CONTRACT)
    return de.enrich(empty, add_location_fill=True,
                     normalize_location_city=True)


def _silver_frame(spark, source):
    """Empty silver frame of ``source``: contract - drops + renames."""
    df = _enriched_contract(spark).drop(*SILVER_DROPS[source])
    return df.toDF(*[_NORM_RENAMES.get(name, name) for name in df.columns])


def _contract_frame(spark, rows):
    """Build a contract-like frame, filling the fields missing in each row."""
    return spark.createDataFrame(
        [{**CONTRACT_DEFAULTS, **row} for row in rows], schema=CONTRACT
    )


# ---------------------------------------------------------------------------
# Toy chain: same shape as the notebooks without the real business rules.
# ---------------------------------------------------------------------------

TOY_COLUMNS = [
    "job_id", "title", "company_name", "location_city", "posted_date",
    "salary", "_ingest_date",
]

PREPARED_SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("company_name", T.StringType(), True),
    T.StructField("location_city", T.StringType(), True),
    T.StructField("posted_date", T.DateType(), True),
    T.StructField("salary", T.DoubleType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])
PREPARED_DEFAULTS = {field.name: None for field in PREPARED_SCHEMA.fields}
PREPARED_DEFAULTS["_source_file"] = "file.parquet"


def _prepared(spark, rows):
    return spark.createDataFrame(
        [{**PREPARED_DEFAULTS, **row} for row in rows],
        schema=PREPARED_SCHEMA,
    )


def _toy_company(col):
    """Idempotent company normalizer: collapses the ACME variants."""
    value = F.trim(F.coalesce(col, F.lit("")))
    return F.when(value.isin("ACME S.L.", "Acme", "ACME SL"), F.lit("Acme")) \
            .otherwise(col)


def _toy_normalize_company(df):
    return df.withColumn("company_name", _toy_company(F.col("company_name")))


def _toy_normalize_city(col):
    """Same rule as the city of the real notebook (test double)."""
    return F.trim(F.coalesce(col, F.lit("")))


def _toy_transform(prepared):
    """Reference chain of the test: contract, key, dedup by group."""
    df = (_toy_normalize_company(prepared)
          .withColumn("location_city",
                      _toy_normalize_city(F.col("location_city")))
          .select(*TOY_COLUMNS))
    df = df.withColumn(inc.KEY, inc.key_expression())
    window = SparkWindow.partitionBy(inc.KEY).orderBy(
        F.desc("posted_date"), F.desc(inc.INGEST_DATE_COLUMN)
    )
    return (df.withColumn(inc.RANK_COLUMN, F.row_number().over(window))
              .filter(F.col(inc.RANK_COLUMN) == 1)
              .drop(inc.KEY, inc.RANK_COLUMN))


def _toy_transform_with_extra(prepared):
    return _toy_transform(prepared).withColumn("extra", F.lit("new"))


def _toy_row(job_id, title, company, city, posted, ingest, salary=None):
    return {
        "job_id": job_id,
        "title": title,
        "company_name": company,
        "location_city": city,
        "posted_date": posted,
        "salary": salary,
        "_ingest_date": ingest,
    }


def _with_changes(rows, changes):
    """Replace rows by ``job_id`` and append the new ones (order kept)."""
    by_id = {row["job_id"]: row for row in rows}
    for change in changes:
        by_id[change["job_id"]] = change
    return list(by_id.values())


def _content(df, columns=None):
    """Rows as tuples, sorted with a None-safe key (content comparison)."""
    names = list(columns) if columns is not None else list(df.columns)
    return sorted((tuple(row) for row in df.select(*names).collect()),
                  key=repr)


# ---------------------------------------------------------------------------
# Target as a temporary view + reference implementation of the MERGE.
# ---------------------------------------------------------------------------

def _publish(df, name):
    """Publish the target (or the result of a merge) as a temporary view.

    The test fixture uses temporary views instead of tables: ``tableExists``
    and ``read.table`` see them like a table and ``saveAsTable`` is not needed
    (it requires winutils on Windows).
    """
    df.createOrReplaceTempView(name)
    return name


def _reference_merge(spark, target_table, merge_src, *, view_name):
    """Reference MERGE of design §3 D-B6, without Delta.

    Rows of the source with ``_ingest_date`` upsert their ``_key``; rows with
    ``_ingest_date IS NULL`` are markers and delete their ``_key``. Columns
    of the source that the target does not have are added at the end (schema
    evolution), with NULL in the rows that are not rewritten.
    """
    target = spark.read.table(target_table).localCheckpoint(eager=True)
    new_columns = [name for name in merge_src.columns
                   if name not in target.columns]
    extended = target
    for name in new_columns:
        extended = extended.withColumn(
            name, F.lit(None).cast(merge_src.schema[name].dataType)
        )
    upsert = merge_src.where(F.col(inc.INGEST_DATE_COLUMN).isNotNull())
    markers = merge_src.where(F.col(inc.INGEST_DATE_COLUMN).isNull())
    upsert_keys = upsert.select(inc.key_expression().alias(inc.KEY))
    marker_keys = markers.select(inc.key_expression().alias(inc.KEY))
    kept = (extended
            .withColumn(inc.KEY, inc.key_expression())
            .join(upsert_keys, inc.KEY, "left_anti")
            .join(marker_keys, inc.KEY, "left_anti")
            .drop(inc.KEY))
    result = kept.unionByName(upsert).localCheckpoint(eager=True)
    result.createOrReplaceTempView(target_table)


def _install_reference_merge(monkeypatch, calls):
    """Monkeypatch ``merge_into_silver`` with the reference implementation."""
    def fake(spark, target_table, merge_src, *, view_name):
        calls.append({"view_name": view_name, "merge_src": merge_src})
        _reference_merge(spark, target_table, merge_src,
                         view_name=view_name)

    monkeypatch.setattr(inc, "merge_into_silver", fake)


# ---------------------------------------------------------------------------
# Fixture of the equivalence gate (design §8, P15/P21): one source, the
# semantic groups G1..G5, the migration of G2 and the real key collision.
# ---------------------------------------------------------------------------

D = dt.date

SCENARIO_INITIAL = [
    # G1: two offers of the same group; B (job 2) is the winner.
    _toy_row("1", "Data Engineer", "ACME S.L.", "Madrid", D(2026, 9, 1),
             D(2026, 9, 1), 100.0),
    _toy_row("2", "Data Engineer", "ACME S.L.", "Madrid", D(2026, 9, 10),
             D(2026, 9, 10), 200.0),
    # G2: will migrate from Dublin to Cork in the batch.
    _toy_row("3", "Data Analyst", "Globex", "Dublin", D(2026, 10, 5),
             D(2026, 10, 5), 300.0),
    # G3: two offers; the older capture of job 14 must not win.
    _toy_row("4", "BI Analyst", "Initech", "Barcelona", D(2026, 10, 1),
             D(2026, 10, 1), 400.0),
    _toy_row("14", "BI Analyst", "Initech", "Barcelona", D(2026, 8, 1),
             D(2026, 8, 1), 1400.0),
    # G4: same title and company as G1 but another city (another key).
    _toy_row("5", "Data Engineer", "ACME S.L.", "Dublin", D(2026, 9, 5),
             D(2026, 9, 5), 500.0),
    # G5: NULL / empty components.
    _toy_row("6", "Data Analyst", None, "Madrid", D(2026, 9, 12),
             D(2026, 9, 12), 600.0),
    _toy_row("7", None, "Globex", "Dublin", D(2026, 9, 13),
             D(2026, 9, 13), 700.0),
    _toy_row("8", "Data Scientist", "Globex", None, D(2026, 9, 14),
             D(2026, 9, 14), 800.0),
    _toy_row("9", "Data Analyst", "", "Madrid", D(2026, 9, 15),
             D(2026, 9, 15), 900.0),
    _toy_row("10", None, None, None, D(2026, 9, 16), D(2026, 9, 16), 1000.0),
    # G5 (vii): two triples with the same _key through the separator.
    # "Data|Analyst|ACME|Madrid" == "Data" + "Analyst" + "ACME|Madrid".
    _toy_row("11", "Data|Analyst", "ACME", "Madrid", D(2026, 9, 20),
             D(2026, 9, 20), 1100.0),
    _toy_row("12", "Data", "Analyst", "ACME|Madrid", D(2026, 9, 10),
             D(2026, 9, 10), 1200.0),
]

SCENARIO_CHANGES = [
    # Newer capture of job 1: it becomes the winner of G1.
    _toy_row("1", "Data Engineer", "ACME S.L.", "Madrid", D(2026, 10, 12),
             D(2026, 10, 12), 150.0),
    # Migration of G2: job 3 changes its city.
    _toy_row("3", "Data Analyst", "Globex", "Cork", D(2026, 10, 11),
             D(2026, 10, 11), 350.0),
    # Older capture of job 14: it does not win G3.
    _toy_row("14", "BI Analyst", "Initech", "Barcelona", D(2026, 7, 1),
             D(2026, 10, 12), 1450.0),
    # New group.
    _toy_row("13", "ML Engineer", "Globex", "Madrid", D(2026, 10, 12),
             D(2026, 10, 12), 1300.0),
    # Newer capture of the collision group: job 12 wins it.
    _toy_row("12", "Data", "Analyst", "ACME|Madrid", D(2026, 10, 13),
             D(2026, 10, 13), 1250.0),
]

WINDOW_FROM = D(2026, 10, 11)
WINDOW_TO = D(2026, 10, 31)

# ---------------------------------------------------------------------------
# P1 — the three reasons of full mode and as_bool over a table
# ---------------------------------------------------------------------------

INGEST_SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
])


def _drop_views(spark, *names):
    for name in names:
        if spark.catalog.tableExists(name):
            spark.catalog.dropTempView(name)


def test_full_mode_only_for_three_reasons(spark):
    names = ("__p1_missing", "__p1_no_date", "__p1_empty", "__p1_data")
    _drop_views(spark, *names)

    # 1. The table does not exist.
    bounds = inc.window_bounds(spark, "__p1_missing")
    assert bounds.mode == inc.MODE_FULL
    assert bounds.reason == inc.REASON_TABLE_MISSING
    assert bounds.mode_reason == bounds.reason
    assert bounds.watermark is None
    assert bounds.from_date is None and bounds.to_date is None
    assert bounds.lookback_days == inc.DEFAULT_LOOKBACK_DAYS

    # 2. The table exists but has no _ingest_date (first run, migration).
    _publish(spark.createDataFrame([("1",)], ["job_id"]), "__p1_no_date")
    bounds = inc.window_bounds(spark, "__p1_no_date")
    assert (bounds.mode, bounds.reason) == (
        inc.MODE_FULL, inc.REASON_MISSING_INGEST_DATE)
    assert bounds.watermark is None

    # 3. Empty table WITH the column: incremental with an open window.
    _publish(spark.createDataFrame([], schema=INGEST_SCHEMA), "__p1_empty")
    bounds = inc.window_bounds(spark, "__p1_empty")
    assert bounds.mode == inc.MODE_INCREMENTAL
    assert bounds.reason is None
    assert bounds.watermark is None
    assert bounds.from_date is None and bounds.to_date is None

    # force_full over a table WITH data: full/force_full and the watermark.
    _publish(
        spark.createDataFrame(
            [("1", D(2026, 9, 1)), ("2", D(2026, 10, 10))],
            schema=INGEST_SCHEMA,
        ),
        "__p1_data",
    )
    bounds = inc.window_bounds(spark, "__p1_data", force_full="true")
    assert (bounds.mode, bounds.reason) == (
        inc.MODE_FULL, inc.REASON_FORCE_FULL)
    assert bounds.watermark == D(2026, 10, 10)
    assert bounds.from_date is None and bounds.to_date is None

    # The widget text "false"/"" does not force the rebuild.
    for value in ("false", "", "FALSE", " 0 ", None, False):
        bounds = inc.window_bounds(spark, "__p1_data", force_full=value)
        assert bounds.mode == inc.MODE_INCREMENTAL
        assert bounds.reason is None
    for value in ("true", "TRUE", " 1 ", True):
        assert inc.as_bool(value) is True
    assert inc.as_bool("") is False
    assert inc.as_bool(None) is False


# ---------------------------------------------------------------------------
# P1b — empty target with _ingest_date, end to end with the reference MERGE
# ---------------------------------------------------------------------------

def test_empty_target_open_window_end_to_end(spark, monkeypatch):
    target = _publish(
        spark.createDataFrame([], schema=T.StructType([
            *[T.StructField(name, dtype, True) for name, dtype in [
                ("job_id", T.StringType()),
                ("title", T.StringType()),
                ("company_name", T.StringType()),
                ("location_city", T.StringType()),
                ("posted_date", T.DateType()),
                ("salary", T.DoubleType()),
                ("_ingest_date", T.DateType()),
            ]],
        ])),
        "__p1b_target",
    )
    prepared = _prepared(spark, SCENARIO_INITIAL)
    calls = []
    _install_reference_merge(monkeypatch, calls)

    summary = inc.run(
        spark, target, prepared, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date="", to_date="",
    )

    expected = _toy_transform(prepared)
    groups = expected.count()
    assert summary["mode"] == inc.MODE_INCREMENTAL
    assert summary["mode_reason"] is None
    assert summary["written"] is True
    assert summary["from_date"] is None
    assert summary["watermark"] is None
    assert summary["changed_rows"] == len(SCENARIO_INITIAL)
    assert summary["changed_rows"] > 0
    assert summary["target_rows"] == groups
    assert summary["affected_groups"] == groups
    assert summary["recomputed_groups"] == groups
    assert summary["deleted_groups"] == 0
    assert len(calls) == 1
    actual = spark.read.table(target)
    assert actual.columns == expected.columns
    assert _content(actual) == _content(expected)


# ---------------------------------------------------------------------------
# P2 — the window is max(_ingest_date) - lookback_days, with no upper bound
# ---------------------------------------------------------------------------

def test_window_bounds_uses_max_ingest_date_minus_lookback(spark):
    _publish(
        spark.createDataFrame(
            [("1", D(2026, 9, 1)), ("2", D(2026, 10, 10))],
            schema=INGEST_SCHEMA,
        ),
        "__p2_target",
    )
    integer = inc.window_bounds(spark, "__p2_target", lookback_days=7)
    text = inc.window_bounds(spark, "__p2_target", lookback_days="7")
    empty = inc.window_bounds(spark, "__p2_target", lookback_days="")
    assert integer.watermark == D(2026, 10, 10)
    assert integer.from_date == D(2026, 10, 3)
    assert integer.to_date is None
    assert integer == text == empty
    assert integer.lookback_days == 7

    zero = inc.window_bounds(spark, "__p2_target", lookback_days=0)
    assert zero.from_date == D(2026, 10, 10)


# ---------------------------------------------------------------------------
# P3 — invalid window parameters and as_bool
# ---------------------------------------------------------------------------

def test_invalid_window_parameters(spark):
    _publish(
        spark.createDataFrame([("1", D(2026, 10, 1))], schema=INGEST_SCHEMA),
        "__p3_target",
    )

    with pytest.raises(ValueError, match="to_date requiere from_date"):
        inc.window_bounds(spark, "__p3_target", to_date="2026-10-01")
    with pytest.raises(ValueError, match="posterior"):
        inc.window_bounds(spark, "__p3_target", from_date="2026-10-10",
                          to_date="2026-10-01")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        inc.window_bounds(spark, "__p3_target", from_date="10/10/2026")
    with pytest.raises(ValueError, match="lookback_days"):
        inc.window_bounds(spark, "__p3_target", lookback_days=-1)
    with pytest.raises(ValueError, match="lookback_days"):
        inc.window_bounds(spark, "__p3_target", lookback_days="seven")
    with pytest.raises(ValueError, match="booleano"):
        inc.window_bounds(spark, "__p3_target", force_full="yes")

    for value in ("yes", "no", "2", "maybe"):
        with pytest.raises(ValueError, match="booleano"):
            inc.as_bool(value)
    assert inc.as_date("", name="from_date") is None
    assert inc.as_date(None, name="to_date") is None
    assert inc.as_date(D(2026, 10, 1), name="from_date") == D(2026, 10, 1)
    assert inc.as_date(dt.datetime(2026, 10, 1, 12, 0),
                       name="from_date") == D(2026, 10, 1)


# ---------------------------------------------------------------------------
# P4 — a new group is inserted
# ---------------------------------------------------------------------------

def test_new_group_is_inserted(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
    ]
    new = _toy_row("3", "ML Engineer", "Globex", "Madrid", D(2026, 10, 12),
                   D(2026, 10, 12), 300.0)
    initial_prepared = _prepared(spark, initial)
    final_prepared = _prepared(spark, initial + [new])
    target = _publish(_toy_transform(initial_prepared), "__p4_target")
    _install_reference_merge(monkeypatch, [])

    summary = inc.run(
        spark, target, final_prepared, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )

    assert (summary["mode"], summary["mode_reason"]) == (
        inc.MODE_INCREMENTAL, None)
    assert summary["written"] is True
    assert summary["changed_rows"] == 1
    assert summary["affected_groups"] == 1
    assert summary["recomputed_groups"] == 1
    assert summary["deleted_groups"] == 0
    assert summary["target_rows"] == 3
    actual = spark.read.table(target)
    expected = _toy_transform(final_prepared)
    assert actual.columns == expected.columns
    assert _content(actual) == _content(expected)
    row = actual.where(F.col("job_id") == "3").collect()
    assert len(row) == 1
    assert row[0]["title"] == "ML Engineer"


# ---------------------------------------------------------------------------
# P5 — the winner capture is written; the older one does not win
# ---------------------------------------------------------------------------

def test_newer_capture_wins_and_older_does_not(spark, monkeypatch):
    base = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Engineer", "Acme", "Madrid", D(2026, 9, 10),
                 D(2026, 9, 10), 200.0),
        _toy_row("3", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 300.0),
    ]
    target = _publish(_toy_transform(_prepared(spark, base)), "__p5_target")
    _install_reference_merge(monkeypatch, [])

    # A newer capture of job 1 becomes the winner of its group.
    newer = _with_changes(base, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 15),
                 D(2026, 10, 15), 150.0),
    ])
    inc.run(
        spark, target, _prepared(spark, newer), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 15), to_date=D(2026, 10, 20),
    )
    rows = spark.read.table(target).where(
        F.col("title") == "Data Engineer").collect()
    assert len(rows) == 1
    assert rows[0]["job_id"] == "1"
    assert rows[0]["posted_date"] == D(2026, 10, 15)

    # An older capture of job 1 does not win: job 2 is the winner again.
    older = _with_changes(base, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 8, 1),
                 D(2026, 10, 16), 150.0),
    ])
    inc.run(
        spark, target, _prepared(spark, older), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 16), to_date=D(2026, 10, 20),
    )
    actual = spark.read.table(target)
    expected = _toy_transform(_prepared(spark, older))
    assert _content(actual) == _content(expected)
    rows = actual.where(F.col("title") == "Data Engineer").collect()
    assert len(rows) == 1
    assert rows[0]["job_id"] == "2"
    assert rows[0]["posted_date"] == D(2026, 9, 10)


# ---------------------------------------------------------------------------
# P6 — a group migration expels the orphan row (marker delete)
# ---------------------------------------------------------------------------

def test_group_migration_expels_the_orphan(spark, monkeypatch):
    initial = [
        _toy_row("3", "Data Analyst", "Globex", "Dublin", D(2026, 10, 5),
                 D(2026, 10, 5), 300.0),
        _toy_row("5", "Data Engineer", "Acme", "Madrid", D(2026, 9, 5),
                 D(2026, 9, 5), 500.0),
    ]
    final = _with_changes(initial, [
        _toy_row("3", "Data Analyst", "Globex", "Cork", D(2026, 10, 11),
                 D(2026, 10, 11), 350.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)), "__p6_target")
    _install_reference_merge(monkeypatch, [])

    summary = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )

    assert summary["affected_groups"] == 2   # old key + new key
    assert summary["recomputed_groups"] == 1
    assert summary["deleted_groups"] == 1
    assert summary["merge_rows"] == 2
    assert summary["target_rows"] == 2
    actual = spark.read.table(target)
    expected = _toy_transform(_prepared(spark, final))
    assert _content(actual) == _content(expected)
    assert actual.where(F.col("location_city") == "Dublin").count() == 0
    assert actual.where(F.col("location_city") == "Cork").count() == 1


# ---------------------------------------------------------------------------
# P7 — only the affected groups reach the MERGE source
# ---------------------------------------------------------------------------

def test_only_affected_groups_are_written(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
        _toy_row("3", "BI Analyst", "Initech", "Barcelona", D(2026, 9, 3),
                 D(2026, 9, 3), 300.0),
        _toy_row("4", "ML Engineer", "Umbrella", "Cork", D(2026, 9, 4),
                 D(2026, 9, 4), 400.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)), "__p7_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
    )

    written = {row["job_id"]
               for row in calls[-1]["merge_src"].collect()}
    assert written == {"1"}
    assert spark.read.table(target).count() == 4


# ---------------------------------------------------------------------------
# P8 — an empty window does not write
# ---------------------------------------------------------------------------

def test_empty_window_does_not_write(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
    ]
    target = _publish(_toy_transform(_prepared(spark, initial)), "__p8_target")
    before = _content(spark.read.table(target))
    calls = []
    _install_reference_merge(monkeypatch, calls)

    summary = inc.run(
        spark, target, _prepared(spark, initial), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 12, 1),
    )

    assert summary["written"] is False
    assert summary["changed_rows"] == 0
    assert summary["affected_groups"] == 0
    assert summary["recomputed_groups"] == 0
    assert summary["merge_rows"] == 0
    assert summary["target_rows"] == 2
    assert calls == []
    assert _content(spark.read.table(target)) == before


# ---------------------------------------------------------------------------
# P9 — repeating the same window leaves the same content
# ---------------------------------------------------------------------------

def test_repeated_window_is_idempotent(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)), "__p9_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    first = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
    )
    after_first = _content(spark.read.table(target))
    second = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
    )

    assert first["written"] is True and second["written"] is True
    assert after_first == _content(spark.read.table(target))
    assert first["target_rows"] == second["target_rows"] == 2
    assert first["recomputed_groups"] == second["recomputed_groups"] == 1
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# P10 — backfill range and force_full
# ---------------------------------------------------------------------------

def test_backfill_range_and_force_full(spark, monkeypatch):
    r1 = _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                  D(2026, 9, 1), 100.0)
    r2 = _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 15),
                  D(2026, 9, 15), 200.0)
    r3 = _toy_row("3", "BI Analyst", "Initech", "Barcelona", D(2026, 10, 1),
                  D(2026, 10, 1), 300.0)
    target = _publish(
        _toy_transform(_prepared(spark, [r1])), "__p10_target")
    _install_reference_merge(monkeypatch, [])

    first = inc.run(
        spark, target, _prepared(spark, [r1, r2, r3]), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 9, 10), to_date=D(2026, 9, 20),
    )
    assert first["changed_rows"] == 1
    assert first["from_date"] == "2026-09-10"
    assert first["to_date"] == "2026-09-20"
    assert first["target_rows"] == 2

    second = inc.run(
        spark, target, _prepared(spark, [r1, r2, r3]), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 9, 25), to_date=D(2026, 10, 5),
    )
    assert second["changed_rows"] == 1
    assert second["target_rows"] == 3
    assert _content(spark.read.table(target)) == _content(
        _toy_transform(_prepared(spark, [r1, r2, r3])))

    # force_full from the widget text: only "true"/"1" force the rebuild.
    assert inc.window_bounds(spark, target, force_full="false").mode == \
        inc.MODE_INCREMENTAL
    assert inc.window_bounds(spark, target, force_full="").mode == \
        inc.MODE_INCREMENTAL
    forced = inc.window_bounds(spark, target, force_full="true")
    assert (forced.mode, forced.reason) == (
        inc.MODE_FULL, inc.REASON_FORCE_FULL)


# ---------------------------------------------------------------------------
# P11 — key_from_columns / key_expression and the SQL text give the same key
# ---------------------------------------------------------------------------

def test_key_expression_matches_sql_text(spark):
    rows = [
        ("Data   Scientist", "  ACME  ", "Madrid  "),
        ("Data\tScientist", None, ""),
        (None, "Acme", None),
        ("Data", "Scientist", "New   York"),
        ("Data|Analyst", "ACME", "Madrid"),
        ("", "  ", "\t"),
    ]
    df = spark.createDataFrame(rows, ["title", "company_name", "location_city"])
    df.createOrReplaceTempView("__p11_key")

    expression = [row["k"] for row in
                  df.select(inc.key_expression().alias("k")).collect()]
    explicit = [row["k"] for row in df.select(
        inc.key_from_columns(
            F.col("title"), F.col("company_name"), F.col("location_city")
        ).alias("k")).collect()]
    sql = [row["k"] for row in spark.sql(
        f"SELECT {inc.key_sql('x')} AS k FROM __p11_key AS x").collect()]

    assert expression == explicit == sql
    # The single backslash case is the counterexample: Spark keeps "s+".
    wrong_sql = inc.key_sql("x").replace(r"'\\s+'", r"'\s+'")
    assert wrong_sql != inc.key_sql("x")
    wrong = [row["k"] for row in spark.sql(
        f"SELECT {wrong_sql} AS k FROM __p11_key AS x").collect()]
    assert wrong != expression
    # Measured with Spark 4.2: "data   scientist|  acme  |madrid".
    assert wrong[0] == "data   scienti t|  acme  |madrid"


# ---------------------------------------------------------------------------
# P12 — MERGE statement shape, view lifecycle and unique view names
# ---------------------------------------------------------------------------

def test_merge_sql_shape(spark, monkeypatch):
    target = "job_offers.silver.offers_indeed"
    view = "__silver_batch_indeed_1a2b3c4d"
    sql = inc.merge_sql(target, view)

    assert sql.startswith(
        f"MERGE WITH SCHEMA EVOLUTION INTO {target} AS t\n")
    assert f"USING {view} AS s\n" in sql
    assert f"ON  {inc.key_sql('t')}\n  = {inc.key_sql('s')}\n" in sql
    assert "concat_ws('|', t.title, t.company_name, t.location_city)" in sql
    assert "concat_ws('|', s.title, s.company_name, s.location_city)" in sql

    delete = "WHEN MATCHED AND s._ingest_date IS NULL THEN DELETE"
    update = "WHEN MATCHED THEN UPDATE SET *"
    insert = ("WHEN NOT MATCHED AND s._ingest_date IS NOT NULL "
              "THEN INSERT *")
    assert delete in sql and update in sql and insert in sql
    assert sql.index(delete) < sql.index(update) < sql.index(insert)
    assert "WHEN MATCHED AND (" not in sql
    assert sql.count("WHEN MATCHED") == 2
    assert sql.count("WHEN NOT MATCHED") == 1

    # The view is created for the statement and dropped in the finally block.
    _publish(spark.createDataFrame([("1", "A")], ["job_id", "title"]),
             "__p12_target")
    seen = {}

    def fake_merge_sql(target_table, view_name):
        seen["exists_inside"] = spark.catalog.tableExists(view_name)
        return "SELECT 1"

    monkeypatch.setattr(inc, "merge_sql", fake_merge_sql)
    source = spark.createDataFrame([("1", "A")], ["job_id", "title"])
    inc.merge_into_silver(spark, "__p12_target", source,
                          view_name="__p12_batch")
    assert seen["exists_inside"] is True
    assert spark.catalog.tableExists("__p12_batch") is False

    # A failing statement also drops the view.
    _publish(spark.createDataFrame([("1", "A")], ["job_id", "title"]),
             "__p12_target")
    monkeypatch.setattr(
        inc, "merge_sql",
        lambda target_table, view_name: "SELECT * FROM __missing_in_p12")
    with pytest.raises(Exception):
        inc.merge_into_silver(spark, "__p12_target", source,
                              view_name="__p12_batch_fail")
    assert spark.catalog.tableExists("__p12_batch_fail") is False

    # A missing destination fails with the migration message.
    with pytest.raises(ValueError, match="no existe"):
        inc.merge_into_silver(spark, "__p12_missing", source,
                              view_name="__p12_batch_missing")


def test_view_name_is_unique_per_run(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p12_unique_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)
    for _ in range(2):
        inc.run(
            spark, target, _prepared(spark, final), source="indeed",
            transform=_toy_transform,
            normalize_company=_toy_normalize_company,
            normalize_city_column=_toy_normalize_city,
            from_date=D(2026, 10, 12), to_date=WINDOW_TO,
        )
    names = [call["view_name"] for call in calls]
    assert len(names) == 2 and names[0] != names[1]
    for name in names:
        assert re.fullmatch(r"__silver_batch_indeed_[0-9a-f]{8}", name)


# ---------------------------------------------------------------------------
# P13 — MERGE source contract, parameterized by source and target.columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", sorted(TARGET_SIZES))
def test_merge_source_contract_by_source(spark, source):
    target_columns = _silver_frame(spark, source).columns
    assert len(target_columns) == TARGET_SIZES[source]
    assert inc.INGEST_DATE_COLUMN in target_columns
    assert inc.KEY not in target_columns
    assert inc.RANK_COLUMN not in target_columns
    assert not [name for name in target_columns if name.startswith("_")
                and name != inc.INGEST_DATE_COLUMN]

    recompute = _silver_frame(spark, source)
    markers = recompute.withColumn(
        inc.INGEST_DATE_COLUMN, F.lit(None).cast("date"))
    merge_src = inc.merge_source(recompute, markers, target_columns)
    assert merge_src.columns == target_columns
    assert merge_src.count() == 0

    with pytest.raises(ValueError, match="faltan columnas"):
        inc.merge_source(recompute.drop("job_url"), markers, target_columns)
    with pytest.raises(ValueError, match="columnas internas"):
        inc.merge_source(
            recompute.withColumn(inc.KEY, F.lit("x")), markers,
            target_columns,
        )
    with pytest.raises(ValueError, match="columnas internas"):
        inc.merge_source(
            recompute, markers.withColumn(inc.RANK_COLUMN, F.lit(1)),
            target_columns,
        )


def test_silver_contract_feeds_gold_and_exports_without_control_columns(spark):
    offers = _silver_frame(spark, "indeed")
    fact = gold.build_fact_offers(offers, gold.dim_currency(spark))
    assert set(fact.columns) <= set(gold.FACT_OFFERS_COLUMNS)
    assert inc.INGEST_DATE_COLUMN not in fact.columns
    assert not [name for name in fact.columns if name.startswith("_")]
    skills = gold.build_fact_offer_skills(offers)
    assert inc.INGEST_DATE_COLUMN not in skills.columns
    # The export contracts project explicit lists: no control columns.
    assert not [name for name in web.WEB_FACT_COLUMNS
                if name.startswith("_")]
    assert not [name for name in pbi.PBI_FACT_OFFERS_COLUMNS
                if name.startswith("_")]
    for columns in (web.WEB_FACT_COLUMNS, pbi.PBI_FACT_OFFERS_COLUMNS):
        assert inc.INGEST_DATE_COLUMN not in columns


# ---------------------------------------------------------------------------
# P14 — a NULL _ingest_date in the recompute aborts before the MERGE
# ---------------------------------------------------------------------------

def _transform_with_null_ingest_date(prepared):
    return (_toy_transform(prepared)
            .withColumn(inc.INGEST_DATE_COLUMN, F.lit(None).cast("date")))


def test_recompute_rejects_null_ingest_date(spark, monkeypatch):
    nulls = _toy_transform(_prepared(spark, SCENARIO_INITIAL[:1])).withColumn(
        inc.INGEST_DATE_COLUMN, F.lit(None).cast("date"))
    with pytest.raises(ValueError, match="_ingest_date"):
        inc.require_no_null_ingest_date(nulls)

    initial = [_toy_row("1", "Data Engineer", "Acme", "Madrid",
                        D(2026, 9, 1), D(2026, 9, 1), 100.0)]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p14_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)
    with pytest.raises(ValueError, match="_ingest_date"):
        inc.run(
            spark, target, _prepared(spark, final), source="indeed",
            transform=_transform_with_null_ingest_date,
            normalize_company=_toy_normalize_company,
            normalize_city_column=_toy_normalize_city,
            from_date=D(2026, 10, 12), to_date=WINDOW_TO,
        )
    assert calls == []
    assert spark.read.table(target).count() == 1

# ---------------------------------------------------------------------------
# Reference chain of P15b: rename, contract, enrich, initcap, canonical, key,
# dedup and drop/rename copied from the silver notebooks (T-09 moves it into
# prepare_bronze/build_offers; the copy here is the derive control of the
# diff, the real chain is validated in T-10).
# ---------------------------------------------------------------------------

INDEED_RENAME = {
    "job_key": "job_id",
    "viewjob_url": "job_url",
    "title": "title",
    "company": "company_name",
    "city": "location_city",
    "state": "location_region",
    "country": "location_country",
    "salary_text": "salary_raw",
    "salary_type": "salary_period",
    "workplace_type": "work_mode",
    "search_term": "search_role",
    "ingest_date": "_ingest_date",
    "description_text": "description_clean",
}

LINKEDIN_RENAME = {"description_full": "description_clean"}


def _apply_company_normalization(df, col_name="company_name"):
    """initcap + whitespace collapse, copied from the silver notebooks."""
    return df.withColumn(
        col_name,
        F.initcap(F.trim(F.regexp_replace(F.col(col_name), r"\s+", " "))),
    )


def _cast_contract(df):
    return df.select([
        F.col(name).cast(CONTRACT[name].dataType).alias(name)
        for name in CONTRACT.fieldNames()
    ])


def _dedup_offers(df):
    """Semantic dedup of the notebooks (same window, same key)."""
    df = df.withColumn(inc.KEY, inc.key_expression())
    window = SparkWindow.partitionBy(inc.KEY).orderBy(
        F.desc("posted_date"), F.desc(inc.INGEST_DATE_COLUMN)
    )
    return (df.withColumn(inc.RANK_COLUMN, F.row_number().over(window))
              .filter(F.col(inc.RANK_COLUMN) == 1)
              .drop(inc.KEY, inc.RANK_COLUMN))


def _indeed_prepare(raw):
    df = raw.withColumnsRenamed(INDEED_RENAME)
    return df.withColumns({
        "salary_currency": F.lit("EUR"),
        "is_salary_available":
            F.when(F.col("salary_raw") != "", True).otherwise(False),
        "experience_level": F.lit(""),
        "skills": F.lit(""),
        "source_scraper": F.lit("Indeed"),
    })


def _indeed_build(prepared):
    df = _cast_contract(prepared)
    df = de.enrich(df, add_location_fill=True, normalize_location_city=True)
    df = de.apply_company_canonical(_apply_company_normalization(df))
    df = _dedup_offers(df)
    df = df.drop("salary_min", "salary_max", "scraped_at", "salary_period",
                 "work_mode")
    return df.withColumnsRenamed({"salary_period_norm": "salary_period",
                                  "work_mode_norm": "work_mode"})


def _linkedin_posted_date(df):
    """posted_date derivation copied from silver_linkedin (cell 6)."""
    n = (
        F.regexp_extract(
            F.lower(F.col("posted_relative")),
            r"hace\s+(\d+)",
            1,
        ).cast("int")
    )
    unit = (
        F.when(F.col("posted_relative").rlike("(?i)minuto"), F.lit("minute"))
        .when(F.col("posted_relative").rlike("(?i)hora"), F.lit("hour"))
        .when(F.col("posted_relative").rlike("(?i)d[ií]a"), F.lit("day"))
        .when(F.col("posted_relative").rlike("(?i)semana"), F.lit("week"))
        .when(F.col("posted_relative").rlike("(?i)mes"), F.lit("month"))
        .when(F.col("posted_relative").rlike("(?i)a[ñn]o"), F.lit("year"))
    )
    return df.withColumn(
        "posted_date",
        F.when(
            F.col("posted_datetime").isNotNull()
            & (F.trim(F.col("posted_datetime")) != ""),
            F.to_date(F.col("posted_datetime")),
        ).otherwise(
            F.when(
                unit == "minute",
                F.to_date(F.to_timestamp(F.col("scraped_at"))
                          - F.make_interval(mins=n)),
            )
            .when(
                unit == "hour",
                F.to_date(F.to_timestamp(F.col("scraped_at"))
                          - F.make_interval(hours=n)),
            )
            .when(unit == "day",
                  F.date_add(F.to_date(F.col("scraped_at")), -n))
            .when(unit == "week",
                  F.date_add(F.to_date(F.col("scraped_at")), -n * 7))
            .when(unit == "month",
                  F.add_months(F.to_date(F.col("scraped_at")), -n))
            .when(unit == "year",
                  F.add_months(F.to_date(F.col("scraped_at")), -n * 12))
        ),
    )


def _linkedin_prepare(raw):
    df = dedup.keep_latest_by(raw, ["job_id"])
    df = df.withColumnsRenamed(LINKEDIN_RENAME)
    df = _linkedin_posted_date(df)
    return df.withColumns({
        "is_salary_available":
            F.when(F.col("salary_raw") != "", True).otherwise(False),
        "source_scraper": F.lit("Linkedin"),
    })


def _linkedin_build(prepared):
    df = _cast_contract(prepared)
    df = de.enrich(df, add_location_fill=True, normalize_location_city=True)
    df = de.apply_company_canonical(_apply_company_normalization(df))
    df = _dedup_offers(df)
    df = df.drop("scraped_at", "salary_period", "work_mode")
    return df.withColumnsRenamed({"salary_period_norm": "salary_period",
                                  "work_mode_norm": "work_mode"})


INDEED_RAW_SCHEMA = T.StructType([
    T.StructField("job_key", T.StringType(), True),
    T.StructField("viewjob_url", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("company", T.StringType(), True),
    T.StructField("city", T.StringType(), True),
    T.StructField("state", T.StringType(), True),
    T.StructField("country", T.StringType(), True),
    T.StructField("salary_text", T.StringType(), True),
    T.StructField("salary_type", T.StringType(), True),
    T.StructField("workplace_type", T.StringType(), True),
    T.StructField("search_term", T.StringType(), True),
    T.StructField("description_text", T.StringType(), True),
    T.StructField("salary_min", T.DoubleType(), True),
    T.StructField("salary_max", T.DoubleType(), True),
    T.StructField("scraped_at", T.TimestampType(), True),
    T.StructField("posted_date", T.DateType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])

LINKEDIN_RAW_SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("job_url", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("company_name", T.StringType(), True),
    T.StructField("location_city", T.StringType(), True),
    T.StructField("location_region", T.StringType(), True),
    T.StructField("location_country", T.StringType(), True),
    T.StructField("posted_datetime", T.StringType(), True),
    T.StructField("posted_relative", T.StringType(), True),
    T.StructField("scraped_at", T.StringType(), True),
    T.StructField("salary_currency", T.StringType(), True),
    T.StructField("salary_min", T.DoubleType(), True),
    T.StructField("salary_max", T.DoubleType(), True),
    T.StructField("salary_period", T.StringType(), True),
    T.StructField("salary_raw", T.StringType(), True),
    T.StructField("experience_level", T.StringType(), True),
    T.StructField("description_clean", T.StringType(), True),
    T.StructField("skills", T.StringType(), True),
    T.StructField("search_role", T.StringType(), True),
    T.StructField("work_mode", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])


def _indeed_raw(spark, scenario):
    rows = [{
        "job_key": row["job_id"],
        "viewjob_url": f"https://indeed.example/{row['job_id']}",
        "title": row["title"],
        "company": row["company_name"],
        "city": row["location_city"],
        "state": None,
        "country": None,
        "salary_text": None,
        "salary_type": None,
        "workplace_type": None,
        "search_term": "data",
        "description_text": "",
        "salary_min": row["salary"],
        "salary_max": None,
        "scraped_at": dt.datetime(2026, 9, 1, 10, 0),
        "posted_date": row["posted_date"],
        "_ingest_date": row["_ingest_date"],
        "_source_file": "file.parquet",
    } for row in scenario]
    return spark.createDataFrame(rows, schema=INDEED_RAW_SCHEMA)


def _linkedin_raw(spark, scenario):
    rows = [{
        "job_id": row["job_id"],
        "job_url": f"https://linkedin.example/{row['job_id']}",
        "title": row["title"],
        "company_name": row["company_name"],
        "location_city": row["location_city"],
        "location_region": None,
        "location_country": None,
        "posted_datetime": (row["posted_date"].isoformat() + " 00:00:00"
                            if row["posted_date"] is not None else None),
        "posted_relative": None,
        "scraped_at": "2026-09-01T10:00:00+00:00",
        "salary_currency": "EUR",
        "salary_min": row["salary"],
        "salary_max": None,
        "salary_period": None,
        "salary_raw": None,
        "experience_level": None,
        "description_clean": "",
        "skills": None,
        "search_role": "data",
        "work_mode": None,
        "_ingest_date": row["_ingest_date"],
        "_source_file": "file.parquet",
    } for row in scenario]
    return spark.createDataFrame(rows, schema=LINKEDIN_RAW_SCHEMA)


def _reference_chain(source):
    if source == "indeed":
        return (_indeed_prepare, _indeed_build, _indeed_raw)
    if source == "linkedin":
        return (_linkedin_prepare, _linkedin_build, _linkedin_raw)
    raise AssertionError(f"fuente sin cadena de referencia: {source}")


def _reference_normalize_company(df):
    return de.apply_company_canonical(_apply_company_normalization(df))


# ---------------------------------------------------------------------------
# P15 — incremental equals the full rebuild (open gate, toy chain)
# ---------------------------------------------------------------------------

def test_equivalence_incremental_equals_full_rebuild(spark, monkeypatch):
    initial = _prepared(spark, SCENARIO_INITIAL)
    final = _prepared(
        spark, _with_changes(SCENARIO_INITIAL, SCENARIO_CHANGES))
    expected = _toy_transform(final)
    target = _publish(_toy_transform(initial), "__p15_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    first = inc.run(
        spark, target, final, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )

    actual = spark.read.table(target)
    assert first["mode"] == inc.MODE_INCREMENTAL
    assert first["mode_reason"] is None
    assert first["written"] is True
    assert first["changed_rows"] == len(SCENARIO_CHANGES)
    assert actual.columns == expected.columns
    assert _content(actual) == _content(expected)
    assert first["target_rows"] == expected.count()
    assert first["deleted_groups"] == 1        # G2 (Dublin) is emptied
    # The collision group is won by job 12 and G1 by job 1.
    collision = actual.where(F.col("title") == "Data").collect()
    assert len(collision) == 1 and collision[0]["job_id"] == "12"
    assert actual.where(F.col("job_id") == "1").count() == 1
    # G3 keeps its winner although the changed capture is older.
    bi = actual.where(F.col("title") == "BI Analyst").collect()
    assert len(bi) == 1 and bi[0]["job_id"] == "4"

    # Repeating the same window: content, target_rows and recomputed_groups.
    second = inc.run(
        spark, target, final, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )
    assert second["mode"] == inc.MODE_INCREMENTAL
    assert second["mode_reason"] is None
    assert second["written"] is True
    assert second["changed_rows"] == first["changed_rows"]
    assert _content(spark.read.table(target)) == _content(expected)
    assert second["target_rows"] == first["target_rows"]
    assert second["recomputed_groups"] == first["recomputed_groups"]
    # These may go down after the first pass cleaned the migrated group.
    assert second["affected_groups"] <= first["affected_groups"]
    assert second["deleted_groups"] <= first["deleted_groups"]
    assert second["merge_rows"] <= first["merge_rows"]

    # force_full -> full rebuild (its write is T-10, not testable without
    # Delta/saveAsTable): the mode and the reason are what P15 can fix.
    forced = inc.window_bounds(spark, target, force_full=True)
    assert (forced.mode, forced.reason) == (
        inc.MODE_FULL, inc.REASON_FORCE_FULL)


# ---------------------------------------------------------------------------
# P15b — same gate with the reference chain (enrich + initcap + canonical)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["indeed", "linkedin"])
def test_equivalence_with_reference_chain_indeed_and_linkedin(
    spark, monkeypatch, source
):
    prepare, build, raw = _reference_chain(source)
    initial = prepare(raw(spark, SCENARIO_INITIAL))
    final = prepare(
        raw(spark, _with_changes(SCENARIO_INITIAL, SCENARIO_CHANGES)))
    initial_silver = build(initial).localCheckpoint(eager=True)
    expected = build(final).localCheckpoint(eager=True)
    target = _publish(initial_silver, f"__p15b_{source}")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    summary = inc.run(
        spark, target, final, source=source,
        transform=build,
        normalize_company=_reference_normalize_company,
        normalize_city_column=de.location_city_normalized_column,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )

    actual = spark.read.table(target)
    assert summary["mode"] == inc.MODE_INCREMENTAL
    assert summary["mode_reason"] is None
    assert summary["written"] is True
    assert summary["changed_rows"] == len(SCENARIO_CHANGES)
    assert actual.columns == expected.columns
    assert _content(actual) == _content(expected)
    assert summary["target_rows"] == expected.count()
    assert summary["deleted_groups"] == 1
    # The migrated group has no Dublin row left; the collision group is won
    # by job 12 (same key, other triple) and G3 keeps job 4.
    assert actual.where(
        (F.col("title") == "Data Analyst")
        & (F.col("location_city") == "Dublin")).count() == 0
    collision = actual.where(F.col("title") == "Data").collect()
    assert len(collision) == 1 and collision[0]["job_id"] == "12"
    bi = actual.where(F.col("title") == "BI Analyst").collect()
    assert len(bi) == 1 and bi[0]["job_id"] == "4"

# ---------------------------------------------------------------------------
# P16 — company normalization is a fixed point and keeps the name set
# ---------------------------------------------------------------------------

def _company_frame(spark, names):
    schema = T.StructType([
        T.StructField("job_id", T.StringType(), True),
        T.StructField("company_name", T.StringType(), True),
    ])
    return spark.createDataFrame(
        [(str(index), name) for index, name in enumerate(names)],
        schema=schema,
    )


def _infojobs_canonical(df, col_name="company_name"):
    """Columnar canonical variant copied from silver_infojobs (NULL -> '')."""
    base = F.trim(F.coalesce(F.col(col_name), F.lit("")))
    expr = base
    for canon, pattern in de.COMPANY_ALIAS_PATTERNS:
        expr = F.when(base.rlike(pattern), F.lit(canon)).otherwise(expr)
    return df.withColumn(col_name, expr)


def _company_map(df):
    return {row["job_id"]: row["company_name"] for row in df.collect()}


def test_company_normalization_is_fixed_point_and_name_set(spark):
    literals = [canon for canon, _ in de.COMPANY_ALIAS_PATTERNS]
    assert len(literals) == 128
    frame = _company_frame(spark, literals)
    joined = _company_map(de.apply_company_canonical(frame))
    columnar = _company_map(_infojobs_canonical(frame))
    for index, canon in enumerate(literals):
        assert joined[str(index)] == canon
        assert columnar[str(index)] == canon

    # Fixed point on a fixture with NULL, '', '&', apostrophes, repeated
    # spaces and Unicode: canonical(initcap(stored)) == stored.
    names = [
        None, "", "McKinsey & Company", "IKEA", "T-Mobile", "AT&T",
        "O'Hara Ltd", "ACME S.L.", "acme s.l.", "Foo  Bar", "Strasse GmbH",
        "Zürich Data", "  google ireland ltd  ", "Johnson & Johnson",
        "Some Startup",
    ]
    frame = _company_frame(spark, names)
    for function in (de.apply_company_canonical, _infojobs_canonical):
        stored = function(_apply_company_normalization(frame))
        again = function(_apply_company_normalization(stored))
        assert _company_map(stored) == _company_map(again)

    # Without initcap the stored name changes for the companies that do not
    # match any pattern: initcap is required to keep the name (and the
    # companies_* dimension that reads silver) stable. The key lowercases its
    # input, so the case alone does not change it; what must be shared on both
    # sides is the same composition (P15b/P16b fix the key equality).
    base = (frame
            .withColumn("title", F.lit("Data Engineer"))
            .withColumn("location_city", F.lit("Madrid")))
    without = de.apply_company_canonical(base)
    with_initcap = de.apply_company_canonical(
        _apply_company_normalization(base))
    without_names = _company_map(without)
    initcap_names = _company_map(with_initcap)
    changed = [key for key in without_names
               if without_names[key] != initcap_names[key]]
    assert changed


# ---------------------------------------------------------------------------
# P16b — city column vs enrich column vs pure function; bronze vs silver key
# ---------------------------------------------------------------------------

def _city_fixture_values():
    """Explicit fixture: every key/value of the city maps plus adversaries."""
    values = set(de._LOCATION_CITY_CANONIC)
    values |= set(de._LOCATION_CITY_CANONIC.values())
    values |= set(de._LOCATION_CITY_TO_COUNTRY)
    values |= set(de._LOCATION_CITY_TO_COUNTRY.values())
    values |= set(de._LOCATION_COUNTRY_NAMES)
    values |= {
        "Dublín", "Málaga", "Zürich", "SAN SEBASTIÁN", "A Coruña",
        "Dublin 1", "Dublin 10", "Dublin - 2", "Greater Dublin Area",
        "Dublin Metropolitan Area", "Madrid y alrededores", "Madrid area",
        "Madrid region", "Pais Vasco / Euskadi", "dublin and surroundings",
        "dublin surrounding area", "  barcelona  ", "", None, "   ",
        "Ciudad Inexistente", "New   York", "rotterdam and the hague",
        "Dublin 3", "greater madrid metropolitan area",
    }
    return sorted(values, key=lambda value: (value is not None, value or ""))


def test_city_normalized_column_matches_enrich_and_key_matches_silver(spark):
    values = _city_fixture_values()
    frame = _contract_frame(spark, [
        {
            "job_id": str(index),
            "title": "Data Engineer",
            "company_name": "Acme",
            "location_city": value,
            "description_clean": "",
            "_ingest_date": D(2026, 10, 1),
        }
        for index, value in enumerate(values)
    ])

    enriched = de.enrich(frame, add_location_fill=True,
                         normalize_location_city=True)
    from_enrich = {
        row["job_id"]: row["location_city"]
        for row in enriched.select("job_id", "location_city").collect()
    }
    from_column = {
        row["job_id"]: row["city"] for row in frame.select(
            "job_id",
            de.location_city_normalized_column(
                F.col("location_city")).alias("city"),
        ).collect()
    }
    from_pure = {
        str(index): de.location_city_normalized(value)
        for index, value in enumerate(values)
    }
    checked = len(values)
    assert checked == len(from_enrich) == len(from_column) == len(from_pure)
    assert from_pure == from_column
    assert from_enrich == from_column
    assert sum(1 for key in from_pure if from_pure[key] == from_column[key]) \
        == checked

    # add_location_fill=False leaves the city unnormalized: not a valid
    # combination for the bronze key.
    without_fill = de.enrich(frame, add_location_fill=False,
                             normalize_location_city=True)
    without_fill_city = {
        row["job_id"]: row["location_city"]
        for row in without_fill.select("job_id", "location_city").collect()
    }
    assert [key for key in without_fill_city
            if without_fill_city[key] != from_column[key]]
    without_normalize = de.enrich(frame, add_location_fill=True,
                                  normalize_location_city=False)
    without_normalize_city = {
        row["job_id"]: row["location_city"]
        for row in without_normalize.select(
            "job_id", "location_city").collect()
    }
    assert [key for key in without_normalize_city
            if without_normalize_city[key] != from_column[key]]

    # Bronze key (initcap + canonical + columnar city) == silver key
    # (enrich + canonical + key_expression), NULL components included.
    key_rows = [
        ("Data Engineer", "ACME S.L.", "Madrid"),
        ("Data Engineer", "acme s.l.", "  madrid  "),
        ("Data Engineer", "Google Ireland Ltd", "Dublin"),
        ("Data Engineer", None, "Madrid"),
        ("Data Engineer", "Madrid", None),
        (None, "Acme", "Madrid"),
        ("Data Engineer", "", "Madrid"),
        (None, None, None),
        ("Data|Analyst", "ACME", "Madrid"),
        ("Data", "Analyst", "ACME|Madrid"),
        ("Data Engineer", "ACME S.L.", "Dublín"),
        ("Data Engineer", "ACME S.L.", "Ciudad Inexistente"),
        ("Data Engineer", "ACME S.L.", "Málaga"),
        ("Data Engineer", "ACME S.L.", "Dublin 1"),
        ("Data Engineer", "ACME S.L.", "Madrid y alrededores"),
    ]
    key_frame = _contract_frame(spark, [
        {
            "job_id": str(index),
            "title": title,
            "company_name": company,
            "location_city": city,
            "description_clean": "",
            "_ingest_date": D(2026, 10, 1),
        }
        for index, (title, company, city) in enumerate(key_rows)
    ])
    bronze = de.apply_company_canonical(
        _apply_company_normalization(key_frame))
    bronze_keys = {
        row["job_id"]: row["k"] for row in bronze.select(
            "job_id",
            inc.key_from_columns(
                F.col("title"),
                F.col("company_name"),
                de.location_city_normalized_column(F.col("location_city")),
            ).alias("k"),
        ).collect()
    }
    silver = de.apply_company_canonical(_apply_company_normalization(
        de.enrich(key_frame, add_location_fill=True,
                  normalize_location_city=True)))
    silver_keys = {
        row["job_id"]: row["k"] for row in silver.select(
            "job_id", inc.key_expression().alias("k")
        ).collect()
    }
    assert len(bronze_keys) == len(key_rows)
    assert bronze_keys == silver_keys


# ---------------------------------------------------------------------------
# P17 — candidates are selected by exact key equality
# ---------------------------------------------------------------------------

def test_candidates_are_exact_key_equality(spark):
    rows = [
        _toy_row("a1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("b1", "Data Engineer", "Acme", "Dublin", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("c1", "Data|Analyst", "ACME", "Madrid", D(2026, 9, 20),
                 D(2026, 9, 20), 1100.0),
        _toy_row("c2", "Data", "Analyst", "ACME|Madrid", D(2026, 9, 10),
                 D(2026, 9, 10), 1200.0),
    ]
    prepared = _prepared(spark, rows)
    keyed = _toy_normalize_company(prepared).withColumn(
        inc.KEY,
        inc.key_from_columns(
            F.col("title"),
            F.col("company_name"),
            _toy_normalize_city(F.col("location_city")),
        ),
    )

    # A row with the same title and company but another city is excluded.
    madrid = keyed.where(F.col("job_id") == "a1").select(inc.KEY).distinct()
    candidates = inc.select_candidates(keyed, madrid)
    assert {row["job_id"] for row in candidates.collect()} == {"a1"}

    # A key shared by two different triples selects both rows of the group.
    collision = keyed.where(
        F.col("job_id").isin("c1", "c2")).select(inc.KEY).distinct()
    assert collision.count() == 1
    candidates = inc.select_candidates(keyed, collision)
    assert {row["job_id"] for row in candidates.collect()} == {"c1", "c2"}

    # transform(candidates) restricted to the affected set equals
    # transform(prepared) restricted to the affected set.
    key_value = collision.first()[inc.KEY]
    recomputed = inc.recompute_groups(candidates, transform=_toy_transform)
    full = _toy_transform(prepared)
    expected = full.where(inc.key_expression() == key_value)
    actual = recomputed.where(inc.key_expression() == key_value)
    assert _content(actual) == _content(expected)


# ---------------------------------------------------------------------------
# P18 — NULL components are grouped by the exact key on both sides
# ---------------------------------------------------------------------------

NULL_ROWS = [
    _toy_row("n1", "Data Analyst", None, "Madrid", D(2026, 9, 12),
             D(2026, 9, 12), 100.0),
    _toy_row("n2", "Data Analyst", "Madrid", None, D(2026, 9, 13),
             D(2026, 9, 13), 200.0),
    _toy_row("n3", None, "Acme", "Madrid", D(2026, 9, 14),
             D(2026, 9, 14), 300.0),
    _toy_row("n4", "Data Scientist", "Acme", None, D(2026, 9, 15),
             D(2026, 9, 15), 400.0),
    _toy_row("n5", None, None, None, D(2026, 9, 16), D(2026, 9, 16), 500.0),
    _toy_row("n6", "Data Analyst", "", "Madrid", D(2026, 9, 17),
             D(2026, 9, 17), 600.0),
]


def test_null_components_are_grouped_by_exact_key(spark, monkeypatch):
    final_rows = _with_changes(NULL_ROWS, [
        _toy_row("n1", "Data Analyst", None, "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, NULL_ROWS)),
                      "__p18_target")
    _install_reference_merge(monkeypatch, [])

    summary = inc.run(
        spark, target, _prepared(spark, final_rows), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 1), to_date=WINDOW_TO,
    )

    assert summary["mode"] == inc.MODE_INCREMENTAL
    assert summary["mode_reason"] is None
    expected = _toy_transform(_prepared(spark, final_rows))
    actual = spark.read.table(target)
    assert _content(actual) == _content(expected)
    assert summary["target_rows"] == len(NULL_ROWS)

    # The bronze key (normalized company and city) equals the silver key.
    keyed = _toy_normalize_company(_prepared(spark, final_rows)).withColumn(
        "bronze_key",
        inc.key_from_columns(
            F.col("title"),
            F.col("company_name"),
            _toy_normalize_city(F.col("location_city")),
        ),
    )
    bronze = {row["job_id"]: row["bronze_key"] for row in keyed.select(
        "job_id", "bronze_key").collect()}
    silver = {row["job_id"]: row["k"] for row in expected.select(
        "job_id", inc.key_expression().alias("k")).collect()}
    assert bronze == silver


# ---------------------------------------------------------------------------
# P19 — a new column of the transform travels with schema evolution
# ---------------------------------------------------------------------------

def test_merge_source_allows_new_columns(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Analyst", "Globex", "Dublin", D(2026, 10, 5),
                 D(2026, 10, 5), 300.0),
        _toy_row("2", "Data Engineer", "Acme", "Madrid", D(2026, 9, 5),
                 D(2026, 9, 5), 500.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Analyst", "Globex", "Cork", D(2026, 10, 11),
                 D(2026, 10, 11), 350.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p19_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    summary = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform_with_extra,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
    )

    merge_src = calls[-1]["merge_src"]
    assert merge_src.columns == TOY_COLUMNS + ["extra"]
    rows = merge_src.collect()
    upserts = [row for row in rows
               if row[inc.INGEST_DATE_COLUMN] is not None]
    markers = [row for row in rows
               if row[inc.INGEST_DATE_COLUMN] is None]
    assert len(upserts) == 1
    assert upserts[0]["job_id"] == "1" and upserts[0]["extra"] == "new"
    # The marker of the emptied old group carries the new column as NULL.
    assert len(markers) == 1
    assert markers[0]["extra"] is None
    assert summary["new_columns"] == ["extra"]

    actual = spark.read.table(target)
    assert "extra" in actual.columns
    # A group that was not rewritten keeps NULL in the evolved column.
    untouched = actual.where(F.col("job_id") == "2").collect()
    assert len(untouched) == 1 and untouched[0]["extra"] is None

# ---------------------------------------------------------------------------
# P20 — the summary has exactly the 19 fields (16 business + 3 of T-02) and
# serializes in one line
# ---------------------------------------------------------------------------

def test_summary_fields_and_json(spark, monkeypatch):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p20_target")
    _install_reference_merge(monkeypatch, [])

    summary = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
    )
    assert len(inc.SUMMARY_FIELDS) == 19
    assert tuple(summary) == inc.SUMMARY_FIELDS
    assert len(summary) == 19
    assert summary["source"] == "indeed"
    assert summary["watermark"] == "2026-09-01"
    assert summary["from_date"] == "2026-10-12"
    assert summary["to_date"] == "2026-10-31"
    assert summary["lookback_days"] == 7
    assert isinstance(summary["elapsed_s"], float)
    assert re.fullmatch(r"[0-9]+\.[0-9]", str(summary["elapsed_s"]))
    for field in ("watermark", "from_date", "to_date"):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", summary[field])
    # T-02: the three new fields are additive; local Spark has no Delta, so
    # the history read degrades to None and the config is read from the
    # session (no query).
    assert isinstance(summary["timings_s"], dict) and summary["timings_s"]
    assert set(summary["config"]) == {"shuffle_partitions",
                                      "adaptive_enabled", "overrides"}
    assert summary["merge_metrics"] is None

    text = json.dumps(summary, ensure_ascii=True,
                      separators=(",", ":"), default=str)
    assert "\n" not in text
    assert json.loads(text) == summary

    # Full mode: from_date and watermark are null (the table does not exist).
    bounds = inc.window_bounds(spark, "__p20_missing")
    full = inc._summary(
        "indeed", bounds, changed_rows=0, affected_groups=0,
        recomputed_groups=3, deleted_groups=0, merge_rows=0,
        new_columns=[], written=True, target_rows=3, elapsed_s=0.04,
        timings_s={"window": 0.01, "full_write": 0.02, "target_count": 0.01},
        merge_metrics=None,
        config={"shuffle_partitions": "1", "adaptive_enabled": "true",
                "overrides": {"shuffle_partitions": "1"}},
    )
    assert tuple(full) == inc.SUMMARY_FIELDS
    assert len(full) == 19
    assert full["mode"] == inc.MODE_FULL
    assert full["mode_reason"] == inc.REASON_TABLE_MISSING
    assert full["watermark"] is None
    assert full["from_date"] is None and full["to_date"] is None
    assert full["changed_rows"] == 0 and full["merge_rows"] == 0
    assert full["new_columns"] == []
    assert full["timings_s"] == {"window": 0.01, "full_write": 0.02,
                                 "target_count": 0.01}
    assert full["config"] == {"shuffle_partitions": "1",
                              "adaptive_enabled": "true",
                              "overrides": {"shuffle_partitions": "1"}}

    # mode_reason only takes the three reasons of full mode or None.
    _publish(spark.createDataFrame([("1",)], ["job_id"]), "__p20_no_date")
    reasons = {
        inc.window_bounds(spark, "__p20_missing").reason,
        inc.window_bounds(spark, "__p20_no_date").reason,
        inc.window_bounds(spark, "__p20_target", force_full=True).reason,
        summary["mode_reason"],
    }
    assert reasons <= {
        None,
        inc.REASON_FORCE_FULL,
        inc.REASON_TABLE_MISSING,
        inc.REASON_MISSING_INGEST_DATE,
    }
    assert {inc.REASON_FORCE_FULL, inc.REASON_TABLE_MISSING,
            inc.REASON_MISSING_INGEST_DATE} <= reasons


# ---------------------------------------------------------------------------
# P21 — repeat_window envelope (T-10 run B)
# ---------------------------------------------------------------------------

def test_repeat_window_envelope(spark, monkeypatch):
    initial = [
        _toy_row("3", "Data Analyst", "Globex", "Dublin", D(2026, 10, 5),
                 D(2026, 10, 5), 300.0),
        _toy_row("5", "Data Engineer", "Acme", "Madrid", D(2026, 9, 5),
                 D(2026, 9, 5), 500.0),
    ]
    final = _with_changes(initial, [
        _toy_row("3", "Data Analyst", "Globex", "Cork", D(2026, 10, 11),
                 D(2026, 10, 11), 350.0),
    ])
    prepared = _prepared(spark, final)
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p21_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    envelope = inc.run(
        spark, target, prepared, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=WINDOW_FROM, to_date=WINDOW_TO,
        repeat_window="true",
    )
    assert envelope["repeat_window"] is True
    first, second = envelope["runs"]
    assert len(envelope["runs"]) == 2
    for run in envelope["runs"]:
        assert tuple(run) == inc.SUMMARY_FIELDS
        assert len(run) == len(inc.SUMMARY_FIELDS) == 19
    # T-02: one MERGE call per pass, no extra write.
    assert len(calls) == 2

    # The content, target_rows and recomputed_groups are the criterion.
    expected = _toy_transform(prepared)
    assert _content(spark.read.table(target)) == _content(expected)
    for field in ("mode", "mode_reason", "written", "changed_rows",
                  "recomputed_groups", "new_columns", "target_rows"):
        assert first[field] == second[field]
    # The first pass already deleted the migrated group: the counts go down
    # and are not an idempotence criterion.
    assert first["deleted_groups"] == 1
    assert second["deleted_groups"] == 0
    assert second["affected_groups"] < first["affected_groups"]
    assert second["merge_rows"] <= first["merge_rows"]

    # Flat summary when the widget is false or empty.
    for value in ("false", ""):
        flat = inc.run(
            spark, target, prepared, source="indeed",
            transform=_toy_transform,
            normalize_company=_toy_normalize_company,
            normalize_city_column=_toy_normalize_city,
            from_date=WINDOW_FROM, to_date=WINDOW_TO,
            repeat_window=value,
        )
        assert "runs" not in flat and "repeat_window" not in flat
        assert tuple(flat) == inc.SUMMARY_FIELDS

    # An invalid widget value fails before any window or write.
    with pytest.raises(ValueError, match="booleano"):
        inc.run(
            spark, "__p21_missing", prepared, source="indeed",
            transform=_toy_transform,
            normalize_company=_toy_normalize_company,
            normalize_city_column=_toy_normalize_city,
            repeat_window="yes",
        )

# ---------------------------------------------------------------------------
# P22 — instrumentation of T-02: one timing step per existing action, the 16
# business values unchanged and the metadata reads degrading without Delta
# ---------------------------------------------------------------------------

#: One step per action that ``_run_once`` already had in incremental mode.
TIMING_STEPS_INCREMENTAL = {
    "window",             # window_bounds (tableExists + read + max)
    "changed_empty",      # changed.isEmpty()
    "affected_count",     # affected.count()
    "recompute_nulls",    # require_no_null_ingest_date
    "recompute_unique",   # require_unique_groups
    "changed_count",      # changed.count()
    "recompute_count",    # recompute.count()
    "markers_count",      # markers.count()
    "merge_source_count", # merge_src.count()
    "merge",              # MERGE WITH SCHEMA EVOLUTION
    "target_count",       # target.count() after the MERGE
}


def test_timed_accumulates_by_step_and_reraises():
    timings = {}

    assert inc._timed(timings, "noop", lambda: 42) == 42
    assert timings["noop"] >= 0.0

    inc._timed(timings, "noop", lambda: time.sleep(0.02))
    assert timings["noop"] >= 0.01

    # Another step does not touch the first one.
    inc._timed(timings, "other", lambda: None)
    assert timings["noop"] >= 0.01
    assert timings["other"] >= 0.0

    # The exception is re-raised and the elapsed time is still accumulated.
    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        inc._timed(timings, "fail", fail)
    assert "fail" in timings


class _FakeRow:
    """Minimal ``Row``: ``_merge_metrics`` only calls ``asDict``."""

    def __init__(self, **values):
        self._values = values

    def asDict(self):
        return dict(self._values)


class _FakeHistory:
    def __init__(self, rows):
        self._rows = rows

    def collect(self):
        return list(self._rows)


class _FakeSpark:
    """Double of ``SparkSession``: records the SQL and returns fake rows."""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    def sql(self, statement):
        self.statements.append(statement)
        return _FakeHistory(self._rows)


def test_merge_metrics_only_uses_the_merge_operation():
    metrics = {"numTargetRowsInserted": "3", "numTargetRowsUpdated": "2"}
    merge = _FakeRow(operation="MERGE", operationMetrics=metrics)
    write = _FakeRow(operation="WRITE", operationMetrics={"numFiles": "1"})

    # The newest version is this run's MERGE: its metrics are returned.
    spark = _FakeSpark([merge, write])
    assert inc._merge_metrics(spark, "__p22_history") == metrics
    assert "DESCRIBE HISTORY __p22_history LIMIT 2" in spark.statements

    # The operation name is matched case-insensitively.
    lower = _FakeRow(operation=" merge ", operationMetrics=metrics)
    assert inc._merge_metrics(_FakeSpark([lower]), "__p22_history") == \
        metrics

    # A newer WRITE with an older MERGE: the MERGE metrics are still the
    # ones of this run, which is exactly why two versions are read.
    assert inc._merge_metrics(
        _FakeSpark([write, merge]), "__p22_history") == metrics
    # No MERGE among the last two versions.
    assert inc._merge_metrics(
        _FakeSpark([write, _FakeRow(operation="OPTIMIZE")]),
        "__p22_history",
    ) is None
    # A MERGE without metrics is not reported with invented values.
    assert inc._merge_metrics(
        _FakeSpark([_FakeRow(operation="MERGE", operationMetrics=None)]),
        "__p22_history",
    ) is None


def test_merge_metrics_and_config_degrade_without_delta(spark):
    _publish(spark.createDataFrame([("1",)], ["job_id"]),
             "__p22_no_delta")
    # A temporary view has no Delta history: the read degrades to None.
    assert inc._merge_metrics(spark, "__p22_no_delta") is None
    # A missing table must never fail the run either.
    assert inc._merge_metrics(spark, "__p22_missing_table") is None

    # The config reports the effective values (runtime default included) and
    # which keys this session set explicitly. No query is involved.
    config = inc._config(spark)
    assert set(config) == {"shuffle_partitions", "adaptive_enabled",
                           "overrides"}
    assert config["shuffle_partitions"] == spark.conf.get(
        "spark.sql.shuffle.partitions")
    assert config["adaptive_enabled"] == spark.conf.get(
        "spark.sql.adaptive.enabled")
    assert config["adaptive_enabled"] is not None
    # The fixture sets shuffle.partitions=1 at session creation.
    assert config["overrides"]["shuffle_partitions"] == "1"
    assert "adaptive_enabled" not in config["overrides"]


def test_full_mode_timing_steps(spark, monkeypatch):
    def fake_save_as_table(writer, name, *args, **kwargs):
        """Stand-in for ``saveAsTable`` using this file's own mechanism.

        The tests have no winutils/Delta, so the write is not possible. Every
        table of the file is published as a temporary view with ``_publish``;
        the fake applies that same mechanism, so the module's later
        ``read.table`` sees the frame without duplicating a writer.
        """
        _publish(writer._df, name)

    monkeypatch.setattr(DataFrameWriter, "saveAsTable", fake_save_as_table)
    prepared = _prepared(spark, SCENARIO_INITIAL)

    summary = inc.run(
        spark, "__p22_full", prepared, source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        force_full=True,
    )

    assert summary["mode"] == inc.MODE_FULL
    assert summary["mode_reason"] == inc.REASON_FORCE_FULL
    assert summary["written"] is True
    assert summary["changed_rows"] == 0 and summary["merge_rows"] == 0
    assert summary["merge_metrics"] is None
    assert summary["target_rows"] == _toy_transform(prepared).count()
    # The full path has its own three actions: window, transform+write and
    # the final count (no MERGE).
    assert set(summary["timings_s"]) == {
        "window", "full_write", "target_count"}


def test_incremental_summary_adds_no_action_and_keeps_the_16_values(
    spark, monkeypatch
):
    initial = [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Analyst", "Globex", "Dublin", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
    ]
    final = _with_changes(initial, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 10, 12),
                 D(2026, 10, 12), 150.0),
    ])
    target = _publish(_toy_transform(_prepared(spark, initial)),
                      "__p22_target")
    calls = []
    _install_reference_merge(monkeypatch, calls)

    summary = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
    )

    # The 16 business fields keep the values they had before T-02.
    assert {name: summary[name] for name in inc.SUMMARY_FIELDS[:16]} == {
        "source": "indeed",
        "mode": inc.MODE_INCREMENTAL,
        "mode_reason": None,
        "watermark": "2026-09-02",
        "from_date": "2026-10-12",
        "to_date": "2026-10-31",
        "lookback_days": 7,
        "changed_rows": 1,
        "affected_groups": 1,
        "recomputed_groups": 1,
        "deleted_groups": 0,
        "merge_rows": 1,
        "new_columns": [],
        "written": True,
        "target_rows": 2,
        "elapsed_s": summary["elapsed_s"],
    }
    assert isinstance(summary["elapsed_s"], float)
    # Exactly one step per existing action: the instrumentation adds none.
    assert set(summary["timings_s"]) == TIMING_STEPS_INCREMENTAL
    for step, seconds in summary["timings_s"].items():
        assert isinstance(seconds, float) and seconds >= 0.0, step
    assert len(calls) == 1  # one MERGE per pass
    assert summary["merge_metrics"] is None  # no Delta locally
    assert set(summary["config"]) == {"shuffle_partitions",
                                      "adaptive_enabled", "overrides"}

    # The empty window keeps its short-circuit: three actions, no MERGE.
    empty = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 12, 1),
    )
    assert set(empty["timings_s"]) == {
        "window", "changed_empty", "target_count"}
    assert empty["written"] is False
    assert len(calls) == 1

    # repeat_window: two passes and two MERGE calls, same step keys.
    envelope = inc.run(
        spark, target, _prepared(spark, final), source="indeed",
        transform=_toy_transform,
        normalize_company=_toy_normalize_company,
        normalize_city_column=_toy_normalize_city,
        from_date=D(2026, 10, 12), to_date=WINDOW_TO,
        repeat_window=True,
    )
    assert len(calls) == 3
    assert len(envelope["runs"]) == 2
    for run in envelope["runs"]:
        assert set(run["timings_s"]) == TIMING_STEPS_INCREMENTAL


# ---------------------------------------------------------------------------
# Validation rules of the module contract (§4): control column, uniqueness,
# unknown source.
# ---------------------------------------------------------------------------

def test_changed_since_validates_the_control_column(spark):
    window = inc.Window(
        mode=inc.MODE_INCREMENTAL, reason=None, watermark=None,
        from_date=None, to_date=None,
        lookback_days=inc.DEFAULT_LOOKBACK_DAYS,
    )
    rows = spark.createDataFrame([("1", D(2026, 10, 1))],
                                 ["job_id", "_ingest_date"])
    assert inc.changed_since(rows, window).count() == 1

    without = spark.createDataFrame([("1",)], ["job_id"])
    with pytest.raises(ValueError, match="_ingest_date"):
        inc.changed_since(without, window)

    wrong_type = spark.createDataFrame([("1", "2026-10-01")],
                                       ["job_id", "_ingest_date"])
    with pytest.raises(ValueError, match="date"):
        inc.changed_since(wrong_type, window)


def test_recompute_rejects_duplicate_groups_and_unknown_source(spark):
    duplicated = _prepared(spark, [
        _toy_row("1", "Data Engineer", "Acme", "Madrid", D(2026, 9, 1),
                 D(2026, 9, 1), 100.0),
        _toy_row("2", "Data Engineer", "Acme", "Madrid", D(2026, 9, 2),
                 D(2026, 9, 2), 200.0),
    ])
    recompute = (_toy_normalize_company(duplicated)
                 .withColumn(inc.KEY, inc.key_expression()))
    assert recompute.select(inc.KEY).distinct().count() == 1
    with pytest.raises(ValueError, match="mas de una fila por grupo"):
        inc.require_unique_groups(recompute)
    inc.require_unique_groups(_toy_transform(duplicated))

    with pytest.raises(ValueError, match="Fuente desconocida"):
        inc.get_source_spec("glassdoor")
    with pytest.raises(ValueError, match="Fuente desconocida"):
        inc.run(
            spark, "__validation_missing", _prepared(spark, []),
            source="glassdoor", transform=_toy_transform,
            normalize_company=_toy_normalize_company,
            normalize_city_column=_toy_normalize_city,
        )
