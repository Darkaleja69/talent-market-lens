"""Catalog of the nine independent sources measured by the diagnostic.

Each source is measured on its own (RF-2): the three direct scrapers
(Indeed, LinkedIn, InfoJobs) and the six independent Multi-site portals
(IrishJobs, StepStone NL, DevITjobs, NVB, Jobs.ch, Glassdoor).

The catalog is pure data plus query helpers, so it is directly testable:
canonical field -> origin column aliases, deduplication key and the local
locations of its run evidence (plan section 3.2).
"""
from __future__ import annotations

from dataclasses import dataclass

# Canonical fields measured for every offer (RF-4). `id` is the unique
# identifier; the rest are the relevant job data.
CANONICAL_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "company",
    "description",
    "salary",
    "skills",
    "work_mode",
    "location",
    "posted_date",
)

# Real `site` values emitted by Multi-site (config_*.py / run_all.ps1).
MULTI_SITE_SITE_IDS: tuple[str, ...] = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)

# Mandatory Parquet columns each source must expose, mirroring the
# `RequiredColumns` field of `scrapers-pipeline/config.ps1` (the project's
# operational authority). A file lacking any of these is a structural failure
# of the source (RF-3).
_DIRECT_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "indeed": (
        "job_key",
        "title",
        "company",
        "viewjob_url",
        "scraped_at",
    ),
    "linkedin": (
        "job_id",
        "job_url",
        "title",
        "company_name",
        "scraped_at",
    ),
    "infojobs": (
        "id_oferta",
        "titulo",
        "empresa",
        "url_oferta",
        "fecha_scraped",
    ),
}
_MULTI_SITE_REQUIRED_COLUMNS: tuple[str, ...] = (
    "job_id",
    "job_url",
    "title",
    "company_name",
    "scraped_at",
)


@dataclass(frozen=True)
class Source:
    """One independent source of job offers."""

    id: str
    display_name: str
    group: str  # "direct" | "multi_site"
    site: str | None
    dedup_key: str
    # Mandatory Parquet columns; mirrors `RequiredColumns` in config.ps1.
    required_columns: tuple[str, ...]
    field_map: dict[str, tuple[str, ...]]
    evidence: dict[str, str]


def _multi_site_field_map() -> dict[str, tuple[str, ...]]:
    return {
        "id": ("job_id",),
        "title": ("title",),
        "company": ("company_name",),
        "description": ("description_full", "description_snippet"),
        "salary": ("salary_raw", "salary_min", "salary_max"),
        "skills": ("skills",),
        "work_mode": ("work_mode",),
        "location": (
            "location_raw",
            "location_city",
            "location_region",
            "location_country",
        ),
        "posted_date": ("posted_datetime",),
    }


def _general_log() -> str:
    return "scrapers-pipeline/logs/upload-<fecha>.log"


def _multi_site_evidence(site: str) -> dict[str, str]:
    base = f"multi_site_job_scraper/data/{site}"
    return {
        "general_log": _general_log(),
        "run_log": f"{base}/run.log",
        "attempt_logs": f"{base}/run_console.log",
        "checkpoints": f"{base}/checkpoints",
        "snapshot": f"{base}/output/jobs.parquet",
        "result": "multi_site_job_scraper/data/merged/last_run.json",
        "manifest": "landing/multi_site",
        "landing": "landing/multi_site/dia=<fecha>",
    }


_DIRECT_SOURCES: tuple[Source, ...] = (
    Source(
        id="indeed",
        display_name="Indeed",
        group="direct",
        site=None,
        dedup_key="job_key",
        required_columns=_DIRECT_REQUIRED_COLUMNS["indeed"],
        field_map={
            "id": ("job_key",),
            "title": ("title",),
            "company": ("company",),
            "description": ("description_text",),
            "salary": ("salary_text", "salary_min", "salary_max"),
            "skills": (),
            "work_mode": ("workplace_type",),
            "location": ("location", "city", "state", "country"),
            "posted_date": ("posted_date",),
        },
        evidence={
            "general_log": _general_log(),
            "run_log": "indeed_jobs_scraper/output/nightly.log",
            "attempt_logs": "indeed_jobs_scraper/output/nightly_*_attempt*.log",
            "checkpoints": "indeed_jobs_scraper/output/checkpoints",
            "snapshot": "indeed_jobs_scraper/output/indeed_jobs_*.parquet",
            "result": "indeed_jobs_scraper/output/last_nightly_run_indeed.txt",
            "manifest": "landing/indeed",
            "landing": "landing/indeed/dia=<fecha>",
        },
    ),
    Source(
        id="linkedin",
        display_name="LinkedIn",
        group="direct",
        site=None,
        dedup_key="job_id",
        required_columns=_DIRECT_REQUIRED_COLUMNS["linkedin"],
        field_map={
            "id": ("job_id",),
            "title": ("title",),
            "company": ("company_name",),
            "description": ("description_full",),
            "salary": ("salary_raw", "salary_min", "salary_max"),
            "skills": ("skills",),
            "work_mode": ("work_mode",),
            "location": (
                "location_raw",
                "location_city",
                "location_region",
                "location_country",
            ),
            "posted_date": ("posted_datetime",),
        },
        evidence={
            "general_log": _general_log(),
            "run_log": "linkedin_jobs_scraper/data/run_nightly.log",
            "attempt_logs": "linkedin_jobs_scraper/data/nightly_*_attempt*.log",
            "checkpoints": "linkedin_jobs_scraper/data/checkpoints",
            "snapshot": "linkedin_jobs_scraper/data/output/jobs.parquet",
            "result": "linkedin_jobs_scraper/data/last_nightly_run.txt",
            "manifest": "landing/linkedin",
            "landing": "landing/linkedin/dia=<fecha>",
        },
    ),
    Source(
        id="infojobs",
        display_name="InfoJobs",
        group="direct",
        site=None,
        dedup_key="id_oferta",
        required_columns=_DIRECT_REQUIRED_COLUMNS["infojobs"],
        field_map={
            "id": ("id_oferta",),
            "title": ("titulo",),
            "company": ("empresa",),
            "description": ("descripcion_snippet",),
            "salary": ("salario_raw", "salario_min", "salario_max"),
            "skills": (),
            "work_mode": ("modalidad",),
            "location": ("ciudad", "provincia", "pais"),
            "posted_date": ("fecha_publicacion",),
        },
        evidence={
            "general_log": _general_log(),
            "run_log": "infojobs_jobs_scraper/data/run_nightly.log",
            "attempt_logs": "infojobs_jobs_scraper/data/nightly_*_attempt*.log",
            "checkpoints": "infojobs_jobs_scraper/data/delta_table",
            "snapshot": "infojobs_jobs_scraper/data/offers_*.parquet",
            "result": "infojobs_jobs_scraper/data/last_nightly_run.txt",
            "manifest": "landing/infojobs",
            "landing": "landing/infojobs/dia=<fecha>",
        },
    ),
)

_PORTAL_DISPLAY_NAMES: dict[str, str] = {
    "irishjobs": "IrishJobs",
    "stepstone_nl": "StepStone NL",
    "devitjobs": "DevITjobs",
    "nvb": "NVB",
    "jobs_ch": "Jobs.ch",
    "glassdoor": "Glassdoor",
}

_MULTI_SITE_SOURCES: tuple[Source, ...] = tuple(
    Source(
        id=site,
        display_name=_PORTAL_DISPLAY_NAMES[site],
        group="multi_site",
        site=site,
        dedup_key="job_id",
        required_columns=_MULTI_SITE_REQUIRED_COLUMNS,
        field_map=_multi_site_field_map(),
        evidence=_multi_site_evidence(site),
    )
    for site in MULTI_SITE_SITE_IDS
)

_SOURCES: tuple[Source, ...] = _DIRECT_SOURCES + _MULTI_SITE_SOURCES
_BY_ID: dict[str, Source] = {source.id: source for source in _SOURCES}

# The six real `site` values of Multi-site, in run order.
MULTI_SITE_SITES: tuple[str, ...] = MULTI_SITE_SITE_IDS


def all_sources() -> tuple[Source, ...]:
    """Return the nine independent sources in run order."""
    return _SOURCES


def get_source(source_id: str) -> Source:
    """Return a source by id; raises KeyError if unknown."""
    return _BY_ID[source_id]


def source_ids() -> tuple[str, ...]:
    """Return the ids of the nine independent sources in run order."""
    return tuple(source.id for source in _SOURCES)


def field_map(source_id: str) -> dict[str, tuple[str, ...]]:
    """Return the canonical field -> origin column alias map of a source."""
    return get_source(source_id).field_map


def field_aliases(source_id: str, field: str | None = None):
    """Return the origin column aliases of a source.

    With `field`, return that canonical field's aliases (possibly empty);
    without it, return the full canonical field -> aliases map.
    """
    mapping = field_map(source_id)
    if field is None:
        return mapping
    return mapping[field]


def dedup_key(source_id: str) -> str:
    """Return the unique-offer deduplication key of a source."""
    return get_source(source_id).dedup_key


def required_columns(source_id: str) -> tuple[str, ...]:
    """Return the mandatory Parquet columns of a source (mirrors config.ps1)."""
    return get_source(source_id).required_columns


def multi_site_portals() -> tuple[Source, ...]:
    """Return the six Multi-site portals, each measured independently."""
    return _MULTI_SITE_SOURCES
