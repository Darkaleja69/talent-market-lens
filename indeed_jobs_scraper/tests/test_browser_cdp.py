"""Tests offline del handshake CDP, reintentos y fallback (T-32).

Sin navegador ni red: ``open_cdp_browser`` se ejercita con un patchright falso
inyectado (conexiones que fallan, conectan pero no responden, o responden) y
``BrowserSession`` se prueba monkeypatcheando ``scraper.browser.open_cdp_browser``
o con una secuencia de instancias Playwright falsas. El fallback degrada a
navegador propio headed y el runner limita a 1 pagina.

Iteracion 2 (run 2026-10-11): si el driver de patchright muere, la fase CDP se
aborta sin mas intentos (un connect contra el driver muerto puede colgarse) y
``BrowserSession`` descarta la instancia Playwright y arranca una nueva para el
fallback; los timeouts normales siguen reintentando.
"""
from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest

from scraper import browser as browser_mod
from scraper.browser import (
    BrowserSession,
    discard_playwright,
    has_driver_dead_marker,
    is_driver_dead_error,
    open_cdp_browser,
    probe_cdp_handshake,
)
from scraper.config import COUNTRIES
from scraper.runner import ScraperRunner


# --- Dobles de patchright ---------------------------------------------------


class FakePage:
    def __init__(self, fail_close: bool = False):
        self.closed = False
        self.fail_close = fail_close
        self.url = ""
        self.goto_calls: list[tuple] = []

    def close(self):
        if self.fail_close:
            raise RuntimeError("close fallo")
        self.closed = True

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        self.goto_calls.append((url, wait_until, timeout))


class FakeContext:
    def __init__(self, fail_new_page: bool = False, new_page_error: Exception | None = None):
        self.fail_new_page = fail_new_page
        self.new_page_error = new_page_error
        self.pages: list[FakePage] = []
        self.closed = False

    def new_page(self) -> FakePage:
        if self.new_page_error is not None:
            raise self.new_page_error
        if self.fail_new_page:
            raise RuntimeError("Network.setCacheDisabled ... session closed")
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class FakeBrowser:
    """Navegador conectado por CDP."""

    def __init__(self, contexts=None, fail_new_page: bool = False):
        self.contexts = list(contexts) if contexts is not None else []
        self.fail_new_page = fail_new_page
        self.closed = False
        self.new_contexts: list[FakeContext] = []

    def new_context(self) -> FakeContext:
        context = FakeContext(fail_new_page=self.fail_new_page)
        self.new_contexts.append(context)
        return context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[tuple] = []

    def connect_over_cdp(self, endpoint, timeout=None):
        self.calls.append((endpoint, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakePlaywright:
    def __init__(self, connect_results, launch_results=None):
        self.chromium = FakeChromium(connect_results)
        self.chromium.launch = self.launch
        self.launch_results = list(launch_results or [])
        self.launch_calls: list[dict] = []
        self.stopped = False

    def stop(self):
        self.stopped = True

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        if self.launch_results:
            result = self.launch_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return FakeOwnedBrowser()

    def launch_persistent_context(self, **kwargs):  # pragma: no cover - no usado
        raise AssertionError("el fallback no debe usar perfil persistente en los tests")


class FakeOwnedContext(FakeContext):
    def __init__(self):
        super().__init__()
        self.init_scripts: list[str] = []

    def add_init_script(self, script):
        self.init_scripts.append(script)


class FakeOwnedBrowser(FakeBrowser):
    def __init__(self):
        super().__init__()
        self.owned_context = FakeOwnedContext()
        self.new_context_kwargs: dict | None = None

    def new_context(self, **kwargs):
        self.new_context_kwargs = kwargs
        return self.owned_context


class FakeStarter:
    def __init__(self, playwright):
        self._playwright = playwright

    def start(self):
        return self._playwright


class FakeSyncPlaywrightFactory:
    """Doble de ``sync_playwright``: una instancia nueva por cada llamada.

    Cada ``sync_playwright()`` devuelve un starter que entrega la siguiente
    instancia de la lista. Si el codigo pide mas instancias que las previstas,
    el doble falla (senal de que no se creo exactamente una instancia nueva).
    """

    def __init__(self, playwrights):
        self._pending = list(playwrights)
        self.starters: list[FakeStarter] = []

    def __call__(self) -> FakeStarter:
        if not self._pending:
            raise AssertionError("el doble no tiene mas instancias de Playwright")
        starter = FakeStarter(self._pending.pop(0))
        self.starters.append(starter)
        return starter


# --- probe_cdp_handshake ----------------------------------------------------


def test_probe_handshake_uses_an_existing_context():
    context = FakeContext()
    browser = FakeBrowser([context])

    ok, detail = probe_cdp_handshake(browser)

    assert ok is True
    assert "1 contexto" in detail
    assert context.pages and context.pages[0].closed is True
    assert browser.new_contexts == []


def test_probe_handshake_creates_and_closes_a_temporary_context():
    browser = FakeBrowser([])

    ok, detail = probe_cdp_handshake(browser)

    assert ok is True
    assert browser.new_contexts and browser.new_contexts[0].closed is True


def test_probe_handshake_detects_broken_session():
    browser = FakeBrowser([FakeContext(fail_new_page=True)])

    ok, detail = probe_cdp_handshake(browser)

    assert ok is False
    assert "session closed" in detail


def test_probe_handshake_never_raises_when_contexts_fail():
    class ExplodingBrowser:
        @property
        def contexts(self):
            raise RuntimeError("CDP caido")

    ok, detail = probe_cdp_handshake(ExplodingBrowser())

    assert ok is False
    assert "contextos" in detail


# --- open_cdp_browser: reintentos + handshake -------------------------------


def _sleep_recorder():
    slept: list[float] = []

    def _sleep(seconds):
        slept.append(seconds)
        return None

    return slept, _sleep


def test_open_cdp_browser_retries_until_handshake_is_verified():
    fresh = FakeBrowser([FakeContext()])
    broken = FakeBrowser([FakeContext(fail_new_page=True)])
    pw = FakePlaywright(
        [RuntimeError("timeout"), broken, fresh],
        launch_results=[],
    )
    slept, sleep_fn = _sleep_recorder()

    browser = open_cdp_browser(
        pw, 9222, attempts=3, timeout_ms=20000, backoff_s=(1.0, 1.0), sleep_fn=sleep_fn
    )

    assert browser is fresh
    assert broken.closed is True
    assert len(pw.chromium.calls) == 3
    assert pw.chromium.calls[0][0] == "http://localhost:9222"
    assert pw.chromium.calls[0][1] == 20000
    assert slept == [1.0, 1.0]


def test_open_cdp_browser_returns_none_when_every_attempt_fails():
    pw = FakePlaywright([RuntimeError("x"), RuntimeError("y"), RuntimeError("z")])
    slept, sleep_fn = _sleep_recorder()

    browser = open_cdp_browser(
        pw, 9222, attempts=3, backoff_s=(2.0, 2.0), sleep_fn=sleep_fn
    )

    assert browser is None
    assert len(pw.chromium.calls) == 3
    assert slept == [2.0, 2.0]


def test_open_cdp_browser_does_not_sleep_after_the_last_attempt():
    pw = FakePlaywright([RuntimeError("x")])
    slept, sleep_fn = _sleep_recorder()

    assert open_cdp_browser(pw, 9222, attempts=1, sleep_fn=sleep_fn) is None
    assert slept == []


def test_is_driver_dead_error_matches_crash_signals():
    class TargetClosedError(RuntimeError):
        pass

    class ProtocolError(RuntimeError):
        pass

    assert is_driver_dead_error(
        RuntimeError(
            "BrowserType.connect_over_cdp: Connection closed while reading "
            "from the driver"
        )
    )
    assert is_driver_dead_error(TargetClosedError("target closed"))
    assert is_driver_dead_error(ProtocolError("Network.setCacheDisabled failed"))


def test_is_driver_dead_error_ignores_normal_timeouts():
    assert (
        is_driver_dead_error(
            RuntimeError("BrowserType.connect_over_cdp: Timeout 20000ms exceeded.")
        )
        is False
    )
    assert has_driver_dead_marker("la operacion minima fallo: timeout de pagina") is False


def test_open_cdp_browser_aborts_immediately_when_driver_dies():
    pw = FakePlaywright(
        [
            RuntimeError(
                "BrowserType.connect_over_cdp: Connection closed while reading "
                "from the driver"
            )
        ]
    )
    slept, sleep_fn = _sleep_recorder()

    browser = open_cdp_browser(pw, 9222, attempts=3, sleep_fn=sleep_fn)

    assert browser is None
    assert len(pw.chromium.calls) == 1  # sin mas intentos CDP
    assert slept == []  # ni backoff: la instancia ya no sirve


def test_open_cdp_browser_aborts_when_probe_reports_a_dead_driver():
    dead = FakeBrowser(
        [
            FakeContext(
                new_page_error=RuntimeError(
                    "Target page, context or browser has been closed"
                )
            )
        ]
    )
    pw = FakePlaywright([dead])
    slept, sleep_fn = _sleep_recorder()

    browser = open_cdp_browser(pw, 9222, attempts=3, sleep_fn=sleep_fn)

    assert browser is None
    assert dead.closed is True  # desconectado antes de abortar
    assert len(pw.chromium.calls) == 1
    assert slept == []


def test_open_cdp_browser_keeps_retrying_on_connect_timeouts():
    pw = FakePlaywright(
        [
            RuntimeError("BrowserType.connect_over_cdp: Timeout 20000ms exceeded."),
            RuntimeError("BrowserType.connect_over_cdp: Timeout 20000ms exceeded."),
            RuntimeError("BrowserType.connect_over_cdp: Timeout 20000ms exceeded."),
        ]
    )
    slept, sleep_fn = _sleep_recorder()

    browser = open_cdp_browser(pw, 9222, attempts=3, backoff_s=(2.0, 2.0), sleep_fn=sleep_fn)

    assert browser is None
    assert len(pw.chromium.calls) == 3
    assert slept == [2.0, 2.0]


# --- discard_playwright: descarte de la instancia muerta ---------------------


def test_discard_playwright_stops_a_healthy_instance():
    pw = FakePlaywright([])

    discard_playwright(pw)

    assert pw.stopped is True


def test_discard_playwright_never_raises_when_the_driver_died():
    class DeadPlaywright:
        def stop(self):
            raise RuntimeError("Connection closed while reading from the driver")

    discard_playwright(DeadPlaywright())  # no debe propagar la excepcion


def test_discard_playwright_accepts_none():
    discard_playwright(None)


# --- BrowserSession: fallback a navegador propio ----------------------------


def _install_fake_patchright(monkeypatch, pw):
    module = types.SimpleNamespace(sync_playwright=lambda: FakeStarter(pw))
    monkeypatch.setitem(sys.modules, "patchright.sync_api", module)


def _install_fake_patchright_factory(monkeypatch, factory):
    module = types.SimpleNamespace(sync_playwright=factory)
    monkeypatch.setitem(sys.modules, "patchright.sync_api", module)


def test_browser_session_falls_back_to_owned_browser(monkeypatch, tmp_path, caplog):
    discarded_pw = FakePlaywright([])
    owned_pw = FakePlaywright([])
    factory = FakeSyncPlaywrightFactory([discarded_pw, owned_pw])
    _install_fake_patchright_factory(monkeypatch, factory)
    monkeypatch.setattr(browser_mod, "open_cdp_browser", lambda *a, **k: None)

    session = BrowserSession(COUNTRIES["ES"], tmp_path, cdp_port=9222)
    with caplog.at_level(logging.WARNING):
        session._start()

    assert session.cdp_fallback is True
    assert session.connected_via_cdp is False
    assert len(factory.starters) == 2  # el fallback usa una instancia nueva
    assert discarded_pw.stopped is True
    assert len(owned_pw.launch_calls) == 1
    assert owned_pw.launch_calls[0]["headless"] is False
    assert session._pw is owned_pw
    assert session._context is not None
    assert any("navegador propio headed" in r.getMessage() for r in caplog.records)


def test_browser_session_uses_new_playwright_instance_after_driver_death(
    monkeypatch, tmp_path, caplog
):
    dead_pw = FakePlaywright(
        [
            RuntimeError(
                "BrowserType.connect_over_cdp: Connection closed while reading "
                "from the driver"
            )
        ]
    )
    fresh_pw = FakePlaywright([])
    factory = FakeSyncPlaywrightFactory([dead_pw, fresh_pw])
    _install_fake_patchright_factory(monkeypatch, factory)

    session = BrowserSession(COUNTRIES["ES"], tmp_path, cdp_port=9222)
    with caplog.at_level(logging.WARNING):
        session._start()

    assert session.cdp_fallback is True
    assert session.connected_via_cdp is False
    assert len(dead_pw.chromium.calls) == 1  # no se reintenta contra el driver muerto
    assert dead_pw.stopped is True  # la instancia muerta se descarta
    assert len(factory.starters) == 2
    assert len(fresh_pw.launch_calls) == 1
    assert session._pw is fresh_pw
    assert any("se aborta" in r.getMessage() for r in caplog.records)


def test_browser_session_fallback_failure_is_fast_and_releases_playwright(
    monkeypatch, tmp_path
):
    dead_pw = FakePlaywright(
        [RuntimeError("Connection closed while reading from the driver")]
    )
    broken_pw = FakePlaywright([], launch_results=[RuntimeError("no hay Chrome")])
    factory = FakeSyncPlaywrightFactory([dead_pw, broken_pw])
    _install_fake_patchright_factory(monkeypatch, factory)

    session = BrowserSession(COUNTRIES["ES"], tmp_path, cdp_port=9222)

    with pytest.raises(RuntimeError, match="no hay Chrome"):
        session._start()

    assert dead_pw.stopped is True
    assert broken_pw.stopped is True
    assert session._pw is None


def test_browser_session_without_cdp_uses_a_single_playwright_instance(
    monkeypatch, tmp_path
):
    pw = FakePlaywright([])
    factory = FakeSyncPlaywrightFactory([pw])
    _install_fake_patchright_factory(monkeypatch, factory)

    session = BrowserSession(COUNTRIES["ES"], tmp_path, cdp_port=0)
    session._start()

    assert len(factory.starters) == 1  # sin CDP no se descarta ninguna instancia
    assert session.connected_via_cdp is False
    assert session.cdp_fallback is False
    assert len(pw.launch_calls) == 1
    assert session._context is not None


def test_browser_session_uses_cdp_context_when_handshake_ok(monkeypatch, tmp_path):
    pw = FakePlaywright([])
    _install_fake_patchright(monkeypatch, pw)
    cdp_browser = FakeBrowser([FakeContext()])
    monkeypatch.setattr(browser_mod, "open_cdp_browser", lambda *a, **k: cdp_browser)

    session = BrowserSession(COUNTRIES["ES"], tmp_path, cdp_port=9222)
    session._start()

    assert session.connected_via_cdp is True
    assert session.cdp_fallback is False
    assert pw.launch_calls == []


# --- Runner: paginas efectivas tras el fallback -----------------------------


def test_runner_caps_pages_to_one_after_cdp_fallback(tmp_path, caplog):
    runner = ScraperRunner("NL", ["Rotterdam"], ["data"], 2, tmp_path, cdp_port=9222)
    session = SimpleNamespace(connected_via_cdp=False, cdp_fallback=True)

    with caplog.at_level(logging.WARNING):
        runner._apply_session_mode(session)

    assert runner.pages == 1
    assert runner._cdp_connected is False
    assert any("1 pagina" in r.getMessage() for r in caplog.records)


def test_runner_keeps_pages_when_cdp_handshake_ok(tmp_path):
    runner = ScraperRunner("NL", ["Rotterdam"], ["data"], 2, tmp_path, cdp_port=9222)

    runner._apply_session_mode(SimpleNamespace(connected_via_cdp=True))

    assert runner.pages == 2
    assert runner._cdp_connected is True


def test_runner_without_cdp_is_unaffected(tmp_path):
    runner = ScraperRunner("ES", ["Madrid"], ["data"], 2, tmp_path, cdp_port=0)

    runner._apply_session_mode(SimpleNamespace(connected_via_cdp=False))

    assert runner.pages == 2
    assert runner._cdp_connected is False


def test_runner_without_cdp_single_page_is_unaffected(tmp_path):
    # Modo propio del nightly sin handshake CDP: --pages 1 debe quedar intacto.
    runner = ScraperRunner("ES", ["Madrid"], ["data"], 1, tmp_path, cdp_port=0)

    runner._apply_session_mode(SimpleNamespace(connected_via_cdp=False))

    assert runner.pages == 1
    assert runner._cdp_connected is False


# --- cdp_probe: CLI del pre-flight ------------------------------------------


def test_cdp_probe_reports_ok(monkeypatch, capsys):
    from scraper import cdp_probe

    healthy = FakeBrowser([FakeContext()])
    _install_fake_patchright(monkeypatch, FakePlaywright([]))
    monkeypatch.setattr(cdp_probe, "open_cdp_browser", lambda *a, **k: healthy)

    code = cdp_probe.main(["--port", "9222"])

    assert code == 0
    assert "CDP_HANDSHAKE_OK port=9222" in capsys.readouterr().out
    assert healthy.closed is True


def test_cdp_probe_reports_failure(monkeypatch, capsys):
    from scraper import cdp_probe

    _install_fake_patchright(monkeypatch, FakePlaywright([]))
    monkeypatch.setattr(cdp_probe, "open_cdp_browser", lambda *a, **k: None)

    code = cdp_probe.main(["--port", "9222"])

    assert code == 1
    assert "CDP_HANDSHAKE_FAIL port=9222" in capsys.readouterr().out


def test_enrich_progress_heartbeat_advances_the_progress_counter(tmp_path, caplog):
    runner = ScraperRunner("ES", ["Madrid"], ["data"], 1, tmp_path)

    with caplog.at_level(logging.INFO):
        runner._emit_enrich_progress(1, 3)

    assert runner._progress.parsed == 1
    assert any(
        "PROGRESS source=indeed parsed=1" in r.getMessage() for r in caplog.records
    )
