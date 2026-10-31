"""Local benchmark of the instrumented incremental of ``Silver_Incremental``.

Spec 010, T-02. Run it from the repository root::

    python databricks_notebooks/tests/bench_silver_incremental.py \
        --label before

The benchmark reuses the *real* chain of ``test_silver_incremental.py`` (the
``_indeed_prepare`` / ``_indeed_build`` reference of the notebooks'
``prepare_bronze`` / ``build_offers``) so there is a single copy of the chain,
and it measures ``Silver_Incremental.run`` itself (window, affected groups,
recompute, counts and summary) with the instrumentation that T-02 adds.

What is measured
----------------
Three scenarios, each run twice (cold and hot: the second pass of the dev run
B was ~30 % faster because the JVM, the caches and the shuffle files are warm):

* ``full``: ``force_full=True``; the summary has ``window``, ``full_write``
  and ``target_count``.
* ``wide``: incremental with the default 7-day lookback, which covers the
  whole bronze of the fixture (the dev worst case).
* ``small``: incremental from the newest ``_ingest_date`` (~1 % of bronze).

The fixture is synthetic and sized like dev: **31,000 bronze rows** across
8 ``_ingest_date`` values (10-20..10-27); the 7-day lookback of the wide
scenario covers **all 8 values and all 31,000 rows**, and the newest date
(10-27) holds ~1 % of the rows for the small window; **12,000 silver rows**
(one per semantic group).

``sum(timings_s)`` does not have to equal ``elapsed_s``: the module also
spends time in planning, ``cache``/``unpersist``, ``DESCRIBE HISTORY`` and
``_config``, which are outside the timed actions. The table prints both
totals on purpose.

What is NOT measured
--------------------
* **The real Delta MERGE**: local Spark has no Delta, so ``merge_into_silver``
  is replaced by the reference implementation of the test module
  (``_reference_merge``). The ``merge`` step measures that reference merge,
  not the ``MERGE WITH SCHEMA EVOLUTION`` of the cluster.
* **The physical write of the full mode**: ``saveAsTable`` needs winutils on
  Windows, so it is replaced by an eager ``localCheckpoint`` of the transform
  published as the target view. The transform is measured; the Parquet/Delta
  write and its commit are not.
* The Spark UI, the real cluster config and the real ``operationMetrics``,
  which are read in the dev run of T-07/T-08.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_HERE, _ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from pyspark.sql import DataFrameWriter, SparkSession  # noqa: E402

import Enrich_Job_Offers_Dataframes as de  # noqa: E402
import Silver_Incremental as inc  # noqa: E402
import test_silver_incremental as helpers  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture size and shape (dev: bronze ~31k rows, silver ~12k rows)
# ---------------------------------------------------------------------------

GROUPS = 12_000          # semantic groups == silver rows
BRONZE_ROWS = 31_000     # bronze rows (several captures per group)
RECENT_ROWS = 310        # newest date: ~1 % of bronze (small window)
BASE_FIRST = dt.date(2026, 10, 20)
BASE_DAYS = 7            # wide window = 7-day lookback -> 10-20..10-26
RECENT_DATE = BASE_FIRST + dt.timedelta(days=BASE_DAYS)
SEED = 20261028
SALARY_BASE = 24_000.0

#: 20 roles x 4 seniorities = 80 titles (the dedup must not collapse them).
ROLES = (
    "Data Engineer", "Data Analyst", "Business Intelligence Analyst",
    "Machine Learning Engineer", "Data Scientist", "Analytics Engineer",
    "Data Architect", "Platform Engineer", "Backend Engineer",
    "Full Stack Developer", "Cloud Engineer", "DevOps Engineer",
    "Product Manager", "Product Owner", "Quantitative Analyst",
    "Statistician", "Database Administrator", "ETL Developer",
    "QA Automation Engineer", "Solutions Architect",
)
SENIORITIES = ("", "Junior ", "Senior ", "Lead ")
TITLES = tuple(f"{prefix}{role}" for role in ROLES for prefix in SENIORITIES)

#: Generic names that do not match any company alias (no group collapses).
COMPANIES = tuple(f"Company {index:03d}" for index in range(60))

#: Real cities: distinct after ``de.location_city_normalized`` (checked when
#: the fixture is built: the silver must have exactly ``GROUPS`` rows).
CITIES = (
    "Madrid", "Barcelona", "Valencia", "Sevilla", "Bilbao", "Málaga",
    "Zaragoza", "Murcia", "Alicante", "Valladolid", "Dublin", "Cork",
    "Galway", "London", "Manchester", "Berlin", "Munich", "Paris",
    "Lyon", "Amsterdam", "Rotterdam", "Lisbon", "Porto", "Milan", "Rome",
)


def synthetic_bronze() -> list:
    """31,000 rows shaped like ``helpers._indeed_raw`` (deterministic).

    * One base capture per semantic group, ``_ingest_date`` cycling over the
      7 base dates (10-20..10-26) and ``posted_date`` a few days older.
    * 18,690 extra captures of random groups (2-3 captures per group).
    * 310 groups get one extra capture on ``RECENT_DATE`` (10-27; its posted
      date is the newest one, so it wins the dedup and the watermark becomes
      that date). That is the 8th ``_ingest_date`` value and the ~1 % of the
      small window.

    Titles, companies and cities are drawn as unique triples from an 80 x 60
    x 25 grid (``rng.sample``), so the semantic dedup keeps 12,000 groups and
    the fixture does not collapse to a handful of them.
    """
    rng = random.Random(SEED)
    combos = [(title, company, city)
              for title in TITLES
              for company in COMPANIES
              for city in CITIES]
    groups = rng.sample(combos, GROUPS)
    rows: list = []

    def add(group, ingest, posted):
        title, company, city = group
        index = len(rows) + 1
        rows.append({
            "job_id": f"indeed-{index:06d}",
            "title": title,
            "company_name": company,
            "location_city": city,
            "posted_date": posted,
            "salary": SALARY_BASE + (index % 60) * 500.0,
            "_ingest_date": ingest,
        })

    for index, group in enumerate(groups):
        ingest = BASE_FIRST + dt.timedelta(days=index % BASE_DAYS)
        add(group, ingest, ingest - dt.timedelta(days=index % 3))
    for _ in range(BRONZE_ROWS - GROUPS - RECENT_ROWS):
        group = groups[rng.randrange(GROUPS)]
        ingest = BASE_FIRST + dt.timedelta(days=rng.randrange(BASE_DAYS))
        add(group, ingest, ingest - dt.timedelta(days=rng.randrange(3)))
    for group in rng.sample(groups, RECENT_ROWS):
        add(group, RECENT_DATE, RECENT_DATE)
    return rows


# ---------------------------------------------------------------------------
# Local stand-ins for the pieces that need Delta / winutils
# ---------------------------------------------------------------------------

_ORIGINAL_SAVE_AS_TABLE = DataFrameWriter.saveAsTable


def _local_save_as_table(writer, name, *args, **kwargs):
    """Stand-in for ``saveAsTable``: materialise the transform, no files.

    Windows has no winutils, so a managed table (and a Parquet write) fails
    with ``HADOOP_HOME and hadoop.home.dir are unset``. The transform is
    materialised with ``localCheckpoint(eager=True)`` and published as a
    temporary view with the target name, which is what ``_run_once`` counts
    afterwards. The physical write is **not** measured.
    """
    frame = writer._df.localCheckpoint(eager=True)
    frame.createOrReplaceTempView(name)


def _install_local_merge():
    """Replace the Delta MERGE with the reference merge of the tests."""

    def fake(spark, target_table, merge_src, *, view_name):
        helpers._reference_merge(spark, target_table, merge_src,
                                 view_name=view_name)

    inc.merge_into_silver = fake


# ---------------------------------------------------------------------------
# JVM log capture (the codegen warning is logged by Spark's CodeGenerator)
# ---------------------------------------------------------------------------

class Log4jCodegenCapture:
    """Capture the ``CodeGenerator`` errors of the JVM in a file.

    Spark logs «Code grows beyond 64 KB» from
    ``org.apache.spark.sql.catalyst.expressions.codegen.CodeGenerator``. The
    JVM console handle cannot be read from Python on Windows (verified: the
    process ``fd 2`` does not receive it), so a log4j2 ``FileAppender`` is
    attached to that logger for the duration of the benchmark and the file is
    read afterwards. It captures the real warning, not an inference.
    """

    LOGGER = ("org.apache.spark.sql.catalyst.expressions.codegen."
              "CodeGenerator")
    APPENDER = "bench-codegen-capture"

    def __init__(self, spark):
        self._jvm = spark._jvm
        self._path = os.path.join(
            tempfile.mkdtemp(prefix="bench-log4j-"), "codegen.log")
        self._appender = None
        self._context = None
        self._logger_config = None
        self._text = ""

    def __enter__(self):
        jvm = self._jvm
        builder = (jvm.org.apache.logging.log4j.core.appender.FileAppender
                   .newBuilder())
        builder.withName(self.APPENDER)
        builder.withFileName(self._path)
        builder.withAppend(False)
        builder.withImmediateFlush(True)
        layout = (jvm.org.apache.logging.log4j.core.layout.PatternLayout
                  .newBuilder().withPattern("%m%n").build())
        builder.withLayout(layout)
        self._appender = builder.build()
        self._appender.start()
        self._context = jvm.org.apache.logging.log4j.LogManager.getContext(
            False)
        config = self._context.getConfiguration()
        config.addAppender(self._appender)
        self._logger_config = config.getLoggerConfig(self.LOGGER)
        self._logger_config.addAppender(
            self._appender, jvm.org.apache.logging.log4j.Level.ERROR, None)
        self._context.updateLoggers()
        # Create the logger AFTER registering the appender: log4j2 copies the
        # appenders of the closest config when the LoggerConfig is built, so
        # an appender added to a parent later would not reach this logger.
        jvm.org.apache.logging.log4j.LogManager.getLogger(self.LOGGER)
        return self

    def __exit__(self, *exc_info):
        self._logger_config.removeAppender(self.APPENDER)
        self._context.getConfiguration().removeAppender(self.APPENDER)
        self._context.updateLoggers()
        self._appender.stop()
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8",
                      errors="replace") as log:
                self._text = log.read()
        return False

    def text(self) -> str:
        """Captured log (read from the file if ``__exit__`` has not run)."""
        if not self._text and os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8",
                      errors="replace") as log:
                self._text = log.read()
        return self._text


def explain_text(df, mode: str) -> str:
    """``DataFrame.explain`` as text (PySpark only prints it, returns None)."""
    jvm = df.sparkSession._jvm
    return jvm.PythonSQLUtils.explainString(df._jdf.queryExecution(), mode)


def codegen_text(spark, df) -> str:
    """Generated Java code of ``df`` (``explain("codegen")``).

    Spark 4.2 reports «Found 0 WholeStageCodegen subtrees» for an adaptive
    plan (the AQE wrapper is not descended), so AQE is switched off **only**
    for the explanation: the generated expressions are the same, and the
    benchmark runs still use the session config (AQE on, as in dev). The
    previous value is restored, or unset when it was not explicit.
    """
    previous = spark.conf.get("spark.sql.adaptive.enabled", None)
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    try:
        return explain_text(df, "codegen")
    finally:
        if previous is None:
            spark.conf.unset("spark.sql.adaptive.enabled")
        else:
            spark.conf.set("spark.sql.adaptive.enabled", previous)


# ---------------------------------------------------------------------------
# Spark session and runs
# ---------------------------------------------------------------------------

def build_spark() -> SparkSession:
    os.environ["PYSPARK_PYTHON"] = sys.executable
    os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
    warehouse = tempfile.mkdtemp(prefix="bench-spark-warehouse-")
    return (
        SparkSession.builder
        .master("local[4]")
        .appName("bench-silver-incremental")
        .config("spark.ui.enabled", "false")
        # Dev runs the job with 8 shuffle partitions (T-03).
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.python.worker.timeout", "180")
        .config("spark.network.timeout", "300")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )


def run_once(spark, target, prepared, **kwargs):
    """One instrumented run of the production module."""
    return inc.run(
        spark,
        target,
        prepared,
        source="indeed",
        transform=helpers._indeed_build,
        normalize_company=helpers._reference_normalize_company,
        normalize_city_column=de.location_city_normalized_column,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _format(value) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_table(title: str, headers: list, rows: list) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(_format(value)))
    print()
    print(title)
    print("  ".join(header.ljust(widths[index])
                    for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(_format(value).ljust(widths[index])
                        for index, value in enumerate(row)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--label", default="sin-etiqueta",
        help="texto de la cabecera para comparar antes/despues",
    )
    args = parser.parse_args(argv)
    label = args.label

    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")
    spark_version = spark.version
    DataFrameWriter.saveAsTable = _local_save_as_table
    _install_local_merge()
    results: list = []
    captured = ""
    rows: list = []
    initial_rows = 0
    setup_s = 0.0
    codegen = ""
    codegen_size = 0
    notice = False

    try:
        with Log4jCodegenCapture(spark) as jvm_log:
            # Fixture: the same chain the notebook uses (prepare + build).
            rows = synthetic_bronze()
            prepared = helpers._indeed_prepare(
                helpers._indeed_raw(spark, rows))

            setup_t0 = time.monotonic()
            initial_silver = helpers._indeed_build(prepared).localCheckpoint(
                eager=True)
            initial_rows = initial_silver.count()
            setup_s = time.monotonic() - setup_t0
            if initial_rows != GROUPS:
                raise SystemExit(
                    f"Fixture invalida: la silver tiene {initial_rows} filas "
                    f"y se esperaban {GROUPS} (el dedup colapso grupos: "
                    "revisa los titulos, empresas y ciudades sinteticos)."
                )
            initial_silver.createOrReplaceTempView("__bench_full")
            initial_silver.createOrReplaceTempView("__bench_wide")
            initial_silver.createOrReplaceTempView("__bench_small")

            scenarios = (
                ("full  (force_full=True)", "__bench_full",
                 {"force_full": True}),
                ("wide  (lookback 7 d = todo el bronce)", "__bench_wide", {}),
                ("small (desde la fecha reciente, ~1 %)", "__bench_small",
                 {"from_date": RECENT_DATE}),
            )
            for scenario, target, kwargs in scenarios:
                for number in (1, 2):
                    started = time.monotonic()
                    summary = run_once(spark, target, prepared, **kwargs)
                    elapsed = time.monotonic() - started
                    results.append({
                        "scenario": scenario,
                        "pass": number,
                        "wall_s": elapsed,
                        "summary": summary,
                    })
            codegen = codegen_text(spark, helpers._indeed_build(prepared))
            codegen_size = len(codegen)
        captured = jvm_log.text()
        notice = "Code grows beyond 64 KB" in captured
    finally:
        DataFrameWriter.saveAsTable = _ORIGINAL_SAVE_AS_TABLE
        spark.stop()

    # ----------------------------------------------------------------- report
    print("=" * 78)
    print(f"Bench Silver_Incremental — label: {label}")
    print("=" * 78)
    print(f"Spark {spark_version} | master=local[4] | "
          "spark.sql.shuffle.partitions=8")
    print(f"Datos: {len(rows):,} filas de bronce, {GROUPS:,} grupos, "
          f"silver destino {initial_rows:,} filas")
    print(f"Fixture: fechas {BASE_FIRST}..{RECENT_DATE} "
          f"({BASE_DAYS} dias de ventana ancha), "
          f"{RECENT_ROWS} filas en {RECENT_DATE} (~1 %).")
    print(f"Setup (cadena completa una vez): {setup_s:.1f} s")
    print("NO medido en local: el MERGE real de Delta (se usa la "
          "implementacion de referencia de los tests)")
    print("NO medido en local: la escritura fisica del modo full "
          "(localCheckpoint en vez de saveAsTable)")

    summary_rows = []
    for result in results:
        summary = result["summary"]
        summary_rows.append([
            result["scenario"],
            f"pase {result['pass']}",
            round(result["wall_s"], 1),
            summary["mode"],
            summary["changed_rows"],
            summary["affected_groups"],
            summary["recomputed_groups"],
            summary["merge_rows"],
            summary["target_rows"],
        ])
    print_table(
        "Resumen de negocio por escenario y pase",
        ["escenario", "pase", "wall_s", "mode", "changed", "affected",
         "recomputed", "merge_rows", "target"],
        summary_rows,
    )

    steps = []
    for result in results:
        for step in result["summary"]["timings_s"]:
            if step not in steps:
                steps.append(step)
    step_rows = []
    headers = ["timings_s", *[f"{result['scenario'].split()[0]}"
                               f"#{result['pass']}" for result in results]]
    for step in steps:
        step_rows.append([
            step,
            *[result["summary"]["timings_s"].get(step, "")
              for result in results],
        ])
    step_rows.append([
        "TOTAL (suma de pasos)",
        *[round(sum(result["summary"]["timings_s"].values()), 3)
          for result in results],
    ])
    step_rows.append([
        "elapsed_s (resumen)",
        *[result["summary"]["elapsed_s"] for result in results],
    ])
    step_rows.append([
        "wall_s (reloj del banco)",
        *[round(result["wall_s"], 3) for result in results],
    ])
    print_table("Tiempos por paso (segundos)", headers, step_rows)

    print()
    print("Config efectiva del resumen (primer pase de cada escenario):")
    for result in results:
        if result["pass"] != 1:
            continue
        print(f"  {result['scenario']}: {result['summary']['config']}")
    metrics = [result["summary"]["merge_metrics"] for result in results]
    if all(value is None for value in metrics):
        print(f"merge_metrics: None en las {len(results)} pasadas "
              "(sin Delta local; en el cluster lo lee DESCRIBE HISTORY)")
    else:
        print(f"merge_metrics: {metrics}")
    print()
    print(f"Codegen de build_offers sobre el bronce preparado: "
          f"{codegen_size:,} caracteres (len de explain(\"codegen\"); AQE "
          "desactivado solo para explicar: con AQE, Spark 4.2 informa de 0 "
          "subtrees)")
    first_line = codegen.splitlines()[0] if codegen else ""
    if first_line:
        print(f"  {first_line}")
    if notice:
        print("Aviso «Code grows beyond 64 KB»: SI (capturado del log4j "
              "del JVM durante la ejecucion)")
    else:
        print("Aviso «Code grows beyond 64 KB»: no en el log del JVM")
    print()
    print("Recordatorio: el MERGE real y la escritura fisica del modo full "
          "no se miden en local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
