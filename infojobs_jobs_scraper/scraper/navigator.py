import html as html_lib
import os
import random
import re
import time
import unicodedata

# patchright is a drop-in replacement of playwright (binary-level anti-detection
# patches). Fallback keeps the scraper usable if it is not installed.
try:
    from patchright.sync_api import Page
except ImportError:  # pragma: no cover - depends on the environment
    from playwright.sync_api import Page  # type: ignore

from rich.console import Console
from scraper.config import (
    SCROLL_STEP,
    SCROLL_WAIT_MIN,
    SCROLL_WAIT_MAX,
    BASE_URL,
)

console = Console()

# T-27/T-28: challenge markers and session cookie. Only cookie *names* are
# inspected; values are never read nor logged (RF-11).
CHALLENGE_COOKIE_NAME = "reese84"
COOKIE_BANNER_SELECTORS: tuple[str, ...] = (
    "#didomi-notice-agree-button",
    "button[id*='didomi'][id*='agree']",
    "button[aria-label*='Aceptar']",
)

_CANONICAL_TAG_RE = re.compile(r"(?is)<link\b[^>]*>")
_CANONICAL_REL_RE = re.compile(r"(?i)\brel\s*=\s*[\"']?canonical")
_HREF_RE = re.compile(r"(?i)\bhref\s*=\s*[\"']([^\"']*)")
_H1_RE = re.compile(r"(?is)<h1\b[^>]*>(.*?)</h1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_IFRAME_DISTIL_RE = re.compile(r"(?is)<iframe\b[^>]*\bsrc\s*=\s*[\"'][^\"']*distil")
_IMG_SHERLOCK_RE = re.compile(r"(?is)<img\b[^>]*\bsrc\s*=\s*[\"'][^\"']*sherlock")


def respectful_sleep(min_s: float, max_s: float) -> None:
    duration = random.uniform(min_s, max_s)
    time.sleep(duration)


def _normalize_text(text: str) -> str:
    """Lowercase and strip accents so marker matching is stable."""
    unescaped = html_lib.unescape(text)
    decomposed = unicodedata.normalize("NFKD", unescaped)
    return "".join(
        char for char in decomposed if not unicodedata.combining(char)
    ).lower()


def _canonical_href(html: str) -> str:
    for tag in _CANONICAL_TAG_RE.findall(html):
        if _CANONICAL_REL_RE.search(tag):
            match = _HREF_RE.search(tag)
            if match:
                return match.group(1)
    return ""


def _heading_text(html: str) -> str:
    match = _H1_RE.search(html)
    if match is None:
        return ""
    return _TAG_RE.sub(" ", match.group(1))


def captcha_marker_from_html(html: str, url: str = "") -> str | None:
    """Return the challenge marker found in the page, or ``None`` when clean.

    Markers come from the T-27 investigation of the real Distil/Imperva +
    GeeTest challenge: the URL, the canonical link, the heading, a GeeTest
    script/``initGeetest`` call and a Distil iframe; the ``sherlock`` image is
    kept as a residual marker. Pure function: no browser and no network, so
    every real variant is unit-testable offline.
    """
    lowered_url = (url or "").lower()
    if "/distil/" in lowered_url:
        return "url_distil"
    if "captcha" in lowered_url:
        return "url_captcha"

    canonical = _canonical_href(html).lower()
    if "captcha" in canonical:
        return "canonical_captcha"
    if "distil" in canonical:
        return "canonical_distil"

    heading = _normalize_text(_heading_text(html))
    if "eres humano" in heading or "un robot" in heading:
        return "h1_human_check"
    if "no podemos identificar tu navegador" in heading:
        return "h1_legacy"

    if "geetest" in html.lower():
        return "geetest"
    if _IFRAME_DISTIL_RE.search(html):
        return "iframe_distil"
    if _IMG_SHERLOCK_RE.search(html):
        return "sherlock"
    return None


def captcha_marker(page: Page) -> str | None:
    """Inspect the live page and return the marker that fires, if any.

    Reading failures (detached page, closed context) degrade to an empty
    value for that source: the marker is only reported when observed.
    """
    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    html = ""
    try:
        html = page.content()
    except Exception:
        pass
    return captcha_marker_from_html(html, url)


def handle_captcha(page: Page) -> tuple[bool, str | None]:
    """Apply the abort policy for a visible challenge (person, 2026-10-08).

    Returns ``(True, None)`` when the page is clean and ``(False, marker)``
    when a challenge is present, after logging the marker. There is no long
    pause and no blind reload: reloading could renew the challenge and worsen
    the session reputation. Solving the challenge is out of scope.
    """
    marker = captcha_marker(page)
    if marker is None:
        return True, None
    console.log(
        f"[red]Challenge visible detectado ({marker}); "
        "se aborta sin recargas ni pausas[/]"
    )
    return False, marker


def session_has_reese84(page: Page) -> bool:
    """True when the persistent session carries the Distil challenge token.

    Only the cookie name is checked; its value is never logged (RF-11).
    """
    try:
        cookies = page.context.cookies()
    except Exception:
        return False
    return any(
        isinstance(cookie, dict) and cookie.get("name") == CHALLENGE_COOKIE_NAME
        for cookie in cookies
    )


def accept_cookies(page: Page) -> bool:
    """Best-effort click on the Didomi cookie banner, when it appears."""
    for selector in COOKIE_BANNER_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=1000):
                button.click(timeout=3000)
                console.log("  [dim]Banner de cookies aceptado[/]")
                return True
        except Exception:
            continue
    return False


def warm_up(page: Page) -> None:
    """Human-like warm-up before the first SERP: home, cookies, short pause.

    Visits the home page and accepts the cookie banner (best-effort) so the
    first search request does not arrive on a cold, cookieless session.
    """
    console.log("  [dim]Warm-up: home + banner de cookies[/]")
    try:
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        console.log(
            "  [yellow]Warm-up: no se pudo cargar la home; se continua[/]"
        )
        return
    respectful_sleep(2, 4)
    accept_cookies(page)
    respectful_sleep(1, 2)


def slow_scroll_to_bottom(page: Page) -> None:
    prev_height = page.evaluate("document.body.scrollHeight")
    while True:
        page.mouse.wheel(0, SCROLL_STEP)
        respectful_sleep(SCROLL_WAIT_MIN, SCROLL_WAIT_MAX)
        new_height = page.evaluate("document.body.scrollHeight")
        if new_height == prev_height:
            page.mouse.wheel(0, SCROLL_STEP * 2)
            respectful_sleep(SCROLL_WAIT_MIN * 2, SCROLL_WAIT_MAX * 2)
            new_height = page.evaluate("document.body.scrollHeight")
            if new_height == prev_height:
                break
        prev_height = new_height


def random_mouse_move(page: Page) -> None:
    vs = page.viewport_size
    if vs is None:
        return
    w, h = vs["width"], vs["height"]
    x = random.randint(w // 4, w * 3 // 4)
    y = random.randint(h // 4, h * 3 // 4)
    page.mouse.move(x, y)


def auto_login(page: Page) -> bool:
    email = os.getenv("INFOJOBS_EMAIL")
    password = os.getenv("INFOJOBS_PASSWORD")
    if not email or not password:
        console.log("[yellow]INFOJOBS_EMAIL o INFOJOBS_PASSWORD no configurados en .env[/]")
        return False

    console.log("  [dim]Iniciando sesion automatica...[/]")
    page.goto(f"{BASE_URL}/candidate/login/index.xhtml", wait_until="domcontentloaded", timeout=20000)
    respectful_sleep(2, 3)

    clean, marker = handle_captcha(page)
    if not clean:
        console.log(f"  [red]Challenge en login ({marker}); abortando login[/]")
        return False

    try:
        email_input = page.locator("input[type='email'], input[name*='email'], input[name*='user']").first
        if email_input.is_visible():
            email_input.fill(email)
            respectful_sleep(0.3, 0.6)
    except Exception:
        console.log("  [yellow]Campo email no encontrado[/]")
        return False

    try:
        pass_input = page.locator("input[type='password']").first
        if pass_input.is_visible():
            pass_input.fill(password)
            respectful_sleep(0.3, 0.6)
    except Exception:
        console.log("  [yellow]Campo password no encontrado[/]")
        return False

    try:
        submit_btn = page.locator("button[type='submit'], input[type='submit']").first
        if submit_btn.is_visible():
            submit_btn.click()
    except Exception:
        pass

    respectful_sleep(3, 5)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass

    clean, marker = handle_captcha(page)
    if not clean:
        console.log(f"  [red]Challenge en login ({marker}); abortando login[/]")
        return False

    logged_in = False
    try:
        page.wait_for_selector("a[title*='ACCESO CANDIDATOS']", timeout=5000)
        logged_in = page.locator("a[title*='ACCESO CANDIDATOS']").is_visible()
    except Exception:
        pass

    if not logged_in:
        try:
            has_menu = page.locator(".ij-Menu, .user-menu, [data-testid='user-menu']").first
            logged_in = has_menu.is_visible()
        except Exception:
            pass

    if logged_in:
        console.log("  [green]Sesion iniciada automaticamente![/]")
    else:
        console.log("  [yellow]No se detecto sesion iniciada, continuando...[/]")

    return True
