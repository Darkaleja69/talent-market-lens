"""Export of the Gold layer for the public web dashboard.

This module owns the Parquet + ``meta.json`` export consumed by the static site
under ``docs/dashboard``:

- ``WEB_*_COLUMNS`` fix the public column contract of each exported table and
  the ``project_*`` helpers select exactly those columns (adding or removing a
  column is a deliberate contract change).
- ``build_meta``/``meta_json`` produce the deterministic freshness payload
  (version, data date, generated at, mode, counts, size) and
  ``export_exceeds_limit`` decides full vs aggregated against the configurable
  25 MiB limit.
- ``build_web_export`` projects the four Gold tables and ``write_web_export``
  writes one Parquet file per table plus ``meta.json`` (idempotent). The file
  operations route through ``dbutils.fs`` on Databricks (Unity Catalog
  credentials, any access mode) and through the Hadoop FileSystem locally.
- ``geo_country`` ports the M partition of ``Fact_Offers`` (trim, US states
  used as country, language aliases, empty -> ``(Not specified)``).
- ``REGION_MAP`` is the full ``Dim_RegionMap`` catalog; ``region_map_df`` turns
  it into a Spark dimension and ``geo_region`` maps exact ``(country, region)``
  pairs to their target region with ``(Other)`` as fallback.

``Dim_Geo`` and that M partition live only in the semantic model (a documented
exception in ``Prepare_Gold.py``), so they are ported here on purpose to keep
the export and the map independent from Power BI. If the model mapping changes,
this constant is updated together with its fidelity test.

Only the standard library is imported at module level, so the contract and the
geography can be imported and tested without Spark. PySpark is imported lazily
inside the functions that need it.

Whitespace semantics: the Spark projection trims with ``_trim_whitespace``
(Java ``\\s``: space, tab, LF, CR, FF and VT) instead of Spark ``trim`` (which
only removes the ASCII space), so DataFrame results match the pure helpers on
the whitespace the scrapers can introduce. Exotic Unicode blanks (NBSP,
em-space) are not normalized by the Spark regex and are not expected in the
data (scrapers deliver pre-cleaned text); note Python's ``str.strip()`` is
broader and would remove them, so they must not reach the export.
"""
from __future__ import annotations

import datetime as dt
import json
import os

# ---------------------------------------------------------------------------
# Web export contract (public columns per table)
# ---------------------------------------------------------------------------

# Gold fact_offers projected for the web. Excluded on purpose (internal or
# unused by the views): description_clean, skills, salary_quality,
# skills_source, experience_level_source, posted_date_raw, posted_date_source,
# salary_currency and salary_period.
WEB_FACT_COLUMNS = [
    "job_id",
    "job_url",
    "title",
    "company_name",
    "location_city",
    "location_region",
    "location_country",
    "GeoCountry",
    "GeoRegion",
    "posted_date",
    "PostedYearMonth",
    "IsValidPostingDate",
    "WorkModeBucket",
    "SalaryMinAnnual_EUR",
    "SalaryMaxAnnual_EUR",
    "SalaryMidAnnual_EUR",
    "experience_level",
    "role_category",
    "employment_type",
    "source_scraper",
]

WEB_OFFER_SKILL_COLUMNS = ["JobID", "Skill"]

WEB_SKILL_LIST_COLUMNS = ["SkillName", "SkillCategory"]

# Exactly the columns produced by Prepare_Gold.dim_calendar.
WEB_CALENDAR_COLUMNS = [
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

# Contract version of meta.json and default size limit (configurable and
# orientative; the export only switches to aggregated when strictly larger).
EXPORT_VERSION = 1
DEFAULT_MAX_EXPORT_BYTES = 25 * 1024 * 1024  # 25 MiB

# ---------------------------------------------------------------------------
# Reference data (equivalent to the Power BI table Dim_RegionMap)
# ---------------------------------------------------------------------------

# Exact copy of every row of the Dim_RegionMap #table(...) block, in model
# order, including the country-level "(Country)" fallback rows and the
# duplicated ("Spain", "Cantabria", "Cantabria") row that exists in the model.
REGION_MAP = (
    # Spain
    ("Spain", "Madrid", "Madrid"),
    ("Spain", "Catalonia", "Catalonia"),
    ("Spain", "Valencia", "Valencia"),
    ("Spain", "Sevilla", "Sevilla"),
    ("Spain", "Basque Country", "Basque Country"),
    ("Spain", "Málaga", "Málaga"),
    ("Spain", "Zaragoza", "Zaragoza"),
    ("Spain", "Granada", "Granada"),
    ("Spain", "Pontevedra", "Pontevedra"),
    ("Spain", "Murcia", "Murcia"),
    ("Spain", "Islas Baleares", "Islas Baleares"),
    ("Spain", "Las Palmas", "Las Palmas"),
    ("Spain", "Santa Cruz de Tenerife", "Santa Cruz de Tenerife"),
    ("Spain", "A Coruña", "A Coruña"),
    ("Spain", "Asturias", "Asturias"),
    ("Spain", "Cantabria", "Cantabria"),
    ("Spain", "Navarra", "Navarra"),
    ("Spain", "La Rioja", "La Rioja"),
    ("Spain", "Valladolid", "Valladolid"),
    ("Spain", "Alicante", "Alicante"),
    ("Spain", "Córdoba", "Córdoba"),
    ("Spain", "Salamanca", "Salamanca"),
    ("Spain", "Burgos", "Burgos"),
    ("Spain", "Pozuelo de Alarcón", "Madrid"),
    ("Spain", "Alcobendas", "Madrid"),
    ("Spain", "Moncloa-Aravaca", "Madrid"),
    ("Spain", "Boadilla del Monte", "Madrid"),
    ("Spain", "Moralzarzal", "Madrid"),
    ("Spain", "El Prat de Llobregat", "Catalonia"),
    ("Spain", "Esplugues de Llobregat", "Catalonia"),
    ("Spain", "Hospitalet de Llobregat", "Catalonia"),
    ("Spain", "Sant Just Desvern", "Catalonia"),
    ("Spain", "Sant Vicenç dels Horts", "Catalonia"),
    ("Spain", "Terrassa", "Catalonia"),
    ("Spain", "Sabadell", "Catalonia"),
    ("Spain", "Amorebieta-Etxano", "Basque Country"),
    ("Spain", "Amorebieta", "Basque Country"),
    ("Spain", "Getxo", "Basque Country"),
    ("Spain", "Mungia", "Basque Country"),
    ("Spain", "Igorre", "Basque Country"),
    ("Spain", "Mallabia", "Basque Country"),
    ("Spain", "Ibarra", "Basque Country"),
    ("Spain", "Valencian Community", "Valencia"),
    ("Spain", "Comunidad Valenciana", "Valencia"),
    ("Spain", "Andalusia", "Sevilla"),
    ("Spain", "Andalucía", "Sevilla"),
    ("Spain", "Galicia", "A Coruña"),
    ("Spain", "Castile and Leon", "Valladolid"),
    ("Spain", "Canary Islands", "Las Palmas"),
    ("Spain", "Castile-La Mancha", "Toledo"),
    ("Spain", "Cantabria", "Cantabria"),
    # Ireland
    ("Ireland", "Dublin", "Dublin"),
    ("Ireland", "Dublín", "Dublin"),
    ("Ireland", "County Dublin", "Dublin"),
    ("Ireland", "Fingal", "Dublin"),
    ("Ireland", "Dún Laoghaire-Rathdown", "Dublin"),
    ("Ireland", "Cork", "Cork"),
    ("Ireland", "County Cork", "Cork"),
    ("Ireland", "Galway", "Galway"),
    ("Ireland", "County Galway", "Galway"),
    ("Ireland", "Limerick", "Limerick"),
    ("Ireland", "County Limerick", "Limerick"),
    ("Ireland", "Waterford", "Waterford"),
    ("Ireland", "County Waterford", "Waterford"),
    ("Ireland", "Condado de Waterford", "Waterford"),
    ("Ireland", "Kilkenny", "Kilkenny"),
    ("Ireland", "County Kilkenny", "Kilkenny"),
    ("Ireland", "Sligo", "Sligo"),
    ("Ireland", "County Sligo", "Sligo"),
    ("Ireland", "Louth", "Louth"),
    ("Ireland", "County Louth", "Louth"),
    ("Ireland", "Westmeath", "Westmeath"),
    ("Ireland", "County Westmeath", "Westmeath"),
    ("Ireland", "Kerry", "Kerry"),
    ("Ireland", "County Kerry", "Kerry"),
    ("Ireland", "Clare", "Clare"),
    ("Ireland", "Co. Clare", "Clare"),
    ("Ireland", "County Clare", "Clare"),
    ("Ireland", "Shannon", "Clare"),
    ("Ireland", "Wexford", "Wexford"),
    ("Ireland", "County Wexford", "Wexford"),
    ("Ireland", "Wicklow", "Wicklow"),
    ("Ireland", "County Wicklow", "Wicklow"),
    ("Ireland", "Condado de Wicklow", "Wicklow"),
    ("Ireland", "Carlow", "Carlow"),
    ("Ireland", "County Carlow", "Carlow"),
    ("Ireland", "Kildare", "Kildare"),
    ("Ireland", "County Kildare", "Kildare"),
    ("Ireland", "Condado de Kildare", "Kildare"),
    ("Ireland", "Mayo", "Mayo"),
    ("Ireland", "County Mayo", "Mayo"),
    ("Ireland", "Longford", "Longford"),
    ("Ireland", "County Longford", "Longford"),
    ("Ireland", "Monaghan", "Monaghan"),
    ("Ireland", "County Monaghan", "Monaghan"),
    ("Ireland", "Roscommon", "Roscommon"),
    ("Ireland", "County Roscommon", "Roscommon"),
    ("Ireland", "Meath", "Meath"),
    ("Ireland", "County Meath", "Meath"),
    ("Ireland", "Condado de Meath", "Meath"),
    ("Ireland", "Tipperary", "Tipperary"),
    ("Ireland", "County Tipperary", "Tipperary"),
    ("Ireland", "Offaly", "Offaly"),
    ("Ireland", "County Offaly", "Offaly"),
    ("Ireland", "Leitrim", "Leitrim"),
    ("Ireland", "County Leitrim", "Leitrim"),
    ("Ireland", "Laois", "Laois"),
    ("Ireland", "County Laois", "Laois"),
    ("Ireland", "Donegal", "Donegal"),
    ("Ireland", "County Donegal", "Donegal"),
    ("Ireland", "Cavan", "Cavan"),
    ("Ireland", "County Cavan", "Cavan"),
    # Netherlands
    ("Netherlands", "North Holland", "North Holland"),
    ("Netherlands", "Holanda Septentrional", "North Holland"),
    ("Netherlands", "Noord-Holland", "North Holland"),
    ("Netherlands", "Amsterdam", "North Holland"),
    ("Netherlands", "South Holland", "South Holland"),
    ("Netherlands", "Holanda Meridional", "South Holland"),
    ("Netherlands", "Zuid-Holland", "South Holland"),
    ("Netherlands", "Utrecht", "Utrecht"),
    ("Netherlands", "Gelderland", "Gelderland"),
    ("Netherlands", "Flevoland", "Flevoland"),
    ("Netherlands", "Flevolanda", "Flevoland"),
    ("Netherlands", "North Brabant", "North Brabant"),
    ("Netherlands", "Noord-Brabant", "North Brabant"),
    ("Netherlands", "Groningen", "Groningen"),
    ("Netherlands", "Limburg", "Limburg"),
    ("Netherlands", "Overijssel", "Overijssel"),
    ("Netherlands", "Friesland", "Friesland"),
    ("Netherlands", "Zeeland", "Zeeland"),
    ("Netherlands", "Drenthe", "Drenthe"),
    # Switzerland
    ("Switzerland", "Zurich", "Zurich"),
    ("Switzerland", "Zürich", "Zurich"),
    ("Switzerland", "Zúrich", "Zurich"),
    ("Switzerland", "Geneva", "Geneva"),
    ("Switzerland", "Ginebra", "Geneva"),
    ("Switzerland", "Genf", "Geneva"),
    ("Switzerland", "Thônex", "Geneva"),
    ("Switzerland", "Basel", "Basel-Stadt"),
    ("Switzerland", "Basel-Stadt", "Basel-Stadt"),
    ("Switzerland", "Basilea", "Basel-Stadt"),
    ("Switzerland", "Bern", "Bern"),
    ("Switzerland", "Berne", "Bern"),
    ("Switzerland", "Vaud", "Vaud"),
    ("Switzerland", "Lausanne", "Vaud"),
    ("Switzerland", "Lucerne", "Lucerne"),
    ("Switzerland", "Lucerna", "Lucerne"),
    ("Switzerland", "Luzern", "Lucerne"),
    ("Switzerland", "St. Gallen", "St. Gallen"),
    ("Switzerland", "St Gallen", "St. Gallen"),
    ("Switzerland", "San Galo", "St. Gallen"),
    ("Switzerland", "Ticino", "Ticino"),
    ("Switzerland", "Tesino", "Ticino"),
    ("Switzerland", "Zug", "Zug"),
    ("Switzerland", "Fribourg", "Fribourg"),
    ("Switzerland", "Friburgo", "Fribourg"),
    ("Switzerland", "Neuchâtel", "Neuchâtel"),
    ("Switzerland", "Neuchatel", "Neuchâtel"),
    ("Switzerland", "Aargau", "Aargau"),
    ("Switzerland", "Argovia", "Aargau"),
    ("Switzerland", "Schaffhausen", "Schaffhausen"),
    ("Switzerland", "Escafusa", "Schaffhausen"),
    ("Switzerland", "Schwyz", "Schwyz"),
    ("Switzerland", "Glarus", "Glarus"),
    ("Switzerland", "Thurgau", "Thurgau"),
    ("Switzerland", "Turgovia", "Thurgau"),
    ("Switzerland", "Obwalden", "Obwalden"),
    ("Switzerland", "Solothurn", "Solothurn"),
    # United States
    ("United States", "AL", "Alabama"),
    ("United States", "AK", "Alaska"),
    ("United States", "AZ", "Arizona"),
    ("United States", "AR", "Arkansas"),
    ("United States", "CA", "California"),
    ("United States", "CO", "Colorado"),
    ("United States", "CT", "Connecticut"),
    ("United States", "DE", "Delaware"),
    ("United States", "DC", "District of Columbia"),
    ("United States", "FL", "Florida"),
    ("United States", "GA", "Georgia"),
    ("United States", "HI", "Hawaii"),
    ("United States", "ID", "Idaho"),
    ("United States", "IL", "Illinois"),
    ("United States", "IN", "Indiana"),
    ("United States", "IA", "Iowa"),
    ("United States", "KS", "Kansas"),
    ("United States", "KY", "Kentucky"),
    ("United States", "LA", "Louisiana"),
    ("United States", "ME", "Maine"),
    ("United States", "MD", "Maryland"),
    ("United States", "MA", "Massachusetts"),
    ("United States", "MI", "Michigan"),
    ("United States", "MN", "Minnesota"),
    ("United States", "MS", "Mississippi"),
    ("United States", "MO", "Missouri"),
    ("United States", "MT", "Montana"),
    ("United States", "NE", "Nebraska"),
    ("United States", "NV", "Nevada"),
    ("United States", "NH", "New Hampshire"),
    ("United States", "NJ", "New Jersey"),
    ("United States", "NM", "New Mexico"),
    ("United States", "NY", "New York"),
    ("United States", "NC", "North Carolina"),
    ("United States", "ND", "North Dakota"),
    ("United States", "OH", "Ohio"),
    ("United States", "OK", "Oklahoma"),
    ("United States", "OR", "Oregon"),
    ("United States", "PA", "Pennsylvania"),
    ("United States", "RI", "Rhode Island"),
    ("United States", "SC", "South Carolina"),
    ("United States", "SD", "South Dakota"),
    ("United States", "TN", "Tennessee"),
    ("United States", "TX", "Texas"),
    ("United States", "UT", "Utah"),
    ("United States", "VT", "Vermont"),
    ("United States", "VA", "Virginia"),
    ("United States", "WA", "Washington"),
    ("United States", "WV", "West Virginia"),
    ("United States", "WI", "Wisconsin"),
    ("United States", "WY", "Wyoming"),
    ("United States", "PR", "Puerto Rico"),
    # Country-level fallback rows
    ("Spain", "Spain", "(Country)"),
    ("Ireland", "Ireland", "(Country)"),
    ("Netherlands", "Netherlands", "(Country)"),
    ("Switzerland", "Switzerland", "(Country)"),
    ("United States", "United States", "(Country)"),
    ("Europe", "Europe", "(Country)"),
)

# Exact pair lookup used by geo_region. The duplicated Cantabria pair in the
# model collapses safely in a dict.
_REGION_LOOKUP = {
    (country, region): target
    for country, region, target in REGION_MAP
}


def _text(value) -> str:
    """M ``try Text.Trim(value) otherwise ""``: null becomes empty, then trim."""
    if value is None:
        return ""
    return str(value).strip()


def geo_country(value) -> str:
    """Normalize a location_country value exactly like the model M partition.

    Empty or ``(Not specified)`` -> ``(Not specified)``; the US states used as
    country in the source (``CA``/``FL``/``PA``) -> ``United States``;
    ``Alemania`` -> ``Germany``; ``Austria y Suiza``/``Austria and
    Switzerland`` -> ``Austria``; ``Oriente Medio y África`` -> ``Middle East &
    Africa``; anything else is kept as trimmed. Comparisons are exact (no case
    folding).
    """
    country = _text(value)
    if country == "" or country == "(Not specified)":
        return "(Not specified)"
    if country in ("CA", "FL", "PA"):
        return "United States"
    if country == "Alemania":
        return "Germany"
    if country in ("Austria y Suiza", "Austria and Switzerland"):
        return "Austria"
    if country == "Oriente Medio y África":
        return "Middle East & Africa"
    return country


def geo_region(country, region) -> str:
    """Map an exact trimmed ``(country, region)`` pair against REGION_MAP.

    Equivalent to the model left merge against Dim_RegionMap followed by
    ``TargetRegion{0} otherwise "(Other)"``: catalog pairs return their target
    region (including the country-level ``(Country)`` fallback) and unknown
    pairs return ``(Other)``.
    """
    c = _text(country)
    r = _text(region)
    return _REGION_LOOKUP.get((c, r), "(Other)")


# ---------------------------------------------------------------------------
# meta.json and size control (pure, no Spark)
# ---------------------------------------------------------------------------


def export_exceeds_limit(size_bytes, limit_bytes=DEFAULT_MAX_EXPORT_BYTES) -> bool:
    """True only when the export is strictly larger than the limit.

    ``limit_bytes`` is configurable and orientative (25 MiB by default); at the
    exact threshold the export still fits and stays ``full``.
    """
    return size_bytes > limit_bytes


def _as_data_date(data_date) -> str:
    """Normalize a date, datetime or string to the "YYYY-MM-DD" contract."""
    if isinstance(data_date, dt.datetime):
        return data_date.date().isoformat()
    if isinstance(data_date, dt.date):
        return data_date.isoformat()
    return str(data_date)


def _as_generated_at(generated_at) -> str:
    """ISO-8601 UTC ending in Z (seconds precision).

    ``None`` means now; naive datetimes are assumed UTC and aware ones are
    converted to UTC; strings pass through unchanged.
    """
    if generated_at is None:
        generated_at = dt.datetime.now(dt.timezone.utc)
    if isinstance(generated_at, dt.datetime):
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=dt.timezone.utc)
        else:
            generated_at = generated_at.astimezone(dt.timezone.utc)
        return (generated_at.replace(microsecond=0)
                .isoformat().replace("+00:00", "Z"))
    return str(generated_at)


def build_meta(table_counts, source_counts, data_date, generated_at=None,
               size_bytes=0, mode=None, export_version=EXPORT_VERSION) -> dict:
    """Build the ``meta.json`` payload of the export contract (plan 5.4).

    ``mode`` defaults to ``"aggregated"`` only when the export exceeds the size
    limit, otherwise ``"full"``; pass it explicitly to override.
    ``generated_at`` defaults to now (UTC) and ``data_date`` accepts a date, a
    datetime or a "YYYY-MM-DD" string.
    """
    if mode is None:
        mode = "aggregated" if export_exceeds_limit(size_bytes) else "full"
    return {
        "export_version": export_version,
        "data_date": _as_data_date(data_date),
        "generated_at": _as_generated_at(generated_at),
        "mode": mode,
        "tables": dict(table_counts),
        "sources": dict(source_counts),
        "size_bytes": int(size_bytes),
    }


def meta_json(meta) -> str:
    """Serialize ``meta`` deterministically (sorted keys, no ASCII escapes)."""
    return json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Spark projection to the web contract
# ---------------------------------------------------------------------------


def _require_columns(df, columns, table: str) -> None:
    """Fail loudly when the source table does not match the contract."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(
            f"{table}: faltan columnas de origen en el DataFrame -> {missing}")


def _trim_whitespace(col):
    """Trim Java ``\\s`` whitespace: space, tab, LF, CR, FF and VT.

    Spark ``F.trim`` only removes the ASCII space, so it diverges from
    Python's ``str.strip()`` on tabs/newlines; ``regexp_replace`` aligns the
    DataFrame projection with the pure geography helpers.
    """
    from pyspark.sql import functions as F

    return F.regexp_replace(col, r"^\s+|\s+$", "")


def region_map_df(spark):
    """Dim_RegionMap as a Spark dimension (SourceCountry, SourceRegion, Target).

    The model repeats ("Spain", "Cantabria", "Cantabria"); dropping duplicates
    on the (SourceCountry, SourceRegion) pair keeps the GeoRegion join
    one-to-one so it cannot multiply fact rows.
    """
    schema = "SourceCountry string, SourceRegion string, TargetRegion string"
    return (spark.createDataFrame(list(REGION_MAP), schema)
            .dropDuplicates(["SourceCountry", "SourceRegion"]))


def project_fact_offers(df, region_map):
    """Project Gold fact_offers to the web contract, adding the geography.

    GeoCountry replicates the M normalization with a ``F.when`` chain over the
    trimmed country (no Python UDFs). GeoRegion is a left join of the trimmed
    raw ``location_country``/``location_region`` pair against the region map
    with ``coalesce(TargetRegion, "(Other)")``; like the model M
    (``Fact_Offers.tmdl:280-282``) the join uses the raw values, not the
    normalized GeoCountry.

    Raises ValueError listing the missing columns instead of writing an
    incomplete export.
    """
    from pyspark.sql import functions as F

    _require_columns(
        df,
        [c for c in WEB_FACT_COLUMNS if c not in ("GeoCountry", "GeoRegion")],
        "project_fact_offers")

    trimmed_country = _trim_whitespace(F.col("location_country"))
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
                            _trim_whitespace(F.col("location_region")))
                .withColumn("GeoCountry", geo_country_col))
    joined = prepared.join(
        region_map,
        on=((F.col("__country_trim") == region_map["SourceCountry"])
            & (F.col("__region_trim") == region_map["SourceRegion"])),
        how="left")
    return (joined
            .withColumn("GeoRegion",
                        F.coalesce(F.col("TargetRegion"), F.lit("(Other)")))
            .select(*WEB_FACT_COLUMNS))


def project_offer_skills(df):
    """Project the offer-skill bridge to exactly (JobID, Skill)."""
    _require_columns(df, WEB_OFFER_SKILL_COLUMNS, "project_offer_skills")
    return df.select(*WEB_OFFER_SKILL_COLUMNS)


def project_skill_list(df):
    """Project the skill catalog to exactly (SkillName, SkillCategory)."""
    _require_columns(df, WEB_SKILL_LIST_COLUMNS, "project_skill_list")
    return df.select(*WEB_SKILL_LIST_COLUMNS)


def project_calendar(df):
    """Project the calendar to exactly the 14 contract columns."""
    _require_columns(df, WEB_CALENDAR_COLUMNS, "project_calendar")
    return df.select(*WEB_CALENDAR_COLUMNS)


# ---------------------------------------------------------------------------
# Build and write the export
# ---------------------------------------------------------------------------


def build_web_export(spark, fact_offers, fact_offer_skills,
                     dim_skill_list, dim_calendar) -> dict:
    """Project the four already-built Gold tables to the web contract.

    The notebook reads Gold and only projects here, so the dedup logic stays in
    ``gold_build.ipynb``. Returns the ``{name: DataFrame}`` dict with
    ``fact_offers`` (adding ``GeoCountry``/``GeoRegion`` through
    ``region_map_df``), ``fact_offer_skills``, ``dim_skill_list`` and
    ``dim_calendar``.
    """
    region_map = region_map_df(spark)
    return {
        "fact_offers": project_fact_offers(fact_offers, region_map),
        "fact_offer_skills": project_offer_skills(fact_offer_skills),
        "dim_skill_list": project_skill_list(dim_skill_list),
        "dim_calendar": project_calendar(dim_calendar),
    }


def collect_export_stats(tables) -> tuple:
    """Return ``(table_counts, source_counts, data_date)`` for ``meta.json``.

    ``table_counts`` counts rows per table; ``source_counts`` counts offers per
    non-null/non-blank ``source_scraper`` (trimmed, ordered by source); and
    ``data_date`` is the max ``posted_date`` as a ``datetime.date``, or ``None``
    when fact_offers is empty or has no posting dates.
    """
    from pyspark.sql import functions as F

    table_counts = {name: int(df.count()) for name, df in tables.items()}
    offers = tables["fact_offers"]
    source_rows = (offers
                   .select(F.trim(F.col("source_scraper")).alias("source"))
                   .where(F.col("source").isNotNull() & (F.col("source") != ""))
                   .groupBy("source")
                   .count()
                   .orderBy("source")
                   .collect())
    source_counts = {row["source"]: int(row["count"]) for row in source_rows}
    data_date = offers.select(F.max("posted_date")).collect()[0][0]
    return table_counts, source_counts, data_date


def _as_uri(path) -> str:
    """Normalize a path to a URI; local Windows drives become ``file:///``."""
    uri = str(path).replace("\\", "/").rstrip("/")
    if len(uri) >= 2 and uri[1] == ":":
        uri = "file:///" + uri
    return uri


def _get_dbutils(spark):
    """Return the Databricks ``dbutils`` facade, or ``None`` off Databricks.

    Databricks sets ``DATABRICKS_RUNTIME_VERSION``; only there ``DBUtils``
    exists and ``dbutils.fs`` receives the cluster credentials (Unity Catalog
    aware, any access mode). Everywhere else — local tests included — the
    Hadoop FileSystem path is used instead. This helper is the single seam the
    routing tests monkeypatch; any failure falls back to Hadoop.
    """
    if not os.environ.get("DATABRICKS_RUNTIME_VERSION"):
        return None
    try:
        from pyspark.dbutils import DBUtils
        return DBUtils(spark)
    except Exception:  # noqa: BLE001 - no dbutils means Hadoop fallback
        return None


def _hadoop_context(spark, path):
    """Return ``(FileSystem, Path)`` for ``path`` using Spark's JVM config."""
    jpath = spark._jvm.org.apache.hadoop.fs.Path(path)
    return jpath.getFileSystem(spark._jsc.hadoopConfiguration()), jpath


def _fs_delete(spark, path, recursive=False) -> None:
    """Delete ``path`` through the credential-aware FileSystem (idempotent).

    On Databricks ``dbutils.fs.rm`` authenticates with the cluster
    credentials; a missing path may raise ``java.io.FileNotFoundException``
    (first run) or return ``False``, so only the "not found" message is
    tolerated and real failures (credentials, permissions) still propagate.
    Locally the Hadoop ``delete`` already returns ``False`` for missing paths.
    """
    dbutils = _get_dbutils(spark)
    if dbutils is not None:
        try:
            dbutils.fs.rm(path, recursive)
        except Exception as exc:  # noqa: BLE001 - only "not found" is safe
            if "FileNotFoundException" not in str(exc):
                raise
        return
    fs, jpath = _hadoop_context(spark, path)
    fs.delete(jpath, recursive)


def _delete_checksum(spark, path) -> None:
    """Remove the hidden ``.crc`` next to ``path`` (local Hadoop FS only)."""
    if _get_dbutils(spark) is not None:
        return
    parent, _, name = str(path).rstrip("/").rpartition("/")
    _fs_delete(spark, f"{parent}/.{name}.crc")


def _single_part_file(spark, directory) -> str:
    """Path of the only ``part-*`` file written by ``coalesce(1)``."""
    dbutils = _get_dbutils(spark)
    if dbutils is not None:
        parts = [entry.path for entry in dbutils.fs.ls(directory)
                 if entry.name.startswith("part-")]
    else:
        fs, jdir = _hadoop_context(spark, directory)
        parts = [status.getPath().toString() for status in fs.listStatus(jdir)
                 if status.getPath().getName().startswith("part-")]
    if len(parts) != 1:
        raise RuntimeError(
            f"se esperaba un único part-* en {directory}, "
            f"encontrados {len(parts)}")
    return parts[0]


def _fs_rename(spark, source, target) -> None:
    """Move ``source`` to ``target`` through the credential-aware FileSystem."""
    dbutils = _get_dbutils(spark)
    if dbutils is not None:
        dbutils.fs.mv(source, target)
        return
    fs, jsource = _hadoop_context(spark, source)
    if not fs.rename(jsource, spark._jvm.org.apache.hadoop.fs.Path(target)):
        raise RuntimeError(f"no se pudo mover {source} a {target}")


def _fs_size(spark, path) -> int:
    """Size in bytes of the single file at ``path`` (export Parquet)."""
    dbutils = _get_dbutils(spark)
    if dbutils is not None:
        return int(dbutils.fs.ls(path)[0].size)
    fs, jpath = _hadoop_context(spark, path)
    return int(fs.getFileStatus(jpath).getLen())


def _fs_write_text(spark, path, text) -> None:
    """Write UTF-8 text as a single file (create/overwrite).

    On Databricks ``dbutils.fs.put`` may append a final newline, which keeps
    ``meta.json`` valid. Locally the Hadoop stream is rewritten and the
    ``.crc`` checksum of the file is removed.
    """
    dbutils = _get_dbutils(spark)
    if dbutils is not None:
        dbutils.fs.put(path, text, overwrite=True)
        return
    fs, jpath = _hadoop_context(spark, path)
    stream = fs.create(jpath, True)
    try:
        stream.write(bytearray(text.encode("utf-8")))
    finally:
        stream.close()
    _delete_checksum(spark, path)


def _write_parquet(df, uri) -> None:
    """Write ``df`` as a single-part Parquet dataset under the temporary URI."""
    df.coalesce(1).write.mode("overwrite").parquet(uri)


def write_web_export(spark, tables, dest, meta=None, size_provider=None):
    """Write the export to ``dest``: one Parquet file per table (+ meta.json).

    Final layout, fixed names and idempotent::

        <dest>/fact_offers.parquet
        <dest>/fact_offer_skills.parquet
        <dest>/dim_skill_list.parquet
        <dest>/dim_calendar.parquet
        <dest>/meta.json            (only when ``meta`` is provided)

    Each table is written with ``coalesce(1)`` into a temporary directory
    inside ``dest`` and its single ``part-*`` file is renamed through the
    module's file helpers: ``dbutils.fs`` on Databricks (Unity Catalog
    credentials, works in any access mode) and the Hadoop FileSystem locally
    (``file:///``). A previous final file is deleted before the rename and the
    temporary directory (plus local checksum files) is removed, so re-running
    never duplicates rows nor leaves temporaries.

    ``size_bytes`` sums the real sizes of the four final files; pass
    ``size_provider`` (called without arguments) to override it, e.g. a
    wrapper around ``dbutils.fs.ls``. When ``meta`` is not ``None``, a copy is
    updated with the real size and the derived ``mode``, written to
    ``meta.json`` and returned; otherwise ``None`` is returned.
    """
    dest_uri = _as_uri(dest)

    for name, df in tables.items():
        temp_uri = f"{dest_uri}/__tmp_{name}"
        final_uri = f"{dest_uri}/{name}.parquet"
        _fs_delete(spark, final_uri)
        _fs_delete(spark, temp_uri, recursive=True)
        try:
            _write_parquet(df, temp_uri)
            part_path = _single_part_file(spark, temp_uri)
            _fs_rename(spark, part_path, final_uri)
            _delete_checksum(spark, final_uri)
        finally:
            _fs_delete(spark, temp_uri, recursive=True)

    if meta is None:
        return None

    if size_provider is not None:
        size_bytes = int(size_provider())
    else:
        size_bytes = sum(_fs_size(spark, f"{dest_uri}/{name}.parquet")
                         for name in tables)

    updated = dict(meta)
    updated["size_bytes"] = size_bytes
    updated["mode"] = ("aggregated" if export_exceeds_limit(size_bytes)
                       else "full")
    _fs_write_text(spark, f"{dest_uri}/meta.json", meta_json(updated))
    return updated
