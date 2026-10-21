"""Tests del SERP de IrishJobs (T-34, spec 004).

Sin red ni navegador: las fixtures reproducen la estructura real observada el
2026-10-17 (marcadores data-at, tarjetas job-item, paginacion del sitio y una
pagina mitigada de control; ver
`repairs/20261017-irishjobs/evidence/serp-probe-facts.md`).
"""
from pathlib import Path

from fake_dom import FakePage
from src.irishjobs import serp

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_cards_with_current_selectors():
    page = FakePage(_fixture("irishjobs_serp_cards.html"))
    cards = serp.parse_cards(page, base_url="https://www.irishjobs.ie")
    assert len(cards) == 2

    first = cards[0]
    assert first["job_id"] == "108057919"
    assert first["job_url"] == (
        "https://www.irishjobs.ie/job/staff-data-engineer/"
        "reperio-human-capital-ltd-job108057919")
    assert first["title"] == "Staff Data Engineer"
    assert first["company_name"] == "Reperio Human Capital Ltd"
    assert first["location_raw"] == "Dublin, County Dublin"
    assert first["posted_datetime"] == "2026-10-15T09:00:00.00Z"
    assert first["posted_relative"] == "2 days ago"
    assert first["salary_min"] == 144000.0
    assert first["salary_max"] == 156000.0
    assert first["salary_period"] == "year"
    assert first["salary_disclosed"] is True

    second = cards[1]
    assert second["job_id"] == "108057920"
    assert second["work_mode"] == "Remote"
    assert second["salary_raw"] == ""
    assert second["salary_disclosed"] is False


def test_detect_block_valid_page_and_legitimate_empty_page():
    cards_page = FakePage(_fixture("irishjobs_serp_cards.html"))
    blocked, reason, markers = serp.detect_block(cards_page)
    assert blocked is False
    assert reason == ""
    assert markers["resultlist"] is True

    empty_page = FakePage(_fixture("irishjobs_serp_empty.html"))
    blocked, reason, markers = serp.detect_block(empty_page)
    assert blocked is False
    assert markers["resultlist"] is True
    assert markers["job_item"] is False


def test_detect_block_legitimate_empty_by_text_only():
    html = ("<html><head><title>No jobs</title></head><body>"
            "<p>There is no job matching your search Data Analyst</p>"
            "</body></html>")
    blocked, reason, _ = serp.detect_block(FakePage(html))
    assert blocked is False
    assert reason == "no_results"


def test_detect_block_mitigated_challenge_page():
    page = FakePage(_fixture("irishjobs_serp_blocked.html"))
    blocked, reason, markers = serp.detect_block(page)
    assert blocked is True
    assert reason == "challenge:access_denied"
    assert markers["resultlist"] is False
    assert markers["job_item"] is False


def test_detect_block_without_any_marker_is_blocked():
    html = ("<html><head><title>Loading</title></head>"
            "<body><div></div></body></html>")
    blocked, reason, _ = serp.detect_block(FakePage(html))
    assert blocked is True
    assert reason == "no_result_markers"


def test_detect_block_title_challenge_wins_over_structural_markers():
    """Un challenge mitigado puede conservar el contenedor del listado."""
    html = ("<html><head><title>Access Denied</title></head><body>"
            "<div data-at=\"unified-resultlist\"></div>"
            "<span data-at=\"search-jobs-count\">25 jobs</span>"
            "</body></html>")
    blocked, reason, markers = serp.detect_block(FakePage(html))
    assert blocked is True
    assert reason == "challenge:access_denied"
    assert markers["resultlist"] is True


def test_detect_block_title_challenge_word_is_not_a_false_positive():
    """"Challenge" en un titulo legitimo no debe marcar la pagina como bloqueada."""
    html = ("<html><head><title>Data Challenge Jobs</title></head><body>"
            "<div data-at=\"unified-resultlist\"></div>"
            "<span data-at=\"search-jobs-count\">12 jobs</span>"
            "</body></html>")
    blocked, reason, markers = serp.detect_block(FakePage(html))
    assert blocked is False
    assert reason == ""
    assert markers["resultlist"] is True


def test_detect_block_ignores_body_challenge_on_valid_page():
    """Un 'challenge' en el cuerpo no bloquea una pagina con listado."""
    html = ("<html><head><title>Python Data job vacancies</title></head><body>"
            "<div data-at=\"unified-resultlist\">"
            "<p>Great challenge for a data engineer</p></div>"
            "</body></html>")
    blocked, reason, _ = serp.detect_block(FakePage(html))
    assert blocked is False
    assert reason == ""


def test_detect_block_valid_page_is_not_blocked():
    page = FakePage(_fixture("irishjobs_serp_cards.html"))
    blocked, reason, _ = serp.detect_block(page)
    assert blocked is False
    assert reason == ""


def test_pagination_url_has_no_action_and_page_is_first_param():
    url = serp.build_search_url("https://www.irishjobs.ie", "/jobs/{role_slug}",
                                "Python Data", 2, "7")
    assert url == "https://www.irishjobs.ie/jobs/python-data?page=2&age=7"
    assert "action" not in url
    assert "&page=" not in url

    first = serp.build_search_url("https://www.irishjobs.ie", "/jobs/{role_slug}",
                                  "Python Data", 1, "7")
    assert first == "https://www.irishjobs.ie/jobs/python-data?age=7"
    assert "action" not in first


def test_url_with_page_puts_page_first_and_drops_action():
    url = serp._url_with_page(
        "https://www.irishjobs.ie/jobs/x?age=7&action=paging_next", 3)
    assert url == "https://www.irishjobs.ie/jobs/x?page=3&age=7"
    assert "action" not in url
    assert "&page=" not in url


def test_has_next_page_from_site_nav_and_last_page():
    page = FakePage(_fixture("irishjobs_serp_cards.html"))
    assert serp.has_next_page(page) is True
    href = serp.next_page_href(page)
    assert href == "/jobs/python-data?page=2&ag=7"
    assert "action" not in href

    last = FakePage(_fixture("irishjobs_serp_cards.html"),
                    url="https://www.irishjobs.ie/jobs/python-data?page=3&age=7")
    assert serp.has_next_page(last) is False


def test_go_to_next_page_uses_site_href_without_action():
    page = FakePage(_fixture("irishjobs_serp_cards.html"))
    assert serp.go_to_next_page(page) is True
    assert page.goto_calls
    assert "action" not in page.goto_calls[-1]
    assert "page=2" in page.goto_calls[-1]
