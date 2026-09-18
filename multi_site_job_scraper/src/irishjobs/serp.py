"""Parseo del SERP de IrishJobs / StepStone (Playwright).

Plataforma StepStone: React SPA con data-testid attributes estables.
Selectores verificados contra HTML renderizado real de IrishJobs.
"""
from __future__ import annotations

import logging
import time
import re
from typing import Optional
from urllib.parse import urlencode, urljoin

log = logging.getLogger(__name__)

# IrishJobs: /job/<slug>-job<id>.html  |  StepStone NL (mismo scraper): /banen--<slug>-<id>-inline.html
# Si no coincide NINGUN patron, job_id = "" en vez de guardar la ruta completa
# como identificador (contaminacion cruzada historica: job_id = "/banen--...").
_RE_JOB_ID_FROM_URL = re.compile(
    r"-job(\d+)|-(\d{4,})(?:-inline)?\.html(?:[?#].*)?$"
)
# Salario de tarjeta. Cubre formato ingles (per annum/year/month/hour/day)
# y neerlandes (per jaar/maand/uur/dag/week, p/m, p/j, p/u).
_RE_SALARY = re.compile(
    r"(?:€\s*Not\s+Disclosed"
    r"|(?:€|EUR)\s*([\d][\d.,]*)\s*"
    r"(?:-\s*(?:€\s*)?([\d][\d.,]*))?\s*"
    r"(?:per\s+(annum|year|month|hour|day|week|jaar|maand|uur|dag)"
    r"|p\s*[./]?\s*(m|j|u)\.?))",
    re.IGNORECASE,
)
_RE_DATE_SHORT = re.compile(
    r"(\d+)\s+(?:hour|day|week|month|year|uur|dag|week|maand|jaar)s?\s+"
    r"(?:ago|geleden)|Today|Yesterday|just now|vandaag|gisteren",
    re.IGNORECASE,
)
_RE_RESULTS_COUNT = re.compile(
    r"(?:of|from)\s+([\d,]+)\s+(?:results|jobs|vacancies)",
    re.IGNORECASE,
)

# Textos de widgets genericos de salario ("desbloquea la informacion salarial",
# "lo que podrias ganar", ...). Nunca son el salario de la oferta: si aparecen,
# se descarta el match para no contaminar el dato.
_WIDGET_SALARY_HINTS = re.compile(
    r"salarisinformatie|ontgrendel|wat je zou kunnen verdienen|"
    r"could be earning|unlock (?:your |the )?salary|salary (?:insight|estimate)|"
    r"inicia sesi[oó]n para ver|sign in to see",
    re.IGNORECASE,
)

# Modo de trabajo en tarjeta (multi-idioma: ingles, español, neerlandes).
_RE_WORK_REMOTE = re.compile(
    r"work\s+from\s+home|\bwfh\b|\bremote\b|remoto|remota|teletrabajo|"
    r"thuiswerk|telewerk|thuis\s+werken|flexibel\s+thuiswerken",
    re.IGNORECASE,
)
_RE_WORK_HYBRID = re.compile(
    r"\bhybrid\b|h[íi]brid[oa]|hibrid[eo]|hybride|semipresencial",
    re.IGNORECASE,
)

SEL_JOB_ITEM = '[data-testid="job-item"]'
SEL_JOB_TITLE_LINK = '[data-testid="job-item-title"]'
SEL_JOB_CARD = '[data-testid="job-card-content"]'
SEL_NEXT_PAGE = 'a[rel="next"], a[aria-label*="Next"], nav[aria-label="Pagination"] a[class*="next"]'

# Selectores estructurados de StepStone (data-at estables). Cuando existen se
# prefieren al parseo de texto: evitan mezclar empresa/ubicacion/descripcion.
SEL_ST_COMPANY = '[data-at="job-item-company-name"]'
SEL_ST_LOCATION = '[data-at="job-item-location"]'
SEL_ST_TIMEAGO = '[data-at="job-item-timeago"]'
SEL_ST_WFH = '[data-at="job-item-work-from-home"]'

_COOKIE_DISMISSED = False


def build_search_url(base_url: str, search_path: str, role: str,
                     page: int = 1, date_filter: str = "30",
                     location: str = "") -> str:
    role_slug = role.lower().replace(" ", "-")
    url = f"{base_url.rstrip('/')}{search_path.format(role_slug=role_slug)}"
    params = {}
    if date_filter:
        params["age"] = date_filter
    if location:
        city_slug_path = location.lower().replace(" ", "-")
        url = f"{url}/in-{city_slug_path}"
    if page > 1:
        params["page"] = str(page)
        params["action"] = "paging_next"
    if params:
        url += "?" + urlencode(params)
    return url


def _text(el) -> str:
    if not el:
        return ""
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        try:
            return (el.text_content() or "").strip()
        except Exception:
            return ""


def dismiss_cookie_banner(page) -> None:
    global _COOKIE_DISMISSED
    if _COOKIE_DISMISSED:
        return
    try:
        btns = page.query_selector_all("button")
        for b in btns:
            txt = (_text(b) or "").lower()
            if any(kw in txt for kw in ("accept all", "accept all cookies",
                                          "allow all", "agree", "aceptar",
                                          "i agree", "consent", "accept")):
                try:
                    b.click(timeout=3000)
                    time.sleep(1)
                    _COOKIE_DISMISSED = True
                    return
                except Exception:
                    pass
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        _COOKIE_DISMISSED = True
    except Exception:
        pass


def parse_cards(page, base_url: str = "") -> list[dict]:
    """Extrae todas las tarjetas visibles del SERP.

    `base_url` es el dominio de la fuente (p.ej. https://www.stepstone.nl).
    Se usa para construir las URLs de detalle: nunca se debe asumir el dominio
    de IrishJobs cuando se reutiliza el scraper para StepStone.
    """
    cards = []

    items = page.query_selector_all(SEL_JOB_ITEM)
    if not items:
        items = page.query_selector_all(SEL_JOB_CARD)

    if not items:
        items = page.query_selector_all("a[data-testid='job-item-title']")
        if items:
            items = [link.evaluate("el => el.closest('[data-testid]')") for link in items]
            items = [i for i in items if i]

    if not items:
        items = page.query_selector_all("[data-testid='job-card']")

    log.info("Tarjetas encontradas: %d", len(items))

    seen_ids = set()
    for item in items:
        try:
            card = _parse_card(item, base_url)
            if card and card["job_id"] not in seen_ids:
                seen_ids.add(card["job_id"])
                cards.append(card)
        except Exception as e:
            log.debug("Error parseando tarjeta: %s", e)

    log.info("Tarjetas validas: %d", len(cards))
    return cards


def _sel_text(item, selector: str) -> str:
    """Texto de un subelemento por selector ("" si no existe)."""
    try:
        el = item.query_selector(selector)
    except Exception:
        el = None
    return _text(el) if el else ""


def _parse_card(item, base_url: str = "") -> Optional[dict]:
    title_link = item.query_selector(SEL_JOB_TITLE_LINK) or \
                 item.query_selector("a[href*='/job/']") or \
                 item.query_selector("a[href*='banen--']")
    if not title_link:
        return None

    href = title_link.get_attribute("href") or ""
    title = _text(title_link)

    if not title:
        return None

    # Dominio correcto de la fuente (StepStone NL != IrishJobs).
    job_url = urljoin(base_url or "https://www.irishjobs.ie", href)

    m = _RE_JOB_ID_FROM_URL.search(href)
    job_id = (m.group(1) or m.group(2)) if m else ""

    card_text = _text(item) if hasattr(item, "inner_text") else ""

    # 1) Campos estructurados (StepStone: data-at). 2) Fallback a texto.
    company = _sel_text(item, SEL_ST_COMPANY)
    location = _sel_text(item, SEL_ST_LOCATION)
    posted_relative = _sel_text(item, SEL_ST_TIMEAGO)
    posted_datetime = ""
    try:
        time_el = item.query_selector(f"{SEL_ST_TIMEAGO} time")
        if time_el:
            posted_datetime = time_el.get_attribute("datetime") or ""
    except Exception:
        posted_datetime = ""

    if not (company or location or posted_relative):
        company, location, posted_relative = _parse_text_fields(card_text, title)
    else:
        # Desambigua: el elemento de empresa en StepStone tambien puede
        # contener la ubicacion; se limpia lo que coincida con location.
        if company and location and location in company:
            company = company.replace(location, "").strip(" ,-")

    salary_raw, smin, smax, cur, period, disclosed = _parse_salary(card_text)

    # Modo de trabajo: icono estructurado de StepStone o texto multi-idioma.
    work_mode = ""
    try:
        has_wfh = item.query_selector(SEL_ST_WFH) is not None
    except Exception:
        has_wfh = False
    if _RE_WORK_HYBRID.search(card_text):
        work_mode = "Hybrid"
    elif _RE_WORK_REMOTE.search(card_text) or has_wfh:
        work_mode = "Remote"

    emp_type = ""
    if re.search(r"permanent|onbepaalde tijd|vast contract", card_text, re.I):
        emp_type = "Permanent"
    elif re.search(r"contract|bepaalde tijd|tijdelijk", card_text, re.I):
        emp_type = "Contract"
    elif re.search(r"temporary|uitzend", card_text, re.I):
        emp_type = "Temporary"

    return {
        "job_id": job_id,
        "job_url": job_url,
        "title": title,
        "company_name": company,
        "location_raw": location,
        "posted_datetime": posted_datetime,
        "posted_relative": posted_relative,
        "salary_raw": salary_raw,
        "salary_min": smin,
        "salary_max": smax,
        "salary_currency": cur,
        "salary_period": period,
        "salary_disclosed": disclosed,
        "work_mode": work_mode,
        "employment_type": emp_type,
        "description_snippet": card_text[:300] if card_text else "",
    }


def _parse_salary(text: str) -> tuple[str, Optional[float], Optional[float], str, str, bool]:
    if not text:
        return "", None, None, "EUR", "", False
    text = text.strip()
    if "not disclosed" in text.lower() or "niet bekend" in text.lower():
        return "", None, None, "EUR", "", False
    # Descarta salarios de widgets genericos (no son de la oferta).
    if _WIDGET_SALARY_HINTS.search(text):
        return "", None, None, "EUR", "", False
    m = _RE_SALARY.search(text)
    if not m:
        return "", None, None, "EUR", "", False

    raw = m.group(0).strip()

    def _to_float(s):
        if not s:
            return None
        # Formato europeo "3.500" / "3.500,00" vs "3,500.00".
        s = s.strip()
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", "")
        else:
            s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None

    smin = _to_float(m.group(1))
    smax = _to_float(m.group(2))
    if smin is None:
        return "", None, None, "EUR", "", False

    period = (m.group(3) or m.group(4) or "").lower()
    period = {
        "annum": "year", "annual": "year", "year": "year", "jaar": "year",
        "j": "year",
        "month": "month", "maand": "month", "m": "month",
        "week": "week",
        "day": "day", "dag": "day",
        "hour": "hour", "uur": "hour", "u": "hour",
    }.get(period, "")

    cur = "EUR"
    if re.search(r"\bCHF\b|SFr", text):
        cur = "CHF"
    elif re.search(r"£|\bGBP\b", text):
        cur = "GBP"
    elif re.search(r"\$|\bUSD\b", text):
        cur = "USD"

    return raw, smin, smax, cur, period, True


def _parse_text_fields(card_text: str, title: str) -> tuple[str, str, str]:
    """Extrae empresa, ubicacion y fecha del texto de la tarjeta."""
    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
    company = ""
    location = ""
    posted_relative = ""

    for line in lines:
        if not line or line == title:
            continue
        if not company and _looks_like_company(line, title):
            company = line
        elif not location and _looks_like_location(line):
            location = line
        elif not posted_relative and _RE_DATE_SHORT.search(line):
            posted_relative = line
        elif not location and "County" in line:
            location = line
        elif not posted_relative and "ago" in line.lower():
            posted_relative = line

    if not company:
        non_title = [l for l in lines if l != title and l]
        if non_title and "€" not in non_title[0] and "per annum" not in non_title[0]:
            company = non_title[0]

    return company, location, posted_relative


def _looks_like_company(text: str, title: str) -> bool:
    if text == title:
        return False
    if len(text) < 2 or len(text) > 80:
        return False
    if "€" in text:
        return False
    if _RE_DATE_SHORT.search(text):
        return False
    if re.match(r"^\d+", text):
        return False
    return True


def _looks_like_location(text: str) -> bool:
    if not text:
        return False
    if "," not in text:
        return False
    if "€" in text:
        return False
    return True


def parse_location(raw: str) -> tuple[str, str, str]:
    if not raw:
        return "", "", ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], "", ""


def get_total_results(page) -> int:
    """Intenta leer el total de resultados del DOM (ej: 'Showing 1-25 of 342 results')."""
    try:
        body_text = _text(page.query_selector("body")) or ""
        m = _RE_RESULTS_COUNT.search(body_text)
        if m:
            return int(m.group(1).replace(",", ""))
    except Exception:
        pass
    try:
        count_el = page.query_selector("[data-testid*='result-count'], [data-testid*='total'], "
                                        "[class*='result-count'], [class*='total-count']")
        if count_el:
            txt = _text(count_el)
            digits = re.findall(r"[\d,]+", txt)
            if digits:
                return int(digits[-1].replace(",", ""))
    except Exception:
        pass
    return 0


def has_next_page(page) -> bool:
    try:
        link = page.query_selector("link[rel='next']")
        if link:
            return bool(link.get_attribute("href"))
    except Exception:
        pass
    try:
        btn = page.query_selector("a[rel='next']")
        if btn:
            return True
    except Exception:
        pass
    try:
        btn = page.query_selector("[aria-label*='Next page']")
        if btn:
            return True
    except Exception:
        pass
    try:
        btns = page.query_selector_all("[data-testid='pagination-next'], a[aria-label*='Next']")
        if btns:
            return True
    except Exception:
        pass
    return False


def cards_still_different(page, previous_ids: set, jobs_per_search: int) -> bool:
    """Detecta si la pagina actual tiene tarjetas diferentes a la anterior.
    Si todas las tarjetas ya estaban en previous_ids, la paginacion real termino.
    Se usa como fallback cuando has_next_page da False pero podria haber mas paginas."""
    if not previous_ids:
        return True
    cards = parse_cards(page)
    current_ids = {c["job_id"] for c in cards if c.get("job_id")}
    if not current_ids:
        return False
    new_count = len(current_ids - previous_ids)
    return new_count > 0


def go_to_next_page(page) -> bool:
    try:
        link = page.query_selector("link[rel='next']")
        if link:
            href = link.get_attribute("href")
            if href:
                full_url = href if href.startswith("http") else urljoin(page.url, href)
                page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                return True
    except Exception:
        pass

    try:
        btn = page.query_selector("a[rel='next'], a[aria-label*='Next'], "
                                  "[aria-label*='Next page'], [data-testid='pagination-next']")
        if btn:
            btn.click(timeout=10000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                time.sleep(2)
            return True
    except Exception:
        pass

    try:
        current = page.query_selector("[class*='active'][class*='page'], "
                                       "[aria-current='page']")
        if current:
            next_btn = current.evaluate("el => el.nextElementSibling")
            if next_btn:
                try:
                    nxt = page.query_selector(f"[class*='{next_btn.get('class','')}']")
                    if nxt:
                        nxt.click(timeout=5000)
                        return True
                except Exception:
                    pass
    except Exception:
        pass

    try:
        current_url = page.url
        if "page=" in current_url:
            current_page = int(re.search(r"page=(\d+)", current_url).group(1))
            next_url = re.sub(r"page=\d+", f"page={current_page + 1}", current_url)
        else:
            sep = "&" if "?" in current_url else "?"
            next_url = f"{current_url}{sep}page=2&action=paging_next"
        page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
        return True
    except Exception:
        pass

    return False
