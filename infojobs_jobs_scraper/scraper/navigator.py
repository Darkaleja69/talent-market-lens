import time
import random
import os
from playwright.sync_api import Page
from rich.console import Console
from scraper.config import (
    SCROLL_STEP,
    SCROLL_WAIT_MIN,
    SCROLL_WAIT_MAX,
    BASE_URL,
    CAPTCHA_TIMEOUT,
    CAPTCHA_MAX_ATTEMPTS,
)

console = Console()


def respectful_sleep(min_s: float, max_s: float) -> None:
    duration = random.uniform(min_s, max_s)
    time.sleep(duration)


def detect_captcha(page: Page) -> bool:
    url = page.url.lower()
    if "/distil/" in url or "captcha" in url:
        return True
    try:
        heading = page.locator("h1").first.text_content() or ""
        if "no podemos identificar tu navegador" in heading.lower():
            return True
    except Exception:
        pass
    try:
        canonical = page.locator("link[rel='canonical']").get_attribute("href") or ""
        if "captcha" in canonical or "distil" in canonical:
            return True
    except Exception:
        pass
    try:
        image_alt = page.locator("img[alt='']").first.get_attribute("src") or ""
        if "sherlock" in image_alt.lower():
            return True
    except Exception:
        pass
    return False


def handle_captcha(
    page: Page,
    max_attempts: int = CAPTCHA_MAX_ATTEMPTS,
    timeout: int = CAPTCHA_TIMEOUT,
) -> bool:
    for attempt in range(1, max_attempts + 1):
        console.log(
            f"[bold yellow]CAPTCHA detectado (intento {attempt}/{max_attempts})[/] "
            f"[white]Resuelvelo en el navegador... esperando hasta {timeout}s...[/]"
        )
        # Intentar hacer scroll del iframe/elemento del captcha a la vista para
        # que sea visible en pantallas pequenas. Heuristica: captchas de InfoJobs
        # suelen estar dentro de iframes de distilcloudframe; scroll al centro.
        for _scroll in range(3):
            try:
                page.evaluate(
                    "() => {"
                    "  const iframe = document.querySelector('iframe[src*=\"distil\"], iframe[src*=\"captcha\"], iframe[title*=\"captcha\"], iframe[title*=\"challenge\"]');"
                    "  if (iframe) iframe.scrollIntoView({block:'center'});"
                    "  else window.scrollTo(0, Math.max(document.body.scrollHeight - window.innerHeight - 50, 0));"
                    "}"
                )
                time.sleep(0.5)
            except Exception:
                pass
        for _catch in range(timeout * 2):
            time.sleep(0.5)
            try:
                if not detect_captcha(page):
                    console.log("[green]CAPTCHA resuelto![/]")
                    time.sleep(2)
                    return True
            except Exception:
                pass
        console.log("  [yellow]Timeout, forzando recarga...[/]")
        try:
            page.reload(wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            if not detect_captcha(page):
                console.log("[green]CAPTCHA resuelto tras recarga![/]")
                return True
        except Exception:
            pass
    return False


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

    if detect_captcha(page):
        ok = handle_captcha(page)
        if not ok:
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

    if detect_captcha(page):
        handle_captcha(page)

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
