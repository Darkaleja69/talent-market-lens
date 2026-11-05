"""Tests for the bronze streaming summary (spec 011, T-03).

Pure tests: no Spark session, no Delta and no I/O beyond reading the module
and the notebook as text. They pin the published contract (field names, the
``null`` values the engine does not expose, the "no microbatch" note), the
ordering and duplicate rule of :func:`Bronze_Summary.merge_summaries`, the
byte-stable JSON and the two cells the notebook wires to this module.
"""
import copy
import json
import os
import pathlib
import sys

import pytest

DATABRICKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, DATABRICKS_DIR)

import Bronze_Summary as summary_module  # noqa: E402

NOTEBOOK_PATH = pathlib.Path(DATABRICKS_DIR) / "bronze_ingest.ipynb"


# ---------------------------------------------------------------------------
# Synthetic engine progress: the shape of query.lastProgress after
# trigger(availableNow=True) on a file source (fields trimmed to the ones the
# contract reads, plus a few that must be ignored).
# ---------------------------------------------------------------------------

LAST_PROGRESS = {
    "id": "query-indeed",
    "runId": "run-1",
    "batchId": 7,
    "numInputRows": 120,
    "inputRowsPerSecond": 40.0,
    "processedRowsPerSecond": 25.5,
    "durationMs": {
        "triggerExecution": 4830,
        "addBatch": 4200,
        "walCommit": 25,
        "walFetch": 3,
    },
    "stateOperators": [
        {
            "operatorName": "stateStoreSave",
            "numRowsTotal": 12,
            "numRowsUpdated": 3,
            "allUpdatesTimeMs": 9,
        }
    ],
    "sources": [
        {
            "description": "abfss://landing@acct.dfs.core.windows.net/indeed/",
            "metrics": {
                "numFilesOutstanding": 5,
                "numBytesOutstanding": 1024,
            },
        }
    ],
}

RECENT_PROGRESS = [
    {"batchId": 5, "numInputRows": 40},
    {"batchId": 6, "numInputRows": 0},
    {"batchId": 7, "numInputRows": 120},
]

CHECKPOINT = (
    "abfss://landing@acct.dfs.core.windows.net/_checkpoints/indeed/ckpt"
)


def test_full_progress_publishes_the_exact_summary():
    result = summary_module.build_stream_summary(
        "indeed",
        last_progress=LAST_PROGRESS,
        recent_progress=RECENT_PROGRESS,
        checkpoint=CHECKPOINT,
        quarantine_rows=4,
        status="Stopped",
    )

    assert result == {
        "source": "indeed",
        "batch_id": 7,
        "num_input_rows": 120,
        "num_input_rows_total": 160,
        "input_rows_per_second": 40.0,
        "processed_rows_per_second": 25.5,
        "duration_ms": {
            "trigger_execution": 4830,
            "add_batch": 4200,
            "wal_commit": 25,
        },
        "state_operators": [
            {
                "operatorName": "stateStoreSave",
                "numRowsTotal": 12,
                "numRowsUpdated": 3,
                "allUpdatesTimeMs": 9,
            }
        ],
        "backlog": {"files_outstanding": 5, "bytes_outstanding": 1024},
        "checkpoint": CHECKPOINT,
        "quarantined_rows": 4,
        "status": "Stopped",
    }
    # The caller's progress is never mutated.
    assert LAST_PROGRESS["stateOperators"][0]["numRowsTotal"] == 12
    assert "note" not in result


def test_no_progress_publishes_explicit_nulls_and_zero_quarantine():
    result = summary_module.build_stream_summary("linkedin")

    assert result == {
        "source": "linkedin",
        "batch_id": None,
        "num_input_rows": None,
        "num_input_rows_total": None,
        "input_rows_per_second": None,
        "processed_rows_per_second": None,
        "duration_ms": {
            "trigger_execution": None,
            "add_batch": None,
            "wal_commit": None,
        },
        "state_operators": [],
        "backlog": None,
        "checkpoint": None,
        "quarantined_rows": 0,
        "status": None,
        "note": "no microbatch reported by the query",
    }
    payload = summary_module.to_json(result)
    assert '"batch_id":null' in payload
    assert '"num_input_rows":null' in payload
    assert '"num_input_rows_total":null' in payload
    assert '"quarantined_rows":0' in payload
    assert '"state_operators":[]' in payload


def test_empty_batch_is_zero_not_missing():
    progress = {"batchId": 3, "numInputRows": 0, "durationMs": {}}

    result = summary_module.build_stream_summary(
        "infojobs",
        last_progress=progress,
        recent_progress=[{"batchId": 3, "numInputRows": 0}],
    )

    assert result["batch_id"] == 3
    assert result["num_input_rows"] == 0
    assert result["num_input_rows_total"] == 0
    assert result["num_input_rows"] is not None
    assert result["num_input_rows_total"] is not None


def test_quarantine_rows_default_and_explicit():
    assert (
        summary_module.build_stream_summary("indeed")["quarantined_rows"] == 0
    )
    assert (
        summary_module.build_stream_summary("indeed", quarantine_rows=None)[
            "quarantined_rows"
        ]
        == 0
    )
    assert (
        summary_module.build_stream_summary("indeed", quarantine_rows=0)[
            "quarantined_rows"
        ]
        == 0
    )
    assert (
        summary_module.build_stream_summary("indeed", quarantine_rows=17)[
            "quarantined_rows"
        ]
        == 17
    )
    # An unexpected type never raises and never invents a figure.
    assert (
        summary_module.build_stream_summary("indeed", quarantine_rows="17")[
            "quarantined_rows"
        ]
        == 0
    )


def test_quarantine_total_is_idempotent_per_microbatch():
    counts = {}
    # The first run of batch 7 and a retry of the same batch are one entry.
    counts[("indeed", 7)] = 5
    counts[("indeed", 7)] = 5
    assert summary_module.quarantine_total(counts, "indeed") == 5
    # Two different microbatches of the same source add up.
    counts[("indeed", 8)] = 3
    assert summary_module.quarantine_total(counts, "indeed") == 8
    # Another source never leaks into this source's figure.
    counts[("linkedin", 7)] = 100
    assert summary_module.quarantine_total(counts, "indeed") == 8
    assert summary_module.quarantine_total(counts, "linkedin") == 100
    # No entry at all (or no mapping) is an empty total.
    assert summary_module.quarantine_total({}, "indeed") == 0
    assert summary_module.quarantine_total(None, "indeed") == 0


def test_quarantine_total_ignores_unexpected_keys_and_values():
    weird = {
        "indeed": 4,  # not a tuple
        ("indeed",): 4,  # no batch id
        ("indeed", 1, 2): 4,  # too many parts
        ("indeed", 1): "4",  # not a number
        ("indeed", 2): None,  # not a number
        ("infojobs", 1): 1,  # another source
    }

    assert summary_module.quarantine_total(weird, "indeed") == 0
    assert summary_module.quarantine_total(weird, "infojobs") == 1


def test_partial_duration_keeps_the_three_keys_with_none():
    result = summary_module.build_stream_summary(
        "multi_site",
        last_progress={"batchId": 1, "durationMs": {"triggerExecution": 900}},
    )

    assert result["duration_ms"] == {
        "trigger_execution": 900,
        "add_batch": None,
        "wal_commit": None,
    }
    assert list(result["duration_ms"]) == [
        "trigger_execution",
        "add_batch",
        "wal_commit",
    ]
    # No durationMs at all: same three keys, all None.
    assert summary_module.build_stream_summary("multi_site")[
        "duration_ms"
    ] == {
        "trigger_execution": None,
        "add_batch": None,
        "wal_commit": None,
    }


def test_total_input_rows_sums_every_microbatch():
    progress = {"batchId": 2, "numInputRows": 5}

    assert (
        summary_module.build_stream_summary(
            "indeed",
            last_progress=progress,
            recent_progress=[{"numInputRows": 1}, {"numInputRows": 4}],
        )["num_input_rows_total"]
        == 5
    )
    # An entry without the key contributes nothing (it does not fail).
    assert (
        summary_module.build_stream_summary(
            "indeed",
            last_progress=progress,
            recent_progress=[{"batchId": 1}, {"numInputRows": 4}],
        )["num_input_rows_total"]
        == 4
    )
    # A real empty list of microbatches is 0; a missing report is None.
    assert (
        summary_module.build_stream_summary(
            "indeed", last_progress=progress, recent_progress=[]
        )["num_input_rows_total"]
        == 0
    )
    assert (
        summary_module.build_stream_summary(
            "indeed", last_progress=progress, recent_progress=None
        )["num_input_rows_total"]
        is None
    )


def test_backlog_is_published_only_when_the_engine_reports_it():
    two_sources = {
        "batchId": 1,
        "sources": [
            {
                "metrics": {
                    "numFilesOutstanding": 2,
                    "numBytesOutstanding": 100,
                }
            },
            {
                "metrics": {
                    "numFilesOutstanding": 3,
                    "numBytesOutstanding": 50,
                }
            },
        ],
    }
    assert summary_module.build_stream_summary(
        "indeed", last_progress=two_sources
    )["backlog"] == {"files_outstanding": 5, "bytes_outstanding": 150}

    # Files without bytes: the missing figure counts as 0.
    assert summary_module.build_stream_summary(
        "indeed",
        last_progress={"sources": [{"metrics": {"numFilesOutstanding": 1}}]},
    )["backlog"] == {"files_outstanding": 1, "bytes_outstanding": 0}

    # No metrics (or metrics without the backlog keys): explicit null.
    assert (
        summary_module.build_stream_summary(
            "indeed", last_progress={"sources": [{"description": "x"}]}
        )["backlog"]
        is None
    )
    assert (
        summary_module.build_stream_summary(
            "indeed",
            last_progress={"sources": [{"metrics": {"numInputRows": 3}}]},
        )["backlog"]
        is None
    )
    assert summary_module.build_stream_summary("indeed")["backlog"] is None


def test_merge_summaries_orders_copies_and_rejects_duplicates():
    sources = ["multi_site", "indeed", "infojobs", "linkedin"]
    streams = [
        summary_module.build_stream_summary(name, quarantine_rows=index)
        for index, name in enumerate(sources)
    ]
    before = copy.deepcopy(streams)
    config = {"catalog": "job_offers", "trigger": "availableNow"}

    merged = summary_module.merge_summaries(streams, config=config)

    assert merged["version"] == 1
    assert summary_module.SUMMARY_VERSION == 1
    assert [item["source"] for item in merged["streams"]] == [
        "indeed",
        "infojobs",
        "linkedin",
        "multi_site",
    ]
    assert merged["config"] == config
    assert merged["config"] is not config
    assert streams == before

    with pytest.raises(ValueError, match="duplicada"):
        summary_module.merge_summaries(
            [
                summary_module.build_stream_summary("indeed"),
                summary_module.build_stream_summary("indeed"),
            ]
        )

    assert summary_module.merge_summaries([]) == {
        "version": 1,
        "streams": [],
        "config": {},
    }


def test_to_json_is_stable_and_parses_back():
    built = summary_module.build_stream_summary(
        "indeed",
        last_progress=LAST_PROGRESS,
        recent_progress=RECENT_PROGRESS,
        checkpoint=CHECKPOINT,
        quarantine_rows=4,
        status="Stopped",
    )
    payload = summary_module.to_json(built)

    assert payload == summary_module.to_json(built)
    assert " " not in payload
    parsed = json.loads(payload)
    assert parsed["num_input_rows"] == 120
    assert parsed["num_input_rows_total"] == 160
    assert parsed["processed_rows_per_second"] == 25.5
    assert parsed["duration_ms"]["trigger_execution"] == 4830
    # Sorted keys: the payload does not depend on insertion order.
    assert summary_module.to_json({"b": 1, "a": [2.5, None]}) == (
        '{"a":[2.5,null],"b":1}'
    )


def test_non_finite_rates_are_published_as_null():
    progress = {
        "batchId": 1,
        "numInputRows": 10,
        "inputRowsPerSecond": float("inf"),
        "processedRowsPerSecond": float("nan"),
    }

    result = summary_module.build_stream_summary(
        "indeed", last_progress=progress
    )

    assert result["input_rows_per_second"] is None
    assert result["processed_rows_per_second"] is None

    payload = summary_module.to_json(result)
    assert "Infinity" not in payload
    assert "NaN" not in payload
    parsed = json.loads(payload)
    assert parsed["input_rows_per_second"] is None
    assert parsed["processed_rows_per_second"] is None

    # A caller-built payload is sanitized too: strict JSON or nothing.
    hand_built = {"a": [float("inf"), {"b": float("-inf")}], "c": 1.5}
    strict = summary_module.to_json(hand_built)
    assert strict == '{"a":[null,{"b":null}],"c":1.5}'
    assert json.loads(strict) == {"a": [None, {"b": None}], "c": 1.5}


def test_module_is_pure_and_does_not_touch_the_batch():
    source = pathlib.Path(summary_module.__file__).read_text(encoding="utf-8")

    assert "pyspark" not in source
    assert "count(" not in source
    assert "collect(" not in source
    assert "readStream" not in source
    assert "dbutils" not in source
    assert "__main__" not in source


def test_state_operators_are_copied_from_last_progress():
    progress = copy.deepcopy(LAST_PROGRESS)

    result = summary_module.build_stream_summary(
        "indeed", last_progress=progress
    )

    assert result["state_operators"][0]["numRowsTotal"] == 12
    assert result["state_operators"][0] is not progress["stateOperators"][0]
    result["state_operators"][0]["numRowsTotal"] = 999
    assert progress["stateOperators"][0]["numRowsTotal"] == 12


def test_source_is_the_only_validated_argument():
    for invalid in ("", "   ", None, 5):
        with pytest.raises(ValueError, match="source"):
            summary_module.build_stream_summary(invalid)

    # Unexpected types in the rest of the arguments never raise.
    result = summary_module.build_stream_summary(
        "indeed",
        last_progress="not a dict",
        recent_progress=5,
        checkpoint=3,
        quarantine_rows="abc",
        status=b"Stopped",
    )
    assert result["batch_id"] is None
    assert result["num_input_rows"] is None
    assert result["num_input_rows_total"] is None
    assert result["checkpoint"] is None
    assert result["quarantined_rows"] == 0
    assert result["status"] is None


# ---------------------------------------------------------------------------
# Notebook wiring: the summary is built from the engine progress of the four
# queries already started and published as a JSON string, without adding any
# landing read or batch-wide action (spec 011, T-03).
# ---------------------------------------------------------------------------

def _notebook_code():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    return "\n".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "code"
    )


def test_notebook_publishes_the_merged_json_per_source():
    code = _notebook_code()

    assert "import Bronze_Summary" in code
    assert 'SOURCES = ("indeed", "linkedin", "infojobs", "multi_site")' in code
    assert "QUARANTINE_COUNTS = {}" in code
    assert "Bronze_Summary.build_stream_summary(" in code
    assert "Bronze_Summary.merge_summaries(" in code
    assert "summary_json = Bronze_Summary.to_json(summary)" in code
    assert (
        'dbutils.jobs.taskValues.set(key="bronze_summary", value=summary_json)'
        in code
    )
    assert "dbutils.notebook.exit(summary_json)" in code


def test_notebook_keys_quarantine_counts_by_source_and_batch():
    code = _notebook_code()

    assert "QUARANTINE_COUNTS[(source, batch_id)] = new_count" in code
    assert "Bronze_Summary.quarantine_total(" in code
    assert "QUARANTINE_COUNTS.get(source, 0)" not in code


def test_notebook_refuses_a_sources_queries_mismatch():
    code = _notebook_code()

    assert "if len(QUERIES) != len(SOURCES):" in code
    assert "Desajuste entre SOURCES" in code
    # The guard runs before the summaries are paired with the queries.
    guard = code.index("if len(QUERIES) != len(SOURCES):")
    pairing = code.index("for source, query in zip(SOURCES, QUERIES)")
    assert guard < pairing


def test_notebook_keeps_the_current_streams_and_actions():
    code = _notebook_code()

    # Same four streams, same order, same checkpoint layout as before T-03.
    assert code.count("QUERIES.append(") == 4
    assert code.count('readStream.format("cloudFiles")') == 4
    assert code.count('.option("checkpointLocation"') == 4
    # The only action added is the row count of the small quarantine frame.
    assert code.count("count(") == 1
    assert "new_rows.count()" in code
    assert "batch_df.count()" not in code
