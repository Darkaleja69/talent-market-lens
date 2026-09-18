"""Tests unitarios del parser de Indeed."""
import pytest

from scraper.parser import (
    _balance_braces,
    _clean_html,
    _epoch_ms_to_iso,
    _extract_initial_data_descriptions,
    _extract_jobcards_results,
    _looks_remote,
    _map_item_to_offer,
    _salary_from_item,
)


class TestBalanceBraces:
    def test_simple_object(self):
        s = '{"a": 1, "b": 2}'
        result = _balance_braces(s, 0)
        assert result == len(s)

    def test_nested_object(self):
        s = '{"a": {"nested": true}, "b": [1, 2, 3]}'
        result = _balance_braces(s, 0)
        assert result == len(s)

    def test_brace_inside_string(self):
        s = '{"a": "text with } inside", "b": 2}'
        result = _balance_braces(s, 0)
        assert result == len(s)

    def test_escaped_quote_in_string(self):
        s = '{"a": "text with \\"quote\\"", "b": 2}'
        result = _balance_braces(s, 0)
        assert result == len(s)

    def test_deeply_nested_with_strings(self):
        s = '{"metaData":{"mosaicProviderJobCardsModel":{"results":[{"jobkey":"abc123","snippet":"<b>Desc con } braces</b>"}]}}}'
        result = _balance_braces(s, 0)
        assert result == len(s)

    def test_unmatched_returns_minus_one(self):
        s = '{"a": 1'
        result = _balance_braces(s, 0)
        assert result == -1

    def test_start_not_at_brace(self):
        s = 'prefix {"a": 1}'
        result = _balance_braces(s, s.find("{"))
        assert result == len(s)


class TestCleanHtml:
    def test_bold_tags(self):
        result = _clean_html("<b>Hello</b> <p>World</p>")
        assert "Hello" in result
        assert "World" in result
        assert "<b>" not in result

    def test_empty_string(self):
        assert _clean_html("") == ""

    def test_none(self):
        assert _clean_html(None) == ""

    def test_html_entities(self):
        result = _clean_html("&amp; &lt; &gt;")
        assert result == "& < >"

    def test_multiple_spaces_collapsed(self):
        result = _clean_html("a   b\n\nc")
        assert result == "a b c"


class TestEpochMsToIso:
    def test_valid_epoch(self):
        result = _epoch_ms_to_iso(1781860197173)
        assert result == "2026-06-19T09:09:57+00:00"

    def test_zero_returns_empty(self):
        assert _epoch_ms_to_iso(0) == ""

    def test_none_returns_empty(self):
        assert _epoch_ms_to_iso(None) == ""


class TestLooksRemote:
    def test_remote_in_location(self):
        assert _looks_remote("Madrid, remoto", "", None) is True

    def test_hybrid_in_title(self):
        assert _looks_remote("Madrid", "Híbrido", None) is True

    def test_remote_model_dict(self):
        model = {"text": "Trabajo remoto", "type": "REMOTE"}
        assert _looks_remote("", "", model) is True

    def test_not_remote(self):
        assert _looks_remote("Madrid", "Data Engineer", None) is False

    def test_teleworking_keyword(self):
        assert _looks_remote("teletrabajo Madrid", "", None) is True


class TestSalaryFromItem:
    def test_extracted_salary(self):
        item = {
            "extractedSalary": {"min": 30000, "max": 45000, "type": "YEARLY"},
            "salarySnippet": {"text": "30.000 € - 45.000 € al año"},
        }
        smin, smax, stype, text = _salary_from_item(item)
        assert smin == 30000
        assert smax == 45000
        assert stype == "YEARLY"
        assert "30.000" in text

    def test_salary_snippet_only(self):
        item = {
            "salarySnippet": {"text": "50.000 € al año"},
        }
        smin, smax, stype, text = _salary_from_item(item)
        assert smin is None
        assert smax is None
        assert stype == ""
        assert "50.000" in text

    def test_no_salary(self):
        item = {}
        smin, smax, stype, text = _salary_from_item(item)
        assert smin is None
        assert smax is None
        assert stype == ""
        assert text == ""


class TestMapItemToOffer:
    def test_basic_item(self):
        item = {
            "jobkey": "abc123def456",
            "displayTitle": "Data Engineer",
            "company": "Tech Corp",
            "formattedLocation": "Madrid, Madrid provincia",
            "jobLocationCity": "Madrid",
            "jobLocationState": "Madrid",
            "companyRating": 4.2,
            "companyReviewCount": 150,
            "formattedRelativeTime": "hace 30+ días",
            "pubDate": 1781860197173,
            "sponsored": False,
        }
        offer = _map_item_to_offer(item, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is not None
        assert offer.job_key == "abc123def456"
        assert offer.title == "Data Engineer"
        assert offer.company == "Tech Corp"
        assert offer.location == "Madrid, Madrid provincia"
        assert offer.city == "Madrid"
        assert offer.company_rating == 4.2
        assert offer.company_review_count == 150
        assert offer.is_sponsored is False
        assert offer.posted_date != ""

    def test_missing_jobkey_returns_none(self):
        offer = _map_item_to_offer({}, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is None

    def test_jobkey_from_mouse_down_handler(self):
        item = {"mouseDownHandlerOption": {"jobKey": "xyz999"}}
        offer = _map_item_to_offer(item, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is not None
        assert offer.job_key == "xyz999"

    def test_jobkey_from_link(self):
        item = {"link": "/viewjob?jk=deadbeef00112233"}
        offer = _map_item_to_offer(item, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is not None
        assert offer.job_key == "deadbeef00112233"

    def test_fill_trace(self):
        item = {"jobkey": "test123", "displayTitle": "Title"}
        offer = _map_item_to_offer(item, "IE", "Dublin", "python", 2, "ie.indeed.com")
        assert offer is not None
        assert offer.country == "IE"
        assert offer.city_query == "Dublin"
        assert offer.search_term == "python"
        assert offer.page == 2
        assert offer.scraped_at != ""


class TestExtractJobcardsResults:
    def test_valid_mosaic_data(self):
        html = """<html><script id="mosaic-data">
window.mosaic.providerData["mosaic-provider-jobcards"] = {
  "metaData": {
    "mosaicProviderJobCardsModel": {
      "results": [
        {"jobkey": "abc123", "displayTitle": "Test Job", "company": "Test Co"}
      ]
    }
  }
}
</script></html>"""
        results = _extract_jobcards_results(html)
        assert results is not None
        assert len(results) == 1
        assert results[0]["jobkey"] == "abc123"

    def test_no_mosaic_data_script(self):
        html = "<html><body>no data</body></html>"
        results = _extract_jobcards_results(html)
        assert results is None

    def test_jobcard_with_special_chars(self):
        html = '<script id="mosaic-data">window.mosaic.providerData["mosaic-provider-jobcards"] = {"metaData":{"mosaicProviderJobCardsModel":{"results":[{"jobkey":"jk001","snippet":"<b>text with } and \\" and \\\\ inside</b>"}]}}}</script>'
        results = _extract_jobcards_results(html)
        assert results is not None
        assert len(results) == 1
        assert results[0]["jobkey"] == "jk001"


class TestExtractInitialDataDescriptions:
    def test_extracts_descriptions(self):
        html = (
            'window._initialData={"autoOpenTwoPaneViewjobResponse":{"body":{'
            '"hostQueryExecutionResult":{"data":{"jobData":'
            '{"results":['
            '{"job":{"key":"abc123","description":{"html":"\\u003Cp>Descripción del puesto\\u003C/p>"}}},'
            '{"job":{"key":"def456","description":{"html":"\\u003Cb>Otro puesto\\u003C/b>"}}}'
            ']}}}}}}'
        )
        descriptions = _extract_initial_data_descriptions(html)
        assert len(descriptions) == 2
        assert "<p>" in descriptions["abc123"]
        assert "Descripción" in descriptions["abc123"]
        assert "<b>Otro puesto</b>" in descriptions["def456"]

    def test_missing_path_returns_empty(self):
        html = 'window._initialData={"otherKey": "no descriptions here"}'
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {}

    def test_no_initial_data_returns_empty(self):
        html = "<html><body>nothing</body></html>"
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {}

    def test_skips_invalid_items(self):
        html = (
            'window._initialData={"autoOpenTwoPaneViewjobResponse":{"body":{'
            '"hostQueryExecutionResult":{"data":{"jobData":'
            '{"results":['
            '{"notajob": true},'
            '{"job": {"key": "valid", "description": {"html": "desc"}}}'
            ']}}}}}}'
        )
        descriptions = _extract_initial_data_descriptions(html)
        assert len(descriptions) == 1
        assert "valid" in descriptions

    def test_missing_auto_open_returns_empty(self):
        html = 'window._initialData={"hostQueryExecutionResult":{"data":{"jobData":{"results":[{"job":{"key":"x","description":{"html":"d"}}}]}}}}'
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {}
