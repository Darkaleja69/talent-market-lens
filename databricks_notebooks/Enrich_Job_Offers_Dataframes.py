"""Enriquecimiento Spark de ofertas en UN solo script.

Todo en una unica funcion publica `enrich(df)` (100% columnar, sin UDFs) que
anade, en pasadas independientes (cada una opcional via flag):

- skills              : regexp_extract_all + normalizacion canonica de keywords.
                        Vocabulario ampliado (sinonimos: ML -> Machine Learning,
                        Postgres -> PostgreSQL, ...) y patrones estrictos para
                        keywords cortas/ambiguas (p.ej. "R" no matchea "R&D").
    - experience_level    : cascada when/rlike de mayor a menor seniority.
                            Ahora se evalua PRIMERO el titulo (alta precision) y,
                            si no aporta, la descripcion con frases de contexto
                            excluidas ("senior stakeholders", "reporting to ...")
                            para reducir falsos positivos. No clasificable ->
                            "Unknown" (valor explicito, filtrable en BI).
    - employment_type     : cascada when/rlike por tipo de jornada: primero en el
                            title; si no encuenta nada, en la descripcion.
    - salary_min/max_annual + salary_midpoint : normaliza el salario a base anual
                          para comparar toda la tabla en BI (año x1, mes x12,
                          hora x2080 = 40h/semana x 52 semanas). Requiere
                          columnas salary_min/max y salary_period.
    - salary_raw          : parseo columnar del texto bruto de salario cuando
                          salary_min/max estan vacios: rangos con 'k'
                          ("40k-55k"), numeros con separadores ("40,000 - 55,000"),
                          unico valor ("120k"), moneda ($/€/£/CHF) y periodo
                          derivados del propio texto. Las columnas originales
                          (salary_raw, salary_min, salary_max) se CONSERVAN.
    - salary_quality      : semantica ampliada: "missing" (sin salario),
                          "invalid" (min<=0 o min>max), "unknown_period",
                          "outlier_review", "ok". Es la unica columna de
                          control de calidad salarial por defecto.
    - salary_validation_reason : texto libre con el motivo detallado. Se
                          SOLAPA con salary_quality -> opt-in via
                          add_salary_validation_reason (por defecto NO se
                          genera, para no inflar la tabla).
    - role_category       : categoria de rol derivada del titulo. El
                          placeholder "Other" se RECALCULA si el titulo da
                          una categoria real (un "Other" heredado de un run
                          previo no bloquea una mejor clasificacion).
    - salary_period_norm  : normaliza salary_period a un enum cerrado
                          YEAR/MONTH/WEEK (desconocido/vacio y periodos
                          sub-mensuales hora/diario -> "", para no falsear
                          el dato; el factor anual x2080/x260 ya se aplica
                          en salary_min_annual/max_annual). Si salary_period
                          esta vacio, el periodo se deriva del texto de
                          salary_raw ("per hour", "annual", "mensual", ...).
    - work_mode : Remoto/ Hibrido / Presencial (On-site). Reglas corregidas:
                          sin rama vacia en el regex (antes falseaba a
                          On-site) y con prioridad Hybrid > Remote > On-site.
                          Si el campo explicito falta o no se reconoce
                          (p.ej. "Flexible"), se deriva de titulo+descripcion.
                          Vocabulario ampliado: "fully remote", "100% remote",
                          "remote-first", "telecommute", "home-based",
                          "office-based", "work from office", ...
    - location_region, location_country: Si están vacíos, se copia la ciudad en
      la región (si no es un país) y se infiere el país correcto. Además, los
      valores YA existentes se normalizan al nombre canónico en inglés
      (España -> Spain, Irlanda -> Ireland, Holanda -> Netherlands,
      Suiza -> Switzerland, Europa -> Europe), tanto si vienen en español
      como en inglés. En España location_region se normaliza al nombre inglés
      de la COMUNIDAD AUTÓNOMA (Vizcaya/País Vasco -> Basque Country,
      Cataluña/Barcelona -> Catalonia, Comunidad de Madrid -> Madrid, ...)
      para pintar el mapa de España por región.
    - location_city         : normalizacion al nombre canonico en ingles
                          (alineado con Dim_Geo): "Madrid y alrededores" ->
                          Madrid, "Greater Dublin Area" -> Dublin,
                          "Dublin 1" -> Dublin, "Dublín" -> Dublin, ...
                          Desconocida -> se conserva tal cual (no se inventa).
    - posted_date         : si la oferta no trae fecha de publicacion, se
                          rellena con la fecha de scraping (scraped_at) o, en
                          su defecto, con _ingest_date. Evita perder ofertas
                          con salario de portales que no publican fecha. El
                          valor original queda en posted_date_raw y el origen
                          en posted_date_source (posted/scraped).
    - Trazabilidad          : columnas *_source que indican el origen de cada
                          valor: work_mode_source (explicit/derived),
                          experience_level_source (existing/title/description),
                          skills_source (existing/derived),
                          salary_source (existing/parsed_raw).
                          Se recalculan siempre (describen el estado de la fila).

NOTA: la columna de descripcion por defecto es "description_clean" (inmutable
en este proyecto). Si tu pipeline usa otro nombre, pasalo con desc_col=...

CONTRATO DE PRIORIDAD (first-match wins):
  La PRIMERA regla de cada lista (_EXP_RULES, _EMP_RULES, _ROLE_RULES) que
  matchee gana. Las funciones puras role_category / experience_level /
  employment_type devuelven exactamente el mismo resultado que las columnas de
  enrich() y sirven para diagnosticar sin Spark.
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
            "PySpark", "NumPy", "Keras", "NLP", "Airbyte", "dbt Core",
            "Snowflake", "BigQuery", "Redshift", "Data Warehouse",
            "ETL/ELT", "Streaming", "ClickHouse", "Elasticsearch",
            "OpenAI", "LangChain", "RAG", "LLM", "GenAI","Microsoft Fabric",
        ]

# Skills adicionales que amplian el vocabulario (se suman a las keywords
# curadas vengan de donde vengan). Canonical name = la propia keyword.
_EXTRA_SKILLS = [
    "Statistics", "Forecasting", "Time Series", "A/B Testing",
    "Experimentation", "Data Modeling", "Data Governance", "Data Quality",
    "Data Lake", "Lakehouse", "Unity Catalog", "API", "REST", "GitHub",
    "GitLab", "Jenkins", "CI/CD", "SAP", "Salesforce", "Google Analytics",
    "Amplitude", "Mixpanel", "SharePoint", "Power Apps", "VBA", "SPSS",
    "Data Mining", "Data Visualization", "Dashboarding",
    "Statistical Analysis", "PowerPoint","Fabric"
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
    "pyspark": "PySpark", "numpy": "NumPy", "keras": "Keras",
    "openai": "OpenAI", "langchain": "LangChain", "rag": "RAG",
    "llm": "LLM", "genai": "GenAI", "nlp": "NLP", "dbt core": "dbt",
    "etl/elt": "ETL/ELT", "data warehouse": "Data Warehouse",
    "machine learning": "Machine Learning", "deep learning": "Deep Learning",
    "airbyte": "Airbyte", "clickhouse": "ClickHouse",
    "elasticsearch": "Elasticsearch",
    # --- ampliacion de vocabulario (sinonimos y nuevas skills) ---
    "ml": "Machine Learning", "postgres": "PostgreSQL",
    "statistics": "Statistics", "forecasting": "Forecasting",
    "time series": "Time Series", "a/b testing": "A/B Testing",
    "experimentation": "Experimentation", "data modeling": "Data Modeling",
    "data governance": "Data Governance", "data quality": "Data Quality",
    "data lake": "Data Lake", "lakehouse": "Lakehouse",
    "unity catalog": "Unity Catalog", "api": "API", "rest": "REST",
    "github": "GitHub", "gitlab": "GitLab", "jenkins": "Jenkins",
    "ci/cd": "CI/CD", "sap": "SAP", "salesforce": "Salesforce",
    "google analytics": "Google Analytics", "amplitude": "Amplitude",
    "mixpanel": "Mixpanel", "sharepoint": "SharePoint",
    "power apps": "Power Apps", "vba": "VBA", "spss": "SPSS",
    "data mining": "Data Mining", "data visualization": "Data Visualization",
    "dashboarding": "Dashboarding", "statistical analysis": "Statistical Analysis",
    "powerpoint": "PowerPoint","fabric":"Microsoft Fabric"
}

# Patrones mas estrictos para keywords cortas/ambiguas: la keyword suelta con
# word boundaries puede producir falsos positivos ("R" en "R&D", "ML" en
# "MLOps" no, pero "ML" es demasiado generico en otros contextos). Aqui se
# afina el patron de cada token. El texto capturado sigue siendo la keyword en
# minusculas (clave del mapa canonic).
SKILLS_STRICT_PATTERNS = {
    "r": r"\br\b(?!&)",            # "R" pero no "R&D"
    "ml": r"\bml\b",               # "ML" -> Machine Learning (sin MLOps/MLflow)
    "api": r"\bapi\b",
    "sap": r"\bsap\b",
    "vba": r"\bvba\b",
}

# Un solo regex con todas las keywords (word boundaries + case-insensitive).
# OJO: en Spark hay que envolverlo en F.lit() (si se pasa como string plano, lo
# interpreta como nombre de columna -> UNRESOLVED_COLUMN).
# OJO2: UN SOLO grupo capturador alrededor de toda la alternancia (regexp_
# extract_all devuelve el grupo 1 de cada match; si cada alternativa tuviera su
# propio grupo, las alternativas 2..N devolverian null).
_SKILLS_TOKEN_PATTERNS: list[tuple[str, str]] = []
for _kw in DATA_SKILLS_KEYWORDS + _EXTRA_SKILLS:
    _key = _kw.lower()
    _pat = SKILLS_STRICT_PATTERNS.get(_key, r"\b" + re.escape(_key) + r"\b")
    _SKILLS_TOKEN_PATTERNS.append((_key, _pat))

SKILLS_REGEX = "(?i)(" + "|".join(
    pat for _, pat in _SKILLS_TOKEN_PATTERNS
) + ")"

# Reglas de seniority. ORDEN = prioridad (mayor a menor): la primera regla que
# matchee gana. `senior` suelto como ultimo recurso (replica
# models.EXPERIENCE_LEVEL_MAP); riesgo de falso positivo ("senior
# stakeholders") se mitiga con _EXP_EXCLUDE_PHRASES sobre la descripcion.
_EXP_RULES = [
    ("Executive",   r"(?i)\b(?:executive|ejecutivo|executivo|vp|"
                    r"vice\s+president|head\s+of|chief)\b"),
    ("Director",    r"(?i)\b(?:director(?:a)?|directiva)\b"),
    ("Mid-Senior",  r"(?i)\b(?:mid[\s\-–]+senior|senior|sr\.?|staff|principal)\b"),
    ("Associate",   r"(?i)\b(?:associate|asociad[oa])\b"),
    ("Intern",   r"(?i)\b(?:internship|intern|practicas|prácticas|becari[oa]|"
                 r"pasant[íi]a|trainee|apprentice|placement)\b"),
    ("Entry",       r"(?i)\b(?:entry\s*[- ]?level|entry|j[úu]nior|jr\.?|"
                    r"graduate|early\s+career|nivel de entrada)\b"),
]

# Frases de CONTEXTO que, si aparecen en la descripcion, no son el nivel del
# candidato ("interactua con senior management", "reporting to the senior
# director", ...). Se eliminan de la descripcion ANTES de aplicar _EXP_RULES
# cuando se deriva desde descripcion (el titulo no se toca).
_EXP_EXCLUDE_PHRASES = [
    r"senior\s+stakeholders?",
    r"senior\s+leadership",
    r"senior\s+executives?",
    r"senior\s+management",
    r"senior\s+directors?",
    r"reporting\s+to\s+(?:the\s+|a\s+|our\s+)?(?:senior|head|vp|chief|director)",
    r"work(?:ing)?\s+with\s+(?:the\s+)?senior",
]

# Reglas de tipo de jornada. ORDEN = prioridad: la primera que matchee gana.
# Replica el mapeo de parse_detail._normalize_emp_type.
_EMP_RULES = [
    ("Internship",       r"(?i)\b(?:internship|practicas|prácticas|becari[oa]|"
                         r"pasant[íi]a|trainee|en pr[áa]cticas)\b"),
    ("Part Time",   r"(?i)\b(?:media jornada|part[- ]?time|part time|"
                    r"medio tiempo)\b"),
    ("Contract",        r"(?i)\b(?:contrato|contract|temporal|freelance)\b"),
    ("Full Time", r"(?i)\b(?:jornada completa|tiempo completo|"
                     r"full[- ]?time|full time)\b"),
]

# Categorias de rol. ORDEN = prioridad (lo especifico primero). "Data (Other)"
# es el cajon de sastre y SIEMPRE va el ultimo.
_ROLE_RULES = [
    ("Machine Learning & AI",
     r"(?i)\b(?:machine learning|ml engineer|mlops|deep learning|ai engineer|"
     r"ai solution|ai platform|ai product|artificial intelligence|llm|"
     r"generative ai|genai|\bai\b)\b"),
    ("Data Scientist",
     r"(?i)\b(?:data scientist|scientist|cient[ií]fic[oa] de datos|"
     r"cienc[ií]a de datos|daten\s*scientist)\b"),
    ("Analytics Engineer",
     r"(?i)\b(?:analytics engineer|analytics engineering)\b"),
    ("Data Engineer",
     r"(?i)\b(?:data engineer|data engineering|big data|data platform|"
     r"data solution|data architect|database engineer|datenbank|daten\s*"
     r"engineer|\bdatabase\b|ingenier[oa] de datos)\b"),
    ("Data Analyst & BI",
     r"(?i)\b(?:data analyst|data analytics|business analyst|analista|"
     r"analyst|analytics|business intelligence|power bi|bi developer|"
     r"tableau|qlik|looker|datenanalyst|datenanalyse|reporting analyst|"
     r"insights analyst|\bbi\b)\b"),
    ("Data (Other)",
     r"(?i)\b(?:data|datos|dato|daten)\b"),
]

# Multiplicador a base ANUAL segun el periodo del salario (orden = prioridad,
# el primero que aparezca en el texto del periodo gana):
#   hour x2080   (jornada de 40h/semana x 52 semanas)
#   day  x260    (5 dias laborables x 52 semanas)
#   week x52     (calendario estandar; equivale a algo menos que "x4 y x12")
#   month x12
#   (cualquier otra cosa / year / "" -> x1 = ya es anual)
#
# IMPORTANTE (bug corregido): cascade() itera las reglas EN ORDEN INVERSO
# para que rules[0] quede mas externa y gane (first-match wins). El bucle
# del factor salarial debe usar reversed(_ANNUAL_TOKEN_FACTORS) por la misma
# razon: si se iterara en orden directo, el ULTIMO token ("month") ganaria y
# un texto como "45€/hour, monthly" se anualizaria x12 en vez de x2080.
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
# (hora/diario) NO tienen cubo propio en el enum -> "" (no falsear el dato;
# el factor x2080/x260 ya lo aplica salary_min_annual/max_annual).
# NOTA: patrones SIN acentos (año->ano) porque rlike en Spark no es
# Unicode-aware: "año" no matchea \ba[ñn]o\b en algunos runtimes.
_PERIOD_NORM_RULES = [
    ("YEAR",  r"(?i)(?:anual|year|ano|jaar|jahr|per\s+year|per\s+annum|"
              r"annual|yearly|p\s*[./]?\s*j\.?)"),
    ("MONTH", r"(?i)(?:mensual|month|mes|maand|monat|per\s+month|monthly|"
              r"p\s*[./]?\s*m\.?)"),
    ("WEEK",  r"(?i)(?:semanal|semana|week|woche|weekly|per\s+week)"),
    ("DAY",   r"(?i)(?:daily|diari[oa]|dag|t[äa]glich)"),
    ("HOUR",  r"(?i)(?:hora|hour|hourly|uur|stunde|per\s+hour|"
              r"p\s*[./]?\s*u\.?)"),

]

# Normalizacion de work_mode según los diferentes idiomas
# OJO: las alternativas van SIN acentos y SIN la rama vacia al final
# (antes habia "|)" final -> el regex matcheaba SIEMPRE y podia falsear
# el valor a On-site). Prioridad: Hybrid antes que Remote (un "remote
# hybrid" es hibrido) y On-site al final.
# Se aplica sobre el valor explicito de work_mode y, si este no aporta,
# sobre el texto concatenado de titulo + descripcion (ver enrich()).
_WRK_RULES = [
    ("Hybrid",  r"(?i)\b(?:hybrid|hibrid[oa]?|híbrid[oa]?|semipresencial|mixt[oa]|"
                r"hybride|mobiles?\s+arbeiten|telearbeit)\b"),
    ("Remote",  r"(?i)\b(?:remote|remoto|remota|teletrabajo|work\s*from\s*home|"
                r"\bwfh\b|home\s*based|home[\s-]?office|thuiswerk\w*|telewerk\w*|"
                r"telecommute|remote[- ]?first|fully\s+remote|100\s*%\s*"
                r"(?:remote|home)|trabajo\s+a\s+distancia)\b"),
    ("On-site", r"(?i)\b(?:onsite|on\s*[- ]?site|presencial|in\s*[- ]?person|"
                r"office[- ]?based|work\s*from\s*office|in\s*[- ]?office|"
                r"vor\s+ort)\b"),
]

# ---------------------------------------------------------------------------
# Parseo de salary_raw (texto bruto de salario). Mismos patrones en la
# funcion pura parse_salary_raw() y en la pasada columnar de enrich().
# ---------------------------------------------------------------------------
_SALARY_RNG_K = re.compile(
    r"(\d+(?:\.\d+)?)\s*k\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*k", re.I)
_SALARY_ONE_K = re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.I)
_SALARY_RNG = re.compile(r"(\d+(?:[.,]\d+)*)\s*(?:-|–|—|to)\s*(\d+(?:[.,]\d+)*)")
_SALARY_ONE = re.compile(r"(\d+(?:[.,]\d+)*)")

_SALARY_CLEAN_RE = re.compile(
    r"[€£$]|\b(?:eur|euros?|gbp|usd|chf|sfr|francs?)\b|"
    r"\bper\s+|\bp\.?\s*[mju]\.?\b", re.I)

_SALARY_CURRENCY_RE = [
    ("EUR", re.compile(r"€|eur\b", re.I)),
    ("GBP", re.compile(r"£|gbp\b", re.I)),
    ("USD", re.compile(r"\$|usd\b", re.I)),
    ("CHF", re.compile(r"chf|sfr|francs?\b", re.I)),
]


def _parse_number(s: str):
    """Convierte un importe con separadores ES/EN/NL a float.

    - "41,260" / "3,500.00" (EN) -> 260 / 3500.0
    - "3.500" / "3.500,00" (ES/NL) -> 3500.0
    """
    if not s:
        return None
    s = s.strip()
    if "," in s and "." in s:
        # El ultimo separador es el decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # "41,260" (miles EN) vs "3,50" (decimal ES).
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s:
        # "3.500" (miles ES/NL) vs "3.50" (decimal).
        parts = s.split(".")
        if all(len(p) == 3 for p in parts[1:]) and len(parts[0]) <= 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None

# ---------------------------------------------------------------------------
# Agrupacion de las empresas mas grandes (normalizacion canonica de
# company_name). Las variantes de una misma compania ("Google" / "Google
# Ireland Ltd" / "Google DeepMind") se agrupan bajo un UNICO nombre para que
# el conteo de ofertas por empresa agregue correctamente en BI.
#
# first-match wins: el PRIMER patron que matchee gana (mismo contrato que
# _EXP_RULES). Patrones con lookahead negativo para evitar falsos positivos
# ("Audi Revolut F1 Team" no es Revolut, "Orange Quarter" no es Orange).
# ---------------------------------------------------------------------------
COMPANY_ALIAS_PATTERNS = [
    # --- Big tech ---
    ("Google", r"(?i)\bgoogle\b"),
    ("Amazon", r"(?i)\bamazon\b|\baws\b"),
    ("Microsoft", r"(?i)\bmicrosoft\b"),
    ("Apple", r"(?i)\bapple\b"),
    ("Meta", r"(?i)\bmeta\b"),
    ("IBM", r"(?i)\bibm\b"),
    ("Salesforce", r"(?i)\bsalesforce\b"),
    ("Oracle", r"(?i)\boracle\b"),
    ("Nvidia", r"(?i)\bnvidia\b"),
    ("Intel", r"(?i)\bintel\b"),
    ("Samsung", r"(?i)\bsamsung\b"),
    ("SAP", r"(?i)\bsap\b"),
    ("Logitech", r"(?i)\blogitech\b"),
    ("Nokia", r"(?i)\bnokia\b"),
    ("Lenovo", r"(?i)\blenovo\b"),
    # --- Consultoras y servicios TI ---
    ("Accenture", r"(?i)\baccenture\b"),
    ("Deloitte", r"(?i)\bdeloitte\b"),
    ("Capgemini", r"(?i)\bcapgemini\b"),
    ("Cognizant", r"(?i)\bcognizant\b"),
    ("EPAM", r"(?i)\bepam\b"),
    ("Infosys", r"(?i)\binfosys\b"),
    ("Globant", r"(?i)\bglobant\b"),
    ("McKinsey", r"(?i)\bmckinsey\b|\bquantumblack\b"),
    ("EY", r"(?i)\bey\b|\bernst\s*&\s*young\b"),
    ("PwC", r"(?i)\bpwc\b"),
    ("KPMG", r"(?i)\bkpmg\b"),
    ("UST", r"(?i)\bust\b"),
    ("NTT Data", r"(?i)\bntt\s+data\b"),
    ("Sopra Steria", r"(?i)\bsopra\b"),
    ("Inetum", r"(?i)\binetum\b"),
    ("Devoteam", r"(?i)\bdevoteam\b"),
    ("Adecco", r"(?i)\badecco\b"),
    ("CGI", r"(?i)\bcgi\b"),
    ("Logicalis", r"(?i)\blogicalis\b"),
    ("Aubay", r"(?i)\baubay\b"),
    ("Indra", r"(?i)\bindra\b"),
    ("Izertis", r"(?i)\bizertis\b"),
    ("Babel", r"(?i)\bbabel\b"),
    ("Manpower", r"(?i)\bmanpower(?:group)?\b"),
    ("Randstad", r"(?i)\brandstad\b"),
    ("Gi Group", r"(?i)\bgi\s+group\b"),
    ("Hays", r"(?i)\bhays\b"),
    ("Teleperformance", r"(?i)\bteleperformance\b"),
    ("Acciona", r"(?i)\bacciona\b"),
    ("Criteo", r"(?i)\bcriteo\b"),
    ("Adevinta", r"(?i)\badevinta\b"),
    # --- Banca, seguros y servicios financieros ---
    ("JPMorgan Chase", r"(?i)\bjp\s*morgan(?:chase)?\b"),
    ("Morgan Stanley", r"(?i)\bmorgan\s+stanley\b"),
    ("Mastercard", r"(?i)\bmastercard\b"),
    ("Santander", r"(?i)\bsantander\b"),
    ("BBVA", r"(?i)\bbbva\b"),
    ("CaixaBank", r"(?i)\bcaixabank\b|\bvidacaixa\b"),
    ("ABN AMRO", r"(?i)\babn\s+amro\b"),
    ("Rabobank", r"(?i)\brabobank\b"),
    ("Deutsche Bank", r"(?i)\bdeutsche\s+bank\b"),
    ("Deutsche Telekom", r"(?i)\bdeutsche\s+telekom\b"),
    ("Zurich", r"(?i)\bzurich\b"),
    ("Northern Trust", r"(?i)\bnorthern\s+trust\b"),
    ("Marsh", r"(?i)\bmarsh\b"),
    ("UPMC", r"(?i)\bupmc\b"),
    ("TD SYNNEX", r"(?i)\btd\s+synnex\b"),
    # --- Telecom, transporte y energia ---
    ("Vodafone", r"(?i)\bvodafone(?:ziggo)?\b"),
    ("Telefonica", r"(?i)\btelef[oó]nica\b"),
    ("Orange", r"(?i)\borange\b(?!\s+quarter)"),
    ("T-Mobile", r"(?i)\bt[- ]?mobile\b"),
    ("Verizon", r"(?i)\bverizon\b"),
    ("Boeing", r"(?i)\bboeing\b"),
    ("Airbus", r"(?i)\bairbus\b"),
    # --- Industrial y hardware ---
    ("Siemens", r"(?i)\bsiemens\b"),
    ("Bosch", r"(?i)\bbosch\b"),
    ("ABB", r"(?i)\babb\b"),
    ("Abbott", r"(?i)\babbott\b"),
    ("Philips", r"(?i)\bphilips\b"),
    ("Lockheed Martin", r"(?i)\blockheed\b"),
    ("Northrop Grumman", r"(?i)\bnorthrop\b"),
    ("Honeywell", r"(?i)\bhoneywell\b"),
    ("Vertiv", r"(?i)\bvertiv\b"),
    ("Stryker", r"(?i)\bstryker\b"),
    ("Analog Devices", r"(?i)\banalog\s+devices\b"),
    ("Bentley Systems", r"(?i)\bbentley\s+systems\b"),
    ("Keysight", r"(?i)\bkeysight\b"),
    ("Keyrus", r"(?i)\bkeyrus\b"),
    ("Keyrock", r"(?i)\bkeyrock\b"),
    ("Disney", r"(?i)\bdisney\b"),
    # --- Farma y consumo ---
    ("Roche", r"(?i)\broche\b"),
    ("Thermo Fisher", r"(?i)\bthermo\s+fisher\b"),
    ("Novartis", r"(?i)\bnovartis\b"),
    ("AstraZeneca", r"(?i)\bastrazeneca\b"),
    ("Pfizer", r"(?i)\bpfizer\b"),
    ("Sanofi", r"(?i)\bsanofi\b"),
    ("Merck", r"(?i)\bmerck\b"),
    ("Bayer", r"(?i)\bbayer\b"),
    ("Johnson & Johnson", r"(?i)\bjohnson\s*&\s*johnson\b|\bjanssen\b"),
    ("Bristol Myers Squibb", r"(?i)\bbristol\b"),
    ("Eli Lilly", r"(?i)\blilly\b"),
    ("Takeda", r"(?i)\btakeda\b"),
    ("Medtronic", r"(?i)\bmedtronic\b"),
    ("PepsiCo", r"(?i)\bpepsico\b"),
    ("Procter & Gamble", r"(?i)\bprocter\b"),
    ("Nestle", r"(?i)\bnestl[ée]\b"),
    ("Unilever", r"(?i)\bunilever\b"),
    ("Danone", r"(?i)\bdanone\b"),
    ("Coca-Cola", r"(?i)\bcoca\s*-?\s*cola\b"),
    ("Heineken", r"(?i)\bheineken\b"),
    ("Hershey", r"(?i)\bhershey\b"),
    ("Philip Morris", r"(?i)\bphilip\s+morris\b"),
    ("Nike", r"(?i)\bnike\b"),
    ("Adidas", r"(?i)\badidas\b"),
    # --- Internet, SaaS y retail ---
    ("Fever", r"(?i)\bfever(?:up)?\b"),
    ("Glovo", r"(?i)\bglovo\b"),
    ("Revolut", r"(?i)\brevolut\b(?!\s+f1)"),
    ("Booking", r"(?i)\bbooking\b"),
    ("Expedia", r"(?i)\bexpedia\b"),
    ("Adyen", r"(?i)\badyen\b"),
    ("Stripe", r"(?i)\bstripe\b"),
    ("Twilio", r"(?i)\btwilio\b"),
    ("Databricks", r"(?i)\bdatabricks\b"),
    ("Snowflake", r"(?i)\bsnowflake\b"),
    ("ServiceNow", r"(?i)\bservicenow\b"),
    ("Workday", r"(?i)\bworkday\b"),
    ("GitHub", r"(?i)\bgithub\b"),
    ("CrowdStrike", r"(?i)\bcrowdstrike\b"),
    ("Uber", r"(?i)\buber\b"),
    ("Carrefour", r"(?i)\bcarrefour\b"),
    ("Lidl", r"(?i)\blidl\b"),
    ("IKEA", r"(?i)\bikea\b"),
    ("Just Eat Takeaway", r"(?i)\bjust\s+eat\b"),
    ("Zalando", r"(?i)\bzalando\b"),
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
# pura y en la pasada columnar de enrich(). Los mismos caracteres se usan en
# la pasada columnar (F.translate) y en la pura (str.maketrans).
_LOCATION_ACCENT_SRC = "áéíóúüñèàç"
_LOCATION_ACCENT_DST = "aeiouuneac"
_LOCATION_ACCENTS = str.maketrans(_LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)

_LOCATION_CITY_TO_COUNTRY = {
    "sant cugat del valles":"Spain","palau-solita i plegamans":"Spain","hospitalet de llobregat":"Spain","boadilla del monte":"Spain","pozuelo de alarcon":"Spain","esplugues de llobregat":"Spain","sant quirze del valles":"Spain","getxo":"Spain","amorebieta-etxano":"Spain","la cuesta":"Spain","moncloa-aravaca":"Spain","ibarra":"Spain","mungia":"Spain","amorebieta":"Spain","moralzarzal":"Spain","terrassa":"Spain","sant vicenc dels horts":"Spain",
    "parets del valles":"Spain","el prat de llobregat":"Spain","alcobendas":"Spain","sabadell":"Spain","hospitalet de llobregat":"Spain","igorre":"Spain","barbera del valles":"Spain","boadilla del monte":"Spain","esplugues de llobregat":"Spain","sant just desvern":"Spain","mallabia":"Spain",
    "madrid": "Spain", "barcelona": "Spain", "bilbao": "Spain",
    "valencia": "Spain", "sevilla": "Spain", "seville": "Spain",
    "malaga": "Spain", "zaragoza": "Spain", "granada": "Spain",
    "vigo": "Spain","sant boi de llobregat":"Spain","madrid y alrededores": "Spain",
    "bilbao y alrededores": "Spain","greater madrid metropolitan area":"Spain",
    "greater barcelona metropolitan area":"Spain", "barcelona y alrededores":"Spain",
    "greater bilbao metropolitan area":"Spain","comunidad de madrid":"Spain","community of madrid":"Spain",
    "vizcaya":"Spain","pais vasco / euskadi":"Spain","zamudio":"Spain","san vicente de barakaldo":"Spain",
    "basque country":"Spain", 
    "dublin": "Ireland","greater dublin":"Ireland","dublin y alrededores":"Ireland", "cork": "Ireland", "galway": "Ireland","county dublin":"Ireland",
    "limerick": "Ireland",
    "amsterdam": "Netherlands","amsterdam area": "Netherlands","south holland":"Netherlands", "rotterdam": "Netherlands","rotterdam and the hague":"Netherlands","holanda septentrional":"Netherlands","north holland":"Netherlands",
    "the hague": "Netherlands", "eindhoven": "Netherlands",
    "utrecht": "Netherlands","utrecht area":"Netherlands",
    "zurich": "Switzerland","zurich metropolitan area": "Switzerland", "geneva": "Switzerland", "ginebra": "Switzerland",
    "basel": "Switzerland", "bern": "Switzerland", "lausanne": "Switzerland",
    "espacio economico europeo":"Europe","union europea":"Europe","europa":"Europe"
}

_LOCATION_COUNTRY_NAMES = {
    "spain": "Spain", "espana": "Spain",
    "ireland": "Ireland", "irlanda": "Ireland",
    "netherlands": "Netherlands", "holanda": "Netherlands",
    "paises bajos": "Netherlands",
    "switzerland": "Switzerland", "suiza": "Switzerland",
    "germany": "Germany", "deutschland": "Germany", "alemania": "Germany",
    "austria": "Austria", "austria y suiza": "Austria and Switzerland",
    "united states": "United States", "estados unidos": "United States",
    "europe": "Europe", "europa": "Europe",
    "oriente medio y africa": "Middle East and Africa",
    "middle east and africa": "Middle East and Africa",
}

# Codigos de estado de USA que Glassdoor escribe directamente en la columna
# location_country ("NY", "FL", "PA", ...). Se aplican SOLO sobre el campo de
# pais (no sobre region, que usa _LOCATION_COUNTRY_NAMES como fallback y debe
# conservar los codigos/estados para el mapa de USA por estado).
_LOCATION_US_STATE_ABBREV = {
    "al": "United States", "ak": "United States", "az": "United States",
    "ar": "United States", "ca": "United States", "co": "United States",
    "ct": "United States", "de": "United States", "fl": "United States",
    "ga": "United States", "hi": "United States", "id": "United States",
    "il": "United States", "in": "United States", "ia": "United States",
    "ks": "United States", "ky": "United States", "la": "United States",
    "me": "United States", "md": "United States", "ma": "United States",
    "mi": "United States", "mn": "United States", "ms": "United States",
    "mo": "United States", "mt": "United States", "ne": "United States",
    "nv": "United States", "nh": "United States", "nj": "United States",
    "nm": "United States", "ny": "United States", "nc": "United States",
    "nd": "United States", "oh": "United States", "ok": "United States",
    "or": "United States", "pa": "United States", "ri": "United States",
    "sc": "United States", "sd": "United States", "tn": "United States",
    "tx": "United States", "ut": "United States", "vt": "United States",
    "va": "United States", "wa": "United States", "wv": "United States",
    "wi": "United States", "wy": "United States", "dc": "United States",
}

# Codigo de estado (2 letras) -> nombre completo del estado. Se usa para
# (a) normalizar la region de USA a su nombre canonico (alineado con Dim_Geo)
# y (b) inferir la region desde la ciudad/metro de USA.
_LOCATION_US_STATE_FULL = {
    "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
    "ca": "California", "co": "Colorado", "ct": "Connecticut",
    "de": "Delaware", "fl": "Florida", "ga": "Georgia", "hi": "Hawaii",
    "id": "Idaho", "il": "Illinois", "in": "Indiana", "ia": "Iowa",
    "ks": "Kansas", "ky": "Kentucky", "la": "Louisiana", "me": "Maine",
    "md": "Maryland", "ma": "Massachusetts", "mi": "Michigan",
    "mn": "Minnesota", "ms": "Mississippi", "mo": "Missouri",
    "mt": "Montana", "ne": "Nebraska", "nv": "Nevada", "nh": "New Hampshire",
    "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
    "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio",
    "ok": "Oklahoma", "or": "Oregon", "pa": "Pennsylvania",
    "ri": "Rhode Island", "sc": "South Carolina", "sd": "South Dakota",
    "tn": "Tennessee", "tx": "Texas", "ut": "Utah", "vt": "Vermont",
    "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
    "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
}

# Ciudad / metro de USA (normalizada: minusculas, sin acentos) -> CODIGO de
# estado (2 letras). Se usa cuando location_country ya es "United States" y
# location_region esta vacia: la region se infiere desde la ciudad. Muchas
# veces la "ciudad" que llega es directamente el estado ("New Jersey",
# "Texas") o un metro/area conocida ("Dallas-Fort Worth", "Manhattan").
_LOCATION_US_CITY_TO_STATE = {
    # Estados como ciudad
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO",
    "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "new york state": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    # Ciudades / metros conocidos
    "new york city": "NY", "nyc": "NY", "manhattan": "NY",
    "brooklyn": "NY", "queens": "NY", "long island": "NY",
    "long island-queens": "NY", "dallas": "TX", "dallas-fort worth": "TX",
    "austin": "TX", "houston": "TX", "san antonio": "TX",
    "los angeles": "CA", "la": "CA", "san francisco": "CA",
    "san diego": "CA", "san jose": "CA", "sacramento": "CA",
    "chicago": "IL", "boston": "MA", "seattle": "WA",
    "philadelphia": "PA", "pittsburgh": "PA", "denver": "CO",
    "phoenix": "AZ", "atlanta": "GA", "miami": "FL",
    "minneapolis": "MN", "detroit": "MI", "nashville": "TN",
    "portland": "OR", "las vegas": "NV", "salt lake city": "UT",
    "charlotte": "NC", "raleigh": "NC", "columbus": "OH",
    "indianapolis": "IN", "milwaukee": "WI", "kansas city": "MO",
    "st. louis": "MO", "baltimore": "MD", "new orleans": "LA",
    "tampa": "FL", "orlando": "FL", "cleveland": "OH",
    "cincinnati": "OH", "pittsburg": "PA", "oklahoma city": "OK",
    "tulsa": "OK", "richmond": "VA", "norfolk": "VA",
    "hartford": "CT", "providence": "RI", "albuquerque": "NM",
    "boise": "ID", "des moines": "IA", "omaha": "NE",
    "memphis": "TN", "louisville": "KY", "little rock": "AR",
    "birmingham": "AL", "montgomery": "AL", "jackson": "MS",
    "redstone arsenal": "AL", "township of hamilton": "NJ",
    "hamilton township": "NJ",
}

# Ciudad canonica en ingles (alineada con la tabla Dim_Geo: mismo nombre que
# el campo city de Dim_Geo para poder hacer el JOIN). Claves SIN acentos en
# minusculas; valor = nombre tal cual existe en Dim_Geo ("malaga" -> "Málaga",
# "gijon" -> "Gijón", "dublín"/"dublin 1" -> "Dublin", ...). Si una ciudad no
# esta aqui, no se toca (no se inventa).
_LOCATION_CITY_CANONIC = {
    # Espana
    "madrid": "Madrid", "barcelona": "Barcelona", "valencia": "Valencia",
    "sevilla": "Sevilla", "seville": "Sevilla", "bilbao": "Bilbao",
    "malaga": "Málaga", "zaragoza": "Zaragoza", "granada": "Granada",
    "vigo": "Vigo", "murcia": "Murcia", "palma": "Palma",
    "las palmas": "Las Palmas de Gran Canaria",
    "las palmas de gran canaria": "Las Palmas de Gran Canaria",
    "santa cruz de tenerife": "Santa Cruz de Tenerife",
    "san sebastian": "San Sebastián", "vitoria": "Vitoria-Gasteiz",
    "vitoria-gasteiz": "Vitoria-Gasteiz", "a coruna": "A Coruña",
    "coruna": "A Coruña", "gijon": "Gijón", "oviedo": "Oviedo",
    "santander": "Santander", "pamplona": "Pamplona", "logrono": "Logroño",
    "valladolid": "Valladolid", "alicante": "Alicante",
    "cordoba": "Córdoba", "salamanca": "Salamanca", "burgos": "Burgos",
    "sant boi de llobregat": "Sant Boi de Llobregat",
    "zamudio": "Zamudio",
    "san vicente de barakaldo": "San Vicente de Barakaldo",
    "sant cugat del valles": "Sant Cugat del Vallès",
    "sant quirze del valles": "Sant Quirze del Vallès",
    "parets del valles": "Parets del Vallès",
    "barbera del valles": "Barberà del Vallès",
    "palau-solita i plegamans": "Palau-solità i Plegamans",
    "sant vicenc dels horts": "Sant Vicenç dels Horts",
    # Irlanda
    "dublin": "Dublin", "cork": "Cork", "galway": "Galway",
    "limerick": "Limerick", "waterford": "Waterford",
    "kilkenny": "Kilkenny", "sligo": "Sligo", "drogheda": "Drogheda",
    "athlone": "Athlone", "tralee": "Tralee", "ennis": "Ennis",
    "wexford": "Wexford",
    # Holanda
    "amsterdam": "Amsterdam", "rotterdam": "Rotterdam",
    "rotterdam and the hague": "Rotterdam and The Hague",
    "the hague": "The Hague", "den haag": "The Hague",
    "utrecht": "Utrecht", "eindhoven": "Eindhoven",
    "groningen": "Groningen", "tilburg": "Tilburg", "breda": "Breda",
    "nijmegen": "Nijmegen", "haarlem": "Haarlem",
    "amersfoort": "Amersfoort", "leiden": "Leiden", "delft": "Delft",
    "maastricht": "Maastricht", "zwolle": "Zwolle",
    "enschede": "Enschede", "almere": "Almere", "arnhem": "Arnhem",
    # Suiza
    "zurich": "Zurich", "geneva": "Geneva", "ginebra": "Geneva",
    "basel": "Basel", "bern": "Bern", "berne": "Bern",
    "lausanne": "Lausanne", "winterthur": "Winterthur",
    "lucerne": "Lucerne", "st. gallen": "St. Gallen",
    "st gallen": "St. Gallen", "lugano": "Lugano", "zug": "Zug",
    "fribourg": "Fribourg", "neuchatel": "Neuchâtel",
    # Estados Unidos
    "new york": "New York", "los angeles": "Los Angeles",
    "chicago": "Chicago", "houston": "Houston", "phoenix": "Phoenix",
    "philadelphia": "Philadelphia", "san antonio": "San Antonio",
    "san diego": "San Diego", "dallas": "Dallas", "san jose": "San Jose",
    "austin": "Austin", "san francisco": "San Francisco",
    "seattle": "Seattle", "denver": "Denver",
    "washington": "Washington D.C.", "washington d.c.": "Washington D.C.",
    "boston": "Boston", "miami": "Miami", "atlanta": "Atlanta",
    "detroit": "Detroit", "minneapolis": "Minneapolis",
    "las vegas": "Las Vegas", "portland": "Portland", "nashville": "Nashville",
}

# Limpieza por regex del location_city (ya normalizado: minusculas, sin
# acentos) ANTES del lookup canonico. Se aplican EN ORDEN y de forma
# acumulativa; tras cada regla se reintenta el lookup. Cubre distritos
# numerados ("Dublin 1", "Dublin 10", "Dublin - 2"), "Greater X Area",
# "X y alrededores", "X area", "X region", ...
_LOCATION_CITY_CLEAN_RULES = [
    (r"\s+-\s+\d+$", ""),                      # "dublin - 1" -> "dublin"
    (r"\s+\d+(?:st|nd|rd|th)?$", ""),          # "dublin 1", "dublin 10" -> "dublin"
    (r"^greater\s+", ""),                      # "greater dublin ..." -> "dublin ..."
    (r"\s+metropolitan\s+area\s*$", ""),       # "... metropolitan area" -> "..."
    (r"\s+y\s+alrededores\s*$", ""),           # "madrid y alrededores" -> "madrid"
    (r"\s+(?:and|&)\s+surroundings\s*$", ""),  # "dublin and surroundings" -> "dublin"
    (r"\s+surrounding\s+area\s*$", ""),        # "dublin surrounding area" -> "dublin"
    (r"\s+area\s*$", ""),                      # "amsterdam area" -> "amsterdam"
    (r"\s+region\s*$", ""),                    # "dublin region" -> "dublin"
    (r"\s*/\s*.*$", ""),                       # "pais vasco / euskadi" -> "pais vasco"
]

# Normalizacion de location_region al nombre canonico en ingles de la
# COMUNIDAD AUTONOMA espanola (para pintar el mapa de Espana por region):
#   "Vizcaya"/"País Vasco" -> Basque Country, "Cataluña"/"Barcelona" ->
#   Catalonia, "Comunidad de Madrid" -> Madrid, ...
# Las claves van SIN acentos en minusculas. Provincias y ciudades espanolas
# mapean a su comunidad; regiones de OTROS paises NO estan aqui -> se
# conservan tal cual (no se inventa).
_LOCATION_REGION_CANONIC = {
    # --- Pais Vasco / Euskadi ---
    "pais vasco": "Basque Country", "pais vasco / euskadi": "Basque Country",
    "euskadi": "Basque Country", "basque country": "Basque Country",
    "vizcaya": "Basque Country", "guipuzcoa": "Basque Country",
    "alava": "Basque Country", "bilbao": "Basque Country",
    "zamudio": "Basque Country", "san vicente de barakaldo": "Basque Country",
    "san sebastian": "Basque Country", "vitoria": "Basque Country",
    "vitoria-gasteiz": "Basque Country",
    "amorebieta":"Basque Country","amorebieta-etxano":"Basque Country",
    "getxo":"Basque Country","ibarra":"Basque Country","igorre":"Basque Country","mungia":"Basque Country",
    # --- Cataluna ---
    "cataluna": "Catalonia", "catalunya": "Catalonia",
    "catalonia": "Catalonia", "barcelona": "Catalonia",
    "barcelona y alrededores": "Catalonia",
    "greater barcelona metropolitan area": "Catalonia",
    "sant boi de llobregat": "Catalonia",
    "girona": "Catalonia", "gerona": "Catalonia",
    "lleida": "Catalonia", "lerida": "Catalonia",
    "tarragona": "Catalonia","sabadell":"Catalonia","terrassa":"Catalonia","el prat de llobregat":"Catalonia","esplugues de llobregat":"Catalonia","hospitalet de llobregat":"Catalonia",
    "sant cugat del valles": "Catalonia",
    "sant quirze del valles": "Catalonia",
    "parets del valles": "Catalonia",
    "barbera del valles": "Catalonia",
    "palau-solita i plegamans": "Catalonia",
    "sant vicenc dels horts": "Catalonia",
    # --- Madrid ---
    "madrid": "Madrid", "comunidad de madrid": "Madrid",
    "community of madrid": "Madrid", "madrid y alrededores": "Madrid",
    "greater madrid metropolitan area": "Madrid","alcobendas":"Madrid","spain":"Madrid","madrid (m":"Madrid","moncloa-aravaca":"Madrid","pozuelo-alarcon":"Madrid",
    # --- Dublin ---
    "county dublin": "Dublin", "dublin": "Dublin","ireland":"Dublin","condado de kildare":"Kildare",
    "condado de meath":"Meath","condado de waterford":"Waterford","condado de wicklow":"Wicklow","county carlow":"Carlow",
    "county cavan":"Cavan","county clare":"Clare","county cork":"Cork","county limerick":"Limerick",
    "county longford":"Longford","county monaghan":"Monaghan","galway and cork":"Galway",
    "county roscommon":"Roscommon",
    "county sligo":"Sligo","county tipperary":"Tipperary",
    "county wexford":"Wexford",
    "county kildare":"Kildare",
    "county meath":"Meath","county waterford":"Waterford","county wicklow":"Wicklow",
    # --- Netherlands ---
    "netherlands":"North Holland","the netherlands":"North Holland",
    "rotterdam and the hague": "South Holland",
    "rotterdam": "South Holland", "the hague": "South Holland",
    "holanda septentrional": "North Holland",
    "holanda meridional": "South Holland",
    "north holland": "North Holland", "south holland": "South Holland",
    "flevoland": "Flevoland", "flevolanda": "Flevoland",
    # --- Germany ---
    "baden-wurtemberg": "Baden-Württemberg", "baden wurtemberg": "Baden-Württemberg",
    "baviera": "Bavaria", "bayern": "Bavaria", "bavaria": "Bavaria",
    # --- Switzerland ---
    "switzerland":"Zurich", "suiza": "Zurich",
    "lucerna": "Lucerne", "luzern": "Lucerne",
    "argovia": "Aargau",
    "escafusa": "Schaffhausen",
    "ginebra": "Geneva",
    # --- Europe (nivel continente/region, usado para corregir el pais) ---
    "europe": "Europe", "europa": "Europe",
    "union europea": "Europe", "espacio economico europeo": "Europe",
    "european union": "Europe", "european economic area": "Europe",
}

# ---------------------------------------------------------------------------
# Extraccion de region CONOCIDA embebida en un texto de location_region sucio
# (a veces llega texto de la descripcion, p.ej. "...based in Athenry... 
# Galway. This is a full-time position..."). Se busca el nombre de una region
# conocida (pais/condado/provincia/canton/estado USA) como palabra, y si se
# encuentra se usa esa region en vez del texto. Si no se encuentra NADA y el
# texto tiene mas de 100 caracteres, la region se deja vacia (ruido).
#
# Fuentes de nombres: los valores canonicos de _LOCATION_REGION_CANONIC, las
# claves del mismo mapa (variantes ES/EN, "county X", "condado de X", ...),
# los estados USA por su nombre completo, y una lista extra de condados/
# provincias/cantones no cubiertos. El patron gana mas especifico primero
# (los mas largos antes que los cortos: "Basque Country" > "Country").
_LOCATION_REGION_TEXT_EXTRA = [
    # Irlanda (condados)
    "Antrim", "Armagh", "Carlow", "Cavan", "Clare", "Cork", "Donegal",
    "Down", "Dublin", "Fermanagh", "Galway", "Kerry", "Kildare", "Kilkenny",
    "Laois", "Leitrim", "Limerick", "Londonderry", "Longford", "Louth",
    "Mayo", "Meath", "Monaghan", "Offaly", "Roscommon", "Sligo", "Tipperary",
    "Tyrone", "Waterford", "Westmeath", "Wexford", "Wicklow", "Shannon",
    "Fingal", "Dun Laoghaire-Rathdown", "South Dublin", "North Dublin",
    "North Brabant", "Groningen", "Gelderland", "Limburg", "Overijssel",
    "Friesland", "Zeeland", "Drenthe", "Ticino", "Fribourg", "Neuchatel",
    "Nidwalden", "Uri", "Appenzell", "Graubunden", "Jura", "Valais",
    "Solothurn", "Obwalden", "Thurgau", "Glarus", "Schwyz",
    # Espana (comunidades no incluidas en _LOCATION_REGION_CANONIC)
    "Valencia", "Sevilla", "Malaga", "Zaragoza", "Granada", "Murcia",
    "Valladolid", "Alicante", "Cordoba", "Salamanca", "Burgos", "Andalusia",
    "Galicia", "Castile and Leon", "Castile-La Mancha", "Canary Islands",
    "Islas Baleares", "Las Palmas", "Santa Cruz de Tenerife",
]

_region_names = (
    set(_LOCATION_REGION_CANONIC.values())
    | set(_LOCATION_US_STATE_FULL.values())
    | set(_LOCATION_REGION_TEXT_EXTRA)
)
_LOCATION_REGION_TEXT_TOKENS: dict[str, str] = {}
for _name in _region_names:
    _key = _name.lower().translate(_LOCATION_ACCENTS)
    if not _key:
        continue
    # El nombre de la region como token. Con el lookbehind negativo del regex
    # combinado basta: "county galway" / "co. galway" contienen " galway" con
    # espacio antes, asi que el token base ya matchea ("galway" -> Galway).
    _LOCATION_REGION_TEXT_TOKENS.setdefault(_key, _name)
# Claves del mapa de regiones canonicas tambien son variantes buscables
# (p.ej. "cataluna" -> Catalonia, "county dublin" -> Dublin, "holanda
# septentrional" -> North Holland). OJO: se EXCLUYEN las claves que son
# nombres de PAIS ("ireland" -> Dublin, "spain" -> Madrid): en un texto de
# descripcion "Ireland" aparece con frecuencia y generaria falsos positivos.
_country_name_keys = set(_LOCATION_COUNTRY_NAMES) | set(_LOCATION_US_STATE_ABBREV)
for _alias, _canon in _LOCATION_REGION_CANONIC.items():
    _a = _alias.lower().translate(_LOCATION_ACCENTS)
    if not _a or _a in _country_name_keys:
        continue
    _LOCATION_REGION_TEXT_TOKENS.setdefault(_a, _canon)

# UN solo regex con todas las variantes (alternancia ordenada por longitud
# descendente, lookbehind negativo a la izquierda para matchear la region como
# prefijo de palabra: "kilkennylocation" -> Kilkenny, "KildareSalary" ->
# Kildare). El grupo capturador devuelve EXACTAMENTE la variante matcheada,
# que luego se resuelve a su region canonica con _LOCATION_REGION_TEXT_TOKENS.
# Se usa igual en la funcion pura (re.search) y en la pasada columnar
# (regexp_extract + mapa), para que el resultado sea identico.
_LOCATION_REGION_TEXT_REGEX = (
    "(?i)(?<!\\w)("
    + "|".join(re.escape(t) for t in sorted(
        _LOCATION_REGION_TEXT_TOKENS, key=len, reverse=True))
    + ")"
)
_LOCATION_REGION_TEXT_REGEX_OBJ = re.compile(_LOCATION_REGION_TEXT_REGEX)

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
    """Nivel de seniority sobre UN texto (titulo o descripcion): gana la
    PRIMERA regla de _EXP_RULES que matchee. Las frases de contexto de
    _EXP_EXCLUDE_PHRASES se eliminan antes del matching.

    No clasificable -> "Unknown" (la columna de enrich() deriva del titulo
    primero y, si no aporta, de la descripcion).
    """
    scrubbed = text or ""
    for pat in _EXP_EXCLUDE_PHRASES:
        scrubbed = re.sub(pat, " ", scrubbed)
    for level, pattern in _EXP_RULES:
        if re.search(pattern, scrubbed):
            return level
    return "Unknown"


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
    Desconocido/vacio -> "". Los periodos sub-mensuales (hora/diario) NO
    tienen cubo propio en el enum -> "" (no falsear el dato).
    """
    return _period_from_text(period) if _period_from_text(period) in ("YEAR", "MONTH", "WEEK") else ""


def _period_from_text(text: str) -> str:
    """Primer token de periodo (YEAR/MONTH/WEEK/DAY/HOUR) en un texto."""
    t = (text or "").strip().lower()
    for value, pattern in _PERIOD_NORM_RULES:
        if re.search(pattern, t):
            return value
    return ""


def work_mode_normalized(text: str) -> str:
    """Normaliza el modo de trabajo a Hybrid/Remote/On-site.

    Identico resultado al de la columna work_mode_norm que crea enrich().
    Desconocido/vacio -> "".
    """
    t = text.strip().lower()
    for value, pattern in _WRK_RULES:
        if re.search(pattern, t):
            return value
    return ""


def company_name_canonical(name: str) -> str:
    """Nombre canonico de empresa: agrupa variantes de las grandes companias.

    - first-match wins sobre COMPANY_ALIAS_PATTERNS: "Google Ireland Ltd" ->
      "Google", "Amazon Web Services (aws)" -> "Amazon", "Jp Morgan" ->
      "JPMorgan Chase", "Ust España & Latam" -> "UST", ...
    - Los lookaheads negativos evitan falsos positivos ("Audi Revolut F1 Team"
      no se agrupa en Revolut, "Orange Quarter" no en Orange).
    - Nombre no reconocido -> se conserva tal cual (no se inventa).

    Identico resultado al de la funcion apply_company_canonical() (columnar).
    """
    value = (name or "").strip()
    for canon, pattern in COMPANY_ALIAS_PATTERNS:
        if re.search(pattern, value):
            return canon
    return value


def parse_salary_raw(text: str) -> tuple[float | None, float | None, str, str]:
    """Parsea un texto bruto de salario -> (salary_min, salary_max, currency,
    period).

    - "40k-55k" / "€40k–€55k"          -> (40000, 55000, EUR, "")
    - "40,000 - 55,000 EUR per year"   -> (40000, 55000, EUR, YEAR)
    - "120k" / "120k annually"         -> (120000, None, "", YEAR)
    - "£25/hour"                       -> (25, None, GBP, HOUR)
    - "competitive salary"             -> (None, None, "", "")

    Misma logica que la pasada columnar de enrich() (add_salary_parse_raw).
    """
    raw = (text or "").strip().lower()
    clean = re.sub(r"\s+", " ", _SALARY_CLEAN_RE.sub(" ", raw))
    m = _SALARY_RNG_K.search(clean)
    if m:
        smin = float(m.group(1)) * 1000
        smax = float(m.group(2)) * 1000
    else:
        m = _SALARY_ONE_K.search(clean)
        if m:
            smin = float(m.group(1)) * 1000
            smax = None
        else:
            m = _SALARY_RNG.search(clean)
            if m:
                smin = _parse_number(m.group(1))
                smax = _parse_number(m.group(2))
            else:
                m = _SALARY_ONE.search(clean)
                smin = _parse_number(m.group(1)) if m else None
                smax = None
    currency = next((code for code, pat in _SALARY_CURRENCY_RE
                     if pat.search(raw)), "")
    return smin, smax, currency, _period_from_text(raw)


def _city_clean(norm: str) -> str:
    """Aplica en orden (y de forma acumulativa) las reglas de limpieza de
    _LOCATION_CITY_CLEAN_RULES sobre una ciudad ya normalizada (minusculas,
    sin acentos). Resultado identico al del paso columnar de enrich().
    """
    cleaned = norm
    for pattern, repl in _LOCATION_CITY_CLEAN_RULES:
        cleaned = re.sub(pattern, repl, cleaned).strip()
    return cleaned


def location_city_normalized(city: str) -> str:
    """Normaliza location_city al nombre canonico en ingles (Dim_Geo).

    - minusculas + sin acentos antes del lookup ("Dublín" -> "dublin").
    - lookup exacto en _LOCATION_CITY_CANONIC (gana).
    - si no hay match, limpieza por regex (_city_clean): distritos numerados
      ("Dublin 1", "Dublin 10"), "Greater X Area", "X y alrededores",
      "X area", ... y reintento del lookup.
    - si aun asi no se reconoce, devuelve el valor original (no se inventa).

    Identico resultado al de la columna location_city que normaliza enrich().
    """
    value = (city or "").strip()
    if not value:
        return ""
    norm = value.lower().translate(_LOCATION_ACCENTS)
    canon = _LOCATION_CITY_CANONIC.get(norm)
    if canon:
        return canon
    return _LOCATION_CITY_CANONIC.get(_city_clean(norm), value)


def location_fill(city: str, region: str, country: str) -> tuple[str, str]:
    """Devuelve (region, country) corregidos a partir de location_city.

    Identico resultado al de las columnas location_region / location_country
    que crea enrich():
    - city vacia o no reconocida -> devuelve region/country sin tocar
      (pero normalizados a ingles si se reconocen).
    - city es un pais (Spain/Ireland/Netherlands/Switzerland, en ingles o
      español) -> se copia en region y country (solo si estan vacios).
    - city es una ciudad conocida:
        region vacia  -> region = ciudad canonica, o su COMUNIDAD AUTONOMA
                         si es espanola ("Barcelona" -> Catalonia,
                         "Sant Boi de Llobregat" -> Catalonia).
        country vacio -> country = pais de la ciudad.
    - Los valores YA existentes de region/country se traducen al nombre
      canonico en ingles cuando se reconocen: Espana -> Spain, Irlanda ->
      Ireland, Holanda -> Netherlands, Suiza -> Switzerland, Dublin ->
      Ireland, Vizcaya/País Vasco -> Basque Country, Cataluña/Barcelona ->
      Catalonia, Comunidad de Madrid -> Madrid, ... Si no se reconocen, se
      conservan tal cual.
    """

    def _to_english(name: str, with_cities: bool = False) -> str:
        value = (name or "").strip()
        if not value:
            return ""
        norm = value.lower().translate(_LOCATION_ACCENTS)
        mapped = _LOCATION_COUNTRY_NAMES.get(norm)
        if mapped:
            return mapped
        us_state = _LOCATION_US_STATE_ABBREV.get(norm)
        if us_state:
            return us_state
        if with_cities:
            return _LOCATION_CITY_TO_COUNTRY.get(norm, value)
        return value

    def _region_to_english(name: str) -> str:
        value = (name or "").strip()
        if not value:
            return ""
        norm = value.lower().translate(_LOCATION_ACCENTS)
        mapped = _LOCATION_REGION_CANONIC.get(norm)
        if mapped:
            return mapped
        mapped = _LOCATION_REGION_CANONIC.get(_city_clean(norm))
        if mapped:
            return mapped
        country_mapped = _LOCATION_COUNTRY_NAMES.get(norm)
        if country_mapped:
            return country_mapped
        # location_region a veces llega contaminado con texto de la descripcion
        # ("...based in Athenry. Galway. This is a full-time position...") y
        # dentro del texto hay una region CONOCIDA. Se extrae la region
        # embebida (mas especifica primero); si no hay ninguna region conocida
        # y el texto es largo (>100 chars), se devuelve vacio (ruido).
        match = _LOCATION_REGION_TEXT_REGEX_OBJ.search(norm)
        if match:
            return _LOCATION_REGION_TEXT_TOKENS[match.group(1)]
        if len(value) > 100:
            return ""
        return value

    def _us_state_from_city(city_key: str) -> str:
        """Codigo de estado USA (2 letras) si la ciudad/metro es de USA."""
        return (_LOCATION_US_CITY_TO_STATE.get(city_key)
                or _LOCATION_US_CITY_TO_STATE.get(_city_clean(city_key))
                or "")

    city_n = (city or "").strip().lower().translate(_LOCATION_ACCENTS)
    city_canon = location_city_normalized(city)
    region_ = _region_to_english(region)
    country_ = _to_english(country, with_cities=True)
    if city_n:
        country_name = _LOCATION_COUNTRY_NAMES.get(city_n)
        if country_name:
            region_ = region_ or country_name
            country_ = country_ or country_name
        else:
            country_of_city = (_LOCATION_CITY_TO_COUNTRY.get(city_n)
                               or _LOCATION_CITY_TO_COUNTRY.get(_city_clean(city_n)))
            if country_of_city:
                city_key = city_canon.lower().translate(_LOCATION_ACCENTS)
                city_region = _LOCATION_REGION_CANONIC.get(city_key, city_canon)
                region_ = region_ or city_region
                country_ = country_ or country_of_city
    # USA: si el pais ya es "United States" y la region esta vacia, la region
    # (estado) se infiere desde la ciudad/metro (p.ej. "New Jersey" -> "NJ",
    # "Dallas-Fort Worth" -> "TX"). El codigo de 2 letras es el formato
    # canonico de location_region en USA (Dim_RegionMap lo expande a nombre).
    if (country_ == "United States" and not region_
            and _us_state_from_city(city_n)):
        region_ = _us_state_from_city(city_n)
    # Regla de coherencia continente->pais (identica a la pasada columnar): si
    # la region normalizada es Europe, el pais debe ser Europe (LinkedIn pone
    # "Oriente Medio y Africa" como pais pero la region es Europe).
    if region_ and region_.lower().translate(_LOCATION_ACCENTS) == "europe":
        country_ = "Europe"
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
    salary_raw_col: str = "salary_raw",
    salary_period_norm_col: str = "salary_period_norm",
    work_mode_col: str = "work_mode",
    work_mode_norm_col: str = "work_mode_norm",
    location_city_col: str = "location_city",
    location_region_col: str = "location_region",
    location_country_col: str = "location_country",
    only_if_missing: bool = True,
    add_salary_annual: bool = True,
    add_salary_period_norm: bool = True,
    add_salary_parse_raw: bool = True,
    add_salary_validation_reason: bool = False,
    add_role_category: bool = True,
    add_employment_type: bool = True,
    add_work_mode: bool = True,
    add_location_fill: bool = True,
    normalize_location_city: bool = True,
    add_posted_date_fill: bool = True,
    posted_date_col: str = "posted_date",
    scraped_col: str = "scraped_at",
    add_traceability: bool = True
):
    """Enriquece `df` con skills/experience/employment/role/salario anual/work_mode.

    only_if_missing=True (default): conserva lo ya rellenado y completa huecos.
    False: recalcula todo. Cada mejora se activa con su flag.

    Nuevo:
    - experience_level: titulo primero, descripcion (con frases de contexto
      excluidas) como fallback; "Unknown" si no clasifica.
    - add_salary_parse_raw: parsea salary_raw cuando min/max estan vacios y
      deriva currency/period; requiere la columna salary_raw en el df.
    - salary_quality: "missing" (sin salario), "invalid" (min<=0 | min>max),
      ademas de unknown_period / outlier_review / ok.
    - add_traceability: columnas *_source (origen de cada valor).
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

    not_blank = lambda c: F.trim(F.coalesce(c, F.lit(""))) != ""  # noqa: E731

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
    # experience_level: PRIMERO el titulo (precision alta); si no aporta,
    # la descripcion con las frases de contexto excluidas (recall sin
    # falsos positivos tipo "senior stakeholders"). No clasificable ->
    # "Unknown".
    exp_from_title = cascade(F.col(title_col), _EXP_RULES, default="Unknown")
    exp_desc_src = F.col(desc_col)
    for pat in _EXP_EXCLUDE_PHRASES:
        exp_desc_src = F.regexp_replace(exp_desc_src, pat, " ")
    exp_from_desc = cascade(exp_desc_src, _EXP_RULES, default="Unknown")
    exp_new = F.when(exp_from_title != "Unknown", exp_from_title) \
        .otherwise(exp_from_desc)
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
    if skills_col not in result.columns:
        fill(skills_col, skills_new, F.lit(True), False)
        skills_had_existing = F.lit(False)
    else:
        # Snapshot del valor ORIGINAL en una columna temporal: fill() reemplaza
        # skills_col y las expresiones que referencian skills por NOMBRE
        # resolverian contra la columna nueva (array), rompiendo el analisis.
        result = result.withColumn("__skills_orig", F.col(skills_col))
        orig = F.col("__skills_orig")
        if isinstance(result.schema["__skills_orig"].dataType, ArrayType):
            existing = F.coalesce(orig, F.array())
            skills_had_existing = F.size(existing) > 0
            fill(skills_col, skills_new, skills_had_existing, True,
                 keep_value=existing)
        else:
            existing = F.split(F.trim(F.coalesce(orig, F.lit(""))), r"\s*\|\s*")
            # "[]" (placeholder de skills vacias de algunos scrapers) NO se
            # considera un valor real: se deja que se rellene desde la descripcion.
            skills_had_existing = (
                (F.trim(F.coalesce(orig, F.lit(""))) != "")
                & (F.trim(F.coalesce(orig, F.lit(""))) != "[]")
            )
            fill(skills_col, skills_new, skills_had_existing, True,
                 keep_value=existing)
        if add_traceability:
            skills_src = F.when(skills_had_existing, F.lit("existing")) \
                .when(F.size(skills_new) > 0, F.lit("derived")) \
                .otherwise(F.lit(""))
            result = result.withColumn("skills_source", skills_src)
        result = result.drop("__skills_orig")

    # --- experience_level / employment_type / role_category (strings) ---
    if add_traceability:
        exp_src = F.when(not_blank(F.col(experience_level_col)),
                         F.lit("existing")) \
            .when(exp_from_title != "Unknown", F.lit("title")) \
            .when(exp_from_desc != "Unknown", F.lit("description")) \
            .otherwise(F.lit(""))
        result = result.withColumn("experience_level_source", exp_src)
    fill(experience_level_col, exp_new,
         not_blank(F.col(experience_level_col)),
         experience_level_col in result.columns)
    if add_employment_type:
        fill(employment_type_col, emp_new,
             not_blank(F.col(employment_type_col)),
             employment_type_col in result.columns)
    if add_role_category:
        # role_category usa "Other" como placeholder del cascade: cuando la
        # columna YA existe y solo tiene "Other", se recalcula SI el titulo
        # da una categoria real (si vuelve a dar "Other", se conserva la
        # original). Asi un "Other" heredado de un run previo no bloquea una
        # mejor clasificacion con only_if_missing=True.
        # OJO (bug corregido): si la columna NO existe (primera pasada) no se
        # puede referenciar F.col(role_category_col) dentro de la expresion ->
        # UNRESOLVED_COLUMN al analizar la query.
        if role_category_col in result.columns:
            keep_role = (
                (F.trim(F.coalesce(F.col(role_category_col), F.lit(""))) != "")
                & (F.trim(F.coalesce(F.col(role_category_col), F.lit("")))
                   != "Other")
            )
            role_new_final = F.when(role_new != "Other", role_new) \
                .otherwise(F.coalesce(F.col(role_category_col), F.lit("")))
            fill(role_category_col, role_new_final, keep_role, True)
        else:
            fill(role_category_col, role_new, F.lit(True), False)

    # --- periodo efectivo de salario: columna explicita o derivada de raw ---
    has_raw_col = add_salary_parse_raw and salary_raw_col in result.columns
    if salary_period_col in result.columns:
        period_col_v = F.lower(F.trim(
            F.coalesce(F.col(salary_period_col), F.lit(""))))
        period_raw_v = (cascade(F.col(salary_raw_col), _PERIOD_NORM_RULES,
                                default="")
                        if has_raw_col else F.lit(""))
        # OJO: lowercase final (el cascade devuelve YEAR/MONTH/... en
        # mayusculas y los rlike del factor salarial son case-sensitive).
        period = F.lower(F.when(not_blank(period_col_v), period_col_v) \
            .otherwise(period_raw_v))
    else:
        period = F.lit("")

    # --- parseo de salary_raw (solo rellena huecos en min/max) ---
    if has_raw_col:
        raw = F.lower(F.trim(F.coalesce(F.col(salary_raw_col), F.lit(""))))
        clean = F.regexp_replace(
            raw,
            r"[€£$]|\b(?:eur|euros?|gbp|usd|chf|sfr|francs?)\b|\bper\s+|"
            r"\bp\.?\s*[mju]\.?\b",
            " ")
        # Separador de miles europeo ("3.500" -> "3500"); el decimal "." de
        # un numero no agrupado (p.ej. "3.5") se conserva.
        clean = F.regexp_replace(clean, r"(?<=\d)\.(?=\d{3}(?:\D|$))", "")
        # Coma decimal ("3500,00" -> "3500.00"); la coma de miles ("41,260")
        # se deja intacta (el replace de "," posterior la elimina).
        clean = F.regexp_replace(clean, r",(?=\d{1,2}(?:\D|$))", ".")
        clean = F.regexp_replace(clean, r"\s+", " ")
        RNG_K = r"(\d+(?:\.\d+)?)\s*k\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*k"
        ONE_K = r"(\d+(?:\.\d+)?)\s*k\b"
        RNG = r"(\d+(?:[.,]\d+)*)\s*(?:-|–|—|to)\s*(\d+(?:[.,]\d+)*)"
        ONE = r"(\d+(?:[.,]\d+)*)"
        k_min = F.regexp_extract(clean, RNG_K, 1).cast("double") * 1000
        k_max = F.regexp_extract(clean, RNG_K, 2).cast("double") * 1000
        one_k = F.regexp_extract(clean, ONE_K, 1).cast("double") * 1000
        r_min = F.regexp_replace(F.regexp_extract(clean, RNG, 1), ",", "") \
            .cast("double")
        r_max = F.regexp_replace(F.regexp_extract(clean, RNG, 2), ",", "") \
            .cast("double")
        one_min = F.regexp_replace(F.regexp_extract(clean, ONE, 1), ",", "") \
            .cast("double")
        min_parsed = F.when(clean.rlike(RNG_K), k_min) \
            .when(clean.rlike(ONE_K), one_k) \
            .when(clean.rlike(RNG), r_min) \
            .when(clean.rlike(ONE), one_min) \
            .otherwise(F.lit(None).cast("double"))
        max_parsed = F.when(clean.rlike(RNG_K), k_max) \
            .when(clean.rlike(RNG), r_max) \
            .otherwise(F.lit(None).cast("double"))
        if salary_min_col in result.columns:
            # salary_source se materializa ANTES de los fills (las expresiones
            # por nombre resolverian contra la columna ya rellenada).
            if add_traceability:
                salary_src = F.when(
                    F.col(salary_min_col).isNotNull()
                    | F.col(salary_max_col).isNotNull(),
                    F.lit("existing")) \
                    .when(min_parsed.isNotNull() | max_parsed.isNotNull(),
                          F.lit("parsed_raw")) \
                    .otherwise(F.lit(""))
                result = result.withColumn("salary_source", salary_src)
            fill(salary_min_col, min_parsed,
                 F.col(salary_min_col).isNotNull(), True)
            fill(salary_max_col, max_parsed,
                 F.col(salary_max_col).isNotNull(), True)
        # Moneda derivada del texto (solo si el campo explicito esta vacio).
        cur = F.when(raw.rlike(r"(?i)€|eur\b"), F.lit("EUR")) \
            .when(raw.rlike(r"(?i)£|gbp\b"), F.lit("GBP")) \
            .when(raw.rlike(r"(?i)\$|usd\b"), F.lit("USD")) \
            .when(raw.rlike(r"(?i)chf|sfr|francs?\b"), F.lit("CHF")) \
            .otherwise(F.lit(""))
        if "salary_currency" in result.columns:
            fill("salary_currency", cur,
                 not_blank(F.col("salary_currency")), True)

    # --- salario anual para comparar toda la tabla en BI ---
    if add_salary_annual:
        # Factor por token (subcadena, tolerante a "weekly"/"per week"/"semana"):
        # hour > day > week > month > year (first-match wins, igual que
        # cascade(): se itera en ORDEN INVERSO). Si el periodo NO se reconoce
        # (vacio/desconocido) el factor es NULL -> salario anual NULL (no
        # falsear: NO se asume que es anual).
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

        # Semantica ampliada: "missing" (sin salario), "invalid" (min<=0 o
        # min>max), "unknown_period", "outlier_review", "ok".
        quality = F.when(is_outlier, F.lit("outlier_review")) \
            .when(non_pos, F.lit("invalid")) \
            .when(inv_range, F.lit("invalid")) \
            .when(unknown_period, F.lit("unknown_period")) \
            .when(has_salary, F.lit("ok")) \
            .otherwise(F.lit("missing"))
        salary_cols = [
            ("salary_min_annual", min_ann),
            ("salary_max_annual", max_ann),
            ("salary_quality", quality),
        ]
        # salary_validation_reason (texto libre) se solapa con salary_quality
        # (categoria) y no aporta al informe -> opt-in, por defecto NO se genera.
        if add_salary_validation_reason:
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
            salary_cols.append(("salary_validation_reason", val_reason))
        for col_name, expr in salary_cols:
            not_null = lambda c: c.isNotNull()
            fill(col_name, expr, not_null(F.col(col_name)), col_name in result.columns)

    # --- salary_period normalizado a YEAR/MONTH/WEEK para BI ---
    if add_salary_period_norm:
        period_norm = cascade(period, _PERIOD_NORM_RULES, default="")
        # Sub-mensual (hora/diario) -> "" (no falsear: el factor anual ya se
        # aplico en salary_min_annual/max_annual).
        period_norm = F.when(
            period_norm.isin("DAY", "HOUR"), F.lit("")).otherwise(period_norm)
        fill(salary_period_norm_col, period_norm,
             not_blank(F.col(salary_period_norm_col)),
             salary_period_norm_col in result.columns)

    if add_work_mode:
        # 1) Normalizar el valor explicito de work_mode (si existe).
        work_explicit = cascade(F.col(work_mode_col), _WRK_RULES, default="")
        # 2) Si el explicito no aporta (vacio o variante no reconocida tipo
        #    "Flexible"), derivar de titulo + descripcion para no dejar el
        #    hueco en blanco. Orden de fiabilidad: explicito > texto libre.
        #    Si el texto no menciona nada -> "" (no se inventa).
        blob = F.concat_ws(
            " ",
            F.coalesce(F.col(title_col), F.lit("")),
            F.coalesce(F.col(desc_col), F.lit("")),
        )
        work_from_text = cascade(blob, _WRK_RULES, default="")
        work_derived = F.when(not_blank(work_explicit), work_explicit) \
            .otherwise(work_from_text)
        # El keep se basa en el valor YA normalizado de la columna: si existe
        # un valor valido se conserva; si no, se rellena con work_derived.
        fill(work_mode_norm_col, work_derived,
             not_blank(F.col(work_mode_norm_col)),
             work_mode_norm_col in result.columns)
        if add_traceability:
            work_src = F.when(not_blank(work_explicit), F.lit("explicit")) \
                .when(not_blank(work_from_text), F.lit("derived")) \
                .otherwise(F.lit(""))
            result = result.withColumn("work_mode_source", work_src)
    # --- location_city / location_region / location_country ------------------
    #  Normalizacion de location_city al nombre canonico en ingles (alineado
    #  con Dim_Geo): "Madrid y alrededores" -> Madrid, "Greater Dublin Area" ->
    #  Dublin, "Dublin 1" -> Dublin, "Dublín" -> Dublin, ... Si la ciudad no se
    #  reconoce, se conserva tal cual (no se inventa).
    #  Normalizacion de location_region al nombre ingles de la COMUNIDAD
    #  AUTONOMA espanola (mapa de Espana por region): "Vizcaya"/"País Vasco" ->
    #  Basque Country, "Cataluña"/"Barcelona" -> Catalonia, "Comunidad de
    #  Madrid" -> Madrid, ... Regiones de otros paises se conservan tal cual.
    #  Relleno de huecos (via fill) + NORMALIZACION A INGLES de los valores ya
    #  existentes: region/country pueden venir en español ("España", "Irlanda",
    #  "Holanda", "Suiza", "Europa", ...) y se traducen al nombre canonico en
    #  ingles. Si el valor no se reconoce, se conserva tal cual (no se inventa).
    #  (Solo rellena huecos si only_if_missing=True: via keep_value se devuelve
    #  el valor existente TRADUCIDO, no el crudo.)
    if add_location_fill and location_city_col in result.columns:
        city_v = F.trim(F.coalesce(F.col(location_city_col), F.lit("")))
        city_norm = F.translate(F.lower(city_v), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)
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
        canonic_city = F.create_map([
            F.lit(item)
            for kv in _LOCATION_CITY_CANONIC.items()
            for item in (kv[0], kv[1])
        ])
        region_canon_map = F.create_map([
            F.lit(item)
            for kv in _LOCATION_REGION_CANONIC.items()
            for item in (kv[0], kv[1])
        ])
        us_state_map = F.create_map([
            F.lit(item)
            for kv in _LOCATION_US_STATE_ABBREV.items()
            for item in (kv[0], kv[1])
        ])
        us_city_state_map = F.create_map([
            F.lit(item)
            for kv in _LOCATION_US_CITY_TO_STATE.items()
            for item in (kv[0], kv[1])
        ])
        region_text_tokens = F.create_map([
            F.lit(item)
            for kv in _LOCATION_REGION_TEXT_TOKENS.items()
            for item in (kv[0], kv[1])
        ])
        country_by_name = country_names[city_norm]

        # Limpieza acumulativa por regex (mismas reglas que la funcion pura
        # _city_clean): distritos numerados ("dublin 1"), "greater X area",
        # "X y alrededores", "X area", ... -> se reintenta el lookup.
        city_clean = city_norm
        for pattern, repl in _LOCATION_CITY_CLEAN_RULES:
            city_clean = F.regexp_replace(city_clean, pattern, repl)
        # Ciudad canonica en ingles (Dim_Geo); si no se reconoce -> la original.
        city_canon = F.coalesce(
            canonic_city[city_norm], canonic_city[city_clean], city_v)
        country_by_city = F.coalesce(
            city_to_country[city_clean], city_to_country[city_norm])

        # Valores existentes -> ingles canonico. region reconoce nombres de
        # COMUNIDAD AUTONOMA espanola (provincia/ciudad -> comunidad) y luego
        # nombres de pais; country ademas reconoce lugares del mapa de
        # ciudades (p.ej. "Dublin" -> Ireland).
        has_region = location_region_col in result.columns
        has_country = location_country_col in result.columns
        region_v = (F.trim(F.coalesce(F.col(location_region_col), F.lit("")))
                    if has_region else F.lit(""))
        region_key = F.translate(F.lower(region_v), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)
        region_clean = region_key
        for pattern, repl in _LOCATION_CITY_CLEAN_RULES:
            region_clean = F.regexp_replace(region_clean, pattern, repl)
        region_mapped = F.coalesce(
            region_canon_map[region_key], region_canon_map[region_clean],
            country_names[region_key])
        # location_region a veces llega contaminado con texto de la descripcion
        # y dentro hay una region CONOCIDA ("...based in Athenry. Galway. This
        # is a full-time..."). Se extrae la region embebida (mas especifica
        # primero). Si no hay ninguna region conocida y el texto es largo
        # (>100 chars), se anula (ruido).
        region_from_text = F.lit("")
        # UN solo regexp_extract con el regex combinado (alternancia ordenada
        # por longitud, lookbehind a la izquierda) devuelve la variante
        # matcheada (grupo 1); el mapa token->region la resuelve a su region
        # canonica. Asi evitamos encadenar ~250 rlike (lentisimo en Spark).
        region_match = F.regexp_extract(region_key, _LOCATION_REGION_TEXT_REGEX, 1)
        region_from_text = F.coalesce(
            region_text_tokens[region_match], F.lit(""))
        region_canon = F.when(region_mapped.isNotNull(), region_mapped) \
            .when(region_from_text != "", region_from_text) \
            .when(F.length(region_v) > 100, F.lit("")) \
            .otherwise(region_v)
        country_v = (F.trim(F.coalesce(F.col(location_country_col), F.lit("")))
                     if has_country else F.lit(""))
        country_en = F.coalesce(
            country_names[F.translate(F.lower(country_v), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)],
            us_state_map[F.translate(F.lower(country_v), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)],
            city_to_country[F.translate(F.lower(country_v), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)],
            country_v)

        # region: pais si city era un pais; si no, la ciudad canonica (o su
        # COMUNIDAD AUTONOMA si es espanola). Si ya habia valor, se conserva
        # traducido a ingles. Para USA, si la ciudad/metro esta en el mapa de
        # estados (p.ej. "New Jersey" -> NJ, "Dallas-Fort Worth" -> TX), la
        # region se infiere desde la ciudad.
        city_key = F.translate(F.lower(city_canon), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)
        us_state_from_city = F.coalesce(
            us_city_state_map[city_key], us_city_state_map[city_clean])
        country_is_us = (country_en == "United States") | \
            (country_by_name == "United States")
        city_region = F.coalesce(region_canon_map[city_key], city_canon)
        new_region = F.when(region_v != "", region_canon) \
            .when(country_by_name.isNotNull(), country_by_name) \
            .when(country_is_us & us_state_from_city.isNotNull(),
                  us_state_from_city) \
            .when(country_by_city.isNotNull(), city_region) \
            .otherwise(F.lit(""))
        # Regla de coherencia continente->pais: si la region (normalizada) es
        # Europe (o equivalente), el pais debe ser Europe (LinkedIn pone
        # "Oriente Medio y Africa" como pais pero la region es Europe).
        region_is_europe = F.translate(
            F.lower(region_canon), _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST) == "europe"
        # country: valor existente traducido a ingles; si vacio, pais inferido
        # (por nombre de pais, por estado USA desde la ciudad, o por ciudad).
        # Si no se reconoce -> vacio (no se inventa el pais).
        us_country_from_city = F.when(
            us_state_from_city.isNotNull(), F.lit("United States"))
        new_country = F.when(region_is_europe, F.lit("Europe")) \
            .when(country_v != "", country_en) \
            .otherwise(F.coalesce(
                country_by_name, us_country_from_city, country_by_city,
                F.lit("")))
        # El valor YA existente se conserva traducido (keep_value), pero la
        # regla continente->pais debe imponerse tambien sobre el existente:
        # un pais "Oriente Medio y Africa" con region Europe no se conserva.
        country_keep = F.when(region_is_europe, F.lit("Europe")).otherwise(country_en)
        fill(location_region_col, new_region,
             not_blank(region_v),
             has_region,
             keep_value=region_canon)
        fill(location_country_col, new_country,
             not_blank(country_v),
             has_country,
             keep_value=country_keep)

        # Normalizacion del propio location_city (NO es relleno de huecos: se
        # aplica SIEMPRE, tambien sobre valores ya existentes, para que el
        # JOIN con Dim_Geo funcione).
        if normalize_location_city:
            result = result.withColumn(location_city_col, city_canon)

    # --- posted_date: si falta, usar la fecha de scraping --------------------
    # Muchos portales (InfoJobs, Indeed, ...) no publican la fecha de la oferta.
    # Esas filas suelen traer salario y penalizaban en la deduplicacion y en la
    # validacion de fechas. Regla: posted_date = coalesce(posted_date,
    # to_date(scraped_at), _ingest_date). El valor original se conserva en
    # posted_date_raw y el origen en posted_date_source ("posted"/"scraped").
    # Solo se rellena lo que falta (nunca se sobreescribe una fecha real).
    if add_posted_date_fill:
        # Snapshot INMUTABLE de la fecha original: posted_date_raw. Se toma
        # ANTES de rellenar posted_date y NUNCA se sobreescribe, de modo que la
        # clasificacion del origen no se contamine con el valor ya rellenado.
        if "posted_date_raw" not in result.columns:
            if posted_date_col in result.columns:
                result = result.withColumn(
                    "posted_date_raw", F.to_date(F.col(posted_date_col)))
            else:
                result = result.withColumn(
                    "posted_date_raw", F.lit(None).cast("date"))
        # Idempotente: si scraped_date ya existe (reejecucion sobre una Plata
        # que ya no trae scraped_at/_ingest_date) NO se sobreescribe con NULL.
        if "scraped_date" not in result.columns:
            scraped_date = (
                F.to_date(F.col(scraped_col)) if scraped_col in result.columns
                else F.lit(None).cast("date"))
            if "_ingest_date" in result.columns:
                scraped_date = F.coalesce(
                    scraped_date, F.to_date(F.col("_ingest_date")))
            result = result.withColumn("scraped_date", scraped_date)

        # posted_date = fecha original si existe; si no, la de scraping.
        # OJO: posted_raw referencia posted_date_raw (snapshot), NO
        # posted_date (que se sobreescribe en el withColumn siguiente).
        posted_raw = F.to_date(F.col("posted_date_raw"))
        result = result.withColumn(
            posted_date_col,
            F.coalesce(posted_raw, F.col("scraped_date")))
        if add_traceability:
            posted_src = F.when(posted_raw.isNotNull(), F.lit("posted")) \
                .when(F.col("scraped_date").isNotNull(), F.lit("scraped")) \
                .otherwise(F.lit(""))
            result = result.withColumn("posted_date_source", posted_src)

    return result


def apply_company_canonical(df, col_name: str = "company_name"):
    """Agrupa las variantes de las grandes companias en un unico nombre
    canonico (Google / Google Ireland Ltd / Google DeepMind -> "Google").

    Identico resultado al de la funcion pura company_name_canonical().

    RENDIMIENTO (importante): la expresion con los ~110 patrones se evalua
    SOLO sobre los valores DISTINTOS de company_name (centenares/miles) y
    luego se hace un join, en vez de aplicar los 110 regex por fila a toda la
    tabla. Asi el coste no depende del numero de filas.
    """
    from pyspark.sql import functions as F

    raw = F.col(col_name)
    base = F.trim(F.coalesce(raw, F.lit("")))
    expr = base
    for canon, pattern in COMPANY_ALIAS_PATTERNS:
        expr = F.when(base.rlike(pattern), F.lit(canon)).otherwise(expr)
    # Los NULL se conservan tal cual (el join por clave null no matchea y el
    # coalesce devuelve el valor original).
    expr = F.when(raw.isNull(), raw).otherwise(expr)

    mapped = (df.select(col_name)
                .distinct()
                .withColumn("__company_canon", expr))
    return (df.join(mapped, col_name, "left")
              .withColumn(col_name,
                          F.coalesce(F.col("__company_canon"), F.col(col_name)))
              .drop("__company_canon"))