"""Construccion de la capa Oro analitica a partir de la capa Plata enriquecida.

Mueve a Databricks (PySpark) lo que antes se hacia en Power Query, para que la
capa Oro sea directamente consumible por el informe:

- Divisas: tabla dim_currency + conversion de salarios a EUR
  (SalaryMinAnnual_EUR / SalaryMaxAnnual_EUR / SalaryMidAnnual_EUR).
- Bucket de modalidad de trabajo (WorkModeBucket): Remote/Hybrid/On-site.
- Ano-mes de publicacion (PostedYearMonth) y flag de fecha valida
  (IsValidPostingDate).
- Skills: explosion a fact_offer_skills (una fila por oferta+skill).
- Dimensiones de referencia: dim_skill_list y dim_calendar.
- Proyeccion final de fact_offers con SOLO las columnas que usa el informe
  (se dejan fuera salary_min, salary_max, salary_min_annual, salary_max_annual,
  salary_raw, salary_source y salary_validation_reason).

EXCEPCION DEL PROYECTO: todo lo relacionado con el mapa (GeoCountry, GeoRegion,
Dim_RegionMap, Dim_Geo, coordenadas, MapFeature, Geo Selection, colores) se
mantiene en Power BI y NO se genera aqui.

CONTRATO:
- La entrada (`offers`) es el DataFrame consolidado y deduplicado de Silver
  (salida del notebook de Oro), ya enriquecido por enrich().
- Llamar a build_gold(spark, offers) y escribir el dict resultante; o usar las
  funciones individuales.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Datos de referencia (equivalentes a las tablas que estaban en Power Query)
# ---------------------------------------------------------------------------

# Tipos de cambio: unidades de divisa por 1 EUR (se DIVIDE el importe entre la
# tasa para pasar a EUR). AltCode es el simbolo/alias con el que tambien puede
# llegar salary_currency.
CURRENCY_RATES = [
    ("EUR", "Euro", "€", 1.0),
    ("USD", "US Dollar", "$", 1.1622),
    ("GBP", "British Pound", "£", 0.85898),
    ("CHF", "Swiss Franc", "CHF", 0.9405),
    ("CAD", "Canadian Dollar", "CA$", 1.6038),
    ("AUD", "Australian Dollar", "A$", 1.6134),
    ("PLN", "Polish Zloty", "zł", 4.3148),
    ("SEK", "Swedish Krona", "kr", 11.1005),
    ("NOK", "Norwegian Krone", "Nkr", 10.8035),
    ("DKK", "Danish Krone", "Dkr", 7.4747),
    ("CZK", "Czech Koruna", "Kč", 24.189),
    ("HUF", "Hungarian Forint", "Ft", 363.28),
    ("JPY", "Japanese Yen", "¥", 181.59),
    ("BRL", "Brazilian Real", "R$", 5.9405),
    ("CNY", "Chinese Yuan", "CN¥", 7.7994),
    ("INR", "Indian Rupee", "₹", 109.8165),
    ("SGD", "Singapore Dollar", "S$", 1.4724),
    ("NZD", "New Zealand Dollar", "NZ$", 1.9755),
    ("ZAR", "South African Rand", "R", 18.5571),
]

FX_SNAPSHOT_DATE = "2026-09-04"

# Catalogo de skills (equivalente a Dim_SkillList de Power Query).
SKILL_CATALOG = [
    ("Python", "Programming Languages"), ("SQL", "Programming Languages"),
    ("Java", "Programming Languages"), ("JavaScript", "Programming Languages"),
    ("TypeScript", "Programming Languages"), ("C#", "Programming Languages"),
    ("C++", "Programming Languages"), ("Go", "Programming Languages"),
    ("Kotlin", "Programming Languages"), ("Swift", "Programming Languages"),
    ("Ruby", "Programming Languages"), ("PHP", "Programming Languages"),
    ("Rust", "Programming Languages"), ("Scala", "Programming Languages"),
    ("R", "Programming Languages"),
    ("React", "Frameworks & Web"), ("Angular", "Frameworks & Web"),
    ("Vue.js", "Frameworks & Web"), ("Node.js", "Frameworks & Web"),
    ("Next.js", "Frameworks & Web"), (".NET", "Frameworks & Web"),
    ("ASP.NET", "Frameworks & Web"), ("Spring", "Frameworks & Web"),
    ("Django", "Frameworks & Web"), ("Flask", "Frameworks & Web"),
    ("React Native", "Frameworks & Web"), ("Flutter", "Frameworks & Web"),
    ("Microservices", "Frameworks & Web"), ("REST APIs", "Frameworks & Web"),
    ("GraphQL", "Frameworks & Web"),
    ("AWS", "Cloud & DevOps"), ("Azure", "Cloud & DevOps"),
    ("Google Cloud", "Cloud & DevOps"), ("Microsoft Fabric", "Cloud & DevOps"),
    ("GCP", "Cloud & DevOps"),
    ("Docker", "Cloud & DevOps"), ("Kubernetes", "Cloud & DevOps"),
    ("Terraform", "Cloud & DevOps"), ("Jenkins", "Cloud & DevOps"),
    ("GitLab", "Cloud & DevOps"), ("GitHub Actions", "Cloud & DevOps"),
    ("Ansible", "Cloud & DevOps"), ("CI/CD", "Cloud & DevOps"),
    ("Linux", "Cloud & DevOps"), ("Observability", "Cloud & DevOps"),
    ("Machine Learning", "Data & AI"), ("Deep Learning", "Data & AI"),
    ("NLP", "Data & AI"), ("LLM", "Data & AI"),
    ("Data Science", "Data & AI"), ("Data Engineering", "Data & AI"),
    ("Data Analysis", "Data & AI"), ("Spark", "Data & AI"),
    ("Databricks", "Data & AI"), ("Hadoop", "Data & AI"),
    ("Airflow", "Data & AI"), ("Snowflake", "Data & AI"),
    ("BigQuery", "Data & AI"), ("ETL", "Data & AI"), ("ELT", "Data & AI"),
    ("Statistics", "Data & AI"), ("TensorFlow", "Data & AI"),
    ("PyTorch", "Data & AI"), ("Pandas", "Data & AI"),
    ("Power BI", "BI & Analytics"), ("Tableau", "BI & Analytics"),
    ("Looker", "BI & Analytics"), ("Excel", "BI & Analytics"),
    ("DAX", "BI & Analytics"),
    ("SAP", "ERP & Platforms"), ("Salesforce", "ERP & Platforms"),
    ("ServiceNow", "ERP & Platforms"), ("Workday", "ERP & Platforms"),
    ("Oracle", "ERP & Platforms"),
    ("Agile", "Methods & Practices"), ("Scrum", "Methods & Practices"),
    ("Kanban", "Methods & Practices"), ("Project Management", "Methods & Practices"),
    ("Product Management", "Methods & Practices"), ("UX Research", "Methods & Practices"),
    ("Design Thinking", "Methods & Practices"),
    ("Communication", "Soft Skills"), ("Leadership", "Soft Skills"),
    ("Teamwork", "Soft Skills"), ("Problem Solving", "Soft Skills"),
    ("Stakeholder Management", "Soft Skills"), ("Negotiation", "Soft Skills"),
    ("Mentoring", "Soft Skills"),
    ("English", "Languages"), ("Spanish", "Languages"),
    ("German", "Languages"), ("French", "Languages"),
    ("Dutch", "Languages"), ("Italian", "Languages"),
    ("Portuguese", "Languages"),
]

# Etiquetas en espanol para dim_calendar (se generan aqui porque la locale de
# Spark no garantiza los nombres en castellano).
_MONTHS_ES = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November",
              "December"]
_SHORT_MONTHS_ES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DAYS_ES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]

# Columnas finales de fact_offers: SOLO lo que consume el informe.
# Fuera a proposito: salary_min, salary_max, salary_min_annual,
# salary_max_annual, salary_raw, salary_source, salary_validation_reason.
FACT_OFFERS_COLUMNS = [
    "job_id",
    "job_url",
    "title",
    "company_name",
    "location_city",
    "location_region",
    "location_country",
    "posted_date",
    "posted_date_raw",
    "posted_date_source",
    "PostedYearMonth",
    "IsValidPostingDate",
    "work_mode",
    "WorkModeBucket",
    "is_salary_available",
    "salary_currency",
    "salary_period",
    "SalaryMinAnnual_EUR",
    "SalaryMaxAnnual_EUR",
    "SalaryMidAnnual_EUR",
    "salary_quality",
    "experience_level",
    "experience_level_source",
    "employment_type",
    "role_category",
    "description_clean",
    "skills",
    "skills_source",
    "source_scraper",
]


def dim_currency(spark, snapshot: str = FX_SNAPSHOT_DATE):
    """Tabla de divisas (CurrencyCode, CurrencyName, AltCode, UnitsPerEUR)."""
    from pyspark.sql import functions as F

    schema = "CurrencyCode string, CurrencyName string, AltCode string, UnitsPerEUR double"
    return (spark.createDataFrame(CURRENCY_RATES, schema)
            .withColumn("FXSnapshotDate", F.lit(snapshot)))


def _currency_lookup(dim_cur):
    """Mapa currency_key -> UnitsPerEUR, aceptando codigo y AltCode."""
    from pyspark.sql import functions as F

    by_code = dim_cur.select(
        F.upper(F.trim(F.col("CurrencyCode"))).alias("currency_key"),
        F.col("UnitsPerEUR"))
    by_alt = dim_cur.select(
        F.upper(F.trim(F.col("AltCode"))).alias("currency_key"),
        F.col("UnitsPerEUR"))
    return (by_code.unionByName(by_alt)
            .where(F.col("currency_key").isNotNull()
                   & (F.trim(F.col("currency_key")) != ""))
            .dropDuplicates(["currency_key"]))


def add_salary_eur(offers, dim_cur):
    """Anade SalaryMin/Max/MidAnnual_EUR convirtiendo desde salary_currency.

    EUR = importe / UnitsPerEUR (tasa = unidades de divisa por 1 EUR). Sin tasa
    conocida -> NULL (no se inventa el tipo de cambio).
    """
    from pyspark.sql import functions as F

    with_key = offers.withColumn(
        "__currency_key",
        F.upper(F.trim(F.coalesce(F.col("salary_currency"), F.lit("")))))
    joined = with_key.join(
        _currency_lookup(dim_cur),
        F.col("__currency_key") == F.col("currency_key"),
        "left")
    return (joined
            .withColumn(
                "SalaryMinAnnual_EUR",
                F.when((F.col("salary_min_annual") > 0) & (F.col("UnitsPerEUR") > 0),
                       F.col("salary_min_annual") / F.col("UnitsPerEUR")))
            .withColumn(
                "SalaryMaxAnnual_EUR",
                F.when((F.col("salary_max_annual") > 0) & (F.col("UnitsPerEUR") > 0),
                       F.col("salary_max_annual") / F.col("UnitsPerEUR")))
            .withColumn(
                "SalaryMidAnnual_EUR",
                F.when(F.col("SalaryMinAnnual_EUR").isNotNull()
                       & F.col("SalaryMaxAnnual_EUR").isNotNull(),
                       (F.col("SalaryMinAnnual_EUR")
                        + F.col("SalaryMaxAnnual_EUR")) / 2))
            .drop("__currency_key", "currency_key", "UnitsPerEUR"))


def add_work_mode_bucket(df):
    """WorkModeBucket: Remote / Hybrid / On-site / (Not specified)."""
    from pyspark.sql import functions as F

    mode = F.upper(F.trim(F.coalesce(F.col("work_mode"), F.lit(""))))
    onsite = ["ON-SITE", "ONSITE", "ON SITE", "OFFICE", "IN-OFFICE",
              "IN OFFICE", "PRESENTIAL", "ON-SITE/REMOTE"]
    hybrid_alt = ["REMOTE/HYBRID", "HYBRID/REMOTE", "FLEXIBLE", "MIXED"]
    return df.withColumn(
        "WorkModeBucket",
        F.when(mode == "REMOTE", F.lit("Remote"))
         .when(mode == "HYBRID", F.lit("Hybrid"))
         .when(mode.isin(*onsite), F.lit("On-site"))
         .when(mode.isin(*hybrid_alt), F.lit("Hybrid"))
         .when(mode == "", F.lit("(Not specified)"))
         .otherwise(mode))


def add_posted_date_flags(df):
    """PostedYearMonth (yyyy-MM) e IsValidPostingDate (2026..2089)."""
    from pyspark.sql import functions as F

    return (df
            .withColumn("PostedYearMonth",
                        F.date_format(F.col("posted_date"), "yyyy-MM"))
            .withColumn("IsValidPostingDate",
                        F.col("posted_date").isNotNull()
                        & (F.year(F.col("posted_date")) >= 2026)
                        & (F.year(F.col("posted_date")) < 2090)))


def dim_skill_list(spark):
    """Catalogo de skills (SkillName, SkillCategory)."""
    schema = "SkillName string, SkillCategory string"
    return spark.createDataFrame(SKILL_CATALOG, schema)


def build_fact_offer_skills(offers):
    """Explota skills (array<string> o texto 'a|b') -> (JobID, Skill) unico."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import ArrayType

    if "skills" not in offers.columns:
        raise ValueError("La tabla de ofertas no tiene columna 'skills'.")
    if isinstance(offers.schema["skills"].dataType, ArrayType):
        skill_arr = F.col("skills")
    else:
        skill_arr = F.split(
            F.trim(F.coalesce(F.col("skills"), F.lit(""))), r"\s*\|\s*")
    return (offers
            .select(F.col("job_id").alias("JobID"), skill_arr.alias("__skills"))
            .select("JobID", F.explode_outer("__skills").alias("Skill"))
            .withColumn("Skill", F.trim(F.col("Skill")))
            .where(F.col("Skill").isNotNull()
                   & (F.col("Skill") != "")
                   & (F.col("Skill") != "[]"))
            .dropDuplicates(["JobID", "Skill"]))


def dim_calendar(spark, start, end):
    """Calendario diario entre start y end (Date) con etiquetas en espanol."""
    from pyspark.sql import functions as F

    months = F.array([F.lit(m) for m in _MONTHS_ES])
    short_months = F.array([F.lit(m) for m in _SHORT_MONTHS_ES])
    days = F.array([F.lit(d) for d in _DAYS_ES])

    base = spark.range(1).select(
        F.explode(F.sequence(F.lit(start), F.lit(end),
                             F.expr("interval 1 day"))).alias("Date"))
    weekday = ((F.dayofweek("Date") + 5) % 7) + 1  # Lunes=1 .. Domingo=7
    return (base
            .withColumn("DateKey", F.date_format("Date", "yyyyMMdd").cast("int"))
            .withColumn("Year", F.year("Date"))
            .withColumn("MonthNumber", F.month("Date"))
            .withColumn("MonthName", F.element_at(months, F.month("Date")))
            .withColumn("ShortMonth", F.element_at(short_months, F.month("Date")))
            .withColumn("YearMonth", F.date_format("Date", "yyyy-MM"))
            .withColumn("Quarter", F.quarter("Date"))
            .withColumn("YearQuarter",
                        F.concat(F.year("Date"), F.lit("-T"), F.quarter("Date")))
            .withColumn("WeekISO", F.weekofyear("Date"))
            .withColumn("Day", F.dayofmonth("Date"))
            .withColumn("WeekDay", weekday)
            .withColumn("DayName", F.element_at(days, weekday))
            .withColumn("IsWeekend", weekday >= 6)
            .orderBy("Date"))


def build_fact_offers(offers, dim_cur):
    """fact_offers analitica: EUR + workmode + fechas, con solo lo necesario."""
    df = add_salary_eur(offers, dim_cur)
    df = add_work_mode_bucket(df)
    df = add_posted_date_flags(df)
    keep = [c for c in FACT_OFFERS_COLUMNS if c in df.columns]
    missing = [c for c in FACT_OFFERS_COLUMNS if c not in df.columns]
    if missing:
        print("AVISO fact_offers: columnas ausentes ->", missing)
    return df.select(*keep)


def build_gold(spark, offers, calendar_start=None, calendar_end=None):
    """Construye todas las tablas Oro y devuelve un dict nombre -> DataFrame.

    `offers` es el DataFrame consolidado/deduplicado de Silver (enriquecido).
    El calendario se deriva del rango de posted_date salvo que se indique.
    """
    from pyspark.sql import functions as F

    dim_cur = dim_currency(spark)
    fact_offers = build_fact_offers(offers, dim_cur)
    fact_offer_skills = build_fact_offer_skills(offers)

    if calendar_start is None or calendar_end is None:
        bounds = (offers.select(
            F.min("posted_date").alias("min_d"),
            F.max("posted_date").alias("max_d")).collect()[0])
        calendar_start = calendar_start or bounds["min_d"]
        calendar_end = calendar_end or bounds["max_d"]
    calendar = dim_calendar(spark, calendar_start, calendar_end)

    return {
        "fact_offers": fact_offers,
        "fact_offer_skills": fact_offer_skills,
        "dim_currency": dim_cur,
        "dim_skill_list": dim_skill_list(spark),
        "dim_calendar": calendar,
    }


def write_gold(tables, catalog: str = "job_offers", schema: str = "gold"):
    """Escribe cada DataFrame como tabla Delta <catalog>.<schema>.<nombre>."""
    for name, df in tables.items():
        (df.write.mode("overwrite")
           .option("overwriteSchema", "true")
           .saveAsTable(f"{catalog}.{schema}.{name}"))
        print(f"escrito {catalog}.{schema}.{name}")
