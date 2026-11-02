"""Run-level metrics of the weekly job (spec 011, T-04).

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
  :func:`checks_json`, :func:`metrics_json`, :func:`merge_sql` and
  :func:`table_name`;
* :func:`record`, the single writer of the table. It is **best effort**: a
  failure is logged and returned as ``{"status": "failed", "error": ...}``
  instead of raising, so a metrics problem never fails a business task.
  ``spark.conf`` is never used as a parameter bag.
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
    ``None`` when not given; ``data_age_days`` is its age against UTC and can
    be negative for a future date.

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
    day = None if data_date is None else _as_date(data_date)
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
