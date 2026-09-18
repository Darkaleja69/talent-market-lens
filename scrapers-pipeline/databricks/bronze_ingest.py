# Databricks notebook source
# bronze_ingest.py
# FASE 8: ingesta incremental de los 4 scrapers a tablas Delta en job_offers.bronze
#
# Cada celda lanza un Auto Loader (cloudFiles) que:
#   1. Lee los parquets nuevos del landing (abfss://landing@<storage-account>...)
#   2. Anade columnas de auditoria (_ingest_date, _source_file)
#   3. Escribe a una tabla Delta en job_offers.bronze.<scraper>
#   4. trigger(availableNow=True) -> modo batch: procesa lo pendiente y para
#
# IMPORTANTE (duplicados en bronze):
#   jobs.parquet (LinkedIn) y jobs_unified.parquet (multi_site) son SNAPSHOTS
#   ACUMULATIVOS: el pipeline local sube cada noche una copia timestampada con
#   TODO el historico del run. Auto Loader la ingiere entera -> sin el dedup
#   de la ultima celda, bronze acumula millones de filas duplicadas.
#   La celda 5 (MANTENIMIENTO DIARIO) deja 1 fila por clave de oferta.
#
#   Regla de oro: NUNCA borres ni reseteces las carpetas _checkpoints/ del
#   landing; si el checkpoint se pierde, Auto Loader re-ingiere TODO el
#   landing (mas duplicados; el mantenimiento diario los limpiaria igualmente).
#
# Ejecutar en: Databricks Workspace -> Create -> Notebook -> nombre "bronze_ingest"
# Lenguaje: Python (PySpark)
# Cluster: cualquiera con Databricks Runtime 13.3+ (para availableNow=True)

import os

from pyspark.sql import functions as F

# Configuracion externalizada: la Storage Account no se versiona.
dbutils.widgets.text("storage_account", "")
dbutils.widgets.text("container", "landing")
STORAGE_ACCOUNT = dbutils.widgets.get("storage_account") or os.environ.get(
    "LANDING_STORAGE_ACCOUNT", "")
CONTAINER = dbutils.widgets.get("container") or "landing"
if not STORAGE_ACCOUNT:
    raise ValueError("Configura el widget/variable 'storage_account'.")
STORAGE_BASE = f"abfss://{CONTAINER}@{STORAGE_ACCOUNT}.dfs.core.windows.net"

# COMMAND ----------
# 1) INDEED (Parquet)
# Source: landing/indeed/ (tu PC sube indeed_jobs_YYYYMMDD_HHMM.parquet via azcopy)
# Dest:  job_offers.bronze.indeed

(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", f"{STORAGE_BASE}/_checkpoints/indeed/schema")
    .option("cloudFiles.useNotifications", "false")
    .load(f"{STORAGE_BASE}/indeed/")
    .withColumn("_ingest_date", F.current_date())
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .writeStream
    .format("delta")
    .option("checkpointLocation", f"{STORAGE_BASE}/_checkpoints/indeed/ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("job_offers.bronze.indeed")
)

# COMMAND ----------
# 2) LINKEDIN (Parquet)
# Source: landing/linkedin/ (tu PC sube jobs_YYYYMMDD_HHMMSS.parquet via azcopy)
# Dest:  job_offers.bronze.linkedin
#
# LinkedIn es el scraper que mas tarda (hasta 10h). Su output es un unico
# jobs.parquet acumulativo que el pipeline local renombra con sufijo de
# timestamp antes de subir, para que Autoloader lo detecte como archivo nuevo.

(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", f"{STORAGE_BASE}/_checkpoints/linkedin/schema")
    .option("cloudFiles.useNotifications", "false")
    .load(f"{STORAGE_BASE}/linkedin/")
    .withColumn("_ingest_date", F.current_date())
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .writeStream
    .format("delta")
    .option("checkpointLocation", f"{STORAGE_BASE}/_checkpoints/linkedin/ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("job_offers.bronze.linkedin")
)

# COMMAND ----------
# 3) INFOJOBS (Parquet)
# Source: landing/infojobs/ (tu PC sube offers_YYYYMMDD_HHMMSS.parquet via azcopy)
# Dest:  job_offers.bronze.infojobs
#
# InfoJobs genera muchos archivos pequenos por run (uno por ciudad/keyword).
# Autoloader los procesa todos en una sola pasada gracias al checkpoint.

(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", f"{STORAGE_BASE}/_checkpoints/infojobs/schema")
    .option("cloudFiles.useNotifications", "false")
    .load(f"{STORAGE_BASE}/infojobs/")
    .withColumn("_ingest_date", F.current_date())
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .writeStream
    .format("delta")
    .option("checkpointLocation", f"{STORAGE_BASE}/_checkpoints/infojobs/ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("job_offers.bronze.infojobs")
)

# COMMAND ----------
# 4) MULTI_SITE (Parquet)
# Source: landing/multi_site/ (tu PC sube jobs_unified_YYYYMMDD_HHMMSS.parquet)
# Dest:  job_offers.bronze.multi_site
#
# Multi-site es un merge de 4 subscrapers (irishjobs, stepstone_nl, jobs_ch,
# glassdoor). El archivo jobs_unified.parquet se renombra con timestamp antes
# de subir para que Autoloader lo detecte.

(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", f"{STORAGE_BASE}/_checkpoints/multi_site/schema")
    .option("cloudFiles.useNotifications", "false")
    .load(f"{STORAGE_BASE}/multi_site/")
    .withColumn("_ingest_date", F.current_date())
    .withColumn("_source_file", F.col("_metadata.file_path"))
    .writeStream
    .format("delta")
    .option("checkpointLocation", f"{STORAGE_BASE}/_checkpoints/multi_site/ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("job_offers.bronze.multi_site")
)

# COMMAND ----------
# VERIFICACION (opcional, descomentar para comprobar tras la primera run)
# %sql
# SELECT 'indeed' AS scraper, COUNT(*) AS rows FROM job_offers.bronze.indeed
# UNION ALL SELECT 'linkedin', COUNT(*) FROM job_offers.bronze.linkedin
# UNION ALL SELECT 'infojobs', COUNT(*) FROM job_offers.bronze.infojobs
# UNION ALL SELECT 'multi_site', COUNT(*) FROM job_offers.bronze.multi_site;

# COMMAND ----------
# 5) MANTENIMIENTO DIARIO: deduplicacion de bronze por clave de oferta
# ---------------------------------------------------------------------------
# Los snapshots acumulativos (jobs.parquet / jobs_unified.parquet) se ingieren
# enteros cada noche. Esta celda deja 1 fila por clave (la mas reciente por
# _ingest_date/_source_file), igual que fix_bronze_duplicates.py. Sin esto,
# bronze crece sin limite (millones de filas) y el pipeline silver/gold se
# vuelve lentiisimo.
from pyspark.sql import Window, functions as F

BRONZE_KEYS = {
    "indeed": "job_key",
    "linkedin": "job_id",
    "infojobs": "id_oferta",
    "multi_site": "job_id",
}

for _name, _key in BRONZE_KEYS.items():
    _tbl = f"job_offers.bronze.{_name}"
    try:
        _df = spark.read.table(_tbl)
    except Exception:  # noqa: BLE001
        print(f"[mantenimiento] {_name}: no se puede leer {_tbl}, se omite")
        continue
    _total = _df.count()
    if _total == 0 or _key not in _df.columns:
        print(f"[mantenimiento] {_name}: {_total} filas, sin clave '{_key}' -> se omite")
        continue
    _w = Window.partitionBy(_key).orderBy(
        F.desc_nulls_last("_ingest_date"), F.desc_nulls_last("_source_file"))
    _dedup = (_df
              .where(F.col(_key).isNotNull() & (F.col(_key) != ""))
              .withColumn("_rn", F.row_number().over(_w))
              .where(F.col("_rn") == 1)
              .drop("_rn"))
    _noid = _df.where(F.col(_key).isNull() | (F.col(_key) == ""))
    _result = _dedup.unionByName(_noid, allowMissingColumns=True)
    _result.write.mode("overwrite").saveAsTable(_tbl)
    print(f"[mantenimiento] {_name}: {_total} -> {spark.read.table(_tbl).count()} filas")

# COMMAND ----------
# 6) CUARENTENA POR MANIFIESTO (defensa en profundidad, OPCIONAL)
# ---------------------------------------------------------------------------
# El pipeline local valida los parquet ANTES de subirlos (ensure_compatible.py)
# y publica un manifest en landing/_manifests/<scraper>/<stamp>.json con la
# lista de ficheros validos (campo "remote" = dia=YYYY-MM-DD/<fichero>).
#
# Esta celda cruza los ficheros que Autoloader ve en landing/<scraper>/ contra
# los manifests: cualquier parquet que NO este en un manifest valido se mueve
# a landing/_quarantine/<scraper>/... para que no se vuelva a ingerir y quede
# visible para investigacion.
#
# ATENCION: activar SOLO cuando el pipeline local suba manifests de forma
# estable. Los ficheros historicos subidos antes de esta funcionalidad no
# tienen manifest y serian movidos a cuarentena (no pierdes datos ya
# ingeridos en Bronze, pero si la trazabilidad del origen).
ENABLE_MANIFEST_QUARANTINE = False

if ENABLE_MANIFEST_QUARANTINE:
    from pyspark.sql import functions as F

    MANIFEST_BASE = f"{STORAGE_BASE}/_manifests"
    QUARANTINE_BASE = f"{STORAGE_BASE}/_quarantine"

    def _list_files(path, out):
        for f in dbutils.fs.ls(path):
            if f.isDir():
                _list_files(f.path, out)
            else:
                out.append(f.path)

    for scraper in ["indeed", "linkedin", "infojobs", "multi_site"]:
        valid = set()
        try:
            for m in dbutils.fs.ls(f"{MANIFEST_BASE}/{scraper}/"):
                if m.path.endswith(".json"):
                    mdf = spark.read.json(m.path)
                    remotes = mdf.select(F.explode("files").alias("f")).select("f.remote").collect()
                    for row in remotes:
                        if row["remote"]:
                            valid.add(str(row["remote"]).replace("\\", "/"))
        except Exception as e:
            print(f"[quarantine] sin manifests validos para {scraper}: {e}")
            continue
        if not valid:
            print(f"[quarantine] {scraper}: no hay manifests, se omite.")
            continue

        ingested = []
        _list_files(f"{STORAGE_BASE}/{scraper}/", ingested)
        moved = 0
        for f in ingested:
            if not f.endswith(".parquet"):
                continue
            rel = f.replace(f"{STORAGE_BASE}/{scraper}/", "").replace("\\", "/")
            if rel not in valid:
                dest = f"{QUARANTINE_BASE}/{scraper}/{rel}"
                try:
                    dbutils.fs.mkdirs(dest.rsplit("/", 1)[0])
                    dbutils.fs.mv(f, dest)
                    moved += 1
                    print(f"[quarantine] {scraper}: movido {f} -> {dest}")
                except Exception as e:
                    print(f"[quarantine] {scraper}: ERROR moviendo {f}: {e}")
        print(f"[quarantine] {scraper}: {moved} fichero(s) sin manifest movidos a cuarentena")