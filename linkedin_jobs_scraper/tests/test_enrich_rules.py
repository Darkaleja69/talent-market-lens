"""Tests de la logica PURA de clasificacion (sin pyspark, corren en local).

Verifican el CONTRATO DE PRIORIDAD (first-match wins) que deben cumplir tanto
estas funciones como las columnas que construye enrich() en Spark:
  - La primera regla de la lista que matchee gana.
  - "Data (Other)" / "Other" solo se devuelven si NINGUNA regla anterior matchea.
"""
from __future__ import annotations

import pytest

from src.enrich_spark import (
    employment_type,
    experience_level,
    role_category,
    role_category_debug,
)


class TestRoleCategoryPriority:
    @pytest.mark.parametrize("title,expected", [
        ("Data Analyst (Senior)", "Data Analyst & BI"),
        ("Senior Data Analyst", "Data Analyst & BI"),
        ("Analista Senior de Datos", "Data Analyst & BI"),   # variante ES
        ("Data Analyst - Power BI", "Data Analyst & BI"),
        ("Data Engineer", "Data Engineer"),
        ("Ingeniero de Datos", "Data Engineer"),             # variante ES
        ("Analytics Engineer", "Analytics Engineer"),
        ("Data Scientist", "Data Scientist"),
        ("Científico de Datos", "Data Scientist"),           # variante ES
        ("Machine Learning Engineer", "Machine Learning & AI"),
        ("Contable senior", "Other"),                        # sin hints de data
        ("Data Analytics Manager", "Data Analyst & BI"),     # analytics
        ("Business Intelligence Analyst", "Data Analyst & BI"),
    ], ids=lambda x: str(x)[:24])
    def test_categoria_correcta(self, title, expected):
        assert role_category(title) == expected

    def test_data_other_solo_cuando_nada_anterior_matchea(self):
        # "Data" suelto sin categoria especifica -> cajon de sastre
        assert role_category("Data Officer") == "Data (Other)"
        # Palabra data en un rol no-data pero con termino mas concreto antes:
        # "Analista" matchea en la regla Data Analyst & BI (prioridad mayor).
        assert role_category("Analista de sistemas") == "Data Analyst & BI"

    def test_debug_ordena_matches_por_prioridad(self):
        assert role_category_debug("Analista de Datos") == [
            "Data Analyst & BI", "Data (Other)",
        ]
        assert role_category_debug("Data Analyst (Senior)") == [
            "Data Analyst & BI", "Data (Other)",
        ]
        assert role_category_debug("Contable") == []


class TestExperiencePriority:
    def test_varias_menciones_gana_la_mayor(self):
        # "Becario junior" -> talent practicas CIERTO (Practicas pesa que Entry)
        assert experience_level("Becario junior en analitica.") == "Practicas"

    def test_director_vence_a_senior(self):
        assert experience_level("Directora de datos senior, lidera.") == "Director"

    def test_terminos_es_en(self):
        assert experience_level("Senior SQL developer 5 años") == "Mid-Senior"
        assert experience_level("Puesto junior con nivel de entrada") == "Entry"
        assert experience_level("Buscamos associate con 3 años") == "Associate"
        assert experience_level("Puesto ejecutivo") == "Executive"

    def test_sin_seniority_vacio(self):
        assert experience_level("Gran equipo joven.") == ""


class TestEmploymentTypePriority:
    def test_practicas_gana_a_full_time(self):
        # "One year internship, full-time position" -> Practicas (especifico)
        assert employment_type("One year internship, full-time position.") == "Practicas"

    def test_tipos(self):
        assert employment_type("Puesto de media jornada") == "Media jornada"
        assert employment_type("Contrato temporal de 6 meses") == "Contrato"
        assert employment_type("Full-time con jornada completa") == "Jornada completa"

    def test_sin_tipo_vacio(self):
        assert employment_type("Buscamos perfil analítico") == ""