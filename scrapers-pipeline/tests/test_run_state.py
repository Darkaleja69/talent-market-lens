"""Tests for the shared run-state helper ``run_state.ps1`` (T-03; RF-1).

Offline: each test drives Windows PowerShell 5.1 (``-NoProfile
-ExecutionPolicy Bypass``) against a temporary state directory, then parses the
resulting JSON and checks the documented schema, the ``pending`` -> ``closed``
levels, the atomic write (no BOM, no leftover temp files) and the per-source
update. Nothing touches Azure or needs credentials. If PowerShell or the helper
is missing, the test fails with a clear message instead of being skipped.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parents[1]
HELPER = PIPELINE_DIR / "run_state.ps1"
POWERSHELL = shutil.which("powershell.exe")
RUN_DATE = "2026-10-01"
ISO_8601 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$"
)


def _require_powershell() -> str:
    if not HELPER.is_file():
        pytest.fail(f"Falta el helper {HELPER}; la tarea T-03 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail(
            "powershell.exe no esta disponible; no se puede ejecutar run_state.ps1."
        )
    return POWERSHELL


def _write_driver(tmp_path: Path, state_dir: Path, body: str) -> Path:
    """Creates a .ps1 driver that dot-sources the helper under strict mode."""
    helper = str(HELPER).replace("'", "''")
    directory = str(state_dir).replace("'", "''")
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "Set-StrictMode -Version Latest",
            "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }",
            f". '{helper}'",
            f"$StateDir = '{directory}'",
            body,
            "",
        ]
    )
    driver = tmp_path / "driver.ps1"
    driver.write_text(script, encoding="utf-8")
    return driver


def _check_completed(completed: subprocess.CompletedProcess) -> None:
    if completed.returncode != 0:
        pytest.fail(
            "PowerShell fallo ejecutando el helper run_state.ps1 "
            f"(exit {completed.returncode}):\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )


def _run_driver(driver: Path) -> None:
    ps = _require_powershell()
    completed = subprocess.run(
        [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    _check_completed(completed)


def _wait_for_temp_file(
    state_dir: Path, proc: subprocess.Popen, timeout: float = 30.0
) -> bool:
    """Waits until the helper has created its .tmp file next to the state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(path.name.endswith(".tmp") for path in state_dir.iterdir()):
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.002)
    return False


def _read_state(state_dir: Path) -> dict:
    path = state_dir / f"{RUN_DATE}.json"
    if not path.is_file():
        pytest.fail(f"No se creo el fichero de estado esperado: {path}")
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "El JSON del estado no debe llevar BOM (UTF-8)"
    assert not raw.startswith(b"\xff\xfe"), "El JSON del estado no debe llevar BOM (UTF-16 LE)"
    assert not raw.startswith(b"\xfe\xff"), "El JSON del estado no debe llevar BOM (UTF-16 BE)"
    state = json.loads(raw.decode("utf-8"))
    assert isinstance(state, dict)
    return state


def _assert_only_state_files(state_dir: Path) -> None:
    leftovers = sorted(path.name for path in state_dir.iterdir())
    assert leftovers == [f"{RUN_DATE}.json"], (
        f"El directorio de estado debe contener solo {RUN_DATE}.json; hay: {leftovers}"
    )


def test_T03_new_state_is_pending_with_documented_schema(tmp_path):
    state_dir = tmp_path / "run_state"  # does not exist: the helper must create it
    driver = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(driver)

    assert state_dir.is_dir(), "Write-RunState debe crear el directorio de estado"
    state = _read_state(state_dir)
    _assert_only_state_files(state_dir)

    assert set(state) == {
        "schema_version",
        "run_date",
        "started_at",
        "finished_at",
        "status",
        "sources",
    }
    assert state["schema_version"] == 1
    assert state["run_date"] == RUN_DATE
    assert state["status"] == "pending"
    assert state["finished_at"] is None
    assert state["sources"] == {}
    assert ISO_8601.match(state["started_at"]), state["started_at"]


def test_T03_source_update_round_trips_status_counts_and_activity(tmp_path):
    # `sources` is deliberately removed and then damaged: Set-RunSourceState
    # must reinitialize the container instead of failing on a null index.
    state_dir = tmp_path / "run_state"
    driver = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "$state.Remove('sources')\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'failed'\n"
        "$state['sources'] = 'dano'\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'partial' "
        "-Uploaded 4 -Rejected 1 "
        "-LastActivityAt '2026-10-01T01:59:29.1234567+02:00'\n"
        "Set-RunSourceState -State $state -Source 'linkedin' -Status 'no_data'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(driver)

    state = _read_state(state_dir)
    _assert_only_state_files(state_dir)

    assert state["status"] == "pending"
    assert set(state["sources"]) == {"indeed", "linkedin"}
    assert state["sources"]["indeed"] == {
        "status": "partial",
        "uploaded": 4,
        "rejected": 1,
        "last_activity_at": "2026-10-01T01:59:29.1234567+02:00",
    }
    linkedin = state["sources"]["linkedin"]
    assert linkedin["status"] == "no_data"
    assert linkedin["uploaded"] == 0
    assert linkedin["rejected"] == 0
    assert ISO_8601.match(linkedin["last_activity_at"]), linkedin["last_activity_at"]


def test_T03_close_marks_run_closed_with_finished_at(tmp_path):
    state_dir = tmp_path / "run_state"
    driver = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Set-RunSourceState -State $state -Source 'multi_site' -Status 'ok' "
        "-Uploaded 1\n"
        "Close-RunState -State $state -FinishedAt '2026-10-01T02:10:00.0000000+02:00'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(driver)

    state = _read_state(state_dir)
    _assert_only_state_files(state_dir)

    assert state["status"] == "closed"
    assert state["finished_at"] == "2026-10-01T02:10:00.0000000+02:00"
    assert state["sources"]["multi_site"]["status"] == "ok"
    assert state["sources"]["multi_site"]["uploaded"] == 1


def test_T03_atomic_write_replaces_without_bom_or_temp_leftovers(tmp_path):
    state_dir = tmp_path / "run_state"
    # First write: a truncated-looking pending run.
    first = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'failed'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(first)
    assert _read_state(state_dir)["status"] == "pending"
    _assert_only_state_files(state_dir)

    # Second write over the same file: the recovered/closed run.
    second = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'ok' -Uploaded 2\n"
        "Close-RunState -State $state -FinishedAt '2026-10-01T03:00:00.0000000+02:00'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(second)

    state = _read_state(state_dir)
    _assert_only_state_files(state_dir)
    assert state["status"] == "closed"
    assert state["sources"]["indeed"]["status"] == "ok"
    assert state["sources"]["indeed"]["uploaded"] == 2


def test_T03_write_succeeds_while_a_reader_holds_the_destination(tmp_path):
    """The atomic write must survive a transient reader lock (retry loop).

    CPython opens files without FILE_SHARE_DELETE, so while the handle is open
    File.Replace fails with a sharing violation (verified on this platform) and
    the destination is never left missing or half-written. The handle is held
    until the helper's temp file has appeared and at least one replace attempt
    has failed; the success after releasing it is therefore due to the retry
    loop, not to a lucky first attempt.
    """
    state_dir = tmp_path / "run_state"
    first = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'failed'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )
    _run_driver(first)
    target = state_dir / f"{RUN_DATE}.json"

    second = _write_driver(
        tmp_path,
        state_dir,
        f"$state = New-RunState -RunDate '{RUN_DATE}'\n"
        "Set-RunSourceState -State $state -Source 'indeed' -Status 'ok' -Uploaded 2\n"
        "Close-RunState -State $state -FinishedAt '2026-10-01T03:00:00.0000000+02:00'\n"
        "Write-RunState -State $state -StateDir $StateDir",
    )

    ps = _require_powershell()
    with open(target, "rb") as handle:
        assert handle.read(1), "el destino debe existir antes de la prueba"
        proc = subprocess.Popen(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(second)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert _wait_for_temp_file(state_dir, proc), (
            "el helper debe crear su temporal antes de reemplazar"
        )
        # The read handle is still open: File.Replace cannot have succeeded,
        # so the write must still be waiting in its retry loop.
        time.sleep(0.15)
        assert proc.poll() is None, (
            "el reemplazo no debe completarse mientras un lector mantiene el "
            "destino abierto; el reintento debe seguir esperando"
        )
    # Handle released: the retry loop can now publish the temp file.
    stdout, stderr = proc.communicate(timeout=120)
    _check_completed(
        subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    )

    state = _read_state(state_dir)
    _assert_only_state_files(state_dir)
    assert state["status"] == "closed"
    assert state["finished_at"] == "2026-10-01T03:00:00.0000000+02:00"
    assert state["sources"]["indeed"]["status"] == "ok"
    assert state["sources"]["indeed"]["uploaded"] == 2
