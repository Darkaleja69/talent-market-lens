"""Tests del backoff acotado ante HTTP 429 del SERP guest (T-33).

Verifican (sin red; `urllib.request.urlopen` y `time.sleep` monkeypatcheados):
 - 429 sin Retry-After: backoff propio, maximo 2 reintentos y "" al agotar.
 - 429 con Retry-After: se honra (segundos) y se aplica el tope de 60 s.
 - 429 seguido de exito: reintenta y devuelve el HTML.
 - errores que no son 429: sin reintentos, "" como antes.
"""
from __future__ import annotations

import urllib.error
import urllib.request

import src.search as search


def _http_error(code: int = 429, retry_after: str | None = None):
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return urllib.error.HTTPError(
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        code, "error", headers, None)


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _patch_urlopen(monkeypatch, side_effect):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        result = side_effect(calls["n"])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _fetch(monkeypatch, side_effect):
    calls = _patch_urlopen(monkeypatch, side_effect)
    sleeps: list[float] = []
    monkeypatch.setattr(search.time, "sleep", sleeps.append)
    html = search._fetch_guest_search_html(
        "Data Analyst", {"text": "Madrid"}, "r604800", 0)
    return html, calls["n"], sleeps


def test_429_without_retry_after_retries_bounded_then_gives_up(monkeypatch):
    html, calls, sleeps = _fetch(monkeypatch, lambda n: _http_error(429))
    assert html == ""
    assert calls == 1 + search._GUEST_429_RETRIES
    assert len(sleeps) == search._GUEST_429_RETRIES
    assert all(0.0 < wait <= search._GUEST_429_MAX_WAIT_S for wait in sleeps)
    # Backoff propio creciente (1 s y 2 s + jitter acotado).
    assert sleeps[0] <= 1.0 + search._GUEST_429_JITTER_S
    assert sleeps[1] >= 2.0


def test_429_honors_retry_after_seconds(monkeypatch):
    html, calls, sleeps = _fetch(monkeypatch, lambda n: _http_error(429, "7"))
    assert html == ""
    assert calls == 1 + search._GUEST_429_RETRIES
    assert 7.0 <= sleeps[0] <= 7.0 + search._GUEST_429_JITTER_S


def test_429_retry_after_is_capped(monkeypatch):
    html, calls, sleeps = _fetch(monkeypatch, lambda n: _http_error(429, "600"))
    assert html == ""
    assert calls == 1 + search._GUEST_429_RETRIES
    assert sleeps[0] == search._GUEST_429_MAX_WAIT_S
    assert all(wait <= search._GUEST_429_MAX_WAIT_S for wait in sleeps)


def test_429_then_success_returns_html(monkeypatch):
    def side_effect(n):
        if n == 1:
            return _http_error(429, "1")
        return _FakeResponse(b"<html>tarjetas</html>")

    html, calls, sleeps = _fetch(monkeypatch, side_effect)
    assert html == "<html>tarjetas</html>"
    assert calls == 2
    assert len(sleeps) == 1


def test_non_429_error_returns_empty_without_retries(monkeypatch):
    html, calls, sleeps = _fetch(monkeypatch, lambda n: _http_error(403))
    assert html == ""
    assert calls == 1
    assert sleeps == []


def test_network_error_returns_empty_without_retries(monkeypatch):
    def side_effect(n):
        raise TimeoutError("connection timed out")

    html, calls, sleeps = _fetch(monkeypatch, side_effect)
    assert html == ""
    assert calls == 1
    assert sleeps == []
