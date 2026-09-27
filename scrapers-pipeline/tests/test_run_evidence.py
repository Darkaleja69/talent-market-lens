"""Tests for run-evidence collection (T-06..T-12; RF-1, RF-2, RF-3, RF-13, RF-14).

Every fixture is synthetic and built under pytest's ``tmp_path``: no real run
data, no network and no Parquet reading are involved.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from verification import run_evidence as re_mod
from verification import sources


# --------------------------------------------------------------------------
# Fixture helpers
# --------------------------------------------------------------------------


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(root: Path, relative: str, payload: object) -> Path:
    return _write(root, relative, json.dumps(payload))


def _summary(
    date_str: str = "2026-09-26",
    start: str = "2026-09-26T00:00:05",
    end: str = "2026-09-26T07:05:39",
    completed: bool = True,
) -> re_mod.PipelineSummary:
    return re_mod.PipelineSummary(
        log_path="fixture.log",
        date=date_str,
        started_at=start,
        finished_at=end if completed else None,
        completed=completed,
        global_failures=0,
        duration_seconds=1,
        statuses={},
        errors=(),
        warnings=(),
    )


def _touch(path: Path, when: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    stamp = when.timestamp()
    import os

    os.utime(path, (stamp, stamp))


def _write_at(root: Path, relative: str, content: str, when: datetime) -> Path:
    """Write a file and force its mtime so it falls (or not) in the run window."""
    path = _write(root, relative, content)
    stamp = when.timestamp()
    import os

    os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------
# T-06 / T-12: general pipeline log parsing and run discovery
# --------------------------------------------------------------------------

COMPLETE_LOG = """\
00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====
00:00:08  [INFO]  [indeed] Lanzado PID 9348 (wrapper: run_nightly_indeed.ps1)
00:54:44  [WARN]  [infojobs] Scraper fallo (exit -1). Se validan SOLO los ficheros validos...
00:54:45  [WARN]  [infojobs] No se generaron archivos .parquet en este run. Se omite subida.
01:00:00  [ERROR]  Something bad happened
07:05:39  [INFO]  ====  Fin pipeline. Fallos: 2  Duracion: 25535s ====
07:05:39  [INFO]    [infojobs] status=no_data subidos=0 rechazados=0
07:05:39  [INFO]    [linkedin] status=ok subidos=1 rechazados=0
07:05:39  [INFO]    [indeed] status=ok subidos=3 rechazados=2
07:05:39  [INFO]    [multi_site] status=ok subidos=1 rechazados=0
"""


def test_T06_parse_complete_pipeline_log(tmp_path):
    log = _write(tmp_path, "logs/upload-2026-09-26.log", COMPLETE_LOG)

    summary = re_mod.parse_pipeline_log(log)

    assert summary.date == "2026-09-26"
    assert summary.started_at == "2026-09-26T00:00:05"
    assert summary.finished_at == "2026-09-26T07:05:39"
    assert summary.completed is True
    assert summary.global_failures == 2
    assert summary.duration_seconds == 25535
    assert summary.errors == ("Something bad happened",)
    assert len(summary.warnings) == 2

    assert summary.statuses["indeed"].status == "ok"
    assert summary.statuses["indeed"].uploaded == 3
    assert summary.statuses["indeed"].rejected == 2
    assert summary.statuses["infojobs"].status == "no_data"
    assert summary.statuses["linkedin"].uploaded == 1
    assert summary.statuses["multi_site"].status == "ok"


def test_T06_truncated_log_is_not_completed(tmp_path):
    # The log exists and has an Inicio line, but there is no Fin line.
    truncated = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "00:05:00  [INFO]  [indeed] status=ok subidos=3 rechazados=0\n"
    )
    log = _write(tmp_path, "logs/upload-2026-09-26.log", truncated)

    summary = re_mod.parse_pipeline_log(log)

    assert summary.completed is False
    assert summary.finished_at is None
    # Existence of the log alone never implies success, but the parsed
    # counters are still available.
    assert summary.statuses["indeed"].uploaded == 3


def test_T06_parse_last_block_only(tmp_path):
    text = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "01:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
        "02:00:00  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "03:00:00  [INFO]  ====  Fin pipeline. Fallos: 1  Duracion: 200s ====\n"
    )
    log = _write(tmp_path, "logs/upload-2026-09-26.log", text)

    summary = re_mod.parse_pipeline_log(log)

    assert summary.started_at == "2026-09-26T02:00:00"
    assert summary.finished_at == "2026-09-26T03:00:00"
    assert summary.global_failures == 1


def test_T12_discover_runs_returns_one_per_block(tmp_path):
    text = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-25) ====\n"
        "01:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
    )
    _write(tmp_path, "logs/upload-2026-09-25.log", text)
    text2 = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "00:30:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
        "02:00:00  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "03:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 200s ====\n"
    )
    _write(tmp_path, "logs/upload-2026-09-26.log", text2)
    # A non-standard log must be ignored.
    _write(tmp_path, "logs/upload-multi_site-2026-09-26.log", text2)

    runs = re_mod.discover_runs(tmp_path / "logs")

    assert [run.date for run in runs] == ["2026-09-25", "2026-09-26", "2026-09-26"]
    assert runs[-1].started_at == "2026-09-26T02:00:00"


def test_T12_select_last_run_prefers_last_finished_of_the_day(tmp_path):
    text = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "01:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
        "02:00:00  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "03:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 200s ====\n"
    )
    _write(tmp_path, "logs/upload-2026-09-26.log", text)

    selected = re_mod.select_last_run(tmp_path / "logs")

    assert selected is not None
    assert selected.started_at == "2026-09-26T02:00:00"


def test_T12_truncated_run_is_never_selected(tmp_path):
    text = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "01:00:00  [INFO]  ====  Fin pipeline. Fallos: 0  Duracion: 100s ====\n"
        "02:00:00  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "02:30:00  [INFO]  [indeed] Aun corriendo...\n"
    )
    _write(tmp_path, "logs/upload-2026-09-26.log", text)

    selected = re_mod.select_last_run(tmp_path / "logs")

    assert selected is not None
    assert selected.started_at == "2026-09-26T00:00:05"


def test_T12_no_run_when_no_logs(tmp_path):
    assert re_mod.select_last_run(tmp_path / "logs") is None


def test_T11_last_run_without_offers_is_still_selected(tmp_path):
    # A finished run where every source produced no data is still selectable.
    text = (
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "07:00:00  [INFO]  ====  Fin pipeline. Fallos: 4  Duracion: 100s ====\n"
        "07:00:00  [INFO]    [indeed] status=no_data subidos=0 rechazados=0\n"
        "07:00:00  [INFO]    [linkedin] status=no_data subidos=0 rechazados=0\n"
    )
    _write(tmp_path, "logs/upload-2026-09-26.log", text)

    selected = re_mod.select_last_run(tmp_path / "logs")

    assert selected is not None
    assert selected.completed is True
    assert selected.statuses["indeed"].uploaded == 0


# --------------------------------------------------------------------------
# T-07: Indeed
# --------------------------------------------------------------------------


def _indeed_log(root: Path, tail: str) -> None:
    _write(root, "indeed_jobs_scraper/output/nightly.log", tail)


def test_T07_indeed_ok_reads_run_scoped_metadata(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 101, "finished_at": "2026-09-26T05:04:55"},
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.offers_current_run == 101
    assert evidence.attempt == 1
    assert evidence.attempts == 2


def test_T07_indeed_captcha_is_blocked(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:08:14   STDERR: [!] CAPTCHA/CHALLENGE DETECTADO en ES/Madrid.\n"
        "2026-09-26 00:09:14 === FIN RUN NOCTURNA INDEED exit=-1 ===\n",
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_BLOCKED


def test_T07_indeed_technical_error(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 01:13:05 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 01:13:07 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 01:39:08 Fallo tecnico detectado. Stderr (ultimas lineas):\n"
        "2026-09-26 01:44:08 === FIN RUN NOCTURNA INDEED exit=-1 ===\n",
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_ERROR


def test_T07_indeed_finished_without_offers_is_empty(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 0, "finished_at": "2026-09-26T05:04:55"},
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_EMPTY
    assert evidence.offers_current_run == 0


def test_T07_indeed_old_snapshot_is_stale_and_not_counted(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 50, "finished_at": "2026-09-26T05:04:55"},
    )
    # A snapshot dated the previous night, before the run started.
    _touch(
        tmp_path / "indeed_jobs_scraper/output/indeed_jobs_20260925_2330.parquet",
        datetime(2026, 9, 25, 23, 30),
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.snapshot_stale is True
    # The stale snapshot is never counted as offers of the current run.
    assert evidence.offers_current_run == 50


def test_T07_indeed_ignores_later_truncated_block(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 07:05:14 [IE] Scrape completado detectado en stdout aunque "
        "exit=-1. Se considera OK.\n"
        "2026-09-26 07:05:14 === FIN RUN NOCTURNA INDEED exit=0 ===\n"
        "2026-09-27 00:00:17 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-27 00:00:20 [NL] Intento 1/2 - lanzando python main.py --country NL ...\n",
    )
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 77, "finished_at": "2026-09-26T05:04:55"},
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.offers_current_run == 77


def test_T07_indeed_missing_log_is_no_evidence(tmp_path):
    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_NO_EVIDENCE


# --------------------------------------------------------------------------
# T-08: LinkedIn
# --------------------------------------------------------------------------


def test_T08_linkedin_run_counter_differs_from_snapshot(tmp_path):
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 00:30:01 Intento 2/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Intento 2 finalizado exit=-1 tiempo=66m37s\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt2.log",
        "00:38:13 INFO src.store: --append: 7039 ofertas cargadas de jobs.csv "
        "(total en store: 7039)\n"
        "00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] 50 ofertas base "
        "anadidas (sin detalle aun).\n"
        "00:48:49 INFO linkedin_scraper: [Data Engineer/Barcelona] 60 ofertas base "
        "anadidas (sin detalle aun).\n"
        "00:52:47 INFO src.store: Parquet escrito: data\\output\\jobs.parquet "
        "(7200 filas, esquema tipado)\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/last_nightly_run.txt",
        "last_run=20260926_013540 exit=0 attempts=2\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.attempt == 2
    assert evidence.attempts == 5
    # Offers captured during the run (base cards) are not the accumulated total.
    assert evidence.offers_current_run == 110
    assert evidence.offers_snapshot == 7200
    assert evidence.offers_current_run != evidence.offers_snapshot


def test_T08_linkedin_snapshot_is_never_the_run_counter(tmp_path):
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Resumen final detectado en stdout aunque exit=-1. "
        "Se considera run completada (exit 0).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "00:38:13 INFO src.store: --append: 5000 ofertas cargadas (total en store: 5000)\n"
        "00:52:47 INFO src.store: Parquet escrito: data\\output\\jobs.parquet "
        "(5000 filas, esquema tipado)\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    # No cards were added this run: it is empty, even though the snapshot has 5000.
    assert evidence.outcome == re_mod.OUTCOME_EMPTY
    assert evidence.offers_current_run == 0
    assert evidence.offers_snapshot == 5000


def test_T08_linkedin_ignores_later_truncated_block(tmp_path):
    # The next night's run truncates the log; the analysed run's own block must
    # still be selected.
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n"
        "2026-09-27 00:00:18 === INICIO RUN NOCTURNA ===\n"
        "2026-09-27 00:00:18 Intento 1/5 - lanzando scraper con --append...\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] 12 ofertas base "
        "anadidas (sin detalle aun).\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.offers_current_run == 12


def test_T08_linkedin_captcha_is_blocked(tmp_path):
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 00:10:00   STDERR: challenge CAPTCHA detected.\n"
        "2026-09-26 00:11:00 === FIN RUN NOCTURNA exit=-1 ===\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_BLOCKED


def test_T08_linkedin_error_on_nonzero_exit(tmp_path):
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 00:10:00 Stderr (ultimas 15 lineas):\n"
        "2026-09-26 00:11:00 === FIN RUN NOCTURNA exit=-1 ===\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_ERROR


# --------------------------------------------------------------------------
# T-09: InfoJobs
# --------------------------------------------------------------------------


def _infojobs_log(root: Path, tail: str) -> None:
    _write(root, "infojobs_jobs_scraper/data/run_nightly.log", tail)


def test_T09_infojobs_result_ok(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:31 Intento 1 finalizado exit=0 tiempo=31m3s\n"
        "2026-09-26 00:31:38 RESULT: total=42 incidencias=0 blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.offers_current_run == 42
    assert evidence.attempt == 1
    assert evidence.attempts == 3


def test_T09_infojobs_zero_offers_is_empty(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=0 incidencias=1 blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_EMPTY
    assert evidence.offers_current_run == 0


def test_T09_infojobs_captcha_is_blocked(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=0 incidencias=1 blocked=True\n"
        "2026-09-26 00:31:38 BLOQUEO por CAPTCHA (exit=-1, total=0).\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=-1 ===\n",
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_BLOCKED


def test_T09_infojobs_failure_without_result(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:38 Fallo sin RESULT (exit=-1). Stderr:\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=-1 ===\n",
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_ERROR


def test_T09_infojobs_dated_output_from_current_run_is_fresh(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=5 incidencias=0 blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )
    _touch(
        tmp_path / "infojobs_jobs_scraper/data/offers_20260926_003100.parquet",
        datetime(2026, 9, 26, 0, 31),
    )
    _touch(
        tmp_path / "infojobs_jobs_scraper/data/offers_20260925_230000.parquet",
        datetime(2026, 9, 25, 23, 0),
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.snapshot_stale is False


def test_T09_infojobs_only_stale_output_is_flagged(tmp_path):
    _infojobs_log(
        tmp_path,
        "2026-09-26 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-26 00:00:13 Intento 1/3 - lanzando python -m scraper.main --unattended ...\n"
        "2026-09-26 00:31:38 RESULT: total=5 incidencias=0 blocked=False\n"
        "2026-09-26 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )
    _touch(
        tmp_path / "infojobs_jobs_scraper/data/offers_20260925_230000.parquet",
        datetime(2026, 9, 25, 23, 0),
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.snapshot_stale is True


# --------------------------------------------------------------------------
# T-10: Multi-site
# --------------------------------------------------------------------------

PORTALS = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)


def _multi_site_last_run(root: Path, results: dict, outputs: list, merge_exit: int = 0):
    _write_json(
        root,
        "multi_site_job_scraper/data/merged/last_run.json",
        {
            "merge_mode": "parcial",
            "results": results,
            "run_at": "2026-09-26T00:57:16",
            "global_exit": 1 if any(v != 0 for v in results.values()) else 0,
            "merge_exit": merge_exit,
            "outputs_merged": outputs,
            "merge_ran": True,
            "per_scraper_timeout_min": 1080,
        },
    )


def test_T10_multi_site_six_separate_states(tmp_path):
    results = {portal: 0 for portal in PORTALS}
    results["glassdoor"] = 1
    # The merge succeeded even though Glassdoor failed.
    _multi_site_last_run(
        tmp_path,
        results,
        outputs=[p for p in PORTALS if p != "glassdoor"],
        merge_exit=0,
    )
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: === DEVITJOBS DONE: 13 nuevas, 13 total, 13 detalles ===\n",
        datetime(2026, 9, 26, 0, 19, 28),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert tuple(runs) == PORTALS
    assert runs["glassdoor"].outcome == re_mod.OUTCOME_ERROR
    assert "exit=1" in (runs["glassdoor"].error or "")
    for portal in PORTALS:
        if portal != "glassdoor":
            assert runs[portal].outcome == re_mod.OUTCOME_OK
    assert runs["devitjobs"].offers_current_run == 13


def test_T10_multi_site_timeout_portal(tmp_path):
    results = {portal: 0 for portal in PORTALS}
    results["nvb"] = "timeout"
    _multi_site_last_run(tmp_path, results, outputs=[p for p in PORTALS if p != "nvb"])

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["nvb"].outcome == re_mod.OUTCOME_ERROR
    assert runs["nvb"].error == "portal timeout"


def test_T10_multi_site_without_last_run_is_no_evidence(tmp_path):
    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert set(runs) == set(PORTALS)
    assert all(r.outcome == re_mod.OUTCOME_NO_EVIDENCE for r in runs.values())


def test_T10_multi_site_reads_last_run_with_utf8_bom(tmp_path):
    # PowerShell writes last_run.json with a UTF-8 BOM.
    import codecs

    path = tmp_path / "multi_site_job_scraper/data/merged/last_run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "results": {portal: 0 for portal in PORTALS},
            "outputs_merged": list(PORTALS),
            "merge_exit": 0,
            "run_at": "2026-09-26T00:57:16",
        }
    ).encode("utf-8")
    path.write_bytes(codecs.BOM_UTF8 + payload)

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert all(r.outcome == re_mod.OUTCOME_OK for r in runs.values())


def test_T10_multi_site_real_done_formats(tmp_path):
    # Real per-portal DONE formats differ: "N nuevas" and "N ofertas nuevas".
    _multi_site_last_run(
        tmp_path,
        {portal: 0 for portal in PORTALS},
        outputs=list(PORTALS),
    )
    done_lines = {
        "irishjobs": "00:20:00 INFO irishjobs: === IRISHJOBS DONE: 21 ofertas nuevas, "
        "21 total, 21 detalles ===",
        "stepstone_nl": "00:21:00 INFO stepstone_nl: === STEPSTONE_NL DONE: 9 nuevas, "
        "9 total, 9 detalles ===",
        "devitjobs": "00:22:00 INFO devitjobs: === DEVITJOBS DONE: 13 nuevas, "
        "13 total, 13 detalles ===",
        "nvb": "00:23:00 INFO nvb: === NVB DONE: 4 nuevas, 4 total, 4 detalles ===",
        "jobs_ch": "00:24:00 INFO jobs_ch: === JOBS.CH DONE: 7 ofertas nuevas, "
        "7 total, 7 detalles ===",
        "glassdoor": "00:25:00 INFO glassdoor: === GLASSDOOR DONE: 5 ofertas nuevas, "
        "5 total, 5 detalles ===",
    }
    expected = {
        "irishjobs": 21,
        "stepstone_nl": 9,
        "devitjobs": 13,
        "nvb": 4,
        "jobs_ch": 7,
        "glassdoor": 5,
    }
    for portal, line in done_lines.items():
        _write_at(
            tmp_path,
            f"multi_site_job_scraper/data/{portal}/run.log",
            line + "\n",
            datetime(2026, 9, 26, 0, 25, 0),
        )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    for portal, count in expected.items():
        assert runs[portal].outcome == re_mod.OUTCOME_OK, portal
        assert runs[portal].offers_current_run == count, portal


# --------------------------------------------------------------------------
# T-11 / T-12: diagnostic aggregation
# --------------------------------------------------------------------------


def test_T11_build_run_diagnostic_analyzable_with_nine_sources(tmp_path):
    _write(tmp_path, "logs/upload-2026-09-26.log", COMPLETE_LOG)

    diagnostic = re_mod.build_run_diagnostic(tmp_path, tmp_path / "logs")

    assert diagnostic.analyzable is True
    assert diagnostic.inconclusive_reason is None
    assert diagnostic.summary is not None
    assert tuple(diagnostic.sources) == sources.source_ids()
    assert len(diagnostic.sources) == 9


def test_T11_missing_source_evidence_is_no_evidence_but_run_analyzable(tmp_path):
    _write(tmp_path, "logs/upload-2026-09-26.log", COMPLETE_LOG)

    diagnostic = re_mod.build_run_diagnostic(tmp_path, tmp_path / "logs")

    assert diagnostic.analyzable is True
    assert diagnostic.sources["indeed"].outcome == re_mod.OUTCOME_NO_EVIDENCE
    assert diagnostic.sources["linkedin"].outcome == re_mod.OUTCOME_NO_EVIDENCE


def test_T12_build_run_diagnostic_inconclusive_without_run(tmp_path):
    diagnostic = re_mod.build_run_diagnostic(tmp_path, tmp_path / "logs")

    assert diagnostic.analyzable is False
    assert diagnostic.inconclusive_reason
    assert diagnostic.summary is None
    assert diagnostic.sources == {}


def test_T12_truncated_only_run_is_inconclusive(tmp_path):
    _write(
        tmp_path,
        "logs/upload-2026-09-26.log",
        "00:00:05  [INFO]  ====  Inicio pipeline scrapers  (2026-09-26) ====\n"
        "02:30:00  [INFO] casos\n",
    )

    diagnostic = re_mod.build_run_diagnostic(tmp_path, tmp_path / "logs")

    assert diagnostic.analyzable is False
    assert diagnostic.summary is None


# --------------------------------------------------------------------------
# R1: evidence from another execution must never be attributed to the run
# --------------------------------------------------------------------------


def test_R1_linkedin_previous_night_block_is_no_evidence(tmp_path):
    # Only a block from the previous night exists: it must not be reused.
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-25 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-25 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-25 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-25 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] 77 ofertas base "
        "anadidas (sin detalle aun).\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_NO_EVIDENCE
    assert evidence.offers_current_run is None


def test_R1_linkedin_result_marker_from_another_run_is_no_evidence(tmp_path):
    # The block is in the window, but the last_run marker belongs to another run.
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/run_nightly.log",
        "2026-09-26 00:00:13 === INICIO RUN NOCTURNA ===\n"
        "2026-09-26 00:00:14 Intento 1/5 - lanzando scraper con --append...\n"
        "2026-09-26 01:35:40 Run completada: 20 combinacion(es) con tarjetas OK "
        "(exit=-1).\n"
        "2026-09-26 01:35:40 === FIN RUN NOCTURNA exit=0 ===\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/nightly_stdout_attempt1.log",
        "00:45:36 INFO linkedin_scraper: [Data Engineer/Madrid] 12 ofertas base "
        "anadidas (sin detalle aun).\n",
    )
    _write(
        tmp_path,
        "linkedin_jobs_scraper/data/last_nightly_run.txt",
        "last_run=20260925_013540 exit=0 attempts=1\n",
    )

    evidence = re_mod.read_linkedin_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_NO_EVIDENCE


def test_R1_infojobs_previous_night_result_is_no_evidence(tmp_path):
    _write(
        tmp_path,
        "infojobs_jobs_scraper/data/run_nightly.log",
        "2026-09-25 00:00:12 === INICIO RUN NOCTURNA INFOJOBS ===\n"
        "2026-09-25 00:00:13 Intento 1/3 - lanzando python -m scraper.main ...\n"
        "2026-09-25 00:31:38 RESULT: total=88 incidencias=0 blocked=False\n"
        "2026-09-25 00:31:38 === FIN RUN NOCTURNA INFOJOBS exit=0 ===\n",
    )

    evidence = re_mod.read_infojobs_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_NO_EVIDENCE
    assert evidence.offers_current_run is None


def test_R1_indeed_metadata_from_another_run_is_no_evidence(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:21:40 Scrape completado detectado en stdout aunque exit=-1. "
        "Se considera OK.\n"
        "2026-09-26 00:21:40 === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    # Only metadata from the previous night is available.
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260925_0045.json",
        {"total_unique": 42, "finished_at": "2026-09-25T05:04:55"},
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_NO_EVIDENCE
    assert evidence.offers_current_run is None


def test_R1_multi_site_previous_run_at_is_no_evidence(tmp_path):
    _write_json(
        tmp_path,
        "multi_site_job_scraper/data/merged/last_run.json",
        {
            "results": {portal: 0 for portal in PORTALS},
            "outputs_merged": list(PORTALS),
            "merge_exit": 0,
            "run_at": "2026-09-25T00:57:16",
        },
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert set(runs) == set(PORTALS)
    assert all(r.outcome == re_mod.OUTCOME_NO_EVIDENCE for r in runs.values())


def test_R1_multi_site_missing_run_at_is_no_evidence(tmp_path):
    _write_json(
        tmp_path,
        "multi_site_job_scraper/data/merged/last_run.json",
        {
            "results": {portal: 0 for portal in PORTALS},
            "outputs_merged": list(PORTALS),
            "merge_exit": 0,
        },
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert all(r.outcome == re_mod.OUTCOME_NO_EVIDENCE for r in runs.values())


# --------------------------------------------------------------------------
# R3: Indeed finish line with a challenge suffix
# --------------------------------------------------------------------------


def test_R3_indeed_finish_with_challenge_suffix_is_blocked(tmp_path):
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:08:14   STDERR: [!] CAPTCHA/CHALLENGE DETECTADO en ES/Madrid.\n"
        "2026-09-26 00:09:14 === FIN RUN NOCTURNA INDEED exit=-1 "
        "(challenge anti-bot: espera 24h) ===\n",
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_BLOCKED


def test_R3_indeed_finish_suffix_still_captures_exit_code(tmp_path):
    # A non-captcha suffix must not hide the captured non-zero exit code.
    _indeed_log(
        tmp_path,
        "2026-09-26 00:00:07 === INICIO RUN NOCTURNA INDEED ===\n"
        "2026-09-26 00:00:09 Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        "2026-09-26 00:09:14 === FIN RUN NOCTURNA INDEED exit=3 (cierre forzado) ===\n",
    )

    evidence = re_mod.read_indeed_run(tmp_path, _summary())

    assert evidence.outcome == re_mod.OUTCOME_ERROR
    assert "exit=3" in (evidence.error or "")


# --------------------------------------------------------------------------
# R4: timezone normalization
# --------------------------------------------------------------------------


def test_R4_indeed_aware_metadata_is_normalized_to_local(tmp_path):
    aware = datetime(2026, 9, 26, 5, 4, 55, tzinfo=timezone.utc)
    local = aware.astimezone().replace(tzinfo=None)
    start = (local - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    end = (local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    block_stamp = (local - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    _indeed_log(
        tmp_path,
        f"{block_stamp} === INICIO RUN NOCTURNA INDEED ===\n"
        f"{block_stamp} Intento 1/2 - lanzando python main.py --cdp 9222 ...\n"
        f"{block_stamp} Scrape completado detectado en stdout. Se considera OK.\n"
        f"{block_stamp} === FIN RUN NOCTURNA INDEED exit=0 ===\n",
    )
    _write_json(
        tmp_path,
        "indeed_jobs_scraper/output/scrape_metadata_20260926_0045.json",
        {"total_unique": 12, "finished_at": aware.isoformat()},
    )
    summary = _summary(start=start, end=end)

    evidence = re_mod.read_indeed_run(tmp_path, summary)

    assert evidence.outcome == re_mod.OUTCOME_OK
    assert evidence.offers_current_run == 12


def test_R4_multi_site_aware_run_at_is_normalized_to_local(tmp_path):
    aware = datetime(2026, 9, 26, 0, 57, 16, tzinfo=timezone.utc)
    local = aware.astimezone().replace(tzinfo=None)
    start = (local - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    end = (local + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    _write_json(
        tmp_path,
        "multi_site_job_scraper/data/merged/last_run.json",
        {
            "results": {portal: 0 for portal in PORTALS},
            "outputs_merged": list(PORTALS),
            "merge_exit": 0,
            "run_at": aware.isoformat(),
        },
    )
    summary = _summary(start=start, end=end)

    runs = re_mod.read_multi_site_runs(tmp_path, summary)

    assert all(r.outcome == re_mod.OUTCOME_OK for r in runs.values())


# --------------------------------------------------------------------------
# R5: Multi-site DONE counter belongs to the run only
# --------------------------------------------------------------------------


def _all_portals_ok(root: Path) -> None:
    _multi_site_last_run(
        root,
        {portal: 0 for portal in PORTALS},
        outputs=list(PORTALS),
    )


def test_R5_multi_site_previous_night_done_is_not_attributed(tmp_path):
    # The DONE line's time-of-day falls in the window, but the file was last
    # modified the previous night: the counter must not be inherited.
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: === DEVITJOBS DONE: 99 nuevas, 99 total ===\n",
        datetime(2026, 9, 25, 0, 19, 28),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["devitjobs"].outcome == re_mod.OUTCOME_OK
    assert runs["devitjobs"].offers_current_run is None


def test_R5_multi_site_current_run_done_is_attributed(tmp_path):
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: === DEVITJOBS DONE: 13 nuevas, 13 total ===\n",
        datetime(2026, 9, 26, 0, 19, 28),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["devitjobs"].outcome == re_mod.OUTCOME_OK
    assert runs["devitjobs"].offers_current_run == 13


def test_R5_multi_site_done_followed_by_non_start_line_is_attributed(tmp_path):
    # A DONE followed by a benign/error line that is not a start banner still
    # belongs to the run (the old "must be the last line" rule was too strict).
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: === DEVITJOBS DONE: 13 nuevas, 13 total ===\n"
        "00:20:00 ERROR devitjobs: Error en scraper: Timeout\n",
        datetime(2026, 9, 26, 0, 20, 0),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["devitjobs"].outcome == re_mod.OUTCOME_OK
    assert runs["devitjobs"].offers_current_run == 13


def test_R5_multi_site_without_done_line_is_none(tmp_path):
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: Ofertas en el tablon: 164\n",
        datetime(2026, 9, 26, 0, 19, 28),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["devitjobs"].offers_current_run is None


def test_R5_multi_site_unknown_window_is_none(tmp_path):
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/devitjobs/run.log",
        "00:19:28 INFO devitjobs: === DEVITJOBS DONE: 13 nuevas, 13 total ===\n",
        datetime(2026, 9, 26, 0, 19, 28),
    )
    summary = re_mod.PipelineSummary(
        log_path="fixture.log",
        date=None,
        started_at=None,
        finished_at=None,
        completed=True,
        global_failures=0,
        duration_seconds=1,
        statuses={},
        errors=(),
        warnings=(),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, summary)

    assert runs["devitjobs"].outcome == re_mod.OUTCOME_OK
    assert runs["devitjobs"].offers_current_run is None


# --------------------------------------------------------------------------
# T-04 / T-05: real completion cases (benign trailing lines, INTERRUMPIDO,
# a new execution start after the DONE)
# --------------------------------------------------------------------------


def test_T04_glassdoor_done_followed_by_stored_count_is_attributed(tmp_path):
    # Real Glassdoor log: run() prints the DONE line and __main__ then logs
    # "Total ofertas almacenadas: N". The counter must still be attributed.
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/glassdoor/run.log",
        "00:14:31 INFO glassdoor: === Glassdoor USA Scraper ===\n"
        "00:36:18 INFO glassdoor: === GLASSDOOR DONE: 570 ofertas nuevas, "
        "570 total en store ===\n"
        "00:36:26 INFO glassdoor: Total ofertas almacenadas: 570\n",
        datetime(2026, 9, 26, 0, 36, 26),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["glassdoor"].outcome == re_mod.OUTCOME_OK
    assert runs["glassdoor"].offers_current_run == 570


def test_T04_nvb_interrupted_is_not_a_done(tmp_path):
    # NVB emits "INTERRUMPIDO" instead of DONE when interrupted: no counter.
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/nvb/run.log",
        "00:00:20 INFO nvb: === Nationale Vacaturebank Scraper ===\n"
        "00:16:20 INFO nvb: === NVB INTERRUMPIDO: 5 nuevas, 5 total ===\n",
        datetime(2026, 9, 26, 0, 16, 20),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["nvb"].outcome == re_mod.OUTCOME_OK
    assert runs["nvb"].offers_current_run is None


def test_T04_new_run_start_after_done_invalidates_it(tmp_path):
    # A newer execution started after the DONE: that DONE is from an earlier
    # run and must not be attributed (defect R5).
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/irishjobs/run.log",
        "00:00:20 INFO irishjobs: === IrishJobs Scraper ===\n"
        "00:20:00 INFO irishjobs: === IRISHJOBS DONE: 76 ofertas nuevas, "
        "76 total, 76 detalles ===\n"
        "01:00:00 INFO irishjobs: === IrishJobs Scraper ===\n"
        "01:00:05 INFO irishjobs: Iniciando nueva ejecucion...\n",
        datetime(2026, 9, 26, 1, 0, 5),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["irishjobs"].outcome == re_mod.OUTCOME_OK
    assert runs["irishjobs"].offers_current_run is None


def test_RA_benign_scraper_line_after_done_is_attributed(tmp_path):
    # A benign trailing line that merely contains the word "Scraper" must not
    # be mistaken for a new execution banner (regression R-A): the start regex
    # is anchored to a single line.
    _all_portals_ok(tmp_path)
    _write_at(
        tmp_path,
        "multi_site_job_scraper/data/glassdoor/run.log",
        "00:14:31 INFO glassdoor: === Glassdoor USA Scraper ===\n"
        "00:36:18 INFO glassdoor: === GLASSDOOR DONE: 570 ofertas nuevas, "
        "570 total en store ===\n"
        "00:36:26 INFO glassdoor: Scraper finalizado correctamente\n",
        datetime(2026, 9, 26, 0, 36, 26),
    )

    runs = re_mod.read_multi_site_runs(tmp_path, _summary())

    assert runs["glassdoor"].outcome == re_mod.OUTCOME_OK
    assert runs["glassdoor"].offers_current_run == 570
