"""Login de LinkedIn con tu propia cuenta (legitimo, no bypass).

Flujo:
 1. Si storage_state.json valido -> se carga y se verifica navegando al feed.
 2. Si no / caducado -> login con email+password desde .env.
 3. Si aparece 2FA -> pausa y pide el codigo OTP por input() en consola
    (NO se automatiza el OTP).
 4. Tras exito -> guarda storage_state.json (valido ~1 ano salvo logout).

Senales de bloqueo que hacemos respetar (NO se eluden):
 - URL /checkpoint/  -> challenge de LinkedIn. Se detiene y lanza BlockedException.
 - iframe de reCAPTCHA -> idem.
 - Pagina "authwall" sin resultado -> sesion caducada, reintentar login.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from .browser import STORAGE_STATE_PATH, save_storage_state
from .human import delay, mouse_jitter

log = logging.getLogger(__name__)

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"
JOBS_URL = "https://www.linkedin.com/jobs/"


class BlockedException(Exception):
    """LinkedIn mostro un challenge (checkpoint / CAPTCHA). No se elude."""


class LoginFailedError(Exception):
    """Login fallo tras reintentos (credenciales, 2FA no resuelto, etc)."""


# Selectores del formulario de login (verificados contra HTML real 2026).
# IMPORTANTE: LinkedIn randomiza los id de los inputs con guillemets « »
# en cada carga, y la app actual (Flagship/SDUI) NO tiene <form> ni
# button[type="submit"]. Usamos atributos semanticos estables (autocomplete,
# type) y resolvemos la visibilidad en runtime porque existen copias
# responsive duplicadas (solo 1 visible).
SEL_EMAIL = 'input[autocomplete="username"]'
SEL_PASSWORD = 'input[autocomplete="current-password"]'
SEL_EMAIL_FALLBACK = 'input[type="email"]'
SEL_PASSWORD_FALLBACK = 'input[type="password"]'
# Texto del boton de submit (es) / (en) — no hay type="submit"; el boton real
# es type="button". Se prefiere Enter en el campo password y, como fallback,
# un boton cuyo texto normalizado sea exacto (excluye SSO).
SUBMIT_TEXTS_ES = ["Iniciar sesión", "Inicia sesión"]
SUBMIT_TEXTS_EN = ["Sign in"]
# Boton que revela el formulario en los modales contextuales guest.
SIGN_IN_WITH_EMAIL_TEXTS = ["Sign in with Email", "Iniciar sesión con correo electrónico"]
# Indicadores de sesion iniciada (cualquiera de ellos). No usar enlaces a
# /jobs/ (la pagina guest tambien los tiene) ni la URL /jobs (guest la usa).
# Verificados contra el DOM real 2026-09: LinkedIn rediseno el nav y elimino
# meta#config, .global-nav y las clases antiguas. El nav autenticado ahora
# expone botones/links con aria-label "Mi red", "Empleos,", "Notificaciones",
# "Activar Premium", etc. La pagina guest NO tiene header (verificado).
SEL_FEED_INDICATORS = [
    'a[aria-label*="Mi red"], button[aria-label*="Mi red"]',
    'a[aria-label*="Inicio,"], button[aria-label*="Inicio,"]',
    'button[aria-label*="Notificaciones"]',
    'a[aria-label*="Mensajes"], button[aria-label*="Mensajes"]',
    '[aria-label*="Activar Premium"]',
    ".global-nav__me-photo",
    '[data-test-id="global-nav__me"]',
    'input[placeholder*="busca"]',
    'input[placeholder*="Search"]',
]
# URLs que indican sesion iniciada (no estamos en login/authwall).
# NOTA: se excluye "/jobs" porque la pagina guest publica tambien usa /jobs.
LOGGED_IN_URL_SUBSTRINGS = ["/feed", "/my-items", "/mynetwork", "/notifications"]
# Marcas explicitas de pagina guest (no autenticada) en el meta#config.
GUEST_META_SELECTORS = [
    'meta[name="pageKey"][content="d_jobs_guest_search"]',
    'meta[name="pageKey"][content="d_jobs_guest_details"]',
    'meta[id="config"][data-multiproduct-name="jobs-guest-frontend"]',
]
# Indicadores de challenge / bloqueo real (NO incluye /authwall que es solo
# el muro de login para invitados — no es un CAPTCHA ni un challenge).
CHALLENGE_URL_SUBSTRINGS = ["/checkpoint/", "/uas/challenge"]
# /authwall es el muro de login: redirige a invitados. No es un bloqueo real,
# solo significa "no logueado, proceder a login".
AUTHWALL_SUBSTRING = "/authwall"
# Banner de cookies (inyectado por JS tras hidratacion; no en HTML estatico)
COOKIE_ACCEPT_TEXTS = ["Aceptar", "Accept", "Akzeptieren", "J'accepte"]


def _is_challenge_url(url: str) -> bool:
    return any(sub in url for sub in CHALLENGE_URL_SUBSTRINGS)


def _detect_recaptcha(page) -> bool:
    """True si hay un challenge de reCAPTCHA VISIBLE en la pagina.

    IMPORTANTE: LinkedIn carga reCAPTCHA v3 (invisible, scoring en segundo
    plano) en TODAS sus paginas. Eso NO es un challenge — es un script de
    puntuacion que no bloquea al usuario. Solo marcamos bloqueo si hay un
    iframe de reCAPTCHA v2 VISIBLE (checkbox / image challenge).
    """
    try:
        # Buscar iframes de reCAPTCHA que sean visibles (challenge real)
        iframes = page.query_selector_all(
            'iframe[src*="recaptcha"][src*="bframe"], '
            'iframe[src*="recaptcha"][src*="anchor"], '
            'iframe[title*="reCAPTCHA"]'
        )
        for iframe in iframes:
            try:
                if iframe.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        # Tambien comprobar si la URL del frame principal indica captcha
        if "captcha" in (page.url or "").lower():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _is_guest_page(page) -> bool:
    """True si la pagina es publica/guest (no autenticada).

    La pagina guest de Jobs usa data-member-id="0" en meta#config y un
    pageKey d_jobs_guest_*. Esto evita falsos positivos de sesion iniciada
    cuando la URL contiene /jobs.
    """
    try:
        cfg = page.query_selector("meta#config")
        if cfg and cfg.get_attribute("data-member-id") == "0":
            return True
    except Exception:  # noqa: BLE001
        pass
    for sel in GUEST_META_SELECTORS:
        try:
            if page.query_selector(sel):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _is_headless(config: dict[str, Any]) -> bool:
    """Misma logica que browser._resolve_headless: HEADLESS del .env manda."""
    env_val = os.getenv("HEADLESS")
    if env_val is not None:
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    return bool(config.get("browser", {}).get("headless", config.get("headless", False)))


def _manual_challenge_resolution(page, context, config: dict[str, Any]) -> bool:
    """Si el navegador es visible, espera a que el HUMANO resuelva el challenge
    (CAPTCHA / 2FA) en la ventana y revalida la sesion. Devuelve True si la
    sesion quedo iniciada (y se refresco storage_state). En headless nadie
    puede resolverlo -> devuelve False para que el llamador aborte/commute.

    No elude nada: solo da tiempo a que una persona resuelva el challenge.
    """
    if _is_headless(config):
        return False
    timeout_sec = int(config.get("challenge_wait_minutes", 5)) * 60
    log.warning("=== CHALLENGE DE LINKEDIN DETECTADO ===")
    log.warning("Resuelvelo MANUALMENTE en la ventana del navegador")
    log.warning("(CAPTCHA o verificacion 2FA). Esperando hasta %d min...",
                timeout_sec // 60)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        delay((3.0, 5.0), "esperando-resolucion-manual")
        try:
            url = page.url or ""
            if (not _is_challenge_url(url) and not _detect_recaptcha(page)
                    and _is_logged_in(page)):
                log.info("Challenge resuelto manualmente. Sesion iniciada.")
                try:
                    save_storage_state(context)
                except Exception:  # noqa: BLE001
                    log.debug("save_storage_state opcional fallo.", exc_info=True)
                return True
        except Exception:  # noqa: BLE001
            log.warning("Pagina no accesible durante espera manual "
                        "(navegador cerrado?). Abortando resolucion.")
            return False
    log.warning("Tiempo de espera manual agotado (%d min). Lanzando bloqueo.",
                timeout_sec // 60)
    return False


def _pick_visible(locator):
    """Devuelve el primer elemento VISIBLE de un locator, o None.

    LinkedIn renderiza copias responsive duplicadas (solo 1 visible).
    """
    try:
        n = locator.count()
    except Exception:  # noqa: BLE001
        return None
    for i in range(n):
        try:
            el = locator.nth(i)
            if el.is_visible():
                return el
        except Exception:  # noqa: BLE001
            continue
    return None


def _is_logged_in(page) -> bool:
    """Comprueba si la pagina actual muestra indicadores de sesion iniciada."""
    try:
        url = page.url
        if _is_challenge_url(url):
            return False
        # Una pagina guest (member-id=0) nunca es una sesion iniciada, aunque
        # su URL contenga /jobs.
        if _is_guest_page(page):
            return False
        # Senal mas fuerte: meta#config con data-member-id != "0" -> usuario
        # autenticado. Funciona en /jobs, /feed y cualquier pagina flagship
        # (los indicadores de nav pueden fallar por cambio de clases o por
        # selectores CSS case-sensitive, p.ej. placeholder="Buscar" vs
        # [placeholder*="busca"]).
        try:
            cfg = page.query_selector("meta#config")
            if cfg:
                mid = cfg.get_attribute("data-member-id")
                if mid and mid.strip() and mid.strip() != "0":
                    return True
        except Exception:  # noqa: BLE001
            pass
        # URL tipica de sesion iniciada + confirmacion con un selector del DOM.
        if any(sub in url for sub in LOGGED_IN_URL_SUBSTRINGS) and "/login" not in url:
            for sel in SEL_FEED_INDICATORS:
                if page.query_selector(sel):
                    return True
        for sel in SEL_FEED_INDICATORS:
            if page.query_selector(sel):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _dismiss_cookie_banner(page) -> None:
    """Descarta un banner de cookies si aparece (inyectado por JS tras hidratacion).

    Best-effort: busca botones con texto exacto 'Aceptar'/'Accept' y hace click.
    Evita clickar 'Aceptar y unirse a LinkedIn' usando match exacto.
    """
    try:
        for text in COOKIE_ACCEPT_TEXTS:
            btn = page.get_by_role("button", name=text, exact=True)
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                log.debug("Cookie banner descartado con '%s'.", text)
                delay((0.5, 1.0), "post-cookie-dismiss")
                return
    except Exception as e:  # noqa: BLE001
        log.debug("Cookie banner no detectado o no se pudo descartar: %s", e)


def _goto_with_retry(page, url: str, timeout_ms: int = 60000,
                     attempts: int = 2) -> None:
    """page.goto con un reintento ante fallos de red (timeout / net::ERR).

    Los timeouts de red en login son frecuentes (vistos en los logs
    nocturnos: Page.goto Timeout 45000ms en /jobs/ y /login). Un intento
    mas con pausa evita que la run nocturna se rinda en el primer fallo.
    Si todos los intentos fallan, la excepcion se propaga para que main.py
    decida (conmutar el run completo a Apify en lugar de reintentar en bucle).
    """
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            is_network = "timeout" in msg or "net::err" in msg or "err_" in msg
            if attempt >= attempts or not is_network:
                raise
            log.warning("goto %s fallo (intento %d/%d): %s. Reintentando.",
                        url, attempt, attempts, e)
            delay((3.0, 6.0), "post-goto-retry")


def ensure_logged_in(context, config: dict[str, Any]) -> bool:
    """Garantiza sesion iniciada en LinkedIn. Devuelve True si ok.

    Lanza BlockedException si aparece un challenge (no se elude).
    Lanza LoginFailedError si las credenciales fallan o 2FA no se resuelve.
    """
    page = context.new_page()
    try:
        # 1. Intentar reusar storage_state (el perfil persistente ya trae cookies)
        log.info("Verificando sesion existente via %s", JOBS_URL)
        _goto_with_retry(page, JOBS_URL)
        delay((2.0, 4.0), "post-goto-jobs")
        _dismiss_cookie_banner(page)
        mouse_jitter(page, n=1)

        # authwall = no logueado (NO es un challenge). checkpoint/captcha = bloqueo real.
        # IMPORTANTE: /authwall es el muro de login para invitados. NO debe lanzar
        # BlockedException (-era el bug de run.log:10). Solo procede a login.
        # Solo disparamos BlockedException ante un challenge REAL (checkpoint/captcha).
        url = page.url or ""
        is_authwall = AUTHWALL_SUBSTRING in url
        if _detect_recaptcha(page) or (_is_challenge_url(url) and not is_authwall):
            # Challenge real (CAPTCHA / checkpoint). Intentar resolucion manual
            # (ventana visible) antes de abortar; en headless aborta directo.
            if _manual_challenge_resolution(page, context, config):
                return True
            raise BlockedException(
                f"Challenge real detectado en {url}. Resuelvelo manualmente o usa Apify."
            )
        # Si estamos en /authwall sin challenge, caer abajo a _do_login().

        if _is_logged_in(page):
            log.info("Sesion ya activa (storage_state/perfil valido).")
            # Refrescar storage_state por si acaso
            try:
                save_storage_state(context)
            except Exception as e:  # noqa: BLE001
                log.debug("save_storage_state opcional fallo: %s", e)
            return True

        # 2. Hacer login
        log.info("Sesion no activa. Procediendo a login en %s", LOGIN_URL)
        return _do_login(page, context, config)
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass


def _save_login_diagnostic(page) -> None:
    """Guarda diagnostico ligero del fallo de login (sin credenciales).

    Escribe un resumen de texto (URL, titulo, selectores encontrados y su
    visibilidad) y una captura de pantalla. NO guarda el HTML completo porque
    podria contener el email tecleado en el DOM.
    """
    try:
        import datetime as dt
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent
        diag_dir = project_root / "data" / "raw_html"
        diag_dir.mkdir(parents=True, exist_ok=True)
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = diag_dir / f"login_diag_{ts}"
        lines: list[str] = []
        try:
            lines.append(f"url={page.url}")
        except Exception:  # noqa: BLE001
            pass
        try:
            lines.append(f"title={page.title()}")
        except Exception:  # noqa: BLE001
            pass
        for label, sel in [
            ("email", SEL_EMAIL), ("email_fb", SEL_EMAIL_FALLBACK),
            ("password", SEL_PASSWORD), ("password_fb", SEL_PASSWORD_FALLBACK),
        ]:
            try:
                loc = page.locator(sel)
                n = loc.count()
                vis = sum(1 for i in range(n) if loc.nth(i).is_visible()) if n else 0
                lines.append(f"{label}={sel} count={n} visible={vis}")
            except Exception:  # noqa: BLE001
                lines.append(f"{label}={sel} ERROR")
        base.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
        try:
            page.screenshot(path=str(base.with_suffix(".png")))
        except Exception:  # noqa: BLE001
            pass
        log.info("Diagnostico de login guardado en %s", base)
    except Exception as e:  # noqa: BLE001
        log.debug("No se pudo guardar diagnostico de login: %s", e)


def _do_login(page, context, config: dict[str, Any]) -> bool:
    email = os.getenv("LINKEDIN_EMAIL")
    password = os.getenv("LINKEDIN_PASS")
    if not email or not password or email.startswith("tu_"):
        raise LoginFailedError(
            "Falta configurar LINKEDIN_EMAIL / LINKEDIN_PASS en .env"
            " (copia .env.example a .env y rellenalo)."
        )

    _goto_with_retry(page, LOGIN_URL)
    # Esperar a que React hidrate el DOM (los click handlers no funcionan antes)
    delay((2.0, 4.0), "post-goto-login")
    _dismiss_cookie_banner(page)

    # Si /login redirige a una pagina autenticada (feed, jobs), la sesion ya
    # estaba activa aunque la deteccion previa fallo -> aceptar sin credenciales
    # (evita el falso "campo de email no encontrado").
    if "/login" not in page.url and _is_logged_in(page):
        log.info("Sesion ya activa (redirigido de /login a %s).", page.url)
        save_storage_state(context)
        return True

    if _detect_recaptcha(page) or _is_challenge_url(page.url):
        if _manual_challenge_resolution(page, context, config):
            return True
        raise BlockedException(f"Challenge en login {page.url}")

    # Esperar a que exista el input de email (React hydration completa).
    # Se espera "attached" en vez de "visible" porque el formulario puede estar
    # oculto tras el boton "Sign in with Email" de un modal contextual.
    try:
        page.wait_for_selector(SEL_EMAIL, state="attached", timeout=15000)
    except Exception:  # noqa: BLE001
        try:
            page.wait_for_selector(SEL_EMAIL_FALLBACK, state="attached", timeout=10000)
        except Exception:  # noqa: BLE001
            # La redireccion a /checkpoint/ puede ocurrir DURANTE la espera
            # (la pagina /login deriva a un challenge de seguridad). En ese
            # caso es un bloqueo real, no un fallo de credenciales.
            if _is_challenge_url(page.url) or _detect_recaptcha(page):
                if _manual_challenge_resolution(page, context, config):
                    return True
                raise BlockedException(
                    f"Challenge real durante login en {page.url}"
                )
            _save_login_diagnostic(page)
            raise LoginFailedError(
                "No se encontro el campo de email en la pagina de login."
                " LinkedIn puede haber cambiado el DOM o estar bloqueando."
            )

    # Si el formulario esta oculto (modal guest), revelarlo pulsando
    # "Sign in with Email".
    email_el = _pick_visible(page.locator(SEL_EMAIL))
    if email_el is None:
        email_el = _pick_visible(page.locator(SEL_EMAIL_FALLBACK))
    if email_el is None:
        for txt in SIGN_IN_WITH_EMAIL_TEXTS:
            try:
                btn = page.get_by_role("button", name=txt)
                if btn.count() > 0:
                    btn.first.click(timeout=10000)
                    delay((1.0, 2.0), "post-sign-in-with-email")
                    email_el = _pick_visible(page.locator(SEL_EMAIL)) or \
                        _pick_visible(page.locator(SEL_EMAIL_FALLBACK))
                    break
            except Exception:  # noqa: BLE001
                continue

    if email_el is None:
        if _is_challenge_url(page.url) or _detect_recaptcha(page):
            if _manual_challenge_resolution(page, context, config):
                return True
            raise BlockedException(
                f"Challenge real durante login en {page.url}"
            )
        _save_login_diagnostic(page)
        raise LoginFailedError(
            "El campo de email existe pero no es visible (modal sin abrir o"
            " layout cambiado)."
        )

    # Rellenar email en la copia visible.
    email_el.fill(email)
    delay((0.4, 1.0), "post-email")

    # Rellenar password en la copia visible.
    pw_el = _pick_visible(page.locator(SEL_PASSWORD)) or \
        _pick_visible(page.locator(SEL_PASSWORD_FALLBACK))
    if pw_el is None:
        _save_login_diagnostic(page)
        raise LoginFailedError("No se encontro el campo de password visible.")
    pw_el.fill(password)
    delay((0.4, 1.0), "post-password")
    mouse_jitter(page, n=1)

    # Submit: la app actual no tiene <form> ni button[type=submit].
    # Se prefiere Enter en el campo password (dispara el submit nativo).
    try:
        pw_el.press("Enter")
        log.info("Login enviado via Enter en password. Esperando resultado...")
    except Exception as e:  # noqa: BLE001
        log.debug("Enter fallo (%s); probando boton por texto.", e)
        # Fallback: boton visible con texto exacto (es o en), excluye SSO.
        submitted = False
        for texts in [SUBMIT_TEXTS_ES, SUBMIT_TEXTS_EN]:
            for txt in texts:
                try:
                    btn = page.get_by_role("button", name=txt, exact=True)
                    if btn.count() > 0:
                        btn.first.click(timeout=10000)
                        submitted = True
                        log.info("Login enviado (boton: '%s').", txt)
                        break
                except Exception:  # noqa: BLE001
                    continue
            if submitted:
                break
        if not submitted:
            _save_login_diagnostic(page)
            raise LoginFailedError("No se pudo enviar el formulario de login.")

    # Esperar resultado: feed, 2FA, o challenge
    deadline = time.time() + 60
    while time.time() < deadline:
        delay((1.5, 3.0), "esperando-resultado-login")
        url = page.url
        if _is_challenge_url(url) or _detect_recaptcha(page):
            # /checkpoint/ puede ser 2FA legitimo o un CAPTCHA. Diferenciar:
            # si la URL contiene "challenge" pero no "captcha"/"authwall", puede
            # ser 2FA -> intentar manejarlo abajo. Si es captcha real, bloquear.
            if "captcha" in url.lower() or _detect_recaptcha(page):
                if _manual_challenge_resolution(page, context, config):
                    return True
                raise BlockedException(f"CAPTCHA tras login en {url}")
            # No es CAPTCHA explicito, continuar para detectar 2FA
        if _is_logged_in(page):
            log.info("Login OK (sesion iniciada).")
            save_storage_state(context)
            return True
        # 2FA: buscar campo de codigo OTP (en /checkpoint/...)
        otp_filled = _handle_2fa_if_present(page, context, config)
        if otp_filled:
            return True

    raise LoginFailedError(
        "Login no completo en 60s. Revisa credenciales / 2FA en .env o consola."
    )


def _handle_2fa_if_present(page, context, config: dict[str, Any]) -> bool:
    """Detecta y maneja un formulario de 2FA/OTP. Devuelve True si login OK.

    Busca inputs de codigo OTP por atributos estables (autocomplete, inputmode,
    type). Si lo encuentra, pide el codigo por consola y lo envia.
    """
    otp_selectors = [
        'input[autocomplete="one-time-code"]:visible',
        'input[inputmode="numeric"]:visible',
        'input[name="pin"]:visible',
        'input[type="tel"]:visible',
    ]
    otp_input = None
    for sel in otp_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            otp_input = loc.first
            break
    if not otp_input:
        return False

    log.warning("=== 2FA / VERIFICACION DETECTADA ===")
    log.warning("Introduce el codigo OTP enviado a tu dispositivo.")
    try:
        code = input("Codigo 2FA (o 'skip' para abortar): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise LoginFailedError("2FA abortado (no hay terminal interactiva).")
    if code.lower() in {"skip", "", "abort"}:
        raise LoginFailedError("2FA abortado por el usuario.")

    otp_input.fill(code)
    delay((0.5, 1.2), "post-otp")

    # Buscar boton de verificar/continue (texto es o en, sin type="submit")
    verify_texts = ["Verificar", "Verify", "Continuar", "Continue", "Enviar", "Submit"]
    clicked = False
    for txt in verify_texts:
        try:
            btn = page.get_by_role("button", name=txt, exact=True)
            if btn.count() > 0:
                btn.first.click(timeout=10000)
                clicked = True
                log.info("Boton 2FA clickado: '%s'.", txt)
                break
        except Exception:  # noqa: BLE001
            continue
    if not clicked:
        # Fallback: Enter en el input
        try:
            otp_input.press("Enter")
            clicked = True
            log.info("2FA enviado via Enter.")
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar 2FA: %s", e)

    delay((3.0, 5.0), "post-otp-submit")
    if _is_logged_in(page):
        log.info("2FA resuelto. Sesion iniciada.")
        save_storage_state(context)
        return True
    if _is_challenge_url(page.url) and "captcha" in page.url.lower():
        if _manual_challenge_resolution(page, context, config):
            return True
        raise BlockedException(f"CAPTCHA tras 2FA en {page.url}")
    return False
