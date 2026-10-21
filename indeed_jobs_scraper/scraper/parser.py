"""Parser de la SERP de Indeed.

Estrategia (revisada tras diagnostico en produccion):
  Indeed BORRA window._initialData y window.mosaic tras hidratar (anti-scraping),
  asi que ya no se puede usar page.evaluate para leerlos. Pero los <script>
  embebidos siguen en el DOM (page.content()), y contienen los datos intactos.

  Fuente principal: <script id="mosaic-data"> que asigna
    window.mosaic.providerData["mosaic-provider-jobcards"] = {...}
  Dentro: metaData.mosaicProviderJobCardsModel.results[]  (array plano, 15-16 items)

  Campos disponibles en cada item (confirmado):
    jobkey (minuscula!), displayTitle, company, companyIdEncrypted, companyRating,
    companyReviewCount, companyOverviewLink, formattedLocation, jobLocationCity,
    jobLocationState, extractedSalary {min,max,type}, salarySnippet {text,...},
    formattedRelativeTime, createDate, pubDate, sponsored, snippet (HTML),
    remoteWorkModel {text,type}, viewJobLink, link, title, translatedAttributes[],
    jobTypes[].

  Fallback: selectores HTML corregidos (div.cardOutline con [data-jk], ya que
  div[id^="job_"] ya no existe; a.jcs-JobTitle para titulo).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, TYPE_CHECKING

from .models import JobOffer

if TYPE_CHECKING:
    from patchright.sync_api import Page

log = logging.getLogger(__name__)


# --- Utilidades -----------------------------------------------------------
def _epoch_ms_to_iso(ms: int | float | None) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def _clean_html(html_fragment: str) -> str:
    """Convierte un snippet HTML en texto plano legible.

    Elimina primero bloques <script>/<style>/<!-- --> (fuente de CSS y
    texto oculto en las descripciones), luego las etiquetas restantes y
    normaliza el espacio.
    """
    if not html_fragment:
        return ""
    s = html_fragment
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r'(?i)\sstyle\s*=\s*("[^"]*"|\'[^\']*\')', " ", s)
    text = re.sub(r"<[^>]+>", " ", s)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _balance_braces(s: str, start: int) -> int:
    """Devuelve el indice (exclusive) del cierre de la llave que se abre en `start`.

    Maneja strings con comillas y escapes para no romper con `}` literales dentro
    de cadenas (ej. descripciones de empleo).
    """
    depth = 0
    in_str = False
    esc = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def _looks_remote(location: str, title: str, remote_model: dict | None) -> bool:
    if remote_model and isinstance(remote_model, dict):
        rtype = (remote_model.get("type") or "").upper()
        if rtype.startswith("REMOTE"):
            return True
    blob = f"{location} {title}".lower()
    return any(k in blob for k in ("remoto", "remote", "teletrabajo", "híbrido", "hibrido", "hybrid"))


def _salary_from_item(item: dict) -> tuple[int | None, int | None, str, str]:
    """Devuelve (min, max, type, text) combinando extractedSalary y salarySnippet."""
    extracted = item.get("extractedSalary")
    smin = smax = None
    stype = ""
    if isinstance(extracted, dict):
        try:
            smin = int(extracted["min"]) if extracted.get("min") is not None else None
            smax = int(extracted["max"]) if extracted.get("max") is not None else None
        except (TypeError, ValueError, KeyError):
            pass
        stype = extracted.get("type", "") or ""
    salary_text = ""
    snip = item.get("salarySnippet")
    if isinstance(snip, dict):
        salary_text = snip.get("text", "") or ""
    if not salary_text and smin is not None and smax is not None:
        salary_text = f"{smin} - {smax}"
    return smin, smax, stype, salary_text


# --- Extraccion del JSON embebido -----------------------------------------
_MOSAIC_DATA_RE = re.compile(r'<script id="mosaic-data"[^>]*>(.*?)</script>', re.DOTALL)
_JOBCARDS_ASSIGN_RE = re.compile(
    r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*'
)
# Contenedor de la descripcion del job auto-abierto (two-pane). Indeed ya no
# publica `window._initialData =`; el contenedor sigue embebido en los
# <script> y se localiza de forma tolerante por su clave, tanto en la forma
# `"autoOpenTwoPaneViewjobResponse": {...}` como `... = {...}` (T-32).
_SCRIPT_BODY_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
_AUTO_OPEN_RESPONSE_RE = re.compile(
    r'autoOpenTwoPaneViewjobResponse["\']?\s*[:=]\s*'
)


def _extract_jobcards_results(html: str) -> list[dict] | None:
    """Extrae el array results de providerData["mosaic-provider-jobcards"] del HTML.

    Retorna la lista de items o None si no se encuentra/no parsea.
    """
    m = _MOSAIC_DATA_RE.search(html)
    if not m:
        log.debug("no se encontro <script id=mosaic-data> en el HTML")
        return None
    body = m.group(1)

    am = _JOBCARDS_ASSIGN_RE.search(body)
    if not am:
        log.debug("no se encontro asignacion providerData[mosaic-provider-jobcards]")
        return None
    brace = body.find("{", am.end())
    if brace < 0:
        return None
    end = _balance_braces(body, brace)
    if end < 0:
        log.warning("no se pudo balancear el JSON de mosaic-provider-jobcards")
        return None
    raw = body[brace:end]
    try:
        import json

        obj = json.loads(raw)
    except Exception as e:
        log.warning("JSON de mosaic-provider-jobcards no parseo: %s", e)
        return None

    # navegar: metaData.mosaicProviderJobCardsModel.results
    try:
        results = obj["metaData"]["mosaicProviderJobCardsModel"]["results"]
    except (KeyError, TypeError):
        log.warning("ruta metaData.mosaicProviderJobCardsModel.results no existe")
        return None
    if not isinstance(results, list):
        return None
    return results


def _descriptions_from_auto_open(response: dict) -> dict[str, str]:
    """Extrae ``job.key -> description.html`` de la respuesta auto-open (T-32).

    El ``body`` de la respuesta puede ser un objeto JSON o una cadena JSON
    codificada. Acepta dos formas:

    - ruta actual (layout two-pane 2026-10): ``body.jobKey`` +
      ``body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription`` (HTML);
    - ruta antigua (fallback): ``body.hostQueryExecutionResult.data.jobData.
      results[].job.key`` + ``job.description.html``.

    Devuelve {} cuando ninguna ruta existe; nunca lanza.
    """
    if not isinstance(response, dict):
        return {}
    body = response.get("body")
    if isinstance(body, str):
        try:
            import json
            body = json.loads(body)
        except Exception as e:
            log.debug("body de autoOpenTwoPaneViewjobResponse no parseo: %s", e)
            return {}
    if not isinstance(body, dict):
        return {}

    # Ruta nueva (2026-10): la descripcion embebida vive en
    # jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription (HTML) y la
    # clave del job en body.jobKey.
    try:
        new_jk = body["jobKey"]
        new_desc = body["jobInfoWrapperModel"]["jobInfoModel"]["sanitizedJobDescription"]
    except (KeyError, TypeError):
        new_jk, new_desc = "", ""
    if isinstance(new_jk, str) and new_jk and isinstance(new_desc, str) and new_desc:
        return {new_jk: unescape(new_desc)}

    # Ruta antigua (fallback): hostQueryExecutionResult.data.jobData.results[].
    try:
        results = body["hostQueryExecutionResult"]["data"]["jobData"]["results"]
    except (KeyError, TypeError):
        return {}
    if not isinstance(results, list):
        return {}

    descriptions: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        job = item.get("job")
        if not isinstance(job, dict):
            continue
        try:
            jk = job["key"]
            desc_html = job["description"]["html"]
        except (KeyError, TypeError):
            continue
        if jk and desc_html:
            descriptions[jk] = unescape(desc_html)

    return descriptions


def _iter_auto_open_responses(html: str):
    """Itera las respuestas auto-open embebidas, de forma tolerante (T-32).

    Búsqueda por clave (``autoOpenTwoPaneViewjobResponse`` seguida de ``:`` o
    ``=``) en cada <script> y, como respaldo, en el HTML completo (fixtures sin
    etiquetas). Cada candidata se recorta con el balanceo de llaves existente y
    se parsea a JSON; las candidatas que no parsean o no llevan la clave se
    descartan sin romper.
    """
    import json

    contents = [m.group(1) for m in _SCRIPT_BODY_RE.finditer(html)]
    contents.append(html)
    for content in contents:
        for marker in _AUTO_OPEN_RESPONSE_RE.finditer(content):
            brace = content.find("{", marker.end())
            if brace < 0:
                continue
            end = _balance_braces(content, brace)
            if end < 0:
                continue
            try:
                obj = json.loads(content[brace:end])
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _extract_initial_data_descriptions(html: str) -> dict[str, str]:
    """Extrae la descripcion del job auto-abierto en el panel derecho (two-pane).

    Indeed ya no publica ``window._initialData =`` de forma estable, pero el
    contenedor ``autoOpenTwoPaneViewjobResponse`` sigue embebido en los
    ``<script>``; se busca de forma tolerante por su clave y se acepta tanto la
    ruta actual (``body.jobInfoWrapperModel.jobInfoModel.
    sanitizedJobDescription`` con la clave en ``body.jobKey``) como la antigua
    (``body.hostQueryExecutionResult.data.jobData.results[]``); ``body`` puede
    ser un objeto o una cadena JSON.

    Solo devuelve 1 descripcion (la del job auto-seleccionado), no todas.
    Para obtener todas las descripciones, usar --enrich (panel derecho por
    clics). Devuelve {} sin romper cuando el contenedor no aparece.
    """
    for response in _iter_auto_open_responses(html):
        descriptions = _descriptions_from_auto_open(response)
        if descriptions:
            return descriptions
    log.debug("no se encontro autoOpenTwoPaneViewjobResponse con descripciones en el HTML")
    return {}


def _map_item_to_offer(
    item: dict, country_code: str, city_query: str, search_term: str, page_num: int, domain: str
) -> JobOffer | None:
    jk = item.get("jobkey") or ""
    if not jk:
        # fallback: mouseDownHandlerOption.jobKey o extraer del link
        mso = item.get("mouseDownHandlerOption")
        if isinstance(mso, dict):
            jk = mso.get("jobKey", "") or ""
        if not jk:
            link = item.get("link", "") or item.get("viewJobLink", "") or ""
            mm = re.search(r"[?&]jk=([a-f0-9]+)", link)
            if mm:
                jk = mm.group(1)
    if not jk:
        return None

    title = item.get("displayTitle") or item.get("title") or ""
    company = item.get("company") or ""
    location = item.get("formattedLocation") or ""
    smin, smax, stype, salary_text = _salary_from_item(item)

    # job_type: primer texto de translatedAttributes (ej. "Jornada completa") y,
    # si viene vacio (caso real: 15/15 en la SERP de 2026-10-18), primer
    # jobTypes[] de la tarjeta (["Contract"], ["Full-time","Permanent"]).
    job_type = ""
    ta = item.get("translatedAttributes")
    if isinstance(ta, list) and ta:
        first = ta[0]
        if isinstance(first, dict):
            job_type = first.get("text", "") or first.get("label", "") or ""
    if not job_type:
        jts = item.get("jobTypes")
        if isinstance(jts, list) and jts:
            first = jts[0]
            if isinstance(first, dict):
                job_type = first.get("text", "") or first.get("label", "") or ""
            elif isinstance(first, str):
                job_type = first

    # remote
    remote_model = item.get("remoteWorkModel")
    is_remote = _looks_remote(location, title, remote_model if isinstance(remote_model, dict) else None)

    workplace_type = ""
    if isinstance(remote_model, dict):
        # HYBRID antes que REMOTE: `REMOTE_HYBRID` contiene ambas cadenas y es
        # hibrida (bug corregido en la reparacion 2026-10-18). El texto del
        # modelo solo se usa cuando el tipo no aporta nada.
        rtype = (remote_model.get("type") or "").upper()
        if "HYBRID" in rtype:
            workplace_type = "hybrid"
        elif "REMOTE" in rtype:
            workplace_type = "remote"
        if not workplace_type:
            rtext = (remote_model.get("text") or "").upper()
            if "HYBRID" in rtext:
                workplace_type = "hybrid"
            elif "REMOTE" in rtext:
                workplace_type = "remote"
    if not workplace_type and is_remote:
        # Heuristico de titulo/location solo como ultimo recurso: un
        # remoteWorkModel explicito nunca se pisa.
        workplace_type = "remote"

    # apply_url: thirdPartyApplyUrl si existe (suele venir del GraphQL, no del plano)
    apply_url = item.get("thirdPartyApplyUrl", "") or ""

    # viewjob URL canonica
    vj = item.get("viewJobLink") or ""
    if vj and jk in vj:
        viewjob_url = vj if vj.startswith("http") else f"https://{domain}{vj}"
    else:
        viewjob_url = f"https://{domain}/viewjob?jk={jk}"

    offer = JobOffer(
        job_key=jk,
        viewjob_url=viewjob_url,
        apply_url=apply_url,
        title=title,
        company=company,
        company_id_encrypted=item.get("companyIdEncrypted", "") or "",
        company_rating=item.get("companyRating"),
        company_review_count=item.get("companyReviewCount"),
        company_overview_link=item.get("companyOverviewLink", "") or "",
        location=location,
        city=item.get("jobLocationCity", "") or "",
        state=item.get("jobLocationState", "") or "",
        is_remote=is_remote,
        salary_min=smin,
        salary_max=smax,
        salary_type=stype,
        salary_text=salary_text,
        job_type=job_type,
        workplace_type=workplace_type,
        snippet=_clean_html(item.get("snippet", "") or ""),
        description_html="",  # se llena desde el JSON auto-open (ver _extract_initial_data_descriptions)
        posted_relative=item.get("formattedRelativeTime", "") or "",
        posted_date=_epoch_ms_to_iso(item.get("pubDate") or item.get("createDate")),
        is_sponsored=bool(item.get("sponsored")),
    )
    offer.fill_trace(country_code, city_query, search_term, page_num)
    return offer


# --- API publica ----------------------------------------------------------
def parse_serp(
    page: "Page", country_code: str, city_query: str, search_term: str, page_num: int, domain: str
) -> list[JobOffer]:
    """Extrae todas las ofertas de la SERP actual y devuelve lista de JobOffer."""
    try:
        html = page.content()
    except Exception as e:
        log.warning("no se pudo obtener page.content(): %s", e)
        return _fallback_html_parse(page, country_code, city_query, search_term, page_num, domain)

    results = _extract_jobcards_results(html)
    if not results:
        log.warning("JSON embebido sin resultados; intentando fallback HTML")
        return _fallback_html_parse(page, country_code, city_query, search_term, page_num, domain)

    offers: list[JobOffer] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        offer = _map_item_to_offer(item, country_code, city_query, search_term, page_num, domain)
        if offer and offer.job_key not in seen:
            seen.add(offer.job_key)
            offers.append(offer)

    descriptions = _extract_initial_data_descriptions(html)
    if descriptions:
        filled = 0
        for offer in offers:
            if not offer.description_html and offer.job_key in descriptions:
                offer.description_html = descriptions[offer.job_key]
                offer.description_text = _clean_html(offer.description_html)
                offer.snippet = offer.description_text[:500]
                filled += 1
        if filled:
            log.info(
                "descripciones from _initialData (autoOpen embedded JSON): %d/%d ofertas",
                filled, len(offers),
            )

    log.info(
        "parseadas %d ofertas (pagina %d, %s/%s) via JSON embebido",
        len(offers), page_num, country_code, city_query,
    )
    return offers


# --- Enriquecimiento de descripciones via panel derecho --------------------
def _map_conditions_to_offer(offer, conditions_text: str) -> None:
    """Mapea texto de condiciones del panel derecho a campos individuales de JobOffer."""
    text_lower = conditions_text.lower()

    workplace_keywords = {
        "remote": "remoto",
        "hybrid": "hibrido",
        "onsite": "presencial",
    }
    found = False
    for wp_type, keywords in [
        ("remote", ["remoto", "remote", "teletrabajo", "work from home", "wfh"]),
        ("hybrid", ["hibrido", "híbrido", "hybrid", "semipresencial"]),
        ("onsite", ["presencial", "onsite", "on-site", "en persona"]),
    ]:
        if any(k in text_lower for k in keywords):
            offer.workplace_type = wp_type
            found = True
            break
    if not found and offer.is_remote:
        offer.workplace_type = "remote"

    contract_keywords = {
        "Jornada completa": ["jornada completa", "full-time", "full time", "jornada completa"],
        "Jornada parcial": ["jornada parcial", "part-time", "part time", "jornada parcial"],
        "Indefinido": ["indefinido", "permanent", "contrato indefinido"],
        "Temporal": ["temporal", "temporary", "contrato temporal", "por obra"],
        "Autónomo": ["autonomo", "autónomo", "freelance", "freelancer"],
    }
    for ct, keywords in contract_keywords.items():
        if any(k in text_lower for k in keywords):
            offer.contract_type = ct
            break
    if not offer.contract_type and offer.job_type:
        offer.contract_type = offer.job_type

    schedule_keywords = [
        "turno", "horario", "turn", "shift", "schedule",
        "mañana", "tarde", "noche", "rotativo",
        "de lunes a", "flexible",
    ]
    for kw in schedule_keywords:
        if kw in text_lower:
            idx = text_lower.find(kw)
            line_end = text_lower.find("\n", idx)
            if line_end < 0:
                line_end = len(text_lower)
            offer.schedule = text_lower[idx:line_end].strip()
            break

    benefit_keywords = [
        "beneficio", "benefit", "ventaja", "perk",
        "seguro", "insurance", "comida", "meal", "ticket",
        "parking", "parking", "gimnasio", "gym",
        "formación", "training", "formacion",
        "descuento", "discount",
        "plan de pension", "pension", "retirement",
        "vacaciones", "vacation", "pto",
    ]
    benefits_lines = []
    for line in conditions_text.split("\n"):
        line_lower = line.strip().lower()
        if any(k in line_lower for k in benefit_keywords):
            benefits_lines.append(line.strip())
    if benefits_lines:
        offer.benefits = "; ".join(benefits_lines)


# Cadena de esperas del panel derecho (T-32; iteracion 3): la UI two-pane
# actual usa testids `vj-*`/`viewjob-*`; los selectores del layout antiguo
# (`jobsearch-*`, `#jobDescriptionText`) se conservan al final como respaldo.
# El encabezado nuevo (`vj-job-title`) va primero; un timeout ya no aborta la
# oferta, solo tras agotar la cadena se registra como fallida.
_PANEL_HEADER_SELECTOR = (
    '[data-testid="vj-job-title"], '
    '[data-testid="jobsearch-JobInfoHeader-title"]'
)
# El marcador `vj-job-description-heading` identifica el contenedor de la
# descripcion: `_read_panel_description` lee su contenedor padre (el heading
# solo es el titulo visible).
_PANEL_DESC_HEADING_SELECTOR = '[data-testid="vj-job-description-heading"]'
_PANEL_DESC_SELECTORS = (
    '[data-testid="viewjob-job-content"]',
    _PANEL_DESC_HEADING_SELECTOR,
    "#jobDescriptionText",
    '[data-testid="jobDescriptionText"]',
    'div[class*="jobDescriptionText"]',
    'div[id*="jobDescription"]',
)
_PANEL_DESC_SELECTOR = ", ".join(_PANEL_DESC_SELECTORS)
_PANEL_DESC_PARENT_XPATH = "xpath=ancestor::div[1]"
_PANEL_WAIT_STEPS = (
    (_PANEL_HEADER_SELECTOR, 4000),
    (_PANEL_DESC_SELECTOR, 8000),
)
_MIN_DESC_HTML_LEN = 30


def _is_timeout_error(exc: BaseException) -> bool:
    """True para el TimeoutError de Playwright/patchright o el builtin."""
    return type(exc).__name__ in ("TimeoutError", "PlaywrightTimeoutError")


def _wait_for_selector_safe(page, selector: str, timeout_ms: int) -> bool:
    """Espera un selector sin propagar errores; True si aparecio (T-32)."""
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        return True
    except Exception as e:
        if not _is_timeout_error(e):
            log.debug("wait_for_selector fallo para %r: %s", selector, e)
        return False


def _parent_of_heading(locator):
    """Contenedor padre del marcador de descripcion; el marcador si falla."""
    try:
        parent = locator.locator(_PANEL_DESC_PARENT_XPATH)
        if parent.count() > 0:
            return parent
    except Exception:
        pass
    return locator


def _read_panel_description(page) -> str:
    """Lee el HTML/texto de la descripcion del panel derecho, si ya esta.

    Orden (T-32, iteracion 3): contenedor actual `viewjob-job-content`, marcador
    `vj-job-description-heading` (se lee el contenedor padre del marcador) y,
    al final, los selectores del layout antiguo (`#jobDescriptionText`,
    `jobDescriptionText`, clases `jobDescriptionText`/`jobDescription`).
    """
    for sel in _PANEL_DESC_SELECTORS:
        try:
            el = page.locator(sel).first
        except Exception:
            continue
        if el.count() > 0:
            if sel == _PANEL_DESC_HEADING_SELECTOR:
                el = _parent_of_heading(el)
            try:
                desc_html = el.inner_html() or el.inner_text() or ""
            except Exception:
                desc_html = ""
            if desc_html and len(desc_html) > _MIN_DESC_HTML_LEN:
                return desc_html
    return ""


def _is_card_active(page, jk: str) -> bool:
    """True cuando la tarjeta clicada quedo activa (`vjs-highlight`)."""
    selector = (
        f'div.vjs-highlight a[data-jk="{jk}"], '
        f'a[data-jk="{jk}"].vjs-highlight'
    )
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def _wait_for_right_panel(page, jk: str) -> tuple[str, bool]:
    """Espera la descripcion del panel derecho con selectores encadenados.

    Encadena el encabezado (`vj-job-title` y respaldo
    `jobsearch-JobInfoHeader-title`, 4 s) y los contenedores de descripcion
    (`viewjob-job-content`/`vj-job-description-heading` y respaldos antiguos,
    8 s), comprobando ademas si la tarjeta quedo activa (`vjs-highlight`).
    Nunca aborta la oferta: si la cadena se agota devuelve ("", card_active) y
    el llamante registra el fallo y sigue.
    """
    card_active = False
    for selector, timeout_ms in _PANEL_WAIT_STEPS:
        _wait_for_selector_safe(page, selector, timeout_ms)
        if not card_active:
            card_active = _is_card_active(page, jk)
        desc_html = _read_panel_description(page)
        if desc_html:
            return desc_html, card_active
    return "", card_active


_CONSENT_BANNER_SELECTORS = (
    # OneTrust: cierre (X) y, como respaldo, aceptar
    # (`#onetrust-accept-btn-handler`), que PULSA ACEPTAR y por tanto otorga el
    # consentimiento de cookies (el perfil persistente ya lo tiene; un perfil
    # fresco muestra el banner).
    "#onetrust-close-btn-container button",
    "#onetrust-accept-btn-handler",
)


def _dismiss_consent_banner(page) -> bool:
    """Cierra el banner de consentimiento (OneTrust) si esta visible (2026-10-18).

    Best-effort: primero intenta cerrar (X); si no existe, el respaldo
    `#onetrust-accept-btn-handler` PULSA ACEPTAR, es decir, otorga el
    consentimiento de cookies (no solo cierra el banner). No altera nada mas de
    la pagina (no navega ni toca el panel). Si no hay banner o el clic falla, se
    continua (el reintento del clic cubre el caso de overlay). Devuelve True
    solo si se llego a clickar un control del banner.
    """
    for selector in _CONSENT_BANNER_SELECTORS:
        try:
            el = page.locator(selector).first
            if el.count() == 0 or not el.is_visible():
                continue
            el.click(timeout=3000)
            log.info("banner de consentimiento cerrado (%s)", selector)
            return True
        except Exception as e:
            log.debug("no se pudo cerrar el banner de consentimiento (%s): %s", selector, e)
    return False


def _click_card_with_retry(card_link, jk: str, sleep_fn) -> bool:
    """Clica una tarjeta con 2 intentos (reparacion 2026-10-18).

    El primer intento usa la espera actual (5 s); si expira, el segundo hace
    scroll into view, espera breve (1-2 s) y reintenta con timeout 10 s (caso
    real: `Locator.click: Timeout 5000ms` en jk=3a8c91f14177f18a; un banner
    OneTrust pudo cubrir la tarjeta). Devuelve True si algun intento completo
    el clic; False solo cuando ambos se agotan.
    """
    try:
        card_link.click(timeout=5000)
        return True
    except Exception as e:
        log.debug("primer clic fallido jk=%s: %s; reintentando", jk, e)
    try:
        card_link.scroll_into_view_if_needed()
        sleep_fn(1.0 + (hash(jk) % 10) * 0.1)  # 1.0-1.9 s
        card_link.click(timeout=10000)
        return True
    except Exception as e:
        log.warning("clic fallido tras 2 intentos jk=%s: %s", jk, e)
        return False


def enrich_offers_via_right_panel(
    page: "Page",
    offers: list[JobOffer],
    domain: str = "",
    rate: float = 1.0,
    state=None,
    cache: dict | None = None,
    max_enrich: int = 0,
    allow_viewjob_fallback: bool = False,
    progress_cb=None,
) -> tuple[int, int]:
    """Clica cada job card en el panel izquierdo y cosecha la descripcion
    completa y las condiciones (beneficios, jornada, schedule, compensacion)
    del panel derecho de la SERP two-pane de Indeed.

    Llamar despues de parse_serp, con la pagina aun abierta.
    Modifica los JobOffer in-place (description_html, benefits, etc.).
    Con delay entre clics (~6s) para no disparar anti-bot.

    Descripcion (T-32): la espera del panel es una cadena escalonada de
    selectores (`_wait_for_right_panel`); si se agota, la oferta se registra
    como fallida (WARNING) y el bucle sigue con la siguiente, sin `continue`
    silencioso. `progress_cb(done, total)` se invoca tras procesar cada oferta
    para emitir latido PROGRESS durante el enriquecimiento (el watchdog del
    pipeline decide por ese contador).

    Fallback /viewjob: **desactivado por defecto**. `robots.txt` de Indeed
    prohibe `/viewjob` y `/graphql` para `User-agent: *` y el fallback era
    inalcanzable por el orden del codigo; solo un interruptor explicito
    (`allow_viewjob_fallback=True`) lo activa, jamas la ejecucion normal.

    Estrategia mixta y segura (cache + tope):
      1. Primero rellena desde `cache` (desc_cache) las ofertas ya conocidas: 0 clics.
      2. Solo las que siguen sin descripcion son candidatas a clic.
      3. Se seleccionan las candidatas segun `rate` (>= 1.0: todas) y se aplica
         el tope duro `max_enrich` (0 = sin tope) para no disparar heuristicas
         anti-bot.

    Clic con reintento (2026-10-18): cada tarjeta se clica con hasta 2 intentos
    (el 2o hace scroll + espera breve + timeout 10 s) y el jk solo se registra
    como fallido tras agotar ambos; antes del bucle se descarta el banner de
    consentimiento visible (OneTrust) como best-effort.

    rate: fraccion de candidatas a clicar.
      - >= 1.0: todas, sin muestreo que pueda saltarse ninguna.
      - 0.0: todas solo si hay tope (`max_enrich > 0`), que recorta la lista;
        sin tope no se hace ningun clic.
      - 0 < rate < 1: muestreo explicito de la fraccion y registro INFO de
        cuantas candidatas se saltan.
    Si `max_enrich` recorta la muestra, se emite WARNING con las pendientes.
    state (RunState): se mantiene por compatibilidad; ya NO se usa para saltar
        ofertas (el cache es quien evita re-clicar entre runs).
    max_enrich: numero maximo de clics por SERP (0 = sin tope).

    Devuelve (attempted, success): ofertas clickadas y ofertas con descripcion.
    """
    import random as _random
    import time

    if not offers:
        return 0, 0

    # 1) rellenar desde cache persistente (sin clics)
    if cache:
        try:
            from .desc_cache import apply_cache_to_offers

            apply_cache_to_offers(offers, cache)
        except Exception as e:
            log.debug("no se pudo aplicar desc-cache: %s", e)

    if rate <= 0 and max_enrich <= 0:
        return 0, 0

    # 2) candidatas = ofertas aun sin descripcion
    candidates = [
        o for o in offers
        if o.job_key and not (o.description_html and len(o.description_html) > 50)
    ]
    if not candidates:
        return 0, 0

    # 3) seleccion por rate + tope duro
    # Con rate >= 1.0 se intentan TODAS las candidatas (sin `int()` ni
    # `_random.sample` que puedan saltarse alguna: causa raiz 13/14 del run
    # 2026-10-18 con rate=0.9). rate == 0.0 tambien cae aqui como "todas"
    # cuando hay tope (`max_enrich > 0`); sin tope el early return de arriba no
    # hace ningun clic. rate < 1 (y > 0) solo prorratea de forma explicita y
    # deja registro de cuantas candidatas se saltan.
    if rate >= 1.0 or rate <= 0:
        sample = list(candidates)
    else:
        sample_size = min(max(1, int(len(candidates) * rate)), len(candidates))
        sample = _random.sample(candidates, sample_size)
        skipped_by_rate = len(candidates) - len(sample)
        if skipped_by_rate:
            log.info(
                "enriquecimiento: rate=%.0f%% salta %d candidatas sin descripcion "
                "(se intentaran el resto; subir el rate o el tope las recupera)",
                rate * 100, skipped_by_rate,
            )
    if max_enrich > 0 and len(sample) > max_enrich:
        pending_due_to_max = len(sample) - max_enrich
        sample = sample[:max_enrich]
        # WARNING (no INFO): el tope deja ofertas sin descripcion que la web si
        # publica; es una decision de volumen, no un exito silencioso.
        log.warning(
            "enriquecimiento limitado a %d clics (max_enrich=%d); quedan %d "
            "candidatas sin descripcion pendientes (sin cache todavia)",
            len(sample), max_enrich, pending_due_to_max,
        )

    cond_selectors = [
        '[data-testid="structured-job-summary"]',
        '[data-testid="jobsearch-OtherJobDetailsContainer"]',
        '[data-testid="jobDetailsSection"]',
        'div[class*="jobDetails"]',
        'div[class*="benefits"]',
        'div[class*="qualifications"]',
        'ul[class*="jobDetails"]',
    ]

    # Best-effort: descarta el banner de consentimiento visible (OneTrust) para
    # que no cubra las tarjetas durante los clics (perfil fresco); el respaldo
    # pulsa aceptar (otorga el consentimiento de cookies) y no altera nada mas
    # de la pagina; no es bloqueante.
    _dismiss_consent_banner(page)

    enriched = 0
    success = 0
    failed = 0
    total = len(sample)
    for offer in sample:
        jk = offer.job_key
        if not jk:
            continue
        if offer.description_html and len(offer.description_html) > 50:
            continue

        conditions_text = ""
        vj_conditions = ""
        desc_html = ""
        try:
            card_link = page.locator(f'a[data-jk="{jk}"]').first
            if card_link.count() == 0:
                card_link = page.locator(f'a[href*="jk={jk}"]').first
            if card_link.count() == 0:
                failed += 1
                log.warning(
                    "enriquecimiento fallido jk=%s: no se encontro tarjeta clicable en la SERP",
                    jk,
                )
            else:
                card_link.scroll_into_view_if_needed()
                time.sleep(0.8)
                clicked = _click_card_with_retry(card_link, jk, time.sleep)
                if not clicked:
                    # Solo tras agotar los 2 intentos se registra el jk como
                    # fallido (caso real: Locator.click Timeout 5000ms en
                    # jk=3a8c91f14177f18a).
                    failed += 1
                    log.warning(
                        "enriquecimiento fallido jk=%s: clic agotado tras 2 intentos; "
                        "se continua con la siguiente oferta",
                        jk,
                    )
                else:
                    # pausa amplia y variable tras cada clic (perfil lento/seguro)
                    time.sleep(6.0 + (hash(jk) % 50) * 0.12)

                    # Cadena de esperas: ya no hay `continue` que aborte la oferta.
                    desc_html, card_active = _wait_for_right_panel(page, jk)

                    if desc_html:
                        offer.description_html = desc_html
                        offer.description_text = _clean_html(desc_html)
                        offer.snippet = offer.description_text[:500]
                    elif allow_viewjob_fallback and domain:
                        try:
                            vj_url = f"https://{domain}/viewjob?jk={jk}"
                            desc_page = page.context.new_page()
                            try:
                                desc_page.goto(vj_url, wait_until="domcontentloaded", timeout=15000)
                                time.sleep(1.5)
                                desc_el = desc_page.locator(
                                    "#jobDescriptionText, [data-testid='jobDescriptionText']"
                                ).first
                                if desc_el.count() > 0:
                                    vj_desc = desc_el.inner_html() or ""
                                    if vj_desc and len(vj_desc) > _MIN_DESC_HTML_LEN:
                                        offer.description_html = vj_desc
                                        offer.description_text = _clean_html(vj_desc)
                                        offer.snippet = offer.description_text[:500]
                                        desc_html = vj_desc
                                        log.debug("descripcion obtenida via /viewjob para jk=%s", jk)
                                for sel in cond_selectors:
                                    el = desc_page.locator(sel).first
                                    if el.count() > 0:
                                        try:
                                            vj_conditions += (el.inner_text() or "") + "\n"
                                        except Exception:
                                            pass
                            finally:
                                desc_page.close()
                        except Exception as e:
                            log.debug("fallback /viewjob fallo para jk=%s: %s", jk, e)

                    if not desc_html:
                        # Se agoto la cadena de selectores: se registra el fallo y
                        # se sigue (la oferta conserva lo que traia la SERP).
                        failed += 1
                        active_note = "tarjeta activa" if card_active else "tarjeta sin vjs-highlight"
                        log.warning(
                            "enriquecimiento fallido jk=%s: sin descripcion en el panel derecho "
                            "tras la cadena de esperas (%s); se continua con la siguiente oferta",
                            jk, active_note,
                        )

                    for sel in cond_selectors:
                        el = page.locator(sel).first
                        if el.count() > 0:
                            try:
                                conditions_text += (el.inner_text() or "") + "\n"
                            except Exception:
                                pass

                    if not conditions_text:
                        try:
                            extra_el = page.locator(
                                '[data-testid="attribute_snippet_testid"], '
                                'text="Jornada", text="Tipo de contrato", '
                                'text="Beneficios", text="Horario", text="Salario base", '
                                'text="Tipo de empleo"'
                            ).first
                            if extra_el.count() > 0:
                                parent = extra_el.locator("xpath=ancestor::div[1]")
                                if parent.count() > 0:
                                    conditions_text = (parent.inner_text() or "")
                        except Exception:
                            pass

                    if vj_conditions and vj_conditions.strip():
                        conditions_text += vj_conditions

                    if conditions_text and conditions_text.strip():
                        _map_conditions_to_offer(offer, conditions_text)

        except Exception as e:
            if not desc_html:
                failed += 1
                log.warning("enriquecimiento fallido jk=%s: %s", jk, e)
            else:
                log.debug("error tras obtener la descripcion de jk=%s: %s", jk, e)

        enriched += 1
        if offer.description_html and len(offer.description_html) > 50:
            success += 1
        log.debug("enriquecida oferta jk=%s: desc=%d chars, cond=%d chars",
                  jk, len(desc_html), len(conditions_text))
        if progress_cb is not None:
            try:
                progress_cb(enriched, total)
            except Exception as e:
                log.debug("progress_cb del enrich fallo: %s", e)

    log.info(
        "enriquecidas %d/%d clics (con descripcion: %d, sin descripcion: %d; "
        "candidatas sin descripcion: %d, rate=%.0f%%, max_enrich=%s)",
        enriched, total, success, failed, len(candidates), rate * 100,
        max_enrich if max_enrich > 0 else "sin tope",
    )
    return total, success


# --- Fallback HTML (cuando el JSON embebido falla) -------------------------
def _fallback_html_parse(
    page: "Page", country_code: str, city_query: str, search_term: str, page_num: int, domain: str
) -> list[JobOffer]:
    """Extrae ofertas via selectores CSS si el JSON embebido falla.

    Selectores confirmados en diagnostico (2026-06):
      card:       div.cardOutline  (16) -- ya NO existe div[id^="job_"]
      job key:    atributo data-jk (16) en el <a> del titulo
      titulo:     a.jcs-JobTitle   (16) -- ya NO hay h2.jobTitle/h3.jobTitle literales
      empresa:    [data-testid="company-name"]      (16)
      ubicacion:  [data-testid="text-location"]     (16)
      salario:    [data-testid*="salary-snippet-container"] span
      snippet:    [data-testid="belowJobSnippet"]
    """
    offers: list[JobOffer] = []

    # cada cardOutline es una tarjeta; data-jk esta en el <a> del titulo dentro
    cards = page.locator("div.cardOutline").all()
    log.info("fallback HTML: %d div.cardOutline detectadas", len(cards))

    for card in cards:
        try:
            # job key via data-jk en el enlace del titulo
            jk = ""
            title_link = card.locator("a.jcs-JobTitle, a[data-jk]").first
            if title_link.count() > 0:
                jk = title_link.get_attribute("data-jk") or ""
                if not jk:
                    href = title_link.get_attribute("href") or ""
                    mm = re.search(r"[?&]jk=([a-f0-9]+)", href)
                    if mm:
                        jk = mm.group(1)
            if not jk:
                continue

            title = ""
            if title_link.count() > 0:
                title = (title_link.get_attribute("title") or title_link.inner_text() or "").strip()

            company = ""
            comp_el = card.locator('[data-testid="company-name"]').first
            if comp_el.count() > 0:
                company = (comp_el.inner_text() or "").strip()

            location = ""
            loc_el = card.locator('[data-testid="text-location"]').first
            if loc_el.count() > 0:
                location = (loc_el.inner_text() or "").strip()

            salary_text = ""
            sal_el = card.locator('[data-testid*="salary-snippet-container"] span').first
            if sal_el.count() > 0:
                salary_text = (sal_el.inner_text() or "").strip()

            snippet = ""
            snip_el = card.locator('[data-testid="belowJobSnippet"]').first
            if snip_el.count() > 0:
                snippet = _clean_html(snip_el.inner_text() or "")

            offer = JobOffer(
                job_key=jk,
                viewjob_url=f"https://{domain}/viewjob?jk={jk}",
                title=title,
                company=company,
                location=location,
                salary_text=salary_text,
                snippet=snippet,
                is_remote=_looks_remote(location, title, None),
            )
            offer.fill_trace(country_code, city_query, search_term, page_num)
            offers.append(offer)
        except Exception as e:
            log.debug("fallback: error en una card: %s", e)
            continue

    log.info("fallback HTML: parseadas %d ofertas", len(offers))
    return offers
