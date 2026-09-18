import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA_DIR = ROOT / "data"
PROFILE_DIR = DATA_DIR / "profile"
LOG_DIR = DATA_DIR / "logs"
DELTA_DIR = DATA_DIR / "delta_table"

CIUDADES: list[dict[str, str]] = [
    {
        "nombre": "Madrid",
        "provincia": "Madrid",
        "ciudad_slug": "Madrid",
        "city_id": "3533",
        "slug": "madrid/madrid",
    },
    {
        "nombre": "Barcelona",
        "provincia": "Barcelona",
        "ciudad_slug": "Barcelona",
        "city_id": "7824",
        "slug": "barcelona/barcelona",
    },
    {
        "nombre": "Bilbao",
        "provincia": "Vizcaya",
        "ciudad_slug": "Bilbao",
        "city_id": "6693",
        "slug": "vizcaya/bilbao",
    },
]

KEYWORDS: list[str] = [
    "data",
    "data analyst",
    "data engineer",
    "data scientist",
    "big data",
    "datos",
]

MAX_PAGES: int = 13

SCROLL_STEP: int = 100
SCROLL_WAIT_MIN: float = 0.8
SCROLL_WAIT_MAX: float = 1.5

RATE_LIMIT_MIN: float = 5.0
RATE_LIMIT_MAX: float = 9.0

CAPTCHA_TIMEOUT: int = int(os.getenv("INFOJOBS_CAPTCHA_TIMEOUT", "300"))
CAPTCHA_MAX_ATTEMPTS: int = int(os.getenv("INFOJOBS_CAPTCHA_ATTEMPTS", "3"))

HEADLESS: bool = False

VIEWPORT: dict[str, int] = {"width": 1920, "height": 1080}
LOCALE: str = "es-ES"
TIMEZONE: str = "Europe/Madrid"

BASE_URL: str = "https://www.infojobs.net"

ROBOTS_DISALLOWED: list[str] = [
    "/ver-oferta.xhtml",
    "/visualizar_oferta.ij/",
    "/visualizar_oferta.cfm",
    "/visualizar_oferta_no_accesible.cfm",
    "/inscripcion_oferta.cfm",
    "/buscar.empleo/",
    "/react",
    "/webapp",
]
