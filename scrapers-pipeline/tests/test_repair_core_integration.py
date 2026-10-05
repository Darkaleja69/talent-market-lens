"""Offline integration test of the deterministic repair core (T-23).

End-to-end walkthrough of ``targets -> brief -> record -> threshold -> quality``
on the sanitized 2026-10-03 diagnostic (RF-1-RF-11, RF-16): the five failed
sources and the fourteen secondary completeness objectives are selected from
the real fixture, and the whole chain runs for every primary source plus one
secondary objective (``nvb`` ``work_mode``) without touching the network.

Each target is repaired with a synthetic Parquet: every field whose brief
target is 100.0 is published with a valid value through the source's first
``verification.sources.field_aliases`` alias and the mandatory columns of
``verification.sources.required_columns`` are always present, so
``quality.evaluate_quality`` passes with the exact offer count. The record is
then completed as ``probado`` and its ``quality_after.json``, index row and
history entry are checked.

Isolation is explicit: two records of different sources are created and
completed interleaved (create A, create B, complete B, complete A) and each
record, history entry and threshold stays independent. A dedicated test runs
the whole chain with ``socket.socket`` and ``socket.create_connection`` patched
to fail, so an accidental connection would break the test.

Every repair lives under ``tmp_path`` (``repairs_dir``/``index_path``/
``history_path`` are always explicit): the repository ``repairs/`` is never
touched, and the dates are pinned (``on_date``, ``scraped_at``) so nothing
depends on the wall clock.
"""
from __future__ import annotations

import json
import re
import socket
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from repair import brief, quality, records, targets, threshold
from verification import field_contract, sources

_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "diagnostic_2026-10-03.sanitized.json"
)

# Pinned dates and offer count of the synthetic live test (no wall clock).
_OPENED = date(2026, 10, 4)
_SCRAPED_AT = "2026-10-03"
_OFFERS = 3

_PRIMARY_ORDER = ("indeed", "linkedin", "infojobs", "irishjobs", "glassdoor")

# source, kind, outcome, offers_current_run, offers_snapshot.
_PRIMARY_EXPECTATIONS = (
    ("indeed", "direct", "ok", 57, None),
    ("linkedin", "direct", "ok", 1590, 8387),
    ("infojobs", "direct", "blocked", 0, None),
    ("irishjobs", "multi_site", "error", None, None),
    ("glassdoor", "multi_site", "error", None, None),
)

# source, field, trigger, current_pct, in diagnostic order.
_SECONDARY_EXPECTATIONS = (
    ("indeed", "description", "required_field_below_target", 0.0),
    ("indeed", "salary", "optional_field_at_or_below_threshold", 35.08771929824562),
    ("indeed", "skills", "optional_field_at_or_below_threshold", 0.0),
    ("indeed", "work_mode", "optional_field_at_or_below_threshold", 17.54385964912281),
    ("linkedin", "description", "required_field_below_target", 88.44640515082867),
    ("linkedin", "salary", "optional_field_at_or_below_threshold", 16.31095743412424),
    ("linkedin", "work_mode", "optional_field_at_or_below_threshold", 42.42279718612138),
    ("stepstone_nl", "salary", "optional_field_at_or_below_threshold", 0.0),
    ("stepstone_nl", "skills", "optional_field_at_or_below_threshold", 0.0),
    ("stepstone_nl", "work_mode", "optional_field_at_or_below_threshold", 0.0),
    ("nvb", "work_mode", "optional_field_at_or_below_threshold", 0.0),
    ("jobs_ch", "salary", "optional_field_at_or_below_threshold", 40.38461538461539),
    ("jobs_ch", "skills", "optional_field_at_or_below_threshold", 32.69230769230769),
    ("jobs_ch", "work_mode", "optional_field_at_or_below_threshold", 11.538461538461538),
)

# Source, field -> a value that satisfies the data contract of the field. The
# ``id`` value is generated per row because it is the deduplication key.
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


# --- Fixture and queue --------------------------------------------------------


def _loaded():
    return targets.load_diagnostic(_FIXTURE)


def _target(source: str, field: str | None = None) -> targets.RepairTarget:
    """Return the selected target of ``source`` (and ``field``), or fail."""
    queue = targets.select_all_targets(_loaded())
    return next(
        target
        for target in queue
        if target.source == source and (field is None or target.field == field)
    )


def test_real_fixture_queue_matches_the_expected_primaries_and_secondaries():
    loaded = _loaded()
    queue = targets.select_all_targets(loaded)

    assert loaded.warnings == ()
    primaries = queue[:5]
    assert [target.source for target in primaries] == list(_PRIMARY_ORDER)
    assert all(target.role == targets.ROLE_PRIMARY for target in primaries)
    secondaries = queue[5:]
    assert len(secondaries) == 14
    assert all(target.role == targets.ROLE_SECONDARY for target in secondaries)
    assert [target.source for target in secondaries] == [
        source for source, _, _, _ in _SECONDARY_EXPECTATIONS
    ]
    assert [target.field for target in secondaries] == [
        field for _, field, _, _ in _SECONDARY_EXPECTATIONS
    ]
    assert Counter(target.source for target in secondaries) == Counter(
        {"indeed": 4, "linkedin": 3, "stepstone_nl": 3, "nvb": 1, "jobs_ch": 3}
    )
    assert all(target.run_date == date(2026, 10, 3) for target in queue)


def test_selected_targets_carry_their_fields_evidence_and_quality_profiles():
    queue = targets.select_all_targets(_loaded())

    for target, expected in zip(
        queue[:5], _PRIMARY_EXPECTATIONS, strict=True
    ):
        source, kind, outcome, offers_run, offers_snapshot = expected
        assert target.source == source
        assert target.status == targets.SOURCE_FAILED
        assert target.kind == kind
        assert target.outcome == outcome
        assert target.offers_current_run == offers_run
        assert target.offers_snapshot == offers_snapshot
        assert target.evidence

    for target, expected in zip(
        queue[5:], _SECONDARY_EXPECTATIONS, strict=True
    ):
        source, field, trigger, current_pct = expected
        assert target.source == source
        assert target.field == field
        assert target.trigger == trigger
        assert target.current_pct == pytest.approx(current_pct)
        focus = [entry.field for entry in target.quality if entry.is_focus]
        assert focus == [field]

    for target in queue:
        assert [entry.field for entry in target.quality] == list(
            field_contract.MEASURED_FIELDS
        )
        assert str(_FIXTURE) in target.evidence_paths


# --- Synthetic live-test Parquet ---------------------------------------------


def _write_live_parquet(
    path: Path, target: targets.RepairTarget, *, total: int
) -> None:
    """Write a valid live-test Parquet for a target, with ``total`` offers.

    Every field of the brief whose target is 100.0 is published with a valid
    value through its first ``sources.field_aliases`` alias; the mandatory
    columns of the source are added when they are not an alias of a measured
    field, and the id values are unique so every offer survives the
    deduplication of the completeness measurement.
    """
    columns: dict[str, list] = {}
    for entry in target.quality:
        if entry.target_pct != 100.0:
            continue
        aliases = sources.field_aliases(target.source, entry.field)
        assert aliases, entry.field
        alias = aliases[0]
        if entry.field == "id":
            columns[alias] = [f"{target.source}-{index}" for index in range(total)]
        else:
            value = _VALID_VALUES[entry.field]
            assert (
                field_contract.classify(entry.field, value)
                is field_contract.VALID
            ), entry.field
            columns[alias] = [value] * total
    for column in sources.required_columns(target.source):
        if column not in columns:
            columns[column] = _required_column_values(column, total)
    pq.write_table(pa.table(columns), str(path))


def _required_column_values(column: str, total: int) -> list[str]:
    """Return filler values for a mandatory column that is not an alias."""
    if column.endswith("url") or column.endswith("link"):
        return [f"https://example.com/{column}/{index}" for index in range(total)]
    return [_SCRAPED_AT] * total


# --- Chain helpers ------------------------------------------------------------


def _write_index(path: Path) -> None:
    path.write_text(_INDEX_TEMPLATE, encoding="utf-8")


def _record_files(record: records.RepairRecord) -> dict[str, bytes]:
    """Return relative path -> bytes of every file of a record directory."""
    return {
        path.relative_to(record.directory).as_posix(): path.read_bytes()
        for path in sorted(record.directory.rglob("*"))
        if path.is_file()
    }


def _index_rows(text: str) -> list[str]:
    match = _INDEX_RE.search(text)
    assert match is not None
    lines = match.group("body").strip().splitlines()
    assert len(lines) >= 2
    return lines[2:]


def _build_record(
    target: targets.RepairTarget, repairs_dir: Path
) -> records.RepairRecord:
    """Build the brief and open the ``planificado`` record of a target."""
    built = brief.build_brief(target)
    brief.validate_brief(built)
    record = records.create_record(
        built, repairs_dir=repairs_dir, on_date=_OPENED
    )

    assert record.source == target.source
    assert record.date == date(2026, 10, 3)
    assert record.branch == f"repair/{target.source}-20261003"
    assert record.directory.name == f"20261003-{target.source}"
    assert record.evidence_dir.is_dir()
    assert list(record.evidence_dir.iterdir()) == []
    assert json.loads(record.context_path.read_text(encoding="utf-8")) == built
    quality_before = json.loads(
        record.quality_before_path.read_text(encoding="utf-8")
    )
    assert quality_before["source"] == target.source
    assert quality_before["fields"] == built["quality"]
    plan = record.plan_path.read_text(encoding="utf-8")
    assert "- **Estado:** planificado" in plan
    assert "{{" not in plan
    return record


def _measure_live_test(
    target: targets.RepairTarget, tmp_path: Path, *, offers: int = _OFFERS
) -> quality.QualityVerdict:
    """Measure a synthetic live test and require the gate to pass."""
    parquet = tmp_path / f"{target.source}-{target.field or 'primary'}.parquet"
    _write_live_parquet(parquet, target, total=offers)
    verdict = quality.evaluate_quality(parquet, target.source, target.quality)
    assert verdict.blockers == ()
    assert verdict.ok is True
    assert verdict.total_offers == offers
    return verdict


def _complete(
    record: records.RepairRecord,
    target: targets.RepairTarget,
    verdict: quality.QualityVerdict,
    *,
    offers: int,
    index_path: Path,
    history_path: Path,
) -> None:
    """Complete a record as ``probado`` and check its stored outcome."""
    records.complete_record(
        record,
        status=records.STATUS_TESTED,
        result=f"Reparación probada: {offers} ofertas con umbral 1",
        changes="- Ajuste de selectores y de sesión.",
        tests="- `python -m pytest tests -q` → OK.",
        live_test=(
            "- Alcance acotado y repetible.\n"
            f"- Recuento: {offers} ofertas."
        ),
        verified_offers=offers,
        quality_after=verdict,
        index_path=index_path,
        history_path=history_path,
    )

    stored = json.loads(record.quality_after_path.read_text(encoding="utf-8"))
    assert stored == quality.verdict_to_dict(verdict)

    history = json.loads(history_path.read_text(encoding="utf-8"))
    entry = history["sources"][target.source]
    assert entry["best_verified_offers"] == offers
    assert entry["last_result"] == records.STATUS_TESTED
    assert entry["last_run_date"] == "2026-10-03"
    assert entry["quality_ok"] is True
    reloaded = threshold.load_history(history_path)
    assert threshold.threshold_for(reloaded.history, target.source) == offers + 1

    plan = record.plan_path.read_text(encoding="utf-8")
    assert "- **Estado:** probado" in plan
    assert "Veredicto de `quality.py`: **OK**." in plan

    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    matching = [
        row for row in rows if row.startswith(f"| 2026-10-03 | {target.source} |")
    ]
    assert len(matching) == 1
    assert matching[0].startswith(
        f"| 2026-10-03 | {target.source} | probado "
        f"| repair/{target.source}-20261003 |"
    )


def _run_full_chain(
    target: targets.RepairTarget,
    tmp_path: Path,
    *,
    offers: int = _OFFERS,
) -> tuple[records.RepairRecord, quality.QualityVerdict]:
    """Walk ``targets -> brief -> record -> threshold -> quality`` once."""
    repairs_dir = tmp_path / "repairs"
    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    if not index_path.exists():
        _write_index(index_path)

    loaded = threshold.load_history(history_path)
    assert loaded.warnings == ()
    assert threshold.threshold_for(loaded.history, target.source) == 1

    record = _build_record(target, repairs_dir)
    verdict = _measure_live_test(target, tmp_path, offers=offers)
    _complete(
        record,
        target,
        verdict,
        offers=offers,
        index_path=index_path,
        history_path=history_path,
    )
    return record, verdict


# --- Full chain per target ----------------------------------------------------


@pytest.mark.parametrize("source", _PRIMARY_ORDER)
def test_full_chain_repairs_each_failed_source(tmp_path, source):
    target = _target(source)
    assert target.role == targets.ROLE_PRIMARY

    record, verdict = _run_full_chain(target, tmp_path)

    assert record.source == source
    assert verdict.ok is True
    assert verdict.total_offers == _OFFERS


def test_full_chain_repairs_the_nvb_work_mode_secondary(tmp_path):
    target = _target("nvb", "work_mode")
    assert target.role == targets.ROLE_SECONDARY
    assert target.current_pct == 0.0

    built = brief.build_brief(target)
    brief.validate_brief(built)
    assert built["field"] == "work_mode"
    assert built["reason"]["trigger"] == targets.TRIGGER_OPTIONAL_FIELD
    work_mode = next(
        entry for entry in built["quality"] if entry["field"] == "work_mode"
    )
    assert work_mode["is_focus"] is True
    assert work_mode["target_pct"] == 100.0

    record, verdict = _run_full_chain(target, tmp_path)

    assert record.directory.name == "20261003-nvb"
    stored = json.loads(record.context_path.read_text(encoding="utf-8"))
    assert stored["role"] == "secondary"
    assert stored["field"] == "work_mode"
    assert verdict.ok is True


# --- Isolation between per-source repairs -------------------------------------


def test_repairs_of_different_sources_stay_isolated(tmp_path):
    repairs_dir = tmp_path / "repairs"
    index_path = tmp_path / "README.md"
    history_path = tmp_path / "history.json"
    _write_index(index_path)

    infojobs = _target("infojobs")
    nvb = _target("nvb", "work_mode")
    record_a = _build_record(infojobs, repairs_dir)
    record_b = _build_record(nvb, repairs_dir)
    assert record_a.directory != record_b.directory
    a_before = _record_files(record_a)

    # Interleaved order: create A, create B, complete B, complete A.
    verdict_b = _measure_live_test(nvb, tmp_path, offers=5)
    _complete(
        record_b,
        nvb,
        verdict_b,
        offers=5,
        index_path=index_path,
        history_path=history_path,
    )

    # Completing B neither touches A's record nor A's threshold.
    assert _record_files(record_a) == a_before
    assert not record_a.quality_after_path.exists()
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert set(history["sources"]) == {"nvb"}
    assert history["sources"]["nvb"]["best_verified_offers"] == 5
    assert threshold.threshold_for(history, "infojobs") == 1
    b_after = _record_files(record_b)

    verdict_a = _measure_live_test(infojobs, tmp_path)
    _complete(
        record_a,
        infojobs,
        verdict_a,
        offers=_OFFERS,
        index_path=index_path,
        history_path=history_path,
    )

    # Completing A keeps B's record and its verified count untouched.
    assert _record_files(record_b) == b_after
    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["sources"]["infojobs"]["best_verified_offers"] == _OFFERS
    assert history["sources"]["nvb"]["best_verified_offers"] == 5
    assert threshold.threshold_for(history, "infojobs") == _OFFERS + 1
    assert threshold.threshold_for(history, "nvb") == 6

    rows = _index_rows(index_path.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert rows[0].startswith("| 2026-10-03 | infojobs | probado |")
    assert rows[1].startswith("| 2026-10-03 | nvb | probado |")


# --- No network ---------------------------------------------------------------


def test_whole_core_flow_needs_no_network(tmp_path, monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("network access is not allowed in offline tests")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    infojobs, _ = _run_full_chain(_target("infojobs"), tmp_path, offers=3)
    nvb, _ = _run_full_chain(_target("nvb", "work_mode"), tmp_path, offers=4)

    assert infojobs.source == "infojobs"
    assert nvb.source == "nvb"
    # The shared history keeps the two sources independent.
    history = json.loads(
        (tmp_path / "history.json").read_text(encoding="utf-8")
    )
    assert history["sources"]["infojobs"]["best_verified_offers"] == 3
    assert history["sources"]["nvb"]["best_verified_offers"] == 4
    assert (
        len(_index_rows((tmp_path / "README.md").read_text(encoding="utf-8")))
        == 2
    )
