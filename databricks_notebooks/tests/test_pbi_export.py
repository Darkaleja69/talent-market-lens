"""Tests for the Power BI report export module.

Pure tests lock the full column contract (31 columns of ``fact_offers`` plus
the five dimensions), the ``meta.json`` payload (always ``full``/``pbi``) and
the per-target destination helper. The Spark tests reuse the geography owned by
``Prepare_Web_Export`` (``REGION_MAP`` and country normalization) and check the
``skills`` text rendering. The write tests use the same Windows fallback as
``test_web_export.py`` (no ``winutils``) so the fixed file names, idempotency
and metadata stay under test locally. If pyspark is not installed, only the
Spark tests skip.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Prepare_Gold as g  # noqa: E402
import Prepare_PBI_Export as p  # noqa: E402
import Prepare_Web_Export as w  # noqa: E402

try:
    from pyspark.sql import SparkSession
    from pyspark.sql import types as T
    _HAS_PYSPARK = True
except ImportError:  # pragma: no cover - Spark tests skip without pyspark
    SparkSession = T = None
    _HAS_PYSPARK = False

requires_spark = pytest.mark.skipif(not _HAS_PYSPARK,
                                    reason="pyspark no instalado")

# ---------------------------------------------------------------------------
# Contract reference (spec 009, T-02 inventory)
# ---------------------------------------------------------------------------

EXPECTED_FACT_COLUMNS = [
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

EXPECTED_CALENDAR_COLUMNS = [
    "Date", "DateKey", "Year", "MonthNumber", "MonthName", "ShortMonth",
    "YearMonth", "Quarter", "YearQuarter", "WeekISO", "Day", "WeekDay",
    "DayName", "IsWeekend",
]

EXPECTED_COMPANY_COLUMNS = [
    "company_name", "company_industry", "company_description", "company_size",
    "source", "company_rating", "company_review_count",
]

EXPECTED_CURRENCY_COLUMNS = [
    "CurrencyCode", "CurrencyName", "AltCode", "UnitsPerEUR", "FXSnapshotDate",
]

# Internal columns the report needs and the public export excludes on purpose.
INTERNAL_FACT_COLUMNS = [
    "work_mode", "is_salary_available", "salary_currency", "salary_period",
    "description_clean", "skills", "salary_quality", "skills_source",
    "experience_level_source", "posted_date_raw", "posted_date_source",
]

EXPECTED_WEB_FACT_COLUMNS = [
    "job_id", "job_url", "title", "company_name", "location_city",
    "location_region", "location_country", "GeoCountry", "GeoRegion",
    "posted_date", "PostedYearMonth", "IsValidPostingDate", "WorkModeBucket",
    "SalaryMinAnnual_EUR", "SalaryMaxAnnual_EUR", "SalaryMidAnnual_EUR",
    "experience_level", "role_category", "employment_type", "source_scraper",
]


# ---------------------------------------------------------------------------
# Column contract
# ---------------------------------------------------------------------------


def test_fact_offers_contract_has_31_unique_columns():
    assert p.PBI_FACT_OFFERS_COLUMNS == EXPECTED_FACT_COLUMNS
    assert len(p.PBI_FACT_OFFERS_COLUMNS) == 31
    assert len(set(p.PBI_FACT_OFFERS_COLUMNS)) == 31


def test_fact_offers_physical_columns_match_gold_plus_derived_geography():
    physical = [c for c in p.PBI_FACT_OFFERS_COLUMNS
                if c not in ("GeoCountry", "GeoRegion")]
    assert len(physical) == 29
    assert set(physical) == set(g.FACT_OFFERS_COLUMNS)
    assert len(g.FACT_OFFERS_COLUMNS) == 29
    assert (set(p.PBI_FACT_OFFERS_COLUMNS) - set(g.FACT_OFFERS_COLUMNS)
            == {"GeoCountry", "GeoRegion"})


def test_report_exports_the_internal_columns_the_model_uses():
    for column in INTERNAL_FACT_COLUMNS:
        assert column in p.PBI_FACT_OFFERS_COLUMNS
        assert column not in w.WEB_FACT_COLUMNS


def test_dimension_contracts_are_exact():
    assert p.PBI_FACT_OFFER_SKILLS_COLUMNS == ["JobID", "Skill"]
    assert p.PBI_DIM_SKILL_LIST_COLUMNS == ["SkillName", "SkillCategory"]
    assert p.PBI_DIM_CALENDAR_COLUMNS == EXPECTED_CALENDAR_COLUMNS
    assert p.PBI_DIM_COMPANIES_COLUMNS == EXPECTED_COMPANY_COLUMNS
    assert p.PBI_DIM_CURRENCY_COLUMNS == EXPECTED_CURRENCY_COLUMNS
    assert len(p.PBI_DIM_CALENDAR_COLUMNS) == 14
    assert len(p.PBI_DIM_COMPANIES_COLUMNS) == 7
    assert len(p.PBI_DIM_CURRENCY_COLUMNS) == 5
    assert p.PBI_TABLES == (
        "fact_offers", "fact_offer_skills", "dim_skill_list", "dim_calendar",
        "dim_companies", "dim_currency")


def test_public_export_contract_is_unchanged():
    assert w.WEB_FACT_COLUMNS == EXPECTED_WEB_FACT_COLUMNS
    assert len(w.WEB_FACT_COLUMNS) == 20
    assert w.WEB_OFFER_SKILL_COLUMNS == p.PBI_FACT_OFFER_SKILLS_COLUMNS
    assert w.WEB_SKILL_LIST_COLUMNS == p.PBI_DIM_SKILL_LIST_COLUMNS
    assert w.WEB_CALENDAR_COLUMNS == p.PBI_DIM_CALENDAR_COLUMNS
    # Independent lists: evolving the report contract must not mutate the web.
    assert p.PBI_DIM_CALENDAR_COLUMNS is not w.WEB_CALENDAR_COLUMNS
    assert p.PBI_FACT_OFFERS_COLUMNS is not w.WEB_FACT_COLUMNS
    assert not set(INTERNAL_FACT_COLUMNS) & set(w.WEB_FACT_COLUMNS)


def test_export_dest_uses_the_target_prefix_and_no_catalog():
    assert p.export_dest("acct", "landing") == (
        "abfss://landing@acct.dfs.core.windows.net/gold/pbi_export")
    assert p.export_dest(
        "acct", "landing", "gold/pbi_export_dev").endswith(
            "/gold/pbi_export_dev")
    assert "job_offers" not in p.export_dest("acct", "landing")
    with pytest.raises(ValueError):
        p.export_dest("", "landing")


# ---------------------------------------------------------------------------
# meta.json (pure, no Spark)
# ---------------------------------------------------------------------------


def test_build_meta_exact_keys_values_and_counts_copy():
    tables = {"fact_offers": 10, "dim_calendar": 7}
    meta = p.build_meta(
        tables, dt.date(2026, 9, 3),
        generated_at=dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc),
        size_bytes=123456)
    assert set(meta) == {"export_version", "data_date", "generated_at", "mode",
                         "audience", "tables", "size_bytes"}
    assert meta["export_version"] == 1
    assert meta["data_date"] == "2026-09-03"
    assert meta["generated_at"] == "2026-10-23T12:00:00Z"
    assert meta["mode"] == "full"
    assert meta["audience"] == "pbi"
    assert meta["tables"] == tables
    assert meta["tables"] is not tables
    assert meta["size_bytes"] == 123456


def test_build_meta_data_date_accepts_date_datetime_and_str():
    assert (p.build_meta({}, dt.date(2026, 10, 5))["data_date"]
            == "2026-10-05")
    assert (p.build_meta({}, dt.datetime(2026, 10, 5, 23, 30))["data_date"]
            == "2026-10-05")
    assert p.build_meta({}, "2026-10-05")["data_date"] == "2026-10-05"


def test_build_meta_generated_at_defaults_to_now_utc():
    before = dt.datetime.now(dt.timezone.utc)
    meta = p.build_meta({}, "2026-10-05")
    after = dt.datetime.now(dt.timezone.utc)
    assert meta["generated_at"].endswith("Z")
    parsed = dt.datetime.fromisoformat(
        meta["generated_at"].replace("Z", "+00:00"))
    assert parsed.microsecond == 0
    assert (before - dt.timedelta(seconds=1)
            <= parsed <= after + dt.timedelta(seconds=1))


def test_build_meta_is_always_full_even_over_the_public_limit():
    big = w.DEFAULT_MAX_EXPORT_BYTES + 1
    meta = p.build_meta({"fact_offers": 1}, "2026-10-05", size_bytes=big)
    assert meta["mode"] == "full"
    assert meta["audience"] == "pbi"
    assert meta["size_bytes"] == big


def test_meta_json_is_deterministic_and_round_trips():
    meta = p.build_meta(
        {"fact_offers": 1}, dt.date(2026, 10, 5),
        generated_at=dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc))
    first = p.meta_json(meta)
    assert first == p.meta_json(meta)
    assert json.loads(first) == meta
    pairs = json.loads(first, object_pairs_hook=list)
    assert [key for key, _ in pairs] == sorted(meta)


# ---------------------------------------------------------------------------
# Spark integration: contract, geography parity and skills text
# ---------------------------------------------------------------------------

if _HAS_PYSPARK:
    _GOLD_TYPES = {
        "posted_date": T.DateType(),
        "posted_date_raw": T.DateType(),
        "IsValidPostingDate": T.BooleanType(),
        "is_salary_available": T.BooleanType(),
        "SalaryMinAnnual_EUR": T.DoubleType(),
        "SalaryMaxAnnual_EUR": T.DoubleType(),
        "SalaryMidAnnual_EUR": T.DoubleType(),
        "skills": T.ArrayType(T.StringType(), True),
    }
    GOLD_FACT_SCHEMA = T.StructType([
        T.StructField(name, _GOLD_TYPES.get(name, T.StringType()), True)
        for name in g.FACT_OFFERS_COLUMNS
    ])
    GOLD_FACT_DEFAULTS = {f.name: None for f in GOLD_FACT_SCHEMA.fields}

    COMPANY_SCHEMA = T.StructType([
        T.StructField("company_name", T.StringType(), True),
        T.StructField("company_industry", T.StringType(), True),
        T.StructField("company_description", T.StringType(), True),
        T.StructField("company_size", T.StringType(), True),
        T.StructField("source", T.StringType(), True),
        T.StructField("company_rating", T.DoubleType(), True),
        T.StructField("company_review_count", T.LongType(), True),
    ])
else:  # pragma: no cover - Spark tests skip without pyspark
    GOLD_FACT_SCHEMA = None
    GOLD_FACT_DEFAULTS = {}
    COMPANY_SCHEMA = None


def _gold_fact_df(spark, rows):
    data = []
    for row in rows:
        merged = dict(GOLD_FACT_DEFAULTS)
        merged.update(row)
        data.append(merged)
    return spark.createDataFrame(data, schema=GOLD_FACT_SCHEMA)


def _gold_tables(spark):
    """Synthetic Gold tables: two offers (Spain/Madrid and Alemania/Berlín)."""
    fact = _gold_fact_df(spark, [
        {"job_id": "1", "title": "Data Engineer", "location_city": "Madrid",
         "location_region": "Madrid", "location_country": "Spain",
         "posted_date": dt.date(2026, 9, 1), "source_scraper": "Indeed",
         "IsValidPostingDate": True, "is_salary_available": True,
         "skills": ["Python", "SQL"],
         "SalaryMinAnnual_EUR": 40000.0, "SalaryMaxAnnual_EUR": 50000.0,
         "SalaryMidAnnual_EUR": 45000.0},
        {"job_id": "2", "title": "Analyst", "location_city": "Berlín",
         "location_region": "Berlín", "location_country": "Alemania",
         "posted_date": dt.date(2026, 9, 3), "source_scraper": "LinkedIn",
         "IsValidPostingDate": True, "is_salary_available": False,
         "skills": None,
         "SalaryMinAnnual_EUR": 30000.0, "SalaryMaxAnnual_EUR": 36000.0,
         "SalaryMidAnnual_EUR": 33000.0},
    ])
    offer_skills = spark.createDataFrame(
        [("1", "Python"), ("1", "SQL"), ("2", "SQL")], ["JobID", "Skill"])
    skill_list = g.dim_skill_list(spark)
    calendar = g.dim_calendar(spark, dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    companies = spark.createDataFrame([
        ("Acme", "IT", "Acme description", "50-100", "indeed", 4.1, 120),
        ("Beta", None, None, None, "linkedin", None, None),
    ], schema=COMPANY_SCHEMA)
    currency = g.dim_currency(spark)
    return {"fact_offers": fact, "fact_offer_skills": offer_skills,
            "dim_skill_list": skill_list, "dim_calendar": calendar,
            "dim_companies": companies, "dim_currency": currency}


def _build_export(spark, tables):
    return p.build_pbi_export(
        spark, tables["fact_offers"], tables["fact_offer_skills"],
        tables["dim_skill_list"], tables["dim_calendar"],
        tables["dim_companies"], tables["dim_currency"])


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
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-pbi-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("pbi-export-integration-tests")
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


@pytest.fixture
def local_export_io(monkeypatch):
    """Run the write tests on Windows hosts without Hadoop winutils.

    The writer delegates to ``Prepare_Web_Export``'s helpers, so the same
    low-level functions are replaced by os/pyarrow equivalents; the layout,
    fixed names, rename, idempotency, size and meta.json logic stay under test.
    """
    if os.name != "nt" or os.environ.get("HADOOP_HOME"):
        return
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("pandas")
    import pyarrow.parquet as pq

    def _local(path):
        text = str(path)
        if text.startswith("file:///"):
            text = text[len("file:///"):]
        return text.replace("/", os.sep)

    def _write_parquet(df, uri):
        base = _local(uri)
        os.makedirs(base, exist_ok=True)
        pdf = df.toPandas()
        for column in pdf.columns:
            if pdf[column].isna().all():
                pdf[column] = pdf[column].astype("string")
        table = pa.Table.from_pandas(pdf, preserve_index=False)
        pq.write_table(table, os.path.join(base, "part-00000-local.parquet"))

    def _delete(spark, path, recursive=False):
        target = _local(path)
        if os.path.isdir(target):
            if recursive:
                shutil.rmtree(target)
            else:
                os.rmdir(target)
            return True
        if os.path.isfile(target):
            os.remove(target)
            return True
        return False

    def _single_part(spark, directory):
        base = _local(directory)
        parts = [name for name in os.listdir(base)
                 if name.startswith("part-")]
        if len(parts) != 1:
            raise RuntimeError(
                f"se esperaba un único part-* en {directory}, "
                f"encontrados {len(parts)}")
        return os.path.join(base, parts[0])

    def _rename(spark, source, target):
        os.replace(_local(source), _local(target))

    def _size(spark, path):
        return os.path.getsize(_local(path))

    def _write_text(spark, path, text):
        with open(_local(path), "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    # The writer calls these helpers through the shared module object.
    monkeypatch.setattr(w, "_write_parquet", _write_parquet)
    monkeypatch.setattr(w, "_fs_delete", _delete)
    monkeypatch.setattr(w, "_single_part_file", _single_part)
    monkeypatch.setattr(w, "_fs_rename", _rename)
    monkeypatch.setattr(w, "_fs_size", _size)
    monkeypatch.setattr(w, "_fs_write_text", _write_text)


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
    ("\tSpain\n", "\r\nMadrid\t", "whitespace"),
]


@requires_spark
def test_project_fact_offers_exact_31_columns_and_types(spark):
    out = p.project_fact_offers(
        _gold_fact_df(spark, [{"job_id": "1", "title": "Data Engineer"}]),
        w.region_map_df(spark))
    assert out.columns == list(p.PBI_FACT_OFFERS_COLUMNS)
    assert out.count() == 1
    dtypes = dict(out.dtypes)
    assert dtypes["skills"] == "string"
    assert dtypes["posted_date"] == "date"
    assert dtypes["IsValidPostingDate"] == "boolean"
    assert dtypes["SalaryMidAnnual_EUR"] == "double"
    assert dtypes["GeoCountry"] == "string"
    assert dtypes["GeoRegion"] == "string"


@requires_spark
def test_project_fact_offers_geo_matches_the_public_export(spark):
    rows = [{"job_id": job_id, "location_country": country,
             "location_region": region}
            for country, region, job_id in GEO_SAMPLES]
    gold = _gold_fact_df(spark, rows)
    report = p.project_fact_offers(gold, w.region_map_df(spark))
    public = w.project_fact_offers(gold, w.region_map_df(spark))

    assert report.count() == len(GEO_SAMPLES)
    assert public.count() == len(GEO_SAMPLES)
    report_rows = {r["job_id"]: r for r in report.collect()}
    public_rows = {r["job_id"]: r for r in public.collect()}
    for country, region, job_id in GEO_SAMPLES:
        assert (report_rows[job_id]["GeoCountry"]
                == public_rows[job_id]["GeoCountry"]), job_id
        assert (report_rows[job_id]["GeoRegion"]
                == public_rows[job_id]["GeoRegion"]), job_id
        # The pure functions of Prepare_Web_Export stay the reference.
        assert report_rows[job_id]["GeoCountry"] == w.geo_country(country)
        assert report_rows[job_id]["GeoRegion"] == w.geo_region(country, region)
    # Raw "CA" as country normalizes to the US but never hits the US catalog,
    # because the join uses the raw pair ("CA", "CA").
    assert report_rows["us-state-as-country"]["GeoCountry"] == "United States"
    assert report_rows["us-state-as-country"]["GeoRegion"] == "(Other)"


@requires_spark
def test_project_fact_offers_renders_skills_as_text(spark):
    rows = [
        {"job_id": "multi", "skills": ["Python", "SQL"]},
        {"job_id": "single", "skills": ["Python"]},
        {"job_id": "empty", "skills": []},
        {"job_id": "null", "skills": None},
    ]
    out = p.project_fact_offers(_gold_fact_df(spark, rows),
                                w.region_map_df(spark))
    got = {r["job_id"]: r["skills"] for r in out.collect()}
    assert got == {"multi": "Python, SQL", "single": "Python",
                   "empty": "", "null": ""}
    assert dict(out.dtypes)["skills"] == "string"


@requires_spark
def test_project_fact_offers_missing_columns_raise(spark):
    df = _gold_fact_df(spark, [{"job_id": "1"}]).drop("title", "skills")
    with pytest.raises(ValueError) as err:
        p.project_fact_offers(df, w.region_map_df(spark))
    assert "title" in str(err.value)
    assert "skills" in str(err.value)


@requires_spark
def test_project_dimensions_exact_columns(spark):
    tables = _gold_tables(spark)
    assert (p.project_fact_offer_skills(tables["fact_offer_skills"]).columns
            == p.PBI_FACT_OFFER_SKILLS_COLUMNS)
    assert (p.project_dim_skill_list(tables["dim_skill_list"]).columns
            == p.PBI_DIM_SKILL_LIST_COLUMNS)
    assert (p.project_dim_calendar(tables["dim_calendar"]).columns
            == p.PBI_DIM_CALENDAR_COLUMNS)
    assert (p.project_dim_companies(tables["dim_companies"]).columns
            == p.PBI_DIM_COMPANIES_COLUMNS)
    assert (p.project_dim_currency(tables["dim_currency"]).columns
            == p.PBI_DIM_CURRENCY_COLUMNS)
    assert p.project_dim_calendar(tables["dim_calendar"]).count() == 3
    assert p.project_dim_companies(tables["dim_companies"]).count() == 2


@requires_spark
def test_project_dimensions_missing_columns_raise(spark):
    with pytest.raises(ValueError, match="Skill"):
        p.project_fact_offer_skills(spark.createDataFrame([], "JobID string"))
    with pytest.raises(ValueError, match="SkillCategory"):
        p.project_dim_skill_list(spark.createDataFrame([], "SkillName string"))
    with pytest.raises(ValueError, match="IsWeekend"):
        p.project_dim_calendar(spark.createDataFrame([], "Date date"))
    with pytest.raises(ValueError, match="company_review_count"):
        p.project_dim_companies(spark.createDataFrame([], "company_name string"))
    with pytest.raises(ValueError, match="FXSnapshotDate"):
        p.project_dim_currency(spark.createDataFrame([], "CurrencyCode string"))


@requires_spark
def test_build_pbi_export_projects_the_six_tables(spark):
    tables = _gold_tables(spark)
    exported = _build_export(spark, tables)

    assert tuple(exported) == p.PBI_TABLES
    assert exported["fact_offers"].columns == list(p.PBI_FACT_OFFERS_COLUMNS)
    assert (exported["fact_offer_skills"].columns
            == p.PBI_FACT_OFFER_SKILLS_COLUMNS)
    assert (exported["dim_skill_list"].columns
            == p.PBI_DIM_SKILL_LIST_COLUMNS)
    assert exported["dim_calendar"].columns == p.PBI_DIM_CALENDAR_COLUMNS
    assert exported["dim_companies"].columns == p.PBI_DIM_COMPANIES_COLUMNS
    assert exported["dim_currency"].columns == p.PBI_DIM_CURRENCY_COLUMNS

    offers = {r["job_id"]: r for r in exported["fact_offers"].collect()}
    assert offers["1"]["GeoCountry"] == "Spain"
    assert offers["1"]["GeoRegion"] == "Madrid"
    assert offers["1"]["skills"] == "Python, SQL"
    assert offers["2"]["GeoCountry"] == "Germany"
    assert offers["2"]["GeoRegion"] == "(Other)"
    assert offers["2"]["skills"] == ""
    assert exported["fact_offer_skills"].count() == 3


@requires_spark
def test_collect_export_stats_counts_tables_and_data_date(spark):
    tables = _gold_tables(spark)
    exported = _build_export(spark, tables)
    counts, data_date = p.collect_export_stats(exported)
    assert counts == {
        "fact_offers": 2,
        "fact_offer_skills": 3,
        "dim_skill_list": tables["dim_skill_list"].count(),
        "dim_calendar": 3,
        "dim_companies": 2,
        "dim_currency": tables["dim_currency"].count(),
    }
    assert data_date == dt.date(2026, 9, 3)


@requires_spark
def test_collect_export_stats_empty_offers_have_no_data_date(spark):
    empty = spark.createDataFrame([], schema=GOLD_FACT_SCHEMA)
    counts, data_date = p.collect_export_stats({"fact_offers": empty})
    assert counts == {"fact_offers": 0}
    assert data_date is None


@requires_spark
def test_write_pbi_export_fixed_files_meta_and_idempotent(
        spark, tmp_path, local_export_io):
    exported = _build_export(spark, _gold_tables(spark))
    counts, data_date = p.collect_export_stats(exported)
    meta = p.build_meta(
        counts, data_date,
        generated_at=dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc))
    dest = tmp_path / "pbi_export"

    written = p.write_pbi_export(spark, exported, dest, meta=meta)

    expected_files = sorted([f"{name}.parquet" for name in p.PBI_TABLES]
                            + ["meta.json"])
    assert sorted(item.name for item in dest.iterdir()) == expected_files
    for name in p.PBI_TABLES:
        path = dest / f"{name}.parquet"
        assert path.is_file()
        read = spark.read.parquet(path.as_uri())
        assert read.columns == exported[name].columns
        assert read.count() == exported[name].count()

    fact = spark.read.parquet((dest / "fact_offers.parquet").as_uri())
    assert dict(fact.dtypes)["skills"] == "string"
    offers = {r["job_id"]: r for r in fact.collect()}
    assert offers["1"]["skills"] == "Python, SQL"
    assert offers["2"]["skills"] == ""

    payload = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert payload["size_bytes"] > 0
    assert payload["size_bytes"] == written["size_bytes"]
    assert payload["mode"] == "full"
    assert payload["audience"] == "pbi"
    assert payload["generated_at"] == "2026-10-23T12:00:00Z"
    assert payload["data_date"] == "2026-09-03"
    assert payload["tables"] == counts

    # Idempotent: the second run leaves the same files, rows and no temporaries.
    p.write_pbi_export(spark, exported, dest, meta=meta)
    assert sorted(item.name for item in dest.iterdir()) == expected_files
    for name in p.PBI_TABLES:
        path = dest / f"{name}.parquet"
        assert spark.read.parquet(path.as_uri()).count() == \
            exported[name].count()


@requires_spark
def test_write_pbi_export_stays_full_over_the_public_limit(
        spark, tmp_path, local_export_io):
    exported = _build_export(spark, _gold_tables(spark))
    meta = p.build_meta({"fact_offers": 2}, dt.date(2026, 9, 3))
    dest = tmp_path / "pbi_export_big"
    big = w.DEFAULT_MAX_EXPORT_BYTES + 1

    written = p.write_pbi_export(spark, exported, dest, meta=meta,
                                 size_provider=lambda: big)

    assert written["size_bytes"] == big
    assert written["mode"] == "full"
    assert written["audience"] == "pbi"
    payload = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert payload["size_bytes"] == big
    assert payload["mode"] == "full"
    assert payload["audience"] == "pbi"


def test_report_module_has_no_python_udfs():
    # The export is columnar: geography and skills stay in F.when/joins.
    source = Path(p.__file__).read_text(encoding="utf-8")
    assert "udf(" not in source
