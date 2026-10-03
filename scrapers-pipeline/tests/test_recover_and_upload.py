"""Tests for the plan-driven recovery executor (T-11; RF-2, RF-3).

Offline: ``recover_and_upload.ps1`` runs with a fake AzCopy (a .ps1 that
records its arguments and copies the uploaded manifest to a known place), a
temporary ``uploaded_keys`` directory and a temporary quarantine. Real
``ensure_compatible.py``/``filter_new_offers.py`` validate the fixtures built
with pyarrow. No Azure, SAS or credentials are used (the SAS env var is a
dummy), and every temporary file lives under pytest's ``tmp_path``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import run_evidence, verify_run

PIPELINE_DIR = Path(__file__).resolve().parents[1]
RECOVER = PIPELINE_DIR / "recover_and_upload.ps1"
POWERSHELL = shutil.which("powershell.exe")
DATE = "2026-10-01"
COMPACT = "20261001"


def _require_powershell() -> str:
    if not RECOVER.is_file():
        pytest.fail(f"Falta {RECOVER}; la tarea T-11 no esta implementada.")
    if POWERSHELL is None:
        pytest.fail("powershell.exe no esta disponible.")
    return POWERSHELL


def _write_parquet(path: Path, columns: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)
    return path


def _item(**overrides) -> dict:
    base = {
        "source": "indeed",
        "recoverable": True,
        "mode": "file",
        "path": None,
        "day": DATE,
        "required_cols": [],
        "coherence": "",
        "fingerprint_source": "",
        "key_column": "job_key",
        "only_new": False,
        "reconstruct": False,
        "merge_since": None,
        "inputs": [],
        "new_keys": None,
        "published_key": None,
        "reason": "",
    }
    base.update(overrides)
    return base


def _write_plan(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"schema_version": 1, "run_date": DATE, "items": items}),
        encoding="utf-8",
    )
    return path


def _write_fake_azcopy(tmp_path: Path, *, exit_code: int = 0) -> Path:
    """Fake AzCopy: records every call, answers ``list`` and keeps copies.

    ``list`` prints the lines of ``<tmp>/ready_list.txt`` when it exists (used
    to simulate an existing ``_READY``). Every ``copy`` of a leaf file keeps a
    copy under ``<tmp>/uploaded_files/<name>`` (manifests also go to the legacy
    ``manifest_uploaded.json``).
    """
    calls = tmp_path / "azcopy_calls.log"
    uploaded = tmp_path / "manifest_uploaded.json"
    ready_list = tmp_path / "ready_list.txt"
    uploaded_dir = tmp_path / "uploaded_files"
    script = tmp_path / "fake_azcopy.ps1"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)",
                f"Add-Content -LiteralPath '{calls}' -Value ($AzArgs -join '|')",
                "if ($AzArgs.Count -ge 1 -and $AzArgs[0] -eq 'list') {",
                f"    if (Test-Path -LiteralPath '{ready_list}') {{ Get-Content -LiteralPath '{ready_list}' }}",
                "    exit 0",
                "}",
                "if ($AzArgs.Count -ge 3 -and $AzArgs[0] -eq 'copy') {",
                "    $src = $AzArgs[1]",
                "    if (Test-Path -LiteralPath $src -PathType Leaf) {",
                f"        New-Item -ItemType Directory -Force -Path '{uploaded_dir}' | Out-Null",
                f"        Copy-Item -LiteralPath $src -Destination (Join-Path '{uploaded_dir}' (Split-Path -Leaf $src)) -Force",
                "    }",
                f"    if ($src -like '*.json' -and (Test-Path -LiteralPath $src -PathType Leaf)) {{",
                f"        Copy-Item -LiteralPath $src -Destination '{uploaded}' -Force",
                "    }",
                "}",
                f"exit {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _write_fake_azcopy_manifest_failure(tmp_path: Path) -> Path:
    """Fake AzCopy: fails only the upload whose destination is _manifests/."""
    calls = tmp_path / "azcopy_calls.log"
    script = tmp_path / "fake_azcopy_manifest_fail.ps1"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)",
                f"Add-Content -LiteralPath '{calls}' -Value ($AzArgs -join '|')",
                "if ($AzArgs.Count -ge 3 -and $AzArgs[0] -eq 'copy') {",
                "    $dest = $AzArgs[2]",
                "    if ($dest -like '*_manifests*') { exit 1 }",
                "}",
                "exit 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _run_recover(
    tmp_path: Path,
    *,
    plan: Path | None = None,
    date: str | None = None,
    dry_run: bool = False,
    azcopy: Path | None = None,
    projects_root: Path | None = None,
    extra: list[str] | None = None,
    ready_policy: str | None = None,
):
    log_dir = tmp_path / "logs"
    args = [
        _require_powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RECOVER),
        "-AzCopyPath",
        str(azcopy if azcopy is not None else _write_fake_azcopy(tmp_path)),
        "-PythonExe",
        "python",
        "-LogDir",
        str(log_dir),
        "-StateDir",
        str(tmp_path / "state"),
        "-UploadedKeysDir",
        str(tmp_path / "uploaded_keys"),
        "-QuarantineDir",
        str(tmp_path / "quarantine"),
        "-ProjectsRoot",
        str(projects_root if projects_root is not None else tmp_path / "projects"),
    ]
    if plan is not None:
        args += ["-PlanJson", str(plan)]
        # Keep the log name deterministic (the plan itself wins over -Date).
        if date is None:
            args += ["-Date", DATE]
    elif date is not None:
        args += ["-Date", date]
    if dry_run:
        args += ["-DryRun"]
    if ready_policy is not None:
        args += ["-ReadyPolicy", ready_policy]
    if extra:
        args += extra
    env = {
        **os.environ,
        "LANDING_SAS_TOKEN": "test-sas",
        "LANDING_STORAGE_ACCOUNT": "testaccount",
    }
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
    )
    return completed, log_dir


# T-16A: manifest destination keys look like
# ``.../_manifests/<source>/<stamp>.json<SAS>`` inside the recorded AzCopy call.
_MANIFEST_KEY_RE = re.compile(r"/_manifests/([^/|]+)/(\d{8}_\d{6})\.json")


def _manifest_stamps(calls: str) -> dict[str, str]:
    """Map scraper -> stamp from the recorded manifest upload destinations."""
    stamps: dict[str, str] = {}
    for line in calls.splitlines():
        if not line.startswith("copy|"):
            continue
        for match in _MANIFEST_KEY_RE.finditer(line):
            stamps[match.group(1)] = match.group(2)
    return stamps


def _write_general_log_header(
    log_dir: Path, *, start_clock: str = "00:00:00", run_date: str = DATE
) -> Path:
    """Create the run's general log with its ``Inicio`` header (T-16A)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    general_log = log_dir / f"upload-{run_date}.log"
    general_log.write_text(
        f"{start_clock}  [INFO]  ====  Inicio pipeline scrapers  ({run_date}) ====\n"
        "00:00:05  [INFO]  Lanzando 4 scrapers en paralelo...\n",
        encoding="utf-8",
    )
    return general_log


def _run_window(log_path: Path) -> tuple[datetime, datetime]:
    """Return the ``[started_at, finished_at]`` window parsed from the log."""
    summary = run_evidence.parse_pipeline_log(log_path)
    assert summary.completed is True
    assert summary.started_at and summary.finished_at
    return (
        datetime.fromisoformat(summary.started_at),
        datetime.fromisoformat(summary.finished_at),
    )


def _indeed_fixture(projects: Path, *, with_title: bool = True) -> Path:
    columns = {
        "job_key": ["a"],
        "viewjob_url": ["https://example.com/1"],
        "company": ["ACME"],
        "scraped_at": ["2026-10-01"],
    }
    if with_title:
        columns["title"] = ["Data Engineer"]
    return _write_parquet(
        projects
        / "indeed_jobs_scraper"
        / "output"
        / f"indeed_jobs_{COMPACT}_0001.parquet",
        columns,
    )


# Mirrors `sources.required_columns("indeed")` (config.ps1).
INDEED_REQUIRED = ["job_key", "viewjob_url", "company", "title", "scraped_at"]


def test_T11_file_item_uploads_with_as_subdir_and_manifest(tmp_path):
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(encoding="utf-8", errors="replace")
    assert "copy|" in calls
    assert "--overwrite=true" in calls
    assert "--recursive" in calls
    assert "--as-subdir=false" in calls
    # The manifest was uploaded with `remote` and without BOM.
    uploaded = tmp_path / "manifest_uploaded.json"
    assert uploaded.is_file()
    raw = uploaded.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    manifest = json.loads(raw.decode("utf-8"))
    assert manifest["files"][0]["remote"] == (
        f"dia={DATE}/indeed_jobs_{COMPACT}_0001.parquet"
    )


def test_T11_delta_item_filters_and_updates_uploaded_keys_after_success(tmp_path):
    projects = tmp_path / "projects"
    snapshot = _write_parquet(
        projects / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet",
        {
            "job_id": ["a", "b"],
            "job_url": ["https://example.com/1", "https://example.com/2"],
            "company_name": ["ACME", "Beta"],
            "title": ["Data Engineer", "Analyst"],
            "scraped_at": ["2026-10-01", "2026-10-01"],
        },
    )
    keys_dir = tmp_path / "uploaded_keys"
    keys_dir.mkdir()
    (keys_dir / "linkedin.json").write_text(
        json.dumps({"keys": ["a"]}), encoding="utf-8"
    )
    plan = _write_plan(
        tmp_path,
        [
            _item(
                source="linkedin",
                mode="delta",
                path=str(snapshot),
                required_cols=[
                    "job_id",
                    "job_url",
                    "title",
                    "company_name",
                    "scraped_at",
                ],
                coherence="linkedin",
                fingerprint_source="linkedin",
                key_column="job_id",
                only_new=True,
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads((keys_dir / "linkedin.json").read_text(encoding="utf-8-sig"))
    assert state["keys"] == ["a", "b"]


def test_T11_azcopy_failure_leaves_uploaded_keys_intact(tmp_path):
    projects = tmp_path / "projects"
    snapshot = _write_parquet(
        projects / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet",
        {
            "job_id": ["a", "b"],
            "job_url": ["https://example.com/1", "https://example.com/2"],
            "company_name": ["ACME", "Beta"],
            "title": ["Data Engineer", "Analyst"],
            "scraped_at": ["2026-10-01", "2026-10-01"],
        },
    )
    keys_dir = tmp_path / "uploaded_keys"
    keys_dir.mkdir()
    (keys_dir / "linkedin.json").write_text(
        json.dumps({"keys": ["a"]}), encoding="utf-8"
    )
    plan = _write_plan(
        tmp_path,
        [
            _item(
                source="linkedin",
                mode="delta",
                path=str(snapshot),
                required_cols=[
                    "job_id",
                    "job_url",
                    "title",
                    "company_name",
                    "scraped_at",
                ],
                coherence="linkedin",
                fingerprint_source="linkedin",
                key_column="job_id",
                only_new=True,
            )
        ],
    )
    failing = _write_fake_azcopy(tmp_path, exit_code=1)

    completed, _ = _run_recover(
        tmp_path, plan=plan, azcopy=failing, projects_root=projects
    )

    assert completed.returncode == 1
    state = json.loads((keys_dir / "linkedin.json").read_text(encoding="utf-8-sig"))
    assert state["keys"] == ["a"]  # untouched: azcopy failed


def test_T11_validation_rejection_is_counted_and_not_uploaded(tmp_path):
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects, with_title=False)  # missing required title
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, log_dir = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 1
    calls_log = tmp_path / "azcopy_calls.log"
    calls = calls_log.read_text(encoding="utf-8", errors="replace") if calls_log.is_file() else ""
    assert "copy|" not in calls  # nothing was uploaded
    log_text = (log_dir / f"recover-{DATE}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "rechaz" in log_text
    assert (tmp_path / "quarantine" / "indeed").is_dir()


def test_T11_not_recoverable_item_is_omitted(tmp_path):
    plan = _write_plan(
        tmp_path,
        [
            _item(
                recoverable=False,
                reason="ya publicado en la run (estado: status=ok, uploaded=1)",
            )
        ],
    )

    completed, log_dir = _run_recover(tmp_path, plan=plan)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "azcopy_calls.log").exists()
    log_text = (log_dir / f"recover-{DATE}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "omitido" in log_text
    assert "ya publicado" in log_text


def test_T11_delta_without_new_keys_is_omitted(tmp_path):
    projects = tmp_path / "projects"
    snapshot = _write_parquet(
        projects / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet",
        {"job_id": ["a", "b"]},
    )
    keys_dir = tmp_path / "uploaded_keys"
    keys_dir.mkdir()
    (keys_dir / "linkedin.json").write_text(
        json.dumps({"keys": ["a", "b"]}), encoding="utf-8"
    )
    plan = _write_plan(
        tmp_path,
        [
            _item(
                source="linkedin",
                mode="delta",
                path=str(snapshot),
                required_cols=["job_id"],
                key_column="job_id",
                only_new=True,
            )
        ],
    )

    completed, log_dir = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "azcopy_calls.log").exists()
    log_text = (log_dir / f"recover-{DATE}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "sin ofertas nuevas" in log_text


def test_T11_date_generates_the_plan_and_recovers(tmp_path):
    projects = tmp_path / "projects"
    _indeed_fixture(projects)

    completed, _ = _run_recover(tmp_path, date=DATE, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(encoding="utf-8", errors="replace")
    assert "--as-subdir=false" in calls


def test_T11_legacy_mode_is_kept_without_date_or_plan(tmp_path):
    empty_projects = tmp_path / "empty_projects"
    empty_projects.mkdir()

    completed, log_dir = _run_recover(
        tmp_path, dry_run=True, projects_root=empty_projects
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "azcopy_calls.log").exists()
    today = datetime.now().strftime("%Y-%m-%d")
    log_text = (log_dir / f"recover-{today}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "Inicio recuperacion de datos historicos" in log_text


def test_T11_manifest_upload_failure_is_a_failure(tmp_path):
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )
    azcopy = _write_fake_azcopy_manifest_failure(tmp_path)

    completed, log_dir = _run_recover(
        tmp_path, plan=plan, azcopy=azcopy, projects_root=projects
    )

    # The data went up but the manifest did not: not a success.
    assert completed.returncode == 1
    log_text = (log_dir / f"recover-{DATE}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "manifest" in log_text
    assert "fallo subiendo el manifest" in log_text
    assert "manifest no subido" in log_text


def test_T11_dry_run_merge_does_not_touch_the_canonical(tmp_path):
    projects = tmp_path / "projects"
    merged_dir = projects / "multi_site_job_scraper" / "data" / "merged"
    canonical = _write_parquet(
        merged_dir / "jobs_unified.parquet", {"job_id": ["canonical"]}
    )
    original = canonical.read_bytes()
    csv_input = (
        projects
        / "multi_site_job_scraper"
        / "data"
        / "irishjobs"
        / "output"
        / "jobs.csv"
    )
    csv_input.parent.mkdir(parents=True, exist_ok=True)
    csv_input.write_text("job_id\nx\n", encoding="utf-8")
    # If merge ran, it would fail loudly (dry-run must never execute it).
    fake_merge = tmp_path / "fake_merge.py"
    fake_merge.write_text("raise SystemExit(9)\n", encoding="utf-8")
    plan = _write_plan(
        tmp_path,
        [
            _item(
                source="multi_site",
                mode="merge",
                path=None,
                reconstruct=True,
                inputs=[str(csv_input)],
                merge_since=f"{DATE}T00:00:00",
                required_cols=["job_id"],
                key_column="job_id",
                only_new=True,
            )
        ],
    )

    completed, log_dir = _run_recover(
        tmp_path,
        plan=plan,
        dry_run=True,
        projects_root=projects,
        extra=["-MergeScript", str(fake_merge)],
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert canonical.read_bytes() == original  # canonical untouched
    assert not (tmp_path / "azcopy_calls.log").exists()
    log_text = (log_dir / f"recover-{DATE}.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "reconstruiria y subiria" in log_text


# --------------------------------------------------------------------------
# T-12: closing block, faithful _READY and run-state closure
# --------------------------------------------------------------------------


def _successful_indeed_plan(tmp_path: Path, projects: Path) -> Path:
    parquet = _indeed_fixture(projects)
    return _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )


def _log_text(log_dir: Path, run_date: str = DATE) -> str:
    return (log_dir / f"recover-{run_date}.log").read_text(
        encoding="utf-8", errors="replace"
    )


def test_T12_recovery_appends_closing_block_and_log_is_completed(tmp_path):
    projects = tmp_path / "projects"
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    general_log = log_dir / f"upload-{DATE}.log"
    general_log.write_text(
        f"00:00:00  [INFO]  ====  Inicio pipeline scrapers  ({DATE}) ====\n"
        "00:00:05  [INFO]  Lanzando 4 scrapers en paralelo...\n",
        encoding="utf-8",
    )
    plan = _successful_indeed_plan(tmp_path, projects)

    completed, log_dir = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = run_evidence.parse_pipeline_log(general_log)
    assert summary.completed is True
    assert summary.date == DATE
    assert summary.statuses["indeed"].status == "ok"
    assert summary.statuses["indeed"].uploaded == 1
    assert run_evidence.select_last_run(log_dir) is not None


def test_T12_ready_written_when_policy_met(tmp_path):
    projects = tmp_path / "projects"
    plan = _successful_indeed_plan(tmp_path, projects)

    completed, _ = _run_recover(
        tmp_path, plan=plan, projects_root=projects, ready_policy="any_valid"
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert f"_READY/dia={DATE}.txt" in calls
    ready = tmp_path / "uploaded_files" / f"ready_{DATE}.txt"
    assert ready.is_file()
    assert ready.read_text(encoding="utf-8").startswith(f"RECOVERED {DATE} ")


def test_T12_ready_not_written_when_policy_fails(tmp_path):
    plan = _write_plan(
        tmp_path,
        [_item(recoverable=False, reason="no hay parquet de Indeed con la fecha del run")],
    )

    completed, log_dir = _run_recover(
        tmp_path, plan=plan, ready_policy="any_valid"
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls_log = tmp_path / "azcopy_calls.log"
    calls = calls_log.read_text(encoding="utf-8", errors="replace") if calls_log.is_file() else ""
    assert f"_READY/dia={DATE}.txt" not in calls
    log_text = _log_text(log_dir)
    assert "No se escribe _READY" in log_text
    assert "ningun dato valido publicado" in log_text


def test_T12_existing_ready_is_not_rewritten(tmp_path):
    projects = tmp_path / "projects"
    (tmp_path / "ready_list.txt").write_text(
        f"INFO: _READY/dia={DATE}.txt; Content Length: 10\n", encoding="utf-8"
    )
    plan = _successful_indeed_plan(tmp_path, projects)

    completed, log_dir = _run_recover(
        tmp_path, plan=plan, projects_root=projects, ready_policy="any_valid"
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    ready_copies = [
        line
        for line in calls.splitlines()
        if line.startswith("copy|") and "_READY" in line
    ]
    assert ready_copies == []
    assert "ya existe; no se reescribe" in _log_text(log_dir)


def test_T12_run_state_is_closed_with_sources(tmp_path):
    projects = tmp_path / "projects"
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / f"{DATE}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": DATE,
                "started_at": f"{DATE}T00:00:00.0000000+02:00",
                "finished_at": None,
                "status": "pending",
                "sources": {
                    "linkedin": {
                        "status": "pending",
                        "uploaded": 0,
                        "rejected": 0,
                        "last_activity_at": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    plan = _successful_indeed_plan(tmp_path, projects)

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    state = json.loads((state_dir / f"{DATE}.json").read_text(encoding="utf-8-sig"))
    assert state["status"] == "closed"
    assert state["finished_at"]
    assert state["sources"]["indeed"]["status"] == "ok"
    assert state["sources"]["indeed"]["uploaded"] == 1
    assert state["sources"]["linkedin"]["status"] == "pending"  # untouched


def test_T12_missing_run_state_is_recorded(tmp_path):
    projects = tmp_path / "projects"
    plan = _successful_indeed_plan(tmp_path, projects)

    completed, log_dir = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "state" / f"{DATE}.json").exists()
    log_text = _log_text(log_dir)
    assert "No existe el estado del run" in log_text
    assert "no se inventa" in log_text


# --------------------------------------------------------------------------
# T-16A: run-date manifest stamping for late recoveries
# --------------------------------------------------------------------------


def test_T16A_late_recovery_manifest_is_stamped_with_the_run_date(tmp_path):
    """Days-late recovery: the manifest anchors the recovered run, not today.

    Before T-16A the stamp was ``Get-Date`` (the recovery day), so
    ``run_diagnostic`` found no manifest inside the run window and Indeed's
    publication would have been pending (the T-17 reproduction).
    """
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    log_dir = tmp_path / "logs"
    general_log = _write_general_log_header(log_dir)
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    stamps = _manifest_stamps(calls)
    assert set(stamps) == {"indeed"}
    # The recovery day is not the run day: the stamp carries the run date.
    assert stamps["indeed"].startswith(f"{COMPACT}_")
    # Reuse the diagnostic's own window check: the stamp is an anchor.
    window = _run_window(general_log)
    assert verify_run._stamp_within_window(stamps["indeed"], window)


def test_T16A_missing_general_log_falls_back_to_the_run_date(tmp_path):
    """Without a readable ``Inicio`` time, now is used on the run date."""
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    # No general log existed when the item was sealed: the clock is the
    # recovery's own, but the date is still the run date.
    assert _manifest_stamps(calls)["indeed"].startswith(f"{COMPACT}_")


def test_T16A_filtered_delta_name_and_manifest_share_the_run_stamp(tmp_path):
    """The ``_new_$stamp`` file name matches the manifest stamp (T-16A)."""
    projects = tmp_path / "projects"
    snapshot = _write_parquet(
        projects / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet",
        {
            "job_id": ["a", "b"],
            "job_url": ["https://example.com/1", "https://example.com/2"],
            "company_name": ["ACME", "Beta"],
            "title": ["Data Engineer", "Analyst"],
            "scraped_at": ["2026-10-01", "2026-10-01"],
        },
    )
    log_dir = tmp_path / "logs"
    _write_general_log_header(log_dir)
    plan = _write_plan(
        tmp_path,
        [
            _item(
                source="linkedin",
                mode="delta",
                path=str(snapshot),
                required_cols=[
                    "job_id",
                    "job_url",
                    "title",
                    "company_name",
                    "scraped_at",
                ],
                coherence="linkedin",
                fingerprint_source="linkedin",
                key_column="job_id",
                only_new=True,
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    manifest_stamp = _manifest_stamps(calls)["linkedin"]
    assert manifest_stamp.startswith(f"{COMPACT}_")
    uploaded = json.loads(
        (tmp_path / "manifest_uploaded.json").read_text(encoding="utf-8")
    )
    remote = uploaded["files"][0]["remote"]
    match = re.search(r"_new_(\d{8}_\d{6})\.parquet$", remote)
    assert match is not None, remote
    assert match.group(1) == manifest_stamp


def test_T16A_stamp_stays_in_window_when_the_run_crosses_midnight(tmp_path):
    """A reconciliation closing past midnight keeps the log's start time."""
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    log_dir = tmp_path / "logs"
    general_log = _write_general_log_header(log_dir, start_clock="23:59:55")
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    stamp = _manifest_stamps(calls)["indeed"]
    assert stamp.startswith(f"{COMPACT}_")
    window = _run_window(general_log)
    assert verify_run._stamp_within_window(stamp, window)
    if datetime.now().strftime("%H:%M:%S") < "23:59:55":
        # Recovered after midnight: max(start, now) keeps the run start.
        assert stamp == f"{COMPACT}_235955"


def test_T16A_stamp_uses_the_last_inicio_block_analysed_by_the_diagnostic(
    tmp_path,
):
    """Multi-block day: the seal follows ``parse_pipeline_log``'s last block.

    A finished earlier run (22:00) and a later truncated one (23:50) share the
    date. ``run_evidence.parse_pipeline_log`` analyses the last block, so the
    stamp must use its start: with the first block it would fall outside the
    analysed window (verifier's reproduction).
    """
    projects = tmp_path / "projects"
    parquet = _indeed_fixture(projects)
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    general_log = log_dir / f"upload-{DATE}.log"
    general_log.write_text(
        f"22:00:00  [INFO]  ====  Inicio pipeline scrapers  ({DATE}) ====\n"
        "22:30:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 1800s ====\n"
        f"23:50:00  [INFO]  ====  Inicio pipeline scrapers  ({DATE}) ====\n"
        "23:50:05  [INFO]  Lanzando 4 scrapers en paralelo...\n",
        encoding="utf-8",
    )
    plan = _write_plan(
        tmp_path,
        [
            _item(
                path=str(parquet),
                required_cols=INDEED_REQUIRED,
                coherence="indeed",
                fingerprint_source="indeed",
            )
        ],
    )

    completed, _ = _run_recover(tmp_path, plan=plan, projects_root=projects)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    stamp = _manifest_stamps(calls)["indeed"]
    assert stamp.startswith(f"{COMPACT}_")
    # The diagnostic analyses the last block of the day (23:50), not the first.
    window = _run_window(general_log)
    assert window[0] == datetime.fromisoformat(f"{DATE}T23:50:00")
    assert verify_run._stamp_within_window(stamp, window)
    if datetime.now().strftime("%H:%M:%S") < "23:50:00":
        # max(last start, now) keeps the last block's start.
        assert stamp == f"{COMPACT}_235000"
