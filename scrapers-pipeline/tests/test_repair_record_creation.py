"""Tests for opening a repair record from its brief (T-13; RF-2, RF-11).

Offline tests for ``repair.records.create_record`` over the real sanitized
diagnostic of 2026-10-03:

- creation of ``repairs/<YYYYMMDD>-<source>/`` from a real brief (InfoJobs
  primary and NVB secondary): layout, plan instantiation without leftover
  placeholders, state ``planificado``, filled failure/evidence/investigation/
  quality sections and pending changes/tests/live test;
- ``context.json`` is the brief verbatim and ``quality_before.json`` has the
  stable English shape of the sanitized example; ``evidence/`` is empty;
- collision: an existing record (or an existing directory) raises
  ``RecordError`` and the existing content is untouched;
- dates: a brief without a usable run date fails clearly, ``on_date`` opens
  the record and sets the creation date, invalid dates fail;
- determinism: the same input produces byte-identical files;
- a failure after claiming the directory removes the half-written record.

The record template, the index and the example keep their own tests in
``test_repair_records.py``; no structure is duplicated here.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from repair import brief, records, targets

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "diagnostic_2026-10-03.sanitized.json"
)

# Opening date used to pin the record content in tests (the wall clock never
# takes part in the assertions).
_OPENED = date(2026, 10, 4)

_MARKER_RE = re.compile(r"<!--\s*(/?)record:section:([a-z_]+)\s*-->")
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
_QUALITY_BEFORE_KEYS = {
    "schema_version",
    "source",
    "run_date",
    "offers_current_run",
    "offers_snapshot",
    "completeness_scope",
    "fields",
}
_QUALITY_FIELD_KEYS = {
    "field",
    "required",
    "current_pct",
    "target_pct",
    "is_focus",
}


def _infojobs_brief() -> dict:
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        target
        for target in targets.select_targets(loaded)
        if target.source == "infojobs"
    )
    return brief.build_brief(target)


def _nvb_work_mode_brief() -> dict:
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        target
        for target in targets.select_secondary_targets(loaded)
        if target.source == "nvb" and target.field == "work_mode"
    )
    return brief.build_brief(target)


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


def _created(tmp_path: Path, built: dict | None = None) -> records.RepairRecord:
    return records.create_record(
        built if built is not None else _infojobs_brief(),
        repairs_dir=tmp_path,
        on_date=_OPENED,
    )


# --- Defaults and layout ------------------------------------------------------


def test_default_paths_are_anchored_to_the_repository():
    package_dir = Path(records.__file__).resolve().parent

    assert records.DEFAULT_REPAIRS_DIR == (
        Path(records.__file__).resolve().parents[2] / "repairs"
    )
    assert records.DEFAULT_TEMPLATE_PATH == package_dir / "record_template.md"


def test_create_record_from_the_real_brief_creates_the_expected_layout(tmp_path):
    record = _created(tmp_path)

    directory = tmp_path / "20261003-infojobs"
    assert record.directory == directory
    assert record.plan_path == directory / "plan.md"
    assert record.context_path == directory / "context.json"
    assert record.quality_before_path == directory / "quality_before.json"
    assert record.evidence_dir == directory / "evidence"
    assert record.source == "infojobs"
    assert record.date == date(2026, 10, 3)
    assert record.branch == "repair/infojobs-20261003"
    for path in (
        record.plan_path,
        record.context_path,
        record.quality_before_path,
    ):
        assert path.is_file(), path
    assert record.evidence_dir.is_dir()


def test_evidence_directory_is_created_empty(tmp_path):
    record = _created(tmp_path)

    assert record.evidence_dir.is_dir()
    assert list(record.evidence_dir.iterdir()) == []


# --- Plan instantiation -------------------------------------------------------


def test_created_plan_keeps_every_section_marker_in_order(tmp_path):
    text = _read(_created(tmp_path).plan_path)

    assert _markers(text) == _expected_markers()


def test_created_plan_has_no_leftover_placeholders_and_is_planificado(tmp_path):
    text = _read(_created(tmp_path).plan_path)

    assert "{{" not in text
    assert "}}" not in text
    assert "# Reparación infojobs — run 2026-10-03" in text
    assert "**Rama del fix:** repair/infojobs-20261003" in text
    assert "**Estado:** planificado" in text


def test_created_plan_fills_source_failure_evidence_and_investigation(tmp_path):
    sections = _sections(_read(_created(tmp_path).plan_path))

    assert "**Fuente:** infojobs" in sections["source"]
    assert "**Run de origen:** 2026-10-03" in sections["source"]
    assert "**Registro creado:** 2026-10-04" in sections["source"]
    assert "## Fallo observado" in sections["failure"]
    assert "blocked" in sections["failure"]
    assert "sin evidencia suficiente para confirmar la fuente" in sections[
        "failure"
    ]
    assert "**Ofertas del run:** 0" in sections["failure"]
    assert "**Ofertas del snapshot:** no aplica" in sections["failure"]
    assert "## Evidencia" in sections["evidence"]
    assert "captcha block detected" in sections["evidence"]
    assert "infojobs_jobs_scraper/data/run_nightly.log" in sections["evidence"]
    assert "## Plan de investigación" in sections["investigation"]
    assert "§6.1" in sections["investigation"]
    assert "InfoJobs - CAPTCHA block" in sections["investigation"]
    assert "1 keyword x 1 city x 1 page" in sections["investigation"]
    assert "no merge" in sections["investigation"]


def test_created_plan_leaves_changes_tests_and_live_test_pending(tmp_path):
    sections = _sections(_read(_created(tmp_path).plan_path))

    for section_id in ("changes", "tests", "live_test"):
        assert "Pendiente" in sections[section_id], section_id
    assert "**Resultado:** (pendiente)" in sections["result"]
    assert "planificado" in sections["result"]


def test_created_plan_fills_the_quality_table_before_after(tmp_path):
    built = _infojobs_brief()
    quality = _sections(_read(_created(tmp_path, built).plan_path))["quality"]

    assert "| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |" in quality
    for entry in built["quality"]:
        assert f"| {entry['field']} |" in quality, entry["field"]
    assert "| description | sí |" in quality
    assert (
        "| skills | no | sin dato | pendiente | pendiente | sin meta | pendiente |"
        in quality
    )
    assert "Pendiente" in quality


def test_created_plan_of_a_secondary_target_describes_the_field(tmp_path):
    sections = _sections(
        _read(_created(tmp_path, _nvb_work_mode_brief()).plan_path)
    )

    assert "secondary" in sections["failure"]
    assert "optional_field_at_or_below_threshold" in sections["failure"]
    assert "work_mode" in sections["failure"]
    assert "0.0 %" in sections["failure"]
    assert "§6.6" in sections["investigation"]


def test_created_plan_is_sanitized(tmp_path):
    text = _read(_created(tmp_path).plan_path)

    brief.validate_brief({"plan": text})
    assert "aleja" not in text.lower()
    assert re.search(r"(?i)[a-z]:[\\/]users[\\/]", text) is None
    assert "password" not in text.lower()


# --- Machine files ------------------------------------------------------------


def test_context_json_is_the_brief_verbatim(tmp_path):
    built = _infojobs_brief()
    parsed = json.loads(_read(_created(tmp_path, built).context_path))

    assert parsed == built
    brief.validate_brief(parsed)


def test_quality_before_json_has_the_expected_shape(tmp_path):
    built = _infojobs_brief()
    before = json.loads(_read(_created(tmp_path, built).quality_before_path))

    assert set(before) == _QUALITY_BEFORE_KEYS
    assert before["schema_version"] == records.QUALITY_BEFORE_SCHEMA_VERSION
    assert before["source"] == "infojobs"
    assert before["run_date"] == "2026-10-03"
    assert before["offers_current_run"] == 0
    assert before["offers_snapshot"] is None
    assert before["completeness_scope"] == "run"
    expected_fields = [
        {
            "field": entry["field"],
            "required": entry["required"],
            "current_pct": entry["current_pct"],
            "target_pct": entry["target_pct"],
            "is_focus": entry["is_focus"],
        }
        for entry in built["quality"]
    ]
    assert before["fields"] == expected_fields
    for entry in before["fields"]:
        assert set(entry) == _QUALITY_FIELD_KEYS
    assert [entry["field"] for entry in before["fields"]][:4] == [
        "id",
        "title",
        "company",
        "description",
    ]


def test_brief_without_quality_gets_an_empty_profile(tmp_path):
    built = dict(_infojobs_brief())
    del built["quality"]
    record = _created(tmp_path, built)

    before = json.loads(_read(record.quality_before_path))
    assert before["fields"] == []
    assert "_(pendiente)_" in _sections(_read(record.plan_path))["quality"]


# --- Collision ----------------------------------------------------------------


def test_collision_raises_and_preserves_the_existing_record(tmp_path):
    built = _infojobs_brief()
    record = _created(tmp_path, built)
    sentinel = "# CENTINELA: contenido existente\n"
    record.plan_path.write_text(sentinel, encoding="utf-8")
    before = {
        path.name: path.read_bytes()
        for path in record.directory.iterdir()
        if path.is_file()
    }

    with pytest.raises(records.RecordError) as excinfo:
        records.create_record(
            built, repairs_dir=tmp_path, on_date=_OPENED
        )

    assert "ya existe" in str(excinfo.value)
    assert "20261003-infojobs" in str(excinfo.value)
    assert _read(record.plan_path) == sentinel
    after = {
        path.name: path.read_bytes()
        for path in record.directory.iterdir()
        if path.is_file()
    }
    assert after == before


def test_collision_with_an_existing_directory_does_not_write_inside(tmp_path):
    directory = tmp_path / "20261003-infojobs"
    directory.mkdir()

    with pytest.raises(records.RecordError):
        records.create_record(
            _infojobs_brief(), repairs_dir=tmp_path, on_date=_OPENED
        )

    assert list(directory.iterdir()) == []


# --- Dates --------------------------------------------------------------------


def test_missing_run_date_without_on_date_raises(tmp_path):
    built = dict(_infojobs_brief())
    built["run_date"] = None

    with pytest.raises(records.RecordError) as excinfo:
        records.create_record(built, repairs_dir=tmp_path)

    message = str(excinfo.value)
    assert "run_date" in message
    assert "fecha" in message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value",
    ["03/10/2026", "2026-13-01", "20261003", 20261003, ""],
    ids=repr,
)
def test_unusable_run_date_raises(tmp_path, value):
    built = dict(_infojobs_brief())
    built["run_date"] = value

    with pytest.raises(records.RecordError):
        records.create_record(built, repairs_dir=tmp_path, on_date=_OPENED)

    assert list(tmp_path.iterdir()) == []


def test_on_date_opens_a_record_without_a_usable_run_date(tmp_path):
    built = dict(_infojobs_brief())
    built["run_date"] = None

    record = _created(tmp_path, built)

    assert record.directory.name == "20261004-infojobs"
    assert record.date == date(2026, 10, 4)
    text = _read(record.plan_path)
    assert "run 2026-10-04" in text
    assert "**Registro creado:** 2026-10-04" in text
    assert json.loads(_read(record.context_path))["run_date"] is None


def test_on_date_sets_the_creation_date_and_accepts_iso_text(tmp_path):
    record = records.create_record(
        _infojobs_brief(), repairs_dir=tmp_path, on_date="2026-10-09"
    )

    # A usable run date always names the record; on_date only sets the
    # opening date (the folder convention is `<run YYYYMMDD>-<source>`).
    assert record.directory.name == "20261003-infojobs"
    assert "**Registro creado:** 2026-10-09" in _read(record.plan_path)


@pytest.mark.parametrize("value", ["09/10/2026", "2026-02-30", 5], ids=repr)
def test_invalid_on_date_raises(tmp_path, value):
    with pytest.raises(records.RecordError) as excinfo:
        records.create_record(
            _infojobs_brief(), repairs_dir=tmp_path, on_date=value
        )

    assert "on_date" in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []


# --- Robustness ---------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [None, "", "../evil", "a/b", "info jobs", "infojobs\n"],
    ids=repr,
)
def test_source_must_be_a_safe_id(tmp_path, source):
    built = dict(_infojobs_brief())
    built["source"] = source

    with pytest.raises(records.RecordError):
        records.create_record(built, repairs_dir=tmp_path, on_date=_OPENED)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "quality",
    ["no", [{"required": True}], [None]],
    ids=repr,
)
def test_malformed_quality_profile_raises(tmp_path, quality):
    built = dict(_infojobs_brief())
    built["quality"] = quality

    with pytest.raises(records.RecordError):
        records.create_record(built, repairs_dir=tmp_path, on_date=_OPENED)

    assert list(tmp_path.iterdir()) == []


def test_unsafe_brief_is_rejected_without_leftovers(tmp_path):
    built = dict(_infojobs_brief())
    built["evidence"] = [
        r"C:\Users\example\AppData\Local\Google\Chrome\User Data\Default\Cookies"
    ]

    with pytest.raises(records.RecordError) as excinfo:
        records.create_record(built, repairs_dir=tmp_path, on_date=_OPENED)

    assert "perfil de navegador" in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []


def test_create_is_deterministic(tmp_path):
    built = _infojobs_brief()

    first = records.create_record(
        built, repairs_dir=tmp_path / "a", on_date=_OPENED
    )
    second = records.create_record(
        built, repairs_dir=tmp_path / "b", on_date=_OPENED
    )

    assert first.directory.name == second.directory.name
    for name in ("plan.md", "context.json", "quality_before.json"):
        assert (first.directory / name).read_bytes() == (
            second.directory / name
        ).read_bytes(), name


def test_failure_after_claiming_the_directory_removes_the_record(
    tmp_path, monkeypatch
):
    def boom(path, text):
        raise OSError("fallo simulado de escritura")

    monkeypatch.setattr(records, "_write_text", boom)

    with pytest.raises(records.RecordError) as excinfo:
        records.create_record(
            _infojobs_brief(), repairs_dir=tmp_path, on_date=_OPENED
        )

    assert "no se pudo crear" in str(excinfo.value)
    assert list(tmp_path.iterdir()) == []
