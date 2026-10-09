"""Persistent Chrome context for the InfoJobs scraper.

patchright is used as a drop-in replacement of playwright (same API,
binary-level anti-detection patches): it reduces the automation fingerprint
that Distil/Imperva uses to serve the challenge (T-27). If patchright is not
installed, the scraper falls back to plain playwright with a warning.
``navigator.webdriver`` is never patched by hand.

T-30 (iteracion): with ``INFOJOBS_CDP_URL`` set the scraper connects over CDP
to a Chrome launched directly by the OS (no Playwright automation flags), the
same pattern Indeed uses. That launch method is what actually passes the
Distil/Imperva challenge; the persistent patchright context stays as the
default fallback.
"""
from __future__ import annotations

import logging

from scraper.config import PROFILE_DIR, HEADLESS, LOCALE, TIMEZONE, CDP_URL

log = logging.getLogger(__name__)

# patchright is a drop-in replacement of playwright
try:
    from patchright.sync_api import (
        sync_playwright,
        Browser,
        BrowserContext,
        Playwright,
    )
except ImportError:  # pragma: no cover - depends on the environment
    log.warning("patchright no disponible, usando playwright (mas detectable).")
    from playwright.sync_api import (  # type: ignore
        sync_playwright,
        Browser,
        BrowserContext,
        Playwright,
    )

# Browser connected over CDP (None in persistent mode); tracked so shutdown
# disconnects instead of closing the external Chrome.
_connected_browser: Browser | None = None


def launch_persistent_context(playwright: Playwright) -> BrowserContext:
    if not PROFILE_DIR.exists():
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=HEADLESS,
        channel="chrome",
        # Timeout ampliado (5 min): en el nightly se lanzan varios Chrome en
        # paralelo (CDP de Indeed + subscrapers multi_site) y el arranque puede
        # superar los 180s por defecto, abortando el run completo.
        timeout=300000,
        # Importante: no forzar un viewport fijo. Usamos no_viewport=True para
        # que el layout coincida con la ventana REAL del navegador (maximizada
        # via --start-maximized). Si fijamos viewport=1920x1080 pero la ventana
        # del OS es menor (p.ej. ajuste de escala de fuente != 100%), el captcha
        # se renderiza fuera del area visible y es imposible resolverlo a mano.
        no_viewport=True,
        locale=LOCALE,
        timezone_id=TIMEZONE,
        args=["--start-maximized", "--start-fullscreen"],
    )
    return context


def get_browser_context() -> tuple[Playwright, BrowserContext]:
    global _connected_browser
    playwright: Playwright = sync_playwright().start()
    if CDP_URL:
        log.info(
            "conectando por CDP a Chrome existente en %s (sin flags de "
            "automatizacion de Playwright)",
            CDP_URL,
        )
        try:
            _connected_browser = playwright.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            playwright.stop()
            raise
        contexts = _connected_browser.contexts
        context = contexts[0] if contexts else _connected_browser.new_context()
        return playwright, context
    context = launch_persistent_context(playwright)
    return playwright, context


def close_browser_context(playwright: Playwright, context: BrowserContext) -> None:
    """Release the browser without killing an external Chrome in CDP mode.

    In CDP mode the browser was launched outside Playwright; ``close()``
    disconnects from it and its cookies/session stay in the Chrome profile
    (the Indeed pattern). In persistent mode the context is closed as before.
    Never raises: shutdown must not mask the run result.
    """
    global _connected_browser
    connected = _connected_browser
    _connected_browser = None
    try:
        if connected is not None:
            connected.close()
        else:
            context.close()
    except Exception:
        pass
    try:
        playwright.stop()
    except Exception:
        pass


def pausa_manual(page, mensaje: str) -> None:
    from rich.console import Console
    console = Console()
    console.print(f"\n[bold yellow]>>> {mensaje}[/]")
    console.print("[bold yellow]>>> Pulsa ENTER en esta terminal cuando estes listo.[/]\n")
    input()
