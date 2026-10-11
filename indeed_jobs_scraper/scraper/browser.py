"""Gestion del navegador: patchright (Playwright con stealth integrado) headed.

Por que patchright y no playwright + playwright-stealth:
  - patchright es un fork que parchea navigator.webdriver, runtime, CDP detection
    de forma integrada y mantenida. playwright-stealth lleva tiempo estancado.
  - Mismo API que Playwright: `from patchright.sync_api import sync_playwright`.

Estrategia anti-bot:
  - Headed (no headless): headless es una bandera roja para Cloudflare.
  - viewport 1920x1080, locale y timezone del pais, User-Agent real de Chrome.
  - storage_state persistente: guardamos cookies entre ejecuciones para parecer
    un usuario recurrente (Indeed baja la friccion si ve sesion conocida).
  - Argumentos de Chromium para reducir fingerprints sin romper compatibilidad.
"""
from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .config import SESSION_STATE_FILE, CountryConfig

if TYPE_CHECKING:
    from patchright.sync_api import Browser, BrowserContext, Page, Playwright

log = logging.getLogger(__name__)

# --- Handshake CDP (T-32) ---------------------------------------------------
# El pre-flight del wrapper solo comprueba GET /json: prueba que el endpoint
# HTTP de DevTools responde, no que un cliente real complete el handshake. El
# run del 2026-10-10 murio en connect_over_cdp (patchright 1.60.1 contra Chrome
# 154: "Network.setCacheDisabled ... session closed" / timeout 180s) con /json
# respondiendo y agent-browser operando contra ese mismo Chrome. Por eso aqui
# la conexion se verifica con una operacion real minima y se reintenta con
# backoff corto; si no se logra, el run degrada al navegador propio headed.
CDP_ATTEMPTS = 3
CDP_CONNECT_TIMEOUT_MS = 20000
CDP_RETRY_BACKOFF_S = (2.0, 5.0)


def probe_cdp_handshake(browser: "Browser") -> tuple[bool, str]:
    """Verifica una conexion CDP con una operacion real minima (T-32).

    Abrir (y cerrar) una pagina en el navegador conectado es la operacion que
    fallaba con el handshake roto ("session closed"); leer solo
    ``browser.contexts`` no lo detecta. No navega a ningun sitio, no toca
    pestanas existentes y nunca lanza excepciones: devuelve ``(ok, detalle)``.
    """
    try:
        contexts = list(browser.contexts)
    except Exception as e:
        return False, f"no se pudieron listar los contextos: {e}"

    context = None
    created_context = False
    try:
        if contexts:
            context = contexts[0]
        else:
            context = browser.new_context()
            created_context = True
        page = context.new_page()
        try:
            page.close()
        except Exception:
            pass
        return True, f"{len(contexts)} contexto(s); pagina de prueba abierta y cerrada"
    except Exception as e:
        return False, f"la operacion minima fallo: {e}"
    finally:
        if created_context and context is not None:
            try:
                context.close()
            except Exception:
                pass


def open_cdp_browser(
    pw: "Playwright",
    port: int,
    *,
    attempts: int = CDP_ATTEMPTS,
    timeout_ms: int = CDP_CONNECT_TIMEOUT_MS,
    backoff_s: tuple[float, float] = CDP_RETRY_BACKOFF_S,
    sleep_fn=time.sleep,
    log_fn=None,
) -> "Browser | None":
    """Conecta al endpoint CDP y verifica el handshake, con reintentos (T-32).

    Devuelve el ``Browser`` conectado o ``None`` cuando todos los intentos
    fallan. Un navegador que conecta pero no supera la operacion minima se
    desconecta antes del siguiente intento; el Chrome vivo del usuario no se
    toca mas alla de las paginas de prueba (que se cierran). ``sleep_fn`` se
    inyecta en los tests para no esperar de verdad.
    """
    logger = log_fn if log_fn is not None else log
    endpoint = f"http://localhost:{port}"

    for attempt in range(1, attempts + 1):
        browser = None
        try:
            browser = pw.chromium.connect_over_cdp(endpoint, timeout=timeout_ms)
        except Exception as e:
            logger.warning(
                "CDP intento %d/%d: connect_over_cdp fallo en %s: %s",
                attempt, attempts, endpoint, e,
            )

        if browser is not None:
            ok, detail = probe_cdp_handshake(browser)
            if ok:
                logger.info(
                    "CDP intento %d/%d: handshake verificado (%s)",
                    attempt, attempts, detail,
                )
                return browser
            logger.warning(
                "CDP intento %d/%d: handshake no verificado (%s)",
                attempt, attempts, detail,
            )
            try:
                browser.close()
            except Exception:
                pass

        if attempt < attempts:
            delay = random.uniform(*backoff_s) if backoff_s else 0.0
            if delay > 0:
                logger.info("CDP: fallo el intento %d/%d; reintentando en %.1fs", attempt, attempts, delay)
                sleep_fn(delay)

    return None

# User-Agents reales de Chrome estable (Windows). Rotamos entre varios.
# Mantenemos una lista corta y estable para no llamar la atencion cambiando UA
# constantemente; se elige uno por sesion y se mantiene toda la ejecucion.
CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
]


def _get_chrome_ua() -> str:
    try:
        from fake_useragent import UserAgent
        ua = UserAgent(browsers=['chrome'], os=['windows'])
        return ua.random
    except Exception:
        return random.choice(CHROME_UAS)


class BrowserSession:
    """Contexto de navegador con sesion persistente.

    Uso:
        with BrowserSession(country, output_dir) as session:
            page = session.new_page()
            ...
    """

    def __init__(self, country: CountryConfig, output_dir: Path, use_chrome_profile: bool = False, cdp_port: int = 0):
        self.country = country
        self.output_dir = output_dir
        self.session_path = output_dir / SESSION_STATE_FILE
        self.use_chrome_profile = use_chrome_profile
        self.cdp_port = cdp_port  # >0 => conectar a Chrome existente via CDP
        # Estado real de la sesion (T-32): `cdp_port > 0` es solo la peticion;
        # `connected_via_cdp` confirma que el handshake se verifico.
        self.connected_via_cdp = False
        self.cdp_fallback = False
        self._pw: "Playwright | None" = None
        self._browser: "Browser | None" = None
        self._context: "BrowserContext | None" = None
        self._ua = _get_chrome_ua()
        m = re.search(r'Chrome/(\d+)', self._ua)
        self._chrome_ver = m.group(1) if m else "141"

    def __enter__(self) -> "BrowserSession":
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._close()

    def _start(self) -> None:
        # import diferido para que el scraper pueda importarse sin patchright instalado
        from patchright.sync_api import sync_playwright

        self._pw = sync_playwright().start()

        # --- Modo CDP: conectar a Chrome que el usuario lanzo a mano -------
        # El usuario lanza Chrome con --remote-debugging-port=9222 y se loguea
        # en Indeed normalmente (sin automatizacion, todos los botones funcionan).
        # El scraper se conecta a ese Chrome ya abierto y hace el scraping.
        # Ventaja: Chrome fue lanzado por el usuario, no por Playwright, asi que
        # no hay flags de automatizacion y Google/Indeed no bloquean los botones.
        if self.cdp_port > 0:
            log.info("conectando a Chrome existente via CDP en puerto %d...", self.cdp_port)
            browser = open_cdp_browser(self._pw, self.cdp_port)
            if browser is not None:
                self._browser = browser
                # usar el contexto existente del navegador conectado
                contexts = self._browser.contexts
                if contexts:
                    self._context = contexts[0]
                    log.info("conectado al contexto existente de Chrome (%d pestañas abiertas)", len(self._context.pages))
                else:
                    self._context = self._browser.new_context()
                self.connected_via_cdp = True
                return

            # Fallback documentado (T-32): /json responde pero el handshake CDP
            # no se verifica. No morimos en seco: navegador propio headed (sin
            # sesion CDP), suficiente para la pagina 1; el runner limita pages a 1.
            self.cdp_fallback = True
            log.warning(
                "CDP: handshake no verificado tras %d intentos en localhost:%d; "
                "continuando con navegador propio headed (sin sesion de Chrome). "
                "Para el flujo con sesion, relanza Chrome (start_chrome_cdp.ps1) y "
                "vuelve a intentarlo.",
                CDP_ATTEMPTS, self.cdp_port,
            )

        self._launch_owned()

    def _launch_owned(self) -> None:
        """Lanza un Chrome propio de patchright (headed), con o sin perfil.

        Es el modo por defecto y tambien el fallback documentado del modo CDP
        (T-32): no toca ningun Chrome vivo del usuario.
        """
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-infobars",
            "--disable-component-extensions-with-background-pages",
            "--start-maximized",
        ]

        init_script = """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = window.chrome || { runtime: {} };
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) =>
              parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
            """

        context_kwargs = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": self.country.locale,
            "timezone_id": self.country.timezone,
            "user_agent": self._ua,
            "java_script_enabled": True,
            "ignore_https_errors": False,
            "color_scheme": "light",
            "extra_http_headers": {
                "Accept-Language": self._accept_language(),
                "Sec-Ch-Ua": f'"Chromium";v="{self._chrome_ver}", "Not_A Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        }

        if self.use_chrome_profile:
            # Usar un perfil DEDICADO dentro del proyecto (Chrome rechaza el
            # remote debugging en su directorio por defecto). El usuario se
            # loguea a Google UNA vez aqui; las cookies persisten entre
            # ejecuciones y "Continuar con Google" en Indeed funciona con un
            # clic en adelante.
            user_data_dir = str(self.output_dir / "chrome_profile")
            log.info("usando perfil dedicado de Chrome: %s", user_data_dir)
            args_with_profile = launch_args
            persistent_kwargs = dict(context_kwargs)
            persistent_kwargs.pop("viewport", None)
            persistent_kwargs["viewport"] = None  # ventana real maximizada
            persistent_kwargs["channel"] = "chrome"
            persistent_kwargs["headless"] = False
            persistent_kwargs["args"] = args_with_profile
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                **persistent_kwargs,
            )
            self._context.add_init_script(init_script)
            return

        self._browser = self._pw.chromium.launch(
            headless=False,  # headed: headless dispara heuristicas de Cloudflare
            args=launch_args,
            channel="chrome",  # usa Chrome real instalado si esta disponible (mas realista)
        )

        # reusar sesion previa si existe
        if self.session_path.exists():
            log.info("reusando sesion persistente: %s", self.session_path)
            context_kwargs["storage_state"] = str(self.session_path)

        self._context = self._browser.new_context(**context_kwargs)

        # parchea navigator.webdriver y propiedades que delatan automation
        self._context.add_init_script(init_script)

    def _accept_language(self) -> str:
        # Accept-Language coherente con el locale del pais
        base = self.country.locale  # ej "es-ES"
        primary = base.split("-")[0]
        return f"{base},{primary};q=0.9,en;q=0.8"

    def new_page(self) -> "Page":
        assert self._context is not None, "sesion no iniciada"
        page = self._context.new_page()
        # timeout generoso: Indeed a veces tarda en renderizar el SPA
        page.set_default_timeout(45000)
        page.set_default_navigation_timeout(60000)
        return page

    def save_session(self) -> None:
        if self._context is None:
            return
        # con perfil real de Chrome las cookies se guardan en el propio perfil;
        # storage_state() puede fallar o no ser necesario, lo ignoramos.
        if self.use_chrome_profile:
            log.debug("perfil real de Chrome: cookies ya persisten en el perfil, no se exporta storage_state")
            return
        try:
            self._context.storage_state(path=str(self.session_path))
            log.info("sesion guardada en %s", self.session_path)
        except Exception as e:
            log.debug("no se pudo guardar storage_state: %s", e)

    def _close(self) -> None:
        # En modo CDP NO cerramos el Chrome del usuario, solo nos desconectamos.
        # Con el fallback (cdp_fallback=True) el navegador es nuestro y si se
        # cierra, porque nadie mas lo usa.
        if self.cdp_port > 0 and self.connected_via_cdp:
            try:
                if self._browser is not None:
                    # disconnect en vez de close: no mata el navegador del usuario
                    self._browser.close()
            except Exception:
                pass
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:
                pass
            log.info("desconectado de Chrome CDP (el navegador del usuario sigue abierto)")
            return
        try:
            self.save_session()
        except Exception as e:
            log.warning("no se pudo guardar la sesion: %s", e)
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
