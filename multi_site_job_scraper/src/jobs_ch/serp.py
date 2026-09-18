"""Parseo del SERP de jobs.ch (React SSR + JSON-LD).

Estrategia (orden de preferencia):
  1. JSON-LD (ItemList + JobPosting) -> datos estructurados con UUID, fecha
     ISO exacta, empresa, tipo de contrato, ubicacion, URL y descripcion SEO.
  2. DOM SSR con selectores data-cy estables (fallback).
  3. Parseo de texto de tarjeta (ultimo recurso).

Verificado contra HTML real (2026-08):
  - Tarjeta: a[data-cy="job-link"] con contenedor [data-cy="vacancy-serp-item"]
  - Paginacion: link[rel="next"] y a[data-cy="paginator-next"]
  - El parametro "place=" NO filtra en el SERP actual (misma lista nacional),
    por lo que la cobertura se hace con busqueda nacional + atribucion de
    ciudad por ubicacion.
"""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from src.core.normalize import as_text

log = logging.getLogger(__name__)

_RE_JOB_ID_FROM_URL = re.compile(r"/detail/([^/?]+)")
_RE_ISO = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?"
)
_RE_DATE = re.compile(
    r"(\d+)\s+(?:hour|day|week|month|year)s?\s+ago|"
    r"Last\s+(?:hour|day|week|month|year)|"
    r"Today|Yesterday|just now|"
    r"vor\s+\d+|il y a|"
    r"(\d{1,3})\s*(?:–|-|to)\s*\d{1,3}%|\d{1,3}%",
    re.IGNORECASE,
)
_RE_WORKLOAD = re.compile(r"(\d{1,3}\s*(?:–|-|to)\s*\d{1,3}%|\d{1,3}%)")
_RE_DAYS_AGO = re.compile(
    r"(?:(?P<n>\d+)\s+(?P<u>hour|day|week|month|year)s?\s+ago|"
    r"Last\s+(?P<lu>hour|day|week|month|year)|"
    r"(?P<today>Today)|(?P<yest>Yesterday)|(?P<now>just now))",
    re.IGNORECASE,
)
_RE_MULTILINGUAL = re.compile(
    r"vor\s+(\d+)\s+(?:Stunden?|Tag(?:en?)?|Wochen?|Monat(?:en?)?|Jahr(?:en?)?)|"
    r"il y a\s+(\d+)\s+(?:heure|jour|semaine|mois|an)s?",
    re.IGNORECASE,
)

_COUNTRY_NAMES = {
    "CH": "Switzerland", "DE": "Germany", "FR": "France", "IT": "Italy",
    "AT": "Austria", "LI": "Liechtenstein", "GB": "United Kingdom",
    "US": "United States", "ES": "Spain",
}


def _country_name(code: str) -> str:
    if not code:
        return ""
    c = str(code).upper()
    if c in _COUNTRY_NAMES:
        return _COUNTRY_NAMES[c]
    return str(code)


SEL_CARD = 'a[data-cy="job-link"], a[href*="/detail/"]'
SEL_CARD_ITEM = '[data-cy="vacancy-serp-item"]'
SEL_NEXT_PAGE_LINK = 'link[rel="next"]'
SEL_NEXT_PAGE_BTN = (
    'a[data-cy="paginator-next"], a[rel="next"], '
    'a[aria-label*="next" i], button[aria-label*="next" i]'
)


def extract_json_ld(html_str: str) -> list:
    """Extrae todos los bloques application/ld+json de un HTML."""
    blocks = []
    if not html_str:
        return blocks
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html_str, re.DOTALL | re.IGNORECASE,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            blocks.extend(data)
        else:
            blocks.append(data)
    return blocks


def _by_type(blocks: list, type_name: str) -> list:
    return [b for b in blocks if (b or {}).get("@type") == type_name]


def build_search_url(base_url: str, search_path: str, role: str,
                     page: int = 1) -> str:
    """Construye la URL de busqueda nacional (term). Sin filtro de ciudad.

    El SERP actual ignora el parametro "place" para filtrar (2026-08), asi que
    la busqueda es nacional; la ciudad se atribuye por la ubicacion de cada
    oferta.
    """
    url = f"{base_url.rstrip('/')}{search_path}"
    params = {"term": role}
    if page > 1:
        params["page"] = str(page)
    return url + "?" + urlencode(params)


def _relative_from_iso(iso: str) -> str:
    """Convierte una fecha ISO a texto relativo ('X days ago')."""
    if not iso:
        return ""
    iso = iso.strip().replace("Z", "+00:00")
    if " " in iso and "T" not in iso:
        iso = iso.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        m = _RE_ISO.search(iso)
        if not m:
            return ""
        try:
            dt = datetime.fromisoformat(m.group(0).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    secs = max(int((now - dt).total_seconds()), 0)
    if secs < 3600:
        return f"{max(secs // 60, 1)} minutes ago"
    if secs < 86400:
        return f"{secs // 3600} hours ago"
    if secs < 7 * 86400:
        return f"{secs // 86400} days ago"
    if secs < 30 * 86400:
        return f"{secs // (7 * 86400)} weeks ago"
    if secs < 365 * 86400:
        return f"{secs // (30 * 86400)} months ago"
    return f"{secs // (365 * 86400)} years ago"


def _is_recent_iso(iso: str, max_days: int) -> bool:
    if not iso:
        return True
    rel = _relative_from_iso(iso)
    if not rel:
        return True
    days = _days_ago(rel)
    if days is None:
        return True
    return days <= max_days


def parse_cards_from_ld(html_str: str) -> list[dict]:
    """Parsea ofertas desde el JSON-LD ItemList del SERP."""
    cards = []
    blocks = extract_json_ld(html_str)
    item_lists = _by_type(blocks, "ItemList")
    if not item_lists:
        return cards

    seen = set()
    for il in item_lists:
        for elem in il.get("itemListElement") or []:
            item = elem.get("item") or {}
            if item.get("@type") not in ("JobPosting",):
                continue
            card = _card_from_jobposting_ld(item)
            if card and card["job_id"] not in seen:
                seen.add(card["job_id"])
                cards.append(card)
    if cards:
        log.info("JSON-LD: %d ofertas extraidas", len(cards))
    return cards


def _card_from_jobposting_ld(jp: dict) -> Optional[dict]:
    identifier = jp.get("identifier") or {}
    job_id = identifier.get("value", "") if isinstance(identifier, dict) else ""
    if not job_id:
        m = _RE_JOB_ID_FROM_URL.search(jp.get("url", ""))
        job_id = m.group(1) if m else ""
    if not job_id:
        return None

    url = jp.get("url", "")
    if url.startswith("/"):
        url = "https://www.jobs.ch" + url

    org = jp.get("hiringOrganization") or {}
    company_name = org.get("name", "") if isinstance(org, dict) else ""
    company_url = org.get("sameAs", "") if isinstance(org, dict) else ""

    addr = ((jp.get("jobLocation") or {}).get("address") or {}) if isinstance(
        jp.get("jobLocation"), dict) else {}
    if isinstance(addr, dict):
        city = addr.get("addressLocality", "")
        region = addr.get("addressRegion", "")
        postal = addr.get("postalCode", "")
        street = addr.get("streetAddress", "")
        country = addr.get("addressCountry", "")
        loc_parts = [p for p in (street, city, region, postal) if p]
        location_raw = ", ".join(loc_parts) if loc_parts else (city or "")
        if isinstance(country, dict):
            country = country.get("name", "")
    else:
        city = region = postal = street = country = ""
        location_raw = ""

    posted_iso = jp.get("datePosted", "")
    posted_rel = _relative_from_iso(posted_iso) or ""
    is_new = _is_recent_iso(posted_iso, 1)

    title = jp.get("title", "")
    workload = ""
    mw = _RE_WORKLOAD.search(title)
    if mw:
        workload = mw.group(1)

    employment_type = as_text(jp.get("employmentType", ""))

    return {
        "job_id": job_id,
        "job_url": url,
        "title": title,
        "company_name": company_name,
        "company_url": company_url,
        "location_raw": location_raw,
        "location_city": city,
        "location_region": region,
        "location_country": _country_name(country) or "Switzerland",
        "posted_datetime": posted_iso,
        "posted_relative": posted_rel,
        "is_new": is_new,
        "employment_type": employment_type,
        "workload_pct": workload,
        "description_snippet": (jp.get("description") or "").strip(),
        "work_mode": "",
    }


def parse_cards(page) -> list[dict]:
    """Extrae tarjetas: JSON-LD primero, DOM SSR como fallback.

    Si el JSON-LD carece de algunos campos (ubicacion, workload), se rellenan
    desde las tarjetas del DOM cuando hay coincidencia por UUID.
    """
    try:
        html_str = page.content()
    except Exception:
        html_str = ""

    cards = parse_cards_from_ld(html_str)
    if not cards:
        cards = _parse_dom_cards(page)
        return cards

    dom_cards = _parse_dom_cards(page)
    if dom_cards:
        by_id = {c["job_id"]: c for c in dom_cards}
        for c in cards:
            dom = by_id.get(c["job_id"])
            if not dom:
                continue
            if not c.get("location_raw") and dom.get("location_raw"):
                c["location_raw"] = dom["location_raw"]
                c["location_city"] = dom.get("location_city", "")
            if not c.get("workload_pct") and dom.get("workload_pct"):
                c["workload_pct"] = dom["workload_pct"]
            if not c.get("posted_relative") and dom.get("posted_relative"):
                c["posted_relative"] = dom["posted_relative"]
                c["is_new"] = dom.get("is_new", False)
    return cards


def _parse_dom_cards(page) -> list[dict]:
    """Parsea tarjetas desde el DOM SSR con selectores data-cy estables."""
    cards = []
    try:
        page.wait_for_selector(SEL_CARD, timeout=30000)
    except Exception:
        log.warning("Timeout esperando tarjetas en SERP de jobs.ch.")
        return cards

    links = page.query_selector_all(SEL_CARD)
    log.info("Tarjetas encontradas (DOM): %d", len(links))
    seen_ids = set()

    for link in links:
        try:
            href = link.get_attribute("href") or ""
            m = _RE_JOB_ID_FROM_URL.search(href)
            job_id = m.group(1) if m else href
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            job_url = f"https://www.jobs.ch{href}" if href.startswith("/") else href
            card_text = link.inner_text()
            title, company, location, workload, contract_type, posted_rel = \
                _parse_card_text(card_text)
            if not title:
                continue

            cards.append({
                "job_id": job_id,
                "job_url": job_url,
                "title": title,
                "company_name": company,
                "company_url": "",
                "location_raw": location,
                "location_city": location.split(",")[0].strip() if location else "",
                "location_region": "",
                "location_country": "Switzerland",
                "posted_datetime": "",
                "posted_relative": posted_rel,
                "is_new": bool(re.search(r"just now|today|new", posted_rel or "",
                                         re.IGNORECASE)),
                "employment_type": contract_type,
                "workload_pct": workload,
                "description_snippet": "",
                "work_mode": "",
            })
        except Exception as e:
            log.debug("Error parseando tarjeta jobs.ch: %s", e)

    log.info("Tarjetas validas (DOM): %d", len(cards))
    return cards


def _parse_card_text(text: str) -> tuple[str, str, str, str, str, str]:
    """Parsea el texto estructurado de una tarjeta jobs.ch.

    El HTML actual separa el label, los ':' y el valor en lineas
    independientes:
        Place of work
        :
        Bern
        Workload
        :
        100%
        Contract type
        :
        Permanent position
        Empresa
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return "", "", "", "", "", ""

    posted_relative = ""
    title = ""
    company = ""
    location = ""
    workload = ""
    contract_type = ""

    i = 0
    if lines and _RE_DATE.search(lines[0]):
        posted_relative = lines[0]
        i = 1

    if i < len(lines) and not _is_label(lines[i]):
        title = lines[i]
        i += 1

    def _label_value(line: str, idx: int) -> tuple[str, int]:
        """Extrae el valor de un label.

        Soportado:
          - 'Place of work: Geneva' (label y valor en la misma linea)
          - 'Place of work' / ':' / 'Geneva' (lineas independientes)
        """
        if ":" in line:
            val = line.split(":", 1)[1].strip()
            if val and not _is_label(val):
                return val, idx + 1
        j = idx + 1
        while j < len(lines) and (lines[j] == ":" or _is_label(lines[j])):
            j += 1
        if j < len(lines) and lines[j] != ":" and not _is_label(lines[j]):
            return lines[j], j + 1
        return "", idx + 1

    while i < len(lines):
        line = lines[i]
        lower = line.lower()
        if any(k in lower for k in ("place of work", "arbeitsort",
                                    "lieu de travail")):
            value, i = _label_value(line, i)
            if value and not location:
                location = value
        elif any(k in lower for k in ("workload", "pensum", "taux d")):
            value, i = _label_value(line, i)
            if value and not workload:
                workload = value
        elif any(k in lower for k in ("contract type", "vertragsart",
                                      "type de contrat")):
            value, i = _label_value(line, i)
            if value and not contract_type:
                contract_type = value
        elif any(k in lower for k in ("published", "publie",
                                      "veroffentlicht")):
            _, i = _label_value(line, i)
        elif _RE_DATE.search(line) and not posted_relative:
            posted_relative = line
            i += 1
        elif not _is_label(line) and line != ":" and not company and line != title:
            company = line
            i += 1
        else:
            i += 1

    if not company:
        for line in reversed(lines):
            if (not _is_label(line) and line != ":" and line != title
                    and line != posted_relative and not _RE_DATE.search(line)):
                company = line
                break

    return title, company, location, workload, contract_type, posted_relative


def _is_label(text: str) -> bool:
    """Detecta si una linea es un label (Place of work, Workload, etc)."""
    labels = [
        "place of work", "workload", "contract type", "published",
        "arbeitsort", "pensum", "vertragsart", "veroffentlicht",
        "lieu de travail", "taux d", "type de contrat", "publie",
        "easy apply", "promoted", "new", "save", "apply",
    ]
    t = text.lower()
    return any(l in t for l in labels)


def has_next_page(page) -> bool:
    try:
        link = page.query_selector(SEL_NEXT_PAGE_LINK)
        if link and link.get_attribute("href"):
            return True
    except Exception:
        pass
    try:
        btn = page.query_selector(SEL_NEXT_PAGE_BTN)
        if btn:
            return True
    except Exception:
        pass
    return False


def go_to_next_page(page) -> bool:
    try:
        link = page.query_selector(SEL_NEXT_PAGE_LINK)
        if link:
            href = link.get_attribute("href")
            if href:
                url = f"https://www.jobs.ch{href}" if href.startswith("/") else href
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return True
    except Exception:
        pass
    try:
        btn = page.query_selector(SEL_NEXT_PAGE_BTN)
        if btn:
            btn.click(timeout=10000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            return True
    except Exception:
        pass

    # Fallback: construir ?page=N+1 a partir de la URL actual
    try:
        from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
        parts = urlsplit(page.url)
        qs = parse_qs(parts.query)
        cur = int((qs.get("page") or ["1"])[0])
        qs["page"] = [str(cur + 1)]
        next_url = urlunsplit((parts.scheme, parts.netloc, parts.path,
                               urlencode(qs, doseq=True), parts.fragment))
        page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
        return True
    except Exception:
        pass
    return False


def _days_ago(text: str) -> int | None:
    """Parsea posted_relative a dias aproximados desde publicacion.

    Soporta ingles, aleman y frances. Retorna None si no se puede parsear.
    """
    if not text:
        return None
    text = text.strip()
    lower = text.lower()

    if lower in ("today", "just now", "heure", "heures", "stunde", "stunden"):
        return 0
    if lower in ("yesterday", "gestern", "hier"):
        return 1

    m = _RE_DAYS_AGO.search(text)
    if m:
        if m.group("today") or m.group("now"):
            return 0
        if m.group("yest"):
            return 1
        num_str = m.group("n")
        unit = (m.group("u") or m.group("lu") or "").lower()
        if num_str:
            num = int(num_str)
            if unit.startswith("hour"):
                return 0
            elif unit.startswith("day"):
                return num
            elif unit.startswith("week"):
                return num * 7
            elif unit.startswith("month"):
                return num * 30
            elif unit.startswith("year"):
                return num * 365
            return None
        # "Last <unit>"
        if unit.startswith("hour"):
            return 0
        if unit.startswith("day"):
            return 1
        if unit.startswith("week"):
            return 7
        if unit.startswith("month"):
            return 30
        if unit.startswith("year"):
            return 365
        return None

    m = _RE_MULTILINGUAL.search(text)
    if m:
        if m.group(1):
            num = int(m.group(1))
            post = text[m.start():].lower()
            if "stunde" in post:
                return 0
            if "tag" in post:
                return num
            if "woche" in post:
                return num * 7
            if "monat" in post:
                return num * 30
            if "jahr" in post:
                return num * 365
        if m.group(2):
            num = int(m.group(2))
            post = text[m.start():].lower()
            if "heure" in post:
                return 0
            if "jour" in post:
                return num
            if "semaine" in post:
                return num * 7
            if "mois" in post:
                return num * 30
            if "an" in post:
                return num * 365

    if "last week" in lower:
        return 7
    if "last month" in lower:
        return 30

    return None
