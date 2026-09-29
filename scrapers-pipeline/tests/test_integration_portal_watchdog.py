"""Integration test of per-portal watchdog blocking (T-43; RF-3, RF-12, RF-14, RF-15).

One scenario runs the six Multi-site portals at once: five keep printing a
growing counter (including portals whose counter advances with ``new=0``
duplicates already known) while Glassdoor stalls. The stalled portal is halted
exactly once with the watchdog reason, the others are only ever kept, a later
poll does not re-halt it, and the retry policy refuses to relaunch a watchdog
stop. This is the integrated counterpart of the per-module tests of T-19/T-20/
T-24: growing + stalled happen together, and isolation and no-relaunch are
proven in the same flow.

Offline and deterministic: streams are temporary files, ``stop`` is a spy that
records the pid, and the clock is injected through ``poll(now=...)`` (and the
CLI ``--now``), so there is no ``sleep`` and no real process.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from verification import retry_policy, sources, supervisor

T0 = datetime(2026, 9, 26, 0, 0, 0)

# The six independent Multi-site portals (RF-2, RF-15). One of them stalls.
PORTALS = sources.MULTI_SITE_SITES
STALLED = "glassdoor"

# Portals used to prove that a growing ``parsed`` with ``new=0`` (duplicates
# already known from a previous run) still counts as progress (RF-15).
_DUPLICATE_PORTALS = ("nvb", "jobs_ch")


def _progress(source: str, parsed: int, at: datetime, new: int = 0) -> str:
    return f"PROGRESS source={source} parsed={parsed} new={new} ts={at.isoformat()}"


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _pids() -> dict[str, int]:
    return {portal: 1000 + index for index, portal in enumerate(PORTALS)}


def _register_all(sup: supervisor.Supervisor, tmp_path: Path):
    """Register the six portals, each with one initial progress line at ``T0``."""
    pids = _pids()
    streams: dict[str, Path] = {}
    for portal in PORTALS:
        stream = tmp_path / f"{portal}.log"
        _write(stream, _progress(portal, 5, T0))
        streams[portal] = stream
        sup.register(portal, pids[portal], stream, started_at=T0)
    return pids, streams


def _cli_args(stream: Path, state: Path, portal: str, pid: int, now: datetime):
    return [
        "--source", portal,
        "--pid", str(pid),
        "--stream", str(stream),
        "--state", str(state),
        "--started-at", T0.isoformat(),
        "--now", now.isoformat(),
    ]


# --------------------------------------------------------------------------
# Integrated scenario through the Supervisor API
# --------------------------------------------------------------------------


def test_T43_growing_portal_keeps_and_stalled_portal_is_halted_alone(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    pids, streams = _register_all(sup, tmp_path)

    # 1) Anchoring poll: every portal is alive, nothing is stopped.
    assert all(decision.keep for decision in sup.poll(now=T0).values())
    assert calls == []
    assert sup.stops == []

    # 2) Healthy portals advance; Glassdoor stays at its first line.
    mid = T0 + timedelta(minutes=30)
    for portal in PORTALS:
        if portal != STALLED:
            _append(streams[portal], _progress(portal, 7, mid))
    assert all(decision.keep for decision in sup.poll(now=mid).values())
    assert calls == []

    # 3) At the 40-minute threshold the healthy portals show fresh progress:
    #    two of them only through a growing ``parsed`` with ``new=0`` (the
    #    duplicates already known still prove the page is being processed).
    #    Glassdoor has been idle since T0 and exceeds its inactivity period.
    at = T0 + timedelta(minutes=40)
    for portal in _DUPLICATE_PORTALS:
        _append(streams[portal], _progress(portal, 40, at, new=0))
    for portal in PORTALS:
        if portal != STALLED and portal not in _DUPLICATE_PORTALS:
            _append(streams[portal], _progress(portal, 12, at))
    decisions = sup.poll(now=at)

    # Growing counter -> keep and never stopped, even with ``new=0`` duplicates.
    for portal in PORTALS:
        if portal == STALLED:
            continue
        assert decisions[portal].keep, portal
        assert decisions[portal].blocked is False, portal
        assert sup.units[portal].stopped is False, portal

    # Stalled portal -> blocked, stopped exactly once, with the watchdog reason.
    assert decisions[STALLED].blocked
    assert sup.units[STALLED].stopped is True
    assert sup.units[STALLED].stop_reason == supervisor.WATCHDOG_REASON
    assert calls == [pids[STALLED]]
    assert [record.source for record in sup.stops] == [STALLED]
    assert sup.stops[0].pid == pids[STALLED]
    assert sup.stops[0].reason == supervisor.WATCHDOG_REASON
    assert "Watchdog" in sup.stops[0].message

    # No relaunch: a watchdog stop forbids a retry (RF-12, RF-15).
    policy = retry_policy.evaluate_retry(
        STALLED,
        watchdog_blocked=True,
        exit_code=supervisor.WATCHDOG_EXIT_CODE,
    )
    assert policy.decision == retry_policy.DECISION_STOP
    assert policy.retry is False
    assert retry_policy.should_retry(
        STALLED, watchdog_blocked=True, exit_code=supervisor.WATCHDOG_EXIT_CODE
    ) is False

    # 4) Idempotency: a later poll does not halt the stalled portal again and
    #    leaves all the healthy portals running.
    late = at + timedelta(minutes=5)
    for portal in PORTALS:
        if portal != STALLED:
            _append(streams[portal], _progress(portal, 50, late, new=0))
    later = sup.poll(now=late)

    assert calls == [pids[STALLED]]
    assert len(sup.stops) == 1
    for portal in PORTALS:
        if portal != STALLED:
            assert later[portal].keep, portal
            assert sup.units[portal].stopped is False, portal


# --------------------------------------------------------------------------
# No relaunch, contrasted with a genuine technical error
# --------------------------------------------------------------------------


def test_T43_watchdog_stop_is_not_relaunched_but_technical_error_is():
    # A portal the watchdog halted must never be relaunched...
    watchdog = retry_policy.evaluate_retry(
        STALLED,
        watchdog_blocked=True,
        exit_code=supervisor.WATCHDOG_EXIT_CODE,
    )
    assert watchdog.decision == retry_policy.DECISION_STOP
    assert watchdog.retry is False
    assert watchdog.reason == retry_policy.REASON_WATCHDOG
    assert retry_policy.should_retry(
        STALLED, watchdog_blocked=True, exit_code=supervisor.WATCHDOG_EXIT_CODE
    ) is False

    # ...whereas a genuine technical error without a watchdog block still does.
    technical = retry_policy.evaluate_retry("indeed", exit_code=1)
    assert technical.decision == retry_policy.DECISION_RETRY
    assert technical.retry is True
    assert retry_policy.should_retry("indeed", exit_code=1) is True


# --------------------------------------------------------------------------
# Same isolation and no-relaunch through the thin CLI the wrappers consume
# --------------------------------------------------------------------------


def test_T43_cli_isolates_the_stalled_portal_and_persists_state(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )

    pids = _pids()
    streams: dict[str, Path] = {}
    states: dict[str, Path] = {}
    for portal in PORTALS:
        stream = tmp_path / f"{portal}.log"
        _write(stream, _progress(portal, 5, T0))
        streams[portal] = stream
        states[portal] = tmp_path / f"{portal}.state.json"

    # 1) First poll: every portal keeps and no state records a halt.
    first = {
        portal: supervisor.main(
            _cli_args(streams[portal], states[portal], portal, pids[portal], T0)
        )
        for portal in PORTALS
    }
    capsys.readouterr()
    assert set(first.values()) == {0}
    assert calls == []

    # 2) Healthy portals keep growing (with ``new=0``); Glassdoor stays idle.
    at = T0 + timedelta(minutes=40)
    for portal in PORTALS:
        if portal != STALLED:
            _append(streams[portal], _progress(portal, 30, at, new=0))

    decisions = {
        portal: supervisor.main(
            _cli_args(streams[portal], states[portal], portal, pids[portal], at)
        )
        for portal in PORTALS
    }
    out = capsys.readouterr().out

    # Only the stalled portal exits with the watchdog code; the rest keep.
    assert decisions[STALLED] == supervisor.WATCHDOG_EXIT_CODE
    assert all(decisions[portal] == 0 for portal in PORTALS if portal != STALLED)
    assert calls == [pids[STALLED]]
    assert "state=blocked" in out
    assert f"reason={supervisor.WATCHDOG_REASON}" in out

    # Per-portal state: only the stalled portal is recorded as stopped.
    stalled_state = json.loads(states[STALLED].read_text(encoding="utf-8"))
    assert stalled_state["stopped"] is True
    assert stalled_state["stop_reason"] == supervisor.WATCHDOG_REASON
    for portal in PORTALS:
        if portal == STALLED:
            continue
        saved = json.loads(states[portal].read_text(encoding="utf-8"))
        assert saved["stopped"] is False, portal

    # 3) A later CLI poll reports blocked again without re-stopping it.
    later = at + timedelta(minutes=10)
    rc = supervisor.main(
        _cli_args(streams[STALLED], states[STALLED], STALLED, pids[STALLED], later)
    )
    capsys.readouterr()
    assert rc == supervisor.WATCHDOG_EXIT_CODE
    assert calls == [pids[STALLED]]
