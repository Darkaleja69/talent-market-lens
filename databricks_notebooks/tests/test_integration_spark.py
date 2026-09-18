"""Tests de integracion con Spark (DataFrames reales, sin ficheros).

Cubren los criterios minimos de la Fase 0 del roadmap:
- enrich() sobre un DataFrame Spark
- build_fact_offers() y proyeccion de columnas
- conversion de divisas a EUR
- build_fact_offer_skills() (explode + deduplicacion oferta/skill)
- tabla vacia
- reejecucion idempotente de enrich()
- dim_calendar()

Se ejecutan en local con pyspark (ver requirements.txt) y en Databricks. Si
pyspark no esta instalado, el modulo se salta automaticamente.
"""
import datetime as dt
import functools
import os
import sys
import tempfile
import time

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F841

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import Enrich_Job_Offers_Dataframes as e  # noqa: E402
import Prepare_Gold as g  # noqa: E402


# ---------------------------------------------------------------------------
# SparkSession de test (local, un solo core, sin UI)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def spark():
    # En Windows/local esto evita que los Python workers usen otro interprete
    # y que el driver anuncie una interfaz de red no accesible.
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="spark-warehouse-")
    ipv4 = "-Djava.net.preferIPv4Stack=true"
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("job-offers-integration-tests")
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
    # Warm-up: fuerza el arranque de un Python worker y reintenta si el
    # handshake falla (fallo transitorio conocido en Windows/local).
    for _ in range(3):
        try:
            session.sparkContext.parallelize(range(8), 1).map(lambda x: x).count()
            break
        except Exception:  # noqa: BLE001
            time.sleep(5)
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Datos de ejemplo
# ---------------------------------------------------------------------------

SCHEMA = T.StructType([
    T.StructField("job_id", T.StringType(), True),
    T.StructField("job_url", T.StringType(), True),
    T.StructField("title", T.StringType(), True),
    T.StructField("company_name", T.StringType(), True),
    T.StructField("location_city", T.StringType(), True),
    T.StructField("location_region", T.StringType(), True),
    T.StructField("location_country", T.StringType(), True),
    T.StructField("posted_date", T.DateType(), True),
    T.StructField("scraped_at", T.TimestampType(), True),
    T.StructField("work_mode", T.StringType(), True),
    T.StructField("is_salary_available", T.BooleanType(), True),
    T.StructField("salary_currency", T.StringType(), True),
    T.StructField("salary_min", T.DoubleType(), True),
    T.StructField("salary_max", T.DoubleType(), True),
    T.StructField("salary_period", T.StringType(), True),
    T.StructField("salary_raw", T.StringType(), True),
    T.StructField("experience_level", T.StringType(), True),
    T.StructField("employment_type", T.StringType(), True),
    T.StructField("role_category", T.StringType(), True),
    T.StructField("description_clean", T.StringType(), True),
    T.StructField("skills", T.StringType(), True),
    T.StructField("source_scraper", T.StringType(), True),
    T.StructField("search_role", T.StringType(), True),
    T.StructField("_ingest_date", T.DateType(), True),
])

DEFAULTS = {f.name: None for f in SCHEMA.fields}


def _row(**kwargs):
    row = dict(DEFAULTS)
    row.update(kwargs)
    return row


def _df(spark, rows):
    return spark.createDataFrame([_row(**r) for r in rows], schema=SCHEMA)


SAMPLE = dict(
    job_id="1",
    job_url="https://example.com/1",
    title="Senior Data Engineer",
    company_name="Acme",
    location_city="Madrid",
    posted_date=dt.date(2026, 9, 1),
    scraped_at=dt.datetime(2026, 9, 2, 10, 0, 0),
    work_mode="",
    salary_currency="EUR",
    salary_min=40000.0,
    salary_max=55000.0,
    salary_period="year",
    salary_raw="€40,000 - €55,000 per year",
    description_clean="Buscamos un perfil con Python y SQL. Fully remote role.",
    source_scraper="Indeed",
)


# ---------------------------------------------------------------------------
# enrich()
# ---------------------------------------------------------------------------

def test_enrich_columnas_y_reglas(spark):
    out = e.enrich(_df(spark, [SAMPLE])).collect()[0]

    assert set(out["skills"]) == {"Python", "SQL"}
    assert out["role_category"] == "Data Engineer"
    assert out["experience_level"] == "Mid-Senior"
    assert out["salary_min_annual"] == 40000.0
    assert out["salary_max_annual"] == 55000.0
    assert out["salary_quality"] == "ok"
    assert out["salary_period_norm"] == "YEAR"
    assert out["work_mode_norm"] == "Remote"          # derivado de la descripcion
    assert out["work_mode_source"] == "derived"
    assert out["location_country"] == "Spain"          # inferido desde Madrid
    assert out["location_region"] == "Madrid"
    assert out["posted_date_source"] == "posted"
    assert out["skills_source"] == "derived"


def test_enrich_periodo_desconocido_no_falsea_anual(spark):
    # Periodo no reconocido -> factor NULL -> no se inventa el salario anual.
    row = dict(SAMPLE, job_id="2", salary_period="", salary_raw="competitive salary",
               salary_min=20.0, salary_max=None, location_city=None)
    out = e.enrich(_df(spark, [row])).collect()[0]
    assert out["salary_quality"] == "unknown_period"
    assert out["salary_min_annual"] is None
    assert out["salary_period_norm"] == ""


def test_enrich_periodo_hora_se_anualiza(spark):
    # Periodo reconocido (hora) -> se anualiza (x2080) aunque no tenga cubo.
    row = dict(SAMPLE, job_id="3", salary_period="hour", salary_raw="",
               salary_min=20.0, salary_max=25.0, location_city=None)
    out = e.enrich(_df(spark, [row])).collect()[0]
    assert out["salary_min_annual"] == pytest.approx(20.0 * 2080)
    assert out["salary_max_annual"] == pytest.approx(25.0 * 2080)
    assert out["salary_quality"] == "ok"
    assert out["salary_period_norm"] == ""


def test_enrich_reejecucion_idempotente(spark):
    first = e.enrich(_df(spark, [SAMPLE]))
    # localCheckpoint corta el linaje: sin el, enrich(enrich(df)) multiplica
    # las expresiones y dispara el tiempo de optimizacion de Catalyst.
    first = first.localCheckpoint(eager=True)
    second = e.enrich(first)
    cols = ["skills", "role_category", "experience_level", "work_mode_norm",
            "salary_min_annual", "salary_max_annual", "salary_quality",
            "location_country", "location_region"]
    assert [first.select(*cols).collect()[0][c] for c in cols] == \
           [second.select(*cols).collect()[0][c] for c in cols]


# ---------------------------------------------------------------------------
# Conversion de divisas
# ---------------------------------------------------------------------------

def test_conversion_divisas_y_altcode(spark):
    dim = g.dim_currency(spark)
    rows = [
        (1000.0, 5000.0, "USD", "1"),
        (2000.0, 2000.0, "€", "2"),        # AltCode del EUR
        (3000.0, 3000.0, "XXX", "3"),      # divisa desconocida -> NULL
    ]
    df = spark.createDataFrame(rows, ["salary_min_annual", "salary_max_annual",
                                      "salary_currency", "job_id"])
    out = {r["job_id"]: r for r in g.add_salary_eur(df, dim).collect()}

    assert out["1"]["SalaryMinAnnual_EUR"] == pytest.approx(1000 / 1.1622)
    assert out["2"]["SalaryMinAnnual_EUR"] == pytest.approx(2000.0)
    assert out["2"]["SalaryMidAnnual_EUR"] == pytest.approx(2000.0)
    assert out["3"]["SalaryMinAnnual_EUR"] is None


# ---------------------------------------------------------------------------
# fact_offers
# ---------------------------------------------------------------------------

def test_build_fact_offers(spark):
    dim = g.dim_currency(spark)
    df = spark.createDataFrame(
        [("1", "A", dt.date(2026, 9, 15), "REMOTE/HYBRID", "EUR", 1000.0, 3000.0)],
        ["job_id", "title", "posted_date", "work_mode", "salary_currency",
         "salary_min_annual", "salary_max_annual"])
    out = g.build_fact_offers(df, dim)
    assert set(out.columns) <= set(g.FACT_OFFERS_COLUMNS)
    row = out.collect()[0]
    assert row["WorkModeBucket"] == "Hybrid"
    assert row["PostedYearMonth"] == "2026-09"
    assert row["SalaryMidAnnual_EUR"] == pytest.approx(2000.0)
    assert row["IsValidPostingDate"] is True


# ---------------------------------------------------------------------------
# fact_offer_skills (explode + deduplicacion)
# ---------------------------------------------------------------------------

def test_fact_offer_skills_explode_dedup(spark):
    array_df = spark.createDataFrame(
        [("1", ["Python", "SQL", "Python"])], ["job_id", "skills"])
    string_df = spark.createDataFrame(
        [("2", "Python|SQL|"), ("3", "")], ["job_id", "skills"])

    out_array = {(r["JobID"], r["Skill"]) for r in g.build_fact_offer_skills(array_df).collect()}
    out_string = {(r["JobID"], r["Skill"]) for r in g.build_fact_offer_skills(string_df).collect()}

    assert out_array == {("1", "Python"), ("1", "SQL")}     # duplicados eliminados
    assert out_string == {("2", "Python"), ("2", "SQL")}    # vacios descartados


# ---------------------------------------------------------------------------
# Tabla vacia
# ---------------------------------------------------------------------------

def test_tabla_vacia(spark):
    empty = spark.createDataFrame([], schema=SCHEMA)
    enriched = e.enrich(empty)
    assert enriched.count() == 0
    assert "salary_min_annual" in enriched.columns
    assert g.build_fact_offer_skills(empty).count() == 0


# ---------------------------------------------------------------------------
# dim_calendar
# ---------------------------------------------------------------------------

def test_dim_calendar(spark):
    cal = g.dim_calendar(spark, dt.date(2026, 9, 1), dt.date(2026, 9, 7))
    rows = cal.collect()
    assert len(rows) == 7
    first = rows[0]
    assert first["Date"] == dt.date(2026, 9, 1)
    assert first["DayName"] == "Tuesday"
    assert first["WeekDay"] == 2
    assert first["IsWeekend"] is False


# ---------------------------------------------------------------------------
# build_gold expone las tablas esperadas
# ---------------------------------------------------------------------------

def test_build_gold_tablas(spark):
    offers = e.enrich(_df(spark, [SAMPLE])).localCheckpoint(eager=True)
    tables = g.build_gold(spark, offers)
    assert set(tables) == {"fact_offers", "fact_offer_skills", "dim_currency",
                           "dim_skill_list", "dim_calendar"}
    assert tables["fact_offers"].count() == 1


# ---------------------------------------------------------------------------
# write_gold idempotente (reejecucion no duplica)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.name == "nt" and not os.environ.get("HADOOP_HOME"),
    reason="saveAsTable local en Windows requiere winutils/HADOOP_HOME")
def test_write_gold_reejecucion_idempotente(spark):
    schema = "gold_integration_test"
    catalog = "spark_catalog"
    spark.sql(f"CREATE DATABASE IF NOT EXISTS {schema}")
    try:
        offers = e.enrich(_df(spark, [SAMPLE])).localCheckpoint(eager=True)
        tables = {
            "fact_offers": g.build_fact_offers(offers, g.dim_currency(spark)),
            "fact_offer_skills": g.build_fact_offer_skills(offers),
        }
        g.write_gold(tables, catalog=catalog, schema=schema)
        g.write_gold(tables, catalog=catalog, schema=schema)
        assert spark.table(f"{catalog}.{schema}.fact_offers").count() == 1
        assert spark.table(f"{catalog}.{schema}.fact_offer_skills").count() == 2
    finally:
        spark.sql(f"DROP DATABASE IF EXISTS {schema} CASCADE")


# ---------------------------------------------------------------------------
# Reintento ante fallos transitorios de infraestructura (no de aserciones).
# En Windows/local Spark puede lanzar "Python worker failed to connect back"
# de forma intermitente en el primer job tras arrancar el worker.
# ---------------------------------------------------------------------------

def _retry_on_worker_flake(fn, attempts: int = 3, delay: int = 5):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last = None
        for _ in range(attempts):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(delay)
        raise last
    return wrapper


for _name, _obj in list(globals().items()):
    if _name.startswith("test_") and callable(_obj):
        globals()[_name] = _retry_on_worker_flake(_obj)
