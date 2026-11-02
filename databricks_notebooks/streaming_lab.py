"""Local laboratory for Structured Streaming semantics (spec 011, T-02).

The module is didactic: no notebook, job or bundle entry imports it. Each
scenario runs on a local ``SparkSession`` and writes only under the
``base_dir`` it receives, so the tests can use temporary directories. The
returned dictionaries are JSON serializable and contain the numbers actually
observed, not expected values.

Scenarios (same order as ``plan.md`` section 5):

1. ``checkpoint_restart``: a checkpoint makes a restarted query continue where
   it stopped instead of reprocessing the source.
2. ``microbatch_triggers``: ``availableNow``, ``once`` and ``processingTime``
   build different numbers of microbatches.
3. ``watermark_late_event``: an event older than the restored watermark is
   dropped and no window before the watermark is emitted.
4. ``exactly_once``: the checkpoint prevents reprocessing; deleting it makes
   an append sink duplicate; an idempotent handler keeps one row per key.
5. ``foreach_batch_upsert``: ``append`` duplicates a reprocessed microbatch,
   while an upsert by natural key keeps the latest capture.

Measured notes (Spark 4.2.0, local[2], Hadoop 3.5.0):

* ``trigger(once=True)`` processes exactly one microbatch and terminates. In
  this runtime it reads *all* files available at launch in that batch, even
  with ``maxFilesPerTrigger=1``; files published after the run stay pending
  until the next ``once`` run.
* With ``withWatermark(..., "10 minutes")`` and a 10-minute window, append
  output mode emits nothing on the first run because no window end has passed
  the watermark. The watermark scenario therefore uses ``update`` output mode
  and appends each changed window to Parquet through ``foreachBatch``; that is
  the smallest change that makes the requested windows observable.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from pyspark.sql import Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

SCENARIO_NAMES = (
    "checkpoint_restart",
    "microbatch_triggers",
    "watermark_late_event",
    "exactly_once",
    "foreach_batch_upsert",
)

JOB_SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("captured_at", T.TimestampType(), True),
])

EVENT_SCHEMA = T.StructType([
    T.StructField("event_id", T.StringType(), True),
    T.StructField("event_time", T.TimestampType(), True),
])


# ---------------------------------------------------------------------------
# Small helpers shared by the scenarios
# ---------------------------------------------------------------------------

def _scenario_dir(base_dir, name):
    """Return a clean scenario directory below ``base_dir``."""
    path = Path(base_dir) / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path, rows):
    """Write newline-delimited JSON rows atomically (one row per line).

    The file is written to a hidden temporary in the same directory and then
    renamed, so ``FileStreamSource`` never lists a half-written file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parquet_count(spark, path):
    """Count rows in a Parquet directory, tolerating an empty sink."""
    path = Path(path)
    if not path.exists() or not any(path.rglob("*.parquet")):
        return 0
    return spark.read.parquet(str(path)).count()


def _run_file_sink(df, sink, checkpoint, trigger):
    """Run an append file sink with the given trigger and wait for it."""
    query = (
        df.writeStream
        .format("parquet")
        .outputMode("append")
        .option("path", str(sink))
        .option("checkpointLocation", str(checkpoint))
        .trigger(**trigger)
        .start()
    )
    query.awaitTermination()
    return query


def _progress(query):
    return list(query.recentProgress or [])


def _non_empty_progress(query):
    return [
        progress for progress in _progress(query)
        if progress.get("numInputRows", 0) > 0
    ]


def _input_rows(query):
    return sum(progress.get("numInputRows", 0) for progress in _progress(query))


def _batch_count(query):
    return len(_non_empty_progress(query))


def _wait_until(predicate, timeout_s, interval_s=0.05):
    """Poll ``predicate`` until it is true or ``timeout_s`` expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def _upsert_parquet(batch_df, sink, key, keep_latest):
    """Upsert a microbatch into a Parquet directory by a natural key.

    The existing sink is read with the batch's session, the union is deduped
    by ``key`` (optionally keeping the newest ``captured_at``) and the result
    is written to a staging directory before replacing the sink. The staging
    step keeps the read and the overwrite from racing on the same path.
    """
    sink_path = Path(sink)
    existing = None
    if sink_path.exists() and any(sink_path.rglob("*.parquet")):
        existing = batch_df.sparkSession.read.parquet(str(sink_path))

    combined = batch_df if existing is None else existing.unionByName(batch_df)

    if keep_latest:
        latest_first = Window.partitionBy(key).orderBy(
            F.col("captured_at").desc_nulls_last()
        )
        deduped = (
            combined
            .withColumn("_row_number", F.row_number().over(latest_first))
            .filter("_row_number = 1")
            .drop("_row_number")
        )
    else:
        deduped = combined.dropDuplicates([key])

    staging = sink_path.parent / f".{sink_path.name}-staging-{uuid.uuid4().hex}"
    deduped.write.mode("overwrite").parquet(str(staging))
    if sink_path.exists():
        shutil.rmtree(sink_path)
    os.replace(str(staging), str(sink_path))


# ---------------------------------------------------------------------------
# 1. Checkpoint and restart
# ---------------------------------------------------------------------------

def checkpoint_restart(spark, base_dir):
    """Show that a restarted query resumes at the committed checkpoint.

    The first run ingests two rows and stops; the second run starts from the
    same checkpoint after one new row arrives. It processes only the new row,
    the sink accumulates three and the batch id continues.

    Guide question: if the process dies between the offset commit and the sink
    write (or the other way round), the restart can replay that batch because
    the two operations are not atomic. The checkpoint gives at-least-once, not
    exactly-once; the business guarantee must come from an idempotent sink
    (the project's MERGE/upsert by natural key). This lab does not kill the
    process.
    """
    base = _scenario_dir(base_dir, "checkpoint_restart")
    source = base / "source"
    sink = base / "sink"
    checkpoint = base / "checkpoint"
    source.mkdir(parents=True)

    _write_json(source / "run1.json", [
        {"job_id": "1", "title": "first", "captured_at": "2026-10-01T10:00:00"},
        {"job_id": "2", "title": "second", "captured_at": "2026-10-01T10:00:00"},
    ])
    stream = spark.readStream.schema(JOB_SCHEMA).json(str(source))

    first = _run_file_sink(stream, sink, checkpoint, {"once": True})
    run1 = first.lastProgress or {}
    run1_rows = run1.get("numInputRows", 0)
    run1_batch_id = run1.get("batchId", -1)

    _write_json(source / "run2.json", [
        {"job_id": "3", "title": "third", "captured_at": "2026-10-01T10:00:00"},
    ])
    second = _run_file_sink(stream, sink, checkpoint, {"once": True})
    run2 = second.lastProgress or {}
    run2_rows = run2.get("numInputRows", 0)
    run2_batch_id = run2.get("batchId", -1)

    return {
        "run1_rows": run1_rows,
        "run2_rows": run2_rows,
        "sink_rows": _parquet_count(spark, sink),
        "run1_batch_id": run1_batch_id,
        "run2_batch_id": run2_batch_id,
        "note": (
            "The checkpoint restores the committed offset: the second run "
            "reads only the file that arrived after the first run."
        ),
    }


# ---------------------------------------------------------------------------
# 2. Microbatch triggers
# ---------------------------------------------------------------------------

def microbatch_triggers(spark, base_dir):
    """Compare availableNow, once and processingTime with one file per batch."""
    base = _scenario_dir(base_dir, "microbatch_triggers")
    available_dir = base / "available_now"
    once_dir = base / "once"
    processing_dir = base / "processing_time"
    for scenario in (available_dir, once_dir, processing_dir):
        (scenario / "source").mkdir(parents=True)

    def publish(path, prefix, count):
        for index in range(count):
            _write_json(path / f"{prefix}-{index}.json", [{
                "job_id": f"{prefix}-{index}",
                "title": prefix,
                "captured_at": "2026-10-01T10:00:00",
            }])

    # availableNow: three files, maxFilesPerTrigger=1 -> three microbatches.
    available_source = available_dir / "source"
    publish(available_source, "available", 3)
    available_stream = (
        spark.readStream.schema(JOB_SCHEMA)
        .option("maxFilesPerTrigger", 1)
        .json(str(available_source))
    )
    available_query = _run_file_sink(
        available_stream,
        available_dir / "sink",
        available_dir / "checkpoint",
        {"availableNow": True},
    )
    available_now = {
        "batches": _batch_count(available_query),
        "rows": _input_rows(available_query),
        "sink_rows": _parquet_count(spark, available_dir / "sink"),
        "note": (
            "availableNow processes every available file in several "
            "microbatches (maxFilesPerTrigger=1) and then terminates."
        ),
    }

    # once: one microbatch per run. The first run sees one file; two files
    # published afterwards stay pending until the second run.
    once_source = once_dir / "source"
    publish(once_source, "once-first", 1)
    once_stream = (
        spark.readStream.schema(JOB_SCHEMA)
        .option("maxFilesPerTrigger", 1)
        .json(str(once_source))
    )
    once_first = _run_file_sink(
        once_stream,
        once_dir / "sink",
        once_dir / "checkpoint",
        {"once": True},
    )
    publish(once_source, "once-pending", 2)
    pending_after_first = (
        len(list(once_source.glob("*.json"))) - _input_rows(once_first)
    )

    once_second = _run_file_sink(
        once_stream,
        once_dir / "sink",
        once_dir / "checkpoint",
        {"once": True},
    )
    second_rows = _input_rows(once_second)
    pending_after_second = max(0, pending_after_first - second_rows)
    pending_after_drain = pending_after_second
    runs_to_drain = 2
    # Spark 4.2 reads all pending files in the second ``once`` batch (it
    # ignores maxFilesPerTrigger). A runtime that honours the limit would
    # need one more ``once`` run; the bounded loop drains the rest without
    # assuming a number of files per batch.
    while pending_after_drain > 0 and runs_to_drain < 4:
        extra = _run_file_sink(
            once_stream,
            once_dir / "sink",
            once_dir / "checkpoint",
            {"once": True},
        )
        pending_after_drain = max(
            0, pending_after_drain - _input_rows(extra)
        )
        runs_to_drain += 1

    once = {
        "batches": _batch_count(once_first),
        "rows": _input_rows(once_first),
        "pending_after_first": pending_after_first,
        "second_batches": _batch_count(once_second),
        "second_rows": second_rows,
        "pending_after_second": pending_after_second,
        "pending_after_drain": pending_after_drain,
        "runs_to_drain": runs_to_drain,
        "sink_rows": _parquet_count(spark, once_dir / "sink"),
        "note": (
            "once runs exactly one microbatch and terminates. Spark 4.2 reads "
            "all files available at launch in that batch (it ignores "
            "maxFilesPerTrigger), so the two files published after the first "
            "run stay pending and the second run reads both in one microbatch; "
            "a runtime that honours maxFilesPerTrigger would need an extra "
            "once run. The bounded loop drains the pending files in any case."
        ),
    }

    # processingTime: the query stays alive and picks up a file added while
    # it is running; the bounded wait proves liveness without a fixed sleep.
    processing_source = processing_dir / "source"
    publish(processing_source, "processing", 3)
    processing_stream = (
        spark.readStream.schema(JOB_SCHEMA)
        .option("maxFilesPerTrigger", 1)
        .json(str(processing_source))
    )
    processing_query = (
        processing_stream.writeStream
        .format("parquet")
        .outputMode("append")
        .option("path", str(processing_dir / "sink"))
        .option("checkpointLocation", str(processing_dir / "checkpoint"))
        .trigger(processingTime="200 milliseconds")
        .start()
    )
    alive_after_first_batch = False
    picked_up_added_file = False
    batches = 0
    rows = 0
    try:
        first_batch = _wait_until(
            lambda: _batch_count(processing_query) >= 1, timeout_s=30
        )
        alive_after_first_batch = bool(first_batch and processing_query.isActive)
        _write_json(processing_source / "processing-added.json", [{
            "job_id": "processing-added",
            "title": "added while running",
            "captured_at": "2026-10-01T10:00:00",
        }])
        picked_up_added_file = _wait_until(
            lambda: _input_rows(processing_query) >= 4, timeout_s=30
        )
        batches = _batch_count(processing_query)
        rows = _input_rows(processing_query)
    finally:
        processing_query.stop()
    processing_time = {
        "batches": batches,
        "rows": rows,
        "alive_after_first_batch": alive_after_first_batch,
        "picked_up_added_file": picked_up_added_file,
        "sink_rows": _parquet_count(spark, processing_dir / "sink"),
        "note": (
            "processingTime keeps the query alive between triggers; the file "
            "published while it runs is processed in a later microbatch."
        ),
    }

    return {
        "available_now": available_now,
        "once": once,
        "processing_time": processing_time,
    }


# ---------------------------------------------------------------------------
# 3. Watermark and late events
# ---------------------------------------------------------------------------

def watermark_late_event(spark, base_dir):
    """Drop a late event after the checkpoint restores the watermark.

    The scenario uses ``outputMode("update")`` plus an append-only Parquet
    sink (through ``foreachBatch``) because append output mode emits no window
    until the watermark passes the window end. Warning: appending updates can
    duplicate a window if the same window changes in several microbatches;
    here each window changes at most once, so the sink has one row per
    window. A production sink would need an upsert by window key.

    The late event is evidenced by the absence of the 11:50 window in the
    sink; ``numRowsDroppedByWatermark`` is the engine counter and is 0 here
    because the checkpoint drops the event before it enters the state.
    """
    base = _scenario_dir(base_dir, "watermark_late_event")
    source = base / "source"
    sink = base / "sink"
    checkpoint = base / "checkpoint"
    source.mkdir(parents=True)

    def run_watermark():
        stream = spark.readStream.schema(EVENT_SCHEMA).json(str(source))
        windows = (
            stream
            .withWatermark("event_time", "10 minutes")
            .groupBy(F.window("event_time", "10 minutes"))
            .count()
        )
        sink_path = str(sink)

        def append_batch(batch_df, batch_id):
            batch_df.write.mode("append").parquet(sink_path)

        query = (
            windows.writeStream
            .outputMode("update")
            .foreachBatch(append_batch)
            .option("checkpointLocation", str(checkpoint))
            .trigger(availableNow=True)
            .start()
        )
        query.awaitTermination()
        return query

    _write_json(source / "run1.json", [
        {"event_id": "1", "event_time": "2026-10-01T12:00:00"},
        {"event_id": "2", "event_time": "2026-10-01T12:05:00"},
        {"event_id": "3", "event_time": "2026-10-01T12:10:00"},
    ])
    run_watermark()
    windows_run1 = _parquet_count(spark, sink)

    _write_json(source / "run2.json", [
        {"event_id": "4", "event_time": "2026-10-01T11:50:00"},
        {"event_id": "5", "event_time": "2026-10-01T12:20:00"},
    ])
    second = run_watermark()
    windows_run2 = _parquet_count(spark, sink)

    window_rows = (
        spark.read.parquet(str(sink))
        .selectExpr(
            "date_format(window.start, 'yyyy-MM-dd HH:mm') AS window_start",
        )
        .collect()
    )
    window_starts = sorted(row["window_start"] for row in window_rows)
    late_start = "2026-10-01 11:50"
    late_end = "2026-10-01 12:00"

    state_operators = []
    if second.lastProgress:
        state_operators = second.lastProgress.get("stateOperators", [])

    return {
        "windows_run1": windows_run1,
        "windows_run2": windows_run2,
        "new_windows_run2": windows_run2 - windows_run1,
        "late_event_window": {
            "start": late_start,
            "end": late_end,
            "present": late_start in window_starts,
        },
        "windows": window_starts,
        "state_operators": state_operators,
        "note": (
            "The checkpoint restores the watermark (12:00), so the 11:50 "
            "event is discarded and only the 12:20 window is added. Update "
            "output mode is used because append would not emit a window until "
            "the watermark passes its end; foreachBatch appends the changed "
            "windows to Parquet."
        ),
    }


# ---------------------------------------------------------------------------
# 4. Exactly-once and the role of the checkpoint
# ---------------------------------------------------------------------------

def exactly_once(spark, base_dir):
    """Show checkpoint idempotency, duplicate reprocessing and an upsert."""
    base = _scenario_dir(base_dir, "exactly_once")
    source = base / "source"
    source.mkdir(parents=True)

    _write_json(source / "batch.json", [
        {"job_id": "1", "title": "one", "captured_at": "2026-10-01T10:00:00"},
        {"job_id": "2", "title": "two", "captured_at": "2026-10-01T10:00:00"},
        {"job_id": "3", "title": "three", "captured_at": "2026-10-01T10:00:00"},
    ])

    append_sink = base / "append_sink"
    append_checkpoint = base / "append_checkpoint"

    # A plain foreachBatch append is used instead of the file sink's own
    # metadata log: the file sink keeps ``_spark_metadata`` in the sink and
    # would also suppress the reprocessing after deleting the checkpoint,
    # hiding the checkpoint's role. Here the checkpoint is the only guard.
    def append_batch(batch_df, batch_id):
        batch_df.write.mode("append").parquet(str(append_sink))

    def run_append():
        stream = spark.readStream.schema(JOB_SCHEMA).json(str(source))
        query = (
            stream.writeStream
            .outputMode("append")
            .foreachBatch(append_batch)
            .option("checkpointLocation", str(append_checkpoint))
            .trigger(once=True)
            .start()
        )
        query.awaitTermination()
        return query

    run_append()
    with_checkpoint_rows = _parquet_count(spark, append_sink)

    second = run_append()
    second_run_rows = _input_rows(second)
    rows_after_second_run = _parquet_count(spark, append_sink)

    shutil.rmtree(append_checkpoint, ignore_errors=True)
    reprocess = run_append()
    without_checkpoint_rows = _parquet_count(spark, append_sink)

    # An idempotent handler by the natural key keeps the sink at one row per
    # key even when the same microbatch is processed twice.
    upsert_sink = base / "upsert_sink"
    upsert_checkpoint = base / "upsert_checkpoint"

    def run_upsert():
        stream = spark.readStream.schema(JOB_SCHEMA).json(str(source))
        query = (
            stream.writeStream
            .outputMode("append")
            .foreachBatch(
                lambda batch_df, batch_id: _upsert_parquet(
                    batch_df, upsert_sink, key="job_id", keep_latest=False
                )
            )
            .option("checkpointLocation", str(upsert_checkpoint))
            .trigger(once=True)
            .start()
        )
        query.awaitTermination()
        return query

    run_upsert()
    shutil.rmtree(upsert_checkpoint, ignore_errors=True)
    run_upsert()
    upsert_rows = _parquet_count(spark, upsert_sink)

    duplicates = without_checkpoint_rows - with_checkpoint_rows
    return {
        "with_checkpoint_rows": with_checkpoint_rows,
        "second_run_rows": second_run_rows,
        "rows_after_second_run": rows_after_second_run,
        "reprocessed_rows": _input_rows(reprocess),
        "without_checkpoint_rows": without_checkpoint_rows,
        "duplicates_without_checkpoint": duplicates,
        "upsert_rows": upsert_rows,
        "note": (
            "The checkpoint is the guarantee: with it the second run reads 0 "
            "rows; without it the same three-row batch is appended again. The "
            "append sink uses foreachBatch so the checkpoint (not the file "
            "sink metadata log) is the only guard. The upsert handler is "
            "idempotent by job_id and stays at three rows."
        ),
    }


# ---------------------------------------------------------------------------
# 5. foreachBatch: append versus upsert
# ---------------------------------------------------------------------------

def foreach_batch_upsert(spark, base_dir):
    """Process the same microbatch twice with append and with an upsert."""
    base = _scenario_dir(base_dir, "foreach_batch_upsert")
    source = base / "source"
    source.mkdir(parents=True)

    _write_json(source / "batch.json", [
        {"job_id": "1", "title": "old", "captured_at": "2026-10-01T10:00:00"},
        {"job_id": "1", "title": "new", "captured_at": "2026-10-01T11:00:00"},
        {"job_id": "2", "title": "only", "captured_at": "2026-10-01T10:00:00"},
    ])

    def run_twice(handler, sink, prefix):
        checkpoint = base / f"{prefix}_checkpoint"

        def start():
            stream = spark.readStream.schema(JOB_SCHEMA).json(str(source))
            query = (
                stream.writeStream
                .outputMode("append")
                .foreachBatch(handler)
                .option("checkpointLocation", str(checkpoint))
                .trigger(once=True)
                .start()
            )
            query.awaitTermination()

        start()
        shutil.rmtree(checkpoint, ignore_errors=True)
        start()
        return _parquet_count(spark, sink)

    append_sink = base / "append_sink"

    def append_handler(batch_df, batch_id):
        batch_df.write.mode("append").parquet(str(append_sink))

    append_rows = run_twice(append_handler, append_sink, "append")

    upsert_sink = base / "upsert_sink"

    def upsert_handler(batch_df, batch_id):
        _upsert_parquet(
            batch_df, upsert_sink, key="job_id", keep_latest=True
        )

    upsert_rows = run_twice(upsert_handler, upsert_sink, "upsert")

    latest = {
        row["job_id"]: row["title"]
        for row in spark.read.parquet(str(upsert_sink)).collect()
    }

    return {
        "append_rows": append_rows,
        "upsert_rows": upsert_rows,
        "distinct_keys": len(latest),
        "latest_values": latest,
        "note": (
            "append duplicates the same microbatch (three rows each run); the "
            "upsert keeps one row per job_id and lets the newest captured_at win."
        ),
    }


# ---------------------------------------------------------------------------
# Full bench
# ---------------------------------------------------------------------------

def run_all(spark, base_dir):
    """Run the five scenarios in plan order and return results and timings."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)

    functions = {
        "checkpoint_restart": checkpoint_restart,
        "microbatch_triggers": microbatch_triggers,
        "watermark_late_event": watermark_late_event,
        "exactly_once": exactly_once,
        "foreach_batch_upsert": foreach_batch_upsert,
    }

    scenarios = {}
    timings_ms = {}
    for name in SCENARIO_NAMES:
        started = time.perf_counter()
        scenarios[name] = functions[name](spark, base)
        timings_ms[name] = round((time.perf_counter() - started) * 1000, 1)

    return {"scenarios": scenarios, "timings_ms": timings_ms}
