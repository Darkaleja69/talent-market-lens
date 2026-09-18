"""Tests unitarios de las rutas GUEST de parse_detail (sin navegador).

Verifica contra fakes de Page que:
 - _get_description encuentra la descripcion guest (.show-more-less-html__markup)
   y cae al selector auth cuando no existe.
 - _collect_chips lee criterios estructurados guest
   (.description__job-criteria-*) y normaliza valores.
 - _collect_header_meta lee fecha relativa y solicitantes guest
   (.posted-time-ago__text / .num-applicants__caption), EN y ES.
 - _collect_company extrae el link de empresa guest (.topcard__org-name-link)
   e industria/tamano desde los criterios.
 - parse_detail completo (fake) rellena todos los campos desde una pagina
   guest; _RE_POSTED_RELATIVE cubre variantes en ingles.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.models import JobOffer, now_utc_iso
import src.parse_detail as pd
from src.parse_detail import (
    SEL_APPLICANTS_GUEST,
    SEL_COMPANY_GUEST,
    SEL_CRITERIA_ITEM_GUEST,
    SEL_CRITERIA_SUBHEADER_GUEST,
    SEL_CRITERIA_TEXT_GUEST,
    SEL_DESCRIPTION,
    SEL_DESCRIPTION_GUEST,
    SEL_POSTED_GUEST,
    SEL_SEE_MORE_GUEST,
    _RE_POSTED_RELATIVE,
    _collect_chips,
    _collect_company,
    _collect_header_meta,
    _get_description,
    parse_detail,
)


class _El:
    """Elemento fake: texto, html, atributos, hijos y visibilidad."""

    def __init__(self, text: str = "", html: str = "", attrs: dict[str, str] | None = None,
                 visible: bool = True, children: dict[str, "_El"] | None = None,
                 on_click=None):
        self._text = text
        self._html = html
        self._attrs = attrs or {}
        self._visible = visible
        self._children = children or {}
        self._on_click = on_click
        self.clicked = False

    def inner_text(self) -> str:
        return self._text

    def text_content(self) -> str:
        return self._text

    def inner_html(self) -> str:
        return self._html

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)

    def is_visible(self) -> bool:
        return self._visible

    def query_selector(self, sel: str):
        return self._children.get(sel)

    def click(self, timeout: int | None = None) -> None:
        self.clicked = True
        if self._on_click:
            self._on_click()


class _FakeMouse:
    def move(self, x: int, y: int, steps: int = 1) -> None:
        pass


class _FakePage:
    """Page fake con registro de selectores->elementos y listas."""

    def __init__(self, url: str = "https://www.linkedin.com/jobs/view/1234567890/",
                 elements: dict[str, _El] | None = None,
                 lists: dict[str, list[_El]] | None = None):
        self.url = url
        self._elements = elements or {}
        self._lists = lists or {}
        self.mouse = _FakeMouse()
        self.goto_calls: list[str] = []

    def query_selector(self, sel: str):
        return self._elements.get(sel)

    def query_selector_all(self, sel: str):
        return self._lists.get(sel, [])

    def wait_for_selector(self, sel: str, **kwargs):
        raise TimeoutError(f"selector {sel} no disponible (fake)")

    def goto(self, url: str, **kwargs) -> None:
        self.goto_calls.append(url)

    def wait_for_load_state(self, state: str, timeout: int | None = None) -> None:
        pass

    def evaluate(self, expr: str, arg: Any = None):
        return False


def _criteria(sub: str, val: str) -> _El:
    return _El(children={
        SEL_CRITERIA_SUBHEADER_GUEST: _El(text=sub),
        SEL_CRITERIA_TEXT_GUEST: _El(text=val),
    })


# ---------- _get_description ----------

def test_get_description_guest_selector():
    desc = _El(text="Descripcion completa de la oferta.")
    page = _FakePage(elements={SEL_DESCRIPTION_GUEST: desc})
    assert _get_description(page) == "Descripcion completa de la oferta."


def test_get_description_falls_back_to_auth_when_no_guest():
    desc = _El(text="Descripcion del fixture auth.")
    page = _FakePage(elements={SEL_DESCRIPTION: desc})
    assert _get_description(page) == "Descripcion del fixture auth."


def test_get_description_returns_empty_when_nothing():
    assert _get_description(_FakePage()) == ""


# ---------- _collect_chips (criterios guest) ----------

def test_collect_chips_guest_criteria():
    page = _FakePage(lists={
        SEL_CRITERIA_ITEM_GUEST: [
            _criteria("Seniority level", "Mid-Senior level"),
            _criteria("Employment type", "Full-time"),
            _criteria("Workplace type", "Hybrid"),
            _criteria("Job function", "Data"),
        ],
    })
    work_mode, emp_type, exp_level = _collect_chips(page)
    assert work_mode == "Hibrido"
    assert emp_type == "Jornada completa"
    assert exp_level == "Mid-Senior"


def test_collect_chips_guest_criteria_es():
    page = _FakePage(lists={
        SEL_CRITERIA_ITEM_GUEST: [
            _criteria("Nivel de experiencia", "Senior"),
            _criteria("Tipo de empleo", "Jornada completa"),
            _criteria("Modalidad", "Remoto"),
        ],
    })
    work_mode, emp_type, exp_level = _collect_chips(page)
    assert work_mode == "Remoto"
    assert emp_type == "Jornada completa"
    assert exp_level == "Mid-Senior"


def test_collect_chips_no_criteria_returns_empty():
    work_mode, emp_type, exp_level = _collect_chips(_FakePage())
    assert work_mode == "" and emp_type == "" and exp_level == ""


# ---------- _collect_header_meta (guest) ----------

def test_collect_header_meta_guest_en():
    page = _FakePage(elements={
        SEL_POSTED_GUEST: _El(text="2 weeks ago"),
        SEL_APPLICANTS_GUEST: _El(text="159 applicants"),
    })
    posted_rel, location, applicants = _collect_header_meta(page)
    assert posted_rel == "2 weeks ago"
    assert applicants == 159


def test_collect_header_meta_guest_es():
    page = _FakePage(elements={
        SEL_POSTED_GUEST: _El(text="hace 3 días"),
        SEL_APPLICANTS_GUEST: _El(text="Más de 100 solicitudes"),
    })
    posted_rel, location, applicants = _collect_header_meta(page)
    assert posted_rel == "hace 3 días"
    assert applicants == 100


def test_re_posted_relative_english_variants():
    assert _RE_POSTED_RELATIVE.search("3 days ago")
    assert _RE_POSTED_RELATIVE.search("2 weeks ago")
    assert _RE_POSTED_RELATIVE.search("1 hour ago")
    assert _RE_POSTED_RELATIVE.search("6 months ago")
    assert _RE_POSTED_RELATIVE.search("hace 5 horas")
    assert not _RE_POSTED_RELATIVE.search("Yesterday")


# ---------- _collect_company (guest) ----------

def test_collect_company_guest_link_and_criteria():
    comp = _El(attrs={"href": "https://www.linkedin.com/company/acme-corp/?originalSubdomain=es"})
    page = _FakePage(
        elements={SEL_COMPANY_GUEST: comp},
        lists={
            SEL_CRITERIA_ITEM_GUEST: [
                _criteria("Industries", "Information Technology"),
                _criteria("Company size", "1,001-5,000 employees"),
                _criteria("Seniority level", "Mid-Senior level"),
            ],
        },
    )
    company_url, industry, size = _collect_company(page)
    assert company_url == "https://www.linkedin.com/company/acme-corp/"
    assert industry == "Information Technology"
    assert size == "1,001-5,000 employees"


# ---------- parse_detail end-to-end (fake guest) ----------

@pytest.fixture
def no_pauses(monkeypatch):
    monkeypatch.setattr(pd, "delay", lambda *a, **k: None)
    monkeypatch.setattr(pd, "mouse_jitter", lambda *a, **k: None)
    monkeypatch.setattr(pd, "scroll_slow", lambda *a, **k: 0)


def _make_job() -> JobOffer:
    return JobOffer(
        job_id="1234567890",
        job_url="https://www.linkedin.com/jobs/view/1234567890/",
        title="Data Analyst",
        company_name="ACME",
        search_role="Data Analyst",
        search_city="Madrid",
        source="local",
        scraped_at=now_utc_iso(),
    )


def test_parse_detail_guest_end_to_end(no_pauses):
    desc_html = (
        "<strong>About the company</strong> We are ACME, a data company.<br>"
        "<strong>What you'll do</strong> Build dashboards with Python.<br>"
        "<strong>Requirements</strong> Experience with Python and SQL."
    )
    desc_text = (
        "About the company\nWe are ACME, a data company.\n"
        "What you'll do\nBuild dashboards with Python.\n"
        "Requirements\nExperience with Python and SQL."
    )
    see_more = _El(text="Show more", on_click=lambda: None)
    page = _FakePage(
        elements={
            SEL_DESCRIPTION_GUEST: _El(text=desc_text, html=desc_html),
            SEL_SEE_MORE_GUEST: see_more,
            SEL_POSTED_GUEST: _El(text="2 weeks ago"),
            SEL_APPLICANTS_GUEST: _El(text="159 applicants"),
            SEL_COMPANY_GUEST: _El(
                attrs={"href": "https://www.linkedin.com/company/acme-corp/"}
            ),
        },
        lists={
            SEL_CRITERIA_ITEM_GUEST: [
                _criteria("Seniority level", "Mid-Senior level"),
                _criteria("Employment type", "Full-time"),
                _criteria("Workplace type", "Hybrid"),
                _criteria("Industries", "Information Technology"),
                _criteria("Company size", "1,001-5,000 employees"),
            ],
        },
    )
    job = _make_job()
    parse_detail(page, job, {"delays": {}})

    assert page.goto_calls == [job.job_url]
    assert see_more.clicked  # se intento expandir la descripcion
    assert job.description_full == desc_text
    assert job.posted_relative == "2 weeks ago"
    assert job.num_applicants == 159
    assert job.company_url == "https://www.linkedin.com/company/acme-corp/"
    assert job.company_industry == "Information Technology"
    assert job.company_size == "1,001-5,000 employees"
    assert job.work_mode == "Hibrido"
    assert job.employment_type == "Jornada completa"
    assert job.experience_level == "Mid-Senior"
    assert "Python" in job.skills
    assert "responsibilities" in job.responsibilities.lower() or job.responsibilities
    assert job.requirements