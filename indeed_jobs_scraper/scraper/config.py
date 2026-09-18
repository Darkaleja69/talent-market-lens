"""Configuracion central del scraper de Indeed.

Define paises soportados (dominio, locale, timezone, moneda, ciudades),
terminos de busqueda por defecto, y constantes anti-bot (delays, scrolling).

El prototipo ejecuta solo Espana (ES) con Madrid, Barcelona y Bilbao,
pero IE/CH/NL estan preparados para activarse con --country.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CountryConfig:
    code: str
    domain: str
    locale: str
    timezone: str
    currency: str
    cities: tuple[str, ...]

    def url(self, term: str, city: str, start: int = 0, radius: int = 25, limit: int = 0, fromage: int = 0, remote: bool = False, job_type: str = "") -> str:
        """Construye la URL de la SERP de Indeed para un termino/ciudad/pagina.

        Usa el patron confirmado en la investigacion:
        https://<domain>/jobs?q=<term>&l=<city>&radius=<r>&start=<N>

        start=0   -> pagina 1
        start=10  -> pagina 2
        start=20  -> pagina 3 ...

        limit>0 anade &limit=<N> para pedir mas resultados en una sola pagina
        (util porque Indeed exige login para paginar mas alla de la pagina 1;
        con limit=50 se obtienen hasta 50 resultados sin login).

        fromage>0 filtra por antiguedad en dias (ej: --fromage 7).
        remote=True filtra solo ofertas remotas (param sc=0kf...).
        job_type filtra por tipo (fulltime, parttime, contract, etc.).
        """
        params = f"q={term}&l={city}&radius={radius}"
        if start > 0:
            params += f"&start={start}"
        if limit and limit > 0:
            params += f"&limit={limit}"
        if fromage > 0:
            params += f"&fromage={fromage}"
        if remote:
            params += "&sc=0kf%3Aattr%28DSQF7%29%3B"
        if job_type:
            params += f"&jt={job_type}"
        return f"https://{self.domain}/jobs?{params}"


# Paises soportados. El prototipo ejecuta solo ES; el resto queda preparado.
COUNTRIES: dict[str, CountryConfig] = {
    "ES": CountryConfig(
        code="ES",
        domain="es.indeed.com",
        locale="es-ES",
        timezone="Europe/Madrid",
        currency="EUR",
        cities=("Madrid", "Barcelona", "Bilbao"),
    ),
    "IE": CountryConfig(
        code="IE",
        domain="ie.indeed.com",
        locale="en-IE",
        timezone="Europe/Dublin",
        currency="EUR",
        cities=("Dublin", "Cork"),
    ),
    "CH": CountryConfig(
        code="CH",
        domain="ch.indeed.com",
        locale="de-CH",
        timezone="Europe/Zurich",
        currency="CHF",
        cities=("Zurich", "Geneva"),
    ),
    "NL": CountryConfig(
        code="NL",
        domain="nl.indeed.com",
        locale="nl-NL",
        timezone="Europe/Amsterdam",
        currency="EUR",
        cities=("Amsterdam", "Rotterdam"),
    ),
}

DEFAULT_TERM = "data"
DEFAULT_COUNTRY = "ES"
DEFAULT_PAGES = 2
RESULTS_PER_PAGE = 10  # Indeed sirve 10 job cards por pagina (start=0,10,20...)

# --- Constantes anti-bot (sin proxy) ---------------------------------------
# Delays amplios y aleatorios para parecer un humano tranquilo.
# Perfil "lento y estable": la prioridad es NO disparar challenges (y poder
# dejar la run corriendo 8h), no la velocidad.
# Rangos en segundos; el modulo anti_bot escoge un valor uniforme dentro de cada rango.
DELAY_PRE_NAV = (10.0, 20.0)         # antes de navegar a una URL
DELAY_BETWEEN_PAGES = (20.0, 35.0)   # entre pagina 1 y 2 de la misma ciudad
DELAY_BETWEEN_CITIES = (120.0, 180.0)  # entre ciudades (alto para no encender heuristicas)
DELAY_BETWEEN_TERMS = (120.0, 180.0)   # entre terminos de busqueda
DELAY_BETWEEN_COUNTRIES = (240.0, 360.0)

# Scrolling gradual humano (lo que pidio el usuario: "muy poco a poco").
SCROLL_STEP_PX = (200, 400)        # pixels por paso (rango)
SCROLL_STEP_DELAY = (1.0, 2.0)     # segundos entre pasos
SCROLL_PAUSE_BIG_EVERY = (5, 9)    # cada N pasos, pausa larga
SCROLL_PAUSE_BIG_DELAY = (3.0, 5.0)
SCROLL_SETTLE_AFTER = 2.0          # espera tras llegar al fondo antes de parsear

# Sesiones/cookies persistentes para parecer usuario recurrente.
SESSION_STATE_FILE = "session_state.json"

# Cache persistente de descripciones/condiciones por job_key (evita re-clicar
# las mismas ofertas cada noche; ver scraper/desc_cache.py).
DESC_CACHE_FILE = "descriptions_cache.json"

# Tope de clics de enriquecimiento por run (0 = sin tope). Default prudente:
# menos clics = menos riesgo de challenge anti-bot.
DEFAULT_ENRICH_MAX = 40

# Deteccion de CAPTCHA / challenges anti-bot.
CAPTCHA_URL_MARKERS = (
    "/captcha",
    "chkjsproc",
    "/cdn-cgi/challenge-platform/",
    "challenges.cloudflare.com",
    "px-captcha",
    "dd-captcha",
    "funcaptcha",
    "arkoselabs",
)
CAPTCHA_TITLE_MARKERS = (
    "just a moment",
    "verificación",
    "verificacion",
    "are you a human",
    "captcha",
    "comprobación",
    "comprobacion",
)

# Backoff si salta challenge: 1 reintento con nueva sesion tras este delay.
BACKOFF_RETRY_DELAY = 120.0
BACKOFF_MAX_RETRIES = 1

# Volumen autoimpuesto para no disparar heuristicas (sin proxy).
MAX_SERPS_PER_RUN = 50  # Hasta 5 terms x 3 cities x 3 pages (sin proxy, sesion reusada)
