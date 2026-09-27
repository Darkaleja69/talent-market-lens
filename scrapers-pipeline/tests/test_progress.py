"""Tests for progress events and the inactivity rule (T-13, T-14; RF-12, RF-15).

All tests use a simulated clock (no ``sleep``) and synthetic lines; offline.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from verification import progress


# --------------------------------------------------------------------------
# T-13: event format and per-source threshold
# --------------------------------------------------------------------------


def test_T13_default_inactivity_is_40_minutes():
    assert progress.DEFAULT_INACTIVITY_SECONDS == 40 * 60
    assert progress.inactivity_timeout("indeed") == timedelta(minutes=40)
    # Unknown sources also fall back to the default.
    assert progress.inactivity_timeout("not_a_source") == timedelta(minutes=40)


def test_T13_inactivity_override_per_source(monkeypatch):
    monkeypatch.setitem(
        progress.INACTIVITY_OVERRIDES, "glassdoor", timedelta(minutes=5)
    )

    assert progress.inactivity_timeout("glassdoor") == timedelta(minutes=5)
    assert progress.inactivity_timeout("indeed") == timedelta(minutes=40)


def test_T13_parse_valid_event():
    event = progress.parse_progress_event(
        "PROGRESS source=indeed parsed=120 new=12 ts=2026-09-26T00:10:00"
    )

    assert event is not None
    assert event.source == "indeed"
    assert event.parsed == 120
    assert event.new == 12
    assert event.at == datetime(2026, 9, 26, 0, 10, 0)


def test_T13_parse_tolerates_prefix_spaces_and_quotes():
    event = progress.parse_progress_event(
        '  12:00:00 INFO PROGRESS  source="indeed"  parsed="120"  new=12 '
    )

    assert event is not None
    assert event.source == "indeed"
    assert event.parsed == 120
    assert event.new == 12
    assert event.at is None


def test_T13_parse_optional_new_and_ts():
    event = progress.parse_progress_event("PROGRESS source=nvb parsed=5")

    assert event is not None
    assert event.source == "nvb"
    assert event.parsed == 5
    assert event.new is None
    assert event.at is None


def test_T13_parse_duplicates_are_counted_in_parsed():
    # parsed is the cumulative counter; it may grow while new stays at zero.
    event = progress.parse_progress_event(
        "PROGRESS source=linkedin parsed=7238 new=0 ts=2026-09-26T01:00:00"
    )

    assert event is not None
    assert event.parsed == 7238
    assert event.new == 0


def test_T13_parse_invalid_lines_return_none():
    invalid = (
        "",
        "INFO nothing to see here",
        "PROGRESS source=indeed",  # missing parsed
        "PROGRESS parsed=5",  # missing source
        "PROGRESS source=indeed parsed=abc",  # parsed not an integer
        "PROGRESS source= parsed=5",  # empty source
    )
    for line in invalid:
        assert progress.parse_progress_event(line) is None, line


# --------------------------------------------------------------------------
# T-14: pure keep/blocked rule with a simulated clock
# --------------------------------------------------------------------------

TIMEOUT = timedelta(minutes=40)


def _event(parsed: int, at: datetime | None = None, source: str = "indeed"):
    return progress.ProgressEvent(source=source, parsed=parsed, new=0, at=at)


def test_T14_growing_counter_keeps_after_a_long_time():
    now = datetime(2026, 9, 26, 12, 0, 0)
    parsed_at = now - timedelta(hours=10)

    decision = progress.evaluate_progress(
        last_parsed=0,
        parsed_at=parsed_at,
        event=_event(5, at=now),
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.keep is True
    assert decision.blocked is False
    assert decision.last_parsed == 5
    assert decision.last_progress_at == now


def test_T14_idle_just_before_timeout_keeps():
    parsed_at = datetime(2026, 9, 26, 0, 0, 0)
    now = parsed_at + TIMEOUT - timedelta(seconds=1)

    decision = progress.evaluate_progress(
        last_parsed=5,
        parsed_at=parsed_at,
        event=_event(5),
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.keep is True
    assert decision.blocked is False
    assert decision.last_parsed == 5


def test_T14_idle_at_timeout_blocks():
    parsed_at = datetime(2026, 9, 26, 0, 0, 0)
    now = parsed_at + TIMEOUT

    decision = progress.evaluate_progress(
        last_parsed=5,
        parsed_at=parsed_at,
        event=_event(5),
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.blocked is True
    assert decision.state == progress.PROGRESS_BLOCKED
    assert decision.idle_seconds == TIMEOUT.total_seconds()


def test_T14_idle_after_timeout_blocks():
    parsed_at = datetime(2026, 9, 26, 0, 0, 0)
    now = parsed_at + TIMEOUT + timedelta(minutes=5)

    decision = progress.evaluate_progress(
        last_parsed=5,
        parsed_at=parsed_at,
        event=_event(5),
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.blocked is True
    assert decision.last_progress_at == parsed_at


def test_T14_duplicate_progress_keeps():
    # parsed grows, new stays 0: duplicates already known are still progress.
    now = datetime(2026, 9, 26, 12, 0, 0)
    parsed_at = now - timedelta(hours=2)
    event = progress.ProgressEvent(source="linkedin", parsed=7100, new=0, at=now)

    decision = progress.evaluate_progress(
        last_parsed=7039,
        parsed_at=parsed_at,
        event=event,
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.keep is True
    assert decision.last_parsed == 7100


def test_T14_override_changes_threshold(monkeypatch):
    monkeypatch.setitem(
        progress.INACTIVITY_OVERRIDES, "glassdoor", timedelta(minutes=5)
    )
    parsed_at = datetime(2026, 9, 26, 0, 0, 0)
    now = parsed_at + timedelta(minutes=10)

    # Same 10-minute idle: default source keeps, overridden source blocks.
    default_decision = progress.evaluate_progress(
        last_parsed=5,
        parsed_at=parsed_at,
        event=_event(5, source="indeed"),
        now=now,
        timeout=progress.inactivity_timeout("indeed"),
    )
    override_decision = progress.evaluate_progress(
        last_parsed=5,
        parsed_at=parsed_at,
        event=_event(5, source="glassdoor"),
        now=now,
        timeout=progress.inactivity_timeout("glassdoor"),
    )

    assert default_decision.keep is True
    assert override_decision.blocked is True


def test_T14_decreasing_counter_is_not_progress():
    parsed_at = datetime(2026, 9, 26, 0, 0, 0)
    now = parsed_at + TIMEOUT

    decision = progress.evaluate_progress(
        last_parsed=10,
        parsed_at=parsed_at,
        event=_event(4),
        now=now,
        timeout=TIMEOUT,
    )

    assert decision.blocked is True
