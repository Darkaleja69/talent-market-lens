"""Tests unitarios de la configuracion y la CLI de Indeed.

Cubren la URL de la SERP (paginacion permitida por `start=` y el parametro
`limit`, que queda opt-in) y el texto de ayuda de `--limit`, que debe avisar de
que dispara el challenge de Cloudflare en lugar de prometer que evita el login.
"""
from __future__ import annotations

import sys

import pytest

from scraper.config import COUNTRIES


def test_default_url_has_no_limit_parameter():
    url = COUNTRIES["ES"].url("data", "Madrid")
    assert "&limit=" not in url


def test_first_page_uses_start_zero_for_robots_allowed_path():
    # La pagina 1 se pide con &start=0 para alinearse con `Allow: /*&start=0&`
    # de robots.txt (el matching incluye la query, RFC 9309; decision de la
    # reparacion 2026-10-18). Antes se omitia start y la URL quedaba bajo la
    # zona gris de `Disallow: /*radius=`.
    url = COUNTRIES["ES"].url("data", "Madrid", fromage=7)
    assert "&start=0&fromage=7" in url


def test_first_page_without_filters_closes_start_with_ampersand():
    # Invariante `&start=N&`: sin filtros `start` es el ultimo parametro, asi
    # que la URL debe cerrar con `&` o no casaria con `Allow: /*&start=0&`.
    url = COUNTRIES["ES"].url("data", "Madrid")
    assert url.endswith("&start=0&")


def test_second_page_without_filters_closes_start_with_ampersand():
    url = COUNTRIES["ES"].url("data", "Madrid", start=10)
    assert url.endswith("&start=10&")


def test_limit_is_opt_in():
    url = COUNTRIES["ES"].url("data", "Madrid", limit=50)
    assert "&limit=50" in url


def test_start_pagination_is_the_supported_route():
    url = COUNTRIES["NL"].url("data", "Rotterdam", start=10, fromage=7)
    assert "&start=10" in url
    assert "&fromage=7" in url
    assert "&start=10&fromage=7" in url


def test_limit_help_warns_about_cloudflare(monkeypatch, capsys):
    import main as main_mod

    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])
    with pytest.raises(SystemExit):
        main_mod.parse_args()

    output = " ".join(capsys.readouterr().out.split())
    assert "Cloudflare" in output
    assert "NO evita el login" in output
