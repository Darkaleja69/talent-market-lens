"""Flow-limit tests of the deterministic repair core (T-24).

Integration scenarios that exercise the limits of the whole repair flow
(``targets -> brief -> record -> threshold -> quality``) on the sanitized
2026-10-03 diagnostic (RF-1, RF-9, RF-10, RF-14):

- a missing diagnostic fails with a clear Spanish error and produces no
  targets, both in the core and in the ``targets`` CLI;
- an inconclusive diagnostic is rejected even when it still lists failed
  sources, so no repair starts;
- a stale diagnostic (an ``upload-*.log`` newer than ``run.date``) only warns
  and its queue is still selected;
- the Multi-site watchdog (``exit=75`` with ``merge_exit=0`` and
  ``output_merged=False``) reaches the brief and the record as a progress
  failure, and the repair is escalated without raising
  ``best_verified_offers``;
- each Multi-site portal is repaired as an independent record and branch, and
  completing one does not touch the other's record or history;
- escalation never lowers the incremental threshold, with or without previous
  history;
- simultaneous repairs of the same source (two run dates) share the index and
  the history without overwriting each other, and the history keeps the
  maximum;
- a source absent from the source catalog follows the generic playbook §6.7
  without invented evidence paths, columns or quality targets.

Everything runs offline (``socket`` is patched to fail for the whole module)
and under ``tmp_path``: ``repairs_dir``/``index_path``/``history_path`` are
always explicit, the repository ``repairs/`` is never touched, and the dates
are pinned (``on_date``, ``run.date`` copies, ``scraped_at``) so nothing
depends on the wall clock.
"""
from __future__ import annotations

import json
import re
import socket
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from repair import brief, cli, quality, records, targets, threshold
from verification import field_contract, sources

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "diagnostic_2026-10-03.sanitized.json"
)

# Pinned dates and offer counts of the synthetic live tests (no wall clock).
_OPENED = date(2026, 10, 4)
_SCRAPED_AT = "2026-10-03"
_OFFERS = 3
_WATCHDOG_SOURCES = ("irishjobs", "glassdoor")

# The failure-mode matrix of the diagnostic rejects a wrong schema_version,
# but the freshness warning and the target selection work on a stale one.
_PRIMARY_SOURCES = ("indeed", "linkedin", "infojobs", "irishjobs", "glassdoor")

# A source absent from `verification.sources`, as a future failure would
# arrive in the diagnostic (plan §6.7): no playbook paths and no catalog.
_NEW_SOURCE = {
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

# Valid values of the synthetic live-test Parquet, one per canonical field
# (``id`` is generated per row because it is the deduplication key).
_VALID_VALUES: dict[str, str] = {
    "title": "Data Engineer",
    "company": "ACME",
    "description": "Full description of the offer",
    "salary": "40000 EUR",
    "skills": "Python|SQL",
    "work_mode": "Remote",
    "location": "Amsterdam",
    "posted_date": "2026-09-20",
}

_INDEX_TEMPLATE = """# Registros de reparaciones (prueba)

Índice de los registros.

<!-- repair-index:start -->
| fecha | fuente | estado | rama | resultado | calidad |
|---|---|---|---|---|---|
<!-- repair-index:end -->
"""

_INDEX_RE = re.compile(
    r"<!--\s*repair-index:start\s*-->(?P<body>.*?)"
    r"<!--\s*repair-index:end\s*-->",
    re.DOTALL,
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail on any accidental connection during the whole module (T-24)."""

    def _forbidden(*args, **kwargs):
        raise AssertionError("network access is not allowed in offline tests")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)


# --- Fixture copies and selection helpers ------------------------------------


def _fixture_payload() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _diagnostic_copy(
    tmp_path: Path,
    name: str,
    *,
    run_date: str | None = None,
    extra_source: dict | None = None,
) -> Path:
    """Write a copy of the real fixture with optional run-date/source changes."""
    payload = _fixture_payload()
    if run_date is not None:
        payload["run"]["date"] = run_date
    if extra_source is not None:
        payload["sources"].append(extra_source)
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _target(
    source: str, field: str | None = None, *, diagnostic: Path = _FIXTURE
) -> targets.RepairTarget:
    """Return the selected target of ``source`` (and ``field``), or fail."""
    queue = targets.select_all_targets(targets.load_diagnostic(diagnostic))
    return next(
        target
        for target in queue
        if target.source == source and (field is None or target.field == field)
    )


# --- Record/index helpers -----------------------------------------------------


def _write_index(path: Path) -> None:
    path.write_text(_INDEX_TEMPLATE, encoding="utf-8")


def _index_rows(text: str) -> list[str]:
    match = _INDEX_RE.search(text)
    assert match is not None
    lines = match.group("body").strip().splitlines()
    assert len(lines) >= 2
    return lines[2:]


def _record_files(record: records.RepairRecord) -> dict[str, bytes]:
    """Return relative path -> bytes of every file of a record directory."""
    return {
        path.relative_to(record.directory).as_posix(): path.read_bytes()
        for path in sorted(record.directory.rglob("*"))
        if path.is_file()
    }


def _open_record(
    target: targets.RepairTarget, repairs_dir: Path
) -> tuple[dict, records.RepairRecord]:
    """Build the brief and open the ``planificado`` record of a target."""
    built = brief.build_brief(target)
    brief.validate_brief(built)
    record = records.create_record(
        built, repairs_dir=repairs_dir, on_date=_OPENED
    )
    assert record.source == target.source
    assert record.date == target.run_date
    assert record.branch == f"repair/{target.source}-{target.run_date:%Y%m%d}"
    assert record.directory.name == f"{target.run_date:%Y%m%d}-{target.source}"
    return built, record


def _complete(
    record: records.RepairRecord,
    *,
    status: str,
    result: str,
    index_path: Path,
    history_path: Path,
    offers: int | None = None,
    quality_after: quality.QualityVerdict | None = None,
) -> None:
    """Complete a record with a final status and the shared index/history."""
    records.complete_record(
        record,
        status=status,
        result=result,
        changes="- Cambio de prueba documentado.",
        tests="- `python -m pytest tests -q` → OK.",
        live_test="- Alcance acotado; sin Azure, landing ni merge.",
        verified_offers=offers,
        quality_after=quality_after,
        index_path=index_path,
        history_path=history_path,
    )


def _write_live_parquet(
    path: Path, target: targets.RepairTarget, *, total: int
) -> None:
    """Write a valid live-test Parquet for a target, with ``total`` offers.

    Every field of the brief whose target is 100.0 is published through its
    first ``sources.field_aliases`` alias; the mandatory columns of the source
    are added when they are not an alias of a measured field, and the id
    values are unique so every offer survives the deduplication.
    """
    columns: dict[str, list] = {}
    for entry in target.quality:
        if entry.target_pct != 100.0:
            continue
        alias = sources.field_aliases(target.source, entry.field)[0]
        if entry.field == "id":
            columns[alias] = [f"{target.source}-{index}" for index in range(total)]
        else:
            columns[alias] = [_VALID_VALUES[entry.field]] * total
    for column in sources.required_columns(target.source):
        if column not in columns:
            if column.endswith("url") or column.endswith("link"):
                columns[column] = [
                    f"https://example.com/{column}/{index}"
                    for index in range(total)
                ]
            else:
                columns[column] = [_SCRAPED_AT] * total
    pq.write_table(pa.table(columns), str(path))


def _measure(
    target: targets.RepairTarget, path: Path, *, offers: int
) -> quality.QualityVerdict:
    """Measure a synthetic live test and require the gate to pass."""
    _write_live_parquet(path, target, total=offers)
    verdict = quality.evaluate_quality(path, target.source, target.quality)
    assert verdict.blockers == ()
    assert verdict.ok is True
    assert verdict.total_offers == offers
    return verdict


def _complete_probado(
    record: records.RepairRecord,
    target: targets.RepairTarget,
    tmp_path: Path,
    *,
    offers: int,
    index_path: Path,
    history_path: Path,
) -> quality.QualityVerdict:
    """Measure a live test, complete the record as ``probado`` and check it."""
    verdict = _measure(
        target, tmp_path / f"{record.directory.name}.parquet", offers=offers
    )
    _complete(
        record,
        status=records.STATUS_TESTED,
        result=f"Reparación probada: {offers} ofertas",
        index_path=index_path,
        history_path=history_path,
        offers=offers,
        quality_after=verdict,
    )
    stored = json.loads(record.quality_after_path.read_text(encoding="utf-8"))
    assert stored == quality.verdict_to_dict(verdict)
    assert "- **Estado:** probado" in record.plan_path.read_text(
        encoding="utf-8"
    )
    return verdict


# --- Missing, inconclusive and stale diagnostics ------------------------------


def test_missing_diagnostic_fails_the_flow_without_targets(tmp_path, capsys):
    missing = tmp_path / "no-existe.json"

    with pytest.raises(targets.DiagnosticError) as excinfo:
        targets.load_diagnostic(missing)
    message = str(excinfo.value)
    assert "no se encontró el diagnóstico" in message
    assert str(missing) in message

    code = cli.main(["targets", "--diagnostic", str(missing)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert captured.err.startswith("Error: ")
    assert "no se encontró el diagnóstico" in captured.err
    assert "Traceback" not in captured.err


def test_inconclusive_diagnostic_produces_no_targets_at_all(tmp_path, capsys):
    path = _diagnostic_copy(tmp_path, "diagnostic_inconclusive.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["global_status"]["status"] = "inconclusive"
    path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    loaded = targets.load_diagnostic(path)
    for selector in (
        targets.select_targets,
        targets.select_secondary_targets,
        targets.select_all_targets,
    ):
        with pytest.raises(targets.DiagnosticError) as excinfo:
            selector(loaded)
        assert "inconcluso" in str(excinfo.value)
        assert str(path) in str(excinfo.value)

    code = cli.main(["targets", "--diagnostic", str(path)])
    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "inconcluso" in captured.err
    assert "Traceback" not in captured.err


def test_stale_diagnostic_warns_and_still_selects_the_queue(tmp_path, capsys):
    path = _diagnostic_copy(tmp_path, "diagnostic_last.json")
    (tmp_path / "upload-2026-10-04.log").write_text("", encoding="utf-8")

    loaded = targets.load_diagnostic(path, logs_dir=tmp_path)
    assert len(loaded.warnings) == 1
    warning = loaded.warnings[0]
    assert "2026-10-03" in warning
    assert "upload-2026-10-04.log" in warning
    assert "refresca" in warning
    assert len(targets.select_all_targets(loaded)) == 19

    code = cli.main(["targets", "--diagnostic", str(path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "Aviso:" in captured.err
    assert "upload-2026-10-04.log" in captured.err
    assert "Total: 19 objetivos (5 primarios, 14 secundarios)" in captured.out


# --- Multi-site watchdog and escalation ---------------------------------------


@pytest.mark.parametrize("source", _WATCHDOG_SOURCES)
def test_watchdog_exit_75_escalates_without_raising_the_bar(tmp_path, source):
    target = _target(source)
    assert target.role == targets.ROLE_PRIMARY
    assert target.outcome == "error"
    for token in ("exit=75", "merge_exit=0", "output_merged=False"):
        assert any(token in line for line in target.evidence), token

    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    _write_index(index_path)
    built, record = _open_record(target, tmp_path / "repairs")

    assert built["run_date"] == "2026-10-03"
    assert built["reason"]["outcome"] == "error"
    assert built["reason"]["summary"].startswith("error:")
    for token in ("exit=75", "merge_exit=0", "output_merged=False"):
        assert any(token in line for line in built["evidence"]), token
    plan = record.plan_path.read_text(encoding="utf-8")
    for token in ("exit=75", "merge_exit=0", "output_merged=False"):
        assert token in plan, token

    _complete(
        record,
        status=records.STATUS_ESCALATED,
        result="Watchdog de progreso (exit=75): se escala a la persona",
        index_path=index_path,
        history_path=history_path,
    )

    # A progress failure proves nothing: no measurement and no raised bar.
    assert not record.quality_after_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    entry = history["sources"][source]
    assert entry["best_verified_offers"] == 0
    assert entry["last_result"] == "escalado"
    assert entry["last_run_date"] == "2026-10-03"
    assert threshold.threshold_for(history, source) == 1
    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0].startswith(f"| 2026-10-03 | {source} | escalado |")


def test_escalation_with_history_keeps_the_previous_threshold(tmp_path):
    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    repairs_dir = tmp_path / "repairs"
    _write_index(index_path)

    old_diagnostic = _diagnostic_copy(
        tmp_path, "diagnostic_old.json", run_date="2026-10-02"
    )
    target_old = _target("infojobs", diagnostic=old_diagnostic)
    target_new = _target("infojobs")
    _, record_old = _open_record(target_old, repairs_dir)
    _, record_new = _open_record(target_new, repairs_dir)
    assert record_old.directory.name == "20261002-infojobs"
    assert record_new.directory.name == "20261003-infojobs"

    _complete_probado(
        record_old,
        target_old,
        tmp_path,
        offers=3,
        index_path=index_path,
        history_path=history_path,
    )
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert threshold.threshold_for(history, "infojobs") == 4

    _complete(
        record_new,
        status=records.STATUS_ESCALATED,
        result="Bloqueo persistente: se escala a la persona",
        index_path=index_path,
        history_path=history_path,
    )

    # The escalation records its outcome without lowering the bar.
    history = json.loads(history_path.read_text(encoding="utf-8"))
    entry = history["sources"]["infojobs"]
    assert entry["best_verified_offers"] == 3
    assert entry["last_result"] == "escalado"
    assert entry["last_run_date"] == "2026-10-03"
    assert threshold.threshold_for(history, "infojobs") == 4
    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0].startswith("| 2026-10-02 | infojobs | probado |")
    assert rows[1].startswith("| 2026-10-03 | infojobs | escalado |")


# --- Multi-site portals -------------------------------------------------------


def test_multi_site_portals_are_independent_records_and_branches(tmp_path):
    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    repairs_dir = tmp_path / "repairs"
    _write_index(index_path)

    irishjobs = _target("irishjobs")
    glassdoor = _target("glassdoor")
    built_ie, record_ie = _open_record(irishjobs, repairs_dir)
    built_gd, record_gd = _open_record(glassdoor, repairs_dir)

    assert record_ie.directory != record_gd.directory
    assert record_ie.branch == "repair/irishjobs-20261003"
    assert record_gd.branch == "repair/glassdoor-20261003"
    for built, source in ((built_ie, "irishjobs"), (built_gd, "glassdoor")):
        scope = built["test_scope"]
        assert scope["parameters"]["portals"] == [source]
        assert "affected portal only" in scope["description"]
        assert "only the affected portal" in scope["constraints"]
        assert scope["command"] == f"python -m src.{source} --max-pages 1"

    gd_before = _record_files(record_gd)
    _complete_probado(
        record_ie,
        irishjobs,
        tmp_path,
        offers=2,
        index_path=index_path,
        history_path=history_path,
    )

    # Completing IrishJobs touches neither the Glassdoor record nor its bar.
    assert _record_files(record_gd) == gd_before
    assert not record_gd.quality_after_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert set(history["sources"]) == {"irishjobs"}
    assert history["sources"]["irishjobs"]["best_verified_offers"] == 2
    assert threshold.threshold_for(history, "glassdoor") == 1

    ie_after = _record_files(record_ie)
    _complete(
        record_gd,
        status=records.STATUS_ESCALATED,
        result="Watchdog de progreso: se escala a la persona",
        index_path=index_path,
        history_path=history_path,
    )

    # Completing Glassdoor keeps the IrishJobs record and its count untouched.
    assert _record_files(record_ie) == ie_after
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["sources"]["irishjobs"]["best_verified_offers"] == 2
    assert history["sources"]["glassdoor"]["best_verified_offers"] == 0
    assert threshold.threshold_for(history, "irishjobs") == 3
    assert threshold.threshold_for(history, "glassdoor") == 1
    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0].startswith("| 2026-10-03 | glassdoor | escalado |")
    assert rows[1].startswith("| 2026-10-03 | irishjobs | probado |")


def test_multi_site_secondary_repair_is_scoped_to_its_portal_and_field(
    tmp_path,
):
    target = _target("stepstone_nl", "salary")
    assert target.role == targets.ROLE_SECONDARY
    assert target.status == "ok"
    assert target.outcome == "ok"

    built, record = _open_record(target, tmp_path / "repairs")

    assert built["playbook"]["section"] == "6.6"
    scope = built["test_scope"]
    assert scope["description"] == (
        "representative sample of the source detail for the affected field"
    )
    assert scope["parameters"] == {
        "scope": "representative sample",
        "detail": True,
        "field": "salary",
    }
    assert record.directory.name == "20261003-stepstone_nl"
    assert record.branch == "repair/stepstone_nl-20261003"
    plan = record.plan_path.read_text(encoding="utf-8")
    assert "representative sample" in plan
    assert "§6.6" in plan


# --- Simultaneous repairs and unknown sources ---------------------------------


def test_simultaneous_repairs_of_the_same_source_share_max_and_index(tmp_path):
    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    repairs_dir = tmp_path / "repairs"
    _write_index(index_path)

    old_diagnostic = _diagnostic_copy(
        tmp_path, "diagnostic_old.json", run_date="2026-10-02"
    )
    target_old = _target("infojobs", diagnostic=old_diagnostic)
    target_new = _target("infojobs")
    _, record_old = _open_record(target_old, repairs_dir)
    _, record_new = _open_record(target_new, repairs_dir)
    assert record_old.directory.name == "20261002-infojobs"
    assert record_new.directory.name == "20261003-infojobs"
    new_before = _record_files(record_new)

    _complete_probado(
        record_old,
        target_old,
        tmp_path,
        offers=3,
        index_path=index_path,
        history_path=history_path,
    )

    # The simultaneous repair of the newer run is still planned and untouched.
    assert _record_files(record_new) == new_before
    assert "- **Estado:** planificado" in record_new.plan_path.read_text(
        encoding="utf-8"
    )
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["sources"]["infojobs"]["best_verified_offers"] == 3
    assert threshold.threshold_for(history, "infojobs") == 4
    assert len(_index_rows(index_path.read_text(encoding="utf-8"))) == 1

    old_after = _record_files(record_old)
    _complete_probado(
        record_new,
        target_new,
        tmp_path,
        offers=5,
        index_path=index_path,
        history_path=history_path,
    )

    # The history keeps the maximum and the index accumulates both rows.
    assert _record_files(record_old) == old_after
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["sources"]["infojobs"]["best_verified_offers"] == 5
    assert threshold.threshold_for(history, "infojobs") == 6
    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0].startswith("| 2026-10-02 | infojobs | probado |")
    assert rows[1].startswith("| 2026-10-03 | infojobs | probado |")


def test_unknown_source_follows_the_generic_flow_without_inventing_data(
    tmp_path,
):
    path = _diagnostic_copy(
        tmp_path, "diagnostic_unknown.json", extra_source=_NEW_SOURCE
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    target = _target("nuevafuente", diagnostic=path)

    assert target.role == targets.ROLE_PRIMARY
    assert target.kind == "direct"
    assert target.outcome == "error"
    assert target.evidence_paths == (str(path), payload["run"]["log_path"])

    built = brief.build_brief(target)
    brief.validate_brief(built)
    assert built["playbook"]["section"] == "6.7"
    assert (
        built["playbook"]["title"]
        == "Generic playbook for new sources or future failures"
    )
    # No playbook path and no catalog column are invented.
    assert built["evidence_paths"] == [str(path), payload["run"]["log_path"]]
    required_goals = {
        entry["field"]
        for entry in built["quality"]
        if entry["target_pct"] == 100.0
    }
    assert required_goals == set(field_contract.REQUIRED_FIELDS)
    assert all(
        entry["target_pct"] is None
        for entry in built["quality"]
        if entry["field"] not in required_goals
    )

    built_record, record = _open_record(target, tmp_path / "repairs")
    assert record.directory.name == "20261003-nuevafuente"
    assert record.branch == "repair/nuevafuente-20261003"
    assert record.context_path.read_text(encoding="utf-8") == (
        brief.render_brief(built_record) + "\n"
    )
    plan = record.plan_path.read_text(encoding="utf-8")
    assert "§6.7" in plan
    assert "indeed_jobs_scraper" not in plan
    quality_before = json.loads(
        record.quality_before_path.read_text(encoding="utf-8")
    )
    assert {
        entry["field"]
        for entry in quality_before["fields"]
        if entry["target_pct"] == 100.0
    } == set(field_contract.REQUIRED_FIELDS)

    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    _write_index(index_path)
    loaded = threshold.load_history(history_path)
    assert threshold.threshold_for(loaded.history, "nuevafuente") == 1

    _complete(
        record,
        status=records.STATUS_ESCALATED,
        result="Fuente sin catálogo: sin columnas obligatorias; se escala",
        index_path=index_path,
        history_path=history_path,
    )

    history = json.loads(history_path.read_text(encoding="utf-8"))
    entry = history["sources"]["nuevafuente"]
    assert entry["best_verified_offers"] == 0
    assert entry["last_result"] == "escalado"
    assert threshold.threshold_for(history, "nuevafuente") == 1
    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0].startswith(
        "| 2026-10-03 | nuevafuente | escalado | repair/nuevafuente-20261003 |"
    )
