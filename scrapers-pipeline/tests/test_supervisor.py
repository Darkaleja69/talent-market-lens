"""Tests for the watchdog supervisor (T-19; RF-12, RF-15).

Offline, with a simulated clock: streams are temporary files, ``stop`` is a
spy that only records the pid and the CLI is driven with ``--now`` so no
``sleep`` or real process is involved.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from verification import progress, supervisor

T0 = datetime(2026, 9, 26, 0, 0, 0)


def _progress(source: str, parsed: int, at: datetime | None = None) -> str:
    line = f"PROGRESS source={source} parsed={parsed} new=0"
    if at is not None:
        line += f" ts={at.isoformat()}"
    return line


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Exit code and tolerant stream reading
# --------------------------------------------------------------------------


def test_T19_watchdog_exit_code_does_not_collide():
    used = {0, 1, 2, 3, 4, 130}
    assert supervisor.WATCHDOG_EXIT_CODE not in used
    assert supervisor.WATCHDOG_REASON == "watchdog_no_progress"


def test_T19_read_last_progress_event_tolerates_bad_files(tmp_path):
    assert supervisor.read_last_progress_event(tmp_path / "missing.log") is None

    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert supervisor.read_last_progress_event(empty) is None

    # A partial trailing line is skipped in favour of the previous full one.
    partial = tmp_path / "partial.log"
    partial.write_text(
        "noise\n" + _progress("indeed", 7, T0) + "\nPROGRESS source=indeed pars",
        encoding="utf-8",
    )
    event = supervisor.read_last_progress_event(partial)
    assert event is not None
    assert event.source == "indeed"
    assert event.parsed == 7


def test_B1_load_state_tolerates_bom(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("\ufeff" + json.dumps({"pid": 1}), encoding="utf-8")
    assert supervisor.load_state(state) == {"pid": 1}


def test_T19_tracker_ignores_event_from_another_source():
    tracker = supervisor.ProgressTracker("indeed", T0)
    other = progress.ProgressEvent(source="linkedin", parsed=999, new=0)

    decision = tracker.observe(other, T0 + timedelta(minutes=1))

    # The foreign counter must not be attributed to this unit.
    assert decision.keep
    assert tracker.last_parsed == 0


# --------------------------------------------------------------------------
# T-20: append-only Multi-site run.log scoped to the current run
# --------------------------------------------------------------------------


def test_T20_read_last_progress_event_ignores_previous_night(tmp_path):
    log = tmp_path / "run.log"
    _write(
        log,
        _progress("nvb", 999, T0 - timedelta(days=1)),  # last night
        _progress("nvb", 5, T0),  # this run
    )

    scoped = supervisor.read_last_progress_event(log, since=T0)
    assert scoped is not None
    assert scoped.parsed == 5

    # Without a run scope the newest line is still returned.
    unscoped = supervisor.read_last_progress_event(log)
    assert unscoped is not None
    assert unscoped.parsed == 5


def test_T20_only_stale_progress_is_not_run_progress(tmp_path):
    log = tmp_path / "run.log"
    _write(log, _progress("glassdoor", 999, T0 - timedelta(days=1)))

    assert supervisor.read_last_progress_event(log, since=T0) is None


def test_T20_supervisor_scopes_append_only_log_to_the_run(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    log = tmp_path / "run.log"
    # Yesterday's high counter alone must not count as this run's progress.
    _write(log, _progress("glassdoor", 999, T0 - timedelta(days=1)))
    sup.register("glassdoor", 7, log, started_at=T0)

    assert sup.poll(now=T0)["glassdoor"].keep
    assert sup.poll(now=T0 + timedelta(minutes=39))["glassdoor"].keep
    assert sup.poll(now=T0 + timedelta(minutes=41))["glassdoor"].blocked
    assert calls == [7]


# --------------------------------------------------------------------------
# Growing counter keeps the process alive
# --------------------------------------------------------------------------


def test_T19_growing_counter_keeps_and_never_stops(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / "indeed.log"
    _write(stream, _progress("indeed", 10, T0))
    sup.register("indeed", 111, stream, started_at=T0)

    assert sup.poll(now=T0)["indeed"].keep

    later = T0 + timedelta(minutes=90)  # well past the 40-minute threshold
    _write(
        stream,
        _progress("indeed", 10, T0),
        _progress("indeed", 42, later),  # duplicates also count as progress
    )
    decision = sup.poll(now=later)["indeed"]

    assert decision.keep
    assert decision.blocked is False
    assert calls == []
    assert sup.stops == []


# --------------------------------------------------------------------------
# Idle counter: stop only the blocked unit, record the reason
# --------------------------------------------------------------------------


def test_T19_idle_unit_stopped_alone_with_reason(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    streams: dict[str, Path] = {}
    for source, pid in (("indeed", 111), ("linkedin", 222), ("infojobs", 333)):
        path = tmp_path / f"{source}.log"
        _write(path, _progress(source, 5, T0))
        streams[source] = path
        sup.register(source, pid, path, started_at=T0)

    assert all(d.keep for d in sup.poll(now=T0).values())
    assert calls == []

    later = T0 + timedelta(minutes=41)
    # Indeed stays quiet; LinkedIn and InfoJobs keep advancing.
    _write(
        streams["linkedin"],
        _progress("linkedin", 5, T0),
        _progress("linkedin", 150, later),
    )
    _write(
        streams["infojobs"],
        _progress("infojobs", 5, T0),
        _progress("infojobs", 200, later),
    )

    decisions = sup.poll(now=later)

    assert decisions["indeed"].blocked
    assert decisions["linkedin"].keep
    assert decisions["infojobs"].keep
    # Only the blocked source's process tree was signalled.
    assert calls == [111]
    assert [record.source for record in sup.stops] == ["indeed"]
    assert sup.stops[0].pid == 111
    assert sup.stops[0].reason == supervisor.WATCHDOG_REASON
    assert "Watchdog" in sup.stops[0].message

    # A later poll does not stop the already-halted unit again.
    sup.poll(now=later + timedelta(minutes=10))
    assert calls == [111]


def test_T19_no_progress_line_blocks_after_timeout(tmp_path):
    calls: list[int] = []
    sup = supervisor.Supervisor(stop=calls.append)
    stream = tmp_path / "infojobs.log"
    _write(stream, "INFO scraper starting, nothing parsed yet")
    sup.register("infojobs", 999, stream, started_at=T0)

    assert sup.poll(now=T0 + timedelta(minutes=39))["infojobs"].keep
    assert sup.poll(now=T0 + timedelta(minutes=41))["infojobs"].blocked
    assert calls == [999]


def test_T19_stop_receives_the_blocked_pid(tmp_path):
    seen: list[tuple[str, int]] = []

    def fake_stop_process_tree(pid: int) -> bool:
        seen.append(("tree", pid))
        return True

    sup = supervisor.Supervisor(stop=fake_stop_process_tree)
    stream = tmp_path / "glassdoor.log"
    _write(stream, _progress("glassdoor", 3, T0))
    sup.register("glassdoor", 4242, stream, started_at=T0)
    sup.poll(now=T0)
    sup.poll(now=T0 + timedelta(minutes=41))

    assert seen == [("tree", 4242)]


# --------------------------------------------------------------------------
# Thin CLI: state persists between calls and drives the process-tree stop
# --------------------------------------------------------------------------


def test_T19_cli_persists_state_and_stops_via_process_tree(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )

    stream = tmp_path / "linkedin.log"
    _write(stream, _progress("linkedin", 5, T0))
    state = tmp_path / "state.json"

    rc_keep = supervisor.main(
        [
            "--source", "linkedin",
            "--pid", "222",
            "--stream", str(stream),
            "--state", str(state),
            "--started-at", T0.isoformat(),
            "--now", T0.isoformat(),
        ]
    )
    out_keep = capsys.readouterr().out
    assert rc_keep == 0
    assert "state=keep" in out_keep
    assert calls == []

    later = T0 + timedelta(minutes=41)
    rc_blocked = supervisor.main(
        [
            "--source", "linkedin",
            "--pid", "222",
            "--stream", str(stream),
            "--state", str(state),
            "--now", later.isoformat(),
        ]
    )
    out_blocked = capsys.readouterr().out
    assert rc_blocked == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out_blocked
    assert f"reason={supervisor.WATCHDOG_REASON}" in out_blocked
    assert "stopped=true" in out_blocked
    assert calls == [222]

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["stopped"] is True
    assert saved["stop_reason"] == supervisor.WATCHDOG_REASON
    assert saved["last_parsed"] == 5


def test_T19_cli_does_not_restop_an_already_stopped_unit(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "infojobs.log"
    stream.write_text("started, no progress\n", encoding="utf-8")
    state = tmp_path / "state.json"

    first = [
        "--source", "infojobs",
        "--pid", "333",
        "--stream", str(stream),
        "--state", str(state),
        "--started-at", T0.isoformat(),
        "--now", (T0 + timedelta(minutes=41)).isoformat(),
    ]
    assert supervisor.main(first) == supervisor.WATCHDOG_EXIT_CODE
    capsys.readouterr()
    assert calls == [333]

    # A later poll reuses the persisted "stopped" state and must not re-signal.
    rc = supervisor.main(
        [
            "--source", "infojobs",
            "--pid", "333",
            "--stream", str(stream),
            "--state", str(state),
            "--now", (T0 + timedelta(minutes=90)).isoformat(),
        ]
    )
    out = capsys.readouterr().out
    assert rc == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out
    assert calls == [333]


def test_T19_cli_no_stop_only_reports(tmp_path, monkeypatch, capsys):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "infojobs.log"
    stream.write_text("started, no progress\n", encoding="utf-8")
    state = tmp_path / "state.json"

    rc = supervisor.main(
        [
            "--source", "infojobs",
            "--pid", "333",
            "--stream", str(stream),
            "--state", str(state),
            "--started-at", T0.isoformat(),
            "--now", (T0 + timedelta(minutes=41)).isoformat(),
            "--no-stop",
        ]
    )
    out = capsys.readouterr().out
    assert rc == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out
    assert calls == []


# --------------------------------------------------------------------------
# B1: watchdog state must not leak across runs / processes
# --------------------------------------------------------------------------

YESTERDAY = T0 - timedelta(days=1)


def _stale_state(stream: Path, **overrides) -> dict:
    payload = {
        "source": "indeed",
        "pid": 111,
        "stream": str(stream),
        "last_parsed": 120,  # last night's high counter
        "last_progress_at": YESTERDAY.isoformat(),
        "stopped": False,
        "stop_reason": None,
    }
    payload.update(overrides)
    return payload


def _cli_args(stream: Path, state: Path, pid: int, now: datetime, **extra):
    args = [
        "--source", "indeed",
        "--pid", str(pid),
        "--stream", str(stream),
        "--state", str(state),
        "--now", now.isoformat(),
    ]
    for key, value in extra.items():
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool):
            if value:
                args.append(flag)
        else:
            args += [flag, value]
    return args


def test_B1_stale_state_with_other_pid_does_not_block_new_run(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "indeed.log"
    _write(stream, _progress("indeed", 5, T0))
    state = tmp_path / "state.json"
    # Last night: pid 111 with a huge counter; tonight: pid 222, tiny counter.
    state.write_text(json.dumps(_stale_state(stream)), encoding="utf-8")

    rc = supervisor.main(
        _cli_args(stream, state, 222, T0, started_at=T0.isoformat())
    )
    out = capsys.readouterr().out

    assert rc == 0  # healthy new process: keep, not blocked
    assert "state=keep" in out
    assert calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pid"] == 222
    assert saved["last_parsed"] == 5


def test_B1_stale_baseline_is_discarded_even_with_same_pid(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "indeed.log"
    _write(stream, _progress("indeed", 5, T0))
    state = tmp_path / "state.json"
    # Same pid but a baseline from before this run started.
    state.write_text(
        json.dumps(_stale_state(stream, pid=222)), encoding="utf-8"
    )

    rc = supervisor.main(
        _cli_args(stream, state, 222, T0, started_at=T0.isoformat())
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "state=keep" in out
    assert calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_parsed"] == 5


def test_B1_stopped_state_other_pid_does_not_leave_orphan(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "indeed.log"
    _write(stream, _progress("indeed", 5, T0))
    state = tmp_path / "state.json"
    # Last night ended stopped; tonight is a different process (pid 222).
    state.write_text(
        json.dumps(
            _stale_state(
                stream, stopped=True, stop_reason=supervisor.WATCHDOG_REASON
            )
        ),
        encoding="utf-8",
    )

    rc = supervisor.main(
        _cli_args(stream, state, 222, T0, started_at=T0.isoformat())
    )
    out = capsys.readouterr().out

    # It must not report a halt it never performed: the new process keeps alive.
    assert rc == 0
    assert "state=keep" in out
    assert calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["stopped"] is False


def test_B1_same_pid_and_baseline_continues_accumulating(
    tmp_path, monkeypatch, capsys
):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "indeed.log"
    state = tmp_path / "state.json"

    # First poll of this run: small progress, must keep.
    _write(stream, _progress("indeed", 10, T0))
    assert supervisor.main(
        _cli_args(stream, state, 111, T0, started_at=T0.isoformat())
    ) == 0
    capsys.readouterr()

    # The counter advances: same pid + baseline must keep accumulating.
    later = T0 + timedelta(minutes=10)
    _write(stream, _progress("indeed", 10, T0), _progress("indeed", 25, later))
    assert supervisor.main(_cli_args(stream, state, 111, later)) == 0
    capsys.readouterr()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["last_parsed"] == 25
    assert saved["last_progress_at"] == later.isoformat()

    # 35 min idle keeps; 41 min blocks (baseline moved to `later`).
    keep_at = later + timedelta(minutes=35)
    assert supervisor.main(_cli_args(stream, state, 111, keep_at)) == 0
    capsys.readouterr()

    block_at = later + timedelta(minutes=41)
    rc = supervisor.main(_cli_args(stream, state, 111, block_at))
    out = capsys.readouterr().out
    assert rc == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out
    assert calls == [111]


def test_B1_no_stop_does_not_persist_a_halt(tmp_path, monkeypatch, capsys):
    calls: list[int] = []
    monkeypatch.setattr(
        supervisor, "stop_process_tree", lambda pid: calls.append(pid)
    )
    stream = tmp_path / "indeed.log"
    stream.write_text("started, no progress\n", encoding="utf-8")
    state = tmp_path / "state.json"

    rc = supervisor.main(
        _cli_args(
            stream, state, 111, T0 + timedelta(minutes=41),
            started_at=T0.isoformat(), no_stop=True,
        )
    )
    out = capsys.readouterr().out

    assert rc == supervisor.WATCHDOG_EXIT_CODE
    assert "state=blocked" in out
    assert "stopped=false" in out
    assert calls == []
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["stopped"] is False
