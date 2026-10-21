"""Tests del latido de progreso y la parada por bloqueo de IrishJobs (T-34).

Sin red ni navegador: se ejercita `_process_combo` con un SERP simulado para
comprobar que una pagina de 0 tarjetas (o con todas descartadas por el filtro
de titulo) sigue emitiendo PROGRESS y que un bloqueo detectado para el portal
con motivo y evidencia.
"""
from __future__ import annotations

import logging

from src.irishjobs import scraper as scraper_mod
from src.irishjobs import title_filter
from src.irishjobs.config_ie import DEFAULT_CONFIG as IRISHJOBS_CFG
from src.irishjobs.scraper import EmptyComboGuard, IrishJobsScraper


def _cfg(tmp_path, **overrides):
    cfg = dict(IRISHJOBS_CFG)
    out_dir = tmp_path / "data" / "irishjobs"
    cfg["output"] = {
        "csv": str(out_dir / "output" / "jobs.csv"),
        "parquet": str(out_dir / "output" / "jobs.parquet"),
        "checkpoint_dir": str(out_dir / "checkpoints"),
    }
    cfg.update(overrides)
    return cfg


class _FakeResponse:
    """Respuesta falsa con solo `status` (lo que lee `_process_combo`)."""

    def __init__(self, status: int):
        self.status = status


class _FakePage:
    """Pagina falsa: solo lo que toca `_process_combo`."""

    def __init__(self, response=None):
        self.goto_calls: list[str] = []
        self.url = "https://www.irishjobs.ie/jobs/x?age=7"
        self._response = response

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self.url = url
        return self._response

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def evaluate(self, *args, **kwargs):
        return 1

    def title(self):
        return ""

    def query_selector(self, selector):
        return None


def _offline(monkeypatch):
    monkeypatch.setattr(scraper_mod, "delay", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod, "mouse_jitter", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod, "scroll_slow", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod.serp, "dismiss_cookie_banner", lambda _p: None)


def _run_combo(scraper, **overrides):
    params = dict(
        role="Data Analyst",
        city_name="Ireland (nationwide)",
        location="",
        is_nationwide=True,
        radius=25,
        base_url="https://www.irishjobs.ie",
        search_path="/jobs/{role_slug}",
        date_filter="7",
        do_detail=False,
        max_detail=0,
        max_pages=1,
        max_total=0,
        total_so_far=0,
        jobs_per_search=50,
        post_goto=(0.0, 0.0),
        step_px=(200, 400),
        scroll_pause=(0.0, 0.0),
        between_pages=(0.0, 0.0),
        between_details=(0.0, 0.0),
        site="irishjobs",
        country="IE",
        country_name="Ireland",
        max_retries=1,
        retry_delay=(0.0, 0.0),
    )
    params.update(overrides)
    return scraper._process_combo(**params)


def _card(job_id: str, title: str) -> dict:
    return {
        "job_id": job_id,
        "job_url": f"https://www.irishjobs.ie/job/x-job{job_id}",
        "title": title,
        "company_name": "ACME",
        "location_raw": "Cork, County Cork",
        "posted_datetime": "",
        "posted_relative": "Today",
        "salary_raw": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "EUR",
        "salary_period": "",
        "salary_disclosed": False,
        "work_mode": "",
        "employment_type": "",
        "description_snippet": "",
    }


def test_zero_cards_page_still_emits_progress(tmp_path, monkeypatch, caplog):
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage()
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    monkeypatch.setattr(scraper_mod.serp, "parse_cards",
                        lambda page, base_url="": [])
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (False, "no_results", {"resultlist": True}))
    caplog.set_level(logging.INFO, logger="irishjobs")

    total, details = _run_combo(scraper)

    assert (total, details) == (0, 0)
    assert scraper._progress.parsed >= 1
    assert scraper._combo_empty is True
    assert scraper._blocked_reason == ""
    lines = [r.getMessage() for r in caplog.records
             if "PROGRESS source=irishjobs" in r.getMessage()]
    assert len(lines) == 1


def test_combo_with_cards_then_empty_page_is_not_empty(tmp_path, monkeypatch):
    """Un combo con tarjetas en la pagina 1 y vacia en la 2 NO es un combo vacio."""
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage()
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    calls = {"n": 0}

    def fake_parse(page, base_url=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_card("1", "Data Analyst"), _card("2", "Data Engineer")]
        return []

    monkeypatch.setattr(scraper_mod.serp, "parse_cards", fake_parse)
    monkeypatch.setattr(scraper_mod.serp, "has_next_page", lambda page: True)
    monkeypatch.setattr(scraper_mod.serp, "go_to_next_page", lambda page: True)
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (False, "no_results", {"resultlist": True}))

    total, details = _run_combo(scraper, max_pages=2)

    assert (total, details) == (2, 0)
    assert scraper._combo_empty is False
    assert scraper._blocked_reason == ""


def test_combo_empty_only_when_no_page_has_cards(tmp_path, monkeypatch):
    """Sin tarjetas en la pagina 1 el combo si es vacio (candidato a la guarda)."""
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage()
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    monkeypatch.setattr(scraper_mod.serp, "parse_cards",
                        lambda page, base_url="": [])
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (False, "no_results", {"resultlist": True}))

    total, _ = _run_combo(scraper)

    assert total == 0
    assert scraper._combo_empty is True


def test_title_filter_drops_do_not_freeze_progress(tmp_path, monkeypatch):
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage()
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    monkeypatch.setattr(
        scraper_mod.serp, "parse_cards",
        lambda page, base_url="": [_card("1", "Warehouse Technician"),
                                   _card("2", "Sales Executive")])
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (False, "", {"resultlist": True}))

    total, _ = _run_combo(scraper)

    assert total == 0
    # Cuenta tarjetas parseadas ANTES del filtro de titulo (2, no 0).
    assert scraper._progress.parsed == 2
    assert scraper._progress.new == 0


def test_blocked_page_logs_reason_and_dumps_evidence(tmp_path, monkeypatch, caplog):
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage()
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    markers = {"resultlist": False, "result_count": False,
               "job_item": False, "job_card": False}
    monkeypatch.setattr(scraper_mod.serp, "parse_cards",
                        lambda page, base_url="": [])
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (True, "challenge:access_denied", markers))
    caplog.set_level(logging.ERROR, logger="irishjobs")

    total, details = _run_combo(scraper)

    assert (total, details) == (0, 0)
    assert scraper._blocked_reason == "challenge:access_denied"
    assert scraper._combo_empty is False
    assert scraper._progress.parsed == 1

    blocked_lines = [r.getMessage() for r in caplog.records
                     if r.getMessage().startswith("BLOCKED ")]
    assert len(blocked_lines) == 1
    assert "source=irishjobs" in blocked_lines[0]
    assert "reason=challenge:access_denied" in blocked_lines[0]

    dumps = list((tmp_path / "data" / "irishjobs").glob("blocked_*.txt"))
    assert len(dumps) == 1
    content = dumps[0].read_text(encoding="utf-8")
    assert "reason=challenge:access_denied" in content
    assert "status=unknown" in content
    assert "markers_absent=job_card,job_item,result_count,resultlist" in content


def test_fallback_navigation_resets_stale_status(tmp_path, monkeypatch, caplog):
    """El fallback de paginacion no arrastra el status de la pagina anterior."""
    scraper = IrishJobsScraper(_cfg(tmp_path))
    scraper._page = _FakePage(response=_FakeResponse(200))
    scraper._title_phrases = title_filter.phrases_from_roles(scraper.config["roles"])
    _offline(monkeypatch)
    calls = {"n": 0}

    def fake_parse(page, base_url=""):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_card("1", "Data Analyst")]
        return []

    markers = {"resultlist": False, "result_count": False,
               "job_item": False, "job_card": False}
    monkeypatch.setattr(scraper_mod.serp, "parse_cards", fake_parse)
    monkeypatch.setattr(scraper_mod.serp, "has_next_page", lambda page: False)
    monkeypatch.setattr(scraper_mod.serp, "go_to_next_page", lambda page: True)
    monkeypatch.setattr(scraper_mod.serp, "detect_block",
                        lambda page: (True, "no_result_markers", markers))
    caplog.set_level(logging.ERROR, logger="irishjobs")

    total, _ = _run_combo(scraper, max_pages=2, jobs_per_search=1)

    assert total == 1
    assert scraper._blocked_reason == "no_result_markers"
    blocked_lines = [r.getMessage() for r in caplog.records
                     if r.getMessage().startswith("BLOCKED ")]
    assert len(blocked_lines) == 1
    assert "status=unknown" in blocked_lines[0]

    dump = next((tmp_path / "data" / "irishjobs").glob("blocked_*.txt"))
    assert "status=unknown" in dump.read_text(encoding="utf-8")


def test_empty_combo_guard_fires_only_after_limit():
    guard = EmptyComboGuard(3)
    assert guard.note(True) is False
    assert guard.note(True) is False
    assert guard.note(True) is True
    assert guard.note(False) is False
    assert guard.streak == 0


def test_empty_combo_guard_disabled_with_zero_limit():
    guard = EmptyComboGuard(0)
    for _ in range(50):
        assert guard.note(True) is False


def test_default_config_enables_block_detection_and_guard(tmp_path):
    assert IRISHJOBS_CFG["detect_block"] is True
    assert IRISHJOBS_CFG["max_consecutive_empty_combos"] == 25
    assert IRISHJOBS_CFG["warmup"] is True

    scraper = IrishJobsScraper(_cfg(tmp_path))
    assert scraper._block_detection is True
    assert scraper._warmup_enabled is True


def test_stepstone_nl_config_does_not_inherit_irishjobs_repairs(tmp_path):
    """stepstone_nl comparte la clase y solo recibe el cambio de latido.

    Sin las claves en su config, la guarda de combos vacios, la deteccion de
    bloqueo y el warm-up quedan desactivados: son de la reparacion de irishjobs.
    """
    from src.stepstone_nl.config_nl import DEFAULT_CONFIG as STEPSTONE_CFG

    assert "detect_block" not in STEPSTONE_CFG
    assert "max_consecutive_empty_combos" not in STEPSTONE_CFG
    assert "warmup" not in STEPSTONE_CFG

    cfg = dict(STEPSTONE_CFG)
    out_dir = tmp_path / "data" / "stepstone_nl"
    cfg["output"] = {
        "csv": str(out_dir / "output" / "jobs.csv"),
        "parquet": str(out_dir / "output" / "jobs.parquet"),
        "checkpoint_dir": str(out_dir / "checkpoints"),
    }
    scraper = IrishJobsScraper(cfg)
    assert scraper._block_detection is False
    assert scraper._warmup_enabled is False
    # La guarda que lee run() con la clave ausente queda desactivada (0).
    assert cfg.get("max_consecutive_empty_combos", 0) == 0


def test_warm_up_loads_homepage_and_dismisses_banner(tmp_path, monkeypatch):
    scraper = IrishJobsScraper(_cfg(tmp_path))
    page = _FakePage()
    scraper._page = page
    dismissed: list[str] = []
    monkeypatch.setattr(scraper_mod, "delay", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod, "mouse_jitter", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod.serp, "dismiss_cookie_banner",
                        lambda p: dismissed.append("dismiss"))

    scraper._warm_up("https://www.irishjobs.ie", (0.0, 0.0))

    assert page.goto_calls == ["https://www.irishjobs.ie"]
    assert dismissed == ["dismiss"]
