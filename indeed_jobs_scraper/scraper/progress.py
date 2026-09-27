"""Machine-readable run progress events (RF-15, T-15).

Format, kept literal so the pipeline supervisor can parse it:
    PROGRESS source=<source_id> parsed=<n> new=<n> ts=<iso>

- ``parsed`` is the cumulative count of offers processed during the run,
  *including duplicates already known* from previous runs. The watchdog uses
  this field: a duplicate still proves the page keeps being processed.
- ``new`` is optional and counts only offers not seen before (informational).
- ``ts`` is optional ISO timestamp.

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
