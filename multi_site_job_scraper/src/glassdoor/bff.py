"""Acceso al endpoint GraphQL/BFF de Glassdoor (Apollo).

Glassdoor (2026) carga las ofertas del SERP via POST JSON al endpoint
"{base_url}/graph" (Apollo) con la operacion JobSearchResultsQuery. La
paginacion se hace por cursores (paginationCursors), no por p=N.

Flujo:
  1. Bootstrap de sesion: visitar la homepage para cookies y CSRF.
  2. CSRF token: se extrae del HTML de la homepage (patron "token":"...").
  3. POST /graph con el payload GraphQL y header gd-csrf-token.
  4. Parseo de jobListings y cursor siguiente.

Referencia: JobSpy (speedyapply/JobSpy) y capturas reales de 2026.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

GRAPH_ENDPOINT = "/graph"
BFF_ENDPOINT = "/job-search-next/bff/jobSearchResultsQuery"

# Token publico de referencia (JobSpy) usado cuando no se puede extraer el
# token real de la pagina. El servidor lo acepta para sesiones sin login.
FALLBACK_CSRF_TOKEN = (
    "Ft6oHEWlRZrxDww95Cpazw:0pGUrkb2y3TyOpAIqF2vbPmUXoXVkD3oEGDVkvfeCerceQ5"
    "-n8mBg3BovySUIjmCPHCaW0H2nQVdqzbtsYqf4Q"
    ":wcqRqeegRUa9MVLJGyujVXB7vWFPjdaS1CtrrzJq-ok"
)

APOLLO_HEADERS = {
    "apollographql-client-name": "job-search-next",
    "apollographql-client-version": "4.65.5",
}

QUERY_TEMPLATE = """
query JobSearchResultsQuery(
    $excludeJobListingIds: [Long!],
    $keyword: String,
    $locationId: Int,
    $locationType: LocationTypeEnum,
    $numJobsToShow: Int!,
    $pageCursor: String,
    $pageNumber: Int,
    $filterParams: [FilterParams],
    $originalPageUrl: String,
    $seoFriendlyUrlInput: String,
    $parameterUrlInput: String,
    $seoUrl: Boolean
) {
    jobListings(
        contextHolder: {
            searchParams: {
                excludeJobListingIds: $excludeJobListingIds,
                keyword: $keyword,
                locationId: $locationId,
                locationType: $locationType,
                numPerPage: $numJobsToShow,
                pageCursor: $pageCursor,
                pageNumber: $pageNumber,
                filterParams: $filterParams,
                originalPageUrl: $originalPageUrl,
                seoFriendlyUrlInput: $seoFriendlyUrlInput,
                parameterUrlInput: $parameterUrlInput,
                seoUrl: $seoUrl,
                searchType: SR
            }
        }
    ) {
        companyFilterOptions {
            id
            shortName
            __typename
        }
        filterOptions
        indeedCtk
        jobListings {
            ...JobView
            __typename
        }
        jobListingSeoLinks {
            linkItems {
                position
                url
                __typename
            }
            __typename
        }
        jobSearchTrackingKey
        jobsPageSeoData {
            pageMetaDescription
            pageTitle
            __typename
        }
        paginationCursors {
            cursor
            pageNumber
            __typename
        }
        indexablePageForSeo
        searchResultsMetadata {
            searchCriteria {
                implicitLocation {
                    id
                    localizedDisplayName
                    type
                    __typename
                }
                keyword
                location {
                    id
                    shortName
                    localizedShortName
                    localizedDisplayName
                    type
                    __typename
                }
                __typename
            }
            helpCenterDomain
            helpCenterLocale
            jobSerpJobOutlook {
                occupation
                paragraph
                __typename
            }
            showMachineReadableJobs
            __typename
        }
        totalJobsCount
        __typename
    }
}

fragment JobView on JobListingSearchResult {
    jobview {
        header {
            adOrderId
            advertiserType
            adOrderSponsorshipLevel
            ageInDays
            divisionEmployerName
            easyApply
            employer {
                id
                name
                shortName
                __typename
            }
            employerNameFromSearch
            goc
            gocConfidence
            gocId
            jobCountryId
            jobLink
            jobResultTrackingKey
            jobTitleText
            locationName
            locationType
            locId
            needsCommission
            payCurrency
            payPeriod
            payPeriodAdjustedPay {
                p10
                p50
                p90
                __typename
            }
            rating
            salarySource
            savedJobId
            sponsored
            __typename
        }
        job {
            description
            importConfigId
            jobTitleId
            jobTitleText
            listingId
            __typename
        }
        jobListingAdminDetails {
            cpcVal
            importConfigId
            jobListingId
            jobSourceId
            userEligibleForAdminJobDetails
            __typename
        }
        overview {
            shortName
            squareLogoUrl
            __typename
        }
        __typename
    }
    __typename
}
"""

_RE_CSRF = [
    re.compile(r'"token"\s*:\s*"([^"]+)"'),
    re.compile(r'gdCSRFToken\s*=\s*"([^"]+)"'),
    re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"'),
]


def extract_csrf_token(html: str) -> str:
    """Extrae el CSRF token del HTML de Glassdoor."""
    if not html:
        return ""
    for pat in _RE_CSRF:
        m = pat.search(html)
        if m and m.group(1):
            return m.group(1)
    return ""


def build_payload(keyword: str, location_id: int, location_type: str,
                  page_num: int, cursor: Optional[str] = None,
                  fromage: Optional[int] = None,
                  num_jobs: int = 30,
                  original_page_url: str = "") -> str:
    """Construye el payload GraphQL de JobSearchResultsQuery.

    Espejo del payload que usa JobSpy (2026): filterParams para fromAge,
    y 'fromage'/'sort' a nivel de variables.
    """
    filter_params = []
    if fromage:
        filter_params.append({"filterKey": "fromAge", "values": str(fromage)})
    variables = {
        "excludeJobListingIds": [],
        "filterParams": filter_params,
        "keyword": keyword,
        "locationId": int(location_id),
        "locationType": location_type,
        "numJobsToShow": num_jobs,
        "originalPageUrl": original_page_url,
        "pageCursor": cursor,
        "pageNumber": page_num,
        "parameterUrlInput": f"IL.0,12_I{location_type}{location_id}",
        "fromage": fromage,
        "sort": "date",
    }
    return json.dumps([{
        "operationName": "JobSearchResultsQuery",
        "variables": variables,
        "query": QUERY_TEMPLATE,
    }])


def _extract_listings(resp_json, next_page_num: int = 2) -> tuple[list, str, int]:
    """Extrae (listings, next_cursor, total_count) de la respuesta.

    next_cursor: cursor de la pagina siguiente (pageNumber == next_page_num),
    o el del mayor pageNumber si no hay coincidencia.

    Tolerante a errores parciales de Apollo que NO afecten a jobListings
    (ej: sub-errores en jobsPageSeoData).
    """
    if isinstance(resp_json, list):
        resp_json = resp_json[0] if resp_json else {}
    if not isinstance(resp_json, dict):
        return [], "", 0

    errors = resp_json.get("errors") or []
    data = resp_json.get("data") or {}
    listings_node = data.get("jobListings") or {}
    listings = listings_node.get("jobListings") or []

    if not listings:
        if errors:
            fatal = False
            for err in errors:
                path = err.get("path") or []
                msg = str(err.get("message", ""))
                if (path == ["jobListings"] or
                        (isinstance(path, list) and len(path) == 1
                         and path[0] == "jobListings")):
                    fatal = True
                    break
                # Errores del subrequest de SEO (jobsPageSeoData) NO son fatales
                if not isinstance(path, list):
                    continue
                if any("jobsPageSeoData" in str(p) for p in path):
                    continue
                if "jobListings" in msg and "jobsPageSeoData" not in msg:
                    fatal = True
                    break
            if fatal:
                log.warning("GraphQL: error fatal en jobListings -> %s",
                            json.dumps(errors)[:200])
                return [], "", 0
        log.debug("Sin jobListings en respuesta (errors=%d, total=%s)",
                  len(errors or []), listings_node.get("totalJobsCount"))
        return [], "", 0

    cursor = ""
    cursors = listings_node.get("paginationCursors") or []
    if isinstance(cursors, list):
        best = None
        for c in cursors:
            if not isinstance(c, dict):
                continue
            pn = c.get("pageNumber")
            if pn == next_page_num:
                best = c
                break
            if best is None or (isinstance(pn, int)
                                and isinstance(best.get("pageNumber"), int)
                                and pn > best.get("pageNumber")):
                best = c
        if best and best.get("cursor"):
            cursor = best["cursor"]

    total = listings_node.get("totalJobsCount") or 0
    return listings, cursor, int(total) if isinstance(total, (int, float)) else 0


def search_jobs(context_request, base_url: str, csrf_token: str,
                keyword: str, location_id: int, location_type: str,
                page_num: int, cursor: Optional[str] = None,
                fromage: Optional[int] = None,
                num_jobs: int = 30,
                original_page_url: str = "",
                user_agent: str = "") -> tuple[list, str, int]:
    """Ejecuta la busqueda GraphQL y devuelve (listings, cursor, total).

    context_request: el APIRequestContext de un BrowserContext de Playwright
    (comparte cookies con el navegador persistente).

    Cloudflare bloquea la llamada de forma intermitente (403): se reintenta
    con backoff, alternando el orden de los endpoints.
    """
    payload = build_payload(keyword, location_id, location_type, page_num,
                            cursor, fromage, num_jobs, original_page_url)
    token = csrf_token or FALLBACK_CSRF_TOKEN
    max_attempts = 3

    for attempt in range(max_attempts):
        endpoints = [GRAPH_ENDPOINT, BFF_ENDPOINT]
        if attempt % 2 == 1:
            endpoints.reverse()

        for endpoint in endpoints:
            url = f"{base_url.rstrip('/')}{endpoint}"
            headers = {
                "content-type": "application/json",
                "gd-csrf-token": token,
                "origin": base_url.rstrip("/"),
                "referer": f"{base_url.rstrip('/')}/",
                "accept": "*/*",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                **APOLLO_HEADERS,
            }
            if user_agent:
                headers["user-agent"] = user_agent
            try:
                resp = context_request.post(url, data=payload, headers=headers,
                                            timeout=30000)
            except Exception as e:
                log.debug("POST %s fallido (intento %d): %s",
                          endpoint, attempt + 1, e)
                continue
            if resp.status in (403, 429):
                log.debug("POST %s -> status %s (intento %d)",
                          endpoint, resp.status, attempt + 1)
                continue
            if resp.status != 200:
                log.debug("POST %s -> status %s", endpoint, resp.status)
                continue
            try:
                resp_json = resp.json()
            except Exception as e:
                log.debug("JSON invalido en %s: %s", endpoint, e)
                continue
            listings, cursor_next, total = _extract_listings(resp_json, page_num + 1)
            if listings:
                log.info("BFF %s: %d ofertas (total=%s, cursor=%s)",
                         endpoint, len(listings), total, bool(cursor_next))
                return listings, cursor_next, total
            log.debug("%s respondio sin listados", endpoint)

        if attempt < max_attempts - 1:
            import time as _t
            _t.sleep(4 + attempt * 3)

    return [], "", 0


def resolve_location(context_request, base_url: str,
                     location: str) -> tuple[Optional[int], str]:
    """Resuelve una ubicacion a (locationId, locationType).

    Usa el endpoint ajax de autocompletado de Glassdoor.
    """
    if not location:
        return None, ""
    import urllib.parse
    term = urllib.parse.quote(location)
    url = (f"{base_url.rstrip('/')}/findPopularLocationAjax.htm"
           f"?maxLocationsToReturn=10&term={term}")
    try:
        resp = context_request.get(url, timeout=20000)
        if resp.status != 200:
            log.warning("resolve_location %s -> status %s", location, resp.status)
            return None, ""
        items = resp.json()
    except Exception as e:
        log.warning("resolve_location fallo para '%s': %s", location, e)
        return None, ""
    if not isinstance(items, list) or not items:
        log.warning("Ubicacion '%s' no encontrada en Glassdoor", location)
        return None, ""
    first = items[0]
    loc_type = first.get("locationType", "")
    if loc_type == "C":
        loc_type = "CITY"
    elif loc_type == "S":
        loc_type = "STATE"
    elif loc_type == "N":
        loc_type = "COUNTRY"
    try:
        return int(first.get("locationId", 0)), loc_type
    except (TypeError, ValueError):
        return None, ""


def parse_jobview(jobview: dict, base_url: str,
                  country: str = "") -> Optional[dict]:
    """Convierte un jobview de la API en tarjeta normalizada.

    Devuelve None si el objeto no tiene listingId (no es una oferta valida).
    """
    if not isinstance(jobview, dict):
        return None
    # La respuesta real envuelve cada resultado en {"jobview": {...}}
    if "jobview" in jobview and isinstance(jobview["jobview"], dict):
        jobview = jobview["jobview"]
    header = jobview.get("header") or {}
    job = jobview.get("job") or {}
    employer = header.get("employer") or {}

    listing_id = job.get("listingId") or ""
    if not listing_id:
        return None

    title = job.get("jobTitleText") or header.get("jobTitleText") or ""
    company = (header.get("employerNameFromSearch")
               or (employer.get("name") if isinstance(employer, dict) else "")
               or "")
    company_id = employer.get("id") if isinstance(employer, dict) else ""

    # Defensa anti-contaminacion: nunca dejar que un rating o una URL
    # sobreviva en company_name (caso real visto en produccion: "4,0").
    if _is_rating_str(company) or re.match(r"^https?://", company.strip()):
        company = ""

    location_name = header.get("locationName") or ""
    location_type = header.get("locationType") or ""
    work_mode = _work_mode_from(location_type, location_name, title)
    if location_type == "S" or "remote" in str(location_name).lower():
        location_raw = location_name if location_name else "Remote"
    else:
        location_raw = location_name

    age_days = header.get("ageInDays")
    posted_datetime = None
    posted_relative = ""
    if isinstance(age_days, (int, float)):
        age = int(age_days)
        posted_datetime = (datetime.utcnow() - timedelta(days=age)).isoformat(
            timespec="seconds") + "Z"
        posted_relative = f"{age} days ago" if age else "Today"

    salary_raw = ""
    salary_raw_original = ""
    salary_min = salary_max = None
    salary_currency = header.get("payCurrency", "")
    pay_period = header.get("payPeriod", "")
    salary_source = header.get("salarySource", "")
    adjusted = header.get("payPeriodAdjustedPay") or {}
    if isinstance(adjusted, dict):
        p10 = adjusted.get("p10")
        p90 = adjusted.get("p90")
        if isinstance(p10, (int, float)) and p10:
            salary_min = float(p10)
        if isinstance(p90, (int, float)) and p90:
            salary_max = float(p90)
        if salary_min or salary_max:
            salary_raw_original = _format_salary(salary_min, salary_max,
                                                 salary_currency, pay_period)
        # Validacion: payPeriodAdjustedPay es un importe AJUSTADO/ESTIMADO por
        # Glassdoor (no siempre un salario publicado por el empleador). Solo se
        # acepta un rango positivo y ordenado (min <= max); si no, se descarta.
        if (salary_min is not None and salary_min <= 0) or \
           (salary_max is not None and salary_max <= 0):
            salary_min, salary_max = None, None
        if salary_min is not None and salary_max is not None \
                and salary_min > salary_max:
            salary_min, salary_max = None, None
        if salary_min or salary_max:
            salary_raw = salary_raw_original
    # Marcar como estimado salvo que Glassdoor indique que el importe lo
    # publica el propio empleador.
    salary_is_estimated = bool(adjusted) and not (
        salary_source and salary_source.upper() in ("EMP", "EMPLOYER", "COMPANY"))

    rating = header.get("rating")
    rating_str = ""
    if isinstance(rating, (int, float)) and rating > 0:
        rating_str = f"{rating:.1f}"

    description = job.get("description") or ""
    if isinstance(description, (dict, list)):
        description = json.dumps(description, ensure_ascii=False)
    description = _desc_to_text(str(description))

    job_url = f"{base_url.rstrip('/')}/job-listing/j?jl={listing_id}"

    company_url = ""
    if company_id:
        company_url = f"{base_url.rstrip('/')}/Overview/W-EI_IE{company_id}.htm"

    return {
        "job_id": f"gd_{listing_id}",
        "job_url": job_url,
        "title": title,
        "company_name": company,
        "company_url": company_url,
        "company_rating": rating_str,
        "location_raw": location_raw,
        "location_city": "",
        "location_region": "",
        "location_country": country,
        "posted_datetime": posted_datetime,
        "posted_relative": posted_relative,
        "is_new": bool(age_days is not None and int(age_days) <= 1),
        "salary_raw": salary_raw,
        "salary_raw_original": salary_raw_original,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": salary_currency,
        "salary_period": _normalize_period(pay_period),
        "salary_source": salary_source,
        "salary_is_estimated": salary_is_estimated,
        "work_mode": work_mode,
        "employment_type": "",
        "description_snippet": description[:500] if description else "",
        "description_full": description,
        "easy_apply": bool(header.get("easyApply", False)),
        "sponsored": bool(header.get("sponsored", False)),
        "skills": [],
    }


JOB_DETAIL_QUERY = """
query JobDetailQuery($jl: Long!, $queryString: String, $pageTypeEnum: PageTypeEnum) {
    jobview: jobView(
        listingId: $jl
        contextHolder: {queryString: $queryString, pageTypeEnum: $pageTypeEnum}
    ) {
        job {
            description
            __typename
        }
        __typename
    }
}
"""

_PERIOD_LABEL = {
    "ANNUAL": "year", "YEARLY": "year", "MONTHLY": "month",
    "HOURLY": "hour", "WEEKLY": "week", "DAILY": "day",
}


def _normalize_period(period: str) -> str:
    """Normaliza el periodo salarial del BFF al enum cerrado de la capa
    Silver (YEAR/MONTH/WEEK/DAY/HOUR). El BFF usa ANNUAL; el resto del
    pipeline espera YEAR."""
    return {
        "ANNUAL": "YEAR", "YEARLY": "YEAR",
        "MONTHLY": "MONTH", "WEEKLY": "WEEK",
        "DAILY": "DAY", "HOURLY": "HOUR",
    }.get((period or "").upper(), "")


def _format_salary(min_v: Optional[float], max_v: Optional[float],
                   currency: str, period: str) -> str:
    cur = currency or ""
    parts = []
    if min_v:
        parts.append(f"{int(min_v):,}")
    if max_v:
        parts.append(f"{int(max_v):,}")
    if not parts:
        return ""
    sep = " - " if len(parts) == 2 else ""
    per = ""
    label = _PERIOD_LABEL.get((period or "").upper())
    if label:
        per = f" per {label}"
    return f"{cur} {sep.join(parts)}{per}".strip()


def _desc_to_text(raw) -> str:
    """Convierte la descripcion (HTML) a texto plano."""
    if not raw:
        return ""
    import html as html_mod
    txt = re.sub(r"<[^>]+>", " ", str(raw))
    txt = html_mod.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def fetch_description(context_request, base_url: str, csrf_token: str,
                      listing_id, user_agent: str = "",
                      timeout: int = 30000) -> str:
    """Obtiene la descripcion completa de una oferta via JobDetailQuery."""
    if not listing_id:
        return ""
    body = json.dumps([{
        "operationName": "JobDetailQuery",
        "variables": {
            "jl": int(listing_id),
            "queryString": "q",
            "pageTypeEnum": "SERP",
        },
        "query": JOB_DETAIL_QUERY,
    }])
    headers = {
        "content-type": "application/json",
        "gd-csrf-token": csrf_token or FALLBACK_CSRF_TOKEN,
        "origin": base_url.rstrip("/"),
        "referer": f"{base_url.rstrip('/')}/",
        "accept": "*/*",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        **APOLLO_HEADERS,
    }
    if user_agent:
        headers["user-agent"] = user_agent

    url = f"{base_url.rstrip('/')}{GRAPH_ENDPOINT}"
    for attempt in range(2):
        try:
            resp = context_request.post(url, data=body, headers=headers,
                                        timeout=timeout)
        except Exception as e:
            log.debug("JobDetailQuery %s intento %d fallido: %s",
                      listing_id, attempt + 1, e)
            continue
        if resp.status != 200:
            log.debug("JobDetailQuery %s -> status %s (intento %d)",
                      listing_id, resp.status, attempt + 1)
            continue
        try:
            resp_json = resp.json()
        except Exception:
            continue
        if isinstance(resp_json, list):
            resp_json = resp_json[0] if resp_json else {}
        data = resp_json.get("data") or {}
        jobview = data.get("jobview") or {}
        desc = (jobview.get("job") or {}).get("description")
        if desc:
            return _desc_to_text(desc)
    return ""


def parse_location_raw(location_raw: str) -> tuple[str, str, str]:
    """Parsea location 'City, ST, Country' / 'Remote' -> (city, region, country)."""
    if not location_raw:
        return "", "", ""
    if location_raw.lower() in ("remote",):
        return location_raw, "", ""
    parts = [p.strip() for p in location_raw.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], "", ""


def _is_rating_str(value: str) -> bool:
    """True si el texto es un rating tipo '4,1', '4.1' o '3,0'."""
    return bool(re.match(r"^\s*\d[,.]\d{1,2}\s*$", value or ""))


def _work_mode_from(location_type: str, location_name: str, title: str) -> str:
    """Deriva work_mode del BFF con mayor cobertura que el codigo anterior.

    Prioridad (misma que _WRK_RULES en enrich: "remote hybrid" es hibrido):
      1. Texto explicito de locationName/titulo: hybrid > remote > on-site.
      2. locationType 'S' (structured remote) -> Remote.
      3. locationType 'O' (office) -> On-site.
    """
    if not location_name and not title:
        return ""
    blob = f"{location_name} {title}".lower()
    if re.search(r"\bhybrid\b|\bhibrid[oa]?\b|híbrid[oa]?\b|semipresencial", blob):
        return "Hybrid"
    if location_type == "S" or re.search(r"\bremote\b|\bremoto\b|teletrabajo|\bwfh\b", blob):
        return "Remote"
    if location_type == "O" or re.search(r"\bon[- ]?site\b|\bonsite\b|presencial", blob):
        return "On-site"
    return ""