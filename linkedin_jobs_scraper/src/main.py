"""Orquestador del scraper LinkedIn Jobs (Data, Espana).

Flujo:
 1. Carga config.yaml + .env.
 2. Lanza navegador (patchright + perfil persistente).
 3. Login (storage_state si existe, sino login interactivo + 2FA manual).
 4. Por cada (role, ciudad):
      a. Intentar nucleo local: search.run_search -> parse_serp -> por cada
         oferta, parse_detail. Pausas humanas.
      b. Si BlockedException >= max_block_retries para esta combinacion,
         conmutar a apify_fallback.scrape.
      c. Acumular en Store (dedup por job_id) + checkpoint.
 5. Volcado final: jobs.csv + jobs.parquet.

Senales respetadas (NO eludidas):
 - BlockedException detiene el nucleo local y conmuta a Apify (si token).
 - Si Apify tampoco disponible, esa combinacion se salta con aviso.
 - No se reintenta ante CAPTCHAs; se asume bloqueo real.

Uso:
    python -m src.main
    python -m src.main --role "Data Analyst" --city Madrid   # filtrar
    python -m src.main --no-detail                            # solo SERP
    python -m src.main --max-jobs-per-search 10
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .apify_fallback import ApifyNotConfiguredError, is_available as apify_available, \
    scrape as apify_scrape
from .browser import close_context, launch_context
from .human import delay, mouse_jitter
from .login import BlockedException, LoginFailedError, ensure_logged_in
from .models import JobOffer, now_utc_iso
from .parse_detail import parse_detail
from .parse_serp import to_job_offer
from .search import run_search
from .store import Store

log = logging.getLogger("linkedin_scraper")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _WarningCounterHandler(logging.Handler):
    """Cuenta registros WARNING/ERROR emitidos durante el run para exponerlos
    en el resumen final (telemetria best-effort). Reemplaza parcialmente el
    `except Exception: pass` silencioso disperso por log.warning contabilizado.
    """
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self.count += 1


_WARNING_COUNTER = _WarningCounterHandler()


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # RotatingFileHandler: conserva historico entre runs sin crecer indefinidamente.
    # maxBytes=5MB, backupCount=5 -> run.log + run.log.1..run.log.5 (~30MB max).
    log_path = PROJECT_ROOT / "data" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.basicConfig(
        level=level,
        handlers=[stream_handler, file_handler],
    )
    logging.getLogger().addHandler(_WARNING_COUNTER)


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or (PROJECT_ROOT / "config.yaml")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="linkedin_jobs_scraper",
        description="Scraper de LinkedIn Jobs (Data, Espana).",
    )
    ap.add_argument("--role", action="append", default=None,
                    help="Filtrar a uno o varios roles (repitible).")
    ap.add_argument("--city", action="append", default=None,
                    help="Filtrar a una o varias ciudades (repitible).")
    ap.add_argument("--no-detail", action="store_true",
                    help="Solo SERP (no entrar en cada oferta).")
    ap.add_argument("--max-jobs-per-search", type=int, default=None,
                    help="Sobreescribe jobs_per_search de config.yaml.")
    ap.add_argument("--no-apify", action="store_true",
                    help="Desactiva el respaldo Apify.")
    ap.add_argument("--guest", action="store_true",
                    help="Modo invitado SIN login: scrapea la busqueda publica "
                         "de LinkedIn Jobs. Usa un perfil anonimo separado "
                         "(auth/profile_guest). Util si la cuenta esta bloqueada. "
                         "Algunos campos (nº solicitantes) pueden faltar.")
    ap.add_argument("--append", action="store_true",
                    help="Carga el CSV/Parquet previo al iniciar para acumular "
                         "ofertas historicas y saltar detalles ya procesados "
                         "(idempotencia entre runs).")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap.parse_args()


def _filter_combos(config: dict[str, Any], args: argparse.Namespace
                   ) -> list[tuple[str, str, dict[str, str]]]:
    """Devuelve lista de (role, city_name, city_cfg) a procesar.

    Roles permitidos por ciudad (mayor prioridad gana):
      --role (CLI) > config.cities[<ciudad>].roles > config.roles
    Se itera por rol (outer) para agrupar las ciudades del mismo rol y no
    disparar la pausa larga 'after_city' en cada combinacion.
    """
    cities = config["cities"]
    city_filter = set(args.city) if args.city else None

    if args.role:
        roles_order = list(args.role)
    else:
        # Union en orden: roles globales primero, luego los especificos de
        # ciudad que no esten ya (p.ej. "AI Engineer" solo en internacionales).
        roles_order = list(config["roles"])
        for cfg_city in cities.values():
            for r in (cfg_city.get("roles") or []):
                if r not in roles_order:
                    roles_order.append(r)

    combos: list[tuple[str, str, dict[str, str]]] = []
    for role in roles_order:
        for city_name, city_cfg in cities.items():
            if city_filter and city_name not in city_filter:
                continue
            if args.role:
                allowed = args.role
            else:
                # Sin --role: la ciudad manda si define `roles`; si no, los globales.
                allowed = city_cfg.get("roles") or config["roles"]
            if role not in allowed:
                continue
            combos.append((role, city_name, city_cfg))
    return combos


def _scrape_one_local(context, page, role: str, city_name: str,
                      city_cfg: dict[str, str],
                      config: dict[str, Any], store: Store, do_detail: bool
                      ) -> tuple[int, Any, str]:
    """Nucleo local para UNA combinacion (role, ciudad).

    Devuelve (numero de ofertas nuevas anadidas, page_actual, estado).
    estado: "ok" (hay tarjetas), "empty" (0 resultados), "unknown" (layout
    indetectable: posible authwall/crash).
    La page_actual puede ser DISTINTA a la de entrada si se recreo por
    cierre/bloqueo; el llamador debe reasignar su referencia para evitar
    operar sobre una page muerta en la siguiente combinacion (fix F3-18).
    Lanza BlockedException si aparece challenge.
    """
    delays = config.get("delays", {})
    # Verificar si la pagina sigue abierta antes de empezar
    try:
        _ = page.title()
    except Exception:  # noqa: BLE001
        log.warning("Pagina cerrada al iniciar combinacion. Creando nueva.")
        page = context.new_page()
        delay((2.0, 4.0), "post-new-page-start")
    info = run_search(page, role, city_name, city_cfg, config)
    cards = info.get("cards_raw", [])
    layout = info.get("layout", "unknown")
    if layout == "unknown":
        log.error("Layout indetectable para '%s' en %s (authwall/crash?).",
                  role, city_name)
        return 0, page, "unknown"
    if not cards:
        log.warning("Sin tarjetas para '%s' en %s (resultado vacio).",
                    role, city_name)
        return 0, page, "empty"

    # Convertir tarjetas a JobOffer y anadir al store
    base_offers: list[JobOffer] = []
    for raw in cards:
        base_offers.append(to_job_offer(raw, role, city_name, source="local"))
    store.add_many(base_offers)
    log.info("[%s/%s] %d ofertas base anadidas (sin detalle aun).",
             role, city_name, len(base_offers))

    if not do_detail:
        store.checkpoint(tag=f"{role}_{city_name}_serp")
        return len(base_offers), page, "ok"

    # Detalle: visitar cada oferta, saltando ya procesados.
    # detail_max_jobs <= 0 = ilimitado (detallar TODAS las ofertas encontradas).
    max_detail = config.get("detail_max_jobs", 25)
    target_offers = base_offers if max_detail <= 0 else base_offers[:max_detail]
    n_detail_ok = 0
    # Saltar las que YA tienen descripcion en el store: las base_offers vienen
    # recien creadas del SERP (sin detalle), asi que hay que consultar el store.
    pending: list[JobOffer] = []
    for o in target_offers:
        existing = store.get(o.job_id)
        if existing is not None and existing.description_full:
            continue
        pending.append(o)
    skipped = len(target_offers) - len(pending)
    if skipped:
        log.info("[%s/%s] %d detalles saltados (ya procesados en run previo).",
                 role, city_name, skipped)
    for i, offer in enumerate(pending):
        log.info("[%s/%s] Detalle %d/%d: %s",
                 role, city_name, i + 1, len(pending), offer.title[:50])
        try:
            try:
                _ = page.title()
            except Exception:  # noqa: BLE001
                log.warning("Pagina cerrada. Creando pagina nueva.")
                page = context.new_page()
                delay((2.0, 4.0), "post-new-page")
            parse_detail(page, offer, config)
            # Fusionar el detalle en el store. Si la oferta ya existia (p.ej.
            # anadida sin detalle en un run previo), store.add reemplaza el
            # objeto antiguo por este ya enriquecido; sin esto, el detalle se
            # escribia en un objeto huerfano y se perdia.
            store.add(offer)
            n_detail_ok += 1
        except BlockedException:
            log.warning("Bloqueo en detalle %s. Relanzando para conmutar.",
                        offer.job_id)
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Error en detalle %s: %s", offer.job_id, e)
            log.debug(traceback.format_exc())
            try:
                _ = page.title()
            except Exception:  # noqa: BLE001
                try:
                    page = context.new_page()
                    delay((2.0, 4.0), "post-new-page-recovery")
                except Exception:  # noqa: BLE001
                    log.error("No se pudo crear pagina nueva. Abortando detalles.")
                    break
        if config.get("_guest"):
            # Modo invitado: detalle por API HTTP, sin navegacion real.
            delay(tuple(delays.get("guest_between_details", [1.5, 4.0])),
                  "guest-between-details")
        else:
            delay(tuple(delays.get("between_details", [5.0, 12.0])), "between-details")
            mouse_jitter(page, n=1)
        ck_every = int(config.get("checkpoint_every_details", 5) or 5)
        if ck_every > 0 and (i + 1) % ck_every == 0:
            store.checkpoint(tag=f"{role}_{city_name}_{i+1}det")

    store.checkpoint(tag=f"{role}_{city_name}_done")
    log.info("[%s/%s] %d/%d detalles completados.",
             role, city_name, n_detail_ok, len(pending))
    return n_detail_ok, page, "ok"


def _scrape_one_apify(role: str, city_name: str, city_cfg: dict[str, str],
                      config: dict[str, Any], store: Store) -> int:
    """Respaldo Apify para una combinacion. Devuelve nº ofertas nuevas."""
    if not apify_available():
        log.warning("Apify no disponible (APIFY_TOKEN vacio). Saltando.")
        return 0
    try:
        offers = apify_scrape(role, city_name, city_cfg, config)
        n = store.add_many(offers)
        log.info("[Apify %s/%s] %d ofertas anadidas (%d nuevas).",
                 role, city_name, len(offers), n)
        store.checkpoint(tag=f"apify_{role}_{city_name}")
        return n
    except ApifyNotConfiguredError as e:
        log.warning("Apify no configurado: %s", e)
        return 0
    except Exception as e:  # noqa: BLE001
        log.error("Apify fallo para '%s'/%s: %s", role, city_name, e)
        log.debug(traceback.format_exc())
        return 0


def _restart_context(pw, config: dict[str, Any]) -> tuple[Any, Any] | None:
    """Recrea contexto + login tras crash del navegador.

    Devuelve (context, page) o None si no se pudo (bloqueo, error, etc.).
    """
    log.warning("Recreando contexto completo tras crash del navegador...")
    try:
        new_ctx = launch_context(pw, config)
        ensure_logged_in(new_ctx, config)
        new_page = new_ctx.new_page()
        delay((2.0, 4.0), "post-restart-context")
        return new_ctx, new_page
    except BlockedException as e:
        log.error("Bloqueo al recrear contexto: %s", e)
        return None
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo recrear contexto: %s", e)
        log.debug(traceback.format_exc())
        return None


def _scrape_all_via_apify(combos: list[tuple[str, str, dict[str, str]]],
                          config: dict[str, Any], store: Store) -> int:
    """Respaldo Apify para TODAS las combinaciones (login no disponible)."""
    n_total = 0
    for role, city_name, city_cfg in combos:
        n = _scrape_one_apify(role, city_name, city_cfg, config, store)
        n_total += n
        delay(tuple(config["delays"].get("between_searches", [8, 20])),
              "between-searches-apify")
    return n_total


def _run_login_failure_fallback(combos, config, store, message: str,
                                use_apify: bool, started: float) -> int:
    """Gestiona un fallo de login: conmuta todo el run a Apify si esta
    disponible (exit 0 si hay ofertas), o aborta (exit != 0)."""
    log.error("%s", message)
    if not use_apify:
        log.error("No se pudo iniciar sesion y Apify desactivado. Abort.")
        # Persistir SIEMPRE lo capturado antes de salir: un run sin sesion
        # no debe perder el progreso ya guardado (--append / checkpoints).
        store.write_all()
        _print_summary(0, 0, 0, len(store), started, offers=store.all())
        if len(store) == 0:
            log.error("Run completo fallido: sin sesion y sin datos previos "
                      "que conservar. Exit code != 0.")
            return 3
        log.warning("Progreso conservado: %d ofertas en jobs.csv/parquet.",
                    len(store))
        return 0
    log.warning("Conmutando TODO el run a Apify.")
    n = _scrape_all_via_apify(combos, config, store)
    store.write_all()
    _print_summary(0, 0, n, len(store), started, offers=store.all())
    if len(store) == 0:
        log.error("Run completo fallido: login no disponible y Apify sin "
                  "ofertas. Exit code != 0.")
        return 4
    return 0


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== LinkedIn Jobs Scraper %s ===", __version__)

    config = load_config()
    if args.max_jobs_per_search is not None:
        config["jobs_per_search"] = args.max_jobs_per_search
    use_apify = config.get("use_apify_fallback", True) and not args.no_apify
    do_detail = not args.no_detail
    guest_mode = args.guest
    # parse_detail lo consulta para usar la API publica de detalle.
    config["_guest"] = guest_mode

    combos = _filter_combos(config, args)
    log.info("Combinaciones a procesar: %d (detail=%s, apify=%s)",
             len(combos), do_detail, use_apify)

    store = Store(config)
    if args.append:
        log.info("--append: cargando CSV previo para idempotencia...")
        store.load_from_csv()
    max_block_retries = config.get("max_block_retries", 2)

    # Importar playwright/patchright aqui para que el CLI no dependa de que
    # este instalado para mostrar --help.
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
            log.warning("patchright no instalado; usando playwright (mas detectable).")
        except ImportError:
            log.error("Ni patchright ni playwright instalados. Ejecuta: "
                      "pip install patchright && patchright install chromium")
            return 2

    overall_ok = 0
    overall_blocked = 0
    overall_apify = 0
    overall_empty = 0
    overall_unknown = 0
    combo_stats: list[dict[str, Any]] = []
    started = time.time()

    with sync_playwright() as pw:
        context = launch_context(pw, config, guest=guest_mode)
        try:
            if guest_mode:
                # Modo invitado: NO se hace login. LinkedIn sirve la busqueda
                # publica de Jobs; los parsers ya soportan el layout guest.
                log.warning("MODO INVITADO: sin login (perfil anonimo). "
                            "Se extraen solo datos publicos; algunos campos "
                            "(nº solicitantes) pueden faltar.")
            else:
                # Login (gestiona storage_state / 2FA / bloqueos)
                try:
                    ensure_logged_in(context, config)
                except BlockedException as e:
                    return _run_login_failure_fallback(
                        combos, config, store,
                        f"Bloqueo en login: {e}", use_apify, started)
                except LoginFailedError as e:
                    return _run_login_failure_fallback(
                        combos, config, store,
                        f"Login fallo: {e}", use_apify, started)
                except Exception as e:  # noqa: BLE001
                    # Timeouts de red, crash del navegador, etc. durante el login:
                    # conmutar a Apify en lugar de reintentar el login en bucle.
                    return _run_login_failure_fallback(
                        combos, config, store,
                        f"Fallo inesperado en login: {e}", use_apify, started)

            page = context.new_page()
            try:
                for idx, (role, city_name, city_cfg) in enumerate(combos, 1):
                    log.info("--- COMBINACION %d/%d: '%s' en %s ---",
                             idx, len(combos), role, city_name)
                    combo_start_len = len(store)
                    # Verificar si la pagina sigue abierta; recrear si se cerro
                    context_alive = True
                    try:
                        _ = page.title()
                    except Exception:  # noqa: BLE001
                        log.warning("Pagina cerrada antes de combinacion %d. "
                                    "Creando pagina nueva.", idx)
                        try:
                            page = context.new_page()
                            delay((2.0, 4.0), "post-new-page-combo")
                        except Exception as e:  # noqa: BLE001
                            log.error("Context cerrado: %s. Intentando recrear "
                                      "navegador completo.", e)
                            restarted = _restart_context(pw, config)
                            if restarted is not None:
                                try:
                                    close_context(context)
                                except Exception:  # noqa: BLE001
                                    pass
                                context, page = restarted
                                log.warning("Contexto recreado OK.")
                            else:
                                log.error("No se pudo recrear el navegador. "
                                          "Usando Apify para esta y restantes "
                                          "combinaciones.")
                                context_alive = False

                    block_retries = 0
                    done = False
                    combo_blocked = 0
                    combo_apify = 0
                    combo_state = "unknown"
                    if not context_alive:
                        block_retries = max_block_retries  # skip while loop -> Apify
                    while block_retries < max_block_retries and not done:
                        try:
                            n, page, combo_state = _scrape_one_local(
                                context, page, role, city_name, city_cfg,
                                config, store, do_detail)
                            overall_ok += n
                            if combo_state == "empty":
                                overall_empty += 1
                            elif combo_state == "unknown":
                                overall_unknown += 1
                            done = True
                        except BlockedException as e:
                            block_retries += 1
                            combo_blocked += 1
                            overall_blocked += 1
                            log.warning("Bloqueo #%d en '%s'/%s: %s",
                                        block_retries, role, city_name, e)
                            # Pausa larga antes de reintentar
                            delay((30.0, 60.0), "post-block-retry")
                            # Recrear pagina tras bloqueo por si acaso
                            try:
                                _ = page.title()
                            except Exception:  # noqa: BLE001
                                try:
                                    page = context.new_page()
                                    delay((2.0, 4.0), "post-block-new-page")
                                except Exception:  # noqa: BLE001
                                    pass
                        except Exception as e:  # noqa: BLE001
                            # Errores no-bloqueo (pagina cerrada, etc): recrear y reintentar
                            block_retries += 1
                            log.warning("Error no-bloqueo #%d en '%s'/%s: %s",
                                        block_retries, role, city_name, e)
                            try:
                                page = context.new_page()
                                delay((2.0, 4.0), "post-error-new-page")
                            except Exception:  # noqa: BLE001
                                log.error("Context cerrado. Intentando recrear "
                                          "navegador completo.")
                                restarted = _restart_context(pw, config)
                                if restarted is not None:
                                    try:
                                        close_context(context)
                                    except Exception:  # noqa: BLE001
                                        pass
                                    context, page = restarted
                                else:
                                    log.error("No se pudo recrear. No se puede "
                                              "continuar localmente.")
                                    break
                            delay((10.0, 20.0), "post-error-recovery")
                    if not done:
                        log.warning("Conmutando '%s'/%s a Apify tras %d bloqueos.",
                                    role, city_name, block_retries)
                        if use_apify:
                            n = _scrape_one_apify(role, city_name, city_cfg,
                                                  config, store)
                            combo_apify += n
                            overall_apify += n
                        else:
                            log.error("Apify desactivado. Combinacion saltada.")
                    combo_stats.append({
                        "role": role, "city": city_name,
                        "new": len(store) - combo_start_len,
                        "state": combo_state,
                        "blocked": combo_blocked,
                        "apify": combo_apify,
                    })
                    # Pausa entre combinaciones
                    if idx < len(combos):
                        # Pausa larga al cambiar de rol (entre el ultimo ciudad
                        # de un rol y el primero del siguiente). Simula volcado
                        # de actividad antes de iniciar un nuevo set de busquedas.
                        next_role = combos[idx][0] if idx < len(combos) else None
                        if next_role and next_role != role:
                            delay(tuple(config["delays"].get("after_city", [30, 60])),
                                  "after-city")
                        else:
                            delay(tuple(config["delays"].get("between_searches", [8, 20])),
                                  "between-searches")
            finally:
                try:
                    page.close()
                except Exception:  # noqa: BLE001
                    pass
        except BaseException:
            # Volcado de emergencia: cualquier excepcion no capturada (crash,
            # cuenta revocada a mitad de run, Ctrl-C) persiste lo capturado
            # antes de propagar el fallo.
            try:
                store.write_all()
            except Exception as e:  # noqa: BLE001
                log.error("Error al volcar de emergencia: %s", e)
            raise
        finally:
            close_context(context)

    # Volcado final — SIEMPRE, incluso si hubo errores arriba
    try:
        store.write_all()
    except Exception as e:  # noqa: BLE001
        log.error("Error al volcar output final: %s", e)
    _print_summary(overall_ok, overall_blocked, overall_apify,
                   len(store), started, combo_stats,
                   overall_empty, overall_unknown,
                   offers=store.all())
    if len(store) == 0 and overall_blocked == 0 and overall_apify == 0:
        log.error("Run completo fallido: 0 ofertas, 0 bloqueos, 0 via Apify "
                  "(posible fallo sistematico de login/parser). Exit != 0.")
        return 4
    return 0


def _print_summary(ok: int, blocked: int, apify: int, total: int,
                   started: float, combo_stats: list[dict[str, Any]] | None = None,
                   empty: int = 0, unknown: int = 0,
                   offers: list[JobOffer] | None = None) -> None:
    elapsed = time.time() - started
    log.info("=== RESUMEN ===")
    log.info("Ofertas locales OK:  %d", ok)
    log.info("Bloqueos locales:    %d", blocked)
    log.info("Ofertas via Apify:   %d", apify)
    log.info("Combos sin resultados: %d (layout indetectable: %d)",
             empty, unknown)
    log.info("Total unico en store: %d", total)
    if offers:
        n = len(offers)
        with_desc = sum(1 for o in offers if len(o.description_full or "") > 100)
        with_skills = sum(1 for o in offers if o.skills)
        with_salary = sum(1 for o in offers if o.salary_raw)
        with_work_mode = sum(1 for o in offers if o.work_mode)
        with_exp = sum(1 for o in offers if o.experience_level)
        log.info("Completitud (%d ofertas): descripcion=%d skills=%d "
                 "salario=%d modalidad=%d nivel_exp=%d",
                 n, with_desc, with_skills, with_salary,
                 with_work_mode, with_exp)
    if combo_stats:
        log.info("Por combinacion:")
        for c in combo_stats:
            log.info("  %-20s %-12s nuevas=%-3d estado=%-7s bloqueos=%d apify=%d",
                     c["role"][:20], c["city"][:12], c["new"], c["state"],
                     c["blocked"], c["apify"])
    log.info("Warnings/Errores telemetria: %d", _WARNING_COUNTER.count)
    log.info("Tiempo total: %.1f min", elapsed / 60.0)
    log.info("Salidas: data/output/jobs.csv + data/output/jobs.parquet")


if __name__ == "__main__":
    raise SystemExit(main())
