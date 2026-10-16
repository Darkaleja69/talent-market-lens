"""Offline tests for the InfoJobs challenge detection and abort policy (T-28).

No browser and no network: the marker detection is exercised through the pure
``captcha_marker_from_html`` with the real variants observed in T-27, and the
abort policy through page doubles that fail if a reload/goto is attempted.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from scraper.navigator import (
    captcha_marker,
    captcha_marker_from_html,
    handle_captcha,
    session_has_reese84,
)

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "listing_madrid.html"
)
_CLEAN_SERP_URL = "https://www.infojobs.net/ofertas-trabajo/madrid/madrid"

# Real variant observed in T-27 (curl probe): canonical to the Distil captcha,
# h1 «¿Eres humano o un robot?» and GeeTest (gt.js + initGeetest).
_REAL_CHALLENGE_HTML = (
    "<html><head>"
    '<link rel="canonical" '
    'href="https://www.infojobs.net/distil/distil/captcha.xhtml">'
    "</head><body>"
    "<h1>&iquest;Eres humano o un robot?</h1>"
    '<script src="https://static.geetest.com/static/tools/gt.js"></script>'
    "<script>initGeetest({}, function(){});</script>"
    "</body></html>"
)


class _FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        if isinstance(self._cookies, Exception):
            raise self._cookies
        return self._cookies


class _FakePage:
    """Minimal page double: no reload/goto on purpose."""

    def __init__(self, url: str = "", html: str = "", cookies=()):
        self.url = url
        self._html = html
        self.context = _FakeContext(cookies)

    def content(self) -> str:
        return self._html


class _NoContentPage:
    url = "https://www.infojobs.net/distil/distil/captcha.xhtml"

    def content(self) -> str:
        raise RuntimeError("page closed")


_MARKER_CASES = (
    # URL variants: /distil/ wins over the generic captcha URL.
    (
        "url_distil",
        "<html><body></body></html>",
        "https://www.infojobs.net/distil/distil/captcha.xhtml",
    ),
    (
        "url_captcha",
        "<html><body></body></html>",
        "https://www.infojobs.net/ofertas-trabajo/captcha.xhtml",
    ),
    # Canonical link (the marker that fired in the real nightly blocks).
    (
        "canonical_captcha",
        '<html><head><link rel="canonical" '
        'href="https://www.infojobs.net/distil/distil/captcha.xhtml">'
        "</head></html>",
        _CLEAN_SERP_URL,
    ),
    (
        "canonical_distil",
        '<html><head><link href="https://www.infojobs.net/distil/challenge" '
        'rel="canonical"></head></html>',
        _CLEAN_SERP_URL,
    ),
    # Heading variants, with and without accents/entities.
    (
        "h1_human_check",
        "<html><body><h1>&iquest;Eres humano o un robot?</h1></body></html>",
        _CLEAN_SERP_URL,
    ),
    (
        "h1_human_check",
        "<html><body><h1>Verificación: ¿Eres humano o un robot?</h1>"
        "</body></html>",
        _CLEAN_SERP_URL,
    ),
    (
        "h1_legacy",
        "<html><body><h1>No podemos identificar tu navegador</h1>"
        "</body></html>",
        _CLEAN_SERP_URL,
    ),
    # GeeTest: script tag and initGeetest call.
    (
        "geetest",
        '<html><body><script src="https://static.geetest.com/static/tools/'
        'gt.js"></script></body></html>',
        _CLEAN_SERP_URL,
    ),
    (
        "geetest",
        "<html><body><script>initGeetest({}, function(){});</script>"
        "</body></html>",
        _CLEAN_SERP_URL,
    ),
    # Distil iframe and the residual sherlock image.
    (
        "iframe_distil",
        '<html><body><iframe src="https://captcha.infojobs.net/distil/'
        'cloudframe"></iframe></body></html>',
        _CLEAN_SERP_URL,
    ),
    (
        "sherlock",
        '<html><body><img alt="" src="https://captcha.infojobs.net/'
        'sherlock.png"></body></html>',
        _CLEAN_SERP_URL,
    ),
)


@pytest.mark.parametrize(
    ("expected", "html", "url"),
    _MARKER_CASES,
    ids=[case[0] for case in _MARKER_CASES],
)
def test_T28_detects_each_real_challenge_marker(expected, html, url):
    assert captcha_marker_from_html(html, url) == expected


def test_T28_marker_precedence_prefers_url_then_canonical():
    assert captcha_marker_from_html(
        _REAL_CHALLENGE_HTML,
        "https://www.infojobs.net/distil/distil/captcha.xhtml",
    ) == "url_distil"
    assert captcha_marker_from_html(_REAL_CHALLENGE_HTML, _CLEAN_SERP_URL) == (
        "canonical_captcha"
    )


def test_T28_clean_serp_fixture_has_no_marker():
    if not _FIXTURE.is_file():
        pytest.skip("Fixture not found")
    html = _FIXTURE.read_text(encoding="utf-8")
    assert captcha_marker_from_html(html, _CLEAN_SERP_URL) is None


def test_T28_captcha_marker_reads_url_and_content_from_the_page():
    page = _FakePage(url=_CLEAN_SERP_URL, html=_REAL_CHALLENGE_HTML)
    assert captcha_marker(page) == "canonical_captcha"


def test_T28_captcha_marker_keeps_url_marker_when_content_fails():
    assert captcha_marker(_NoContentPage()) == "url_distil"


def test_T28_handle_captcha_aborts_without_reload_or_pause():
    page = _FakePage(url=_CLEAN_SERP_URL, html=_REAL_CHALLENGE_HTML)

    started = time.monotonic()
    clean, marker = handle_captcha(page)
    elapsed = time.monotonic() - started

    # No reload/goto: the page double does not even define them, so a call
    # would raise AttributeError. No long pause either.
    assert clean is False
    assert marker == "canonical_captcha"
    assert elapsed < 1.0


def test_T28_handle_captcha_reports_a_clean_page():
    page = _FakePage(url=_CLEAN_SERP_URL, html="<html><body></body></html>")
    assert handle_captcha(page) == (True, None)


def test_T28_session_has_reese84_checks_presence_only():
    assert session_has_reese84(
        _FakePage(cookies=[{"name": "reese84"}, {"name": "JSESSIONID"}])
    )
    assert not session_has_reese84(
        _FakePage(cookies=[{"name": "JSESSIONID"}, {"name": "AWSALB"}])
    )


def test_T28_session_has_reese84_is_false_when_cookies_fail():
    assert not session_has_reese84(_FakePage(cookies=RuntimeError("closed")))
