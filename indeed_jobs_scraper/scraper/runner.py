"""Orquestador del scraper: navega termino -> pais -> ciudad -> N paginas, dedup y export.

Flujo:
  1. Abre BrowserSession (patchright headed + stealth + sesion persistente) UNA VEZ por run.
  2. Por cada (termino, ciudad, pagina):
     a. delay_pre_nav()
     b. page.goto(url)  (usa start=15*(n-1) para paginas >1; evita clickar paginacion)
     c. detect_captcha() -> si salta, screenshot + log + aborta limpio
     d. human_scroll()   -> scrolling muy poco a poco para cargar todas las cards
     e. parse_serp()     -> extrae JSON embebido del HTML y mapea a JobOffer
     f. delay_between_pages() / delay_between_cities() / delay_between_terms()
  3. Dedup por job_key (un job remoto puede salir en varias ciudades/terminos).
  4. Exporta a Parquet, Delta, JSONL, JSON, XLSX, CSV + metadata.
"""
from __future__ import annotations

import json
import logging
import random
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .anti_bot import (
    CaptchaDetected,
    backoff_delay,
    delay_between_cities,
    delay_between_countries,
    delay_between_pages,
    delay_between_terms,
    delay_pre_nav,
    detect_captcha,
    human_scroll,
    should_retry,
)
from .browser import BrowserSession
from .config import COUNTRIES, CountryConfig, DESC_CACHE_FILE, MAX_SERPS_PER_RUN, RESULTS_PER_PAGE
from .desc_cache import load_cache, save_cache, update_cache_from_offers
from .models import JobOffer
from .parser import parse_serp, enrich_offers_via_right_panel
from .state import RunState
from . import schema as job_schema
from . import exporters



log = logging.getLogger(__name__)


class ScraperRunner:
    def __init__(self, country_code: str, cities: list[str], terms: list[str], pages: int, output_dir: Path, limit: int = 0, login: bool = False, use_chrome_profile: bool = False, cdp_port: int = 0, enrich: bool = False, fromage: int = 0, remote: bool = False, job_type: str = "", pause_every: int = 0, pause_min: float = 5.0, pause_max: float = 8.0, enrich_rate: float = 0.0, enrich_max: int = 0):
        self.country: CountryConfig = COUNTRIES[country_code]
        self.cities = cities
        self.terms = terms
        self.pages = pages
        self.limit = limit
        self.login = login
        self.use_chrome_profile = use_chrome_profile
        self.cdp_port = cdp_port
        self.enrich = enrich
        self.fromage = fromage
        self.remote = remote
        self.job_type = job_type
        self.pause_every = pause_every
        self.pause_min = pause_min
        self.pause_max = pause_max
        self.enrich_rate = enrich_rate
        self.enrich_max = enrich_max
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        self.started_at = datetime.now(tz=timezone.utc)
        self._logged_in = False
        self._by_city: dict[str, int] = {}
        self._serps_completed = 0
        self.captcha_aborted = False

        self.state = RunState(output_dir)
        self.desc_cache = load_cache(output_dir / DESC_CACHE_FILE)
        self._enrich_attempted = 0
        self._enrich_success = 0

        total_serps = len(cities) * pages * len(terms)
        if total_serps > MAX_SERPS_PER_RUN:
            raise ValueError(
                f"Volumen too high sin proxy: {total_serps} SERPs > {MAX_SERPS_PER_RUN}. "
                f"Reduce ciudades, paginas o terminos, o anade proxies."
            )

    def run(self) -> list[JobOffer]:
        all_offers: list[JobOffer] = []
        with BrowserSession(self.country, self.output_dir, use_chrome_profile=self.use_chrome_profile, cdp_port=self.cdp_port) as session:
            if self.login and not self._logged_in and self.cdp_port == 0:
                self._do_assisted_login(session)
            elif self.cdp_port > 0:
                self._logged_in = True
                log.info("modo CDP: asumiendo que el usuario ya esta logueado en Chrome")
                if not self._verify_cdp_session(session):
                    log.error("sesion CDP expirada. Abortando.")
                    return []

            self._serps_completed = 0
            first_term = True
            for term in self.terms:
                if not first_term:
                    delay_between_terms()
                first_term = False

                first_city = True
                for city in self.cities:
                    if not first_city:
                        delay_between_cities()
                    first_city = False
                    try:
                        offers = self._scrape_city(city, term, session)
                        all_offers.extend(offers)
                        key = f"{term}|{city}"
                        self._by_city[key] = len(offers)
                        self.state.checkpoint(f"{term}_{city}", offers)
                        save_cache(self.output_dir / DESC_CACHE_FILE, self.desc_cache)
                    except CaptchaDetected as e:
                        self._handle_captcha(e, city, page_num=1)
                        self.captcha_aborted = True
                        log.error("Abortando ejecucion por CAPTCHA. Ofertas conseguidas hasta ahora: %d", len(all_offers))
                        break

        total_before = len(all_offers)
        all_offers = self._dedup(all_offers)
        for o in all_offers:
            o.fill_posted_date_typed()
        self.state.save()
        save_cache(self.output_dir / DESC_CACHE_FILE, self.desc_cache)
        self._export(all_offers, total_before)
        return all_offers

    def _maybe_long_pause(self) -> None:
        if self.pause_every <= 0:
            return
        self._serps_completed += 1
        if self._serps_completed % self.pause_every == 0:
            duration = random.uniform(self.pause_min, self.pause_max)
            log.info("pausa larga %.1f min (SERP %d/%d)...", duration, self._serps_completed, self.pause_every)
            time.sleep(duration * 60)

    def _scrape_city(self, city: str, term: str, session) -> list[JobOffer]:
        city_offers: list[JobOffer] = []
        log.info("=== %s / %s / termino=%r ===", self.country.code, city, term)

        for page_num in range(1, self.pages + 1):
            if page_num > 1:
                delay_between_pages()
            start = (page_num - 1) * RESULTS_PER_PAGE
            url = self.country.url(term, city, start=start, limit=self.limit, fromage=self.fromage, remote=self.remote, job_type=self.job_type)

            attempt = 0
            while True:
                attempt += 1
                delay_pre_nav()
                log.info("navegando pagina %d (start=%d): %s", page_num, start, url)
                page = session.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    if self._is_login_redirect(page):
                        log.warning(
                            "Indeed redirige a login en pagina %d. Para paginar necesitas "
                            "una cuenta: ejecuta con --login (login asistido). Se omite esta pagina.",
                            page_num,
                        )
                        print(
                            f"\n[!] Indeed pide iniciar sesion para la pagina {page_num} de {city}.\n"
                            f"    Crea una cuenta en indeed.com y ejecuta: python main.py --login ...\n"
                            f"    El scraper abrira el navegador para que te loguees a mano.\n",
                            file=sys.stderr,
                        )
                        page.close()
                        break
                    try:
                        page.wait_for_selector(
                            'div.cardOutline, [data-testid="slider_container"]',
                            timeout=20000,
                        )
                    except Exception:
                        log.warning("no aparecieron job cards en 20s; continuamos igualmente")

                    detect_captcha(page)
                    human_scroll(page)
                    detect_captcha(page)

                    offers = parse_serp(
                        page, self.country.code, city, term, page_num, self.country.domain
                    )
                    if len(offers) > 0 and (self.enrich or self.enrich_rate > 0 or self.enrich_max > 0):
                        attempted, success = enrich_offers_via_right_panel(
                            page, offers, self.country.domain,
                            rate=self.enrich_rate,
                            state=self.state,
                            cache=self.desc_cache,
                            max_enrich=self.enrich_max,
                        )
                        self._enrich_attempted += attempted
                        self._enrich_success += success
                        update_cache_from_offers(self.desc_cache, offers)
                    if len(offers) == 0:
                        if attempt < 2:
                            log.warning("pagina %d: 0 ofertas, reintentando (intento %d/2)...", page_num, attempt)
                            page.close()
                            time.sleep(10)
                            continue
                        self._dump_diagnostics(page, city, page_num)
                    city_offers.extend(offers)
                    jks = [o.job_key for o in offers if o.job_key]
                    self.state.mark_processed(jks)
                    log.info("pagina %d: %d ofertas (acumulado ciudad: %d)", page_num, len(offers), len(city_offers))
                    self._maybe_long_pause()
                    break
                except CaptchaDetected as e:
                    page.close()
                    if should_retry(attempt):
                        log.warning("CAPTCHA en pagina %d, intento %d. Backoff y nueva sesion.", page_num, attempt)
                        backoff_delay()
                        continue
                    raise
                except Exception as e:
                    is_timeout = type(e).__name__ == "TimeoutError"
                    self._save_screenshot(page, city, page_num, suffix="error")
                    page.close()
                    if is_timeout and attempt < 3:
                        wait_s = random.uniform(20.0, 45.0)
                        log.warning(
                            "timeout en pagina %d (intento %d/3); esperando %.0fs y reintentando",
                            page_num, attempt, wait_s,
                        )
                        time.sleep(wait_s)
                        continue
                    log.exception("error inesperado en pagina %d: %s", page_num, e)
                    break
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
        return city_offers

    # --- Helpers -----------------------------------------------------------
    def _dedup(self, offers: list[JobOffer]) -> list[JobOffer]:
        seen: set[str] = set()
        unique: list[JobOffer] = []
        for o in offers:
            if not o.job_key or o.job_key in seen:
                continue
            seen.add(o.job_key)
            unique.append(o)
        if len(unique) < len(offers):
            log.info("dedup: %d -> %d ofertas unicas", len(offers), len(unique))
        return unique

    def _verify_cdp_session(self, session) -> bool:
        """Verifica que la sesion CDP sigue logueada navegando a pagina 2."""
        try:
            test_url = self.country.url(self.terms[0], self.cities[0] if self.cities else "Madrid", start=10)
            page = session.new_page()
            try:
                page.goto(test_url, wait_until="domcontentloaded", timeout=15000)
                if self._is_login_redirect(page):
                    log.error(
                        "sesion CDP expirada: Indeed redirige a login. "
                        "Vuelve a loguearte en Chrome y re-ejecuta."
                    )
                    print(
                        "\n[!] Sesion CDP EXPIRADA. Indeed pide login en pagina 2.\n"
                        "    Vuelve a iniciar sesion en tu Chrome y re-ejecuta el scraper.\n",
                        file=sys.stderr,
                    )
                    return False
                return True
            finally:
                page.close()
        except Exception as e:
            log.warning("no se pudo verificar sesion CDP: %s; continuando", e)
            return True

    # --- Login asistido ----------------------------------------------------
    def _is_login_redirect(self, page) -> bool:
        """Detecta si Indeed redirigio a la pagina de login (impide paginar)."""
        try:
            url = (page.url or "").lower()
            if "secure.indeed.com/auth" in url:
                return True
            title = (page.title() or "").lower()
            if "iniciar sesi" in title and "cuentas indeed" in title:
                return True
            if "sign in" in title and "indeed" in title and "auth" in url:
                return True
        except Exception:
            pass
        return False

    def _do_assisted_login(self, session) -> None:
        """Login manual asistido con verificacion real (perfil dedicado de Chrome).

        Flujo robusto (sin falsos positivos):
          1. Abre la pagina de login REAL de Indeed (secure.indeed.com/auth).
             El usuario se loguea a mano (email/password o "Continuar con Google").
          2. Espera hasta que la URL SALGA de secure.indeed.com/auth (login redirige).
          3. VERIFICACION: navega a una URL de pagina 2 de prueba (start=10).
             Si NO redirige a /auth, el login es real y se puede paginar.
             Si redirige, el login no completo: vuelve a esperar al usuario.
          4. Repite la verificacion hasta pasar o timeout (8 min total).
        """
        import time as _time

        print(
            "\n" + "=" * 60 + "\n"
            "LOGIN ASISTIDO\n"
            "Se abrira la pagina de login de Indeed.\n"
            "1. Inicia sesion (email+password o 'Continuar con Google').\n"
            "2. Resuelve el CAPTCHA si aparece.\n"
            "3. Espera a volver a Indeed ya logueado.\n"
            "El scraper verificara el login navegando a una pagina 2 de prueba.\n"
            "Timeout: 8 minutos.\n"
            + "=" * 60,
            file=sys.stderr,
        )

        login_url = (
            f"https://secure.indeed.com/auth?co={self.country.code}"
            f"&hl={self.country.locale.replace('-', '_')}"
        )
        page = session.new_page()
        try:
            page.goto(login_url, wait_until="domcontentloaded")
        except Exception as e:
            log.warning("no se pudo abrir la pagina de login: %s", e)

        deadline = _time.time() + 480  # 8 min total
        verified = False
        last_url = ""

        while _time.time() < deadline and not verified:
            try:
                cur = (page.url or "").lower()
                if cur != last_url:
                    log.info("login: URL actual=%s", cur[:100])
                    last_url = cur

                # Paso A: esperar a que la URL salga de /auth (login redirige)
                if "secure.indeed.com/auth" in cur:
                    _time.sleep(3)
                    continue

                # Paso B: la URL salio de /auth. Verificar de verdad navegando
                # a una pagina 2 (start=10). Si no redirige a /auth, login real.
                _time.sleep(2)  # asentar cookies tras la redireccion
                log.info("login: URL salio de /auth, verificando con pagina 2 de prueba...")
                test_url = self.country.url(self.terms[0], self.cities[0] if self.cities else "Madrid", start=10)
                try:
                    page.goto(test_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    log.warning("login: navegacion de prueba fallo: %s", e)
                    _time.sleep(3)
                    continue

                test_cur = (page.url or "").lower()
                if "secure.indeed.com/auth" in test_cur:
                    log.warning("login: la verificacion redirigio a /auth. Login no completo. Esperando de nuevo al usuario...")
                    print("Login no completo (pagina 2 sigue pidiendo login). Termina de loguearte en Indeed...", file=sys.stderr)
                    # volver a la pagina de login para que el usuario reintente
                    try:
                        page.goto(login_url, wait_until="domcontentloaded")
                    except Exception:
                        pass
                    _time.sleep(5)
                    continue

                # verificacion OK: pagina 2 carga sin redirigir a login
                log.info("login: VERIFICADO. Pagina 2 accesible sin login. Login real.")
                verified = True

            except Exception as e:
                log.debug("login: error en bucle: %s", e)
                _time.sleep(3)

        if verified:
            self._logged_in = True
            log.info("login asistido VERIFICADO; perfil guardado para paginar sin limite")
            print("Login VERIFICADO. Continuando con el scraping de 2 paginas...", file=sys.stderr)
        else:
            log.warning("timeout de login (8 min) sin verificar login completo; paginacion puede fallar")
            print("Timeout: no se verifico login en 8 min. Continuando igualmente...", file=sys.stderr)

        try:
            page.close()
        except Exception:
            pass

    def _handle_captcha(self, e: CaptchaDetected, city: str, page_num: int) -> None:
        log.error("CAPTCHA detectado en %s pagina %d: %s", city, page_num, e)
        # el screenshot lo intenta el caller si tiene page; aqui solo logueamos
        # avisamos al usuario por stderr de forma clara
        print(
            f"\n[!] CAPTCHA/CHALLENGE DETECTADO en {self.country.code}/{city} pag {page_num}.\n"
            f"    Razon: {e.reason}\n"
            f"    URL: {e.url}\n"
            f"    Se ha detenido el scraper para no exponer la IP.\n"
            f"    Reintenta mas tarde o anade proxies residenciales.\n",
            file=sys.stderr,
        )

    def _save_screenshot(self, page, city: str, page_num: int, suffix: str = "") -> None:
        try:
            name = f"screenshot_{self.country.code}_{city}_{page_num}_{self.timestamp}"
            if suffix:
                name += f"_{suffix}"
            path = self.output_dir / f"{name}.png"
            page.screenshot(path=str(path), full_page=True)
            log.warning("screenshot guardado: %s", path)
        except Exception as ex:
            log.debug("no se pudo guardar screenshot: %s", ex)

    def _dump_diagnostics(self, page, city: str, page_num: int) -> None:
        """Cuando parse_serp devuelve 0 ofertas, vuelca screenshot + HTML + info
        para diagnosticar si Indeed mostro un challenge, cambio la estructura, etc."""
        stem = f"diag_{self.country.code}_{city}_{page_num}_{self.timestamp}"
        # screenshot
        try:
            spath = self.output_dir / f"{stem}.png"
            page.screenshot(path=str(str(spath)), full_page=True)
            log.warning("diagnostico screenshot: %s", spath)
        except Exception as ex:
            log.debug("no se pudo guardar screenshot diagnostico: %s", ex)
        # info de la pagina
        try:
            title = page.title()
            url = page.url
            log.warning("diagnostico: title=%r url=%r", title, url)
        except Exception:
            title = url = "?"
        # comprobar que variables globales existen
        try:
            info = page.evaluate(
                """() => {
                    const out = {};
                    out.hasInitialData = typeof window._initialData !== 'undefined';
                    out.hasMosaic = typeof window.mosaic !== 'undefined';
                    out.initialDataKeys = window._initialData ? Object.keys(window._initialData).slice(0,20) : null;
                    out.mosaicKeys = window.mosaic ? Object.keys(window.mosaic).slice(0,20) : null;
                    out.jobCardCount = document.querySelectorAll('div[id^="job_"]').length;
                    out.cardOutlineCount = document.querySelectorAll('div.cardOutline, [class*="cardOutline"]').length;
                    out.sliderCount = document.querySelectorAll('[data-testid="slider_container"]').length;
                    out.bodyClass = document.body ? document.body.className : '';
                    out.bodyTextStart = document.body ? document.body.innerText.slice(0,300) : '';
                    return out;
                }"""
            )
            log.warning("diagnostico info: %s", info)
        except Exception as ex:
            log.warning("diagnostico info no disponible: %s", ex)
            info = None
        # guardar HTML completo
        try:
            hpath = self.output_dir / f"{stem}.html"
            html = page.content()
            hpath.write_text(html, encoding="utf-8")
            log.warning("diagnostico HTML: %s (%d bytes)", hpath, len(html))
        except Exception as ex:
            log.debug("no se pudo guardar HTML diagnostico: %s", ex)

    # --- Export ------------------------------------------------------------
    def _export(self, offers: list[JobOffer], total_before_dedup: int) -> None:
        if not offers:
            log.warning("no hay ofertas que exportar")
            return

        # convertir a DataFrame tipado via schema + exporters
        df = job_schema.parse_typed_df([o.to_typed_dict() for o in offers])

        enriched_count = sum(1 for o in offers if o.description_html and len(o.description_html) > 50)
        described_count = sum(1 for o in offers if o.description_text or (o.description_html and len(o.description_html) > 50))

        meta = exporters.build_metadata(
            country=self.country.code,
            cities=self.cities,
            terms=self.terms,
            pages=self.pages,
            total_offers=total_before_dedup,
            total_unique=len(offers),
            by_city=self._by_city,
            started_at=self.started_at,
            limit=self.limit,
            logged_in=self._logged_in or self.cdp_port > 0,
            cdp_port=self.cdp_port,
            enrich_success_rate=f"{self._enrich_success}/{self._enrich_attempted}" if self.enrich or self.enrich_rate > 0 else "N/A",
            description_filled_rate=f"{described_count}/{len(offers)}",
            enrich_rate_target=self.enrich_rate,
            enrich_attempted=self._enrich_attempted,
            enrich_success=self._enrich_success,
        )

        exporters.export_all(
            df, meta, self.output_dir, self.timestamp,
            include_delta=True,
        )
