"""Offline tests of the thin repair CLI (T-17; RF-13).

End-to-end tests of ``repair.cli`` invoked in process with ``cli.main([...])``
(which returns the exit code) and captured with ``capsys``: the five
subcommands, the default and the explicit diagnostic paths, the Spanish
messages and the exit codes of success, domain errors and argparse usage
errors.

Nothing under the repository ``repairs/`` is touched: the defaults of
``records``/``threshold`` are patched to ``tmp_path`` (or explicit paths are
passed), every fixture is written under ``tmp_path`` and no test uses the
network.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from repair import brief, cli, quality, records, targets, threshold

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "diagnostic_2026-10-03.sanitized.json"
)

# Failed sources of the sanitized fixture, in diagnostic order (RF-1).
_PRIMARY_ORDER = ("indeed", "linkedin", "infojobs", "irishjobs", "glassdoor")

# One numbered target block starts with "N. source — ...".
_NUMBERED_SOURCE_RE = re.compile(
    r"^\d+\.\s+([A-Za-z0-9_]+)\s+—", re.MULTILINE
)

_INDEX_TEMPLATE = """# Registros de reparaciones (prueba)

Índice de los registros.

<!-- repair-index:start -->
| fecha | fuente | estado | rama | resultado | calidad |
|---|---|---|---|---|---|
<!-- repair-index:end -->
"""


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI in process and return its code plus stdout and stderr."""
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _fixture_arguments() -> list[str]:
    """Return the explicit ``--diagnostic`` argument of the real fixture."""
    return ["--diagnostic", str(_FIXTURE)]


def _brief(source: str, field: str | None = None) -> dict:
    """Build the brief of one target of the sanitized fixture."""
    loaded = targets.load_diagnostic(_FIXTURE)
    target = next(
        target
        for target in targets.select_all_targets(loaded)
        if target.source == source
        and (field is None or target.field == field)
    )
    return brief.build_brief(target)


def _write_live_parquet(
    path: Path,
    *,
    total: int,
    valid_counts: dict[str, int] | None = None,
) -> None:
    """Write a Multi-site Parquet with the given per-field valid counts.

    The first ``valid_counts[field]`` offers carry a valid value and the rest
    ``None``; a field absent from the map is valid in every offer.
    """
    counts = valid_counts or {}

    def values(field: str, valid_value) -> list:
        count = counts.get(field, total)
        return [valid_value] * count + [None] * (total - count)

    columns = {
        "job_id": [f"job-{index}" for index in range(total)],
        "job_url": [f"https://example.com/{index}" for index in range(total)],
        "title": values("title", "Data Engineer"),
        "company_name": values("company", "ACME"),
        "scraped_at": ["2026-10-03"] * total,
        "description_full": values("description", "Full description"),
        "salary_raw": values("salary", "40.000 EUR"),
        "skills": values("skills", "Python|SQL"),
        "work_mode": values("work_mode", "Remote"),
        "location_raw": values("location", "Amsterdam"),
        "posted_datetime": values("posted_date", "2026-09-20"),
    }
    pq.write_table(pa.table(columns), str(path))


def _nvb_record(tmp_path: Path) -> records.RepairRecord:
    """Create a real record in tmp from the NVB ``work_mode`` brief."""
    return records.create_record(
        _brief("nvb", field="work_mode"),
        repairs_dir=tmp_path / "repairs",
        on_date=date(2026, 10, 4),
    )


# --- targets ------------------------------------------------------------------


def test_targets_lists_primaries_then_secondaries_in_spanish(capsys):
    code, out, err = _run(capsys, ["targets", *_fixture_arguments()])

    assert code == 0
    assert err == ""
    assert "Objetivos de reparación — run de origen: 2026-10-03" in out
    assert "Total: 19 objetivos (5 primarios, 14 secundarios)" in out
    assert out.count("primario (primary)") == 5
    assert out.count("secundario (secondary)") == 14
    sources = _NUMBERED_SOURCE_RE.findall(out)
    assert sources[:5] == list(_PRIMARY_ORDER)
    assert sources[5] == "indeed"
    assert "Playbook: §6.1 InfoJobs" in out


def test_targets_default_path_uses_the_patched_default(tmp_path, monkeypatch, capsys):
    payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    # A distinct copy: only InfoJobs remains, so an untouched repository path
    # would still print the 19 targets of the real fixture.
    payload["sources"] = [
        entry for entry in payload["sources"] if entry["id"] == "infojobs"
    ]
    payload["investigations"] = [
        entry
        for entry in payload["investigations"]
        if entry["source"] == "infojobs"
    ]
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    default_path = logs_dir / "diagnostic_last.json"
    default_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(targets, "DEFAULT_DIAGNOSTIC_PATH", default_path)

    code, out, err = _run(capsys, ["targets"])

    assert code == 0
    assert err == ""
    assert "run de origen: 2026-10-03" in out
    assert "Total: 1 objetivo (1 primario, 0 secundarios)" in out
    assert "infojobs" in out
    assert "19 objetivos" not in out


def test_targets_missing_diagnostic_reports_a_spanish_error(tmp_path, capsys):
    missing = tmp_path / "no-existe.json"

    code, out, err = _run(capsys, ["targets", "--diagnostic", str(missing)])

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "no se encontró el diagnóstico" in err
    assert str(missing) in err
    assert "Traceback" not in err


_INCONCLUSIVE_DIAGNOSTIC = json.dumps(
    {
        "schema_version": 1,
        "run": {"date": "2026-10-03"},
        "global_status": {"status": "inconclusive", "reason": None},
        "sources": [],
        "investigations": [],
    }
)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{no es json", "no contiene JSON válido"),
        ('{"schema_version": 99}', "schema_version"),
        (_INCONCLUSIVE_DIAGNOSTIC, "inconcluso"),
    ],
    ids=["malformed", "unknown-schema", "inconclusive"],
)
def test_targets_rejects_an_invalid_diagnostic(tmp_path, capsys, content, expected):
    path = tmp_path / "diagnostic_last.json"
    path.write_text(content, encoding="utf-8")

    code, out, err = _run(capsys, ["targets", "--diagnostic", str(path)])

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert expected in err
    assert "Traceback" not in err


# --- brief --------------------------------------------------------------------


def test_brief_prints_the_english_brief_of_a_primary_source(capsys):
    code, out, err = _run(
        capsys, ["brief", "--source", "infojobs", *_fixture_arguments()]
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["source"] == "infojobs"
    assert payload["role"] == "primary"
    assert payload["playbook"]["section"] == "6.1"


def test_brief_output_writes_the_file_and_announces_it(tmp_path, capsys):
    output = tmp_path / "infojobs.brief.json"

    code, out, err = _run(
        capsys,
        [
            "brief",
            "--source",
            "infojobs",
            "--output",
            str(output),
            *_fixture_arguments(),
        ],
    )

    assert code == 0
    assert err == ""
    assert f"Brief escrito en {output}" in out
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["source"] == "infojobs"
    assert written["role"] == "primary"


def test_brief_selects_a_secondary_field(capsys):
    code, out, err = _run(
        capsys,
        [
            "brief",
            "--source",
            "nvb",
            "--field",
            "work_mode",
            *_fixture_arguments(),
        ],
    )

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["source"] == "nvb"
    assert payload["role"] == "secondary"
    assert payload["field"] == "work_mode"
    assert payload["trigger"] == "optional_field_at_or_below_threshold"


def test_brief_ambiguous_source_without_field_lists_the_candidates(capsys):
    code, out, err = _run(
        capsys, ["brief", "--source", "indeed", *_fixture_arguments()]
    )

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "--field" in err
    assert "candidatos" in err
    assert "indeed" in err
    assert "Traceback" not in err


def test_brief_unknown_source_reports_a_spanish_error(capsys):
    code, out, err = _run(
        capsys, ["brief", "--source", "desconocida", *_fixture_arguments()]
    )

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "no hay ningún objetivo" in err
    assert "desconocida" in err


# --- threshold ----------------------------------------------------------------


def test_threshold_without_history_uses_the_minimum(tmp_path, capsys):
    code, out, err = _run(
        capsys, ["threshold", "--history", str(tmp_path / "sin-historial.json")]
    )

    assert code == 0
    assert err == ""
    assert "No hay fuentes con historial" in out
    assert "umbral de cualquier fuente es 1" in out


def test_threshold_lists_sources_in_alphabetical_order(tmp_path, capsys):
    history = tmp_path / "history.json"
    threshold.save_history(
        {
            "schema_version": 1,
            "sources": {
                "indeed": {"best_verified_offers": 2},
                "linkedin": {"best_verified_offers": 0},
            },
        },
        history,
    )

    code, out, err = _run(capsys, ["threshold", "--history", str(history)])

    assert code == 0
    assert err == ""
    assert out.splitlines() == [
        "indeed | mejor verificado: 2 | umbral: 3",
        "linkedin | mejor verificado: 0 | umbral: 1",
    ]


def test_threshold_of_one_source_shows_its_best_and_limit(tmp_path, capsys):
    history = tmp_path / "history.json"
    threshold.save_history(
        {
            "schema_version": 1,
            "sources": {
                "indeed": {"best_verified_offers": 2},
                "linkedin": {"best_verified_offers": 0},
            },
        },
        history,
    )

    code, out, err = _run(
        capsys, ["threshold", "--source", "linkedin", "--history", str(history)]
    )

    assert code == 0
    assert err == ""
    assert out.strip() == "linkedin | mejor verificado: 0 | umbral: 1"


def test_threshold_of_a_source_without_entry_uses_the_minimum(tmp_path, capsys):
    history = tmp_path / "history.json"
    threshold.save_history(
        {"schema_version": 1, "sources": {"nvb": {"best_verified_offers": 1}}},
        history,
    )

    code, out, err = _run(
        capsys, ["threshold", "--source", "glassdoor", "--history", str(history)]
    )

    assert code == 0
    assert err == ""
    assert out.strip() == "glassdoor | mejor verificado: sin dato | umbral: 1"


def test_threshold_warns_about_a_corrupt_history_in_spanish(tmp_path, capsys):
    history = tmp_path / "history.json"
    history.write_text("no es json", encoding="utf-8")

    code, out, err = _run(capsys, ["threshold", "--history", str(history)])

    assert code == 0
    assert "Aviso:" in err
    assert "no contiene JSON válido" in err
    assert "umbral de cualquier fuente es 1" in out


# --- quality ------------------------------------------------------------------


def test_quality_passing_test_prints_ok_and_writes_the_verdict(tmp_path, capsys):
    record = _nvb_record(tmp_path)
    parquet = tmp_path / "live.parquet"
    _write_live_parquet(parquet, total=4)
    output = tmp_path / "quality_after.json"

    code, out, err = _run(
        capsys,
        [
            "quality",
            "--record",
            str(record.directory),
            "--parquet",
            str(parquet),
            "--output",
            str(output),
        ],
    )

    assert code == 0
    assert err == ""
    assert "Veredicto: OK" in out
    assert f"Calidad escrita en {output}" in out
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {
        "source",
        "ok",
        "total_offers",
        "blockers",
        "missing_columns",
        "read_error",
        "regression_tolerance_pp",
        "fields",
    }
    assert payload["ok"] is True
    assert payload["source"] == "nvb"
    assert payload["total_offers"] == 4


def test_quality_incomplete_description_fails_the_gate_with_exit_one(
    tmp_path, capsys
):
    record = _nvb_record(tmp_path)
    parquet = tmp_path / "live.parquet"
    _write_live_parquet(parquet, total=4, valid_counts={"description": 0})

    code, out, err = _run(
        capsys,
        [
            "quality",
            "--record",
            str(record.directory),
            "--parquet",
            str(parquet),
        ],
    )

    assert code == 1
    assert err == ""
    assert "Veredicto: NO OK" in out
    assert "falta la descripción (obligatoria y prioritaria)" in out


def test_quality_missing_record_reports_a_spanish_error(tmp_path, capsys):
    code, out, err = _run(
        capsys,
        [
            "quality",
            "--record",
            str(tmp_path / "no-existe"),
            "--parquet",
            str(tmp_path / "live.parquet"),
        ],
    )

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "no se encontró el perfil de calidad" in err
    assert "Traceback" not in err


# --- record -------------------------------------------------------------------


def test_record_open_reports_the_record_and_refuses_a_duplicate(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(records, "DEFAULT_REPAIRS_DIR", tmp_path / "repairs")
    brief_file = tmp_path / "context.json"
    brief_file.write_text(
        json.dumps(_brief("infojobs"), ensure_ascii=False), encoding="utf-8"
    )

    code, out, err = _run(capsys, ["record", "--brief", str(brief_file)])

    record_dir = tmp_path / "repairs" / "20261003-infojobs"
    assert code == 0
    assert err == ""
    assert record_dir.is_dir()
    assert f"Registro creado en {record_dir}" in out
    assert "Rama del fix: repair/infojobs-20261003" in out
    assert "Estado: planificado" in out
    assert json.loads(
        (record_dir / "context.json").read_text(encoding="utf-8")
    )["source"] == "infojobs"

    code, out, err = _run(capsys, ["record", "--brief", str(brief_file)])

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "ya existe" in err
    assert "Traceback" not in err


def test_record_completion_updates_index_and_history(tmp_path, monkeypatch, capsys):
    record = records.create_record(
        _brief("infojobs"),
        repairs_dir=tmp_path / "repairs",
        on_date=date(2026, 10, 4),
    )
    index = tmp_path / "README.md"
    index.write_text(_INDEX_TEMPLATE, encoding="utf-8")
    history = tmp_path / "history.json"
    monkeypatch.setattr(records, "DEFAULT_INDEX_PATH", index)
    monkeypatch.setattr(threshold, "DEFAULT_HISTORY_PATH", history)

    code, out, err = _run(
        capsys,
        [
            "record",
            "--record",
            str(record.directory),
            "--status",
            "descartado",
            "--result",
            "prueba",
        ],
    )

    assert code == 0
    assert err == ""
    assert f"Registro completado en {record.directory}" in out
    assert "Estado: descartado" in out
    assert (
        "| 2026-10-03 | infojobs | descartado | repair/infojobs-20261003 "
        "| prueba | sin medición |"
        in index.read_text(encoding="utf-8")
    )
    stored = json.loads(history.read_text(encoding="utf-8"))
    assert stored["sources"]["infojobs"]["last_result"] == "descartado"


def test_record_tested_without_verified_offers_reports_a_spanish_error(
    tmp_path, capsys
):
    record = records.create_record(
        _brief("infojobs"),
        repairs_dir=tmp_path / "repairs",
        on_date=date(2026, 10, 4),
    )

    code, out, err = _run(
        capsys,
        [
            "record",
            "--record",
            str(record.directory),
            "--status",
            "probado",
            "--result",
            "prueba",
        ],
    )

    assert code == 1
    assert out == ""
    assert err.startswith("Error: ")
    assert "recuento verificado" in err
    assert "Traceback" not in err


# --- argparse usage -----------------------------------------------------------


def test_main_without_subcommand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])

    assert excinfo.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_record_without_mode_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["record"])

    assert excinfo.value.code == 2
    assert "el modo del registro es obligatorio" in capsys.readouterr().err


def test_record_completion_without_status_or_result_is_a_usage_error(
    tmp_path, capsys
):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["record", "--record", str(tmp_path / "20261003-infojobs")])

    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "--status" in err and "--result" in err


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("targets", "brief", "threshold", "quality", "record"):
        assert command in out
