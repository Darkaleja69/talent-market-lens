"""Truncated-run discovery for the recovery cycle (T-05; RF-1).

``discover_truncated_runs`` answers one question: which pipeline runs need a
data recovery because they never finished? It reuses ``verification.run_evidence``
for the general-log parsing (``discover_runs``, which reads the ``Inicio``/
``Fin`` blocks) and reads the per-run state files written by
``scrapers-pipeline/run_state.ps1`` (T-03/T-04).

State files
-----------
They live in ``<state_dir>/<run_date>.json``; ``state_dir`` defaults to
``logs_dir/run_state`` when not given. Schema (T-03): ``run_date``,
``started_at``, ``finished_at``, ``status`` (``pending``/``closed``) and
``sources``. A missing, unreadable or unrecognized file never raises: it is
reported as ``missing``/``unreadable``/``unknown`` in ``RunStateInfo.status``
so the caller can decide (a corrupt state never hides a truncated log). A
``run_date`` that is not a real calendar date (e.g. ``"banana"`` or
``"2026-13-45"``) is dropped to ``None``; the state-only path then falls back
to the file name.

Precedence (documented contract)
--------------------------------
1. A log block with a ``Fin pipeline`` line means the run finished and is
   never returned, even when its state file still says ``pending`` (the final
   state save may have failed): the run already published its data, and
   reconciling the state of a finished run belongs to the startup
   reconciliation (T-10), not to data recovery.
2. A truncated block (no ``Fin pipeline``) whose per-date state file is
   ``closed`` **and attributable to that block** is not returned: the run was
   already closed (for example by a previous recovery) and re-recovering it
   could duplicate uploads (RF-3 idempotency).
3. Every other truncated block is returned: state
   ``pending``/``missing``/``unreadable``/``unknown``, or a ``closed`` state
   that cannot be attributed to the block (it belongs to another run of the
   same date).
4. A ``pending`` state file whose date has no log block at all is also
   returned: the state is the durable signal RF-1 added for the case where
   the log evidence is gone.

Same-day blocks and state attribution
-------------------------------------
A general log can hold several ``Inicio`` blocks for the same date (manual
re-runs). **Every** block without ``Fin pipeline`` is returned: a later
finished block does not erase the earlier truncated one, because the later run
starts after ``$RunStart`` and does not pick up the earlier run's files. The
record marks such a block with ``superseded=True`` so T-06/T-10 decide with
context; de-duplication and avoiding re-uploads belong to T-07's idempotency,
not to hiding the truncated run.

The state file is per date and the later run overwrites it, so a ``closed``
state only suppresses a truncated block when it can be attributed to that
block. With a single block for the date it always is; with several blocks the
state's ``started_at`` must fall inside the block's time window (from its
``Inicio`` to the next block's ``Inicio``, or its own end / one day for the
last one). The window applies a short tolerance (``_STATE_START_TOLERANCE``,
5 s) because the pipeline captures the state's ``started_at`` a few lines
before writing its ``Inicio`` line, so a crossed second can place the later
run's state just before its own block; the tolerance keeps it attributed to
that later run instead of hiding the earlier truncated one. A state that
cannot be attributed (another run's, missing or unreadable) never hides a
truncated block; the record exposes ``state_attributed`` and the reason states
that the date's state belongs to another run.

Results are sorted ascending by ``(run_date, started_at)`` and carry the
parsed log summary (``run_evidence.PipelineSummary``), the state info and a
Spanish operator-facing ``reason``.

Recovery plan (T-06; RF-2, RF-3)
--------------------------------
``build_plan(run_date, projects_root, config=None, *, run_start=None,
run_end=None)`` returns a serializable ``RecoveryPlan`` for the four logical
scrapers of ``config.ps1`` (``indeed``, ``linkedin``, ``multi_site``,
``infojobs``), each with a ``RecoveryItem``. The config defaults mirror
``verification.sources`` plus the operational values of ``config.ps1``
(``coherence`` and ``fingerprint_source`` are the scraper name, ``key_column``
is the source dedup key, ``only_new`` is true for LinkedIn and Multi-site);
the recovery executor may override any field per source.

Per-source selection:

- Indeed: the newest ``indeed_jobs_YYYYMMDD_*.parquet`` of the run date in
  ``indeed_jobs_scraper/output`` (by the timestamp in the name, never mtime).
- InfoJobs: the newest ``offers_YYYYMMDD_HHMMSS.parquet`` of the run date in
  ``infojobs_jobs_scraper/data``.
- LinkedIn: the cumulative ``data/output/jobs.parquet`` snapshot; the plan
  counts its ``key_column`` values missing from
  ``uploaded_keys/linkedin.json`` (a missing state file means all are new) and
  omits the source when there are no new offers.
- Multi-site: first a dated ``jobs_unified_YYYYMMDD_*.parquet`` of the run
  date; otherwise the canonical ``jobs_unified.parquet`` only when its mtime
  falls on the run date (a snapshot from a later run is never attributed to
  this one); otherwise a **reconstruction** item (``mode="merge"``,
  ``reconstruct=True``) feeding ``merge.py`` with the portal ``jobs.csv``
  outputs modified inside the run window, and only when no portal output is
  newer than ``run_end`` (a newer output belongs to another run: no data is
  invented). Multi-site candidates are also filtered by delta against
  ``uploaded_keys/multi_site.json``.

``run_start``/``run_end`` default to the full run date; the executor can pass
the real window (for example the supervisor's abort/last-activity time) so a
run crossing midnight is still recoverable. Items carry the local paths, the
real data ``day``, ``required_cols``, ``coherence``, ``fingerprint_source``,
``key_column`` and ``only_new`` for the executor, plus a Spanish ``reason``
when they are not recoverable. ``RecoveryPlan.to_dict()`` is JSON-ready for
the executor's ``-PlanJson``.

Idempotence (T-07; RF-3)
------------------------
``apply_idempotence(plan, reader)`` returns a **new** plan (the input is never
modified) where:

- a ``file`` item whose expected landing key ``<source>/dia=<day>/<file>`` is
  already published (resolved with
  ``verification.landing.resolve_published_key``, which tolerates a unique
  staging-folder match) is omitted with ``recoverable=False``,
  ``published_key`` set and a Spanish reason; an absent or ambiguous key is
  **not** omitted;
- a ``delta``/``merge`` item whose ``new_keys`` is 0 is omitted; when the
  delta is unknown (``None``, as in reconstructions) the item stays, because
  the definitive filter runs at execution time (T-11) against
  ``uploaded_keys`` after the merge;
- a ``RemoteError`` from the reader propagates unchanged: a connectivity
  failure is never treated as "absent", and no half-updated plan is returned.

Non-recoverable items are passed through untouched, so calling the function
twice with the same reader yields the same plan. The module itself performs no
network: remote access happens only through the injected ``RemoteReader``.

``apply_state_idempotence(plan, state)`` complements that remote check with
local evidence: a recoverable item whose run-state entry
(``sources[<source>]``) shows a valid status (``ok``/``partial``) with
``uploaded > 0`` was already published by the run, so it is omitted with a
Spanish reason even if ``uploaded_keys`` was left stale. It is pure and is
applied by the ``plan`` CLI when ``--state-dir`` is given.

``_READY`` decision and closing block (T-08; RF-5, RF-6)
-------------------------------------------------------
``decide_ready(policy, published)`` is pure: with ``any_valid`` it writes only
when at least one source published valid data (``ok``/``partial``); with
``all`` every expected source must have done so; an unknown policy behaves
like ``all`` (as the pipeline does) and the Spanish reason says so. It
returns a ``ReadyDecision`` (``write``, effective ``policy``,
``valid_sources`` and the reason) and never touches the landing: T-12 writes
``_READY``.

``closing_block(published, failures=..., duration_seconds=..., finished_at=...)``
returns the closing lines with the pipeline's exact format
(``====  Fin pipeline. Fallos: N  Duracion: Ds ====`` plus one
``  [source] status=... subidos=N rechazados=N`` per source), so appending
them to a truncated general log makes ``run_evidence.parse_pipeline_log``
return ``completed=True`` and the diagnostic select the run (RF-5).

CLI (T-10/T-11; RF-2, RF-4)
---------------------------
``python -m verification.recovery pending [--logs-dir DIR] [--state-dir DIR]
[--before YYYY-MM-DD]`` prints a stable JSON document with the truncated runs
older than ``--before`` (the pipeline's startup reconciliation consumes it):
``{"schema_version": 1, "before": ..., "count": N, "runs": [...]}``.

``python -m verification.recovery plan --run-date YYYY-MM-DD
[--projects-root DIR] [--state-dir DIR] [--out FILE]`` builds the
``RecoveryPlan`` of that run (``build_plan`` plus ``apply_state_idempotence``
when ``--state-dir`` is given) and writes it as JSON (no BOM with ``--out``,
stdout otherwise); the recovery executor consumes it (T-11).

``python -m verification.recovery closing --results-file FILE --failures N
--duration D [--finished-at ISO] [--out FILE]`` emits the exact closing lines
of ``closing_block``, and ``... ready --policy P --results-file FILE
[--out FILE]`` emits the ``decide_ready`` decision as JSON
(``{"write", "policy", "valid_sources", "reason"}``); the recovery executor
appends the former to the run's general log and uses the latter for ``_READY``
(T-12). All commands are local and offline; an invalid argument exits 2 with a
Spanish message.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path, PurePosixPath

import pyarrow.parquet as pq

from verification import landing, run_evidence, sources

# Run-level state classification (T-03 schema plus file-level outcomes).
STATE_PENDING = "pending"
STATE_CLOSED = "closed"
STATE_MISSING = "missing"
STATE_UNREADABLE = "unreadable"
STATE_UNKNOWN = "unknown"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Attribution tolerance: the pipeline captures the state's ``started_at``
# ($StartTime) a few lines before writing its "Inicio" log line, so a crossed
# second can place a run's state just before its own block. Five seconds is
# well below the gap between real same-day runs.
_STATE_START_TOLERANCE = timedelta(seconds=5)


def _is_valid_date(value: str | None) -> bool:
    """Whether ``value`` is a real ``YYYY-MM-DD`` calendar date."""
    if not value or not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _parse_local(value: str | None) -> datetime | None:
    """Parse a log/state ISO timestamp to naive local time (log reference).

    Aware timestamps (the state file uses an offset) are converted to the
    machine's local time, the same convention ``run_evidence`` uses for the
    log, so a state written near midnight is not misattributed.
    """
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class RunStateInfo:
    """Outcome of reading one ``logs/run_state/<date>.json`` file (T-03).

    ``status`` is one of ``pending``/``closed`` when the file is readable and
    carries a recognized run-level status, or ``missing``/``unreadable``/
    ``unknown`` otherwise. ``sources`` is the parsed per-source mapping (empty
    when unavailable) and ``error`` carries a readable detail for the
    unreadable case. An invalid ``run_date`` is normalized to ``None``.
    """

    path: str | None
    status: str
    run_date: str | None
    started_at: str | None
    finished_at: str | None
    sources: dict[str, object]
    error: str | None


@dataclass(frozen=True)
class TruncatedRun:
    """One run that needs data recovery, with its evidence and state signals.

    ``run_date`` is the date from the log block (or from the state file for a
    state-only run). ``log_path``/``summary`` are ``None`` for a state-only
    run; ``started_at`` falls back to the state's own value in that case.
    ``log_completed`` is always ``False`` in returned records: a log with
    ``Fin pipeline`` is never returned (see the module docstring).
    ``superseded`` is ``True`` when a later block of the same date finished:
    the run is still truncated, but its data may or may not have been covered
    by that later run (T-06/T-10 decide). ``state_attributed`` is whether the
    per-date state file describes this block; when it is ``False`` the state
    belongs to another run and never suppresses the truncated block.
    ``reason`` is the Spanish operator-facing explanation.
    """

    run_date: str | None
    log_path: str | None
    started_at: str | None
    log_completed: bool
    superseded: bool
    state_attributed: bool
    summary: run_evidence.PipelineSummary | None
    state: RunStateInfo
    reason: str


def read_run_state(path: Path) -> RunStateInfo:
    """Read a T-03 state file without ever raising.

    Missing files yield ``STATE_MISSING``; OSError/JSON errors and non-object
    JSON yield ``STATE_UNREADABLE``; a readable object whose ``status`` is
    neither ``pending`` nor ``closed`` yields ``STATE_UNKNOWN``. Extra or
    unusual keys are ignored, and an invalid ``run_date`` becomes ``None``.
    """
    path = Path(path)
    if not path.is_file():
        return RunStateInfo(
            path=str(path),
            status=STATE_MISSING,
            run_date=None,
            started_at=None,
            finished_at=None,
            sources={},
            error=None,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return RunStateInfo(
            path=str(path),
            status=STATE_UNREADABLE,
            run_date=None,
            started_at=None,
            finished_at=None,
            sources={},
            error=str(exc),
        )
    if not isinstance(payload, dict):
        return RunStateInfo(
            path=str(path),
            status=STATE_UNREADABLE,
            run_date=None,
            started_at=None,
            finished_at=None,
            sources={},
            error="el JSON no es un objeto",
        )

    raw_status = payload.get("status")
    status = raw_status if raw_status in (STATE_PENDING, STATE_CLOSED) else STATE_UNKNOWN

    def _text(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) and value else None

    run_date = _text("run_date")
    if not _is_valid_date(run_date):
        run_date = None
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        sources = {}
    return RunStateInfo(
        path=str(path),
        status=status,
        run_date=run_date,
        started_at=_text("started_at"),
        finished_at=_text("finished_at"),
        sources=sources,
        error=None,
    )


def _state_attributed_to_block(
    state: RunStateInfo,
    block: run_evidence.PipelineSummary,
    blocks: list[run_evidence.PipelineSummary],
) -> bool:
    """Whether the per-date state file describes ``block``.

    With a single block for the date the state is always attributed to it.
    With several blocks the state's ``started_at`` must fall inside the
    block's tolerance-adjusted time window: ``[Inicio - 5 s, next Inicio -
    5 s)``, or up to the block's own end / one day for the last one. The
    tolerance covers the pipeline capturing ``$StartTime`` a few lines before
    its ``Inicio`` line, so the later run's state is never attributed to the
    earlier truncated block. A state without a parseable ``started_at``
    cannot be attributed.
    """
    if len(blocks) == 1:
        return True
    state_start = _parse_local(state.started_at)
    block_start = _parse_local(block.started_at)
    if state_start is None or block_start is None:
        return False
    lower = block_start - _STATE_START_TOLERANCE
    later_starts = [
        parsed
        for parsed in (_parse_local(other.started_at) for other in blocks)
        if parsed is not None and parsed > block_start
    ]
    if later_starts:
        return lower <= state_start < min(later_starts) - _STATE_START_TOLERANCE
    end = _parse_local(block.finished_at) or block_start + timedelta(days=1)
    return lower <= state_start <= end


def _block_reason(state: RunStateInfo, attributed: bool, superseded: bool) -> str:
    if state.status == STATE_MISSING:
        reason = "el log no tiene 'Fin pipeline' (sin fichero de estado)"
    elif state.status == STATE_UNREADABLE:
        detail = f": {state.error}" if state.error else ""
        reason = f"el log no tiene 'Fin pipeline' (estado ilegible{detail})"
    elif not attributed:
        reason = (
            "el log no tiene 'Fin pipeline' "
            f"(el estado de la fecha es de otro run: {state.status})"
        )
    elif state.status == STATE_PENDING:
        reason = "el log no tiene 'Fin pipeline' y el estado sigue pending"
    else:
        reason = "el log no tiene 'Fin pipeline' (estado sin status reconocible)"
    if superseded:
        reason += "; hay un bloque posterior terminado el mismo dia"
    return reason


def discover_truncated_runs(
    logs_dir: Path, state_dir: Path | None = None
) -> list[TruncatedRun]:
    """Return every run that needs data recovery, sorted ascending.

    ``state_dir`` defaults to ``logs_dir/run_state``. All log blocks without
    ``Fin pipeline`` are returned, including ones superseded by a later
    finished block the same date (``superseded=True``). A ``Fin pipeline``
    block is never returned, and a ``closed`` state only excludes the block it
    can be attributed to. A ``pending`` state without any log block for its
    date is returned with ``summary=None``.
    """
    logs = Path(logs_dir)
    states = Path(state_dir) if state_dir is not None else logs / "run_state"

    blocks_by_date: dict[str, list[run_evidence.PipelineSummary]] = {}
    for summary in run_evidence.discover_runs(logs):
        if summary.date:
            blocks_by_date.setdefault(summary.date, []).append(summary)
    # Chronological order by start inside each date: "later block" means
    # "started later", regardless of when each one finished.
    for date_blocks in blocks_by_date.values():
        date_blocks.sort(key=lambda block: _parse_local(block.started_at) or datetime.min)

    truncated: list[TruncatedRun] = []
    for run_date in sorted(blocks_by_date):
        blocks = blocks_by_date[run_date]
        state = read_run_state(states / f"{run_date}.json")
        for index, block in enumerate(blocks):
            if block.completed:
                continue
            attributed = _state_attributed_to_block(state, block, blocks)
            if attributed and state.status == STATE_CLOSED:
                # Closed state of this very block: re-recovering could
                # duplicate uploads (RF-3 idempotency).
                continue
            superseded = any(later.completed for later in blocks[index + 1:])
            truncated.append(
                TruncatedRun(
                    run_date=run_date,
                    log_path=block.log_path,
                    started_at=block.started_at or state.started_at,
                    log_completed=False,
                    superseded=superseded,
                    state_attributed=attributed,
                    summary=block,
                    state=state,
                    reason=_block_reason(state, attributed, superseded),
                )
            )

    # State-only pending runs: durable signal without a log block for the date.
    for path in sorted(states.glob("*.json")):
        file_date = path.stem
        if not _is_valid_date(file_date) or file_date in blocks_by_date:
            continue
        state = read_run_state(path)
        if state.status != STATE_PENDING:
            continue
        run_date = state.run_date if _is_valid_date(state.run_date) else file_date
        truncated.append(
            TruncatedRun(
                run_date=run_date,
                log_path=None,
                started_at=state.started_at,
                log_completed=False,
                superseded=False,
                state_attributed=True,
                summary=None,
                state=state,
                reason="estado pending sin bloque de log para esa fecha",
            )
        )

    truncated.sort(key=lambda run: (run.run_date or "", run.started_at or ""))
    return truncated


# --------------------------------------------------------------------------
# Recovery plan (T-06; RF-2, RF-3)
# --------------------------------------------------------------------------

# Plan item modes.
PLAN_MODE_FILE = "file"  # publish a dated/canonical parquet as-is
PLAN_MODE_DELTA = "delta"  # publish a cumulative snapshot minus uploaded keys
PLAN_MODE_MERGE = "merge"  # rebuild jobs_unified.parquet with merge.py
PLAN_MODE_NONE = "none"  # not recoverable

# The four logical scrapers of config.ps1, in its order.
PLAN_SOURCE_IDS: tuple[str, ...] = ("indeed", "linkedin", "multi_site", "infojobs")

_INDEED_RE = re.compile(r"^indeed_jobs_(\d{8})_(\d{4,6})\.parquet$")
_INFOJOBS_RE = re.compile(r"^offers_(\d{8})_(\d{4,6})\.parquet$")
_MULTI_DATED_RE = re.compile(r"^jobs_unified_(\d{8})_(\d{4,6})\.parquet$")

def _default_plan_fields(source_id: str) -> dict[str, object]:
    """Operational defaults for one plan source.

    The catalog has no ``multi_site`` id (it is represented by its six
    portals), so Multi-site mirrors the shared contract of its portals; the
    rest mirror ``config.ps1``: ``CoherenceSite``/``FingerprintSource`` are the
    scraper name, ``KeyColumn`` is the dedup key and ``OnlyNewOffers`` is true
    for LinkedIn and Multi-site.
    """
    reference = sources.MULTI_SITE_SITES[0] if source_id == "multi_site" else source_id
    return {
        "required_cols": sources.required_columns(reference),
        "coherence": source_id,
        "fingerprint_source": source_id,
        "key_column": sources.dedup_key(reference),
        "only_new": source_id in ("linkedin", "multi_site"),
    }


_DEFAULT_PLAN_CONFIG: dict[str, dict[str, object]] = {
    source_id: _default_plan_fields(source_id) for source_id in PLAN_SOURCE_IDS
}


@dataclass(frozen=True)
class RecoveryItem:
    """One source's recovery item inside a plan (T-06; RF-2, RF-3).

    ``recoverable`` is the decision; ``mode`` is one of ``PLAN_MODE_FILE``
    (publish ``path``), ``PLAN_MODE_DELTA`` (publish ``path`` minus the keys
    already in ``uploaded_keys``), ``PLAN_MODE_MERGE`` (reconstruction: run
    ``merge.py --since merge_since`` over ``inputs`` and then publish the
    result) or ``PLAN_MODE_NONE``. ``day`` is the real data day
    (``YYYY-MM-DD``), never the recovery day. The executor passes
    ``required_cols``/``coherence``/``fingerprint_source`` to
    ``ensure_compatible.py`` and uses ``key_column``/``only_new`` for the
    delta filter. ``new_keys`` is the measured delta for file/delta items
    (``None`` for reconstructions, where it is only known after merging).
    ``published_key`` is the resolved landing key when ``apply_idempotence``
    found the file already published (``None`` otherwise). ``reason`` is the
    Spanish operator-facing explanation (empty when recoverable).
    """

    source: str
    recoverable: bool
    mode: str
    path: str | None
    day: str | None
    required_cols: tuple[str, ...]
    coherence: str
    fingerprint_source: str
    key_column: str
    only_new: bool
    reconstruct: bool
    merge_since: str | None
    inputs: tuple[str, ...]
    new_keys: int | None
    published_key: str | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready dictionary (T-11 ``-PlanJson``)."""
        return {
            "source": self.source,
            "recoverable": self.recoverable,
            "mode": self.mode,
            "path": self.path,
            "day": self.day,
            "required_cols": list(self.required_cols),
            "coherence": self.coherence,
            "fingerprint_source": self.fingerprint_source,
            "key_column": self.key_column,
            "only_new": self.only_new,
            "reconstruct": self.reconstruct,
            "merge_since": self.merge_since,
            "inputs": list(self.inputs),
            "new_keys": self.new_keys,
            "published_key": self.published_key,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RecoveryPlan:
    """The recovery plan of one run date (T-06; RF-2, RF-3)."""

    run_date: str
    items: tuple[RecoveryItem, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready dictionary (T-11 ``-PlanJson``)."""
        return {
            "schema_version": 1,
            "run_date": self.run_date,
            "items": [item.to_dict() for item in self.items],
        }


def _resolve_plan_config(
    config: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, dict[str, object]]:
    """Merge the caller's per-source overrides over the operational defaults."""
    resolved: dict[str, dict[str, object]] = {}
    for source_id in PLAN_SOURCE_IDS:
        base = dict(_DEFAULT_PLAN_CONFIG[source_id])
        override = (config or {}).get(source_id)
        if override:
            base.update(override)
        resolved[source_id] = base
    return resolved


def _coerce_dt(value: str | datetime) -> datetime:
    """Parse an ISO string or normalize a datetime to naive local time."""
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = _parse_local(value)
        if parsed is None:
            raise ValueError(f"timestamp invalido: {value!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _item(
    source: str,
    cfg: Mapping[str, object],
    *,
    recoverable: bool,
    mode: str,
    path: Path | None = None,
    day: str | None = None,
    reason: str = "",
    reconstruct: bool = False,
    merge_since: str | None = None,
    inputs: list[Path] | None = None,
    new_keys: int | None = None,
    published_key: str | None = None,
) -> RecoveryItem:
    """Build a plan item from the resolved per-source config."""
    return RecoveryItem(
        source=source,
        recoverable=recoverable,
        mode=mode,
        path=str(path) if path is not None else None,
        day=day,
        required_cols=tuple(cfg["required_cols"]),  # type: ignore[arg-type]
        coherence=str(cfg["coherence"]),
        fingerprint_source=str(cfg["fingerprint_source"]),
        key_column=str(cfg["key_column"]),
        only_new=bool(cfg["only_new"]),
        reconstruct=reconstruct,
        merge_since=merge_since,
        inputs=tuple(str(item) for item in (inputs or [])),
        new_keys=new_keys,
        published_key=published_key,
        reason=reason,
    )


def _select_latest_for_day(
    directory: Path, pattern: re.Pattern[str], run_date: str
) -> Path | None:
    """Newest dated parquet of ``run_date`` by the timestamp in its name."""
    if not directory.is_dir():
        return None
    compact = run_date.replace("-", "")
    best: Path | None = None
    best_stamp = ""
    for path in directory.iterdir():
        match = pattern.match(path.name)
        if not match or match.group(1) != compact:
            continue
        stamp = match.group(2).ljust(6, "0")
        if best is None or stamp > best_stamp:
            best, best_stamp = path, stamp
    return best


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _mtime_day(path: Path) -> str | None:
    modified = _mtime(path)
    return modified.date().isoformat() if modified is not None else None


def _uploaded_keys_path(projects_root: Path, source_id: str) -> Path:
    return projects_root / "scrapers-pipeline" / "uploaded_keys" / f"{source_id}.json"


def _load_uploaded_keys(path: Path) -> set[str]:
    """Read the ``{"keys": [...]}`` state file; missing/unreadable means empty."""
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    keys = payload.get("keys") if isinstance(payload, dict) else None
    if not isinstance(keys, list):
        return set()
    return {str(key).strip() for key in keys if str(key).strip()}


def _count_new_keys(path: Path, key_column: str, known: set[str]) -> int | None:
    """Count non-empty keys of ``key_column`` missing from ``known``.

    Returns ``None`` when the parquet cannot be read or lacks the key column,
    so the caller reports the source as not recoverable instead of proposing
    a file that cannot be filtered.
    """
    try:
        table = pq.read_table(str(path), columns=[key_column])
    except Exception:  # noqa: BLE001 - pyarrow raises several exception types
        return None
    if key_column not in table.schema.names:
        return None
    count = 0
    for value in table.column(key_column).to_pylist():
        text = str(value).strip() if value is not None else ""
        if text and text not in known:
            count += 1
    return count


def _plan_indeed(projects_root: Path, run_date: str, cfg: Mapping[str, object]) -> RecoveryItem:
    selected = _select_latest_for_day(
        projects_root / "indeed_jobs_scraper" / "output", _INDEED_RE, run_date
    )
    if selected is None:
        return _item(
            "indeed",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="no hay parquet de Indeed con la fecha del run",
        )
    return _item(
        "indeed", cfg, recoverable=True, mode=PLAN_MODE_FILE, path=selected, day=run_date
    )


def _plan_infojobs(projects_root: Path, run_date: str, cfg: Mapping[str, object]) -> RecoveryItem:
    selected = _select_latest_for_day(
        projects_root / "infojobs_jobs_scraper" / "data", _INFOJOBS_RE, run_date
    )
    if selected is None:
        return _item(
            "infojobs",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason=(
                "no hay parquet de InfoJobs con la fecha del run "
                "(el scraper no llego a generar datos)"
            ),
        )
    return _item(
        "infojobs", cfg, recoverable=True, mode=PLAN_MODE_FILE, path=selected, day=run_date
    )


def _plan_linkedin(projects_root: Path, run_date: str, cfg: Mapping[str, object]) -> RecoveryItem:
    snapshot = projects_root / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    if not snapshot.is_file():
        return _item(
            "linkedin",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="no existe el snapshot de LinkedIn",
        )
    known = _load_uploaded_keys(_uploaded_keys_path(projects_root, "linkedin"))
    new_keys = _count_new_keys(snapshot, str(cfg["key_column"]), known)
    if new_keys is None:
        return _item(
            "linkedin",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="no se pudo leer el snapshot de LinkedIn o falta su columna clave",
        )
    if new_keys == 0:
        return _item(
            "linkedin",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            day=run_date,
            reason="sin ofertas nuevas (todas ya estan en uploaded_keys)",
        )
    return _item(
        "linkedin",
        cfg,
        recoverable=True,
        mode=PLAN_MODE_DELTA,
        path=snapshot,
        day=run_date,
        new_keys=new_keys,
    )


def _multi_site_file_item(
    cfg: Mapping[str, object],
    path: Path,
    run_date: str,
    known: set[str],
) -> RecoveryItem:
    new_keys = _count_new_keys(path, str(cfg["key_column"]), known)
    if new_keys is None:
        return _item(
            "multi_site",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="no se pudo leer el snapshot de Multi-site o falta su columna clave",
        )
    if new_keys == 0:
        return _item(
            "multi_site",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            day=run_date,
            reason="sin ofertas nuevas (todas ya estan en uploaded_keys)",
        )
    return _item(
        "multi_site",
        cfg,
        recoverable=True,
        mode=PLAN_MODE_DELTA,
        path=path,
        day=run_date,
        new_keys=new_keys,
    )


def _plan_multi_site(
    projects_root: Path,
    run_date: str,
    cfg: Mapping[str, object],
    run_start: datetime,
    run_end: datetime,
) -> RecoveryItem:
    merged_dir = projects_root / "multi_site_job_scraper" / "data" / "merged"
    known = _load_uploaded_keys(_uploaded_keys_path(projects_root, "multi_site"))

    # 1) Dated merged parquet of the run date (name timestamp, not mtime).
    dated = _select_latest_for_day(merged_dir, _MULTI_DATED_RE, run_date)
    if dated is not None:
        return _multi_site_file_item(cfg, dated, run_date, known)

    # 2) Canonical only when its mtime falls on the run day: a snapshot from a
    # later run must never be attributed to this one.
    canonical = merged_dir / "jobs_unified.parquet"
    if canonical.is_file() and _mtime_day(canonical) == run_date:
        return _multi_site_file_item(cfg, canonical, run_date, known)

    # 3) Reconstruction from the portal outputs of the run.
    inputs: list[Path] = []
    newest: datetime | None = None
    for portal in sources.MULTI_SITE_SITES:
        csv_path = (
            projects_root / "multi_site_job_scraper" / "data" / portal / "output" / "jobs.csv"
        )
        if not csv_path.is_file():
            continue
        modified = _mtime(csv_path)
        if modified is None:
            continue
        if newest is None or modified > newest:
            newest = modified
        if run_start <= modified <= run_end:
            inputs.append(csv_path)
    if not inputs and newest is None:
        return _item(
            "multi_site",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="no hay salidas por portal para reconstruir Multi-site",
        )
    if newest is not None and newest > run_end:
        return _item(
            "multi_site",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason=(
                "salidas por portal posteriores al final del run "
                "(son de otro run): no se reconstruye ni se inventan datos"
            ),
        )
    if not inputs:
        return _item(
            "multi_site",
            cfg,
            recoverable=False,
            mode=PLAN_MODE_NONE,
            reason="las salidas por portal no pertenecen al run: no se reconstruye",
        )
    return _item(
        "multi_site",
        cfg,
        recoverable=True,
        mode=PLAN_MODE_MERGE,
        day=run_date,
        reconstruct=True,
        merge_since=run_start.isoformat(timespec="seconds"),
        inputs=inputs,
    )


def build_plan(
    run_date: str,
    projects_root: Path,
    config: Mapping[str, Mapping[str, object]] | None = None,
    *,
    run_start: str | datetime | None = None,
    run_end: str | datetime | None = None,
) -> RecoveryPlan:
    """Build the recovery plan of ``run_date`` for the four logical scrapers.

    ``projects_root`` is the workspace root (the parent of
    ``scrapers-pipeline``), the same reference ``run_evidence`` uses. ``config``
    optionally overrides per-source fields (``required_cols``, ``coherence``,
    ``fingerprint_source``, ``key_column``, ``only_new``); unspecified fields
    keep the operational defaults. ``run_start``/``run_end`` bound the run
    window used to accept Multi-site portal outputs; they default to the whole
    ``run_date``. Raises ``ValueError`` for an invalid ``run_date``.
    """
    if not _is_valid_date(run_date):
        raise ValueError(f"run_date invalido: {run_date!r}; se espera YYYY-MM-DD")
    root = Path(projects_root)
    resolved = _resolve_plan_config(config)
    start = _coerce_dt(run_start) if run_start is not None else datetime.combine(
        date.fromisoformat(run_date), time.min
    )
    end = _coerce_dt(run_end) if run_end is not None else datetime.combine(
        date.fromisoformat(run_date), time.max
    )
    return RecoveryPlan(
        run_date=run_date,
        items=(
            _plan_indeed(root, run_date, resolved["indeed"]),
            _plan_linkedin(root, run_date, resolved["linkedin"]),
            _plan_multi_site(root, run_date, resolved["multi_site"], start, end),
            _plan_infojobs(root, run_date, resolved["infojobs"]),
        ),
    )


# --------------------------------------------------------------------------
# Idempotence (T-07; RF-3)
# --------------------------------------------------------------------------


def _expected_published_key(item: RecoveryItem) -> str | None:
    """Landing key a ``file`` item would publish (``None`` for other modes).

    The pipeline uploads to ``<source>/dia=<day>/<file>`` with
    ``--as-subdir=false``, so the local file name is the remote one.
    """
    if item.mode != PLAN_MODE_FILE or not item.path or not item.day:
        return None
    name = PurePosixPath(item.path.replace("\\", "/")).name
    if not name:
        return None
    return f"{item.source}/dia={item.day}/{name}"


def apply_idempotence(
    plan: RecoveryPlan, reader: landing.RemoteReader
) -> RecoveryPlan:
    """Return a new plan with the already-published work omitted (RF-3).

    ``file`` items are checked against the landing with
    :func:`verification.landing.resolve_published_key` on their expected key
    ``<source>/dia=<day>/<file>``: when the key resolves (exact or unique
    staging-folder match) the item is returned as non-recoverable with
    ``published_key`` and a Spanish reason; an absent or ambiguous key leaves
    the item recoverable (never guess). ``delta``/``merge`` items cannot be
    checked by key: when ``new_keys`` is 0 they are omitted, otherwise they
    stay and the execution-time filter against ``uploaded_keys`` (T-11) keeps
    the upload idempotent.

    The input plan is never modified; non-recoverable items pass through
    untouched, so calling the function twice with the same reader yields the
    same plan. A :class:`verification.landing.RemoteError` propagates
    unchanged: a connectivity failure is never treated as "absent" and no
    partially updated plan is returned.
    """
    updated: list[RecoveryItem] = []
    for item in plan.items:
        if not item.recoverable:
            updated.append(item)
            continue
        expected = _expected_published_key(item)
        if expected is not None:
            published = landing.resolve_published_key(reader, expected)
            if published is not None:
                updated.append(
                    replace(
                        item,
                        recoverable=False,
                        published_key=published,
                        reason=f"ya publicado en la landing: {published}",
                    )
                )
                continue
        if item.mode in (PLAN_MODE_DELTA, PLAN_MODE_MERGE) and item.new_keys == 0:
            updated.append(
                replace(
                    item,
                    recoverable=False,
                    reason="sin ofertas nuevas (nada que publicar en la recuperacion)",
                )
            )
            continue
        updated.append(item)
    return RecoveryPlan(run_date=plan.run_date, items=tuple(updated))


def apply_state_idempotence(
    plan: RecoveryPlan, state: RunStateInfo
) -> RecoveryPlan:
    """Omit items the run state already confirms as published (RF-3; T-11).

    Complements the remote check of :func:`apply_idempotence` with local
    evidence: when ``state.sources[<source>]`` carries a valid status
    (``ok``/``partial``) and ``uploaded > 0``, the run already published that
    source's data, so the item is omitted even if ``uploaded_keys`` was left
    stale by the abort. Missing, unreadable or unrecognized states never omit
    anything, and the input plan is not modified.
    """
    updated: list[RecoveryItem] = []
    for item in plan.items:
        if not item.recoverable:
            updated.append(item)
            continue
        source_state = state.sources.get(item.source)
        if isinstance(source_state, dict):
            status = str(source_state.get("status") or "")
            uploaded = source_state.get("uploaded")
            if (
                status in VALID_PUBLISHED_STATUSES
                and isinstance(uploaded, (int, float))
                and not isinstance(uploaded, bool)
                and int(uploaded) > 0
            ):
                updated.append(
                    replace(
                        item,
                        recoverable=False,
                        reason=(
                            "ya publicado en el run "
                            f"(estado: status={status}, uploaded={int(uploaded)})"
                        ),
                    )
                )
                continue
        updated.append(item)
    return RecoveryPlan(run_date=plan.run_date, items=tuple(updated))


# --------------------------------------------------------------------------
# _READY decision and closing block (T-08; RF-5, RF-6)
# --------------------------------------------------------------------------

# Machine statuses the pipeline writes per source (run_evidence parses them).
PUBLISHED_STATUSES: tuple[str, ...] = ("ok", "partial", "failed", "no_data", "killed")
# Statuses that mean the source really published valid data.
VALID_PUBLISHED_STATUSES: tuple[str, ...] = ("ok", "partial")

READY_POLICIES: tuple[str, ...] = ("any_valid", "all")

_SOURCE_RE = re.compile(r"^\w+$")


@dataclass(frozen=True)
class PublishedSource:
    """Publication outcome of one source, as the pipeline records it (T-08).

    ``status`` is the pipeline's machine value: ``ok``/``partial`` mean valid
    data was published; ``failed``/``no_data``/``killed`` mean it was not.
    ``uploaded``/``rejected`` are the counters of the pipeline's summary line.
    """

    source: str
    status: str
    uploaded: int = 0
    rejected: int = 0


@dataclass(frozen=True)
class ReadyDecision:
    """Whether ``_READY`` must be written, with the operator reason (T-08).

    ``policy`` is the policy actually applied (an unknown request falls back
    to ``all``, as the pipeline does) and ``valid_sources`` is how many
    entries of ``published`` carried valid data.
    """

    write: bool
    policy: str
    valid_sources: int
    reason: str


def decide_ready(
    policy: str, published: Sequence[PublishedSource]
) -> ReadyDecision:
    """Decide whether ``_READY`` may be written (RF-6; T-08).

    ``published`` must list every source the run was supposed to process,
    including the ones that failed (the pipeline's ``PublishResults``); a
    valid source is one whose status is ``ok`` or ``partial``. ``any_valid``
    writes when at least one source is valid; ``all`` writes only when every
    listed source is valid and there is at least one (a recovery with nothing
    published must never signal ``_READY``). An unknown policy behaves like
    ``all``, matching the pipeline, and the Spanish reason records that
    fallback. Pure: no network and no file is written (T-12 does that).
    """
    results = list(published)
    valid = sum(1 for result in results if result.status in VALID_PUBLISHED_STATUSES)
    requested = policy if policy in READY_POLICIES else "all"
    fallback = (
        f"politica desconocida ('{policy}'): se aplica '{requested}'. "
        if requested != policy
        else ""
    )

    if requested == "any_valid":
        write = valid > 0
        if valid == 1:
            detail = "se publico 1 fuente con datos validos"
        elif valid > 1:
            detail = f"se publicaron {valid} fuentes con datos validos"
        else:
            detail = "ningun dato valido publicado"
    elif not results:
        write = False
        detail = "no hay fuentes publicadas: la politica all no puede cumplirse"
    else:
        write = valid == len(results)
        if write:
            detail = (
                "todas las fuentes previstas publicaron datos validos "
                f"({valid}/{len(results)})"
            )
        else:
            detail = (
                "la politica all exige que todas las fuentes publiquen; "
                f"validas {valid} de {len(results)}"
            )
    return ReadyDecision(
        write=write, policy=requested, valid_sources=valid, reason=fallback + detail
    )


def closing_block(
    published: Sequence[PublishedSource],
    *,
    failures: int,
    duration_seconds: int,
    finished_at: datetime | str | None = None,
) -> tuple[str, ...]:
    """Return the pipeline's closing lines for a recovered run (RF-5; T-08).

    The first line is exactly ``====  Fin pipeline. Fallos: N  Duracion: Ds
    ====`` and each source adds ``  [source] status=... subidos=N
    rechazados=N``, prefixed with the ``HH:mm:ss  [INFO]  `` stamp the
    pipeline writes, so appending these lines to a truncated general log makes
    ``run_evidence`` parse the block as completed and the diagnostic select
    the run. ``finished_at`` is the closing instant (defaults to now); it must
    be inside the run window so ``finished_at`` is parsed without a day shift.
    Statuses are the pipeline's machine values; an unknown status or a source
    name the parser cannot match raises ``ValueError``. The lines carry no
    newline; the caller writes them.
    """
    moment = (
        _coerce_dt(finished_at) if finished_at is not None else datetime.now()
    )
    for result in published:
        if not _SOURCE_RE.match(result.source):
            raise ValueError(
                f"nombre de fuente invalido para el cierre: {result.source!r}"
            )
        if result.status not in PUBLISHED_STATUSES:
            raise ValueError(
                f"status invalido para el cierre de {result.source!r}: {result.status!r}"
            )

    stamp = moment.strftime("%H:%M:%S")
    lines = [
        f"{stamp}  [INFO]  ====  Fin pipeline. Fallos: {int(failures)}  "
        f"Duracion: {int(duration_seconds)}s ===="
    ]
    for result in published:
        lines.append(
            f"{stamp}  [INFO]    [{result.source}] status={result.status} "
            f"subidos={int(result.uploaded)} rechazados={int(result.rejected)}"
        )
    return tuple(lines)


# --------------------------------------------------------------------------
# CLI: previous pending runs for the startup reconciliation (T-10; RF-4)
# --------------------------------------------------------------------------


def _truncated_to_dict(run: TruncatedRun) -> dict[str, object]:
    """Stable JSON shape of one truncated run for the PowerShell consumer."""
    return {
        "run_date": run.run_date,
        "log_path": run.log_path,
        "started_at": run.started_at,
        "superseded": run.superseded,
        "state_attributed": run.state_attributed,
        "state_path": run.state.path,
        "state_status": run.state.status,
        "state_run_date": run.state.run_date,
        "reason": run.reason,
    }


def pending_runs(
    logs_dir: Path,
    state_dir: Path | None = None,
    before: str | None = None,
) -> list[TruncatedRun]:
    """Truncated runs strictly before ``before`` (``YYYY-MM-DD``), if given.

    ``before`` excludes the run that is starting; ``None`` returns every
    discovered truncated run. Pure and local: no network and no Azure.
    """
    runs = discover_truncated_runs(logs_dir, state_dir)
    if before is None:
        return runs
    return [run for run in runs if (run.run_date or "") < before]


def _read_results_file(path: Path) -> list[PublishedSource]:
    """Read the executor's per-source results JSON.

    Accepts a bare list or ``{"results": [...]}``; each entry needs at least
    ``source``/``status`` (``uploaded``/``rejected`` default to 0). Raises
    ``ValueError`` (Spanish) for an unreadable or ill-shaped file.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"resultados ilegibles ({path}): {exc}") from exc
    items = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ValueError("los resultados deben ser una lista o {'results': [...]}")
    results: list[PublishedSource] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        results.append(
            PublishedSource(
                source=str(entry.get("source") or ""),
                status=str(entry.get("status") or ""),
                uploaded=int(entry.get("uploaded") or 0),
                rejected=int(entry.get("rejected") or 0),
            )
        )
    return results


def _write_cli_output(text: str, out: str | None) -> None:
    """Write ``text`` to ``out`` (no BOM) or stdout."""
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(out)
    else:
        print(text, end="")


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verification.recovery",
        description="Recuperacion de runs truncados del pipeline (RF-2, RF-4).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    pending = subparsers.add_parser(
        "pending",
        help="lista en JSON los runs truncados/pendientes anteriores",
    )
    pending.add_argument("--logs-dir", default=None)
    pending.add_argument("--state-dir", default=None)
    pending.add_argument(
        "--before",
        default=None,
        help="solo runs con run_date estrictamente anterior (YYYY-MM-DD)",
    )
    plan = subparsers.add_parser(
        "plan",
        help="genera el plan de recuperacion de un run en JSON",
    )
    plan.add_argument("--run-date", required=True)
    plan.add_argument("--projects-root", default=None)
    plan.add_argument("--state-dir", default=None)
    plan.add_argument("--out", default=None, help="fichero JSON de salida")
    closing = subparsers.add_parser(
        "closing",
        help="genera el bloque de cierre del pipeline para el log general",
    )
    closing.add_argument("--results-file", required=True)
    closing.add_argument("--failures", type=int, required=True)
    closing.add_argument("--duration", type=int, required=True)
    closing.add_argument("--finished-at", default=None)
    closing.add_argument("--out", default=None)
    ready = subparsers.add_parser(
        "ready",
        help="decide si procede escribir _READY",
    )
    ready.add_argument("--policy", required=True)
    ready.add_argument("--results-file", required=True)
    ready.add_argument("--out", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``pending``/``plan`` print stable JSON, return 0 or 2."""
    args = _build_cli_parser().parse_args(argv)

    if args.command == "pending":
        if args.before is not None and not _is_valid_date(args.before):
            print(
                f"recovery: --before invalido: {args.before!r}; se espera YYYY-MM-DD",
                file=sys.stderr,
            )
            return 2
        logs_dir = (
            Path(args.logs_dir)
            if args.logs_dir
            else run_evidence.DEFAULT_PROJECTS_ROOT / run_evidence.DEFAULT_LOGS_SUBDIR
        )
        state_dir = Path(args.state_dir) if args.state_dir else None
        runs = pending_runs(logs_dir, state_dir, args.before)
        payload = {
            "schema_version": 1,
            "before": args.before,
            "count": len(runs),
            "runs": [_truncated_to_dict(run) for run in runs],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.command == "plan":
        if not _is_valid_date(args.run_date):
            print(
                f"recovery: --run-date invalido: {args.run_date!r}; "
                "se espera YYYY-MM-DD",
                file=sys.stderr,
            )
            return 2
        projects_root = (
            Path(args.projects_root)
            if args.projects_root
            else run_evidence.DEFAULT_PROJECTS_ROOT
        )
        plan = build_plan(args.run_date, projects_root)
        if args.state_dir:
            state = read_run_state(
                Path(args.state_dir) / f"{args.run_date}.json"
            )
            plan = apply_state_idempotence(plan, state)
        _write_cli_output(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
            args.out,
        )
        return 0

    if args.command == "closing":
        try:
            results = _read_results_file(args.results_file)
            lines = closing_block(
                results,
                failures=args.failures,
                duration_seconds=args.duration,
                finished_at=args.finished_at,
            )
        except ValueError as exc:
            print(f"recovery: {exc}", file=sys.stderr)
            return 2
        _write_cli_output("\n".join(lines) + "\n", args.out)
        return 0

    if args.command == "ready":
        try:
            results = _read_results_file(args.results_file)
        except ValueError as exc:
            print(f"recovery: {exc}", file=sys.stderr)
            return 2
        decision = decide_ready(args.policy, results)
        _write_cli_output(
            json.dumps(
                {
                    "write": decision.write,
                    "policy": decision.policy,
                    "valid_sources": decision.valid_sources,
                    "reason": decision.reason,
                },
                ensure_ascii=False,
            )
            + "\n",
            args.out,
        )
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
