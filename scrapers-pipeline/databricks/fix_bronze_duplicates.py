# Databricks notebook source
# fix_bronze_duplicates.py
# REMEDIACION (una sola vez): reduce las tablas bronze a filas UNICAS por clave
# de oferta, eliminando la duplicacion causada por los snapshots acumulativos.
#
# CAUSA RAIZ: jobs.parquet (LinkedIn) y jobs_unified.parquet (multi_site) son
# SNAPSHOTS ACUMULATIVOS (contienen todo el historico visto en el run). Cada
# noche el pipeline local hace una copia timestampada y la sube a ADLS; Auto
# Loader la ingiere ENTERA como filas nuevas. Ademas, recover_and_upload.ps1
# re-subia "el snapshot mas reciente de cada dia". Resultado: las mismas
# ofertas repetidas cientos de veces (millones de filas en bronze).
#
# Este script deja 1 fila por clave (la mas reciente por _ingest_date y
# _source_file). Las filas sin clave se conservan tal cual (no deduplicables).
#
# Ejecutar en Databricks UNA vez (Workflow -> Notebook -> fix_bronze_duplicates)
# ANTES de relanzar silver/gold. Despues ya se mantiene solo con la celda de
# mantenimiento de bronze_ingest.py.

from pyspark.sql import Window, functions as F

# Clave natural de cada scraper en BRONZE (nombres crudos del parquet, no los
# renombrados de silver).
KEYS = {
    "indeed": "job_key",
    "linkedin": "job_id",
    "infojobs": "id_oferta",
    "multi_site": "job_id",
}

for name, key in KEYS.items():
    tbl = f"job_offers.bronze.{name}"
    print(f"=== {name} ===")
    try:
        df = spark.read.table(tbl)
    except Exception as e:  # noqa: BLE001
        print(f"  [skip] no se puede leer {tbl}: {e}")
        continue
    total = df.count()
    if total == 0:
        print("  [skip] tabla vacia")
        continue
    if key not in df.columns:
        print(f"  [skip] no existe la columna clave '{key}' (columnas: {df.columns})")
        continue

    uniq = (
        df.where(F.col(key).isNotNull() & (F.col(key) != ""))
        .select(key).distinct().count()
    )
    print(f"  antes: {total} filas | claves unicas no vacias: {uniq}")

    # Mantener la fila MAS RECIENTE por clave (nulls de _ingest_date al final)
    w = Window.partitionBy(key).orderBy(
        F.desc_nulls_last("_ingest_date"), F.desc_nulls_last("_source_file"))
    dedup = (df
             .where(F.col(key).isNotNull() & (F.col(key) != ""))
             .withColumn("_rn", F.row_number().over(w))
             .where(F.col("_rn") == 1)
             .drop("_rn"))
    # Filas sin clave: se conservan tal cual (no se pueden deduplicar)
    noid = df.where(F.col(key).isNull() | (F.col(key) == ""))
    result = dedup.unionByName(noid, allowMissingColumns=True)

    result.write.mode("overwrite").saveAsTable(tbl)
    after = spark.read.table(tbl).count()
    print(f"  despues: {after} filas")

# COMMAND ----------
# Compactar los archivos de cada tabla (recomendado tras el dedup masivo)
for name in KEYS:
    spark.sql(f"OPTIMIZE job_offers.bronze.{name}")
print("OPTIMIZE completado")