"""Tests del scraper de Nationale Vacaturebank (mapeo, sin red)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.nvb.config_nl import DEFAULT_CONFIG
from src.nvb.scraper import (
    NvbScraper,
    _parse_dt,
    _salary,
    _strip_html,
    _workload_pct,
)

JOB = {
    "id": "f5be9f83-f433-4da5-b5b7-31db3dbd1f32",
    "title": "Data Engineer",
    "startDate": "2026-09-16T22:00:00Z",
    "salary": {"min": 3824, "max": 5624, "type": "unspecified"},
    "workingPlace": "Hybride",
    "contractType": "Vast",
    "careerLevel": "Ervaren",
    "workingHours": {"min": 32, "max": 40},
    "company": {"name": "Gemeente Hollands Kroon",
                "website": "werkenbij.hollandskroon.nl",
                "slug": "gemeente-hollands-kroon", "type": "direct_employer"},
    "workLocation": {"city": "Den Bosch", "province": "Noord-Brabant",
                     "displayName": "Den Bosch",
                     "country": {"iso": "NL", "name": "Nederland"}},
    "industries": ["Automatisering/Internet"],
    "description": "<p>Wij zoeken een Data Engineer met SQL en Python.</p>",
    "requirements": "<ul><li>Minimaal 5 jaar ervaring met SQL</li></ul>",
    "_links": {"detail": {"href": "https://www.nationalevacaturebank.nl/vacature/f5be/data-engineer"}},
}


def _scraper() -> NvbScraper:
    d = Path(tempfile.mkdtemp())
    cfg = dict(DEFAULT_CONFIG)
    cfg["output"] = {
        "csv": str(d / "jobs.csv"),
        "parquet": str(d / "jobs.parquet"),
        "checkpoint_dir": str(d / "cp"),
    }
    return NvbScraper(cfg)


def test_helpers():
    assert _strip_html("<p>Hola</p><ul><li>uno</li></ul>").startswith("Hola")
    assert _parse_dt("2026-09-16T22:00:00Z") is not None
    mn, mx, raw, period = _salary({"min": 3824, "max": 5624})
    assert mn == 3824.0 and mx == 5624.0 and "per maand" in raw and period == "MONTH"
    assert _salary({"min": 85, "max": 95})[3] == "HOUR"
    assert _salary(None) == (None, None, "", "")
    assert _workload_pct({"min": 32, "max": 40}) == "80-100%"
    assert _workload_pct({"min": 36, "max": 36}) == "90%"
    assert _workload_pct({"min": 95, "max": 100}) == "95-100%"
    assert _workload_pct(None) == ""


def test_to_offer_campos_completos_holandes():
    sc = _scraper()
    o = sc._to_offer(JOB, "data engineer", "nvb", "NL", "Netherlands")
    assert o.job_id == "nvb_f5be9f83-f433-4da5-b5b7-31db3dbd1f32"
    assert o.job_url == "https://www.nationalevacaturebank.nl/vacature/f5be/data-engineer"
    assert o.title == "Data Engineer"
    assert o.company_name == "Gemeente Hollands Kroon"
    assert o.company_url == "https://werkenbij.hollandskroon.nl"
    assert o.location_city == "Den Bosch"
    assert o.location_region == "Noord-Brabant"
    assert o.location_country == "Netherlands"
    # Valores en holandes (se traducen en el merge).
    assert o.work_mode == "Hybride"
    assert o.employment_type == "Vast"
    assert o.experience_level == "Ervaren"
    assert o.salary_min == 3824.0 and o.salary_max == 5624.0
    assert o.salary_period == "MONTH"
    assert o.salary_currency == "EUR"
    assert o.salary_source == "EMPLOYER_PROVIDED"
    assert o.salary_disclosed is True
    assert o.workload_pct == "80-100%"
    assert "Wij zoeken een Data Engineer" in o.description_full
    assert "5 jaar ervaring met SQL" in o.requirements
    assert "SQL" in o.skills
    assert o.site == "nvb" and o.country == "NL"
    assert o.search_role == "data engineer"
    assert o.company_industry == "Automatisering/Internet"
