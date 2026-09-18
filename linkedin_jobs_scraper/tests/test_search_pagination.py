"""Tests unitarios del fix de paginacion auth en search.py.

Verifica que run_search acumula IDs unicos entre paginas (LinkedIn reemplaza
las tarjetas al cambiar de pagina — no las acumula en el DOM).
Mock completa: no requiere navegador ni red.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.parse_serp import SerpCardRaw
from src.search import run_search


def _make_card(job_id: str, page_num: int = 1) -> SerpCardRaw:
    """Fabrica una SerpCardRaw con titulo para tracking."""
    return SerpCardRaw(
        job_id=job_id,
        job_url=f"https://linkedin.com/jobs/view/{job_id}/",
        title=f"Card {job_id} (pag {page_num})",
        company_name=f"ACME {page_num}",
        location_raw=f"Location {page_num}",
        posted_datetime=None,
        posted_relative="",
        is_new=False,
        work_mode="",
    )


# Fixture para inyectar has_next_page/clic_next_page/parse_serp como mocks
# sin modificar el codigo productivo. Mockeamos las funciones importadas
# en search.py via monkeypatch.


def _default_config():
    return {
        "jobs_per_search": 50,
        "max_pages": 10,
        "delays": {
            "between_scrolls": [0.01, 0.02],
            "between_details": [0.01, 0.02],
            "between_searches": [0.01, 0.02],
            "scroll_step_px": [200, 400],
        },
    }


def _fake_run_search(page, role, city_name, city_cfg, config,
                     serp_pages: list[list[SerpCardRaw]],
                     next_page_available: int = 2) -> dict:
    """Ejecuta run_search con paginas SERP fakeadas via monkeypatch.

    serp_pages[0] son cards de pag 1, serp_pages[1] de pag 2, ...
    next_page_available: cuantas paginas tienen boton Siguiente.
    page_num no muestra has_next_page cuando page_num >= next_page_available.
    """
    # Mock parse_serp para devolver cada pagina secuencialmente
    call_count = [0]
    def _fake_parse(_page, _role, _city, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(serp_pages):
            return serp_pages[idx]
        return []

    # Mock has_next_page: devuelve True para las primeras N paginas
    def _fake_has_next(_page):
        return call_count[0] < next_page_available

    # Mock click_next_page: simplemente incrementa contador
    def _fake_click_next(_page, **kwargs):
        return True

    # Mock _check_block: no-op
    def _fake_check_block(_page):
        pass

    # Mock funciones de deteccion de layout y contador
    def _fake_detect_layout(_page):
        return "auth"

    def _fake_get_result_count(_page):
        return "Mas de 500 resultados"

    def _fake_count_cards(_page, _layout):
        return 7

    with (
        patch("src.search.parse_serp", side_effect=_fake_parse),
        patch("src.search.has_next_page", side_effect=_fake_has_next),
        patch("src.search.click_next_page", side_effect=_fake_click_next),
        patch("src.search._check_block", side_effect=_fake_check_block),
        patch("src.search.detect_layout", side_effect=_fake_detect_layout),
        patch("src.search.get_result_count", side_effect=_fake_get_result_count),
        patch("src.search._count_loaded_cards", side_effect=_fake_count_cards),
        patch("src.search._wait_for_results", return_value=True),
        patch("src.search.delay", return_value=None),
        patch("src.search.mouse_jitter", return_value=None),
        patch("src.search.scroll_slow", return_value=None),
    ):
        return run_search(page, role, city_name, city_cfg, config)


def test_pagination_accumulates_unique_ids_across_pages(monkeypatch):
    """Verifica que run_search acumula IDs unicos entre paginas (fix del bug)."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # Pagina 1: cards 100,101,102
    # Pagina 2: cards 103,104,105
    # Pagina 3: cards 106,107,108
    serp_pages = [
        [_make_card("100", 1), _make_card("101", 1), _make_card("102", 1)],
        [_make_card("103", 2), _make_card("104", 2), _make_card("105", 2)],
        [_make_card("106", 3), _make_card("107", 3), _make_card("108", 3)],
    ]

    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        _default_config(),
        serp_pages=serp_pages,
        next_page_available=3,
    )

    cards = result["cards_raw"]
    assert result["cards_loaded"] == 9
    assert len(cards) == 9
    all_ids = {c.job_id for c in cards}
    assert all_ids == {"100", "101", "102", "103", "104", "105", "106", "107", "108"}


def test_pagination_dedups_repeated_ids_across_pages(monkeypatch):
    """Si una pagina devuelve IDs ya vistos, no los duplica en all_cards."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # Pagina 2 tiene IDs repetidos de la pagina 1 -> deben filtrarse
    serp_pages = [
        [_make_card("100", 1), _make_card("101", 1), _make_card("102", 1)],
        [_make_card("100", 2), _make_card("101", 2), _make_card("103", 2)],
    ]

    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        _default_config(),
        serp_pages=serp_pages,
        next_page_available=2,
    )

    cards = result["cards_raw"]
    ids = [c.job_id for c in cards]
    # Esperado: 100, 101 de pag 1; 103 de pag 2; sin dupes 100/101
    assert ids == ["100", "101", "102", "103"]


def test_pagination_stops_when_no_new_ids_three_times(monkeypatch):
    """Tres paginas consecutivas sin IDs nuevos -> break (pagina trampa)."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # Pag 1: 3 cards
    # Pag 2-4: todas repetidas -> 3 sin IDs nuevos -> break
    serp_pages = [
        [_make_card("100", 1), _make_card("101", 1), _make_card("102", 1)],
        [_make_card("100", 2)],   # sin IDs nuevos (cuenta 1/3)
        [_make_card("101", 3)],   # sin IDs nuevos (cuenta 2/3)
        [_make_card("102", 4)],   # sin IDs nuevos (cuenta 3/3 -> break)
        [_make_card("200", 5)],   # nunca se alcanza
    ]

    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        _default_config(),
        serp_pages=serp_pages,
        next_page_available=5,
    )

    # Solo pagina 1 aporto IDs nuevos
    assert result["cards_loaded"] == 3


def test_pagination_stops_when_no_next_page(monkeypatch):
    """has_next_page devuelve False -> break inmediato."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    serp_pages = [
        [_make_card("100", 1), _make_card("101", 1)],
    ]

    # next_page_available=0 -> has_next_page False desde el inicio
    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        _default_config(),
        serp_pages=serp_pages,
        next_page_available=0,
    )

    assert result["cards_loaded"] == 2


def test_pagination_respects_max_pages_cap(monkeypatch):
    """max_pages cap se respeta aunque haya mas paginas disponibles."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # 10 paginas de 3 cards cada una, sin IDs repetidos
    serp_pages = [
        [_make_card(f"{100+i*3+j}", i+1) for j in range(3)]
        for i in range(20)
    ]

    config = _default_config()
    config["jobs_per_search"] = 9999  # nunca alcanzar target
    config["max_pages"] = 5           # cap bajo para el test

    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        config,
        serp_pages=serp_pages,
        next_page_available=20,
    )

    # max_pages = 5 -> pagina 0 + 4 paginadas = 5 paginas parseadas * 3 cards = 15
    assert result["cards_loaded"] == 15


def test_pagination_stops_on_target_reached(monkeypatch):
    """Alcanzar target (jobs_per_search) detiene la paginacion temprano."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # 10 paginas con cards unicas
    serp_pages = [
        [_make_card(f"{100+i*3+j}", i+1) for j in range(3)]
        for i in range(10)
    ]

    config = _default_config()
    config["jobs_per_search"] = 9  # target bajo
    config["max_pages"] = 10

    result = _fake_run_search(
        page, "Data Analyst", "Madrid",
        {"text": "Madrid", "geoId": "103374081"},
        config,
        serp_pages=serp_pages,
        next_page_available=10,
    )

    # target=9 -> pag 1:3, pag 2:6, pag 3:9 -> para
    assert result["cards_loaded"] == 9


def test_guest_layout_returns_all_visible_cards(monkeypatch):
    """Guest: todas las tarjetas visibles tras scroll -> parseadas una vez."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    # Guest: una sola pagina con 25 cards (scroll infinito las acumula)
    guest_cards = [_make_card(str(100 + i), 1) for i in range(25)]

    def _fake_parse(_page, _role, _city, **kwargs):
        return guest_cards

    def _fake_detect_layout(_page):
        return "guest"

    with (
        patch("src.search.parse_serp", side_effect=_fake_parse),
        patch("src.search.detect_layout", side_effect=_fake_detect_layout),
        patch("src.search.get_result_count", return_value="25 resultados"),
        patch("src.search._count_loaded_cards", return_value=25),
        patch("src.search._check_block", return_value=None),
        patch("src.search._wait_for_results", return_value=True),
        patch("src.search._is_viewed_all", return_value=False),
        patch("src.search.delay", return_value=None),
        patch("src.search.mouse_jitter", return_value=None),
        patch("src.search.scroll_slow", return_value=None),
    ):
        result = run_search(
            page, "Data Analyst", "Madrid",
            {"text": "Madrid", "geoId": "103374081"},
            _default_config(),
        )

    assert result["cards_loaded"] == 25
    assert len(result["cards_raw"]) == 25
    ids = {c.job_id for c in result["cards_raw"]}
    assert len(ids) == 25


def test_guest_loop_clicks_see_more_and_detects_no_growth():
    """Guest: intenta click en 'See more jobs' y para sin crecimiento."""
    from src.parse_serp import SEL_SHOW_MORE_GUEST

    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    guest_cards = [_make_card("100"), _make_card("101"), _make_card("102")]
    seq = [3, 3, 4, 6, 6, 10, 10, 10, 10, 10, 10]
    idx = [0]

    def _fake_count(_page, _layout):
        i = idx[0]
        idx[0] += 1
        return seq[min(i, len(seq) - 1)]

    def _fake_parse(_page, _role, _city, **kwargs):
        return guest_cards

    def _fake_detect_layout(_page):
        return "guest"

    with (
        patch("src.search.parse_serp", side_effect=_fake_parse),
        patch("src.search.detect_layout", side_effect=_fake_detect_layout),
        patch("src.search.get_result_count", return_value="10 resultados"),
        patch("src.search._count_loaded_cards", side_effect=_fake_count),
        patch("src.search._check_block", return_value=None),
        patch("src.search._wait_for_results", return_value=True),
        patch("src.search._is_viewed_all", return_value=False),
        patch("src.search.delay", return_value=None),
        patch("src.search.mouse_jitter", return_value=None),
        patch("src.search.scroll_slow", return_value=None) as scroll_slow,
    ):
        result = run_search(
            page, "Data Analyst", "Madrid",
            {"text": "Madrid", "geoId": "103374081"},
            _default_config(),
        )

    show_more_calls = [
        c for c in page.query_selector.call_args_list
        if c.args and c.args[0] == SEL_SHOW_MORE_GUEST
    ]
    assert show_more_calls, "El bucle guest debe intentar 'See more jobs'"
    assert scroll_slow.call_count == 0
    assert idx[0] == 9  # 1 conteo inicial + 2 por iteracion * 4 iteraciones
    assert result["cards_loaded"] == 3
    assert len(result["cards_raw"]) == 3
    assert result["layout"] == "guest"


def test_guest_loop_stops_on_viewed_all():
    """Guest: 'See more jobs' visto completo -> el bucle para al instante."""
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/search/..."

    guest_cards = [_make_card("200"), _make_card("201")]
    viewed_all = [False, True]

    def _fake_viewed(_page):
        return viewed_all.pop(0) if viewed_all else True

    def _fake_parse(_page, _role, _city, **kwargs):
        return guest_cards

    def _fake_detect_layout(_page):
        return "guest"

    with (
        patch("src.search.parse_serp", side_effect=_fake_parse),
        patch("src.search.detect_layout", side_effect=_fake_detect_layout),
        patch("src.search.get_result_count", return_value="2 resultados"),
        patch("src.search._count_loaded_cards", return_value=2),
        patch("src.search._check_block", return_value=None),
        patch("src.search._wait_for_results", return_value=True),
        patch("src.search._is_viewed_all", side_effect=_fake_viewed) as is_viewed,
        patch("src.search.delay", return_value=None),
        patch("src.search.mouse_jitter", return_value=None),
        patch("src.search.scroll_slow", return_value=None),
    ):
        result = run_search(
            page, "Data Analyst", "Madrid",
            {"text": "Madrid", "geoId": "103374081"},
            _default_config(),
        )

    assert is_viewed.call_count == 2  # 1 chequeo por iteracion; corta en el 2
    assert result["cards_loaded"] == 2
    assert len(result["cards_raw"]) == 2
