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
from pathlib import Path
from typing import TYPE_CHECKING

from .config import SESSION_STATE_FILE, CountryConfig

if TYPE_CHECKING:
    from patchright.sync_api import Browser, BrowserContext, Page, Playwright

log = logging.getLogger(__name__)

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
            try:
                self._browser = self._pw.chromium.connect_over_cdp(
                    f"http://localhost:{self.cdp_port}"
                )
            except Exception as e:
                raise RuntimeError(
                    f"No se pudo conectar a Chrome en localhost:{self.cdp_port}. "
                    f"Lanza Chrome primero con:\n"
                    f'  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                    f'--remote-debugging-port={self.cdp_port} '
                    f'--user-data-dir="{self.output_dir / "chrome_profile"}"\n'
                    f"Error: {e}"
                ) from e
            # usar el contexto existente del navegador conectado
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                log.info("conectado al contexto existente de Chrome (%d pestañas abiertas)", len(self._context.pages))
            else:
                self._context = self._browser.new_context()
            return

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
        if self.cdp_port > 0:
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
