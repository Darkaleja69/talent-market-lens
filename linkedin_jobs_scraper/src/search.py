"""Construccion de URLs de busqueda y navegacion del SERP.

Soporta dos modos segun el layout detectado:
- **Guest**: scroll infinito (boton 'See more jobs').
- **Auth**: paginacion numerada (boton 'Siguiente' o URL &start=25*N).

Flujo por (role, ciudad):
  1. Construir URL: /jobs/search/?keywords=...&location=...&geoId=...&f_TPR=...
  2. page.goto con wait_until=domcontentloaded + networkidle.
  3. Esperar a que cargue la lista de resultados.
  4. Scroll lento (guest) o esperar tarjetas (auth, ~25 por pagina).
  5. Devolver la pagina lista para parse_serp.parse_serp().
"""
from __future__ import annotations

import logging
import urllib.request
from typing import Any
from urllib.parse import urlencode

from .human import delay, mouse_jitter, scroll_slow
from .login import BlockedException, _detect_recaptcha, _is_challenge_url
from .parse_serp import (
    SEL_JOB_CARD_AUTH,
    SEL_JOB_CARD_AUTH_ALT,
    SEL_JOB_CARD_GUEST,
    SEL_LIST_SENTINEL_AUTH,
    SEL_PAGINATION_NEXT,
    SEL_RESULT_COUNT_AUTH,
    SEL_RESULT_COUNT_GUEST,
    SEL_SHOW_MORE_GUEST,
    SEL_VIEWED_ALL_GUEST,
    SerpCardRaw,
    click_next_page,
    detect_layout,
    get_result_count,
    has_next_page,
    parse_serp,
)

log = logging.getLogger(__name__)

JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search/"

# --- SERP guest via API publica (sin login) ---
# La pagina /jobs/search redirige a /authwall para invitados cuando LinkedIn
# detecta navegacion automatizada/repetida. El endpoint guest, en cambio,
# devuelve fragmentos HTML con 10 tarjetas por start. Se inyectan con
# set_content() y se parsean con parse_serp (selectores guest ya verificados).
GUEST_SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_GUEST_PAGE_SIZE = 10
_GUEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _city_setting(config: dict[str, Any], city_cfg: dict[str, Any],
                  key: str, default: Any = None) -> Any:
    """Valor por ciudad si esta definido (p.ej. jobs_per_search/date_filter);
    si no, el global de config. Permite busquedas por pais con mas volumen."""
    if key in city_cfg:
        return city_cfg[key]
    return config.get(key, default)


def _fetch_guest_search_html(role: str, city_cfg: dict[str, str],
                             date_filter: str, start: int,
                             timeout: int = 30) -> str:
    """Descarga un fragmento HTML (10 tarjetas) del SERP guest. "" si falla."""
    params: dict[str, str] = {
        "keywords": role,
        "location": city_cfg["text"],
        "start": str(start),
    }
    geo_id = city_cfg.get("geoId")
    if geo_id:
        params["geoId"] = geo_id
    if date_filter:
        params["f_TPR"] = date_filter
    url = GUEST_SEARCH_API + "?" + urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": _GUEST_UA,
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.6",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log.warning("Guest API search fallo (start=%d): %s", start, e)
        return ""


def _run_search_guest_http(page, role: str, city_name: str,
                           city_cfg: dict[str, str],
                           config: dict[str, Any]) -> dict[str, Any]:
    """SERP invitado via API HTTP paginada (sin login). Acumula IDs unicos."""
    delays = config.get("delays", {})
    target = int(_city_setting(config, city_cfg, "jobs_per_search", 25))
    date_filter = _city_setting(config, city_cfg, "date_filter", "")
    pause = tuple(delays.get("between_scrolls", [1.5, 3.5]))

    all_cards: list[SerpCardRaw] = []
    seen: set[str] = set()
    start = 0
    # El API invitado devuelve HTTP 400 a partir de start=1000 (tope ~1000
    # resultados por busqueda). Nunca pedir por encima de 990.
    max_start = min(max(target, 10) + 100, 990)

    while len(seen) < target and start <= max_start:
        html = _fetch_guest_search_html(role, city_cfg, date_filter, start)
        if not html:
            break
        try:
            page.set_content(html)
        except Exception as e:  # noqa: BLE001
            log.warning("Guest API: set_content fallo (start=%d): %s", start, e)
            break
        cards = parse_serp(page, role, city_name, layout="guest")
        new = 0
        for c in cards:
            if c.job_id and c.job_id not in seen:
                seen.add(c.job_id)
                all_cards.append(c)
                new += 1
        log.info("Guest API start=%d -> %d tarjetas (%d nuevas, total=%d)",
                 start, len(cards), new, len(all_cards))
        if new == 0:
            break
        start += _GUEST_PAGE_SIZE
        delay(pause, "guest-api-pause")

    log.info("Guest API: %d tarjetas unicas para '%s' en %s",
             len(all_cards), role, city_name)
    return {"url": GUEST_SEARCH_API, "result_count": None,
            "cards_loaded": len(all_cards), "layout": "guest",
            "cards_raw": all_cards}


def build_search_url(role: str, city_cfg: dict[str, str], date_filter: str,
                     start: int = 0) -> str:
    """Construye la URL de busqueda de LinkedIn Jobs.

    Args:
        role: termino de busqueda (p.ej. "Data Analyst").
        city_cfg: {"text": "Madrid, Spain", "geoId": "103374081"}.
        date_filter: r86400 / r604800 / r2592000 / "".
        start: offset para paginacion (0, 25, 50, ...). Solo auth page.
    """
    params: dict[str, str] = {
        "keywords": role,
        "location": city_cfg["text"],
    }
    geo_id = city_cfg.get("geoId")
    if geo_id:
        params["geoId"] = geo_id
    if date_filter:
        params["f_TPR"] = date_filter
    if start > 0:
        params["start"] = str(start)
    return JOBS_SEARCH_URL + "?" + urlencode(params)


def _wait_for_results(page, timeout_ms: int = 30000) -> bool:
    """Espera a que aparezca la lista de resultados o el contador."""
    # Selectores auth (paginas autenticadas)
    auth_selectors = [
        SEL_LIST_SENTINEL_AUTH,
        SEL_JOB_CARD_AUTH,
        SEL_JOB_CARD_AUTH_ALT,
        SEL_RESULT_COUNT_AUTH,
    ]
    # Selectores guest (paginas publicas)
    guest_selectors = [
        "ul.jobs-search__results-list",
        SEL_JOB_CARD_GUEST,
        SEL_RESULT_COUNT_GUEST,
    ]
    # 1. Probar auth (mas probable si estamos logueados)
    for sel in auth_selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout_ms // 3)
            log.debug("Selector auth encontrado: %s", sel)
            return True
        except Exception:  # noqa: BLE001
            continue
    # 2. Probar guest
    for sel in guest_selectors:
        try:
            page.wait_for_selector(sel, timeout=timeout_ms // 3)
            log.debug("Selector guest encontrado: %s", sel)
            return True
        except Exception:  # noqa: BLE001
            continue
    # 3. Fallbacks genericos
    for sel in ["[class*='jobs-search-results']", "[class*='job-card-container']",
                "div.scaffold-layout__list"]:
        try:
            el = page.query_selector(sel)
            if el:
                log.info("Selector fallback encontrado: %s", sel)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _count_loaded_cards(page, layout: str = "auto") -> int:
    """Cuenta las tarjetas visibles segun el layout."""
    if layout == "auto":
        layout = detect_layout(page)
    sel = SEL_JOB_CARD_AUTH if layout == "auth" else SEL_JOB_CARD_GUEST
    try:
        n = len(page.query_selector_all(sel))
        if n == 0 and layout == "auth":
            n = len(page.query_selector_all(SEL_JOB_CARD_AUTH_ALT))
        return n
    except Exception:  # noqa: BLE001
        return 0


def _is_viewed_all(page) -> bool:
    """Guest: comprobar si se llego al final del scroll infinito."""
    el = page.query_selector(SEL_VIEWED_ALL_GUEST)
    if not el:
        return False
    try:
        cls = el.get_attribute("class") or ""
        return "hidden" not in cls
    except Exception:  # noqa: BLE001
        return False


def _check_block(page) -> None:
    """Lanza BlockedException si la pagina actual es un challenge real."""
    if _is_challenge_url(page.url) or _detect_recaptcha(page):
        raise BlockedException(
            f"Challenge/block detectado en {page.url}. Conmutando a Apify."
        )


def _save_debug_html(page, role: str, city_name: str) -> None:
    """Guarda el HTML actual para depurar selectores cuando falla la busqueda."""
    import datetime as dt
    from pathlib import Path
    try:
        project_root = Path(__file__).resolve().parent.parent
        raw_dir = project_root / "data" / "raw_html"
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_role = "".join(c if c.isalnum() else "_" for c in role)
        safe_city = "".join(c if c.isalnum() else "_" for c in city_name)
        p = raw_dir / f"debug_{ts}_{safe_role}_{safe_city}.html"
        p.write_text(page.content(), encoding="utf-8")
        log.info("HTML de depuracion guardado: %s (%d bytes)", p, p.stat().st_size)
    except Exception as e:  # noqa: BLE001
        log.debug("No se pudo guardar HTML de depuracion: %s", e)


def _goto_next_start(page, role: str, city_cfg: dict[str, str],
                     date_filter: str, page_num: int) -> bool:
    """Navega directamente a la pagina siguiente via ?start=25*N.

    Fallback robusto cuando el click en 'Siguiente' falla (boton superpuesto
    por el contenedor de paginacion) o navega sin cambiar la URL. La URL con
    start es el mecanismo nativo de paginacion de LinkedIn auth.
    Devuelve True si la navegacion se completo.
    """
    next_start = page_num * 25
    url = build_search_url(role, city_cfg, date_filter, start=next_start)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:  # noqa: BLE001
            log.debug("networkidle tras start=%d no alcanzado.", next_start)
        delay((2.0, 4.0), "post-next-page-direct")
        _check_block(page)
        return True
    except BlockedException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("Auth: fallo goto directo start=%d: %s", next_start, e)
        return False


def run_search(
    page,
    role: str,
    city_name: str,
    city_cfg: dict[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Navega a la busqueda (role, ciudad) y carga tarjetas del SERP.

    En auth page: iteracion de paginas (click Siguiente), acumulando IDs
    unicos entre paginas (LinkedIn reemplaza las tarjetas, no las acumula
    en el DOM — por eso usamos un set de job_ids como contador real).

    Devuelve un dict con:
      {"url", "result_count", "cards_loaded", "layout", "cards_raw"}.
      cards_raw contiene TODAS las tarjetas parseadas de todas las paginas.
    Lanza BlockedException si aparece challenge.
    """
    if config.get("_guest"):
        # Modo invitado: /jobs/search cae en /authwall; usar el API publico.
        log.info("Buscando (guest API) '%s' en %s", role, city_name)
        return _run_search_guest_http(page, role, city_name, city_cfg, config)

    delays = config.get("delays", {})
    date_filter = _city_setting(config, city_cfg, "date_filter", "")
    url = build_search_url(role, city_cfg, date_filter)
    target = int(_city_setting(config, city_cfg, "jobs_per_search", 25))

    log.info("Buscando '%s' en %s -> %s", role, city_name, url)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # SPA: dar tiempo a que JS renderice
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001
        log.debug("networkidle no alcanzado, continuando.")
    delay((2.0, 4.5), "post-goto-serp")
    mouse_jitter(page, n=1)
    _check_block(page)

    if not _wait_for_results(page):
        _check_block(page)
        log.warning("No aparecio lista de resultados para '%s' en %s.",
                    role, city_name)
        _save_debug_html(page, role, city_name)
        return {"url": url, "result_count": None, "cards_loaded": 0, "layout": "unknown"}

    layout = detect_layout(page)
    log.info("Layout detectado: %s", layout)

    count = get_result_count(page)
    log.info("Contador total para '%s' en %s: %s", role, city_name, count)

    loaded = _count_loaded_cards(page, layout)
    log.info("Tarjetas iniciales cargadas: %d", loaded)

    all_cards: list[SerpCardRaw] = []
    seen_ids: set[str] = set()

    if layout == "guest":
        # Guest: scroll infinito + click en "See more jobs" hasta target o fin.
        step_px_rng = tuple(delays.get("scroll_step_px", [200, 400]))
        pause_rng = tuple(delays.get("between_scrolls", [1.5, 3.5]))
        max_steps = config.get("scroll_max_steps", 25)
        steps = 0
        no_growth = 0
        while steps < max_steps and loaded < target:
            if _is_viewed_all(page):
                break
            clicked = False
            try:
                btn = page.query_selector(SEL_SHOW_MORE_GUEST)
                if btn and btn.is_visible():
                    btn.click(timeout=8000)
                    clicked = True
            except Exception:  # noqa: BLE001
                pass
            before = _count_loaded_cards(page, layout)
            if not clicked:
                scroll_slow(page, step_px_rng=step_px_rng, max_steps=1,
                            pause_rng=pause_rng)
            delay(pause_rng, label="serp-pause")
            steps += 1
            loaded = _count_loaded_cards(page, layout)
            if loaded <= before:
                no_growth += 1
                if no_growth >= 3:
                    break
            else:
                no_growth = 0
        log.info("Scroll guest terminado: %d tarjetas en %d pasos.", loaded, steps)
        # Guest: todas las tarjetas visibles en el DOM -> parseo unico
        all_cards = parse_serp(page, role, city_name, layout=layout)
        seen_ids = {c.job_id for c in all_cards if c.job_id}
        loaded = len(all_cards)
    else:
        # Auth: las tarjetas de la primera pagina ya estan cargadas (~25).
        # Scroll suave para que todas sean visibles (render lazy). Algunas
        # paginas solo montan ~7 tarjetas iniciales y cargan el resto al
        # hacer scroll dentro de la lista; usamos bastantes pasos.
        scroll_slow(page, step_px_rng=(300, 500), max_steps=12,
                    pause_rng=(1.0, 2.0))
        # Parsear pag 1 y registrar IDs unicos (no contar tarjetas del DOM).
        page1_cards = parse_serp(page, role, city_name, layout=layout)
        for c in page1_cards:
            if c.job_id and c.job_id not in seen_ids:
                seen_ids.add(c.job_id)
                all_cards.append(c)
        loaded = len(all_cards)
        log.info("Auth: %d tarjetas unicas en pagina 1.", loaded)

        # Paginacion auth: iterar paginas 2..N, acumulando IDs unicos.
        # IMPORTANTE: LinkedIn auth REEMPLAZA las tarjetas al cambiar de pagina.
        # Antes contabamos tarjetas del DOM y new_loaded <= loaded era SIEMPRE
        # cierto (bug raiz de "solo 7 ofertas por combo"). Ahora usamos el set
        # seen_ids para diferenciar entre IDs viejos (repetidos en otra pagina)
        # y IDs NUEVOS no vistos antes.
        max_pages = config.get("max_pages", 40)
        page_num = 1
        no_new_count = 0
        while len(seen_ids) < target and page_num < max_pages:
            if not has_next_page(page):
                log.info("Auth: sin boton Siguiente en pag %d. Fin.", page_num)
                break
            delay(tuple(delays.get("between_scrolls", [1.5, 3.5])),
                  "pre-next-page")
            mouse_jitter(page, n=1)
            url_before = page.url
            if not click_next_page(page):
                log.warning("Auth: fallo al clickar Siguiente (pag %d). "
                            "Probando navegacion directa start=%d.",
                            page_num, page_num * 25)
                if not _goto_next_start(page, role, city_cfg,
                                        date_filter,
                                        page_num):
                    break
            else:
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:  # noqa: BLE001
                    log.debug("networkidle tras next_page no alcanzado.")
                delay((2.0, 4.0), "post-next-page")
                # El click puede "funcionar" sin navegar (boton tapado o
                # handler no disparado). Si la URL no cambio, la pagina
                # actual es la misma -> ir directo por start.
                if page.url == url_before:
                    log.warning("Auth: click Siguiente no navego (URL sin "
                                "cambios en pag %d). Usando start=%d.",
                                page_num, page_num * 25)
                    if not _goto_next_start(page, role, city_cfg,
                                            date_filter,
                                            page_num):
                        break
            _check_block(page)
            scroll_slow(page, step_px_rng=(300, 500), max_steps=8,
                        pause_rng=(1.0, 2.0))
            # Parsear pagina actual y filtrar solo IDs NO vistos antes.
            # Si no hay IDs nuevos, es pagina trampa o fin de datos.
            page_cards = parse_serp(page, role, city_name, layout=layout)
            new_in_page = 0
            for c in page_cards:
                if c.job_id and c.job_id not in seen_ids:
                    seen_ids.add(c.job_id)
                    all_cards.append(c)
                    new_in_page += 1
            page_num += 1
            if new_in_page == 0:
                no_new_count += 1
                log.info("Auth: pag %d sin IDs nuevos (count=%d/3 trampas).",
                         page_num, no_new_count)
                if no_new_count >= 3:
                    log.info("Auth: 3 paginas sin IDs nuevos. Fin de paginacion.")
                    break
            else:
                no_new_count = 0
                log.info("Auth: pag %d -> +%d IDs nuevos (total=%d).",
                         page_num, new_in_page, len(all_cards))

    _check_block(page)
    return {"url": url, "result_count": count,
            "cards_loaded": len(all_cards), "layout": layout,
            "cards_raw": all_cards}
