# Databricks notebook source
# fix_misaligned_rows.py
# Repara filas desplazadas (shift) en tablas bronze/silver ya ingeridas.
#
# Sintoma: "se ha comido" una columna (p.ej. company_name) y todos los
# valores posteriores quedaron en la columna incorrecta.
#
# Metodo: para cada fila se prueban desplazamientos -MAX_SHIFT..+MAX_SHIFT y
# se puntua con validadores por columna (URL, fecha, numerico, rating, id,
# booleano, texto). Se aplica el desplazamiento con mejor puntuacion; si hay
# empate o la mejora es minima, la fila se deja intacta (conservador).
#
# IMPORTANTE: aplica esto a tablas donde las columnas sigan siendo strings
# (bronze sin cast). Si la tabla ya convirtio tipos (fecha -> timestamp),
# los valores corruptos ya se perdieron como null y no se pueden recuperar;
# en ese caso re-ingiere desde el landing o usa repair_misaligned.py local.
#
# Ejecutar en: Databricks Workspace -> Notebook (Python / PySpark)
# Cluster: cualquiera con Databricks Runtime 10.4+

import re

from pyspark.sql import functions as F
from pyspark.sql.types import (ArrayType, IntegerType, StringType,
                               StructField, StructType)

# COMMAND ----------
# TABLA A REPARAR (cambiar): el notebook detecta el esquema y aplica el
# config del scraper correspondiente. Solo repara si coincide con uno
# conocido; si no, lista las columnas para anadirlo a SITES.

TABLE_NAME = "job_offers.bronze.indeed"     # <- tabla de origen
OUT_TABLE = "job_offers.silver.indeed_repaired"  # <- tabla de salida (no sobrescribe)
MAX_SHIFT = 3
MIN_SCORE_DELTA = 1

# COMMAND ----------
# VALIDADORES

_URL = re.compile(r"^https?://\S+$")
_RATING = re.compile(r"^\d[,.]\d{1,2}$")
_NUM = re.compile(r"^-?\d+(?:[.,]\d+)?$")
_ID = re.compile(r"^[\w.\-/]{1,80}$")
_BOOLS = {"true", "false", "yes", "no", "1", "0"}


def _v_url(v): return bool(v) and bool(_URL.match(v.strip()))
def _v_rating(v): return bool(v) and bool(_RATING.match(v.strip()))
def _v_num(v): return bool(v) and bool(_NUM.match(v.strip().replace("€", "").replace("$", "").replace("£", "").strip()))
def _v_id(v): return bool(v) and bool(_ID.match(v.strip()))
def _v_bool(v): return bool(v) and v.strip().lower() in _BOOLS
def _v_text(v): return bool(v and v.strip()) and not _URL.match(v.strip())


def _v_date(v):
    if not v or not v.strip():
        return False
    try:
        from datetime import datetime
        datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        return True
    except Exception:
        return False

# COMMAND ----------
# CONFIG POR SCRAPER (mismo esquema que SILVER_LAYER.md)

SITES = {
    "multi_site": {
        "columns": [
            "job_id", "job_url", "title", "company_name", "location_raw",
            "location_city", "location_region", "location_country",
            "posted_datetime", "posted_relative", "is_new", "company_url",
            "company_industry", "company_size", "work_mode", "employment_type",
            "experience_level", "salary_raw", "salary_min", "salary_max",
            "salary_currency", "salary_period", "num_applicants",
            "description_full", "description_snippet", "role_summary",
            "company_description", "responsibilities", "requirements",
            "benefits", "skills", "site", "country", "workload_pct",
            "salary_disclosed", "search_role", "search_city", "source",
            "scraped_at",
        ],
        "validators": {
            "job_id": _v_id, "job_url": _v_url, "title": _v_text,
            "company_name": _v_text, "location_raw": _v_text,
            "location_city": _v_text, "location_region": _v_text,
            "location_country": _v_text, "posted_datetime": _v_date,
            "posted_relative": _v_text, "is_new": _v_bool,
            "company_url": _v_url, "salary_min": _v_num, "salary_max": _v_num,
            "num_applicants": _v_num, "workload_pct": _v_num,
            "salary_disclosed": _v_bool, "scraped_at": _v_date,
        },
    },
    "indeed": {
        "columns": [
            "job_key", "viewjob_url", "apply_url", "title", "company",
            "company_id_encrypted", "company_rating", "company_review_count",
            "company_overview_link", "location", "city", "state", "country",
            "salary_min", "salary_max", "salary_type", "salary_text",
            "is_remote", "job_type", "snippet", "description_html",
            "description_text", "posted_relative", "posted_date", "benefits",
            "contract_type", "schedule", "workplace_type", "is_sponsored",
            "city_query", "search_term", "page", "scraped_at",
        ],
        "validators": {
            "job_key": _v_id, "viewjob_url": _v_url, "apply_url": _v_url,
            "title": _v_text, "company": _v_text, "company_rating": _v_rating,
            "company_review_count": _v_num, "company_overview_link": _v_url,
            "location": _v_text, "city": _v_text, "state": _v_text,
            "country": _v_text, "salary_min": _v_num, "salary_max": _v_num,
            "is_remote": _v_bool, "posted_date": _v_date,
            "is_sponsored": _v_bool, "page": _v_num, "scraped_at": _v_date,
        },
    },
    "linkedin": {
        "columns": [
            "job_id", "job_url", "title", "company_name", "location_raw",
            "location_city", "location_region", "location_country",
            "posted_datetime", "posted_relative", "is_new", "company_url",
            "company_industry", "company_size", "work_mode", "employment_type",
            "experience_level", "salary_raw", "salary_min", "salary_max",
            "salary_currency", "salary_period", "num_applicants",
            "description_full", "role_summary", "company_description",
            "responsibilities", "requirements", "benefits", "skills",
            "search_role", "search_city", "source", "scraped_at",
        ],
        "validators": {
            "job_id": _v_id, "job_url": _v_url, "title": _v_text,
            "company_name": _v_text, "location_raw": _v_text,
            "location_city": _v_text, "location_region": _v_text,
            "location_country": _v_text, "posted_datetime": _v_date,
            "posted_relative": _v_text, "is_new": _v_bool,
            "company_url": _v_url, "salary_min": _v_num, "salary_max": _v_num,
            "num_applicants": _v_num, "scraped_at": _v_date,
        },
    },
    "infojobs": {
        "columns": [
            "id_oferta", "titulo", "empresa", "ciudad", "provincia", "pais",
            "fecha_publicacion", "categoria", "salario_raw", "salario_min",
            "salario_max", "moneda", "periodo", "jornada", "tipo_contrato",
            "experiencia_min", "modalidad", "descripcion_snippet", "url_oferta",
            "fecha_scraped", "fuente", "ciudad_buscada", "keyword_buscada",
            "pagina",
        ],
        "validators": {
            "id_oferta": _v_id, "titulo": _v_text, "empresa": _v_text,
            "ciudad": _v_text, "provincia": _v_text, "pais": _v_text,
            "fecha_publicacion": _v_date, "salario_min": _v_num,
            "salario_max": _v_num, "url_oferta": _v_url,
            "fecha_scraped": _v_date, "pagina": _v_num,
        },
    },
}

# COMMAND ----------
# LECTURA COMO STRINGS + DETECCION DE ESQUEMA

def detect_site(cols):
    best, best_overlap = None, -1
    for name, cfg in SITES.items():
        overlap = len(set(cols).intersection(cfg["columns"]))
        if overlap > best_overlap:
            best, best_overlap = name, overlap
    return best

df = spark.table(TABLE_NAME)
cols = df.columns
site = detect_site(cols)
print(f"Tabla: {TABLE_NAME} | columnas: {len(cols)} | esquema detectado: {site}")

if site is None:
    print("Esquema no reconocido. Columnas disponibles:")
    print(cols)
    raise SystemExit(1)

cfg = SITES[site]
col_order, validators = cfg["columns"], cfg["validators"]
if list(cols) != col_order:
    print("AVISO: el orden de columnas no coincide con el esquema del scraper.")
    print("Usando el orden de la tabla y validadores por nombre de columna.")
    col_order = list(cols)

# CAST A STRING: si ya hay columnas tipadas (timestamp/num), los valores
# corruptos ya son null; se avisa pero se sigue (el resto se repara).
str_exprs = [F.col(c).cast(StringType()).alias(c) for c in col_order]
base = df.select(*str_exprs).withColumn("_values",
    F.array(*[F.col(c) for c in col_order]))

# COMMAND ----------
# UDF DE REPARACION

def _repair_row(values):
    n = len(values)
    best_score, best_shift, best_row = -1, 0, list(values)
    for k in range(-MAX_SHIFT, MAX_SHIFT + 1):
        row = [""] * n
        for i, v in enumerate(values):
            j = i + k
            if 0 <= j < n:
                row[j] = "" if v is None else str(v)
        score = 0
        for name, v in zip(col_order, row):
            fn = validators.get(name)
            if v and v.strip() and fn and fn(v):
                score += 1
        if score > best_score:
            best_score, best_shift, best_row = score, k, row
    base_score = 0
    for name, v in zip(col_order, values):
        v = "" if v is None else str(v)
        fn = validators.get(name)
        if v and v.strip() and fn and fn(v):
            base_score += 1
    conflict = best_shift != 0 and (best_score - base_score) < MIN_SCORE_DELTA
    return best_shift if not conflict else 0, best_score, best_row

repair_schema = StructType([
    StructField("_shift", IntegerType(), True),
    StructField("_score", IntegerType(), True),
    StructField("_row", ArrayType(StringType()), True),
])
repair_udf = F.udf(_repair_row, repair_schema)

repaired = base.select(
    "*",
    repair_udf(F.col("_values")).alias("_rep")
).withColumn("_repair_shift", F.col("_rep._shift")) \
 .withColumn("_repair_score", F.col("_rep._score")) \
 .withColumn("_values", F.col("_rep._row")) \
 .drop("_rep")

out = repaired.select(
    *[F.col("_values").getItem(i).alias(c) for i, c in enumerate(col_order)],
    F.col("_repair_shift"), F.col("_repair_score")
)

# COMMAND ----------
# AUDITORIA ANTES DE ESCRIBIR

total = out.count()
shifted = out.filter(F.col("_repair_shift") != 0)
n_shifted = shifted.count()
print(f"Total filas: {total}")
print(f"Filas desplazadas reparadas: {n_shifted} ({100 * n_shifted / max(total, 1):.2f}%)")

if n_shifted > 0:
    print("\nDistribucion de desplazamientos aplicados:")
    shifted.groupBy("_repair_shift").count().orderBy("_repair_shift").show(10, False)

    print("\nMuestra de filas reparadas (primeras 10):")
    shifted.limit(10).select(
        *[F.col(c) for c in col_order[:8]], "_repair_shift"
    ).show(10, False)

# COMMAND ----------
# ESCRITURA (descomentar cuando la auditoria este bien)
# Escribe a una tabla NUEVA; no sobrescribe la original.
#
# out.write.mode("overwrite").saveAsTable(OUT_TABLE)
# print(f"Escrito a {OUT_TABLE}")

# COMMAND ----------
# CONSULTA RAPIDA PARA LOCALIZAR FILAS SOSPECHOSAS EN CUALQUIER TABLA
# (sin reparar): filas donde company_name parece un rating o una URL, o
# donde posted_date no es una fecha.
#
# %sql
# SELECT job_id, title, company_name, location_city, posted_date
# FROM job_offers.gold.offers_complete_catalog
# WHERE company_name RLIKE '^[0-9][,.]' OR company_name RLIKE '^https?://'
#    OR posted_date IS NULL
# LIMIT 50;