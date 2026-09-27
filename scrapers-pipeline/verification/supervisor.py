"""Watchdog supervisor for Indeed, LinkedIn and InfoJobs (T-19; RF-12, RF-15).

This module is the *only* place where the keep/blocked decision and the
process-tree stop are wired together. The decision rule itself is pure and
lives in :mod:`verification.progress`; here we:

- track the last observed progress per unit (source + pid + stream file);
- read the last ``PROGRESS`` line from a stream file (``stdout`` of a scraper);
- stop *only* the process tree of the unit declared blocked, recording why;
- expose a thin CLI so the PowerShell wrappers can poll the decision without
  keeping any state in memory between calls.

The wrappers never reimplement the rule: they call the CLI, read ``state=keep``
or ``state=blocked`` and log the result. The CLI persists the little state
needed (last counter value and when it was reached) in a JSON file.

Exit code
---------
``WATCHDOG_EXIT_CODE`` is a dedicated, otherwise unused code so a watchdog stop
is never mistaken for a retryable technical error. Existing wrapper codes are
Indeed ``0/1/2/3/130``, LinkedIn ``0/4`` and InfoJobs ``0/1/2``; ``75``
(``EX_TEMPFAIL``) does not collide with any of them. The wrappers still need
the retry-skip wiring done in T-21..T-23; the point where that must happen is
marked in each wrapper.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from verification.progress import (
    PROGRESS_BLOCKED,
    PROGRESS_KEEP,
    ProgressDecision,
    ProgressEvent,
    evaluate_progress,
    inactivity_timeout,
    parse_progress_event,
)

# Reason recorded in the log and the final marker when the watchdog stops a unit.
WATCHDOG_REASON = "watchdog_no_progress"

# Dedicated exit code for a watchdog stop (does not collide with scraper codes).
WATCHDOG_EXIT_CODE = 75

# How many trailing bytes of the stream are scanned for the last PROGRESS line.
DEFAULT_TAIL_BYTES = 64 * 1024


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def read_last_progress_event(
    path: str | os.PathLike[str], max_bytes: int = DEFAULT_TAIL_BYTES
) -> ProgressEvent | None:
    """Return the last usable ``PROGRESS`` event of a stream file, or ``None``.

    Tolerant to a missing, empty or still-being-written file: only the trailing
    ``max_bytes`` are read and a partial last line simply yields no event, so an
    earlier complete line is used instead.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - max_bytes)
            handle.seek(start)
            data = handle.read()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return None

    for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
        event = parse_progress_event(line)
        if event is not None:
            return event
    return None


class ProgressTracker:
    """Per-source tracker around the pure inactivity rule.

    ``last_parsed``/``last_progress_at`` are the last observed improvement (the
    counter value and when it was reached). ``started_at`` anchors the clock
    before the first event is seen, so a scraper that never prints a
    ``PROGRESS`` line is still declared blocked after its timeout.
    """

    def __init__(self, source: str, started_at: datetime | None = None):
        self.source = source
        self.last_parsed = 0
        self.last_progress_at = started_at

    def observe(
        self, event: ProgressEvent | None, now: datetime
    ) -> ProgressDecision:
        """Evaluate one observation and advance the tracker state."""
        if event is not None and event.source != self.source:
            # A line from another unit must never advance this counter.
            event = None
        if event is None:
            # No usable line yet: evaluate against the counter we already have.
            event = ProgressEvent(source=self.source, parsed=self.last_parsed)
        if self.last_progress_at is None:
            # First observation anchors the clock at the event time (or now).
            self.last_progress_at = event.at if event.at is not None else now

        decision = evaluate_progress(
            last_parsed=self.last_parsed,
            parsed_at=self.last_progress_at,
            event=event,
            now=now,
            timeout=inactivity_timeout(self.source),
        )
        self.last_parsed = decision.last_parsed
        self.last_progress_at = decision.last_progress_at
        return decision


# --------------------------------------------------------------------------
# Process-tree stop (injectable)
# --------------------------------------------------------------------------


def _noop_stop(pid: int) -> bool:  # pragma: no cover - trivial
    """Placeholder used by ``--no-stop`` so no process tree is signalled."""
    return False


def stop_process_tree(pid: int) -> bool:
    """Stop ``pid`` and its children. Returns ``True`` when it was signalled.

    On Windows ``Stop-Process``/``os.kill`` only affects one PID, so ``taskkill
    /T /F`` is required to take down the whole Python/Chrome tree. On POSIX the
    process group is killed as a reasonable fallback.
    """
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(int(pid))],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return False
        return completed.returncode == 0

    # POSIX fallback (never taken on the Windows target). ``getattr`` keeps the
    # Windows typeshed, where these attributes do not exist, happy.
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    if killpg is not None and getpgid is not None:
        try:
            killpg(getpgid(int(pid)), sigkill)
            return True
        except ProcessLookupError:
            return True
        except OSError:
            pass
    try:
        os.kill(int(pid), sigkill)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StopRecord:
    """Audit entry written when the watchdog stops a unit."""

    source: str
    pid: int
    reason: str
    at: datetime
    message: str


@dataclass
class SupervisionUnit:
    """One supervised scraping unit: a source, its process and its stream."""

    source: str
    pid: int
    stream: str
    tracker: ProgressTracker
    stopped: bool = False
    stop_reason: str | None = None
    last_decision: ProgressDecision | None = None


class Supervisor:
    """Keep/blocked decisions for several units, stopping only the blocked one.

    ``stop`` is injectable so tests can observe the exact pids without killing
    real processes; it defaults to :func:`stop_process_tree`.
    """

    def __init__(
        self,
        stop=None,
        clock=None,
        logger=None,
    ):
        self._stop = stop if stop is not None else stop_process_tree
        self._clock = clock if clock is not None else datetime.now
        self._logger = logger
        self._units: dict[str, SupervisionUnit] = {}
        self.stops: list[StopRecord] = []

    @property
    def units(self) -> dict[str, SupervisionUnit]:
        """Registered units, keyed by source id."""
        return self._units

    def register(
        self,
        source: str,
        pid: int,
        stream: str | os.PathLike[str],
        started_at: datetime | None = None,
        tracker: ProgressTracker | None = None,
    ) -> SupervisionUnit:
        """Register (or replace) the unit of ``source``."""
        unit = SupervisionUnit(
            source=source,
            pid=int(pid),
            stream=str(stream),
            tracker=tracker if tracker is not None else ProgressTracker(source, started_at),
        )
        self._units[source] = unit
        return unit

    def poll(self, now: datetime | None = None) -> dict[str, ProgressDecision]:
        """Evaluate every unit once; stop only the units declared blocked."""
        moment = now if now is not None else self._clock()
        decisions: dict[str, ProgressDecision] = {}
        for source, unit in self._units.items():
            if unit.stopped:
                if unit.last_decision is not None:
                    decisions[source] = unit.last_decision
                continue

            event = read_last_progress_event(unit.stream)
            decision = unit.tracker.observe(event, moment)
            unit.last_decision = decision
            decisions[source] = decision

            if decision.state == PROGRESS_BLOCKED:
                self._halt(unit, moment)
        return decisions

    def _halt(self, unit: SupervisionUnit, at: datetime) -> None:
        """Stop only ``unit``'s process tree and record the watchdog reason."""
        message = (
            "Watchdog: sin progreso durante el periodo de inactividad; "
            f"detenido el arbol de procesos de {unit.source} (pid={unit.pid})."
        )
        try:
            self._stop(unit.pid)
        except Exception:  # pragma: no cover - defensive: never break the poll
            pass
        unit.stopped = True
        unit.stop_reason = WATCHDOG_REASON
        record = StopRecord(
            source=unit.source,
            pid=unit.pid,
            reason=WATCHDOG_REASON,
            at=at,
            message=message,
        )
        self.stops.append(record)
        if self._logger is not None:
            self._logger(record)


# --------------------------------------------------------------------------
# Thin CLI used by the PowerShell wrappers
# --------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: str | None) -> datetime | None:
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


def load_state(path: str | os.PathLike[str]) -> dict:
    """Read the JSON state file, or an empty dict when absent/unreadable.

    ``utf-8-sig`` transparently skips a BOM so a state written by another tool
    is still readable.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _state_matches_run(
    state: dict,
    source: str,
    pid: int,
    started_at: datetime | None,
) -> bool:
    """True only when ``state`` belongs to this exact process of this run.

    A watchdog state file is deterministic per attempt and survives the night,
    so a new run must discard it unless the pid, the source and the baseline
    agree: a baseline earlier than ``started_at`` belongs to a previous run.
    """
    if not state:
        return False
    if state.get("source") != source or state.get("pid") != pid:
        return False
    baseline = _parse_iso(state.get("last_progress_at"))
    if baseline is None:
        return False
    if started_at is not None and baseline < started_at:
        return False
    return True


def save_state(
    path: str | os.PathLike[str],
    unit: SupervisionUnit,
    stopped: bool | None = None,
) -> None:
    """Persist the minimal state the next CLI invocation needs.

    ``stopped`` overrides ``unit.stopped`` so ``--no-stop`` never persists a
    stop it did not perform (which would leave an orphan process reported as
    halted on the next poll).
    """
    halted = unit.stopped if stopped is None else stopped
    payload = {
        "source": unit.source,
        "pid": unit.pid,
        "stream": unit.stream,
        "last_parsed": unit.tracker.last_parsed,
        "last_progress_at": _iso(unit.tracker.last_progress_at),
        "stopped": halted,
        "stop_reason": (unit.stop_reason if halted else None),
    }
    parent = Path(path).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verification.supervisor",
        description=(
            "Consulta una vez la decision keep/blocked de una unidad de scraping "
            "y detiene su arbol de procesos si esta bloqueada."
        ),
    )
    parser.add_argument("--source", required=True, help="source id (indeed, ...)")
    parser.add_argument("--pid", type=int, required=True, help="scraper root pid")
    parser.add_argument("--stream", required=True, help="stdout log with PROGRESS lines")
    parser.add_argument("--state", required=True, help="JSON state file to persist")
    parser.add_argument(
        "--started-at",
        default=None,
        help="ISO time the process started (anchors inactivity before first event)",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO time to use as 'now' (test seam; defaults to the wall clock)",
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="only report the decision; do not stop the process tree",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Poll once, persist the state and print one PowerShell-friendly line."""
    args = _build_parser().parse_args(argv)

    state = load_state(args.state)
    arg_started_at = _parse_iso(args.started_at)

    same_run = _state_matches_run(state, args.source, args.pid, arg_started_at)
    if not same_run:
        # State from another process or another run (or unreadable): start clean
        # so last night's counter/baseline never blocks a healthy new process.
        state = {}

    # Already halted in this run: report blocked without re-stopping.
    if same_run and state.get("stopped"):
        print(
            "SUPERVISOR source={source} pid={pid} state=blocked reason={reason} "
            "last_parsed={parsed} idle_seconds=0.0 stopped=true".format(
                source=args.source,
                pid=args.pid,
                reason=state.get("stop_reason") or WATCHDOG_REASON,
                parsed=state.get("last_parsed", 0),
            )
        )
        return WATCHDOG_EXIT_CODE

    started_at: datetime | None
    if arg_started_at is not None:
        started_at = arg_started_at
    else:
        started_at = _parse_iso(state.get("last_progress_at"))

    tracker = ProgressTracker(args.source, started_at)
    if same_run:
        last_parsed = state.get("last_parsed")
        if last_parsed is not None:
            try:
                tracker.last_parsed = int(last_parsed)
            except (TypeError, ValueError):
                pass
        restored_at = _parse_iso(state.get("last_progress_at"))
        if restored_at is not None:
            tracker.last_progress_at = restored_at

    stop = _noop_stop if args.no_stop else stop_process_tree
    supervisor = Supervisor(stop=stop)
    unit = supervisor.register(
        args.source, args.pid, args.stream, tracker=tracker
    )

    now = _parse_iso(args.now) or datetime.now()
    decision = supervisor.poll(now=now)[args.source]

    # --no-stop only reports; it must not persist a halt it did not perform.
    did_stop = unit.stopped and not args.no_stop
    save_state(args.state, unit, stopped=did_stop)

    if decision.state == PROGRESS_BLOCKED:
        print(
            "SUPERVISOR source={source} pid={pid} state=blocked reason={reason} "
            "last_parsed={parsed} idle_seconds={idle:.1f} stopped={stopped}".format(
                source=decision.source,
                pid=args.pid,
                reason=WATCHDOG_REASON,
                parsed=decision.last_parsed,
                idle=decision.idle_seconds,
                stopped=str(did_stop).lower(),
            )
        )
        return WATCHDOG_EXIT_CODE

    assert decision.state == PROGRESS_KEEP
    print(
        "SUPERVISOR source={source} pid={pid} state=keep "
        "last_parsed={parsed} idle_seconds={idle:.1f}".format(
            source=decision.source,
            pid=args.pid,
            parsed=decision.last_parsed,
            idle=decision.idle_seconds,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
