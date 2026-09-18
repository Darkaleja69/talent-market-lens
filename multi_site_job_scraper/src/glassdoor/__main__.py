"""Entry point: python -m src.glassdoor

Ejecuta el scraper de Glassdoor (USA) para ofertas de datos.
Objetivo: ~1.000 ofertas unicas entre Data Analyst, Data Scientist,
Data Engineer y Analytics Engineer.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config_gd import DEFAULT_CONFIG
from .scraper import GlassdoorScraper

log = logging.getLogger("glassdoor")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "glassdoor" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[sh, fh])


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="glassdoor",
        description="Glassdoor USA scraper — ofertas de datos (~1.000 objetivo)",
    )
    ap.add_argument("--role", action="append", default=None,
                    help="Filtrar por rol (repetible). Ej: --role 'Data Analyst'")
    ap.add_argument("--no-detail", action="store_true",
                    help="Omitir paginas de detalle (default: ya omitidas).")
    ap.add_argument("--with-detail", action="store_true",
                    help="Visitar paginas de detalle (mas lento).")
    ap.add_argument("--max-jobs-per-search", type=int, default=None,
                    help="Max ofertas a procesar por busqueda.")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Max paginas por rol.")
    ap.add_argument("--max-detail", type=int, default=None,
                    help="Max paginas de detalle a visitar.")
    ap.add_argument("--target", type=int, default=1000,
                    help="Objetivo de ofertas totales (default: 1000).")
    ap.add_argument("--append", action="store_true",
                    help="Reanudar desde CSV anterior (--append).")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Modo debug con logs detallados.")
    ap.add_argument("--headless", action="store_true",
                    help="Ejecutar navegador en headless (mas detectable).")
    ap.add_argument("--clean-profile", action="store_true",
                    help="Eliminar perfil persistente y empezar fresco.")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== Glassdoor USA Scraper ===")
    log.info("Objetivo: %d ofertas unicas de datos", args.target)

    config = DEFAULT_CONFIG.copy()
    if args.role:
        config["roles"] = args.role
    if args.max_jobs_per_search is not None:
        config["jobs_per_search"] = args.max_jobs_per_search
    if args.max_pages is not None:
        config["max_pages"] = args.max_pages
    if args.max_detail is not None:
        config["max_detail_jobs"] = args.max_detail
    if args.with_detail:
        config["do_detail"] = True
        if not args.max_detail:
            config["max_detail_jobs"] = 50
    if args.no_detail:
        config["do_detail"] = False
        config["max_detail_jobs"] = 0
    if args.headless:
        import os
        os.environ["HEADLESS"] = "true"

    config["target"] = args.target
    config["clean_profile"] = args.clean_profile

    scraper = GlassdoorScraper(config)
    if args.append:
        scraper.store.load_from_csv()
        # Si el CSV de output esta vacio (crash previo), cargar desde el checkpoint mas reciente
        if len(scraper.store) == 0:
            loaded = scraper.store.load_from_latest_checkpoint()
            if loaded:
                log.info("Recuperadas %d ofertas desde checkpoint", loaded)
    scraper.store.rebuild_state()

    try:
        total = scraper.run()
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario. Guardando progreso...")
        scraper.stop()
        scraper.store.write_all()
        log.info("Progreso guardado. Usa --append para reanudar.")
        return 130
    except Exception as e:
        log.error("Error en scraper: %s", e, exc_info=args.verbose)
        scraper.store.write_all()
        return 1

    log.info("Total ofertas almacenadas: %d", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
