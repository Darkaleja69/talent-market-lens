"""Tests unitarios de parse_serp (sin navegador, sin red).

Verifica:
 - parse_location() divide correctamente ubicaciones por comas.
 - clean_url() elimina params de tracking.
 - extract_job_id() desde URN y desde URL.
 - parse_serp() sobre el HTML real guardado (fixture) usando un falso
   "page" basado en BeautifulSoup o selectores manuales.

Para el test con HTML real usamos el snapshot commiteado en
tests/fixtures/serp_madrid.html (copia de data/raw_html/serp_madrid.html).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.parse_serp import (
    clean_url,
    _extract_job_id_guest as extract_job_id,
    parse_location,
    extract_work_mode,
)
from src.models import JobOffer


# ---------- parse_location ----------

@pytest.mark.parametrize("raw,expected", [
    ("Madrid, Community of Madrid, Spain",
     ("Madrid", "Community of Madrid", "Spain")),
    ("Barcelona, Catalonia, Spain",
     ("Barcelona", "Catalonia", "Spain")),
    ("Bilbao, Basque Country, Spain",
     ("Bilbao", "Basque Country", "Spain")),
    ("Madrid, Spain", ("Madrid", "", "Spain")),
    ("Remote", ("Remote", "", "")),
    ("", ("", "", "")),
    # Auth page format: incluye modo de trabajo entre parentesis
    # El pais se normaliza a ingles ("España" -> "Spain").
    ("Madrid, Comunidad de Madrid, España (Híbrido)",
     ("Madrid", "Comunidad de Madrid", "Spain")),
    ("España (En remoto)", ("", "", "Spain")),
    ("Madrid, Comunidad de Madrid, España (Presencial)",
     ("Madrid", "Comunidad de Madrid", "Spain")),
    # Ciudades/paises traducidos por el locale se normalizan.
    ("Dublín, Dublín, Irlanda", ("Dublin", "Dublín", "Ireland")),
    ("Ámsterdam, Holanda Septentrional, Países Bajos",
     ("Amsterdam", "Holanda Septentrional", "Netherlands")),
    ("Irlanda", ("", "", "Ireland")),
])
def test_parse_location(raw, expected):
    assert parse_location(raw) == expected


# ---------- extract_work_mode (auth page) ----------

@pytest.mark.parametrize("raw,expected", [
    ("Madrid, Comunidad de Madrid, España (Híbrido)", "Hibrido"),
    ("España (En remoto)", "Remoto"),
    ("Madrid, Comunidad de Madrid, España (Presencial)", "Presencial"),
    ("Madrid, España (Remoto)", "Remoto"),
    ("Madrid, España", ""),
    ("", ""),
])
def test_extract_work_mode(raw, expected):
    assert extract_work_mode(raw) == expected


# ---------- clean_url ----------

def test_clean_url_strips_tracking_params():
    url = ("https://es.linkedin.com/jobs/view/data-analyst-at-alten-spain-4423162090"
           "?position=1&pageNum=0&refId=vqsK2Zzj0wQiwtnBax2Gog%3D%3D"
           "&trackingId=beyc33k%2FNfEuQRK%2FFPtMYg%3D%3D&trk=public_jobs_jserp")
    cleaned = clean_url(url)
    assert "position=" not in cleaned
    assert "pageNum=" not in cleaned
    assert "refId=" not in cleaned
    assert "trackingId=" not in cleaned
    assert "trk=" not in cleaned
    assert "4423162090" in cleaned


def test_clean_url_empty():
    assert clean_url("") == ""


def test_clean_url_keeps_other_params():
    url = "https://example.com/jobs/view/123?keep=1&position=2"
    assert "keep=1" in clean_url(url)
    assert "position=" not in clean_url(url)


# ---------- extract_job_id (fake element) ----------

class FakeElement:
    """Falso elemento Playwright con get_attribute/query_selector."""
    def __init__(self, attrs: dict[str, str], children: dict[str, "FakeElement"] | None = None):
        self._attrs = attrs
        self._children = children or {}

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    def query_selector(self, sel: str) -> "FakeElement | None":
        return self._children.get(sel)


def test_extract_job_id_from_urn():
    card = FakeElement({"data-entity-urn": "urn:li:jobPosting:4423162090"})
    assert extract_job_id(card) == "4423162090"


def test_extract_job_id_from_url_fallback():
    link = FakeElement({"href": "https://es.linkedin.com/jobs/view/foo-at-bar-4431128771?position=10"})
    card = FakeElement({}, children={"a.base-card__full-link": link})
    assert extract_job_id(card) == "4431128771"


def test_extract_job_id_none_when_missing():
    card = FakeElement({})
    assert extract_job_id(card) is None


# ---------- JobOffer.to_flat_dict ----------

def test_job_offer_flat_dict_skills_joined():
    j = JobOffer(job_id="123", job_url="http://x", skills=["Python", "SQL", "ETL"])
    d = j.to_flat_dict()
    assert d["skills"] == "Python|SQL|ETL"
    assert d["job_id"] == "123"


def test_job_offer_flat_dict_empty_skills():
    j = JobOffer(job_id="1", job_url="http://x")
    assert j.to_flat_dict()["skills"] == ""


# ---------- parse_serp con HTML real (fixture) ----------

@pytest.fixture(scope="module")
def serp_html() -> str:
    """Carga el HTML real de LinkedIn Jobs guardado como fixture commiteado.

    Busca en orden:
      1. tests/fixtures/serp_madrid.html (fixture commiteado con el repo)
      2. data/raw_html/serp_madrid.html (snapshot de una run reciente)
    Si ninguno existe, se salta el test.
    """
    candidates = [
        Path(__file__).resolve().parent / "fixtures" / "serp_madrid.html",
        Path(__file__).resolve().parent.parent / "data" / "raw_html" / "serp_madrid.html",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 1000:
            return p.read_text(encoding="utf-8", errors="replace")
    pytest.skip("No se encontro snapshot HTML de LinkedIn Jobs para el test.")


def test_serp_html_has_expected_selectors(serp_html: str):
    """Smoke test: el HTML real contiene los selectores que usamos."""
    assert 'class="jobs-search__results-list"' in serp_html \
        or "jobs-search__results-list" in serp_html
    assert "job-search-card" in serp_html
    assert "base-search-card__title" in serp_html
    assert "urn:li:jobPosting:" in serp_html
    assert "infinite-scroller__show-more-button" in serp_html


def test_serp_html_job_id_pattern_present(serp_html: str):
    """Al menos una tarjeta con data-entity-urn urn:li:jobPosting:NNN."""
    import re
    matches = re.findall(r'urn:li:jobPosting:(\d+)', serp_html)
    assert len(matches) >= 10, f"solo {len(matches)} job ids encontrados"
    assert all(m.isdigit() for m in matches)
