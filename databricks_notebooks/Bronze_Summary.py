"""Per-source streaming summary of the bronze ingestion (spec 011, T-03).

The bronze task runs four ``foreachBatch`` streams with
``trigger(availableNow=True)``. After ``awaitTermination`` this module turns
the engine progress of each query into a small, stable JSON payload that the
run summary consumes, without touching the landing, counting the batch or
reading table history:

* :func:`build_stream_summary` normalizes ``query.lastProgress`` and
  ``query.recentProgress`` for one source.
* :func:`merge_summaries` orders the per-source summaries and wraps them with
  the ``version`` and the run ``config``.
* :func:`to_json` serializes the payload byte-stable (sorted keys, no spaces).

The module is pure: standard library only, no Spark import and no I/O, so the
contract is testable without a cluster. Missing or unexpected engine keys
never raise: they become ``None`` (or ``0``/``[]`` where the contract fixes
those values). Only ``source`` is validated.

``last_progress`` is the progress of the last microbatch; ``None`` means the
query reported no progress at all (for example a stream that never started).
A real microbatch without rows reports ``numInputRows = 0`` and is published
as ``0``, never as ``None``. ``num_input_rows_total`` adds ``numInputRows``
over ``recent_progress`` (the microbatches of this query still kept by the
driver); an entry without the key contributes nothing.

``backlog`` is best effort: the file source only exposes
``numFilesOutstanding``/``numBytesOutstanding`` in ``sources[].metrics`` when
the trigger is throttled (``maxFilesPerTrigger``/``maxBytesPerTrigger``); when
no source exposes them the field stays ``None`` and the backlog is measured by
the diagnostic (landing files against ``batch_id``), never by the run.

When the query reported no progress the summary carries a short ``note`` so
the run summary can tell "no microbatch" from "a microbatch without rows".
Non-finite figures (``NaN``/``+-Infinity``) are published as ``None``: they do
not describe the query and would make the payload invalid JSON.
"""
import json
import math

#: Version of the published payload; bump it on any breaking change.
SUMMARY_VERSION = 1

#: ``durationMs`` keys of ``lastProgress`` published under a stable name.
#: Spark may omit any of them (the sink and the trigger decide), so the three
#: keys are always present in the payload, with ``None`` when not reported.
DURATION_KEYS = (
    ("trigger_execution", "triggerExecution"),
    ("add_batch", "addBatch"),
    ("wal_commit", "walCommit"),
)

_NO_MICROBATCH_NOTE = "no microbatch reported by the query"


def _as_dict(value):
    """Return ``value`` when it is a mapping, or an empty dict."""
    return value if isinstance(value, dict) else {}


def _int_or_none(value):
    """Return ``value`` as ``int``, or ``None`` when missing or not numeric.

    ``bool`` is excluded even though it is an ``int`` subclass: a boolean is
    never a valid row or byte figure.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _int_or_zero(value):
    """Return ``value`` as ``int``, or ``0`` when missing or not numeric."""
    return _int_or_none(value) or 0


def _float_or_none(value):
    """Return ``value`` as a finite float, or ``None`` otherwise.

    A missing, non-numeric or non-finite rate (``NaN``/``+-Infinity``) is
    published as ``None``: the engine should not report it, and it would make
    the JSON payload invalid.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _json_safe(value):
    """Recursively replace non-finite floats with ``None``.

    ``json.dumps`` writes ``NaN``/``Infinity`` by default (not valid JSON) and
    raises with ``allow_nan=False``. The summary prefers ``null`` so any JSON
    reader can parse the payload; dicts and sequences are walked because a
    caller may serialize its own summary.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _total_input_rows(recent_progress):
    """Sum ``numInputRows`` over the microbatches of the query.

    ``None`` means the caller did not pass ``recent_progress`` (the value is
    not reported); an empty list is a real ``0``. Entries without the key (or
    with a non-numeric value) contribute nothing.
    """
    if not isinstance(recent_progress, (list, tuple)):
        return None
    total = 0
    for entry in recent_progress:
        total += _int_or_none(_as_dict(entry).get("numInputRows")) or 0
    return total


def _state_operators(value):
    """Shallow copy of the ``stateOperators`` list, or ``[]`` when absent.

    The list is published as the engine reports it: the fields are not
    normalized because they depend on the query plan (a stateful stream has
    them, a stateless one does not).
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) if isinstance(item, dict) else item for item in value]


def _backlog(progress):
    """Best-effort backlog from ``sources[].metrics``, or ``None``.

    Sums ``numFilesOutstanding``/``numBytesOutstanding`` over the sources that
    expose them (best effort: the file source only reports them when the
    trigger is throttled). ``None`` when no source reports either figure.
    """
    sources = progress.get("sources")
    if not isinstance(sources, (list, tuple)):
        return None
    files_outstanding = 0
    bytes_outstanding = 0
    found = False
    for entry in sources:
        metrics = _as_dict(_as_dict(entry).get("metrics"))
        files = _int_or_none(metrics.get("numFilesOutstanding"))
        pending_bytes = _int_or_none(metrics.get("numBytesOutstanding"))
        if files is None and pending_bytes is None:
            continue
        found = True
        files_outstanding += files or 0
        bytes_outstanding += pending_bytes or 0
    if not found:
        return None
    return {
        "files_outstanding": files_outstanding,
        "bytes_outstanding": bytes_outstanding,
    }


def build_stream_summary(
    source,
    *,
    last_progress=None,
    recent_progress=None,
    checkpoint=None,
    quarantine_rows=None,
    status=None,
) -> dict:
    """Summarize one source from the progress of its streaming query.

    ``source`` must be a non-empty string (the only validated argument). The
    rest are the engine reports of the query, read after ``awaitTermination``
    (``lastProgress``, ``recentProgress`` and ``status["message"]``), the
    checkpoint location and the new quarantine rows of the run. None of them
    is mutated: the state operators are copied before publishing them.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError(
            "El resumen de streaming necesita un 'source' no vacio."
        )
    progress = _as_dict(last_progress)
    durations = _as_dict(progress.get("durationMs"))

    summary = {
        "source": source,
        "batch_id": _int_or_none(progress.get("batchId")),
        "num_input_rows": _int_or_none(progress.get("numInputRows")),
        "num_input_rows_total": _total_input_rows(recent_progress),
        "input_rows_per_second": _float_or_none(
            progress.get("inputRowsPerSecond")
        ),
        "processed_rows_per_second": _float_or_none(
            progress.get("processedRowsPerSecond")
        ),
        "duration_ms": {
            name: _int_or_none(durations.get(key))
            for name, key in DURATION_KEYS
        },
        "state_operators": _state_operators(progress.get("stateOperators")),
        "backlog": _backlog(progress),
        "checkpoint": checkpoint if isinstance(checkpoint, str) else None,
        "quarantined_rows": _int_or_zero(quarantine_rows),
        "status": status if isinstance(status, str) else None,
    }
    if not progress:
        summary["note"] = _NO_MICROBATCH_NOTE
    return summary


def quarantine_total(counts, source):
    """Sum the run's new quarantine rows of ``source`` over its microbatches.

    ``counts`` maps ``(source, batch_id)`` to the rows that batch added, so a
    microbatch retried by ``foreachBatch`` overwrites its own entry instead of
    adding twice (the engine re-invokes the handler with the same
    ``batch_id``). Keys of other sources, or with an unexpected shape, are
    ignored: the figure of one source never depends on another source's
    bookkeeping. A missing or non-dict ``counts`` is an empty total.
    """
    if not isinstance(counts, dict):
        return 0
    total = 0
    for key, value in counts.items():
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        if key[0] != source:
            continue
        total += _int_or_none(value) or 0
    return total


def merge_summaries(streams, config=None) -> dict:
    """Consolidate the per-source summaries into the published payload.

    The streams are shallow-copied and ordered by ``source`` so the payload
    does not depend on the order the queries were started. A repeated source
    is a contract error (it would hide the summary of another run). ``config``
    is copied as received and becomes ``{}`` when not given.
    """
    ordered = []
    seen = set()
    for stream in streams or ():
        if not isinstance(stream, dict):
            raise ValueError("Cada resumen de fuente debe ser un diccionario.")
        source = stream.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Cada resumen de fuente necesita un 'source'.")
        if source in seen:
            raise ValueError(
                f"Fuente duplicada en el resumen de bronce: {source!r}."
            )
        seen.add(source)
        ordered.append(dict(stream))
    ordered.sort(key=lambda item: item["source"])
    return {
        "version": SUMMARY_VERSION,
        "streams": ordered,
        "config": dict(config) if config is not None else {},
    }


def to_json(summary) -> str:
    """Serialize the payload with sorted keys and no whitespace.

    Stable byte to byte for the same input: the task value and the JSON stored
    in ``ops.run_metrics`` can be compared without normalizing them. ASCII is
    kept as the default of ``json.dumps`` so the payload is safe to pass
    through the job task value. Non-finite floats become ``null`` and
    ``allow_nan`` stays ``False``, so the output is strictly valid JSON and
    never carries ``NaN``/``Infinity``.
    """
    return json.dumps(
        _json_safe(summary),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
