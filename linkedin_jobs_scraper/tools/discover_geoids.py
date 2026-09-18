"""Descubre el geoId real de LinkedIn Jobs para una lista de ciudades.

Navega a /jobs/search/?location=<texto> con la sesion persistente y lee el
geoId resuelto por LinkedIn desde la URL final. No requiere credenciales
adicionales: reutiliza auth/storage_state.json o auth/profile.

Uso:
    python -m tools.discover_geoids "Dublin, Ireland" "Amsterdam, Netherlands" "Zurich, Switzerland"
"""
from __future__ import annotations

import logging
import re
import sys
import time
from urllib.parse import parse_qs, urlencode, urlparse

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("discover_geoids")

JOBS_SEARCH_URL = "https://www.linkedin.com/jobs/search/"


def _extract_geoid(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    return qs.get("geoId", [""])[0]


def main(cities: list[str]) -> int:
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright  # type: ignore

    results: dict[str, str] = {}
    with sync_playwright() as pw:
        # Perfil persistente (arrastra cookies); headless para no molestar
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir="auth/profile",
            headless=True,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        try:
            page = ctx.new_page()
            for city_text in cities:
                params = {"keywords": "data", "location": city_text}
                url = JOBS_SEARCH_URL + "?" + urlencode(params)
                log.info("Buscando geoId para %r ...", city_text)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(3)
                    final_url = page.url
                    geoid = _extract_geoid(final_url)
                    if geoid:
                        log.info("  -> geoId=%s  (resolved URL=%s)", geoid, final_url)
                        results[city_text] = geoid
                    else:
                        log.warning("  -> no se encontro geoId en %s", final_url)
                except Exception as e:  # noqa: BLE001
                    log.error("  -> fallo: %s", e)
                time.sleep(2)
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    print("\n=== RESUMEN ===")
    for city, geoid in results.items():
        print(f'    "{city}": geoId "{geoid}"')
    return 0 if results else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m tools.discover_geoids 'City, Country' ...")
        sys.exit(2)
    sys.exit(main(sys.argv[1:]))