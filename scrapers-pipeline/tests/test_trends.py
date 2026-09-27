"""Tests for the completeness trend (T-33; RF-7).

Azure is simulated: a fake :class:`landing.RemoteReader` serves manifests and
published objects from an in-memory dict and materialises downloads into local
temporary files. Parquet payloads are built with PyArrow (already justified by
the plan, section 6.3). No network, AzCopy or credentials are involved.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import completeness, field_contract, fingerprint, landing, trends


class FakeReader:
    """In-memory :class:`landing.RemoteReader` simulating the landing."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})

    def list_objects(self, prefix: str) -> list[landing.RemoteObject]:
        return [
            landing.RemoteObject(path=key, size=len(data))
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def download(self, remote_path: str, local_path: Path) -> None:
        data = self.objects.get(remote_path)
        if data is None:
            raise landing.RemoteError("objeto no encontrado")
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    def close(self) -> None:  # pragma: no cover - protocol completeness
        pass


def _parquet_bytes(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)
    return sink.getvalue().to_pybytes()


def _fp_indeed() -> dict:
    return fingerprint.build_fingerprint(
        ["indeed"], [fingerprint.SearchDimension(source="indeed", search="python")]
    )


def _fp_linkedin() -> dict:
    return fingerprint.build_fingerprint(
        ["linkedin"], [fingerprint.SearchDimension(source="linkedin", search="python")]
    )


def _field(field: str, pct: float, *, total: int = 10) -> completeness.FieldCompleteness:
    valid = round(pct * total / 100)
    return completeness.FieldCompleteness(
        field=field,
        required=field_contract.is_required(field),
        total=total,
        valid=valid,
        absent=0,
        invalid=total - valid,
        completeness_pct=pct,
    )


def _source_completeness(
    source: str, pcts: dict[str, float]
) -> completeness.SourceCompleteness:
    return completeness.SourceCompleteness(
        source=source,
        total_offers=10,
        fields={field: _field(field, pct) for field, pct in pcts.items()},
    )


def _snapshot(
    label: str,
    fp: dict | None,
    pcts_by_source: dict[str, dict[str, float]] | None = None,
) -> trends.RunSnapshot:
    measured = {
        source: _source_completeness(source, pcts)
        for source, pcts in (pcts_by_source or {}).items()
    }
    return trends.RunSnapshot(
        label=label, fingerprint=fp, completeness_by_source=measured
    )


def _manifest(
    scraper: str,
    stamp: str,
    *,
    fp: dict | None = None,
    entries: tuple[landing.ManifestFile, ...] = (),
) -> landing.Manifest:
    return landing.Manifest(
        scraper=scraper,
        stamp=stamp,
        schema_version=1,
        total_files=len(entries),
        bad_files=0,
        files=entries,
        fingerprint=fp,
    )


# --------------------------------------------------------------------------
# select_comparable_runs (pure)
# --------------------------------------------------------------------------


def test_select_comparable_runs_keeps_current_and_only_matching():
    fp_a = _fp_indeed()
    fp_b = _fp_linkedin()
    current = _snapshot("2026-09-25", fp_a)
    matching = _snapshot("2026-09-24", fp_a)
    other = _snapshot("2026-09-23", fp_b)
    without = _snapshot("2026-09-22", None)

    result = trends.select_comparable_runs(current, [other, without, matching])

    assert [run.label for run in result] == ["2026-09-24", "2026-09-25"]
    assert result[-1] is current


def test_select_comparable_runs_excludes_candidates_without_fingerprint():
    current = _snapshot("2026-09-25", _fp_indeed())
    no_fp = _snapshot("2026-09-24", None)

    result = trends.select_comparable_runs(current, [no_fp])

    assert result == (current,)


def test_select_comparable_runs_current_without_fingerprint_is_alone():
    current = _snapshot("2026-09-25", None)
    candidate = _snapshot("2026-09-24", _fp_indeed())

    result = trends.select_comparable_runs(current, [candidate])

    assert result == (current,)


def test_select_comparable_runs_sorts_chronologically():
    fp = _fp_indeed()
    current = _snapshot("2026-09-25", fp)
    candidates = [
        _snapshot("2026-09-23", fp),
        _snapshot("2026-09-21", fp),
        _snapshot("2026-09-24", fp),
        _snapshot("2026-09-22", fp),
    ]

    result = trends.select_comparable_runs(current, candidates)

    assert [run.label for run in result] == [
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
        "2026-09-24",
        "2026-09-25",
    ]


def test_select_comparable_runs_respects_limit_of_five():
    fp = _fp_indeed()
    current = _snapshot("2026-09-30", fp)
    # Ten matching candidates older than the current run.
    candidates = [_snapshot(f"2026-09-{day:02d}", fp) for day in range(10, 20)]

    result = trends.select_comparable_runs(current, candidates, limit=5)

    assert len(result) == 5
    assert result[-1] is current
    assert [run.label for run in result] == [
        "2026-09-16",
        "2026-09-17",
        "2026-09-18",
        "2026-09-19",
        "2026-09-30",
    ]


def test_select_comparable_runs_never_drops_current_even_if_older():
    fp = _fp_indeed()
    current = _snapshot("2026-01-01", fp)
    candidates = [_snapshot(f"2026-09-{day:02d}", fp) for day in range(20, 25)]

    result = trends.select_comparable_runs(current, candidates, limit=5)

    assert len(result) == 5
    assert any(run is current for run in result)


# --------------------------------------------------------------------------
# build_trend (pure)
# --------------------------------------------------------------------------


def _five_runs() -> list[trends.RunSnapshot]:
    series = {
        "title": [90.0, 92.0, 94.0, 96.0, 100.0],
        "company": [100.0, 99.0, 98.0, 97.0, 95.0],
        "description": [80.0, 80.0, 80.0, 80.0, 80.0],
    }
    runs = []
    for index, day in enumerate(range(22, 27)):
        pcts = {field: values[index] for field, values in series.items()}
        runs.append(
            _snapshot(f"2026-09-{day}", _fp_indeed(), {"indeed": pcts})
        )
    return runs


def test_build_trend_five_runs_reports_series_and_directions():
    result = trends.build_trend(_five_runs())

    assert result.runs_used == 5
    assert result.comparable is True
    assert result.note is None
    assert set(result.sources) == {"indeed"}

    source = result.sources["indeed"]
    assert source.runs_used == 5

    title = source.fields["title"]
    assert title.values == (90.0, 92.0, 94.0, 96.0, 100.0)
    assert title.direction == trends.DIRECTION_IMPROVED
    assert title.delta == pytest.approx(4.0)
    assert title.required is True

    company = source.fields["company"]
    assert company.direction == trends.DIRECTION_REGRESSED
    assert company.delta == pytest.approx(-2.0)

    description = source.fields["description"]
    assert description.direction == trends.DIRECTION_STABLE
    assert description.delta == pytest.approx(0.0)


def test_build_trend_skips_sources_and_fields_not_in_every_run():
    fp = _fp_indeed()
    run_one = _snapshot(
        "2026-09-22",
        fp,
        {
            "indeed": {"title": 90.0, "company": 90.0},
            "linkedin": {"title": 90.0},
        },
    )
    run_two = _snapshot("2026-09-23", fp, {"indeed": {"title": 100.0}})

    result = trends.build_trend([run_one, run_two])

    assert result.comparable is True
    # LinkedIn is absent from the second run, so it has no false series.
    assert set(result.sources) == {"indeed"}
    # Company is absent from the second run, so it is not traced either.
    assert set(result.sources["indeed"].fields) == {"title"}
    assert result.sources["indeed"].fields["title"].values == (90.0, 100.0)


@pytest.mark.parametrize("runs", [[], [_snapshot("2026-09-25", _fp_indeed())]])
def test_build_trend_with_less_than_two_runs_is_not_comparable(runs):
    result = trends.build_trend(runs)

    assert result.comparable is False
    assert result.runs_used == len(runs)
    assert result.note is not None
    assert "tendencia" in result.note
    assert result.sources == {}


# --------------------------------------------------------------------------
# load_published_completeness / snapshot_from_manifest (fake Azure)
# --------------------------------------------------------------------------


def _indeed_table() -> pa.Table:
    return pa.table(
        {
            "job_key": ["1", "2"],
            "title": ["Data Engineer", None],
            "company": ["Acme", "Beta"],
            "description_text": ["desc one", "desc two"],
            "salary_text": ["30k", None],
            "workplace_type": ["Remote", "Hybrid"],
            "location": ["Madrid", "Barcelona"],
            "posted_date": ["2026-09-01", "2026-09-02"],
        }
    )


def test_snapshot_from_manifest_measures_published_completeness(tmp_path):
    fp = _fp_indeed()
    key = "indeed/dia=2026-09-26/jobs.parquet"
    reader = FakeReader({key: _parquet_bytes(_indeed_table())})
    manifest = _manifest(
        "indeed",
        "20260926T010000",
        fp=fp,
        entries=(
            landing.ManifestFile(
                file="jobs.parquet", remote="dia=2026-09-26/jobs.parquet"
            ),
        ),
    )

    snapshot = trends.snapshot_from_manifest(
        manifest,
        reader,
        sources_scope=["indeed"],
        label=manifest.stamp,
        workdir=tmp_path,
    )

    assert snapshot.label == "20260926T010000"
    assert snapshot.fingerprint == fp
    assert set(snapshot.completeness_by_source) == {"indeed"}
    source = snapshot.completeness_by_source["indeed"]
    assert source.total_offers == 2
    assert source.fields["title"].valid == 1
    assert source.fields["title"].completeness_pct == pytest.approx(50.0)
    assert source.fields["company"].completeness_pct == pytest.approx(100.0)


def test_snapshot_from_manifest_measures_multi_site_portals_separately(tmp_path):
    table = pa.table(
        {
            "job_id": ["1", "2", "3"],
            "job_url": ["u1", "u2", "u3"],
            "title": ["Data Engineer", "Python Dev", "Analyst"],
            "company_name": ["Acme", "Beta", "Gamma"],
            "scraped_at": ["2026-09-26"] * 3,
            "site": ["irishjobs", "irishjobs", "glassdoor"],
            "description_full": ["d1", "d2", "d3"],
        }
    )
    key = "multi_site/dia=2026-09-26/jobs_unified.parquet"
    reader = FakeReader({key: _parquet_bytes(table)})
    manifest = _manifest(
        "multi_site",
        "20260926T010000",
        fp=fingerprint.build_fingerprint(["irishjobs", "glassdoor"], []),
        entries=(
            landing.ManifestFile(
                file="jobs_unified.parquet",
                remote="dia=2026-09-26/jobs_unified.parquet",
            ),
        ),
    )

    snapshot = trends.snapshot_from_manifest(
        manifest,
        reader,
        sources_scope=["irishjobs", "glassdoor", "devitjobs"],
        label=manifest.stamp,
        workdir=tmp_path,
    )

    # `devitjobs` published no rows that day, so it is omitted (no false zero).
    assert set(snapshot.completeness_by_source) == {"irishjobs", "glassdoor"}
    assert snapshot.completeness_by_source["irishjobs"].total_offers == 2
    assert snapshot.completeness_by_source["glassdoor"].total_offers == 1


def test_load_published_completeness_skips_bad_and_missing_remote(tmp_path):
    key = "indeed/dia=2026-09-26/jobs.parquet"
    reader = FakeReader({key: _parquet_bytes(_indeed_table())})
    manifest = _manifest(
        "indeed",
        "20260926T010000",
        entries=(
            landing.ManifestFile(
                file="bad.parquet", status="bad", remote="dia=2026-09-26/bad.parquet"
            ),
            landing.ManifestFile(file="local.parquet", remote=None),
            landing.ManifestFile(
                file="jobs.parquet", remote="dia=2026-09-26/jobs.parquet"
            ),
        ),
    )

    result = trends.load_published_completeness(manifest, reader, "indeed")

    assert result is not None
    assert result.total_offers == 2


def test_load_published_completeness_returns_none_without_readable_objects(tmp_path):
    key = "indeed/dia=2026-09-26/broken.parquet"
    reader = FakeReader({key: b"not a parquet"})
    manifest = _manifest(
        "indeed",
        "20260926T010000",
        entries=(
            landing.ManifestFile(
                file="broken.parquet", remote="dia=2026-09-26/broken.parquet"
            ),
        ),
    )

    assert trends.load_published_completeness(manifest, reader, "indeed") is None


def test_load_published_completeness_propagates_remote_error(tmp_path):
    manifest = _manifest(
        "indeed",
        "20260926T010000",
        entries=(
            landing.ManifestFile(
                file="jobs.parquet", remote="dia=2026-09-26/jobs.parquet"
            ),
        ),
    )
    reader = FakeReader({})  # the object is not present

    with pytest.raises(landing.RemoteError):
        trends.load_published_completeness(manifest, reader, "indeed")


def test_load_published_completeness_cleans_its_temp_dir(monkeypatch):
    key = "indeed/dia=2026-09-26/jobs.parquet"
    reader = FakeReader({key: _parquet_bytes(_indeed_table())})
    manifest = _manifest(
        "indeed",
        "20260926T010000",
        entries=(
            landing.ManifestFile(
                file="jobs.parquet", remote="dia=2026-09-26/jobs.parquet"
            ),
        ),
    )

    created: list[tempfile.TemporaryDirectory] = []
    real = tempfile.TemporaryDirectory

    class Tracking(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", Tracking)

    result = trends.load_published_completeness(manifest, reader, "indeed")

    assert result is not None
    assert created, "se esperaba un directorio temporal propio"
    assert all(not Path(item.name).exists() for item in created)


# --------------------------------------------------------------------------
# select_history (fake Azure)
# --------------------------------------------------------------------------


def _manifest_payload(fp: dict | None) -> bytes:
    body: dict = {"schema_version": 1, "total_files": 0, "bad_files": 0, "files": []}
    if fp is not None:
        body["fingerprint"] = fp
    return json.dumps(body).encode("utf-8")


def test_select_history_returns_only_comparable_manifests():
    fp_a = _fp_indeed()
    fp_b = _fp_linkedin()
    objects = {
        "_manifests/indeed/20260920T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260921T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260922T010000.json": _manifest_payload(fp_b),
        "_manifests/indeed/20260923T010000.json": _manifest_payload(None),
        "_manifests/indeed/20260924T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260926T010000.json": _manifest_payload(fp_a),
    }
    reader = FakeReader(objects)

    result = trends.select_history(
        reader,
        current_fingerprint=fp_a,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
    )

    assert [run.label for run in result] == [
        "20260920T010000",
        "20260921T010000",
        "20260924T010000",
        "20260926T010000",
    ]


def test_select_history_respects_limit_of_five():
    fp = _fp_indeed()
    objects = {
        f"_manifests/indeed/202609{day:02d}T010000.json": _manifest_payload(fp)
        for day in range(18, 27)  # 18..26 includes the current stamp
    }
    reader = FakeReader(objects)

    result = trends.select_history(
        reader,
        current_fingerprint=fp,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
    )

    assert len(result) == 5
    assert result[-1].label == "20260926T010000"


def test_select_history_anchor_survives_when_current_manifest_is_absent():
    fp = _fp_indeed()
    reader = FakeReader(
        {"_manifests/indeed/20260925T010000.json": _manifest_payload(fp)}
    )

    result = trends.select_history(
        reader,
        current_fingerprint=fp,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
    )

    assert [run.label for run in result] == ["20260925T010000", "20260926T010000"]


# --------------------------------------------------------------------------
# summarize_history / build_trend_from_candidates (T-34; RF-7, RF-13)
# --------------------------------------------------------------------------


def _comparable_snapshots(
    count: int, fp: dict, *, label: str = "2026-09-30"
) -> tuple[trends.RunSnapshot, list[trends.RunSnapshot]]:
    """Return a ``current`` run plus ``count`` older comparable candidates."""
    current = _snapshot(
        label, fp, {"indeed": {"title": 100.0, "company": 100.0}}
    )
    candidates = [
        _snapshot(
            f"2026-09-{10 + index:02d}",
            fp,
            {"indeed": {"title": 90.0 + index, "company": 90.0}},
        )
        for index in range(count)
    ]
    return current, candidates


def test_trend_result_keeps_positional_construction():
    # The T-34 fields have defaults, so the old positional call still works.
    result = trends.TrendResult(1, False, "nota", {})

    assert result.considered == 0
    assert result.excluded_without_fingerprint == 0
    assert result.excluded_different_fingerprint == 0


def test_build_trend_from_candidates_without_previous_runs_is_not_comparable():
    current, _ = _comparable_snapshots(0, _fp_indeed())

    result = trends.build_trend_from_candidates(current, [])

    assert result.runs_used == 1
    assert result.comparable is False
    assert result.considered == 1
    assert result.excluded_without_fingerprint == 0
    assert result.excluded_different_fingerprint == 0
    assert result.note is not None
    assert "tendencia" in result.note
    assert result.sources == {}


@pytest.mark.parametrize("previous", [1, 2, 3, 4])
def test_build_trend_from_candidates_uses_every_available_run(previous):
    current, candidates = _comparable_snapshots(previous, _fp_indeed())

    result = trends.build_trend_from_candidates(current, candidates)

    assert result.runs_used == previous + 1
    assert result.comparable is True
    assert result.considered == previous + 1
    assert result.excluded_without_fingerprint == 0
    assert result.excluded_different_fingerprint == 0
    assert result.note is None
    source = result.sources["indeed"]
    assert source.runs_used == previous + 1
    assert len(source.fields["title"].values) == previous + 1


def test_build_trend_from_candidates_caps_at_five():
    current, candidates = _comparable_snapshots(8, _fp_indeed())

    result = trends.build_trend_from_candidates(current, candidates)

    assert result.considered == 9
    assert result.runs_used == trends.DEFAULT_HISTORY_LIMIT
    assert result.comparable is True
    assert len(result.sources["indeed"].fields["title"].values) == 5


def test_build_trend_from_candidates_excludes_different_fingerprint():
    fp_a = _fp_indeed()
    fp_b = _fp_linkedin()
    current = _snapshot("2026-09-30", fp_a, {"indeed": {"title": 100.0}})
    good = _snapshot("2026-09-29", fp_a, {"indeed": {"title": 90.0}})
    other = _snapshot("2026-09-28", fp_b, {"indeed": {"title": 80.0}})

    result = trends.build_trend_from_candidates(current, [good, other])

    assert result.runs_used == 2
    assert result.comparable is True
    assert result.considered == 3
    assert result.excluded_different_fingerprint == 1
    assert result.excluded_without_fingerprint == 0
    assert result.note is not None
    assert "configuraci" in result.note
    assert "cambi" in result.note
    # The run with a different fingerprint never enters the series.
    assert result.sources["indeed"].fields["title"].values == (90.0, 100.0)


def test_build_trend_from_candidates_excludes_runs_without_fingerprint():
    fp = _fp_indeed()
    current = _snapshot("2026-09-30", fp, {"indeed": {"title": 100.0}})
    good = _snapshot("2026-09-29", fp, {"indeed": {"title": 90.0}})
    old = _snapshot("2026-09-28", None, {"indeed": {"title": 80.0}})

    result = trends.build_trend_from_candidates(current, [good, old])

    assert result.runs_used == 2
    assert result.comparable is True
    assert result.excluded_without_fingerprint == 1
    assert result.excluded_different_fingerprint == 0
    assert result.note is not None
    assert "huella" in result.note
    assert result.sources["indeed"].fields["title"].values == (90.0, 100.0)


def test_build_trend_from_candidates_only_old_run_without_fingerprint():
    current = _snapshot("2026-09-30", _fp_indeed(), {"indeed": {"title": 100.0}})
    old = _snapshot("2026-09-28", None, {"indeed": {"title": 80.0}})

    result = trends.build_trend_from_candidates(current, [old])

    assert result.runs_used == 1
    assert result.comparable is False
    assert result.excluded_without_fingerprint == 1
    assert result.note is not None
    assert "tendencia" in result.note
    assert "huella" in result.note
    # No invented series from a manifest without a fingerprint.
    assert result.sources == {}


def test_summarize_history_counts_exclusions_and_combines_notes():
    fp_a = _fp_indeed()
    fp_b = _fp_linkedin()
    current = _snapshot("2026-09-30", fp_a, {"indeed": {"title": 100.0}})
    candidates = [
        _snapshot("2026-09-29", fp_a, {"indeed": {"title": 90.0}}),
        _snapshot("2026-09-28", fp_a, {"indeed": {"title": 80.0}}),
        _snapshot("2026-09-27", fp_b, {"indeed": {"title": 70.0}}),
        _snapshot("2026-09-26", None, {"indeed": {"title": 60.0}}),
    ]

    summary = trends.summarize_history(current, candidates)

    assert summary.considered == 5
    assert summary.runs_used == 3
    assert summary.comparable is True
    assert summary.excluded_different_fingerprint == 1
    assert summary.excluded_without_fingerprint == 1
    assert summary.note is not None
    assert "cambi" in summary.note
    assert "huella" in summary.note
    assert "tendencia" not in summary.note


def test_summarize_history_without_previous_runs_notes_missing_reference():
    current = _snapshot("2026-09-30", _fp_indeed(), {})

    summary = trends.summarize_history(current, [])

    assert summary.considered == 1
    assert summary.runs_used == 1
    assert summary.comparable is False
    assert summary.note is not None
    assert "tendencia" in summary.note


def test_summarize_history_keeps_insufficient_note_with_exclusions():
    current = _snapshot("2026-09-30", _fp_indeed(), {"indeed": {"title": 100.0}})
    candidates = [
        _snapshot("2026-09-28", None, {"indeed": {"title": 80.0}}),
        _snapshot("2026-09-27", _fp_linkedin(), {"indeed": {"title": 70.0}}),
    ]

    summary = trends.summarize_history(current, candidates)

    assert summary.runs_used == 1
    assert summary.comparable is False
    assert "tendencia" in summary.note
    assert "cambi" in summary.note
    assert "huella" in summary.note


def test_summarize_history_never_counts_current_twice():
    fp = _fp_indeed()
    current = _snapshot("2026-09-30", fp, {"indeed": {"title": 100.0}})

    summary = trends.summarize_history(current, [current])

    assert summary.considered == 1
    assert summary.runs_used == 1
    assert summary.excluded_without_fingerprint == 0
    assert summary.excluded_different_fingerprint == 0


# --------------------------------------------------------------------------
# select_history_summary (fake Azure; T-34)
# --------------------------------------------------------------------------


def test_select_history_summary_reports_exclusions():
    fp_a = _fp_indeed()
    fp_b = _fp_linkedin()
    objects = {
        "_manifests/indeed/20260920T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260921T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260922T010000.json": _manifest_payload(fp_b),
        "_manifests/indeed/20260923T010000.json": _manifest_payload(None),
        "_manifests/indeed/20260924T010000.json": _manifest_payload(fp_a),
        "_manifests/indeed/20260926T010000.json": _manifest_payload(fp_a),
    }

    runs, summary = trends.select_history_summary(
        FakeReader(objects),
        current_fingerprint=fp_a,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
    )

    assert [run.label for run in runs] == [
        "20260920T010000",
        "20260921T010000",
        "20260924T010000",
        "20260926T010000",
    ]
    assert summary.considered == 6
    assert summary.runs_used == 4
    assert summary.comparable is True
    assert summary.excluded_different_fingerprint == 1
    assert summary.excluded_without_fingerprint == 1
    assert summary.note is not None
    assert "cambi" in summary.note
    assert "huella" in summary.note


def test_select_history_summary_without_comparable_history_is_not_comparable():
    fp = _fp_indeed()
    reader = FakeReader(
        {"_manifests/indeed/20260926T010000.json": _manifest_payload(fp)}
    )

    runs, summary = trends.select_history_summary(
        reader,
        current_fingerprint=fp,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
    )

    assert [run.label for run in runs] == ["20260926T010000"]
    assert summary.runs_used == 1
    assert summary.comparable is False
    assert summary.note is not None
    assert "tendencia" in summary.note


def test_select_history_summary_with_limit_of_one_is_not_comparable():
    fp = _fp_indeed()
    reader = FakeReader(
        {
            "_manifests/indeed/20260925T010000.json": _manifest_payload(fp),
            "_manifests/indeed/20260926T010000.json": _manifest_payload(fp),
        }
    )

    runs, summary = trends.select_history_summary(
        reader,
        current_fingerprint=fp,
        scraper="indeed",
        label="20260926T010000",
        sources_scope=["indeed"],
        limit=1,
    )

    assert [run.label for run in runs] == ["20260926T010000"]
    assert summary.runs_used == 1
    assert summary.comparable is False
    assert summary.note is not None
    assert "tendencia" in summary.note
