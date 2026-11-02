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

Configuration comes from Spark conf (set them in the notebook):
``landing_root``, ``target_schema``, optional ``schema_path`` and
``checkpoint_root``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

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


def _conf(spark: SparkSession, name: str, default: Optional[str] = None) -> str:
    value = spark.conf.get(name, None)
    if value:
        return value
    if default is not None:
        return default
    raise ValueError(
        f"Configura spark.conf '{name}' antes de llamar a run()."
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


def run(spark: Optional[SparkSession] = None) -> List[Dict[str, Any]]:
    """Run the current mechanism once per source and wait for all streams."""
    spark = _spark_session(spark)
    spark.conf.set("spark.sql.session.timeZone", "UTC")

    landing_root = _conf(spark, "landing_root")
    target_schema = _conf(spark, "target_schema")
    schema_path = _conf(
        spark, "schema_path", f"{landing_root.rstrip('/')}/schema.json"
    )
    checkpoint_root = _conf(
        spark,
        "checkpoint_root",
        f"{landing_root.rstrip('/')}/_checkpoints/current",
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


if __name__ == "__main__":
    run()
