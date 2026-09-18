"""Orquestador del scraper de Glassdoor (Playwright + BFF GraphQL).

Flujo para cada rol:
  1. Lanzar navegador Playwright (patchright) con perfil persistente.
  2. Bootstrap de sesion en la homepage (cookies + CSRF token).
  3. Resolver la ubicacion objetivo (pais/ciudad) a locationId.
  4. Consultar el endpoint GraphQL/BFF (/graph) con paginacion por cursor.
  5. Fallback: parsear el DOM renderizado solo si el BFF no devuelve datos.
  6. Almacenar via Store + checkpoints incrementales.
"""
from __future__ import annotations

import logging
import random
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from src.core.browser import launch_context, close_context, PROFILE_DIR
from src.core.human import delay, mouse_jitter, scroll_slow
from src.core.models import JobOffer, now_utc_iso
from src.core.normalize import extract_skills
from src.core.store import Store
from . import serp
from . import bff
from . import detail as detail_parser
from .config_gd import DEFAULT_CONFIG

log = logging.getLogger("glassdoor")

try:
    from patchright._impl._errors import TargetClosedError
except ImportError:
    try:
        from playwright._impl._errors import TargetClosedError
    except ImportError:
        TargetClosedError = Exception

_LANDING_MARKERS = [
    "tu búsqueda de empleo empieza aquí",
    "your job search starts here",
    "jobs_for_you",
    "recommended jobs for you",
]
_ANTIBOT_MARKERS = [
    "humans only", "security protections", "just a moment",
    "checking your browser", "attention required",
]


def _apply_stealth(page) -> None:
    """Aplica stealth playwright-stealth si esta disponible."""
    try:
        from playwright_stealth import Stealth
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        log.info("playwright-stealth aplicado")
    except ImportError:
        log.warning("playwright-stealth no disponible")


def _clean_profile(profile_name: str = "glassdoor") -> None:
    """Elimina el perfil persistente para empezar fresco."""
    profile_path = PROFILE_DIR / profile_name
    if profile_path.exists():
        try:
            shutil.rmtree(profile_path, ignore_errors=True)
            log.info("Perfil '%s' eliminado para arranque limpio", profile_name)
        except Exception as e:
            log.debug("No se pudo limpiar perfil: %s", e)


class GlassdoorScraper:
    """Scraper de Glassdoor USA con Playwright/patchright + BFF."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or DEFAULT_CONFIG
        out = self.config.get("output", {})
        project_root = Path(__file__).resolve().parent.parent.parent
        self.store = Store(
            csv_path=project_root / out.get("csv", "data/glassdoor/output/jobs.csv"),
            parquet_path=project_root / out.get("parquet", "data/glassdoor/output/jobs.parquet"),
            checkpoint_dir=project_root / out.get("checkpoint_dir", "data/glassdoor/checkpoints"),
        )
        self.target = self.config.get("target", 1000)
        self._sig_stop = False
        self._csrf_token = ""

    def run(self) -> int:
        roles = self.config.get("roles", [])
        base_url = self.config["base_url"]
        search_path = self.config.get("search_path", "/Job/jobs.htm")
        date_filter = self.config.get("date_filter", "")
        max_detail = self.config.get("max_detail_jobs", 0)
        do_detail = self.config.get("do_detail", False)
        max_pages = self.config.get("max_pages", 15)
        jobs_per_search = self.config.get("jobs_per_search", 200)
        site = self.config.get("site", "glassdoor")
        country = self.config.get("country", "US")
        country_name = self.config.get("country_name", "United States")
        location_name = self.config.get("location_name", "")
        locale = self.config.get("locale", "en-US")
        timezone = self.config.get("timezone", "America/New_York")
        profile_name = self.config.get("profile_name", site)

        delays_cfg = self.config.get("delays", {})
        post_goto = tuple(delays_cfg.get("post_goto", [4.0, 7.0]))
        step_px = tuple(delays_cfg.get("scroll_step_px", [200, 400]))
        scroll_pause = tuple(delays_cfg.get("between_scrolls", [1.5, 3.5]))
        between_pages = tuple(delays_cfg.get("between_pages", [4.0, 8.0]))
        between_details = tuple(delays_cfg.get("between_details", [4.0, 9.0]))
        between_searches = tuple(delays_cfg.get("between_searches", [10.0, 18.0]))

        log.info("=== Glassdoor Scraper: %d roles ===", len(roles))
        log.info("Roles: %s", ", ".join(roles))
        log.info("Paginas max/rol: %d | detalle: %s | jobs_per_search: %d | ubicacion: %s",
                 max_pages, do_detail, jobs_per_search, location_name or country_name)

        total_new = 0
        browser_crashes = 0
        max_browser_restarts = 3

        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            from playwright.sync_api import sync_playwright  # type: ignore

        pw = sync_playwright().start()

        interrupted = False
        try:
            context, page = self._launch_browser(
                pw, locale, timezone, profile_name, clean=False)
            effective_base_url, self._csrf_token = self._warmup_homepage(
                page, base_url, search_path, date_filter)
            log.info("CSRF token: %s",
                     (self._csrf_token[:20] + "...") if self._csrf_token else "NO")

            loc_term = location_name or country_name
            loc_id, loc_type = self._resolve_location(
                context, effective_base_url, loc_term)

            for idx, role in enumerate(roles):
                if self._sig_stop:
                    break

                log.info("--- [%d/%d] '%s' ---", idx + 1, len(roles), role)
                time.sleep(random.uniform(2.0, 5.0))

                for attempt in range(3):
                    try:
                        n = self._process_role(
                            page=page, context=context, role=role,
                            base_url=effective_base_url, search_path=search_path,
                            date_filter=date_filter, loc_id=loc_id, loc_type=loc_type,
                            do_detail=do_detail, max_detail=max_detail,
                            max_pages=max_pages, jobs_per_search=jobs_per_search,
                            post_goto=post_goto, step_px=step_px,
                            scroll_pause=scroll_pause, between_pages=between_pages,
                            between_details=between_details,
                            site=site, country=country, country_name=country_name,
                        )
                        break
                    except TargetClosedError:
                        log.warning("Navegador cerrado durante '%s'. Reintentando (%d/3)...",
                                    role, attempt + 1)
                        if attempt < 2:
                            try:
                                close_context(context)
                            except Exception:
                                pass
                            browser_crashes += 1
                            if browser_crashes >= max_browser_restarts:
                                log.error("Demasiados crashes de navegador. Abortando.")
                                self._sig_stop = True
                                n = 0
                                break
                            time.sleep(random.uniform(5.0, 10.0))
                            context, page = self._launch_browser(
                                pw, locale, timezone, profile_name, clean=False)
                            effective_base_url, self._csrf_token = self._warmup_homepage(
                                page, base_url, search_path, date_filter)
                            loc_id, loc_type = self._resolve_location(
                                context, effective_base_url, loc_term)
                        else:
                            log.error("No se pudo recuperar tras 3 intentos para '%s'.", role)
                            n = 0
                            break
                    except Exception as e:
                        log.error("Error en '%s': %s", role, e)
                        self.store.write_all()
                        n = 0
                        break

                total_new += n
                log.info("'%s' completado: %d ofertas nuevas (total store: %d)",
                         role, n, len(self.store))

                if len(self.store) >= self.target:
                    log.info("Objetivo de %d ofertas alcanzado. Fin.", self.target)
                    break

                if idx < len(roles) - 1 and not self._sig_stop:
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
                close_context(context)
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass

        if interrupted:
            self.store.checkpoint(tag=f"{site}_interrupted")
        self.store.write_all()
        if interrupted:
            log.info("=== %s INTERRUMPIDO: %d ofertas nuevas, %d total en store ===",
                     site.upper(), total_new, len(self.store))
            return total_new
        log.info("=== GLASSDOOR DONE: %d ofertas nuevas, %d total en store ===",
                 total_new, len(self.store))
        return total_new

    def _resolve_location(self, context, base_url: str,
                          location_name: str) -> tuple[Optional[int], str]:
        """Resuelve la ubicacion objetivo; fallback (1, COUNTRY) = USA nacional."""
        loc_id, loc_type = bff.resolve_location(context.request, base_url,
                                                location_name)
        if loc_id:
            log.info("Ubicacion '%s' -> id=%s type=%s", location_name, loc_id, loc_type)
            return loc_id, loc_type
        log.warning("No se pudo resolver '%s'; usando busqueda nacional (1, COUNTRY)",
                    location_name)
        return 1, "COUNTRY"

    def _process_role(self, page, context, role, base_url, search_path,
                      date_filter, loc_id, loc_type, do_detail, max_detail,
                      max_pages, jobs_per_search, post_goto,
                      step_px, scroll_pause, between_pages,
                      between_details, site, country, country_name) -> int:
        total = 0
        consecutive_empty = 0
        cursor: Optional[str] = None
        fromage = None
        fetch_desc = self.config.get("fetch_descriptions", False)
        max_desc = self.config.get("max_description_jobs", 100)
        desc_fetched = 0
        if date_filter:
            try:
                fromage = int(date_filter)
            except (TypeError, ValueError):
                fromage = None

        for pg in range(1, max_pages + 1):
            if self._sig_stop:
                break
            if consecutive_empty >= 2:
                log.info("2 paginas vacias consecutivas. Fin de resultados para '%s'.",
                         role)
                break
            if len(self.store) >= self.target:
                log.info("Objetivo de %d ofertas alcanzado. Deteniendo busqueda.",
                         self.target)
                break

            serp_url = serp.build_search_url(base_url, search_path, role, pg,
                                             date_filter)
            log.info("SERP '%s' page %d: %s", role, pg, serp_url)

            try:
                page.goto(serp_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
            except Exception as e:
                log.error("Error navegando a SERP '%s': %s", role, e)
                break

            if self._check_blocked_or_landing(page, role):
                break

            time.sleep(random.uniform(1.0, 2.0))
            serp.dismiss_overlays(page)
            delay(post_goto, label="post-goto-serp")
            mouse_jitter(page, n=random.randint(1, 2))

            listings, cursor_next, total_count = bff.search_jobs(
                context.request, base_url, self._csrf_token,
                keyword=role, location_id=loc_id, location_type=loc_type,
                page_num=pg, cursor=cursor, fromage=fromage,
                num_jobs=30, original_page_url=serp_url,
                user_agent=self._page_ua(page),
            )

            cards = []
            for jv in listings:
                card = bff.parse_jobview(jv, base_url, country_name)
                if card:
                    cards.append(card)
            source = "bff"
            if cards:
                log.info("BFF page %d '%s': %d ofertas (total=%s)",
                         pg, role, len(cards), total_count)

            if not cards:
                scroll_slow(page, step_px_rng=step_px, max_steps=5,
                            pause_rng=scroll_pause)
                serp.dismiss_overlays(page)
                cards = serp.parse_cards(page)
                source = "dom"
                if cards:
                    log.info("DOM page %d '%s': %d tarjetas", pg, role, len(cards))

            if not cards:
                if self._check_blocked_or_landing(page, role):
                    break
                log.info("Sin ofertas en page %d de '%s'.", pg, role)
                consecutive_empty += 1
                continue

            consecutive_empty = 0
            processed_this_page = 0
            for i, raw in enumerate(cards[:jobs_per_search]):
                if self._sig_stop:
                    break
                if len(self.store) >= self.target:
                    break

                offer = self._card_to_offer(raw, role, site, country, country_name)
                is_new = self.store.add(offer)
                if is_new:
                    total += 1
                    processed_this_page += 1

                if (fetch_desc and is_new and desc_fetched < max_desc
                        and not offer.description_full):
                    desc = bff.fetch_description(
                        context.request, base_url, self._csrf_token,
                        raw.get("job_id", "").replace("gd_", ""),
                        user_agent=self._page_ua(page))
                    if desc:
                        offer.description_full = desc
                        offer.description_snippet = desc[:500]
                        if not offer.skills:
                            offer.skills = extract_skills(desc)
                        desc_fetched += 1
                    time.sleep(random.uniform(0.8, 1.8))

                if (do_detail and is_new and total <= max_detail
                        and offer.job_url and not offer.description_full):
                    delay(between_details, label="pre-detail")
                    try:
                        extra = detail_parser.parse_detail(page, raw)
                        self._enrich_offer(offer, extra)
                    except Exception as e:
                        log.debug("Detalle fallido %s: %s", offer.job_id, e)

                if (i + 1) % 10 == 0 and total > 0:
                    self.store.checkpoint(tag=f"gd_{role}_p{pg}_{i+1}")

            log.info("Page %d '%s': %d procesadas (%d nuevas, fuente=%s)",
                     pg, role, len(cards), processed_this_page, source)

            if total > 0:
                self.store.checkpoint(tag=f"gd_{role}_p{pg}")

            if source == "bff":
                if not listings:
                    log.info("BFF sin listados en page %d. Fin para '%s'.", pg, role)
                    break
                cursor = cursor_next
                if not cursor and pg >= max_pages:
                    break
            else:
                if not serp.has_next_page(page):
                    log.info("Sin pagina siguiente (DOM) para '%s'. Fin.", role)
                    break
                delay(between_pages, label="next-page")
                if not serp.go_to_next_page(page):
                    log.info("No se pudo navegar a la pagina siguiente de '%s'.", role)
                    break
                serp.dismiss_overlays(page)
                continue

            delay(between_pages, label="next-page")

        return total

    @staticmethod
    def _page_ua(page) -> str:
        try:
            return page.evaluate("() => navigator.userAgent")
        except Exception:
            return ""

    def _check_blocked_or_landing(self, page, role: str) -> bool:
        """Detecta anti-bot, landing vacia o paginas de error. True si hay que parar."""
        try:
            body_text = page.evaluate(
                "() => document.body ? document.body.innerText.substring(0, 1200) : ''")
        except Exception:
            body_text = ""
        low = body_text.lower()
        for marker in _ANTIBOT_MARKERS:
            if marker in low:
                log.error("Anti-bot de Glassdoor activado ('%s'). "
                          "Prueba sin --headless o con proxy de USA.", marker)
                return True
        url_low = page.url.lower()
        if any(k in url_low for k in ("jobs_for_you", "index.htm")):
            log.warning("Glassdoor mostro la landing vacia ('%s'). "
                        "Revisa la ubicacion/parametros de busqueda.", role)
            return True
        for marker in _LANDING_MARKERS:
            if marker in low and len(body_text) < 3000:
                log.warning("Glassdoor mostro una pagina vacia ('%s') para '%s'.",
                            marker, role)
                return True
        return False

    def _card_to_offer(self, raw: dict, role: str, site: str,
                       country: str, country_name: str) -> JobOffer:
        city, region, loc_country = bff.parse_location_raw(
            raw.get("location_raw", ""))
        loc_country = loc_country or country_name

        desc_full = raw.get("description_full", "")
        skills = list(raw.get("skills") or [])
        if desc_full and not skills:
            skills = extract_skills(desc_full)

        # Defensa: nunca usar un rating o URL como nombre de empresa
        # (bug historico: company_name='4,1' cuando la API deja el rating
        # en employerNameFromSearch). Si pasa, se descarta la empresa.
        company = raw.get("company_name", "") or ""
        if re.match(r"^\d[,.]\d{1,2}$", company.strip()) or \
           re.match(r"^https?://", company.strip()):
            company = ""

        return JobOffer(
            job_id=raw.get("job_id", ""),
            job_url=raw.get("job_url", ""),
            title=raw.get("title", ""),
            company_name=company,
            company_url=raw.get("company_url", ""),
            location_raw=raw.get("location_raw", ""),
            location_city=city,
            location_region=region,
            location_country=loc_country,
            posted_datetime=raw.get("posted_datetime") or None,
            posted_relative=raw.get("posted_relative", ""),
            is_new=raw.get("is_new", False),
            description_full=raw.get("description_full", ""),
            description_snippet=raw.get("description_snippet", ""),
            salary_raw=raw.get("salary_raw", ""),
            salary_raw_original=raw.get("salary_raw_original", ""),
            salary_min=raw.get("salary_min"),
            salary_max=raw.get("salary_max"),
            salary_currency=raw.get("salary_currency", ""),
            salary_period=raw.get("salary_period", ""),
            salary_source=raw.get("salary_source", ""),
            salary_is_estimated=raw.get("salary_is_estimated", False),
            employment_type=raw.get("employment_type", ""),
            work_mode=raw.get("work_mode", ""),
            skills=skills,
            site=site,
            country=country,
            search_role=role,
            search_city="",
            source="local",
            scraped_at=now_utc_iso(),
        )

    def _enrich_offer(self, offer: JobOffer, extra: dict) -> None:
        for field in [
            "description_full", "requirements", "responsibilities",
            "benefits", "company_description", "salary_raw",
            "employment_type", "work_mode", "company_url", "experience_level",
        ]:
            val = extra.get(field)
            if val and not getattr(offer, field, None):
                setattr(offer, field, val)

    def stop(self) -> None:
        """Senal de parada para detener el scraper limpiamente."""
        self._sig_stop = True

    def _launch_browser(self, pw, locale, timezone, profile_name, clean=False):
        """Lanza el navegador con perfil persistente y stealth."""
        import os as _os
        if not _os.getenv("HEADLESS_I_KNOW_WHAT_IM_DOING"):
            _os.environ["HEADLESS"] = "false"
            log.info("Forzando modo headful")

        if clean or self.config.get("clean_profile", False):
            _clean_profile(profile_name)

        context = launch_context(pw, locale=locale, timezone=timezone,
                                 profile_name=profile_name)
        page = context.new_page()
        _apply_stealth(page)

        context.grant_permissions(["geolocation"])
        context.set_geolocation({"latitude": 40.7128, "longitude": -74.0060})

        # Interceptar countryRedirect: evitar que Glassdoor cambie de pais
        # silenciosamente a .es/.co.uk cuando el target es USA.
        def _block_country_redirect(request):
            url = request.url
            if "countryRedirect=true" in url:
                new_url = url.replace("&countryRedirect=true", "").replace(
                    "?countryRedirect=true&", "?").replace("?countryRedirect=true", "")
                return request.respond(status=302, headers={"Location": new_url})
            return request.fallback()

        page.route("**/*countryRedirect=true*", _block_country_redirect)

        return context, page

    def _warmup_homepage(self, page, base_url, search_path, date_filter):
        """Visita la homepage para calentar cookies y extraer el CSRF token.

        Devuelve (base_url_efectiva, csrf_token).
        """
        try:
            log.info("Visitando homepage %s para calentar cookies...", base_url)
            page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(random.uniform(3.0, 6.0))
            serp.dismiss_overlays(page)

            html = ""
            try:
                html = page.content()
            except Exception:
                pass
            token = bff.extract_csrf_token(html)
            if not token:
                log.warning("No se encontro CSRF token en la homepage")
            else:
                log.info("CSRF token extraido (%d chars)", len(token))

            current_domain = page.url
            if "glassdoor.es" in current_domain or "glassdoor.co.uk" in current_domain:
                log.warning("Detectado dominio regional %s; el target es USA. "
                            "El scraper seguira usando la configuracion original.",
                            current_domain[:60])
            return base_url, token
        except Exception as e:
            log.warning("Homepage no disponible: %s", e)
        return base_url, bff.extract_csrf_token("")