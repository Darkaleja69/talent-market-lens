"""Esquema de datos de una oferta de empleo de LinkedIn.

Define el dataclass JobOffer (27 columnas) y helpers de conversion a dict
para volcar a pandas -> CSV/Parquet. Compatible con ingest en Databricks
Lakehouse (parquet con esquema tipado).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JobOffer:
    """Una oferta de LinkedIn Jobs.

    Campos 1-21 vienen del SERP (pagina de resultados).
    Campos 22-23 requieren visitar el detalle de la oferta.
    Campo 27 (source) indica si lo extrajo el nucleo local o el respaldo Apify.
    """

    # --- Identificadores y enlaces (SERP) ---
    job_id: str                                  # urn:li:jobPosting:XXXX -> solo el numero
    job_url: str                                 # URL canonica sin params de tracking

    # --- Campos basicos (SERP) ---
    title: str = ""
    company_name: str = ""
    location_raw: str = ""                       # texto completo de job-search-card__location
    location_city: str = ""
    location_region: str = ""
    location_country: str = ""

    # --- Fechas (SERP) ---
    posted_datetime: Optional[str] = None        # ISO date del attr datetime
    posted_relative: str = ""                    # "hace 23 horas"
    is_new: bool = False                         # badge --new

    # --- Campos de detalle (visitan la pagina individual) ---
    company_url: str = ""
    company_industry: str = ""
    company_size: str = ""
    work_mode: str = ""                          # Remoto / Hibrido / Presencial
    employment_type: str = ""                    # Jornada completa / Contrato / Practicas
    experience_level: str = ""                   # Entry / Associate / Mid-Senior / Director
    salary_raw: str = ""                         # texto original p.ej. "40.000-55.000 euros/ano"
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = ""
    salary_period: str = ""                      # "year", "month", "hour"
    num_applicants: Optional[int] = None
    description_full: str = ""
    role_summary: str = ""
    company_description: str = ""
    responsibilities: str = ""
    requirements: str = ""
    benefits: str = ""
    skills: list[str] = field(default_factory=list)

    # --- Trazabilidad ---
    search_role: str = ""                        # termino buscado
    search_city: str = ""                        # ciudad buscada
    source: str = "local"                        # "local" | "apify"
    scraped_at: str = ""                         # ISO datetime UTC

    def to_flat_dict(self) -> dict:
        """Dict plano para CSV. skills se une con '|', saltos de linea
        se reemplazan por espacio para garantizar formato tabular puro."""
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


# Mapeo unificado de Seniority (Apify seniorityLevel) a experience_level.
# Usado tanto por apify_fallback._normalize_apify_item como por parse_detail
# para evitar divergencia entre paths local y apify.
# Claves en minuscula para match tolerante a mayusculas.
EXPERIENCE_LEVEL_MAP: dict[str, str] = {
    "internship": "Practicas",
    "practicas": "Practicas",
    "prácticas": "Practicas",
    "becario": "Practicas",
    "becaria": "Practicas",
    "entry level": "Entry",
    "entry": "Entry",
    "entry-level": "Entry",
    "associate": "Associate",
    "asociado": "Associate",
    "mid-senior level": "Mid-Senior",
    "mid-senior": "Mid-Senior",
    "mid senior": "Mid-Senior",
    "mid-senior level": "Mid-Senior",
    "senior": "Mid-Senior",
    "director": "Director",
    "executive": "Executive",
    "ejecutivo": "Executive",
    "executivo": "Executive",
}


# Lista curada de keywords de tecnologias / herramientas Data para extraccion
# de skills desde el texto de la descripcion (keyword matching con \b limites).
# Usado por parse_detail._collect_skills como fallback principal cuando LinkedIn
# no expone un componente SDUI de skills estructurado.
DATA_SKILLS_KEYWORDS: list[str] = [
    "Python", "SQL", "Power BI", "PowerBI", "Azure", "ETL",
    "Machine Learning", "Tableau", "AWS", "Excel", "Databricks",
    "GCP", "Google Cloud", "Spark", "Snowflake", "dbt", "Pandas",
    "R", "Git", "Airflow", "BigQuery", "Redshift", "Docker",
    "Kubernetes", "Terraform", "Scala", "Java", "Deep Learning",
    "Linux", "Hadoop", "Kafka", "Looker", "Power Query",
    "Power Automate", "DAX", "Matplotlib", "Scikit-learn",
    "Scikit learn", "TensorFlow", "PyTorch", "Jupyter", "SAS",
    "MATLAB", "NoSQL", "MongoDB", "PostgreSQL", "MySQL", "Oracle",
    "Data Factory", "SSIS", "SSRS", "Qlik", "MicroStrategy",
    "Alteryx", "Dataiku", "Fivetran", "Prefect", "Dagster",
    "Great Expectations", "MLflow", "Delta Lake",
]


def normalize_experience_level(text: str) -> str:
    """Normaliza un texto de seniority al nivel canonico (Entry/Associate/
    Mid-Senior/Director/Executive/Practicas). Match tolerante a mayusculas.

    Si no encuentra correspondencia, devuelve el texto original limpio.
    Usado por apify_fallback y parse_detail.
    """
    if not text:
        return ""
    t = text.strip()
    if not t:
        return ""
    key = t.lower()
    if key in EXPERIENCE_LEVEL_MAP:
        return EXPERIENCE_LEVEL_MAP[key]
    return t


# Orden de columnas para CSV (estable y legible)
CSV_COLUMN_ORDER: list[str] = [
    "job_id", "job_url", "title", "company_name",
    "location_raw", "location_city", "location_region", "location_country",
    "posted_datetime", "posted_relative", "is_new",
    "company_url", "company_industry", "company_size",
    "work_mode", "employment_type", "experience_level",
    "salary_raw", "salary_min", "salary_max", "salary_currency", "salary_period",
    "num_applicants", "description_full",
    "role_summary", "company_description", "responsibilities", "requirements", "benefits",
    "skills",
    "search_role", "search_city", "source", "scraped_at",
]

# Esquema PyArrow tipado para Parquet (compatibilidad Databricks)
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
    "num_applicants": "int64",
    "description_full": "string",
    "role_summary": "string",
    "company_description": "string",
    "responsibilities": "string",
    "requirements": "string",
    "benefits": "string",
    "skills": "list<string>",
    "search_role": "string",
    "search_city": "string",
    "source": "string",
    "scraped_at": "string",
}
