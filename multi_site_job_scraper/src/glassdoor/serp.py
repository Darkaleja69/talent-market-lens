"""Parseo del SERP de Glassdoor (fallback DOM).

La fuente principal de ofertas es el BFF GraphQL (ver bff.py). Este modulo
es el fallback para cuando el BFF no devuelve datos, y solo parses enlaces
que apuntan a ofertas reales (/job-listing/ y /partner/jobListing). No se
escanean div/li/section arbitrarios para evitar ruido de footer y navegacion.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse, parse_qs

log = logging.getLogger(__name__)

_RE_LISTING_ID = re.compile(r"(?:jl|jobListingId|listingId)=(\d+)")
_RE_DATE = re.compile(
    r"(\d+)\s+(?:d|day|hour|h|week|month)\w*\s+ago|Today|Yesterday|"
    r"Just posted|New|Hace\s+\d+|Anuncio\s+\d+|Publicado\s+hace\s+\d+",
    re.IGNORECASE,
)
_RE_SALARY = re.compile(
    r"[\$€]\s?[\d,.]+[KMB]?\s*(?:[–\-]\s*\$?€?[\d,.]+[KMB]?)?\s*"
    r"(?:per\s+(?:year|month|hour|annum|año|mes|hora))?"
    r"|\d+\.?\d*\s*€",
    re.IGNORECASE,
)

SEL_JOB_LINK = (
    'a[href*="/job-listing/"], a[href*="/partner/jobListing"], '
    'a[href*="listingId="], a[href*="?jl="]'
)
SEL_NEXT_LINK = 'link[rel="next"]'
SEL_NEXT_BTN = [
    'button[data-test="pagination-next"]',
    'button[aria-label="Next"]',
    'a[aria-label="Next"]',
    'button[data-test="page-next"]',
    'button[aria-label*="Siguiente"]',
    'a[aria-label*="Siguiente"]',
]


def build_search_url(base_url: str, search_path: str, role: str,
                     page: int = 1, date_filter: str = "") -> str:
    """Construye la URL del SERP para mantener la sesion/navegacion.

    Nota: los datos reales se obtienen del BFF; esta URL se usa solo para
    calentar la sesion y como referencia para el BFF.
    """
    url = base_url.rstrip("/") + search_path
    sep = "&" if "?" in url else "?"
    params = [f"sc.keyword={role.replace(' ', '+')}"]
    if date_filter:
        params.append(f"fromAge={date_filter}")
    if page > 1:
        params.append(f"p={page}")
    return url + sep + "&".join(params)


def _is_job_href(href: str) -> bool:
    """True si el enlace apunta a una oferta individual."""
    h = href.lower()
    if "/job-listing/" in h or "/partner/joblisting" in h:
        return True
    if "listingid=" in h or "jl=" in h:
        return True
    return False


def _listing_id_from_href(href: str) -> str:
    m = _RE_LISTING_ID.search(href)
    return m.group(1) if m else ""


def dismiss_overlays(page) -> None:
    """Cierra banners de cookies, modales de registro y otros overlays."""
    cookie_keywords = [
        "accept all", "accept all cookies", "allow all", "agree",
        "aceptar", "i agree", "consent", "accept", "ok", "got it",
        "aceptar todas", "aceptar todo",
    ]
    try:
        btns = page.query_selector_all("button")
        for b in btns:
            try:
                txt = (b.inner_text() or "").lower()
            except Exception:
                txt = ""
            if any(kw in txt for kw in cookie_keywords):
                try:
                    b.click(timeout=3000)
                    time.sleep(1)
                    return
                except Exception:
                    pass
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
    except Exception:
        pass


def parse_cards(page) -> list[dict]:
    """Parsea tarjetas de oferta desde el DOM renderizado.

    Solo se consideran enlaces a ofertas individuales; el titulo se toma del
    enlace y el resto de campos de su contenedor mas cercano.
    """
    cards = []
    try:
        page.wait_for_selector(SEL_JOB_LINK, timeout=15000)
    except Exception:
        log.warning("Timeout esperando enlaces de oferta en SERP de Glassdoor.")
        return cards

    links = page.query_selector_all(SEL_JOB_LINK)
    log.info("Enlaces de oferta encontrados: %d", len(links))

    seen = set()
    for link in links:
        try:
            href = link.get_attribute("href") or ""
            if not _is_job_href(href):
                continue
            title = (link.inner_text() or "").strip()
            if len(title) < 3:
                continue

            listing_id = _listing_id_from_href(href)
            if listing_id:
                key = listing_id
            else:
                key = href.split("?")[0]
            if key in seen:
                continue
            seen.add(key)

            job_url = href if href.startswith("http") else urljoin(
                "https://www.glassdoor.com", href)
            job_id = f"gd_{listing_id}" if listing_id else f"gd_{hash(job_url + title)}"

            card_el = _closest_card(link)
            card_text = ""
            if card_el is not None:
                try:
                    card_text = card_el.inner_text() or ""
                except Exception:
                    card_text = ""
            if not card_text:
                try:
                    parent_text = link.evaluate(
                        "el => el.closest('li, article, [data-test], "
                        "[class*=\"JobCard\"], [class*=\"jobListing\"]') "
                        "? el.closest('li, article, [data-test], "
                        "[class*=\"JobCard\"], [class*=\"jobListing\"]').innerText : ''")
                    card_text = parent_text or ""
                except Exception:
                    card_text = ""

            company = _extract_company(card_text, title)
            location = _extract_location(card_text)
            salary = _extract_salary(card_text)
            posted_relative = _extract_date(card_text)
            work_mode = _extract_work_mode(card_text)
            emp_type = _extract_emp_type(card_text)

            cards.append({
                "job_id": job_id,
                "job_url": job_url,
                "title": title,
                "company_name": company,
                "company_url": "",
                "location_raw": location,
                "posted_relative": posted_relative,
                "posted_datetime": None,
                "is_new": bool(re.search(r"just posted|new|today|hace \d+ hora",
                                         posted_relative or "", re.IGNORECASE)),
                "salary_raw": salary,
                "salary_min": None,
                "salary_max": None,
                "salary_currency": "",
                "salary_period": "",
                "work_mode": work_mode,
                "employment_type": emp_type,
                "description_snippet": card_text[:300] if card_text else "",
                "description_full": "",
                "skills": [],
            })
        except Exception as e:
            log.debug("Error parseando enlace de oferta: %s", e)

    log.info("Tarjetas validas (DOM): %d", len(cards))
    return cards


def _closest_card(link):
    """Devuelve el contenedor de tarjeta mas cercano al enlace."""
    try:
        return link.evaluate_handle(
            "el => el.closest('li[data-test=\"jobListing\"], "
            "li.react-job-listing, li[data-id], article, "
            "[data-test=\"jobListing\"], [data-testid=\"jobListing\"], "
            "[class*=\"JobCard\"], [class*=\"jobCard\"], "
            "[class*=\"jobListing\"], [class*=\"JobListing\"]')"
        ).as_element()
    except Exception:
        return None


def _lines(card_text: str) -> list[str]:
    return [l.strip() for l in (card_text or "").split("\n") if l.strip()]


def _extract_company(card_text: str, title: str) -> str:
    """Extrae la empresa evitando confundirla con el rating (4,1 / 4.1)."""
    if not card_text:
        return ""
    lines = _lines(card_text)

    title_idx = -1
    for i, line in enumerate(lines):
        if line == title:
            title_idx = i
            break

    candidates = []
    if title_idx >= 0:
        candidates = lines[title_idx + 1:title_idx + 4]
    if not candidates:
        candidates = lines[:6]

    for cand in candidates:
        if not cand or cand == title:
            continue
        if _is_rating(cand) or _RE_DATE.search(cand) or _RE_SALARY.search(cand):
            continue
        if _looks_like_location(cand):
            continue
        if len(cand) > 100:
            continue
        if "glassdoor" in cand.lower() or "easy apply" in cand.lower():
            continue
        return cand
    return ""


def _is_rating(text: str) -> bool:
    """Detecta ratings tipo '4,1', '4.1', '3,0'."""
    return bool(re.match(r"^\d[,.]\d$", text.strip()))


def _extract_location(card_text: str) -> str:
    """Extrae la ubicacion de la tarjeta (sin caer en el titulo)."""
    if not card_text:
        return ""
    lines = _lines(card_text)
    for line in lines:
        if _is_rating(line):
            continue
        if _RE_SALARY.search(line) or _RE_DATE.search(line):
            continue
        if _looks_like_location(line):
            return line
    return ""


def _extract_salary(card_text: str) -> str:
    if not card_text:
        return ""
    m = _RE_SALARY.search(card_text)
    return m.group(0).strip() if m else ""


def _extract_date(card_text: str) -> str:
    if not card_text:
        return ""
    m = _RE_DATE.search(card_text)
    return m.group(0).strip() if m else ""


def _extract_work_mode(card_text: str) -> str:
    if not card_text:
        return ""
    low = card_text.lower()
    if re.search(r"\bremote\b", low):
        return "Remote"
    if re.search(r"\bhybrid\b", low):
        return "Hybrid"
    if re.search(r"\bon[- ]?site\b", low):
        return "On-site"
    return ""


def _extract_emp_type(card_text: str) -> str:
    if not card_text:
        return ""
    low = card_text.lower()
    if re.search(r"\bfull[- ]?time\b", low):
        return "Full-time"
    if re.search(r"\bpart[- ]?time\b", low):
        return "Part-time"
    if re.search(r"\bcontract\b", low):
        return "Contract"
    if re.search(r"\bintern(ship)?\b", low):
        return "Internship"
    return ""


def _looks_like_location(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if t.lower() in ("remote", "united states", "us", "usa", "spain",
                     "españa", "switzerland", "germany", "france"):
        return True
    if "," in t:
        parts = [p.strip() for p in t.split(",") if p.strip()]
        if len(parts) >= 2 and all(len(p) < 30 for p in parts):
            return True
    return False


def has_next_page(page) -> bool:
    """Detecta pagina siguiente SOLO con indicadores reales de paginacion.

    Ya no se consideran botones genericos 'show more'/'load more' del footer,
    que generaban falsos positivos.
    """
    try:
        link = page.query_selector(SEL_NEXT_LINK)
        if link and link.get_attribute("href"):
            return True
    except Exception:
        pass

    for sel in SEL_NEXT_BTN:
        try:
            btn = page.query_selector(sel)
            if btn:
                disabled = btn.get_attribute("disabled")
                aria_disabled = btn.get_attribute("aria-disabled")
                if (disabled is None or disabled.lower() != "true") and \
                   (aria_disabled is None or aria_disabled.lower() != "true"):
                    return True
        except Exception:
            continue
    return False


def go_to_next_page(page) -> bool:
    """Navega a la pagina siguiente del SERP (link rel=next o boton real)."""
    try:
        link = page.query_selector(SEL_NEXT_LINK)
        if link:
            href = link.get_attribute("href")
            if href:
                full_url = href if href.startswith("http") else urljoin(page.url, href)
                page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                return True
    except Exception:
        pass

    for sel in SEL_NEXT_BTN:
        try:
            btn = page.query_selector(sel)
            if btn:
                disabled = btn.get_attribute("disabled")
                aria_disabled = btn.get_attribute("aria-disabled")
                if (disabled is None or disabled.lower() != "true") and \
                   (aria_disabled is None or aria_disabled.lower() != "true"):
                    btn.scroll_into_view_if_needed()
                    time.sleep(0.5)
                    btn.click(timeout=10000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        time.sleep(2)
                    return True
        except Exception:
            continue

    # Fallback conservador: solo si la URL actual es un SERP con p=N
    try:
        current_url = page.url
        parsed = urlparse(current_url)
        path = parsed.path.lower()
        if not any(p in path for p in ("/job/", "/empleo/", "/job-listing/")):
            return False
        qs = parse_qs(parsed.query)
        if "p" in qs and qs["p"][0].isdigit():
            from urllib.parse import urlencode, urlsplit, urlunsplit
            parts = urlsplit(current_url)
            q = parse_qs(parts.query)
            q["p"] = [str(int(q["p"][0]) + 1)]
            next_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                                   urlencode(q, doseq=True), parts.fragment))
            page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            return True
    except Exception:
        pass
    return False


def parse_location(raw: str) -> tuple[str, str, str]:
    """Parsea ubicacion raw en ciudad, region, pais."""
    if not raw:
        return "", "", ""
    if raw.lower() in ("remote", "united states", "us", "usa"):
        return raw, "", "United States"
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], parts[1], "United States"
    return parts[0], "", "United States"