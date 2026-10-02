"""Tests for the retention-aware cleanup (T-13; RF-2).

Offline: the real ``cleanup_old_data.ps1`` runs with a fake ``python`` CLI
(``-PythonExe``) that emulates ``verification.recovery pending/expire`` under a
temporary projects root. No real data, Azure or credentials are involved.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
CLEANUP = PIPELINE_DIR / "cleanup_old_data.ps1"
POWERSHELL = shutil.which("powershell.exe")


def _require_powershell() -> str:
    if not CLEANUP.is_file():
        pytest.fail(f"Falta {CLEANUP}; la tarea T-13 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail("powershell.exe no esta disponible.")
    return POWERSHELL


def _write_fake_cli(tmp_path: Path, mode: str) -> Path:
    """Fake python CLI: pending -> no runs; expire -> keep/expire all.

    It is argument-aware on purpose: it reads the file paths after ``--out``
    and ``--candidates`` and exits 2 when they are missing, so a cleanup call
    with a wrong flag (the T-13 blocker) fails the test instead of silently
    "working".
    """
    script = tmp_path / f"fake_python_{mode}.ps1"
    if mode == "fail":
        pending_body = "exit 1"
    else:
        pending_body = (
            "[System.IO.File]::WriteAllText($out, "
            "'{\"schema_version\": 1, \"count\": 0, \"runs\": []}'); exit 0"
        )
    if mode == "keep":
        action, reason, pending_flag = "keep", "run pendiente (prueba)", "$true"
    else:
        action, reason, pending_flag = "expire", "fuera de retencion (prueba)", "$false"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)",
                "$command = $Rest[2]",
                "$outIndex = [array]::IndexOf($Rest, '--out')",
                "if ($outIndex -lt 0 -or $outIndex + 1 -ge $Rest.Count) { exit 2 }",
                "$out = $Rest[$outIndex + 1]",
                "if ($command -eq 'pending') {",
                f"    {pending_body}",
                "}",
                "if ($command -eq 'expire') {",
                "    $candIndex = [array]::IndexOf($Rest, '--candidates')",
                "    if ($candIndex -lt 0 -or $candIndex + 1 -ge $Rest.Count) { exit 2 }",
                "    $candidatesFile = $Rest[$candIndex + 1]",
                "    $payload = Get-Content -LiteralPath $candidatesFile -Raw | ConvertFrom-Json",
                "    $decisions = @()",
                "    foreach ($c in @($payload.candidates)) {",
                "        $decisions += @{"
                f" path = $c.path; action = '{action}'; reason = '{reason}';"
                f" file_date = '2026-01-01'; pending = {pending_flag} }}",
                "    }",
                "    [System.IO.File]::WriteAllText($out, (@{"
                " schema_version = 1; decisions = $decisions;"
                " expire = @(); keep = @() } | ConvertTo-Json -Depth 5))",
                "    exit 0",
                "}",
                "exit 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _run_cleanup(
    tmp_path: Path, python_exe: str, projects: Path, *, retention_days: int = 7
):
    return subprocess.run(
        [
            _require_powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(CLEANUP),
            "-ProjectsRoot",
            str(projects),
            "-LogDir",
            str(tmp_path / "logs"),
            "-PythonExe",
            python_exe,
            "-RetentionDays",
            str(retention_days),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    projects = tmp_path / "projects"
    out_dir = projects / "linkedin_jobs_scraper" / "data" / "output"
    out_dir.mkdir(parents=True)
    canonical = out_dir / "jobs.parquet"
    canonical.write_text("canonical", encoding="utf-8")
    latest = out_dir / "jobs_20260102_020202.parquet"
    latest.write_text("latest", encoding="utf-8")
    candidate = out_dir / "jobs_20260101_010101.parquet"
    candidate.write_text("old", encoding="utf-8")
    old = datetime(2020, 1, 1).timestamp()
    for path in (canonical, latest, candidate):
        os.utime(path, (old, old))
    return projects, canonical, latest, candidate


def _log_text(tmp_path: Path) -> str:
    return (tmp_path / "logs" / "cleanup.log").read_text(
        encoding="utf-8-sig", errors="replace"
    )


def test_T13_cleanup_keeps_pending_candidates(tmp_path):
    projects, canonical, latest, candidate = _fixture(tmp_path)
    fake = _write_fake_cli(tmp_path, "keep")

    completed = _run_cleanup(tmp_path, str(fake), projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert candidate.is_file()  # conserved: pending within retention
    assert latest.is_file()
    assert canonical.is_file()
    log_text = _log_text(tmp_path)
    assert "CONSERVA [pendiente]" in log_text
    assert "BORRA" not in log_text


def test_T13_cleanup_expires_candidates_beyond_retention(tmp_path):
    projects, canonical, latest, candidate = _fixture(tmp_path)
    fake = _write_fake_cli(tmp_path, "expire")

    completed = _run_cleanup(tmp_path, str(fake), projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not candidate.exists()  # expired
    assert latest.is_file()  # the most recent is always kept
    assert canonical.is_file()  # canonical output is never touched
    log_text = _log_text(tmp_path)
    assert "BORRA" in log_text
    assert "fuera de retencion" in log_text


def test_T13_cleanup_is_conservative_when_cli_fails(tmp_path):
    projects, canonical, latest, candidate = _fixture(tmp_path)
    fake = _write_fake_cli(tmp_path, "fail")

    completed = _run_cleanup(tmp_path, str(fake), projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert candidate.is_file()  # nothing was deleted
    assert latest.is_file()
    assert canonical.is_file()
    assert "no se borra nada" in _log_text(tmp_path)


def test_T13_cleanup_end_to_end_with_the_real_cli(tmp_path):
    """Real `python` CLI: an old non-pending candidate expires, a pending-run
    candidate is kept, and canonical/latest stay untouched."""
    projects = tmp_path / "projects"
    out_dir = projects / "linkedin_jobs_scraper" / "data" / "output"
    out_dir.mkdir(parents=True)

    today = datetime.now().date()
    pending_day = today - timedelta(days=1)
    old_day = today - timedelta(days=30)

    canonical = out_dir / "jobs.parquet"
    canonical.write_text("canonical", encoding="utf-8")
    latest = out_dir / f"jobs_{today.strftime('%Y%m%d')}_000000.parquet"
    latest.write_text("latest", encoding="utf-8")
    pending_candidate = out_dir / f"jobs_{pending_day.strftime('%Y%m%d')}_010101.parquet"
    pending_candidate.write_text("pending", encoding="utf-8")
    old_candidate = out_dir / f"jobs_{old_day.strftime('%Y%m%d')}_010101.parquet"
    old_candidate.write_text("old", encoding="utf-8")

    # The pending candidate's name carries yesterday's date (within retention)
    # but its mtime is old enough to be a cleanup candidate.
    pending_stamp = (datetime.now() - timedelta(days=2)).timestamp()
    old_stamp = (datetime.now() - timedelta(days=30)).timestamp()
    latest_stamp = datetime.now().timestamp()
    os.utime(canonical, (old_stamp, old_stamp))
    os.utime(latest, (latest_stamp, latest_stamp))
    os.utime(pending_candidate, (pending_stamp, pending_stamp))
    os.utime(old_candidate, (old_stamp, old_stamp))

    # A pending run for yesterday's date in the cleanup state dir.
    state_dir = tmp_path / "logs" / "run_state"
    state_dir.mkdir(parents=True)
    (state_dir / f"{pending_day}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": str(pending_day),
                "started_at": f"{pending_day}T00:00:00.0000000+02:00",
                "finished_at": None,
                "status": "pending",
                "sources": {},
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cleanup(tmp_path, "python", projects, retention_days=7)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not old_candidate.exists()  # outside retention
    assert pending_candidate.is_file()  # pending run within retention
    assert latest.is_file()
    assert canonical.is_file()
    log_text = _log_text(tmp_path)
    assert "BORRA" in log_text
    assert "fuera de la retencion" in log_text
    assert "CONSERVA [pendiente]" in log_text
