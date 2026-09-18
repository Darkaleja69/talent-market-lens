"""Parseo de la pagina de detalle de jobs.ch.

Estrategia (orden de preferencia):
  1. JSON-LD JobPosting de la pagina (descripcion completa, empresa,
     tipo de contrato, ubicacion, industria, beneficios, salario...).
  2. Secciones de la descripcion (responsabilidades / requisitos /
     beneficios) detectadas por cabeceras en el HTML de la descripcion.
  3. DOM renderizado como fallback.
"""
from __future__ import annotations

import html as html_mod
import logging
import re
import time
from typing import Optional

from src.core.normalize import as_text, parse_salary

from .serp import extract_json_ld

log = logging.getLogger(__name__)

_RE_HEADING = re.compile(
    r"<(h[1-4]|strong|b)(?:\s[^>]*)?>(.*?)</\1>", re.DOTALL | re.IGNORECASE
)

_KEYWORDS_REQUIREMENTS = [
    "anforderung", "anforderungen", "qualifikation", "qualifications",
    "requirements", "your profile", "ihr profil", "votre profil", "profil",
    "what you bring", "what we are looking for", "skills", "voraussetzungen",
    "exigences", "profil recherché", "erfahrung",
]
_KEYWORDS_REQUIREMENTS_GENERIC = ["kompetenzen", "compétence", "compétences"]

_KEYWORDS_RESPONSIBILITIES = [
    "aufgaben", "ihre aufgaben", "deine aufgaben", "dein wirkungsfeld",
    "responsibilities", "responsable", "missions", "your role",
    "what you will do", "what you'll do", "tâches", "vos tâches",
    "the role", "about the role", "votre mission",
]
_KEYWORDS_RESPONSIBILITIES_GENERIC = ["description", "job description"]

_KEYWORDS_BENEFITS = [
    "benefits", "vorteile", "avantages", "what we offer", "we offer",
    "das bieten wir", "perks", "what's in it for you",
]
_KEYWORDS_BENEFITS_GENERIC = ["unser angebot", "offer", "package"]

# Modo de trabajo derivado del texto (jobs.ch no lo expone estructurado).
# "Homeoffice"/"home office" se interpreta como trabajo desde casa (Remote).
# El hibrido explicito gana para no perder esa distincion.
_RE_WORK_HYBRID = re.compile(
    r"\bhybrid\b|\bhybride\b|h[íi]brid[oa]|hibrid[eo]|semipresencial",
    re.IGNORECASE,
)
_RE_WORK_REMOTE = re.compile(
    r"\bremote\b|remoto|remota|teletrabajo|thuiswerk|telewerk|"
    r"home[\s-]?office|homeoffice|work\s+from\s+home|\bwfh\b|"
    r"fully\s+remote|100\s*%\s*(?:remote|home)",
    re.IGNORECASE,
)
_RE_WORK_ONSITE = re.compile(
    r"\bonsite\b|on[\s-]?site|presencial|in[\s-]?person|in[\s-]?office|"
    r"vor\s+ort|work\s+from\s+office",
    re.IGNORECASE,
)


def _find_work_mode(text: str) -> str:
    """Clasifica el modo de trabajo desde texto libre (Hybrid/Remote/On-site)."""
    if not text:
        return ""
    if _RE_WORK_HYBRID.search(text):
        return "Hybrid"
    if _RE_WORK_REMOTE.search(text):
        return "Remote"
    if _RE_WORK_ONSITE.search(text):
        return "On-site"
    return ""


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


def _html_to_text(raw: str) -> str:
    """Convierte HTML a texto plano legible."""
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html_mod.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def _split_description(html_str: str) -> list[tuple[str, str]]:
    """Divide la descripcion HTML en (cabecera, contenido) por etiquetas."""
    matches = list(_RE_HEADING.finditer(html_str))
    sections = []
    for idx, m in enumerate(matches):
        heading = _html_to_text(m.group(2)).strip()
        if not heading:
            continue
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(html_str)
        body = _html_to_text(html_str[start:end]).strip()
        sections.append((heading, body))
    return sections


def _match_section(sections: list[tuple[str, str]],
                   keywords: list[str]) -> str:
    """Devuelve el contenido de la primera seccion cuya cabecera matchea."""
    for heading, body in sections:
        low = heading.lower()
        if any(k in low for k in keywords):
            if body:
                return body
    return ""


def parse_detail(page, job: dict) -> dict:
    """Visita la pagina de detalle y extrae datos adicionales."""
    result: dict = {}
    url = job.get("job_url", "")
    if not url:
        return result

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            break
        except Exception as e:
            if attempt < max_retries:
                time.sleep(3 * attempt)
                log.debug("Retry %d/%d detail %s: %s",
                          attempt, max_retries, job.get("job_id"), e)
            else:
                log.warning("Error navegando a detalle %s: %s",
                            job.get("job_id"), e)
                return result

    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        pass

    try:
        html_str = page.content()
    except Exception:
        html_str = ""

    jp = _find_jobposting(html_str)
    if jp:
        _parse_json_ld(jp, result)

    if not result.get("description_full"):
        _parse_dom(page, result)

    if not result.get("salary_raw"):
        sal = _find_salary_in_page(page, html_str)
        if sal:
            result["salary_raw"] = sal

    # Modo de trabajo desde el texto (el JSON-LD de jobs.ch no lo expone).
    if not result.get("work_mode"):
        wm = _find_work_mode(" ".join(filter(None, [
            result.get("title", ""),
            result.get("description_full", ""),
            result.get("requirements", ""),
            result.get("responsibilities", ""),
            result.get("benefits", ""),
        ])))
        if wm:
            result["work_mode"] = wm

    return result


def _find_jobposting(html_str: str) -> Optional[dict]:
    for b in extract_json_ld(html_str):
        if (b or {}).get("@type") == "JobPosting":
            return b
    return None


def _parse_json_ld(jp: dict, result: dict) -> None:
    desc_html = jp.get("description", "") or ""
    if desc_html:
        result["description_full"] = _html_to_text(desc_html)
        sections = _split_description(desc_html)
        if sections:
            result["requirements"] = _match_section(
                sections, _KEYWORDS_REQUIREMENTS) or _match_section(
                sections, _KEYWORDS_REQUIREMENTS_GENERIC)
            result["responsibilities"] = _match_section(
                sections, _KEYWORDS_RESPONSIBILITIES) or _match_section(
                sections, _KEYWORDS_RESPONSIBILITIES_GENERIC)
            result["benefits"] = _match_section(
                sections, _KEYWORDS_BENEFITS) or _match_section(
                sections, _KEYWORDS_BENEFITS_GENERIC)
            if not result.get("benefits"):
                raw_benefits = jp.get("jobBenefits")
                if isinstance(raw_benefits, list) and raw_benefits:
                    result["benefits"] = "\n".join(
                        str(b) for b in raw_benefits if str(b).strip())

    if jp.get("employerOverview"):
        result["company_description"] = _html_to_text(
            str(jp["employerOverview"]))

    if jp.get("title") and not result.get("title"):
        result["title"] = jp["title"]

    if jp.get("datePosted"):
        result["posted_datetime"] = jp["datePosted"]

    if jp.get("employmentType"):
        result["employment_type"] = as_text(jp["employmentType"])

    if jp.get("workHours"):
        result["workload_pct"] = str(jp["workHours"])

    if jp.get("industry"):
        result["company_industry"] = str(jp["industry"])

    org = jp.get("hiringOrganization") or {}
    if isinstance(org, dict):
        if org.get("sameAs") and not result.get("company_url"):
            result["company_url"] = org["sameAs"]

    addr = (jp.get("jobLocation") or {}).get("address") or {}
    if isinstance(addr, dict):
        parts = [addr.get("streetAddress"), addr.get("addressRegion"),
                 addr.get("postalCode")]
        parts = [str(p).strip() for p in parts if p and str(p).strip()]
        if parts and not result.get("location_raw"):
            result["location_raw"] = ", ".join(parts)

    salary = jp.get("baseSalary") or {}
    if isinstance(salary, dict):
        currency = salary.get("currency", "")
        value = salary.get("value") or {}
        if isinstance(value, dict) and value.get("minValue") is not None:
            result["salary_min"] = value.get("minValue")
            result["salary_max"] = value.get("maxValue")
            result["salary_currency"] = currency or "CHF"
            result["salary_period"] = "YEARLY"
            result["salary_raw"] = _format_salary(result, currency)


def _format_salary(result: dict, currency: str) -> str:
    mn, mx = result.get("salary_min"), result.get("salary_max")
    if mn is None:
        return ""
    cur = currency or "CHF"
    if mx is not None:
        return f"{cur} {int(mn):,} - {int(mx):,}"
    return f"{cur} {int(mn):,}"


def _parse_dom(page, result: dict) -> None:
    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        pass

    desc_el = (page.query_selector("[class*='description']") or
               page.query_selector("[class*='Detail']") or
               page.query_selector("article") or
               page.query_selector("main"))
    if desc_el:
        result["description_full"] = _text(desc_el)

    for field, keywords, generic in (
        ("requirements", _KEYWORDS_REQUIREMENTS, _KEYWORDS_REQUIREMENTS_GENERIC),
        ("responsibilities", _KEYWORDS_RESPONSIBILITIES,
         _KEYWORDS_RESPONSIBILITIES_GENERIC),
        ("benefits", _KEYWORDS_BENEFITS, _KEYWORDS_BENEFITS_GENERIC),
    ):
        if not result.get(field):
            val = _extract_section_dom(page, keywords) or \
                  _extract_section_dom(page, generic)
            if val:
                result[field] = val


def _extract_section_dom(page, keywords: list[str]) -> str:
    for tag in ["h2", "h3", "h4", "strong"]:
        headings = page.query_selector_all(tag)
        for h in headings:
            txt = _text(h).lower()
            if any(k in txt for k in keywords):
                content = page.evaluate("""
                    (el) => {
                        let next = el.nextElementSibling;
                        let text = [];
                        while (next && !['H1','H2','H3','H4'].includes(next.tagName)) {
                            text.push(next.innerText);
                            next = next.nextElementSibling;
                        }
                        return text.join('\\n');
                    }
                """, h)
                if content and content.strip():
                    return content.strip()
    return ""


def _find_salary_in_page(page, html_str: str = "") -> Optional[str]:
    """Busca un salario en el texto de la pagina (fallback del JSON-LD).

    Solo se acepta si se parsea a un importe plausible (>= 1000), para no
    capturar numeros sueltos del chrome/estimadores de la pagina.
    """
    patterns = [
        r"(?:CHF|EUR)\s*\d[\d'\u2019.]*(?:\s*[-–]\s*(?:CHF|EUR)?\s*\d[\d'\u2019.]*)?",
        r"\d[\d'\u2019.]*\s*(?:CHF|EUR)",
    ]
    body_text = _text(page.query_selector("body")) or ""
    for source in (body_text, html_str):
        if not source:
            continue
        for pat in patterns:
            for m in re.finditer(pat, source, re.IGNORECASE):
                candidate = m.group(0).strip()
                parsed = parse_salary(candidate, default_currency="CHF")
                if (parsed["salary_min"] or 0) >= 1000 or \
                        (parsed["salary_max"] or 0) >= 1000:
                    return candidate
    return None
