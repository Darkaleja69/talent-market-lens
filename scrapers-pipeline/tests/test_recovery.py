"""Tests for truncated-run discovery (T-05; RF-1).

Offline and pure: synthetic general logs and T-03 state files under pytest's
``tmp_path``. No PowerShell, no Azure and no network are involved; the state
files are written as plain JSON in the same schema the pipeline persists.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import landing, recovery, run_evidence, sources, status, verify_run

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


# --------------------------------------------------------------------------
# T-06: recovery plan per source
# --------------------------------------------------------------------------


def _write_parquet(path: Path, column: str, values: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({column: values}), path)
    return path


def _set_mtime(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def _write_uploaded_keys(projects_root: Path, source_id: str, keys: list[str]) -> Path:
    path = projects_root / "scrapers-pipeline" / "uploaded_keys" / f"{source_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated_at": "2026-10-01T05:00:00", "count": len(keys), "keys": keys}),
        encoding="utf-8",
    )
    return path


def _plan_item(plan: recovery.RecoveryPlan, source_id: str) -> recovery.RecoveryItem:
    return next(item for item in plan.items if item.source == source_id)


def test_T06_indeed_picks_latest_dated_parquet(tmp_path):
    output = tmp_path / "indeed_jobs_scraper" / "output"
    _write_parquet(output / "indeed_jobs_20261001_0001.parquet", "job_key", ["a"])
    _write_parquet(output / "indeed_jobs_20261001_2312.parquet", "job_key", ["b"])
    _write_parquet(output / "indeed_jobs_20260930_0001.parquet", "job_key", ["c"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "indeed")

    assert item.recoverable is True
    assert item.mode == recovery.PLAN_MODE_FILE
    assert item.reconstruct is False
    assert Path(item.path).name == "indeed_jobs_20261001_2312.parquet"
    assert item.day == DATE_NEW
    assert item.only_new is False
    assert item.new_keys is None
    assert item.reason == ""
    assert item.required_cols == sources.required_columns("indeed")
    assert item.coherence == "indeed"
    assert item.fingerprint_source == "indeed"
    assert item.key_column == sources.dedup_key("indeed")


def test_T06_indeed_without_rests_is_not_recoverable(tmp_path):
    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "indeed")

    assert item.recoverable is False
    assert item.mode == recovery.PLAN_MODE_NONE
    assert item.path is None
    assert "Indeed" in item.reason


def test_T06_infojobs_picks_latest_dated_parquet(tmp_path):
    data = tmp_path / "infojobs_jobs_scraper" / "data"
    _write_parquet(data / "offers_20261001_001500.parquet", "id_oferta", ["1"])
    _write_parquet(data / "offers_20261001_231500.parquet", "id_oferta", ["2"])
    _write_parquet(data / "offers_20260930_120000.parquet", "id_oferta", ["3"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "infojobs")

    assert item.recoverable is True
    assert item.mode == recovery.PLAN_MODE_FILE
    assert Path(item.path).name == "offers_20261001_231500.parquet"
    assert item.day == DATE_NEW
    assert item.key_column == "id_oferta"
    assert item.only_new is False


def test_T06_infojobs_without_rests_is_not_recoverable(tmp_path):
    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "infojobs")

    assert item.recoverable is False
    assert item.mode == recovery.PLAN_MODE_NONE
    assert "InfoJobs" in item.reason


def test_T06_linkedin_delta_counts_only_new_keys(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b", "c"])
    _write_uploaded_keys(tmp_path, "linkedin", ["a"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "linkedin")

    assert item.recoverable is True
    assert item.mode == recovery.PLAN_MODE_DELTA
    assert Path(item.path) == snapshot
    assert item.day == DATE_NEW
    assert item.new_keys == 2
    assert item.only_new is True
    assert item.key_column == "job_id"
    assert item.required_cols == sources.required_columns("linkedin")


def test_T06_linkedin_without_new_keys_is_omitted(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b"])
    _write_uploaded_keys(tmp_path, "linkedin", ["a", "b"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "linkedin")

    assert item.recoverable is False
    assert item.mode == recovery.PLAN_MODE_NONE
    assert item.day == DATE_NEW
    assert "sin ofertas nuevas" in item.reason


def test_T06_linkedin_without_state_file_counts_all_as_new(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "linkedin")

    assert item.recoverable is True
    assert item.new_keys == 2


def test_T06_multi_site_dated_parquet_is_preferred(tmp_path):
    merged = tmp_path / "multi_site_job_scraper" / "data" / "merged"
    _write_parquet(merged / "jobs_unified_20261001_030000.parquet", "job_id", ["a", "b"])
    canonical = _write_parquet(merged / "jobs_unified.parquet", "job_id", ["z"])
    _set_mtime(canonical, datetime(2026, 10, 2, 1, 29, 0))
    _write_uploaded_keys(tmp_path, "multi_site", ["a"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "multi_site")

    assert item.recoverable is True
    assert item.mode == recovery.PLAN_MODE_DELTA
    assert Path(item.path).name == "jobs_unified_20261001_030000.parquet"
    assert item.day == DATE_NEW
    assert item.new_keys == 1
    assert item.only_new is True


def test_T06_multi_site_canonical_on_run_day_is_recoverable(tmp_path):
    merged = tmp_path / "multi_site_job_scraper" / "data" / "merged"
    canonical = _write_parquet(merged / "jobs_unified.parquet", "job_id", ["a", "b"])
    _set_mtime(canonical, datetime(2026, 10, 1, 4, 50, 0))
    _write_uploaded_keys(tmp_path, "multi_site", ["a"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "multi_site")

    assert item.recoverable is True
    assert item.mode == recovery.PLAN_MODE_DELTA
    assert Path(item.path).name == "jobs_unified.parquet"
    assert item.new_keys == 1


def test_T06_multi_site_overwritten_snapshot_is_not_recoverable(tmp_path):
    # The canonical was overwritten by the next run and the portal outputs are
    # newer than the run end: nothing of this run is recoverable, no data is
    # invented.
    merged = tmp_path / "multi_site_job_scraper" / "data" / "merged"
    canonical = _write_parquet(merged / "jobs_unified.parquet", "job_id", ["z"])
    _set_mtime(canonical, datetime(2026, 10, 2, 1, 29, 0))
    csv = tmp_path / "multi_site_job_scraper" / "data" / "irishjobs" / "output" / "jobs.csv"
    csv.parent.mkdir(parents=True)
    csv.write_text("job_id\nz\n", encoding="utf-8")
    _set_mtime(csv, datetime(2026, 10, 2, 0, 5, 0))

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "multi_site")

    assert item.recoverable is False
    assert item.mode == recovery.PLAN_MODE_NONE
    assert item.reconstruct is False
    assert "otro run" in item.reason


def test_T06_multi_site_reconstructs_from_portal_outputs_of_the_run(tmp_path):
    merged = tmp_path / "multi_site_job_scraper" / "data" / "merged"
    canonical = _write_parquet(merged / "jobs_unified.parquet", "job_id", ["z"])
    _set_mtime(canonical, datetime(2026, 10, 2, 1, 29, 0))
    data = tmp_path / "multi_site_job_scraper" / "data"
    for portal, when in (
        ("irishjobs", datetime(2026, 10, 1, 4, 47, 0)),
        ("nvb", datetime(2026, 10, 1, 4, 50, 0)),
        ("stepstone_nl", datetime(2026, 9, 25, 0, 17, 0)),  # stale, not this run
    ):
        csv = data / portal / "output" / "jobs.csv"
        csv.parent.mkdir(parents=True, exist_ok=True)
        csv.write_text("job_id\nx\n", encoding="utf-8")
        _set_mtime(csv, when)

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "multi_site")

    assert item.recoverable is True
    assert item.reconstruct is True
    assert item.mode == recovery.PLAN_MODE_MERGE
    assert item.path is None
    assert item.day == DATE_NEW
    assert item.merge_since == f"{DATE_NEW}T00:00:00"
    assert {Path(p).parent.parent.name for p in item.inputs} == {"irishjobs", "nvb"}
    assert item.only_new is True
    assert item.new_keys is None


def test_T06_multi_site_reconstruction_respects_a_cross_midnight_window(tmp_path):
    csv = (
        tmp_path
        / "multi_site_job_scraper"
        / "data"
        / "irishjobs"
        / "output"
        / "jobs.csv"
    )
    csv.parent.mkdir(parents=True)
    csv.write_text("job_id\nx\n", encoding="utf-8")
    _set_mtime(csv, datetime(2026, 10, 2, 1, 0, 0))

    plan = recovery.build_plan(
        DATE_NEW,
        tmp_path,
        run_start=f"{DATE_NEW}T23:00:00",
        run_end="2026-10-02T05:00:00",
    )

    item = _plan_item(plan, "multi_site")
    assert item.recoverable is True
    assert item.reconstruct is True
    assert item.merge_since == f"{DATE_NEW}T23:00:00"


def test_T06_plan_is_json_serializable_with_config_metadata(tmp_path):
    plan = recovery.build_plan(DATE_NEW, tmp_path)
    payload = plan.to_dict()
    assert json.loads(json.dumps(payload, ensure_ascii=False))["run_date"] == DATE_NEW
    assert payload["schema_version"] == 1
    assert [item["source"] for item in payload["items"]] == [
        "indeed",
        "linkedin",
        "multi_site",
        "infojobs",
    ]
    for item in payload["items"]:
        assert isinstance(item["required_cols"], list)
        assert item["required_cols"]
        assert item["coherence"] == item["source"]
        assert item["fingerprint_source"] == item["source"]
        assert item["key_column"] in item["required_cols"]


def test_T06_config_overrides_defaults_per_source(tmp_path):
    config = {
        "indeed": {
            "required_cols": ["job_key", "title"],
            "coherence": "custom_site",
            "fingerprint_source": "custom_fingerprint",
            "key_column": "job_key",
            "only_new": True,
        }
    }

    plan = recovery.build_plan(DATE_NEW, tmp_path, config)
    indeed = _plan_item(plan, "indeed")
    linkedin = _plan_item(plan, "linkedin")

    assert indeed.required_cols == ("job_key", "title")
    assert indeed.coherence == "custom_site"
    assert indeed.fingerprint_source == "custom_fingerprint"
    assert indeed.only_new is True
    # Unspecified sources keep the operational defaults.
    assert linkedin.coherence == "linkedin"
    assert linkedin.required_cols == sources.required_columns("linkedin")


def test_T06_invalid_run_date_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        recovery.build_plan("banana", tmp_path)


# --------------------------------------------------------------------------
# T-07: plan idempotence against the landing
# --------------------------------------------------------------------------


class _FakeReader:
    """Minimal offline ``landing.RemoteReader`` for idempotence tests."""

    def __init__(self, objects=(), error=None):
        self._objects = list(objects)
        self._error = error
        self.list_calls: list[str] = []

    def list_objects(self, prefix: str):
        self.list_calls.append(prefix)
        if self._error is not None:
            raise self._error
        return [obj for obj in self._objects if obj.path.startswith(prefix)]

    def download(self, remote_path: str, local_path: Path) -> None:  # pragma: no cover
        raise AssertionError("la idempotencia no debe descargar objetos")

    def close(self) -> None:  # pragma: no cover
        pass


def _indeed_file_plan(tmp_path: Path) -> recovery.RecoveryPlan:
    _write_parquet(
        tmp_path
        / "indeed_jobs_scraper"
        / "output"
        / "indeed_jobs_20261001_0001.parquet",
        "job_key",
        ["a"],
    )
    return recovery.build_plan(DATE_NEW, tmp_path)


def test_T07_published_file_item_is_omitted(tmp_path):
    plan = _indeed_file_plan(tmp_path)
    key = f"indeed/dia={DATE_NEW}/indeed_jobs_20261001_0001.parquet"
    reader = _FakeReader([landing.RemoteObject(path=key)])

    updated = recovery.apply_idempotence(plan, reader)

    item = _plan_item(updated, "indeed")
    assert item.recoverable is False
    assert item.published_key == key
    assert "ya publicado" in item.reason
    assert item.path is not None  # the local evidence is still reported
    assert _plan_item(plan, "indeed").recoverable is True  # original untouched
    payload = updated.to_dict()
    assert payload["items"][0]["published_key"] == key
    json.dumps(payload, ensure_ascii=False)  # still serializable


def test_T07_absent_file_item_stays_recoverable(tmp_path):
    plan = _indeed_file_plan(tmp_path)
    reader = _FakeReader()

    updated = recovery.apply_idempotence(plan, reader)

    item = _plan_item(updated, "indeed")
    assert item.recoverable is True
    assert item.published_key is None
    assert reader.list_calls  # the landing was consulted


def test_T07_ambiguous_listing_does_not_omit(tmp_path):
    plan = _indeed_file_plan(tmp_path)
    name = "indeed_jobs_20261001_0001.parquet"
    reader = _FakeReader(
        [
            landing.RemoteObject(path=f"indeed/dia={DATE_NEW}/staging-a/{name}"),
            landing.RemoteObject(path=f"indeed/dia={DATE_NEW}/staging-b/{name}"),
        ]
    )

    updated = recovery.apply_idempotence(plan, reader)

    assert _plan_item(updated, "indeed").recoverable is True


def test_T07_remote_error_propagates(tmp_path):
    plan = _indeed_file_plan(tmp_path)
    reader = _FakeReader(error=landing.RemoteError("sin conexion"))

    with pytest.raises(landing.RemoteError):
        recovery.apply_idempotence(plan, reader)

    # Nothing was half-updated: the original plan is untouched.
    assert _plan_item(plan, "indeed").recoverable is True
    assert _plan_item(plan, "indeed").published_key is None


def test_T07_apply_is_idempotent_and_does_not_mutate(tmp_path):
    plan = _indeed_file_plan(tmp_path)
    key = f"indeed/dia={DATE_NEW}/indeed_jobs_20261001_0001.parquet"
    reader = _FakeReader([landing.RemoteObject(path=key)])

    first = recovery.apply_idempotence(plan, reader)
    calls_after_first = len(reader.list_calls)
    second = recovery.apply_idempotence(first, reader)

    assert first.to_dict() == second.to_dict()
    assert len(reader.list_calls) == calls_after_first  # omitted items are skipped
    assert plan.to_dict()["items"][0]["recoverable"] is True


def test_T07_delta_with_zero_new_keys_is_omitted(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b"])
    plan = recovery.build_plan(DATE_NEW, tmp_path)
    delta = _plan_item(plan, "linkedin")
    assert delta.recoverable is True
    assert delta.new_keys == 2
    forced = recovery.RecoveryPlan(
        run_date=plan.run_date, items=(replace(delta, new_keys=0),)
    )
    reader = _FakeReader()

    updated = recovery.apply_idempotence(forced, reader)

    item = updated.items[0]
    assert item.recoverable is False
    assert "sin ofertas nuevas" in item.reason
    assert reader.list_calls == []  # deltas are never checked by remote key


def test_T07_delta_with_keys_is_kept_without_landing_check(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b"])
    plan = recovery.build_plan(DATE_NEW, tmp_path)
    reader = _FakeReader()

    updated = recovery.apply_idempotence(plan, reader)

    item = _plan_item(updated, "linkedin")
    assert item.recoverable is True
    assert item.new_keys == 2
    assert item.published_key is None
    assert reader.list_calls == []


def test_T07_merge_item_idempotence_uses_new_keys_when_known(tmp_path):
    csv = (
        tmp_path
        / "multi_site_job_scraper"
        / "data"
        / "irishjobs"
        / "output"
        / "jobs.csv"
    )
    csv.parent.mkdir(parents=True)
    csv.write_text("job_id\nx\n", encoding="utf-8")
    _set_mtime(csv, datetime(2026, 10, 1, 4, 47, 0))
    plan = recovery.build_plan(DATE_NEW, tmp_path)
    merge = _plan_item(plan, "multi_site")
    assert merge.mode == recovery.PLAN_MODE_MERGE
    assert merge.new_keys is None
    reader = _FakeReader()

    # An unknown delta stays: T-11 filters after the merge.
    kept = recovery.apply_idempotence(plan, reader)
    assert _plan_item(kept, "multi_site").recoverable is True
    assert reader.list_calls == []

    # If a merge item ever carries a measured 0, it is omitted.
    forced = recovery.RecoveryPlan(
        run_date=plan.run_date, items=(replace(merge, new_keys=0),)
    )
    omitted = recovery.apply_idempotence(forced, reader)
    assert omitted.items[0].recoverable is False
    assert "sin ofertas nuevas" in omitted.items[0].reason


def test_T07_repeated_uploaded_keys_do_not_change_the_delta(tmp_path):
    snapshot = tmp_path / "linkedin_jobs_scraper" / "data" / "output" / "jobs.parquet"
    _write_parquet(snapshot, "job_id", ["a", "b", "c"])
    _write_uploaded_keys(tmp_path, "linkedin", ["a", "a", "a"])

    item = _plan_item(recovery.build_plan(DATE_NEW, tmp_path), "linkedin")

    assert item.recoverable is True
    assert item.new_keys == 2  # 'a' is counted once


# --------------------------------------------------------------------------
# T-08: _READY decision and analysable closing block
# --------------------------------------------------------------------------


def test_T08_decide_ready_any_valid():
    empty = recovery.decide_ready("any_valid", [])
    assert empty.write is False
    assert empty.policy == "any_valid"
    assert empty.valid_sources == 0
    assert empty.reason == "ningun dato valido publicado"

    one = recovery.decide_ready(
        "any_valid", [recovery.PublishedSource("indeed", "ok", 3)]
    )
    assert one.write is True
    assert one.valid_sources == 1
    assert one.reason == "se publico 1 fuente con datos validos"

    mixed = recovery.decide_ready(
        "any_valid",
        [
            recovery.PublishedSource("indeed", "ok", 3),
            recovery.PublishedSource("linkedin", "partial", 2, 1),
            recovery.PublishedSource("infojobs", "no_data"),
        ],
    )
    assert mixed.write is True
    assert mixed.valid_sources == 2
    assert mixed.reason == "se publicaron 2 fuentes con datos validos"


def test_T08_decide_ready_all():
    all_valid = recovery.decide_ready(
        "all",
        [
            recovery.PublishedSource("indeed", "ok"),
            recovery.PublishedSource("linkedin", "partial"),
        ],
    )
    assert all_valid.write is True
    assert all_valid.valid_sources == 2
    assert (
        all_valid.reason
        == "todas las fuentes previstas publicaron datos validos (2/2)"
    )

    some_invalid = recovery.decide_ready(
        "all",
        [
            recovery.PublishedSource("indeed", "ok"),
            recovery.PublishedSource("linkedin", "no_data"),
        ],
    )
    assert some_invalid.write is False
    assert "la politica all exige que todas las fuentes publiquen" in some_invalid.reason
    assert "validas 1 de 2" in some_invalid.reason

    empty = recovery.decide_ready("all", [])
    assert empty.write is False
    assert "no hay fuentes publicadas" in empty.reason


def test_T08_decide_ready_unknown_policy_falls_back_to_all():
    decision = recovery.decide_ready(
        "raro", [recovery.PublishedSource("indeed", "ok")]
    )
    assert decision.policy == "all"
    assert decision.write is True
    assert "politica desconocida ('raro')" in decision.reason
    assert "se aplica 'all'" in decision.reason

    fails = recovery.decide_ready(
        "raro", [recovery.PublishedSource("indeed", "failed")]
    )
    assert fails.write is False


def test_T08_closing_block_makes_a_truncated_run_completed(tmp_path):
    logs = tmp_path / "logs"
    log_path = _write_log(
        logs,
        DATE_NEW,
        _inicio(DATE_NEW) + "00:12:45  [INFO]  [indeed] Resultado: ok (1 subidos)\n",
    )
    before = run_evidence.parse_pipeline_log(log_path)
    assert before.completed is False
    assert run_evidence.select_last_run(logs) is None

    lines = recovery.closing_block(
        [
            recovery.PublishedSource("indeed", "ok", 1, 0),
            recovery.PublishedSource("multi_site", "partial", 2, 1),
            recovery.PublishedSource("infojobs", "no_data", 0, 0),
        ],
        failures=1,
        duration_seconds=7200,
        finished_at=datetime(2026, 10, 1, 4, 50, 0),
    )
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    after = run_evidence.parse_pipeline_log(log_path)
    assert after.completed is True
    assert after.finished_at == f"{DATE_NEW}T04:50:00"
    assert after.global_failures == 1
    assert after.duration_seconds == 7200
    assert after.statuses["indeed"].status == "ok"
    assert after.statuses["indeed"].uploaded == 1
    assert after.statuses["multi_site"].status == "partial"
    assert after.statuses["multi_site"].uploaded == 2
    assert after.statuses["multi_site"].rejected == 1
    assert after.statuses["infojobs"].status == "no_data"

    selected = run_evidence.select_last_run(logs)
    assert selected is not None
    assert selected.completed is True
    assert selected.started_at == f"{DATE_NEW}T00:00:04"


def test_T08_closing_block_with_no_published_sources(tmp_path):
    lines = recovery.closing_block(
        [],
        failures=0,
        duration_seconds=60,
        finished_at=datetime(2026, 10, 1, 4, 50, 0),
    )
    assert lines == (
        "04:50:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 60s ====",
    )

    logs = tmp_path / "logs"
    log_path = _write_log(logs, DATE_NEW, _inicio(DATE_NEW))
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    after = run_evidence.parse_pipeline_log(log_path)
    assert after.completed is True
    assert after.statuses == {}
    assert run_evidence.select_last_run(logs) is not None


def test_T08_closing_block_rejects_unknown_machine_values():
    with pytest.raises(ValueError):
        recovery.closing_block(
            [recovery.PublishedSource("indeed", "raro")],
            failures=0,
            duration_seconds=1,
        )
    with pytest.raises(ValueError):
        recovery.closing_block(
            [recovery.PublishedSource("multi-site", "ok")],
            failures=0,
            duration_seconds=1,
        )


def test_T08_diagnostic_sees_the_run_after_closing_block(tmp_path):
    logs = tmp_path / "logs"
    log_path = _write_log(
        logs,
        DATE_NEW,
        _inicio(DATE_NEW) + "00:12:45  [INFO]  [indeed] Resultado: ok (1 subidos)\n",
    )

    before = verify_run.run_diagnostic(tmp_path, logs)
    assert before.run is None
    assert before.global_state == status.GLOBAL_INCONCLUSIVE

    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                recovery.closing_block(
                    [recovery.PublishedSource("indeed", "ok", 1, 0)],
                    failures=0,
                    duration_seconds=7200,
                    finished_at=datetime(2026, 10, 1, 4, 50, 0),
                )
            )
            + "\n"
        )

    after = verify_run.run_diagnostic(tmp_path, logs)
    assert after.run is not None
    assert after.run.date == DATE_NEW
    assert after.run.finished_at == f"{DATE_NEW}T04:50:00"
    assert after.global_state != status.GLOBAL_INCONCLUSIVE
