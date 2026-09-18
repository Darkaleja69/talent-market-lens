"""Parseo de la pagina de detalle de una oferta (paginas autenticadas).

Selectores verificados contra el HTML real autenticado (auth_detail.html).
Las clases estan OBFUSCADAS (hex aleatorio), pero hay hooks estables:
  - data-testid="expandable-text-box"  -> descripcion
  - data-testid="expandable-text-button" -> boton "ver mas"
  - componentkey="JobDetails_AboutTheJob_{id}" -> seccion descripcion
  - Chips <a> con texto Híbrido/Presencial/Remoto + Jornada completa/etc.
  - Header <p> con spans para ubicacion, fecha y solicitantes

NO disponibles en el HTML estatico autenticado:
  - Skills estructuradas (solo en prosa de la descripcion)
  - Salario estructurado (solo en prosa si se menciona)
  - Nivel de experiencia estructurado
  - Industria / tamano de empresa (en componente SDUI sin hidratar)
"""
from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any, Optional

from .human import delay, human_click, mouse_jitter, scroll_slow
from .login import BlockedException, _detect_recaptcha, _is_challenge_url, _is_guest_page
from .models import JobOffer, normalize_experience_level, DATA_SKILLS_KEYWORDS
from .parse_sections import split_description_sections

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, \
        retry_if_exception_type
    _HAS_TENACITY = True
except ImportError:  # tolerar ausencia (graceful degradation)
    _HAS_TENACITY = False


def _goto_with_retry(page, url: str) -> None:
    """page.goto + networkidle con reintentos ante timeout/red (via tenacity
    si esta disponible; sinon 1 intento). BlockedException NO se reintenta.

    Playwright/patchright lanzan su propia excepcion de timeout. La envolvemos
    en TimeoutError built-in para que tenacity la atrape.
    """
    import contextlib

    def _do_goto():
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001  networkidle opcional
            pass

    if not _HAS_TENACITY:
        _do_goto()
        return

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=2, min=2, max=15),
           retry=retry_if_exception_type(TimeoutError),
           reraise=True)
    def _wrapped():
        try:
            _do_goto()
        except Exception as e:  # noqa: BLE001
            # Translate playwright/patchright timeouts to built-in TimeoutError
            # so tenacity can retry. BlockedException (subclass of Exception
            # defined in login.py) propagates WITHOUT retry.
            from .login import BlockedException as _BE
            if isinstance(e, _BE):
                raise
            msg = str(e).lower()
            if "timeout" in msg or "err_" in msg or "net::err" in msg:
                raise TimeoutError(str(e)) from e
            raise
    _wrapped()

log = logging.getLogger(__name__)

# --- Selectores estables (data-testid, componentkey, texto) ---
SEL_DESCRIPTION = '[data-testid="expandable-text-box"]'
SEL_DESCRIPTION_SECTION = '[componentkey^="JobDetails_AboutTheJob_"]'
SEL_SEE_MORE = '[data-testid="expandable-text-button"]'
SEL_COMPANY_LINK = 'a[href*="/company/"]'
SEL_APPLY_BUTTON = '[data-view-name="job-apply-button"]'

# --- Selectores del detalle GUEST (verificado contra HTML vivo 2026-08) ---
# La pagina guest usa jobs-guest-frontend (pageKey d_jobs_guest_details) con
# clases semanticas distintas a la app autenticada (flagship/voyager).
SEL_DESCRIPTION_GUEST = "section.core-section-container.description .show-more-less-html__markup"
SEL_DESCRIPTION_GUEST_ALT = ".show-more-less-html__markup"
SEL_SEE_MORE_GUEST = 'button.show-more-less-html__button--more'
SEL_TITLE_GUEST = ".top-card-layout__title"
SEL_COMPANY_GUEST = "a.topcard__org-name-link"
SEL_POSTED_GUEST = ".posted-time-ago__text"
SEL_APPLICANTS_GUEST = ".num-applicants__caption"
SEL_CRITERIA_ITEM_GUEST = ".description__job-criteria-item"
SEL_CRITERIA_SUBHEADER_GUEST = ".description__job-criteria-subheader"
SEL_CRITERIA_TEXT_GUEST = ".description__job-criteria-text"

# --- Detalle guest via API publica (sin login) ---
# Muchas paginas /jobs/view/<id> redirigen a /authwall para invitados, PERO el
# endpoint guest devuelve igualmente el HTML completo (descripcion + criterios +
# topcard). Se usa como via principal en modo invitado y se inyecta en la
# pagina con set_content() para reutilizar los mismos selectores.
GUEST_DETAIL_API = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
_GUEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def fetch_guest_job_html(job_id: str, timeout: int = 30) -> str:
    """Descarga el HTML del detalle guest (API publica). "" si falla."""
    if not job_id:
        return ""
    url = GUEST_DETAIL_API.format(job_id)
    req = urllib.request.Request(url, headers={
        "User-Agent": _GUEST_UA,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.6",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log.debug("fetch_guest_job_html(%s) fallo: %s", job_id, e)
        return ""

# Textos para identificar chips y spans por contenido (no por clase ofuscada)
WORK_MODE_TEXTS = {"Híbrido", "Presencial", "Remoto", "En remoto", "Hibrido",
                   "Hybrid", "On-site", "Remote"}
EMP_TYPE_TEXTS = {"Jornada completa", "Tiempo completo", "Media jornada",
                  "Contrato", "Prácticas", "Pasantía", "Becario",
                  "Full-time", "Part-time", "Contract", "Internship",
                  "Temporal", "Voluntario"}
EXP_LEVEL_TEXTS = {"Entry", "Associate", "Mid-Senior", "Director", "Executive",
                   "Prácticas", "Becario"}

_RE_NUM_APPLICANTS = re.compile(
    r"(?:Más de\s+|Over\s+)?([\d.,]+)\s*(?:solicitudes|candidatos|aplicaciones|"
    r"applicants|applications)", re.IGNORECASE,
)
_RE_POSTED_RELATIVE = re.compile(
    r"(?:hace\s+\d+\s+(?:d[ií]as?|horas?|semanas?|meses?|a[ñn]os?)|"
    r"\d+\s+(?:days?|hours?|weeks?|months?|years?)\s+ago)",
    re.IGNORECASE,
)
# Regex de salario: exige simbolo de moneda O palabra de moneda adyacente al
# rango numerico para reducir falsos positivos (rangos numericos que no son
# salario: frequencia, presupuesto, años de experiencia, tamaño de equipo).
# Periodo es opcional (bonus): si aparece se extrae; si no, el salario solo
# cita un numero con moneda (caso comun "45.000€"). Grupos con nombre:
#   cur1   = simbolo $/€/£ (variante A, moneda PREFIJA)
#   n1A,n2A, perA (variante A)
#   n1B,n2B, currB, perB (variante B, moneda SUFIJA como palabra)
# Nota: char class [...]corta ([-–]+) sin 'a' para no matchear palabras tipo
# "Media jornada completa" que contienen "a".
_RE_SALARY_IN_TEXT = re.compile(
    r"(?:"
    # Variante A: simbolo de moneda precede al rango (€40.000 / $50k-70k / $17.00/hora)
    r"(?P<cur1>[\$€£])\s*(?P<n1A>[\d.,]+)\s*[kK]?\s*"
    r"(?:[-–]+\s*[\$€£]?\s*(?P<n2A>[\d.,]+)\s*[kK]?)?\s*"
    r"(?:[\s/·]*(?:al\s+|por\s+)?(?P<perA>a[ñn]o|year|yr|mes|month|mo|hora|hour|hr)s?)?"
    r"|"
    # Variante B: rango + palabra de moneda sufija [+ periodo opcional].
    # Cubre "40.000-55.000 euros/año", "30000-40000 EUR / mes", "45.000€ anual".
    r"(?P<n1B>[\d.,]+)\s*[kK]?\s*"
    r"(?:[-–]+\s*(?P<n2B>[\d.,]+)\s*[kK]?)?\s*"
    r"(?P<currB>euros?|€|EUR|dolares?|dólares?|USD|\$|libras?|£|GBP)\s*"
    r"(?:[\s/·]*(?:al\s+|por\s+)?(?P<perB>a[ñn]o|year|yr|mes|month|mo|hora|hour|hr)s?)?"
    r")",
    re.IGNORECASE,
)


def _check_block(page) -> None:
    if _is_challenge_url(page.url) or _detect_recaptcha(page):
        raise BlockedException(f"Challenge en detalle {page.url}")


def _text(el) -> str:
    if not el:
        return ""
    try:
        return (el.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        try:
            return (el.text_content() or "").strip()
        except Exception:  # noqa: BLE001
            return ""


def _normalize_work_mode(text: str) -> str:
    t = text.lower().strip()
    if "híbrido" in t or "hibrido" in t or "hybrid" in t:
        return "Hibrido"
    if "presencial" in t or "on-site" in t or "onsite" in t:
        return "Presencial"
    if "remoto" in t or "remote" in t or "distancia" in t:
        return "Remoto"
    return text.strip()


# El detalle invitado NO trae un campo de modalidad (los criterios solo tienen
# nivel, tipo de empleo y sector). Cuando la descripcion la menciona, se infiere
# de forma conservadora: primero senales fuertes de remoto, luego hibrido, luego
# remoto generico y por ultimo presencial.
_RE_WM_REMOTE_STRONG = re.compile(
    r"(100\s*%\s*remoto|totalmente remoto|fully remote|100%\s*remote|"
    r"trabajo\s+(?:en\s+|100%\s+)?remoto|teletrabajo|remote\s+work|"
    r"work\s+from\s+home|home\s+office)",
    re.IGNORECASE,
)
_RE_WM_HYBRID = re.compile(r"(h[ií]brido|\bhybrid\b)", re.IGNORECASE)
_RE_WM_REMOTE = re.compile(r"(\bremoto\b|\bremote\b|\bteletrabajo\b)", re.IGNORECASE)
_RE_WM_ONSITE = re.compile(r"(\bpresencial\b|on-?site)", re.IGNORECASE)


def _infer_work_mode(text: str) -> str:
    """Infiere la modalidad a partir de la descripcion. "" si no hay senal."""
    if not text:
        return ""
    if _RE_WM_REMOTE_STRONG.search(text):
        return "Remoto"
    if _RE_WM_HYBRID.search(text):
        return "Hibrido"
    if _RE_WM_REMOTE.search(text):
        return "Remoto"
    if _RE_WM_ONSITE.search(text):
        return "Presencial"
    return ""


def _normalize_emp_type(text: str) -> str:
    t = text.lower().strip()
    if "jornada completa" in t or "tiempo completo" in t or "full-time" in t:
        return "Jornada completa"
    if "media jornada" in t or "part-time" in t or "media" in t:
        return "Media jornada"
    if "prácticas" in t or "practicas" in t or "internship" in t or "becario" in t:
        return "Practicas"
    if "contrato" in t or "contract" in t or "temporal" in t:
        return "Contrato"
    return text.strip()


def _expand_description(page) -> None:
    """Click en 'Show more' / '...mas' si existe, para expandir la descripcion."""
    for sel in (SEL_SEE_MORE_GUEST, SEL_SEE_MORE):
        try:
            btn = page.query_selector(sel)
            if btn:
                try:
                    if btn.is_visible():
                        btn.click(timeout=5000)
                        delay((0.8, 1.5), "post-see-more")
                        return
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            log.debug("No se pudo expandir descripcion (%s): %s", sel, e)


def _get_description(page) -> str:
    """Extrae el texto completo de la descripcion (guest o auth)."""
    for sel in (SEL_DESCRIPTION_GUEST, SEL_DESCRIPTION_GUEST_ALT, SEL_DESCRIPTION):
        el = page.query_selector(sel)
        if el:
            txt = _text(el)
            if txt:
                return txt
    # Fallback auth: buscar por componentkey
    section = page.query_selector(SEL_DESCRIPTION_SECTION)
    if section:
        el = section.query_selector("span, div, p")
        if el:
            return _text(el)
    return ""


def _extract_and_segment_description(page, job: JobOffer) -> None:
    """Segmenta la descripcion en role_summary, responsibilities, requirements y benefits.

    Usa el inner HTML del bloque de descripcion (guest .show-more-less-html__markup
    o auth [data-testid=expandable-text-box]) para detectar cabeceras en
    <strong>/<h3> y dividir el contenido.
    """
    el = None
    for sel in (SEL_DESCRIPTION_GUEST, SEL_DESCRIPTION_GUEST_ALT, SEL_DESCRIPTION):
        el = page.query_selector(sel)
        if el:
            break
    if not el:
        return
    try:
        inner_html = el.inner_html()
    except Exception:  # noqa: BLE001
        return
    if not inner_html:
        return
    sections = split_description_sections(inner_html, source="html")
    job.role_summary = sections.get("role_summary", "")
    job.company_description = sections.get("company_description", "")
    job.responsibilities = sections.get("responsibilities", "")
    job.requirements = sections.get("requirements", "")
    job.benefits = sections.get("benefits", "")


def _collect_chips(page) -> tuple[str, str, str]:
    """Lee modo de trabajo, tipo de empleo y nivel (guest criterios + auth chips).

    Guest: criterios .description__job-criteria-item (subheader -> texto).
    Auth (legacy): chips <a> con texto Híbrido/Jornada completa/etc.
    Devuelve (work_mode, employment_type, experience_level).
    """
    work_mode = ""
    emp_type = ""
    exp_level = ""

    # Guest: criterios estructurados (subheader -> texto).
    try:
        items = page.query_selector_all(SEL_CRITERIA_ITEM_GUEST)
        for it in items:
            sub = _text(it.query_selector(SEL_CRITERIA_SUBHEADER_GUEST)).strip().lower()
            val = _text(it.query_selector(SEL_CRITERIA_TEXT_GUEST)).strip()
            if not sub or not val:
                continue
            if "seniority" in sub or "nivel" in sub:
                if not exp_level:
                    exp_level = normalize_experience_level(val)
            elif "employment type" in sub or "tipo de empleo" in sub or "jornada" in sub:
                if not emp_type:
                    emp_type = _normalize_emp_type(val)
            elif "work" in sub or "modalidad" in sub:
                if not work_mode:
                    work_mode = _normalize_work_mode(val)
        if work_mode or emp_type or exp_level:
            return work_mode, emp_type, exp_level
    except Exception:  # noqa: BLE001
        pass

    # Auth legacy: chips como <a href*="/jobs/view/"> con texto corto.
    try:
        links = page.query_selector_all("a[href*='/jobs/view/']")
        for link in links:
            txt = _text(link).strip()
            if not txt or len(txt) > 30:
                continue
            if txt in WORK_MODE_TEXTS and not work_mode:
                work_mode = _normalize_work_mode(txt)
            elif txt in EMP_TYPE_TEXTS and not emp_type:
                emp_type = _normalize_emp_type(txt)
            elif txt in EXP_LEVEL_TEXTS and not exp_level:
                exp_level = normalize_experience_level(txt)
            if work_mode and emp_type and exp_level:
                break
    except Exception:  # noqa: BLE001
        pass
    return work_mode, emp_type, exp_level


def _collect_header_meta(page) -> tuple[str, str, Optional[int]]:
    """Lee los spans del header <p>: ubicacion, fecha relativa, solicitantes.

    Los spans comparten clase ofuscada; se diferencian por texto.
    Devuelve (posted_relative, location_text, num_applicants).
    """
    posted_rel = ""
    applicants = None
    location = ""
    # Guest: selectores dedicados para fecha y solicitantes.
    try:
        posted = page.query_selector(SEL_POSTED_GUEST)
        if posted:
            posted_rel = _text(posted).strip()
        app = page.query_selector(SEL_APPLICANTS_GUEST)
        if app:
            m = _RE_NUM_APPLICANTS.search(_text(app))
            if m:
                try:
                    applicants = int(m.group(1).replace(".", "").replace(",", ""))
                except ValueError:
                    pass
    except Exception:  # noqa: BLE001
        pass
    if posted_rel or applicants is not None:
        return posted_rel, location, applicants
    # Fallback auth: spans genericos del header <p>.
    try:
        spans = page.query_selector_all("span")
        for sp in spans:
            txt = _text(sp).strip()
            if not txt or len(txt) > 80:
                continue
            # Fecha relativa "hace X dias"
            if not posted_rel and _RE_POSTED_RELATIVE.search(txt):
                posted_rel = txt
            # Numero de solicitantes
            if applicants is None:
                m = _RE_NUM_APPLICANTS.search(txt)
                if m:
                    try:
                        applicants = int(m.group(1).replace(".", "").replace(",", ""))
                    except ValueError:
                        pass
            # Ubicacion (contiene "España" o "Spain" o tiene comas)
            if not location and ("España" in txt or "Spain" in txt) and "," in txt:
                location = txt
    except Exception:  # noqa: BLE001
        pass
    return posted_rel, location, applicants


def _collect_company(page) -> tuple[str, str, str]:
    """Devuelve (company_url, company_industry, company_size).

    industry y size NO estan en el HTML estatico (SDUI sin hidratar).
    company_url se extrae del link a /company/<slug>/life/.
    """
    company_url = ""
    industry = ""
    size = ""
    # Guest: enlace de empresa dedicado (topcard__org-name-link).
    try:
        comp = page.query_selector(SEL_COMPANY_GUEST)
        if comp:
            href = comp.get_attribute("href") or ""
            company_url = href.split("?")[0]
            if company_url.endswith("/life/"):
                company_url = company_url[:-5]
    except Exception:  # noqa: BLE001
        pass
    # Guest: industria/tamano desde los criterios estructurados.
    try:
        items = page.query_selector_all(SEL_CRITERIA_ITEM_GUEST)
        for it in items:
            sub = _text(it.query_selector(SEL_CRITERIA_SUBHEADER_GUEST)).strip().lower()
            val = _text(it.query_selector(SEL_CRITERIA_TEXT_GUEST)).strip()
            if not sub or not val:
                continue
            if "industr" in sub and not industry:
                industry = val
            elif "size" in sub or "tamano" in sub or "tamaño" in sub:
                if not size:
                    size = val
        if company_url and (industry or size):
            return company_url, industry, size
    except Exception:  # noqa: BLE001
        pass
    # Fallback auth: primer /company/ del card.
    if not company_url:
        try:
            links = page.query_selector_all(SEL_COMPANY_LINK)
            for link in links:
                href = link.get_attribute("href") or ""
                if "/company/" in href:
                    company_url = href.split("?")[0]
                    if company_url.endswith("/life/"):
                        company_url = company_url[:-5]
                    break
        except Exception:  # noqa: BLE001
            pass
    # industry y size: intentar esperar a que el SDUI hidrate (best-effort)
    try:
        about_company = page.query_selector(
            '[componentkey^="JobDetails_AboutTheCompany_"]'
        )
        if about_company:
            # Esperar a que JS llene el componente (SDUI): buscar un subelemento
            # tipico (entity caption / descripcion) con timeout razonable.
            # Si no aparece (red lenta / sin hidratacion), seguimos best-effort.
            try:
                page.wait_for_selector(
                    '[componentkey^="JobDetails_AboutTheCompany_"] '
                    '[class*="entity"], '
                    '[componentkey^="JobDetails_AboutTheCompany_"] p',
                    timeout=8000, state="attached",
                )
            except Exception:  # noqa: BLE001
                log.debug("AboutTheCompany no hidrato en 8s; seguimos best-effort.")
            # Buscar texto de industria/tamano dentro del componente
            txt = _text(about_company)
            # Patrones comunes: "Sector: X", "X empleados", "1,001-5,000 employees"
            m_ind = re.search(r"(?:Sector|Industria|Industry)[:\s]+([^\n,]+)", txt, re.I)
            if m_ind:
                industry = m_ind.group(1).strip()
            m_size = re.search(r"([\d.,]+(?:\-|–)[\d.,]+)\s*(?:empleados|employees)", txt, re.I)
            if m_size:
                size = m_size.group(0).strip()
    except Exception:  # noqa: BLE001
        pass
    return company_url, industry, size


def _extract_salary_from_description(desc: str) -> tuple[str, Optional[float],
                                                          Optional[float], str, str]:
    """Intenta extraer salario del texto de la descripcion (best-effort).

    La regex _RE_SALARY_IN_TEXT tiene dos variantes:
      A: simbolo de moneda precede al rango (grupos cur1/n1A/n2A/perA).
      B: rango seguido de palabra de moneda (grupos n1B/n2B/currB/perB).
    Solo una variante tiene grupos rellenos; la otra es None.
    """
    if not desc:
        return "", None, None, "", ""
    m = _RE_SALARY_IN_TEXT.search(desc)
    if not m:
        return "", None, None, "", ""
    groups = m.groupdict()
    # Determinar variante activa
    if groups.get("cur1"):
        # Variante A: simbolo + rango
        currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(groups["cur1"], "")
        n1, n2 = groups.get("n1A"), groups.get("n2A")
        period_word = groups.get("perA") or ""
    else:
        # Variante B: rango + palabra de moneda
        cur_word = groups.get("currB") or ""
        n1, n2 = groups.get("n1B"), groups.get("n2B")
        period_word = groups.get("perB") or ""
        cw = cur_word.lower()
        if "euro" in cw or cw == "€":
            currency = "EUR"
        elif "dolar" in cw or cw == "usd" or cw == "$":
            currency = "USD"
        elif "libra" in cw or cw == "gbp" or cw == "£":
            currency = "GBP"
        else:
            currency = cur_word.upper()

    def _to_float(s):
        """Heuristica numerica: distingue separador de miles de decimal segun
        posicion y agrupacion de digitos.
          "40.000"   -> 40000.0 (ES: punto = miles, 3 digitos tras punto)
          "17.00"    -> 17.0    (US: punto = decimal, 2 digitos)
          "80,000"   -> 80000.0 (US: coma = miles)
          "1.234,56" -> 1234.56 (ES: . miles, , decimal)
          "1,234.56" -> 1234.56 (US: , miles, . decimal)
          "1.000.000"-> 1000000.0 (multiples puntos = miles)
        """
        if not s:
            return None
        s = s.strip()
        if not s:
            return None
        has_dot = "." in s
        has_comma = "," in s
        try:
            if has_dot and has_comma:
                last_dot = s.rfind(".")
                last_comma = s.rfind(",")
                if last_dot > last_comma:
                    # US: 1,234.56 -> coma miles, punto decimal
                    return float(s.replace(",", ""))
                # ES: 1.234,56 -> punto miles, coma decimal
                return float(s.replace(".", "").replace(",", "."))
            if has_dot:
                # Solo punto: si hay varios, son miles (1.000.000)
                if s.count(".") > 1:
                    return float(s.replace(".", ""))
                # Un solo punto: si tras el hay exactamente 3 digitos se
                # interpreta como miles (40.000 -> 40000); si 1-2 -> decimal.
                parts = s.split(".")
                if len(parts) == 2 and len(parts[1]) == 3 \
                        and parts[0].strip().isdigit():
                    return float(parts[0] + parts[1])
                return float(s)
            if has_comma:
                if s.count(",") > 1:
                    return float(s.replace(",", ""))
                parts = s.split(",")
                if len(parts) == 2 and len(parts[1]) == 3 \
                        and parts[0].strip().isdigit():
                    return float(parts[0] + parts[1])
                return float(s.replace(",", "."))
            return float(s)
        except ValueError:
            return None

    smin = _to_float(n1)
    smax = _to_float(n2)
    period = ""
    if period_word:
        pw = period_word.lower().rstrip("s")
        if pw in {"ano", "año", "year", "yr"}:
            period = "year"
        elif pw in {"me", "mes", "month", "mo"}:
            period = "month"
        elif pw in {"hora", "hour", "hr"}:
            period = "hour"
    return m.group(0).strip(), smin, smax, currency, period


_RE_SKILLS_HEADER = re.compile(
    r"(?:Competencias|Habilidades|Skills|Requisitos tecnicos|Tech stack)"
    r"\s*[:：\-]?\s*\n+((?:[\t *•\-]\s*.+\s*\n?)+)",
    re.IGNORECASE,
)
_RE_SKILLS_LINE = re.compile(r"^\s*[\t *•\-]+\s*(.+?)\s*$",
                             re.IGNORECASE | re.MULTILINE)


def _collect_skills(page, desc_text: str) -> list[str]:
    """Extrae skills de la oferta.

    Orden de intentos:
      1. Componente SDUI [componentkey*="JobDetails_Skills_"] (si hidratado).
      2. Keyword matching (\bkeyword\b) sobre descripcion usando
         DATA_SKILLS_KEYWORDS (lista curada de ~60 tecnologias Data).
      3. Regex sobre seccion "Skills:/Competencias:/Habilidades:" con bullets.
    Los metodos 2 y 3 son sobre la descripcion; no requieren navegador extra.
    """
    skills: list[str] = []
    # 1. Componente SDUI (estructurado, si LinkedIn lo hidrato)
    try:
        comp = page.query_selector('[componentkey*="JobDetails_Skills_"]')
        if comp:
            items = comp.query_selector_all("li, a[href*='/skills/'], span")
            for it in items:
                txt = _text(it)
                if txt and 1 < len(txt) <= 80 and not txt.isdigit():
                    if txt not in skills:
                        skills.append(txt)
    except Exception:  # noqa: BLE001
        pass
    if skills:
        return skills

    if not desc_text:
        return skills

    # 2. Keyword matching (metodo principal: recorre la descripcion buscando
    #    tecnologias de la lista curada con limites de palabra \bkeyword\b)
    import re as _re
    desc_lower = desc_text.lower()
    for kw in DATA_SKILLS_KEYWORDS:
        # Construir patron con \b para limite de palabra (evita falsos
        # positivos como "Python" dentro de "Cython" o "SQL" en "PostgreSQL").
        # Para keywords con espacios, escapamos y usamos \b en ambos extremos.
        kw_escaped = _re.escape(kw)
        pattern = _re.compile(r"\b" + kw_escaped + r"\b", _re.IGNORECASE)
        if pattern.search(desc_lower):
            # Normalizar nombre: algunas keywords tienen alias (PowerBI -> Power BI)
            normalized = kw
            if kw == "PowerBI":
                normalized = "Power BI"
            elif kw == "Scikit learn":
                normalized = "Scikit-learn"
            elif kw == "Google Cloud":
                normalized = "GCP"
            if normalized not in skills:
                skills.append(normalized)
        if len(skills) >= 40:  # safety cap (evita spam)
            break

    if skills:
        return skills

    # 3. Fallback: regex sobre seccion "Skills:/Competencias:/..." con bullets
    m = _RE_SKILLS_HEADER.search(desc_text)
    if not m:
        return skills
    block = m.group(1)
    for line in block.splitlines():
        lm = _RE_SKILLS_LINE.match(line)
        if not lm:
            continue
        s = lm.group(1).strip().rstrip(",;|")
        if not s or len(s) > 80 or s.isdigit():
            continue
        if s in skills:
            continue
        skills.append(s)
        if len(skills) >= 30:
            break
    return skills


def _extract_from_page(page, job: JobOffer) -> JobOffer:
    """Extrae todos los campos de detalle desde la pagina/contenido actual.

    No navega: asume que `page` ya muestra el detalle (bien por goto, bien por
    set_content del HTML de la API guest).
    """
    # Descripcion: texto plano + segmentacion por secciones logicas
    job.description_full = _get_description(page)
    _extract_and_segment_description(page, job)

    # Chips: modo de trabajo, tipo de empleo y nivel de experiencia
    work_mode, emp_type, exp_level = _collect_chips(page)
    # NO sobreescribir work_mode si ya viene del SERP y el detalle no lo encuentra
    if work_mode:
        job.work_mode = work_mode
    if emp_type:
        job.employment_type = emp_type
    if exp_level:
        job.experience_level = exp_level

    # Modalidad: los criterios guest no la traen; si la descripcion la menciona,
    # inferirla (Remoto/Hibrido/Presencial).
    if not job.work_mode and job.description_full:
        inferred = _infer_work_mode(job.description_full)
        if inferred:
            job.work_mode = inferred

    # Header metadata: fecha, solicitantes
    posted_rel, _location, applicants = _collect_header_meta(page)
    if posted_rel:
        job.posted_relative = posted_rel
    if applicants is not None:
        job.num_applicants = applicants

    # Empresa
    company_url, industry, size = _collect_company(page)
    if company_url:
        job.company_url = company_url
    if industry:
        job.company_industry = industry
    if size:
        job.company_size = size

    # Salario: buscar en la descripcion (no hay campo estructurado)
    if job.description_full:
        sal_raw, smin, smax, cur, per = _extract_salary_from_description(
            job.description_full
        )
        if sal_raw:
            job.salary_raw = sal_raw
            job.salary_min = smin
            job.salary_max = smax
            job.salary_currency = cur
            job.salary_period = per

    # Skills: intentar componente SDUI JobDetails_Skills; fallback a regex de desc.
    if not job.skills:
        job.skills = _collect_skills(page, job.description_full)

    log.debug("Detalle ok: desc=%d chars salario=%s modo=%s tipo=%s exp=%s skills=%d",
              len(job.description_full), bool(job.salary_raw),
              job.work_mode, job.employment_type, job.experience_level,
              len(job.skills))
    return job


def parse_detail(page, job: JobOffer, config: dict[str, Any]) -> JobOffer:
    """Rellena los campos de detalle de una oferta.

    Modo invitado (config["_guest"]): usa la API publica de detalle (evita el
    /authwall al que redirige la pagina /jobs/view/<id> para invitados) y
    reutiliza los selectores guest via page.set_content().
    Modo normal: navega a job.job_url y parsea la pagina autenticada/guest.
    Modifica job in-place. Lanza BlockedException si aparece challenge.
    """
    log.info("Detalle: [%s] %s @ %s", job.job_id, job.title[:40], job.company_name)

    if config.get("_guest"):
        html = fetch_guest_job_html(job.job_id)
        if html:
            try:
                page.set_content(html)
                delay((0.3, 0.9), "post-guest-api-content")
                return _extract_from_page(page, job)
            except Exception as e:  # noqa: BLE001
                log.debug("set_content fallo para %s (%s); navegando directo.",
                          job.job_id, e)
        else:
            log.debug("API guest sin HTML para %s; navegando directo.", job.job_id)

    delays = config.get("delays", {})
    step_px = tuple(delays.get("scroll_step_px", [200, 400]))
    pause_rng = tuple(delays.get("between_scrolls", [1.5, 3.5]))

    # Goto + networkidle con reintentos (tenacity si esta disponible).
    # BlockedException NO se reintenta: propaga a main.py para conmutar a Apify.
    try:
        _goto_with_retry(page, job.job_url)
    except TimeoutError as e:
        # Reintentos agotados: logear y dejar que parse_detail siga best-effort.
        log.warning("Detalle %s: goto agoto reintentos: %s", job.job_id, e)
    delay((2.0, 4.0), "post-goto-detail")
    mouse_jitter(page, n=1)
    _check_block(page)

    # Expandir descripcion y scrollear lento
    _expand_description(page)
    scroll_slow(page, step_px_rng=step_px, max_steps=8, pause_rng=pause_rng)
    _check_block(page)

    return _extract_from_page(page, job)
