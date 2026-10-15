"""Tests del mercado Irlanda de Glassdoor (T-35, spec 004).

Sin red: comprueban la configuracion del mercado (glassdoor.ie), que la
resolucion de pais no hereda el fallback estadounidense y que los marcadores
de challenge del SERP (ingles y espanol) detienen el scraper.
"""
from __future__ import annotations

import logging
import types

import pytest

from src.glassdoor import scraper as scraper_mod
from src.glassdoor.config_gd import DEFAULT_CONFIG
from src.glassdoor.scraper import GlassdoorScraper

_FAKE_CONTEXT = types.SimpleNamespace(request=object())


def _cfg(tmp_path, **overrides):
    cfg = dict(DEFAULT_CONFIG)
    cfg["output"] = {
        "csv": str(tmp_path / "jobs.csv"),
        "parquet": str(tmp_path / "jobs.parquet"),
        "checkpoint_dir": str(tmp_path / "cp"),
    }
    cfg.update(overrides)
    return cfg


def test_market_ireland_config():
    cfg = DEFAULT_CONFIG
    assert cfg["site"] == "glassdoor"
    assert cfg["base_url"] == "https://www.glassdoor.ie"
    assert cfg["country"] == "IE"
    assert cfg["country_name"] == "Ireland"
    assert cfg["currency"] == "EUR"
    assert cfg["locale"] == "en-IE"
    assert cfg["timezone"] == "Europe/Dublin"
    # .ie sirve el SERP en /Job/jobs.htm (sonda 2026-10-12).
    assert cfg["search_path"] == "/Job/jobs.htm"
    # Perfil persistente unico y caliente: se mantiene el valor actual.
    assert cfg["profile_name"] == "glassdoor"
    # Geolocalizacion coherente con el mercado (Dublin).
    geo = cfg["geolocation"]
    assert 53.0 < geo["latitude"] < 54.0
    assert -7.0 < geo["longitude"] < -6.0


def test_market_change_keeps_output_paths():
    # El mercado sustituye, no anade: mismas rutas de salida.
    assert DEFAULT_CONFIG["output"] == {
        "csv": "data/glassdoor/output/jobs.csv",
        "parquet": "data/glassdoor/output/jobs.parquet",
        "checkpoint_dir": "data/glassdoor/checkpoints",
    }


def test_roles_and_filters_unchanged():
    assert DEFAULT_CONFIG["roles"] == [
        "Data Analyst",
        "Data Scientist",
        "Data Engineer",
        "Analytics Engineer",
        "Machine Learning Engineer",
        "Business Intelligence",
    ]
    assert DEFAULT_CONFIG["date_filter"] == "7"
    assert DEFAULT_CONFIG["max_pages"] == 15


def test_description_budget_covers_target():
    # Presupuesto declarado: 2000 >= target 1000; no recorta cobertura.
    assert DEFAULT_CONFIG["max_description_jobs"] >= 1000
    assert DEFAULT_CONFIG["fetch_descriptions"] is True


def test_location_fallback_does_not_inherit_us(tmp_path, monkeypatch):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    # En .ie no se hereda el (1, "COUNTRY") de glassdoor.com: el fallback es
    # el pais del propio dominio, confirmado con la sesion real (T-35).
    assert DEFAULT_CONFIG["location_fallback"] == {"id": 70, "type": "COUNTRY"}
    monkeypatch.setattr(scraper_mod.bff, "resolve_location",
                        lambda *a, **k: (None, ""))
    assert scraper._resolve_location(
        _FAKE_CONTEXT, DEFAULT_CONFIG["base_url"], "Ireland") == (70, "COUNTRY")


def test_location_fallback_used_when_declared(tmp_path, monkeypatch):
    scraper = GlassdoorScraper(
        _cfg(tmp_path, location_fallback={"id": 7, "type": "COUNTRY"}))
    monkeypatch.setattr(scraper_mod.bff, "resolve_location",
                        lambda *a, **k: (None, ""))
    assert scraper._resolve_location(
        _FAKE_CONTEXT, "https://www.glassdoor.ie", "Ireland") == (7, "COUNTRY")


def test_resolved_location_wins_over_fallback(tmp_path, monkeypatch):
    scraper = GlassdoorScraper(
        _cfg(tmp_path, location_fallback={"id": 7, "type": "COUNTRY"}))
    monkeypatch.setattr(scraper_mod.bff, "resolve_location",
                        lambda *a, **k: (123, "COUNTRY"))
    assert scraper._resolve_location(
        _FAKE_CONTEXT, "https://www.glassdoor.ie", "Ireland") == (123, "COUNTRY")


def test_missing_location_without_fallback_aborts(tmp_path, monkeypatch):
    # Sin ubicacion ni fallback declarado se aborta con error en vez de
    # buscar en el pais equivocado.
    scraper = GlassdoorScraper(_cfg(tmp_path, location_fallback=None))
    monkeypatch.setattr(scraper, "_resolve_location", lambda *a, **k: (None, ""))
    with pytest.raises(RuntimeError):
        scraper._resolve_location_or_fail(
            None, "https://www.glassdoor.ie", "Ireland")


class _FakeTextPage:
    def __init__(self, text, url=(
            "https://www.glassdoor.ie/Job/jobs.htm?sc.keyword=Data+Analyst")):
        self.text = text
        self.url = url

    def evaluate(self, script, *args):
        return self.text


def test_block_detection_english_challenge(tmp_path):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    assert scraper._check_blocked_or_landing(
        _FakeTextPage("Humans only. Glassdoor has been built on the "
                      "contributions of millions of people."),
        "Data Analyst") is True


def test_block_detection_spanish_challenge(tmp_path):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    assert scraper._check_blocked_or_landing(
        _FakeTextPage("Solo personas reales. Sistemas de seguridad avanzados."),
        "Data Analyst") is True


def test_block_detection_empty_landing(tmp_path):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    assert scraper._check_blocked_or_landing(
        _FakeTextPage("contenido", url="https://www.glassdoor.ie/index.htm"),
        "Data Analyst") is True


def test_normal_serp_not_blocked(tmp_path):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    assert scraper._check_blocked_or_landing(
        _FakeTextPage("Ofertas de empleo para Data Analyst en Dublin"),
        "Data Analyst") is False


def test_warmup_warns_on_other_domain(tmp_path, monkeypatch, caplog):
    scraper = GlassdoorScraper(_cfg(tmp_path))

    class _Page:
        url = "https://www.glassdoor.com/Job/jobs.htm"

        def goto(self, *args, **kwargs):
            pass

        def content(self):
            return '"token":"tok-123"'

    monkeypatch.setattr(scraper_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scraper_mod.serp, "dismiss_overlays", lambda _p: None)
    with caplog.at_level(logging.WARNING, logger="glassdoor"):
        base_url, token = scraper._warmup_homepage(
            _Page(), DEFAULT_CONFIG["base_url"], "/Job/jobs.htm", "7")

    assert base_url == "https://www.glassdoor.ie"
    assert token == "tok-123"
    assert any("distinto del configurado" in r.getMessage()
               for r in caplog.records)
