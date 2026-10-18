"""Tests unitarios del parser de Indeed."""
import json

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

    # --- work_mode (reparacion 2026-10-18) --------------------------------

    def test_remote_hybrid_maps_to_hybrid(self):
        item = {
            "jobkey": "jk1",
            "remoteWorkModel": {"type": "REMOTE_HYBRID", "text": "Hybrid work"},
        }
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.workplace_type == "hybrid"
        assert offer.is_remote is True

    def test_remote_work_from_home_maps_to_remote(self):
        item = {
            "jobkey": "jk2",
            "remoteWorkModel": {"type": "REMOTE_WORK_FROM_HOME", "text": "Remote"},
        }
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.workplace_type == "remote"

    def test_explicit_remote_model_not_overridden_by_heuristic(self):
        # Titulo y localizacion dicen "remote", pero el modelo explicito manda.
        item = {
            "jobkey": "jk3",
            "displayTitle": "Remote Data Analyst",
            "formattedLocation": "Remote",
            "remoteWorkModel": {"type": "REMOTE_HYBRID", "text": "Hybrid work"},
        }
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.workplace_type == "hybrid"

    def test_remote_model_text_used_when_type_missing(self):
        item = {
            "jobkey": "jk4",
            "displayTitle": "Remote Data Analyst",
            "remoteWorkModel": {"text": "Hybrid work"},
        }
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.workplace_type == "hybrid"

    def test_heuristic_still_applies_without_remote_model(self):
        item = {"jobkey": "jk5", "displayTitle": "Trabajo remoto"}
        offer = _map_item_to_offer(item, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is not None
        assert offer.workplace_type == "remote"

    # --- job_type desde jobTypes (reparacion 2026-10-18) -------------------

    def test_job_type_from_job_types_when_translated_attributes_empty(self):
        item = {"jobkey": "jk6", "translatedAttributes": [], "jobTypes": ["Contract"]}
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.job_type == "Contract"

    def test_job_type_uses_first_job_type(self):
        item = {"jobkey": "jk7", "jobTypes": ["Full-time", "Permanent"]}
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.job_type == "Full-time"

    def test_job_type_from_job_types_object_form(self):
        item = {"jobkey": "jk8", "jobTypes": [{"text": "Permanent"}]}
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.job_type == "Permanent"

    def test_translated_attributes_wins_over_job_types(self):
        item = {
            "jobkey": "jk9",
            "translatedAttributes": [{"text": "Jornada completa"}],
            "jobTypes": ["Contract"],
        }
        offer = _map_item_to_offer(item, "ES", "Madrid", "data", 1, "es.indeed.com")
        assert offer is not None
        assert offer.job_type == "Jornada completa"

    def test_job_type_falls_back_when_translated_attributes_lack_text(self):
        # Borde: translatedAttributes presente pero sin text/label (dict con
        # otras claves) -> cae al primer jobTypes de la tarjeta.
        item = {
            "jobkey": "jk11",
            "translatedAttributes": [{"foo": "bar"}],
            "jobTypes": ["Contract"],
        }
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.job_type == "Contract"

    def test_job_type_empty_without_any_source(self):
        item = {"jobkey": "jk10"}
        offer = _map_item_to_offer(item, "IE", "Dublin", "data", 1, "ie.indeed.com")
        assert offer is not None
        assert offer.job_type == ""


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

    def test_extracts_from_auto_open_key_without_initial_data(self):
        # T-32: Indeed ya no publica `window._initialData =`; el contenedor
        # sigue embebido en un <script> y se localiza por su clave.
        html = (
            '<html><script type="text/javascript">\n'
            'window.mosaic.providerData["modules"] = {'
            '"autoOpenTwoPaneViewjobResponse":{"body":{"hostQueryExecutionResult":'
            '{"data":{"jobData":{"results":['
            '{"job":{"key":"jk9","description":{"html":"<p>Descripcion embebida</p>"}}}'
            ']}}}}}};\n</script></html>'
        )
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {"jk9": "<p>Descripcion embebida</p>"}

    def test_extracts_when_body_is_an_encoded_json_string(self):
        payload = {
            "hostQueryExecutionResult": {
                "data": {
                    "jobData": {
                        "results": [
                            {
                                "job": {
                                    "key": "jk10",
                                    "description": {"html": "<b>cuerpo string</b>"},
                                }
                            }
                        ]
                    }
                }
            }
        }
        container = {
            "autoOpenTwoPaneViewjobResponse": {"body": json.dumps(payload)}
        }
        html = "<script>window.x = " + json.dumps(container) + ";</script>"
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {"jk10": "<b>cuerpo string</b>"}

    def test_tolerates_unrelated_scripts_and_missing_key(self):
        html = (
            "<script>var autoOpenTwoPaneViewjobResponseInfo = 1;</script>"
            '<script id="mosaic-data">window.mosaic.providerData["x"] = {}</script>'
        )
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {}

    def test_extracts_from_job_info_wrapper_model_path(self):
        # Iteracion 3 (layout 2026-10): la descripcion embebida vive en
        # body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription (HTML)
        # y la clave del job en body.jobKey.
        payload = {
            "jobKey": "jk20",
            "jobInfoWrapperModel": {
                "jobInfoModel": {
                    "sanitizedJobDescription": (
                        "&#x26; <p>Descripcion nueva &amp; completa</p>"
                    )
                }
            },
        }
        container = {"autoOpenTwoPaneViewjobResponse": {"body": payload}}
        html = "<script>window.x = " + json.dumps(container) + ";</script>"
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {"jk20": "& <p>Descripcion nueva & completa</p>"}

    def test_extracts_new_path_when_body_is_an_encoded_json_string(self):
        payload = {
            "jobKey": "jk21",
            "jobInfoWrapperModel": {
                "jobInfoModel": {"sanitizedJobDescription": "<b>ruta nueva string</b>"}
            },
        }
        container = {"autoOpenTwoPaneViewjobResponse": {"body": json.dumps(payload)}}
        html = "<script>window.y = " + json.dumps(container) + ";</script>"
        descriptions = _extract_initial_data_descriptions(html)
        assert descriptions == {"jk21": "<b>ruta nueva string</b>"}

    def test_returns_empty_when_new_and_old_paths_are_missing(self):
        payload = {
            "jobKey": "jk22",
            "jobInfoWrapperModel": {"jobInfoModel": {"title": "sin descripcion"}},
        }
        container = {"autoOpenTwoPaneViewjobResponse": {"body": payload}}
        html = "<script>window.z = " + json.dumps(container) + ";</script>"
        assert _extract_initial_data_descriptions(html) == {}

    def test_old_path_still_works_after_adding_new_path(self):
        payload = {
            "hostQueryExecutionResult": {
                "data": {
                    "jobData": {
                        "results": [
                            {
                                "job": {
                                    "key": "jk23",
                                    "description": {"html": "<i>ruta antigua</i>"},
                                }
                            }
                        ]
                    }
                }
            }
        }
        container = {"autoOpenTwoPaneViewjobResponse": {"body": payload}}
        html = "<script>window.w = " + json.dumps(container) + ";</script>"
        assert _extract_initial_data_descriptions(html) == {"jk23": "<i>ruta antigua</i>"}
