"""Progress events and the inactivity/watchdog rule (T-13, T-14; RF-12, RF-15).

Each scraping unit emits a machine-readable progress line so the supervisor can
tell whether a process is still advancing or is stuck (RF-15). The supervisor
only decides *keep* or *blocked*; stopping the process tree and avoiding a
retry after a watchdog stop live in the wrappers (T-19..T-24).

Event format
------------

The stable line format emitted by a unit is::

    PROGRESS source=<source_id> parsed=<n> new=<n> ts=<iso>

- ``source``  : one of the nine source ids from ``verification.sources``.
- ``parsed``  : cumulative offers/tickets parsed so far **during the run**.
  It counts every parsed item, *including duplicates already known* from a
  previous run: a duplicate still proves the page is being processed. This is
  the field the watchdog uses.
- ``new``     : optional, only the offers not seen before. It is informational
  and is deliberately **not** used for the progress decision.
- ``ts``      : optional ISO timestamp of the event. When absent the supervisor
  uses the wall clock of the observation.

``parse_progress_event`` is tolerant: the ``PROGRESS`` token may appear after a
log prefix, values may be quoted and extra whitespace is accepted. A line that
does not carry a usable ``source`` and integer ``parsed`` yields ``None``.

Inactivity threshold
--------------------

Each source has a configurable inactivity period. The initial default is 40
minutes (``DEFAULT_INACTIVITY_SECONDS``); per-source overrides can be added in
``INACTIVITY_OVERRIDES`` for units with longer normal pauses (T-19/T-20). A
process is declared blocked only when no progress has been observed for at
least its threshold; any growth of the counter keeps it alive and restarts the
improvement instant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Initial inactivity period before a process is declared blocked (T-13).
DEFAULT_INACTIVITY_SECONDS = 40 * 60

# Decision states returned by the pure rule (RF-15). They are intentionally
# distinct so a watchdog stop can be told apart from a retryable technical
# error by the wrappers (T-21..T-23).
PROGRESS_KEEP = "keep"
PROGRESS_BLOCKED = "blocked"

# Per-source overrides of the inactivity period. Empty by default; extended in
# T-19/T-20 for sources with known longer pauses. Values are ``timedelta``.
INACTIVITY_OVERRIDES: dict[str, timedelta] = {}

# Tolerant event parsing.
_EVENT_RE = re.compile(r"\bPROGRESS\b(?P<rest>.*)$", re.IGNORECASE)
_TOKEN_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s]+)"
)


@dataclass(frozen=True)
class ProgressEvent:
    """One parsed ``PROGRESS`` line emitted by a scraping unit."""

    source: str
    parsed: int  # cumulative parsed items this run (includes duplicates)
    new: int | None = None  # optional, offers not seen before
    at: datetime | None = None  # optional event timestamp (naive local)


@dataclass(frozen=True)
class ProgressDecision:
    """Outcome of evaluating one event against the last observed progress."""

    state: str
    source: str
    last_parsed: int
    last_progress_at: datetime
    idle_seconds: float

    @property
    def keep(self) -> bool:
        """True while the process must be kept running."""
        return self.state == PROGRESS_KEEP

    @property
    def blocked(self) -> bool:
        """True when the process must be stopped as blocked by the watchdog."""
        return self.state == PROGRESS_BLOCKED


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _to_local_naive(value: datetime) -> datetime:
    """Normalise an aware timestamp to the machine's local naive time."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _parse_timestamp(value: str) -> datetime | None:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _to_local_naive(parsed)


def parse_progress_event(line: str) -> ProgressEvent | None:
    """Parse a tolerant ``PROGRESS`` line, or return ``None`` if unusable.

    Accepts log prefixes, quoted values and extra whitespace; requires a
    non-empty ``source`` and an integer ``parsed``.
    """
    if not line:
        return None
    marker = _EVENT_RE.search(line)
    if marker is None:
        return None
    tokens = {
        key.lower(): _unquote(value)
        for key, value in _TOKEN_RE.findall(marker.group("rest"))
    }
    source = tokens.get("source", "").strip()
    parsed_raw = tokens.get("parsed")
    if not source or parsed_raw is None:
        return None
    try:
        parsed = int(parsed_raw)
    except ValueError:
        return None

    new: int | None = None
    if "new" in tokens:
        try:
            new = int(tokens["new"])
        except ValueError:
            new = None

    at: datetime | None = None
    if "ts" in tokens:
        at = _parse_timestamp(tokens["ts"])

    return ProgressEvent(source=source, parsed=parsed, new=new, at=at)


def inactivity_timeout(source_id: str) -> timedelta:
    """Return the inactivity period of a source (override or 40 min default)."""
    override = INACTIVITY_OVERRIDES.get(source_id)
    if override is not None:
        return override
    return timedelta(seconds=DEFAULT_INACTIVITY_SECONDS)


def evaluate_progress(
    last_parsed: int,
    parsed_at: datetime,
    event: ProgressEvent,
    now: datetime,
    timeout: timedelta,
) -> ProgressDecision:
    """Pure watchdog rule: keep on progress, block after ``timeout`` idle.

    ``last_parsed``/``parsed_at`` describe the last observed improvement (the
    counter value and when it was reached). If ``event.parsed`` is greater, the
    process advances even if ``event.new`` is zero (duplicates already known
    still count as progress); otherwise it is blocked only once the idle time
    reaches the threshold of its source. A counter that does not grow before
    the threshold keeps the process alive.
    """
    if event.parsed > last_parsed:
        improved_at = event.at if event.at is not None else now
        if improved_at < parsed_at:
            improved_at = parsed_at
        return ProgressDecision(
            state=PROGRESS_KEEP,
            source=event.source,
            last_parsed=event.parsed,
            last_progress_at=improved_at,
            idle_seconds=0.0,
        )

    idle = (now - parsed_at).total_seconds()
    if idle < 0:
        idle = 0.0
    state = PROGRESS_BLOCKED if idle >= timeout.total_seconds() else PROGRESS_KEEP
    return ProgressDecision(
        state=state,
        source=event.source,
        last_parsed=last_parsed,
        last_progress_at=parsed_at,
        idle_seconds=idle,
    )
