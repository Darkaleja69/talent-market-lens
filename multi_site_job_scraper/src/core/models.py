"""Esquema de datos unificado para todas las fuentes de ofertas de empleo.

Define el dataclass JobOffer con campos comunes a todos los sitios
(LinkedIn, IrishJobs, StepStone NL, jobs.ch, Glassdoor).

Campos especificos de cada sitio se almacenan en los mismos campos
cuando es posible, o en campos dedicados (workload_pct para jobs.ch).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JobOffer:
    """Una oferta de empleo de cualquier fuente."""

    job_id: str = ""
    job_url: str = ""
    title: str = ""
    company_name: str = ""
    location_raw: str = ""
    location_city: str = ""
    location_region: str = ""
    location_country: str = ""

    posted_datetime: Optional[str] = None
    posted_relative: str = ""
    is_new: bool = False

    company_url: str = ""
    company_industry: str = ""
    company_size: str = ""

    work_mode: str = ""
    employment_type: str = ""
    experience_level: str = ""

    salary_raw: str = ""
    salary_raw_original: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_period: str = ""
    salary_source: str = ""
    salary_is_estimated: bool = False

    num_applicants: Optional[int] = None

    description_full: str = ""
    description_snippet: str = ""
    role_summary: str = ""
    company_description: str = ""
    responsibilities: str = ""
    requirements: str = ""
    benefits: str = ""

    skills: list[str] = field(default_factory=list)

    # --- Multi-site ---
    site: str = ""
    country: str = ""
    workload_pct: str = ""
    salary_disclosed: bool = True

    # --- Trazabilidad ---
    search_role: str = ""
    search_city: str = ""
    source: str = "local"
    scraped_at: str = ""

    def to_flat_dict(self) -> dict:
        """Dict plano para CSV. skills se une con '|'."""
        import re

        d = dataclasses.asdict(self)
        for k, v in d.items():
            if isinstance(v, str) and "\n" in v:
                d[k] = re.sub(r"\n+", " ", v).strip()
        d["skills"] = "|".join(self.skills) if self.skills else ""
        return d

    def to_parquet_dict(self) -> dict:
        """Dict para Parquet (skills como lista nativa list<string>)."""
        return dataclasses.asdict(self)


def now_utc_iso() -> str:
    """Timestamp ISO 8601 UTC para scraped_at."""
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


CSV_COLUMN_ORDER: list[str] = [
    "job_id", "job_url", "title", "company_name",
    "location_raw", "location_city", "location_region", "location_country",
    "posted_datetime", "posted_relative", "is_new",
    "company_url", "company_industry", "company_size",
    "work_mode", "employment_type", "experience_level",
    "salary_raw", "salary_min", "salary_max", "salary_currency", "salary_period",
    "salary_source", "salary_is_estimated", "salary_raw_original",
    "num_applicants",
    "description_full", "description_snippet",
    "role_summary", "company_description", "responsibilities", "requirements", "benefits",
    "skills",
    "site", "country", "workload_pct", "salary_disclosed",
    "search_role", "search_city", "source", "scraped_at",
]

PARQUET_SCHEMA_FIELDS: dict[str, str] = {
    "job_id": "string",
    "job_url": "string",
    "title": "string",
    "company_name": "string",
    "location_raw": "string",
    "location_city": "string",
    "location_region": "string",
    "location_country": "string",
    "posted_datetime": "string",
    "posted_relative": "string",
    "is_new": "bool",
    "company_url": "string",
    "company_industry": "string",
    "company_size": "string",
    "work_mode": "string",
    "employment_type": "string",
    "experience_level": "string",
    "salary_raw": "string",
    "salary_min": "float64",
    "salary_max": "float64",
    "salary_currency": "string",
    "salary_period": "string",
    "salary_source": "string",
    "salary_is_estimated": "bool",
    "salary_raw_original": "string",
    "num_applicants": "int64",
    "description_full": "string",
    "description_snippet": "string",
    "role_summary": "string",
    "company_description": "string",
    "responsibilities": "string",
    "requirements": "string",
    "benefits": "string",
    "skills": "list<string>",
    "site": "string",
    "country": "string",
    "workload_pct": "string",
    "salary_disclosed": "bool",
    "search_role": "string",
    "search_city": "string",
    "source": "string",
    "scraped_at": "string",
}
