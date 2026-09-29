"""Integration tests of evidence, field quality and thresholds (T-41).

Covers RF-1-RF-5, RF-13 and RF-14 end to end through
``verification.verify_run.run_diagnostic`` over a temporary nine-source
fixture: run selection, per-source parsing/evidence, per-field completeness
counts, duplicate handling and the integrated thresholds/global states.

Everything is offline: no scraper is executed, no network/Azure/credentials
are used and the workspace is never written. The Parquet files are built with
PyArrow (a dependency already justified in the plan, section 6.3).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from verification import report, sources, status, verify_run

PORTALS = sources.MULTI_SITE_SITES

# The analysed run window: the general log opens at 00:00:05 and closes at
# 07:05:39 local time, so every artifact below is stamped inside it.
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
    """Build a table with every column present, even with no rows."""
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


# --- Column sets (same contract as test_verify_run) -------------------------


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


# --- Rows per source --------------------------------------------------------


def _indeed_row(index: int) -> dict:
    # Salary is valid on the first offer, invalid on the second and absent on
    # the rest, so one source exercises all three field statuses.
    return {
        "job_key": f"indeed-{index}",
        "title": f"Data Engineer {index}",
        "company": "ACME Corp",
        "description_text": "A real description.",
        "salary_text": (
            "30000" if index == 0 else ("negotiable" if index == 1 else None)
        ),
        "workplace_type": "Remote",
        "location": "Madrid",
        "posted_date": "2026-09-01",
        "viewjob_url": f"https://indeed.example/{index}",
        "scraped_at": "2026-09-26",
        "search_term": "data engineer",
        "city_query": "Madrid",
        "country": "España",
    }


def _indeed_rows(count: int = 3) -> list[dict]:
    return [_indeed_row(index) for index in range(count)]


def _indeed_rows_with_title(valid: int, total: int) -> list[dict]:
    """All fields valid except ``title``, missing on the offers past ``valid``."""
    rows = []
    for index in range(total):
        row = _indeed_row(index)
        if index >= valid:
            row["title"] = None
        rows.append(row)
    return rows


def _indeed_rows_with_salary(valid: int, total: int) -> list[dict]:
    """All required fields valid; ``salary`` missing past ``valid``."""
    rows = []
    for index in range(total):
        row = _indeed_row(index)
        row["salary_text"] = "30000" if index < valid else None
        rows.append(row)
    return rows


def _linkedin_row(index: int) -> dict:
    return {
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


def _linkedin_rows(count: int = 2) -> list[dict]:
    return [_linkedin_row(index) for index in range(count)]


def _infojobs_row(index: int) -> dict:
    return {
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


def _infojobs_rows(count: int = 2) -> list[dict]:
    return [_infojobs_row(index) for index in range(count)]


def _multi_site_row(portal: str, index: int) -> dict:
    return {
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


def _multi_site_rows(portal: str, count: int = 2) -> list[dict]:
    return [_multi_site_row(portal, index) for index in range(count)]


def _with_duplicate(rows: list[dict]) -> list[dict]:
    """Append a copy of the first row to exercise key-based deduplication."""
    return [*rows, dict(rows[0])]


def _default_portal_rows() -> dict[str, list[dict]]:
    return {portal: _multi_site_rows(portal, 2) for portal in PORTALS}


# --- Whole-run fixtures -----------------------------------------------------


def _write_general_log(root: Path, *, earlier_run: bool = False) -> None:
    lines: list[str] = []
    if earlier_run:
        # An earlier, finished run of the same day that left no artifacts.
        lines.extend(
            [
                "00:00:05  [INFO]  ====  Inicio pipeline scrapers  "
                "(2026-09-26) ====",
                "00:10:00  [INFO]  ====  Fin pipeline. Fallos: 9  "
                "Duracion: 595s ====",
            ]
        )
    start = "00:11:00" if earlier_run else "00:00:05"
    lines.extend(
        [
            f"{start}  [INFO]  ====  Inicio pipeline scrapers  "
            "(2026-09-26) ====",
            f"{start}  [INFO]  [indeed] Lanzado PID 100 "
            "(wrapper: run_nightly_indeed.ps1)",
            "07:05:39  [INFO]  ====  Fin pipeline. Fallos: 0  "
            "Duracion: 25534s ====",
        ]
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


def _build_indeed(
    root: Path, rows: list[dict], *, total_unique: int | None = None
) -> None:
    count = len(rows) if total_unique is None else total_unique
    _write(
        root,
        "indeed_jobs_scraper/output/nightly.log",
        "2026-09-26 00:12:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:12:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        root,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": count, "finished_at": "2026-09-26T05:04:55"},
    )
    _parquet_at(
        root,
        "indeed_jobs_scraper/output/indeed_jobs_20260926_0045.parquet",
        _INDEED_COLUMNS,
        rows,
    )


def _build_linkedin(root: Path, rows: list[dict]) -> None:
    _write(
        root,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:12:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:12:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        root,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        f"00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] {len(rows)} "
        "ofertas base anadidas (sin detalle aun).\n",
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
        rows,
    )


def _build_infojobs(root: Path, rows: list[dict]) -> None:
    _write(
        root,
        "infojobs_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:12:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:12:13 Intento 1/3 - lanzando python -m scraper.main "
        "--unattended ...\n"
        f"2026-09-26 00:31:38 RESULT: total={len(rows)} incidencias=0 "
        "blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )
    _parquet_at(
        root,
        "infojobs_jobs_scraper/data/offers_20260926_003100.parquet",
        _INFOJOBS_COLUMNS,
        rows,
    )


def _build_multi_site(
    root: Path, rows_by_portal: dict[str, list[dict]] | None
) -> None:
    portal_rows = {**_default_portal_rows(), **(rows_by_portal or {})}
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
        rows = portal_rows.get(portal, [])
        _write(
            root,
            f"multi_site_job_scraper/data/{portal}/run.log",
            f"00:19:28 INFO {portal}: === {portal.upper()} DONE: {len(rows)} "
            f"ofertas nuevas, {len(rows)} total ===\n",
            when=_PORTAL_MTIME,
        )
        _parquet_at(
            root,
            f"multi_site_job_scraper/data/{portal}/output/jobs.parquet",
            _MULTI_SITE_COLUMNS,
            rows,
        )


def _build_run_fixture(
    root: Path,
    *,
    indeed_rows: list[dict] | None = None,
    linkedin_rows: list[dict] | None = None,
    infojobs_rows: list[dict] | None = None,
    portal_rows: dict[str, list[dict]] | None = None,
    earlier_run: bool = False,
) -> Path:
    """Build a finished nine-source run and return its logs directory."""
    _write_general_log(root, earlier_run=earlier_run)
    _build_indeed(root, _indeed_rows() if indeed_rows is None else indeed_rows)
    _build_linkedin(
        root, _linkedin_rows() if linkedin_rows is None else linkedin_rows
    )
    _build_infojobs(
        root, _infojobs_rows() if infojobs_rows is None else infojobs_rows
    )
    _build_multi_site(root, portal_rows)
    return root / "scrapers-pipeline" / "logs"


def _build_empty_run(root: Path) -> Path:
    """Build a finished run without any source artifact (missing evidence)."""
    _write_general_log(root)
    return root / "scrapers-pipeline" / "logs"


# --- Report helpers ---------------------------------------------------------


def _report_by_source(
    diagnostic: report.DiagnosticReport, source_id: str
) -> report.SourceReport:
    return next(item for item in diagnostic.sources if item.source == source_id)


def _field(source_report: report.SourceReport, name: str) -> report.FieldReport:
    return next(item for item in source_report.fields if item.field == name)


def _investigated_fields(
    diagnostic: report.DiagnosticReport, source_id: str
) -> set[str | None]:
    return {
        outcome.context.field
        for outcome in diagnostic.investigations
        if outcome.context.source == source_id
    }


# --------------------------------------------------------------------------
# RUN SELECTION (RF-1, RF-13)
# --------------------------------------------------------------------------


def test_diagnostic_selects_the_latest_finished_run_of_the_day(tmp_path):
    logs_dir = _build_run_fixture(tmp_path, earlier_run=True)
    # The earlier, finished run left its own Indeed metadata behind; the
    # diagnostic must not attribute it to the analysed (later) run.
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0005.json",
        {"total_unique": 1, "finished_at": "2026-09-26T00:05:00"},
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)
    indeed = _report_by_source(diagnostic, "indeed")

    # The later run (00:11 -> 07:05) won, and its own metadata (3 offers), not
    # the earlier run's (1 offer), is the evidence used.
    assert diagnostic.global_state == status.GLOBAL_CORRECT
    assert indeed.offers_current_run == 3
    assert indeed.total_offers == 3


# --------------------------------------------------------------------------
# PARSING AND EVIDENCE (RF-1-RF-3)
# --------------------------------------------------------------------------


def test_nine_sources_are_reconciled_with_their_states(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)

    # All nine independent sources are recognised in catalog order.
    assert len(diagnostic.sources) == 9
    assert tuple(item.source for item in diagnostic.sources) == sources.source_ids()
    assert diagnostic.global_state == status.GLOBAL_CORRECT

    for source_report in diagnostic.sources:
        assert source_report.state in {
            status.SOURCE_CORRECT,
            status.SOURCE_FAILED,
        }
        assert source_report.total_offers is not None
        assert source_report.total_offers > 0
        assert source_report.fields, source_report.source


# --------------------------------------------------------------------------
# FIELD COMPLETENESS (RF-4, RF-5)
# --------------------------------------------------------------------------


def test_field_completeness_reports_valid_absent_invalid_and_pct(tmp_path):
    logs_dir = _build_run_fixture(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.total_offers == 3
    title = _field(indeed, "title")
    assert (title.valid, title.absent, title.invalid) == (3, 0, 0)
    assert title.completeness_pct == 100.0
    # One valid, one invalid ("negotiable") and one absent salary.
    salary = _field(indeed, "salary")
    assert (salary.valid, salary.absent, salary.invalid) == (1, 1, 1)
    assert round(salary.completeness_pct, 1) == 33.3
    # Indeed does not publish skills at all: absent, never invalid.
    skills = _field(indeed, "skills")
    assert (skills.valid, skills.absent, skills.invalid) == (0, 3, 0)
    assert skills.completeness_pct == 0.0

    linkedin = _report_by_source(diagnostic, "linkedin")
    assert _field(linkedin, "description").valid == 2
    assert _field(linkedin, "title").completeness_pct == 100.0

    infojobs = _report_by_source(diagnostic, "infojobs")
    assert _field(infojobs, "company").valid == 2
    assert _field(infojobs, "description").valid == 2

    # A Multi-site portal is measured on its own rows as an independent source.
    irishjobs = _report_by_source(diagnostic, "irishjobs")
    assert _field(irishjobs, "title").valid == 2
    assert _field(irishjobs, "work_mode").valid == 2


# --------------------------------------------------------------------------
# DUPLICATES (RF-4)
# --------------------------------------------------------------------------


def test_duplicate_keys_count_as_unique_offers(tmp_path):
    logs_dir = _build_run_fixture(
        tmp_path,
        indeed_rows=_with_duplicate(_indeed_rows(2)),
        linkedin_rows=_with_duplicate(_linkedin_rows(2)),
        infojobs_rows=_with_duplicate(_infojobs_rows(2)),
        portal_rows={"irishjobs": _with_duplicate(_multi_site_rows("irishjobs", 2))},
    )

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)

    # job_key (Indeed): three raw rows but two unique offers.
    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.total_offers == 2
    assert indeed.offers_current_run == 3  # raw run counter is not the denominator
    assert _field(indeed, "title").total == 2

    # job_id (LinkedIn and a Multi-site portal) and id_oferta (InfoJobs).
    assert _report_by_source(diagnostic, "linkedin").total_offers == 2
    assert _report_by_source(diagnostic, "infojobs").total_offers == 2
    assert _report_by_source(diagnostic, "irishjobs").total_offers == 2


# --------------------------------------------------------------------------
# INTEGRATED THRESHOLDS (RF-3, RF-10)
# --------------------------------------------------------------------------


def _diagnose_indeed(tmp_path, rows: list[dict]) -> report.DiagnosticReport:
    logs_dir = _build_run_fixture(tmp_path, indeed_rows=rows)
    return verify_run.run_diagnostic(tmp_path, logs_dir)


def test_required_field_at_89_9_percent_fails_the_source(tmp_path):
    diagnostic = _diagnose_indeed(
        tmp_path, _indeed_rows_with_title(899, 1000)
    )

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.state == status.SOURCE_FAILED
    assert indeed.failures
    assert "required field 'title'" in indeed.failures[0]
    assert _field(indeed, "title").completeness_pct < status.REQUIRED_FAIL_PCT


def test_required_field_at_90_percent_is_an_incident_without_failure(tmp_path):
    diagnostic = _diagnose_indeed(
        tmp_path, _indeed_rows_with_title(900, 1000)
    )

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.state == status.SOURCE_CORRECT
    assert indeed.incidents == ("title",)
    assert "title" in _investigated_fields(diagnostic, "indeed")


def test_required_field_at_100_percent_has_no_incident_or_investigation(tmp_path):
    diagnostic = _diagnose_indeed(
        tmp_path, _indeed_rows_with_title(1000, 1000)
    )

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.state == status.SOURCE_CORRECT
    assert indeed.incidents == ()
    assert "title" not in _investigated_fields(diagnostic, "indeed")


def test_optional_field_at_60_percent_triggers_investigation_without_failure(
    tmp_path,
):
    diagnostic = _diagnose_indeed(
        tmp_path, _indeed_rows_with_salary(600, 1000)
    )

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.state == status.SOURCE_CORRECT
    assert indeed.failures == ()
    assert "salary" in _investigated_fields(diagnostic, "indeed")


def test_optional_field_above_60_percent_does_not_trigger_investigation(tmp_path):
    diagnostic = _diagnose_indeed(
        tmp_path, _indeed_rows_with_salary(601, 1000)
    )

    indeed = _report_by_source(diagnostic, "indeed")
    assert indeed.state == status.SOURCE_CORRECT
    assert indeed.failures == ()
    assert "salary" not in _investigated_fields(diagnostic, "indeed")


# --------------------------------------------------------------------------
# GLOBAL STATES AND ZERO OFFERS (RF-3, RF-13, RF-14)
# --------------------------------------------------------------------------


def test_global_partial_when_a_source_fails(tmp_path):
    # Glassdoor finishes without offers: it fails on its own while the other
    # eight sources stay correct (RF-2, RF-14).
    logs_dir = _build_run_fixture(tmp_path, portal_rows={"glassdoor": []})

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)
    glassdoor = _report_by_source(diagnostic, "glassdoor")

    assert glassdoor.total_offers == 0
    assert glassdoor.state == status.SOURCE_FAILED
    assert glassdoor.failures == ("zero offers",)
    assert diagnostic.global_state == status.GLOBAL_PARTIAL


def test_global_failed_when_every_source_lacks_evidence(tmp_path):
    logs_dir = _build_empty_run(tmp_path)

    diagnostic = verify_run.run_diagnostic(tmp_path, logs_dir)

    assert len(diagnostic.sources) == 9
    assert all(
        item.state == status.SOURCE_FAILED for item in diagnostic.sources
    )
    assert diagnostic.global_state == status.GLOBAL_FAILED
