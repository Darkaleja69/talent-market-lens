"""Orquestador del scraper de DevITJobs.nl.

Flujo:
  1. GET /api/jobsLight -> lista de ofertas (resumen).
  2. Filtrar por techCategory / titulo de datos.
  3. GET /api/job/<_id> -> detalle completo (descripcion, requisitos,
     responsabilidades, salario, perks...).
  4. Mapear a JobOffer y volcar a Store (CSV + Parquet).
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import re
from pathlib import Path
from typing import Any, Optional

from src.core.models import JobOffer, now_utc_iso
from src.core.normalize import as_text, extract_skills
from src.core.store import Store
from . import title_filter_nl
from .api import DevITJobsClient
from .config_nl import DEFAULT_CONFIG

log = logging.getLogger("devitjobs")

_RE_TAGS = re.compile(r"<[^>]+>")
_RE_WORKLOAD = re.compile(r"(\d{1,2})\s*(?:-|–|tot)\s*(\d{1,2})\s*uur", re.I)
_RE_WORKLOAD_SINGLE = re.compile(r"(\d{1,2})\s*uur", re.I)

# Frases de condiciones laborales, para rellenar "benefits" cuando la oferta
# no trae perkKeys estructurados (habitual en el feed de talent.com).
_RE_BENEFIT_HINT = re.compile(
    r"pensioen|vakantie|vakantiedagen|vergoeding|leaseauto|leasefiets|"
    r"mobiliteitsbudget|thuiswerk|bonus|opleidingsbudget|ontwikkelbudget|"
    r"persoonlijk budget|arbeidsvoorwaarden|secundaire|voordelen|"
    r"wij bieden|we bieden|wat wij bieden|reiskosten|verzekering|"
    r"collective insurance|benefits|we offer|perks|allowance",
    re.IGNORECASE,
)

_WORK_MODE = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "office": "On-site",
    "on-site": "On-site",
    "onsite": "On-site",
}

# perkKeys de DevITJobs -> etiqueta legible.
_PERKS = {
    "hybridwork": "Hybrid working",
    "remote1day": "Remote 1 day/week",
    "remote2day": "Remote 2 days/week",
    "remote3day": "Remote 3 days/week",
    "remotefull": "Fully remote",
    "flexiblework": "Flexible working hours",
    "parttime": "Part-time possible",
    "wweek40": "40-hour work week",
    "weeksvacation5": "5 weeks vacation",
    "buymorevacation": "Option to buy extra vacation",
    "vacationbonus": "Vacation bonus",
    "companycar": "Company car",
    "mobilityallowance": "Mobility allowance",
    "publictransport": "Public transport allowance",
    "coparking": "Parking",
    "extraretirement": "Extra retirement contribution",
    "collectiveinsurance": "Collective insurance",
    "bonuspay": "Bonus",
    "individualbudget": "Individual development budget",
    "phonesub": "Phone subscription",
    "hardware": "Hardware",
    "standingdesk": "Standing desk",
    "conferences": "Conference visits",
    "coworkshops": "Workshops",
    "retreat": "Company retreat",
    "teamevents": "Team events",
    "careerpath": "Career path",
    "wellconnected": "Well-connected office",
    "cooloffice": "Cool office",
    "quietoffice": "Quiet office",
    "coffee": "Coffee",
    "freshfruits": "Fresh fruit",
    "sweets": "Snacks",
    "beer": "Beer",
    "discounts": "Discounts",
    "sabbatical": "Sabbatical",
    "startupculture": "Startup culture",
    "intteam": "International team",
}


def _clean_text(value: Any) -> str:
    txt = as_text(value)
    if not txt:
        return ""
    if "<" in txt and ">" in txt:
        txt = _RE_TAGS.sub(" ", txt)
    txt = html_mod.unescape(txt)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "", 0, "0"):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    txt = str(value).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(txt)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _relative_from_dt(d: Optional[dt.datetime]) -> str:
    if not d:
        return ""
    now = dt.datetime.now(dt.timezone.utc)
    secs = (now - d).total_seconds()
    if secs < 3600:
        mins = max(1, int(secs // 60))
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    if secs < 86400:
        hours = int(secs // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(secs // 86400)
    if days == 1:
        return "Yesterday"
    return f"{days} days ago"


def _age_days(d: Optional[dt.datetime]) -> Optional[int]:
    if not d:
        return None
    return int((dt.datetime.now(dt.timezone.utc) - d).total_seconds() // 86400)


def _workload(description: str) -> str:
    """Carga horaria de la oferta -> porcentaje de jornada (40 h/semana = 100%)."""
    def _pct(hours: str) -> int:
        return round(int(hours) / 40 * 100)

    m = _RE_WORKLOAD.search(description or "")
    if m:
        return f"{_pct(m.group(1))}-{_pct(m.group(2))}%"
    m = _RE_WORKLOAD_SINGLE.search(description or "")
    if m:
        return f"{_pct(m.group(1))}%"
    return ""


def _perks_text(perks: Any) -> str:
    if not isinstance(perks, list):
        return ""
    labels = []
    for k in perks:
        key = str(k)
        label = _PERKS.get(key)
        if not label:
            label = re.sub(r"(?<!^)(?=[A-Z])", " ", key).replace("_", " ").title()
        if label and label not in labels:
            labels.append(label)
    return " | ".join(labels)


def _benefits_from_description(description: str) -> str:
    """Extrae frases de condiciones laborales de la descripcion (fallback)."""
    if not description:
        return ""
    parts = re.split(r"(?<=[.;:])\s+|\n", description)
    hits: list[str] = []
    for p in parts:
        p = p.strip(" -•\t")
        if len(p) < 15 or not _RE_BENEFIT_HINT.search(p):
            continue
        if p not in hits:
            hits.append(p)
    return " | ".join(hits)[:1500]


def _benefits_text(perks: Any, description: str) -> str:
    txt = _perks_text(perks)
    return txt if txt else _benefits_from_description(description)


def _company_url(value: Any) -> str:
    v = as_text(value)
    if not v:
        return ""
    if not v.startswith("http"):
        v = "https://" + v
    return v


class DevITJobsScraper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or DEFAULT_CONFIG
        out = self.config.get("output", {})
        project_root = Path(__file__).resolve().parent.parent.parent
        self.store = Store(
            csv_path=project_root / out.get("csv", "data/devitjobs/output/jobs.csv"),
            parquet_path=project_root / out.get("parquet", "data/devitjobs/output/jobs.parquet"),
            checkpoint_dir=project_root / out.get("checkpoint_dir", "data/devitjobs/checkpoints"),
        )

    # ------------------------------------------------------------------ run
    def run(self, max_jobs: int | None = None) -> int:
        cfg = self.config
        site = cfg.get("site", "devitjobs")
        country = cfg.get("country", "NL")
        country_name = cfg.get("country_name", "Netherlands")
        base_url = cfg.get("base_url", "https://devitjobs.nl").rstrip("/")
        tech_categories = set(cfg.get("tech_categories") or [])
        include_title = cfg.get("include_title_match", True)
        allow_all = cfg.get("include_all_technologies", False)
        excludes = [title_filter_nl.normalize_title(p)
                    for p in (cfg.get("exclude_title_patterns") or [])]
        max_age = int(cfg.get("max_age_days", 0) or 0)
        fetch_detail = cfg.get("fetch_detail", True)
        max_detail = int(cfg.get("max_detail_jobs", 0) or 0)
        phrases = title_filter_nl.phrases_from_roles(cfg.get("roles", []))

        delays = cfg.get("delays", {})
        rng_req = tuple(delays.get("between_requests", [0.6, 1.6]))
        rng_detail = tuple(delays.get("between_details", [0.5, 1.4]))

        client = DevITJobsClient(cfg.get("retry"))

        log.info("=== DevITJobs.nl Scraper ===")
        log.info("Filtrando techCategory=%s | titulo=%s | max_age=%sd",
                 sorted(tech_categories) or "-", include_title, max_age or "-")

        try:
            jobs = client.fetch_jobs()
        except Exception as e:
            log.error("Error obteniendo /api/jobsLight: %s", e)
            return 0
        log.info("Ofertas en el tablon: %d", len(jobs))

        candidates = []
        for job in jobs:
            if self._is_candidate(job, tech_categories, include_title,
                                  allow_all, excludes, phrases, max_age):
                candidates.append(job)
        log.info("Candidatas tras filtro: %d", len(candidates))
        if max_jobs:
            candidates = candidates[:max_jobs]

        total_new = 0
        details_visited = 0
        interrupted = False
        try:
            for idx, job in enumerate(candidates, 1):
                log.info("--- [%d/%d] %s | %s ---", idx, len(candidates),
                         job.get("name"), job.get("company"))
                detail = {}
                if fetch_detail and (max_detail == 0 or details_visited < max_detail):
                    try:
                        detail = client.fetch_job_detail(job.get("_id", ""))
                        details_visited += 1
                    except Exception as e:
                        log.debug("Detalle fallido %s: %s", job.get("_id"), e)
                    _sleep(rng_detail)

                offer = self._to_offer(job, detail, site, country, country_name,
                                       base_url)
                if self.store.add(offer):
                    total_new += 1
                    if total_new % 5 == 0:
                        self.store.checkpoint(tag=f"{site}_p{idx}")
                _sleep(rng_req)
        except KeyboardInterrupt:
            interrupted = True
            log.warning("Ctrl+C recibido. Guardando progreso...")

        if interrupted:
            self.store.checkpoint(tag=f"{site}_interrupted")
        self.store.write_all()
        state = "INTERRUMPIDO" if interrupted else "DONE"
        log.info("=== %s %s: %d nuevas, %d total, %d detalles ===",
                 site.upper(), state, total_new, len(self.store), details_visited)
        return total_new

    # -------------------------------------------------------------- filtros
    def _is_candidate(self, job: dict, tech_categories: set[str],
                      include_title: bool, allow_all: bool, excludes: list[str],
                      phrases: list[str], max_age: int) -> bool:
        if job.get("isDisabledOrOutdated") or job.get("isPaused"):
            return False
        name = job.get("name", "")
        norm = title_filter_nl.normalize_title(name)
        if any(ex and ex in norm for ex in excludes):
            return False
        if max_age > 0:
            age = _age_days(_parse_dt(job.get("activeFrom") or job.get("createdAt")))
            if age is not None and age > max_age:
                return False
        if allow_all:
            return True
        if job.get("techCategory") in tech_categories:
            return True
        if include_title and title_filter_nl.is_relevant_title(name, phrases):
            return True
        return False

    # --------------------------------------------------------------- mapeo
    def _to_offer(self, job: dict, detail: dict, site: str, country: str,
                  country_name: str, base_url: str) -> JobOffer:
        # El detalle es superconjunto del resumen: usarlo cuando exista.
        j = {**job, **{k: v for k, v in detail.items() if v not in (None, "", [])}}

        job_id = f"dij_{j.get('_id', '')}"
        slug = j.get("jobUrl") or j.get("_id", "")
        job_url = f"{base_url}/jobs/{slug}" if slug else ""

        city = as_text(j.get("actualCity")) or as_text(j.get("cityCategory"))
        region = as_text(j.get("cityCategory"))
        address = as_text(j.get("address"))
        postal = as_text(j.get("postalCode"))
        loc_parts = []
        if address:
            loc_parts.append(address)
        tail = " ".join(p for p in (postal, city) if p).strip()
        if tail:
            loc_parts.append(tail)
        location_raw = ", ".join(loc_parts) if loc_parts else city

        posted_dt = _parse_dt(j.get("createdAt") or j.get("activeFrom"))
        posted_iso = (j.get("createdAt") or j.get("activeFrom") or "")
        if posted_dt:
            posted_iso = posted_dt.isoformat()

        sal_min = _to_float(j.get("annualSalaryFrom"))
        sal_max = _to_float(j.get("annualSalaryTo"))
        salary_raw = ""
        if sal_min and sal_max:
            if sal_min == sal_max:
                salary_raw = f"€{int(sal_min)} per year"
            else:
                salary_raw = f"€{int(sal_min)} - €{int(sal_max)} per year"
        elif sal_min:
            salary_raw = f"€{int(sal_min)} per year"
        elif sal_max:
            salary_raw = f"€{int(sal_max)} per year"

        description = _clean_text(j.get("description"))
        requirements = _clean_text(j.get("requirementsMustTextArea"))
        responsibilities = _clean_text(j.get("responsibilitiesTextArea"))

        skills: list[str] = []
        for src in (j.get("technologies"), j.get("filterTags")):
            if isinstance(src, list):
                for s in src:
                    s = as_text(s)
                    if s and s not in skills:
                        skills.append(s)
        for s in extract_skills(" ".join([description, requirements, responsibilities])):
            if s not in skills:
                skills.append(s)

        work_mode = _WORK_MODE.get(as_text(j.get("workplace")).lower(), "")

        company_url = _company_url(j.get("companyWebsiteLink"))
        if not company_url:
            lite = as_text(j.get("liteCompanyPageUrl"))
            if lite:
                company_url = f"{base_url}/company-jobs/{lite}"

        return JobOffer(
            job_id=job_id,
            job_url=job_url,
            title=as_text(j.get("name")),
            company_name=as_text(j.get("company")),
            location_raw=location_raw,
            location_city=city,
            location_region=region,
            location_country=country_name,
            posted_datetime=posted_iso,
            posted_relative=_relative_from_dt(posted_dt),
            is_new=(_age_days(posted_dt) or 99) <= 1,
            company_url=company_url,
            company_industry=as_text(j.get("companyType")),
            company_size=as_text(j.get("companySize")),
            work_mode=work_mode,
            employment_type=as_text(j.get("jobType")),
            experience_level=as_text(j.get("expLevel")),
            salary_raw=salary_raw,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency="EUR" if salary_raw else "",
            salary_period="YEAR" if salary_raw else "",
            salary_source="EMPLOYER_PROVIDED" if salary_raw else "",
            salary_is_estimated=False,
            description_full=description,
            description_snippet=description[:300],
            responsibilities=responsibilities,
            requirements=requirements,
            benefits=_benefits_text(j.get("perkKeys"), description),
            skills=skills,
            site=site,
            country=country,
            workload_pct=_workload(description),
            salary_disclosed=bool(salary_raw),
            search_role=as_text(j.get("techCategory")),
            search_city=city,
            source="local",
            scraped_at=now_utc_iso(),
        )


def _sleep(rng: tuple[float, float]) -> None:
    import random
    import time
    if rng and rng[0] >= 0:
        time.sleep(random.uniform(*rng))
