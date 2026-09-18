"""Entry point: python -m src.nvb

Ejecuta el scraper de Nationale Vacaturebank (Paises Bajos) via su API JSON.
Los textos se guardan en holandes; el merge los traduce a ingles.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config_nl import DEFAULT_CONFIG
from .scraper import NvbScraper

log = logging.getLogger("nvb")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "nvb" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[sh, fh])


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="nvb",
                                 description="Nationale Vacaturebank scraper")
    ap.add_argument("--query", action="append", default=None,
                    help="Consulta concreta (repetible). Por defecto, las de config.")
    ap.add_argument("--max-age-days", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Maximo de paginas por consulta.")
    ap.add_argument("--max-total", type=int, default=None)
    ap.add_argument("--no-title-filter", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== Nationale Vacaturebank Scraper ===")

    config = DEFAULT_CONFIG.copy()
    if args.query:
        config["queries"] = args.query
    if args.max_age_days is not None:
        config["max_age_days"] = args.max_age_days
    if args.max_pages is not None:
        config["max_pages_per_query"] = args.max_pages
    if args.max_total is not None:
        config["max_total_jobs"] = args.max_total
    if args.no_title_filter:
        config["title_filter"] = False

    scraper = NvbScraper(config)
    try:
        scraper.run()
    except Exception as e:
        log.error("Error en scraper: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
