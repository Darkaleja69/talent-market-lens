"""Normalizacion de campos (salario, habilidades) para todas las fuentes."""
from __future__ import annotations

import re
from typing import Optional

_RE_SALARY_RANGE = re.compile(
    r"(?P<cur>CHF|EUR|USD|GBP|€|£|\$)?\s*"
    r"(?P<min>\d[\d'.,]*[KM]?)\s*(?:[–\-]\s*"
    r"(?:CHF|EUR|USD|GBP|€|£|\$)?\s*"
    r"(?P<max>\d[\d'.,]*[KM]?)\s*)?"
    r"(?:CHF|EUR|USD|GBP|€|£|\$)?\s*"
    r"(?P<per>per\s+(?:year|month|hour|annum|año|ano|mes|hora|day|week|"
    r"jaar|maand|uur|dag|jahr|monat|woche|día|dia|semana)"
    r"|p\s*[./]?\s*[mju]\.?)?",
    re.IGNORECASE,
)

_CURRENCY_MAP = {
    "chf": "CHF", "eur": "EUR", "usd": "USD", "gbp": "GBP",
    "€": "EUR", "$": "USD", "£": "GBP",
}

_PERIOD_MAP = {
    "year": "YEARLY", "annum": "YEARLY", "año": "YEARLY", "ano": "YEARLY",
    "jaar": "YEARLY", "jahr": "YEARLY", "pj": "YEARLY",
    "month": "MONTHLY", "mes": "MONTHLY", "maand": "MONTHLY",
    "monat": "MONTHLY", "pm": "MONTHLY",
    "hour": "HOURLY", "hora": "HOURLY", "uur": "HOURLY", "pu": "HOURLY",
    "day": "DAILY", "dag": "DAILY", "día": "DAILY", "dia": "DAILY",
    "week": "WEEKLY", "semana": "WEEKLY", "woche": "WEEKLY",
}


def as_text(value: object, sep: str = "|") -> str:
    """Convierte un valor de JSON-LD a texto plano.

    Los campos escalares del dataclass JobOffer son `str`, pero algunos
    JSON-LD (p.ej. `employmentType`) pueden venir como lista (schema.org
    permite arrays). Sin esto, un list llega al Parquet tipado y revienta
    el cast a `string` ("Expected bytes, got a 'list' object").
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return sep.join(as_text(v, sep) for v in value if as_text(v, sep))
    if isinstance(value, dict):
        return sep.join(as_text(v, sep) for v in value.values() if as_text(v, sep))
    return str(value).strip()


def _to_number(s: str) -> Optional[float]:
    """Convierte importes con separadores ES/EN/NL a float.

    - "41,260" / "3,500.00" (EN) -> 41260 / 3500.0
    - "3.500" / "3.500,00" (ES/NL) -> 3500.0
    - "90'000" (CH) -> 90000.0
    """
    if not s:
        return None
    v = s.strip().replace("'", "").upper()
    mult = 1.0
    if v.endswith("M"):
        mult, v = 1_000_000.0, v[:-1]
    elif v.endswith("K"):
        mult, v = 1_000.0, v[:-1]
    v = v.strip().rstrip(".,")
    if "," in v and "." in v:
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    elif "," in v:
        parts = v.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            v = v.replace(",", "")
        else:
            v = v.replace(",", ".")
    elif "." in v:
        parts = v.split(".")
        if len(parts[1:]) >= 1 and all(len(p) == 3 for p in parts[1:]) \
                and len(parts[0]) <= 3:
            v = v.replace(".", "")
    try:
        return float(v) * mult
    except (ValueError, TypeError):
        return None

_SKILLS = [
    "python", "r", "sql", "excel", "tableau", "power bi", "powerbi",
    "sas", "spss", "scala", "java", "spark", "pyspark", "databricks",
    "snowflake", "dbt", "airflow", "kafka", "hadoop", "bigquery",
    "redshift", "aws", "azure", "gcp", "kubernetes", "docker", "git",
    "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch", "keras",
    "mlflow", "dbt", "looker", "qlik", "matlab", "stata", "etl",
    "data warehousing", "machine learning", "deep learning", "nlp",
    "statistics", "regression", "a/b testing", "power query", "dax",
    "cognos", "microstrategy", "sap bw", "hana", "oracle", "teradata",
    "cloud", "linux", "bash", "julia", "nosql", "mongodb", "postgres",
    "mysql", "t-sql", "pl/sql", "ci/cd", "jira", "confluence",
    "excel vba", "power automate", "power apps", "dataiku",
    "dataproc", "vertex ai", "sagemaker", "terraform", "fivetran",
    "matillion", "informatica", "talend", "ssis", "sap hana", "sap bw",
]

_SKILL_ALIAS = {
    "python": "Python", "sql": "SQL", "r": "R", "excel": "Excel",
    "power bi": "Power BI", "powerbi": "Power BI", "tableau": "Tableau",
    "sas": "SAS", "spss": "SPSS", "scala": "Scala", "java": "Java",
    "spark": "Spark", "pyspark": "PySpark", "databricks": "Databricks",
    "snowflake": "Snowflake", "dbt": "dbt", "airflow": "Airflow",
    "kafka": "Kafka", "hadoop": "Hadoop", "bigquery": "BigQuery",
    "redshift": "Redshift", "aws": "AWS", "azure": "Azure", "gcp": "GCP",
    "kubernetes": "Kubernetes", "docker": "Docker", "git": "Git",
    "pandas": "Pandas", "numpy": "NumPy", "scikit-learn": "scikit-learn",
    "tensorflow": "TensorFlow", "pytorch": "PyTorch", "keras": "Keras",
    "mlflow": "MLflow", "looker": "Looker", "qlik": "Qlik",
    "matlab": "MATLAB", "stata": "Stata", "etl": "ETL",
    "data warehousing": "Data Warehousing", "machine learning": "Machine Learning",
    "deep learning": "Deep Learning", "nlp": "NLP", "statistics": "Statistics",
    "regression": "Regression", "a/b testing": "A/B testing",
    "power query": "Power Query", "dax": "DAX", "cognos": "Cognos",
    "microstrategy": "MicroStrategy", "sap bw": "SAP BW", "hana": "SAP HANA",
    "sap hana": "SAP HANA", "oracle": "Oracle", "teradata": "Teradata",
    "cloud": "Cloud", "linux": "Linux", "bash": "Bash", "julia": "Julia",
    "nosql": "NoSQL", "mongodb": "MongoDB", "postgres": "PostgreSQL",
    "mysql": "MySQL", "t-sql": "T-SQL", "pl/sql": "PL/SQL",
    "ci/cd": "CI/CD", "jira": "Jira", "confluence": "Confluence",
    "excel vba": "Excel VBA", "power automate": "Power Automate",
    "power apps": "Power Apps", "dataiku": "Dataiku",
    "dataproc": "Dataproc", "vertex ai": "Vertex AI", "sagemaker": "SageMaker",
    "terraform": "Terraform", "fivetran": "Fivetran",
    "informatica": "Informatica", "talend": "Talend", "ssis": "SSIS",
}


def parse_salary(text: str,
                 default_currency: str = "") -> dict[str, Optional[float | str]]:
    """Parsea un rango salarial a (min, max, currency, period, raw).

    Ejemplos: 'CHF 90\'000 - 110\'000', '$80K - $134K per year', '60-80k EUR'.
    """
    result = {
        "salary_min": None, "salary_max": None,
        "salary_currency": "", "salary_period": "",
    }
    if not text:
        return result

    text = str(text).strip().replace("\u202f", " ").replace("\xa0", " ")
    raw_cur = None
    m_cur = re.search(r"(CHF|EUR|USD|GBP|€|£|\$)", text, re.IGNORECASE)
    if m_cur:
        raw_cur = m_cur.group(1)

    m = _RE_SALARY_RANGE.search(text)
    if not m:
        return result

    def _num(s: str) -> Optional[float]:
        return _to_number(s)

    mn = _num(m.group("min"))
    mx = _num(m.group("max")) if m.group("max") else None
    if mn is not None:
        # '80K' -> 80000; multiplicador ya aplicado
        result["salary_min"] = mn
    if mx is not None:
        result["salary_max"] = mx

    cur_key = (raw_cur or "").lower()
    if cur_key in _CURRENCY_MAP:
        result["salary_currency"] = _CURRENCY_MAP[cur_key]
    elif default_currency:
        result["salary_currency"] = default_currency

    per = (m.group("per") or "").lower()
    # Normaliza "p/m", "p. m.", "per year" -> token sin separadores.
    per_norm = re.sub(r"[^a-záéíóúñ]", "", per)
    tokens = set(per.split())
    for key, val in _PERIOD_MAP.items():
        if key in tokens or key in per_norm:
            result["salary_period"] = val
            break
    return result


def extract_skills(text: str) -> list[str]:
    """Extrae habilidades conocidas de un texto (deduplicado, orden estable)."""
    if not text:
        return []
    low = text.lower()
    found = []
    for skill in _SKILLS:
        if re.search(r"\b" + re.escape(skill) + r"\b", low):
            label = _SKILL_ALIAS.get(skill, skill)
            if label not in found:
                found.append(label)
    return found[:30]