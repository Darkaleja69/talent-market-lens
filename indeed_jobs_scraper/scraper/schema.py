"""Esquema tipado explicito de JobOffer para export.

Define los dtypes pandas y el orden de columnas. Aplicable antes de cualquier
export (Parquet, Delta, JSONL, XLSX, CSV). Garantiza que el esquema sea
estable entre ejecuciones: mismo dtype para la misma columna siempre,
sin que pandas infiera float64 donde deberia ser str.

Databricks/Spark lee el esquema directamente del Parquet/Delta, sin
sorpresas de tipos.
"""
from __future__ import annotations

# No import pandas aqui para que el modulo no dependa de el en tiempo de
# import (solo se usa cuando se llama a parse_typed_df). pandas se importa
# en exporters.py.
from typing import Any


# dtypes pandas para cada columna. Aplicar con df.astype(...) antes de escribir.
# Usamos los alias nullable de pandas (Float64, Int64, boolean) para preservar
# NaN en columnas opcionales. datetime64[ns, UTC] para fechas.
JOB_OFFER_DTYPES: dict[str, str] = {
    # Identificacion
    "job_key": "string",
    "viewjob_url": "string",
    "apply_url": "string",
    # Contenido principal
    "title": "string",
    "company": "string",
    "company_id_encrypted": "string",
    "company_rating": "Float64",           # nullable float
    "company_review_count": "Int64",       # nullable int
    "company_overview_link": "string",
    # Ubicacion
    "location": "string",
    "city": "string",
    "state": "string",
    "is_remote": "boolean",
    # Salario
    "salary_min": "Int64",                 # nullable int
    "salary_max": "Int64",
    "salary_type": "string",
    "salary_text": "string",
    # Tipo y condiciones
    "job_type": "string",
    # Descripcion
    "snippet": "string",
    "description_html": "string",
    "description_text": "string",
    "posted_relative": "string",
    "posted_date": "datetime64[ns, UTC]",
    # Condiciones
    "benefits": "string",
    "contract_type": "string",
    "schedule": "string",
    "workplace_type": "string",  # parseado desde ISO string
    # Flags
    "is_sponsored": "boolean",
    # Trazabilidad
    "country": "string",
    "city_query": "string",
    "search_term": "string",
    "page": "Int64",                       # nullable int
    "scraped_at": "datetime64[ns, UTC]",   # parseado desde ISO string
}

# Orden de columnas para presentacion (legible: campos principales primero,
# trazabilidad al final).
COLUMN_ORDER: list[str] = [
    "job_key",
    "title",
    "company",
    "location",
    "city",
    "state",
    "country",
    "salary_text",
    "salary_min",
    "salary_max",
    "salary_type",
    "is_remote",
    "job_type",
    "workplace_type",
    "contract_type",
    "schedule",
    "benefits",
    "posted_date",
    "posted_relative",
    "company_rating",
    "company_review_count",
    "snippet",
    "description_text",
    "description_html",
    "viewjob_url",
    "apply_url",
    "company_overview_link",
    "company_id_encrypted",
    "is_sponsored",
    "search_term",
    "city_query",
    "page",
    "scraped_at",
]

# Columnas a excluir del Excel (para aligerarlo, el usuario decide cuales).
# description_html puede tener varios KB y hace el Excel pesado e ilegible.
COLUMNS_EXCEL_EXCLUDE: list[str] = [
    "description_html",
]


def parse_typed_df(offers: list[dict[str, Any]]) -> "pd.DataFrame":
    """Convierte una lista de dicts (salida de JobOffer.to_typed_dict) en un
    DataFrame con el esquema tipado explicito.

    - Aplica JOB_OFFER_DTYPES via astype().
    - Parsea posted_date y scraped_at a datetime64[ns, UTC].
    - Reordena columnas segun COLUMN_ORDER.
    - Convierte strings vacios "" de vuelta a pd.NA para columnas string
      (pandas string nullable compat) — se hace en exporters para cada formato.
    """
    import pandas as pd

    df = pd.DataFrame(offers)

    # reordenar columnas (solo las que existen)
    cols_present = [c for c in COLUMN_ORDER if c in df.columns]
    df = df[cols_present]  # type: ignore[assignment]

    # parsear fechas: de ISO string a datetime64[ns, UTC]
    for date_col in ("posted_date", "scraped_at"):
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], utc=True, errors="coerce")

    # aplicar dtypes explicitos
    dtypes_present = {k: v for k, v in JOB_OFFER_DTYPES.items() if k in df.columns}
    df = df.astype(dtypes_present, errors="ignore")

    return df
