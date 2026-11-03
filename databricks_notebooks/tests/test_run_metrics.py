"""Tests for the run metrics module (spec 011, T-04 and T-05).

The pure part pins the thirteen-column contract, the row builder, the stable
JSON and the best-effort writer without touching Spark. The closing task adds
the landing/checkpoint rule (:func:`Run_Metrics.pending_files`), the checks of
the run (:func:`Run_Metrics.build_checks`, with an injected ``now``) and the
notebook that only coordinates them. The integration part writes to a Delta
table with a local Spark session and checks the ``MERGE`` by ``run_id``:
create, idempotency, two runs and an unreachable table.

Delta needs ``winutils.exe``/``hadoop.dll`` on Windows (``conftest.py``
configures ``HADOOP_HOME`` at import time, before the first session), and the
integration fixture calls ``require_writable_local_fs`` so a host that cannot
write with Hadoop skips with instructions instead of failing. When Delta is
not importable, or an already-running session was created without the Delta
extension (shared suite), the integration tests skip with instructions too;
run this file alone to execute them:

    python -m pytest databricks_notebooks/tests/test_run_metrics.py -q
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import SparkSession  # noqa: E402

DATABRICKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DATABRICKS_DIR)
sys.path.insert(0, TESTS_DIR)

import Run_Metrics as metrics  # noqa: E402
from _local_spark import require_writable_local_fs  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures of the pure part
# ---------------------------------------------------------------------------

CONTRACT = (
    ("run_id", "string"),
    ("job_id", "string"),
    ("started_at", "timestamp"),
    ("finished_at", "timestamp"),
    ("duration_s", "double"),
    ("bronze_rows", "bigint"),
    ("quarantine_rows", "bigint"),
    ("data_date", "date"),
    ("data_age_days", "int"),
    ("status", "string"),
    ("checks", "string"),
    ("metrics", "string"),
    ("recorded_at", "timestamp"),
)

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)
STARTED = dt.datetime(2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc)
FINISHED = dt.datetime(2026, 11, 2, 10, 2, tzinfo=dt.timezone.utc)

BRONZE_SUMMARY = {
    "version": 1,
    "streams": [
        {
            "source": "indeed",
            "num_input_rows_total": 110,
            "num_input_rows": 999,
            "quarantined_rows": 4,
        },
        {
            "source": "infojobs",
            "num_input_rows_total": 20,
            "quarantined_rows": 1,
        },
        {
            "source": "linkedin",
            "num_input_rows_total": 5,
            "quarantined_rows": 0,
        },
        {
            "source": "multi_site",
            "num_input_rows": 0,
            "quarantined_rows": 2,
        },
    ],
    "config": {"catalog": "job_offers", "trigger": "availableNow"},
}


def _micros(moment):
    """Epoch microseconds of an aware datetime (host-independent assertion)."""
    return int(moment.timestamp() * 1_000_000)


def _full_row(
    run_id="run-0001",
    *,
    status="success",
    checks=None,
    data_date=dt.date(2026, 10, 31),
    **extra,
):
    """A complete row, built by the module under test.

    ``extra`` keys are added after the build, to test forward compatibility
    (``record`` ignores them); every contract column goes through
    ``build_run_row`` so its value has the published type.
    """
    row = metrics.build_run_row(
        run_id=run_id,
        job_id="916863262593632",
        started_at=STARTED,
        finished_at=FINISHED,
        bronze_summary=BRONZE_SUMMARY,
        data_date=data_date,
        status=status,
        checks={"bronze_summary": "ok"} if checks is None else checks,
        now=NOW,
    )
    row.update(extra)
    return row


class _UntouchableSpark:
    """Dummy that fails the test as soon as ``record`` touches it."""

    def __getattr__(self, name):
        raise AssertionError(
            f"record no debe tocar spark.{name} si falta una columna"
        )


class _BoomSpark:
    """Dummy whose catalog fails: the writer must catch it."""

    @property
    def catalog(self):
        raise RuntimeError("catálogo no disponible")


class _RecordingLogger:
    def __init__(self):
        self.messages = []

    def warning(self, message):
        self.messages.append(message)


# ---------------------------------------------------------------------------
# Contract: table name, columns and types
# ---------------------------------------------------------------------------

def test_table_name_uses_the_ops_schema_by_default():
    assert metrics.OPS_SCHEMA == "ops"
    assert metrics.METRICS_TABLE == "run_metrics"
    assert metrics.table_name("job_offers") == "job_offers.ops.run_metrics"
    assert (
        metrics.table_name("job_offers", schema="ops_dev")
        == "job_offers.ops_dev.run_metrics"
    )


def test_columns_and_schema_share_one_order_and_one_type_definition():
    assert metrics.COLUMNS == tuple(name for name, _ in CONTRACT)
    assert [field.name for field in metrics.TABLE_SCHEMA.fields] == list(
        metrics.COLUMNS
    )
    assert [
        (field.name, field.dataType.simpleString())
        for field in metrics.TABLE_SCHEMA.fields
    ] == list(CONTRACT)
    assert all(field.nullable for field in metrics.TABLE_SCHEMA.fields)


# ---------------------------------------------------------------------------
# build_run_row
# ---------------------------------------------------------------------------

def test_build_run_row_returns_the_full_contract_in_column_order():
    checks = {
        "bronze_summary": "ok",
        "freshness": {"data_age_days": 2, "ok": True},
    }

    row = metrics.build_run_row(
        run_id="run-1",
        job_id="916863262593632",
        started_at=STARTED,
        finished_at=FINISHED,
        bronze_summary=BRONZE_SUMMARY,
        data_date="2026-10-31",
        checks=checks,
        now=NOW,
    )

    assert list(row) == list(metrics.COLUMNS)
    assert row == {
        "run_id": "run-1",
        "job_id": "916863262593632",
        "started_at": STARTED,
        "finished_at": FINISHED,
        "duration_s": 120.0,
        "bronze_rows": 135,
        "quarantine_rows": 7,
        "data_date": dt.date(2026, 10, 31),
        "data_age_days": 2,
        "status": "success",
        "checks": (
            '{"bronze_summary":"ok",'
            '"freshness":{"data_age_days":2,"ok":true}}'
        ),
        "metrics": metrics.metrics_json(BRONZE_SUMMARY),
        "recorded_at": NOW,
    }


def test_started_and_finished_are_normalized_to_utc():
    row = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at="2026-11-02T11:00:00+01:00",
        finished_at=dt.datetime(2026, 11, 2, 11, 0),  # naive -> UTC
        now="2026-11-02T12:00:00Z",
    )

    assert row["started_at"] == dt.datetime(
        2026, 11, 2, 10, 0, tzinfo=dt.timezone.utc
    )
    assert row["finished_at"] == dt.datetime(
        2026, 11, 2, 11, 0, tzinfo=dt.timezone.utc
    )
    assert row["duration_s"] == 3600.0
    assert row["recorded_at"] == dt.datetime(
        2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc
    )


def test_duration_is_clamped_and_finished_defaults_to_now():
    backwards = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at="2026-11-02T10:00:00+00:00",
        finished_at="2026-11-02T09:00:00+00:00",
        now="2026-11-02T12:00:00+00:00",
    )
    assert backwards["duration_s"] == 0.0

    default_finished = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at="2026-11-02T10:00:00+00:00",
        now="2026-11-02T12:30:00+00:00",
    )
    assert default_finished["finished_at"] == default_finished["recorded_at"]
    assert default_finished["duration_s"] == 9000.0


def test_bronze_rows_prefers_the_run_total_and_falls_back_to_the_batch():
    summary = {
        "streams": [
            {"num_input_rows_total": 10, "num_input_rows": 99},
            {"num_input_rows": 5},
            {"num_input_rows_total": None, "num_input_rows": 3},
            {},
            {"num_input_rows_total": "7"},
            "unexpected",
        ]
    }

    row = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        bronze_summary=summary,
        now=NOW,
    )

    assert row["bronze_rows"] == 18
    assert row["quarantine_rows"] == 0


def test_bronze_rows_is_none_without_summary_and_zero_without_streams():
    def rows(summary):
        return metrics.build_run_row(
            run_id="run-1",
            job_id="job-1",
            started_at=STARTED,
            bronze_summary=summary,
            now=NOW,
        )

    assert rows(None)["bronze_rows"] is None
    assert rows(None)["quarantine_rows"] is None
    assert rows({})["bronze_rows"] == 0
    assert rows({"streams": []})["bronze_rows"] == 0
    assert rows({"streams": []})["quarantine_rows"] == 0
    assert rows({"streams": "not a list"})["bronze_rows"] == 0


def test_quarantine_rows_sums_the_stream_counters():
    summary = {
        "streams": [
            {"quarantined_rows": 4},
            {"quarantined_rows": 0},
            {},
            {"quarantined_rows": "2"},
            {"quarantined_rows": 3},
        ]
    }

    row = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        bronze_summary=summary,
        now=NOW,
    )

    assert row["quarantine_rows"] == 7


@pytest.mark.parametrize(
    "value",
    [
        dt.date(2026, 10, 31),
        dt.datetime(2026, 10, 31, 23, 30),
        "2026-10-31",
    ],
)
def test_data_date_accepts_date_datetime_and_iso_string(value):
    row = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        data_date=value,
        now=NOW,
    )

    assert row["data_date"] == dt.date(2026, 10, 31)
    assert row["data_age_days"] == 2


def test_data_date_is_optional_and_a_future_date_is_negative():
    missing = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        now=NOW,
    )
    assert missing["data_date"] is None
    assert missing["data_age_days"] is None

    future = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        data_date="2026-11-05",
        now=NOW,
    )
    assert future["data_date"] == dt.date(2026, 11, 5)
    assert future["data_age_days"] == -3


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-10-31", dt.date(2026, 10, 31)),
        (" 2026-10-31 ", dt.date(2026, 10, 31)),
        (dt.date(2026, 10, 31), dt.date(2026, 10, 31)),
        (dt.datetime(2026, 10, 31, 23, 30), dt.date(2026, 10, 31)),
    ],
)
def test_parse_data_date_accepts_the_published_contract(value, expected):
    assert metrics.parse_data_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "None",
        " none ",
        "no-es-fecha",
        "2026-13-01",
        20261031,
        True,
        [],
        {},
    ],
)
def test_parse_data_date_returns_none_instead_of_raising(value):
    assert metrics.parse_data_date(value) is None


def test_build_run_row_without_a_usable_date_does_not_raise():
    for value in ("", "None", "no-es-fecha", None):
        row = metrics.build_run_row(
            run_id="run-1",
            job_id="job-1",
            started_at=STARTED,
            data_date=value,
            now=NOW,
        )

        assert row["data_date"] is None
        assert row["data_age_days"] is None


def test_checks_and_metrics_default_to_an_empty_json_object():
    row = metrics.build_run_row(
        run_id="run-1",
        job_id="job-1",
        started_at=STARTED,
        now=NOW,
    )

    assert row["checks"] == "{}"
    assert row["metrics"] == "{}"
    assert row["status"] == "success"


def test_status_is_published_as_received():
    for status in ("success", "failed", "cancelled"):
        row = metrics.build_run_row(
            run_id="run-1",
            job_id="job-1",
            started_at=STARTED,
            status=status,
            now=NOW,
        )
        assert row["status"] == status


def test_run_id_and_job_id_are_validated_in_spanish():
    for overrides in (
        {"run_id": "", "job_id": "job-1"},
        {"run_id": "   ", "job_id": "job-1"},
        {"run_id": None, "job_id": "job-1"},
        {"run_id": "run-1", "job_id": ""},
        {"run_id": "run-1", "job_id": 123},
    ):
        with pytest.raises(ValueError, match="cadena no vacía"):
            metrics.build_run_row(started_at=STARTED, now=NOW, **overrides)


# ---------------------------------------------------------------------------
# Stable JSON
# ---------------------------------------------------------------------------

def test_json_helpers_are_stable_and_strict():
    first = {"b": [1, 2], "a": {"y": True, "x": None}}
    second = {"a": {"x": None, "y": True}, "b": [1, 2]}

    assert (
        metrics.checks_json(first)
        == '{"a":{"x":null,"y":true},"b":[1,2]}'
    )
    assert metrics.checks_json(first) == metrics.checks_json(second)
    assert metrics.checks_json(None) == "{}"
    assert metrics.metrics_json(None) == "{}"
    assert metrics.metrics_json(BRONZE_SUMMARY) == metrics.metrics_json(
        json.loads(metrics.metrics_json(BRONZE_SUMMARY))
    )


def test_non_finite_figures_are_published_as_null():
    payload = {
        "nan": float("nan"),
        "positive": float("inf"),
        "negative": float("-inf"),
        "nested": [float("nan"), {"x": float("inf")}],
        "ok": 1.5,
    }

    encoded = metrics.checks_json(payload)

    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert json.loads(encoded) == {
        "nan": None,
        "positive": None,
        "negative": None,
        "nested": [None, {"x": None}],
        "ok": 1.5,
    }

    summary = {"streams": [{"num_input_rows_total": float("inf")}]}
    assert json.loads(metrics.metrics_json(summary)) == {
        "streams": [{"num_input_rows_total": None}]
    }


# ---------------------------------------------------------------------------
# merge_sql and the best-effort writer (no Spark needed)
# ---------------------------------------------------------------------------

def test_merge_sql_upserts_by_run_id():
    sql = metrics.merge_sql("job_offers.ops.run_metrics")

    assert metrics.METRICS_VIEW == "__run_metrics"
    assert "MERGE INTO job_offers.ops.run_metrics AS t" in sql
    assert "USING __run_metrics AS s" in sql
    assert "ON t.run_id = s.run_id" in sql
    assert "WHEN MATCHED THEN UPDATE SET *" in sql
    assert "WHEN NOT MATCHED THEN INSERT *" in sql
    assert "USING stage_metrics AS s" in metrics.merge_sql(
        "job_offers.ops.run_metrics", view_name="stage_metrics"
    )


def test_record_fails_before_touching_spark_when_a_column_is_missing():
    row = _full_row()
    del row["checks"]

    result = metrics.record(
        _UntouchableSpark(), "ops.run_metrics", row, logger=_RecordingLogger()
    )

    assert result["status"] == "failed"
    assert result["error"].startswith("Faltan columnas")
    assert "checks" in result["error"]


@pytest.mark.parametrize(
    "column, value",
    [
        ("run_id", None),
        ("run_id", ""),
        ("job_id", None),
        ("job_id", ""),
    ],
)
def test_record_rejects_a_null_or_empty_merge_key_without_touching_spark(
    column, value
):
    row = _full_row()
    row[column] = value

    result = metrics.record(
        _UntouchableSpark(), "ops.run_metrics", row, logger=_RecordingLogger()
    )

    assert result["status"] == "failed"
    assert column in result["error"]


def test_record_catches_a_spark_error_and_logs_it():
    logger = _RecordingLogger()

    result = metrics.record(
        _BoomSpark(), "ops.run_metrics", _full_row(), logger=logger
    )

    assert result["status"] == "failed"
    assert "catálogo no disponible" in result["error"]
    assert logger.messages
    assert "ops.run_metrics" in logger.messages[0]
    assert "catálogo no disponible" in logger.messages[0]


def test_record_prints_the_failure_without_a_logger(capsys):
    result = metrics.record(_BoomSpark(), "ops.run_metrics", _full_row())

    captured = capsys.readouterr().out
    assert result["status"] == "failed"
    assert "[run_metrics]" in captured
    assert "catálogo no disponible" in captured


def test_record_truncates_the_error_message():
    class _LongBoomSpark:
        @property
        def catalog(self):
            raise RuntimeError("x" * 2000)

    result = metrics.record(
        _LongBoomSpark(), "ops.run_metrics", _full_row()
    )

    assert result["status"] == "failed"
    assert len(result["error"]) == 500


# ---------------------------------------------------------------------------
# pending_files: what was waiting in the landing when the run started
# ---------------------------------------------------------------------------

RUN_START_MS = 1_763_000_000_000
COMMIT_MS = RUN_START_MS - 3_600_000
LANDING_ROOT = "abfss://landing@acct.dfs.core.windows.net"


def _landing_file(source, name, modification_ms):
    """Metadata entry of a landing file, as the Hadoop listing builds it."""
    return {
        "source": source,
        "path": f"{LANDING_ROOT}/{source}/{name}",
        "modification_ms": modification_ms,
    }


def test_pending_files_keeps_only_what_is_newer_than_the_commit():
    files = [
        _landing_file("indeed", "already_committed.parquet", COMMIT_MS - 1),
        _landing_file("indeed", "at_commit.parquet", COMMIT_MS),
        _landing_file("indeed", "waiting.parquet", COMMIT_MS + 1),
    ]

    pending = metrics.pending_files(files, {"indeed": COMMIT_MS}, RUN_START_MS)

    waiting = f"{LANDING_ROOT}/indeed/waiting.parquet"
    assert pending == {
        "indeed": {"count": 1, "oldest": waiting, "examples": [waiting]}
    }


def test_pending_files_ignores_what_lands_during_the_run():
    files = [
        _landing_file("indeed", "at_start.parquet", RUN_START_MS),
        _landing_file("indeed", "during_run.parquet", RUN_START_MS + 1),
        _landing_file("indeed", "waiting.parquet", RUN_START_MS - 1),
    ]

    pending = metrics.pending_files(files, {}, RUN_START_MS)

    assert pending["indeed"]["count"] == 1
    assert pending["indeed"]["oldest"].endswith("waiting.parquet")


def test_pending_files_counts_everything_without_a_commit():
    files = [
        _landing_file("infojobs", "a.parquet", 1),
        _landing_file("infojobs", "b.parquet", 2),
    ]

    assert metrics.pending_files(files, {}, RUN_START_MS)[
        "infojobs"
    ]["count"] == 2
    assert metrics.pending_files(files, {"infojobs": None}, RUN_START_MS)[
        "infojobs"
    ]["count"] == 2
    # A source with nothing newer than its commit is not published at all.
    assert metrics.pending_files(files, {"infojobs": 2}, RUN_START_MS) == {}


def test_pending_files_without_files_is_empty():
    assert metrics.pending_files(None, {"indeed": None}, RUN_START_MS) == {}
    assert metrics.pending_files([], {}, RUN_START_MS) == {}
    assert metrics.pending_files([], None, RUN_START_MS) == {}


def test_pending_files_requires_the_start_of_the_run():
    with pytest.raises(ValueError, match="run_start_ms"):
        metrics.pending_files([], {}, None)
    with pytest.raises(ValueError, match="run_start_ms"):
        metrics.pending_files([], {}, "not-a-number")


def test_pending_files_ignores_incomplete_entries():
    files = [
        {"path": "no-source.parquet", "modification_ms": COMMIT_MS + 1},
        {"source": "indeed", "path": "no-mtime.parquet"},
        {"source": "indeed", "path": "bad.parquet", "modification_ms": "x"},
        {"source": "  ", "path": "blank.parquet", "modification_ms": 1},
        "unexpected",
        None,
        _landing_file("indeed", "valid.parquet", COMMIT_MS + 1),
    ]

    pending = metrics.pending_files(files, {"indeed": COMMIT_MS}, RUN_START_MS)

    assert list(pending) == ["indeed"]
    assert pending["indeed"]["count"] == 1
    assert pending["indeed"]["oldest"].endswith("valid.parquet")


def test_pending_files_limits_the_examples_and_keeps_the_oldest():
    files = [
        _landing_file("indeed", f"file-{index}.parquet", COMMIT_MS + index + 1)
        for index in range(5)
    ]
    files.reverse()

    pending = metrics.pending_files(
        files, {"indeed": COMMIT_MS}, RUN_START_MS
    )["indeed"]

    assert pending["count"] == 5
    assert pending["oldest"].endswith("indeed/file-0.parquet")
    assert len(pending["examples"]) == metrics.MAX_EXAMPLES == 3
    assert all(path.endswith(".parquet") for path in pending["examples"])


# ---------------------------------------------------------------------------
# build_checks: the four checks of the run, with native JSON values
# ---------------------------------------------------------------------------

CHECKS_NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)

ZERO_ROWS_SUMMARY = {
    "streams": [
        {"source": source, "num_input_rows_total": 0}
        for source in ("indeed", "infojobs", "linkedin", "multi_site")
    ]
}


def test_build_checks_is_green_with_fresh_data_and_no_backlog():
    checks = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY,
        data_date="2026-10-31",
        pending={},
        now=CHECKS_NOW,
    )

    assert checks == {
        "bronze_summary": {
            "ok": True,
            "sources": ["indeed", "infojobs", "linkedin", "multi_site"],
        },
        "bronze_sources": {"ok": True, "expected": 4, "present": 4},
        "freshness": {
            "ok": True,
            "data_date": "2026-10-31",
            "data_age_days": 2,
            "max_age_days": metrics.MAX_DATA_AGE_DAYS,
        },
        "pending_files": {
            "ok": True,
            "total": 0,
            "by_source": {},
            "bronze_rows": 135,
            "listing_ok": True,
        },
    }


def test_freshness_fails_with_data_older_than_the_threshold():
    checks = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY,
        data_date="2026-10-24",  # nine days before CHECKS_NOW
        now=CHECKS_NOW,
    )

    assert checks["freshness"] == {
        "ok": False,
        "data_date": "2026-10-24",
        "data_age_days": 9,
        "max_age_days": 8,
    }


def test_freshness_accepts_the_threshold_and_rejects_a_future_date():
    same_day = metrics.build_checks(data_date="2026-11-02", now=CHECKS_NOW)
    assert same_day["freshness"]["data_age_days"] == 0
    assert same_day["freshness"]["ok"] is True

    boundary = metrics.build_checks(data_date="2026-10-25", now=CHECKS_NOW)
    assert boundary["freshness"]["data_age_days"] == 8
    assert boundary["freshness"]["ok"] is True

    stricter = metrics.build_checks(
        data_date="2026-10-25", now=CHECKS_NOW, max_age_days=7
    )
    assert stricter["freshness"]["max_age_days"] == 7
    assert stricter["freshness"]["ok"] is False

    future = metrics.build_checks(data_date="2026-11-05", now=CHECKS_NOW)
    assert future["freshness"]["data_age_days"] == -3
    assert future["freshness"]["ok"] is False


def test_freshness_fails_without_a_usable_data_date():
    checks = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY, now=CHECKS_NOW
    )
    assert checks["freshness"] == {
        "ok": False,
        "data_date": None,
        "data_age_days": None,
        "max_age_days": 8,
    }

    malformed = metrics.build_checks(data_date="not-a-date", now=CHECKS_NOW)
    assert malformed["freshness"]["ok"] is False
    assert malformed["freshness"]["data_date"] is None


def test_build_checks_without_summary_flags_the_bronze_and_zero_rows():
    checks = metrics.build_checks(
        data_date="2026-10-31", pending={}, now=CHECKS_NOW
    )

    assert checks["bronze_summary"] == {"ok": False, "sources": []}
    assert checks["bronze_sources"] == {
        "ok": False,
        "expected": 4,
        "present": 0,
    }
    assert checks["pending_files"]["bronze_rows"] == 0

    empty = metrics.build_checks(
        bronze_summary={"streams": []}, now=CHECKS_NOW
    )
    assert empty["bronze_summary"]["ok"] is False
    assert empty["pending_files"]["bronze_rows"] == 0


def test_bronze_sources_fails_when_one_source_is_missing():
    summary = {
        "streams": [
            {"source": "indeed"},
            {"source": "infojobs"},
            {"source": "linkedin"},
        ]
    }

    checks = metrics.build_checks(bronze_summary=summary, now=CHECKS_NOW)

    assert checks["bronze_summary"]["ok"] is True
    assert checks["bronze_sources"] == {
        "ok": False,
        "expected": 4,
        "present": 3,
    }


def test_pending_files_fail_only_when_the_batch_was_empty():
    pending = {
        "indeed": {
            "count": 2,
            "oldest": f"{LANDING_ROOT}/indeed/a.parquet",
            "examples": [f"{LANDING_ROOT}/indeed/a.parquet"],
        },
        "infojobs": {
            "count": 1,
            "oldest": f"{LANDING_ROOT}/infojobs/b.parquet",
            "examples": [],
        },
    }

    stopped = metrics.build_checks(
        bronze_summary=ZERO_ROWS_SUMMARY, pending=pending, now=CHECKS_NOW
    )
    assert stopped["pending_files"] == {
        "ok": False,
        "total": 3,
        "by_source": pending,
        "bronze_rows": 0,
        "listing_ok": True,
    }

    moved = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY, pending=pending, now=CHECKS_NOW
    )
    assert moved["pending_files"]["ok"] is True
    assert moved["pending_files"]["total"] == 3
    assert moved["pending_files"]["bronze_rows"] == 135


def test_pending_files_fail_when_the_landing_could_not_be_listed():
    checks = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY,
        data_date="2026-10-31",
        pending={},
        listing_ok=False,
        now=CHECKS_NOW,
    )

    assert checks["pending_files"] == {
        "ok": False,
        "total": 0,
        "by_source": {},
        "bronze_rows": 135,
        "listing_ok": False,
    }

    # Only an explicit True keeps the check green (None or a truthy value do
    # not "nothing pending" when the listing did not run).
    for value in (None, 1, "ok"):
        unchecked = metrics.build_checks(
            bronze_summary=BRONZE_SUMMARY, listing_ok=value, now=CHECKS_NOW
        )
        assert unchecked["pending_files"]["ok"] is False
        assert unchecked["pending_files"]["listing_ok"] is False


def test_checks_are_serializable_without_a_json_default():
    checks = metrics.build_checks(
        bronze_summary=BRONZE_SUMMARY,
        data_date=dt.date(2026, 10, 31),
        pending={
            "indeed": {
                "count": 1,
                "oldest": f"{LANDING_ROOT}/indeed/a.parquet",
                "examples": [f"{LANDING_ROOT}/indeed/a.parquet"],
            }
        },
        now=CHECKS_NOW,
    )

    encoded = json.dumps(checks, sort_keys=True)  # no default= on purpose
    assert json.loads(encoded) == checks
    assert json.loads(metrics.checks_json(checks)) == checks
    assert "NaN" not in encoded
    assert "Infinity" not in encoded


# ---------------------------------------------------------------------------
# failed_checks: what the closing task turns into a red run
# ---------------------------------------------------------------------------

def test_failed_checks_lists_the_false_ones_in_order():
    checks = {
        "bronze_summary": {"ok": True},
        "bronze_sources": {"ok": False},
        "freshness": {"ok": False},
        "pending_files": {"ok": True},
    }

    assert metrics.failed_checks(checks) == ["bronze_sources", "freshness"]
    assert metrics.failed_checks(None) == []
    assert metrics.failed_checks({}) == []


def test_failed_checks_requires_an_explicit_true():
    checks = {
        "a": {},
        "b": "ok",
        "c": {"ok": 1},
        "d": {"ok": False},
        "e": {"ok": True},
    }

    assert metrics.failed_checks(checks) == ["a", "b", "c", "d"]


# ---------------------------------------------------------------------------
# Closing notebook: coordination only (spec 011, T-05)
# ---------------------------------------------------------------------------

RUN_SUMMARY_PATH = os.path.join(DATABRICKS_DIR, "run_summary.ipynb")


def _notebook_cells():
    with open(RUN_SUMMARY_PATH, encoding="utf-8") as handle:
        notebook = json.load(handle)
    return ["".join(cell["source"]) for cell in notebook["cells"]]


def _notebook_code():
    return "\n".join(_notebook_cells())


def test_run_summary_notebook_exists_and_imports_the_module():
    assert os.path.exists(RUN_SUMMARY_PATH)
    code = _notebook_code()

    assert "import json" in code
    assert "import Run_Metrics" in code
    assert "Run_Metrics.table_name(CATALOG)" in code


def test_run_summary_requires_the_identity_and_the_storage_of_the_run():
    code = _notebook_code()

    assert 'dbutils.widgets.text("catalog", "job_offers")' in code
    assert 'dbutils.widgets.text("storage_account", "")' in code
    assert 'dbutils.widgets.text("container", "landing")' in code
    assert 'dbutils.widgets.text("export_prefix", "gold/web_export")' in code
    for name in ("run_id", "job_id", "started_at"):
        assert f'dbutils.widgets.text("{name}", "")' in code
    assert "raise ValueError(" in code
    assert "Faltan parametros obligatorios" in code


def test_run_summary_reads_the_bronze_task_value_and_meta_json():
    code = _notebook_code()

    assert "dbutils.jobs.taskValues.get(" in code
    # The API needs the task that published the value, not only its key.
    assert '"Bronze_Ingestion", "bronze_summary"' in code
    assert "json.loads(raw_summary)" in code
    assert 'f"{STORAGE_BASE}/{EXPORT_PREFIX}/meta.json"' in code
    assert "dbutils.fs.head(META_PATH, 65536)" in code
    assert 'raw_data_date = meta.get("data_date")' in code
    # A corrupt date is null (the freshness check fails); no private helper.
    assert "data_date = Run_Metrics.parse_data_date(raw_data_date)" in code
    assert "_as_date" not in code


def test_run_summary_lists_files_and_commits_without_reading_data():
    code = _notebook_code()

    assert "listFiles(root, True)" in code
    assert "getModificationTime()" in code
    assert "dbutils.fs.ls(path)" in code
    assert "/ckpt/commits" in code
    # A failed listing is never "nothing pending".
    assert "source_files, source_ok = _landing_files(source)" in code
    assert "listing_ok = listing_ok and source_ok" in code
    assert "return found, False" in code
    assert "return found, True" in code
    # Metadata listing only: no table read and no Parquet read.
    assert "spark.read" not in code
    assert "spark.table(" not in code


def test_run_summary_delegates_the_rules_to_the_module():
    code = _notebook_code()

    assert "Run_Metrics.pending_files(files, commits, run_start_ms)" in code
    assert "Run_Metrics.build_checks(" in code
    assert "listing_ok=listing_ok" in code
    assert "Run_Metrics.failed_checks(checks)" in code
    assert "Run_Metrics.build_run_row(" in code
    assert "Run_Metrics.record(" in code
    assert "logger=_PrintLogger()" in code
    # The threshold is the module default, never a number of the notebook.
    assert "max_age_days" not in code


def test_run_summary_fails_after_writing_and_exits_only_when_green():
    code = _notebook_code()

    write = code.index("Run_Metrics.record(")
    failure = code.index("raise RuntimeError")
    exit_call = code.index("dbutils.notebook.exit(")

    # A failed business task must still leave its row: write, then fail.
    assert write < failure
    # notebook.exit stops the notebook, so it goes after the failure.
    assert failure < exit_call
    assert '"Comprobaciones del run en falso: "' in code
    assert 'problems.append("record")' in code


# ---------------------------------------------------------------------------
# Integration: Delta table, MERGE by run_id
# ---------------------------------------------------------------------------

DELTA_EXTENSION = "io.delta.sql.DeltaSparkSessionExtension"
DELTA_CATALOG = "org.apache.spark.sql.delta.catalog.DeltaCatalog"


@pytest.fixture(scope="module")
def spark():
    """Local ``local[2]`` session with Delta, or a skip with instructions.

    ``configure_spark_with_delta_pip`` adds the jars but does **not** set the
    extension nor the catalog key (measured on this host), so both are passed
    to the builder. ``getOrCreate`` may return a session created by another
    test module, where the builder configs no longer apply: if such session
    has no Delta extension the integration tests skip instead of failing.
    """
    pytest.importorskip(
        "delta",
        reason=(
            "delta-spark no está instalado: los tests de integración de "
            "ops.run_metrics necesitan Delta."
        ),
    )
    from delta import configure_spark_with_delta_pip

    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="run-metrics-warehouse-")
    active = SparkSession.getActiveSession()
    builder = (
        SparkSession.builder.master("local[2]")
        .appName("run-metrics-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .config("spark.sql.extensions", DELTA_EXTENSION)
        .config("spark.sql.catalog.spark_catalog", DELTA_CATALOG)
    )
    try:
        session = configure_spark_with_delta_pip(builder).getOrCreate()
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        native_markers = (
            "HADOOP_HOME",
            "hadoop.home.dir",
            "winutils",
            "hadoop.dll",
            "Hadoop bin directory does not exist",
            "NativeIO$Windows",
            "UnsatisfiedLinkError",
        )
        if any(marker in detail for marker in native_markers):
            pytest.skip(
                "Spark no puede arrancar sin Hadoop nativo en Windows: "
                "instala winutils.exe y hadoop.dll en "
                "%LOCALAPPDATA%\\hadoop\\bin y define HADOOP_HOME (la "
                "carpeta que contiene bin\\). En Linux/CI no hace falta. "
                f"Detalle: {detail[-400:]}"
            )
        raise
    extensions = session.conf.get("spark.sql.extensions", "")
    catalog = session.conf.get("spark.sql.catalog.spark_catalog", "")
    if DELTA_EXTENSION not in extensions or DELTA_CATALOG not in catalog:
        pytest.skip(
            "La SparkSession existente no tiene Delta configurado (suite "
            "compartida). Ejecuta este fichero en aislamiento para probar el "
            "MERGE: python -m pytest "
            "databricks_notebooks/tests/test_run_metrics.py -q"
        )
    # A host without winutils.exe/hadoop.dll cannot write with Hadoop either:
    # skip the integration tests with instructions instead of failing on the
    # first write.
    require_writable_local_fs(session)
    session.sql("CREATE SCHEMA IF NOT EXISTS ops")
    # Warm-up: start a Python worker and retry the known transient handshake
    # failure on Windows/local.
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 2).map(
                lambda value: value
            ).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    try:
        yield session
    finally:
        # Drop only the tables this module creates. Dropping `ops.run_metrics`
        # itself (or the `ops` schema) would delete artifacts the tests never
        # created when the session points at a real catalog.
        for table in (
            "ops.run_metrics_created",
            "ops.run_metrics_idempotent",
            "ops.run_metrics_two_runs",
        ):
            session.sql(f"DROP TABLE IF EXISTS {table}")
        if active is None:
            session.stop()


def test_record_creates_the_table_and_writes_the_full_row(spark):
    table = "ops.run_metrics_created"
    assert spark.catalog.tableExists(table) is False
    # Extra keys are ignored (forward compatibility).
    row = _full_row(run_id="run-created", future_column="ignored")

    result = metrics.record(spark, table, row)

    assert result == {"status": "ok", "error": None}
    assert spark.catalog.tableExists(table) is True
    assert metrics.ensure_table(spark, table) is False

    stored = spark.table(table).collect()
    assert len(stored) == 1
    stored = stored[0]
    # PySpark renders TIMESTAMP values with the host zone on this runtime, so
    # the instants are compared as epoch microseconds, not as wall clock.
    instants = spark.sql(
        "SELECT unix_micros(started_at) AS started, "
        "unix_micros(finished_at) AS finished, "
        "unix_micros(recorded_at) AS recorded "
        f"FROM {table}"
    ).collect()[0]
    assert stored["run_id"] == "run-created"
    assert stored["job_id"] == "916863262593632"
    assert instants["started"] == _micros(STARTED)
    assert instants["finished"] == _micros(FINISHED)
    assert stored["duration_s"] == pytest.approx(120.0)
    assert stored["bronze_rows"] == 135
    assert stored["quarantine_rows"] == 7
    assert stored["data_date"] == dt.date(2026, 10, 31)
    assert stored["data_age_days"] == 2
    assert stored["status"] == "success"
    assert stored["checks"] == '{"bronze_summary":"ok"}'
    assert json.loads(stored["metrics"]) == BRONZE_SUMMARY
    assert instants["recorded"] == _micros(NOW)


def test_record_is_idempotent_per_run_id_and_the_second_write_wins(spark):
    table = "ops.run_metrics_idempotent"
    first = _full_row(run_id="run-repeat", checks={"attempt": "first"})
    second = _full_row(
        run_id="run-repeat",
        status="failed",
        checks={"attempt": "second"},
        data_date=dt.date(2026, 11, 1),
    )

    assert metrics.record(spark, table, first)["status"] == "ok"
    assert metrics.record(spark, table, second)["status"] == "ok"

    stored = spark.table(table).collect()
    assert len(stored) == 1
    assert stored[0]["status"] == "failed"
    assert json.loads(stored[0]["checks"]) == {"attempt": "second"}
    assert stored[0]["data_date"] == dt.date(2026, 11, 1)


def test_two_run_ids_keep_two_rows(spark):
    table = "ops.run_metrics_two_runs"

    assert metrics.record(spark, table, _full_row(run_id="run-a"))[
        "status"
    ] == "ok"
    assert metrics.record(
        spark, table, _full_row(run_id="run-b", checks={"other": True})
    )["status"] == "ok"

    stored = spark.table(table).collect()
    assert sorted(row["run_id"] for row in stored) == ["run-a", "run-b"]


def test_record_does_not_raise_when_the_table_is_unreachable(spark):
    result = metrics.record(
        spark, "missing_schema.run_metrics", _full_row(run_id="run-lost")
    )

    assert result["status"] == "failed"
    assert isinstance(result["error"], str)
    assert result["error"]
