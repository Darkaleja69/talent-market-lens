"""Orquestador del scraper de jobs.ch (Playwright).

Flujo por rol:
  1. Lanzar navegador Playwright (patchright) con perfil persistente.
  2. Busqueda nacional por rol (el SERP ignora filtros de ciudad en 2026-08).
  3. Paginar hasta que deje de existir "siguiente" (rel=next / cursor).
  4. Deduplicar por UUID; atribuir search_city por la ubicacion de la oferta.
  5. Visitar detalle en una pagina separada (no rompe el estado del SERP).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from src.core.browser import launch_context, close_context
from src.core.human import delay, mouse_jitter, scroll_slow
from src.core.models import JobOffer, now_utc_iso
from src.core.normalize import extract_skills, parse_salary
from src.core.store import Store
from . import serp
from . import detail as detail_parser
from .config_ch import DEFAULT_CONFIG

log = logging.getLogger("jobs_ch")


class JobsChScraper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or DEFAULT_CONFIG
        out = self.config.get("output", {})
        project_root = Path(__file__).resolve().parent.parent.parent
        self.store = Store(
            csv_path=project_root / out.get("csv", "data/jobs_ch/output/jobs.csv"),
            parquet_path=project_root / out.get("parquet", "data/jobs_ch/output/jobs.parquet"),
            checkpoint_dir=project_root / out.get("checkpoint_dir", "data/jobs_ch/checkpoints"),
        )

    def run(self) -> int:
        roles = self.config.get("roles", [])
        base_url = self.config["base_url"]
        search_path = self.config.get("search_path", "/en/vacancies/")
        max_detail = self.config.get("max_detail_jobs", 50)
        do_detail = self.config.get("do_detail", True)
        max_pages = self.config.get("max_pages", 40)
        site = self.config.get("site", "jobs_ch")
        country = self.config.get("country", "CH")
        country_name = self.config.get("country_name", "Switzerland")
        locale = self.config.get("locale", "en-GB")
        timezone = self.config.get("timezone", "Europe/Zurich")
        profile_name = self.config.get("profile_name", site)

        delays_cfg = self.config.get("delays", {})
        post_goto = tuple(delays_cfg.get("post_goto", [2.0, 4.0]))
        step_px = tuple(delays_cfg.get("scroll_step_px", [200, 400]))
        scroll_pause = tuple(delays_cfg.get("between_scrolls", [1.5, 3.5]))
        between_pages = tuple(delays_cfg.get("between_pages", [2.0, 5.0]))
        between_details = tuple(delays_cfg.get("between_details", [3.0, 8.0]))
        between_searches = tuple(delays_cfg.get("between_searches", [5.0, 12.0]))

        cities_cfg = self.config.get("cities", {})
        log.info("=== jobs.ch Scraper: %d roles (busqueda nacional) ===", len(roles))
        log.info("Roles: %s | detalle: %s | max_pages: %d | ciudades(attr): %s",
                 ", ".join(roles), do_detail, max_pages,
                 ", ".join(cities_cfg.keys()) or "-")

        total_new = 0
        interrupted = False
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            from playwright.sync_api import sync_playwright  # type: ignore

        with sync_playwright() as pw:
            context = launch_context(pw, locale=locale, timezone=timezone,
                                     profile_name=profile_name)
            page = context.new_page()
            detail_page = context.new_page()

            try:
                for idx, role in enumerate(roles):
                    log.info("--- [%d/%d] '%s' ---", idx + 1, len(roles), role)
                    n = self._process_role(
                        page=page, detail_page=detail_page, role=role,
                        base_url=base_url, search_path=search_path,
                        do_detail=do_detail, max_detail=max_detail,
                        max_pages=max_pages, post_goto=post_goto,
                        step_px=step_px, scroll_pause=scroll_pause,
                        between_pages=between_pages,
                        between_details=between_details,
                        site=site, country=country, country_name=country_name,
                        cities_cfg=cities_cfg,
                    )
                    total_new += n
                    if idx < len(roles) - 1:
                        delay(between_searches, label="between-searches")
            except KeyboardInterrupt:
                interrupted = True
                log.warning("Ctrl+C recibido. Guardando lo encontrado y cerrando...")
            finally:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    detail_page.close()
                except Exception:
                    pass
                close_context(context)

        if interrupted:
            self.store.checkpoint(tag=f"{site}_interrupted")
        self.store.write_all()
        if interrupted:
            log.info("=== %s INTERRUMPIDO: %d ofertas nuevas, %d total en store ===",
                     site.upper(), total_new, len(self.store))
            return total_new
        log.info("=== %s DONE: %d ofertas nuevas, %d total en store ===",
                 site.upper(), total_new, len(self.store))
        return total_new

    def _process_role(self, page, detail_page, role, base_url, search_path,
                      do_detail, max_detail, max_pages, post_goto,
                      step_px, scroll_pause, between_pages,
                      between_details, site, country, country_name,
                      cities_cfg) -> int:
        total_new = 0
        max_days = self.config.get("date_filter", 0)
        jobs_per_search = self.config.get("jobs_per_search", 50)
        filtered_out = 0

        for pg in range(1, max_pages + 1):
            url = serp.build_search_url(base_url, search_path, role, pg)
            log.info("SERP page %d: %s", pg, url)

            # jobs.ch puede servir una pagina sin tarjetas (rate-limit/bloqueo
            # temporal): se reintenta una vez con recarga y espera extra antes
            # de darla por vacia y terminar el rol.
            cards = []
            for attempt in range(1, 3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=25000)
                    except Exception:
                        pass
                except Exception as e:
                    log.error("Error navegando a SERP (intento %d): %s", attempt, e)
                    if attempt < 2:
                        time.sleep(8)
                        continue
                    break

                delay(post_goto, label="post-goto-serp")
                mouse_jitter(page, n=1)
                scroll_slow(page, step_px_rng=step_px, max_steps=5,
                            pause_rng=scroll_pause)

                cards = serp.parse_cards(page)
                if cards:
                    break
                if attempt < 2:
                    log.warning("SERP page %d sin tarjetas (intento %d); "
                                "recargando...", pg, attempt)
                    time.sleep(8)

            if not cards:
                log.info("Sin tarjetas en page %d. Fin.", pg)
                break

            if max_days > 0:
                before = len(cards)
                cards = [c for c in cards if self._is_recent(c, max_days)]
                filtered_out += before - len(cards)

            log.info("Page %d: %d tarjetas", pg, len(cards))

            processed_page = 0
            for i, raw in enumerate(cards[:jobs_per_search]):
                offer = self._card_to_offer(raw, role, site, country,
                                            country_name, cities_cfg)
                is_new = self.store.add(offer)
                if is_new:
                    total_new += 1
                    processed_page += 1

                if (do_detail and is_new and total_new <= max_detail
                        and not offer.description_full and offer.job_url):
                    delay(between_details, label="pre-detail")
                    try:
                        extra = detail_parser.parse_detail(detail_page, raw)
                        self._enrich_offer(offer, extra)
                    except Exception as e:
                        log.debug("Detalle fallido %s: %s", offer.job_id, e)

                if (i + 1) % 10 == 0 and total_new > 0:
                    self.store.checkpoint(tag=f"{role}_p{pg}_{i+1}")

            if processed_page:
                self.store.checkpoint(tag=f"{role}_p{pg}")

            if not serp.has_next_page(page):
                log.info("Sin pagina siguiente para '%s'. Fin.", role)
                break
            delay(between_pages, label="next-page")
            if not serp.go_to_next_page(page):
                log.info("No se pudo navegar a la pagina siguiente de '%s'.", role)
                break

        if filtered_out > 0:
            log.info("Filtro fecha (%dd): %d tarjetas antiguas descartadas",
                     max_days, filtered_out)
        return total_new

    def _is_recent(self, card: dict, max_days: int) -> bool:
        posted_iso = card.get("posted_datetime", "")
        if posted_iso:
            return serp._is_recent_iso(posted_iso, max_days)
        posted = card.get("posted_relative", "")
        if not posted:
            return True
        days = serp._days_ago(posted)
        if days is None:
            return True
        return days <= max_days

    def _card_to_offer(self, raw: dict, role: str, site: str, country: str,
                       country_name: str, cities_cfg: dict) -> JobOffer:
        location_raw = raw.get("location_raw", "")
        city = raw.get("location_city", "")
        region = raw.get("location_region", "")
        if not city:
            parts = [p.strip() for p in location_raw.split(",") if p.strip()]
            city = parts[0] if parts else ""
            region = parts[1] if len(parts) > 1 else ""

        search_city = self._attribute_city(location_raw, city, cities_cfg)

        return JobOffer(
            job_id=raw.get("job_id", ""),
            job_url=raw.get("job_url", ""),
            title=raw.get("title", ""),
            company_name=raw.get("company_name", ""),
            company_url=raw.get("company_url", ""),
            location_raw=location_raw,
            location_city=city,
            location_region=region,
            location_country=raw.get("location_country", "") or country_name,
            posted_datetime=raw.get("posted_datetime") or None,
            posted_relative=raw.get("posted_relative", ""),
            is_new=raw.get("is_new", False),
            employment_type=raw.get("employment_type", "") or raw.get("contract_type", ""),
            workload_pct=raw.get("workload_pct", ""),
            work_mode=raw.get("work_mode", ""),
            description_snippet=raw.get("description_snippet", ""),
            salary_disclosed=False,
            site=site,
            country=country,
            search_role=role,
            search_city=search_city,
            source="local",
            scraped_at=now_utc_iso(),
        )

    @staticmethod
    def _attribute_city(location_raw: str, city: str,
                        cities_cfg: dict) -> str:
        """Atribuye search_city comparando la ubicacion con las ciudades cfg."""
        if not cities_cfg:
            return ""
        text_lower = (location_raw + " " + city).lower()
        for key, val in cities_cfg.items():
            text = (val.get("text", "") if isinstance(val, dict) else str(val))
            if not text:
                continue
            if text.lower().replace("ü", "u") in text_lower.replace("ü", "u"):
                return key
        return ""

    def _enrich_offer(self, offer: JobOffer, extra: dict) -> None:
        for field in [
            "description_full", "requirements", "responsibilities",
            "benefits", "salary_raw", "company_description", "company_industry",
            "employment_type", "workload_pct", "company_url", "work_mode",
        ]:
            val = extra.get(field)
            if val and not getattr(offer, field, None):
                setattr(offer, field, val)

        # Salario: solo se conserva si se puede parsear a importe + periodo.
        # Un salary_raw sin importes (texto de widget/estimador) no debe marcar
        # is_salary_available=True en BI.
        if offer.salary_raw and offer.salary_min is None:
            parsed = parse_salary(offer.salary_raw, default_currency="CHF")
            if parsed["salary_min"] is not None or parsed["salary_max"] is not None:
                offer.salary_min = parsed["salary_min"]
                offer.salary_max = parsed["salary_max"]
                if parsed["salary_currency"]:
                    offer.salary_currency = parsed["salary_currency"]
                if parsed["salary_period"]:
                    offer.salary_period = parsed["salary_period"]
                offer.salary_disclosed = True
            else:
                offer.salary_raw = ""
                offer.salary_disclosed = False

        if offer.description_full and not offer.skills:
            offer.skills = extract_skills(offer.description_full)
