"""Run-level metrics of the weekly job (spec 011, T-04 and T-05).

Every run of the job leaves one row in ``<catalog>.ops.run_metrics`` with its
business figures and its freshness, readable from the cluster without waking
the SQL warehouse. The contract is the one of ``plan.md`` section 7.1 and
``design-observability.md`` section 3: thirteen columns, ``run_id`` as the
``MERGE`` key, ``checks``/``metrics`` as byte-stable JSON strings and
``recorded_at`` for auditing.

The ``ops`` schema is created **outside** this module (by the CLI, at no cost
in dev and prod). :func:`ensure_table` only creates the table itself, which is
derived and rebuildable from the runs: it is not the canonical copy of
anything, so a missing table never needs a manual migration.

Two layers:

* pure helpers, no Spark and no I/O: :func:`build_run_row`,
  :func:`parse_data_date`, :func:`pending_files`, :func:`build_checks`,
  :func:`failed_checks`, :func:`checks_json`, :func:`metrics_json`,
  :func:`merge_sql` and :func:`table_name`;
* :func:`record`, the single writer of the table. It is **best effort**: a
  failure is logged and returned as ``{"status": "failed", "error": ...}``
  instead of raising, so a metrics problem never fails a business task.
  ``spark.conf`` is never used as a parameter bag.

The closing task of the job (``run_summary.ipynb``, T-05) is only
coordination: it reads the bronze task value, ``meta.json`` and the
landing/checkpoint metadata, and hands them to :func:`pending_files` and
:func:`build_checks`. The threshold of freshness and the pending-file rule
live here, where they are tested without a cluster.

``status`` of a row is the **state of the closing** (the four checks), not the
aggregate of the tasks: the per-task state and durations stay in
``system.lakeflow.job_task_run_timeline``, which the run never waits for.

The last commit of each checkpoint is read **at closing time**. A run that
ingests no row writes no new commit (measured with Spark 4.2 locally), so the
files that were already waiting keep counting as pending: an empty batch never
hides the backlog.
"""
import datetime as dt
import json
import math

from pyspark.sql import types as T

#: Schema of the metrics table; created outside this module (CLI).
OPS_SCHEMA = "ops"

#: Table that holds one row per job run.
METRICS_TABLE = "run_metrics"

#: Single definition of the column contract (order and Spark type name). The
#: tuple :data:`COLUMNS`, the :data:`TABLE_SCHEMA` and the ``CREATE TABLE``
#: DDL of :func:`ensure_table` are all derived from it.
_COLUMN_TYPES = (
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

_SPARK_TYPES = {
    "string": T.StringType,
    "timestamp": T.TimestampType,
    "double": T.DoubleType,
    "bigint": T.LongType,
    "date": T.DateType,
    "int": T.IntegerType,
}

#: Column names in contract order (``run_id`` first, its ``MERGE`` key).
COLUMNS = tuple(name for name, _ in _COLUMN_TYPES)

#: Schema of the table, in contract order; every field is nullable.
TABLE_SCHEMA = T.StructType(
    [
        T.StructField(name, _SPARK_TYPES[kind](), True)
        for name, kind in _COLUMN_TYPES
    ]
)

#: Temporary view the staged row is read from by the ``MERGE``.
METRICS_VIEW = "__run_metrics"

#: Maximum length of the error returned by :func:`record` (log hygiene).
_MAX_ERROR_CHARS = 500

#: Maximum age of the published data before the run fails (days). The same
#: threshold as the dashboard workflow, which refuses to publish older data.
MAX_DATA_AGE_DAYS = 8

#: Bronze sources of the job, in the order of ``SOURCES`` of
#: ``bronze_ingest.ipynb``. The notebook owns the tuple and
#: ``Bronze_Summary`` does not publish it, so it is repeated here instead of
#: imported: this module stays independent of the notebook code.
EXPECTED_SOURCES = ("indeed", "linkedin", "infojobs", "multi_site")

#: Pending files published per source (the count is complete, the list is not).
MAX_EXAMPLES = 3


def table_name(catalog, schema=OPS_SCHEMA) -> str:
    """Return the fully qualified name of the metrics table."""
    return f"{catalog}.{schema}.{METRICS_TABLE}"


def _as_dict(value):
    """Return ``value`` when it is a mapping, or an empty dict."""
    return value if isinstance(value, dict) else {}


def _int_or_none(value):
    """Return ``value`` as ``int``, or ``None`` when missing or not numeric.

    ``bool`` is excluded even though it is an ``int`` subclass: a boolean is
    never a valid row figure.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _json_safe(value):
    """Recursively replace non-finite floats with ``None``.

    Same criterion as ``Bronze_Summary._json_safe`` (spec 011): ``json.dumps``
    writes ``NaN``/``Infinity`` by default, which is not valid JSON, and
    raises with ``allow_nan=False``. The payloads of this table prefer
    ``null`` so any JSON reader can parse them; dicts and sequences are
    walked because ``checks`` and ``metrics`` carry caller-built structures.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _stable_json(payload) -> str:
    """Serialize ``payload``: sorted keys, no spaces, strict JSON."""
    return json.dumps(
        _json_safe(payload),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def checks_json(checks) -> str:
    """Serialize the checks of the run; ``None`` is an empty object.

    Stable byte to byte for the same input (sorted keys, no whitespace), so
    two writes of the same run can be compared without normalizing them.
    Non-finite floats become ``null``; the output is always valid JSON.
    """
    return _stable_json({} if checks is None else checks)


def metrics_json(summary) -> str:
    """Serialize the bronze summary as received; ``None`` is ``{}``.

    Same stability and non-finite rules as :func:`checks_json`: the summary
    is published whole, without adding or dropping fields.
    """
    return _stable_json({} if summary is None else summary)


def _as_utc(value, name):
    """Normalize a datetime or an ISO string to an aware UTC datetime.

    A naive datetime is read as UTC; an aware one is converted to UTC. ISO
    strings use ``datetime.fromisoformat``, with ``Z`` accepted as ``+00:00``
    (the format of the job API). The argument name is only used by the error
    message.
    """
    if isinstance(value, dt.datetime):
        moment = value
    elif isinstance(value, str):
        moment = dt.datetime.fromisoformat(
            value.strip().replace("Z", "+00:00")
        )
    else:
        raise ValueError(
            f"'{name}' debe ser un datetime o una cadena ISO."
        )
    if moment.tzinfo is None:
        return moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _as_date(value):
    """Normalize a date, a datetime or a ``YYYY-MM-DD`` string to a date."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value.strip())
    raise ValueError(
        "'data_date' debe ser una fecha, un datetime o una cadena "
        "'YYYY-MM-DD'."
    )


def parse_data_date(value):
    """Normalize ``data_date`` to a date, or ``None`` when it is not usable.

    Accepts a ``date``, a ``datetime`` or a ``YYYY-MM-DD`` string. ``None``,
    an empty string, whitespace, ``"None"``, an unsupported type or an invalid
    date are published as ``None`` instead of raising: a corrupt ``meta.json``
    must never abort the closing task before it writes the row, so the value
    is published as ``null`` and the freshness check is the one that fails.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() == "none":
            return None
    elif not isinstance(value, dt.date):
        return None
    try:
        return _as_date(value)
    except (TypeError, ValueError):
        return None


def _stream_sum(bronze_summary, keys):
    """Sum the first reported figure of each stream over ``keys``.

    A stream without any of the keys contributes ``0``; a missing or
    non-list ``streams`` is an empty total. The caller decides whether a
    missing summary is ``None`` (not reported) or a real zero.
    """
    streams = _as_dict(bronze_summary).get("streams")
    if not isinstance(streams, (list, tuple)):
        return 0
    total = 0
    for stream in streams:
        entry = _as_dict(stream)
        for key in keys:
            value = _int_or_none(entry.get(key))
            if value is not None:
                total += value
                break
    return total


def build_run_row(
    *,
    run_id,
    job_id,
    started_at,
    finished_at=None,
    bronze_summary=None,
    data_date=None,
    status="success",
    checks=None,
    now=None,
) -> dict:
    """Build the metrics row of one run (pure: no Spark, no I/O).

    ``started_at``/``finished_at``/``now`` accept a ``datetime`` or an ISO
    string and are normalized to aware UTC datetimes; ``finished_at``
    defaults to ``now`` and ``now`` to the current UTC time. ``duration_s``
    is never negative (a clock skew is published as ``0``).

    ``bronze_rows``/``quarantine_rows`` are ``None`` when no
    ``bronze_summary`` is given; with one, they sum ``num_input_rows_total``
    (falling back to ``num_input_rows``) and ``quarantined_rows`` over its
    streams respectively, so a summary without streams is a real ``0``.

    ``data_date`` accepts a date, a datetime or a ``YYYY-MM-DD`` string and is
    ``None`` when not given; a value that cannot be parsed is also ``None``
    (see :func:`parse_data_date`), so a corrupt export never raises here.
    ``data_age_days`` is its age against UTC and can be negative for a future
    date.

    ``checks`` and ``metrics`` are serialized with :func:`checks_json` and
    :func:`metrics_json` (``"{}"`` when ``None``); ``recorded_at`` is ``now``.
    ``status`` is published as received. Only ``run_id`` and ``job_id`` are
    validated (non-empty strings).
    """
    for name, value in (("run_id", run_id), ("job_id", job_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{name}' debe ser una cadena no vacía.")

    reference = dt.datetime.now(dt.timezone.utc) if now is None else now
    reference = _as_utc(reference, "now")
    started = _as_utc(started_at, "started_at")
    finished = (
        reference
        if finished_at is None
        else _as_utc(finished_at, "finished_at")
    )
    day = parse_data_date(data_date)
    has_summary = isinstance(bronze_summary, dict)

    return {
        "run_id": run_id,
        "job_id": job_id,
        "started_at": started,
        "finished_at": finished,
        "duration_s": max(0.0, (finished - started).total_seconds()),
        "bronze_rows": (
            _stream_sum(
                bronze_summary,
                ("num_input_rows_total", "num_input_rows"),
            )
            if has_summary
            else None
        ),
        "quarantine_rows": (
            _stream_sum(bronze_summary, ("quarantined_rows",))
            if has_summary
            else None
        ),
        "data_date": day,
        "data_age_days": (
            None if day is None else (reference.date() - day).days
        ),
        "status": status,
        "checks": checks_json(checks),
        "metrics": metrics_json(bronze_summary),
        "recorded_at": reference,
    }


def pending_files(files, commits, run_start_ms) -> dict:
    """Group the landing files the run had not ingested when it started.

    ``files`` are metadata entries of the landing
    (``{"source": str, "path": str, "modification_ms": int}``; no file is
    read), ``commits`` maps each source to the last commit of its checkpoint
    (mtime in ms) and ``run_start_ms`` is the start of the run, in ms. The
    start is **required** (:class:`ValueError` in Spanish otherwise): without
    it, a file that lands while the run is executing cannot be told apart
    from a backlog.

    A file is pending when it is newer than the last commit of its source and
    older than the start of the run: a file that lands *during* the run is
    ingested by the next one and is never a backlog of this one. A source
    without a commit (or with ``None``) counts every file older than the
    start, which is what the first run of a checkpoint must ingest. Entries
    without ``source`` or without a numeric ``modification_ms`` are ignored,
    and ``files=None`` is an empty result. A file without a ``path`` still
    counts (it exists) but cannot be named in ``oldest``/``examples``.

    The returned dict has an entry (``count``, ``oldest`` path and up to
    :data:`MAX_EXAMPLES` example paths) **only** for the sources with pending
    files, so ``{}`` means "nothing pending".
    """
    if run_start_ms is None:
        raise ValueError(
            "'run_start_ms' es obligatorio: sin el inicio del run no se "
            "puede separar lo pendiente de lo que llega durante el run."
        )
    start = _int_or_none(run_start_ms)
    if start is None:
        raise ValueError(
            "'run_start_ms' debe ser un entero (epoch en milisegundos)."
        )
    last_commits = _as_dict(commits)
    pending = {}
    oldest_ms = {}
    for entry in files or ():
        item = _as_dict(entry)
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            continue
        modification = _int_or_none(item.get("modification_ms"))
        if modification is None:
            continue
        commit = _int_or_none(last_commits.get(source))
        if commit is not None and modification <= commit:
            continue
        if modification >= start:
            continue
        detail = pending.setdefault(
            source, {"count": 0, "oldest": None, "examples": []}
        )
        detail["count"] += 1
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        if source not in oldest_ms or modification < oldest_ms[source]:
            oldest_ms[source] = modification
            detail["oldest"] = path
        if len(detail["examples"]) < MAX_EXAMPLES:
            detail["examples"].append(path)
    return pending


def _summary_sources(bronze_summary) -> list:
    """Sorted source names of the streams of ``bronze_summary``.

    A missing summary, a non-list ``streams`` or a stream without a usable
    ``source`` contributes nothing: the caller decides whether the empty list
    is a failed check (it is) or a run without bronze.
    """
    streams = _as_dict(bronze_summary).get("streams")
    if not isinstance(streams, (list, tuple)):
        return []
    found = set()
    for stream in streams:
        entry = _as_dict(stream)
        source = entry.get("source")
        if isinstance(source, str) and source.strip():
            found.add(source)
    return sorted(found)


def build_checks(
    *,
    bronze_summary=None,
    data_date=None,
    pending=None,
    listing_ok=True,
    now=None,
    max_age_days=MAX_DATA_AGE_DAYS,
) -> dict:
    """Build the checks of the run as native JSON values (pure, no I/O).

    ``bronze_summary`` is the payload published by the bronze task,
    ``data_date`` the one of ``meta.json`` (a date, a datetime or a
    ``YYYY-MM-DD`` string, parsed with :func:`parse_data_date`),
    ``pending`` the result of :func:`pending_files` and ``listing_ok``
    whether the landing could be listed: only an explicit ``True`` keeps the
    pending check green, so a broken listing is never published as "nothing
    pending". ``now`` is injectable and defaults to the current UTC time, so
    the age of the data is reproducible in tests.

    Four checks, in this order:

    * ``bronze_summary``: a summary with at least one stream, and its sources;
    * ``bronze_sources``: the four :data:`EXPECTED_SOURCES` are present;
    * ``freshness``: ``data_date`` is known, not in the future and not older
      than ``max_age_days`` (the age is measured against the UTC date of
      ``now``);
    * ``pending_files``: fails when the landing could not be listed or when
      files were waiting before the run started **and** the bronze ingested no
      row (a pending file with rows is the backlog of the next run, not a
      failure).

    Every value is a bool, an int, a string, ``None``, a list or a dict:
    the payload is serialized by :func:`checks_json` without a ``default``.
    A malformed ``data_date`` is reported as an absent date instead of
    raising, so the failure is published in the checks.
    """
    reference = _as_utc(
        dt.datetime.now(dt.timezone.utc) if now is None else now, "now"
    )
    sources = _summary_sources(bronze_summary)
    streams = _as_dict(bronze_summary).get("streams")
    has_streams = isinstance(streams, (list, tuple)) and len(streams) > 0
    present = [source for source in EXPECTED_SOURCES if source in sources]

    day = parse_data_date(data_date)
    age = None if day is None else (reference.date() - day).days
    fresh = day is not None and 0 <= age <= max_age_days

    listed = listing_ok is True
    pending_entries = dict(_as_dict(pending))
    total = 0
    for detail in pending_entries.values():
        total += _int_or_none(_as_dict(detail).get("count")) or 0
    bronze_rows = _stream_sum(
        bronze_summary, ("num_input_rows_total", "num_input_rows")
    )

    return {
        "bronze_summary": {"ok": has_streams, "sources": sources},
        "bronze_sources": {
            "ok": len(present) == len(EXPECTED_SOURCES),
            "expected": len(EXPECTED_SOURCES),
            "present": len(present),
        },
        "freshness": {
            "ok": fresh,
            "data_date": None if day is None else day.isoformat(),
            "data_age_days": age,
            "max_age_days": max_age_days,
        },
        "pending_files": {
            "ok": listed and not (total > 0 and bronze_rows == 0),
            "total": total,
            "by_source": pending_entries,
            "bronze_rows": bronze_rows,
            "listing_ok": listed,
        },
    }


def failed_checks(checks) -> list:
    """Names of the checks whose ``ok`` is not exactly ``True``, in order.

    Truthiness is not accepted (``1`` or ``"ok"`` would hide a broken check)
    and a check without ``ok``, or not a dict, counts as failed: the closing
    task only accepts an explicit ``True``. ``None`` is an empty list.
    """
    return [
        name
        for name, entry in _as_dict(checks).items()
        if _as_dict(entry).get("ok") is not True
    ]


def merge_sql(table, view_name=METRICS_VIEW) -> str:
    """``MERGE`` the staged view into ``table``, upserting by ``run_id``.

    Repeating the write of a run updates its own row instead of duplicating
    it; the source view carries the whole :data:`TABLE_SCHEMA`, so ``SET *``
    updates every column.
    """
    return (
        f"MERGE INTO {table} AS t "
        f"USING {view_name} AS s "
        "ON t.run_id = s.run_id "
        "WHEN MATCHED THEN UPDATE SET * "
        "WHEN NOT MATCHED THEN INSERT *"
    )


def ensure_table(spark, table) -> bool:
    """Create ``table`` when missing; return whether it was created.

    The ``ops`` schema is **not** created here: it is created outside the
    module (by the CLI). A missing schema is an error of the caller, which
    :func:`record` catches like any other write failure. The DDL is derived
    from :data:`TABLE_SCHEMA`, so the table and the staged DataFrame can
    never drift apart.
    """
    if spark.catalog.tableExists(table):
        return False
    columns = ", ".join(
        f"{field.name} {field.dataType.simpleString().upper()}"
        for field in TABLE_SCHEMA.fields
    )
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} ({columns}) USING DELTA")
    return True


def _error_message(exc) -> str:
    """Short single-line description of ``exc`` for the returned status."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text[:_MAX_ERROR_CHARS]


def _report(logger, message):
    """Log ``message`` with ``logger.warning``, or print it without a logger.

    Reporting never breaks the writer: an exception of the logger itself is
    swallowed, because :func:`record` must not propagate anything.
    """
    try:
        warning = getattr(logger, "warning", None)
        if callable(warning):
            warning(message)
        else:
            print(message)
    except Exception:  # noqa: BLE001 - best-effort reporting
        pass


def record(spark, table, row, logger=None) -> dict:
    """Write one metrics row to ``table``; never propagates an error.

    All thirteen columns of :data:`COLUMNS` must be present in ``row``
    (extra keys are ignored, for forward compatibility), and ``run_id`` and
    ``job_id`` must be non-empty strings: a null or empty ``MERGE`` key would
    never match an existing row. The table is created when missing, the row
    is staged as a one-row DataFrame with :data:`TABLE_SCHEMA` in the
    temporary view :data:`METRICS_VIEW` and the ``MERGE`` by ``run_id`` is
    executed, so re-recording a run updates its row.

    Returns ``{"status": "ok", "error": None}`` on success, or
    ``{"status": "failed", "error": "<short message>"}`` after logging the
    failure (with ``logger.warning`` when a logger is given, on stdout
    otherwise). The caller decides what to do with the status: the final
    task fails on its checks, not on this best-effort write.
    """
    try:
        missing = [name for name in COLUMNS if name not in row]
        if missing:
            raise ValueError(
                "Faltan columnas en la fila de ops.run_metrics: "
                + ", ".join(missing)
                + "."
            )
        for name in ("run_id", "job_id"):
            value = row[name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"La columna '{name}' de ops.run_metrics no puede ser "
                    "nula ni vacía."
                )
        ensure_table(spark, table)
        frame = spark.createDataFrame(
            [tuple(row[name] for name in COLUMNS)], schema=TABLE_SCHEMA
        )
        frame.createOrReplaceTempView(METRICS_VIEW)
        spark.sql(merge_sql(table))
        return {"status": "ok", "error": None}
    except Exception as exc:  # noqa: BLE001 - best effort by contract
        message = _error_message(exc)
        _report(
            logger,
            f"[run_metrics] no se pudo escribir en {table}: {message}",
        )
        return {"status": "failed", "error": message}
