"""Tests offline del enriquecimiento por panel derecho (T-32).

Sin red ni navegador: un ``Page`` falso registra las esperas y los clics, y se
comprueba que un timeout del panel ya no abandona la oferta (se registra como
fallida y el bucle continua), que la cadena de esperas 4 s -> 8 s se ejecuta,
que el latido de progreso se emite por oferta y que el fallback /viewjob
(prohibido por robots.txt) queda desactivado por defecto.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

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

    def is_visible(self) -> bool:
        return self._page.counts.get(self._selector, 0) > 0

    def scroll_into_view_if_needed(self, **kwargs):
        self._page.scrolled.append(self._selector)

    def click(self, timeout=None):
        self._page.clicked.append((self._selector, timeout))
        remaining = self._page.fail_clicks.get(self._selector, 0)
        if remaining:
            self._page.fail_clicks[self._selector] = remaining - 1
            raise TimeoutError("click timeout simulado")

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
        # Clics que fallan (una vez por unidad) por selector: simula el timeout
        # real `Locator.click: Timeout 5000ms` del run 2026-10-18.
        self.fail_clicks: dict[str, int] = {}
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


# --- Reparacion 2026-10-18: rate 1.0 sin saltos, reintento de clic y tope ---


def test_enrich_rate_1_attempts_every_candidate(monkeypatch):
    """Con rate=1.0 no se salta ninguna candidata (causa de las 13 perdidas).

    Ademas, el orden es el de la lista (sin `_random.sample`): con el codigo
    anterior (muestreo aleatorio) el orden de clic era aleatorio.
    """
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    offers = [_offer(f"jk{i}") for i in range(7)]
    for o in offers:
        _card(page, o.job_key, active=True)
    desc = "<p>" + ("Descripcion completa del puesto " * 4) + "</p>"
    page.counts[_NEW_DESC_SELECTOR] = 1
    page.texts[_NEW_DESC_SELECTOR] = desc

    attempted, success = enrich_offers_via_right_panel(page, offers, rate=1.0)

    assert (attempted, success) == (7, 7)
    assert all(o.description_html == desc for o in offers)
    # Orden determinista: jk0, jk1, ... jk6 (el primero de la lista primero).
    assert [sel for sel, _ in page.clicked] == [
        f'a[data-jk="jk{i}"]' for i in range(7)
    ]


def test_enrich_max_enrich_keeps_first_candidates_in_order(monkeypatch, caplog):
    """El recorte por tope es determinista: las 2 primeras, en orden de lista."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    offers = [_offer(f"jk{i}") for i in range(4)]
    for o in offers:
        _card(page, o.job_key, active=True)

    with caplog.at_level(logging.WARNING):
        attempted, _ = enrich_offers_via_right_panel(
            page, offers, rate=1.0, max_enrich=2
        )

    assert attempted == 2
    # Se intentan las 2 primeras candidatas, en el orden de la lista.
    assert [sel for sel, _ in page.clicked] == [
        'a[data-jk="jk0"]',
        'a[data-jk="jk1"]',
    ]
    # El WARNING cuenta las 2 candidatas que quedan pendientes por el tope.
    assert any(
        "quedan 2 candidatas sin descripcion pendientes" in r.getMessage()
        for r in caplog.records
    )


def test_enrich_rate_below_1_logs_skipped_candidates(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    offers = [_offer(f"jk{i}") for i in range(4)]

    with caplog.at_level(logging.INFO):
        attempted, _ = enrich_offers_via_right_panel(page, offers, rate=0.5)

    assert attempted == 2
    assert any("salta 2 candidatas" in r.getMessage() for r in caplog.records)


def test_enrich_warns_when_max_enrich_trims_candidates(monkeypatch, caplog):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    offers = [_offer(f"jk{i}") for i in range(3)]

    with caplog.at_level(logging.WARNING):
        attempted, _ = enrich_offers_via_right_panel(
            page, offers, rate=1.0, max_enrich=2
        )

    assert attempted == 2
    assert any(
        "enriquecimiento limitado a 2 clics" in r.getMessage()
        and "quedan 1" in r.getMessage()
        for r in caplog.records
    )


def test_enrich_retries_click_and_succeeds_on_second_attempt(monkeypatch):
    """El 2o intento (scroll + espera + timeout 10 s) recupera el clic."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    page.fail_clicks['a[data-jk="jk1"]'] = 1  # falla el 1er clic, acierta el 2o
    desc = "<p>" + ("Descripcion tras reintento " * 4) + "</p>"
    page.counts[_NEW_DESC_SELECTOR] = 1
    page.texts[_NEW_DESC_SELECTOR] = desc
    offer = _offer("jk1")

    attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 1)
    assert offer.description_html == desc
    assert [timeout for _, timeout in page.clicked] == [5000, 10000]
    # El 2o intento volvio a hacer scroll de la tarjeta.
    assert 'a[data-jk="jk1"]' in page.scrolled


def test_enrich_records_failure_only_after_both_clicks_fail(monkeypatch, caplog):
    """El jk solo se registra como fallido tras agotar los 2 intentos."""
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    _card(page, "jk1", active=True)
    page.fail_clicks['a[data-jk="jk1"]'] = 2
    offer = _offer("jk1")

    with caplog.at_level(logging.WARNING):
        attempted, success = enrich_offers_via_right_panel(page, [offer], rate=1.0)

    assert (attempted, success) == (1, 0)
    assert offer.description_html == ""
    assert [timeout for _, timeout in page.clicked] == [5000, 10000]
    # Sin clic no se espera el panel: no se leen condiciones de otra oferta.
    assert page.waits == []
    assert any(
        "enriquecimiento fallido jk=jk1: clic agotado tras 2 intentos" in r.getMessage()
        for r in caplog.records
    )


def test_enrich_dismisses_visible_consent_banner_before_clicks(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    page = FakePage()
    page.counts["#onetrust-close-btn-container button"] = 1
    _card(page, "jk1", active=True)
    desc = "<p>" + ("Descripcion con banner cerrado " * 4) + "</p>"
    page.counts[_NEW_DESC_SELECTOR] = 1
    page.texts[_NEW_DESC_SELECTOR] = desc

    attempted, success = enrich_offers_via_right_panel(page, [_offer("jk1")], rate=1.0)

    assert (attempted, success) == (1, 1)
    assert ("#onetrust-close-btn-container button", 3000) in page.clicked


def test_nightly_wrapper_pins_enrich_rate_to_1():
    """Regresion 2026-10-18: volver a 0.9 reintroduciria las candidatas saltadas.

    El nightly es el vector principal del run; con rate<1 el muestreo aleatorio
    vuelve a saltarse candidatas de forma no recuperable en la misma noche.
    """
    script = Path(__file__).resolve().parents[1] / "run_nightly_indeed.ps1"
    content = script.read_text(encoding="utf-8-sig")
    assert re.search(r"^\s*\$EnrichRate\s*=\s*1\.0\b", content, re.M), (
        "run_nightly_indeed.ps1 debe mantener $EnrichRate = 1.0"
    )
