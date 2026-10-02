"""Tests for the supervised pipeline wrapper (T-09; RF-7).

Offline: the real ``run_pipeline_supervised.ps1`` launches a fake child
pipeline under a temporary logs/state directory and writes its abort evidence.
No Azure, SAS, ``config.ps1`` or azcopy is involved; every temporary file lives
under pytest's ``tmp_path``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
SUPERVISOR = PIPELINE_DIR / "run_pipeline_supervised.ps1"
POWERSHELL = shutil.which("powershell.exe")
RUN_DATE = "2026-10-01"

TRUNCATED_LOG = (
    f"00:00:04  [INFO]  ====  Inicio pipeline scrapers  ({RUN_DATE}) ====\n"
    "00:00:05  [INFO]  Lanzando 4 scrapers en paralelo...\n"
)
NORMAL_LOG = TRUNCATED_LOG + (
    "04:50:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
)


def _require_powershell() -> str:
    if not SUPERVISOR.is_file():
        pytest.fail(f"Falta {SUPERVISOR}; la tarea T-09 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail(
            "powershell.exe no esta disponible; no se puede ejecutar el supervisor."
        )
    return POWERSHELL


def _write_child(tmp_path: Path, body: str) -> Path:
    child = tmp_path / "fake_pipeline.ps1"
    child.write_text(body, encoding="utf-8")
    return child


def _run_supervisor(tmp_path: Path, child: Path, log_dir: Path):
    state_dir = tmp_path / "state"
    completed = subprocess.run(
        [
            _require_powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SUPERVISOR),
            "-RunDate",
            RUN_DATE,
            "-PipelineScript",
            str(child),
            "-LogDir",
            str(log_dir),
            "-StateDir",
            str(state_dir),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    return completed, state_dir


def _write_log_child(
    tmp_path: Path, log_dir: Path, log_text: str, exit_code: int
) -> Path:
    log_path = log_dir / f"upload-{RUN_DATE}.log"
    body = "\n".join(
        [
            "$ErrorActionPreference = 'Continue'",
            "Write-Output 'salida del hijo'",
            "[Console]::Error.WriteLine('stderr del hijo')",
            f"New-Item -ItemType Directory -Force -Path '{log_dir}' | Out-Null",
            f"[System.IO.File]::WriteAllText('{log_path}', '{log_text}')",
            f"exit {exit_code}",
            "",
        ]
    )
    return _write_child(tmp_path, body)


def _read_abort(state_dir: Path) -> dict:
    path = state_dir / f"{RUN_DATE}.abort.json"
    assert path.is_file(), f"no se escribio la evidencia esperada {path}"
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), "abort.json sin BOM"
    return json.loads(path.read_text(encoding="utf-8"))


def test_T09_truncated_child_writes_abort_evidence(tmp_path):
    log_dir = tmp_path / "logs"
    child = _write_log_child(tmp_path, log_dir, TRUNCATED_LOG, exit_code=7)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = _read_abort(state_dir)
    assert payload["run_date"] == RUN_DATE
    assert payload["exit_code"] == 7
    assert payload["log_exists"] is True
    assert payload["log_path"] == str(log_dir / f"upload-{RUN_DATE}.log")
    assert payload["pipeline_pid"] > 0
    assert payload["detected_at"]
    assert "Fin pipeline" in payload["reason"]
    assert any(
        "Inicio pipeline scrapers" in line for line in payload["last_log_lines"]
    )

    transcripts = payload["transcripts"]
    assert transcripts["stdout_exists"] is True
    assert transcripts["stderr_exists"] is True
    stdout_text = Path(transcripts["stdout"]).read_text(encoding="utf-8")
    assert "salida del hijo" in stdout_text
    assert "Fin pipeline" not in stdout_text
    assert "stderr del hijo" in Path(transcripts["stderr"]).read_text(encoding="utf-8")
    assert (log_dir / f"transcript-{RUN_DATE}.log").is_file()
    assert (log_dir / f"transcript-{RUN_DATE}.err.log").is_file()

    wrappers = payload["live_wrappers"]
    assert len(wrappers) == 4
    assert {entry["name"] for entry in wrappers} == {
        "indeed",
        "linkedin",
        "multi_site",
        "infojobs",
    }
    for entry in wrappers:
        assert isinstance(entry["alive"], bool)
        assert isinstance(entry["command_line_pids"], list)

    events = payload["system_events"]
    assert isinstance(events["window_start"], str)
    assert isinstance(events["events"], list)
    assert isinstance(events["notes"], list)

    # No state file was written for this run: the snapshot is absent, not fatal.
    assert payload["state_path"] == str(state_dir / f"{RUN_DATE}.json")
    assert payload["state_exists"] is False
    assert payload["state_status"] is None
    assert payload["state_sources"] is None
    assert payload["state_error"] is None


def test_T09_abort_includes_run_state_snapshot(tmp_path):
    log_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / f"{RUN_DATE}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": RUN_DATE,
                "started_at": f"{RUN_DATE}T00:00:04.0000000+02:00",
                "finished_at": None,
                "status": "pending",
                "sources": {
                    "indeed": {
                        "status": "ok",
                        "uploaded": 1,
                        "rejected": 0,
                        "last_activity_at": f"{RUN_DATE}T02:10:47.0000000+02:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    child = _write_log_child(tmp_path, log_dir, TRUNCATED_LOG, exit_code=7)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = _read_abort(state_dir)
    assert payload["state_path"] == str(state_dir / f"{RUN_DATE}.json")
    assert payload["state_exists"] is True
    assert payload["state_status"] == "pending"
    assert payload["state_started_at"] == f"{RUN_DATE}T00:00:04.0000000+02:00"
    assert payload["state_finished_at"] is None
    assert payload["state_sources"]["indeed"]["status"] == "ok"
    assert payload["state_sources"]["indeed"]["uploaded"] == 1
    assert payload["state_error"] is None


def test_T09_abort_tolerates_unreadable_state(tmp_path):
    log_dir = tmp_path / "logs"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / f"{RUN_DATE}.json").write_text("{no es json", encoding="utf-8")
    child = _write_log_child(tmp_path, log_dir, TRUNCATED_LOG, exit_code=7)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = _read_abort(state_dir)
    assert payload["state_exists"] is True
    assert payload["state_status"] is None
    assert "ilegible" in payload["state_error"]


def test_T09_system_events_window_starts_at_the_last_inicio(tmp_path):
    log_dir = tmp_path / "logs"
    two_blocks = (
        f"00:00:04  [INFO]  ====  Inicio pipeline scrapers  ({RUN_DATE}) ====\n"
        "01:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
        f"05:00:00  [INFO]  ====  Inicio pipeline scrapers  ({RUN_DATE}) ====\n"
        "05:01:00  [INFO]  [indeed] Aun corriendo...\n"
    )
    child = _write_log_child(tmp_path, log_dir, two_blocks, exit_code=7)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = _read_abort(state_dir)
    assert payload["system_events"]["window_start"].startswith(
        f"{RUN_DATE}T05:00:00"
    )


def test_T09_missing_pipeline_script_exits_2(tmp_path):
    log_dir = tmp_path / "logs"
    missing = tmp_path / "no_existe.ps1"

    completed, state_dir = _run_supervisor(tmp_path, missing, log_dir)

    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "no existe" in completed.stdout
    assert not (state_dir / f"{RUN_DATE}.abort.json").exists()


def test_T09_normal_closure_writes_no_abort(tmp_path):
    log_dir = tmp_path / "logs"
    child = _write_log_child(tmp_path, log_dir, NORMAL_LOG, exit_code=0)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (state_dir / f"{RUN_DATE}.abort.json").exists()
    transcript = log_dir / f"transcript-{RUN_DATE}.log"
    assert transcript.is_file()
    assert "salida del hijo" in transcript.read_text(encoding="utf-8")


def test_T09_normal_closure_propagates_child_exit_code(tmp_path):
    log_dir = tmp_path / "logs"
    child = _write_log_child(tmp_path, log_dir, NORMAL_LOG, exit_code=3)

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 3, completed.stdout + completed.stderr
    assert not (state_dir / f"{RUN_DATE}.abort.json").exists()


def test_T09_child_without_log_is_anomalous_evidence(tmp_path):
    log_dir = tmp_path / "logs"
    child = _write_child(tmp_path, "exit 2\n")

    completed, state_dir = _run_supervisor(tmp_path, child, log_dir)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = _read_abort(state_dir)
    assert payload["exit_code"] == 2
    assert payload["log_exists"] is False
    assert payload["last_log_lines"] == []
