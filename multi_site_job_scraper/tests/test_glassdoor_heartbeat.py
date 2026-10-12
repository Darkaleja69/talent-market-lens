"""Tests del latido de progreso de Glassdoor dentro del bucle de tarjetas.

T-35 (spec 004): sin red; el BFF se simula para comprobar que las lineas
PROGRESS crecen durante el procesado de descripciones (el watchdog del
supervisor solo ve crecer `parsed`) y que el tope de fallos consecutivos
desactiva las descripciones cuando el BFF esta degradado.
"""
from __future__ import annotations

import logging
import re
import types

import pytest

from src.glassdoor import scraper as scraper_mod
from src.glassdoor.config_gd import DEFAULT_CONFIG
from src.glassdoor.scraper import GlassdoorScraper


def _cfg(tmp_path, **overrides):
    cfg = dict(DEFAULT_CONFIG)
    cfg["output"] = {
        "csv": str(tmp_path / "jobs.csv"),
        "parquet": str(tmp_path / "jobs.parquet"),
        "checkpoint_dir": str(tmp_path / "cp"),
    }
    cfg.update(overrides)
    return cfg


class _FakePage:
    """Pagina falsa: solo lo que toca `_process_role` en el camino BFF."""

    url = "https://www.glassdoor.ie/Job/jobs.htm?sc.keyword=Data+Analyst"

    def goto(self, *args, **kwargs):
        pass

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def evaluate(self, script, *args):
        if "navigator.userAgent" in script:
            return "test-ua"
        return "resultados normales de busqueda"


def _jobview(listing_id: int) -> dict:
    return {
        "jobview": {
            "header": {
                "jobTitleText": f"Data Analyst {listing_id}",
                "employerNameFromSearch": "ACME",
                "locationName": "Dublin, County Dublin",
                "locationType": "C",
                "ageInDays": 1,
                "payCurrency": "EUR",
                "payPeriod": "ANNUAL",
            },
            "job": {
                "listingId": listing_id,
                "jobTitleText": f"Data Analyst {listing_id}",
                "description": "",
            },
        }
    }


def _listings(count: int) -> list[dict]:
    return [_jobview(1000 + i) for i in range(count)]


@pytest.fixture
def offline(monkeypatch):
    """Sin red ni esperas: navegador, delays y overlays simulados."""
    monkeypatch.setattr(scraper_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scraper_mod, "delay", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod, "mouse_jitter", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod.serp, "dismiss_overlays", lambda _p: None)


def _run_role(scraper: GlassdoorScraper) -> int:
    return scraper._process_role(
        page=_FakePage(),
        context=types.SimpleNamespace(request=object()),
        role="Data Analyst",
        base_url="https://www.glassdoor.ie",
        search_path="/Job/jobs.htm",
        date_filter="7",
        loc_id=1,
        loc_type="COUNTRY",
        do_detail=False,
        max_detail=0,
        max_pages=1,
        jobs_per_search=200,
        post_goto=(0.0, 0.0),
        step_px=(200, 400),
        scroll_pause=(0.0, 0.0),
        between_pages=(0.0, 0.0),
        between_details=(0.0, 0.0),
        site="glassdoor",
        country="IE",
        country_name="Ireland",
    )


def test_heartbeat_grows_inside_the_card_loop(tmp_path, monkeypatch, offline,
                                              caplog):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    monkeypatch.setattr(scraper_mod.bff, "search_jobs",
                        lambda *a, **k: (_listings(3), "", 3))

    events: list[tuple[str, str]] = []
    original_emit = scraper._emit_progress

    def spy_emit(parsed_delta, new_delta=0):
        line = original_emit(parsed_delta, new_delta)
        events.append(("progress", line))
        return line

    def fake_fetch(*args, **kwargs):
        events.append(("fetch", ""))
        return "Descripcion de prueba con SQL y Python."

    monkeypatch.setattr(scraper, "_emit_progress", spy_emit)
    monkeypatch.setattr(scraper_mod.bff, "fetch_description", fake_fetch)

    caplog.set_level(logging.INFO, logger="glassdoor")
    total = _run_role(scraper)

    assert total == 3
    assert len(scraper.store) == 3
    # Latido inmediato tras parsear + latido por tarjeta + cierre de pagina:
    # cada descripcion (lenta) queda precedida por un latido con parsed > 0.
    assert [kind for kind, _ in events] == [
        "progress", "fetch", "progress", "fetch", "progress", "fetch",
        "progress", "progress",
    ]
    parsed = [int(re.search(r"parsed=(\d+)", value).group(1))
              for kind, value in events if kind == "progress"]
    assert parsed == [0, 1, 2, 3, 3]
    new = [int(re.search(r"new=(\d+)", value).group(1))
           for kind, value in events if kind == "progress"]
    assert new == [0, 1, 2, 3, 3]
    # Las lineas van al logger "glassdoor" (el stream que lee el supervisor).
    progress_lines = [r.getMessage() for r in caplog.records
                      if "PROGRESS source=glassdoor" in r.getMessage()]
    assert len(progress_lines) == 5


def test_page_close_heartbeat_keeps_parsed_semantics_on_early_stop(
        tmp_path, monkeypatch, offline):
    # Con target alcanzado a mitad de pagina, `parsed` sigue contando todas
    # las tarjetas parseadas de la pagina (duplicados incluidos), como antes.
    scraper = GlassdoorScraper(_cfg(tmp_path, target=2))
    monkeypatch.setattr(scraper_mod.bff, "search_jobs",
                        lambda *a, **k: (_listings(4), "", 4))
    monkeypatch.setattr(scraper_mod.bff, "fetch_description",
                        lambda *a, **k: "")

    total = _run_role(scraper)

    assert total == 2
    assert scraper._progress.parsed == 4
    assert scraper._progress.new == 2


def test_consecutive_description_failures_disable_fetching(tmp_path,
                                                           monkeypatch,
                                                           offline, caplog):
    cfg = _cfg(tmp_path, max_description_consecutive_failures=2,
               description_attempts=1, description_timeout_ms=15000)
    scraper = GlassdoorScraper(cfg)
    monkeypatch.setattr(scraper_mod.bff, "search_jobs",
                        lambda *a, **k: (_listings(4), "", 4))
    calls: list[tuple] = []

    def fake_fetch(*args, **kwargs):
        calls.append((kwargs.get("timeout"), kwargs.get("attempts")))
        return ""

    monkeypatch.setattr(scraper_mod.bff, "fetch_description", fake_fetch)

    caplog.set_level(logging.WARNING, logger="glassdoor")
    total = _run_role(scraper)

    # Las 4 tarjetas se siguen almacenando, pero tras 2 fallos consecutivos
    # no se piden mas descripciones (no se encadenan esperas de ~60 s).
    assert total == 4
    assert len(calls) == 2
    assert calls[0] == (15000, 1)
    assert any("fallos consecutivos" in r.getMessage() for r in caplog.records)


def test_description_success_resets_failure_streak(tmp_path, monkeypatch,
                                                   offline):
    cfg = _cfg(tmp_path, max_description_consecutive_failures=2)
    scraper = GlassdoorScraper(cfg)
    monkeypatch.setattr(scraper_mod.bff, "search_jobs",
                        lambda *a, **k: (_listings(4), "", 4))
    results = iter(["", "Descripcion valida", "", ""])
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(1)
        return next(results)

    monkeypatch.setattr(scraper_mod.bff, "fetch_description", fake_fetch)

    total = _run_role(scraper)

    assert total == 4
    # fallo, exito (resetea), fallo, fallo -> el tope no se alcanza: 4 llamadas.
    assert len(calls) == 4
