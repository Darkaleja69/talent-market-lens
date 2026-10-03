"""Integration tests for the diagnostic CLI (T-40; RF-1, RF-11–RF-14).

Everything runs offline against a temporary fixture: no scrapers are executed,
no network/Azure/credentials are used and nothing in the workspace is written.
The Parquet files are written with PyArrow (already a justified dependency).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from verification import (
    fingerprint,
    landing,
    publication,
    report,
    sources,
    status,
    trends,
    verify_run,
)

PORTALS = sources.MULTI_SITE_SITES

# The analysed run window, mirrored by the general log and the artifacts.
_RUN_MTIME = datetime(2026, 9, 26, 1, 0, 0)
_PORTAL_MTIME = datetime(2026, 9, 26, 0, 25, 0)


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------


def _set_mtime(path: Path, when: datetime | None) -> None:
    moment = when if when is not None else _RUN_MTIME
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))


def _write(
    root: Path, relative: str, text: str, when: datetime | None = None
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _set_mtime(path, when)
    return path


def _write_json(
    root: Path, relative: str, payload: object, when: datetime | None = None
) -> Path:
    return _write(root, relative, json.dumps(payload), when)


def _table(columns: tuple[str, ...], rows: list[dict]) -> pa.Table:
    """Build a table with all columns present, even when there are no rows."""
    if rows:
        return pa.table(
            {column: [row.get(column) for row in rows] for column in columns}
        )
    return pa.table({column: pa.array([], type=pa.string()) for column in columns})


def _parquet_at(
    root: Path,
    relative: str,
    columns: tuple[str, ...],
    rows: list[dict],
    when: datetime | None = None,
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(columns, rows), path)
    _set_mtime(path, when)
    return path


def _parquet_bytes(columns: tuple[str, ...], rows: list[dict]) -> bytes:
    sink = pa.BufferOutputStream()
    pq.write_table(_table(columns, rows), sink)
    return sink.getvalue().to_pybytes()


# --- Column sets and valid rows per source ---------------------------------


_INDEED_COLUMNS = (
    "job_key",
    "title",
    "company",
    "description_text",
    "salary_text",
    "workplace_type",
    "location",
    "posted_date",
    "viewjob_url",
    "scraped_at",
    "search_term",
    "city_query",
    "country",
)
_LINKEDIN_COLUMNS = (
    "job_id",
    "title",
    "company_name",
    "description_full",
    "salary_raw",
    "skills",
    "work_mode",
    "location_raw",
    "posted_datetime",
    "job_url",
    "scraped_at",
    "search_role",
    "search_city",
)
_INFOJOBS_COLUMNS = (
    "id_oferta",
    "titulo",
    "empresa",
    "descripcion_snippet",
    "salario_raw",
    "modalidad",
    "ciudad",
    "fecha_publicacion",
    "url_oferta",
    "fecha_scraped",
    "keyword_buscada",
    "ciudad_buscada",
)
_MULTI_SITE_COLUMNS = (
    "job_id",
    "title",
    "company_name",
    "description_full",
    "salary_raw",
    "skills",
    "work_mode",
    "location_raw",
    "posted_datetime",
    "job_url",
    "scraped_at",
    "site",
    "search_role",
    "search_city",
)


def _indeed_rows(count: int = 3) -> list[dict]:
    # Salary is present on a single offer, so the optional field falls at
    # 33.3 % and triggers an investigation without failing Indeed (RF-10).
    return [
        {
            "job_key": f"indeed-{index}",
            "title": f"Data Engineer {index}",
            "company": "ACME Corp",
            "description_text": "A real description.",
            "salary_text": "30000" if index == 0 else None,
            "workplace_type": "Remote",
            "location": "Madrid",
            "posted_date": "2026-09-01",
            "viewjob_url": f"https://indeed.example/{index}",
            "scraped_at": "2026-09-26",
            "search_term": "data engineer",
            "city_query": "Madrid",
            "country": "España",
        }
        for index in range(count)
    ]


def _linkedin_rows(count: int = 2) -> list[dict]:
    return [
        {
            "job_id": f"linkedin-{index}",
            "title": f"Data Engineer {index}",
            "company_name": "ACME Corp",
            "description_full": "A real description.",
            "salary_raw": "30000",
            "skills": ["Python"],
            "work_mode": "Remote",
            "location_raw": "Barcelona",
            "posted_datetime": "2026-09-01",
            "job_url": f"https://linkedin.example/{index}",
            "scraped_at": "2026-09-26",
            "search_role": "data engineer",
            "search_city": "Barcelona",
        }
        for index in range(count)
    ]


def _infojobs_rows(count: int = 2) -> list[dict]:
    return [
        {
            "id_oferta": f"infojobs-{index}",
            "titulo": f"Data Engineer {index}",
            "empresa": "ACME Corp",
            "descripcion_snippet": "A real description.",
            "salario_raw": "30000",
            "modalidad": "Remoto",
            "ciudad": "Madrid",
            "fecha_publicacion": "2026-09-01",
            "url_oferta": f"https://infojobs.example/{index}",
            "fecha_scraped": "2026-09-26",
            "keyword_buscada": "data engineer",
            "ciudad_buscada": "Madrid",
        }
        for index in range(count)
    ]


def _multi_site_rows(portal: str, count: int = 2) -> list[dict]:
    return [
        {
            "job_id": f"{portal}-{index}",
            "title": f"Data Engineer {index}",
            "company_name": "ACME Corp",
            "description_full": "A real description.",
            "salary_raw": "30000",
            "skills": ["Python"],
            "work_mode": "Remote",
            "location_raw": "Dublin",
            "posted_datetime": "2026-09-01",
            "job_url": f"https://{portal}.example/{index}",
            "scraped_at": "2026-09-26",
            "site": portal,
            "search_role": "data engineer",
            "search_city": "Dublin",
        }
        for index in range(count)
    ]


# --- Whole-run fixture ------------------------------------------------------


def _write_general_log(root: Path, *, completed: bool = True) -> None:
    lines = [
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====",
        "00:00:08  [INFO]  [indeed] Lanzado PID 100 "
        "(wrapper: run_nightly_indeed.ps1)",
    ]
    if completed:
        lines.append(
            "07:05:39  [INFO]  ====  Fin pipeline. Fallos: 0  "
            "Duracion: 25534s ===="
        )
        for key in ("indeed", "linkedin", "infojobs", "multi_site"):
            lines.append(
                f"07:05:39  [INFO]    [{key}] status=ok subidos=1 rechazados=0"
            )
    _write(
        root,
        "scrapers-pipeline/logs/upload-2026-09-26.log",
        "\n".join(lines) + "\n",
    )


def _build_indeed(root: Path) -> None:
    _write(
        root,
        "indeed_jobs_scraper/output/nightly.log",
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        root,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 3, "finished_at": "2026-09-26T05:04:55"},
    )
    _parquet_at(
        root,
        "indeed_jobs_scraper/output/indeed_jobs_20260926_0045.parquet",
        _INDEED_COLUMNS,
        _indeed_rows(3),
    )


def _build_linkedin(root: Path) -> None:
    _write(
        root,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        root,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] 50 ofertas base "
        "anadidas (sin detalle aun).\n",
    )
    _write(
        root,
        "linkedin_jobs_scraper/data/last_nightly_run.txt",
        "last_run=20260926_013540 exit=0 attempts=1\n",
    )
    _parquet_at(
        root,
        "linkedin_jobs_scraper/data/output/jobs.parquet",
        _LINKEDIN_COLUMNS,
        _linkedin_rows(2),
    )


def _build_infojobs(root: Path) -> None:
    _write(
        root,
        "infojobs_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main "
        "--unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=2 incidencias=0 blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )
    _parquet_at(
        root,
        "infojobs_jobs_scraper/data/offers_20260926_003100.parquet",
        _INFOJOBS_COLUMNS,
        _infojobs_rows(2),
    )


def _build_multi_site(root: Path, *, glassdoor_rows: int = 0) -> None:
    _write_json(
        root,
        "multi_site_job_scraper/data/merged/last_run.json",
        {
            "results": {portal: 0 for portal in PORTALS},
            "outputs_merged": list(PORTALS),
            "merge_exit": 0,
            "run_at": "2026-09-26T00:57:16",
        },
    )
    for portal in PORTALS:
        count = glassdoor_rows if portal == "glassdoor" else 2
        _write(
            root,
            f"multi_site_job_scraper/data/{portal}/run.log",
            f"00:19:28 INFO {portal}: === {portal.upper()} DONE: {count} "
            f"ofertas nuevas, {count} total ===\n",
            when=_PORTAL_MTIME,
        )
        _parquet_at(
            root,
            f"multi_site_job_scraper/data/{portal}/output/jobs.parquet",
            _MULTI_SITE_COLUMNS,
            _multi_site_rows(portal, count),
        )


def _build_run_fixture(root: Path, *, glassdoor_rows: int = 0) -> Path:
    """Build a finished run with the nine sources; return the logs directory."""
    _write_general_log(root, completed=True)
    _build_indeed(root)
    _build_linkedin(root)
    _build_infojobs(root)
    _build_multi_site(root, glassdoor_rows=glassdoor_rows)
    return root / "scrapers-pipeline" / "logs"


def _fingerprint_tree(root: Path) -> dict[str, tuple[int, float]]:
    """Map every file under ``root`` to (size, mtime) for a no-write check."""
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------
# 1. Main integration
# --------------------------------------------------------------------------


def test_integration_reports_the_latest_finished_run(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    text = report.render_report(diagnostic)

    assert isinstance(diagnostic, report.DiagnosticReport)
    # Glassdoor produced zero offers, so the run is partial (RF-14).
    assert diagnostic.global_state == status.GLOBAL_PARTIAL
    assert len(diagnostic.sources) == 9

    for source in sources.all_sources():
        assert source.display_name in text, source.id

    assert "Completitud por campo:" in text
    assert "%" in text
    assert "Progreso (ofertas parseadas):" in text
    assert "Ofertas capturadas en la ejecución:" in text

    # The optional salary below 60 % on Indeed triggers the web-check section.
    assert "=== Investigación de la web real ===" in text
    assert "Búsqueda: data engineer" in text
    assert "Región: España" in text


def test_report_includes_actionable_recommendation_and_manual_check(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    text = report.render_report(diagnostic)

    assert "Cambio recomendado:" in text
    assert "Comprobación manual:" in text
    # RF-11: both sections must carry concrete guidance, not the empty defaults.
    assert "sin cambio recomendado" not in text
    assert "sin comprobación manual indicada" not in text
    assert "Revisar el parser de Indeed" in text
    assert "Abrir https://indeed.example" in text


# --------------------------------------------------------------------------
# 2. It executes no scraper and writes nothing
# --------------------------------------------------------------------------


def test_diagnostic_does_not_modify_or_create_files(tmp_path, capsys, monkeypatch):
    logs_dir = _build_run_fixture(tmp_path)
    before = _fingerprint_tree(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "el diagnóstico no debe lanzar procesos ni crear temporales"
        )

    # Any attempt to run a scraper (subprocess/os.system) or to create a remote
    # temporary directory would raise here, proving the diagnostic only reads.
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(tempfile, "mkdtemp", forbidden)

    verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    exit_code = verify_run.main(
        [
            "--projects-root",
            str(tmp_path),
            "--logs-dir",
            str(logs_dir),
            "--offline",
        ]
    )
    capsys.readouterr()

    assert exit_code == 0
    # The only allowed new file is the machine-readable result of RF-16; every
    # fixture file stays byte-identical (the tempfile guard above also blocks
    # temporaries outside the fixture).
    expected_result = "scrapers-pipeline\\logs\\diagnostic_last.json"
    after = _fingerprint_tree(tmp_path)
    assert set(after) - set(before) == {expected_result}
    for key, value in before.items():
        assert after[key] == value

    # The module neither imports nor uses subprocess nor the supervisor.
    assert not hasattr(verify_run, "subprocess")
    assert not hasattr(verify_run, "supervisor")


# --------------------------------------------------------------------------
# 3. Run still in progress
# --------------------------------------------------------------------------


def test_run_in_progress_is_inconclusive_in_spanish(tmp_path):
    _write_general_log(tmp_path, completed=False)
    logs_dir = tmp_path / "scrapers-pipeline" / "logs"

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    text = report.render_report(diagnostic)

    assert diagnostic.global_state == status.GLOBAL_INCONCLUSIVE
    assert diagnostic.sources == ()
    assert "no ha terminado" in (diagnostic.global_detail or "")
    assert "Estado global: inconcluso" in text
    assert "no ha terminado" in text


# --------------------------------------------------------------------------
# 4. No run at all
# --------------------------------------------------------------------------


def test_missing_run_is_inconclusive_without_invented_sources(tmp_path):
    logs_dir = tmp_path / "scrapers-pipeline" / "logs"  # never created

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)

    assert diagnostic.global_state == status.GLOBAL_INCONCLUSIVE
    assert diagnostic.sources == ()
    assert "no se encontró" in (diagnostic.global_detail or "")
    text = report.render_report(diagnostic)
    for source in sources.all_sources():
        assert source.display_name not in text


# --------------------------------------------------------------------------
# 5. Zero offers / missing evidence
# --------------------------------------------------------------------------


def test_zero_offers_source_is_failed_and_reported(tmp_path):
    logs_dir = _build_run_fixture(tmp_path, glassdoor_rows=0)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    glassdoor = next(s for s in diagnostic.sources if s.source == "glassdoor")
    text = report.render_report(diagnostic)

    assert glassdoor.state == status.SOURCE_FAILED
    assert glassdoor.total_offers == 0
    assert "cero ofertas capturadas en la ejecución" in text


def test_source_without_evidence_is_failed(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    for path in (tmp_path / "indeed_jobs_scraper").rglob("*"):
        if path.is_file():
            path.unlink()

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")
    text = report.render_report(diagnostic)

    assert indeed.state == status.SOURCE_FAILED
    assert "sin evidencia suficiente para confirmar la fuente" in text


def test_corrupt_parquet_is_reported_as_structural_failure(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    # The log and metadata are valid; only the Parquet is corrupt.
    corrupt = (
        tmp_path / "indeed_jobs_scraper/output/indeed_jobs_20260926_0045.parquet"
    )
    corrupt.write_bytes(b"this is not a parquet file")

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=None)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")
    text = report.render_report(diagnostic)

    assert indeed.state == status.SOURCE_FAILED
    assert indeed.failures and "structural failure" in indeed.failures[0]
    assert "fallo estructural" in text
    # The structural reason must not be hidden behind a generic missing evidence.
    assert "sin evidencia suficiente para confirmar la fuente" not in text


# --------------------------------------------------------------------------
# 6. CLI entry point
# --------------------------------------------------------------------------


def test_main_offline_prints_the_report_and_returns_zero(tmp_path, capsys):
    logs_dir = _build_run_fixture(tmp_path)

    exit_code = verify_run.main(
        [
            "--projects-root",
            str(tmp_path),
            "--logs-dir",
            str(logs_dir),
            "--offline",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Diagnóstico de la ejecución diaria" in captured.out
    assert "Indeed" in captured.out
    assert "Estado global: parcial" in captured.out


# --------------------------------------------------------------------------
# 7. Publication through an in-memory reader (no network)
# --------------------------------------------------------------------------


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


def test_publication_is_incorporated_with_an_in_memory_reader(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    current_fp = fingerprint.build_fingerprint(
        ["indeed"],
        [fingerprint.SearchDimension(source="indeed", search="data engineer")],
    )
    manifest_key = "_manifests/indeed/20260926T010000.json"
    published_key = "indeed/dia=2026-09-26/jobs.parquet"
    manifest_body = {
        "schema_version": 1,
        "total_files": 1,
        "bad_files": 0,
        "fingerprint": current_fp,
        "files": [
            {
                "file": "jobs.parquet",
                "status": "ok",
                "rows": 2,
                "remote": "dia=2026-09-26/jobs.parquet",
            }
        ],
    }
    reader = FakeReader(
        {
            manifest_key: json.dumps(manifest_body).encode("utf-8"),
            published_key: _parquet_bytes(_INDEED_COLUMNS, _indeed_rows(2)),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")
    text = report.render_report(diagnostic)

    assert indeed.obtained_offers == 3
    assert indeed.delta_offers == 2
    assert indeed.publication_state == publication.PUBLICATION_OK
    assert "Publicación:" in text
    assert "correcta" in text


# --------------------------------------------------------------------------
# 8. Run-window manifest anchoring (T-55; RF-1, RF-6, RF-8)
# --------------------------------------------------------------------------

# The analysed run window, as parsed from the general log fixture
# (00:00:05 -> 07:05:39 of 2026-09-26).
_WINDOW = (
    datetime(2026, 9, 26, 0, 0, 5),
    datetime(2026, 9, 26, 7, 5, 39),
)


def _manifest_ok(
    rows: int,
    remote: str = "dia=2026-09-26/jobs.parquet",
    *,
    fp: dict | None = None,
) -> bytes:
    """Build one accepted manifest entry for the analysed day."""
    payload: dict = {
        "schema_version": 1,
        "total_files": 1,
        "bad_files": 0,
        "files": [
            {"file": "jobs.parquet", "status": "ok", "rows": rows, "remote": remote}
        ],
    }
    if fp is not None:
        payload["fingerprint"] = fp
    return json.dumps(payload).encode("utf-8")


def test_manifest_outside_the_run_window_is_not_used_as_anchor(tmp_path):
    # The real run-2026-09-30 case: InfoJobs left no manifest that night and
    # the newest one was 19 days old. It must not anchor this run's report.
    logs_dir = _build_run_fixture(tmp_path)
    reader = FakeReader(
        {
            "_manifests/infojobs/20260911_015946.json": _manifest_ok(2),
            "infojobs/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _INFOJOBS_COLUMNS, _infojobs_rows(2)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    infojobs = next(s for s in diagnostic.sources if s.source == "infojobs")

    # InfoJobs did prepare offers locally, so the publication is pending: not
    # "ok" from the old manifest and not "not applicable".
    assert infojobs.publication_state == publication.PUBLICATION_PENDING
    assert infojobs.delta_offers is None


def test_latest_manifest_inside_the_run_window_wins(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    reader = FakeReader(
        {
            "_manifests/indeed/20260926T010000.json": _manifest_ok(
                2, "dia=2026-09-26/early.parquet"
            ),
            "_manifests/indeed/20260926_030000.json": _manifest_ok(
                3, "dia=2026-09-26/late.parquet"
            ),
            "indeed/dia=2026-09-26/early.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(2)
            ),
            "indeed/dia=2026-09-26/late.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(3)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")

    # Both stamp layouts are admitted and the later one (03:00:00) anchors.
    assert indeed.publication_state == publication.PUBLICATION_OK
    assert indeed.delta_offers == 3


def test_latest_manifest_ignores_unusable_stamps_and_unknown_windows():
    reader = FakeReader(
        {
            "_manifests/infojobs/sin-fecha.json": _manifest_ok(2),
            "_manifests/infojobs/20260926T010000.json": _manifest_ok(2),
        }
    )

    in_window = verify_run._latest_manifest(reader, "infojobs", _WINDOW)

    assert in_window is not None
    assert in_window.stamp == "20260926T010000"

    # A window that cannot be known leaves no anchor, and a listing with only
    # unparseable stamps has no candidate either.
    assert verify_run._latest_manifest(reader, "infojobs", None) is None
    unparseable = FakeReader(
        {"_manifests/infojobs/sin-fecha.json": _manifest_ok(2)}
    )
    assert verify_run._latest_manifest(unparseable, "infojobs", _WINDOW) is None


def test_unknown_window_leaves_every_scraper_without_anchor():
    reader = FakeReader(
        {"_manifests/indeed/20260926T010000.json": _manifest_ok(2)}
    )

    remote = verify_run._collect_remote_state(reader, None)

    assert all(manifest is None for manifest in remote.manifests.values())


@pytest.mark.parametrize(
    "bad_stamp",
    ["20260930_0611", "20260930_061", "20260930_06112"],
)
def test_partial_numeric_stamps_have_no_anchor(bad_stamp):
    # Regression: strptime alone back-fills partial numeric stamps as a wrong
    # time (20260930_0611 -> 06:01:01). Only exact yyyyMMdd_HHmmss /
    # yyyyMMddTHHmmss stamps may anchor (T-55).
    assert verify_run._parse_manifest_stamp(bad_stamp) is None

    window = (
        datetime(2026, 9, 30, 0, 0, 5),
        datetime(2026, 9, 30, 7, 5, 39),
    )
    reader = FakeReader(
        {f"_manifests/infojobs/{bad_stamp}.json": _manifest_ok(2)}
    )

    assert verify_run._latest_manifest(reader, "infojobs", window) is None


def test_exact_stamps_are_still_used_as_anchors():
    reader = FakeReader(
        {
            "_manifests/infojobs/20260930_061155.json": _manifest_ok(2),
            "_manifests/infojobs/20260930T071155.json": _manifest_ok(2),
        }
    )
    window = (
        datetime(2026, 9, 30, 0, 0, 5),
        datetime(2026, 9, 30, 8, 0, 0),
    )

    manifest = verify_run._latest_manifest(reader, "infojobs", window)

    assert manifest is not None
    assert manifest.stamp == "20260930T071155"
    assert verify_run._parse_manifest_stamp("20260930_061155") == datetime(
        2026, 9, 30, 6, 11, 55
    )
    assert verify_run._parse_manifest_stamp("20260930T071155") == datetime(
        2026, 9, 30, 7, 11, 55
    )


def test_source_without_run_manifest_and_without_data_is_not_applicable(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    # InfoJobs was blocked by CAPTCHA and prepared no offers that night.
    for path in (tmp_path / "infojobs_jobs_scraper" / "data").glob(
        "offers_*.parquet"
    ):
        path.unlink()
    _write(
        tmp_path,
        "infojobs_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main "
        "--unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=0 incidencias=0 blocked=True\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )
    reader = FakeReader(
        {"_manifests/infojobs/20260911_015946.json": _manifest_ok(2)}
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    infojobs = next(s for s in diagnostic.sources if s.source == "infojobs")
    text = report.render_report(diagnostic)

    assert infojobs.publication_state == publication.PUBLICATION_NOT_APPLICABLE
    assert publication.is_zero_offers_captured(infojobs.publication_state) is False
    assert "sin datos que publicar" in text
    # The absent publication never fails the source on its own: the failure
    # comes from the blocked ingestion (no data), not from publishing.
    assert infojobs.state == status.SOURCE_FAILED
    assert infojobs.failures == ("missing evidence",)


def test_source_without_run_manifest_but_with_prepared_data_is_pending(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    reader = FakeReader({})  # the landing has nothing for this run

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    infojobs = next(s for s in diagnostic.sources if s.source == "infojobs")

    # InfoJobs prepared offers but left no manifest: the upload is not visible
    # yet, so it is pending, never "not applicable".
    assert infojobs.publication_state == publication.PUBLICATION_PENDING


def test_trend_has_no_anchor_for_a_source_without_a_run_manifest(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    fp = fingerprint.build_fingerprint(["infojobs"], [])
    reader = FakeReader(
        {
            "_manifests/infojobs/20260910_010000.json": _manifest_ok(
                2, "dia=2026-09-10/jobs.parquet", fp=fp
            ),
            "_manifests/infojobs/20260911_015946.json": _manifest_ok(
                2, "dia=2026-09-11/jobs.parquet", fp=fp
            ),
            "infojobs/dia=2026-09-10/jobs.parquet": _parquet_bytes(
                _INFOJOBS_COLUMNS, _infojobs_rows(2)
            ),
            "infojobs/dia=2026-09-11/jobs.parquet": _parquet_bytes(
                _INFOJOBS_COLUMNS, _infojobs_rows(2)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    infojobs = next(s for s in diagnostic.sources if s.source == "infojobs")

    # Neither old manifest may anchor a trend for this run; without the window
    # filter these two comparable manifests would produce a false series.
    assert infojobs.trend is None


def test_trend_is_built_from_the_in_window_anchor_and_comparable_history(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    fp = fingerprint.build_fingerprint(
        ["indeed"],
        [fingerprint.SearchDimension(source="indeed", search="data engineer")],
    )
    reader = FakeReader(
        {
            "_manifests/indeed/20260925T010000.json": _manifest_ok(
                2, "dia=2026-09-25/jobs.parquet", fp=fp
            ),
            "_manifests/indeed/20260926T010000.json": _manifest_ok(
                3, "dia=2026-09-26/jobs.parquet", fp=fp
            ),
            "indeed/dia=2026-09-25/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(2)
            ),
            "indeed/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(3)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")

    # The in-window manifest is the anchor and the older comparable run is its
    # history: the window filter does not break the valid trend (RF-7).
    assert indeed.trend is not None
    assert indeed.trend.runs_used == 2


# --------------------------------------------------------------------------
# 9. Injectable obtained reader
# --------------------------------------------------------------------------


def test_obtained_reader_is_used_for_measurement(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    calls: list[str] = []

    def fake_reader(path: str) -> pa.Table:
        calls.append(path)
        return _table(_INDEED_COLUMNS, _indeed_rows(2))

    diagnostic = verify_run.run_diagnostic(
        tmp_path, logs_dir, obtained_reader=fake_reader
    )
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")

    assert calls, "se esperaba que el lector inyectado fuese usado"
    assert indeed.obtained_offers == 2


# --------------------------------------------------------------------------
# 10. Trend read failures are reported (T-58; RF-7, RF-13)
# --------------------------------------------------------------------------


class ObjectReadFailingReader(FakeReader):
    """FakeReader that lists objects but cannot download published files.

    Manifest JSON downloads keep working, so the run anchor is resolved; any
    published object download raises, modelling credentials/connectivity
    failing between the listing and the reading (T-58). ``fail_prefix`` limits
    the failure to one scraper's objects; empty means every object.
    """

    def __init__(
        self, objects: dict[str, bytes] | None = None, *, fail_prefix: str = ""
    ) -> None:
        super().__init__(objects)
        self.fail_prefix = fail_prefix

    def download(self, remote_path: str, local_path: Path) -> None:
        if not remote_path.startswith("_manifests/") and remote_path.startswith(
            self.fail_prefix
        ):
            raise landing.RemoteError("no se pudieron leer los objetos publicados")
        super().download(remote_path, local_path)


def test_trend_read_failure_is_reported_with_its_cause(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    reader = ObjectReadFailingReader(
        {
            "_manifests/indeed/20260926T010000.json": _manifest_ok(2),
            "indeed/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(2)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")
    note = diagnostic.trend_note or ""

    # The read failure is named and its cause shown; it is never presented as
    # "no comparable history" (T-58).
    assert indeed.trend is None
    assert "no se pudo calcular la tendencia de indeed" in note
    assert "no se pudieron leer los objetos publicados" in note
    assert trends.NO_HISTORY_NOTE not in note


def test_total_trend_read_failure_reports_every_cause(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    objects: dict[str, bytes] = {}
    for scraper in ("indeed", "linkedin", "infojobs", "multi_site"):
        objects[f"_manifests/{scraper}/20260926T010000.json"] = _manifest_ok(2)
        objects[f"{scraper}/dia=2026-09-26/jobs.parquet"] = _parquet_bytes(
            _INDEED_COLUMNS, _indeed_rows(2)
        )
    reader = ObjectReadFailingReader(objects)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    note = diagnostic.trend_note or ""

    assert diagnostic.sources, "la ejecución sigue siendo analizable"
    for scraper in ("indeed", "linkedin", "infojobs", "multi_site"):
        assert f"no se pudo calcular la tendencia de {scraper}" in note
    assert trends.NO_HISTORY_NOTE not in note


def test_trend_without_history_and_without_failures_keeps_the_generic_note(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    reader = FakeReader(
        {
            "_manifests/indeed/20260926T010000.json": _manifest_ok(2),
            "indeed/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(2)
            ),
        }
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    note = diagnostic.trend_note or ""

    # No read failure: the generic "no history" note is still correct (RF-7).
    assert trends.NO_HISTORY_NOTE in note
    assert "no se pudo calcular la tendencia" not in note


def test_mixed_trends_keep_the_series_and_report_the_failure(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)
    fp = fingerprint.build_fingerprint(
        ["indeed"],
        [fingerprint.SearchDimension(source="indeed", search="data engineer")],
    )
    reader = ObjectReadFailingReader(
        {
            "_manifests/indeed/20260925T010000.json": _manifest_ok(
                2, "dia=2026-09-25/jobs.parquet", fp=fp
            ),
            "_manifests/indeed/20260926T010000.json": _manifest_ok(
                3, "dia=2026-09-26/jobs.parquet", fp=fp
            ),
            "indeed/dia=2026-09-25/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(2)
            ),
            "indeed/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _INDEED_COLUMNS, _indeed_rows(3)
            ),
            "_manifests/linkedin/20260926T010000.json": _manifest_ok(2),
            "linkedin/dia=2026-09-26/jobs.parquet": _parquet_bytes(
                _LINKEDIN_COLUMNS, _linkedin_rows(2)
            ),
        },
        fail_prefix="linkedin/",
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir, reader=reader)
    indeed = next(s for s in diagnostic.sources if s.source == "indeed")
    linkedin = next(s for s in diagnostic.sources if s.source == "linkedin")
    note = diagnostic.trend_note or ""
    text = report.render_report(diagnostic)

    # Indeed's series is shown and LinkedIn's failure is mentioned, never
    # hidden behind the "no history" note (RF-7, RF-13).
    assert indeed.trend is not None
    assert indeed.trend.runs_used == 2
    assert linkedin.trend is None
    assert "no se pudo calcular la tendencia de linkedin" in note
    assert trends.NO_HISTORY_NOTE not in note
    assert "Tendencia: " in text
    assert "no se pudo calcular la tendencia de linkedin" in text


# --------------------------------------------------------------------------
# 11. Machine-readable result file (T-60; RF-16)
# --------------------------------------------------------------------------


def _main_args(tmp_path: Path, logs_dir: Path, *extra: str) -> list[str]:
    return [
        "--projects-root",
        str(tmp_path),
        "--logs-dir",
        str(logs_dir),
        "--offline",
        *extra,
    ]


def test_main_writes_the_result_file_with_output(tmp_path, capsys):
    logs_dir = _build_run_fixture(tmp_path)
    output = tmp_path / "resultados" / "diagnostic.json"

    exit_code = verify_run.main(
        _main_args(tmp_path, logs_dir, "--output", str(output))
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert output.is_file()
    raw = output.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # UTF-8 without BOM
    payload = json.loads(raw.decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["global_status"]["status"] in {"ok", "partial", "failed"}
    assert len(payload["sources"]) == 9
    # The result states which run it belongs to (T-59).
    assert payload["run"] == {
        "date": "2026-09-26",
        "started_at": "2026-09-26T00:00:05",
        "finished_at": "2026-09-26T07:05:39",
        "log_path": str(logs_dir / "upload-2026-09-26.log"),
    }
    # The screen report is unchanged and the path is announced in Spanish.
    assert "Diagnóstico de la ejecución diaria" in captured.out
    assert f"Resultado guardado en {output}" in captured.out


def test_main_writes_the_default_result_file_in_the_logs_dir(tmp_path, capsys):
    logs_dir = _build_run_fixture(tmp_path)

    exit_code = verify_run.main(_main_args(tmp_path, logs_dir))
    capsys.readouterr()

    default = logs_dir / "diagnostic_last.json"
    assert exit_code == 0
    assert default.is_file()
    payload = json.loads(default.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["sources"]


def test_main_overwrites_the_result_file_on_each_run(tmp_path, capsys):
    logs_dir = _build_run_fixture(tmp_path)
    results_dir = tmp_path / "resultados"
    results_dir.mkdir()
    output = results_dir / "diagnostic.json"
    output.write_text("contenido anterior que debe desaparecer", encoding="utf-8")
    args = _main_args(tmp_path, logs_dir, "--output", str(output))

    verify_run.main(args)
    first = json.loads(output.read_text(encoding="utf-8"))
    verify_run.main(args)
    second = json.loads(output.read_text(encoding="utf-8"))
    capsys.readouterr()

    # ``generated_at`` is the only clock-dependent field emitted by
    # ``report.diagnostic_to_dict`` (a single ``datetime.now()`` in report.py);
    # neutralise it so two runs crossing a second boundary stay comparable.
    first_generated = first.pop("generated_at")
    second_generated = second.pop("generated_at")
    assert first == second
    assert datetime.fromisoformat(first_generated)
    assert datetime.fromisoformat(second_generated)
    assert "contenido anterior" not in json.dumps(second)
    assert second["schema_version"] == 1
    # The result is overwritten in place: no sibling temporaries remain.
    assert [path.name for path in results_dir.iterdir()] == ["diagnostic.json"]


def test_main_writes_the_result_file_when_inconclusive(tmp_path, capsys):
    logs_dir = tmp_path / "logs"  # never created: no run can be found
    output = tmp_path / "diagnostic.json"

    exit_code = verify_run.main(
        _main_args(tmp_path, logs_dir, "--output", str(output))
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["global_status"]["status"] == "inconclusive"
    assert payload["global_status"]["reason"]
    assert payload["sources"] == []
    # Without an identifiable run there is no run metadata (T-59).
    assert payload["run"] is None
    assert f"Resultado guardado en {output}" in captured.out
