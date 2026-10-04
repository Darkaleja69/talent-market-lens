"""Tests for the repair record template, index and sanitized example (T-12).

Offline tests over the record assets of the repair process (RF-2, RF-11):

- ``repair/record_template.md`` has every required section and the stable HTML
  markers that T-14 uses to update a section without losing what is already
  written;
- ``repairs/README.md`` documents the folder, the record structure, the branch
  convention and the human push validation (RF-12), and holds the index table
  with its exact columns between stable markers;
- ``repairs/example/`` is an illustrative, sanitized InfoJobs record: no
  credentials, tokens, user paths or browser profiles.

The tests only check structure and sanitization: no business rule is invented
here. The example directory is deliberately not named like a real record
(``<YYYYMMDD>-<fuente>``), so it cannot collide with T-13.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from repair import brief

_PIPELINE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PIPELINE_DIR.parent
_TEMPLATE_PATH = _PIPELINE_DIR / "repair" / "record_template.md"
_README_PATH = _REPO_ROOT / "repairs" / "README.md"
_EXAMPLE_DIR = _REPO_ROOT / "repairs" / "example"
_EXAMPLE_PLAN = _EXAMPLE_DIR / "plan.md"
_EXAMPLE_JSONS = ("context.json", "quality_before.json", "quality_after.json")

# Section ids of the record template, in the update order of T-14.
_SECTION_IDS = (
    "source",
    "failure",
    "evidence",
    "investigation",
    "changes",
    "tests",
    "live_test",
    "quality",
    "result",
)

# Stable HTML markers: an opening `record:section:<id>` comment and a closing
# `/record:section:<id>` one. Both live on their own line.
_MARKER_RE = re.compile(r"<!--\s*(/?)record:section:([a-z_]+)\s*-->")

_INDEX_START = "<!-- repair-index:start -->"
_INDEX_END = "<!-- repair-index:end -->"
_INDEX_HEADER = "| fecha | fuente | estado | rama | resultado | calidad |"
_INDEX_SEPARATOR = "|---|---|---|---|---|---|"

_RECORD_DIR_RE = re.compile(r"\d{8}-[a-z0-9_]+")

# Spanish labels and content hints each template section must carry.
_SECTION_HINTS: dict[str, tuple[str, ...]] = {
    "source": ("Fuente y run", "{{source}}", "{{run_date}}", "{{branch}}"),
    "failure": ("Fallo observado",),
    "evidence": ("Evidencia", "Diagnóstico", "Rutas locales", "Extractos"),
    "investigation": ("Plan de investigación", "playbook", "robots.txt"),
    "changes": ("Cambios realizados", "dependencias"),
    "tests": ("Pruebas: tests", "suites"),
    "live_test": (
        "Pruebas: prueba en vivo",
        "alcance",
        "recuento",
        "umbral",
    ),
    "quality": (
        "Calidad",
        "Antes",
        "Después",
        "Meta",
        "quality_before.json",
        "quality_after.json",
    ),
    "result": (
        "Resultado y estado",
        "planificado",
        "probado",
        "descartado",
        "escalado",
    ),
}

_QUALITY_AFTER_FIELD_KEYS = {
    "field",
    "required",
    "before_pct",
    "after_pct",
    "delta_pp",
    "target_pct",
    "status",
    "reasons",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _markers(text: str) -> list[tuple[bool, str]]:
    """Return ``(is_closing, section_id)`` of every marker, in file order."""
    return [
        (match.group(1) == "/", match.group(2))
        for match in _MARKER_RE.finditer(text)
    ]


def _sections(text: str) -> dict[str, str]:
    """Return section id -> text between its opening and closing markers."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = _MARKER_RE.fullmatch(line.strip())
        if match is None:
            if current is not None:
                buffer.append(line)
            continue
        is_closing = match.group(1) == "/"
        section_id = match.group(2)
        if not is_closing:
            assert current is None, f"marcador de apertura duplicado: {section_id}"
            current = section_id
            buffer = []
        else:
            assert current == section_id, (
                f"cierre «{section_id}» sin apertura correspondiente"
            )
            sections[section_id] = "\n".join(buffer)
            current = None
    assert current is None, f"sección sin cierre: {current}"
    return sections


def _expected_markers() -> list[tuple[bool, str]]:
    expected: list[tuple[bool, str]] = []
    for section_id in _SECTION_IDS:
        expected.append((False, section_id))
        expected.append((True, section_id))
    return expected


def _assert_sanitized(text: str) -> None:
    """Reject browser profiles, real user paths, user names and secrets.

    ``brief.validate_brief`` is applied to the whole text as a value, so the
    profile-path patterns stay in one place; the other checks cover what the
    brief gate intentionally allows (``<HOME>``) or does not scan (values).
    """
    brief.validate_brief({"text": text})
    assert "aleja" not in text.lower()
    assert re.search(r"(?i)[a-z]:[\\/]users[\\/]", text) is None
    assert (
        re.search(r"(?i)sharedaccesssignature|accountkey=|sig=[a-z0-9%]", text)
        is None
    )
    assert "password" not in text.lower()


# --- Record template ----------------------------------------------------------


def test_record_template_exists():
    assert _TEMPLATE_PATH.is_file()


def test_record_template_has_every_section_marker_once_in_order():
    assert _markers(_read(_TEMPLATE_PATH)) == _expected_markers()


@pytest.mark.parametrize(
    ("section_id", "hints"),
    sorted(_SECTION_HINTS.items()),
)
def test_record_template_section_has_its_label_and_hints(section_id, hints):
    content = _sections(_read(_TEMPLATE_PATH))[section_id]

    assert content.strip()
    for hint in hints:
        assert hint in content, (section_id, hint)


def test_record_template_documents_the_update_mechanism():
    text = _read(_TEMPLATE_PATH)

    assert "complete_record" in text
    assert "/record:section:<id>" in text
    assert "conserva" in text  # the rest of the file is preserved
    assert "{{status}}" in text


def test_record_template_is_sanitized():
    _assert_sanitized(_read(_TEMPLATE_PATH))


# --- Index (repairs/README.md) ------------------------------------------------


def test_index_exists_and_documents_the_folder_and_record_structure():
    text = _read(_README_PATH)

    for name in (
        "plan.md",
        "context.json",
        "evidence/",
        "quality_before.json",
        "quality_after.json",
        "history.json",
    ):
        assert name in text
    assert "repair/<fuente>-<YYYYMMDD>" in text
    assert "repairs/example/" in text
    assert "RF-14" in text


def test_index_documents_the_human_push_validation():
    text = _read(_README_PATH)

    assert "RF-12" in text
    assert "validación" in text
    assert "push" in text


def test_index_has_the_exact_columns_between_the_stable_markers():
    text = _read(_README_PATH)
    start = text.index(_INDEX_START)
    end = text.index(_INDEX_END)

    assert start < end
    inside = text[start + len(_INDEX_START) : end].strip().splitlines()
    assert inside[0] == _INDEX_HEADER
    assert inside[1] == _INDEX_SEPARATOR
    for row in inside[2:]:
        assert row.startswith("|") and row.endswith("|")
        assert len(row.split("|")) == 8  # six columns


def test_index_documents_the_row_convention_and_states():
    text = _read(_README_PATH)

    for token in ("`fecha`", "`fuente`", "`estado`", "`rama`", "`resultado`", "`calidad`"):
        assert token in text
    for state in ("planificado", "probado", "descartado", "escalado"):
        assert state in text
    assert "quality_after.json" in text


def test_index_is_sanitized():
    _assert_sanitized(_read(_README_PATH))


# --- Sanitized example --------------------------------------------------------


def test_example_files_exist_and_parse_as_json_objects():
    assert _EXAMPLE_PLAN.is_file()
    for name in _EXAMPLE_JSONS:
        path = _EXAMPLE_DIR / name
        assert path.is_file(), name
        assert isinstance(json.loads(_read(path)), dict), name


def test_example_plan_fills_every_section():
    text = _read(_EXAMPLE_PLAN)

    assert _markers(text) == _expected_markers()
    sections = _sections(text)
    assert set(sections) == set(_SECTION_IDS)
    for section_id, content in sections.items():
        assert content.strip(), section_id
        assert "{{" not in content, section_id


def test_example_plan_is_marked_as_illustrative():
    text = _read(_EXAMPLE_PLAN).lower()

    assert "ejemplo" in text
    assert "no un registro real" in text


def test_example_plan_covers_the_infojobs_case_data():
    text = _read(_EXAMPLE_PLAN)

    for token in (
        "infojobs",
        "2026-10-03",
        "repair/infojobs-20261003",
        "blocked",
        "captcha block detected",
        "3 ofertas",
        "umbral",
    ):
        assert token in text, token


def test_example_context_json_is_the_infojobs_brief():
    parsed = json.loads(_read(_EXAMPLE_DIR / "context.json"))

    assert parsed["source"] == "infojobs"
    assert parsed["run_date"] == "2026-10-03"
    assert parsed["reason"]["outcome"] == "blocked"
    assert parsed["playbook"]["section"] == "6.1"
    assert parsed["test_scope"]["parameters"] == {
        "keywords": 1,
        "cities": 1,
        "pages": 1,
    }


def test_example_quality_json_files_match_the_gate_shape():
    before = json.loads(_read(_EXAMPLE_DIR / "quality_before.json"))
    after = json.loads(_read(_EXAMPLE_DIR / "quality_after.json"))

    assert before["source"] == "infojobs"
    assert before["run_date"] == "2026-10-03"
    assert before["fields"]
    assert after["source"] == "infojobs"
    assert after["ok"] is True
    assert after["total_offers"] == 3
    assert after["blockers"] == []
    for entry in after["fields"]:
        assert set(entry) == _QUALITY_AFTER_FIELD_KEYS


def test_example_json_files_pass_the_brief_safety_gate():
    for name in _EXAMPLE_JSONS:
        brief.validate_brief(json.loads(_read(_EXAMPLE_DIR / name)))


@pytest.mark.parametrize(
    "path",
    [_TEMPLATE_PATH, _README_PATH, _EXAMPLE_PLAN]
    + [_EXAMPLE_DIR / name for name in _EXAMPLE_JSONS],
    ids=lambda path: path.name,
)
def test_record_assets_are_sanitized(path):
    _assert_sanitized(_read(path))


def test_example_directory_cannot_collide_with_a_real_record():
    assert _EXAMPLE_DIR.name == "example"
    assert _RECORD_DIR_RE.fullmatch(_EXAMPLE_DIR.name) is None
