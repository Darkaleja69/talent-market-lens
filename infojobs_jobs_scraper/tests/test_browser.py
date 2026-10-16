"""Offline tests for the browser factory and its two launch modes (T-30).

CDP mode (``INFOJOBS_CDP_URL``) connects to a Chrome launched outside
Playwright and must *disconnect* on shutdown, never close the external
browser's context; persistent mode keeps the previous behaviour. No browser
and no network: patchright is replaced by doubles.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import scraper.browser as browser_mod
from scraper.browser import close_browser_context, get_browser_context


class _FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, contexts):
        self.contexts = contexts
        self.closed = False

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self):
        self.external = _FakeBrowser([_FakeContext()])
        self.cdp_urls: list[str] = []
        self.persistent_context = _FakeContext()

    def connect_over_cdp(self, url):
        self.cdp_urls.append(url)
        return self.external

    def launch_persistent_context(self, **kwargs):
        raise AssertionError("persistent context must not be used in CDP mode")


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()
        self.stopped = False

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _reset_cdp_state():
    browser_mod._connected_browser = None
    yield
    browser_mod._connected_browser = None


def _fake_sync_playwright(fake_pw: _FakePlaywright):
    return lambda: SimpleNamespace(start=lambda: fake_pw)


def test_cdp_mode_reuses_the_existing_context(monkeypatch):
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setattr(
        browser_mod, "sync_playwright", _fake_sync_playwright(fake_pw)
    )

    playwright, context = get_browser_context()

    assert playwright is fake_pw
    assert context is fake_pw.chromium.external.contexts[0]
    assert fake_pw.chromium.cdp_urls == ["http://127.0.0.1:9333"]


def test_cdp_shutdown_disconnects_without_killing_chrome(monkeypatch):
    fake_pw = _FakePlaywright()
    monkeypatch.setattr(browser_mod, "CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setattr(
        browser_mod, "sync_playwright", _fake_sync_playwright(fake_pw)
    )
    playwright, context = get_browser_context()

    close_browser_context(playwright, context)

    assert fake_pw.chromium.external.closed is True
    assert context.closed is False  # external Chrome keeps its context
    assert fake_pw.stopped is True


def test_persistent_mode_closes_its_own_context(monkeypatch):
    fake_pw = _FakePlaywright()
    context = fake_pw.chromium.persistent_context
    monkeypatch.setattr(browser_mod, "CDP_URL", "")
    monkeypatch.setattr(
        browser_mod, "sync_playwright", _fake_sync_playwright(fake_pw)
    )
    monkeypatch.setattr(
        browser_mod, "launch_persistent_context", lambda _pw: context
    )

    playwright, returned = get_browser_context()
    close_browser_context(playwright, returned)

    assert returned is context
    assert context.closed is True
    assert fake_pw.stopped is True


def test_get_browser_context_stops_driver_when_cdp_fails(monkeypatch):
    class _BrokenChromium:
        def connect_over_cdp(self, url):
            raise RuntimeError("CDP no responde")

    class _BrokenPlaywright:
        def __init__(self):
            self.chromium = _BrokenChromium()
            self.stopped = False

        def stop(self):
            self.stopped = True

    broken_pw = _BrokenPlaywright()
    monkeypatch.setattr(browser_mod, "CDP_URL", "http://127.0.0.1:9333")
    monkeypatch.setattr(
        browser_mod, "sync_playwright", _fake_sync_playwright(broken_pw)
    )

    with pytest.raises(RuntimeError, match="CDP no responde"):
        get_browser_context()
    assert broken_pw.stopped is True
