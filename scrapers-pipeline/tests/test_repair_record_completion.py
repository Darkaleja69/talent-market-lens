"""Tests for completing a repair record (T-14; RF-11).

Offline tests for ``repair.records.complete_record`` over a record opened by
T-13 from the real sanitized InfoJobs brief:

- the three final states (``probado``, ``descartado``, ``escalado``) update
  ``plan.md``, one index row and the history; only ``probado`` raises
  ``best_verified_offers`` (RF-10);
- ``quality_after.json`` gets the ``verdict_to_dict`` shape and the plan
  quality table shows before/after/delta/target/status, with the "antes" value
  coming from the stored ``quality_before.json``;
- the index gets one deterministic row per ``(fecha, fuente)``, sorted by date
  and source, without duplicating rows on a re-run, and fails clearly when the
  markers or the file are missing (without touching anything);
- validation: unknown status and ``probado`` without a valid verified count
  fail with a Spanish error, and a measurement of another source is rejected;
- a failed write restores the previous record, index and history.

The tests never touch the repository ``repairs/``: every call pins ``index_path``
and ``history_path`` under ``tmp_path``.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from repair import brief, quality, records, targets

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "diagnostic_2026-10-03.sanitized.json"
)

# Opening date used to pin the record content in tests.
_OPENED = date(2026, 10, 4)

# Before coverage used by the default brief: the gate profile of a primary
# failure has no coverage, so the tests pin two fields to exercise the
# before/after table with real deltas.
_BEFORE = {"description": 60.0, "salary": 20.0}

_MARKER_RE = re.compile(r"<!--\s*(/?)record:section:([a-z_]+)\s*-->")

_INDEX_TEMPLATE = """# Registros de reparaciones (prueba)

Índice de los registros.

<!-- repair-index:start -->
| fecha | fuente | estado | rama | resultado | calidad |
|---|---|---|---|---|---|
<!-- repair-index:end -->
"""

_INDEX_ROW_RE = re.compile(
    r"<!--\s*repair-index:start\s*-->(?P<body>.*?)"
    r"<!--\s*repair-index:end\s*-->",
    re.DOTALL,
)

_FIELDS = (
    "id",
    "title",
    "company",
    "description",
    "salary",
    "skills",
    "work_mode",
    "location",
    "posted_date",
)


# --- Builders -----------------------------------------------------------------


def _infojobs_brief(*, before: dict[str, float] | None = None) -> dict:
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        target
        for target in targets.select_targets(loaded)
        if target.source == "infojobs"
    )
    built = brief.build_brief(target)
    for entry in built["quality"]:
        if before and entry["field"] in before:
            entry["current_pct"] = before[entry["field"]]
    return built


def _nvb_brief() -> dict:
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        target
        for target in targets.select_secondary_targets(loaded)
        if target.source == "nvb" and target.field == "work_mode"
    )
    return brief.build_brief(target)


def _qrow(
    field: str,
    *,
    required: bool = False,
    before: float | None = None,
    after: float = 100.0,
    target: float | None = 100.0,
    status: str = quality.STATUS_PASS,
    reasons: tuple[str, ...] = (),
) -> quality.QualityRow:
    delta = None if before is None else after - before
    return quality.QualityRow(
        field=field,
        required=required,
        before_pct=before,
        after_pct=after,
        delta_pp=delta,
        target_pct=target,
        status=status,
        reasons=reasons,
    )


def _infojobs_rows(*, description_before: float | None = 60.0) -> tuple:
    """Build one row per canonical field; description/salary improved."""
    rows = {field: _qrow(field) for field in _FIELDS}
    rows["title"] = _qrow("title", required=True)
    rows["company"] = _qrow("company", required=True)
    rows["description"] = _qrow(
        "description", required=True, before=description_before
    )
    rows["salary"] = _qrow("salary", before=20.0)
    rows["skills"] = _qrow(
        "skills",
        before=None,
        after=0.0,
        target=None,
        status=quality.STATUS_NOT_APPLICABLE,
    )
    return tuple(rows[field] for field in _FIELDS)


def _quality_after(
    *,
    source: str = "infojobs",
    ok: bool = True,
    total_offers: int = 3,
    rows: tuple | None = None,
    blockers: tuple[str, ...] = (),
) -> dict:
    verdict = quality.QualityVerdict(
        source=source,
        ok=ok,
        total_offers=total_offers,
        rows=rows if rows is not None else _infojobs_rows(),
        blockers=blockers,
    )
    return quality.verdict_to_dict(verdict)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(_read(path))


def _created(tmp_path: Path, built: dict | None = None) -> records.RepairRecord:
    return records.create_record(
        built if built is not None else _infojobs_brief(before=_BEFORE),
        repairs_dir=tmp_path,
        on_date=_OPENED,
    )


def _index(tmp_path: Path) -> Path:
    path = tmp_path / "README.md"
    path.write_text(_INDEX_TEMPLATE, encoding="utf-8")
    return path


def _complete(record, tmp_path: Path, **overrides) -> records.RepairRecord:
    params = {
        "status": records.STATUS_TESTED,
        "result": "Fuente reparada: 3 ofertas con umbral 1",
        "changes": "- Se ajustó la sesión y el ritmo del scraper.",
        "tests": "- `python -m pytest tests -q` → OK.",
        "live_test": (
            "- **Alcance:** 1 keyword × 1 ciudad × 1 página.\n"
            "- **Recuento:** 3 ofertas."
        ),
        "verified_offers": 3,
        "quality_after": _quality_after(),
        "index_path": tmp_path / "README.md",
        "history_path": tmp_path / "history.json",
    }
    params.update(overrides)
    return records.complete_record(record, **params)


# --- Plan sections ------------------------------------------------------------


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
            assert current is None, section_id
            current = section_id
            buffer = []
        else:
            assert current == section_id, section_id
            sections[section_id] = "\n".join(buffer)
            current = None
    assert current is None, current
    return sections


def _index_rows(text: str) -> list[str]:
    match = _INDEX_ROW_RE.search(text)
    assert match is not None
    lines = match.group("body").strip().splitlines()
    return lines[2:]


# --- The three final states ---------------------------------------------------


def test_probado_updates_plan_quality_index_and_history(tmp_path):
    record = _created(tmp_path)
    index = _index(tmp_path)
    plan_before = _read(record.plan_path)

    _complete(record, tmp_path)

    sections = _sections(_read(record.plan_path))
    assert "Se ajustó la sesión y el ritmo del scraper." in sections["changes"]
    assert "pytest tests -q" in sections["tests"]
    assert "1 keyword × 1 ciudad × 1 página" in sections["live_test"]
    assert "**Recuento:** 3 ofertas" in sections["live_test"]
    assert (
        "| description | sí | 60.0 % | 100.0 % | +40.0 pp | 100.0 % | cumple |"
        in sections["quality"]
    )
    assert (
        "| salary | no | 20.0 % | 100.0 % | +80.0 pp | 100.0 % | cumple |"
        in sections["quality"]
    )
    assert "| skills | no | sin dato | 0.0 % | sin dato | sin meta | no aplica |" in (
        sections["quality"]
    )
    assert "Veredicto de `quality.py`: **OK**." in sections["quality"]
    assert "- **Resultado:** Fuente reparada: 3 ofertas con umbral 1" in (
        sections["result"]
    )
    assert "- **Ofertas verificadas:** 3" in sections["result"]
    assert "- **Estado:** probado" in sections["result"]
    assert "**Validación del push:** pendiente de la persona (RF-12)" in (
        sections["result"]
    )
    assert "{{" not in _read(record.plan_path)

    rows = _index_rows(_read(index))
    assert rows == [
        "| 2026-10-03 | infojobs | probado | repair/infojobs-20261003 "
        "| Fuente reparada: 3 ofertas con umbral 1 "
        "| OK: cumplen id, title, company, description, salary, work_mode, "
        "location, posted_date |"
    ]

    entry = _read_json(tmp_path / "history.json")["sources"]["infojobs"]
    assert entry["best_verified_offers"] == 3
    assert entry["last_result"] == "probado"
    assert entry["last_run_date"] == "2026-10-03"
    assert entry["quality_ok"] is True
    assert entry["quality_delta_pp"] == 40.0

    assert plan_before != _read(record.plan_path)


def test_probado_writes_quality_after_json_with_the_verdict_shape(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    payload = _quality_after()

    _complete(record, tmp_path, quality_after=payload)

    text = _read(record.quality_after_path)
    assert text == (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    parsed = _read_json(record.quality_after_path)
    assert parsed == payload
    assert parsed["source"] == "infojobs"
    assert parsed["ok"] is True
    assert parsed["total_offers"] == 3
    assert parsed["regression_tolerance_pp"] == quality.QUALITY_REGRESSION_TOLERANCE_PP


def test_quality_after_accepts_a_quality_verdict_object(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    verdict = quality.QualityVerdict(
        source="infojobs",
        ok=True,
        total_offers=3,
        rows=_infojobs_rows(),
        blockers=(),
    )

    _complete(record, tmp_path, quality_after=verdict)

    assert _read_json(record.quality_after_path) == quality.verdict_to_dict(
        verdict
    )


def test_quality_table_before_value_comes_from_the_stored_quality_before(
    tmp_path,
):
    built = _infojobs_brief(before={"description": 60.0})
    record = _created(tmp_path, built)
    _index(tmp_path)
    # The verdict row deliberately carries no before value: the record table
    # must still show the 60.0 % stored in quality_before.json.
    payload = _quality_after(
        rows=(_qrow("description", required=True, before=None),)
    )

    _complete(record, tmp_path, quality_after=payload)

    quality_section = _sections(_read(record.plan_path))["quality"]
    assert (
        "| description | sí | 60.0 % | 100.0 % | sin dato | 100.0 % | cumple |"
        in quality_section
    )


def test_a_not_ok_verdict_is_rendered_with_its_blockers(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    rows = tuple(
        _qrow(
            "description",
            required=True,
            before=60.0,
            after=80.0,
            status=quality.STATUS_FAIL,
            reasons=(quality.REASON_REQUIRED,),
        )
        if row.field == "description"
        else row
        for row in _infojobs_rows()
    )
    payload = _quality_after(
        ok=False,
        rows=rows,
        blockers=(quality.REASON_REQUIRED, quality.REASON_TARGET),
    )

    _complete(record, tmp_path, quality_after=payload)

    section = _sections(_read(record.plan_path))["quality"]
    assert (
        "| description | sí | 60.0 % | 80.0 % | +20.0 pp | 100.0 % | "
        "no cumple |" in section
    )
    assert (
        "Veredicto de `quality.py`: **NO OK** — bloqueos: "
        "`required_below_100`, `target_not_reached`." in section
    )
    assert "| salary | no | 20.0 % | 100.0 % | +80.0 pp | 100.0 % | cumple |" in section

    row = _index_rows(_read(tmp_path / "README.md"))[0]
    assert row.endswith("| NO OK: fallan description |")


def test_plan_sections_other_than_the_five_are_preserved(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    before = _sections(_read(record.plan_path))

    _complete(record, tmp_path)

    after = _sections(_read(record.plan_path))
    assert set(after) == set(before)
    for section_id in ("source", "failure", "evidence", "investigation"):
        assert after[section_id] == before[section_id], section_id


def test_descartado_records_the_outcome_and_keeps_the_threshold(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    "infojobs": {
                        "best_verified_offers": 7,
                        "note": "clave previa",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    _complete(
        record,
        tmp_path,
        status=records.STATUS_DISCARDED,
        result="Bloqueo persistente: se descarta la vía",
        verified_offers=None,
        quality_after=None,
    )

    sections = _sections(_read(record.plan_path))
    assert "- **Estado:** descartado" in sections["result"]
    assert "no aplica (reparación descartado)" in sections["result"]
    assert "Sin medición de calidad" in sections["quality"]
    assert "| description | sí |" in sections["quality"]
    assert "| pendiente |" in sections["quality"]
    assert not record.quality_after_path.exists()

    assert _index_rows(_read(tmp_path / "README.md")) == [
        "| 2026-10-03 | infojobs | descartado | repair/infojobs-20261003 "
        "| Bloqueo persistente: se descarta la vía | sin medición |"
    ]

    entry = _read_json(history_path)["sources"]["infojobs"]
    assert entry["best_verified_offers"] == 7
    assert entry["note"] == "clave previa"
    assert entry["last_result"] == "descartado"
    assert entry["last_run_date"] == "2026-10-03"
    assert entry["quality_ok"] is None
    assert entry["quality_delta_pp"] is None


def test_escalado_creates_the_history_without_raising_the_bar(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)

    _complete(
        record,
        tmp_path,
        status=records.STATUS_ESCALATED,
        result="Se presenta a la persona la opción de proxies",
        verified_offers=None,
        quality_after=None,
    )

    sections = _sections(_read(record.plan_path))
    assert "- **Estado:** escalado" in sections["result"]
    assert "no aplica (reparación escalado)" in sections["result"]

    entry = _read_json(tmp_path / "history.json")["sources"]["infojobs"]
    assert entry["best_verified_offers"] == 0
    assert entry["last_result"] == "escalado"

    assert _index_rows(_read(tmp_path / "README.md"))[0].startswith(
        "| 2026-10-03 | infojobs | escalado |"
    )


# --- Index --------------------------------------------------------------------


def test_index_orders_rows_by_date_and_source_without_duplicates(tmp_path):
    index = _index(tmp_path)
    infojobs = _created(tmp_path)
    nvb_brief = _nvb_brief()
    nvb_brief["run_date"] = "2026-10-01"
    nvb = records.create_record(nvb_brief, repairs_dir=tmp_path, on_date=_OPENED)

    _complete(infojobs, tmp_path)
    _complete(
        nvb,
        tmp_path,
        status=records.STATUS_DISCARDED,
        result="Portal sin cambios útiles",
        verified_offers=None,
        quality_after=None,
    )
    # Re-running the first record must update its row, not duplicate it.
    _complete(infojobs, tmp_path)

    rows = _index_rows(_read(index))
    assert len(rows) == 2
    assert rows[0].startswith("| 2026-10-01 | nvb | descartado |")
    assert rows[1].startswith("| 2026-10-03 | infojobs | probado |")
    assert "{{" not in _read(index)


def test_index_without_markers_raises_and_touches_nothing(tmp_path):
    record = _created(tmp_path)
    index = tmp_path / "README.md"
    index.write_text("# Índice sin marcadores\n", encoding="utf-8")
    plan_before = record.plan_path.read_bytes()

    with pytest.raises(records.RecordError) as excinfo:
        _complete(record, tmp_path)

    message = str(excinfo.value)
    assert "marcadores" in message
    assert "repair-index" in message
    assert record.plan_path.read_bytes() == plan_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()


def test_missing_index_raises_and_touches_nothing(tmp_path):
    record = _created(tmp_path)
    plan_before = record.plan_path.read_bytes()

    with pytest.raises(records.RecordError) as excinfo:
        _complete(record, tmp_path)

    assert "no se pudo leer" in str(excinfo.value)
    assert "README.md" in str(excinfo.value)
    assert record.plan_path.read_bytes() == plan_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()


def test_rerun_is_idempotent_and_keeps_the_row(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)

    _complete(record, tmp_path)
    first = {
        "plan": record.plan_path.read_bytes(),
        "quality": record.quality_after_path.read_bytes(),
        "index": (tmp_path / "README.md").read_bytes(),
        "history": (tmp_path / "history.json").read_bytes(),
    }

    _complete(record, tmp_path)

    assert record.plan_path.read_bytes() == first["plan"]
    assert record.quality_after_path.read_bytes() == first["quality"]
    assert (tmp_path / "README.md").read_bytes() == first["index"]
    assert (tmp_path / "history.json").read_bytes() == first["history"]
    assert len(_index_rows(_read(tmp_path / "README.md"))) == 1


def test_quality_after_of_the_record_is_reused_when_omitted(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    _complete(record, tmp_path)
    written = record.quality_after_path.read_bytes()

    _complete(record, tmp_path, quality_after=None)

    assert record.quality_after_path.read_bytes() == written
    section = _sections(_read(record.plan_path))["quality"]
    assert "Veredicto de `quality.py`: **OK**." in section
    assert (
        "| description | sí | 60.0 % | 100.0 % | +40.0 pp | 100.0 % | "
        "cumple |" in section
    )


# --- History ------------------------------------------------------------------


def test_history_is_created_from_scratch_for_a_proven_repair(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    assert not (tmp_path / "history.json").exists()

    _complete(record, tmp_path)

    history = _read_json(tmp_path / "history.json")
    assert history["schema_version"] == 1
    assert history["sources"]["infojobs"]["best_verified_offers"] == 3


def test_history_previous_best_never_decreases(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    (tmp_path / "history.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {"infojobs": {"best_verified_offers": 7}},
            }
        ),
        encoding="utf-8",
    )

    _complete(record, tmp_path, verified_offers=3)

    entry = _read_json(tmp_path / "history.json")["sources"]["infojobs"]
    assert entry["best_verified_offers"] == 7
    assert entry["last_result"] == "probado"


def test_history_unknown_keys_are_kept_across_states(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    (tmp_path / "history.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": {
                    "infojobs": {
                        "best_verified_offers": 2,
                        "custom_key": {"nested": [1, 2]},
                    },
                    "indeed": {"best_verified_offers": 9},
                },
            }
        ),
        encoding="utf-8",
    )

    _complete(record, tmp_path, status=records.STATUS_ESCALATED,
              verified_offers=None, quality_after=None)

    history = _read_json(tmp_path / "history.json")
    entry = history["sources"]["infojobs"]
    assert entry["custom_key"] == {"nested": [1, 2]}
    assert entry["best_verified_offers"] == 2
    assert history["sources"]["indeed"] == {"best_verified_offers": 9}


# --- Validation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "status", ["planificado", "PROBADO", "", None, 3], ids=repr
)
def test_unknown_final_status_raises_without_touching_anything(
    tmp_path, status
):
    record = _created(tmp_path)
    _index(tmp_path)
    plan_before = record.plan_path.read_bytes()
    index_before = (tmp_path / "README.md").read_bytes()

    with pytest.raises(records.RecordError) as excinfo:
        _complete(record, tmp_path, status=status)

    assert "estado final" in str(excinfo.value)
    assert record.plan_path.read_bytes() == plan_before
    assert (tmp_path / "README.md").read_bytes() == index_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()


@pytest.mark.parametrize(
    "offers", [None, 0, -1, True, "3", 1.5], ids=repr
)
def test_probado_requires_a_valid_verified_count(tmp_path, offers):
    record = _created(tmp_path)
    _index(tmp_path)
    plan_before = record.plan_path.read_bytes()

    with pytest.raises(records.RecordError) as excinfo:
        _complete(record, tmp_path, verified_offers=offers)

    assert "recuento verificado" in str(excinfo.value)
    assert record.plan_path.read_bytes() == plan_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()


def test_quality_of_another_source_is_rejected(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    plan_before = record.plan_path.read_bytes()

    with pytest.raises(records.RecordError) as excinfo:
        _complete(
            record, tmp_path, quality_after=_quality_after(source="linkedin")
        )

    assert "no se sobrescribe" in str(excinfo.value)
    assert "linkedin" in str(excinfo.value)
    assert record.plan_path.read_bytes() == plan_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()


def test_stored_quality_after_of_another_source_is_rejected(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)
    record.quality_after_path.write_text(
        json.dumps({"source": "linkedin", "ok": True, "fields": []}),
        encoding="utf-8",
    )

    with pytest.raises(records.RecordError):
        _complete(record, tmp_path, quality_after=None)

    parsed = _read_json(record.quality_after_path)
    assert parsed["source"] == "linkedin"


def test_empty_result_is_rejected(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)

    with pytest.raises(records.RecordError):
        _complete(record, tmp_path, result="   ")

    assert not (tmp_path / "history.json").exists()


def test_result_summary_is_a_single_line_in_the_index(tmp_path):
    record = _created(tmp_path)
    index = _index(tmp_path)

    _complete(record, tmp_path, result="Línea uno\nLínea dos")

    rows = _index_rows(_read(index))
    assert len(rows) == 1
    assert "| Línea uno Línea dos |" in rows[0]


def test_directory_input_is_accepted(tmp_path):
    record = _created(tmp_path)
    _index(tmp_path)

    returned = _complete(record.directory, tmp_path)

    assert returned.directory == record.directory
    assert returned.source == "infojobs"
    assert returned.date == date(2026, 10, 3)
    assert "- **Estado:** probado" in _sections(_read(record.plan_path))["result"]


# --- Robustness ---------------------------------------------------------------


def test_failed_write_restores_the_previous_files(tmp_path, monkeypatch):
    record = _created(tmp_path)
    index = _index(tmp_path)
    plan_before = record.plan_path.read_bytes()
    index_before = index.read_bytes()
    real_write = records._write_text

    def failing(path, text):
        if Path(path).name == "README.md":
            raise OSError("fallo simulado de escritura")
        return real_write(path, text)

    monkeypatch.setattr(records, "_write_text", failing)

    with pytest.raises(records.RecordError) as excinfo:
        _complete(record, tmp_path)

    assert "no se pudo completar" in str(excinfo.value)
    assert record.plan_path.read_bytes() == plan_before
    assert index.read_bytes() == index_before
    assert not record.quality_after_path.exists()
    assert not (tmp_path / "history.json").exists()
