"""Tests for truncated-run discovery (T-05; RF-1).

Offline and pure: synthetic general logs and T-03 state files under pytest's
``tmp_path``. No PowerShell, no Azure and no network are involved; the state
files are written as plain JSON in the same schema the pipeline persists.
"""
from __future__ import annotations

import json
from pathlib import Path

from verification import recovery

DATE_OLD = "2026-09-30"
DATE_NEW = "2026-10-01"


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------


def _inicio(run_date: str, at: str = "00:00:04") -> str:
    return f"{at}  [INFO]  ====  Inicio pipeline scrapers  ({run_date}) ====\n"


def _fin(at: str = "03:00:00") -> str:
    return f"{at}  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"


def _write_log(logs_dir: Path, run_date: str, text: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"upload-{run_date}.log"
    path.write_text(text, encoding="utf-8")
    return path


def _state_payload(
    run_date: str,
    status: str = "pending",
    sources=None,
    started_at: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "run_date": run_date,
        "started_at": started_at or f"{run_date}T00:00:04.0000000+02:00",
        "finished_at": (
            None if status == "pending" else f"{run_date}T03:00:00.0000000+02:00"
        ),
        "status": status,
        "sources": sources or {},
    }


def _write_state_at(
    state_dir: Path,
    run_date: str,
    status: str = "pending",
    sources=None,
    payload: object | None = None,
    started_at: str | None = None,
) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{run_date}.json"
    data = (
        payload
        if payload is not None
        else _state_payload(run_date, status, sources, started_at)
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _write_state(
    logs_dir: Path,
    run_date: str,
    status: str = "pending",
    sources=None,
    started_at: str | None = None,
) -> Path:
    return _write_state_at(
        logs_dir / "run_state", run_date, status, sources, started_at=started_at
    )


def _truncated_text(run_date: str) -> str:
    return _inicio(run_date) + "00:12:45  [INFO]  [indeed] Resultado: ok (1 subidos)\n"


# --------------------------------------------------------------------------
# Truncated runs (log signal)
# --------------------------------------------------------------------------


def test_T05_truncated_log_without_state_is_detected(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.run_date == DATE_NEW
    assert run.log_completed is False
    assert run.superseded is False
    assert run.state_attributed is True
    assert run.log_path is not None
    assert run.started_at == f"{DATE_NEW}T00:00:04"
    assert run.summary is not None
    assert run.summary.completed is False
    assert run.state.status == recovery.STATE_MISSING
    assert "Fin pipeline" in run.reason


def test_T05_truncated_log_with_pending_state_is_detected(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    sources = {
        "indeed": {
            "status": "ok",
            "uploaded": 1,
            "rejected": 0,
            "last_activity_at": "2026-10-01T00:12:45.0000000+02:00",
        }
    }
    _write_state(logs, DATE_NEW, "pending", sources)

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.superseded is False
    assert run.state_attributed is True
    assert run.state.status == recovery.STATE_PENDING
    assert run.state.sources["indeed"]["status"] == "ok"
    assert "pending" in run.reason


def test_T05_unreadable_state_does_not_crash(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    _write_log(logs, DATE_OLD, _truncated_text(DATE_OLD))
    state_dir = logs / "run_state"
    state_dir.mkdir(parents=True)
    # Corrupt JSON and a valid JSON that is not a state object.
    (state_dir / f"{DATE_NEW}.json").write_text("{no es json", encoding="utf-8")
    (state_dir / f"{DATE_OLD}.json").write_text("[1, 2, 3]", encoding="utf-8")

    runs = recovery.discover_truncated_runs(logs)

    assert [run.run_date for run in runs] == [DATE_OLD, DATE_NEW]
    assert all(run.state.status == recovery.STATE_UNREADABLE for run in runs)
    assert all(run.state.error for run in runs)
    assert all("ilegible" in run.reason for run in runs)


def test_T05_unknown_state_status_is_still_a_candidate(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    payload = _state_payload(DATE_NEW, "pending")
    payload["status"] = "raro"
    payload["extra_key"] = {"anything": True}
    _write_state_at(logs / "run_state", DATE_NEW, payload=payload)

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    assert runs[0].state.status == recovery.STATE_UNKNOWN
    assert "sin status reconocible" in runs[0].reason


# --------------------------------------------------------------------------
# Not truncated (precedence and coherence)
# --------------------------------------------------------------------------


def test_T05_completed_log_with_closed_state_is_not_returned(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _inicio(DATE_NEW) + _fin())
    _write_state(logs, DATE_NEW, "closed")

    assert recovery.discover_truncated_runs(logs) == []


def test_T05_completed_log_with_pending_state_is_not_recoverable(tmp_path):
    # Precedence: a Fin log means the run finished; the pending state is a
    # state-reconciliation case (T-10), never a data-recovery one.
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _inicio(DATE_NEW) + _fin())
    _write_state(logs, DATE_NEW, "pending")

    assert recovery.discover_truncated_runs(logs) == []


def test_T05_truncated_log_with_closed_state_is_not_returned(tmp_path):
    # Single block: the closed state is attributable to it, so the truncated
    # log is not proposed again (a previous recovery already closed it).
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    _write_state(logs, DATE_NEW, "closed")

    assert recovery.discover_truncated_runs(logs) == []


def test_T05_empty_directories_return_nothing(tmp_path):
    assert recovery.discover_truncated_runs(tmp_path / "logs") == []

    empty = tmp_path / "empty_logs"
    empty.mkdir()
    assert recovery.discover_truncated_runs(empty) == []


# --------------------------------------------------------------------------
# Several runs the same day: every truncated block is returned
# --------------------------------------------------------------------------


def test_T05_last_block_truncated_marks_the_date(tmp_path):
    logs = tmp_path / "logs"
    text = (
        _inicio(DATE_NEW, "00:00:04")
        + _fin("01:00:00")
        + _inicio(DATE_NEW, "02:00:00")
        + "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
    )
    _write_log(logs, DATE_NEW, text)
    _write_state(
        logs, DATE_NEW, "pending", started_at=f"{DATE_NEW}T02:00:04.0000000+02:00"
    )

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.started_at == f"{DATE_NEW}T02:00:00"
    assert run.superseded is False
    assert run.state_attributed is True
    assert run.summary is not None
    assert run.summary.completed is False


def test_T05_later_finished_block_does_not_hide_the_truncated_one(tmp_path):
    # 02:00 truncated + 10:00 finished: the later run did not pick up the
    # earlier run's files, so the truncated block must still be returned.
    logs = tmp_path / "logs"
    text = (
        _inicio(DATE_NEW, "02:00:00")
        + "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
        + _inicio(DATE_NEW, "10:00:00")
        + _fin("12:00:00")
    )
    _write_log(logs, DATE_NEW, text)
    # The date's state belongs to the 10:00 run (it overwrote the 02:00 one).
    _write_state(
        logs, DATE_NEW, "closed", started_at=f"{DATE_NEW}T10:00:04.0000000+02:00"
    )

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.started_at == f"{DATE_NEW}T02:00:00"
    assert run.superseded is True
    assert run.state_attributed is False
    assert run.state.status == recovery.STATE_CLOSED
    assert "otro run" in run.reason
    assert "posterior" in run.reason


def test_T05_closed_state_attributed_to_the_truncated_block_excludes_it(tmp_path):
    logs = tmp_path / "logs"
    text = (
        _inicio(DATE_NEW, "02:00:00")
        + "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
        + _inicio(DATE_NEW, "10:00:00")
        + _fin("12:00:00")
    )
    _write_log(logs, DATE_NEW, text)
    # The closed state belongs to the 02:00 truncated block itself.
    _write_state(
        logs, DATE_NEW, "closed", started_at=f"{DATE_NEW}T02:00:04.0000000+02:00"
    )

    assert recovery.discover_truncated_runs(logs) == []


def test_T05_crossed_second_does_not_misattribute_the_later_state(tmp_path):
    # The pipeline captures $StartTime before writing its "Inicio" line, so a
    # crossed second can place the 10:00 run's state at 09:59:59. The
    # tolerance must attribute it to the 10:00 block, not to the truncated
    # 02:00 one, so the earlier truncated run is not hidden.
    logs = tmp_path / "logs"
    text = (
        _inicio(DATE_NEW, "02:00:00")
        + "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
        + _inicio(DATE_NEW, "10:00:00")
        + _fin("12:00:00")
    )
    _write_log(logs, DATE_NEW, text)
    _write_state(
        logs, DATE_NEW, "closed", started_at=f"{DATE_NEW}T09:59:59.0000000+02:00"
    )

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.started_at == f"{DATE_NEW}T02:00:00"
    assert run.superseded is True
    assert run.state_attributed is False
    assert run.state.status == recovery.STATE_CLOSED
    assert "otro run" in run.reason


def test_T05_two_truncated_blocks_same_day_are_both_returned(tmp_path):
    logs = tmp_path / "logs"
    text = (
        _inicio(DATE_NEW, "02:00:00")
        + "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
        + _inicio(DATE_NEW, "10:00:00")
        + "10:30:00  [INFO]  [linkedin] Aun corriendo...\n"
    )
    _write_log(logs, DATE_NEW, text)
    # The pending state belongs to the 10:00 run.
    _write_state(
        logs, DATE_NEW, "pending", started_at=f"{DATE_NEW}T10:00:04.0000000+02:00"
    )

    runs = recovery.discover_truncated_runs(logs)

    assert [run.started_at for run in runs] == [
        f"{DATE_NEW}T02:00:00",
        f"{DATE_NEW}T10:00:00",
    ]
    assert [run.superseded for run in runs] == [False, False]
    assert [run.state_attributed for run in runs] == [False, True]
    assert runs[1].state.status == recovery.STATE_PENDING
    assert "otro run" in runs[0].reason


# --------------------------------------------------------------------------
# State-only signal, ordering, explicit state_dir and invalid dates
# --------------------------------------------------------------------------


def test_T05_state_only_pending_without_log_is_detected(tmp_path):
    logs = tmp_path / "logs"
    _write_state(logs, DATE_NEW, "pending", {"indeed": {"status": "ok"}})

    runs = recovery.discover_truncated_runs(logs)

    assert len(runs) == 1
    run = runs[0]
    assert run.run_date == DATE_NEW
    assert run.log_path is None
    assert run.summary is None
    assert run.superseded is False
    assert run.state_attributed is True
    assert run.state.status == recovery.STATE_PENDING
    assert run.started_at == f"{DATE_NEW}T00:00:04.0000000+02:00"


def test_T05_state_only_ignores_invalid_dates(tmp_path):
    logs = tmp_path / "logs"
    state_dir = logs / "run_state"
    # The JSON run_date is not a date: the valid file name is the fallback.
    payload = _state_payload(DATE_NEW, "pending")
    payload["run_date"] = "banana"
    _write_state_at(state_dir, DATE_NEW, payload=payload)
    # An invalid calendar file name is ignored even when it says pending.
    _write_state_at(state_dir, "2026-13-45", "pending")

    runs = recovery.discover_truncated_runs(logs)

    assert [run.run_date for run in runs] == [DATE_NEW]
    assert runs[0].state.run_date is None  # invalid date dropped


def test_T05_results_are_sorted_ascending_by_date(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    _write_log(logs, DATE_OLD, _truncated_text(DATE_OLD))

    runs = recovery.discover_truncated_runs(logs)

    assert [run.run_date for run in runs] == [DATE_OLD, DATE_NEW]


def test_T05_explicit_state_dir_is_used(tmp_path):
    logs = tmp_path / "logs"
    _write_log(logs, DATE_NEW, _truncated_text(DATE_NEW))
    states = tmp_path / "states"
    _write_state_at(states, DATE_NEW, "pending")

    runs = recovery.discover_truncated_runs(logs, states)

    assert len(runs) == 1
    assert runs[0].state.path == str(states / f"{DATE_NEW}.json")
    assert runs[0].state.status == recovery.STATE_PENDING
