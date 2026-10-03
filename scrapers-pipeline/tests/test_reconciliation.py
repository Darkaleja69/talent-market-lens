"""Tests for the startup reconciliation helper (T-10; RF-4).

Offline: a driver dot-sources ``reconcile_pending_runs.ps1`` and runs it
against temporary logs/state/projects directories. Discovery uses the real
local Python CLI (``verification.recovery pending``); the recovery executor is
a fake script that records the date it received. No Azure, SAS or azcopy is
involved. Runs outside the automatic scope (T-21: historical before
2026-10-01 or superseded) are never invoked and are logged with their reason.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
RECONCILE = PIPELINE_DIR / "reconcile_pending_runs.ps1"
POWERSHELL = shutil.which("powershell.exe")
OLD_DATE = "2026-10-01"  # inside the automatic scope (incident date)
TODAY = "2026-10-02"


def _require_powershell() -> str:
    if not RECONCILE.is_file():
        pytest.fail(f"Falta {RECONCILE}; la tarea T-10 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail("powershell.exe no esta disponible.")
    return POWERSHELL


def _write_log(logs_dir: Path, run_date: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"upload-{run_date}.log"
    path.write_text(
        f"00:00:04  [INFO]  ====  Inicio pipeline scrapers  ({run_date}) ====\n"
        "00:12:45  [INFO]  [indeed] Resultado: ok (1 subidos)\n",
        encoding="utf-8",
    )
    return path


def _write_state(state_dir: Path, run_date: str, status: str = "pending") -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{run_date}.json").write_text(
        '{"schema_version": 1, "run_date": "%s", "status": "%s", "sources": {}}'
        % (run_date, status),
        encoding="utf-8",
    )


def _write_fake_recovery(tmp_path: Path, marker: Path, exit_code: int = 0) -> Path:
    path = tmp_path / "fake_recovery.ps1"
    path.write_text(
        "param([string]$Date)\n"
        f"[System.IO.File]::WriteAllText('{marker}', $Date)\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    return path


def _write_legacy_recovery(tmp_path: Path, marker: Path) -> Path:
    """Legacy executor without -Date: must never be invoked for one run."""
    path = tmp_path / "legacy_recovery.ps1"
    path.write_text(
        "param([switch]$DryRun)\n"
        f"[System.IO.File]::WriteAllText('{marker}', 'legacy')\n"
        "exit 0\n",
        encoding="utf-8",
    )
    return path


def _write_fake_cli(tmp_path: Path) -> Path:
    """Fake CLI: a warning on stderr plus a clean JSON document on stdout."""
    path = tmp_path / "fake_python.cmd"
    path.write_text(
        "@echo off\r\n"
        "echo warning de pyarrow en stderr 1>&2\r\n"
        'echo {"schema_version": 1, "before": "2026-10-01", "count": 0, "runs": []}\r\n',
        encoding="utf-8",
    )
    return path


def _run_reconciliation(
    tmp_path: Path,
    logs: Path,
    state: Path,
    projects: Path,
    recovery: Path,
    *,
    before: str = TODAY,
    python_exe: str = "python",
) -> dict[str, str]:
    log_path = tmp_path / "reconcile.log"
    driver = tmp_path / "reconcile_driver.ps1"
    driver.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "Set-StrictMode -Version Latest",
                f". '{RECONCILE}'",
                f"$logger = {{ param($Message, $Level) Add-Content -LiteralPath '{log_path}' "
                '-Value "[$Level] $Message" }',
                "$result = Invoke-PendingReconciliation "
                f"-ProjectsRoot '{projects}' -LogsDir '{logs}' -StateDir '{state}' "
                f"-Before '{before}' -RecoveryScript '{recovery}' -PythonExe '{python_exe}' "
                "-Logger $logger",
                "$invoked = $result.invoked -join ','",
                "$skipped = $result.skipped_live -join ','",
                "$skippedScope = $result.skipped_scope -join ','",
                "$failures = $result.failures -join '|'",
                'Write-Output "discovered=$($result.discovered)"',
                'Write-Output "invoked=$invoked"',
                'Write-Output "skipped_live=$skipped"',
                'Write-Output "skipped_scope=$skippedScope"',
                'Write-Output "failures=$failures"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            _require_powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(driver),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if completed.returncode != 0:
        pytest.fail(
            "PowerShell fallo ejecutando la reconciliacion "
            f"(exit {completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    parsed: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        for key in ("discovered", "invoked", "skipped_live", "skipped_scope", "failures"):
            if line.startswith(f"{key}="):
                parsed[key] = line[len(key) + 1 :]
    return parsed


def test_T10_reconciliation_invokes_recovery_for_previous_runs(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "called.txt"
    _write_log(logs, OLD_DATE)
    _write_state(state, OLD_DATE, "pending")
    _write_log(logs, TODAY)  # today's truncated run must be excluded
    recovery = _write_fake_recovery(tmp_path, marker)

    result = _run_reconciliation(tmp_path, logs, state, projects, recovery)

    assert result["discovered"] == "1"  # only the previous date
    assert result["invoked"] == OLD_DATE
    assert result["skipped_live"] == ""
    assert result["failures"] == ""
    assert marker.read_text(encoding="utf-8") == OLD_DATE


def test_T10_reconciliation_skips_while_a_wrapper_is_alive(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    lock_dir = projects / "indeed_jobs_scraper" / "output"
    lock_dir.mkdir(parents=True)
    marker = tmp_path / "called.txt"
    _write_log(logs, OLD_DATE)
    _write_state(state, OLD_DATE, "pending")
    recovery = _write_fake_recovery(tmp_path, marker)

    ps = _require_powershell()
    sleeper = subprocess.Popen([ps, "-NoProfile", "-Command", "Start-Sleep -Seconds 120"])
    try:
        (lock_dir / ".nightly.lock").write_text(str(sleeper.pid), encoding="utf-8")
        result = _run_reconciliation(tmp_path, logs, state, projects, recovery)
    finally:
        sleeper.terminate()

    assert result["invoked"] == ""
    assert result["skipped_live"] == OLD_DATE
    assert not marker.exists()


def test_T10_reconciliation_records_executor_failure_without_raising(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "called.txt"
    _write_log(logs, OLD_DATE)
    _write_state(state, OLD_DATE, "pending")
    recovery = _write_fake_recovery(tmp_path, marker, exit_code=5)

    result = _run_reconciliation(tmp_path, logs, state, projects, recovery)

    assert result["invoked"] == ""
    assert "exit 5" in result["failures"]
    assert marker.exists()  # the executor ran and failed


def test_T10_reconciliation_does_not_invoke_legacy_executor(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "legacy_called.txt"
    _write_log(logs, OLD_DATE)
    _write_state(state, OLD_DATE, "pending")
    legacy = _write_legacy_recovery(tmp_path, marker)

    result = _run_reconciliation(tmp_path, logs, state, projects, legacy)

    assert result["invoked"] == ""
    assert "T-11" in result["failures"]
    assert not marker.exists()


def test_T10_reconciliation_separates_cli_stderr(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "called.txt"
    fake_cli = _write_fake_cli(tmp_path)
    recovery = _write_fake_recovery(tmp_path, marker)

    result = _run_reconciliation(
        tmp_path, logs, state, projects, recovery, python_exe=str(fake_cli)
    )

    # The stderr warning did not corrupt the JSON document on stdout.
    assert result["discovered"] == "0"
    assert result["failures"] == ""
    # Add-Content writes the logger file in the console encoding.
    log_text = (tmp_path / "reconcile.log").read_text(encoding="utf-8", errors="replace")
    assert "aviso del descubrimiento" in log_text
    assert "warning de pyarrow" in log_text


# --------------------------------------------------------------------------
# T-21: automatic recovery scope
# --------------------------------------------------------------------------


def test_T21_reconciliation_skips_historical_run_with_reason(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "called.txt"
    historical = "2026-07-22"
    _write_log(logs, historical)
    _write_state(state, historical, "pending")
    recovery = _write_fake_recovery(tmp_path, marker)

    result = _run_reconciliation(tmp_path, logs, state, projects, recovery)

    assert result["discovered"] == "0"
    assert result["invoked"] == ""
    assert result["skipped_scope"] == historical
    assert result["failures"] == ""
    assert not marker.exists()  # never invoked
    log_text = (tmp_path / "reconcile.log").read_text(encoding="utf-8", errors="replace")
    assert "no se recupera automaticamente" in log_text
    assert "2026-10-01" in log_text  # reason names the automatic lower bound


def test_T21_reconciliation_skips_superseded_run_with_reason(tmp_path):
    logs = tmp_path / "logs"
    state = logs / "run_state"
    projects = tmp_path / "projects"
    marker = tmp_path / "called.txt"
    logs.mkdir(parents=True, exist_ok=True)
    # Same date: an earlier truncated block plus a later finished one.
    (logs / f"upload-{OLD_DATE}.log").write_text(
        f"02:00:00  [INFO]  ====  Inicio pipeline scrapers  ({OLD_DATE}) ====\n"
        "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
        f"10:00:00  [INFO]  ====  Inicio pipeline scrapers  ({OLD_DATE}) ====\n"
        "12:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n",
        encoding="utf-8",
    )
    recovery = _write_fake_recovery(tmp_path, marker)

    result = _run_reconciliation(tmp_path, logs, state, projects, recovery)

    assert result["discovered"] == "0"
    assert result["invoked"] == ""
    assert result["skipped_scope"] == OLD_DATE
    assert result["failures"] == ""
    assert not marker.exists()  # never invoked
    log_text = (tmp_path / "reconcile.log").read_text(encoding="utf-8", errors="replace")
    assert "posterior" in log_text
