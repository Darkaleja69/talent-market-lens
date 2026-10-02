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
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

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
    """Fake AzCopy: records every call and keeps a copy of the manifest."""
    calls = tmp_path / "azcopy_calls.log"
    uploaded = tmp_path / "manifest_uploaded.json"
    script = tmp_path / "fake_azcopy.ps1"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)",
                f"Add-Content -LiteralPath '{calls}' -Value ($AzArgs -join '|')",
                "if ($AzArgs.Count -ge 3 -and $AzArgs[0] -eq 'copy') {",
                "    $src = $AzArgs[1]",
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
