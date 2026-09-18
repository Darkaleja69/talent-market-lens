"""Merge de outputs de todos los scrapers en un unico CSV + Parquet.

Uso:
    python merge.py

Lee todos los data/*/output/jobs.csv, los concatena, deduplica por
job_id y escribe data/merged/jobs_unified.csv + .parquet
"""
from __future__ import annotations

import argparse
import datetime as _dt
import logging
import re
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.translate import Translator

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s merge: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("merge")

PROJECT_ROOT = Path(__file__).resolve().parent

_RATING_OR_URL = re.compile(r"^\d[,.]\d{1,2}$|^https?://")

# workload_pct en horas (p.ej. devitjobs: '40 uur', '32-40 uur') -> porcentaje.
# Jornada completa de referencia = 40 h/semana.
_WORKLOAD_RANGE = re.compile(
    r"^\s*(\d{1,2})\s*(?:[-–—]|tot)\s*(\d{1,2})\s*uur\s*$", re.I)
_WORKLOAD_SINGLE = re.compile(r"^\s*(\d{1,2})\s*uur\s*$", re.I)


def _norm_workload(v: str) -> str:
    """Convierte una carga horaria en horas a porcentaje de jornada (40 h)."""
    s = str(v).strip()
    if not s:
        return v
    m = _WORKLOAD_RANGE.match(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return f"{round(lo / 40 * 100)}-{round(hi / 40 * 100)}%"
    m = _WORKLOAD_SINGLE.match(s)
    if m:
        return f"{round(int(m.group(1)) / 40 * 100)}%"
    return v


def clean_incoherent(df: pd.DataFrame) -> pd.DataFrame:
    """Corrige filas incoherentes ANTES de mergear:
    - company_name tipo rating ('4,1') o URL -> se vacia (nunca es empresa).
    - job_url que no empieza por http -> se vacia.
    - job_id que es una ruta URL (contaminacion StepStone: '/banen--...')
      -> se extrae el id numerico final o se vacia.
    - job_id vacio -> se conserva la fila pero el dedup la gestionara.
    - workload_pct en horas ('40 uur', '32-40 uur') -> porcentaje de jornada.
    Ademas normaliza espacios sobrantes en company_name.
    """
    if df is None or len(df) == 0:
        return df
    out = df.copy()
    if "company_name" in out.columns:
        out["company_name"] = out["company_name"].astype(str).map(
            lambda v: re.sub(r"\s+", " ", v.strip())
            if v.strip() and not _RATING_OR_URL.match(v.strip())
            else "")
    if "job_url" in out.columns:
        out["job_url"] = out["job_url"].astype(str).map(
            lambda v: v.strip() if v.strip().startswith("http") else "")
    if "job_id" in out.columns:
        def _clean_id(v):
            v = str(v).strip()
            if not v:
                return v
            if v.startswith("/") or "banen--" in v or ".html" in v:
                m = re.search(r"-(\d{4,})(?:-inline)?\.html(?:[?#].*)?$", v)
                return m.group(1) if m else ""
            return v
        out["job_id"] = out["job_id"].astype(str).map(_clean_id)
    if "workload_pct" in out.columns:
        out["workload_pct"] = out["workload_pct"].astype(str).map(_norm_workload)
    return out


def find_output_csvs(since: _dt.datetime | None = None) -> list[Path]:
    """Encuentra todos los data/*/output/jobs.csv.

    Si `since` se indica, solo devuelve los modificados a partir de ese
    instante (merge parcial: ignora outputs no actualizados en el run actual).
    """
    csvs = list(PROJECT_ROOT.glob("data/*/output/jobs.csv"))
    # Excluir merged
    csvs = [c for c in csvs if "merged" not in str(c)]
    if since is not None:
        csvs = [c for c in csvs
                if _dt.datetime.fromtimestamp(c.stat().st_mtime) >= since]
    return csvs


# Peso de calidad por campo: los campos clave pesan mas que los opcionales
QUALITY_WEIGHTS = {
    "title": 5,
    "company_name": 4,
    "description_full": 4,
    "job_url": 3,
    "location_raw": 3,
    "posted_datetime": 2,
    "salary_min": 3,
    "salary_max": 3,
    "salary_currency": 2,
    "salary_period": 1,
    "employment_type": 2,
    "work_mode": 2,
    "requirements": 1,
    "responsibilities": 1,
    "benefits": 1,
    "skills": 1,
}


def _quality_score(row) -> int:
    """Puntuacion de calidad ponderada de una fila."""
    score = 0
    for col, w in QUALITY_WEIGHTS.items():
        val = row.get(col)
        if val is not None and str(val).strip():
            score += w
    return score


def merge(since: _dt.datetime | None = None) -> pd.DataFrame:
    csvs = find_output_csvs(since)
    if not csvs:
        log.warning("No se encontraron CSVs para mergear.")
        return pd.DataFrame()

    log.info("Encontrados %d CSVs:", len(csvs))
    for c in csvs:
        log.info("  - %s", c)

    dfs = []
    for c in csvs:
        try:
            df = pd.read_csv(c, dtype=str, keep_default_na=False)
            df = clean_incoherent(df)
            dfs.append(df)
            log.info("  Leido: %d filas de %s", len(df), c.parent.parent.name)
        except Exception as e:
            log.warning("  Error leyendo %s: %s", c, e)

    if not dfs:
        return pd.DataFrame()

    merged = pd.concat(dfs, ignore_index=True)

    # Deduplicar por job_id priorizando la fila con mayor calidad ponderada
    if "job_id" in merged.columns:
        merged["_quality"] = merged.apply(_quality_score, axis=1)
        merged = merged.sort_values("_quality", ascending=False)
        merged = merged.drop_duplicates(subset="job_id", keep="first")
        merged = merged.drop(columns=["_quality"])
        log.info("Tras dedup por calidad: %d ofertas unicas", len(merged))

    return merged


def write_outputs(df: pd.DataFrame) -> None:
    out_dir = PROJECT_ROOT / "data" / "merged"
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "jobs_unified.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log.info("CSV unificado: %s (%d filas)", csv_path, len(df))

    # Parquet con tipos basicos
    parquet_path = out_dir / "jobs_unified.parquet"
    for col in ["salary_min", "salary_max"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "num_applicants" in df.columns:
        df["num_applicants"] = pd.to_numeric(df["num_applicants"], errors="coerce")
    if "is_new" in df.columns:
        df["is_new"] = df["is_new"].astype(str).str.lower().isin(["true", "1", "yes"])
    if "salary_disclosed" in df.columns:
        df["salary_disclosed"] = df["salary_disclosed"].astype(str).str.lower().isin(["true", "1", "yes"])

    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, parquet_path, compression="snappy")
    log.info("Parquet unificado: %s (%d filas)", parquet_path, len(df))


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge de outputs multi-site.")
    ap.add_argument("--since", default=None,
                    help="ISO local (YYYY-MM-DDTHH:MM:SS). Ignora CSVs no "
                         "modificados desde entonces (merge parcial).")
    ap.add_argument("--no-translate", action="store_true",
                    help="No traducir los textos al ingles antes de escribir.")
    ap.add_argument("--translate-limit", type=int, default=0,
                    help="Limitar el numero de celdas a traducir (0 = sin limite).")
    ap.add_argument("--translate-cache", default="data/.translate_cache.json",
                    help="Ruta de la cache de traduccion.")
    args = ap.parse_args()
    since = _dt.datetime.fromisoformat(args.since) if args.since else None

    log.info("=== Merge de outputs ===")
    if since is not None:
        log.info("Merge parcial: solo outputs modificados desde %s",
                 since.isoformat())
    df = merge(since)
    if df.empty:
        log.warning("Sin datos para mergear. Ejecuta los scrapers primero.")
        return 1

    if not args.no_translate:
        log.info("=== Traduccion NL->EN (opt-out con --no-translate) ===")
        try:
            translator = Translator(cache_path=args.translate_cache)
            df = translator.translate_dataframe(df, limit=args.translate_limit)
        except Exception as e:  # noqa: BLE001
            log.warning("Traduccion fallida (%s); se escriben los datos originales", e)

    write_outputs(df)

    # Resumen
    if "site" in df.columns:
        log.info("Desglose por sitio:")
        for site, count in df["site"].value_counts().items():
            log.info("  %s: %d ofertas", site, count)
    if "country" in df.columns:
        log.info("Desglose por pais:")
        for country, count in df["country"].value_counts().items():
            log.info("  %s: %d ofertas", country, count)

    # Salarios
    if "salary_min" in df.columns:
        has_salary = df["salary_min"].notna().sum()
        log.info("Ofertas con salario: %d / %d (%.1f%%)",
                 has_salary, len(df),
                 100 * has_salary / len(df) if len(df) else 0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
