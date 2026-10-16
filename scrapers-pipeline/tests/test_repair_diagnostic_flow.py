"""Offline integration test of the repair flow on the real diagnostic (T-07).

End-to-end walkthrough of the deterministic core with the sanitized
2026-10-03 fixture: load and validate the diagnostic, select the prioritized
queue, check the quality profiles, build and validate every brief, render the
Spanish presentation and repeat the flow for an unknown source added in
memory (generic playbook §6.7). No network is used: the full walkthrough runs
with ``socket.socket`` and ``socket.create_connection`` patched to fail, so an
accidental connection would break the test.
"""
from __future__ import annotations

import copy
import json
import re
import socket
from datetime import date
from pathlib import Path

from repair import brief, presentation, targets
from verification import field_contract, sources

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REAL_FIXTURE = _FIXTURES_DIR / "diagnostic_2026-10-03.sanitized.json"

_PRIMARY_ORDER = ["indeed", "linkedin", "infojobs", "irishjobs", "glassdoor"]
_SECONDARY_ORDER = [
    "indeed",
    "indeed",
    "indeed",
    "indeed",
    "linkedin",
    "linkedin",
    "linkedin",
    "stepstone_nl",
    "stepstone_nl",
    "stepstone_nl",
    "nvb",
    "jobs_ch",
    "jobs_ch",
    "jobs_ch",
]
_PLAYBOOK_SECTIONS = {
    "infojobs": "6.1",
    "indeed": "6.2",
    "linkedin": "6.3",
    "irishjobs": "6.4",
    "glassdoor": "6.5",
    "stepstone_nl": "6.6",
    "nvb": "6.6",
    "jobs_ch": "6.6",
}


def _fixture_payload() -> dict:
    return json.loads(_REAL_FIXTURE.read_text(encoding="utf-8"))


def _loaded_fixture():
    return targets.load_diagnostic(_REAL_FIXTURE)


def _queue():
    return targets.select_all_targets(_loaded_fixture())


def _walk_the_whole_flow():
    """Run the deterministic chain once; used by the no-network test."""
    queue = _queue()
    assert len(queue) == 19
    for target in queue:
        built = brief.build_brief(target)
        json.dumps(built, ensure_ascii=False)
    text = presentation.describe_targets(queue)
    assert "Total: 19 objetivos (5 primarios, 14 secundarios)" in text
    return queue, text


# --- Fixture hygiene ---------------------------------------------------------


def test_fixture_is_sanitized():
    text = _REAL_FIXTURE.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "<HOME>" in text
    assert "aleja" not in lowered
    assert re.search(r"[A-Za-z]:[\\/]{1,2}Users", text) is None
    for token in ("appdata", "password", "secret", "token", "sas_token"):
        assert token not in lowered


# --- Selection, prioritization and quality profile ---------------------------


def test_queue_has_five_primaries_then_fourteen_secondaries():
    queue = _queue()

    primaries = queue[:5]
    secondaries = queue[5:]
    assert [target.source for target in primaries] == _PRIMARY_ORDER
    assert all(target.role == targets.ROLE_PRIMARY for target in primaries)
    assert [target.source for target in secondaries] == _SECONDARY_ORDER
    assert all(target.role == targets.ROLE_SECONDARY for target in secondaries)
    assert all(target.run_date == date(2026, 10, 3) for target in queue)


def test_every_target_quality_profile_is_canonical_and_coherent():
    for target in _queue():
        assert [entry.field for entry in target.quality] == list(
            field_contract.MEASURED_FIELDS
        )
        for entry in target.quality:
            published = bool(
                sources.field_aliases(target.source, entry.field)
            )
            if entry.required or published:
                assert entry.target_pct == 100.0
            else:
                assert entry.target_pct is None


# --- Brief and presentation --------------------------------------------------


def test_every_target_builds_a_safe_serializable_brief():
    for target in _queue():
        built = brief.build_brief(target)
        text = json.dumps(built, ensure_ascii=False)

        assert json.loads(text) == built
        assert built["source"] == target.source
        assert built["role"] == target.role
        assert built["playbook"]["section"] == _PLAYBOOK_SECTIONS[target.source]
        assert built["quality"] == [
            {
                "field": entry.field,
                "required": entry.required,
                "current_pct": entry.current_pct,
                "target_pct": entry.target_pct,
                "is_focus": entry.is_focus,
            }
            for entry in target.quality
        ]
        assert "aleja" not in text
        assert "AppData" not in text
        brief.validate_brief(built)


def test_presentation_lists_the_whole_queue_in_spanish():
    text = presentation.describe_targets(_queue())

    assert "Objetivos de reparación — run de origen: 2026-10-03" in text
    assert "Total: 19 objetivos (5 primarios, 14 secundarios)" in text
    for source in _PRIMARY_ORDER + ["stepstone_nl", "nvb", "jobs_ch"]:
        assert source in text
    assert "19. jobs_ch" in text
    for token in (
        "primary",
        "secondary",
        "failed",
        "blocked",
        "description",
        "work_mode",
        "§6.1",
        "§6.6",
    ):
        assert token in text


# --- Unknown source, generic flow --------------------------------------------


def test_unknown_source_follows_the_generic_flow(tmp_path):
    payload = copy.deepcopy(_fixture_payload())
    payload["sources"].append(
        {
            "id": "nuevafuente",
            "kind": "direct",
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

    queue = targets.select_all_targets(targets.load_diagnostic(path))
    unknown = next(
        target for target in queue if target.source == "nuevafuente"
    )

    assert len(queue) == 20
    assert unknown.role == targets.ROLE_PRIMARY
    assert unknown.evidence_paths == (str(path), payload["run"]["log_path"])
    built = brief.build_brief(unknown)
    assert built["playbook"]["section"] == "6.7"
    assert built["test_scope"]["command"] is None
    text = presentation.describe_targets((unknown,))
    assert "nuevafuente" in text
    assert "Playbook: §6.7 genérico para fuentes nuevas o fallos futuros" in text


# --- No network --------------------------------------------------------------


def test_whole_flow_needs_no_network(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("network access is not allowed in offline tests")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    queue, text = _walk_the_whole_flow()

    assert len(queue) == 19
    assert "indeed" in text
