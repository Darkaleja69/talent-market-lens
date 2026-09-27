"""Integration test for per-portal Multi-site supervision (T-20; RF-2, RF-12, RF-15).

Offline and process-free: six append-only ``run.log`` files (each carrying a
stale high counter from the previous night) are polled through the real CLI
(``verification.supervisor``) with an injected ``stop_process_tree``.

The "other five keep running" claim is proven at this layer by their
``state=keep`` decision and by the absence of any stop call for their pid: the
PowerShell wrapper only acts (stops the tree) when the CLI declares blocked, so
a keep decision means the portal is left untouched. No real process is killed,
which keeps the test deterministic on any OS.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from verification import supervisor

PORTALS = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)
T0 = datetime(2026, 9, 26, 0, 0, 0)
YESTERDAY = T0 - timedelta(days=1)


def _progress(source: str, parsed: int, at: datetime) -> str:
    return f"PROGRESS source={source} parsed={parsed} new=0 ts={at.isoformat()}"


def _append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _cli(root: Path, portal: str, state: Path, pid: int, now: datetime):
    return [
        "--source", portal,
        "--pid", str(pid),
        "--stream", str(root / "data" / portal / "run.log"),
        "--state", str(state),
        "--started-at", T0.isoformat(),
        "--now", now.isoformat(),
    ]


def test_T20_only_the_stalled_portal_is_stopped(tmp_path, monkeypatch, capsys):
    stopped: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: stopped.append(pid)
    )

    pids = {portal: 1000 + index for index, portal in enumerate(PORTALS)}
    states: dict[str, Path] = {}
    for portal in PORTALS:
        run_dir = tmp_path / "data" / portal
        run_dir.mkdir(parents=True, exist_ok=True)
        # Append-only log: a stale high counter from the previous night.
        _append(run_dir / "run.log", _progress(portal, 999, YESTERDAY))
        states[portal] = run_dir / ".watchdog.json"

    stalled = "glassdoor"
    healthy = [portal for portal in PORTALS if portal != stalled]

    # 1) Right after the run started: no portal is blocked yet.
    for portal in PORTALS:
        assert supervisor.main(
            _cli(tmp_path, portal, states[portal], pids[portal], T0)
        ) == 0
    capsys.readouterr()
    assert stopped == []

    # 2) The healthy portals advance; the stalled one stays at yesterday's line.
    mid = T0 + timedelta(minutes=30)
    for portal in healthy:
        _append(tmp_path / "data" / portal / "run.log", _progress(portal, 5, mid))
    for portal in PORTALS:
        assert supervisor.main(
            _cli(tmp_path, portal, states[portal], pids[portal], mid)
        ) == 0
    capsys.readouterr()
    assert stopped == []

    # 3) Healthy portals make fresh progress; the stalled one exceeds 40 min.
    last = T0 + timedelta(minutes=50)
    for portal in healthy:
        _append(tmp_path / "data" / portal / "run.log", _progress(portal, 9, last))

    decisions = {
        portal: supervisor.main(
            _cli(tmp_path, portal, states[portal], pids[portal], last)
        )
        for portal in PORTALS
    }
    out = capsys.readouterr().out

    # Only the stalled portal's tree/pid is signalled.
    assert stopped == [pids[stalled]]
    assert decisions[stalled] == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out
    assert f"reason={supervisor.WATCHDOG_REASON}" in out

    # The other five keep running (keep decision, never stopped).
    for portal in healthy:
        assert decisions[portal] == 0, portal
        saved = json.loads(states[portal].read_text(encoding="utf-8"))
        assert saved["stopped"] is False, portal

    stalled_state = json.loads(states[stalled].read_text(encoding="utf-8"))
    assert stalled_state["stopped"] is True
    assert stalled_state["stop_reason"] == supervisor.WATCHDOG_REASON
