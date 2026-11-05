"""Lakeflow declarative pipeline for the T-09 demo (Databricks Free Edition).

It reads the demo CSV files from a Unity Catalog Volume with Auto Loader,
splits valid and invalid rows with the same rules as
``databricks_notebooks/Upsert_Job_Offers.py`` (``missing_key`` wins over
``missing_scraped_at``), keeps the quarantine table and applies an ``AUTO CDC``
SCD1 flow per source keyed by the bronze contract.

It is **not** a replacement for the project pipeline: it runs only in Free
Edition, on the CSV extract, and with zero cost. The target tables are managed
by the pipeline (an ``AUTO CDC`` requirement), so they are new tables in a lab
schema, never the production ``bronze`` tables.

Required pipeline configuration (``configuration`` in the pipeline settings):

* ``landing_root``: Volume folder with ``<source>/<source>.csv`` and
  ``schema.json`` (for example ``/Volumes/workspace/tml_demo/landing``).
* ``target_schema``: lab schema for the demo tables (for example
  ``workspace.bronze_lab``).
* ``schema_path``: optional; defaults to ``<landing_root>/schema.json``.

``@dp.expect`` and ``dp.create_auto_cdc_flow`` are Databricks runtime
extensions, not part of open-source Apache Spark; their availability in Free
Edition is ``[NO VERIFICADO]`` until the demo runs (T-09b).
"""
from __future__ import annotations

from typing import Any, Dict, List

from pyspark import pipelines as dp
from pyspark.sql import DataFrame
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

#: The generator writes naive UTC timestamps with microseconds. ``escape`` is
#: required: the descriptions contain doubled double quotes and Spark's default
#: escape character (backslash) misaligns the CSV rows.
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


def _required_conf(name: str) -> str:
    value = spark.conf.get(name, None)
    if not value:
        raise ValueError(
            f"Falta la configuracion del pipeline '{name}'. "
            "Anade landing_root y target_schema en la configuracion del pipeline."
        )
    return value


def _load_schema(path: str) -> Dict[str, Dict[str, Any]]:
    import json

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


def _key_predicate(keys: List[str]) -> str:
    """SQL predicate: every key is non-NULL and not blank (same as bronze)."""
    return " AND ".join(
        f"(`{key}` IS NOT NULL AND trim(cast(`{key}` AS STRING)) <> '')"
        for key in keys
    )


def _capture_expression(capture_column: str):
    """Capture instant, tolerating malformed strings (same as the bronze)."""
    return F.try_to_timestamp(F.col(capture_column))


def _read_csv(source: str, spec: Dict[str, Any]) -> DataFrame:
    path = f"{LANDING_ROOT.rstrip('/')}/{source}"
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
    )


def _register_source(source: str, spec: Dict[str, Any]) -> None:
    business_columns = [column["name"] for column in spec["columns"]]
    capture_column = spec["capture_column"]
    capture = _capture_expression(capture_column)
    missing_key = F.lit(False)
    for key in spec["keys"]:
        missing_key = (
            missing_key
            | F.col(key).isNull()
            | (F.trim(F.col(key).cast("string")) == F.lit(""))
        )
    quarantine_reason = (
        F.when(missing_key, F.lit("missing_key"))
        .when(capture.isNull(), F.lit("missing_scraped_at"))
    )

    raw_table = f"{TARGET_SCHEMA}.{source}_raw"
    valid_table = f"{TARGET_SCHEMA}.{source}_valid"
    quarantine_table = f"{TARGET_SCHEMA}.{source}_quarantine"
    target_table = f"{TARGET_SCHEMA}.{source}"

    @dp.table(
        name=raw_table,
        comment=(
            f"CSV extract of {source} with the bronze control columns and "
            "the quarantine reason."
        ),
    )
    @dp.expect("has_valid_key", _key_predicate(spec["keys"]))
    @dp.expect("has_valid_scraped_at", "_scraped_ts IS NOT NULL")
    def _raw() -> DataFrame:
        return (
            _read_csv(source, spec)
            .withColumn("_ingest_date", F.current_date())
            .withColumn("_source_file", F.col("_metadata.file_path"))
            .withColumn(
                "_row_hash",
                F.sha2(
                    F.to_json(
                        F.struct(*[F.col(name) for name in business_columns])
                    ),
                    256,
                ),
            )
            .withColumn("_scraped_ts", capture)
            .withColumn("_quarantine_reason", quarantine_reason)
        )

    @dp.table(
        name=valid_table,
        comment=f"Valid rows of {source}: no quarantine reason.",
    )
    def _valid() -> DataFrame:
        return (
            spark.readStream.table(raw_table)
            .filter(F.col("_quarantine_reason").isNull())
            .drop("_quarantine_reason", "_row_hash")
        )

    @dp.table(
        name=quarantine_table,
        comment=(
            f"Invalid rows of {source}: missing_key wins over "
            "missing_scraped_at."
        ),
    )
    def _quarantine() -> DataFrame:
        return (
            spark.readStream.table(raw_table)
            .filter(F.col("_quarantine_reason").isNotNull())
            .withColumn("_quarantine_at", F.current_timestamp())
            .drop("_scraped_ts")
        )

    # No explicit schema: the AUTO CDC flow infers it from the valid source
    # minus ``except_column_list``. An explicit AUTO CDC target schema would
    # require the internal ``__START_AT`` / ``__END_AT`` columns.
    dp.create_streaming_table(
        name=target_table,
        comment=(
            f"SCD1 bronze contract of {source} from the demo CSV "
            "(managed by the pipeline)."
        ),
    )
    dp.create_auto_cdc_flow(
        target=target_table,
        source=valid_table,
        keys=list(spec["keys"]),
        # Capture instant first; _ingest_date only breaks exact ties, as in
        # Upsert_Job_Offers.merge_sql.
        sequence_by=F.struct(F.col("_scraped_ts"), F.col("_ingest_date")),
        stored_as_scd_type=1,
        except_column_list=["_scraped_ts"],
        name=f"{source}_scd1",
    )


# The quarantine fingerprint serializes timestamps with the session time zone:
# pinned to UTC so it is stable, as in bronze_ingest.ipynb.
spark.conf.set("spark.sql.session.timeZone", "UTC")

# ``spark.conf`` is legitimate here: Lakeflow exposes the pipeline
# ``configuration`` as Spark conf. In a notebook the same values are arguments
# (or widgets) of ``mechanism_current.run``, not Spark configuration.
LANDING_ROOT = _required_conf("landing_root")
TARGET_SCHEMA = _required_conf("target_schema")
SCHEMA_PATH = spark.conf.get(
    "schema_path", f"{LANDING_ROOT.rstrip('/')}/schema.json"
)
DEMO_SCHEMA = _load_schema(SCHEMA_PATH)

for _source in SOURCE_ORDER:
    _register_source(_source, DEMO_SCHEMA[_source])
