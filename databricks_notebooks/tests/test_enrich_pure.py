"""Tests de las funciones puras de normalizacion (sin pyspark).

Fijan las reglas ampliadas para mejorar la calidad del modelo: categorias de
rol, modo de trabajo multilingue, periodos salariales NL/DE y parseo de
importes con separadores ES/EN/NL.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Enrich_Job_Offers_Dataframes as e  # noqa: E402
import Prepare_Gold as g  # noqa: E402


def test_role_german_analyst_and_database():
    assert e.role_category("Datenanalyst / Projektleiter") == "Data Analyst & BI"
    assert e.role_category("PostgreSQL Database Engineer") == "Data Engineer"
    assert e.role_category("Strategic Insights Analyst") == "Data Analyst & BI"
    assert e.role_category("Account Executive") == "Other"


def test_normalize_experience_level_spanish_and_english():
    # Valores crudos (castellano e ingles) -> 6 categorias canonicas.
    assert e.normalize_experience_level("Algo de responsabilidad") == "Mid-Senior"
    assert e.normalize_experience_level("Intermedio") == "Mid-Senior"
    assert e.normalize_experience_level("Practicas") == "Intern"
    assert e.normalize_experience_level("Sin Experiencia") == "Junior"
    assert e.normalize_experience_level("No corresponde") == "Unknown"
    assert e.normalize_experience_level("Unkown") == "Unknown"
    assert e.normalize_experience_level("Associate") == "Mid-Senior"
    assert e.normalize_experience_level("Director") == "Executive"
    assert e.normalize_experience_level("Entry") == "Junior"
    assert e.normalize_experience_level("Not Applicable") == "Unknown"
    assert e.normalize_experience_level("Senior") == "Senior"
    assert e.normalize_experience_level("") == "Unknown"
    assert e.normalize_experience_level("otro nivel raro") == "Unknown"


def test_experience_level_rules_output_canonical_categories():
    assert e.experience_level("Senior Data Engineer") == "Senior"
    assert e.experience_level("Mid-Senior Data Analyst") == "Mid-Senior"
    assert e.experience_level("Junior Data Analyst") == "Junior"
    assert e.experience_level("Becario de analitica") == "Intern"
    assert e.experience_level("Head of Data") == "Executive"


def test_work_mode_multilingual():
    assert e.work_mode_normalized("Thuiswerken") == "Remote"
    assert e.work_mode_normalized("Homeoffice möglich") == "Remote"
    assert e.work_mode_normalized("Hybride werken") == "Hybrid"
    assert e.work_mode_normalized("Vor Ort") == "On-site"
    assert e.work_mode_normalized("sin senal") == ""


def test_salary_period_dutch_german():
    assert e.salary_period_normalized("per jaar") == "YEAR"
    assert e.salary_period_normalized("p/m") == "MONTH"
    assert e.salary_period_normalized("per uur") == ""


def test_parse_number_separators():
    assert e._parse_number("3.500") == 3500.0
    assert e._parse_number("41,260") == 41260.0
    assert e._parse_number("3.500,00") == 3500.0
    assert e._parse_number("3,500.00") == 3500.0


def test_parse_salary_raw_dutch():
    smin, smax, cur, period = e.parse_salary_raw("EUR 3.500 - 4.500 per maand")
    assert (smin, smax, cur, period) == (3500.0, 4500.0, "EUR", "MONTH")


def test_skill_catalog_has_no_missing_commas():
    # Regresion: dos comas faltantes en databricks_gold.SKILL_CATALOG partian
    # el import del modulo ('tuple' object is not callable).
    names = {name for name, _ in g.SKILL_CATALOG}
    assert {"GCP", "ELT", "Microsoft Fabric"} <= names
