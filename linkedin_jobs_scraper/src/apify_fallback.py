"""Respaldo comercial via Apify: actor "curious_coder/linkedin-jobs-scraper".

Si el nucleo local (Playwright+patchright) detecta un bloqueo (BlockedException),
main.py conmuta a este modulo para los (role, ciudad) restantes.

Apify gestiona el anti-bot/CAPTCHAs/proxies en SU infraestructura; nosotros
solo hacemos la llamada API. NO es evasion de CAPTCHA por nuestra parte:
externalizamos a un proveedor que cumple sus propias licencias.

Necesita APIFY_TOKEN en .env (vacio -> fallback desactivado con aviso).

Esquema de input del actor (verificado en apify.com/curious_coder/linkedin-jobs-scraper):
  - urls (array, required): URLs de busqueda de LinkedIn Jobs (mismo formato
    que build_search_url() de search.py)
  - scrapeCompany (bool, default true): scrapear tambien detalles de empresa
  - count (int, min 10): limite de ofertas a scrapear

Esquema de output (campos del dataset):
  id, link, title, companyName, companyLinkedinUrl, companyLogo, location,
  salaryInfo (array [min, max]), postedAt, benefits, descriptionHtml,
  applicantsCount (string), applyUrl, descriptionText, jobPosterName,
  jobPosterTitle, jobPosterPhoto, jobPosterProfileUrl, seniorityLevel,
  employmentType, jobFunction, industries, companyDescription,
  companyWebsite, companyEmployeesCount
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urlencode

from .models import JobOffer, now_utc_iso, normalize_experience_level
from .parse_sections import split_description_sections
from .parse_serp import parse_location

log = logging.getLogger(__name__)

JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search/"

# Actor Apify por defecto (pinado). Si needs cambiar, sobreescribir apify_actor
# en config.yaml. Pinar evita divergencia de esquema entre runs (ver run.log:12
# vs run.log:37 mostraban dos esquemas incompatibles en corridas distintas).
APIFY_DEFAULT_ACTOR = "curious_coder/linkedin-jobs-scraper"
APIFY_DEFAULT_ACTOR_MIN_COUNT = 10  # el actor exige minimo 10 en count


class ApifyNotConfiguredError(RuntimeError):
    """APIFY_TOKEN no configurado en .env."""


def _get_client():
    """Construye ApifyClient; lanza ApifyNotConfiguredError si no hay token."""
    token = os.getenv("APIFY_TOKEN", "").strip()
    if not token:
        raise ApifyNotConfiguredError(
            "APIFY_TOKEN vacio en .env. Respaldo Apify no disponible."
        )
    try:
        from apify_client import ApifyClient
    except ImportError as e:
        raise ApifyNotConfiguredError(
            "apify-client no instalado. Ejecuta: pip install apify-client"
        ) from e
    return ApifyClient(token)


def _build_search_url(role: str, city_cfg: dict[str, str],
                      date_filter: str) -> str:
    """Construye la URL de busqueda (igual que search.build_search_url)."""
    params: dict[str, str] = {
        "keywords": role,
        "location": city_cfg["text"],
    }
    geo_id = city_cfg.get("geoId")
    if geo_id:
        params["geoId"] = geo_id
    if date_filter:
        params["f_TPR"] = date_filter
    return JOBS_SEARCH_URL + "?" + urlencode(params)


def _build_run_input(role: str, city_cfg: dict[str, str],
                     config: dict[str, Any]) -> dict[str, Any]:
    """Construye el input para curious_coder/linkedin-jobs-scraper.

    El actor requiere 'urls' (array de URLs de busqueda de LinkedIn Jobs).
    """
    date_filter = config.get("date_filter", "r2592000")
    url = _build_search_url(role, city_cfg, date_filter)
    count = config.get("jobs_per_search", 25)
    return {
        "urls": [url],
        "scrapeCompany": True,
        "count": max(count, 10),  # el actor tiene minimo 10
    }


def _parse_salary_info(salary_info: Any) -> tuple[str, Optional[float],
                                                   Optional[float], str, str]:
    """Parsea salaryInfo del actor (array [min, max] de strings).

    Ej: ["$17.00", "$19.00"] -> ("$17.00-$19.00", 17.0, 19.0, "USD", "hour")
    Ej: ["40.000", "55.000"] -> ("40.000-55.000", 40000.0, 55000.0, "EUR", "")
    """
    if not salary_info or not isinstance(salary_info, list):
        return "", None, None, "", ""
    raw = "-".join(str(s) for s in salary_info)

    def _to_float(s: str) -> Optional[float]:
        s2 = s.replace("$", "").replace("€", "").replace("£", "").strip()
        s2 = s2.replace(".", "").replace(",", ".") if "." in s2 and "," in s2 else s2.replace(",", "")
        try:
            return float(s2)
        except ValueError:
            return None

    smin = _to_float(salary_info[0]) if len(salary_info) > 0 else None
    smax = _to_float(salary_info[1]) if len(salary_info) > 1 else smin

    currency = ""
    raw_str = str(salary_info[0]) if salary_info else ""
    if "$" in raw_str:
        currency = "USD"
    elif "€" in raw_str:
        currency = "EUR"
    elif "£" in raw_str:
        currency = "GBP"

    period = ""
    if "/hr" in raw.lower() or "/hour" in raw.lower():
        period = "hour"
    elif "/yr" in raw.lower() or "/year" in raw.lower() or "/año" in raw.lower() or "/ano" in raw.lower():
        period = "year"
    elif "/mo" in raw.lower() or "/month" in raw.lower() or "/mes" in raw.lower():
        period = "month"

    return raw, smin, smax, currency, period


def _normalize_apify_item(item: dict[str, Any], role: str, city: str) -> JobOffer:
    """Convierte un item JSON del dataset de Apify a JobOffer.

    Mapea los campos del actor curious_coder/linkedin-jobs-scraper al
    esquema JobOffer (homogeneo con el nucleo local).
    """
    def first(*keys: str, default=""):
        for k in keys:
            if k in item and item[k] not in (None, ""):
                return item[k]
        return default

    job_id = str(first("id", "jobId", default=""))
    if not job_id:
        url = first("link", "url", default="")
        m = re.search(r"-(\d{6,})(?:\?|$)", url)
        job_id = m.group(1) if m else ""

    raw_loc = first("location", default="")
    city_p, region, country = parse_location(raw_loc)

    # Salario: salaryInfo es un array [min, max]
    salary_info = first("salaryInfo", default=None)
    salary_raw, smin, smax, currency, period = _parse_salary_info(salary_info)

    # applicantsCount viene como string ("200")
    applicants_raw = first("applicantsCount", "applicantsCount", default="")
    num_applicants = None
    if applicants_raw:
        try:
            num_applicants = int(str(applicants_raw).replace(".", "").replace(",", ""))
        except ValueError:
            num_applicants = None

    # companyEmployeesCount puede ser int o string
    emp_count = first("companyEmployeesCount", "employees", default="")
    company_size = str(emp_count) if emp_count else ""

    # seniorityLevel -> experience_level (mapeo unificado en models.py)
    seniority = normalize_experience_level(str(first("seniorityLevel", default="")))
    # employmentType ya viene en ingles (Contract, Full-time, etc.)

    desc_html = first("descriptionHtml", default="")
    desc_text = first("descriptionText", "description", default="")

    offer = JobOffer(
        job_id=job_id,
        job_url=first("link", "url", default=""),
        title=first("title", default=""),
        company_name=first("companyName", "company", default=""),
        location_raw=raw_loc,
        location_city=city_p,
        location_region=region,
        location_country=country,
        posted_datetime=first("postedAt", "postedDate", default=None),
        posted_relative="",
        is_new=False,
        company_url=first("companyLinkedinUrl", "companyUrl", default=""),
        company_industry=first("industries", "companyIndustry", default=""),
        company_size=company_size,
        work_mode="",
        employment_type=str(first("employmentType", default="")),
        experience_level=seniority,
        salary_raw=salary_raw,
        salary_min=smin,
        salary_max=smax,
        salary_currency=currency,
        salary_period=period,
        num_applicants=num_applicants,
        description_full=desc_text,
        skills=[],  # este actor no devuelve skills destacadas
        search_role=role,
        search_city=city,
        source="apify",
        scraped_at=now_utc_iso(),
    )

    # Segmentar descripcion: preferir HTML del actor si esta disponible
    if desc_html:
        sections = split_description_sections(desc_html, source="html")
    elif desc_text:
        sections = split_description_sections(desc_text, source="text")
    else:
        sections = {}
    offer.role_summary = sections.get("role_summary", "")
    offer.company_description = sections.get("company_description", "")
    offer.responsibilities = sections.get("responsibilities", "")
    offer.requirements = sections.get("requirements", "")
    offer.benefits = sections.get("benefits", "")

    return offer


def scrape(role: str, city_name: str, city_cfg: dict[str, str],
           config: dict[str, Any]) -> list[JobOffer]:
    """Llama al actor de Apify y devuelve lista de JobOffer normalizados.

    Lanza ApifyNotConfiguredError si no hay token.
    Lanza excepcion del propio cliente Apify si el actor falla.
    """
    client = _get_client()
    # Pin del actor: usar DEFAULT_ACTOR constante salvo override explicito.
    actor_id = config.get("apify_actor", APIFY_DEFAULT_ACTOR)
    run_input = _build_run_input(role, city_cfg, config)

    log.info("Apify: llamando actor %s con input=%s", actor_id, run_input)
    try:
        actor = client.actor(actor_id)
        run = actor.call(run_input=run_input)
    except Exception as e:  # noqa: BLE001  actor_id invalido, red, etc.
        log.error("Apify: fallo la llamada al actor %s: %s", actor_id, e)
        return []
    if run is None:
        log.error("Apify: run fue None (posible error de actor).")
        return []

    # El dataset ID puede venir como:
    #   run["defaultDatasetId"]        (apify-client v3, dict-like)
    #   run.defaultDatasetId           (algunas versiones, attr camelCase)
    #   run.default_dataset_id         (apify-client v2 snake_case)
    # Probar las tres formas antes de rendirse.
    dataset_id = None
    for getter in (
        lambda: run["defaultDatasetId"],
        lambda: getattr(run, "defaultDatasetId", None),
        lambda: getattr(run, "default_dataset_id", None),
    ):
        try:
            dataset_id = getter()
            if dataset_id:
                break
        except (KeyError, TypeError, AttributeError):
            continue
    if not dataset_id:
        log.error("Apify: no se pudo obtener defaultDatasetId del run "
                  "(probadas 3 formas). Run keys: %s",
                  list(run.keys()) if hasattr(run, "keys") else type(run))
        return []

    log.info("Apify: dataset ID=%s, obteniendo items...", dataset_id)
    dataset = client.dataset(dataset_id)
    items = list(dataset.iterate_items())
    log.info("Apify: %d items recibidos para '%s' en %s",
             len(items), role, city_name)
    if not items:
        run_id = getattr(run, "id", None)
        if run_id is None and hasattr(run, "get"):
            run_id = run.get("id", None)
        log.warning("Apify: actor devolvio 0 items para '%s' en %s "
                    "(run=%s, dataset=%s).",
                    role, city_name, run_id, dataset_id)

    offers: list[JobOffer] = []
    for it in items:
        try:
            offers.append(_normalize_apify_item(it, role, city_name))
        except Exception as e:  # noqa: BLE001
            log.debug("Apify: item descartado por error: %s", e)
    return offers


def is_available() -> bool:
    """True si APIFY_TOKEN esta configurado."""
    return bool(os.getenv("APIFY_TOKEN", "").strip())
