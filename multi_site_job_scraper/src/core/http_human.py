"""Comportamiento "humano" para scrapers HTTP (requests): delays aleatorios,
rotacion de User-Agent y manejo de sesion respetuoso.

Para scrapers que NO necesitan navegador (IrishJobs, StepStone).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
]


def random_ua() -> str:
    return random.choice(USER_AGENTS)


def delay(rng: tuple[float, float], label: str = "") -> None:
    t = random.uniform(*rng)
    if label:
        log.debug("delay %s: %.2fs", label, t)
    time.sleep(t)


def create_session(
    retries: int = 3,
    backoff_factor: float = 2.0,
    timeout: int = 30,
) -> requests.Session:
    """Crea una requests.Session con reintentos, backoff y headers realistas."""
    s = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    s.timeout = timeout
    return s


def rotate_session(session: requests.Session) -> None:
    """Rota el User-Agent de la sesion."""
    session.headers["User-Agent"] = random_ua()


class HumanHttpClient:
    """Cliente HTTP con delays humanos entre requests."""

    def __init__(self, delay_rng: tuple[float, float] = (1.5, 4.0),
                 timeout: int = 30):
        self.session = create_session(timeout=timeout)
        self.delay_rng = delay_rng
        self._last_request = 0.0

    def _respect_delay(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.delay_rng[0]:
            wait = random.uniform(*self.delay_rng)
            time.sleep(wait)
        self._last_request = time.time()

    def get(self, url: str, **kwargs) -> requests.Response:
        self._respect_delay()
        rotate_session(self.session)
        log.debug("HTTP GET %s", url[:100])
        resp = self.session.get(url, **kwargs)
        resp.raise_for_status()
        return resp
