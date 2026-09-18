"""Tests del parser de SERP compartido IrishJobs/StepStone.

Cubren los puntos que rompian la calidad de datos en StepStone NL:
- salario en formato neerlandes/europeo y rechazo de widgets genericos;
- deteccion multilingue del modo de trabajo.
"""
from src.irishjobs import serp


def test_parse_salary_not_disclosed_is_empty():
    raw, smin, smax, cur, period, disclosed = serp._parse_salary("€ Not Disclosed")
    assert raw == "" and smin is None and smax is None and disclosed is False


def test_parse_salary_dutch_per_month():
    raw, smin, smax, cur, period, disclosed = serp._parse_salary(
        "Salaris € 3.500 - € 4.500 per maand")
    assert smin == 3500.0
    assert smax == 4500.0
    assert period == "month"
    assert disclosed is True


def test_parse_salary_dutch_abbreviation():
    _, smin, _, _, period, _ = serp._parse_salary("€3.500 p/m")
    assert smin == 3500.0
    assert period == "month"
    _, _, _, _, period_j, _ = serp._parse_salary("€60.000 p/j")
    assert period_j == "year"


def test_parse_salary_english_range():
    _, smin, smax, cur, period, _ = serp._parse_salary("€41,260 per annum")
    assert smin == 41260.0
    assert cur == "EUR"
    assert period == "year"


def test_parse_salary_widget_is_rejected():
    # El texto de widgets genericos (?cuanto podrias ganar?) no es de la oferta.
    _, smin, smax, _, _, disclosed = serp._parse_salary(
        "Salarisinformatie ontgrendelen. Wat je zou kunnen verdienen: €41,260")
    assert smin is None and smax is None and disclosed is False


def test_work_mode_dutch_terms():
    assert serp._RE_WORK_REMOTE.search("Telewerken mogelijk") is not None
    assert serp._RE_WORK_REMOTE.search("Thuiswerken") is not None
    assert serp._RE_WORK_HYBRID.search("Hybride werken") is not None


def test_job_id_from_stepstone_url():
    href = "/banen--Data-Analyst-Amsterdam-ACME--544148-inline.html"
    m = serp._RE_JOB_ID_FROM_URL.search(href)
    assert m is not None
    assert (m.group(1) or m.group(2)) == "544148"
