"""Tests for the hygiene audit of the repair records (T-15; RF-11).

Offline tests for ``repair.brief.find_forbidden``/``find_forbidden_text`` and
``repair.records.audit_records``:

- the real ``repairs/`` tree (``README.md``, the sanitized ``example/`` and
  ``history.json`` when it exists) has zero findings;
- a synthetic record with ``password=``, an ``Authorization: Bearer`` header,
  a ``cf_clearance=`` cookie, a JWT and a Chrome profile path inside
  ``plan.md``/``context.json``/``evidence/`` yields findings with their file
  path and Spanish motive, while binary evidence is skipped without failing;
- the negative controls (``<HOME>``, ``AppData\\Local\\Temp``,
  "challenge-token reuse", "cookies/clearance tokens") are clean;
- a record completed through ``create_record`` + ``complete_record`` is clean
  until a secret enters a free text such as ``result``.

Only the real-tree test reads the repository records; the synthetic and flow
tests work under ``tmp_path``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from repair import brief, records, targets

_PIPELINE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PIPELINE_DIR.parent
_FIXTURE = (
    _PIPELINE_DIR / "tests" / "fixtures" / "diagnostic_2026-10-03.sanitized.json"
)

# Opening date used to pin the record content in the flow test.
_OPENED = date(2026, 10, 4)

# Real files the audit must cover; ``history.json`` is optional until a repair
# is completed, so it is not required here.
_REAL_FILES = (
    _REPO_ROOT / "repairs" / "README.md",
    _REPO_ROOT / "repairs" / "example" / "plan.md",
    _REPO_ROOT / "repairs" / "example" / "context.json",
    _REPO_ROOT / "repairs" / "example" / "quality_before.json",
    _REPO_ROOT / "repairs" / "example" / "quality_after.json",
)

_FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
_PROFILE_PATH = r"C:\Users\X\AppData\Local\Google\Chrome\User Data\Default"

# Technical references that are not secrets and must stay clean: sanitized
# user paths, temporary diagnostics and glossary mentions of tokens/cookies.
_NEGATIVE_TEXTS = (
    r"<HOME>\Documents\projects\infojobs_jobs_scraper\data\run_nightly.log",
    r"C:\Users\example\AppData\Local\Temp\diagnostic_last.json",
    "challenge-token reuse",
    "cookies/clearance tokens",
)

_INDEX_TEMPLATE = """# Registros de reparaciones (prueba)

Índice de los registros.

<!-- repair-index:start -->
| fecha | fuente | estado | rama | resultado | calidad |
|---|---|---|---|---|---|
<!-- repair-index:end -->
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _joined_reasons(findings: tuple[records.HygieneFinding, ...]) -> str:
    return "\n".join(finding.reason for finding in findings)


# --- Reusable detection (brief.py) -------------------------------------------


def test_find_forbidden_reports_credential_keys_and_profile_paths():
    reasons = brief.find_forbidden(
        {"nested": [{"api_key": "x"}, {"path": _PROFILE_PATH}]}
    )

    assert any(
        "clave sensible" in reason and "api_key" in reason for reason in reasons
    )
    assert any("perfil de navegador" in reason for reason in reasons)


def test_find_forbidden_is_empty_for_a_clean_structure():
    assert (
        brief.find_forbidden(
            {"source": "infojobs", "paths": ["<HOME>\\data", "C:/tmp/x.json"]}
        )
        == ()
    )


@pytest.mark.parametrize(
    "text",
    [
        "password=hunter2",
        '"password": "hunter2"',
        '"sas": "sv=2021"',
        "access_token=abc123",
        "Authorization: Bearer abcdef123456",
        "SharedAccessSignature sv=2021&sig=abc123",
        "?sv=2021&sig=abc123",
        "AccountKey=abc123==",
        _FAKE_JWT,
        "Cookie: cf_clearance=abc123def456",
        "_abck=abc123",
        "datadome=abc123",
        _PROFILE_PATH,
    ],
)
def test_find_forbidden_text_flags_real_secret_values(text):
    assert brief.find_forbidden_text(text)


@pytest.mark.parametrize("text", _NEGATIVE_TEXTS)
def test_find_forbidden_text_accepts_technical_prose(text):
    assert brief.find_forbidden_text(text) == ()


def test_find_forbidden_text_never_echoes_the_secret_value():
    reasons = brief.find_forbidden_text(
        "password=hunter2\n"
        "Authorization: Bearer abcdef123456\n"
        f"Cookie: cf_clearance=abc123def456\n{_FAKE_JWT}\n"
    )

    assert reasons
    joined = "\n".join(reasons)
    for secret in ("hunter2", "abcdef123456", "abc123def456", _FAKE_JWT):
        assert secret not in joined


# --- Real records tree --------------------------------------------------------


def test_real_repairs_tree_is_clean():
    for path in _REAL_FILES:
        assert path.is_file(), path

    assert records.audit_records() == ()


# --- Positive control: a poisoned synthetic record ----------------------------


def _poisoned_record(base: Path) -> Path:
    record = base / "20261003-infojobs"
    _write(
        record / "plan.md",
        "# Reparación sintética\n\n"
        "- Credencial de prueba: password=hunter2\n"
        "- Petición: Authorization: Bearer abcdef123456\n",
    )
    _write(
        record / "context.json",
        json.dumps(
            {"source": "infojobs", "credentials": {"password": "hunter2"}},
            indent=2,
        ),
    )
    _write(record / "quality_before.json", json.dumps({"fields": []}))
    _write(
        record / "evidence" / "network.txt",
        "Cookie: cf_clearance=abc123def456\n"
        f"Authorization: Bearer {_FAKE_JWT}\n",
    )
    _write(
        record / "evidence" / "session.json",
        json.dumps({"access_token": "abc123"}),
    )
    _write(record / "evidence" / "profile.txt", _PROFILE_PATH + "\n")
    # Binary evidence carrying a decoy secret: it is never decoded as text.
    _write_bytes(
        record / "evidence" / "screenshot.png",
        b"\x89PNG\r\n\x1a\n\x00password=hunter2",
    )
    return record


def test_audit_reports_secrets_across_plan_context_and_evidence(tmp_path):
    base = tmp_path / "repairs"
    record = _poisoned_record(base)

    findings = records.audit_records(base)

    assert findings
    paths = {finding.path for finding in findings}
    assert record / "plan.md" in paths
    assert record / "context.json" in paths
    assert record / "evidence" / "network.txt" in paths
    assert record / "evidence" / "session.json" in paths
    assert record / "evidence" / "profile.txt" in paths
    assert record / "evidence" / "screenshot.png" not in paths

    reasons = _joined_reasons(findings)
    assert "clave sensible" in reasons
    assert "Bearer" in reasons
    assert "challenge" in reasons
    assert "JWT" in reasons
    assert "perfil de navegador" in reasons
    # The audit reports the source of the leak, never the secret itself.
    for secret in ("hunter2", "abc123", "abcdef123456", _FAKE_JWT):
        assert secret not in reasons


def test_audit_reports_malformed_json_and_undecodable_text_as_findings(
    tmp_path,
):
    base = tmp_path / "repairs"
    record = base / "20261003-infojobs"
    # A truncated JSON is reported (as integrity note) but its secret is still
    # caught; a non-UTF-8 text file is a finding, never an exception.
    _write(record / "context.json", '{"token": "abc123",')
    _write_bytes(record / "evidence" / "notes.txt", b"\xff\xfe not utf-8")

    findings = records.audit_records(base)

    context_reasons = " ".join(
        finding.reason
        for finding in findings
        if finding.path == record / "context.json"
    )
    assert "clave sensible" in context_reasons
    assert "JSON" in context_reasons
    assert any(
        finding.path == record / "evidence" / "notes.txt"
        and "UTF-8" in finding.reason
        for finding in findings
    )


def test_audit_skips_binary_evidence_without_failing(tmp_path):
    base = tmp_path / "repairs"
    record = _poisoned_record(base)
    # Only the binary file carries forbidden content: the audit must not raise
    # and must not read it as text.
    for path in (
        record / "plan.md",
        record / "context.json",
        record / "quality_before.json",
        record / "evidence" / "network.txt",
        record / "evidence" / "session.json",
        record / "evidence" / "profile.txt",
    ):
        path.unlink()

    assert records.audit_records(base) == ()


# --- Negative control: technical text in a synthetic record -------------------


def test_technical_text_is_clean_in_a_synthetic_record(tmp_path):
    base = tmp_path / "repairs"
    record = base / "20261003-infojobs"
    text = "\n".join(_NEGATIVE_TEXTS) + "\n"
    _write(record / "plan.md", text)
    _write(record / "context.json", json.dumps({"note": text}))
    _write(record / "evidence" / "notes.txt", text)
    _write(
        base / "history.json",
        json.dumps(
            {"schema_version": 1, "sources": {"infojobs": {"best_verified_offers": 3}}}
        ),
    )
    _write(base / "README.md", _INDEX_TEMPLATE)

    assert records.audit_records(base) == ()


# --- Flow regression: create_record + complete_record -------------------------


def _infojobs_brief() -> dict:
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        item
        for item in targets.select_targets(loaded)
        if item.source == "infojobs"
    )
    return brief.build_brief(target)


def test_flow_record_is_clean_until_a_secret_enters_result(tmp_path):
    base = tmp_path / "repairs"
    record = records.create_record(
        _infojobs_brief(), repairs_dir=base, on_date=_OPENED
    )
    index = base / "README.md"
    _write(index, _INDEX_TEMPLATE)
    history = base / "history.json"

    records.complete_record(
        record,
        status=records.STATUS_TESTED,
        result="Fuente reparada: 3 ofertas con umbral 1",
        changes="- Sesión persistente y ritmo humano.",
        tests="- `python -m pytest tests -q` → OK.",
        live_test="- **Alcance:** 1 keyword × 1 ciudad × 1 página.",
        verified_offers=3,
        index_path=index,
        history_path=history,
    )

    assert records.audit_records(base) == ()

    # T-14 free texts had no sanitization of their own: the secret now in
    # ``result`` must be caught by the T-15 safety net.
    records.complete_record(
        record,
        status=records.STATUS_TESTED,
        result="Sesión reutilizada con token=abc123def456",
        verified_offers=3,
        index_path=index,
        history_path=history,
    )

    findings = records.audit_records(base)
    assert any(
        finding.path == record.plan_path
        and "clave sensible" in finding.reason
        for finding in findings
    )
    assert "abc123def456" not in _joined_reasons(findings)
