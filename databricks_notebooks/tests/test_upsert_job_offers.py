"""Tests for Upsert_Job_Offers (real Spark DataFrames, spec 008 T-02).

Bronze-like schemas per source: Indeed/InfoJobs carry a TIMESTAMP capture
column and LinkedIn/multi_site an ISO-8601 string; ``_ingest_date`` is DATE and
``_source_file`` a string. Runs locally with pyspark (see requirements.txt)
and on Databricks; if pyspark is not installed, the module skips
automatically. The session time zone is fixed to UTC so the ``_row_hash``
fingerprint (``to_json`` of timestamps) is stable.
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

import Upsert_Job_Offers as upsert  # noqa: E402


# ---------------------------------------------------------------------------
# SparkSession for tests (local, one core, no UI): same Windows config as
# test_dedup_job_offers.py, plus a fixed UTC session time zone.
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
        .appName("job-offers-upsert-tests")
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
    # getOrCreate may return a session created by another test module, where
    # builder configs no longer apply: set the time zone explicitly.
    previous_time_zone = session.conf.get("spark.sql.session.timeZone", None)
    session.conf.set("spark.sql.session.timeZone", "UTC")
    # Warm-up: force a Python worker to start and retry if the handshake
    # fails (known transient failure on Windows/local).
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
# Bronze-like schemas per source (design-upsert.md D0)
# ---------------------------------------------------------------------------

INDEED_SCHEMA = T.StructType([
    T.StructField("job_key", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("scraped_at", T.TimestampType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])

LINKEDIN_SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("scraped_at", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])

INFOJOBS_SCHEMA = T.StructType([
    T.StructField("id_oferta", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("fecha_scraped", T.TimestampType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])

MULTISITE_SCHEMA = T.StructType([
    T.StructField("site", T.StringType(), True),
    T.StructField("job_id", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("scraped_at", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
    T.StructField("_source_file", T.StringType(), True),
])

SCHEMAS = {
    "indeed": INDEED_SCHEMA,
    "linkedin": LINKEDIN_SCHEMA,
    "infojobs": INFOJOBS_SCHEMA,
    "multi_site": MULTISITE_SCHEMA,
}

SOURCE_FILE = (
    "abfss://landing@account.dfs.core.windows.net/linkedin/jobs_20261001.parquet"
)


def _batch(spark, source, rows):
    """Build a bronze-like DataFrame for ``source``, filling missing fields."""
    schema = SCHEMAS[source]
    defaults = {field.name: None for field in schema.fields}
    return spark.createDataFrame(
        [{**defaults, **row} for row in rows], schema=schema
    )


def _prepare(spark, source, rows):
    """Return ``(batch, valid, quarantine, deduped)`` for the given rows."""
    batch = _batch(spark, source, rows)
    valid, quarantine = upsert.split_quarantine(batch, source)
    return batch, valid, quarantine, upsert.dedupe_batch(valid, source)


def _rows(df):
    """Rows as tuples, sorted with a None-safe key (for comparisons)."""
    return sorted((tuple(row) for row in df.collect()), key=repr)


# ---------------------------------------------------------------------------
# Batch dedup
# ---------------------------------------------------------------------------

def test_latest_instant_wins_per_key(spark):
    _, _, quarantine, deduped = _prepare(spark, "linkedin", [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "old capture"},
        {"job_id": "1", "scraped_at": "2026-10-03T08:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "new capture"},
        {"job_id": "2", "scraped_at": "2026-10-02T08:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "only capture"},
    ])
    rows = {row["job_id"]: row for row in deduped.collect()}
    assert quarantine.count() == 0
    assert set(rows) == {"1", "2"}
    assert rows["1"]["title"] == "new capture"
    assert rows["1"]["scraped_at"] == "2026-10-03T08:00:00+00:00"
    assert rows["1"]["_ingest_date"] == dt.date(2026, 10, 2)
    assert rows["2"]["title"] == "only capture"


def test_ingest_date_breaks_instant_tie(spark):
    _, _, _, deduped = _prepare(spark, "linkedin", [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "older ingest"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "newer ingest"},
    ])
    out = deduped.collect()
    assert len(out) == 1
    assert out[0]["title"] == "newer ingest"
    assert out[0]["_ingest_date"] == dt.date(2026, 10, 2)


def test_same_day_later_capture_wins(spark):
    # D3: two captures on the same day must be ordered by instant, so the
    # later hour wins even though the date is the same.
    _, _, _, deduped = _prepare(spark, "linkedin", [
        {"job_id": "7", "scraped_at": "2026-10-05T09:30:00+00:00",
         "_ingest_date": dt.date(2026, 10, 5), "title": "morning"},
        {"job_id": "7", "scraped_at": "2026-10-05T18:45:00+00:00",
         "_ingest_date": dt.date(2026, 10, 5), "title": "evening"},
    ])
    out = deduped.collect()
    assert len(out) == 1
    assert out[0]["title"] == "evening"
    assert out[0]["scraped_at"] == "2026-10-05T18:45:00+00:00"


def test_input_order_does_not_change_the_result(spark):
    rows = [
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "old"},
        {"site": "linkedin", "job_id": "1",
         "scraped_at": "2026-10-01T18:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "new"},
        {"site": "indeed", "job_id": "9",
         "scraped_at": "2026-10-02T00:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 2), "title": "other"},
    ]
    forward = _prepare(spark, "multi_site", rows)[3]
    backward = _prepare(spark, "multi_site", list(reversed(rows)))[3]
    assert _rows(forward) == _rows(backward)
    assert len(_rows(forward)) == 2


def test_exact_tie_keeps_a_single_row(spark):
    _, _, _, deduped = _prepare(spark, "linkedin", [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "tie a"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "title": "tie b"},
    ])
    out = deduped.collect()
    assert len(out) == 1
    assert out[0]["title"] in {"tie a", "tie b"}


# ---------------------------------------------------------------------------
# Quarantine split
# ---------------------------------------------------------------------------

def test_null_empty_and_blank_keys_go_to_quarantine(spark):
    _, valid, quarantine, _ = _prepare(spark, "linkedin", [
        {"job_id": None, "scraped_at": "2026-10-01T10:00:00+00:00",
         "title": "null key"},
        {"job_id": "", "scraped_at": "2026-10-01T10:00:00+00:00",
         "title": "empty key"},
        {"job_id": "   ", "scraped_at": "2026-10-01T10:00:00+00:00",
         "title": "blank key"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "title": "valid"},
    ])
    assert valid.count() == 1
    assert valid.collect()[0]["title"] == "valid"
    reasons = {
        row["title"]: row[upsert.QUARANTINE_REASON_COLUMN]
        for row in quarantine.collect()
    }
    assert reasons == {
        "null key": upsert.MISSING_KEY,
        "empty key": upsert.MISSING_KEY,
        "blank key": upsert.MISSING_KEY,
    }


def test_null_blank_and_unparseable_temporal_go_to_quarantine(spark):
    _, valid, quarantine, _ = _prepare(spark, "linkedin", [
        {"job_id": "1", "scraped_at": None, "title": "null temporal"},
        {"job_id": "2", "scraped_at": "", "title": "empty temporal"},
        {"job_id": "3", "scraped_at": "no-es-fecha", "title": "bad temporal"},
    ])
    assert valid.count() == 0
    reasons = {
        row["title"]: row[upsert.QUARANTINE_REASON_COLUMN]
        for row in quarantine.collect()
    }
    assert reasons == {
        "null temporal": upsert.MISSING_SCRAPED_AT,
        "empty temporal": upsert.MISSING_SCRAPED_AT,
        "bad temporal": upsert.MISSING_SCRAPED_AT,
    }


def test_missing_key_takes_precedence_over_missing_temporal(spark):
    _, valid, quarantine, _ = _prepare(spark, "linkedin", [
        {"job_id": None, "scraped_at": None, "title": "both missing"},
        {"job_id": "", "scraped_at": "no-es-fecha", "title": "empty key bad time"},
    ])
    assert valid.count() == 0
    reasons = {
        row["title"]: row[upsert.QUARANTINE_REASON_COLUMN]
        for row in quarantine.collect()
    }
    assert reasons == {
        "both missing": upsert.MISSING_KEY,
        "empty key bad time": upsert.MISSING_KEY,
    }


def test_blank_site_in_multisite_goes_to_quarantine(spark):
    _, valid, quarantine, _ = _prepare(spark, "multi_site", [
        {"site": None, "job_id": "1",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "null site"},
        {"site": "   ", "job_id": "2",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "blank site"},
        {"site": "irishjobs", "job_id": None,
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "null job id"},
        {"site": "irishjobs", "job_id": "3",
         "scraped_at": "2026-10-01T10:00:00+00:00", "title": "valid"},
    ])
    assert valid.count() == 1
    assert valid.collect()[0]["title"] == "valid"
    reasons = {
        row["title"]: row[upsert.QUARANTINE_REASON_COLUMN]
        for row in quarantine.collect()
    }
    assert reasons == {
        "null site": upsert.MISSING_KEY,
        "blank site": upsert.MISSING_KEY,
        "null job id": upsert.MISSING_KEY,
    }


# ---------------------------------------------------------------------------
# Row fingerprint
# ---------------------------------------------------------------------------

def test_row_hash_distinguishes_null_empty_and_separator_collisions(spark):
    schema = T.StructType([
        T.StructField("left", T.StringType(), True),
        T.StructField("right", T.StringType(), True),
        T.StructField("_ingest_date", T.DateType(), True),
    ])
    defaults = {field.name: None for field in schema.fields}
    rows = [
        {"left": "a", "right": None},
        {"left": "a", "right": ""},
        {"left": "a|b", "right": "c"},
        {"left": "a", "right": "b|c"},
    ]
    df = spark.createDataFrame(
        [{**defaults, **row} for row in rows], schema=schema
    )
    hashed = df.withColumn(upsert.ROW_HASH_COLUMN, upsert.row_hash(df))
    hashes = {
        (row["left"], row["right"]): row[upsert.ROW_HASH_COLUMN]
        for row in hashed.collect()
    }
    # to_json(struct(...)) keeps NULL and "" apart and has no separator.
    assert hashes[("a", None)] != hashes[("a", "")]
    assert hashes[("a|b", "c")] != hashes[("a", "b|c")]
    assert len(set(hashes.values())) == 4


def test_row_hash_ignores_control_columns(spark):
    schema = T.StructType([
        T.StructField("left", T.StringType(), True),
        T.StructField("_ingest_date", T.DateType(), True),
        T.StructField("_source_file", T.StringType(), True),
        T.StructField("_quarantine_reason", T.StringType(), True),
        T.StructField("_quarantine_at", T.TimestampType(), True),
    ])
    rows = [
        {"left": "a", "_ingest_date": dt.date(2026, 10, 1),
         "_source_file": "file-1", "_quarantine_reason": "missing_key",
         "_quarantine_at": dt.datetime(2026, 10, 1, 10, 0)},
        {"left": "a", "_ingest_date": dt.date(2026, 10, 2),
         "_source_file": "file-2", "_quarantine_reason": "missing_scraped_at",
         "_quarantine_at": dt.datetime(2026, 10, 2, 10, 0)},
    ]
    df = spark.createDataFrame(rows, schema=schema)
    hashes = {
        row[upsert.ROW_HASH_COLUMN]
        for row in df.withColumn(
            upsert.ROW_HASH_COLUMN, upsert.row_hash(df)
        ).collect()
    }
    assert len(hashes) == 1


# ---------------------------------------------------------------------------
# Quarantine table append
# ---------------------------------------------------------------------------

def test_reprocessing_a_batch_does_not_grow_quarantine(spark):
    _, _, quarantine, _ = _prepare(spark, "linkedin", [
        {"job_id": None, "scraped_at": "2026-10-01T10:00:00+00:00",
         "_source_file": SOURCE_FILE, "title": "null key"},
        {"job_id": "", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_source_file": SOURCE_FILE, "title": "empty key"},
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_source_file": SOURCE_FILE, "title": "valid"},
    ])
    first = upsert.new_quarantine_rows(quarantine, quarantine.limit(0))
    assert first.count() == 2
    second = upsert.new_quarantine_rows(quarantine, first)
    assert second.count() == 0


def test_duplicate_quarantine_rows_in_one_batch_are_written_once(spark):
    repeated = {
        "job_id": None,
        "scraped_at": "2026-10-01T10:00:00+00:00",
        "_source_file": SOURCE_FILE,
        "title": "same invalid row",
    }
    _, _, quarantine, _ = _prepare(spark, "linkedin", [repeated, repeated])
    assert quarantine.count() == 2
    written = upsert.new_quarantine_rows(quarantine, quarantine.limit(0))
    assert written.count() == 1


# ---------------------------------------------------------------------------
# Column contract
# ---------------------------------------------------------------------------

def test_output_frames_keep_the_column_contract(spark):
    batch, valid, quarantine, deduped = _prepare(spark, "linkedin", [
        {"job_id": "1", "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "_source_file": SOURCE_FILE,
         "title": "valid"},
        {"job_id": None, "scraped_at": "2026-10-01T10:00:00+00:00",
         "_ingest_date": dt.date(2026, 10, 1), "_source_file": SOURCE_FILE,
         "title": "null key"},
    ])
    assert valid.columns == batch.columns
    assert deduped.columns == batch.columns
    assert deduped.schema == batch.schema
    for frame in (valid, deduped):
        assert upsert.SCRAPED_TS_COLUMN not in frame.columns
        assert upsert.ROW_HASH_COLUMN not in frame.columns
        assert not [name for name in frame.columns
                    if name.startswith("_quarantine_")]
    assert quarantine.columns == list(batch.columns) + [
        upsert.ROW_HASH_COLUMN,
        upsert.QUARANTINE_REASON_COLUMN,
        upsert.QUARANTINE_AT_COLUMN,
    ]
    row = quarantine.collect()[0]
    assert row[upsert.QUARANTINE_REASON_COLUMN] == upsert.MISSING_KEY
    assert row[upsert.ROW_HASH_COLUMN] is not None
    assert row[upsert.QUARANTINE_AT_COLUMN] is not None
    assert row[upsert.SOURCE_FILE_COLUMN] == SOURCE_FILE


def test_split_adds_source_file_as_null_when_the_batch_lacks_it(spark):
    schema = T.StructType([
        T.StructField("job_id", T.StringType(), True),
        T.StructField("scraped_at", T.StringType(), True),
        T.StructField("_ingest_date", T.DateType(), True),
    ])
    df = spark.createDataFrame(
        [{"job_id": None, "scraped_at": "2026-10-01T10:00:00+00:00",
          "_ingest_date": dt.date(2026, 10, 1)}],
        schema=schema,
    )
    _, quarantine = upsert.split_quarantine(df, "linkedin")
    row = quarantine.collect()[0]
    assert upsert.SOURCE_FILE_COLUMN in quarantine.columns
    assert row[upsert.SOURCE_FILE_COLUMN] is None
    assert row[upsert.QUARANTINE_REASON_COLUMN] == upsert.MISSING_KEY


# ---------------------------------------------------------------------------
# The four bronze schemas (timestamp and ISO string capture columns)
# ---------------------------------------------------------------------------

SOURCE_CASES = [
    (
        "indeed",
        T.TimestampType(),
        {"job_key": "J-1",
         "scraped_at": dt.datetime(2026, 10, 1, 10, 0)},
        {"job_key": "J-1",
         "scraped_at": dt.datetime(2026, 10, 3, 8, 0)},
    ),
    (
        "infojobs",
        T.TimestampType(),
        {"id_oferta": "OF-1",
         "fecha_scraped": dt.datetime(2026, 10, 1, 10, 0)},
        {"id_oferta": "OF-1",
         "fecha_scraped": dt.datetime(2026, 10, 3, 8, 0)},
    ),
    (
        "linkedin",
        T.StringType(),
        {"job_id": "LI-1",
         "scraped_at": "2026-10-01T10:00:00+00:00"},
        {"job_id": "LI-1",
         "scraped_at": "2026-10-03T08:00:00+00:00"},
    ),
    (
        "multi_site",
        T.StringType(),
        {"site": "irishjobs", "job_id": "MS-1",
         "scraped_at": "2026-10-01T10:00:00+00:00"},
        {"site": "irishjobs", "job_id": "MS-1",
         "scraped_at": "2026-10-03T08:00:00+00:00"},
    ),
]


@pytest.mark.parametrize(
    "source, temporal_type, old_capture, new_capture",
    SOURCE_CASES,
    ids=[case[0] for case in SOURCE_CASES],
)
def test_source_schemas_have_the_expected_temporal_type_and_dedupe(
    spark, source, temporal_type, old_capture, new_capture
):
    spec = upsert.SOURCE_SPECS[source]
    assert SCHEMAS[source][spec.scraped_at].dataType == temporal_type
    old = {**old_capture, "title": "old",
           "_ingest_date": dt.date(2026, 10, 1)}
    new = {**new_capture, "title": "new",
           "_ingest_date": dt.date(2026, 10, 3)}
    batch, _, quarantine, deduped = _prepare(spark, source, [old, new])
    assert quarantine.count() == 0
    assert deduped.columns == batch.columns
    assert deduped.schema == batch.schema
    out = deduped.collect()
    assert len(out) == 1
    assert out[0]["title"] == "new"
    assert out[0][spec.scraped_at] == new[spec.scraped_at]


# ---------------------------------------------------------------------------
# MERGE statement
# ---------------------------------------------------------------------------

MERGE_CASES = [
    ("indeed", "ON t.job_key = s.job_key"),
    ("linkedin", "ON t.job_id = s.job_id"),
    ("infojobs", "ON t.id_oferta = s.id_oferta"),
    ("multi_site", "ON t.site = s.site AND t.job_id = s.job_id"),
]


@pytest.mark.parametrize(
    "source, expected_on", MERGE_CASES, ids=[case[0] for case in MERGE_CASES]
)
def test_merge_sql_uses_the_source_key_and_capture_condition(
    source, expected_on
):
    target = f"job_offers.bronze.{source}"
    sql = upsert.merge_sql(target, source)
    temporal = upsert.SOURCE_SPECS[source].scraped_at
    assert upsert.BATCH_VIEW_NAME == "__batch"
    assert f"MERGE WITH SCHEMA EVOLUTION INTO {target} AS t" in sql
    assert "MERGE INTO" not in sql
    assert f"USING {upsert.BATCH_VIEW_NAME} AS s" in sql
    assert expected_on in sql
    assert f"try_to_timestamp(t.{temporal}) IS NULL" in sql
    assert (
        f"try_to_timestamp(s.{temporal}) > try_to_timestamp(t.{temporal})"
        in sql
    )
    assert "s._ingest_date > t._ingest_date" in sql
    assert "WHEN MATCHED AND (" in sql
    assert "THEN UPDATE SET *" in sql
    assert "WHEN NOT MATCHED THEN INSERT *" in sql


def test_merge_sql_uses_the_requested_view_name():
    # The four streams run concurrently: a source must be able to merge from
    # its own temporary view instead of the shared BATCH_VIEW_NAME.
    target = "job_offers.bronze.linkedin"

    default_sql = upsert.merge_sql(target, "linkedin")
    assert f"USING {upsert.BATCH_VIEW_NAME} AS s" in default_sql

    custom_sql = upsert.merge_sql(
        target, "linkedin", view_name="__batch_linkedin"
    )
    assert "USING __batch_linkedin AS s" in custom_sql
    assert f"USING {upsert.BATCH_VIEW_NAME} AS s" not in custom_sql
    # Only the view changes: the rest of the statement is the same.
    assert custom_sql == default_sql.replace(
        f"USING {upsert.BATCH_VIEW_NAME} AS s", "USING __batch_linkedin AS s"
    )


def test_unknown_source_raises_value_error(spark):
    batch = _batch(spark, "linkedin", [])
    with pytest.raises(ValueError, match="Fuente desconocida"):
        upsert.split_quarantine(batch, "glassdoor")
    with pytest.raises(ValueError, match="Fuente desconocida"):
        upsert.dedupe_batch(batch, "glassdoor")
    with pytest.raises(ValueError, match="Fuente desconocida"):
        upsert.merge_sql("job_offers.bronze.glassdoor", "glassdoor")
