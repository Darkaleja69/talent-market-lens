"""Acceso a la API JSON de Nationale Vacaturebank.

La API esta protegida por Akamai Bot Manager: una peticion HTTP directa
(requests/urllib) recibe 403 sin las cookies `_abck`/`bm_sv`. El navegador
(patchright) las obtiene al cargar la web y, al NAVEGAR directamente a la URL
de la API, el cuerpo del documento es el JSON (fetch() falla por CORS).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

log = logging.getLogger(__name__)

API_URL = ("https://api.nationalevacaturebank.nl/api/jobs/v3/"
           "sites/nationalevacaturebank.nl/jobs")


def build_url(query: str, page: int = 1, limit: int = 100,
              sort: str = "date", api_url: str = API_URL) -> str:
    params = {"query": query, "page": page, "limit": limit, "sort": sort}
    return f"{api_url}?{urlencode(params)}"


class NvbBrowserApi:
    """Lee la API navegando con un `page` de Playwright ya inicializado."""

    def __init__(self, page, api_url: str = API_URL):
        self.page = page
        self.api_url = api_url

    def fetch_jobs(self, query: str, page: int = 1, limit: int = 100,
                   sort: str = "date", timeout: int = 60000) -> dict[str, Any]:
        url = build_url(query, page, limit, sort, self.api_url)
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        status = resp.status if resp else None
        body = ""
        try:
            body = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            body = ""
        if status != 200 or not body.lstrip().startswith("{"):
            log.warning("Respuesta no-JSON (status=%s) en '%s' p%d; posible "
                        "bloqueo Akamai.", status, query, page)
            return {}
        try:
            data = json.loads(body)
        except ValueError as e:
            log.warning("JSON invalido en '%s' p%d: %s", query, page, e)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def jobs_of(data: dict) -> list[dict]:
        return (data.get("_embedded") or {}).get("jobs") or []

    @staticmethod
    def total_of(data: dict) -> int:
        try:
            return int(data.get("total") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def pages_of(data: dict) -> int:
        try:
            return int(data.get("pages") or 0)
        except (TypeError, ValueError):
            return 0
