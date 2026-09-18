"""Exportadores tipados: Parquet, Delta Lake, JSONL, XLSX, CSV, metadata.

Cada funcion acepta un DataFrame ya tipado (salida de schema.parse_typed_df)
y escribe un archivo con el formato correspondiente.

Uso tipico desde runner.py:
    from scraper.schema import parse_typed_df
    from scraper.exporters import export_all
    df = parse_typed_df([o.to_typed_dict() for o in offers])
    export_all(df, meta, output_dir, timestamp)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd

from .schema import COLUMNS_EXCEL_EXCLUDE

log = logging.getLogger(__name__)

_RATING_OR_URL = re.compile(r"^\d[,.]\d{1,2}$|^https?://")


def _quarantine_incoherent(df: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    """Descarta filas incoherentes (rating/URL como empresa, job_key vacio,
    URL rota) y las guarda en <output_dir>/quality/ para auditoria."""
    if df is None or len(df) == 0:
        return df

    def _coherent(r) -> bool:
        job_key = str(r.get("job_key", "") or "").strip()
        if not job_key:
            return False
        company = str(r.get("company", "") or "").strip()
        if company and _RATING_OR_URL.match(company):
            return False
        url = str(r.get("viewjob_url", "") or "").strip()
        if url and not re.match(r"^https?://", url):
            return False
        return True

    mask = df.apply(_coherent, axis=1)
    if mask.all():
        return df
    bad, good = df[~mask], df[mask]
    qdir = output_dir / "quality"
    qdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    qpath = qdir / f"bad_rows_{ts}.csv"
    bad.to_csv(qpath, index=False, encoding="utf-8-sig")
    log.warning("Coherencia: %d fila(s) incoherente(s) -> cuarentena en %s",
                len(bad), qpath)
    return good


# --- Helpers ---------------------------------------------------------------
def _as_stem(output_dir: Path, timestamp: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    return f"indeed_jobs_{timestamp}"


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convierte DataFrame a lista de dicts con tipos nativos Python.
    Maneja NaN -> None para JSON/Delta, y datetime -> ISO str para JSON/CSV.
    """
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rec: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, pd.Timestamp):
                rec[col] = val.isoformat()
            elif isinstance(val, (pd.Int64Dtype, pd.BooleanDtype)):
                rec[col] = val if not pd.isna(val) else None
            else:
                rec[col] = val
        records.append(rec)
    return records


# --- Parquet ---------------------------------------------------------------
def to_parquet(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path:
    """Escribe Parquet con snappy. Tipos embebidos, nativo Spark/Databricks."""
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.parquet"
    df.to_parquet(path, index=False, compression="snappy", engine="pyarrow",
                  coerce_timestamps="us", allow_truncated_timestamps=True)
    log.info("Parquet: %s (%d filas, %d cols)", path, len(df), len(df.columns))
    return path


# --- Delta Lake ------------------------------------------------------------
def to_delta(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path | None:
    """Escribe Delta Lake (carpeta con _delta_log). Nativo de Databricks.

    Requiere deltalake instalado (pip install deltalake).
    Delta escribe en modo overwrite: cada ejecucion regenera la carpeta
    limpia (sin historico acumulativo en el delta, cada run es una snapshot).
    """
    try:
        from deltalake.writer import write_deltalake
    except ImportError:
        log.warning("deltalake no instalado. Omite salida Delta. pip install deltalake")
        return None

    stem = _as_stem(output_dir, timestamp)
    path_val = output_dir / f"{stem}.delta"
    # deltalake >= 1.0 acepta pandas DataFrame directamente
    try:
        import pyarrow as pa
        table = pa.Table.from_pandas(df, preserve_index=False)
        write_deltalake(str(path_val), table, mode="append")
    except Exception:
        log.warning("Delta via pyarrow fallo, intentando via pandas DataFrame")
        try:
            write_deltalake(str(path_val), df, mode="append")
        except Exception as e2:
            log.warning("Delta fallo completamente: %s. Omite Delta.", e2)
            return None
    log.info("Delta: %s (%d filas)", path_val, len(df))
    return path_val


# --- JSONL (JSON Lines) ----------------------------------------------------
def to_jsonl(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path:
    """Escribe JSON Lines: un objeto JSON por linea, sin array contenedor.
    Formato ideal para spark.read.json() y pd.read_json(lines=True).
    """
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.jsonl"
    records = _df_to_records(df)
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("JSONL: %s (%d lineas)", path, len(records))
    return path


# --- Nested JSON (legado, para backup legible) -----------------------------
def to_nested_json(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path:
    """JSON anidado por pais -> ciudad (legado, para lectura humana)."""
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.json"
    nested: dict[str, dict[str, list[dict]]] = {}
    for _, row in df.iterrows():
        rec: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                rec[col] = None
            elif isinstance(val, pd.Timestamp):
                rec[col] = val.isoformat()
            else:
                rec[col] = val
        country = row.get("country", "??")
        city = row.get("city_query", row.get("city", "??"))
        nested.setdefault(country, {}).setdefault(city, []).append(rec)
    path.write_text(json.dumps(nested, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("JSON: %s (%d ofertas)", path, len(df))
    return path


# --- XLSX (una sola hoja, tipada, con formato) -----------------------------
def to_xlsx_typed(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path:
    """Escribe un Excel con UNA sola hoja 'jobs', esquema tipado, sin
    description_html (si esta en COLUMNS_EXCEL_EXCLUDE), con encabezado
    congelado, anchos automaticos, bools como TRUE/FALSE, fechas formateadas.
    """
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.xlsx"

    # excluir columnas pesadas (description_html)
    cols = [c for c in df.columns if c not in COLUMNS_EXCEL_EXCLUDE]
    pdf = df[cols].copy()

    # convertir a tipos que Excel entienda bien
    for col in pdf.columns:
        dtype = str(pdf[col].dtype)
        if "bool" in dtype:
            pdf[col] = pdf[col].map(lambda x: "TRUE" if x is True else ("FALSE" if x is False else ""), na_action="ignore")
        elif "datetime" in dtype:
            # formatear como fecha Excel (YYYY-MM-DD HH:MM)
            pdf[col] = pdf[col].dt.strftime("%Y-%m-%d %H:%M")
        elif "string" in dtype or "object" in dtype:
            pdf[col] = pdf[col].fillna("")

    # escribir con openpyxl
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pdf.to_excel(writer, sheet_name="jobs", index=False)

        ws = writer.sheets["jobs"]
        # congelar encabezado
        ws.freeze_panes = "A2"
        # anchos automáticos (max entre encabezado y 100 primeras filas)
        for col_idx, col_name in enumerate(pdf.columns, start=1):
            col_letter = ws.cell(row=1, column=col_idx).column_letter
            col_header = str(col_name)
            # max de encabezado y contenido
            max_len = len(col_header)
            for row in range(2, min(len(pdf) + 2, 102)):
                cell_val = ws.cell(row=row, column=col_idx).value
                if cell_val:
                    max_len = max(max_len, min(len(str(cell_val)), 60))
            ws.column_dimensions[col_letter].width = max_len + 2
        # negrita en encabezado
        from openpyxl.styles import Font

        for cell in ws[1]:
            cell.font = Font(bold=True)

    log.info("XLSX: %s (%d filas, 1 hoja 'jobs')", path, len(df))
    return path


# --- CSV -------------------------------------------------------------------
def to_csv(df: pd.DataFrame, output_dir: Path, timestamp: str) -> Path:
    """CSV UTF-8 con BOM (para Excel). Todas las columnas, fechas ISO."""
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.csv"
    # convertir fechas a ISO string para CSV (sin compatibilidad de tipos)
    pdf = df.copy()
    for col in pdf.columns:
        if "datetime" in str(pdf[col].dtype):
            pdf[col] = pdf[col].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    pdf.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("CSV: %s (%d filas)", path, len(df))
    return path


# --- Metadata ---------------------------------------------------------------
def build_metadata(
    country: str,
    cities: list[str],
    terms: list[str],
    pages: int,
    total_offers: int,
    total_unique: int,
    by_city: dict[str, int],
    started_at: datetime,
    **extra: Any,
) -> dict[str, Any]:
    """Construye el dict de metadatos del run."""
    finished_at = datetime.now(timezone.utc)
    return {
        "run_id": str(uuid4()),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 1),
        "country": country,
        "cities": cities,
        "terms": terms,
        "pages": pages,
        "total_serps": len(cities) * pages * len(terms),
        "total_offers_raw": total_offers,
        "total_unique": total_unique,
        "by_city": by_city,
        "limit": extra.get("limit", 0),
        "logged_in": extra.get("logged_in", False),
        "cdp_port": extra.get("cdp_port", 0),
        "scraper_version": "1.1.0",
        **extra,
    }


def write_metadata(meta: dict[str, Any], output_dir: Path, timestamp: str) -> Path:
    """Guarda los metadatos del run en JSON."""
    stem = _as_stem(output_dir, timestamp)
    path = output_dir / f"{stem}.json"
    # si ya existe el JSON anidado, lo renombra a .jsonl y lo guarda como
    # scrape_metadata_<ts>.json aparte
    meta_path = output_dir / f"scrape_metadata_{timestamp}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Metadata: %s", meta_path)
    return meta_path


# --- Export all -------------------------------------------------------------
def export_all(
    df: pd.DataFrame,
    meta: dict[str, Any],
    output_dir: Path,
    timestamp: str,
    *,
    include_delta: bool = True,
) -> dict[str, Path | None]:
    """Ejecuta todos los exportadores y devuelve un dict con las rutas.

    Resiliente: si un export falla, loguea el error y continua con el siguiente.
    Orden: Parquet (principal), Delta, JSONL, XLSX, CSV, metadata.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path | None] = {}

    df = _quarantine_incoherent(df, output_dir)

    def _try(label: str, fn, *args):
        try:
            paths[label] = fn(*args)
        except Exception as e:
            log.warning("Export %s fallo: %s. Continuando con los demas.", label, e)
            paths[label] = None

    _try("parquet", to_parquet, df, output_dir, timestamp)

    if include_delta:
        _try("delta", to_delta, df, output_dir, timestamp)

    _try("jsonl", to_jsonl, df, output_dir, timestamp)
    _try("json", to_nested_json, df, output_dir, timestamp)
    _try("xlsx", to_xlsx_typed, df, output_dir, timestamp)
    _try("csv", to_csv, df, output_dir, timestamp)
    _try("metadata", write_metadata, meta, output_dir, timestamp)

    return paths
