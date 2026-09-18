"""Entry point: python -m src.stepstone_nl

Ejecuta el scraper de StepStone NL (Holanda). Reutiliza IrishJobs scraper.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ..irishjobs.scraper import IrishJobsScraper
from .config_nl import DEFAULT_CONFIG

log = logging.getLogger("stepstone_nl")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "stepstone_nl" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[sh, fh])


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="stepstone_nl", description="StepStone NL scraper")
    ap.add_argument("--role", action="append", default=None)
    ap.add_argument("--city", action="append", default=None)
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--max-jobs-per-search", type=int, default=None)
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Maximo de paginas por busqueda.")
    ap.add_argument("--max-total", type=int, default=None,
                    help="Target maximo de ofertas totales.")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== StepStone NL Scraper ===")

    config = DEFAULT_CONFIG.copy()
    if args.role:
        config["roles"] = args.role
    if args.city:
        city_filter = set(args.city)
        config["cities"] = {k: v for k, v in config["cities"].items() if k in city_filter}
    if args.max_jobs_per_search is not None:
        config["jobs_per_search"] = args.max_jobs_per_search
    if args.max_pages is not None:
        config["max_pages"] = args.max_pages
    if args.max_total is not None:
        config["max_total_jobs"] = args.max_total
    config["do_detail"] = not args.no_detail

    scraper = IrishJobsScraper(config)  # Reutiliza la logica de IrishJobs
    if args.append:
        scraper.store.load_from_csv()

    try:
        scraper.run()
    except Exception as e:
        log.error("Error en scraper: %s", e, exc_info=args.verbose)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
