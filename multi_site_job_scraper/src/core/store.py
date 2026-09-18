"""Persistencia: CSV + Parquet + checkpoint + dedup.

- CSV: inspeccion rapida (skills como string separado por '|').
- Parquet: esquema tipado para Databricks Lakehouse.
- Checkpoint: vuelca tras cada busqueda y cada N detalles.
- Dedup: por job_id; la primera aparicion se conserva.
- Coherencia: antes de escribir se validan las filas (job_id no vacio,
  company_name sin ratings/URLs, job_url con http). Las filas incoherentes
  NO se escriben: van a cuarentena en data/quality/ y se loguean.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .models import CSV_COLUMN_ORDER, JobOffer, PARQUET_SCHEMA_FIELDS
from .normalize import as_text

log = logging.getLogger(__name__)

# Un rating tipo "4,1", "4.1", "3,0" o "4.00" nunca es un nombre de empresa.
# \d{1,2}$ tambien captura "4.0" y "4,0" (caso real en produccion: Glassdoor).
_RATING_OR_URL = re.compile(r"^\d[,.]\d{1,2}$|^https?://")


def _row_is_coherent(row) -> bool:
    """False si la fila rompe la coherencia esperada del esquema."""
    job_id = str(row.get("job_id", "") or "").strip()
    if not job_id:
        return False
    company = str(row.get("company_name", "") or "").strip()
    if company and _RATING_OR_URL.match(company):
        return False
    url = str(row.get("job_url", "") or "").strip()
    if url and not re.match(r"^https?://", url):
        return False
    return True


class Store:
    """Acumulador en memoria de JobOffer con volcado a CSV/Parquet."""

    def __init__(self, csv_path: str | Path, parquet_path: str | Path,
                 checkpoint_dir: str | Path):
        self.csv_path = Path(csv_path)
        self.parquet_path = Path(parquet_path)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._by_id: dict[str, JobOffer] = {}
        self._processed_ids: set[str] = set()
        self._load_state()

    def _load_state(self) -> None:
        """Carga job_ids del state.json para reanudar runs previos (best-effort)."""
        state_p = self.checkpoint_dir / "state.json"
        if not state_p.exists():
            return
        try:
            state = json.loads(state_p.read_text(encoding="utf-8"))
            ids = state.get("job_ids") or []
            if isinstance(ids, list):
                self._processed_ids = {str(jid) for jid in ids if jid}
                log.info("Reanudacion: %d job_ids cargados de %s",
                         len(self._processed_ids), state_p)
        except (OSError, ValueError, KeyError) as e:
            log.warning("state.json ilegible (%s); arranque fresco.", e)

    def already_processed(self, job_id: str) -> bool:
        return job_id in self._processed_ids

    def load_from_csv(self, path: Path | None = None) -> int:
        """Carga un CSV previo para idempotencia (--append)."""
        p = path or self.csv_path
        if not p.exists():
            return 0
        try:
            df = pd.read_csv(p, dtype=str, keep_default_na=False)
        except Exception as e:
            log.warning("CSV ilegible (%s); run fresco.", e)
            return 0
        n = 0
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            skills_str = row_dict.get("skills", "") or ""
            row_dict["skills"] = [s for s in str(skills_str).split("|") if s] if skills_str else []
            for col in ("num_applicants", "salary_min", "salary_max"):
                val = row_dict.get(col)
                if val in (None, "", "nan", "None"):
                    row_dict[col] = None
                else:
                    try:
                        row_dict[col] = float(val) if col != "num_applicants" else int(float(val))
                    except (ValueError, TypeError):
                        row_dict[col] = None
            is_new_val = row_dict.get("is_new", "")
            if isinstance(is_new_val, str):
                row_dict["is_new"] = is_new_val.lower() in ("true", "1", "yes")
            salary_disc = row_dict.get("salary_disclosed", "")
            if isinstance(salary_disc, str):
                row_dict["salary_disclosed"] = salary_disc.lower() not in ("false", "0", "no", "")
            salary_est = row_dict.get("salary_is_estimated", "")
            if isinstance(salary_est, str):
                row_dict["salary_is_estimated"] = salary_est.lower() not in ("false", "0", "no", "")
            try:
                valid = {k: v for k, v in row_dict.items() if k in JobOffer.__dataclass_fields__}
                offer = JobOffer(**valid)
            except TypeError:
                continue
            self._by_id[offer.job_id] = offer
            self._processed_ids.add(offer.job_id)
            n += 1
        log.info("--append: %d ofertas cargadas de %s (total: %d)",
                 n, p, len(self._by_id))
        return n

    def add(self, offer: JobOffer) -> bool:
        """Anade una oferta. Devuelve True si nueva, False si duplicada."""
        if offer.job_id in self._by_id:
            existing = self._by_id[offer.job_id]
            if not existing.description_full and offer.description_full:
                keep_role = existing.search_role
                keep_city = existing.search_city
                keep_source = existing.source
                keep_scraped = existing.scraped_at
                self._by_id[offer.job_id] = offer
                self._by_id[offer.job_id].search_role = keep_role
                self._by_id[offer.job_id].search_city = keep_city
                self._by_id[offer.job_id].source = keep_source
                self._by_id[offer.job_id].scraped_at = keep_scraped
            return False
        self._by_id[offer.job_id] = offer
        return True

    def add_many(self, offers: list[JobOffer]) -> int:
        return sum(1 for o in offers if self.add(o))

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> list[JobOffer]:
        return list(self._by_id.values())

    def _to_dataframe(self) -> pd.DataFrame:
        rows = [o.to_flat_dict() for o in self._by_id.values()]
        df = pd.DataFrame(rows, columns=CSV_COLUMN_ORDER)
        # Aplana valores no escalares (list/dict) de columnas de texto: evita
        # que un employment_type en lista se serialice como "['FULL_TIME', ...]".
        for col, typ in PARQUET_SCHEMA_FIELDS.items():
            if typ == "string" and col in df.columns:
                df[col] = df[col].map(lambda v: as_text(v))
        df["is_new"] = df["is_new"].astype(bool)
        df["salary_disclosed"] = df["salary_disclosed"].astype(bool)
        for col in ["salary_min", "salary_max"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["num_applicants"] = pd.to_numeric(df["num_applicants"], errors="coerce").astype("Int64")
        return df

    def _quarantine_incoherent(self, df: pd.DataFrame) -> pd.DataFrame:
        """Descarta filas incoherentes (rating/URL como empresa, job_id vacio,
        URL rota) y las guarda en data/quality/ para auditoria."""
        if df is None or len(df) == 0:
            return df
        mask = df.apply(_row_is_coherent, axis=1)
        if mask.all():
            return df
        bad = df[~mask]
        good = df[mask]
        qdir = self.csv_path.parent.parent / "quality"
        qdir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        qpath = qdir / f"bad_rows_{ts}.csv"
        bad.to_csv(qpath, index=False, encoding="utf-8-sig")
        log.warning("Coherencia: %d fila(s) incoherente(s) -> cuarentena en %s",
                    len(bad), qpath)
        return good

    def write_csv(self, path: Path | None = None) -> Path:
        protect = path is None
        p = path or self.csv_path
        df = self._quarantine_incoherent(self._to_dataframe())
        if protect and len(df) == 0 and p.exists() and p.stat().st_size > 0:
            # No destruir un output previo bueno con un run vacio (ej: fallo
            # al lanzar el navegador). Los checkpoints no se protegen.
            log.warning(
                "CSV: run sin datos; se conserva el output previo %s", p)
            return p
        df.to_csv(p, index=False, encoding="utf-8-sig")
        log.info("CSV escrito: %s (%d filas)", p, len(df))
        return p

    def write_parquet(self, path: Path | None = None) -> Path:
        protect = path is None
        p = path or self.parquet_path
        rows = []
        for o in self._by_id.values():
            d = dataclasses.asdict(o)
            d["skills"] = list(o.skills) if o.skills else []
            rows.append(d)
        df = pd.DataFrame(rows, columns=CSV_COLUMN_ORDER)
        df = self._quarantine_incoherent(df)
        df["is_new"] = df["is_new"].astype(bool)
        df["salary_disclosed"] = df["salary_disclosed"].astype(bool)
        for col in ["salary_min", "salary_max"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["num_applicants"] = pd.to_numeric(df["num_applicants"], errors="coerce")
        if protect and len(df) == 0 and p.exists() and p.stat().st_size > 0:
            log.warning(
                "Parquet: run sin datos; se conserva el output previo %s", p)
            return p
        df = self._coerce_schema_types(df)
        table = pa.Table.from_pandas(df, preserve_index=False)
        table = table.cast(self._arrow_schema())
        pq.write_table(table, p, compression="snappy")
        log.info("Parquet escrito: %s (%d filas, esquema tipado)", p, len(df))
        return p

    def _coerce_schema_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fuerza cada columna al tipo declarado antes del cast de Arrow.

        Salvaguarda: un valor no-escalar (list/dict) en una columna `string`
        (p.ej. employment_type = ['FULL_TIME', 'REMOTE_WORKING']) rompia el
        cast con "Expected bytes, got a 'list' object". Aqui se aplanan."""
        for col, typ in PARQUET_SCHEMA_FIELDS.items():
            if col not in df.columns:
                continue
            if typ == "string":
                df[col] = df[col].map(lambda v: as_text(v))
            elif typ == "list<string>":
                df[col] = df[col].map(
                    lambda v: list(v) if isinstance(v, (list, tuple, set))
                    else ([v] if v not in (None, "") else []))
        return df

    def _arrow_schema(self) -> pa.Schema:
        type_map = {
            "string": pa.string(),
            "bool": pa.bool_(),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "list<string>": pa.list_(pa.string()),
        }
        fields = [pa.field(name, type_map[t]) for name, t in PARQUET_SCHEMA_FIELDS.items()]
        return pa.schema(fields)

    def write_all(self) -> tuple[Path, Path]:
        return self.write_csv(), self.write_parquet()

    def load_from_latest_checkpoint(self) -> int:
        """Carga desde el checkpoint CSV mas reciente (no el output)."""
        cp_files = sorted(self.checkpoint_dir.glob("progress_*.csv"))
        if not cp_files:
            log.info("No hay checkpoints para cargar.")
            return 0
        latest = cp_files[-1]
        log.info("Cargando desde checkpoint: %s", latest.name)
        return self.load_from_csv(latest)

    def rebuild_state(self) -> None:
        """Reconstruye state.json desde los datos en memoria."""
        import datetime as dt
        state_p = self.checkpoint_dir / "state.json"
        state = {
            "total": len(self._by_id),
            "job_ids": list(self._by_id.keys()),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self._processed_ids.update(self._by_id.keys())

    def checkpoint(self, tag: str = "") -> Path:
        """Vuelca incremental CSV + state.json para reanudar."""
        import datetime as dt

        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
        cp = self.checkpoint_dir / f"progress_{ts}_{safe_tag}.csv"
        self.write_csv(cp)
        state_p = self.checkpoint_dir / "state.json"
        state = {
            "total": len(self._by_id),
            "job_ids": list(self._by_id.keys()),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self._processed_ids.update(self._by_id.keys())
        log.info("Checkpoint: %s (%d ofertas)", cp, len(self._by_id))
        return cp
