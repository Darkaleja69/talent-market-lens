"""Export of the Gold layer for the Power BI report (Import mode).

This module owns the report export contract, deliberately separate from the
public web export in ``Prepare_Web_Export``:

- ``PBI_*_COLUMNS`` fix the full column contract of the six report tables:
  ``fact_offers`` (31 columns: the 29 physical Gold columns plus the derived
  ``GeoCountry``/``GeoRegion``) and the five dimensions.
- ``project_*`` helpers select exactly those columns. ``fact_offers`` also
  renders ``skills`` as comma-separated text (Gold stores ``ARRAY<string>``
  while the semantic model declares ``string``) and derives the geography with
  the ``REGION_MAP`` catalog and country normalization owned by
  ``Prepare_Web_Export``, using the raw ``location_country``/``location_region``
  pair.
- ``build_meta``/``meta_json`` produce the report ``meta.json`` (always
  ``mode: full``, ``audience: pbi``, data date, table counts and size). The
  25 MiB rule of the public export does not apply: the report always needs the
  full detail.
- ``build_pbi_export`` projects the six Gold tables and ``write_pbi_export``
  writes one fixed-name Parquet per table plus ``meta.json`` (idempotent,
  ``coalesce(1)``), reusing the file helpers of ``Prepare_Web_Export``.

The geography and the file helpers are shared with the public export on
purpose: both exports must show the same countries/regions and keep the same
idempotent write semantics. Only the column contract and ``meta.json`` differ.

Only the standard library is imported at module level (plus the sibling
``Prepare_Web_Export`` module), so the contract can be imported and tested
without Spark. PySpark is imported lazily inside the functions that need it.
"""
from __future__ import annotations

import Prepare_Web_Export as web_export

# ---------------------------------------------------------------------------
# Report export contract (columns per table)
# ---------------------------------------------------------------------------

# Gold fact_offers as consumed by the semantic model: the 29 physical columns
# plus the two geography columns derived in the M partition (and here). Order
# follows the model inventory of spec 009 (T-02).
PBI_FACT_OFFERS_COLUMNS = [
    "job_id",
    "job_url",
    "title",
    "company_name",
    "location_city",
    "location_region",
    "location_country",
    "posted_date",
    "work_mode",
    "is_salary_available",
    "salary_currency",
    "salary_period",
    "experience_level",
    "description_clean",
    "skills",
    "source_scraper",
    "employment_type",
    "role_category",
    "SalaryMinAnnual_EUR",
    "SalaryMaxAnnual_EUR",
    "SalaryMidAnnual_EUR",
    "PostedYearMonth",
    "WorkModeBucket",
    "salary_quality",
    "skills_source",
    "experience_level_source",
    "posted_date_raw",
    "posted_date_source",
    "GeoCountry",
    "GeoRegion",
    "IsValidPostingDate",
]

PBI_FACT_OFFER_SKILLS_COLUMNS = ["JobID", "Skill"]

PBI_DIM_SKILL_LIST_COLUMNS = ["SkillName", "SkillCategory"]

# Exactly the columns produced by Prepare_Gold.dim_calendar.
PBI_DIM_CALENDAR_COLUMNS = [
    "Date",
    "DateKey",
    "Year",
    "MonthNumber",
    "MonthName",
    "ShortMonth",
    "YearMonth",
    "Quarter",
    "YearQuarter",
    "WeekISO",
    "Day",
    "WeekDay",
    "DayName",
    "IsWeekend",
]

# Exactly the columns stored in gold.companies_complete_catalog.
PBI_DIM_COMPANIES_COLUMNS = [
    "company_name",
    "company_industry",
    "company_description",
    "company_size",
    "source",
    "company_rating",
    "company_review_count",
]

# Exactly the columns produced by Prepare_Gold.dim_currency.
PBI_DIM_CURRENCY_COLUMNS = [
    "CurrencyCode",
    "CurrencyName",
    "AltCode",
    "UnitsPerEUR",
    "FXSnapshotDate",
]

# Fixed table set and file names of the export (one Parquet per table).
PBI_TABLES = (
    "fact_offers",
    "fact_offer_skills",
    "dim_skill_list",
    "dim_calendar",
    "dim_companies",
    "dim_currency",
)

# Contract version of the report meta.json and the per-target default prefix.
# The bundle variable pbi_export_prefix overrides it (gold/pbi_export_dev).
PBI_EXPORT_VERSION = 1
PBI_MODE = "full"
PBI_AUDIENCE = "pbi"
DEFAULT_PBI_EXPORT_PREFIX = "gold/pbi_export"


def export_dest(storage_account, container,
                prefix=DEFAULT_PBI_EXPORT_PREFIX) -> str:
    """Build the ADLS URI of the report export for a bundle target.

    ``prefix`` is the per-target bundle variable (``gold/pbi_export`` in prod,
    ``gold/pbi_export_dev`` in dev). No catalog is hardcoded: the caller reads
    Gold from its own ``catalog``/``gold_schema`` parameters and only the export
    location is fixed here.
    """
    if not storage_account or not container:
        raise ValueError(
            "storage_account y container son obligatorios para el export")
    return (f"abfss://{container}@{storage_account}.dfs.core.windows.net/"
            f"{str(prefix).strip().strip('/')}")


# ---------------------------------------------------------------------------
# meta.json (pure, no Spark)
# ---------------------------------------------------------------------------


def build_meta(table_counts, data_date, generated_at=None, size_bytes=0,
               export_version=PBI_EXPORT_VERSION) -> dict:
    """Build the ``meta.json`` payload of the report export.

    Unlike the public export, ``mode`` is always ``"full"`` (the 25 MiB rule
    does not apply) and the payload declares ``audience: "pbi"``.
    ``generated_at`` defaults to now (UTC) and ``data_date`` accepts a date, a
    datetime or a "YYYY-MM-DD" string.
    """
    return {
        "export_version": export_version,
        "data_date": web_export._as_data_date(data_date),
        "generated_at": web_export._as_generated_at(generated_at),
        "mode": PBI_MODE,
        "audience": PBI_AUDIENCE,
        "tables": dict(table_counts),
        "size_bytes": int(size_bytes),
    }


def meta_json(meta) -> str:
    """Serialize ``meta`` deterministically (same format as the public export)."""
    return web_export.meta_json(meta)


# ---------------------------------------------------------------------------
# Spark projection to the report contract
# ---------------------------------------------------------------------------


def _skills_text():
    """Gold ``ARRAY<string>`` -> comma-separated text (null/empty -> "")."""
    from pyspark.sql import functions as F

    return F.concat_ws(", ", F.coalesce(F.col("skills"), F.array()))


def project_fact_offers(df, region_map):
    """Project Gold fact_offers to the 31-column report contract.

    ``GeoCountry`` replicates the M country normalization and ``GeoRegion``
    left-joins the raw, trimmed ``location_country``/``location_region`` pair
    against the shared ``REGION_MAP`` dimension with ``"(Other)"`` as fallback,
    exactly like the public export. ``skills`` is rendered as text because the
    semantic model declares it ``string``.

    Raises ValueError listing the missing columns instead of writing an
    incomplete export.
    """
    from pyspark.sql import functions as F

    required = [c for c in PBI_FACT_OFFERS_COLUMNS
                if c not in ("GeoCountry", "GeoRegion")]
    web_export._require_columns(df, required, "project_pbi_fact_offers")

    trimmed_country = web_export._trim_whitespace(F.col("location_country"))
    geo_country_col = (
        F.when(trimmed_country.isNull()
               | (trimmed_country == "")
               | (trimmed_country == "(Not specified)"),
               F.lit("(Not specified)"))
         .when(trimmed_country.isin("CA", "FL", "PA"), F.lit("United States"))
         .when(trimmed_country == "Alemania", F.lit("Germany"))
         .when(trimmed_country.isin("Austria y Suiza", "Austria and Switzerland"),
               F.lit("Austria"))
         .when(trimmed_country == "Oriente Medio y África",
               F.lit("Middle East & Africa"))
         .otherwise(trimmed_country)
    )
    prepared = (df
                .withColumn("__country_trim", trimmed_country)
                .withColumn("__region_trim",
                            web_export._trim_whitespace(F.col("location_region")))
                .withColumn("GeoCountry", geo_country_col)
                .withColumn("skills", _skills_text()))
    joined = prepared.join(
        region_map,
        on=((F.col("__country_trim") == region_map["SourceCountry"])
            & (F.col("__region_trim") == region_map["SourceRegion"])),
        how="left")
    return (joined
            .withColumn("GeoRegion",
                        F.coalesce(F.col("TargetRegion"), F.lit("(Other)")))
            .select(*PBI_FACT_OFFERS_COLUMNS))


def project_fact_offer_skills(df):
    """Project the offer-skill bridge to exactly (JobID, Skill)."""
    web_export._require_columns(df, PBI_FACT_OFFER_SKILLS_COLUMNS,
                                "project_pbi_fact_offer_skills")
    return df.select(*PBI_FACT_OFFER_SKILLS_COLUMNS)


def project_dim_skill_list(df):
    """Project the skill catalog to exactly (SkillName, SkillCategory)."""
    web_export._require_columns(df, PBI_DIM_SKILL_LIST_COLUMNS,
                                "project_pbi_dim_skill_list")
    return df.select(*PBI_DIM_SKILL_LIST_COLUMNS)


def project_dim_calendar(df):
    """Project the calendar to exactly the 14 report columns."""
    web_export._require_columns(df, PBI_DIM_CALENDAR_COLUMNS,
                                "project_pbi_dim_calendar")
    return df.select(*PBI_DIM_CALENDAR_COLUMNS)


def project_dim_companies(df):
    """Project companies_complete_catalog to its 7 report columns."""
    web_export._require_columns(df, PBI_DIM_COMPANIES_COLUMNS,
                                "project_pbi_dim_companies")
    return df.select(*PBI_DIM_COMPANIES_COLUMNS)


def project_dim_currency(df):
    """Project dim_currency to its 5 report columns."""
    web_export._require_columns(df, PBI_DIM_CURRENCY_COLUMNS,
                                "project_pbi_dim_currency")
    return df.select(*PBI_DIM_CURRENCY_COLUMNS)


# ---------------------------------------------------------------------------
# Build and write the export
# ---------------------------------------------------------------------------


def build_pbi_export(spark, fact_offers, fact_offer_skills, dim_skill_list,
                     dim_calendar, dim_companies, dim_currency) -> dict:
    """Project the six already-built Gold tables to the report contract.

    Returns the ``{table: DataFrame}`` dict with the fixed ``PBI_TABLES`` names
    and columns (``fact_offers`` adds ``GeoCountry``/``GeoRegion`` and renders
    ``skills`` as text).
    """
    region_map = web_export.region_map_df(spark)
    return {
        "fact_offers": project_fact_offers(fact_offers, region_map),
        "fact_offer_skills": project_fact_offer_skills(fact_offer_skills),
        "dim_skill_list": project_dim_skill_list(dim_skill_list),
        "dim_calendar": project_dim_calendar(dim_calendar),
        "dim_companies": project_dim_companies(dim_companies),
        "dim_currency": project_dim_currency(dim_currency),
    }


def collect_export_stats(tables) -> tuple:
    """Return ``(table_counts, data_date)`` for the report ``meta.json``.

    ``table_counts`` counts rows per table and ``data_date`` is the max
    ``posted_date`` of ``fact_offers`` as a ``datetime.date``, or ``None`` when
    the table is empty or has no posting dates.
    """
    from pyspark.sql import functions as F

    table_counts = {name: int(df.count()) for name, df in tables.items()}
    data_date = (tables["fact_offers"]
                 .select(F.max("posted_date")).collect()[0][0])
    return table_counts, data_date


def write_pbi_export(spark, tables, dest, meta=None, size_provider=None):
    """Write the report export to ``dest``: one Parquet per table (+ meta.json).

    Final layout, fixed names and idempotent::

        <dest>/fact_offers.parquet
        <dest>/fact_offer_skills.parquet
        <dest>/dim_skill_list.parquet
        <dest>/dim_calendar.parquet
        <dest>/dim_companies.parquet
        <dest>/dim_currency.parquet
        <dest>/meta.json            (only when ``meta`` is provided)

    Each table is written with ``coalesce(1)`` into a temporary directory inside
    ``dest`` and its single ``part-*`` file is renamed through the shared file
    helpers (``dbutils.fs`` on Databricks, Hadoop locally). A previous final
    file is deleted before the rename and the temporary directory is removed,
    so re-running never duplicates rows nor leaves temporaries.

    ``size_bytes`` sums the real sizes of the six final files; pass
    ``size_provider`` (called without arguments) to override it. When ``meta``
    is not ``None``, a copy is updated with the real size, forced to
    ``mode: full``/``audience: pbi``, written to ``meta.json`` and returned;
    otherwise ``None`` is returned.
    """
    dest_uri = web_export._as_uri(dest)

    for name, df in tables.items():
        temp_uri = f"{dest_uri}/__tmp_{name}"
        final_uri = f"{dest_uri}/{name}.parquet"
        web_export._fs_delete(spark, final_uri)
        web_export._fs_delete(spark, temp_uri, recursive=True)
        try:
            web_export._write_parquet(df, temp_uri)
            part_path = web_export._single_part_file(spark, temp_uri)
            web_export._fs_rename(spark, part_path, final_uri)
            web_export._delete_checksum(spark, final_uri)
        finally:
            web_export._fs_delete(spark, temp_uri, recursive=True)

    if meta is None:
        return None

    if size_provider is not None:
        size_bytes = int(size_provider())
    else:
        size_bytes = sum(
            web_export._fs_size(spark, f"{dest_uri}/{name}.parquet")
            for name in tables)

    updated = dict(meta)
    updated["size_bytes"] = size_bytes
    updated["mode"] = PBI_MODE
    updated["audience"] = PBI_AUDIENCE
    web_export._fs_write_text(spark, f"{dest_uri}/meta.json",
                              meta_json(updated))
    return updated
