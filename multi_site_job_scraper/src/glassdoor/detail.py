"""Parseo de la pagina de detalle de Glassdoor (Playwright).

Estrategia:
  1. JSON-LD JobPosting de la pagina (descripcion, salario, empresa).
  2. DOM renderizado como fallback.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

log = logging.getLogger(__name__)

_RE_LD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


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


def _extract_json_ld(html_str: str) -> list:
    blocks = []
    for m in _RE_LD.finditer(html_str or ""):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            blocks.extend(data)
        else:
            blocks.append(data)
    return blocks


def dismiss_sign_in_wall(page) -> bool:
    """Intenta cerrar el modal de inicio de sesion de Glassdoor."""
    try:
        close_btn = page.query_selector(
            'button[aria-label="Close"], [data-test="gdpr-close"], '
            'button[class*="close"], .modal_close, '
            '[data-test="close-button"]'
        )
        if close_btn:
            close_btn.click(timeout=3000)
            time.sleep(0.5)
            return True
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        return True
    except Exception:
        pass
    return False


def parse_detail(page, job: dict) -> dict:
    """Visita la pagina de detalle y extrae datos adicionales."""
    result: dict = {}
    url = job.get("job_url", "")
    if not url:
        return result

    max_retries = 2
    for attempt in range(1, max_retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            break
        except Exception as e:
            if attempt < max_retries:
                wait = 3 * attempt
                log.debug("Retry %d/%d detail %s (%.1fs): %s",
                          attempt, max_retries, job.get("job_id"), wait, e)
                time.sleep(wait)
            else:
                log.warning("Error navegando a detalle %s: %s",
                            job.get("job_id"), e)
                return result

    dismiss_sign_in_wall(page)
    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        pass

    try:
        html_str = page.content()
    except Exception:
        html_str = ""

    # 1) JSON-LD
    for block in _extract_json_ld(html_str):
        if (block or {}).get("@type") == "JobPosting":
            if block.get("description") and not result.get("description_full"):
                result["description_full"] = _strip_html(block["description"])
            sal = block.get("baseSalary") or {}
            if isinstance(sal, dict) and sal.get("value"):
                val = sal["value"]
                if isinstance(val, dict):
                    mn = val.get("minValue")
                    mx = val.get("maxValue")
                    if mn is not None or mx is not None:
                        result["salary_min"] = mn
                        result["salary_max"] = mx
                        result["salary_currency"] = sal.get("currency", "")
                        # La unidad real sale de unitText/unitCode del JSON-LD
                        # (schema.org QuantitativeValue). Si no viene, NO se
                        # asume anual: el enriquecimiento tratará el salario
                        # como no normalizable (NULL anual) en lugar de
                        # falsearlo.
                        result["salary_period"] = _jsonld_salary_period(sal)
            if block.get("datePosted"):
                result["posted_datetime"] = block["datePosted"]
            if block.get("employmentType"):
                result["employment_type"] = block["employmentType"]
            if isinstance(block.get("hiringOrganization"), dict):
                org = block["hiringOrganization"]
                if org.get("name") and not result.get("company_name"):
                    result["company_name"] = org["name"]
                if org.get("sameAs") and not result.get("company_url"):
                    result["company_url"] = org["sameAs"]
            if result.get("description_full"):
                break

    # 2) Fallback DOM
    if not result.get("description_full"):
        desc_el = (
            page.query_selector('[data-test="jobDescriptionText"]') or
            page.query_selector('[class*="jobDescription"]') or
            page.query_selector('[class*="description"]') or
            page.query_selector("article") or
            page.query_selector("main")
        )
        if desc_el:
            result["description_full"] = _text(desc_el)

    result["requirements"] = _extract_section(page, [
        "requirements", "what you need", "qualifications",
        "your profile", "about you", "who you are",
        "what we are looking for", "what we're looking for",
        "experience required", "skills required", "key skills",
        "required skills", "what you bring", "minimum qualifications",
        "preferred qualifications", "basic qualifications",
    ])
    result["responsibilities"] = _extract_section(page, [
        "responsibilities", "the role", "about the role",
        "job description", "what you will do", "what you'll do",
        "key responsibilities", "duties", "role overview",
        "the opportunity", "job summary", "position summary",
    ])
    result["benefits"] = _extract_section(page, [
        "benefits", "what we offer", "what's in it for you",
        "perks", "why join us", "we offer", "what you get",
        "package", "compensation and benefits",
        "compensation & benefits", "total rewards",
    ])
    result["company_description"] = _extract_section(page, [
        "about us", "about the company", "who we are",
        "company description", "our company", "about",
        "company overview",
    ])

    if not result.get("salary_raw"):
        body_text = _text(page.query_selector("body")) or ""
        sal_m = re.search(
            r"[\$€]\s?[\d,]+[KMB]?\s*(?:[–\-]\s*\$?€?[\d,]+[KMB]?)?"
            r"\s*(?:per\s+(?:year|month|hour|annum|año|mes|hora))?",
            body_text, re.IGNORECASE)
        if sal_m:
            result["salary_raw"] = sal_m.group(0).strip()

    for tag in page.query_selector_all(
        '[class*="tag"], [class*="badge"], [class*="pill"], '
        '[data-test="job-detail-type"], [class*="workType"]'
    ):
        txt = _text(tag).lower()
        if not result.get("work_mode"):
            if "remote" in txt:
                result["work_mode"] = "Remote"
            elif "hybrid" in txt:
                result["work_mode"] = "Hybrid"
            elif "on-site" in txt or "onsite" in txt:
                result["work_mode"] = "On-site"
        if not result.get("employment_type"):
            if "full-time" in txt or "full time" in txt:
                result["employment_type"] = "Full-time"
            elif "part-time" in txt or "part time" in txt:
                result["employment_type"] = "Part-time"
            elif "contract" in txt:
                result["employment_type"] = "Contract"
            elif "intern" in txt:
                result["employment_type"] = "Internship"

    return result


def _strip_html(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", str(raw or ""))
    import html as html_mod
    txt = html_mod.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def _jsonld_salary_period(sal: dict) -> str:
    """Devuelve el periodo salarial del JSON-LD (YEAR/MONTH/WEEK/DAY/HOUR).

    schema.org permite indicar la unidad en unitText ("YEAR", "per hour", ...)
    o unitCode (UNECE: "YEAR"/"MON"/"WK"/"DAY"/"HUR"). Si no hay unidad
    reconocible devuelve "" (desconocido -> NO se asume anual).
    """
    value = sal.get("value")
    unit = ""
    if isinstance(value, dict):
        for key in ("unitText", "unitCode"):
            v = value.get(key)
            if v:
                unit = str(v).strip().lower()
                break
    if not unit:
        return ""
    low = unit
    if any(t in low for t in ("month", "mes")):
        return "MONTH"
    if any(t in low for t in ("week", "semana")):
        return "WEEK"
    if any(t in low for t in ("day", "diar", "jornada")):
        return "DAY"
    if any(t in low for t in ("hour", "hora", "hr")):
        return "HOUR"
    if any(t in low for t in ("year", "annual", "annum", "año", "anual")):
        return "YEAR"
    # unitCode UNECE/CEFACT (mayusculas sin 'per'):
    code = unit.replace(" ", "").upper()
    return {
        "MON": "MONTH", "WK": "WEEK", "DAY": "DAY",
        "HUR": "HOUR", "HR": "HOUR", "ANN": "YEAR", "YEAR": "YEAR",
    }.get(code, "")


def _extract_section(page, keywords: list[str]) -> str:
    """Busca cabeceras que coincidan con keywords y extrae el contenido."""
    for tag_name in ("h2", "h3", "h4", "strong", "b"):
        headings = page.query_selector_all(tag_name)
        for h in headings:
            txt = _text(h).lower()
            if any(kw in txt for kw in keywords):
                try:
                    content = page.evaluate("""
                        (el) => {
                            let next = el.nextElementSibling;
                            let parts = [];
                            while (next && !['H2','H3','H4'].includes(next.tagName)) {
                                parts.push(next.innerText);
                                next = next.nextElementSibling;
                            }
                            return parts.join('\\n');
                        }
                    """, h)
                    if content and content.strip():
                        return content.strip()
                except Exception:
                    pass
    return ""