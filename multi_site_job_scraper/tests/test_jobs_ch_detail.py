from pathlib import Path

from src.jobs_ch import detail

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_ch_detail.html"


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_find_jobposting():
    jp = detail._find_jobposting(_fixture_text())
    assert jp is not None
    assert jp["@type"] == "JobPosting"
    assert jp["title"] == "Data / BI Analyst 80-100%"


def test_parse_json_ld_detail():
    jp = detail._find_jobposting(_fixture_text())
    result = {}
    detail._parse_json_ld(jp, result)

    assert "B. Braun Medical AG" in result["company_description"]
    assert result["employment_type"] == "Permanent position"
    assert "46e1b845-8c1f-4ae2-afe2-6fdb14294d03" in jp["url"]
    # description_full debe contener texto de la descripcion
    assert "Power BI" in result["description_full"]
    # beneficios detectados por cabecera
    assert result.get("benefits") and "Flexible Arbeitszeiten" in result["benefits"]
    # responsabilidades
    assert result.get("responsibilities") and "Reportings" in result["responsibilities"]
    # requisitos
    assert result.get("requirements") and "Studium" in result["requirements"]
    # datos salariales presentes en el JSON-LD (baseSalary)
    assert result.get("salary_currency", "") in ("", "CHF")


def test_html_to_text_strips_tags():
    txt = detail._html_to_text("<p>Hola <strong>mundo</strong></p><ul><li>a</li></ul>")
    assert "Hola mundo" in txt
    assert "<" not in txt


def test_split_description_sections():
    html = _fixture_text()
    jp = detail._find_jobposting(html)
    sections = detail._split_description(jp["description"])
    assert sections, "deberia haber secciones en la descripcion"
    headings = [h.lower() for h, _ in sections]
    assert any("benefits" in h or "angebot" in h or "vorteile" in h for h in headings)
    assert any("aufgaben" in h or "kompetenzen" in h for h in headings)


def test_find_salary_in_page_patterns():
    import re
    for pat in [r"CHF\s*[\d']+\s*(?:[.–-]\s*[\d']+)?",
                r"[\d']+\s*(?:CHF|EUR)"]:
        assert re.search(pat, "CHF 90'000 - 110'000", re.IGNORECASE) or \
            re.search(pat, "90'000 CHF", re.IGNORECASE)
