"""Parseo de la pagina de resultados de busqueda (SERP) de LinkedIn Jobs.

Soporta DOS layouts distintos:

1. **Guest page** (no logueado): usa clases estables base-search-card,
   data-entity-urn, scroll infinito. Verificado en serp_madrid.html.

2. **Authenticated page** (logueado): usa clases job-card-container,
   data-job-id, paginacion numerada, clases obfuscadas. Verificado en
   auth_serp_madrid.html. Las clases del listado estan ofuscadas pero
   las semanticas (job-card-container, artdeco-entity-lockup) son estables.

El modulo autodetecta el layout probando selectores de cada uno.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from .models import JobOffer, now_utc_iso

log = logging.getLogger(__name__)

# --- Selectores GUEST (verificados en HTML real guest) ---
SEL_RESULTS_LIST_GUEST = "ul.jobs-search__results-list"
SEL_JOB_CARD_GUEST = "li > div.job-search-card[data-entity-urn]"
SEL_TITLE_GUEST = "h3.base-search-card__title"
SEL_TITLE_LINK_GUEST = "a.base-card__full-link"
SEL_COMPANY_GUEST = "h4.base-search-card__subtitle a.hidden-nested-link"
SEL_LOCATION_GUEST = "span.job-search-card__location"
SEL_DATE_NEW_GUEST = "time.job-search-card__listdate--new"
SEL_DATE_GUEST = "time.job-search-card__listdate"
SEL_RESULT_COUNT_GUEST = "span.results-context-header__job-count"
SEL_SHOW_MORE_GUEST = "button.infinite-scroller__show-more-button"
SEL_VIEWED_ALL_GUEST = "div.see-more-jobs__viewed-all"

# --- Selectores AUTH (verificados en HTML real autenticado) ---
# El <ul> del listado tiene clase OBFUSCADA; usar el sentinel estable.
SEL_LIST_SENTINEL_AUTH = "div[data-results-list-top-scroll-sentinel]"
SEL_JOB_CARD_AUTH = "div.job-card-container--clickable"
SEL_JOB_CARD_AUTH_ALT = "div.job-card-container[data-job-id]"
SEL_TITLE_AUTH = "div.artdeco-entity-lockup__title"
SEL_TITLE_LINK_AUTH = "a.job-card-container__link"
SEL_TITLE_TEXT_AUTH = "a.job-card-container__link span[aria-hidden='true'] strong"
SEL_COMPANY_AUTH = "div.artdeco-entity-lockup__subtitle span[dir='ltr']"
SEL_LOCATION_AUTH = "div.artdeco-entity-lockup__caption li span[dir='ltr']"
SEL_RESULT_COUNT_AUTH = "small.jobs-search-results-list__text span[dir='ltr']"
SEL_PAGINATION_NEXT = "button.jobs-search-pagination__button--next"
SEL_PAGINATION_STATE = "p.jobs-search-pagination__page-state"

# Regex
_RE_JOB_ID_FROM_URN = re.compile(r"urn:li:jobPosting:(\d+)")
_RE_JOB_ID_FROM_URL = re.compile(r"/jobs/view/(\d+)")
_RE_JOB_ID_FROM_URL_GUEST = re.compile(r"-(\d{6,})(?:\?|$)")
_RE_WORK_MODE = re.compile(
    r"\((H[ií]brido|Presencial|En remoto|Remoto|a distancia|Hybrid|On-site|Remote)\)\s*$",
    re.IGNORECASE,
)
_WORK_MODE_MAP = {
    "híbrido": "Hibrido", "hibrido": "Hibrido", "hybrid": "Hibrido",
    "presencial": "Presencial", "on-site": "Presencial", "onsite": "Presencial",
    "en remoto": "Remoto", "remoto": "Remoto", "remote": "Remoto",
    "a distancia": "Remoto",
}


@dataclass
class SerpCardRaw:
    """Datos crudos extraidos de una tarjeta del SERP (sin detalle)."""
    job_id: str
    job_url: str
    title: str
    company_name: str
    location_raw: str
    posted_datetime: Optional[str]
    posted_relative: str
    is_new: bool
    work_mode: str = ""  # solo disponible en auth page (regex del caption)


def clean_url(url: str) -> str:
    """Quita params de tracking de una URL de detalle."""
    if not url:
        return ""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for k in ["position", "pageNum", "refId", "trackingId", "trk",
              "refId_orig", "original_referer", "sessionRedirect",
              "eBP", "currentJobId"]:
        qs.pop(k, None)
    new_query = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse(parsed._replace(query=new_query, fragment=""))


# LinkedIn devuelve las ubicaciones en el idioma del locale (es-ES -> "Irlanda",
# "Países Bajos"). Se normaliza el pais a ingles para que el analisis sea
# consistente entre runs y busquedas.
_COUNTRY_NORMALIZE = {
    "irlanda": "Ireland",
    "países bajos": "Netherlands", "paises bajos": "Netherlands",
    "españa": "Spain", "espana": "Spain",
    "suiza": "Switzerland", "reino unido": "United Kingdom",
    "alemania": "Germany", "francia": "France", "portugal": "Portugal",
    "italia": "Italy", "bélgica": "Belgium", "belgica": "Belgium",
    "estados unidos": "United States", "canadá": "Canada", "canada": "Canada",
    "luxemburgo": "Luxembourg", "austria": "Austria", "suecia": "Sweden",
    "dinamarca": "Denmark", "noruega": "Norway", "finlandia": "Finland",
    "polonia": "Poland", "chequia": "Czechia", "república checa": "Czechia",
}


# Ciudades que LinkedIn traduce segun locale (Dublín/Dublin, Ámsterdam/
# Amsterdam...). Se normalizan a su forma internacional para que el analisis
# geografico no fragmente una misma ciudad en dos valores.
_CITY_NORMALIZE = {
    "dublín": "Dublin", "dublin": "Dublin",
    "ámsterdam": "Amsterdam", "amsterdam": "Amsterdam",
    "róterdam": "Rotterdam", "rotterdam": "Rotterdam",
    "la haya": "The Hague", "the hague": "The Hague",
    "zúrich": "Zurich", "zurich": "Zurich",
    "ginebra": "Geneva", "geneva": "Geneva",
    "bruselas": "Brussels", "brussels": "Brussels",
    "londres": "London", "london": "London",
    "berlín": "Berlin", "berlin": "Berlin",
    "múnich": "Munich", "munich": "Munich",
    "viena": "Vienna", "vienna": "Vienna",
    "copenhague": "Copenhagen", "copenhagen": "Copenhagen",
    "lisboa": "Lisbon", "lisbon": "Lisbon",
}


def _normalize_country(country: str) -> str:
    if not country:
        return ""
    return _COUNTRY_NORMALIZE.get(country.strip().lower(), country.strip())


def _normalize_city(city: str) -> str:
    if not city:
        return ""
    return _CITY_NORMALIZE.get(city.strip().lower(), city.strip())


def parse_location(raw: str) -> tuple[str, str, str]:
    """Divide 'Madrid, Comunidad de Madrid, España' en (city, region, country).

    Quita el sufijo de modo de trabajo (Híbrido)/(Presencial)/(En remoto) si
    esta presente (pagina autenticada lo incluye en el caption).
    """
    if not raw:
        return "", "", ""
    # Qitar sufijo de modo de trabajo entre parentesis
    raw_clean = _RE_WORK_MODE.sub("", raw).strip().rstrip(",").strip()
    parts = [p.strip() for p in raw_clean.split(",") if p.strip()]
    if len(parts) >= 3:
        return _normalize_city(parts[0]), parts[1], _normalize_country(parts[-1])
    if len(parts) == 2:
        return _normalize_city(parts[0]), "", _normalize_country(parts[1])
    if len(parts) == 1:
        # Si el unico token es un pais ("Irlanda"), va a country, no a city.
        if parts[0].lower() in _COUNTRY_NORMALIZE:
            return "", "", _normalize_country(parts[0])
        return parts[0], "", ""
    return "", "", ""


def extract_work_mode(raw: str) -> str:
    """Extrae el modo de trabajo del texto de ubicacion (auth page).

    El caption auth incluye '(Híbrido)', '(Presencial)' o '(En remoto)' al
    final. Devuelve "" si no se encuentra.
    """
    if not raw:
        return ""
    m = _RE_WORK_MODE.search(raw)
    if not m:
        return ""
    key = m.group(1).lower().strip()
    return _WORK_MODE_MAP.get(key, "")


def _text(el) -> str:
    """inner_text seguro (trim)."""
    if not el:
        return ""
    try:
        return (el.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        try:
            return (el.text_content() or "").strip()
        except Exception:  # noqa: BLE001
            return ""


# --- Deteccion de layout ---
def detect_layout(page) -> str:
    """Detecta si la pagina actual es guest o auth. Devuelve 'guest' o 'auth'."""
    # Auth: tiene el sentinel + job-card-container
    try:
        if page.query_selector(SEL_JOB_CARD_AUTH) or \
           page.query_selector(SEL_JOB_CARD_AUTH_ALT):
            return "auth"
    except Exception:  # noqa: BLE001
        pass
    # Guest: tiene job-search-card con data-entity-urn
    try:
        if page.query_selector(SEL_JOB_CARD_GUEST):
            return "guest"
    except Exception:  # noqa: BLE001
        pass
    # Fallback: si hay sentinel de auth, es auth
    try:
        if page.query_selector(SEL_LIST_SENTINEL_AUTH):
            return "auth"
    except Exception:  # noqa: BLE001
        pass
    return "guest"


# --- Parseo GUEST ---
def _extract_job_id_guest(card) -> Optional[str]:
    urn = card.get_attribute("data-entity-urn") or ""
    m = _RE_JOB_ID_FROM_URN.search(urn)
    if m:
        return m.group(1)
    link = card.query_selector(SEL_TITLE_LINK_GUEST)
    href = link.get_attribute("href") if link else ""
    m2 = _RE_JOB_ID_FROM_URL_GUEST.search(href or "")
    return m2.group(1) if m2 else None


def _parse_card_guest(card) -> Optional[SerpCardRaw]:
    job_id = _extract_job_id_guest(card)
    if not job_id:
        return None
    link = card.query_selector(SEL_TITLE_LINK_GUEST)
    raw_url = link.get_attribute("href") if link else ""
    job_url = clean_url(raw_url)
    title = _text(card.query_selector(SEL_TITLE_GUEST))
    if not title:
        return None
    company = _text(card.query_selector(SEL_COMPANY_GUEST))
    location_raw = _text(card.query_selector(SEL_LOCATION_GUEST))
    time_el = card.query_selector(SEL_DATE_NEW_GUEST)
    is_new = time_el is not None
    if not time_el:
        time_el = card.query_selector(SEL_DATE_GUEST)
    posted_datetime = time_el.get_attribute("datetime") if time_el else None
    posted_relative = _text(time_el)
    return SerpCardRaw(
        job_id=job_id, job_url=job_url, title=title,
        company_name=company, location_raw=location_raw,
        posted_datetime=posted_datetime, posted_relative=posted_relative,
        is_new=is_new, work_mode="",
    )


# --- Parseo AUTH ---
def _extract_job_id_auth(card) -> Optional[str]:
    # data-job-id en el div.job-card-container
    jid = card.get_attribute("data-job-id")
    if jid:
        return jid
    # Fallback: del href del link
    link = card.query_selector(SEL_TITLE_LINK_AUTH)
    href = link.get_attribute("href") if link else ""
    m = _RE_JOB_ID_FROM_URL.search(href or "")
    return m.group(1) if m else None


def _parse_card_auth(card) -> Optional[SerpCardRaw]:
    job_id = _extract_job_id_auth(card)
    if not job_id:
        return None
    # URL de detalle
    link = card.query_selector(SEL_TITLE_LINK_AUTH)
    raw_url = link.get_attribute("href") if link else ""
    if raw_url and not raw_url.startswith("http"):
        raw_url = "https://www.linkedin.com" + raw_url
    job_url = clean_url(raw_url)
    # Titulo: preferir el span strong, fallback al aria-label del link
    title = _text(card.query_selector(SEL_TITLE_TEXT_AUTH))
    if not title and link:
        title = (link.get_attribute("aria-label") or "").strip()
    if not title:
        title = _text(card.query_selector(SEL_TITLE_AUTH))
    if not title:
        return None
    company = _text(card.query_selector(SEL_COMPANY_AUTH))
    location_raw = _text(card.query_selector(SEL_LOCATION_AUTH))
    work_mode = extract_work_mode(location_raw)
    # Auth page: no hay <time> por tarjeta
    return SerpCardRaw(
        job_id=job_id, job_url=job_url, title=title,
        company_name=company, location_raw=location_raw,
        posted_datetime=None, posted_relative="",
        is_new=False, work_mode=work_mode,
    )


# --- API publica (autodetecta layout) ---
def parse_card(card, layout: str = "auto") -> Optional[SerpCardRaw]:
    """Parsea UNA tarjeta. layout='auto' detecta, o forzar 'guest'/'auth'."""
    if layout == "auto":
        # Heuristica rapida por atributo del card
        if card.get_attribute("data-job-id"):
            layout = "auth"
        elif card.get_attribute("data-entity-urn"):
            layout = "guest"
        else:
            layout = "auth"  # fallback
    if layout == "auth":
        return _parse_card_auth(card)
    return _parse_card_guest(card)


def parse_serp(page, search_role: str, search_city: str,
               layout: str = "auto") -> list[SerpCardRaw]:
    """Extrae TODAS las tarjetas visibles del SERP actual.

    Autodetecta el layout (guest vs auth) si layout='auto'.
    """
    if layout == "auto":
        layout = detect_layout(page)
        log.info("parse_serp: layout detectado = '%s'", layout)

    if layout == "auth":
        cards = page.query_selector_all(SEL_JOB_CARD_AUTH)
        if not cards:
            cards = page.query_selector_all(SEL_JOB_CARD_AUTH_ALT)
    else:
        cards = page.query_selector_all(SEL_JOB_CARD_GUEST)

    log.info("parse_serp: %d tarjetas encontradas para '%s' en %s (layout=%s)",
             len(cards), search_role, search_city, layout)

    out: list[SerpCardRaw] = []
    seen_ids: set[str] = set()
    for c in cards:
        try:
            parsed = _parse_card_auth(c) if layout == "auth" else _parse_card_guest(c)
        except Exception as e:  # noqa: BLE001
            log.debug("Error en tarjeta: %s", e)
            continue
        if parsed and parsed.job_id not in seen_ids:
            seen_ids.add(parsed.job_id)
            out.append(parsed)
    log.info("parse_serp: %d tarjetas validas (tras dedup por job_id).", len(out))
    return out


def get_result_count(page) -> Optional[str]:
    """Devuelve el contador total del SERP, o None si no aparece."""
    # Auth: 'Mas de 400 resultados' o similar
    el = page.query_selector(SEL_RESULT_COUNT_AUTH)
    txt = _text(el)
    if txt:
        return txt
    # Guest: numero solo '99'
    el = page.query_selector(SEL_RESULT_COUNT_GUEST)
    return _text(el) or None


def has_next_page(page) -> bool:
    """True si hay boton de pagina siguiente (auth page con paginacion)."""
    try:
        btn = page.query_selector(SEL_PAGINATION_NEXT)
        if not btn:
            return False
        # El boton puede estar disabled
        disabled = btn.get_attribute("disabled")
        return disabled is None
    except Exception:  # noqa: BLE001
        return False


def click_next_page(page, timeout_ms: int = 15000) -> bool:
    """Hace click en 'Siguiente' (auth page). Devuelve True si tuvo exito.

    Fallback escalonado porque LinkedIn suele superponer el contenedor de la
    paginacion sobre el boton, y Playwright rechaza el click por
    "element intercepts pointer events" (bug visto en los logs del SERP auth):
      1. click normal (con actionability checks).
      2. click(force=True): ignora solapamientos.
      3. click via JS (el.click()): no depende de hit-testing.
    """
    try:
        btn = page.query_selector(SEL_PAGINATION_NEXT)
        if not btn:
            return False
        try:
            btn.click(timeout=timeout_ms)
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("click_next_page normal fallo (%s); probando force.", e)
            try:
                btn.click(force=True, timeout=timeout_ms)
                return True
            except Exception as e2:  # noqa: BLE001
                log.debug("click_next_page force fallo (%s); probando JS.", e2)
                try:
                    page.evaluate("(el) => el.click()", btn)
                    return True
                except Exception as e3:  # noqa: BLE001
                    log.debug("click_next_page JS fallo: %s", e3)
                    return False
    except Exception as e:  # noqa: BLE001
        log.debug("click_next_page fallo: %s", e)
        return False


def to_job_offer(raw: SerpCardRaw, search_role: str, search_city: str,
                 source: str = "local") -> JobOffer:
    """Convierte SerpCardRaw en JobOffer (campos de detalle quedan vacios)."""
    city, region, country = parse_location(raw.location_raw)
    return JobOffer(
        job_id=raw.job_id,
        job_url=raw.job_url,
        title=raw.title,
        company_name=raw.company_name,
        location_raw=raw.location_raw,
        location_city=city,
        location_region=region,
        location_country=country,
        posted_datetime=raw.posted_datetime,
        posted_relative=raw.posted_relative,
        is_new=raw.is_new,
        work_mode=raw.work_mode,
        search_role=search_role,
        search_city=search_city,
        source=source,
        scraped_at=now_utc_iso(),
    )
