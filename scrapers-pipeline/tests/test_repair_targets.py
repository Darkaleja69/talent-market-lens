"""Tests for the diagnostic selection (T-01, T-02).

Offline tests for ``repair.targets``: missing/unreadable/invalid diagnostic,
schema-version contract, the non-fatal staleness warning (T-01), and the
primary targets extracted from the real sanitized 2026-10-03 fixture, the
generic playbook, a correct diagnostic and an inconclusive one (T-02;
RF-1, RF-13).
"""
from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest

from repair import targets

_MISSING = object()

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REAL_FIXTURE = _FIXTURES_DIR / "diagnostic_2026-10-03.sanitized.json"

# Literal copy of the playbook table of plan §2.3, so a change in the module
# constant cannot silently pass the tests.
_EXPECTED_PLAYBOOK_PATHS = {
    "indeed": (
        "indeed_jobs_scraper/output/scrape_metadata_20261003_0006.json",
        "indeed_jobs_scraper/output/scrape_metadata_20261003_1247.json",
        "indeed_jobs_scraper/output/nightly_stderr_IE_attempt1.log",
        "indeed_jobs_scraper/output/log_20261003_*.log",
    ),
    "linkedin": (
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "linkedin_jobs_scraper/data/output/jobs.parquet",
        "linkedin_jobs_scraper/data/run.log",
    ),
    "infojobs": (
        "infojobs_jobs_scraper/data/run_nightly.log",
        "infojobs_jobs_scraper/data/nightly_stdout_attempt1.log",
        "infojobs_jobs_scraper/data/logs/run_20261003_*.log",
    ),
    "irishjobs": (
        "multi_site_job_scraper/data/merged/last_run.json",
        "multi_site_job_scraper/data/irishjobs/run.log",
    ),
    "glassdoor": (
        "multi_site_job_scraper/data/merged/last_run.json",
        "multi_site_job_scraper/data/glassdoor/run.log",
    ),
}


def _write_diagnostic(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _diagnostic(
    run_date: str | None = "2026-10-03",
    *,
    schema_version: object = targets.SCHEMA_VERSION,
) -> dict:
    payload: dict = {"run": {}}
    if schema_version is not _MISSING:
        payload["schema_version"] = schema_version
    if run_date is not None:
        payload["run"]["date"] = run_date
    return payload


def _write_upload_log(logs_dir: Path, name: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / name
    path.write_text("Fin pipeline", encoding="utf-8")
    return path


# --- Loading and validation (RF-1, RF-13) ------------------------------------


def test_default_path_is_anchored_to_the_package():
    expected = (
        Path(targets.__file__).resolve().parents[1]
        / "logs"
        / "diagnostic_last.json"
    )

    assert targets.DEFAULT_DIAGNOSTIC_PATH == expected
    assert targets.DEFAULT_DIAGNOSTIC_PATH.name == "diagnostic_last.json"


def test_missing_diagnostic_raises_with_path_and_reason(tmp_path):
    missing = tmp_path / "diagnostic_last.json"

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(missing)

    message = str(excinfo.value)
    assert str(missing) in message
    assert "no se encontró" in message


def test_unreadable_diagnostic_raises_with_read_reason(tmp_path):
    directory = tmp_path / "diagnostic_last.json"
    directory.mkdir()

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(directory)

    message = str(excinfo.value)
    assert str(directory) in message
    assert "no se pudo leer" in message


def test_invalid_json_raises_with_path_and_reason(tmp_path):
    path = tmp_path / "diagnostic_last.json"
    path.write_text("{esto no es json", encoding="utf-8")

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "JSON válido" in message


def test_non_utf8_diagnostic_raises_with_path_and_reason(tmp_path):
    path = tmp_path / "diagnostic_last.json"
    path.write_bytes(b"\xff\xfe{}")

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "no se pudo leer" in message
    assert "UTF-8" in message


def test_json_that_is_not_an_object_raises(tmp_path):
    path = tmp_path / "diagnostic_last.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "no es un objeto JSON" in message


def test_schema_version_missing_raises_naming_the_field(tmp_path):
    path = _write_diagnostic(
        tmp_path / "diagnostic_last.json", _diagnostic(schema_version=_MISSING)
    )

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "schema_version" in message
    assert str(targets.SCHEMA_VERSION) in message


@pytest.mark.parametrize("version", [2, 0, "1", True, 1.0, None])
def test_unknown_or_invalid_schema_version_raises(tmp_path, version):
    path = _write_diagnostic(
        tmp_path / "diagnostic_last.json",
        _diagnostic(schema_version=version),
    )

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "schema_version" in message
    assert str(targets.SCHEMA_VERSION) in message


def test_valid_diagnostic_exposes_payload_and_path(tmp_path):
    payload = _diagnostic(run_date="2026-10-03")
    path = _write_diagnostic(tmp_path / "diagnostic_last.json", payload)

    loaded = targets.load_diagnostic(path)

    assert loaded.path == path
    assert loaded.payload["schema_version"] == 1
    assert loaded.payload["run"]["date"] == "2026-10-03"
    assert loaded.warnings == ()


def test_loaded_diagnostic_is_frozen(tmp_path):
    path = _write_diagnostic(tmp_path / "diagnostic_last.json", _diagnostic())

    loaded = targets.load_diagnostic(path)

    with pytest.raises(FrozenInstanceError):
        loaded.warnings = ()  # type: ignore[misc]


# --- Freshness warning (RF-1; plan section 2.1) ------------------------------


def test_up_to_date_diagnostic_has_no_warning(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-03.log")
    path = _write_diagnostic(logs_dir / "diagnostic_last.json", _diagnostic())

    loaded = targets.load_diagnostic(path)

    assert loaded.warnings == ()


def test_diagnostic_newer_than_uploads_has_no_warning(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-02.log")
    path = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date="2026-10-03")
    )

    loaded = targets.load_diagnostic(path)

    assert loaded.warnings == ()


def test_diagnostic_without_upload_logs_has_no_warning(tmp_path):
    path = _write_diagnostic(tmp_path / "diagnostic_last.json", _diagnostic())

    loaded = targets.load_diagnostic(path)

    assert loaded.warnings == ()


def test_stale_diagnostic_warns_with_both_dates(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-03.log")
    path = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date="2026-09-30")
    )

    loaded = targets.load_diagnostic(path)

    assert len(loaded.warnings) == 1
    warning = loaded.warnings[0]
    assert "más antiguo" in warning
    assert "2026-09-30" in warning
    assert "2026-10-03" in warning
    assert "upload-2026-10-03.log" in warning


def test_stale_diagnostic_warns_with_source_prefixed_upload_log(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-multi_site-2026-09-18.log")
    path = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date="2026-09-17")
    )

    loaded = targets.load_diagnostic(path)

    assert len(loaded.warnings) == 1
    assert "upload-multi_site-2026-09-18.log" in loaded.warnings[0]
    assert "2026-09-17" in loaded.warnings[0]


def test_stale_diagnostic_warns_with_hyphenated_source_upload_log(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-multi-site-2026-10-03.log")
    path = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date="2026-10-02")
    )

    loaded = targets.load_diagnostic(path)

    assert len(loaded.warnings) == 1
    assert "upload-multi-site-2026-10-03.log" in loaded.warnings[0]
    assert "2026-10-02" in loaded.warnings[0]


def test_stale_diagnostic_uses_the_latest_upload_and_ignores_noise(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-02.log")
    _write_upload_log(logs_dir, "upload-2026-10-03.log")
    _write_upload_log(logs_dir, "upload-sin_fecha.log")
    (logs_dir / "diagnostic_last.json.bak").write_text("{}", encoding="utf-8")
    path = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date="2026-10-01")
    )

    loaded = targets.load_diagnostic(path)

    assert len(loaded.warnings) == 1
    assert "upload-2026-10-03.log" in loaded.warnings[0]
    assert "upload-2026-10-02.log" not in loaded.warnings[0]


def test_explicit_logs_dir_overrides_the_diagnostic_directory(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-03.log")
    path = _write_diagnostic(
        tmp_path / "diagnostic_last.json", _diagnostic(run_date="2026-09-29")
    )

    without_override = targets.load_diagnostic(path)
    with_override = targets.load_diagnostic(path, logs_dir=logs_dir)

    assert without_override.warnings == ()
    assert len(with_override.warnings) == 1
    assert "2026-09-29" in with_override.warnings[0]


def test_unusable_run_date_disables_the_warning(tmp_path):
    logs_dir = tmp_path / "logs"
    _write_upload_log(logs_dir, "upload-2026-10-03.log")
    missing_date = _write_diagnostic(
        logs_dir / "diagnostic_last.json", _diagnostic(run_date=None)
    )
    invalid_date = _write_diagnostic(
        logs_dir / "other.json", _diagnostic(run_date="ayer")
    )

    assert targets.load_diagnostic(missing_date).warnings == ()
    assert targets.load_diagnostic(invalid_date).warnings == ()


# --- Primary targets from the diagnostic (T-02; RF-1) ------------------------


def _load_fixture_payload() -> dict:
    return json.loads(_REAL_FIXTURE.read_text(encoding="utf-8"))


def _source(source_id: str, *, status: str = "failed", **overrides) -> dict:
    """Build one diagnostic source entry with sane defaults."""
    source = {
        "id": source_id,
        "kind": "direct",
        "status": status,
        "outcome": "ok",
        "offers_current_run": 1,
        "offers_snapshot": None,
        "completeness": [],
        "publication": {
            "state": "not_checked",
            "obtained_offers": None,
            "delta_offers": None,
        },
        "failures": [],
        "evidence": [],
    }
    source.update(overrides)
    return source


def _payload(
    global_status: str,
    sources: list[dict],
    *,
    reason: str | None = None,
) -> dict:
    return {
        "schema_version": targets.SCHEMA_VERSION,
        "run": {"date": "2026-10-03"},
        "global_status": {"status": global_status, "reason": reason},
        "sources": sources,
        "trend": {"runs_used": 0, "note": None},
        "investigations": [],
    }


def _select_from_payload(tmp_path: Path, payload: dict):
    path = _write_diagnostic(tmp_path / "diagnostic_last.json", payload)
    return targets.select_targets(targets.load_diagnostic(path)), path


def _select_from_fixture():
    return targets.select_targets(targets.load_diagnostic(_REAL_FIXTURE))


def _target(selected, source_id):
    return next(target for target in selected if target.source == source_id)


def test_playbook_evidence_table_matches_plan_section_2_3():
    assert targets.PLAYBOOK_EVIDENCE_PATHS == _EXPECTED_PLAYBOOK_PATHS


def test_playbook_constant_values_are_tuples():
    for paths in targets.PLAYBOOK_EVIDENCE_PATHS.values():
        assert isinstance(paths, tuple)


def test_real_fixture_is_sanitized():
    text = _REAL_FIXTURE.read_text(encoding="utf-8")

    assert "<HOME>" in text
    assert "aleja" not in text
    assert re.search(r"[A-Za-z]:[\\/]{1,2}Users", text) is None


def test_real_diagnostic_selects_only_the_five_failed_sources():
    selected = _select_from_fixture()

    assert [target.source for target in selected] == [
        "indeed",
        "linkedin",
        "infojobs",
        "irishjobs",
        "glassdoor",
    ]
    assert {target.source for target in selected} == {
        "indeed",
        "linkedin",
        "infojobs",
        "irishjobs",
        "glassdoor",
    }


@pytest.mark.parametrize(
    ("source", "kind", "outcome", "offers_current_run", "offers_snapshot"),
    [
        ("indeed", "direct", "ok", 57, None),
        ("linkedin", "direct", "ok", 1590, 8387),
        ("infojobs", "direct", "blocked", 0, None),
        ("irishjobs", "multi_site", "error", None, None),
        ("glassdoor", "multi_site", "error", None, None),
    ],
)
def test_real_diagnostic_targets_carry_state_and_offers(
    source, kind, outcome, offers_current_run, offers_snapshot
):
    target = _target(_select_from_fixture(), source)

    assert target.kind == kind
    assert target.status == "failed"
    assert target.outcome == outcome
    assert target.offers_current_run == offers_current_run
    assert target.offers_snapshot == offers_snapshot
    assert target.run_date == date(2026, 10, 3)


def test_real_diagnostic_targets_carry_reasons_and_evidence():
    selected = _select_from_fixture()

    indeed = _target(selected, "indeed")
    assert indeed.failures == (
        "campo obligatorio 'description' por debajo del umbral",
    )
    assert (
        "detalle: metadata=scrape_metadata_20261003_0006.json "
        "finished_at=2026-10-03T01:21:08.627374"
    ) in indeed.evidence
    assert "intento 2/2" in indeed.evidence

    linkedin = _target(selected, "linkedin")
    assert linkedin.failures == (
        "campo obligatorio 'description' por debajo del umbral",
    )
    assert "detalle: stdout=nightly_stdout_attempt1.log" in linkedin.evidence
    assert "intento 1/5" in linkedin.evidence

    infojobs = _target(selected, "infojobs")
    assert infojobs.outcome == "blocked"
    assert infojobs.failures == (
        "sin evidencia suficiente para confirmar la fuente",
    )
    assert "error registrado: captcha block detected" in infojobs.evidence
    assert "detalle: blocked=true" in infojobs.evidence

    for portal in ("irishjobs", "glassdoor"):
        target = _target(selected, portal)
        # exit=75 is the Multi-site progress watchdog (plan §4).
        assert "error registrado: exit=75" in target.evidence
        assert (
            "detalle: exit=75 merge_exit=0 output_merged=False" in target.evidence
        )


def test_real_diagnostic_targets_carry_playbook_and_generic_evidence_paths():
    loaded = targets.load_diagnostic(_REAL_FIXTURE)
    selected = targets.select_targets(loaded)
    diagnostic_path = str(_REAL_FIXTURE)
    run_log_path = loaded.payload["run"]["log_path"]

    for target in selected:
        playbook = _EXPECTED_PLAYBOOK_PATHS[target.source]
        assert target.evidence_paths[: len(playbook)] == playbook
        assert target.evidence_paths[len(playbook):] == (
            diagnostic_path,
            run_log_path,
        )


def test_unknown_source_produces_a_generic_target_without_playbook_paths(
    tmp_path,
):
    payload = _load_fixture_payload()
    payload["sources"].append(
        _source(
            "nuevafuente",
            outcome="error",
            offers_current_run=None,
            failures=[],
            evidence=["error registrado: prueba"],
        )
    )

    selected, diagnostic_path = _select_from_payload(tmp_path, payload)
    unknown = _target(selected, "nuevafuente")

    assert len(selected) == 6
    assert unknown.kind == "direct"
    assert unknown.outcome == "error"
    assert unknown.evidence == ("error registrado: prueba",)
    assert unknown.evidence_paths == (
        str(diagnostic_path),
        payload["run"]["log_path"],
    )


def test_correct_diagnostic_produces_no_targets(tmp_path):
    payload = _payload(
        "ok",
        [_source("indeed", status="ok"), _source("linkedin", status="ok")],
    )

    selected, _ = _select_from_payload(tmp_path, payload)

    assert selected == ()


def test_ok_source_does_not_become_a_target(tmp_path):
    payload = _payload(
        "partial",
        [
            _source("infojobs", outcome="blocked", evidence=["captcha"]),
            _source("stepstone_nl", status="ok", offers_current_run=3),
        ],
    )

    selected, _ = _select_from_payload(tmp_path, payload)

    assert [target.source for target in selected] == ["infojobs"]


def test_inconclusive_diagnostic_is_rejected_with_a_clear_message(tmp_path):
    payload = _payload(
        "inconclusive",
        [_source("infojobs")],
        reason="no hay una ejecución completa que analizar",
    )
    path = _write_diagnostic(tmp_path / "diagnostic_last.json", payload)

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.select_targets(targets.load_diagnostic(path))

    message = str(excinfo.value)
    assert "inconcluso" in message
    assert "inconclusive" in message
    assert str(path) in message
    assert "no hay una ejecución completa que analizar" in message
    assert "no se inicia ninguna reparación" in message


def test_repair_target_is_frozen():
    target = _select_from_fixture()[0]

    with pytest.raises(FrozenInstanceError):
        target.status = "ok"  # type: ignore[misc]
