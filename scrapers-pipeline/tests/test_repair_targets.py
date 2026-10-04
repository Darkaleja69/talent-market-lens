"""Tests for reading, validating and freshness-checking the diagnostic (T-01).

Offline tests for ``repair.targets``: missing/unreadable/invalid diagnostic,
schema-version contract and the non-fatal staleness warning (RF-1, RF-13).
"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from repair import targets

_MISSING = object()


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
