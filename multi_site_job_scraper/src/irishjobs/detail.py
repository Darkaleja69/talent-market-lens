"""Parseo de la pagina de detalle de IrishJobs / StepStone (Playwright)."""
from __future__ import annotations

import html as html_mod
import json
import logging
import time
import re
from typing import Optional

from src.core.normalize import as_text

log = logging.getLogger(__name__)

_RE_LD_SCRIPT = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html_mod.unescape(txt)
    return re.sub(r"[ \t]+", " ", txt).strip()


def _json_ld_jobposting(html_str: str) -> Optional[dict]:
    """Primer JobPosting encontrado en los bloques JSON-LD de la pagina."""
    for m in _RE_LD_SCRIPT.finditer(html_str or ""):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("@type") == "JobPosting":
                return b
            for g in b.get("@graph") or []:
                if isinstance(g, dict) and g.get("@type") == "JobPosting":
                    return g
    return None


def _parse_json_ld_detail(html_str: str, result: dict) -> None:
    """Rellena huecos desde el JSON-LD JobPosting (StepStone lo embebe)."""
    jp = _json_ld_jobposting(html_str)
    if not jp:
        return

    desc = _strip_html(jp.get("description") or "")
    if desc and not result.get("description_full"):
        result["description_full"] = desc

    if jp.get("employmentType") and not result.get("employment_type"):
        result["employment_type"] = as_text(jp["employmentType"])

    if jp.get("datePosted") and not result.get("posted_datetime"):
        result["posted_datetime"] = jp["datePosted"]

    if jp.get("industry") and not result.get("company_industry"):
        result["company_industry"] = str(jp["industry"])

    sal = jp.get("baseSalary") or {}
    if isinstance(sal, dict) and not result.get("salary_raw"):
        val = sal.get("value") or {}
        cur = sal.get("currency", "") or ""
        if isinstance(val, dict):
            mn, mx = val.get("minValue"), val.get("maxValue")
            if mn is not None or mx is not None:
                unit = val.get("unitText") or ""
                if mn is not None and mx is not None:
                    raw = f"{cur} {int(mn)} - {int(mx)}"
                elif mn is not None:
                    raw = f"{cur} {int(mn)}"
                else:
                    raw = f"{cur} {int(mx)}"
                if unit:
                    raw = f"{raw} per {unit.lower()}"
                result["salary_raw"] = raw.strip()

_RE_SALARY_DETAIL = re.compile(
    r"(?:€|EUR)\s*([\d][\d.,]*)\s*"
    r"(?:-\s*(?:€\s*)?([\d][\d.,]*))?\s*"
    r"(?:per\s+(annum|year|month|hour|day|week|jaar|maand|uur|dag)"
    r"|p\.?\s*[mju]\.?)",
    re.IGNORECASE,
)

# Widgets genericos de salario (no pertenecen a la oferta).
_WIDGET_SALARY_HINTS = re.compile(
    r"salarisinformatie|ontgrendel|wat je zou kunnen verdienen|"
    r"could be earning|unlock (?:your |the )?salary|salary (?:insight|estimate)|"
    r"inicia sesi[oó]n para ver|sign in to see",
    re.IGNORECASE,
)

_RE_WORK_REMOTE = re.compile(
    r"\bremote\b|remoto|remota|work\s+from\s+home|\bwfh\b|telecommut|"
    r"thuiswerk|telewerk|thuis\s+werken",
    re.IGNORECASE,
)
_RE_WORK_HYBRID = re.compile(
    r"\bhybrid\b|h[íi]brid[oa]|hibrid[eo]|hybride|semipresencial",
    re.IGNORECASE,
)

_REQ_KEYWORDS = [
    "requirements", "what you need", "qualifications",
    "your profile", "about you", "who you are",
    "what we are looking for", "what we're looking for",
    "experience required", "skills required", "key skills",
    "essential criteria", "required skills", "what you bring",
    "required experience", "technical skills", "must have",
    "you will have", "you'll have", "you should have",
    "what you will need", "what you'll need",
    "minimum qualifications", "desired skills",
    "your background", "your experience",
]

_RESP_KEYWORDS = [
    "responsibilities", "the role", "about the role",
    "job description", "what you will do", "what you'll do",
    "key responsibilities", "duties", "role overview",
    "the opportunity", "your day-to-day", "day to day",
    "your responsibilities", "main duties", "role responsibilities",
    "what you will be doing", "what you'll be doing",
    "role purpose", "position overview", "primary responsibilities",
    "core responsibilities", "objective",
]

_BENEFITS_KEYWORDS = [
    "benefits", "what we offer", "what's in it for you",
    "what is in it for you", "perks", "why join us",
    "we offer", "what you get", "package",
    "what we can offer", "our benefits", "our offer",
]

_COMPANY_KEYWORDS = [
    "about us", "about the company", "who we are",
    "company description", "our company", "about",
]

_RE_SECTION_BOUNDARY = re.compile(
    r"(?:^|\n)\s*(?:" + "|".join([
        re.escape("requirements"), re.escape("responsibilities"),
        re.escape("benefits"), re.escape("about"),
        re.escape("qualifications"), re.escape("the role"),
        re.escape("what you"), re.escape("what we"),
        re.escape("who you"), re.escape("skills"),
        re.escape("experience"), re.escape("duties"),
        re.escape("perks"), re.escape("package"),
        re.escape("why join"), re.escape("our company"),
    ]) + r")\s*(?:[:-]|\n)",
    re.IGNORECASE,
)


def _text(el) -> str:
    if not el:
        return ""
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def parse_detail(page, offer) -> dict:
    """Navega al detalle y extrae campos adicionales. Modifica offer in-place."""
    result: dict = {}

    url = offer.job_url
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
                         attempt, max_retries, offer.job_id, wait, e)
                time.sleep(wait)
            else:
                log.warning("Error navegando a detalle %s: %s", offer.job_id, e)
                return result

    try:
        page.wait_for_selector("body", timeout=10000)
    except Exception:
        pass

    # 1) JSON-LD JobPosting (fiable y multi-idioma); 2) DOM como respaldo.
    try:
        html_str = page.content()
    except Exception:
        html_str = ""
    if html_str:
        try:
            _parse_json_ld_detail(html_str, result)
        except Exception as e:
            log.debug("JSON-LD detalle fallido %s: %s", offer.job_id, e)

    desc_el = page.query_selector("[data-testid*='description']") or \
              page.query_selector("[class*='description']") or \
              page.query_selector("article") or \
              page.query_selector("main")
    if desc_el:
        result["description_full"] = _text(desc_el)

    body_text = _text(page.query_selector("body")) or ""

    result["requirements"] = _extract_section(page, _REQ_KEYWORDS)
    if not result.get("requirements"):
        result["requirements"] = _fallback_extract(body_text, _REQ_KEYWORDS)

    result["responsibilities"] = _extract_section(page, _RESP_KEYWORDS)
    if not result.get("responsibilities"):
        result["responsibilities"] = _fallback_extract(body_text, _RESP_KEYWORDS)

    result["benefits"] = _extract_section(page, _BENEFITS_KEYWORDS)
    if not result.get("benefits"):
        result["benefits"] = _fallback_extract(body_text, _BENEFITS_KEYWORDS)

    result["company_description"] = _extract_section(page, _COMPANY_KEYWORDS)

    if not _WIDGET_SALARY_HINTS.search(body_text):
        sm = _RE_SALARY_DETAIL.search(body_text)
        if sm:
            result["salary_raw"] = sm.group(0).strip()

        if not result.get("salary_raw"):
            body_no_ws = re.sub(r"\s+", " ", body_text)
            salary_patterns = [
                r"(?:salary|compensation)\s*(?:is\s*)?(?:€\s*[\d,]+(?:\s*-\s*(?:€\s*)?[\d,]+)?)",
                r"(?:salary range|salary band)\s*(?:is\s*)?(?:€\s*[\d,]+(?:\s*-\s*(?:€\s*)?[\d,]+)?)",
            ]
            for pat in salary_patterns:
                sm2 = re.search(pat, body_no_ws, re.IGNORECASE)
                if sm2:
                    result["salary_raw"] = sm2.group(0).strip()
                    break

    for tag in page.query_selector_all("[class*='tag'], [class*='badge'], [class*='pill']"):
        txt = _text(tag).lower()
        if "permanent" in txt or "contract" in txt or "full-time" in txt:
            if not result.get("employment_type"):
                result["employment_type"] = _text(tag)
        if _RE_WORK_HYBRID.search(txt):
            if not result.get("work_mode"):
                result["work_mode"] = "Hybrid"
        elif _RE_WORK_REMOTE.search(txt):
            if not result.get("work_mode"):
                result["work_mode"] = "Remote"

    # Ultimo recurso: derivar el modo de trabajo del texto de la descripcion.
    if not result.get("work_mode"):
        hay = " ".join(filter(None, [
            result.get("description_full", ""),
            result.get("requirements", ""),
            result.get("responsibilities", ""),
            result.get("benefits", ""),
        ]))
        if _RE_WORK_HYBRID.search(hay):
            result["work_mode"] = "Hybrid"
        elif _RE_WORK_REMOTE.search(hay):
            result["work_mode"] = "Remote"

    return result


def _extract_section(page, keywords: list[str]) -> str:
    for tag_name in ("h2", "h3", "h4", "strong", "b", "p", "div"):
        headings = page.query_selector_all(tag_name)
        for h in headings:
            txt = _text(h).lower()
            if any(kw in txt for kw in keywords):
                try:
                    content = page.evaluate("""
                        (el) => {
                            let next = el.nextElementSibling;
                            let parts = [];
                            while (next && !['H2','H3','H4','H1'].includes(next.tagName)) {
                                parts.push(next.innerText);
                                next = next.nextElementSibling;
                            }
                            return parts.join('\\n');
                        }
                    """, h)
                    if content:
                        return content.strip()
                except Exception:
                    pass
    return ""


def _fallback_extract(body_text: str, keywords: list[str]) -> str:
    """Busca secciones usando regex sobre el texto completo del body.
    Busca un heading con alguna keyword y extrae el texto hasta el siguiente heading."""
    if not body_text:
        return ""

    heading_pattern = re.compile(
        r"(?:^|\n)\s*([^\n]{2,80})\s*(?:\n|$)",
        re.MULTILINE,
    )

    potential_headings = heading_pattern.findall(body_text)

    for i, line in enumerate(potential_headings):
        line_lower = line.strip().lower()
        if any(kw in line_lower for kw in keywords):
            start_idx = body_text.find(line)
            if start_idx == -1:
                continue

            end_idx = len(body_text)
            for j in range(i + 1, len(potential_headings)):
                next_line = potential_headings[j].strip().lower()
                if any(kw2 in next_line for kw2 in _REQ_KEYWORDS + _RESP_KEYWORDS +
                       _BENEFITS_KEYWORDS + _COMPANY_KEYWORDS):
                    end_idx = body_text.find(potential_headings[j], start_idx + len(line))
                    if end_idx == -1:
                        end_idx = len(body_text)
                    break

            section_text = body_text[start_idx + len(line):end_idx].strip()
            if len(section_text) > 20:
                return section_text[:3000]

    return ""
