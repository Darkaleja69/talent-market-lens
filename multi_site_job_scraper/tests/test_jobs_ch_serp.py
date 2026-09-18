from pathlib import Path

from src.jobs_ch import serp

FIXTURE = Path(__file__).parent / "fixtures" / "jobs_ch_serp.html"


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_json_ld_serp_parse():
    html = _fixture_text()
    cards = serp.parse_cards_from_ld(html)
    assert len(cards) >= 20, f"esperadas >=20 ofertas, obtenidas {len(cards)}"

    uuids = [c["job_id"] for c in cards]
    assert len(set(uuids)) == len(uuids), "job_id (UUID) duplicados"

    for c in cards[:10]:
        assert c["job_url"].startswith("https://www.jobs.ch/en/vacancies/detail/")
        assert c["title"]
        assert c["company_name"]
        assert c["posted_datetime"]
        assert c["posted_relative"]
        assert c["location_country"] == "Switzerland"


def test_json_ld_fields_mapping():
    html = _fixture_text()
    cards = serp.parse_cards_from_ld(html)
    by_id = {c["job_id"]: c for c in cards}
    # Data / BI Analyst 80-100% (B. Braun)
    card = by_id.get("46e1b845-8c1f-4ae2-afe2-6fdb14294d03")
    assert card, "oferta de referencia no encontrada"
    assert card["company_name"] == "B. Braun Medical AG"
    assert card["employment_type"] == "Permanent position"
    assert "Sempach" in card["location_raw"]
    assert card["workload_pct"] == "80-100%"
    assert card["company_url"].startswith("https://www.jobs.ch/en/companies/")


def test_card_text_colon_lines():
    # El HTML real separa label / ":" / valor en lineas independientes
    text = (
        "2 weeks ago\n"
        "Experte/Expertin Statistik/Data Science\n"
        "Place of work\n"
        ":\n"
        "Bern\n"
        "Workload\n"
        ":\n"
        "80%\n"
        "Contract type\n"
        ":\n"
        "Permanent position\n"
        "OAAT Organisation ambulante Arzttarife AG\n"
        "Promoted\n"
        "Easy apply\n"
    )
    title, company, location, workload, contract, posted = \
        serp._parse_card_text(text)
    assert title == "Experte/Expertin Statistik/Data Science"
    assert location == "Bern", f"ubicacion incorrecta: {location!r}"
    assert workload == "80%", f"workload incorrecto: {workload!r}"
    assert contract == "Permanent position", f"contrato incorrecto: {contract!r}"
    assert company == "OAAT Organisation ambulante Arzttarife AG"
    assert posted == "2 weeks ago"


def test_card_text_same_line_labels():
    # Formato compacto (label y valor en la misma linea)
    text = (
        "3 days ago\n"
        "Data Analyst\n"
        "Place of work: Geneva\n"
        "Workload: 100%\n"
        "Contract type: Permanent position\n"
        "ACME SA\n"
    )
    title, company, location, workload, contract, posted = \
        serp._parse_card_text(text)
    assert title == "Data Analyst"
    assert location == "Geneva"
    assert workload == "100%"
    assert contract == "Permanent position"
    assert company == "ACME SA"


def test_days_ago_multilingual():
    assert serp._days_ago("Today") == 0
    assert serp._days_ago("2 days ago") == 2
    assert serp._days_ago("3 weeks ago") == 21
    assert serp._days_ago("vor 5 Stunden") == 0
    assert serp._days_ago("vor 4 Tagen") == 4
    assert serp._days_ago("il y a 2 semaines") == 14
    assert serp._days_ago("Last month") == 30


def test_relative_from_iso():
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=2))
    rel = serp._relative_from_iso(
        (datetime.now(tz) - timedelta(days=3)).isoformat(timespec="seconds"))
    assert "ago" in rel
    rel_new = serp._relative_from_iso(
        (datetime.now(tz) - timedelta(minutes=5)).isoformat(timespec="seconds"))
    assert rel_new in ("Today", "0 minutes ago") or "minutes ago" in rel_new \
        or "hours ago" in rel_new


def test_build_search_url_national():
    url = serp.build_search_url("https://www.jobs.ch", "/en/vacancies/",
                                "Data Analyst", 1)
    assert url == "https://www.jobs.ch/en/vacancies/?term=Data+Analyst"
    url2 = serp.build_search_url("https://www.jobs.ch", "/en/vacancies/",
                                 "Data Analyst", 3)
    assert "page=3" in url2


def test_is_recent_iso():
    from datetime import datetime, timedelta, timezone

    tz = timezone(timedelta(hours=2))
    recent = (datetime.now(tz) - timedelta(days=3)).isoformat(timespec="seconds")
    old = (datetime.now(tz) - timedelta(days=400)).isoformat(timespec="seconds")
    assert serp._is_recent_iso(recent, 7) is True
    assert serp._is_recent_iso(old, 7) is False
    assert serp._is_recent_iso("", 7) is True
