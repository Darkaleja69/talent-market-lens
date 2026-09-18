"""Persistencia: CSV + Parquet + checkpoint + dedup.

- CSV: para inspeccion rapida (skills como string separado por '|').
- Parquet: esquema tipado para ingest directo en Databricks Lakehouse
  (skills como list<string> nativo).
- Checkpoint: tras cada busqueda y cada N detalles, vuelca a
  data/checkpoints/progress_<timestamp>.csv para reanudar si cae la sesion.
- Dedup: por job_id; una oferta encontrada en varias busquedas se guarda una
  vez conservando todos los search_role/search_city en lista concatenada.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .models import CSV_COLUMN_ORDER, JobOffer, PARQUET_SCHEMA_FIELDS

log = logging.getLogger(__name__)

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

    def __init__(self, config: dict[str, Any]):
        self.config = config
        out = config.get("output", {})
        self.csv_path = Path(out.get("csv", "data/output/jobs.csv"))
        self.parquet_path = Path(out.get("parquet", "data/output/jobs.parquet"))
        self.checkpoint_dir = Path(out.get("checkpoint_dir", "data/checkpoints"))
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # job_id -> JobOffer
        self._by_id: dict[str, JobOffer] = {}
        # job_ids ya procesados (extraidos de state.json) -> reanudacion real
        # Permite saltar el parse_detail en runs sucesivos para no repetir.
        self._processed_ids: set[str] = set()
        self._warning_count: int = 0
        self._load_state()

    def _load_state(self) -> None:
        """Carga job_ids del ultimo state.json para reanudar runs anteriores.

        Best-effort: si el JSON esta corrupto o el schema cambio, se ignora
        con un warning y se arranca un run fresco.
        """
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
            log.warning("state.json ilegible (%s); arrancando run fresco.", e)

    def already_processed(self, job_id: str) -> bool:
        """True si el job_id ya estaba en state.json de un run previo."""
        return job_id in self._processed_ids

    def get(self, job_id: str):
        """Devuelve la JobOffer almacenada (o None). Permite consultar si ya
        tiene detalle antes de volver a visitarla."""
        return self._by_id.get(job_id)

    def warning_inc(self, label: str = "") -> int:
        """Incrementa el contador de warnings best-effort (para telemetria)."""
        self._warning_count += 1
        if label:
            log.debug("warning_inc: %s", label)
        return self._warning_count

    @property
    def warning_count(self) -> int:
        return self._warning_count

    def load_from_csv(self, path: Path | None = None) -> int:
        """Carga un CSV previo en el store para idempotencia (--append).

        Reconstruye JobOffer desde cada fila. Skills se parsea de '|' a lista.
        Devuelve el numero de filas cargadas. Si el CSV no existe, retorna 0.
        """
        p = path or self.csv_path
        if not p.exists():
            log.info("--append: no existe CSV previo (%s); run fresco.", p)
            return 0
        try:
            df = pd.read_csv(p, dtype=str, keep_default_na=False)
        except (OSError, ValueError, pd.errors.ParserError) as e:
            log.warning("--append: CSV ilegible (%s); run fresco.", e)
            self.warning_inc("append_csv_corrupto")
            return 0
        # Normalizar skills (string separado por '|') de vuelta a list[str]
        n_loaded = 0
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            skills_str = row_dict.get("skills", "") or ""
            row_dict["skills"] = [s for s in str(skills_str).split("|") if s] \
                if skills_str else []
            # num_applicants y salary_min/max pueden venir como NaN o str vacio
            for numeric_col in ("num_applicants", "salary_min", "salary_max"):
                val = row_dict.get(numeric_col)
                if val in (None, "", "nan", "None"):
                    row_dict[numeric_col] = None
                else:
                    try:
                        row_dict[numeric_col] = float(val) \
                            if numeric_col != "num_applicants" else int(float(val))
                    except (ValueError, TypeError):
                        row_dict[numeric_col] = None
            is_new_val = row_dict.get("is_new", "")
            if isinstance(is_new_val, str):
                row_dict["is_new"] = is_new_val.lower() in ("true", "1", "yes")
            try:
                offer = JobOffer(**{
                    k: v for k, v in row_dict.items()
                    if k in JobOffer.__dataclass_fields__
                })
            except TypeError as e:
                log.debug("--append: fila invalida saltada (%s)", e)
                continue
            self._by_id[offer.job_id] = offer
            self._processed_ids.add(offer.job_id)
            n_loaded += 1
        log.info("--append: %d ofertas cargadas de %s (total en store: %d)",
                 n_loaded, p, len(self._by_id))
        return n_loaded

    # --- Acumulacion con dedup ---
    def add(self, offer: JobOffer) -> bool:
        """Anade una oferta. Devuelve True si era nueva, False si descartada
        como duplicada (mismo job_id ya presente).

        Si un job_id aparece en varias busquedas, se conservan los
        search_role/search_city de la PRIMERA aparicion (no se sobrescribe
        para mantener estable el origen). Si quieres acumular todos los
        origenes, modificar aqui para concatenar.
        """
        if offer.job_id in self._by_id:
            existing = self._by_id[offer.job_id]
            # Si la nueva tiene datos de detalle y la vieja no, enriquecer
            if (not existing.description_full and offer.description_full):
                # Preservar origen original pero actualizar detalle
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
            return False
        self._by_id[offer.job_id] = offer
        return True

    def add_many(self, offers: list[JobOffer]) -> int:
        n_new = 0
        for o in offers:
            if self.add(o):
                n_new += 1
        return n_new

    def __len__(self) -> int:
        return len(self._by_id)

    def all(self) -> list[JobOffer]:
        return list(self._by_id.values())

    # --- Volcado a ficheros ---
    def _to_dataframe(self) -> pd.DataFrame:
        rows = [o.to_flat_dict() for o in self._by_id.values()]
        df = pd.DataFrame(rows, columns=CSV_COLUMN_ORDER)
        # Tipos basicos para Parquet
        df["is_new"] = df["is_new"].astype(bool)
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
        qdir = self.csv_path.parent / "quality"
        qdir.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        qpath = qdir / f"bad_rows_{ts}.csv"
        bad.to_csv(qpath, index=False, encoding="utf-8-sig")
        log.warning("Coherencia: %d fila(s) incoherente(s) -> cuarentena en %s",
                    len(bad), qpath)
        return good

    def write_csv(self, path: Path | None = None) -> Path:
        p = path or self.csv_path
        df = self._quarantine_incoherent(self._to_dataframe())
        df.to_csv(p, index=False, encoding="utf-8-sig")
        log.info("CSV escrito: %s (%d filas)", p, len(df))
        return p

    def write_parquet(self, path: Path | None = None) -> Path:
        """Escribe Parquet con esquema tipado (skills como list<string>).

        Para Databricks: COPY INTO delta_table FROM '%s' FILEFORMAT=PARQUET
        o usar Autoloader con schema_location.
        """
        p = path or self.parquet_path
        # Construir lista de dicts con skills como lista nativa
        rows = []
        for o in self._by_id.values():
            d = dataclasses.asdict(o)
            d["skills"] = list(o.skills) if o.skills else []
            rows.append(d)
        df = pd.DataFrame(rows, columns=CSV_COLUMN_ORDER)
        df = self._quarantine_incoherent(df)
        # Conversión de tipos
        df["is_new"] = df["is_new"].astype(bool)
        for col in ["salary_min", "salary_max"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["num_applicants"] = pd.to_numeric(df["num_applicants"], errors="coerce")

        table = pa.Table.from_pandas(df, preserve_index=False)
        table = table.cast(self._arrow_schema())
        pq.write_table(table, p, compression="snappy")
        log.info("Parquet escrito: %s (%d filas, esquema tipado)", p, len(df))
        return p

    def _arrow_schema(self) -> pa.Schema:
        """Construye esquema PyArrow tipado desde PARQUET_SCHEMA_FIELDS."""
        type_map = {
            "string": pa.string(),
            "bool": pa.bool_(),
            "int64": pa.int64(),
            "float64": pa.float64(),
            "list<string>": pa.list_(pa.string()),
        }
        fields = []
        for name, typename in PARQUET_SCHEMA_FIELDS.items():
            fields.append(pa.field(name, type_map[typename]))
        return pa.schema(fields)

    def write_all(self) -> tuple[Path, Path]:
        return self.write_csv(), self.write_parquet()

    # --- Checkpoint ---
    def checkpoint(self, tag: str = "") -> Path:
        """Vuelca incremental a checkpoint_dir/progress_<tag>.csv.

        tag suele ser "<role>_<city>" o "<n>_detalles".
        """
        import datetime as dt
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_tag = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)
        p = self.checkpoint_dir / f"progress_{ts}_{safe_tag}.csv"
        self.write_csv(p)
        # Tambien volcado de estado (job_ids) para reanudar
        state_p = self.checkpoint_dir / "state.json"
        state = {
            "total": len(self._by_id),
            "job_ids": list(self._by_id.keys()),
            "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        }
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        # Mantener _processed_ids sincronizado para reanudacion en runs futuras
        self._processed_ids.update(self._by_id.keys())
        # Tambien actualizar output principal (jobs.csv + jobs.parquet) para
        # que los datos sobrevivan a un kill abrupto (timeout del wrapper).
        # Sin esto, si el proceso muere entre checkpoints, el CSV de salida
        # se queda en la ultima ejecucion que llego a write_all().
        self.write_all()
        log.info("Checkpoint: %s (%d ofertas, %d ids procesados)",
                 p, len(self._by_id), len(self._processed_ids))
        return p
