import pytest
from datetime import date
from scraper.parser import (
    parse_listing,
    _extract_offer_id_from_url,
    _extract_offer_id_from_h2,
    _parse_relative_date,
    _parse_salary,
)
from scraper.main import build_search_url, _is_valid_results_page
from bs4 import BeautifulSoup


class TestExtractOfferIdUrl:
    def test_url_offer_pattern(self):
        url = "//www.infojobs.net/logrono/ingeniero-data-mlops/of-i1f3cd1b6654cbd815492329e8b007d"
        assert _extract_offer_id_from_url(url) == "1f3cd1b6654cbd815492329e8b007d"

    def test_url_company_pattern(self):
        url = "https://www.infojobs.net/personas-y-estrategia/em-i55505556495697801011149"
        assert _extract_offer_id_from_url(url) == "55505556495697801011149"

    def test_fallback_to_url(self):
        url = "https://example.com/short"
        assert _extract_offer_id_from_url(url) == url


class TestExtractOfferIdH2:
    def test_from_h2_id(self):
        soup = BeautifulSoup(
            '<h2 id="job-title-abc123def456"></h2>', "lxml"
        )
        h2 = soup.find("h2")
        assert _extract_offer_id_from_h2(h2) == "abc123def456"

    def test_none(self):
        assert _extract_offer_id_from_h2(None) == ""


class TestParseRelativeDate:
    def test_dias(self):
        result = _parse_relative_date("Hace 2 días")
        assert result is not None
        assert (date.today() - result).days == 2

    def test_short_form(self):
        result = _parse_relative_date("Hace 5d")
        assert result is not None
        assert (date.today() - result).days == 5

    def test_hours(self):
        result = _parse_relative_date("Hace 1h")
        assert result is not None
        assert result == date.today()

    def test_minutes(self):
        result = _parse_relative_date("Hace 11m")
        assert result is not None
        assert result == date.today()

    def test_abbreviated_month(self):
        result = _parse_relative_date("27 jul")
        assert result is not None
        assert result.month == 7
        assert result.day == 27

    def test_abbreviated_month_with_leading_zero(self):
        result = _parse_relative_date("05 ago")
        assert result is not None
        assert result.month == 8
        assert result.day == 5

    def test_iso_format(self):
        result = _parse_relative_date("2024-06-15")
        assert result is not None
        assert result.month == 6

    def test_invalid(self):
        assert _parse_relative_date("texto no fecha") is None


class TestParseSalary:
    def test_range_annual(self):
        min_s, max_s, moneda, periodo = _parse_salary("30.000 € - 40.000 € Bruto/año")
        assert min_s == 30000
        assert max_s == 40000
        assert moneda == "EUR"
        assert periodo == "anual"

    def test_single_value(self):
        min_s, max_s, moneda, _ = _parse_salary("25.000 €")
        assert min_s == 25000
        assert max_s is None

    def test_min_only_mas_de(self):
        min_s, max_s, moneda, _ = _parse_salary("Más de 30.000 €")
        assert min_s == 30000
        assert max_s is None
        assert moneda == "EUR"

    def test_no_disponible(self):
        vals = _parse_salary("Salario no disponible")
        assert vals == (None, None, None, None)

    def test_empty(self):
        vals = _parse_salary("")
        assert vals == (None, None, None, None)


class TestParseListing:
    def test_empty_html(self):
        offers = parse_listing("<html><body></body></html>", "Madrid", "data", 1)
        assert isinstance(offers, list)
        assert len(offers) == 0

    def test_real_fixture(self):
        fixture_path = (
            __file__.rsplit("\\", 1)[0]
            + "\\fixtures\\listing_madrid.html"
        )
        try:
            with open(fixture_path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            pytest.skip("Fixture not found — run scraper first to generate it")
        offers = parse_listing(html, "Madrid", "data", 1)
        assert len(offers) >= 1
        for o in offers:
            assert o.titulo != "Desconocido"
            assert o.empresa is not None
            assert o.url_oferta
            assert o.id_oferta
        assert all(emp for emp in (o.empresa for o in offers))

    def test_fixture_excludes_ads(self):
        fixture_path = (
            __file__.rsplit("\\", 1)[0]
            + "\\fixtures\\listing_madrid.html"
        )
        try:
            with open(fixture_path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            pytest.skip("Fixture not found")
        offers = parse_listing(html, "Madrid", "data", 1)
        ids = [o.id_oferta for o in offers]
        assert all(not id.startswith("https://") for id in ids)

    def test_current_fixture(self):
        fixture_path = (
            __file__.rsplit("\\", 1)[0]
            + "\\fixtures\\listing_madrid_current.html"
        )
        try:
            with open(fixture_path, "r", encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            pytest.skip("Fixture not found")
        offers = parse_listing(html, "Madrid", "data", 1)
        assert len(offers) >= 1
        for o in offers:
            assert o.titulo != "Desconocido"
            assert o.empresa
            assert o.url_oferta
            assert o.id_oferta
            assert not o.id_oferta.startswith("https://")
            assert "Publicidad" not in (o.titulo or "")


class TestBuildSearchUrl:
    def test_page_one_no_suffix(self):
        url = build_search_url({"slug": "madrid/madrid"}, "data", 1)
        assert url == "https://www.infojobs.net/ofertas-trabajo/madrid/madrid/data"

    def test_page_two_suffix(self):
        url = build_search_url({"slug": "madrid/madrid"}, "data", 2)
        assert url == "https://www.infojobs.net/ofertas-trabajo/madrid/madrid/data/2"

    def test_keyword_with_spaces(self):
        url = build_search_url({"slug": "madrid/madrid"}, "data analyst", 1)
        assert url == "https://www.infojobs.net/ofertas-trabajo/madrid/madrid/data+analyst"

    def test_keyword_uppercase_lowered(self):
        url = build_search_url({"slug": "vizcaya/bilbao"}, "Data", 1)
        assert url.endswith("/vizcaya/bilbao/data")


class TestIsValidResultsPage:
    def test_with_cards(self):
        html = '<li class="ij-List-item ij-OfferList-offerCardItem"></li>'
        assert _is_valid_results_page(html)

    def test_empty_state(self):
        html = "<html><body>Ningún resultado a la vista</body></html>"
        assert _is_valid_results_page(html)

    def test_captcha_blocked(self):
        html = "<html><body>distil captcha</body></html>"
        assert not _is_valid_results_page(html)
