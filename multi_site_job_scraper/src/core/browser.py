"""Contexto de navegador Playwright (via patchright) para scrapers que
requieren renderizado JS (jobs.ch, glassdoor).

Reutiliza el patron del linkedin scraper: perfil persistente, UA realista,
stealth binario.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

try:
    from patchright.sync_api import sync_playwright, BrowserContext, Playwright
except ImportError:
    log = logging.getLogger(__name__)
    log.warning("patchright no disponible, usando playwright (mas detectable).")
    from playwright.sync_api import sync_playwright, BrowserContext, Playwright  # type: ignore

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUTH_DIR = PROJECT_ROOT / "auth"
PROFILE_DIR = AUTH_DIR / "profile"

load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


def _resolve_headless() -> bool:
    env_val = os.getenv("HEADLESS")
    if env_val is not None:
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    return False


def launch_context(pw: Playwright, locale: str = "en-GB",
                   timezone: str = "Europe/Zurich",
                   profile_name: str = "default") -> BrowserContext:
    """Lanza Chromium con perfil persistente.

    profile_name permite que cada scraper use su propio perfil aislado
    para ejecucion en paralelo sin conflictos.
    """
    headless = _resolve_headless()
    profile_dir = PROFILE_DIR / profile_name
    profile_dir.mkdir(parents=True, exist_ok=True)
    log.info("Lanzando Chromium headless=%s locale=%s tz=%s profile=%s",
             headless, locale, timezone, profile_name)

    # NOTA: no se fuerza user_agent fijo. El UA nativo de Chromium debe
    # coincidir con el binario real (version, geometrias, etc.): un UA
    # desajustado (ej: Chrome/132 con binario 148) es una señal de bot
    # fuerte para Cloudflare y provoca challenges en bucle.
    launch_kwargs = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1536, "height": 864},
        locale=locale,
        timezone_id=timezone,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )

    # Reintentos: lanzar varios Chromium a la vez (run_all en paralelo) puede
    # saturar la maquina y colgar el arranque. Timeout generoso + retry.
    import time

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            context = pw.chromium.launch_persistent_context(timeout=300000, **launch_kwargs)
            break
        except Exception as e:  # pragma: no cover - depende del entorno
            last_err = e
            log.warning("launch_context intento %d/3 fallo: %s", attempt, e)
            if attempt < 3:
                time.sleep(30 * attempt)
    else:
        raise last_err if last_err else RuntimeError("launch_context fallo")

    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
    return context


def close_context(context: BrowserContext) -> None:
    try:
        context.close()
    except Exception as e:
        log.debug("close_context: %s", e)
