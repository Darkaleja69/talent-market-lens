"""Rutinas anti-bot: delays aleatorios, scrolling gradual humano y deteccion de CAPTCHA.

Filosofia: sin proxy, sin CAPTCHA solver. Minimizamos la probabilidad de que
salte un challenge siendo un "humano tranquilo":
  - delays largos y aleatorios entre acciones
  - scrolling muy poco a poco (pasos pequenos de 200-400px con jitter y pausas)
  - deteccion temprana de challenges; si aparece, pausamos y avisamos (sin coste)
"""
from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING

from .config import (
    BACKOFF_MAX_RETRIES,
    BACKOFF_RETRY_DELAY,
    CAPTCHA_TITLE_MARKERS,
    CAPTCHA_URL_MARKERS,
    DELAY_BETWEEN_CITIES,
    DELAY_BETWEEN_COUNTRIES,
    DELAY_BETWEEN_PAGES,
    DELAY_BETWEEN_TERMS,
    DELAY_PRE_NAV,
    SCROLL_PAUSE_BIG_DELAY,
    SCROLL_PAUSE_BIG_EVERY,
    SCROLL_SETTLE_AFTER,
    SCROLL_STEP_DELAY,
    SCROLL_STEP_PX,
)

if TYPE_CHECKING:
    from patchright.sync_api import Page

log = logging.getLogger(__name__)


class CaptchaDetected(Exception):
    """Se ha detectado un challenge anti-bot. El scraper debe pausar y avisar."""

    def __init__(self, reason: str, url: str = "", title: str = ""):
        super().__init__(f"CAPTCHA/challenge detectado: {reason} (url={url!r}, title={title!r})")
        self.reason = reason
        self.url = url
        self.title = title


# --- Delays aleatorios -----------------------------------------------------
def delay(range_s: tuple[float, float], label: str = "") -> None:
    """Espera un tiempo aleatorio uniforme dentro del rango dado."""
    t = random.uniform(*range_s)
    if label:
        log.debug("esperando %.1fs (%s)", t, label)
    time.sleep(t)


def delay_pre_nav() -> None:
    delay(DELAY_PRE_NAV, "pre-navegacion")


def delay_between_pages() -> None:
    delay(DELAY_BETWEEN_PAGES, "entre paginas")


def delay_between_cities() -> None:
    delay(DELAY_BETWEEN_CITIES, "entre ciudades")


def delay_between_terms() -> None:
    delay(DELAY_BETWEEN_TERMS, "entre terminos")


def delay_between_countries() -> None:
    delay(DELAY_BETWEEN_COUNTRIES, "entre paises")


def backoff_delay() -> None:
    log.warning("backoff: esperando %.0fs antes de reintentar con nueva sesion", BACKOFF_RETRY_DELAY)
    time.sleep(BACKOFF_RETRY_DELAY)


# --- Deteccion de CAPTCHA / challenges -------------------------------------
def detect_captcha(page: "Page") -> None:
    """Inspecciona la pagina actual y lanza CaptchaDetected si hay un challenge.

    Comprueba:
      - URL: marcadores como /captcha, /cdn-cgi/challenge-platform/, px-captcha...
      - <title>: "Just a moment", "Verificacion", "Captcha"...
      - iframes de proveedores de challenge (cloudflare, datadome, arkose, perimeterx)

    Lanza CaptchaDetected con info para que el runner haga screenshot + avise.
    """
    url = page.url or ""
    title = ""
    try:
        title = (page.title() or "").strip()
    except Exception:
        pass

    url_lower = url.lower()
    title_lower = title.lower()

    for marker in CAPTCHA_URL_MARKERS:
        if marker in url_lower:
            raise CaptchaDetected(f"URL contiene {marker!r}", url=url, title=title)

    for marker in CAPTCHA_TITLE_MARKERS:
        if marker in title_lower:
            raise CaptchaDetected(f"title contiene {marker!r}", url=url, title=title)

    # iframes de challenge (Cloudflare, DataDome, Arkose, PerimeterX)
    challenge_iframe_selectors = [
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[src*="challenges.cloudflare.com"]',
        'iframe[id="px-captcha"]',
        'iframe[title*="captcha" i]',
        'iframe[title*="challenge" i]',
        'iframe[src*="datadome.co"]',
        'iframe[src*="arkoselabs"]',
        'iframe[src*="funcaptcha"]',
        '#px-captcha',
        '.cf-turnstile',
    ]
    for sel in challenge_iframe_selectors:
        try:
            if page.locator(sel).count() > 0:
                raise CaptchaDetected(f"iframe/elemento de challenge presente: {sel}", url=url, title=title)
        except CaptchaDetected:
            raise
        except Exception:
            continue

    # body text: challenges de Cloudflare que no cambian title ni URL
    # (ej. "Additional Verification Required" con Ray ID)
    body_markers = (
        "additional verification required",
        "troubleshooting cloudflare errors",
        "your ray id",
        "attention required! | cloudflare",
        "please verify you are a human",
    )
    try:
        body_text = (page.inner_text("body", timeout=2000) or "")[:2000].lower()
        for marker in body_markers:
            if marker in body_text:
                raise CaptchaDetected(f"body contiene {marker!r}", url=url, title=title)
    except CaptchaDetected:
        raise
    except Exception:
        pass


def should_retry(attempt: int) -> bool:
    return attempt < BACKOFF_MAX_RETRIES


# --- Scrolling gradual humano ----------------------------------------------
def human_scroll(page: "Page") -> None:
    """Scroll muy poco a poco, como un humano que ojea la lista de ofertas.

    Estrategia (la que pidio el usuario):
      - pasos de 200-400px (aleatorio) cada 0.8-1.5s
      - cada 5-9 pasos, pausa larga de 2-4s (como si leyera una oferta)
      - avanza hasta llegar al fondo de la pagina
      - jitter no lineal: a veces un paso un poco mas grande, a veces retrocede un poco
      - al final, espera a que asienten las cargas diferidas (lazy load de job cards)
    """
    viewport_h = page.viewport_size.get("height", 1080) if page.viewport_size else 1080
    big_every = random.randint(*SCROLL_PAUSE_BIG_EVERY)
    step = 0
    last_y = -1
    stalled = 0

    while True:
        step += 1
        # tamano del paso con jitter ocasional
        base = random.randint(*SCROLL_STEP_PX)
        if random.random() < 0.15:  # 15% de las veces un paso un poco mayor
            base += random.randint(80, 160)
        if random.random() < 0.08:  # 8% retroceder un poco (muy humano)
            base = -random.randint(60, 140)

        page.mouse.wheel(0, base)
        delay(SCROLL_STEP_DELAY)

        # pausa larga cada N pasos
        if step % big_every == 0:
            delay(SCROLL_PAUSE_BIG_DELAY, "pausa de lectura")

        # detectar fin de pagina: si la posicion Y no avanza en 2 iteraciones, salimos
        try:
            y = page.evaluate("() => window.scrollY")
        except Exception:
            y = 0
        if y == last_y:
            stalled += 1
            if stalled >= 2:
                break
        else:
            stalled = 0
        last_y = y

        # salvaguarda: si hemos bajado mas de 30 paginas de viewport, paramos
        if y > viewport_h * 30:
            break

    # asentar cargas diferidas
    time.sleep(SCROLL_SETTLE_AFTER)
    log.debug("scroll completado en %d pasos", step)
