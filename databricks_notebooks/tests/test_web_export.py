"""Tests for the web export module: pure geography and Spark projections.

The pure tests cover ``geo_country``/``geo_region`` and the TMDL fidelity of
``REGION_MAP`` without Spark. The integration tests (pyspark, same fixture
pattern as ``test_integration_spark.py``) lock the public column contract of
every exported table and the geographic parity between ``project_fact_offers``
and the pure functions. If pyspark is not installed, only the Spark tests skip.
"""
import datetime as dt
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Prepare_Gold as g  # noqa: E402
import Prepare_Web_Export as w  # noqa: E402

try:
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql import types as T
    _HAS_PYSPARK = True
except ImportError:  # pragma: no cover - Spark tests skip without pyspark
    SparkSession = F = T = None
    _HAS_PYSPARK = False

requires_spark = pytest.mark.skipif(not _HAS_PYSPARK,
                                    reason="pyspark no instalado")

REPO_ROOT = Path(__file__).resolve().parents[2]
REGION_MAP_TMDL = (REPO_ROOT / "Job_Offers_Dashboard.SemanticModel"
                   / "definition" / "tables" / "Dim_RegionMap.tmdl")

_ROW_RE = re.compile(r'\{\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\}')

# geo_country


def test_geo_country_us_states_used_as_country():
    assert w.geo_country("CA") == "United States"
    assert w.geo_country("FL") == "United States"
    assert w.geo_country("PA") == "United States"


def test_geo_country_language_aliases():
    assert w.geo_country("Alemania") == "Germany"
    assert w.geo_country("Austria y Suiza") == "Austria"
    assert w.geo_country("Austria and Switzerland") == "Austria"
    assert w.geo_country("Oriente Medio y África") == "Middle East & Africa"


def test_geo_country_empty_missing_and_placeholder():
    assert w.geo_country("") == "(Not specified)"
    assert w.geo_country("   ") == "(Not specified)"
    assert w.geo_country(None) == "(Not specified)"
    assert w.geo_country("(Not specified)") == "(Not specified)"
    assert w.geo_country("  (Not specified)  ") == "(Not specified)"


def test_geo_country_trims_and_keeps_unknown_exactly():
    assert w.geo_country("  Spain  ") == "Spain"
    assert w.geo_country("Portugal") == "Portugal"
    # Comparisons are exact: lowercase values are data, not aliases.
    assert w.geo_country("alemania") == "alemania"
    assert w.geo_country("ca") == "ca"
    assert w.geo_country("austria y suiza") == "austria y suiza"
    assert w.geo_country("CA ") == "United States"


# geo_region


def test_geo_region_catalog_pairs():
    assert w.geo_region("Spain", "Madrid") == "Madrid"
    assert w.geo_region("Spain", "Terrassa") == "Catalonia"
    assert w.geo_region("Spain", "Sant Vicenç dels Horts") == "Catalonia"
    assert w.geo_region("Ireland", "Dublín") == "Dublin"
    assert w.geo_region("Switzerland", "Zúrich") == "Zurich"
    assert w.geo_region("Netherlands", "Amsterdam") == "North Holland"
    assert w.geo_region("United States", "CA") == "California"
    assert w.geo_region("United States", "NY") == "New York"


def test_geo_region_country_fallback_rows():
    assert w.geo_region("Spain", "Spain") == "(Country)"
    assert w.geo_region("Ireland", "Ireland") == "(Country)"
    assert w.geo_region("Netherlands", "Netherlands") == "(Country)"
    assert w.geo_region("Switzerland", "Switzerland") == "(Country)"
    assert w.geo_region("United States", "United States") == "(Country)"
    assert w.geo_region("Europe", "Europe") == "(Country)"


def test_geo_region_unknown_pairs_fall_back_to_other():
    # Barcelona is not a Dim_RegionMap row (it only exists in Dim_Geo), so the
    # exact pair match falls back to (Other) like the model's left merge.
    assert w.geo_region("Spain", "Barcelona") == "(Other)"
    assert w.geo_region("Portugal", "Lisboa") == "(Other)"
    assert w.geo_region("Spain", "") == "(Other)"
    assert w.geo_region("", "") == "(Other)"
    assert w.geo_region(None, None) == "(Other)"


def test_geo_region_trims_and_is_case_sensitive():
    assert w.geo_region(" Spain ", " Madrid ") == "Madrid"
    assert w.geo_region("  Ireland  ", "  Dublín  ") == "Dublin"
    assert w.geo_region("spain", "madrid") == "(Other)"
    assert w.geo_region("Spain", "barcelona") == "(Other)"


# Fidelity with the semantic model


def test_region_map_matches_semantic_model():
    text = REGION_MAP_TMDL.read_text(encoding="utf-8")
    parsed = [tuple(match) for match in _ROW_RE.findall(text)]
    assert parsed, "Dim_RegionMap.tmdl should contain #table rows"
    # Multiset equality: any added, removed, changed or duplicated row in the
    # model forces REGION_MAP to be updated.
    assert sorted(parsed) == sorted(w.REGION_MAP)


# meta.json and size control


def test_export_version_and_default_limit():
    assert w.EXPORT_VERSION == 1
    assert w.DEFAULT_MAX_EXPORT_BYTES == 25 * 1024 * 1024


def test_export_exceeds_limit_three_segments():
    limit = 1000
    assert w.export_exceeds_limit(999, limit) is False
    # At the exact threshold the export still fits.
    assert w.export_exceeds_limit(1000, limit) is False
    assert w.export_exceeds_limit(1001, limit) is True
    assert w.export_exceeds_limit(w.DEFAULT_MAX_EXPORT_BYTES) is False
    assert w.export_exceeds_limit(w.DEFAULT_MAX_EXPORT_BYTES + 1) is True


def test_build_meta_exact_keys_types_and_counts():
    tables = {"fact_offers": 10, "fact_offer_skills": 20,
              "dim_skill_list": 5, "dim_calendar": 7}
    sources = {"Indeed": 6, "LinkedIn": 4}
    meta = w.build_meta(
        tables, sources, dt.date(2026, 10, 5),
        generated_at=dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc),
        size_bytes=123456)
    assert set(meta) == {"export_version", "data_date", "generated_at", "mode",
                         "tables", "sources", "size_bytes"}
    assert meta["export_version"] == w.EXPORT_VERSION
    assert meta["data_date"] == "2026-10-05"
    assert meta["generated_at"] == "2026-10-06T12:00:00Z"
    assert meta["mode"] == "full"
    assert meta["tables"] == tables
    assert meta["sources"] == sources
    assert meta["size_bytes"] == 123456
    assert isinstance(meta["export_version"], int)
    assert isinstance(meta["size_bytes"], int)
    # Copies, not aliases of the caller dicts.
    assert meta["tables"] is not tables
    assert meta["sources"] is not sources


def test_build_meta_data_date_accepts_date_datetime_and_str():
    assert w.build_meta({}, {}, dt.date(2026, 10, 5))["data_date"] == "2026-10-05"
    assert (w.build_meta({}, {}, dt.datetime(2026, 10, 5, 23, 30))["data_date"]
            == "2026-10-05")
    assert w.build_meta({}, {}, "2026-10-05")["data_date"] == "2026-10-05"


def test_build_meta_generated_at_defaults_to_now_utc():
    before = dt.datetime.now(dt.timezone.utc)
    meta = w.build_meta({}, {}, "2026-10-05")
    after = dt.datetime.now(dt.timezone.utc)
    assert meta["generated_at"].endswith("Z")
    parsed = dt.datetime.fromisoformat(meta["generated_at"].replace("Z", "+00:00"))
    assert parsed.microsecond == 0
    assert before - dt.timedelta(seconds=1) <= parsed <= after + dt.timedelta(seconds=1)


def test_build_meta_mode_follows_limit_and_explicit_mode_wins():
    limit = w.DEFAULT_MAX_EXPORT_BYTES
    assert w.build_meta({}, {}, "2026-10-05", size_bytes=limit)["mode"] == "full"
    assert w.build_meta({}, {}, "2026-10-05",
                        size_bytes=limit + 1)["mode"] == "aggregated"
    assert w.build_meta({}, {}, "2026-10-05", size_bytes=limit + 1,
                        mode="full")["mode"] == "full"


def test_meta_json_is_deterministic_and_keeps_utf8():
    meta = w.build_meta(
        {"fact_offers": 1}, {"Esló": 1}, "2026-10-05",
        generated_at=dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc),
        size_bytes=10)
    first = w.meta_json(meta)
    assert first == w.meta_json(meta)
    assert "Esló" in first and "\\u00f3" not in first
    assert json.loads(first) == meta
    # Top-level keys are sorted.
    pairs = json.loads(first, object_pairs_hook=list)
    assert [key for key, _ in pairs] == sorted(meta)


# ---------------------------------------------------------------------------
# Spark integration: column contract and geographic parity
# ---------------------------------------------------------------------------

if _HAS_PYSPARK:
    FACT_SOURCE_SCHEMA = T.StructType([
        T.StructField("job_id", T.StringType(), True),
        T.StructField("job_url", T.StringType(), True),
        T.StructField("title", T.StringType(), True),
        T.StructField("company_name", T.StringType(), True),
        T.StructField("location_city", T.StringType(), True),
        T.StructField("location_region", T.StringType(), True),
        T.StructField("location_country", T.StringType(), True),
        T.StructField("posted_date", T.DateType(), True),
        T.StructField("PostedYearMonth", T.StringType(), True),
        T.StructField("IsValidPostingDate", T.BooleanType(), True),
        T.StructField("WorkModeBucket", T.StringType(), True),
        T.StructField("SalaryMinAnnual_EUR", T.DoubleType(), True),
        T.StructField("SalaryMaxAnnual_EUR", T.DoubleType(), True),
        T.StructField("SalaryMidAnnual_EUR", T.DoubleType(), True),
        T.StructField("experience_level", T.StringType(), True),
        T.StructField("role_category", T.StringType(), True),
        T.StructField("employment_type", T.StringType(), True),
        T.StructField("source_scraper", T.StringType(), True),
        # Excluded on purpose: must never reach the export.
        T.StructField("description_clean", T.StringType(), True),
        T.StructField("skills", T.StringType(), True),
        T.StructField("salary_quality", T.StringType(), True),
        T.StructField("skills_source", T.StringType(), True),
        T.StructField("experience_level_source", T.StringType(), True),
        T.StructField("posted_date_raw", T.StringType(), True),
        T.StructField("posted_date_source", T.StringType(), True),
        T.StructField("salary_currency", T.StringType(), True),
        T.StructField("salary_period", T.StringType(), True),
        T.StructField("internal_note", T.StringType(), True),
    ])
    FACT_DEFAULTS = {f.name: None for f in FACT_SOURCE_SCHEMA.fields}
else:  # pragma: no cover - Spark tests skip without pyspark
    FACT_SOURCE_SCHEMA = None
    FACT_DEFAULTS = {}

EXCLUDED_FACT_COLUMNS = [
    "description_clean", "skills", "salary_quality", "skills_source",
    "experience_level_source", "posted_date_raw", "posted_date_source",
    "salary_currency", "salary_period", "internal_note",
]


def _fact_row(**kwargs):
    row = dict(FACT_DEFAULTS)
    row.update(kwargs)
    return row


def _fact_df(spark, rows):
    return spark.createDataFrame([_fact_row(**r) for r in rows],
                                 schema=FACT_SOURCE_SCHEMA)


@pytest.fixture(scope="session")
def spark():
    """Session reused from other test modules when already active."""
    if not _HAS_PYSPARK:  # pragma: no cover
        pytest.skip("pyspark no instalado")
    active = SparkSession.getActiveSession()
    if active is not None:
        yield active
        return
    # Same local-Windows settings as test_integration_spark.py.
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-web-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("web-export-integration-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.extraJavaOptions", ipv4)
        .config("spark.executor.extraJavaOptions", ipv4)
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .config("spark.executorEnv.PYSPARK_PYTHON", sys.executable)
        .getOrCreate()
    )
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 1).map(lambda x: x).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    yield session
    session.stop()


GEO_SAMPLES = [
    # (raw location_country, raw location_region, job_id)
    ("CA", "CA", "us-state-as-country"),
    ("  CA  ", "  CA  ", "trimmed"),
    ("FL", "FL", "fl-state-as-country"),
    ("PA", "PA", "pa-state-as-country"),
    ("Alemania", "Berlín", "germany"),
    ("Austria y Suiza", "Viena", "austria-es"),
    ("Austria and Switzerland", "", "austria-en"),
    ("Oriente Medio y África", "Dubái", "middle-east-africa"),
    ("", "", "empty"),
    ("(Not specified)", "", "placeholder"),
    (None, None, "null"),
    ("Portugal", "Lisboa", "unknown-country"),
    ("Spain", "Madrid", "madrid"),
    ("Spain", "Catalonia", "catalonia"),
    ("Spain", "Spain", "country-fallback"),
    ("Ireland", "Dublín", "dublin"),
    ("Switzerland", "Zúrich", "zurich"),
    ("Netherlands", "Amsterdam", "north-holland"),
    ("United States", "CA", "california"),
]


@requires_spark
def test_project_fact_offers_exact_columns_and_no_internal(spark):
    out = w.project_fact_offers(
        _fact_df(spark, [{"job_id": "1", "title": "Data Engineer"}]),
        w.region_map_df(spark))
    assert out.columns == list(w.WEB_FACT_COLUMNS)
    assert out.count() == 1
    assert not set(EXCLUDED_FACT_COLUMNS) & set(out.columns)
    row = out.collect()[0]
    assert (row["job_id"], row["title"]) == ("1", "Data Engineer")


@requires_spark
def test_project_fact_offers_geo_matches_pure_functions(spark):
    rows = [{"job_id": job_id, "location_country": country,
             "location_region": region}
            for country, region, job_id in GEO_SAMPLES]
    out = w.project_fact_offers(_fact_df(spark, rows), w.region_map_df(spark))
    assert out.count() == len(GEO_SAMPLES)
    got = {r["job_id"]: r for r in out.collect()}
    for country, region, job_id in GEO_SAMPLES:
        assert got[job_id]["GeoCountry"] == w.geo_country(country), job_id
        assert got[job_id]["GeoRegion"] == w.geo_region(country, region), job_id
    # Raw "CA" as country must normalize to the US but never hit the US region
    # catalog, because the join uses the raw pair ("CA", "CA").
    assert got["us-state-as-country"]["GeoCountry"] == "United States"
    assert got["us-state-as-country"]["GeoRegion"] == "(Other)"


@requires_spark
def test_project_fact_offers_trims_ascii_whitespace_like_pure_functions(spark):
    # Spark F.trim only removes the ASCII space, so the projection must trim
    # tab/LF/CR/FF/VT to match the pure helpers (str.strip) on scraped text.
    samples = [
        # (raw country, raw region, expected GeoCountry, expected GeoRegion)
        ("\tCA", "\tCA", "United States", "(Other)"),
        ("Spain\n", "Madrid", "Spain", "Madrid"),
        ("  Alemania ", "Berlín", "Germany", "(Other)"),
        ("Spain", "\r\nMadrid\t", "Spain", "Madrid"),
        ("Spain", " Madrid ", "Spain", "Madrid"),
        ("Spain", "\tCatalonia", "Spain", "Catalonia"),
    ]
    rows = [{"job_id": f"ws{i}", "location_country": country,
             "location_region": region}
            for i, (country, region, _, _) in enumerate(samples)]
    out = w.project_fact_offers(_fact_df(spark, rows), w.region_map_df(spark))
    assert out.count() == len(samples)
    got = {r["job_id"]: r for r in out.collect()}
    for i, (country, region, expected_country, expected_region) in enumerate(samples):
        row = got[f"ws{i}"]
        assert row["GeoCountry"] == expected_country, (country, region)
        assert row["GeoRegion"] == expected_region, (country, region)
        # The T-02 pure semantics stay the reference.
        assert row["GeoCountry"] == w.geo_country(country)
        assert row["GeoRegion"] == w.geo_region(country, region)


@requires_spark
def test_region_map_df_dedup_and_no_row_multiplication(spark):
    region_map = w.region_map_df(spark)
    assert region_map.columns == ["SourceCountry", "SourceRegion", "TargetRegion"]
    unique_pairs = {(c, r) for c, r, _ in w.REGION_MAP}
    assert region_map.count() == len(unique_pairs)
    # The model repeats ("Spain", "Cantabria", "Cantabria"); the dimension
    # deduplicates it so the join stays one-to-one.
    assert region_map.count() < len(w.REGION_MAP)
    out = w.project_fact_offers(
        _fact_df(spark, [
            {"job_id": "dup", "location_country": "Spain",
             "location_region": "Cantabria"},
            {"job_id": "other", "location_country": "Spain",
             "location_region": "Madrid"},
        ]),
        region_map)
    assert out.count() == 2
    regions = {r["job_id"]: r["GeoRegion"] for r in out.collect()}
    assert regions == {"dup": "Cantabria", "other": "Madrid"}


@requires_spark
def test_project_offer_skills_exact_columns(spark):
    df = spark.createDataFrame([("1", "Python", "x")],
                               ["JobID", "Skill", "Extra"])
    out = w.project_offer_skills(df)
    assert out.columns == list(w.WEB_OFFER_SKILL_COLUMNS)
    assert out.count() == 1


@requires_spark
def test_project_skill_list_exact_columns(spark):
    df = spark.createDataFrame(
        [("Python", "Programming Languages", 1)],
        ["SkillName", "SkillCategory", "Extra"])
    out = w.project_skill_list(df)
    assert out.columns == list(w.WEB_SKILL_LIST_COLUMNS)
    assert out.count() == 1


@requires_spark
def test_project_calendar_matches_producer_and_contract(spark):
    cal = g.dim_calendar(spark, dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    # The producer itself must generate exactly the contract columns.
    assert cal.columns == list(w.WEB_CALENDAR_COLUMNS)
    out = w.project_calendar(cal.withColumn("Extra", F.lit(1)))
    assert out.columns == list(w.WEB_CALENDAR_COLUMNS)
    assert out.count() == 3


@requires_spark
def test_project_fact_offers_missing_columns_raise(spark):
    df = _fact_df(spark, [{"job_id": "1"}]).drop("title", "source_scraper")
    with pytest.raises(ValueError) as err:
        w.project_fact_offers(df, w.region_map_df(spark))
    assert "title" in str(err.value)
    assert "source_scraper" in str(err.value)


@requires_spark
def test_project_bridge_tables_missing_columns_raise(spark):
    with pytest.raises(ValueError, match="Skill"):
        w.project_offer_skills(spark.createDataFrame([], "JobID string"))
    with pytest.raises(ValueError, match="SkillCategory"):
        w.project_skill_list(spark.createDataFrame([], "SkillName string"))
    with pytest.raises(ValueError, match="IsWeekend"):
        w.project_calendar(spark.createDataFrame([], "Date date"))


def test_module_uses_no_python_udfs():
    # The export is columnar: geography must stay in F.when / joins.
    source = Path(w.__file__).read_text(encoding="utf-8")
    assert "udf(" not in source
