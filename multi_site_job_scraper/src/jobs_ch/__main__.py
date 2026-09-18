"""Entry point: python -m src.jobs_ch

Ejecuta el scraper de jobs.ch (Suiza).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config_ch import DEFAULT_CONFIG
from .scraper import JobsChScraper

log = logging.getLogger("jobs_ch")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    project_root = Path(__file__).resolve().parent.parent.parent
    log_path = project_root / "data" / "jobs_ch" / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logging.basicConfig(level=level, handlers=[sh, fh])


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="jobs_ch", description="jobs.ch scraper")
    ap.add_argument("--role", action="append", default=None)
    ap.add_argument("--city", action="append", default=None)
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--max-jobs-per-search", type=int, default=None)
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--headless", action="store_true",
                    help="Ejecutar navegador en headless (mas rapido, mas detectable).")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log.info("=== jobs.ch Scraper ===")

    config = DEFAULT_CONFIG.copy()
    if args.role:
        config["roles"] = args.role
    if args.city:
        city_filter = set(args.city)
        config["cities"] = {k: v for k, v in config["cities"].items() if k in city_filter}
    if args.max_jobs_per_search is not None:
        config["jobs_per_search"] = args.max_jobs_per_search
    config["do_detail"] = not args.no_detail
    if args.headless:
        import os
        os.environ["HEADLESS"] = "true"

    scraper = JobsChScraper(config)
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
