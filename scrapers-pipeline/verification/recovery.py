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
Spanish operator-facing ``reason``. The module is pure: standard library only,
no network and no Azure.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from verification import run_evidence

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
