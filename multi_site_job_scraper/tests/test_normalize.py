from src.core.normalize import extract_skills, parse_salary


def test_parse_salary_chf_range():
    r = parse_salary("CHF 90'000 - 110'000")
    assert r["salary_min"] == 90000
    assert r["salary_max"] == 110000
    assert r["salary_currency"] == "CHF"


def test_parse_salary_usd_period():
    r = parse_salary("$80K - $134K per year")
    assert r["salary_min"] == 80000
    assert r["salary_max"] == 134000
    assert r["salary_currency"] == "USD"
    assert r["salary_period"] == "YEARLY"


def test_parse_salary_eur_single():
    r = parse_salary("60k EUR")
    assert r["salary_min"] == 60000
    assert r["salary_max"] is None
    assert r["salary_currency"] == "EUR"


def test_parse_salary_empty():
    r = parse_salary("")
    assert r["salary_min"] is None
    assert r["salary_max"] is None


def test_parse_salary_default_currency():
    r = parse_salary("80'000", default_currency="CHF")
    assert r["salary_min"] == 80000
    assert r["salary_currency"] == "CHF"


def test_parse_salary_k_m_suffixes():
    # 1.5M debe ser 1.500.000 (no 15.000.000: el sufijo M con decimal se
    # aplicaba como concatenacion de ceros).
    r = parse_salary("€1.5M per year")
    assert r["salary_min"] == 1500000
    assert r["salary_currency"] == "EUR"
    assert r["salary_period"] == "YEARLY"

    r2 = parse_salary("$80K - $100K per year")
    assert r2["salary_min"] == 80000
    assert r2["salary_max"] == 100000


def test_parse_salary_european_thousands():
    r = parse_salary("EUR 45.000 - 55.000 per year")
    assert r["salary_min"] == 45000
    assert r["salary_max"] == 55000
    assert r["salary_period"] == "YEARLY"


def test_parse_salary_dutch_periods():
    r = parse_salary("€ 3.500 - € 4.500 per maand")
    assert r["salary_min"] == 3500
    assert r["salary_max"] == 4500
    assert r["salary_period"] == "MONTHLY"

    r2 = parse_salary("€3.500 p/m")
    assert r2["salary_min"] == 3500
    assert r2["salary_period"] == "MONTHLY"

    r3 = parse_salary("60.000 EUR per jaar")
    assert r3["salary_min"] == 60000
    assert r3["salary_period"] == "YEARLY"


def test_extract_skills():
    text = ("We need Python, SQL and Power BI. Experience with Airflow "
            "and Databricks is a plus. Also pandas and PySpark.")
    skills = extract_skills(text)
    assert "Python" in skills
    assert "SQL" in skills
    assert "Power BI" in skills
    assert "Airflow" in skills
    assert "Databricks" in skills
    assert len(skills) == len(set(skills))


def test_extract_skills_empty():
    assert extract_skills("") == []
    assert extract_skills("sin habilidades relevantes") == []