"""Tests for the web export module: pure geography and Spark projections.

The pure tests cover ``geo_country``/``geo_region`` and the TMDL fidelity of
``REGION_MAP`` without Spark. The file-operation routing tests use a fake
``dbutils`` and a fake Hadoop FileSystem (via the ``_get_dbutils`` seam), so
they also run without Spark: ``dbutils.fs`` on Databricks, Hadoop locally.
The integration tests (pyspark, same fixture pattern as
``test_integration_spark.py``) lock the public column contract of every
exported table and the geographic parity between ``project_fact_offers`` and
the pure functions. If pyspark is not installed, only the Spark tests skip.
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import tempfile
import time
import types
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
# File operation routing: dbutils.fs on Databricks, Hadoop FileSystem locally
# ---------------------------------------------------------------------------


class _FakeFileInfo:
    """Databricks ``FileInfo`` stand-in (only path/name/size are used)."""

    def __init__(self, path, size=0):
        self.path = path
        self.name = path.rstrip("/").rsplit("/", 1)[-1]
        self.size = size


class _FakeFs:
    """Records ``dbutils.fs`` calls and can be told to raise on ``rm``."""

    def __init__(self, entries=()):
        self.calls = []
        self.entries = list(entries)
        self.rm_error = None

    def rm(self, path, recurse=False):
        self.calls.append(("rm", path, recurse))
        if self.rm_error is not None:
            raise self.rm_error
        return True

    def mv(self, source, target):
        self.calls.append(("mv", source, target))
        return True

    def ls(self, directory):
        self.calls.append(("ls", directory))
        return list(self.entries)

    def put(self, path, text, overwrite=False):
        self.calls.append(("put", path, text, overwrite))
        return True


class _FakeDbutils:
    def __init__(self, fs):
        self.fs = fs


def _use_fake_dbutils(monkeypatch, fs):
    """Route the module's file operations through a fake ``dbutils``."""
    fake = _FakeDbutils(fs)
    monkeypatch.setattr(w, "_get_dbutils", lambda spark: fake)
    return fake


class _FakeJPath:
    """Minimal Hadoop ``Path``: resolves its FileSystem from the conf."""

    def __init__(self, path):
        self.path = str(path)

    def getFileSystem(self, conf):
        return conf.fs

    def toString(self):
        return self.path

    def getName(self):
        return self.path.rstrip("/").rsplit("/", 1)[-1]


class _FakeHadoopStream:
    def __init__(self, calls):
        self.calls = calls

    def write(self, data):
        self.calls.append(("write", bytes(data)))

    def close(self):
        self.calls.append(("close",))


class _FakeHadoopFs:
    """Records the Hadoop FileSystem calls of the local branch."""

    def __init__(self, statuses=(), size=0):
        self.calls = []
        self.statuses = list(statuses)
        self.size = size

    def delete(self, jpath, recursive):
        self.calls.append(("delete", jpath.toString(), recursive))
        return True

    def rename(self, source, target):
        self.calls.append(("rename", source.toString(), target.toString()))
        return True

    def listStatus(self, directory):
        return [types.SimpleNamespace(
                    getPath=lambda path=status: _FakeJPath(path))
                for status in self.statuses]

    def getFileStatus(self, jpath):
        return types.SimpleNamespace(getLen=lambda: self.size)

    def create(self, jpath, overwrite):
        self.calls.append(("create", jpath.toString(), overwrite))
        return _FakeHadoopStream(self.calls)


def _fake_spark(hadoop_fs):
    """Spark stand-in exposing only the JVM/conf access the module uses."""
    jvm = types.SimpleNamespace(org=types.SimpleNamespace(
        apache=types.SimpleNamespace(hadoop=types.SimpleNamespace(
            fs=types.SimpleNamespace(Path=_FakeJPath)))))
    conf = types.SimpleNamespace(fs=hadoop_fs)
    jsc = types.SimpleNamespace(hadoopConfiguration=lambda: conf)
    return types.SimpleNamespace(_jvm=jvm, _jsc=jsc)


def test_get_dbutils_returns_none_without_databricks_runtime(monkeypatch):
    monkeypatch.delenv("DATABRICKS_RUNTIME_VERSION", raising=False)
    assert w._get_dbutils(object()) is None


def test_get_dbutils_returns_db_utils_on_databricks(monkeypatch):
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    sentinel = object()
    fake_module = types.ModuleType("pyspark.dbutils")
    fake_module.DBUtils = lambda spark: (spark, "dbutils")
    monkeypatch.setitem(sys.modules, "pyspark.dbutils", fake_module)
    assert w._get_dbutils(sentinel) == (sentinel, "dbutils")


def test_get_dbutils_falls_back_to_none_on_any_failure(monkeypatch):
    monkeypatch.setenv("DATABRICKS_RUNTIME_VERSION", "15.4")
    # ``DBUtils`` cannot be imported (no dbutils on the runtime).
    monkeypatch.setitem(sys.modules, "pyspark.dbutils", None)
    assert w._get_dbutils(object()) is None

    # ``DBUtils`` exists but building it fails.
    fake_module = types.ModuleType("pyspark.dbutils")

    def _boom(spark):
        raise RuntimeError("dbutils unavailable")

    fake_module.DBUtils = _boom
    monkeypatch.setitem(sys.modules, "pyspark.dbutils", fake_module)
    assert w._get_dbutils(object()) is None


def test_fs_delete_uses_dbutils_rm(monkeypatch):
    fs = _FakeFs()
    _use_fake_dbutils(monkeypatch, fs)

    w._fs_delete(None, "abfss://landing@acct.dfs.core.windows.net/gold/x")
    w._fs_delete(None, "abfss://landing@acct.dfs.core.windows.net/gold/__tmp",
                 recursive=True)

    assert fs.calls == [
        ("rm", "abfss://landing@acct.dfs.core.windows.net/gold/x", False),
        ("rm", "abfss://landing@acct.dfs.core.windows.net/gold/__tmp", True),
    ]


def test_fs_delete_tolerates_only_not_found_errors(monkeypatch):
    fs = _FakeFs()
    _use_fake_dbutils(monkeypatch, fs)
    path = "abfss://landing@acct.dfs.core.windows.net/gold/x.parquet"

    # First run: the final file does not exist yet and rm raises.
    fs.rm_error = Exception(
        "java.io.FileNotFoundException: Path does not exist: " + path)
    w._fs_delete(None, path)  # must not raise

    # Real failures (missing storage credential) must propagate.
    fs.rm_error = Exception(
        "Invalid configuration value detected for fs.azure.account.key")
    with pytest.raises(Exception, match="fs.azure.account.key"):
        w._fs_delete(None, path)


def test_single_part_file_selects_part_via_dbutils(monkeypatch):
    base = "abfss://landing@acct.dfs.core.windows.net/gold/__tmp_fact_offers"
    fs = _FakeFs(entries=[
        _FakeFileInfo(f"{base}/_SUCCESS"),
        _FakeFileInfo(f"{base}/part-00000-abc-c000.snappy.parquet", size=10),
    ])
    _use_fake_dbutils(monkeypatch, fs)

    assert (w._single_part_file(None, base)
            == f"{base}/part-00000-abc-c000.snappy.parquet")
    assert fs.calls == [("ls", base)]


def test_single_part_file_requires_exactly_one_part(monkeypatch):
    fs = _FakeFs(entries=[_FakeFileInfo("/tmp/__tmp_x/_SUCCESS")])
    _use_fake_dbutils(monkeypatch, fs)

    with pytest.raises(RuntimeError, match="encontrados 0"):
        w._single_part_file(None, "/tmp/__tmp_x")

    fs.entries = [_FakeFileInfo("/tmp/__tmp_x/part-a"),
                  _FakeFileInfo("/tmp/__tmp_x/part-b")]
    with pytest.raises(RuntimeError, match="encontrados 2"):
        w._single_part_file(None, "/tmp/__tmp_x")


def test_fs_rename_uses_dbutils_mv(monkeypatch):
    fs = _FakeFs()
    _use_fake_dbutils(monkeypatch, fs)

    w._fs_rename(None, "/tmp/__tmp_x/part-00000.parquet", "/tmp/x.parquet")

    assert fs.calls == [
        ("mv", "/tmp/__tmp_x/part-00000.parquet", "/tmp/x.parquet")]


def test_fs_size_reads_size_via_dbutils(monkeypatch):
    fs = _FakeFs(entries=[_FakeFileInfo("/tmp/x.parquet", size=123456)])
    _use_fake_dbutils(monkeypatch, fs)

    assert w._fs_size(None, "/tmp/x.parquet") == 123456
    assert fs.calls == [("ls", "/tmp/x.parquet")]


def test_fs_write_text_uses_dbutils_put(monkeypatch):
    fs = _FakeFs()
    _use_fake_dbutils(monkeypatch, fs)

    w._fs_write_text(None, "/tmp/meta.json", '{"a": 1}')

    assert fs.calls == [("put", "/tmp/meta.json", '{"a": 1}', True)]


def test_delete_checksum_is_noop_with_dbutils(monkeypatch):
    fs = _FakeFs()
    _use_fake_dbutils(monkeypatch, fs)

    w._delete_checksum(None, "/tmp/meta.json")

    assert fs.calls == []


def test_file_operations_use_hadoop_when_dbutils_is_none(monkeypatch):
    monkeypatch.setattr(w, "_get_dbutils", lambda spark: None)
    hadoop_fs = _FakeHadoopFs(
        statuses=["/tmp/__tmp_x/part-00000.parquet"], size=7)
    spark = _fake_spark(hadoop_fs)

    w._fs_delete(spark, "/tmp/x.parquet")
    w._fs_delete(spark, "/tmp/__tmp_x", recursive=True)
    assert (w._single_part_file(spark, "/tmp/__tmp_x")
            == "/tmp/__tmp_x/part-00000.parquet")
    w._fs_rename(spark, "/tmp/__tmp_x/part-00000.parquet", "/tmp/x.parquet")
    assert w._fs_size(spark, "/tmp/x.parquet") == 7
    w._fs_write_text(spark, "/tmp/meta.json", "{}")

    assert hadoop_fs.calls[:3] == [
        ("delete", "/tmp/x.parquet", False),
        ("delete", "/tmp/__tmp_x", True),
        ("rename", "/tmp/__tmp_x/part-00000.parquet", "/tmp/x.parquet"),
    ]
    assert ("write", b"{}") in hadoop_fs.calls
    # The Hadoop branch removes the local .crc of the rewritten file.
    assert hadoop_fs.calls[-1] == ("delete", "/tmp/.meta.json.crc", False)


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

    # Synthetic Gold fact_offers: exactly the producer contract, so the build
    # tests exercise the real input shape.
    _GOLD_TYPES = {
        "posted_date": T.DateType(),
        "IsValidPostingDate": T.BooleanType(),
        "is_salary_available": T.BooleanType(),
        "SalaryMinAnnual_EUR": T.DoubleType(),
        "SalaryMaxAnnual_EUR": T.DoubleType(),
        "SalaryMidAnnual_EUR": T.DoubleType(),
    }
    GOLD_FACT_SCHEMA = T.StructType([
        T.StructField(name, _GOLD_TYPES.get(name, T.StringType()), True)
        for name in g.FACT_OFFERS_COLUMNS
    ])
    GOLD_FACT_DEFAULTS = {f.name: None for f in GOLD_FACT_SCHEMA.fields}
else:  # pragma: no cover - Spark tests skip without pyspark
    FACT_SOURCE_SCHEMA = None
    FACT_DEFAULTS = {}
    GOLD_FACT_SCHEMA = None
    GOLD_FACT_DEFAULTS = {}

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
         "IsValidPostingDate": True, "SalaryMinAnnual_EUR": 40000.0,
         "SalaryMaxAnnual_EUR": 50000.0, "SalaryMidAnnual_EUR": 45000.0},
        {"job_id": "2", "title": "Analyst", "location_city": "Berlín",
         "location_region": "Berlín", "location_country": "Alemania",
         "posted_date": dt.date(2026, 9, 3), "source_scraper": "LinkedIn",
         "IsValidPostingDate": True, "SalaryMinAnnual_EUR": 30000.0,
         "SalaryMaxAnnual_EUR": 36000.0, "SalaryMidAnnual_EUR": 33000.0},
    ])
    skills = spark.createDataFrame(
        [("1", "Python"), ("1", "SQL"), ("2", "SQL")], ["JobID", "Skill"])
    skill_list = spark.createDataFrame(
        [("Python", "Programming Languages"), ("SQL", "Programming Languages")],
        ["SkillName", "SkillCategory"])
    calendar = g.dim_calendar(spark, dt.date(2026, 9, 1), dt.date(2026, 9, 3))
    return {"fact_offers": fact, "fact_offer_skills": skills,
            "dim_skill_list": skill_list, "dim_calendar": calendar}


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


@pytest.fixture
def local_export_io(monkeypatch):
    """Run the write tests on Windows hosts without Hadoop winutils.

    Spark's writer needs ``winutils.exe`` (HADOOP_HOME) to chmod output
    directories, and Hadoop's local FileSystem metadata raises
    ``UnsatisfiedLinkError`` without the native library. On such hosts the
    low-level writer and the file-operation helpers are replaced with
    os/pyarrow equivalents, so the export layout (one file per table), rename,
    idempotency, size and meta.json logic stay under test. Linux, Databricks
    and Windows with HADOOP_HOME run the real Spark + Hadoop path (T-06 runs
    it for real on a cluster).
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


# build_web_export / collect_export_stats / write_web_export


@requires_spark
def test_build_web_export_projects_four_tables(spark):
    gold = _gold_tables(spark)
    tables = w.build_web_export(spark, gold["fact_offers"],
                                gold["fact_offer_skills"],
                                gold["dim_skill_list"], gold["dim_calendar"])
    assert set(tables) == {"fact_offers", "fact_offer_skills",
                           "dim_skill_list", "dim_calendar"}
    assert tables["fact_offers"].columns == list(w.WEB_FACT_COLUMNS)
    assert tables["fact_offer_skills"].columns == list(w.WEB_OFFER_SKILL_COLUMNS)
    assert tables["dim_skill_list"].columns == list(w.WEB_SKILL_LIST_COLUMNS)
    assert tables["dim_calendar"].columns == list(w.WEB_CALENDAR_COLUMNS)
    offers = {r["job_id"]: r for r in tables["fact_offers"].collect()}
    assert offers["1"]["GeoCountry"] == "Spain"
    assert offers["1"]["GeoRegion"] == "Madrid"
    assert offers["2"]["GeoCountry"] == "Germany"
    assert offers["2"]["GeoRegion"] == "(Other)"
    assert tables["fact_offer_skills"].count() == 3


@requires_spark
def test_collect_export_stats_counts_sources_and_data_date(spark):
    gold = _gold_tables(spark)
    tables = w.build_web_export(spark, gold["fact_offers"],
                                gold["fact_offer_skills"],
                                gold["dim_skill_list"], gold["dim_calendar"])
    table_counts, source_counts, data_date = w.collect_export_stats(tables)
    assert table_counts == {"fact_offers": 2, "fact_offer_skills": 3,
                            "dim_skill_list": 2, "dim_calendar": 3}
    assert source_counts == {"Indeed": 1, "LinkedIn": 1}
    assert data_date == dt.date(2026, 9, 3)


@requires_spark
def test_collect_export_stats_ignores_blank_sources_and_empty_is_none(spark):
    fact = _gold_fact_df(spark, [
        {"job_id": "1", "source_scraper": "Indeed"},
        {"job_id": "2", "source_scraper": ""},
        {"job_id": "3", "source_scraper": "   "},
        {"job_id": "4", "source_scraper": None},
    ])
    empty = spark.createDataFrame([], "JobID string, Skill string")
    table_counts, source_counts, data_date = w.collect_export_stats(
        {"fact_offers": fact, "fact_offer_skills": empty})
    assert table_counts == {"fact_offers": 4, "fact_offer_skills": 0}
    assert source_counts == {"Indeed": 1}
    assert data_date is None


@requires_spark
def test_write_web_export_single_files_meta_and_idempotent(
        spark, tmp_path, local_export_io):
    gold = _gold_tables(spark)
    tables = w.build_web_export(spark, gold["fact_offers"],
                                gold["fact_offer_skills"],
                                gold["dim_skill_list"], gold["dim_calendar"])
    table_counts, source_counts, data_date = w.collect_export_stats(tables)
    meta = w.build_meta(table_counts, source_counts, data_date,
                        generated_at=dt.datetime(2026, 10, 6, 12, 0,
                                                 tzinfo=dt.timezone.utc))
    names = ["fact_offers", "fact_offer_skills", "dim_skill_list",
             "dim_calendar"]
    dest = tmp_path / "web_export"

    written = w.write_web_export(spark, tables, dest, meta=meta)

    expected_files = sorted([f"{name}.parquet" for name in names]
                            + ["meta.json"])
    assert sorted(p.name for p in dest.iterdir()) == expected_files
    for name in names:
        path = dest / f"{name}.parquet"
        assert path.is_file()
        read = spark.read.parquet(path.as_uri())
        assert read.columns == tables[name].columns
        assert read.count() == tables[name].count()
        if name == "fact_offers":
            # Types must survive the round-trip. The fixture gives these
            # columns values on purpose: all-null columns degrade to string in
            # the local pyarrow fallback writer, which would hide a typing
            # regression instead of testing it.
            dtypes = dict(read.dtypes)
            assert dtypes["job_id"] == "string"
            assert dtypes["posted_date"] == "date"
            assert dtypes["IsValidPostingDate"] == "boolean"
            assert dtypes["SalaryMidAnnual_EUR"] == "double"
    payload = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert payload["size_bytes"] > 0
    assert payload["size_bytes"] == written["size_bytes"]
    assert payload["mode"] == "full"
    assert payload["generated_at"] == "2026-10-06T12:00:00Z"
    assert payload["data_date"] == "2026-09-03"
    assert payload["tables"] == {"fact_offers": 2, "fact_offer_skills": 3,
                                 "dim_skill_list": 2, "dim_calendar": 3}

    # Idempotent: the second run leaves the same files and rows behind.
    w.write_web_export(spark, tables, dest, meta=meta)
    assert sorted(p.name for p in dest.iterdir()) == expected_files
    for name in names:
        path = dest / f"{name}.parquet"
        assert spark.read.parquet(path.as_uri()).count() == \
            tables[name].count()


@requires_spark
def test_write_web_export_size_provider_forces_aggregated(
        spark, tmp_path, local_export_io):
    gold = _gold_tables(spark)
    tables = w.build_web_export(spark, gold["fact_offers"],
                                gold["fact_offer_skills"],
                                gold["dim_skill_list"], gold["dim_calendar"])
    meta = w.build_meta({"fact_offers": 2}, {"Indeed": 1},
                        dt.date(2026, 9, 3))
    dest = tmp_path / "aggregated"
    big = w.DEFAULT_MAX_EXPORT_BYTES + 1

    written = w.write_web_export(spark, tables, dest, meta=meta,
                                 size_provider=lambda: big)

    assert written["size_bytes"] == big
    assert written["mode"] == "aggregated"
    payload = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert payload["size_bytes"] == big
    assert payload["mode"] == "aggregated"


def test_module_uses_no_python_udfs():
    # The export is columnar: geography must stay in F.when / joins.
    source = Path(w.__file__).read_text(encoding="utf-8")
    assert "udf(" not in source
