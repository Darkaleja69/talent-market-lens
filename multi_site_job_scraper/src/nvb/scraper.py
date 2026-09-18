"""Orquestador del scraper de Nationale Vacaturebank.

La API (Akamai) exige cookies de sesion, por lo que se lee navegando con el
navegador (patchright). Los textos se guardan en holandes; el merge los traduce.
"""
from __future__ import annotations

import datetime as dt
import html as html_mod
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

from src.core.browser import close_context, launch_context
from src.core.models import JobOffer, now_utc_iso
from src.core.normalize import as_text, extract_skills
from src.core.store import Store
from src.irishjobs import title_filter
from .api import API_URL, NvbBrowserApi
from .config_nl import DEFAULT_CONFIG

log = logging.getLogger("nvb")

_RE_TAGS = re.compile(r"<[^>]+>")
_RE_BREAKS = re.compile(r"</(p|div|li|h[1-6]|tr)>", re.I)


def _strip_html(value: Any) -> str:
    txt = as_text(value)
    if not txt:
        return ""
    if "<" in txt and ">" in txt:
        txt = _RE_BREAKS.sub("\n", txt)
        txt = _RE_TAGS.sub(" ", txt)
    txt = html_mod.unescape(txt)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    txt = str(value).strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(txt)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def _age_days(d: Optional[dt.datetime]) -> Optional[int]:
    if not d:
        return None
    return int((dt.datetime.now(dt.timezone.utc) - d).total_seconds() // 86400)


def _relative(d: Optional[dt.datetime]) -> str:
    if not d:
        return ""
    secs = (dt.datetime.now(dt.timezone.utc) - d).total_seconds()
    if secs < 3600:
        return f"{max(1, int(secs // 60))} minutes ago"
    if secs < 86400:
        h = int(secs // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    days = int(secs // 86400)
    return "Yesterday" if days == 1 else f"{days} days ago"


def _workload_pct(hours: Any) -> str:
    """Normaliza la jornada a porcentaje. La API da horas (<=40) o ya el %."""
    if not isinstance(hours, dict):
        return ""
    def _f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    mn, mx = _f(hours.get("min")), _f(hours.get("max"))
    if not mn and not mx:
        return ""
    base = max([v for v in (mn, mx) if v] or [0])
    scale = 40.0 if base <= 40 else 100.0
    def _p(x):
        return int(round(x / scale * 100)) if x else None
    a, b = _p(mn), _p(mx)
    if a and b and a != b:
        return f"{a}-{b}%"
    return f"{b or a}%"


def _company_url(value: Any) -> str:
    v = as_text(value)
    if not v:
        return ""
    return v if v.startswith("http") else "https://" + v


def _salary(sal: Any) -> tuple[Optional[float], Optional[float], str, str]:
    """Devuelve (min, max, raw, period). El periodo se infiere del importe:
    <500 -> hora; <20000 -> mes; resto -> ano (la API no lo especifica)."""
    if not isinstance(sal, dict):
        return None, None, "", ""
    def _f(x):
        try:
            return float(x) if x not in (None, "", 0, "0") else None
        except (TypeError, ValueError):
            return None
    mn, mx = _f(sal.get("min")), _f(sal.get("max"))
    top = max([v for v in (mn, mx) if v] or [0])
    if top and top < 500:
        period, unit = "HOUR", "per uur"
    elif top < 20000:
        period, unit = "MONTH", "per maand"
    else:
        period, unit = "YEAR", "per jaar"
    raw = ""
    if mn and mx:
        raw = (f"€{int(mn)} - €{int(mx)} {unit}" if mn != mx
               else f"€{int(mn)} {unit}")
    elif mn:
        raw = f"€{int(mn)} {unit}"
    elif mx:
        raw = f"€{int(mx)} {unit}"
    return mn, mx, raw, (period if raw else "")


class NvbScraper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or DEFAULT_CONFIG
        out = self.config.get("output", {})
        project_root = Path(__file__).resolve().parent.parent.parent
        self.store = Store(
            csv_path=project_root / out.get("csv", "data/nvb/output/jobs.csv"),
            parquet_path=project_root / out.get("parquet", "data/nvb/output/jobs.parquet"),
            checkpoint_dir=project_root / out.get("checkpoint_dir", "data/nvb/checkpoints"),
        )

    def run(self, max_jobs: int | None = None) -> int:
        cfg = self.config
        site = cfg.get("site", "nvb")
        country = cfg.get("country", "NL")
        country_name = cfg.get("country_name", "Netherlands")
        api_url = cfg.get("api_url") or API_URL
        base_url = cfg.get("base_url", "https://www.nationalevacaturebank.nl")
        queries = cfg.get("queries", [])
        page_size = int(cfg.get("page_size", 100))
        max_pages = int(cfg.get("max_pages_per_query", 10))
        max_total = int(cfg.get("max_total_jobs", 3000))
        max_age = int(cfg.get("max_age_days", 0) or 0)
        use_filter = cfg.get("title_filter", True)
        broad = cfg.get("title_filter_broad", True)
        phrases = title_filter.phrases_from_roles(queries)
        locale = cfg.get("locale", "nl-NL")
        timezone = cfg.get("timezone", "Europe/Amsterdam")
        profile = cfg.get("profile_name", "nvb")
        delays = cfg.get("delays", {})
        rng = tuple(delays.get("between_requests", [1.5, 3.5]))
        post_goto = tuple(delays.get("post_goto", [1.0, 2.0]))

        log.info("=== Nationale Vacaturebank Scraper ===")
        log.info("Consultas: %d | page_size=%d | max_pages=%d | max_age=%sd",
                 len(queries), page_size, max_pages, max_age or "-")

        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            from playwright.sync_api import sync_playwright  # type: ignore

        total_new = 0
        interrupted = False
        with sync_playwright() as pw:
            context = launch_context(pw, locale=locale, timezone=timezone,
                                     profile_name=profile)
            page = context.new_page()
            api = NvbBrowserApi(page, api_url)
            try:
                log.info("Cargando %s para cookies Akamai...", base_url)
                page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(4)
                for qi, query in enumerate(queries, 1):
                    if max_total > 0 and total_new >= max_total:
                        log.info("Target de %d ofertas alcanzado.", max_total)
                        break
                    log.info("--- [%d/%d] query '%s' ---", qi, len(queries), query)
                    page_num, pages = 1, 1
                    while page_num <= pages and page_num <= max_pages:
                        data = self._fetch(api, page, base_url, query, page_num,
                                           page_size, post_goto)
                        if not data:
                            break
                        jobs = NvbBrowserApi.jobs_of(data)
                        if page_num == 1:
                            pages = NvbBrowserApi.pages_of(data)
                            log.info("  '%s': %d resultados (%d paginas)",
                                     query, NvbBrowserApi.total_of(data), pages)
                        if not jobs:
                            break
                        older = 0
                        for job in jobs:
                            if max_total > 0 and total_new >= max_total:
                                break
                            posted = _parse_dt(job.get("startDate"))
                            age = _age_days(posted)
                            if max_age and age is not None and age > max_age:
                                older += 1
                                continue
                            if use_filter and not title_filter.filter_cards(
                                    [{"title": job.get("title", "")}], phrases,
                                    broad=broad)[0]:
                                continue
                            offer = self._to_offer(job, query, site, country, country_name)
                            if self.store.add(offer):
                                total_new += 1
                                if total_new % 25 == 0:
                                    self.store.checkpoint(tag=f"{site}_{query}_p{page_num}")
                        log.info("  p%d: %d ofertas (acumulado %d)", page_num,
                                 len(jobs), total_new)
                        if max_age and older == len(jobs):
                            log.info("  '%s': pagina sin ofertas recientes. Fin query.", query)
                            break
                        page_num += 1
                        _sleep(rng)
            except KeyboardInterrupt:
                interrupted = True
                log.warning("Ctrl+C recibido. Guardando progreso...")
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                close_context(context)

        if interrupted:
            self.store.checkpoint(tag=f"{site}_interrupted")
        self.store.write_all()
        state = "INTERRUMPIDO" if interrupted else "DONE"
        log.info("=== %s %s: %d nuevas, %d total ===", site.upper(), state,
                 total_new, len(self.store))
        return total_new

    def _fetch(self, api: NvbBrowserApi, page, base_url: str, query: str,
               page_num: int, page_size: int,
               post_goto: tuple[float, float]) -> dict:
        """Fetch con recuperacion: si Akamai bloquea, recarga la home y reintenta."""
        for attempt in range(1, 3):
            data = api.fetch_jobs(query, page=page_num, limit=page_size, sort="date")
            if data:
                _sleep(post_goto)
                return data
            if attempt < 2:
                log.warning("Sin JSON en '%s' p%d (intento %d); recargando home...",
                            query, page_num, attempt)
                try:
                    page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                time.sleep(8)
        return {}

    def _to_offer(self, job: dict, query: str, site: str, country: str,
                  country_name: str) -> JobOffer:
        jid = as_text(job.get("id"))
        links = job.get("_links") or {}
        job_url = as_text((links.get("detail") or {}).get("href"))

        company = job.get("company") or {}
        loc = job.get("workLocation") or {}
        city = as_text(loc.get("city")) or as_text(loc.get("displayName"))
        region = as_text(loc.get("province"))
        display = as_text(loc.get("displayName")) or city
        location_raw = ", ".join(p for p in (display, region) if p and p != display) or display

        posted = _parse_dt(job.get("startDate"))
        posted_iso = posted.isoformat() if posted else ""

        sal_min, sal_max, salary_raw, salary_period = _salary(job.get("salary"))
        description = _strip_html(job.get("description"))
        requirements = _strip_html(job.get("requirements"))

        hours = job.get("workingHours") or {}
        workload = _workload_pct(hours)

        skills: list[str] = []
        for s in extract_skills(" ".join([description, requirements])):
            if s not in skills:
                skills.append(s)

        industries = job.get("industries")
        industry = as_text(industries[0]) if isinstance(industries, list) and industries else ""

        return JobOffer(
            job_id=f"nvb_{jid}",
            job_url=job_url,
            title=as_text(job.get("title")),
            company_name=as_text(company.get("name")),
            location_raw=location_raw,
            location_city=city,
            location_region=region,
            location_country=country_name,
            posted_datetime=posted_iso,
            posted_relative=_relative(posted),
            is_new=(_age_days(posted) or 99) <= 1,
            company_url=_company_url(company.get("website")),
            company_industry=industry,
            work_mode=as_text(job.get("workingPlace")),
            employment_type=as_text(job.get("contractType")),
            experience_level=as_text(job.get("careerLevel")),
            salary_raw=salary_raw,
            salary_min=sal_min,
            salary_max=sal_max,
            salary_currency="EUR" if salary_raw else "",
            salary_period=salary_period,
            salary_source="EMPLOYER_PROVIDED" if salary_raw else "",
            description_full=description,
            description_snippet=description[:300],
            requirements=requirements,
            skills=skills,
            site=site,
            country=country,
            workload_pct=workload,
            salary_disclosed=bool(salary_raw),
            search_role=query,
            search_city=city,
            source="local",
            scraped_at=now_utc_iso(),
        )


def _sleep(rng: tuple[float, float]) -> None:
    if rng and rng[0] >= 0:
        time.sleep(random.uniform(*rng))
