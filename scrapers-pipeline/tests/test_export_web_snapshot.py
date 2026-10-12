"""Tests for the local web-export snapshot script (T-16; RF-11).

Offline: ``export_web_snapshot.ps1`` runs with a fake AzCopy (a .ps1 that
records every call, answers ``list`` from a fixture directory and copies from
the simulated remote tree) and a fake Python (for ``-Serve``). No Azure, real
SAS or download is used: the SAS env var is a dummy and every temporary file
lives under pytest's ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
SNAPSHOT = PIPELINE_DIR / "export_web_snapshot.ps1"
POWERSHELL = shutil.which("powershell.exe")
SAS = "?sv=test&sig=dummy-sas-secret"
FILES = [
    "fact_offers.parquet",
    "fact_offer_skills.parquet",
    "dim_skill_list.parquet",
    "dim_calendar.parquet",
    "meta.json",
]
META = {
    "export_version": 1,
    "data_date": "2026-10-09",
    "generated_at": "2026-10-10T12:53:04Z",
    "mode": "full",
}


def _require_script() -> str:
    if not SNAPSHOT.is_file():
        pytest.fail(f"Falta {SNAPSHOT}; la tarea T-16 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail("powershell.exe no esta disponible.")
    return POWERSHELL


def _write_remote(tmp_path: Path) -> Path:
    """Simulated remote export: the five fixed files under gold/web_export."""
    remote = tmp_path / "remote" / "gold" / "web_export"
    remote.mkdir(parents=True)
    for name in FILES[:-1]:
        (remote / name).write_bytes(b"x" * 100)
    (remote / "meta.json").write_text(json.dumps(META), encoding="utf-8")
    return remote


def _write_fake_azcopy(tmp_path: Path, remote: Path, *, exit_code: int = 0) -> Path:
    """Fake AzCopy: records calls, answers ``list`` and copies from ``remote``."""
    calls = tmp_path / "azcopy_calls.log"
    script = tmp_path / "fake_azcopy.ps1"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)",
                f"Add-Content -LiteralPath '{calls}' -Value ($AzArgs -join '|')",
                "if ($AzArgs.Count -ge 1 -and $AzArgs[0] -eq 'list') {",
                f"    Get-ChildItem -LiteralPath '{remote}' -File | ForEach-Object {{",
                '        Write-Output ("INFO: $($_.Name); Content Length: $($_.Length)")',
                "    }",
                "    exit 0",
                "}",
                "if ($AzArgs.Count -ge 3 -and $AzArgs[0] -eq 'copy') {",
                "    $src = ($AzArgs[1] -split '\\?')[0]",
                "    $name = Split-Path -Leaf $src",
                "    $dest = $AzArgs[2]",
                "    if (Test-Path -LiteralPath $dest -PathType Container) {",
                "        $dest = Join-Path $dest $name",
                "    }",
                f"    Copy-Item -LiteralPath (Join-Path '{remote}' $name) -Destination $dest -Force",
                "}",
                f"exit {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _write_fake_python(tmp_path: Path) -> tuple[Path, Path]:
    """Fake Python that records its arguments and exits 0 (``-Serve`` test)."""
    log = tmp_path / "python_calls.log"
    script = tmp_path / "fake_python.cmd"
    script.write_text(
        "@echo off\r\n" f'echo %* > "{log}"\r\n' "exit /b 0\r\n",
        encoding="ascii",
    )
    return script, log


def _run_snapshot(
    tmp_path: Path,
    *,
    azcopy: Path,
    extra: list[str] | None = None,
    python_exe: Path | None = None,
    env: dict[str, str] | None = None,
):
    powershell = _require_script()
    docs_dir = tmp_path / "docs"
    args = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SNAPSHOT),
        "-AzCopyPath",
        str(azcopy),
        "-StorageAccount",
        "testaccount",
        "-Container",
        "landing",
        "-DocsDir",
        str(docs_dir),
        "-DataDir",
        str(docs_dir / "data"),
    ]
    if python_exe is not None:
        args += ["-PythonExe", str(python_exe)]
    if extra:
        args += extra
    if env is None:
        env = {**os.environ, "LANDING_SAS_TOKEN": SAS}
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )


def _copy_calls(tmp_path: Path) -> list[str]:
    calls_log = tmp_path / "azcopy_calls.log"
    if not calls_log.is_file():
        return []
    return [
        line
        for line in calls_log.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("copy|")
    ]


def test_T16_dry_run_prints_masked_plan_without_copying(tmp_path):
    remote = _write_remote(tmp_path)
    azcopy = _write_fake_azcopy(tmp_path, remote)

    completed = _run_snapshot(tmp_path, azcopy=azcopy, extra=["-DryRun"])

    assert completed.returncode == 0, completed.stdout + completed.stderr
    out = completed.stdout
    assert "<SAS oculta>" in out
    assert SAS not in out
    assert "no se descarga" in out
    for name in FILES:
        assert name in out
    # Remote sizes were listed (all parquet fixtures are 100 bytes).
    assert "100 B" in out
    assert "desconocido" not in out
    # Nothing was copied and no local snapshot was created.
    assert _copy_calls(tmp_path) == []
    assert not (tmp_path / "docs" / "data").exists()


def test_T16_download_writes_five_files_and_reports_data_date(tmp_path):
    remote = _write_remote(tmp_path)
    azcopy = _write_fake_azcopy(tmp_path, remote)

    completed = _run_snapshot(tmp_path, azcopy=azcopy)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = tmp_path / "docs" / "data"
    for name in FILES:
        target = data / name
        assert target.is_file(), name
        assert target.read_bytes() == (remote / name).read_bytes(), name
    assert "data_date: 2026-10-09" in completed.stdout
    assert "modo: full" in completed.stdout
    assert SAS not in completed.stdout

    copies = _copy_calls(tmp_path)
    assert len(copies) == 5
    assert all("--overwrite=true" in call for call in copies)
    copied_names = {Path(call.split("|")[2]).name for call in copies}
    assert copied_names == set(FILES)
    # Every source carried the SAS (blob source) and the script masked it.
    assert all(SAS in call for call in copies)


def test_T16_missing_sas_fails_clearly_without_copying(tmp_path):
    remote = _write_remote(tmp_path)
    azcopy = _write_fake_azcopy(tmp_path, remote)
    env = {**os.environ, "LANDING_SAS_TOKEN": ""}

    completed = _run_snapshot(tmp_path, azcopy=azcopy, env=env)

    assert completed.returncode == 2
    combined = completed.stdout + completed.stderr
    assert "LANDING_SAS_TOKEN" in combined
    assert _copy_calls(tmp_path) == []
    assert not (tmp_path / "docs" / "data").exists()


def test_T16_dry_run_degrades_when_azcopy_is_missing(tmp_path):
    missing = tmp_path / "missing_azcopy.exe"

    completed = _run_snapshot(tmp_path, azcopy=missing, extra=["-DryRun"])

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "AzCopy" in completed.stdout
    assert "desconocido" in completed.stdout
    for name in FILES:
        assert name in completed.stdout


def test_T16_missing_azcopy_fails_the_real_run(tmp_path):
    missing = tmp_path / "missing_azcopy.exe"

    completed = _run_snapshot(tmp_path, azcopy=missing)

    assert completed.returncode == 2
    combined = completed.stdout + completed.stderr
    assert "AzCopy" in combined
    assert not (tmp_path / "docs" / "data").exists()


def test_T16_azcopy_failure_is_reported_and_exits_nonzero(tmp_path):
    remote = _write_remote(tmp_path)
    azcopy = _write_fake_azcopy(tmp_path, remote, exit_code=1)

    completed = _run_snapshot(tmp_path, azcopy=azcopy)

    assert completed.returncode == 1
    assert "fallo" in completed.stdout.lower()
    assert SAS not in completed.stdout


def test_T16_serve_runs_python_http_server_on_docs(tmp_path):
    fake_python, python_log = _write_fake_python(tmp_path)
    docs = tmp_path / "docs"
    (docs / "dashboard").mkdir(parents=True)
    (docs / "dashboard" / "index.html").write_text("<html></html>", encoding="utf-8")
    missing = tmp_path / "missing_azcopy.exe"

    completed = _run_snapshot(
        tmp_path,
        azcopy=missing,
        python_exe=fake_python,
        extra=["-DryRun", "-Serve", "-Port", "9123"],
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    recorded = python_log.read_text(encoding="utf-8", errors="replace").strip()
    assert "-m http.server 9123" in recorded
    assert f"--directory {docs}" in recorded
    assert "http://127.0.0.1:9123/dashboard/" in completed.stdout
    # The plan still ran (dry run) and nothing was downloaded.
    assert _copy_calls(tmp_path) == []
