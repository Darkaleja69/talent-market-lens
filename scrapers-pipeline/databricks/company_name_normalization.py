# Databricks notebook source
# company_name_normalization.py
# Normalizacion de nombres de empresa en la capa gold:
#   - Title case por palabra: primera letra mayuscula, resto minusculas
#   - Trim: quita espacios iniciales/finales y colapsa espacios internos multiples
#
# Uso: copia estas celdas en tu notebook de enriquecimiento gold, o importa
# la funcion via %run / import si la tienes como modulo.
#
# Ejecutar en: Databricks Workspace -> Notebook (Python / PySpark)
# Cluster: cualquiera con Databricks Runtime 10.4+

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

# COMMAND ----------
# FUNCION PRINCIPAL (devuelve una expresion de columna PySpark)
#
# Ejemplos:
#   "MAPFRE"                -> "Mapfre"
#   "  GOOGLE   IRELAND  "  -> "Google Ireland"
#   "RONAL  Wheels "        -> "Ronal Wheels"
#   "google inc"            -> "Google Inc"
#
# Nota: convierte acronimos a title case ("PwC" -> "Pwc", "IBM" -> "Ibm").
# Si quieres preservar ciertos acronimos, anade una transformacion
# post-proceso con F.when / dict de excepciones (ver celda opcional).

def normalize_company_name(col: Column) -> Column:
    """Title case por palabra + trim de espacios sobrantes."""
    return F.initcap(F.trim(F.regexp_replace(col, r"\s+", " ")))

# COMMAND ----------
# APLICAR SOBRE UN DATAFRAME (reemplaza el valor de la columna)

def apply_company_normalization(df: DataFrame, col_name: str = "company_name") -> DataFrame:
    """Devuelve un nuevo DataFrame con la columna indicada normalizada."""
    return df.withColumn(col_name, normalize_company_name(F.col(col_name)))

# COMMAND ----------
# EJEMPLO DE USO EN EL PIPELINE GOLD
# (adaptalo a los nombres reales de tus tablas / steps)

# companies = spark.table("job_offers.gold.companies_complete_catalog")
# offers = spark.table("job_offers.gold.offers_complete_catalog")
#
# companies = apply_company_normalization(companies)
# offers = apply_company_normalization(offers)
#
# companies.write.mode("overwrite").saveAsTable("job_offers.gold.companies_complete_catalog")
# offers.write.mode("overwrite").saveAsTable("job_offers.gold.offers_complete_catalog")

# COMMAND ----------
# VERIFICACION: tras normalizar, comprueba que no quedan duplicados
# (mismo criterio case-insensitive que usara Power BI para la relacion 1:*)

# from pyspark.sql import functions as F
#
# dupes = (companies
#          .groupBy("company_name")
#          .count()
#          .filter("count > 1")
#          .orderBy(F.desc("count")))
# dupes.show(50, truncate=False)
# print("Duplicados:", dupes.count())
#
# # Quita una fila por cada nombre repetido conservando la primera:
# companies = companies.dropDuplicates(["company_name"])

# COMMAND ----------
# ALTERNATIVA 1: expresion SQL directa (SELECT)
# %sql
# SELECT initcap(trim(regexp_replace(company_name, '\\s+', ' '))) AS company_name
# FROM job_offers.gold.companies_complete_catalog

# COMMAND ----------
# ALTERNATIVA 2: funcion SQL reutilizable en Unity Catalog
# %sql
# CREATE OR REPLACE FUNCTION job_offers.gold.fn_title_case_trim(s STRING)
# RETURNS STRING
# RETURN initcap(trim(regexp_replace(s, '\\s+', ' ')));
#
# -- Uso:
# SELECT fn_title_case_trim(company_name) AS company_name
# FROM job_offers.gold.companies_complete_catalog;

# COMMAND ----------
# OPCIONAL: preservar acronimos concretos tras el title case
#
# ACRONYMS = {"Pwc": "PwC", "Ibm": "IBM", "Kpmg": "KPMG", "Deloitte Touche Tohmatsu": "Deloitte"}
#
# def apply_company_normalization_with_acronyms(df, col_name="company_name"):
#     col = F.col(col_name)
#     expr = normalize_company_name(col)
#     for k, v in ACRONYMS.items():
#         expr = F.when(F.lower(col) == k.lower(), F.lit(v)).otherwise(expr)
#     return df.withColumn(col_name, expr)