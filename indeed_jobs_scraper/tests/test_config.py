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
    assert "start=" not in url


def test_limit_is_opt_in():
    url = COUNTRIES["ES"].url("data", "Madrid", limit=50)
    assert "&limit=50" in url


def test_start_pagination_is_the_supported_route():
    url = COUNTRIES["NL"].url("data", "Rotterdam", start=10, fromage=7)
    assert "&start=10" in url
    assert "&fromage=7" in url


def test_limit_help_warns_about_cloudflare(monkeypatch, capsys):
    import main as main_mod

    monkeypatch.setattr(sys, "argv", ["main.py", "--help"])
    with pytest.raises(SystemExit):
        main_mod.parse_args()

    output = " ".join(capsys.readouterr().out.split())
    assert "Cloudflare" in output
    assert "NO evita el login" in output
