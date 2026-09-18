"""Contexto de navegador Playwright (via patchright, fork parcheado anti-deteccion).

patchright es API-compatible con playwright: `from patchright.sync_api import
sync_playwright`. Parchea a nivel binario de Chromium las senales de
automatizacion (mas robusto que parchear JS), reduciendo la probabilidad de
que se dispare el flag data-is-bot y por tanto de CAPTCHAs.

NO es una herramienta de evasion de CAPTCHAs: si aparece un challenge, el
scraper se detiene (ver login.py / main.py). Lo que hace es reducir la
probabilidad de que aparezca, comportandonos como un navegador real.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# patchright es drop-in replacement de playwright
try:
    from patchright.sync_api import sync_playwright, BrowserContext, Playwright
except ImportError:  # fallback a playwright si patchright no esta instalado
    log.warning("patchright no disponible, usando playwright (mas detectable).")
    from playwright.sync_api import sync_playwright, BrowserContext, Playwright  # type: ignore

log = logging.getLogger(__name__)

# Rutas base del proyecto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AUTH_DIR = PROJECT_ROOT / "auth"
PROFILE_DIR = AUTH_DIR / "profile"
# Perfil separado para el modo invitado (sin login). Evita reutilizar el
# perfil de la cuenta bloqueada y que se inyecten cookies de una sesion en
# verificacion.
GUEST_PROFILE_DIR = AUTH_DIR / "profile_guest"
STORAGE_STATE_PATH = AUTH_DIR / "storage_state.json"

load_dotenv(PROJECT_ROOT / ".env")


# User-Agent real de Chrome desktop (no automation flag)
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _resolve_headless(config: dict[str, Any]) -> bool:
    """HEADLESS del .env tiene prioridad sobre config.yaml."""
    env_val = os.getenv("HEADLESS")
    if env_val is not None:
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(config.get("browser", {}).get("headless", config.get("headless", False)))


def launch_context(pw: Playwright, config: dict[str, Any],
                   guest: bool = False) -> BrowserContext:
    """Lanza Chromium con perfil persistente y configuracion realista.

    Usa launch_persistent_context para reutilizar cookies/huella entre
    ejecuciones (mas estable y humano que incognito + storage_state).

    Args:
        pw: instancia de Playwright (de sync_playwright().start()).
        config: dict cargado de config.yaml.
        guest: True para scraping publico sin login. Usa un perfil separado
            (auth/profile_guest) y NO inyecta auth/storage_state.json.
    """
    headless = _resolve_headless(config)
    viewport = config.get("viewport", {"width": 1536, "height": 864})
    locale = config.get("locale", "es-ES")
    timezone = config.get("timezone", "Europe/Madrid")
    profile_dir = GUEST_PROFILE_DIR if guest else PROFILE_DIR

    profile_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "Lanzando Chromium (patchright) headless=%s modo=%s perfil=%s locale=%s tz=%s",
        headless, "guest" if guest else "auth", profile_dir, locale, timezone,
    )

    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport=viewport,
        locale=locale,
        timezone_id=timezone,
        user_agent=DEFAULT_UA,
        # Argumentos para parecer un navegador normal (no automation):
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        # Elimina la senal navigator.webdriver (doble seguro con patchright)
        ignore_default_args=["--enable-automation"],
        # Geolocation desactivada (no revelar IP-based location)
        geolocation=None,
        permissions=[],
        # Color scheme del sistema
        color_scheme="light",
        # No reducir motion (algunos sitios lo usan como senal)
        reduced_motion="no-preference",
    )

    # Patch adicional: navigator.webdriver = false (en cada frame nuevo)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    # Idiomas Accept-Language coherentes con locale es-ES
    context.set_extra_http_headers({"Accept-Language": "es-ES,es;q=0.9,en;q=0.6"})

    # Inyectar cookies de una sesion capturada a mano (manual_login.py). Sin esto
    # el storage_state guardado no se usaba nunca y el scraper re-disparaba el
    # login automatico (origen de los challenges repetidos). En modo invitado
    # no se inyecta nada: debe ser una sesion anonima limpia.
    if not guest:
        _apply_storage_state(context)

    return context


def has_valid_storage_state() -> bool:
    """True si existe storage_state.json (cookies de login guardadas)."""
    return STORAGE_STATE_PATH.exists() and STORAGE_STATE_PATH.stat().st_size > 0


def _apply_storage_state(context: BrowserContext) -> int:
    """Inyecta las cookies de auth/storage_state.json en el contexto.

    launch_persistent_context NO acepta `storage_state=`, por lo que las cookies
    guardadas quedaban sin aplicar. Se inyectan con add_cookies() para reutilizar
    una sesion capturada manualmente sin volver a lanzar el login automatico.
    Devuelve el numero de cookies aplicadas (0 si no hay/esta corrupta).
    """
    if not has_valid_storage_state():
        return 0
    try:
        import json
        state = json.loads(STORAGE_STATE_PATH.read_text(encoding="utf-8"))
        cookies = state.get("cookies") or []
        for c in cookies:
            if c.get("sameSite") not in ("Strict", "Lax", "None"):
                c["sameSite"] = "Lax"
        if cookies:
            context.add_cookies(cookies)
        log.info("storage_state aplicado: %d cookies inyectadas desde %s",
                 len(cookies), STORAGE_STATE_PATH)
        return len(cookies)
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo aplicar storage_state (%s); solo se usara el perfil.", e)
        return 0


def save_storage_state(context: BrowserContext) -> None:
    """Persiste cookies/local storage para reutilizar la sesion la proxima vez."""
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(STORAGE_STATE_PATH))
    log.info("storage_state guardado en %s", STORAGE_STATE_PATH)


def close_context(context: BrowserContext) -> None:
    """Cierra el contexto de forma segura."""
    try:
        context.close()
    except Exception as e:  # noqa: BLE001
        log.debug("close_context: %s", e)
