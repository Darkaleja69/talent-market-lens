"""Tests del canal de navegador opt-in de IrishJobs (T-34, iteracion Akamai).

Sin red ni navegador real: se comprueba que `launch_context` reenvia `channel`
a `launch_persistent_context` solo cuando esta definido (y mantiene las demas
opciones), que la config de irishjobs declara `browser_channel: "chrome"` sin
que stepstone_nl lo herede, y que el scraper pasa el valor de su config tanto
en el arranque como en el relanzamiento de `_ensure_browser_alive`.
"""
from __future__ import annotations

from src.core import browser as browser_mod
from src.irishjobs import scraper as scraper_mod
from src.irishjobs.config_ie import DEFAULT_CONFIG as IRISHJOBS_CFG
from src.irishjobs.scraper import IrishJobsScraper
from src.stepstone_nl.config_nl import DEFAULT_CONFIG as STEPSTONE_CFG


class _FakePage:
    def __init__(self, alive: bool = True):
        self.alive = alive

    def evaluate(self, script, *args, **kwargs):
        if not self.alive:
            raise RuntimeError("page is dead")
        return 1


class _FakeContext:
    def __init__(self):
        self.extra_headers: list[dict] = []
        self.event_handlers: list[tuple] = []
        self.init_scripts: list[str] = []
        self.pages: list[_FakePage] = []
        self.closed = False

    def set_extra_http_headers(self, headers):
        self.extra_headers.append(headers)

    def on(self, event, handler):
        self.event_handlers.append((event, handler))

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self):
        self.calls: list[dict] = []

    def launch_persistent_context(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeContext()


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()


def _patch_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_mod, "PROFILE_DIR", tmp_path)
    monkeypatch.setattr(browser_mod, "_resolve_headless", lambda: False)


def _cfg(base: dict, tmp_path, subdir: str) -> dict:
    cfg = dict(base)
    out_dir = tmp_path / "data" / subdir
    cfg["output"] = {
        "csv": str(out_dir / "output" / "jobs.csv"),
        "parquet": str(out_dir / "output" / "jobs.parquet"),
        "checkpoint_dir": str(out_dir / "checkpoints"),
    }
    return cfg


# --- launch_context: el canal se reenvia solo cuando esta definido ----------

def test_launch_context_forwards_channel_when_defined(tmp_path, monkeypatch):
    _patch_offline(tmp_path, monkeypatch)
    pw = _FakePlaywright()

    context = browser_mod.launch_context(pw, locale="en-GB",
                                         timezone="Europe/Dublin",
                                         profile_name="irishjobs",
                                         channel="chrome")

    call = pw.chromium.calls[0]
    assert call["channel"] == "chrome"
    # Resto de opciones intactas (replica del comportamiento previo).
    assert call["headless"] is False
    assert call["locale"] == "en-GB"
    assert call["timezone_id"] == "Europe/Dublin"
    assert call["ignore_default_args"] == ["--enable-automation"]
    assert call["args"] == [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    assert context.extra_headers == [{"Accept-Language": "en-US,en;q=0.9"}]


def test_launch_context_omits_channel_when_none(tmp_path, monkeypatch):
    _patch_offline(tmp_path, monkeypatch)
    pw = _FakePlaywright()

    browser_mod.launch_context(pw)

    assert "channel" not in pw.chromium.calls[0]


# --- config: solo irishjobs declara el canal -------------------------------

def test_irishjobs_config_declares_chrome_channel():
    assert IRISHJOBS_CFG.get("browser_channel") == "chrome"


def test_stepstone_nl_config_does_not_declare_browser_channel():
    assert "browser_channel" not in STEPSTONE_CFG


# --- scraper: pasa el valor de la config en arranque y relanzamiento -------

def _patch_launch(monkeypatch, calls: dict):
    def fake_launch(pw, **kwargs):
        calls.update(kwargs)
        return _FakeContext()

    monkeypatch.setattr(scraper_mod, "launch_context", fake_launch)


def test_scraper_launch_browser_passes_config_channel(tmp_path, monkeypatch):
    calls: dict = {}
    _patch_launch(monkeypatch, calls)
    scraper = IrishJobsScraper(_cfg(IRISHJOBS_CFG, tmp_path, "irishjobs"))
    scraper._pw = object()

    scraper._launch_browser("en-GB", "Europe/Dublin", "irishjobs")

    assert calls["channel"] == "chrome"
    assert calls["profile_name"] == "irishjobs"
    assert scraper._browser_alive is True


def test_scraper_without_channel_config_passes_none(tmp_path, monkeypatch):
    calls: dict = {}
    _patch_launch(monkeypatch, calls)
    scraper = IrishJobsScraper(_cfg(STEPSTONE_CFG, tmp_path, "stepstone_nl"))
    scraper._pw = object()

    scraper._launch_browser("en-GB", "Europe/Amsterdam", "stepstone_nl")

    assert calls["channel"] is None


def test_ensure_browser_alive_relaunch_keeps_config_channel(tmp_path, monkeypatch):
    calls: dict = {}
    _patch_launch(monkeypatch, calls)
    monkeypatch.setattr(scraper_mod.time, "sleep", lambda *_: None)
    scraper = IrishJobsScraper(_cfg(IRISHJOBS_CFG, tmp_path, "irishjobs"))
    scraper._pw = object()
    # Pagina muerta: obliga al relanzamiento a traves de _launch_browser.
    scraper._page = _FakePage(alive=False)
    scraper._context = _FakeContext()

    alive = scraper._ensure_browser_alive("Data Analyst", "Dublin",
                                          "en-GB", "Europe/Dublin", "irishjobs")

    assert alive is True
    assert calls["channel"] == "chrome"
