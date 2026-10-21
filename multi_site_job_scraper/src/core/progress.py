"""Machine-readable run progress events (RF-15, T-18).

Format, kept literal so the pipeline supervisor can parse it:
    PROGRESS source=<source_id> parsed=<n> new=<n> ts=<iso>

- ``parsed`` is the cumulative count of work units processed during the run
  for *that portal*: it grows with the cards parsed on each page (duplicates
  already known from previous runs included) or, at minimum, by 1 for every
  processed page, even when that page brought no cards. An empty page is
  progress too, and a silent block must not look like a hang. The watchdog
  uses this field: either an already-seen offer or a visited page proves the
  scraper keeps working.
- ``new`` is optional and counts only offers not seen before (informational).
- ``ts`` is optional ISO timestamp.

Each Multi-site portal emits its own ``source`` (``irishjobs``, ``stepstone_nl``,
``devitjobs``, ``nvb``, ``jobs_ch``, ``glassdoor``) so the supervisor can track
them independently.

Local copy of the format: the scrapers are separate projects and must not
import from the diagnostic module.
"""
from __future__ import annotations

from datetime import timezone


def format_progress_line(source: str, parsed: int, new: int | None = None,
                         at=None) -> str:
    """Return a single ``PROGRESS`` line (no trailing newline)."""
    line = f"PROGRESS source={source} parsed={int(parsed)}"
    if new is not None:
        line += f" new={int(new)}"
    if at is not None:
        line += f" ts={_iso(at)}"
    return line


def _iso(value) -> str:
    if isinstance(value, str):
        return value
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds")


class RunProgressCounter:
    """Cumulative per-source counter; duplicates count as progress."""

    def __init__(self, source: str):
        self.source = source
        self.parsed = 0
        self.new = 0

    def add(self, parsed_delta: int, new_delta: int = 0) -> int:
        self.parsed += int(parsed_delta)
        self.new += int(new_delta)
        return self.parsed

    def line(self, at=None) -> str:
        return format_progress_line(self.source, self.parsed, self.new, at)
