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

from verification import (
    fingerprint,
    landing,
    publication,
    report,
    sources,
    status,
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
    # Nothing under the fixture changed and nothing was created anywhere (the
    # tempfile guard above also blocks temporaries outside the fixture).
    assert _fingerprint_tree(tmp_path) == before

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
# 8. Injectable obtained reader
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
