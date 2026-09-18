"""Cliente HTTP para la API JSON de DevITJobs.nl.

No usa navegador: los endpoints /api/jobsLight y /api/job/<id> responden
JSON a una peticion HTTP normal (se comprobo que no hay challenge).
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.http_human import create_session, random_ua

log = logging.getLogger(__name__)

BASE_URL = "https://devitjobs.nl"
JOBS_PATH = "/api/jobsLight"
JOB_DETAIL_PATH = "/api/job/{job_id}"


class DevITJobsClient:
    def __init__(self, retry_cfg: dict | None = None, timeout: int = 30):
        retry_cfg = retry_cfg or {}
        self.timeout = timeout
        retries = int(retry_cfg.get("max_retries", 3))
        self.session = create_session(retries=retries, backoff_factor=2.0,
                                      timeout=timeout)
        self.session.headers.update({
            "User-Agent": random_ua(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
            # Sin brotli instalado, "br" devolveria bytes sin descomprimir.
            "Accept-Encoding": "gzip, deflate",
        })

    def get_json(self, path: str) -> Any:
        url = BASE_URL + path
        log.debug("GET %s", url)
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_jobs(self) -> list[dict]:
        """Lista completa de ofertas (resumen)."""
        data = self.get_json(JOBS_PATH)
        if isinstance(data, dict):
            data = data.get("jobs") or data.get("data") or []
        return data if isinstance(data, list) else []

    def fetch_job_detail(self, job_id: str) -> dict:
        """Detalle completo de una oferta (descripcion, requisitos, ...)."""
        data = self.get_json(JOB_DETAIL_PATH.format(job_id=job_id))
        return data if isinstance(data, dict) else {}
