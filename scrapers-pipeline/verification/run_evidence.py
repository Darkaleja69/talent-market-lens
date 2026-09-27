"""Local run-evidence collection for the daily diagnostic.

This module gathers the evidence a run left on disk (the general pipeline log,
per-source wrapper logs, ``last_run`` markers and dated artifacts) and turns it
into an analysable picture of the run: which execution to diagnose and what
happened to each of the nine independent sources.

Design rules (RF-1, RF-3, RF-13):

- A run is identified only by its general pipeline log, and only a *finished*
  run (a ``Fin pipeline`` line) is selectable. The mere existence of a log or a
  snapshot is never taken as proof of success.
- A finished run is analysable even when every source produced no data; only
  the impossibility of identifying or reading a run makes the diagnostic
  inconclusive.
- Missing evidence for one source yields ``OUTCOME_NO_EVIDENCE`` for that
  source while the run itself stays analysable (RF-13 applies to the run).
- Offer counts are read from run-scoped evidence only. Accumulated snapshots
  (for example LinkedIn's ``output/jobs.parquet``) are never counted as the
  offers of the run; Parquet files are not read here (that is task group 4).

All paths are resolved relative to the workspace root, the parent of the
``scrapers-pipeline`` directory. Tests inject a temporary root/logs directory.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from verification import sources

# Workspace root: scrapers-pipeline/verification/run_evidence.py -> parents[2].
DEFAULT_PROJECTS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGS_SUBDIR = Path("scrapers-pipeline") / "logs"

# Per-source outcome classification.
OUTCOME_OK = "ok"
OUTCOME_EMPTY = "empty"  # run finished without offers
OUTCOME_ERROR = "error"  # technical failure / non-zero exit
OUTCOME_BLOCKED = "blocked"  # CAPTCHA / anti-bot block
OUTCOME_NO_EVIDENCE = "no_evidence"

_INICIO_RE = re.compile(
    r"====\s*Inicio pipeline scrapers\s*\((\d{4}-\d{2}-\d{2})\)\s*===="
)
_FIN_RE = re.compile(r"====\s*Fin pipeline")
_TIME_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})")
_STATUS_RE = re.compile(
    r"\[(?P<source>\w+)\]\s+status=(?P<status>\w+)\s+"
    r"subidos=(?P<uploaded>\d+)\s+rechazados=(?P<rejected>\d+)"
)
_FAILURES_RE = re.compile(r"Fallos:\s*(\d+)")
_DURATION_RE = re.compile(r"Duracion:\s*(\d+)s")
_ERROR_RE = re.compile(r"\[ERROR\]\s*(.*)")
_WARN_RE = re.compile(r"\[WARN\]\s*(.*)")

_UPLOAD_LOG_RE = re.compile(r"^upload-\d{4}-\d{2}-\d{2}\.log$")
_ATTEMPT_RE = re.compile(r"Intento\s+(\d+)/(\d+)")
_STAMP_RE = re.compile(r"(\d{8})_(\d{4,6})")
_METADATA_JSON_RE = re.compile(r"^scrape_metadata_.*\.json$")
_OFFERS_PARQUET_RE = re.compile(r"^offers_.*\.parquet$")
# Real DONE formats: "DONE: 13 nuevas" and "DONE: 5 ofertas nuevas".
_DONE_RE = re.compile(r"DONE:\s*(\d+)\s+(?:ofertas\s+)?nuevas")
# Start-of-execution banner each portal prints, e.g. "=== Glassdoor USA Scraper ===".
# Both whitespace classes exclude the newline so the banner must sit on a single
# line: a benign later line containing "Scraper" cannot match across a newline
# from a DONE line's own "===".
_RUN_START_RE = re.compile(r"===[ \t]+[^=\n]*Scraper")

_RUN_LOG_START_MARKERS = {
    "indeed": "=== INICIO RUN NOCTURNA INDEED ===",
    "linkedin": "=== INICIO RUN NOCTURNA ===",
    "infojobs": "=== INICIO RUN NOCTURNA INFOJOBS ===",
}

_CAPTCHA_MARKERS = ("CAPTCHA", "CHALLENGE", "ANTI-BOT", "ANTIBOT")

_SOURCE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})")


@dataclass(frozen=True)
class SourceStatus:
    """Status line the pipeline prints for one wrapped source."""

    source: str
    status: str  # ok|partial|failed|no_data|killed
    uploaded: int
    rejected: int


@dataclass(frozen=True)
class PipelineSummary:
    """Parsed evidence of one general-pipeline execution block."""

    log_path: str
    date: str | None  # YYYY-MM-DD from the "Inicio" header
    started_at: str | None  # ISO local "YYYY-MM-DDTHH:MM:SS"
    finished_at: str | None
    completed: bool  # True only when the "Fin pipeline" line exists
    global_failures: int | None
    duration_seconds: int | None
    statuses: dict[str, SourceStatus]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RunEvidence:
    """Evidence of what one source did during the analysed run."""

    source: str
    outcome: str
    offers_current_run: int | None  # run counter, never the accumulated snapshot
    offers_snapshot: int | None  # only from run-scoped evidence, else None
    snapshot_stale: bool  # snapshot exists but predates the run
    attempt: int | None
    attempts: int | None
    error: str | None
    detail: str | None


@dataclass(frozen=True)
class DiagnosticRun:
    """Selected run plus the per-source evidence gathered from it."""

    summary: PipelineSummary | None
    sources: dict[str, RunEvidence]  # the nine sources
    analyzable: bool
    inconclusive_reason: str | None


# --------------------------------------------------------------------------
# General pipeline log parsing
# --------------------------------------------------------------------------


def _time_of(line: str) -> time | None:
    """Return the leading ``HH:MM:SS`` time of a log line, if any."""
    match = _TIME_RE.match(line)
    if not match:
        return None
    try:
        return time.fromisoformat(match.group(1))
    except ValueError:
        return None


def _fmt_iso(day: str, moment: time) -> str:
    return f"{day}T{moment.strftime('%H:%M:%S')}"


def _empty_summary(log_path: Path) -> PipelineSummary:
    return PipelineSummary(
        log_path=str(log_path),
        date=None,
        started_at=None,
        finished_at=None,
        completed=False,
        global_failures=None,
        duration_seconds=None,
        statuses={},
        errors=(),
        warnings=(),
    )


def _split_blocks(lines: list[str]) -> list[list[str]]:
    """Split a general log into one block per ``Inicio pipeline`` line."""
    starts = [index for index, line in enumerate(lines) if _INICIO_RE.search(line)]
    if not starts:
        return []
    blocks: list[list[str]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append(lines[start:end])
    return blocks


def _summary_from_block(log_path: Path, block: list[str]) -> PipelineSummary:
    """Build a summary from a single Inicio..(Fin) block."""
    header = block[0]
    date_match = _INICIO_RE.search(header)
    day = date_match.group(1) if date_match else None
    start_time = _time_of(header) if date_match else None
    started_at = _fmt_iso(day, start_time) if day and start_time else None

    fin_line: str | None = None
    statuses: dict[str, SourceStatus] = {}
    errors: list[str] = []
    warnings: list[str] = []

    for line in block:
        status_match = _STATUS_RE.search(line)
        if status_match:
            statuses[status_match.group("source")] = SourceStatus(
                source=status_match.group("source"),
                status=status_match.group("status").lower(),
                uploaded=int(status_match.group("uploaded")),
                rejected=int(status_match.group("rejected")),
            )
            continue
        error_match = _ERROR_RE.search(line)
        if error_match:
            errors.append(error_match.group(1).strip())
        warn_match = _WARN_RE.search(line)
        if warn_match:
            warnings.append(warn_match.group(1).strip())
        if _FIN_RE.search(line):
            fin_line = line

    if fin_line is not None:
        fin_time = _time_of(fin_line)
        finished_at = None
        if day and fin_time:
            start_day = date.fromisoformat(day)
            if start_time and fin_time < start_time:
                start_day = start_day + timedelta(days=1)
            finished_at = _fmt_iso(start_day.isoformat(), fin_time)
        failures_match = _FAILURES_RE.search(fin_line)
        duration_match = _DURATION_RE.search(fin_line)
        completed = True
    else:
        finished_at = None
        failures_match = None
        duration_match = None
        completed = False

    return PipelineSummary(
        log_path=str(log_path),
        date=day,
        started_at=started_at,
        finished_at=finished_at,
        completed=completed,
        global_failures=int(failures_match.group(1)) if failures_match else None,
        duration_seconds=int(duration_match.group(1)) if duration_match else None,
        statuses=statuses,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def parse_pipeline_log(log_path: Path) -> PipelineSummary:
    """Parse the *last* execution block of a general pipeline log.

    A block without the ``Fin pipeline`` line (a truncated run) is returned with
    ``completed=False``; its existence alone never implies success.
    """
    log_path = Path(log_path)
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _empty_summary(log_path)
    blocks = _split_blocks(text.splitlines())
    if not blocks:
        return _empty_summary(log_path)
    return _summary_from_block(log_path, blocks[-1])


def _run_sort_key(summary: PipelineSummary) -> tuple[str, str]:
    return (summary.date or "", summary.finished_at or summary.started_at or "")


def discover_runs(logs_dir: Path) -> list[PipelineSummary]:
    """Return one summary per pipeline block found in ``logs_dir``.

    Multiple blocks in the same file (several runs the same day) are each
    returned. Results are ordered by (date, finish/start time).
    """
    logs_dir = Path(logs_dir)
    runs: list[PipelineSummary] = []
    if not logs_dir.is_dir():
        return runs
    for path in sorted(logs_dir.glob("upload-*.log")):
        if not _UPLOAD_LOG_RE.match(path.name):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for block in _split_blocks(text.splitlines()):
            runs.append(_summary_from_block(path, block))
    runs.sort(key=_run_sort_key)
    return runs


def select_last_run(logs_dir: Path) -> PipelineSummary | None:
    """Return the last *finished* run, or None if there is none.

    A finished run that produced no offers is still selected; a truncated block
    (no ``Fin pipeline`` line) is never selected.
    """
    completed = [run for run in discover_runs(logs_dir) if run.completed]
    return completed[-1] if completed else None


# --------------------------------------------------------------------------
# Shared helpers for source evidence
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_json(path: Path) -> object | None:
    # A UTF-8 BOM is common in PowerShell-written JSON (run_all.ps1).
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_iso(value: object) -> datetime | None:
    """Parse an ISO timestamp to naive local time.

    Aware timestamps (Indeed metadata in UTC, Multi-site ``run_at`` with an
    offset) are converted to the machine's local time, which is the reference
    of the pipeline log, so artifacts near midnight are not misattributed.
    Naive timestamps are assumed to already be local.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _run_start(summary: PipelineSummary) -> datetime | None:
    if summary.started_at:
        parsed = _parse_iso(summary.started_at)
        if parsed is not None:
            return parsed
    if summary.date:
        try:
            return datetime.combine(date.fromisoformat(summary.date), time.min)
        except ValueError:
            return None
    return None


def _run_end(summary: PipelineSummary) -> datetime | None:
    if summary.finished_at:
        parsed = _parse_iso(summary.finished_at)
        if parsed is not None:
            return parsed
    start = _run_start(summary)
    return start + timedelta(days=1) if start is not None else None


def _within_run(moment: datetime | None, summary: PipelineSummary) -> bool:
    if moment is None:
        return False
    start = _run_start(summary)
    end = _run_end(summary)
    if start is None or end is None:
        return True  # cannot filter: accept as run-scoped
    return start <= moment <= end


def _line_datetime(line: str) -> datetime | None:
    match = _SOURCE_TS_RE.match(line)
    if not match:
        return None
    return _parse_iso(f"{match.group(1)}T{match.group(2)}")


def _time_within_run(text: str, summary: PipelineSummary) -> bool:
    """Whether the first ``HH:MM:SS`` in ``text`` falls inside the run window.

    Source artifact logs (stdout, portal run logs) only carry a time of day.
    Candidate dates around the run start are tried so a run crossing midnight
    is still recognised. When the window cannot be determined, the artifact is
    accepted (nothing better is available).
    """
    run_start = _run_start(summary)
    run_end = _run_end(summary)
    if run_start is None or run_end is None:
        return True
    match = re.search(r"(?m)^(\d{2}:\d{2}:\d{2})", text)
    if not match:
        return False
    try:
        moment = time.fromisoformat(match.group(1))
    except ValueError:
        return False
    base = run_start.date()
    for delta in (-1, 0, 1):
        candidate = datetime.combine(base + timedelta(days=delta), moment)
        if run_start <= candidate <= run_end:
            return True
    return False


def _marker_stamp_within_run(
    text: str | None, summary: PipelineSummary
) -> bool | None:
    """Check a ``last_run=YYYYMMDD_HHMMSS`` marker against the run window.

    Returns ``True``/``False`` when a stamp is found, or ``None`` when the
    artifact is absent or has no parseable stamp.
    """
    if not text:
        return None
    match = re.search(r"last_run=(\d{8}_\d{6})", text)
    if not match:
        return None
    stamp = _stamp_datetime(match.group(1))
    if stamp is None:
        return None
    return _within_run(stamp, summary)


def _select_source_block(
    text: str, start_marker: str, summary: PipelineSummary
) -> str:
    """Return the source run block that belongs to the analysed run.

    Source logs accumulate blocks. A block is only usable when it starts
    inside the analysed run's window ``[run_start, run_end]``; a block from an
    earlier night (or a later truncated one) must never be reused as evidence
    of the current run. Returns ``""`` when no block belongs to the window.
    """
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if start_marker in line]
    if not starts:
        return ""
    blocks: list[list[str]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append(lines[start:end])

    run_start = _run_start(summary)
    run_end = _run_end(summary)
    if run_start is None or run_end is None:
        return ""

    eligible: list[list[str]] = []
    for block in blocks:
        start_dt = _line_datetime(block[0])
        if start_dt is not None and run_start <= start_dt <= run_end:
            eligible.append(block)
    return "\n".join(eligible[-1]) if eligible else ""


def _attempts(text: str) -> tuple[int | None, int | None]:
    matches = _ATTEMPT_RE.findall(text)
    if not matches:
        return None, None
    attempt, attempts = matches[-1]
    return int(attempt), int(attempts)


def _has_captcha(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in _CAPTCHA_MARKERS)


def _empty_evidence(source: str, detail: str | None = None) -> RunEvidence:
    return RunEvidence(
        source=source,
        outcome=OUTCOME_NO_EVIDENCE,
        offers_current_run=None,
        offers_snapshot=None,
        snapshot_stale=False,
        attempt=None,
        attempts=None,
        error=None,
        detail=detail,
    )


def _stamp_datetime(filename: str) -> datetime | None:
    """Parse a ``YYYYMMDD_HHMM(SS)`` stamp embedded in a filename."""
    match = _STAMP_RE.search(filename)
    if not match:
        return None
    try:
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        clock = match.group(2)
        fmt = "%H%M%S" if len(clock) == 6 else "%H%M"
        return datetime.combine(day, datetime.strptime(clock, fmt).time())
    except ValueError:
        return None


def _latest_stamp(paths) -> datetime | None:
    stamps = [stamp for stamp in (_stamp_datetime(p.name) for p in paths) if stamp]
    return max(stamps) if stamps else None


def _snapshot_stale_from_stamp(paths, summary: PipelineSummary) -> bool:
    run_start = _run_start(summary)
    latest = _latest_stamp(paths)
    if run_start is None or latest is None:
        return False
    return latest < run_start


def _snapshot_stale_from_mtime(path: Path, summary: PipelineSummary) -> bool:
    run_start = _run_start(summary)
    if run_start is None or not path.is_file():
        return False
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return False
    return modified < run_start


def _select_run_scoped(items, summary: PipelineSummary):
    """Pick the latest ``{name: (moment, data)}`` inside the run window.

    Only evidence that falls inside ``[run_start, run_end]`` is accepted; a
    same-day fallback was deliberately removed so an earlier execution of the
    same day is never attributed to the analysed run. Returns ``None`` when
    nothing matches.
    """
    in_window = [item for item in items if _within_run(item[1], summary)]
    if not in_window:
        return None
    return max(in_window, key=lambda item: item[1])


# --------------------------------------------------------------------------
# Indeed
# --------------------------------------------------------------------------


def _indeed_metadata(projects_root: Path, summary: PipelineSummary):
    output_dir = projects_root / "indeed_jobs_scraper" / "output"
    if not output_dir.is_dir():
        return None
    candidates = []
    for path in sorted(output_dir.glob("scrape_metadata_*.json")):
        if not _METADATA_JSON_RE.match(path.name):
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        moment = _parse_iso(data.get("finished_at")) or _parse_iso(
            data.get("started_at")
        )
        candidates.append((path, moment, data))
    if not candidates:
        return None
    chosen = _select_run_scoped(
        [(path, moment, data) for path, moment, data in candidates], summary
    )
    return chosen


def read_indeed_run(projects_root: Path, summary: PipelineSummary) -> RunEvidence:
    """Read Indeed's run evidence (T-07; RF-1, RF-2, RF-3)."""
    projects_root = Path(projects_root)
    evidence = sources.get_source("indeed").evidence
    run_log = projects_root / evidence["run_log"]
    text = _read_text(run_log)
    if text is None:
        return _empty_evidence("indeed", "run log not found")

    tail = _select_source_block(text, _RUN_LOG_START_MARKERS["indeed"], summary)
    if not tail:
        return _empty_evidence("indeed", "no nightly run block in the run window")

    result_text = _read_text(projects_root / evidence["result"])
    if _marker_stamp_within_run(result_text, summary) is False:
        return _empty_evidence("indeed", "last_nightly_run marker from another run")

    attempt, attempts = _attempts(tail)
    # The exit code may carry a suffix, e.g.
    # "=== FIN RUN NOCTURNA INDEED exit=-1 (challenge anti-bot: espera 24h) ===".
    finish = re.search(r"=== FIN RUN NOCTURNA INDEED exit=(-?\d+)", tail)
    exit_code = int(finish.group(1)) if finish else None
    success_marker = "Scrape completado detectado" in tail
    technical = "Fallo tecnico detectado" in tail
    fatal = "FATAL" in tail or "Abortando nightly run" in tail

    snapshot_dir = projects_root / "indeed_jobs_scraper" / "output"
    snapshots = list(snapshot_dir.glob("indeed_jobs_*.parquet"))
    stale = _snapshot_stale_from_stamp(snapshots, summary)

    def build(outcome, current, snapshot, error, detail):
        return RunEvidence(
            source="indeed",
            outcome=outcome,
            offers_current_run=current,
            offers_snapshot=snapshot,
            snapshot_stale=stale,
            attempt=attempt,
            attempts=attempts,
            error=error,
            detail=detail,
        )

    success = success_marker or exit_code == 0
    if not success:
        if _has_captcha(tail):
            return build(
                OUTCOME_BLOCKED, None, None, "captcha/challenge detected", None
            )
        if technical or fatal:
            return build(
                OUTCOME_ERROR, None, None, "technical failure detected", None
            )
        if exit_code is not None and exit_code != 0:
            return build(OUTCOME_ERROR, None, None, f"exit={exit_code}", None)
        return _empty_evidence("indeed", "run block is truncated (no finish line)")

    chosen = _indeed_metadata(projects_root, summary)
    if chosen is None:
        return _empty_evidence("indeed", "no run-scoped scrape metadata")
    path, moment, data = chosen
    total = data.get("total_unique")
    current = int(total) if isinstance(total, (int, float)) else None
    detail = f"metadata={path.name} finished_at={moment.isoformat() if moment else '?'}"
    if current is None:
        return build(OUTCOME_NO_EVIDENCE, None, None, None, detail)
    outcome = OUTCOME_OK if current > 0 else OUTCOME_EMPTY
    return build(outcome, current, None, None, detail)


# --------------------------------------------------------------------------
# LinkedIn
# --------------------------------------------------------------------------


def _linkedin_stdout(projects_root: Path, attempt: int | None) -> Path | None:
    data_dir = projects_root / "linkedin_jobs_scraper" / "data"
    if attempt is not None:
        path = data_dir / f"nightly_stdout_attempt{attempt}.log"
        if path.is_file():
            return path
    candidates = sorted(data_dir.glob("nightly_stdout_attempt*.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _linkedin_counts(text: str) -> tuple[int | None, int | None]:
    """Return (offers captured this run, accumulated store size).

    The accumulated snapshot size is never used as the run counter.
    """
    base_added = [int(value) for value in re.findall(r"(\d+) ofertas base anadidas", text)]
    store = [int(value) for value in re.findall(r"total en store:\s*(\d+)", text)]
    checkpoints = [int(value) for value in re.findall(r"\((\d+) ofertas,", text)]
    rows = [int(value) for value in re.findall(r"jobs\.parquet \((\d+) filas", text)]
    snapshot_values = store + checkpoints + rows
    # A run stdout without any counter line at all is missing evidence; a
    # stdout that exposes the store but adds no base card means zero offers.
    if not base_added and not snapshot_values:
        return None, None
    current = sum(base_added)
    snapshot = max(snapshot_values) if snapshot_values else None
    return current, snapshot


def read_linkedin_run(projects_root: Path, summary: PipelineSummary) -> RunEvidence:
    """Read LinkedIn's run evidence (T-08; RF-1, RF-2, RF-3).

    Run-scoped counters come from the run log and the attempt stdout. The
    accumulated ``data/output/jobs.parquet`` snapshot is reported separately
    (``offers_snapshot``) and is never the run counter.
    """
    projects_root = Path(projects_root)
    evidence = sources.get_source("linkedin").evidence
    run_log = projects_root / evidence["run_log"]
    text = _read_text(run_log)
    if text is None:
        return _empty_evidence("linkedin", "run log not found")

    tail = _select_source_block(text, _RUN_LOG_START_MARKERS["linkedin"], summary)
    if not tail:
        return _empty_evidence("linkedin", "no nightly run block in the run window")

    result_text = _read_text(projects_root / evidence["result"])
    if _marker_stamp_within_run(result_text, summary) is False:
        return _empty_evidence("linkedin", "last_nightly_run marker from another run")

    attempt, attempts = _attempts(tail)
    if attempts is None and result_text:
        result_attempts = re.search(r"attempts=(\d+)", result_text)
        if result_attempts:
            attempts = int(result_attempts.group(1))
    finish = re.search(r"=== FIN RUN NOCTURNA exit=(-?\d+) ===", tail)
    exit_code = int(finish.group(1)) if finish else None
    success_marker = (
        "Run completada:" in tail or "Resumen final detectado" in tail
    )
    timeout = "TIMEOUT" in tail

    snapshot_file = projects_root / evidence["snapshot"]
    stale = _snapshot_stale_from_mtime(snapshot_file, summary)

    def build(outcome, current, snapshot, error, detail):
        return RunEvidence(
            source="linkedin",
            outcome=outcome,
            offers_current_run=current,
            offers_snapshot=snapshot,
            snapshot_stale=stale,
            attempt=attempt,
            attempts=attempts,
            error=error,
            detail=detail,
        )

    if success_marker:
        pass
    elif _has_captcha(tail):
        return build(OUTCOME_BLOCKED, None, None, "captcha/challenge detected", None)
    elif timeout or (exit_code is not None and exit_code != 0) or "Stderr" in tail:
        return build(
            OUTCOME_ERROR,
            None,
            None,
            "timeout" if timeout else f"exit={exit_code}",
            None,
        )
    else:
        return _empty_evidence("linkedin", "run block is truncated (no finish line)")

    stdout_path = _linkedin_stdout(projects_root, attempt)
    if stdout_path is None:
        return build(OUTCOME_NO_EVIDENCE, None, None, None, "attempt stdout not found")
    stdout_text = _read_text(stdout_path) or ""
    if not _time_within_run(stdout_text, summary):
        return build(
            OUTCOME_NO_EVIDENCE, None, None, None, "attempt stdout from another run"
        )
    current, snapshot = _linkedin_counts(stdout_text)
    detail = f"stdout={stdout_path.name}"
    if current is None:
        return build(OUTCOME_NO_EVIDENCE, None, snapshot, None, detail)
    outcome = OUTCOME_OK if current > 0 else OUTCOME_EMPTY
    return build(outcome, current, snapshot, None, detail)


# --------------------------------------------------------------------------
# InfoJobs
# --------------------------------------------------------------------------


def read_infojobs_run(projects_root: Path, summary: PipelineSummary) -> RunEvidence:
    """Read InfoJobs' run evidence (T-09; RF-1, RF-2, RF-3)."""
    projects_root = Path(projects_root)
    evidence = sources.get_source("infojobs").evidence
    run_log = projects_root / evidence["run_log"]
    text = _read_text(run_log)
    if text is None:
        return _empty_evidence("infojobs", "run log not found")

    tail = _select_source_block(text, _RUN_LOG_START_MARKERS["infojobs"], summary)
    if not tail:
        return _empty_evidence("infojobs", "no nightly run block in the run window")

    result_text = _read_text(projects_root / evidence["result"])
    if _marker_stamp_within_run(result_text, summary) is False:
        return _empty_evidence("infojobs", "last_nightly_run marker from another run")

    attempt, attempts = _attempts(tail)
    finish = re.search(r"=== FIN RUN NOCTURNA INFOJOBS exit=(-?\d+) ===", tail)
    exit_code = int(finish.group(1)) if finish else None

    results = re.findall(
        r"RESULT:\s*total=(\d+)\s+incidencias=\d+\s+blocked=(\w+)", tail
    )
    last_total: int | None = None
    last_blocked = False
    if results:
        last_total = int(results[-1][0])
        last_blocked = results[-1][1].lower() == "true"
    # The last RESULT line is authoritative; the textual block message is only
    # a fallback when the run never produced a RESULT.
    blocked_marker = last_blocked if results else ("BLOQUEO por CAPTCHA" in tail)

    snapshot_paths = [
        path
        for path in (projects_root / "infojobs_jobs_scraper" / "data").glob(
            "offers_*.parquet"
        )
        if _OFFERS_PARQUET_RE.match(path.name)
    ]
    stale = _snapshot_stale_from_stamp(snapshot_paths, summary)

    def build(outcome, current, error, detail):
        return RunEvidence(
            source="infojobs",
            outcome=outcome,
            offers_current_run=current,
            offers_snapshot=None,
            snapshot_stale=stale,
            attempt=attempt,
            attempts=attempts,
            error=error,
            detail=detail,
        )

    if blocked_marker:
        return build(
            OUTCOME_BLOCKED,
            last_total if results else None,
            "captcha block detected",
            "blocked=true" if last_blocked else None,
        )
    if results:
        outcome = OUTCOME_OK if last_total and last_total > 0 else OUTCOME_EMPTY
        return build(outcome, last_total, None, f"total={last_total}")
    if "Fallo sin RESULT" in tail or (
        exit_code is not None and exit_code != 0
    ):
        return build(OUTCOME_ERROR, None, "failure without RESULT", None)
    return _empty_evidence("infojobs", "run block is truncated (no finish line)")


# --------------------------------------------------------------------------
# Multi-site portals
# --------------------------------------------------------------------------


def _multi_site_portal_outcome(exit_value, fresh_output: bool):
    """Classify one portal from its own exit value and fresh-output flag.

    The exit value is authoritative so the merge's success never hides a
    failed portal. A CAPTCHA/anti-bot block is reported by the supervisor (and
    re-checked in group 4); here a non-zero exit is a failure.
    """
    if exit_value == "timeout":
        return OUTCOME_ERROR, "portal timeout"
    if exit_value is None:
        return OUTCOME_ERROR, "portal result missing"
    try:
        code = int(exit_value)
    except (TypeError, ValueError):
        return OUTCOME_ERROR, f"unexpected exit value: {exit_value!r}"
    if code != 0:
        return OUTCOME_ERROR, f"exit={code}"
    return (OUTCOME_OK if fresh_output else OUTCOME_EMPTY), None


def _done_counter_for_run(
    log_text: str, log_path: Path, summary: PipelineSummary
) -> int | None:
    """Return a portal's DONE counter only when it belongs to the run.

    Portal ``run.log`` files are append-only across nights. The counter comes
    from the *last* ``DONE`` marker, and only when no start-of-execution banner
    (``=== ... Scraper ===``) follows it (otherwise that DONE belongs to an
    earlier execution) and the file was modified inside the run window. Benign
    trailing lines such as Glassdoor's ``Total ofertas almacenadas`` are
    ignored; an ``INTERRUMPIDO`` marker is not a ``DONE`` and yields ``None``
    (R5; RF-1).
    """
    run_start = _run_start(summary)
    run_end = _run_end(summary)
    if run_start is None or run_end is None:
        return None  # cannot attribute the evidence to the run

    matches = list(_DONE_RE.finditer(log_text))
    if not matches:
        return None
    last_done = matches[-1]
    # A later start banner means a newer execution began after this DONE, so
    # the DONE belongs to an earlier run.
    if _RUN_START_RE.search(log_text, last_done.end()):
        return None

    if not log_path.is_file():
        return None
    try:
        modified = datetime.fromtimestamp(log_path.stat().st_mtime)
    except OSError:
        return None
    if not (run_start <= modified <= run_end):
        return None
    return int(last_done.group(1))


def read_multi_site_runs(
    projects_root: Path, summary: PipelineSummary
) -> dict[str, RunEvidence]:
    """Read the six independent Multi-site portal results (T-10; RF-1, RF-2).

    The merge's success never hides a failed portal: each portal is classified
    from its own exit value, its log and whether it produced fresh output.
    """
    projects_root = Path(projects_root)
    result_path = projects_root / sources.get_source("glassdoor").evidence["result"]
    payload = _read_json(result_path)

    portals = sources.MULTI_SITE_SITES
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        return {portal: _empty_evidence(portal, "last_run.json not available") for portal in portals}

    results = payload["results"]
    outputs_merged = set(payload.get("outputs_merged") or [])
    merge_exit = payload.get("merge_exit")

    # The merged result must belong to the analysed run; otherwise the previous
    # night's outcome would be attributed to the current run.
    run_at = _parse_iso(payload.get("run_at"))
    if run_at is None or not _within_run(run_at, summary):
        return {
            portal: _empty_evidence(portal, "last_run.json from another run")
            for portal in portals
        }

    evidence_by_portal: dict[str, RunEvidence] = {}
    for portal in portals:
        source = sources.get_source(portal)
        run_log = projects_root / source.evidence["run_log"]
        log_text = _read_text(run_log) or ""
        fresh_output = portal in outputs_merged
        outcome, error = _multi_site_portal_outcome(
            results.get(portal), fresh_output
        )

        # The DONE counter is only meaningful for a portal that succeeded in
        # this run and only when the marker is the file's final entry and the
        # file was modified inside the run window (R5).
        current = None
        if outcome == OUTCOME_OK:
            current = _done_counter_for_run(log_text, run_log, summary)

        snapshot_path = projects_root / source.evidence["snapshot"]
        stale = _snapshot_stale_from_mtime(snapshot_path, summary)

        detail = (
            f"exit={results.get(portal)!r} merge_exit={merge_exit!r} "
            f"output_merged={fresh_output}"
        )
        evidence_by_portal[portal] = RunEvidence(
            source=portal,
            outcome=outcome,
            offers_current_run=current,
            offers_snapshot=None,
            snapshot_stale=stale,
            attempt=None,
            attempts=None,
            error=error,
            detail=detail,
        )
    return evidence_by_portal


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def collect_sources(
    projects_root: Path, summary: PipelineSummary
) -> dict[str, RunEvidence]:
    """Collect evidence for the nine independent sources of the run."""
    projects_root = Path(projects_root)
    collected: dict[str, RunEvidence] = {
        "indeed": read_indeed_run(projects_root, summary),
        "linkedin": read_linkedin_run(projects_root, summary),
        "infojobs": read_infojobs_run(projects_root, summary),
    }
    collected.update(read_multi_site_runs(projects_root, summary))
    # Keep exactly the catalog ids, in catalog order.
    return {source_id: collected[source_id] for source_id in sources.source_ids()}


def build_run_diagnostic(
    projects_root: Path | None = None, logs_dir: Path | None = None
) -> DiagnosticRun:
    """Select the last finished run and gather its per-source evidence.

    ``analyzable`` is False only when no finished run can be identified or read
    (RF-13). A finished run without offers is still analysable; sources that
    lack evidence are reported individually as ``OUTCOME_NO_EVIDENCE``.
    """
    root = Path(projects_root) if projects_root is not None else DEFAULT_PROJECTS_ROOT
    logs = Path(logs_dir) if logs_dir is not None else root / DEFAULT_LOGS_SUBDIR
    summary = select_last_run(logs)
    if summary is None:
        return DiagnosticRun(
            summary=None,
            sources={},
            analyzable=False,
            inconclusive_reason="no completed pipeline run found in logs",
        )
    return DiagnosticRun(
        summary=summary,
        sources=collect_sources(root, summary),
        analyzable=True,
        inconclusive_reason=None,
    )
