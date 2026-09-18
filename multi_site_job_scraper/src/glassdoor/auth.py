"""Script de autenticacion para Glassdoor.

Abre un navegador Playwright con el mismo perfil persistente que usa
el scraper. El usuario puede iniciar sesion manualmente en Glassdoor
y la sesion se guarda para ejecuciones posteriores del scraper.

Uso:
    python -m src.glassdoor.auth
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("glassdoor.auth")


def main() -> int:
    log.info("=== Autenticacion Glassdoor ===")
    log.info("Se abrira un navegador headful con el perfil del scraper.")
    log.info("Inicia sesion en Glassdoor manualmente, luego vuelve aqui y pulsa Enter.")
    log.info("")

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as pw:
        from src.core.browser import PROFILE_DIR

        profile_dir = PROFILE_DIR / "glassdoor"
        profile_dir.mkdir(parents=True, exist_ok=True)

        log.info("Perfil: %s", profile_dir)
        log.info("Lanzando Chromium headful...")

        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1536, "height": 864},
            locale="en-US",
            timezone_id="America/New_York",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
            ignore_default_args=["--enable-automation"],
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        context.grant_permissions(["geolocation"])
        context.set_geolocation({"latitude": 40.7128, "longitude": -74.0060})

        page = context.new_page()

        try:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(page)
            log.info("playwright-stealth aplicado")
        except ImportError:
            pass

        # Navegar a Glassdoor
        log.info("Navegando a https://www.glassdoor.com...")
        page.goto("https://www.glassdoor.com", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Aceptar cookies si aparece
        try:
            btns = page.query_selector_all("button")
            for b in btns:
                txt = (b.inner_text() or "").strip().lower()
                if any(kw in txt for kw in ("accept all", "aceptar todas", "accept", "aceptar", "agree", "ok")):
                    b.click(timeout=3000)
                    time.sleep(1)
                    break
        except Exception:
            pass

        log.info("")
        log.info("============================================================")
        log.info("  AHORA: Inicia sesion o crea cuenta en Glassdoor.")
        log.info("  Ve a: https://www.glassdoor.com/profile/login_input.htm")
        log.info("  Si te redirige a .es, no importa — usa esa version.")
        log.info("")
        log.info("  Cuando hayas terminado, vuelve a esta terminal")
        log.info("  y pulsa ENTER para cerrar el navegador.")
        log.info("============================================================")

        page.goto("https://www.glassdoor.com/profile/login_input.htm",
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        log.info("")
        log.info("============================================================")
        log.info("  AHORA: Inicia sesion o crea cuenta en Glassdoor.")
        log.info("  Ve a: https://www.glassdoor.com/profile/login_input.htm")
        log.info("  Si te redirige a .es, no importa — usa esa version.")
        log.info("")
        log.info("  Cuando hayas terminado, CIERRA la ventana del navegador.")
        log.info("  La sesion se guardara automaticamente.")
        log.info("============================================================")

        # Esperar hasta que el usuario cierre la ventana o se agote el timeout
        try:
            page.wait_for_event("close", timeout=600000)  # 10 minutos
        except Exception:
            log.info("Timeout alcanzado. Cerrando navegador...")

        log.info("Cerrando navegador. La sesion queda guardada en %s", profile_dir)
        try:
            context.close()
        except Exception:
            pass

    log.info("Listo. Ahora ejecuta: python -m src.glassdoor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
