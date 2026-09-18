"""Tests del enriquecimiento Spark (src.enrich_spark).

Requieren pyspark; se saltan donde no este instalado (importorskip).
Verifican:
 - skills: case-insensitive, word boundaries, acronimos y alias normalizados.
 - experience_level: cascada por seniority y terminos ES/EN.
 - employment_type, role_category y salario anual a partir de titulo/descripcion.
 - enrich(only_if_missing=True) conserva lo ya rellenado.
"""
from __future__ import annotations

import pytest

pyspark = pytest.importorskip("pyspark")  # noqa: F401  (skip si no hay Spark)

from pyspark.sql import SparkSession
from pyspark.sql.types import ArrayType, StringType

from src.enrich_spark import enrich, role_category_debug

_SCHEMA = (
    "job_id string, title string, description_clean string, "
    "skills array<string>, salary_min double, salary_max double, "
    "salary_period string, experience_level string, employment_type string"
)


@pytest.fixture(scope="module")
def spark():
    s = SparkSession.builder.master("local[1]") \
        .appName("test_enrich_spark") \
        .config("spark.ui.enabled", "false") \
        .getOrCreate()
    s.sparkContext.setLogLevel("ERROR")
    yield s
    s.stop()


def _df(spark, rows):
    return spark.createDataFrame(rows, schema=_SCHEMA)


class TestSkills:
    def test_case_insensitive_y_word_boundaries(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Engineer", "Usamos python y PostgreSQL para ETL con AWS.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert "Python" in out["skills"]
        assert "ETL" in out["skills"]
        assert "AWS" in out["skills"]
        assert "SQL" not in out["skills"]  # word boundary: no sale de PostgreSQL

    def test_acronimos_y_alias_normalizados(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Engineer",
              "Requisitos: PowerBI y SQL con scikit learn.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["skills"] == ["Power BI", "SQL", "Scikit-learn"]

    def test_sin_match_devuelve_vacio_y_tipo_array(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Engineer", "sin tecnologia",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False)
        assert out.schema["skills"].dataType == ArrayType(StringType())
        assert out.collect()[0]["skills"] == []


class TestExperience:
    def test_cascada_varias_menciones_gana_la_mayor(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Engineer", "Becario junior en analitica.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["experience_level"] == "Practicas"

    def test_director_vence_a_senior(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Analyst", "Directora de datos senior, lidera el equipo.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["experience_level"] == "Director"

    def test_entry_junior_es(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Analyst", "Puesto junior con nivel de entrada en SQL.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["experience_level"] == "Entry"

    def test_sin_seniority_devuelve_vacio(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Analyst", "Gran equipo.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["experience_level"] == ""


class TestEmploymentType:
    def test_practicas_gana_a_full_time(self, spark):
        # "One year internship, full-time position" -> Practicas (mas especifico)
        df = spark.createDataFrame(
            [("1", "Data Engineer", "One year internship, full-time position.",
              [], None, None, None, "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["employment_type"] == "Practicas"

    def test_media_jornada_y_contrato(self, spark):
        df = spark.createDataFrame(
            [
                ("1", "Data Analyst", "Puesto de media jornada en Madrid.",
                 [], None, None, None, "", ""),
                ("2", "Data Engineer", "Contrato temporal de 6 meses.",
                 [], None, None, None, "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert out[0]["employment_type"] == "Media jornada"
        assert out[1]["employment_type"] == "Contrato"


class TestRoleCategory:
    def test_categorias_desde_titulo(self, spark):
        df = spark.createDataFrame(
            [
                ("1", "Senior Data Engineer", "", [], None, None, None, "", ""),
                ("2", "Data Analyst (Power BI)", "", [], None, None, None, "", ""),
                ("3", "Machine Learning Engineer", "", [], None, None, None, "", ""),
                ("4", "Analytics Engineer", "", [], None, None, None, "", ""),
                ("5", "Data Scientist", "", [], None, None, None, "", ""),
                ("6", "Contable senior", "", [], None, None, None, "", ""),
                ("7", "Analista Senior de Datos", "", [], None, None, None, "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert [r["role_category"] for r in out] == [
            "Data Engineer",
            "Data Analyst & BI",
            "Machine Learning & AI",
            "Analytics Engineer",
            "Data Scientist",
            "Other",
            "Data Analyst & BI",
        ]

    def test_debug_muestra_reglas_que_matchean(self, spark):
        assert role_category_debug("Data Analyst (Senior)") == ["Data Analyst & BI"]
        assert role_category_debug("Analista Senior de Datos")[0] == "Data Analyst & BI"
        assert role_category_debug("Científico de Datos")[0] == "Data Scientist"
        assert role_category_debug("Contable senior") == []


class TestSalaryAnnual:
    def test_normaliza_por_periodo(self, spark):
        df = spark.createDataFrame(
            [
                ("1", "", "", [], 40000.0, 50000.0, "year", "", ""),
                ("2", "", "", [], 3000.0, 3500.0, "month", "", ""),
                ("3", "", "", [], 15.0, 20.0, "hour", "", ""),
                ("4", "", "", [], 1000.0, 1200.0, "weekly", "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert out[0]["salary_min_annual"] == 40000.0
        assert out[0]["salary_max_annual"] == 50000.0
        assert out[0]["salary_midpoint"] == 45000.0
        assert out[1]["salary_min_annual"] == pytest.approx(36000.0)
        assert out[1]["salary_max_annual"] == pytest.approx(42000.0)
        assert out[2]["salary_min_annual"] == pytest.approx(31200.0)  # 15*2080
        assert out[2]["salary_max_annual"] == pytest.approx(41600.0)
        assert out[3]["salary_min_annual"] == pytest.approx(52000.0)  # 1000*52
        assert out[3]["salary_max_annual"] == pytest.approx(62400.0)  # 1200*52

    def test_periodo_por_token_tolerante(self, spark):
        # Variantes escritas de forma distinta deben anualizarse igual
        df = spark.createDataFrame(
            [
                ("1", "", "", [], 1000.0, 1000.0, "per week", "", ""),
                ("2", "", "", [], 1000.0, 1000.0, "Weekly", "", ""),
                ("3", "", "", [], 200.0, 200.0, "diario", "", ""),
                ("4", "", "", [], 400.0, 400.0, "por hora", "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert out[0]["salary_min_annual"] == pytest.approx(52000.0)
        assert out[1]["salary_min_annual"] == pytest.approx(52000.0)
        assert out[2]["salary_min_annual"] == pytest.approx(52000.0)  # 200*260
        assert out[3]["salary_min_annual"] == pytest.approx(832000.0)  # 400*2080

    def test_periodo_desconocido_asume_anual(self, spark):
        df = spark.createDataFrame(
            [("1", "", "", [], 40000.0, None, "bonus anual", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] == 40000.0
        assert out["salary_midpoint"] == 40000.0

    def test_periodo_desconocido_no_anualiza(self, spark):
        # Periodo vacio o no reconocido: NO se asume anual -> salario anual NULL
        # y fila marcada (no falsear el dato).
        df = spark.createDataFrame(
            [
                ("1", "", "", [], 40000.0, None, "", "", ""),
                ("2", "", "", [], 40000.0, None, "por proyecto", "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert out[0]["salary_min_annual"] is None
        assert out[0]["salary_quality"] == "unknown_period"
        assert out[1]["salary_min_annual"] is None
        assert out[1]["salary_quality"] == "unknown_period"

    def test_salario_mensual_normal_anualiza(self, spark):
        df = spark.createDataFrame(
            [("1", "", "", [], 8000.0, 8000.0, "month", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] == pytest.approx(96000.0)
        assert out["salary_quality"] == "ok"

    def test_salario_mensual_outlier_anulado(self, spark):
        # 80.000/mes (caso Glassdoor real): supera el umbral mensual -> se
        # anula la base anual y se marca outlier_review.
        df = spark.createDataFrame(
            [("1", "", "", [], 80000.0, 80000.0, "month", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] is None
        assert out["salary_max_annual"] is None
        assert out["salary_quality"] == "outlier_review"

    def test_salario_horario_outlier_anulado(self, spark):
        df = spark.createDataFrame(
            [("1", "", "", [], 500.0, 500.0, "hour", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] is None
        assert out["salary_quality"] == "outlier_review"

    def test_rango_invertido_marcado(self, spark):
        df = spark.createDataFrame(
            [("1", "", "", [], 120000.0, 80000.0, "year", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] is None
        assert out["salary_quality"] == "invalid_range"

    def test_salario_no_positivo_marcado(self, spark):
        df = spark.createDataFrame(
            [("1", "", "", [], 0.0, 50000.0, "year", "", "")],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert out["salary_min_annual"] is None
        assert out["salary_quality"] == "non_positive"


class TestEnrich:
    def test_rellena_solo_lo_vacio(self, spark):
        df = _df(spark, [
            ("1", "Data Engineer", "Python y AWS para ETL.",
             ["Python"], 40000.0, 50000.0, "year", "Associate", "Jornada completa"),
            ("2", "Data Analyst", "Machine Learning senior.",
             [], None, None, None, "", ""),
            ("3", "Data Engineer", "SQL basico, becario.", [], None, None, None, "", ""),
        ])
        out = enrich(df).collect()
        assert out[0]["skills"] == ["Python"]            # conserva lo existente
        assert out[0]["experience_level"] == "Associate"
        assert out[0]["employment_type"] == "Jornada completa"
        assert "Machine Learning" in out[1]["skills"]     # rellena huecos
        assert out[1]["experience_level"] == "Mid-Senior"
        assert out[1]["salary_midpoint"] is None          # sin salario
        assert "SQL" in out[2]["skills"]
        assert out[2]["experience_level"] == "Practicas"
        assert out[2]["employment_type"] == "Practicas"

    def test_sobrescribe_con_only_if_missing_false(self, spark):
        df = _df(spark, [
            ("1", "Data Engineer", "Python senior.", ["Excel"], 20000.0, None, "year",
             "", ""),
        ])
        out = enrich(df, only_if_missing=False).collect()[0]
        assert "Excel" not in out["skills"]
        assert "Python" in out["skills"]
        assert out["experience_level"] == "Mid-Senior"

    def test_skills_coma_string_tipo_csv(self, spark):
        # skills llega como STRING ("Python|AWS") desde un CSV, no como array.
        df = spark.createDataFrame(
            [
                ("1", "Data Engineer", "Python y AWS para ETL.",
                 "Python|AWS", None, None, None, "Associate", ""),
                ("2", "Data Analyst", "Machine Learning senior.",
                 "", None, None, None, "", ""),
            ],
            schema="job_id string, title string, description_clean string, "
                   "skills string, salary_min double, salary_max double, "
                   "salary_period string, experience_level string, "
                   "employment_type string",
        )
        out = enrich(df).collect()
        assert out[0]["skills"] == ["Python", "AWS"]      # string convertido a array
        assert out[0]["experience_level"] == "Associate"
        assert "Machine Learning" in out[1]["skills"]      # string vacio rellenado
        assert out[1]["experience_level"] == "Mid-Senior"

    def test_crea_columnas_si_no_existen(self, spark):
        df = spark.createDataFrame(
            [("1", "Data Engineer", "Excel y becario en Data.")],
            schema="job_id string, title string, description_clean string",
        )
        out = enrich(df, only_if_missing=False).collect()[0]
        assert "Excel" in out["skills"]
        assert out["experience_level"] == "Practicas"
        assert out["role_category"] == "Data Engineer"

    def test_employment_type_primero_title_luego_desc(self, spark):
        # Sin tipo de jornada en el title -> lo toma de la descripcion
        df = spark.createDataFrame(
            [
                ("1", "Data Analyst", "media jornada en Madrid.",
                 [], None, None, None, "", ""),
                # Tipo de jornada en el TITLE -> lo usa aunque la descripcion
                # no lo mencione
                ("2", "Web Developer (Full-Time)", "Desarrollo de webs.",
                 [], None, None, None, "", ""),
                ("3", "Becario de datos", "trabajo de 6 horas diarias",
                 [], None, None, None, "", ""),
            ],
            schema=_SCHEMA,
        )
        out = enrich(df, only_if_missing=False).collect()
        assert out[0]["employment_type"] == "Media jornada"   # del description
        assert out[1]["employment_type"] == "Jornada completa"  # del title
        assert out[2]["employment_type"] == "Practicas"        # del title (prioridad)