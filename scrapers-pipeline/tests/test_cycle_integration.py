"""Offline integration test of the whole recovery cycle (T-15; RF-1..RF-6).

A temporary workspace holds a truncated run (log + pending state) and valid
remains of the four sources. The test drives the REAL components end to end:

1. the ``plan`` CLI builds the recovery plan from the workspace;
2. ``recover_and_upload.ps1`` runs first in ``-DryRun`` (nothing published)
   and then for real with a **fake AzCopy** that maps every landing URL to a
   local directory, implements ``--as-subdir=false`` (staging contents go
   directly under ``dia=...``) and answers ``list`` for the ``_READY`` check;
3. the closing block, ``_READY``, the run state and ``uploaded_keys`` are
   verified, and a repeated run proves idempotency.

No Azure, credentials or real SAS are used: ``LANDING_SAS_TOKEN`` and
``LANDING_STORAGE_ACCOUNT`` are dummies and AzCopy is the fake script, so the
repository's ``config.local.ps1`` is only read (never modified) and no network
call happens. The repository is not written to: every path is overridden to
the temporary workspace.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import landing, publication, run_evidence, verify_run

PIPELINE_DIR = Path(__file__).resolve().parents[1]
RECOVER = PIPELINE_DIR / "recover_and_upload.ps1"
POWERSHELL = shutil.which("powershell.exe")
RUN_DATE = "2026-10-01"
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


def _build_workspace(tmp_path: Path) -> dict:
    """Truncated run + valid remains of the four sources (RF-1..RF-3)."""
    ws = tmp_path / "workspace"
    pipeline = ws / "scrapers-pipeline"
    logs = pipeline / "logs"
    state_dir = logs / "run_state"
    keys_dir = pipeline / "uploaded_keys"
    logs.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    keys_dir.mkdir(parents=True)

    # Truncated general log (Inicio without Fin) and pending state (T-03).
    (logs / f"upload-{RUN_DATE}.log").write_text(
        f"00:00:00  [INFO]  ====  Inicio pipeline scrapers  ({RUN_DATE}) ====\n"
        "00:00:05  [INFO]  Lanzando 4 scrapers en paralelo...\n",
        encoding="utf-8",
    )
    (state_dir / f"{RUN_DATE}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_date": RUN_DATE,
                "started_at": f"{RUN_DATE}T00:00:00.0000000+02:00",
                "finished_at": None,
                "status": "pending",
                "sources": {},
            }
        ),
        encoding="utf-8",
    )

    # Indeed (dated file; required columns + coherence).
    _write_parquet(
        ws / "indeed_jobs_scraper" / "output" / f"indeed_jobs_{COMPACT}_0001.parquet",
        {
            "job_key": ["i1", "i2"],
            "title": ["Data Engineer", "Analyst"],
            "company": ["ACME", "Beta"],
            "viewjob_url": ["https://example.com/1", "https://example.com/2"],
            "scraped_at": [RUN_DATE, RUN_DATE],
        },
    )
    # InfoJobs (dated file).
    _write_parquet(
        ws / "infojobs_jobs_scraper" / "data" / f"offers_{COMPACT}_001500.parquet",
        {
            "id_oferta": ["j1", "j2"],
            "titulo": ["Data Engineer", "Analyst"],
            "empresa": ["ACME", "Beta"],
            "url_oferta": ["https://example.com/j1", "https://example.com/j2"],
            "fecha_scraped": [RUN_DATE, RUN_DATE],
        },
    )
    # LinkedIn cumulative snapshot + one key already published.
    _write_parquet(
        ws / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet",
        {
            "job_id": ["l1", "l2"],
            "job_url": ["https://example.com/l1", "https://example.com/l2"],
            "title": ["Data Engineer", "Analyst"],
            "company_name": ["ACME", "Beta"],
            "scraped_at": [RUN_DATE, RUN_DATE],
        },
    )
    (keys_dir / "linkedin.json").write_text(
        json.dumps({"updated_at": RUN_DATE, "count": 1, "keys": ["l1"]}),
        encoding="utf-8",
    )
    # Multi-site dated merged snapshot + one key already published.
    _write_parquet(
        ws
        / "multi_site_job_scraper"
        / "data"
        / "merged"
        / f"jobs_unified_{COMPACT}_030000.parquet",
        {
            "job_id": ["m1", "m2"],
            "job_url": ["https://example.com/m1", "https://example.com/m2"],
            "title": ["Data Engineer", "Analyst"],
            "company_name": ["ACME", "Beta"],
            "scraped_at": [RUN_DATE, RUN_DATE],
        },
    )
    (keys_dir / "multi_site.json").write_text(
        json.dumps({"updated_at": RUN_DATE, "count": 1, "keys": ["m1"]}),
        encoding="utf-8",
    )

    return {
        "ws": ws,
        "logs": logs,
        "state_dir": state_dir,
        "keys_dir": keys_dir,
        "quarantine_dir": pipeline / "quarantine",
    }


def _write_fake_azcopy(tmp_path: Path, landing_root: Path) -> Path:
    """Fake AzCopy mapping landing URLs to a local directory.

    ``copy`` of a directory implements ``--as-subdir=false`` (the staging
    contents land directly under the destination), ``copy`` of a file keeps
    the file and ``list`` prints the local keys under the prefix. Every call
    is recorded in ``azcopy_calls.log``.
    """
    calls = tmp_path / "azcopy_calls.log"
    script = tmp_path / "fake_azcopy.ps1"
    script.write_text(
        "\n".join(
            [
                "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AzArgs)",
                f"Add-Content -LiteralPath '{calls}' -Value ($AzArgs -join '|')",
                "function Get-LocalPath([string]$url) {",
                "    $clean = ($url -split '\\?')[0]",
                "    $idx = $clean.IndexOf('.net/')",
                "    if ($idx -lt 0) { return $null }",
                "    $key = $clean.Substring($idx + 5).Trim('/')",
                "    $slash = $key.IndexOf('/')",
                "    if ($slash -lt 0) { return $null }",
                "    $rel = $key.Substring($slash + 1)",
                f"    if ([string]::IsNullOrWhiteSpace($rel)) {{ return '{landing_root}' }}",
                f"    return Join-Path '{landing_root}' ($rel -replace '/', '\\')",
                "}",
                "if ($AzArgs[0] -eq 'list') {",
                "    $prefixPath = Get-LocalPath $AzArgs[1]",
                "    if ($prefixPath -and (Test-Path -LiteralPath $prefixPath -PathType Container)) {",
                "        Get-ChildItem -LiteralPath $prefixPath -Recurse -File | ForEach-Object {",
                "            $_.FullName.Substring($prefixPath.Length).TrimStart('\\')",
                "        }",
                "    }",
                "    exit 0",
                "}",
                "if ($AzArgs[0] -eq 'copy') {",
                "    $src = $AzArgs[1]",
                "    $target = Get-LocalPath $AzArgs[2]",
                "    if (Test-Path -LiteralPath $src -PathType Container) {",
                "        New-Item -ItemType Directory -Force -Path $target | Out-Null",
                "        Copy-Item -Path (Join-Path $src '*') -Destination $target -Recurse -Force",
                "    } else {",
                "        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null",
                "        Copy-Item -LiteralPath $src -Destination $target -Force",
                "    }",
                "    exit 0",
                "}",
                "exit 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _run_plan(workspace: dict, plan_path: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "verification.recovery",
            "plan",
            "--run-date",
            RUN_DATE,
            "--projects-root",
            str(workspace["ws"]),
            "--state-dir",
            str(workspace["state_dir"]),
            "--out",
            str(plan_path),
        ],
        cwd=str(PIPELINE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if completed.returncode != 0:
        pytest.fail(f"plan fallo:\n{completed.stdout}\n{completed.stderr}")
    return json.loads(plan_path.read_text(encoding="utf-8"))


def _run_recover(
    workspace: dict, plan_path: Path, azcopy: Path, *, dry_run: bool = False
):
    args = [
        _require_powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(RECOVER),
        "-PlanJson",
        str(plan_path),
        "-Date",
        RUN_DATE,
        "-AzCopyPath",
        str(azcopy),
        "-PythonExe",
        "python",
        "-LogDir",
        str(workspace["logs"]),
        "-StateDir",
        str(workspace["state_dir"]),
        "-UploadedKeysDir",
        str(workspace["keys_dir"]),
        "-QuarantineDir",
        str(workspace["quarantine_dir"]),
        "-ProjectsRoot",
        str(workspace["ws"]),
        "-ReadyPolicy",
        "any_valid",
    ]
    if dry_run:
        args += ["-DryRun"]
    env = {
        **os.environ,
        "LANDING_SAS_TOKEN": "?sv=test",
        "LANDING_STORAGE_ACCOUNT": "testaccount",
    }
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        env=env,
    )


def _landing_tree(root: Path) -> dict[str, int]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.stat().st_size
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _FakeReader:
    """In-memory ``landing.RemoteReader`` over the simulated landing (T-16A).

    Same pattern as ``tests/test_verify_run.py``: the objects dict maps
    container-relative keys to their bytes, so ``run_diagnostic`` can check the
    recovered publication and its trend without Azure or credentials.
    """

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def list_objects(self, prefix: str) -> list[landing.RemoteObject]:
        return [
            landing.RemoteObject(path=key, size=len(data))
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def download(self, remote_path: str, local_path: Path) -> None:
        data = self.objects.get(remote_path)
        if data is None:
            raise landing.RemoteError("objeto no encontrado")
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    def close(self) -> None:  # pragma: no cover - protocol completeness
        pass


def _landing_objects(root: Path) -> dict[str, bytes]:
    """Read every file of the simulated landing into memory."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _parquet_bytes(columns: dict) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(pa.table(columns), sink)
    return sink.getvalue().to_pybytes()


def test_T15_offline_recovery_cycle_end_to_end(tmp_path):
    workspace = _build_workspace(tmp_path)
    landing = tmp_path / "landing"
    azcopy = _write_fake_azcopy(tmp_path, landing)
    plan_path = tmp_path / "plan.json"

    # --- 1) Plan: the four sources with their modes --------------------------
    plan = _run_plan(workspace, plan_path)
    by_source = {item["source"]: item for item in plan["items"]}
    assert set(by_source) == {"indeed", "linkedin", "multi_site", "infojobs"}
    assert by_source["indeed"]["mode"] == "file"
    assert by_source["infojobs"]["mode"] == "file"
    assert by_source["linkedin"]["mode"] == "delta"
    assert by_source["multi_site"]["mode"] == "delta"
    assert all(item["recoverable"] for item in plan["items"])

    # --- 2) Dry run: nothing is published ------------------------------------
    dry = _run_recover(workspace, plan_path, azcopy, dry_run=True)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert _landing_tree(landing) == {}
    general_log = workspace["logs"] / f"upload-{RUN_DATE}.log"
    assert "Fin pipeline" not in general_log.read_text(encoding="utf-8")

    # --- 3) Real run against the simulated landing ---------------------------
    run1 = _run_recover(workspace, plan_path, azcopy)
    assert run1.returncode == 0, run1.stdout + run1.stderr
    calls = (tmp_path / "azcopy_calls.log").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "--overwrite=true" in calls
    assert "--recursive" in calls
    assert "--as-subdir=false" in calls

    tree1 = _landing_tree(landing)
    indeed_key = f"indeed/dia={RUN_DATE}/indeed_jobs_{COMPACT}_0001.parquet"
    infojobs_key = f"infojobs/dia={RUN_DATE}/offers_{COMPACT}_001500.parquet"
    assert indeed_key in tree1
    assert infojobs_key in tree1
    linkedin_files = [
        key
        for key in tree1
        if key.startswith(f"linkedin/dia={RUN_DATE}/") and key.endswith(".parquet")
    ]
    multi_files = [
        key
        for key in tree1
        if key.startswith(f"multi_site/dia={RUN_DATE}/") and key.endswith(".parquet")
    ]
    assert len(linkedin_files) == 1
    assert Path(linkedin_files[0]).name.startswith("jobs_new_")
    assert len(multi_files) == 1
    assert f"jobs_unified_{COMPACT}_030000_new_" in Path(multi_files[0]).name
    # The deltas carry only the not-yet-published key.
    assert pq.read_table(landing / linkedin_files[0]).num_rows == 1
    assert pq.read_table(landing / multi_files[0]).num_rows == 1

    # Manifests: `remote` annotated and no BOM.
    for source in ("indeed", "linkedin", "multi_site", "infojobs"):
        manifests = sorted((landing / "_manifests" / source).glob("*.json"))
        assert len(manifests) == 1, source
        raw = manifests[0].read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), source
        payload = json.loads(raw.decode("utf-8"))
        assert payload["files"][0]["remote"].startswith(f"dia={RUN_DATE}/")

    # uploaded_keys updated only after the successful uploads.
    linkedin_keys = json.loads(
        (workspace["keys_dir"] / "linkedin.json").read_text(encoding="utf-8-sig")
    )
    assert set(linkedin_keys["keys"]) == {"l1", "l2"}
    multi_keys = json.loads(
        (workspace["keys_dir"] / "multi_site.json").read_text(encoding="utf-8-sig")
    )
    assert set(multi_keys["keys"]) == {"m1", "m2"}

    # --- 4) Closing block, state and _READY ----------------------------------
    summary = run_evidence.parse_pipeline_log(general_log)
    assert summary.completed is True
    assert summary.statuses["indeed"].status == "ok"
    assert summary.statuses["linkedin"].status == "ok"
    assert summary.statuses["multi_site"].status == "ok"
    assert summary.statuses["infojobs"].status == "ok"
    assert run_evidence.select_last_run(workspace["logs"]) is not None

    state = json.loads(
        (workspace["state_dir"] / f"{RUN_DATE}.json").read_text(encoding="utf-8-sig")
    )
    assert state["status"] == "closed"
    assert state["finished_at"]
    for source in ("indeed", "linkedin", "multi_site", "infojobs"):
        assert state["sources"][source]["status"] == "ok"
        assert state["sources"][source]["uploaded"] == 1

    ready = landing / "_READY" / f"dia={RUN_DATE}.txt"
    assert ready.is_file()
    ready_bytes = ready.read_bytes()
    assert ready_bytes.decode("utf-8").startswith(f"RECOVERED {RUN_DATE}")

    # --- 5) Idempotency: same plan (no duplicate data, _READY untouched) -----
    keys_before = {
        name: (workspace["keys_dir"] / name).read_bytes()
        for name in ("linkedin.json", "multi_site.json")
    }
    data_keys_before = {
        key: size
        for key, size in tree1.items()
        if key.endswith(".parquet")
    }
    run2 = _run_recover(workspace, plan_path, azcopy)
    assert run2.returncode == 0, run2.stdout + run2.stderr
    data_keys_after = {
        key: size
        for key, size in _landing_tree(landing).items()
        if key.endswith(".parquet")
    }
    assert data_keys_after == data_keys_before  # overwritten, never duplicated
    assert ready.read_bytes() == ready_bytes  # existing _READY not rewritten
    for name, raw in keys_before.items():
        assert (workspace["keys_dir"] / name).read_bytes() == raw

    # --- 5b) Regenerated plan: the closed state makes the cycle a no-op ------
    plan2_path = tmp_path / "plan2.json"
    plan2 = _run_plan(workspace, plan2_path)
    assert all(not item["recoverable"] for item in plan2["items"])
    # Indeed/InfoJobs are already published (run state); LinkedIn/Multi-site
    # have no new keys (uploaded_keys), which is also a no-op.
    assert all(
        ("ya publicado" in item["reason"]) or ("sin ofertas nuevas" in item["reason"])
        for item in plan2["items"]
    )
    tree_after_run2 = _landing_tree(landing)
    run3 = _run_recover(workspace, plan2_path, azcopy)
    assert run3.returncode == 0, run3.stdout + run3.stderr
    assert _landing_tree(landing) == tree_after_run2  # nothing new at all
    assert ready.read_bytes() == ready_bytes
    assert run_evidence.parse_pipeline_log(general_log).completed is True

    # --- 6) Diagnostic over the simulated landing (T-16A) --------------------
    # The recovered manifest is sealed with the run date and an instant inside
    # its window, so run_diagnostic anchors Indeed's publication to the run
    # (before T-16A the manifest carried the recovery day and it was pending).
    objects = _landing_objects(landing)
    indeed_manifests = sorted((landing / "_manifests" / "indeed").glob("*.json"))
    assert indeed_manifests, "Indeed's recovery manifest must exist"
    current_manifest = json.loads(
        max(indeed_manifests, key=lambda path: path.name).read_text(encoding="utf-8")
    )
    fingerprint = current_manifest["fingerprint"]
    history_key = "indeed/dia=2026-09-30/indeed_jobs_20260930_0001.parquet"
    objects["_manifests/indeed/20260930_010000.json"] = json.dumps(
        {
            "schema_version": 1,
            "total_files": 1,
            "bad_files": 0,
            "fingerprint": fingerprint,
            "files": [
                {
                    "file": "indeed_jobs_20260930_0001.parquet",
                    "status": "ok",
                    "rows": 2,
                    "remote": "dia=2026-09-30/indeed_jobs_20260930_0001.parquet",
                }
            ],
        }
    ).encode("utf-8")
    objects[history_key] = _parquet_bytes(
        {
            "job_key": ["h1", "h2"],
            "title": ["Data Engineer", "Analyst"],
            "company": ["ACME", "Beta"],
            "viewjob_url": ["https://example.com/h1", "https://example.com/h2"],
            "scraped_at": [RUN_DATE, RUN_DATE],
        }
    )

    reader = _FakeReader(objects)
    diagnostic = verify_run.run_diagnostic(
        workspace["ws"], workspace["logs"], reader=reader
    )
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")

    assert indeed.publication_state == publication.PUBLICATION_OK
    assert indeed.delta_offers == 2
    # The comparable history makes the recovered run a real trend anchor. The
    # idempotent re-run left a second manifest of the same run (same
    # fingerprint), so the series has at least the anchor plus its history.
    assert indeed.trend is not None
    assert indeed.trend.runs_used >= 2
    assert indeed.trend.fields
