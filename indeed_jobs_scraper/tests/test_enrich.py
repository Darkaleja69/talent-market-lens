"""Tests offline del enriquecimiento por panel derecho (T-32).

Sin red ni navegador: un ``Page`` falso registra las esperas y los clics, y se
comprueba que un timeout del panel ya no abandona la oferta (se registra como
fallida y el bucle continua), que la cadena de esperas 4 s -> 8 s se ejecuta,
que el latido de progreso se emite por oferta y que el fallback /viewjob
(prohibido por robots.txt) queda desactivado por defecto.
"""
from __future__ import annotations

import logging
import time

from scraper.models import JobOffer
from scraper.parser import enrich_offers_via_right_panel

_ACTIVE_SELECTOR = (
    'div.vjs-highlight a[data-jk="{jk}"], a[data-jk="{jk}"].vjs-highlight'
)

# Testids del layout two-pane actual (iteracion 3, 2026-10-11).
_NEW_TITLE_SELECTOR = '[data-testid="vj-job-title"]'
_NEW_DESC_SELECTOR = '[data-testid="viewjob-job-content"]'
_NEW_HEADING_SELECTOR = '[data-testid="vj-job-description-heading"]'
_NEW_COND_SELECTOR = '[data-testid="structured-job-summary"]'


class FakeLocator:
    def __init__(self, page, selector: str):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._page.counts.get(self._selector, 0)

    def scroll_into_view_if_needed(self, **kwargs):
        self._page.scrolled.append(self._selector)

    def click(self, timeout=None):
        self._page.clicked.append((self._selector, timeout))

    def inner_html(self) -> str:
        return self._page.texts.get(self._selector, "")

    def inner_text(self) -> str:
        return self._page.texts.get(self._selector, "")

    def locator(self, selector):
        return FakeLocator(self._page, selector)


class FakeContext:
    def __init__(self, desc_page=None):
        self.desc_page = desc_page
        self.new_pages: list = []

    def new_page(self):
        page = self.desc_page if self.desc_page is not None else FakePage()
        self.new_pages.append(page)
        return page


class FakePage:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.texts: dict[str, str] = {}
        self.waits: list[str] = []
        self.timeouts: list[int] = []
        self.clicked: list[tuple] = []
        self.scrolled: list[str] = []
        self.closed = False
        self.url = ""
        self.goto_calls: list[tuple] = []
        self.context = FakeContext()

    def locator(self, selector):
        return FakeLocator(self, selector)

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.goto_calls.append((url, wait_until, timeout))

    def close(self):
        self.closed = True

    def wait_for_selector(self, selector, timeout=None):
        self.waits.append(selector)
        self.timeouts.append(timeout)
        # Timeout simulado de Playwright/patchright (el nombre de la clase es
        # lo que inspecciona el parser para no tratarlo como fallo inesperado).
        raise TimeoutError("timeout simulado")


def _offer(jk: str) -> JobOffer:
    return JobOffer(job_key=jk)


def _card(page: FakePage, jk: str, active: bool = False) -> None:
    page.counts[f'a[data-jk="{jk}"]'] = 1
    if active:
        page.counts[_ACTIVE_SELECTOR.format(jk=jk)] = 1


def test_enrich_does_not_abort_offer_when_panel_wait_times_out(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1")
    offer = _offer("jk1")
    progress: list[tuple[int, int]] = []

    with caplog.at_level(logging.WARNING):
        attempted, success = enrich_offers_via_right_panel(
            page,
            [offer],
            domain="nl.indeed.com",
            rate=1.0,
            progress_cb=lambda done, total: progress.append((done, total)),
        )

    assert attempted == 1
    assert success == 0
    assert offer.description_html == ""
    # Cadena completa de esperas: encabezado 4 s -> descripcion 8 s.
    assert page.timeouts == [4000, 8000]
    # Los testids nuevos van primero y los antiguos se conservan de respaldo.
    assert page.waits[0].startswith(_NEW_TITLE_SELECTOR)
    assert '[data-testid="jobsearch-JobInfoHeader-title"]' in page.waits[0]
    assert page.waits[1].startswith(_NEW_DESC_SELECTOR)
    assert _NEW_HEADING_SELECTOR in page.waits[1]
    assert "jobDescriptionText" in page.waits[1]
    # La oferta se registro como fallida (no hay exito silencioso).
    assert any(
        "enriquecimiento fallido jk=jk1" in r.getMessage() for r in caplog.records
    )
    # Latido de progreso emitido por la oferta procesada.
    assert progress == [(1, 1)]
    # /viewjob (prohibido por robots.txt) sigue desactivado por defecto.
    assert page.context.new_pages == []


def test_enrich_fills_description_and_conditions_when_panel_responds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    desc = "<p>" + ("Descripcion completa del puesto " * 4) + "</p>"
    page.counts["#jobDescriptionText"] = 1
    page.texts["#jobDescriptionText"] = desc
    page.counts['[data-testid="jobsearch-OtherJobDetailsContainer"]'] = 1
    page.texts['[data-testid="jobsearch-OtherJobDetailsContainer"]'] = (
        "Jornada completa\nSeguro medico"
    )
    offer = _offer("jk1")

    attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert attempted == 1
    assert success == 1
    assert offer.description_html == desc
    assert offer.description_text
    assert offer.snippet
    assert offer.contract_type == "Jornada completa"
    assert "Seguro" in offer.benefits


def test_enrich_reads_new_two_pane_layout_without_old_selectors(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    desc = "<p>" + ("Descripcion del puesto en la UI nueva " * 4) + "</p>"
    page.counts[_NEW_DESC_SELECTOR] = 1
    page.texts[_NEW_DESC_SELECTOR] = desc
    page.counts[_NEW_COND_SELECTOR] = 1
    page.texts[_NEW_COND_SELECTOR] = "Jornada completa\nSeguro medico"
    offer = _offer("jk1")

    attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 1)
    assert offer.description_html == desc
    assert offer.description_text
    assert offer.snippet
    assert offer.contract_type == "Jornada completa"
    assert "Seguro" in offer.benefits
    # Los selectores del layout antiguo no hicieron falta (count 0).
    assert "#jobDescriptionText" not in page.counts


def test_enrich_reads_parent_of_new_description_heading(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    # Solo existe el marcador del heading; su contenedor padre lleva el HTML.
    page.counts[_NEW_HEADING_SELECTOR] = 1
    page.texts[_NEW_HEADING_SELECTOR] = "Volledige vacaturetekst"
    desc = "<p>" + ("Descripcion bajo el heading " * 6) + "</p>"
    page.counts["xpath=ancestor::div[1]"] = 1
    page.texts["xpath=ancestor::div[1]"] = desc
    offer = _offer("jk1")

    attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 1)
    assert offer.description_html == desc


def test_enrich_still_reads_legacy_layout(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    desc = "<p>" + ("Descripcion del layout antiguo " * 4) + "</p>"
    page.counts['[data-testid="jobDescriptionText"]'] = 1
    page.texts['[data-testid="jobDescriptionText"]'] = desc
    offer = _offer("jk1")

    attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 1)
    assert offer.description_html == desc


def test_enrich_records_failure_after_card_without_highlight(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk2", active=False)
    offer = _offer("jk2")

    with caplog.at_level(logging.WARNING):
        attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 0)
    assert any(
        "tarjeta sin vjs-highlight" in r.getMessage() for r in caplog.records
    )


def test_enrich_reports_missing_card_as_failure(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    offer = _offer("jk3")

    with caplog.at_level(logging.WARNING):
        attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 0)
    assert any(
        "no se encontro tarjeta clicable" in r.getMessage() for r in caplog.records
    )


def test_enrich_emits_progress_for_every_offer(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    progress: list[tuple[int, int]] = []

    enrich_offers_via_right_panel(
        page,
        [_offer("a"), _offer("b")],
        rate=1.0,
        progress_cb=lambda done, total: progress.append((done, total)),
    )

    assert progress == [(1, 2), (2, 2)]


def test_viewjob_fallback_only_runs_when_explicitly_enabled(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    desc_page = FakePage()
    vj_desc = "<p>" + ("Descripcion via viewjob " * 4) + "</p>"
    desc_page.counts["#jobDescriptionText, [data-testid='jobDescriptionText']"] = 1
    desc_page.texts["#jobDescriptionText, [data-testid='jobDescriptionText']"] = vj_desc
    page = FakePage()
    page.context = FakeContext(desc_page=desc_page)
    _card(page, "jk4")
    offer = _offer("jk4")

    attempted, success = enrich_offers_via_right_panel(
        page,
        [offer],
        domain="es.indeed.com",
        rate=1.0,
        allow_viewjob_fallback=True,
    )

    assert (attempted, success) == (1, 1)
    assert offer.description_html == vj_desc
    assert desc_page.goto_calls
    assert desc_page.goto_calls[0][0] == "https://es.indeed.com/viewjob?jk=jk4"
    assert page.context.new_pages == [desc_page]
