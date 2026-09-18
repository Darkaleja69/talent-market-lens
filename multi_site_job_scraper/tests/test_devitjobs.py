"""Tests del scraper de DevITJobs.nl (mapeo de campos completo, sin red)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from src.devitjobs.config_nl import DEFAULT_CONFIG
from src.devitjobs.scraper import (
    DevITJobsScraper,
    _benefits_from_description,
    _benefits_text,
    _clean_text,
    _perks_text,
    _relative_from_dt,
    _to_float,
    _workload,
)
from src.devitjobs import title_filter_nl

JOB = {
    "_id": "6aa79f4246ec858eee257c40",
    "jobUrl": "EDSN-Scrum-Master-medior",
    "company": "EDSN",
    "name": "Senior Data Engineer",
    "activeFrom": "2026-09-14T00:00:00.000+02:00",
    "createdAt": "2026-09-14T07:16:18.953Z",
    "workplace": "hybrid",
    "jobType": "Full-Time",
    "expLevel": "Senior",
    "annualSalaryFrom": 50000,
    "annualSalaryTo": 75000,
    "techCategory": "Data",
    "technologies": ["Python", "SQL", "Databricks"],
    "filterTags": ["Data", "SQL"],
    "address": "Van Asch van Wijckstraat 55",
    "actualCity": "Amersfoort",
    "postalCode": "3811LP",
    "cityCategory": "Utrecht",
    "companyWebsiteLink": "edsn.nl",
    "companyType": "Services",
    "companySize": "200-500",
    "isDisabledOrOutdated": False,
    "isPaused": False,
}

DETAIL = {
    "description": "<p>Senior Data Engineer | 36-40 uur | Amersfoort</p><p>Bouw ETL pipelines met Python en SQL.</p>",
    "requirementsMustTextArea": "- Minimaal 5 jaar ervaring\n- Python en SQL",
    "responsibilitiesTextArea": "- Ontwikkelen van data pipelines\n- Onderhouden van Snowflake",
    "perkKeys": ["hybridwork", "remote2day", "companycar"],
}


def _scraper() -> DevITJobsScraper:
    d = Path(tempfile.mkdtemp())
    cfg = dict(DEFAULT_CONFIG)
    cfg["output"] = {
        "csv": str(d / "jobs.csv"),
        "parquet": str(d / "jobs.parquet"),
        "checkpoint_dir": str(d / "cp"),
    }
    return DevITJobsScraper(cfg)


def test_helpers_basicos():
    assert _clean_text("<p>Hola&nbsp;mundo</p>").startswith("Hola")
    assert _to_float(50000) == 50000.0
    assert _to_float(0) is None
    assert _to_float(None) is None
    assert _workload("Functie | 36-40 uur | Amersfoort") == "90-100%"
    assert _workload("Functie | 40 uur | Amersfoort") == "100%"
    assert _perks_text(["hybridwork", "companycar"]) == "Hybrid working | Company car"
    assert _relative_from_dt(None) == ""


def test_benefits_fallback_desde_descripcion():
    # Sin perks -> extrae frases de condiciones.
    desc = ("Bouw ETL pipelines met Python. We bieden een pensioen en "
            "25 vakantiedagen. Leaseauto of mobiliteitsbudget. "
            "Solliciteer nu bij ons team.")
    b = _benefits_from_description(desc)
    assert "pensioen" in b and "vakantiedagen" in b
    assert "Solliciteer nu" not in b
    # Con perks -> usa los perks.
    assert _benefits_text(["hybridwork"], desc) == "Hybrid working"


def test_is_candidate_por_categoria_y_titulo():
    sc = _scraper()
    cats = {"Data", "Machine-Learning"}
    phrases = title_filter_nl.phrases_from_roles(DEFAULT_CONFIG["roles"])
    # Categoria Data -> candidata.
    assert sc._is_candidate(JOB, cats, True, False, [], phrases, 0)
    # Otra categoria pero titulo data -> candidata.
    j2 = dict(JOB, techCategory="Architect", name="Data Architect")
    assert sc._is_candidate(j2, cats, True, False, [], phrases, 0)
    # Otra categoria y titulo no-data -> no candidata.
    j3 = dict(JOB, techCategory="Java", name="Java Developer")
    assert not sc._is_candidate(j3, cats, True, False, [], phrases, 0)
    # Excluida por patron.
    j4 = dict(JOB, techCategory="Data", name="Java Developer (Software Engineer)")
    assert not sc._is_candidate(j4, cats, True, False, ["java developer"], phrases, 0)
    # Deshabilitada.
    j5 = dict(JOB, isDisabledOrOutdated=True)
    assert not sc._is_candidate(j5, cats, True, False, [], phrases, 0)


def test_to_offer_mapea_todos_los_campos():
    sc = _scraper()
    offer = sc._to_offer(JOB, DETAIL, "devitjobs", "NL", "Netherlands",
                         "https://devitjobs.nl")
    assert offer.job_id == "dij_6aa79f4246ec858eee257c40"
    assert offer.job_url == "https://devitjobs.nl/jobs/EDSN-Scrum-Master-medior"
    assert offer.title == "Senior Data Engineer"
    assert offer.company_name == "EDSN"
    assert offer.company_url == "https://edsn.nl"
    assert offer.location_city == "Amersfoort"
    assert offer.location_region == "Utrecht"
    assert offer.location_country == "Netherlands"
    assert "Amersfoort" in offer.location_raw
    assert offer.work_mode == "Hybrid"
    assert offer.employment_type == "Full-Time"
    assert offer.experience_level == "Senior"
    assert offer.salary_min == 50000.0
    assert offer.salary_max == 75000.0
    assert offer.salary_currency == "EUR"
    assert offer.salary_period == "YEAR"
    assert offer.salary_source == "EMPLOYER_PROVIDED"
    assert offer.salary_disclosed is True
    assert "€50000 - €75000 per year" == offer.salary_raw
    assert offer.description_full.startswith("Senior Data Engineer")
    assert "Python" in offer.requirements
    assert "Snowflake" in offer.responsibilities
    assert "Hybrid working" in offer.benefits
    assert offer.workload_pct == "90-100%"
    assert "Python" in offer.skills and "SQL" in offer.skills
    assert offer.site == "devitjobs"
    assert offer.country == "NL"
    assert offer.search_role == "Data"
    assert offer.scraped_at


def test_to_offer_sin_salario_ni_detalle():
    sc = _scraper()
    job = dict(JOB, annualSalaryFrom=0, annualSalaryTo=0)
    offer = sc._to_offer(job, {}, "devitjobs", "NL", "Netherlands",
                         "https://devitjobs.nl")
    assert offer.salary_min is None
    assert offer.salary_disclosed is False
    assert offer.salary_currency == ""
    assert offer.description_full == ""
