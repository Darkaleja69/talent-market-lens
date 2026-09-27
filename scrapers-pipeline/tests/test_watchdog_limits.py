"""Watchdog limits and Multi-site isolation at the supervisor level (T-24).

Explicit, parametrised coverage of the supervisor (not just the pure rule):
growing counters never stop a unit; the threshold edge behaves keep / blocked /
blocked for the three direct sources and the six Multi-site portals; only the
stalled portal is stopped; a blocked unit is stopped exactly once; and the retry
policy answers ``stop`` after a watchdog block.

Offline with a simulated clock: no ``sleep``, no real process, ``stop`` is a
spy that records the pid.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from verification import progress, retry_policy, supervisor

T0 = datetime(2026, 9, 26, 0, 0, 0)

DIRECT_SOURCES = ("indeed", "linkedin", "infojobs")
PORTALS = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)
ALL_SOURCES = DIRECT_SOURCES + PORTALS


def _progress(source: str, parsed: int, at: datetime, new: int = 0) -> str:
    return f"PROGRESS source={source} parsed={parsed} new={new} ts={at.isoformat()}"


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _timeout(source: str) -> timedelta:
    return progress.inactivity_timeout(source)


# --------------------------------------------------------------------------
# 1. A growing counter keeps the unit alive, however long it runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_T24_growing_counter_keeps_far_beyond_timeout(tmp_path, source):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / f"{source}.log"
    _write(stream, _progress(source, 10, T0))
    sup.register(source, 101, stream, started_at=T0)

    assert sup.poll(now=T0)[source].keep

    # Hours beyond the 40-min threshold; every poll still advances the counter.
    for step, hours in enumerate((2, 24, 240)):
        moment = T0 + timedelta(hours=hours)
        _write(stream, _progress(source, 10 + step + 1, moment))
        decision = sup.poll(now=moment)[source]
        assert decision.keep, (source, hours)
        assert decision.blocked is False

    assert calls == []
    assert sup.stops == []


# --------------------------------------------------------------------------
# 2. Threshold edge: just before -> keep, exactly at -> blocked, after -> blocked
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_T24_threshold_edge_before_at_after(tmp_path, source):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / f"{source}.log"
    timeout = _timeout(source)
    _write(stream, _progress(source, 5, T0))
    sup.register(source, 202, stream, started_at=T0)

    assert sup.poll(now=T0)[source].keep  # anchors the baseline at T0

    just_before = sup.poll(now=T0 + timeout - timedelta(seconds=1))[source]
    assert just_before.keep and not just_before.blocked

    at_threshold = sup.poll(now=T0 + timeout)[source]
    assert at_threshold.blocked
    assert at_threshold.idle_seconds == timeout.total_seconds()

    after = sup.poll(now=T0 + timeout + timedelta(minutes=5))[source]
    assert after.blocked  # already halted, stays blocked

    assert calls == [202]  # exactly one stop for the whole edge walk
    assert sup.stops[0].reason == supervisor.WATCHDOG_REASON


# --------------------------------------------------------------------------
# 3. Multi-site isolation and per-portal thresholds
# --------------------------------------------------------------------------


def test_T24_multi_site_only_stalled_portal_is_stopped(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    timeout = _timeout("glassdoor")
    stalled = "glassdoor"
    pids = {portal: 300 + index for index, portal in enumerate(PORTALS)}
    streams: dict[str, Path] = {}
    for portal in PORTALS:
        stream = tmp_path / f"{portal}.log"
        _write(stream, _progress(portal, 5, T0))
        streams[portal] = stream
        sup.register(portal, pids[portal], stream, started_at=T0)

    assert all(decision.keep for decision in sup.poll(now=T0).values())

    # Healthy portals make fresh progress exactly at the observed boundary.
    at = T0 + timeout
    for portal in PORTALS:
        if portal != stalled:
            _write(streams[portal], _progress(portal, 9, at))

    decisions = sup.poll(now=at)
    assert decisions[stalled].blocked
    for portal in PORTALS:
        if portal != stalled:
            assert decisions[portal].keep, portal
            assert sup.units[portal].stopped is False, portal

    assert calls == [pids[stalled]]
    assert [record.source for record in sup.stops] == [stalled]

    # A later poll (still inside the healthy portals' window) neither re-stops
    # the stalled portal nor touches the rest.
    sup.poll(now=at + timedelta(seconds=30))
    assert calls == [pids[stalled]]


def test_T24_per_portal_thresholds_do_not_leak(tmp_path, monkeypatch):
    monkeypatch.setitem(
        progress.INACTIVITY_OVERRIDES, "glassdoor", timedelta(minutes=5)
    )
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    pids = {"glassdoor": 1, "nvb": 2}
    for portal, pid in pids.items():
        stream = tmp_path / f"{portal}.log"
        _write(stream, _progress(portal, 1, T0))
        sup.register(portal, pid, stream, started_at=T0)

    sup.poll(now=T0)
    decisions = sup.poll(now=T0 + timedelta(minutes=10))

    assert decisions["glassdoor"].blocked  # 5-minute override
    assert decisions["nvb"].keep  # default 40 minutes
    assert calls == [pids["glassdoor"]]


# --------------------------------------------------------------------------
# 4. No relaunch: a halted unit is stopped once and the policy returns stop
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_T24_blocked_unit_is_stopped_once_and_policy_says_stop(tmp_path, source):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / f"{source}.log"
    timeout = _timeout(source)
    _write(stream, _progress(source, 5, T0))
    sup.register(source, 404, stream, started_at=T0)

    sup.poll(now=T0)
    sup.poll(now=T0 + timeout)
    sup.poll(now=T0 + timeout + timedelta(hours=1))
    sup.poll(now=T0 + timeout + timedelta(hours=5))

    assert calls == [404]  # idempotent: never stopped twice
    assert len(sup.stops) == 1

    decision = retry_policy.evaluate_retry(
        source,
        watchdog_blocked=True,
        exit_code=supervisor.WATCHDOG_EXIT_CODE,
    )
    assert decision.decision == retry_policy.DECISION_STOP
    assert decision.retry is False


# --------------------------------------------------------------------------
# 5. Edge gaps
# --------------------------------------------------------------------------


def test_T24_duplicate_counter_is_not_progress(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / "linkedin.log"
    timeout = _timeout("linkedin")
    # parsed does not grow and new stays 0: duplicates are not progress.
    _write(stream, _progress("linkedin", 7100, T0, new=0))
    sup.register("linkedin", 55, stream, started_at=T0)
    sup.poll(now=T0)

    assert sup.poll(now=T0 + timeout - timedelta(seconds=1))["linkedin"].keep
    assert sup.poll(now=T0 + timeout)["linkedin"].blocked
    assert calls == [55]


@pytest.mark.parametrize("source", ("glassdoor", "indeed"))
def test_T24_no_progress_event_blocks_after_timeout(tmp_path, source):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / f"{source}.log"
    stream.write_text("INFO scraper starting, no PROGRESS yet\n", encoding="utf-8")
    timeout = _timeout(source)
    sup.register(source, 77, stream, started_at=T0)

    assert sup.poll(now=T0 + timeout - timedelta(seconds=1))[source].keep
    assert sup.poll(now=T0 + timeout)[source].blocked
    assert calls == [77]


@pytest.mark.parametrize("source", ("nvb", "indeed"))
def test_T24_stale_event_before_run_is_not_progress(tmp_path, source):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / f"{source}.log"
    timeout = _timeout(source)
    # Append-only log: only a line from the previous night.
    _write(stream, _progress(source, 999, T0 - timedelta(days=1)))
    sup.register(source, 88, stream, started_at=T0)

    assert sup.poll(now=T0 + timeout - timedelta(seconds=1))[source].keep
    assert sup.poll(now=T0 + timeout)[source].blocked
    assert calls == [88]
