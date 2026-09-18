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
    remoteWorkModel {text,type}, viewJobLink, link, title, translatedAttributes[].

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
_INITIAL_DATA_RE = re.compile(r'window\._initialData\s*=\s*')


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


def _extract_initial_data_descriptions(html: str) -> dict[str, str]:
    """Extrae la descripcion del job auto-abierto en el panel derecho (two-pane).

    window._initialData contiene autoOpenTwoPaneViewjobResponse.body
    con hostQueryExecutionResult.data.jobData.results[0], que tiene
    la descripcion del job que Indeed abre automaticamente al cargar la SERP.

    Solo devuelve 1 descripcion (la del job auto-seleccionado), no todas.
    Para obtener todas las descripciones, usar --enrich (panel derecho por clics)
    o el fallback /viewjob?jk=.
    """
    m = _INITIAL_DATA_RE.search(html)
    if not m:
        log.debug("no se encontro window._initialData en el HTML")
        return {}
    brace = m.end()
    end = _balance_braces(html, brace)
    if end < 0:
        log.warning("no se pudo balancear window._initialData")
        return {}
    raw = html[brace:end]
    try:
        import json
        obj = json.loads(raw)
    except Exception as e:
        log.debug("JSON de window._initialData no parseo: %s", e)
        return {}

    try:
        body = obj["autoOpenTwoPaneViewjobResponse"]["body"]
        results = body["hostQueryExecutionResult"]["data"]["jobData"]["results"]
    except (KeyError, TypeError):
        return {}

    descriptions: dict[str, str] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            jk = item["job"]["key"]
            desc_html = item["job"]["description"]["html"]
        except (KeyError, TypeError):
            continue
        if jk and desc_html:
            descriptions[jk] = unescape(desc_html)

    return descriptions


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

    # job_type: primer texto de translatedAttributes (ej. "Jornada completa")
    job_type = ""
    ta = item.get("translatedAttributes")
    if isinstance(ta, list) and ta:
        first = ta[0]
        if isinstance(first, dict):
            job_type = first.get("text", "") or first.get("label", "") or ""

    # remote
    remote_model = item.get("remoteWorkModel")
    is_remote = _looks_remote(location, title, remote_model if isinstance(remote_model, dict) else None)

    workplace_type = ""
    if isinstance(remote_model, dict):
        rtype = (remote_model.get("type") or "").upper()
        if "REMOTE" in rtype:
            workplace_type = "remote"
        elif "HYBRID" in rtype:
            workplace_type = "hybrid"
    if not workplace_type and is_remote:
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
        description_html="",  # se llena desde _initialData (hostQueryExecutionResult)
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
            log.info("descripciones from _initialData: %d/%d ofertas", filled, len(offers))

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


def enrich_offers_via_right_panel(page: "Page", offers: list[JobOffer], domain: str = "", rate: float = 1.0, state=None, cache: dict | None = None, max_enrich: int = 0) -> tuple[int, int]:
    """Clica cada job card en el panel izquierdo y cosecha la descripcion
    completa y las condiciones (beneficios, jornada, schedule, compensacion)
    del panel derecho de la SERP two-pane de Indeed.

    Llamar despues de parse_serp, con la pagina aun abierta.
    Modifica los JobOffer in-place (description_html, benefits, etc.).
    Con delay entre clics (~3s) para no disparar anti-bot.

    Si domain se proporciona, usa /viewjob?jk= como fallback cuando el panel
    derecho no devuelve descripcion (navega a la pagina de detalle de la oferta).

    Estrategia mixta y segura (cache + tope):
      1. Primero rellena desde `cache` (desc_cache) las ofertas ya conocidas: 0 clics.
      2. Solo las que siguen sin descripcion son candidatas a clic.
      3. Se muestrea `rate` de las candidatas y se aplica el tope duro `max_enrich`
         (0 = sin tope) para no disparar heuristicas anti-bot.

    rate (0.0-1.0): fraccion de candidatas a enriquecer. 1.0 = todas.
    state (RunState): se mantiene por compatibilidad; ya NO se usa para saltar
        ofertas (el cache es quien evita re-clicar entre runs).
    max_enrich: numero maximo de clics por SERP (0 = sin tope).

    Devuelve (attempted, success): ofertas clickadas y ofertas con descripcion.
    """
    import random as _random
    import time
    from playwright.sync_api import TimeoutError as PlaywrightTimeout  # patchright es drop-in

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

    # 3) muestreo con rate + tope duro
    if rate > 0:
        sample_size = max(1, int(len(candidates) * rate))
    else:
        sample_size = len(candidates)
    if max_enrich > 0:
        sample_size = min(sample_size, max_enrich)
    sample = _random.sample(candidates, min(sample_size, len(candidates)))
    if max_enrich > 0 and len(candidates) > len(sample):
        log.info(
            "enriquecimiento limitado a %d clics (candidatas sin descripcion: %d, max_enrich=%d)",
            len(sample), len(candidates), max_enrich,
        )

    cond_selectors = [
        '[data-testid="jobsearch-OtherJobDetailsContainer"]',
        '[data-testid="jobDetailsSection"]',
        'div[class*="jobDetails"]',
        'div[class*="benefits"]',
        'div[class*="qualifications"]',
        'ul[class*="jobDetails"]',
    ]

    enriched = 0
    success = 0
    for offer in sample:
        jk = offer.job_key
        if not jk:
            continue
        if offer.description_html and len(offer.description_html) > 50:
            continue

        conditions_text = ""
        vj_conditions = ""
        try:
            card_link = page.locator(f'a[data-jk="{jk}"]').first
            if card_link.count() == 0:
                card_link = page.locator(f'a[href*="jk={jk}"]').first
            if card_link.count() == 0:
                log.debug("no se encontro card clicable para jk=%s", jk)
                continue

            card_link.scroll_into_view_if_needed()
            time.sleep(0.8)
            card_link.click(timeout=5000)
            # pausa amplia y variable tras cada clic (perfil lento/seguro)
            time.sleep(6.0 + (hash(jk) % 50) * 0.12)

            try:
                page.wait_for_selector(
                    '#jobDescriptionText, [data-testid="jobDescriptionText"], '
                    'div#jobDescriptionText, '
                    '[data-testid="jobsearch-JobInfoHeader-title"]',
                    timeout=8000,
                )
            except PlaywrightTimeout:
                log.debug("timeout esperando descripcion en panel derecho para jk=%s", jk)
                continue

            time.sleep(1)

            desc_html = ""
            desc_selectors = [
                "#jobDescriptionText",
                '[data-testid="jobDescriptionText"]',
                'div[class*="jobDescriptionText"]',
                'div[id*="jobDescription"]',
            ]
            for sel in desc_selectors:
                el = page.locator(sel).first
                if el.count() > 0:
                    try:
                        desc_html = el.inner_html() or el.inner_text() or ""
                    except Exception:
                        pass
                    if desc_html and len(desc_html) > 30:
                        break

            if desc_html:
                offer.description_html = desc_html
                offer.description_text = _clean_html(desc_html)
                offer.snippet = offer.description_text[:500]
            elif domain:
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
                            if vj_desc and len(vj_desc) > 30:
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

            enriched += 1
            if offer.description_html and len(offer.description_html) > 50:
                success += 1
            log.debug("enriquecida oferta jk=%s: desc=%d chars, cond=%d chars",
                      jk, len(desc_html), len(conditions_text))

        except Exception as e:
            log.debug("error enriqueciendo jk=%s: %s", jk, e)
            continue

    log.info(
        "enriquecidas %d/%d clics (candidatas sin descripcion: %d, rate=%.0f%%, max_enrich=%s)",
        enriched, len(sample), len(candidates), rate * 100, max_enrich if max_enrich > 0 else "sin tope",
    )
    return len(sample), success


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
