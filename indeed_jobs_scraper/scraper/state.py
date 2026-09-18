"""Gestión de estado y checkpoints para runs nocturnas.

Permite reanudar entre reintentos del wrapper (intra-noche) trackeando
job_keys ya procesados via state.json. NO acumula cross-noche (runs aislados).

Patrón replicado de linkedin_jobs_scraper (data/checkpoints/state.json).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATE_FILE = "state.json"
CHECKPOINTS_DIR = "checkpoints"


class RunState:
    """Estado ligero del run nocturno: job_keys ya scrapeados/exportados."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = output_dir / STATE_FILE
        self.checkpoints_dir = output_dir / CHECKPOINTS_DIR
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._processed_jks: set[str] = set()
        self._total_exported: int = 0
        self._load()

    def _load(self) -> None:
        """Carga state.json si existe. Si corrupto, arranca limpio."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                jks = data.get("job_keys", [])
                self._processed_jks = set(jks)
                self._total_exported = data.get("total_exported", 0)
                log.info(
                    "state cargado: %d job_keys procesados, %d exportados",
                    len(self._processed_jks),
                    self._total_exported,
                )
            except Exception as e:
                log.warning("state.json corrupto (%s). Arrancando limpio.", e)
                self._processed_jks = set()
                self._total_exported = 0

    def already_processed(self, job_key: str) -> bool:
        """True si este job_key ya fue scrapeado o enriquecido en este run."""
        return job_key in self._processed_jks

    def mark_processed(self, job_keys: list[str]) -> None:
        """Registra job_keys como procesados (en memoria). No persiste a disco aun."""
        self._processed_jks.update(job_keys)

    def checkpoint(self, tag: str, offers: list[Any]) -> Path | None:
        """Vuelca checkpoint CSV con los offers actuales + actualiza state.json.

        Llamar tras cada combinacion (term, ciudad) completada.
        """
        if not offers:
            return None

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"progress_{ts}_{tag}.csv"
        path = self.checkpoints_dir / filename

        try:
            import pandas as pd

            rows = [o.to_dict() if hasattr(o, "to_dict") else o for o in offers]
            df = pd.DataFrame(rows)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            log.info("checkpoint: %s (%d filas)", filename, len(df))
        except Exception as e:
            log.warning("checkpoint CSV fallo: %s", e)
            return None

        self._total_exported = len(self._processed_jks)
        self._save_state()
        return path

    def _save_state(self) -> None:
        """Persiste state.json a disco."""
        data = {
            "job_keys": sorted(self._processed_jks),
            "total_exported": self._total_exported,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save(self) -> None:
        """Guarda state.json explícitamente (llamar al final del run)."""
        self._total_exported = len(self._processed_jks)
        self._save_state()
        log.info("state guardado: %d job_keys", len(self._processed_jks))
