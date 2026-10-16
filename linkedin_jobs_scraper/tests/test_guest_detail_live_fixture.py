"""Test offline de la ruta de parseo guest con la fixture real (T-33).

`tests/fixtures/guest_detail_live_20261011.html` es el detalle guest del job
4475564054 capturado el 2026-10-11 (contenido publico; `loginCsrfParam`
redactado). El test maneja `_get_description` y `_collect_chips` con un
adaptador estatico minimo sobre el HTML real y documenta la forma de la pagina
verificada en la sonda: descripcion presente, tres criterios (Nivel de
antiguedad / Tipo de empleo / Sectores, sin Workplace) y sin senal de
modalidad en el texto.
"""
from __future__ import annotations

import html as html_mod
import re
from pathlib import Path

import pytest

from src.parse_detail import (
    SEL_CRITERIA_ITEM_GUEST,
    SEL_CRITERIA_SUBHEADER_GUEST,
    SEL_CRITERIA_TEXT_GUEST,
    SEL_DESCRIPTION_GUEST_ALT,
    _collect_chips,
    _get_description,
    _infer_work_mode,
)

_FIXTURE = (Path(__file__).resolve().parent / "fixtures"
            / "guest_detail_live_20261011.html")
_TAG = re.compile(r"<[^>]+>")


class _StaticEl:
    """Elemento con el subconjunto de la API de Page que usan los parsers."""

    def __init__(self, text: str = "", html: str = "",
                 children: dict[str, "_StaticEl"] | None = None):
        self._text = text
        self._html = html
        self._children = children or {}

    def inner_text(self) -> str:
        return self._text

    def text_content(self) -> str:
        return self._text

    def inner_html(self) -> str:
        return self._html

    def query_selector(self, sel: str):
        return self._children.get(sel)


class _StaticPage:
    """Page minima que responde a los selectores guest desde el HTML real."""

    def __init__(self, elements: dict[str, _StaticEl],
                 lists: dict[str, list[_StaticEl]]):
        self._elements = elements
        self._lists = lists

    def query_selector(self, sel: str):
        return self._elements.get(sel)

    def query_selector_all(self, sel: str):
        return self._lists.get(sel, [])


def _clean(fragment: str) -> str:
    return " ".join(html_mod.unescape(_TAG.sub(" ", fragment)).split())


def _fixture_page() -> _StaticPage:
    if not _FIXTURE.exists():
        pytest.skip("No se encontro la fixture guest_detail_live_20261011.html.")
    raw = _FIXTURE.read_text(encoding="utf-8")
    desc = re.search(
        r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        raw, re.S | re.IGNORECASE)
    assert desc is not None, "fixture sin bloque de descripcion guest"
    items: list[_StaticEl] = []
    for item in re.findall(
            r"<li[^>]*description__job-criteria-item[^>]*>(.*?)</li>",
            raw, re.S | re.IGNORECASE):
        sub = re.search(
            r"<h3[^>]*description__job-criteria-subheader[^>]*>(.*?)</h3>",
            item, re.S | re.IGNORECASE)
        val = re.search(
            r"<span[^>]*description__job-criteria-text[^>]*>(.*?)</span>",
            item, re.S | re.IGNORECASE)
        if sub is None or val is None:
            continue
        items.append(_StaticEl(children={
            SEL_CRITERIA_SUBHEADER_GUEST: _StaticEl(text=_clean(sub.group(1))),
            SEL_CRITERIA_TEXT_GUEST: _StaticEl(text=_clean(val.group(1))),
        }))
    desc_html = desc.group(1)
    return _StaticPage(
        elements={SEL_DESCRIPTION_GUEST_ALT: _StaticEl(
            text=_clean(desc_html), html=desc_html)},
        lists={SEL_CRITERIA_ITEM_GUEST: items},
    )


def test_guest_fixture_description_is_parsed_from_real_html():
    """La descripcion guest real se extrae entera y sin senal de modalidad."""
    description = _get_description(_fixture_page())
    assert len(description) > 1000
    assert "AI Engineer" in description
    assert _infer_work_mode(description) == ""


def test_guest_fixture_criteria_are_read_without_workplace():
    """Los tres criterios reales se leen; no hay criterio Workplace."""
    work_mode, employment_type, experience_level = _collect_chips(
        _fixture_page())
    assert work_mode == ""
    assert employment_type == "Jornada completa"
    assert experience_level == "Intermedio"
