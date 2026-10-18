"""Tests del fallo honesto de navegacion de Glassdoor (spec 004, 20261018).

Sin red ni navegador real: se simulan playwright, `_launch_browser` y
`_process_role` (o la propia pagina de `_process_role`) para comprobar que un
fallo total de navegacion (ningun rol carga la SERP) termina con
`NavigationFailure` -que `__main__` mapea a exit != 0-, con aborto temprano,
marcador `navigation_failed` en el log y un unico relanzamiento acotado. El
run legitimo (SERP cargada, aunque no deje ofertas o solo duplicados) sigue
devolviendo 0 y sin excepcion.
"""
from __future__ import annotations

import importlib
import logging
import sys
import types

import pytest

from src.glassdoor import scraper as scraper_mod
from src.glassdoor.config_gd import DEFAULT_CONFIG
from src.glassdoor.scraper import (
    MAX_CONSECUTIVE_NAVIGATION_FAILURES,
    ROLE_BLOCKED,
    ROLE_LOADED,
    ROLE_NAVIGATION_FAILED,
    GlassdoorScraper,
    NavigationFailure,
)


def _cfg(tmp_path, **overrides):
    cfg = dict(DEFAULT_CONFIG)
    cfg["output"] = {
        "csv": str(tmp_path / "jobs.csv"),
        "parquet": str(tmp_path / "jobs.parquet"),
        "checkpoint_dir": str(tmp_path / "cp"),
    }
    cfg.update(overrides)
    return cfg


def _offline(monkeypatch):
    """Sin esperas ni dependencias de red."""
    monkeypatch.setattr(scraper_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scraper_mod, "delay", lambda *a, **k: None)


class _FakePage:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePlaywright:
    def stop(self):
        pass


def _patch_playwright(monkeypatch):
    """Sustituye `sync_playwright` en origen (el import es local a `run()`)."""

    class _Factory:
        def start(self):
            return _FakePlaywright()

    patched = []
    for modname in ("patchright.sync_api", "playwright.sync_api"):
        try:
            module = importlib.import_module(modname)
        except ImportError:
            continue
        monkeypatch.setattr(module, "sync_playwright", lambda: _Factory())
        patched.append(modname)
    assert patched, "no hay modulo de playwright que parchear"


def _fake_browser(scraper, monkeypatch, launches):
    """Sustituye arranque, warmup y ubicacion; cuenta relanzamientos."""

    def fake_launch(pw, locale, timezone, profile_name, clean=False):
        launches.append(profile_name)
        return _FakeContext(), _FakePage()

    monkeypatch.setattr(scraper, "_launch_browser", fake_launch)
    monkeypatch.setattr(scraper, "_warmup_homepage",
                        lambda *a, **k: ("https://www.glassdoor.ie", ""))
    monkeypatch.setattr(scraper, "_resolve_location_or_fail",
                        lambda *a, **k: (70, "COUNTRY"))


def _scripted_process(scraper, script):
    """`_process_role` simulado.

    `script` es una lista de (outcome, ofertas) consumida por llamada; la
    ultima entrada se repite si hay mas llamadas que entradas.
    """
    state: dict = {"calls": []}

    def fake_process(**kwargs):
        index = min(len(state["calls"]), len(script) - 1)
        outcome, offers = script[index]
        state["calls"].append((kwargs["role"], outcome))
        scraper._role_outcome = outcome
        return offers

    return fake_process, state


def _navigation_failed_markers(caplog):
    return [r.getMessage() for r in caplog.records
            if r.getMessage().startswith("navigation_failed:")]


# --- Camino de fallo: ningun rol carga la SERP ------------------------------


def test_all_roles_navigation_failure_raises_and_aborts_early(
        tmp_path, monkeypatch, caplog):
    scraper = GlassdoorScraper(_cfg(tmp_path))  # 6 roles de DEFAULT_CONFIG
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    process, state = _scripted_process(scraper, [(ROLE_NAVIGATION_FAILED, 0)])
    monkeypatch.setattr(scraper, "_process_role", process)
    caplog.set_level(logging.ERROR, logger="glassdoor")

    with pytest.raises(NavigationFailure):
        scraper.run()

    # Relanzamiento unico: arranque inicial + un solo reintento.
    assert len(launches) == 2
    # Aborto temprano: N roles distintos (el primero con su reintento), no los 6.
    attempted_roles = [role for role, _ in state["calls"]]
    assert len(attempted_roles) == MAX_CONSECUTIVE_NAVIGATION_FAILURES + 1
    assert len(set(attempted_roles)) == MAX_CONSECUTIVE_NAVIGATION_FAILURES
    markers = _navigation_failed_markers(caplog)
    assert len(markers) == 1
    assert (f"navigation_failed: {MAX_CONSECUTIVE_NAVIGATION_FAILURES}"
            in markers[0])
    assert "roles sin SERP" in markers[0]


def test_all_roles_blocked_fails_without_navigation_relaunch(
        tmp_path, monkeypatch, caplog):
    roles = ["Data Analyst", "Data Scientist", "Data Engineer"]
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=roles))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    process, state = _scripted_process(scraper, [(ROLE_BLOCKED, 0)])
    monkeypatch.setattr(scraper, "_process_role", process)
    caplog.set_level(logging.ERROR, logger="glassdoor")

    with pytest.raises(NavigationFailure):
        scraper.run()

    # El relanzamiento es solo para `navigation_failed`: con bloqueo, un
    # unico arranque y sin reintentos.
    assert len(launches) == 1
    assert len(state["calls"]) == len(roles)
    markers = _navigation_failed_markers(caplog)
    assert len(markers) == 1
    assert "blocked" in markers[0]


# --- Caminos legitimos: al menos un rol carga su SERP ------------------------


def test_unknown_roles_abort_early_without_navigation_failure(
        tmp_path, monkeypatch, caplog):
    """Excepcion generica antes de navegar: racha sin SERP, pero sin fallo.

    El rol no llego a intentar el `goto`, asi que `run()` no puede probar un
    fallo de navegacion (`ROLE_UNKNOWN`): aborta temprano por la racha pero
    mantiene la conducta previa (exit 0, sin `NavigationFailure`).
    """
    roles = ["Data Analyst", "Data Scientist", "Data Engineer", "BI"]
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=roles))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    calls: list[str] = []

    def broken_process(**kwargs):
        calls.append(kwargs["role"])
        # Como `_process_role`: arranca sin estado y falla antes del goto.
        scraper._role_outcome = None
        raise RuntimeError("fallo generico antes de la primera SERP")

    monkeypatch.setattr(scraper, "_process_role", broken_process)
    caplog.set_level(logging.ERROR, logger="glassdoor")

    total = scraper.run()

    assert total == 0
    # Aborto temprano por la racha: 3 roles, no los 4 configurados.
    assert len(calls) == MAX_CONSECUTIVE_NAVIGATION_FAILURES
    # Sin relanzamiento de navegacion (no hubo timeout de goto que reintentar).
    assert len(launches) == 1
    assert _navigation_failed_markers(caplog) == []


def test_failed_role_then_loaded_role_finishes_ok(tmp_path, monkeypatch):
    roles = ["Data Analyst", "Data Scientist"]
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=roles))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    process, state = _scripted_process(scraper, [
        (ROLE_NAVIGATION_FAILED, 0),   # rol 1: fallo de navegacion
        (ROLE_NAVIGATION_FAILED, 0),   # rol 1: el reintento tampoco carga
        (ROLE_LOADED, 2),              # rol 2: SERP cargada con 2 ofertas
    ])
    monkeypatch.setattr(scraper, "_process_role", process)

    total = scraper.run()

    assert total == 2
    assert [role for role, _ in state["calls"]] == [
        "Data Analyst", "Data Analyst", "Data Scientist"]
    assert len(launches) == 2


def test_single_relaunch_recovers_role_and_continues(tmp_path, monkeypatch):
    roles = ["Data Analyst", "Data Scientist"]
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=roles))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    process, state = _scripted_process(scraper, [
        (ROLE_NAVIGATION_FAILED, 0),   # rol 1: fallo de navegacion
        (ROLE_LOADED, 1),              # rol 1: el reintento carga la SERP
        (ROLE_LOADED, 1),              # rol 2
    ])
    monkeypatch.setattr(scraper, "_process_role", process)

    total = scraper.run()

    assert total == 2
    assert [role for role, _ in state["calls"]] == [
        "Data Analyst", "Data Analyst", "Data Scientist"]
    assert len(launches) == 2


def test_loaded_serp_without_offers_or_only_duplicates_is_ok(tmp_path,
                                                             monkeypatch):
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=["Data Analyst"]))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    # La SERP carga y el rol aporta 0 nuevas (sin ofertas o solo duplicados).
    process, _ = _scripted_process(scraper, [(ROLE_LOADED, 0)])
    monkeypatch.setattr(scraper, "_process_role", process)

    total = scraper.run()

    assert total == 0
    # Run sano: sin relanzamientos de navegacion.
    assert len(launches) == 1


def test_loaded_role_resets_consecutive_failure_streak(tmp_path, monkeypatch):
    roles = ["Data Analyst", "Data Scientist", "Data Engineer", "BI"]
    scraper = GlassdoorScraper(_cfg(tmp_path, roles=roles))
    _offline(monkeypatch)
    _patch_playwright(monkeypatch)
    launches: list = []
    _fake_browser(scraper, monkeypatch, launches)
    process, state = _scripted_process(scraper, [
        (ROLE_NAVIGATION_FAILED, 0),   # rol 1: fallo (con reintento)
        (ROLE_NAVIGATION_FAILED, 0),
        (ROLE_LOADED, 1),              # rol 2: cargada -> resetea la racha
        (ROLE_NAVIGATION_FAILED, 0),   # rol 3
        (ROLE_NAVIGATION_FAILED, 0),   # rol 4 (< N desde el ultimo loaded)
    ])
    monkeypatch.setattr(scraper, "_process_role", process)

    total = scraper.run()

    # No hay aborto temprano: la racha se corto con el rol cargado.
    assert total == 1
    assert [role for role, _ in state["calls"]] == [
        "Data Analyst", "Data Analyst", "Data Scientist",
        "Data Engineer", "BI"]
    assert len(launches) == 2


# --- Estado por rol en `_process_role` (pagina simulada) ---------------------


class _TimeoutPage(_FakePage):
    url = "https://www.glassdoor.ie/Job/jobs.htm?sc.keyword=Data+Analyst"

    def goto(self, *args, **kwargs):
        raise TimeoutError("Page.goto: Timeout 60000ms exceeded.")

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def evaluate(self, script, *args):
        return ""


class _TextPage(_FakePage):
    def __init__(self, text, url=(
            "https://www.glassdoor.ie/Job/jobs.htm?sc.keyword=Data+Analyst")):
        super().__init__()
        self.text = text
        self.url = url

    def goto(self, *args, **kwargs):
        pass

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def evaluate(self, script, *args):
        if "navigator.userAgent" in script:
            return "test-ua"
        return self.text


def _run_role(scraper, page):
    return scraper._process_role(
        page=page,
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


def test_process_role_marks_navigation_failed_on_goto_timeout(tmp_path,
                                                              monkeypatch):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    _offline(monkeypatch)

    n = _run_role(scraper, _TimeoutPage())

    assert n == 0
    assert scraper._role_outcome == ROLE_NAVIGATION_FAILED


def test_process_role_marks_blocked_on_first_serp(tmp_path, monkeypatch):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    _offline(monkeypatch)

    n = _run_role(scraper, _TextPage("Humans only. Security protections."))

    assert n == 0
    assert scraper._role_outcome == ROLE_BLOCKED


def test_process_role_marks_loaded_on_normal_serp(tmp_path, monkeypatch):
    scraper = GlassdoorScraper(_cfg(tmp_path))
    _offline(monkeypatch)
    monkeypatch.setattr(scraper_mod.serp, "dismiss_overlays", lambda _p: None)
    monkeypatch.setattr(scraper_mod, "mouse_jitter", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod, "scroll_slow", lambda *a, **k: None)
    monkeypatch.setattr(scraper_mod.bff, "search_jobs",
                        lambda *a, **k: ([], "", 0))
    monkeypatch.setattr(scraper_mod.serp, "parse_cards", lambda *a, **k: [])

    n = _run_role(scraper, _TextPage("Ofertas de empleo para Data Analyst"))

    assert n == 0
    assert scraper._role_outcome == ROLE_LOADED


# --- Exit code del proceso (`__main__`) --------------------------------------


def test_main_returns_non_zero_when_run_raises(tmp_path, monkeypatch):
    from src.glassdoor import __main__ as gd_main

    created: list = []

    class _FakeStore:
        def __init__(self):
            self.written = 0

        def rebuild_state(self):
            pass

        def write_all(self):
            self.written += 1

    class _FakeScraper:
        def __init__(self, config):
            self.store = _FakeStore()
            created.append(self)

        def run(self):
            raise NavigationFailure("ninguna SERP cargo")

    monkeypatch.setattr(gd_main, "setup_logging", lambda verbose=False: None)
    monkeypatch.setattr(gd_main, "GlassdoorScraper", _FakeScraper)
    monkeypatch.setattr(sys, "argv", ["glassdoor", "--role", "Data Analyst"])

    assert gd_main.main() == 1
    # El mapeo de `__main__` guarda el store antes de salir con error.
    assert created[0].store.written == 1
