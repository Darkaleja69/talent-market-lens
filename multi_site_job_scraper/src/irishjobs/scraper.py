"""Orquestador del scraper de IrishJobs (Playwright).

Flujo: Playwright -> SERP -> parsear tarjetas -> detalle -> Store.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from src.core.browser import launch_context, close_context
from src.core.human import delay, mouse_jitter, scroll_slow
from src.core.models import JobOffer, now_utc_iso
from src.core.normalize import parse_salary
from src.core.store import Store
from . import serp
from . import title_filter
from . import detail as detail_parser
from .config_ie import DEFAULT_CONFIG

log = logging.getLogger("irishjobs")


class IrishJobsScraper:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or DEFAULT_CONFIG
        out = self.config.get("output", {})
        project_root = Path(__file__).resolve().parent.parent.parent
        self.store = Store(
            csv_path=project_root / out.get("csv", "data/irishjobs/output/jobs.csv"),
            parquet_path=project_root / out.get("parquet", "data/irishjobs/output/jobs.parquet"),
            checkpoint_dir=project_root / out.get("checkpoint_dir", "data/irishjobs/checkpoints"),
        )
        self._pw = None
        self._context = None
        self._page = None
        self._browser_alive = False

    def run(self) -> int:
        roles = self.config.get("roles", [])
        self._title_phrases = title_filter.phrases_from_roles(roles)
        cities = self.config.get("cities", {})
        base_url = self.config["base_url"]
        search_path = self.config.get("search_path", "/jobs/{role_slug}")
        date_filter = self.config.get("date_filter", "")
        max_detail = self.config.get("max_detail_jobs", 750)
        do_detail = self.config.get("do_detail", True)
        max_pages = self.config.get("max_pages", 15)
        max_total = self.config.get("max_total_jobs", 1000)
        jobs_per_search = self.config.get("jobs_per_search", 50)
        site = self.config.get("site", "irishjobs")
        country = self.config.get("country", "IE")
        country_name = self.config.get("country_name", "Ireland")
        locale = self.config.get("locale", "en-GB")
        timezone = self.config.get("timezone", "Europe/Dublin")
        profile_name = self.config.get("profile_name", site)

        delays_cfg = self.config.get("delays", {})
        post_goto = tuple(delays_cfg.get("post_goto", [2.0, 4.0]))
        step_px = tuple(delays_cfg.get("scroll_step_px", [200, 400]))
        scroll_pause = tuple(delays_cfg.get("between_scrolls", [1.5, 3.0]))
        between_pages = tuple(delays_cfg.get("between_pages", [2.0, 5.0]))
        between_details = tuple(delays_cfg.get("between_details", [3.0, 7.0]))
        between_searches = tuple(delays_cfg.get("between_searches", [5.0, 10.0]))

        retry_cfg = self.config.get("retry", {})
        max_retries = retry_cfg.get("max_retries", 3)
        retry_delay = tuple(retry_cfg.get("retry_delay", [3.0, 8.0]))

        radius_map = self.config.get("radius", {})

        combos = [(r, cn, cc) for r in roles for cn, cc in cities.items()]
        log.info("Combinaciones: %d roles x %d ciudades = %d",
                 len(roles), len(cities), len(combos))

        total_new = 0
        total_detail_visited = 0

        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._launch_browser(locale, timezone, profile_name)

        interrupted = False
        try:
            for idx, (role, city_name, city_cfg) in enumerate(combos):
                if max_total > 0 and total_new >= max_total:
                    log.info("Target de %d ofertas alcanzado. Deteniendo.", max_total)
                    break

                log.info("--- [%d/%d] '%s' en %s (acumulado: %d, detalles: %d) ---",
                         idx + 1, len(combos), role, city_name, total_new, total_detail_visited)

                if not self._ensure_browser_alive(role, city_name, locale, timezone, profile_name):
                    log.error("Browser irrecoverable. Terminando.")
                    break

                radius = radius_map.get(city_name, 25) if radius_map else 25
                location = city_cfg.get("text", "") if city_cfg.get("text") else ""
                is_nationwide = city_cfg.get("nationwide", False)
                location = "" if is_nationwide else location

                n, detail_visited = self._process_combo(
                    role=role, city_name=city_name,
                    location=location, is_nationwide=is_nationwide,
                    radius=radius, base_url=base_url, search_path=search_path,
                    date_filter=date_filter, do_detail=do_detail,
                    max_detail=max_detail, max_pages=max_pages,
                    max_total=max_total, total_so_far=total_new,
                    jobs_per_search=jobs_per_search,
                    post_goto=post_goto, step_px=step_px,
                    scroll_pause=scroll_pause, between_pages=between_pages,
                    between_details=between_details,
                    site=site, country=country, country_name=country_name,
                    max_retries=max_retries, retry_delay=retry_delay,
                )
                total_new += n
                total_detail_visited += detail_visited
                if idx < len(combos) - 1:
                    delay(between_searches, label="between-searches")
        except KeyboardInterrupt:
            interrupted = True
            log.warning("Ctrl+C recibido. Guardando lo encontrado y cerrando...")
        finally:
            self._teardown_browser()
            if self._pw:
                self._pw.stop()

        if interrupted:
            self.store.checkpoint(tag=f"{site}_interrupted")
        self.store.write_all()
        if interrupted:
            log.info("=== %s INTERRUMPIDO: %d ofertas nuevas, %d total, %d detalles ===",
                     site.upper(), total_new, len(self.store), total_detail_visited)
            return total_new
        log.info("=== %s DONE: %d ofertas nuevas, %d total, %d detalles ===",
                 site.upper(), total_new, len(self.store), total_detail_visited)
        return total_new

    def _launch_browser(self, locale: str, timezone: str, profile_name: str) -> None:
        if self._context:
            close_context(self._context)
        self._context = launch_context(self._pw, locale=locale, timezone=timezone,
                                       profile_name=profile_name)
        self._context.on("page", self._handle_popup)
        try:
            self._context.add_init_script("""
                window.open = function() { return null; };
            """)
        except Exception:
            pass
        pages = self._context.pages
        if pages:
            self._page = pages[0]
        else:
            self._page = self._context.new_page()
        self._browser_alive = True

    def _handle_popup(self, popup) -> None:
        try:
            popup.close()
        except Exception:
            pass

    def _ensure_browser_alive(self, role: str, city: str,
                               locale: str, timezone: str, profile_name: str) -> bool:
        try:
            self._page.evaluate("() => 1")
            return True
        except Exception:
            pass
        log.warning("Browser no responde en '%s'/%s. Relanzando...", role, city)
        try:
            self._launch_browser(locale, timezone, profile_name)
            time.sleep(3)
            self._page.evaluate("() => 1")
            return True
        except Exception as e:
            log.error("No se pudo relanzar browser: %s", e)
            return False

    def _teardown_browser(self) -> None:
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
        if self._context:
            close_context(self._context)
        self._browser_alive = False

    def _process_combo(self, role, city_name, location, is_nationwide, radius,
                       base_url, search_path, date_filter, do_detail,
                       max_detail, max_pages, max_total, total_so_far,
                       jobs_per_search,
                       post_goto, step_px,
                       scroll_pause, between_pages, between_details,
                       site, country, country_name,
                       max_retries, retry_delay) -> tuple[int, int]:
        total = 0
        detail_visited = 0
        page_ids_seen: set[str] = set()

        for pg in range(1, max_pages + 1):
            if max_total > 0 and total_so_far + total >= max_total:
                log.info("Target de %d ofertas alcanzado en este combo.", max_total)
                break

            url = serp.build_search_url(base_url, search_path, role, pg,
                                        date_filter, location=location)
            log.info("SERP page %d/%d: %s", pg, max_pages, url)

            success = False
            for attempt in range(1, max_retries + 1):
                try:
                    self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        self._page.wait_for_load_state("networkidle", timeout=20000)
                    except Exception:
                        pass
                    success = True
                    break
                except Exception as e:
                    if attempt < max_retries:
                        wait = 3.0 * attempt + 2.0
                        log.debug("Retry %d/%d SERP (%.1fs): %s",
                                 attempt, max_retries, wait, e)
                        time.sleep(wait)
                    else:
                        log.warning("SERP fallido tras %d intentos: %s", max_retries, e)

            if not success:
                log.warning("Saltando combo '%s' en %s, pagina %d", role, city_name, pg)
                break

            delay(post_goto, label="post-goto-serp")
            serp.dismiss_cookie_banner(self._page)
            mouse_jitter(self._page, n=1)

            if pg == 1 and is_nationwide:
                max_scroll = 8
                early_cards = 25
            elif is_nationwide:
                max_scroll = 5
                early_cards = 20
            else:
                max_scroll = 4
                early_cards = 10

            scroll_slow(self._page, step_px_rng=step_px, max_steps=max_scroll,
                        pause_rng=scroll_pause, early_card_count=early_cards)

            raw_cards = serp.parse_cards(self._page, base_url)
            if not raw_cards:
                log.info("Sin tarjetas en page %d. Fin.", pg)
                break

            cards = raw_cards
            if self.config.get("title_filter", True):
                cards, dropped = title_filter.filter_cards(
                    raw_cards, self._title_phrases,
                    broad=self.config.get("title_filter_broad", False))
                if dropped:
                    log.info("Filtro titulo page %d: %d de %d no son roles data",
                             pg, dropped, len(raw_cards))
                if not cards:
                    sample = " | ".join(
                        repr(c.get("title", "")) for c in raw_cards[:6])
                    log.info("Titulos descartados (muestra): %s", sample)

            current_ids = {c["job_id"] for c in raw_cards if c.get("job_id")}
            new_on_page = len(current_ids - page_ids_seen)
            page_ids_seen |= current_ids

            log.info("Page %d: %d tarjetas (%d nuevas en esta pagina)",
                     pg, len(raw_cards), new_on_page)

            for i, raw in enumerate(cards[:jobs_per_search]):
                offer = self._card_to_offer(raw, role, city_name, site, country, country_name)
                is_new = self.store.add(offer)
                if is_new:
                    total += 1

                if do_detail and is_new and detail_visited < max_detail and offer.job_url:
                    delay(between_details, label="pre-detail")
                    try:
                        extra = detail_parser.parse_detail(self._page, offer)
                        self._enrich_offer(offer, extra)
                        detail_visited += 1
                    except Exception as e:
                        log.debug("Detalle fallido %s: %s", offer.job_id, e)

                if (i + 1) % 10 == 0:
                    self.store.checkpoint(tag=f"{site}_{role}_{city_name}_p{pg}_{i+1}")

            self.store.checkpoint(tag=f"{site}_{role}_{city_name}_p{pg}")

            if pg >= max_pages:
                log.info("Limite de paginas (%d) alcanzado.", max_pages)
                break

            if not serp.has_next_page(self._page):
                if new_on_page > 0 and len(raw_cards) >= jobs_per_search:
                    log.info("has_next_page=False pero %d tarjetas. Intentando URL next...",
                             len(raw_cards))
                    delay(between_pages, label="next-page-fallback")
                    if serp.go_to_next_page(self._page):
                        new_cards = serp.parse_cards(self._page, base_url)
                        new_ids = {c["job_id"] for c in new_cards if c.get("job_id")}
                        if not (new_ids - page_ids_seen):
                            log.info("Pagina siguiente sin nuevas tarjetas. Fin real.")
                            break
                        page_ids_seen |= new_ids
                        continue
                log.info("Fin paginacion combo '%s' en %s tras %d pag.", role, city_name, pg)
                break

            delay(between_pages, label="next-page")
            if not serp.go_to_next_page(self._page):
                log.info("Navegacion a siguiente pagina fallida. Fin combo.")
                break

        return total, detail_visited

    def _card_to_offer(self, raw: dict, role: str, city_name: str,
                       site: str, country: str, country_name: str) -> JobOffer:
        city, region, _ = serp.parse_location(raw.get("location_raw", ""))

        return JobOffer(
            job_id=raw.get("job_id", ""),
            job_url=raw.get("job_url", ""),
            title=raw.get("title", ""),
            company_name=raw.get("company_name", ""),
            location_raw=raw.get("location_raw", ""),
            location_city=city,
            location_region=region,
            location_country=country_name,
            posted_datetime=raw.get("posted_datetime") or None,
            posted_relative=raw.get("posted_relative", ""),
            description_snippet=raw.get("description_snippet", ""),
            salary_raw=raw.get("salary_raw", ""),
            salary_min=raw.get("salary_min"),
            salary_max=raw.get("salary_max"),
            salary_currency=raw.get("salary_currency", "EUR"),
            salary_period=raw.get("salary_period", ""),
            salary_disclosed=raw.get("salary_disclosed", True),
            work_mode=raw.get("work_mode", ""),
            employment_type=raw.get("employment_type", ""),
            site=site,
            country=country,
            search_role=role,
            search_city=city_name,
            source="local",
            scraped_at=now_utc_iso(),
        )

    def _enrich_offer(self, offer: JobOffer, extra: dict) -> None:
        for field in ["description_full", "requirements", "responsibilities",
                       "benefits", "company_description", "salary_raw",
                       "employment_type", "work_mode", "posted_datetime",
                       "company_industry"]:
            if extra.get(field) and not getattr(offer, field, None):
                setattr(offer, field, extra[field])

        # Si el detalle aporta un salario textual pero no min/max, se parsea
        # (cubre formatos ES/EN/NL). Si no se puede parsear, se descarta para
        # no marcar is_salary_available con un texto sin importes.
        if offer.salary_raw and offer.salary_min is None:
            parsed = parse_salary(offer.salary_raw, default_currency="EUR")
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
