"""Tests del parser de detalle de Glassdoor (JSON-LD baseSalary)."""
from src.glassdoor.detail import _jsonld_salary_period


def test_jsonld_period_from_unit_text():
    assert _jsonld_salary_period(
        {"currency": "USD", "value": {"minValue": 100, "maxValue": 200,
                                      "unitText": "YEAR"}}) == "YEAR"
    assert _jsonld_salary_period(
        {"currency": "EUR", "value": {"minValue": 100, "maxValue": 200,
                                      "unitText": "per month"}}) == "MONTH"
    assert _jsonld_salary_period(
        {"currency": "EUR", "value": {"minValue": 50, "maxValue": 80,
                                      "unitText": "HOUR"}}) == "HOUR"


def test_jsonld_period_from_unit_code():
    assert _jsonld_salary_period(
        {"currency": "USD", "value": {"minValue": 100, "maxValue": 200,
                                      "unitCode": "MON"}}) == "MONTH"
    assert _jsonld_salary_period(
        {"currency": "USD", "value": {"minValue": 100, "maxValue": 200,
                                      "unitCode": "YEAR"}}) == "YEAR"


def test_jsonld_period_unknown_no_annual_assumed():
    # Sin unidad: NO se asume anual (antes se forzaba YEARLY -> falsificaba el
    # dato cuando el importe era mensual/horario).
    assert _jsonld_salary_period(
        {"currency": "EUR", "value": {"minValue": 100, "maxValue": 200}}) == ""
    assert _jsonld_salary_period({}) == ""
    assert _jsonld_salary_period(
        {"currency": "EUR", "value": {"minValue": 100, "maxValue": 200,
                                      "unitText": "por proyecto"}}) == ""