"""Entry point: python -m src.devitjobs

Ejecuta el scraper de DevITJobs.nl (Paises Bajos) via su API JSON.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config_nl import DEFAULT_CONFIG
from .scraper import DevITJobsScraper

log = logging.getLogger("devitjobs")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "devitjobs" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[sh, fh])


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="devitjobs", description="DevITJobs.nl scraper")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="Antiguedad maxima de las ofertas (dias).")
    ap.add_argument("--no-detail", action="store_true",
                    help="No pedir el detalle completo (mas rapido, menos campos).")
    ap.add_argument("--all-technologies", action="store_true",
                    help="Incluir todas las ofertas IT (sin filtrar por datos).")
    ap.add_argument("--max-jobs", type=int, default=None,
                    help="Limite de ofertas a procesar.")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== DevITJobs.nl Scraper ===")

    config = DEFAULT_CONFIG.copy()
    if args.max_age_days is not None:
        config["max_age_days"] = args.max_age_days
    if args.no_detail:
        config["fetch_detail"] = False
    if args.all_technologies:
        config["include_all_technologies"] = True

    scraper = DevITJobsScraper(config)
    try:
        scraper.run(max_jobs=args.max_jobs)
    except Exception as e:
        log.error("Error en scraper: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
