"""Enriquecimiento Spark de ofertas de LinkedIn en UN solo script.

Todo en una unica funcion publica `enrich(df)` (100% columnar, sin UDFs) que
anade, en pasadas independientes (cada una opcional via flag):

- skills              : regexp_extract_all + normalizacion canonica de keywords.
    - experience_level    : cascada when/rlike de mayor a menor seniority.
    - employment_type     : cascada when/rlike por tipo de jornada: primero en el
                            title; si no encuenta nada, en la descripcion.
    - salary_min/max_annual + salary_midpoint : normaliza el salario a base anual
                          para comparar toda la tabla en BI (año x1, mes x12,
                          hora x2080 = 40h/semana x 52 semanas). Requiere
                          columnas salary_min/max y salary_period.
    - role_category       : categoria de rol derivada del titulo.
    - salary_period_norm  : normaliza salary_period a un enum cerrado
                          YEAR/MONTH/WEEK (hora/diario -> WEEK, ya que es el
                          cubo sub-mensual mas cercano; desconocido -> "").
    - location_region / location_country : rellena huecos desde location_city.
                          Solo hay 4 paises soportados (ES/IE/NL/CH):
                          si location_city ES un pais (EN o ES) se copia en
                          region y country; si es una ciudad conocida se copia
                          a region (si esta vacia) y se infiere su pais.
                          Ciudad desconocida -> se deja todo como esta (no se
                          inventa nada).

NOTA: la columna de descripcion por defecto es "description_clean" (inmutable
en este proyecto). Si tu pipeline usa otro nombre, pasalo con desc_col=...

CONTRATO DE PRIORIDAD (first-match wins):
  La PRIMERA regla de cada lista (_EXP_RULES, _EMP_RULES, _ROLE_RULES) que
  matchee gana. Las funciones puras role_category / experience_level /
  employment_type devuelven exactamente el mismo resultado que las columnas de
  enrich() y sirven para diagnosticar sin Spark.

El modulo NO importa pyspark al cargarse (imports dentro de enrich), asi se
puede testear la logica pura localmente sin tener Spark instalado.

Uso en Databricks (pega este archivo en una celda o %run desde Repos):
    df = spark.read.option("mergeSchema", "true").parquet("/mnt/.../jobs.parquet")
    enriched = enrich(df)          # descripcion = "description_clean" (default)
    enriched.write.mode("overwrite").saveAsTable("jobs_enriched")

    # Solo si tu pipeline llama a la descripcion de otra forma:
    # enrich(df, desc_col="mi_columna")
    # Recalcular: enrich(df.drop("skills","experience_level","role_category",
    #                               "employment_type"), only_if_missing=False)
"""
from __future__ import annotations

import re

# Reusar lista curada del repo; en notebook standalone sin models.py caemos a
# las keywords embebidas (mismas).
try:
    from .models import DATA_SKILLS_KEYWORDS
except ImportError:  # pragma: no cover - path Databricks/standalone
    try:
        from models import DATA_SKILLS_KEYWORDS  # type: ignore
    except ImportError:  # pragma: no cover - fallback standalone
        DATA_SKILLS_KEYWORDS = [
            "Python", "SQL", "Power BI", "PowerBI", "Azure", "ETL",
            "Machine Learning", "Tableau", "AWS", "Excel", "Databricks",
            "GCP", "Google Cloud", "Spark", "Snowflake", "dbt", "Pandas",
            "R", "Git", "Airflow", "BigQuery", "Redshift", "Docker",
            "Kubernetes", "Terraform", "Scala", "Java", "Deep Learning",
            "Linux", "Hadoop", "Kafka", "Looker", "Power Query",
            "Power Automate", "DAX", "Matplotlib", "Scikit-learn",
            "Scikit learn", "TensorFlow", "PyTorch", "Jupyter", "SAS",
            "MATLAB", "NoSQL", "MongoDB", "PostgreSQL", "MySQL", "Oracle",
            "Data Factory", "SSIS", "SSRS", "Qlik", "MicroStrategy",
            "Alteryx", "Dataiku", "Fivetran", "Prefect", "Dagster",
            "Great Expectations", "MLflow", "Delta Lake",
        ]

# Nombres que regexp_extract_all devuelve en minusculas y que initcap()
# no escribe correctamente (acronimos / alias). El resto usa initcap().
SKILLS_CANONIC = {
    "sql": "SQL", "etl": "ETL", "aws": "AWS", "gcp": "GCP", "dax": "DAX",
    "sas": "SAS", "ssis": "SSIS", "ssrs": "SSRS", "nosql": "NoSQL",
    "matlab": "MATLAB", "mongodb": "MongoDB", "postgresql": "PostgreSQL",
    "mysql": "MySQL", "bigquery": "BigQuery", "mlflow": "MLflow",
    "microstrategy": "MicroStrategy", "tensorflow": "TensorFlow",
    "pytorch": "PyTorch", "power bi": "Power BI", "powerbi": "Power BI",
    "google cloud": "GCP", "scikit learn": "Scikit-learn",
}

# Un solo regex con todas las keywords (word boundaries + case-insensitive).
# OJO: en Spark hay que envolverlo en F.lit() (si se pasa como string plano, lo
# interpreta como nombre de columna -> UNRESOLVED_COLUMN).
SKILLS_REGEX = r"(?i)\b(" + "|".join(
    re.escape(kw) for kw in DATA_SKILLS_KEYWORDS
) + r")\b"

# Reglas de seniority. ORDEN = prioridad (mayor a menor): la primera regla que
# matchee gana. `senior` suelto como ultimo recurso (replica
# models.EXPERIENCE_LEVEL_MAP); riesgo de falso positivo ("senior
# stakeholders") es inherente al matching sobre texto libre.
_EXP_RULES = [
    ("Executive",   r"(?i)\b(?:executive|ejecutivo|executivo)\b"),
    ("Director",    r"(?i)\b(?:director(?:a)?|directiva)\b"),
    ("Mid-Senior",  r"(?i)\b(?:mid[\s\-–]+senior|senior)\b"),
    ("Associate",   r"(?i)\b(?:associate|asociad[oa])\b"),
    ("Practicas",   r"(?i)\b(?:internship|practicas|prácticas|becari[oa]|pasant[íi]a)\b"),
    ("Entry",       r"(?i)\b(?:entry\s*[- ]?level|entry|j[úu]nior|nivel de entrada)\b"),
]

# Reglas de tipo de jornada. ORDEN = prioridad: la primera que matchee gana.
# Replica el mapeo de parse_detail._normalize_emp_type.
_EMP_RULES = [
    ("Practicas",       r"(?i)\b(?:internship|practicas|prácticas|becari[oa]|"
                        r"pasant[íi]a|trainee|en pr[áa]cticas)\b"),
    ("Media jornada",   r"(?i)\b(?:media jornada|part[- ]?time|part time|"
                        r"medio tiempo)\b"),
    ("Contrato",        r"(?i)\b(?:contrato|contract|temporal|freelance)\b"),
    ("Jornada completa", r"(?i)\b(?:jornada completa|tiempo completo|"
                         r"full[- ]?time|full time)\b"),
]

# Categorias de rol. ORDEN = prioridad (lo especifico primero). "Data (Other)"
# es el cajon de sastre y SIEMPRE va el ultimo.
_ROLE_RULES = [
    ("Machine Learning & AI",
     r"(?i)\b(?:machine learning|ml engineer|deep learning|ai engineer|llm|"
     r"generative ai|genai)\b"),
    ("Data Scientist",
     r"(?i)\b(?:data scientist|scientist|cient[ií]fic[oa] de datos|"
     r"cienc[ií]a de datos)\b"),
    ("Analytics Engineer",
     r"(?i)\b(?:analytics engineer|analytics engineering)\b"),
    ("Data Engineer",
     r"(?i)\b(?:data engineer|data engineering|big data|ingenier[oa] de datos|"
     r"data architect)\b"),
    ("Data Analyst & BI",
     r"(?i)\b(?:data analyst|data analytics|business analyst|analista|business "
     r"intelligence|power bi|bi developer|\bbi\b)\b"),
    ("Data (Other)",
     r"(?i)\b(?:data|datos|dato)\b"),
]

# Multiplicador a base ANUAL segun el periodo del salario (orden = prioridad,
# el primero que aparezca en el texto del periodo gana).
#   hour x2080   (jornada de 40h/semana x 52 semanas)
#   day  x260    (5 dias laborables x 52 semanas)
#   week x52     (calendario estandar; equivale a algo menos que "x4 y x12")
#   month x12
#   (cualquier otra cosa / year / "" -> x1 = ya es anual)
_ANNUAL_TOKEN_FACTORS = [
    ("hour", 2080.0),
    ("day", 260.0),
    ("week", 52.0),
    ("month", 12.0),
]

# Token explicito de periodo ANUAL (el unico que anualiza x1). Si el periodo
# no matchea ninguno de _ANNUAL_TOKEN_FACTORS ni este token -> desconocido ->
# el factor queda NULL y el salario anual NO se calcula (no falsear).
_YEAR_TOKEN = r"(?i)(?:year|yearly|annual|annum|anual|a[ñn]o)"

# Normalizacion de salary_period a un enum cerrado YEAR/MONTH/WEEK (orden =
# prioridad, substring match sobre lowercase). Los periodos sub-mensuales
# (hora/diario) caen al cubo mas cercano: WEEK. Desconocido/vacio -> "" (no
# falsear el dato).
_PERIOD_NORM_RULES = [
    ("YEAR",  r"(?i)(?:anual|year|a[ñn]o)"),
    ("MONTH", r"(?i)(?:mensual|month|mes)"),
    ("WEEK",  r"(?i)(?:semanal|semana|week|hora|hour|daily|diari[oa])"),
]

# ---------------------------------------------------------------------------
# Localizacion: relleno de location_region / location_country a partir de
# location_city. Solo 4 paises soportados (los de este proyecto): Spain,
# Ireland, Netherlands, Switzerland.
#
#   - Si location_city ES un pais (en ingles o español) -> se copia en
#     location_region y location_country.
#   - Si location_city es una ciudad conocida -> la ciudad pasa a
#     location_region (si esta vacia) y se infiere su pais en
#     location_country (si esta vacio).
#   - Ciudad/pais NO reconocido -> no se modifica nada (no se inventa).
#
# Las claves van SIN acentos en minusculas (la normalizacion quita los
# acentos antes del lookup: "málaga" -> "malaga"), identico en la funcion
# pura y en la pasada columnar de enrich().
_LOCATION_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")

_LOCATION_CITY_TO_COUNTRY = {
    "madrid": "Spain", "barcelona": "Spain", "bilbao": "Spain",
    "valencia": "Spain", "sevilla": "Spain", "seville": "Spain",
    "malaga": "Spain", "zaragoza": "Spain", "granada": "Spain",
    "vigo": "Spain",
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland",
    "limerick": "Ireland",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "the hague": "Netherlands", "eindhoven": "Netherlands",
    "utrecht": "Netherlands",
    "zurich": "Switzerland", "geneva": "Switzerland", "ginebra": "Switzerland",
    "basel": "Switzerland", "bern": "Switzerland", "lausanne": "Switzerland",
}

_LOCATION_COUNTRY_NAMES = {
    "spain": "Spain", "espana": "Spain",
    "ireland": "Ireland", "irlanda": "Ireland",
    "netherlands": "Netherlands", "holanda": "Netherlands",
    "paises bajos": "Netherlands",
    "switzerland": "Switzerland", "suiza": "Switzerland",
}


# ---------------------------------------------------------------------------
# Funciones puras de clasificacion (sin pyspark) = misma logica que enrich().
# ---------------------------------------------------------------------------

def role_category(title: str) -> str:
    """Categoria de rol: gana la PRIMERA regla de _ROLE_RULES que matchee.

    Identico resultado al de la columna role_category que crea enrich().
    """
    for category, pattern in _ROLE_RULES:
        if re.search(pattern, title):
            return category
    return "Other"


def role_category_debug(title: str) -> list[str]:
    """Diagnostico: todas las reglas que matchean un titulo, en orden de
    prioridad. La primera es la que aplicaria enrich() y role_category().
    """
    return [cat for cat, pat in _ROLE_RULES if re.search(pat, title)]


def experience_level(text: str) -> str:
    """Nivel de seniority: gana la PRIMERA regla de _EXP_RULES que matchee.

    Identico resultado al de la columna experience_level que crea enrich().
    """
    for level, pattern in _EXP_RULES:
        if re.search(pattern, text):
            return level
    return ""


def employment_type(text: str) -> str:
    """Tipo de jornada: gana la PRIMERA regla de _EMP_RULES que matchee.

    Identico resultado al de la columna employment_type que crea enrich().
    """
    for emp_type, pattern in _EMP_RULES:
        if re.search(pattern, text):
            return emp_type
    return ""


def salary_period_normalized(period: str) -> str:
    """Normaliza el periodo salarial a YEAR/MONTH/WEEK.

    Identico resultado al de la columna salary_period_norm que crea enrich().
    Desconocido/vacio -> "".
    """
    text = period.strip().lower()
    for value, pattern in _PERIOD_NORM_RULES:
        if re.search(pattern, text):
            return value
    return ""


def location_fill(city: str, region: str, country: str) -> tuple[str, str]:
    """Devuelve (region, country) corregidos a partir de location_city.

    Identico resultado al de las columnas location_region / location_country
    que crea enrich():
    - city vacia o no reconocida -> devuelve region/country sin tocar.
    - city es un pais (Spain/Ireland/Netherlands/Switzerland, en ingles o
      español) -> se copia en region y country (solo si estan vacios).
    - city es una ciudad conocida:
        region vacia  -> region = city
        country vacio -> country = pais de la ciudad.
    """
    city_n = (city or "").strip().lower().translate(_LOCATION_ACCENTS)
    region_ = (region or "").strip()
    country_ = (country or "").strip()
    if not city_n:
        return region_, country_

    country_name = _LOCATION_COUNTRY_NAMES.get(city_n)
    if country_name:
        return region_ or country_name, country_ or country_name

    country_of_city = _LOCATION_CITY_TO_COUNTRY.get(city_n)
    if country_of_city:
        return region_ or city.strip(), country_ or country_of_city

    return region_, country_


# ---------------------------------------------------------------------------
# Funcion principal (pyspark se importa aqui dentro, no al cargar el modulo).
# ---------------------------------------------------------------------------

def enrich(
    df,
    *,
    desc_col: str = "description_clean",
    skills_col: str = "skills",
    experience_level_col: str = "experience_level",
    employment_type_col: str = "employment_type",
    role_category_col: str = "role_category",
    title_col: str = "title",
    salary_min_col: str = "salary_min",
    salary_max_col: str = "salary_max",
    salary_period_col: str = "salary_period",
    salary_period_norm_col: str = "salary_period_norm",
    location_city_col: str = "location_city",
    location_region_col: str = "location_region",
    location_country_col: str = "location_country",
    only_if_missing: bool = True,
    add_salary_annual: bool = True,
    add_salary_period_norm: bool = True,
    add_role_category: bool = True,
    add_employment_type: bool = True,
    add_location_fill: bool = True,
):
    """Enriquece `df` con skills/experience/employment/role/salario anual.

    only_if_missing=True (default): conserva lo ya rellenado y completa huecos.
    False: recalcula todo. Cada mejora se activa con su flag.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType

    def cascade(col, rules, default):
        """CASE WHEN first-match wins respetando el orden de `rules`.

        Con .otherwise(prev), la regla asignada en ULTIMO lugar queda mas
        externa y se evalua PRIMERO; por eso iteramos en orden inverso para que
        la regla[0] tenga la maxima prioridad (igual que las funciones puras).
        """
        expr = F.lit(default)
        for value, pattern in reversed(rules):
            expr = F.when(col.rlike(pattern), F.lit(value)).otherwise(expr)
        return expr

    # Mapa literal para normalizacion de skills (una sola evaluacion por fila).
    canonic_map = F.create_map([
        F.lit(item)
        for kv in SKILLS_CANONIC.items()
        for item in (kv[0], kv[1])
    ])

    skills_new = F.array_distinct(F.transform(
        # OJO: regexp_extract_all espera Column como regex -> F.lit() imprescindible
        # (un string plano lo interpreta como nombre de columna -> UNRESOLVED_COLUMN).
        F.regexp_extract_all(F.col(desc_col), F.lit(SKILLS_REGEX)),
        lambda m: F.coalesce(canonic_map[F.lower(m)], F.initcap(m)),
    ))
    exp_new = cascade(F.col(desc_col), _EXP_RULES, default="")
    # employment_type: primero en el title; si el title no aporta nada,
    # entonces en la descripcion.
    emp_from_title = cascade(F.col(title_col), _EMP_RULES, default="")
    emp_from_desc = cascade(F.col(desc_col), _EMP_RULES, default="")
    emp_new = F.when(emp_from_title != "", emp_from_title).otherwise(emp_from_desc)
    role_new = cascade(F.col(title_col), _ROLE_RULES, default="Other")

    result = df

    def fill(col_name, new_expr, keep, exists, keep_value=None):
        """withColumn(col, when(keep, keep_value).otherwise(new_expr)).

        keep_value permite devolver en el THEN una transformacion de la columna
        existente (p.ej. skills string -> array) en vez de la columna cruda,
        para que las ramas del CASE tengan SIEMPRE el mismo tipo.
        """
        nonlocal result
        if only_if_missing and exists:
            then_value = keep_value if keep_value is not None else F.col(col_name)
            result = result.withColumn(
                col_name, F.when(keep, then_value).otherwise(new_expr))
        else:
            result = result.withColumn(col_name, new_expr)

    # --- skills (puede venir como array<string> o como string 'a|b' del CSV) ---
    sk = F.col(skills_col)
    if skills_col not in result.columns:
        fill(skills_col, skills_new, F.lit(True), False)
    elif isinstance(result.schema[skills_col].dataType, ArrayType):
        existing = F.coalesce(sk, F.array())
        fill(skills_col, skills_new, F.size(existing) > 0, True,
             keep_value=existing)
    else:
        existing = F.split(F.trim(F.coalesce(sk, F.lit(""))), r"\s*\|\s*")
        keep_skills = F.trim(F.coalesce(sk, F.lit(""))) != ""
        fill(skills_col, skills_new, keep_skills, True, keep_value=existing)

    # --- experience_level / employment_type / role_category (strings) ---
    not_blank = lambda c: F.trim(F.coalesce(c, F.lit(""))) != ""  # noqa: E731
    fill(experience_level_col, exp_new,
         not_blank(F.col(experience_level_col)),
         experience_level_col in result.columns)
    if add_employment_type:
        fill(employment_type_col, emp_new,
             not_blank(F.col(employment_type_col)),
             employment_type_col in result.columns)
    if add_role_category:
        fill(role_category_col, role_new,
             not_blank(F.col(role_category_col)),
             role_category_col in result.columns)

    # --- salario anual para comparar toda la tabla en BI ---
    if add_salary_annual:
        period = F.lower(F.trim(F.coalesce(F.col(salary_period_col), F.lit(""))))
        # Factor por token (subcadena): hour > day > week > month > year
        # (first-match wins, igual que cascade(): se itera en ORDEN INVERSO).
        # Si el periodo NO se reconoce (vacio/desconocido) el factor es NULL ->
        # salario anual NULL (no falsear: NO se asume que es anual).
        factor = F.lit(None).cast("double")
        for token, factor_value in reversed(_ANNUAL_TOKEN_FACTORS):
            factor = F.when(period.rlike(token), F.lit(factor_value)).otherwise(factor)
        factor = F.when(period.rlike(_YEAR_TOKEN), F.lit(1.0)).otherwise(factor)
        smin = F.col(salary_min_col)
        smax = F.col(salary_max_col)
        has_salary = smin.isNotNull() | smax.isNotNull()
        min_ann = F.when(smin.isNotNull(), smin * factor)
        max_ann = F.when(smax.isNotNull(), smax * factor)

        # --- capa de calidad salarial (conserva la fila, marca el problema) ---
        # Umbrales de plausibilidad por periodo (roles de datos, jornada
        # completa). Un salario fuera de estos rangos se anula en la base
        # anual (queda excluido de metricas) y se marca como outlier_review.
        non_pos = (smin.isNotNull() & (smin <= 0)) | (smax.isNotNull() & (smax <= 0))
        inv_range = smin.isNotNull() & smax.isNotNull() & (smin > smax)
        # Periodo vacio o no reconocido: factor queda NULL (no anualizar).
        unknown_period = has_salary & factor.isNull()
        peak = F.coalesce(smax, smin)
        is_outlier = F.coalesce(period.rlike("month") & (peak > 30000), F.lit(False)) | \
            F.coalesce(period.rlike("hour") & (peak > 400), F.lit(False)) | \
            F.coalesce(period.rlike("week") & (peak > 5000), F.lit(False)) | \
            F.coalesce(period.rlike("day") & (peak > 1000), F.lit(False)) | \
            F.coalesce(period.rlike(_YEAR_TOKEN) & (peak > 1000000), F.lit(False))
        bad_salary = non_pos | inv_range | unknown_period | is_outlier
        min_ann = F.when(bad_salary, F.lit(None)).otherwise(min_ann)
        max_ann = F.when(bad_salary, F.lit(None)).otherwise(max_ann)

        val_reason = F.when(non_pos, F.lit("salary <= 0")) \
            .when(inv_range, F.lit("salary_min > salary_max")) \
            .when(unknown_period, F.lit("periodo de salario desconocido")) \
            .when(period.rlike("month") & (peak > 30000),
                  F.lit("salario mensual > 30.000 (outlier)")) \
            .when(period.rlike("hour") & (peak > 400),
                  F.lit("salario horario > 400 (outlier)")) \
            .when(period.rlike("week") & (peak > 5000),
                  F.lit("salario semanal > 5.000 (outlier)")) \
            .when(period.rlike("day") & (peak > 1000),
                  F.lit("salario diario > 1.000 (outlier)")) \
            .when(period.rlike(_YEAR_TOKEN) & (peak > 1000000),
                  F.lit("salario anual > 1.000.000 (outlier)")) \
            .otherwise(F.lit(""))
        quality = F.when(is_outlier, F.lit("outlier_review")) \
            .when(non_pos, F.lit("non_positive")) \
            .when(inv_range, F.lit("invalid_range")) \
            .when(unknown_period, F.lit("unknown_period")) \
            .otherwise(F.lit("ok"))
        mid_ann = F.when(min_ann.isNotNull() & max_ann.isNotNull(),
                         (min_ann + max_ann) / 2).otherwise(F.coalesce(min_ann, max_ann))
        for col_name, expr in [
            ("salary_min_annual", min_ann),
            ("salary_max_annual", max_ann),
            ("salary_midpoint", mid_ann),
            ("salary_quality", quality),
            ("salary_validation_reason", val_reason),
        ]:
            not_null = lambda c: c.isNotNull()
            fill(col_name, expr, not_null(F.col(col_name)), col_name in result.columns)

    # --- salary_period normalizado a YEAR/MONTH/WEEK para BI ---
    if add_salary_period_norm:
        period_norm = cascade(F.col(salary_period_col), _PERIOD_NORM_RULES, default="")
        fill(salary_period_norm_col, period_norm,
             not_blank(F.col(salary_period_norm_col)),
             salary_period_norm_col in result.columns)

    # --- location_region / location_country a partir de location_city --------
    #  Solo rellena huecos (only_if_missing via fill): si ya hay valor se
    #  conserva. City normalizada SIN acentos para el lookup (misma clave que
    #  la funcion pura location_fill).
    if add_location_fill and location_city_col in result.columns:
        city_v = F.trim(F.coalesce(F.col(location_city_col), F.lit("")))
        city_norm = F.translate(F.lower(city_v), "áéíóúüñ", "aeiouun")
        city_to_country = F.create_map([
            F.lit(item)
            for kv in _LOCATION_CITY_TO_COUNTRY.items()
            for item in (kv[0], kv[1])
        ])
        country_names = F.create_map([
            F.lit(item)
            for kv in _LOCATION_COUNTRY_NAMES.items()
            for item in (kv[0], kv[1])
        ])
        country_by_name = country_names[city_norm]
        country_by_city = city_to_country[city_norm]
        # region: pais si city era un pais; si no, la propia ciudad.
        new_region = F.when(country_by_name.isNotNull(), country_by_name) \
            .when(country_by_city.isNotNull(), city_v) \
            .otherwise(F.lit(""))
        # country: pais inferido (por nombre de pais o por ciudad). Si la
        # ciudad no se reconoce -> vacio (no se inventa el pais).
        new_country = F.coalesce(country_by_name, country_by_city, F.lit(""))
        fill(location_region_col, new_region,
             not_blank(F.col(location_region_col)),
             location_region_col in result.columns)
        fill(location_country_col, new_country,
             not_blank(F.col(location_country_col)),
             location_country_col in result.columns)

    return result