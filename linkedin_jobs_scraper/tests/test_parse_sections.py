"""Tests unitarios de parse_sections (segmentacion de descripciones).

Verifica:
  - split_description_sections con HTML (source='html') usando <strong> headers.
  - split_description_sections con texto plano (source='text').
  - Casos borde: vacio, sin cabeceras, solo una seccion, cabeceras bilingue.
  - La seccion role_summary captura texto antes de la primera cabecera.
  - Cabeceras sin dos puntos, con emojis prefijados.
"""
from __future__ import annotations

import pytest

from src.parse_sections import split_description_sections, SECTION_KEYS


# --- Fixtures de HTML ---

HTML_ES = """<div>
<p>Somos una empresa lider en tecnologia con presencia en 30 paises.</p>
<p><strong>Funciones:</strong></p>
<ul>
<li>Desarrollar dashboards en Power BI.</li>
<li>Analizar KPIs de negocio.</li>
</ul>
<p><strong>Requisitos:</strong></p>
<ul>
<li>Grado en Ingenieria o similar.</li>
<li>3+ anos de experiencia en analisis de datos.</li>
</ul>
<p><strong>Lo que ofrecemos:</strong></p>
<ul>
<li>Contrato indefinido.</li>
<li>Horario flexible.</li>
</ul>
</div>"""

HTML_EN = """<div>
<p>We are a global fintech company.</p>
<h3>Main Responsibilities</h3>
<ul>
<li>Build data pipelines.</li>
<li>Optimize SQL queries.</li>
</ul>
<h3>Requirements</h3>
<ul>
<li>5+ years in data engineering.</li>
<li>Strong Python and SQL.</li>
</ul>
<h3>Benefits</h3>
<ul>
<li>Remote work.</li>
<li>Stock options.</li>
</ul>
</div>"""

HTML_NO_HEADERS = """<div>
<p>Buscamos un Data Analyst con experiencia en SQL y Python. Trabajaras con
equipos de negocio para extraer insights de datos. Ofrecemos buen ambiente.</p>
</div>"""

HTML_ONLY_RESPONSIBILITIES = """<div>
<p>Consultoria lider busca incorporar talento.</p>
<p><strong>Funciones del puesto:</strong></p>
<ul>
<li>Gestion de proyectos.</li>
</ul>
</div>"""

HTML_EMOJI_HEADERS = """<div>
<p><strong>📌 Funciones y responsabilidades</strong></p>
<ul><li>Task 1</li></ul>
<p><strong>📌 Requisitos tecnicos indispensables</strong></p>
<ul><li>Skill 1</li></ul>
<p><strong>🎯 ¿Que te ofrecemos?</strong></p>
<ul><li>Benefit 1</li></ul>
</div>"""

HTML_HEADER_NO_COLON = """<div>
<p>Empresa lider en su sector.</p>
<h3>¿Que haras?</h3>
<ul><li>Analizar datos</li></ul>
<h3>¿Que buscamos?</h3>
<ul><li>Experiencia en Python</li></ul>
<h3>¿Que ofrecemos?</h3>
<ul><li>Flexibilidad</li></ul>
</div>"""

HTML_MIXED_LANG = """<div>
<p>Company overview text here.</p>
<p><strong>Company Description</strong></p>
<p>A global tech company with 5000+ employees.</p>
<p><strong>Key Responsibilities</strong></p>
<ul><li>Duty 1</li><li>Duty 2</li></ul>
<p><strong>Requirements</strong></p>
<ul><li>Skill A</li><li>Skill B</li></ul>
<p><strong>Perks & Benefits</strong></p>
<ul><li>Perk X</li></ul>
</div>"""


# --- Tests HTML source ---

def test_html_es_sections():
    result = split_description_sections(HTML_ES, source="html")
    assert len(result) == 5
    assert all(k in result for k in SECTION_KEYS)
    assert "empresa lider" in result["role_summary"].lower()
    assert "Desarrollar dashboards" in result["responsibilities"]
    assert "Grado en Ingenieria" in result["requirements"]
    assert "Contrato indefinido" in result["benefits"]
    assert result["company_description"] == ""


def test_html_en_sections():
    result = split_description_sections(HTML_EN, source="html")
    assert "fintech" in result["role_summary"].lower()
    assert "data pipelines" in result["responsibilities"]
    assert "5+ years" in result["requirements"]
    assert "Remote work" in result["benefits"]


def test_html_no_headers():
    result = split_description_sections(HTML_NO_HEADERS, source="html")
    assert "Data Analyst" in result["role_summary"]
    assert result["responsibilities"] == ""
    assert result["requirements"] == ""
    assert result["benefits"] == ""


def test_html_only_responsibilities():
    result = split_description_sections(HTML_ONLY_RESPONSIBILITIES, source="html")
    assert "Consultoria lider" in result["role_summary"]
    assert "Gestion de proyectos" in result["responsibilities"]
    assert result["requirements"] == ""
    assert result["benefits"] == ""


def test_html_emoji_headers():
    result = split_description_sections(HTML_EMOJI_HEADERS, source="html")
    assert "Task 1" in result["responsibilities"]
    assert "Skill 1" in result["requirements"]
    assert "Benefit 1" in result["benefits"]


def test_html_header_no_colon():
    result = split_description_sections(HTML_HEADER_NO_COLON, source="html")
    assert "Analizar datos" in result["responsibilities"]
    assert "Experiencia en Python" in result["requirements"]
    assert "Flexibilidad" in result["benefits"]


def test_html_mixed_lang():
    result = split_description_sections(HTML_MIXED_LANG, source="html")
    assert "global tech" in result["company_description"].lower()
    assert "Duty 1" in result["responsibilities"]
    assert "Skill A" in result["requirements"]
    assert "Perk X" in result["benefits"]


# --- Tests text source (Apify fallback) ---

TEXT_ES = """Somos una consultora de datos con clientes en toda Europa.

Funciones:
Desarrollar modelos de machine learning.
Crear dashboards en Power BI.

Requisitos:
Python avanzado.
Experiencia en ML.

Lo que ofrecemos:
Trabajo remoto.
Plan de carrera."""

TEXT_EN = """We are a growing startup.

Responsibilities:
Build and maintain data pipelines.
Collaborate with engineers.

Requirements:
SQL expert.
Cloud experience.

Benefits & Perks:
Equity package.
Unlimited PTO."""

TEXT_NO_HEADERS = """Buscamos un perfil junior con ganas de aprender. No importa experiencia previa."""


def test_text_es_sections():
    result = split_description_sections(TEXT_ES, source="text")
    assert "consultora de datos" in result["role_summary"].lower()
    assert "machine learning" in result["responsibilities"].lower()
    assert "Python avanzado" in result["requirements"]
    assert "Trabajo remoto" in result["benefits"]
    assert result["company_description"] == ""


def test_text_en_sections():
    result = split_description_sections(TEXT_EN, source="text")
    assert "growing startup" in result["role_summary"].lower()
    assert "data pipelines" in result["responsibilities"]
    assert "SQL expert" in result["requirements"]
    assert "Equity" in result["benefits"]


def test_text_no_headers():
    result = split_description_sections(TEXT_NO_HEADERS, source="text")
    assert "perfil junior" in result["role_summary"]
    assert result["responsibilities"] == ""
    assert result["requirements"] == ""


# --- Casos borde ---

def test_empty_content():
    result = split_description_sections("", source="html")
    assert all(v == "" for v in result.values())


def test_whitespace_only():
    result = split_description_sections("   \n  ", source="html")
    assert all(v == "" for v in result.values())


def test_result_has_all_keys():
    result = split_description_sections(HTML_ES, source="html")
    for k in SECTION_KEYS:
        assert k in result
    assert len(result) == 5
