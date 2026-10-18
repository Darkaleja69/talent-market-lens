"""Tests for Deduplicate_Job_Offers.keep_latest_by (real Spark DataFrames).

Bronze-like schema: ``job_id``/``site`` strings, ``scraped_at`` as an ISO-8601
string with the single ``+00:00`` offset used in the landing, ``_ingest_date``
as DATE and ``title`` as payload. Runs locally with pyspark (see
requirements.txt) and on Databricks; if pyspark is not installed, the module
skips automatically.
"""
import datetime as dt
import os
import sys
import tempfile
import time

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Deduplicate_Job_Offers as dedup  # noqa: E402


# ---------------------------------------------------------------------------
# SparkSession for tests (local, one core, no UI): same Windows config as
# test_integration_spark.py.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def spark():
    # On Windows/local this keeps the Python workers on the same interpreter
    # and stops the driver from advertising an unreachable network interface.
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("job-offers-integration-tests")
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
        .getOrCreate()
    )
    # Warm-up: force a Python worker to start and retry if the handshake
    # fails (known transient failure on Windows/local).
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 1).map(lambda x: x).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("site", T.StringType(), True),
    T.StructField("scraped_at", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("title", T.StringType(), True),
])

DEFAULTS = {f.name: None for f in SCHEMA.fields}


def _row(**kwargs):
    row = dict(DEFAULTS)
    row.update(kwargs)
    return row


def _df(spark, rows):
    return spark.createDataFrame([_row(**r) for r in rows], schema=SCHEMA)


def _collected(df):
    """Rows as tuples, sorted with a None-safe key (for comparisons)."""
    return sorted((tuple(r) for r in df.collect()), key=repr)


# ---------------------------------------------------------------------------
# keep_latest_by
# ---------------------------------------------------------------------------

def test_latest_scraped_at_wins_per_job_id(spark):
    df = _df(spark, [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "old capture"},
        {"job_id": "1", "scraped_at": "2026-10-03T08:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "new capture"},
    ])
    out = dedup.keep_latest_by(df, ["job_id"]).collect()
    assert len(out) == 1
    assert out[0]["title"] == "new capture"
    assert out[0]["scraped_at"] == "2026-10-03T08:00:00+00:00"
    assert out[0]["_ingest_date"] == dt.date(2026, 10, 2)


def test_ingest_date_breaks_scraped_at_tie(spark):
    df = _df(spark, [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "older ingest"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "newer ingest"},
    ])
    out = dedup.keep_latest_by(df, ["job_id"]).collect()
    assert len(out) == 1
    assert out[0]["title"] == "newer ingest"
    assert out[0]["_ingest_date"] == dt.date(2026, 10, 2)


def test_distinct_job_ids_are_all_kept(spark):
    df = _df(spark, [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00", "title": "a"},
        {"job_id": "2", "scraped_at": "2026-10-01T10:00:00+00:00", "title": "b"},
        {"job_id": "3", "scraped_at": "2026-10-01T10:00:00+00:00", "title": "c"},
    ])
    out = dedup.keep_latest_by(df, ["job_id"])
    assert out.count() == 3
    assert {r["title"] for r in out.collect()} == {"a", "b", "c"}


def test_missing_keys_pass_through_and_valid_pair_is_reduced(spark):
    df = _df(spark, [
        {"job_id": None, "site": "linkedin",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "null key old"},
        {"job_id": None, "site": "linkedin",
         "scraped_at": "2026-10-02T10:00:00+00:00", "title": "null key new"},
        {"job_id": "", "site": "indeed",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "empty key"},
        {"job_id": "   ", "site": "infojobs",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "blank key"},
        {"job_id": "1", "site": "linkedin",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "duplicate old"},
        {"job_id": "1", "site": "linkedin",
         "scraped_at": "2026-10-02T10:00:00+00:00", "title": "duplicate new"},
    ])
    rows = {r["title"]: r for r in dedup.keep_latest_by(df, ["job_id"]).collect()}
    # The two rows sharing the same missing key (job_id=None, site="linkedin")
    # must both survive: the guard bypasses the window selection, so this
    # distinguishes it from a bare row_number() (which would keep only one).
    assert set(rows) == {"null key old", "null key new", "empty key",
                         "blank key", "duplicate new"}
    assert rows["null key old"]["job_id"] is None
    assert rows["null key new"]["job_id"] is None
    assert rows["null key old"]["site"] == "linkedin"
    assert rows["null key new"]["site"] == "linkedin"
    assert rows["empty key"]["job_id"] == ""
    assert rows["blank key"]["job_id"] == "   "
    assert rows["null key old"]["scraped_at"] == "2026-10-01T10:00:00+00:00"
    assert rows["null key new"]["scraped_at"] == "2026-10-02T10:00:00+00:00"
    assert rows["duplicate new"]["scraped_at"] == "2026-10-02T10:00:00+00:00"


def test_multisite_keeps_one_row_per_site_and_job_id(spark):
    df = _df(spark, [
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "linkedin old"},
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-02T10:00:00+00:00", "title": "linkedin new"},
        {"site": "indeed", "job_id": "1",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "indeed same id"},
        {"site": "indeed", "job_id": "2",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "indeed other id"},
    ])
    out = {(r["site"], r["job_id"]): r["title"]
           for r in dedup.keep_latest_by(df, ["site", "job_id"]).collect()}
    assert out == {
        ("linkedin", "1"): "linkedin new",
        ("indeed", "1"): "indeed same id",
        ("indeed", "2"): "indeed other id",
    }


def test_column_contract_is_preserved_without_helper_column(spark):
    df = _df(spark, [
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "a"},
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-02T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "b"},
    ])
    out = dedup.keep_latest_by(df, ["site", "job_id"])
    assert out.columns == df.columns
    assert out.schema == df.schema
    assert "_keep_latest_rn" not in out.columns


def test_second_application_is_idempotent(spark):
    df = _df(spark, [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00", "title": "old"},
        {"job_id": "1", "scraped_at": "2026-10-03T10:00:00+00:00", "title": "new"},
        {"job_id": "2", "scraped_at": "2026-10-02T10:00:00+00:00", "title": "only"},
        {"job_id": None, "scraped_at": "2026-10-01T10:00:00+00:00", "title": "null"},
        {"job_id": "", "scraped_at": "2026-10-01T10:00:00+00:00", "title": "empty"},
    ])
    once = dedup.keep_latest_by(df, ["job_id"])
    twice = dedup.keep_latest_by(once, ["job_id"])
    assert _collected(once) == _collected(twice)


def test_exact_tie_keeps_exactly_one_row(spark):
    df = _df(spark, [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "tie a"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "tie b"},
    ])
    out = dedup.keep_latest_by(df, ["job_id"]).collect()
    assert len(out) == 1
    assert out[0]["title"] in {"tie a", "tie b"}


def test_empty_keys_or_order_by_raise_value_error(spark):
    df = _df(spark, [{"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00"}])
    with pytest.raises(ValueError):
        dedup.keep_latest_by(df, [])
    with pytest.raises(ValueError):
        dedup.keep_latest_by(df, ["job_id"], order_by=[])
