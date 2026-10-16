"""Tests for the English JSON brief of a repair target (T-05; RF-1, RF-3).

Offline tests for ``repair.brief``: exact structure and types, the real
primary (InfoJobs) and secondary (NVB) content, the playbook mapping of plan
§6, the proposed live-test scopes, the generic source path, the JSON
round-trip, the safety gate for credentials/browser profiles and the
coherence with the target quality profile.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from repair import brief, targets
from verification import field_contract

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REAL_FIXTURE = _FIXTURES_DIR / "diagnostic_2026-10-03.sanitized.json"

_EXPECTED_TOP_KEYS = {
    "schema_version",
    "source",
    "kind",
    "role",
    "field",
    "trigger",
    "run_date",
    "offers_current_run",
    "offers_snapshot",
    "completeness_scope",
    "reason",
    "evidence",
    "evidence_paths",
    "evidence_paths_base",
    "playbook",
    "quality",
    "test_scope",
}
_EXPECTED_QUALITY_KEYS = {
    "field",
    "required",
    "current_pct",
    "target_pct",
    "is_focus",
}
_EXPECTED_SCOPE_KEYS = {"description", "parameters", "constraints", "command"}

_PROFILE_PATH = (
    r"C:\Users\example\AppData\Local\Google\Chrome\User Data\Default\Cookies"
)


def _loaded():
    return targets.load_diagnostic(_REAL_FIXTURE)


def _primary(source_id):
    return next(
        target
        for target in targets.select_targets(_loaded())
        if target.source == source_id
    )


def _secondary(source_id, field):
    return next(
        target
        for target in targets.select_secondary_targets(_loaded())
        if target.source == source_id and target.field == field
    )


def _unknown_target(tmp_path: Path, *, kind: str = "direct"):
    payload = json.loads(_REAL_FIXTURE.read_text(encoding="utf-8"))
    payload["sources"].append(
        {
            "id": "nuevafuente",
            "kind": kind,
            "status": "failed",
            "outcome": "error",
            "offers_current_run": None,
            "offers_snapshot": None,
            "completeness": [],
            "publication": {
                "state": "not_checked",
                "obtained_offers": None,
                "delta_offers": None,
            },
            "failures": [],
            "evidence": ["error registrado: prueba"],
        }
    )
    path = tmp_path / "diagnostic_last.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return next(
        target
        for target in targets.select_targets(targets.load_diagnostic(path))
        if target.source == "nuevafuente"
    )


# --- Structure and types -----------------------------------------------------


def test_brief_has_the_exact_structure_and_types():
    built = brief.build_brief(_primary("infojobs"))

    assert set(built) == _EXPECTED_TOP_KEYS
    assert built["schema_version"] == 1
    assert built["source"] == "infojobs"
    assert built["kind"] == "direct"
    assert built["role"] == "primary"
    assert built["field"] is None
    assert built["trigger"] is None
    assert built["run_date"] == "2026-10-03"
    assert set(built["reason"]) == {"outcome", "failures", "summary"}
    assert isinstance(built["reason"]["outcome"], str)
    assert isinstance(built["reason"]["summary"], str)
    assert isinstance(built["reason"]["failures"], list)
    assert all(isinstance(item, str) for item in built["evidence"])
    assert all(isinstance(item, str) for item in built["evidence_paths"])
    assert built["evidence_paths_base"] == "repository_root"
    assert set(built["playbook"]) == {
        "section",
        "title",
        "recipe",
        "test_scope",
    }
    assert built["playbook"]["test_scope"] == built["test_scope"]
    assert len(built["quality"]) == len(field_contract.MEASURED_FIELDS)
    for entry in built["quality"]:
        assert set(entry) == _EXPECTED_QUALITY_KEYS
        assert isinstance(entry["required"], bool)
        assert isinstance(entry["is_focus"], bool)
    assert set(built["test_scope"]) == _EXPECTED_SCOPE_KEYS
    assert isinstance(built["test_scope"]["constraints"], list)


def test_secondary_brief_has_the_secondary_reason_keys():
    built = brief.build_brief(_secondary("nvb", "work_mode"))

    assert set(built["reason"]) == {
        "trigger",
        "field",
        "current_pct",
        "summary",
    }
    assert built["field"] == "work_mode"
    assert built["trigger"] == targets.TRIGGER_OPTIONAL_FIELD


# --- Real primary and secondary content --------------------------------------


def test_infojobs_primary_brief_content():
    built = brief.build_brief(_primary("infojobs"))

    assert built["reason"]["outcome"] == "blocked"
    assert built["reason"]["summary"].startswith("blocked: captcha/anti-bot")
    assert built["reason"]["failures"] == [
        "sin evidencia suficiente para confirmar la fuente"
    ]
    assert any("captcha block detected" in item for item in built["evidence"])
    assert built["playbook"]["section"] == "6.1"
    assert built["playbook"]["title"]
    assert built["playbook"]["recipe"]
    assert built["test_scope"]["parameters"] == {
        "keywords": 1,
        "cities": 1,
        "pages": 1,
    }
    assert "no Azure upload" in built["test_scope"]["constraints"]
    assert "no landing update" in built["test_scope"]["constraints"]
    assert "no merge" in built["test_scope"]["constraints"]

    # The in-repo diagnostic path is re-anchored to the repository root and
    # no local user path, credential or browser profile leaks into the brief.
    assert (
        "scrapers-pipeline/tests/fixtures/diagnostic_2026-10-03.sanitized.json"
        in built["evidence_paths"]
    )
    text = json.dumps(built, ensure_ascii=False)
    assert "aleja" not in text
    assert "AppData" not in text
    assert "Chrome" not in text
    assert "Cookies" not in text


def test_brief_carries_the_run_and_snapshot_offers_and_scope():
    # LinkedIn's completeness aggregates an older snapshot (plan §4); the
    # diagnostic does not publish the age of the rows missing a field.
    linkedin = brief.build_brief(_primary("linkedin"))
    assert linkedin["offers_current_run"] == 1590
    assert linkedin["offers_snapshot"] == 8387
    assert (
        linkedin["completeness_scope"]
        == brief.COMPLETENESS_SCOPE_HISTORICAL_SNAPSHOT
    )

    infojobs = brief.build_brief(_primary("infojobs"))
    assert infojobs["offers_current_run"] == 0
    assert infojobs["offers_snapshot"] is None
    assert infojobs["completeness_scope"] == brief.COMPLETENESS_SCOPE_RUN


def test_indeed_primary_brief_reports_the_completeness_failure():
    built = brief.build_brief(_primary("indeed"))

    assert built["reason"]["outcome"] == "ok"
    assert built["reason"]["failures"] == [
        "campo obligatorio 'description' por debajo del umbral"
    ]
    assert built["reason"]["summary"].startswith("ok: run completed")
    assert "1 diagnostic failure reason" in built["reason"]["summary"]
    assert built["playbook"]["section"] == "6.2"


def test_nvb_secondary_brief_content():
    built = brief.build_brief(_secondary("nvb", "work_mode"))

    assert built["role"] == "secondary"
    assert built["field"] == "work_mode"
    assert built["trigger"] == targets.TRIGGER_OPTIONAL_FIELD
    assert built["reason"]["trigger"] == targets.TRIGGER_OPTIONAL_FIELD
    assert built["reason"]["field"] == "work_mode"
    assert built["reason"]["current_pct"] == 0.0
    assert "work_mode" in built["reason"]["summary"]
    assert built["playbook"]["section"] == "6.6"
    assert built["test_scope"]["parameters"]["scope"] == "representative sample"
    assert built["test_scope"]["parameters"]["field"] == "work_mode"

    focus = [entry for entry in built["quality"] if entry["is_focus"]]
    assert [entry["field"] for entry in focus] == ["work_mode"]
    assert focus[0]["current_pct"] == 0.0
    assert focus[0]["target_pct"] == 100.0


def test_linkedin_required_secondary_brief_content():
    built = brief.build_brief(_secondary("linkedin", "description"))

    assert built["trigger"] == targets.TRIGGER_REQUIRED_FIELD
    assert built["reason"]["current_pct"] == pytest.approx(88.44640515082867)
    assert "required field 'description'" in built["reason"]["summary"]
    assert built["playbook"]["section"] == "6.3"


def test_brief_copies_the_diagnostic_evidence_verbatim():
    target = _primary("infojobs")

    built = brief.build_brief(target)

    assert built["evidence"] == list(target.evidence)
    # Playbook paths are already repository-relative and stay untouched; the
    # in-repo diagnostic path is re-anchored to the repository root.
    assert built["evidence_paths"][:3] == list(target.evidence_paths[:3])
    assert built["evidence_paths"][3] == (
        "scrapers-pipeline/tests/fixtures/diagnostic_2026-10-03.sanitized.json"
    )
    assert built["evidence_paths"][4] == target.evidence_paths[4]


# --- Playbook mapping and test scopes ----------------------------------------


@pytest.mark.parametrize(
    ("source", "section"),
    [
        ("infojobs", "6.1"),
        ("indeed", "6.2"),
        ("linkedin", "6.3"),
        ("irishjobs", "6.4"),
        ("glassdoor", "6.5"),
    ],
)
def test_primary_playbook_section(source, section):
    assert brief.build_brief(_primary(source))["playbook"]["section"] == section


@pytest.mark.parametrize("source", ["stepstone_nl", "nvb", "jobs_ch"])
def test_secondary_only_sources_use_the_completeness_playbook(source):
    target = next(
        target
        for target in targets.select_secondary_targets(_loaded())
        if target.source == source
    )

    assert brief.build_brief(target)["playbook"]["section"] == "6.6"


def test_secondary_of_a_failed_source_keeps_its_source_playbook():
    assert (
        brief.build_brief(_secondary("indeed", "salary"))["playbook"]["section"]
        == "6.2"
    )
    assert (
        brief.build_brief(_secondary("linkedin", "salary"))["playbook"][
            "section"
        ]
        == "6.3"
    )


def test_test_scopes_of_the_known_sources():
    indeed_scope = brief.build_brief(_primary("indeed"))["test_scope"]
    assert indeed_scope["parameters"] == {
        "countries": 1,
        "terms": 1,
        "pages": 1,
    }

    linkedin_scope = brief.build_brief(_primary("linkedin"))["test_scope"]
    assert linkedin_scope["parameters"]["mode"] == "guest"

    irishjobs_scope = brief.build_brief(_primary("irishjobs"))["test_scope"]
    assert irishjobs_scope["command"] == "python -m src.irishjobs --max-pages 1"
    assert irishjobs_scope["parameters"]["portals"] == ["irishjobs"]
    assert "only the affected portal" in irishjobs_scope["constraints"]

    glassdoor_scope = brief.build_brief(_primary("glassdoor"))["test_scope"]
    assert glassdoor_scope["command"] == "python -m src.glassdoor --max-pages 1"
    assert "PROGRESS" in glassdoor_scope["description"]


# --- Unknown sources and serialization ---------------------------------------


def test_unknown_source_gets_the_generic_playbook_and_scope(tmp_path):
    built = brief.build_brief(_unknown_target(tmp_path))

    assert built["playbook"]["section"] == "6.7"
    assert built["playbook"]["title"]
    assert "minimum representative configuration" in built["test_scope"][
        "description"
    ]
    assert built["test_scope"]["command"] is None
    # Only the paths the diagnostic carries: no invented playbook path. The
    # temporary diagnostic lives outside the repository, so it stays absolute.
    assert built["evidence_paths"] == [
        str(tmp_path / "diagnostic_last.json"),
        "<HOME>\\Documents\\projects\\scrapers-pipeline\\logs\\upload-2026-10-03.log",
    ]


def test_unknown_multi_site_source_scope_keeps_only_the_portal(tmp_path):
    built = brief.build_brief(_unknown_target(tmp_path, kind="multi_site"))

    assert built["playbook"]["section"] == "6.7"
    assert built["test_scope"]["parameters"]["portals"] == ["nuevafuente"]
    assert "only the affected portal" in built["test_scope"]["constraints"]


def test_brief_is_json_serializable_round_trip():
    built = brief.build_brief(_primary("indeed"))

    text = brief.render_brief(built)
    parsed = json.loads(text)

    assert parsed == built
    # ensure_ascii=False keeps the Spanish diagnostic evidence readable.
    assert "resultado de la ejecución: correcto (ok)" in text


# --- Safety gate -------------------------------------------------------------


@pytest.mark.parametrize(
    "profile_path",
    [
        _PROFILE_PATH,
        r"C:\Users\example\AppData\Local\Chromium\User Data\Default",
        (
            r"C:\Users\example\AppData\Roaming\Mozilla\Firefox\Profiles"
            r"\abc.default-release"
        ),
        r"C:\Users\example\AppData\Local\Microsoft\Edge\User Data\Default",
        r"C:\Users\example\AppData\Local\Google\Chrome\Network\Cookies",
        r"C:\project\chrome_profile\Default\Login Data",
        "C:/Users/example/AppData/Local/Google/Chrome/User Data/Default",
    ],
)
def test_validate_brief_rejects_browser_profile_paths(profile_path):
    with pytest.raises(brief.BriefError) as excinfo:
        brief.validate_brief({"evidence_paths": [profile_path]})

    message = str(excinfo.value)
    assert "perfil de navegador" in message
    assert profile_path in message


@pytest.mark.parametrize(
    "temp_path",
    [
        r"C:\Users\example\AppData\Local\Temp\diagnostic_last.json",
        "C:/Users/example/AppData/Local/Temp/diagnostic_last.json",
        r"C:\Users\example\AppData\Local\Temp\pytest-of-user\diag.json",
    ],
)
def test_validate_brief_accepts_temp_diagnostic_paths(temp_path):
    # Temporary diagnostics are not browser profiles and must not be rejected
    # (T-16/T-17 use `--diagnostic` with temporary paths).
    brief.validate_brief({"evidence_paths": [temp_path]})


@pytest.mark.parametrize(
    "sensitive_key",
    ["password", "secret", "sas_token", "api_key", "credentials"],
)
def test_validate_brief_rejects_credential_keys(sensitive_key):
    with pytest.raises(brief.BriefError) as excinfo:
        brief.validate_brief({"nested": {sensitive_key: "value"}})

    assert "clave sensible" in str(excinfo.value)


def test_validate_brief_rejects_a_credential_key_nested_in_a_list():
    with pytest.raises(brief.BriefError):
        brief.validate_brief({"items": [{"password": "x"}]})


def test_build_brief_rejects_a_target_with_a_profile_path():
    unsafe = replace(_primary("infojobs"), evidence=(_PROFILE_PATH,))

    with pytest.raises(brief.BriefError):
        brief.build_brief(unsafe)


# --- Quality coherence -------------------------------------------------------


def test_brief_quality_matches_the_target_profile():
    target = _secondary("jobs_ch", "salary")

    built = brief.build_brief(target)

    expected = [
        {
            "field": entry.field,
            "required": entry.required,
            "current_pct": entry.current_pct,
            "target_pct": entry.target_pct,
            "is_focus": entry.is_focus,
        }
        for entry in target.quality
    ]
    assert built["quality"] == expected
    assert [entry["field"] for entry in built["quality"]] == list(
        field_contract.MEASURED_FIELDS
    )
