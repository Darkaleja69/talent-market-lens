"""Current ingestion mechanism adapted to the T-09 demo CSV (Free Edition).

The production bronze uses Structured Streaming + ``foreachBatch`` + ``MERGE``
(``databricks_notebooks/bronze_ingest.ipynb`` and ``Upsert_Job_Offers.py``).
This module runs the same mechanism on the demo Volume so both candidates are
compared in the same environment and with the same data:

* ``cloudFiles`` reads the CSV with the explicit schema of ``schema.json``;
* ``Upsert_Job_Offers.split_quarantine`` routes invalid rows;
* ``dedupe_batch`` keeps one row per key per microbatch;
* ``merge_sql`` builds ``MERGE WITH SCHEMA EVOLUTION``.

Upload ``Upsert_Job_Offers.py`` **and** its dependency
``Deduplicate_Job_Offers.py`` next to this module and add that folder to
``sys.path`` before importing. It is imported (not ``%run``) and ``run()`` is
called explicitly; see ``README.md``.

This file is a **library**: import it from a notebook and call ``run()``. There
is no ``__main__`` block, so executing it does nothing; running it as a notebook
does not define the module nor resolve its imports. Parameters are the explicit
arguments of ``run`` or notebook widgets, not Spark conf settings. In the
pipeline the same names live in ``configuration``, because Lakeflow exposes it
as ``spark.conf``; a notebook passes them as arguments or widgets.
"""
from __future__ import annotations

import builtins
import json
from typing import Any, Callable, Dict, List, Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DateType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

import Upsert_Job_Offers as upsert

CSV_TIMESTAMP_FORMAT = "yyyy-MM-dd HH:mm:ss.SSSSSS"

SOURCE_ORDER = ("indeed", "infojobs", "linkedin", "multi_site")

_SPARK_TYPES = {
    "string": StringType(),
    "long": LongType(),
    "double": DoubleType(),
    "boolean": BooleanType(),
    "timestamp": TimestampType(),
    "date": DateType(),
}


def _spark_session(spark: Optional[SparkSession] = None) -> SparkSession:
    if spark is not None:
        return spark
    active = SparkSession.getActiveSession()
    if active is not None:
        return active
    return SparkSession.builder.getOrCreate()


def _dbutils_widget_getter() -> Optional[Callable[[str], Optional[str]]]:
    """Return ``dbutils.widgets.get`` when a notebook runtime provides it.

    ``dbutils`` is injected into the session globals/builtins; an imported
    module may only reach it through the Databricks SDK. Both lookups are
    optional: outside a notebook there is no widget getter.
    """
    dbutils = getattr(builtins, "dbutils", None)
    if dbutils is None:
        try:
            from databricks.sdk.runtime import dbutils  # type: ignore[no-redef]
        except Exception:
            return None
    try:
        getter = getattr(getattr(dbutils, "widgets", None), "get", None)
    except Exception:
        return None
    if getter is None:
        return None

    def widget_getter(name: str) -> Optional[str]:
        try:
            return getter(name)
        except Exception:
            return None

    return widget_getter


def _resolve(
    name: str,
    explicit: Optional[str],
    widget_getter: Optional[Callable[[str], Optional[str]]],
    default: Optional[str] = None,
) -> str:
    """Resolve one parameter: explicit argument, then widget, then default.

    ``spark.conf`` is deliberately not a source: in a notebook these parameters
    are arguments (or widgets), not Spark configuration.
    """
    if explicit:
        return explicit
    if widget_getter is not None:
        try:
            widget_value = widget_getter(name)
        except Exception:
            widget_value = None
        if widget_value:
            return widget_value
    if default is not None:
        return default
    raise ValueError(
        f"Falta el parámetro '{name}'. Pásalo como argumento de "
        f"run({name}=...) o define el widget de notebook con "
        f"dbutils.widgets.text('{name}', '<valor>')."
    )


def load_schema(path: str) -> Dict[str, Dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    sources = document.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError(f"schema.json sin 'sources': {path}")
    missing = [source for source in SOURCE_ORDER if source not in sources]
    if missing:
        raise ValueError(f"schema.json sin las fuentes: {', '.join(missing)}")
    return sources


def _struct_type(columns: List[Dict[str, Any]]) -> StructType:
    fields = []
    for column in columns:
        spark_type = _SPARK_TYPES.get(column["spark_type"])
        if spark_type is None:
            raise ValueError(
                f"Tipo Spark no soportado para {column['name']}: "
                f"{column['spark_type']}"
            )
        fields.append(
            StructField(column["name"], spark_type, column.get("nullable", True))
        )
    return StructType(fields)


def _target_ddl(columns: List[Dict[str, Any]]) -> str:
    parts = [
        f"`{column['name']}` {column['spark_type']}" for column in columns
    ]
    parts.append("`_ingest_date` DATE")
    parts.append("`_source_file` STRING")
    return ", ".join(parts)


def ensure_schema(spark: SparkSession, target_schema: str) -> None:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {target_schema}")


def ensure_target_table(
    spark: SparkSession, target_table: str, columns: List[Dict[str, Any]]
) -> None:
    """Create the bronze target with the business columns and the controls.

    The production tables already exist; here the demo creates them once. The
    ``MERGE WITH SCHEMA EVOLUTION`` of ``Upsert_Job_Offers`` still adds any
    column that appears later.
    """
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS {target_table} "
        f"({_target_ddl(columns)}) USING DELTA"
    )


def _read_csv(
    spark: SparkSession, landing_root: str, source: str, spec: Dict[str, Any]
) -> DataFrame:
    path = f"{landing_root.rstrip('/')}/{source}"
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("cloudFiles.useNotifications", "false")
        .option("header", "true")
        .option("mode", "PERMISSIVE")
        .option("escape", '"')
        .option("timestampFormat", CSV_TIMESTAMP_FORMAT)
        .schema(_struct_type(spec["columns"]))
        .load(path)
        .withColumn("_ingest_date", F.current_date())
        .withColumn("_source_file", F.col("_metadata.file_path"))
    )


def make_upsert_handler(source: str, target_schema: str):
    """Same handler as ``bronze_ingest`` over the demo CSV batch."""
    target_table = f"{target_schema}.{source}"
    quarantine_table = f"{target_schema}.{source}_quarantine"
    batch_view = f"__batch_{source}"
    merge_sql = upsert.merge_sql(target_table, source, view_name=batch_view)

    def handler(batch_df: DataFrame, batch_id: int) -> None:
        if batch_df.isEmpty():
            return
        session = batch_df.sparkSession
        batch_df.persist()
        try:
            valid, quarantine = upsert.split_quarantine(batch_df, source)

            if not quarantine.isEmpty():
                files = (
                    [
                        row[0]
                        for row in batch_df.select(
                            upsert.SOURCE_FILE_COLUMN
                        ).distinct().collect()
                    ]
                    if upsert.SOURCE_FILE_COLUMN in batch_df.columns
                    else []
                )
                if files and session.catalog.tableExists(quarantine_table):
                    stored = (
                        session.read.table(quarantine_table)
                        .where(
                            F.col(upsert.SOURCE_FILE_COLUMN).isin(files)
                        )
                        .select(*upsert.QUARANTINE_IDENTITY_COLUMNS)
                    )
                else:
                    stored = quarantine.select(
                        *upsert.QUARANTINE_IDENTITY_COLUMNS
                    ).limit(0)
                new_rows = upsert.new_quarantine_rows(quarantine, stored)
                if not new_rows.isEmpty():
                    (
                        new_rows.write.format("delta")
                        .mode("append")
                        .option("mergeSchema", "true")
                        .saveAsTable(quarantine_table)
                    )

            deduped = upsert.dedupe_batch(valid, source)
            if deduped.isEmpty():
                return
            deduped.createOrReplaceTempView(batch_view)
            session.sql(merge_sql)
        finally:
            batch_df.unpersist()

    return handler


def run(
    spark: Optional[SparkSession] = None,
    *,
    landing_root: Optional[str] = None,
    target_schema: Optional[str] = None,
    schema_path: Optional[str] = None,
    checkpoint_root: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run the current mechanism once per source and wait for all streams.

    Each parameter comes from the explicit argument first and from the notebook
    widget with the same name otherwise; ``schema_path`` and ``checkpoint_root``
    fall back to their landing-root defaults. Required parameters raise a clear
    error when neither the argument nor the widget is present.
    """
    spark = _spark_session(spark)
    try:
        spark.conf.set("spark.sql.session.timeZone", "UTC")
    except Exception as exc:
        print(
            "Aviso: no se pudo fijar spark.sql.session.timeZone=UTC "
            f"({exc}). El runtime ya usa UTC por defecto; se continua."
        )

    widget_getter = _dbutils_widget_getter()
    landing_root = _resolve("landing_root", landing_root, widget_getter)
    target_schema = _resolve("target_schema", target_schema, widget_getter)
    schema_path = _resolve(
        "schema_path",
        schema_path,
        widget_getter,
        default=f"{landing_root.rstrip('/')}/schema.json",
    )
    checkpoint_root = _resolve(
        "checkpoint_root",
        checkpoint_root,
        widget_getter,
        default=f"{landing_root.rstrip('/')}/_checkpoints/current",
    )

    schema = load_schema(schema_path)
    ensure_schema(spark, target_schema)

    queries = []
    for source in SOURCE_ORDER:
        spec = schema[source]
        target_table = f"{target_schema}.{source}"
        ensure_target_table(spark, target_table, spec["columns"])
        query = (
            _read_csv(spark, landing_root, source, spec)
            .writeStream.option(
                "checkpointLocation", f"{checkpoint_root}/{source}/ckpt"
            )
            .queryName(f"current_{source}")
            .trigger(availableNow=True)
            .foreachBatch(make_upsert_handler(source, target_schema))
            .start()
        )
        queries.append((source, query))

    results = []
    for source, query in queries:
        query.awaitTermination()
        results.append(
            {
                "source": source,
                "query_id": query.id,
                "status": query.status["message"],
            }
        )
        print(f"Stream {source} finalizada: {query.status['message']}")
    return results
