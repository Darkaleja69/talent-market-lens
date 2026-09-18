import json

from src.glassdoor import bff


def test_extract_csrf_token_patterns():
    assert bff.extract_csrf_token('"token":"abc123"') == "abc123"
    assert bff.extract_csrf_token('gdCSRFToken = "xyz789"') == "xyz789"
    assert bff.extract_csrf_token('<meta name="csrf-token" content="tok456">') == "tok456"
    assert bff.extract_csrf_token("<html>sin token</html>") == ""
    assert bff.extract_csrf_token("") == ""


def test_build_payload():
    payload = bff.build_payload("Data Analyst", 1, "COUNTRY", 1, cursor=None,
                                fromage=7, num_jobs=30)
    obj = json.loads(payload)[0]
    assert obj["operationName"] == "JobSearchResultsQuery"
    vars_ = obj["variables"]
    assert vars_["keyword"] == "Data Analyst"
    assert vars_["locationId"] == 1
    assert vars_["locationType"] == "COUNTRY"
    assert vars_["pageNumber"] == 1
    assert vars_["pageCursor"] is None
    assert {"filterKey": "fromAge", "values": "7"} in vars_["filterParams"]
    assert "query" in obj


def test_extract_listings():
    resp = {
        "data": {
            "jobListings": {
                "jobListings": [{"jobview": {}}],
                "paginationCursors": [
                    {"pageNumber": 1, "cursor": "C1"},
                    {"pageNumber": 2, "cursor": "C2"},
                ],
                "totalJobsCount": 123,
            }
        }
    }
    listings, cursor, total = bff._extract_listings(resp, next_page_num=2)
    assert len(listings) == 1
    assert cursor == "C2"
    assert total == 123


def test_extract_listings_partial_errors_tolerated():
    resp = {
        "errors": [{"message": "err en jobsPageSeoData"}],
        "data": {
            "jobListings": {
                "jobListings": [{"jobview": {}}],
                "paginationCursors": [],
                "totalJobsCount": 5,
            }
        }
    }
    listings, _, total = bff._extract_listings(resp, next_page_num=2)
    assert len(listings) == 1  # error no fatal: se conservan los listados


def test_extract_listings_fatal_error():
    resp = {"errors": [{"message": "jobListings explosion"}], "data": {}}
    listings, cursor, total = bff._extract_listings(resp, next_page_num=2)
    assert listings == []
    assert cursor == ""
    assert total == 0


def _sample_jobview():
    return {
        "header": {
            "jobTitleText": "Senior Data Analyst",
            "employerNameFromSearch": "ACME Corp",
            "employer": {"id": "1001", "name": "ACME Corp"},
            "locationName": "New York, NY",
            "locationType": "C",
            "ageInDays": 3,
            "payCurrency": "USD",
            "payPeriod": "ANNUAL",
            "payPeriodAdjustedPay": {"p10": 80000.0, "p50": 100000.0,
                                     "p90": 134000.0},
            "rating": 4.2,
            "easyApply": True,
            "sponsored": False,
        },
        "job": {
            "listingId": 987654321,
            "jobTitleText": "Senior Data Analyst",
            "description": "We need SQL and Python skills.",
        },
    }


def test_parse_jobview_full():
    # Formato real: el listing viene envuelto en {"jobview": {...}}
    wrapped = {"__typename": "JobListingSearchResult", "jobview": _sample_jobview()}
    card = bff.parse_jobview(wrapped, "https://www.glassdoor.com",
                             "United States")
    assert card is not None
    assert card["job_id"] == "gd_987654321"
    assert card["title"] == "Senior Data Analyst"
    assert card["company_name"] == "ACME Corp"
    assert card["job_url"] == "https://www.glassdoor.com/job-listing/j?jl=987654321"
    assert card["salary_min"] == 80000.0
    assert card["salary_max"] == 134000.0
    assert card["salary_currency"] == "USD"
    assert card["salary_period"] == "YEAR"
    assert card["company_rating"] == "4.2"
    assert card["work_mode"] == ""
    assert card["is_new"] is False
    assert card["description_full"] == "We need SQL and Python skills."

    # Formato plano (tolerado)
    card2 = bff.parse_jobview(_sample_jobview(), "https://www.glassdoor.com",
                              "United States")
    assert card2 is not None
    assert card2["job_id"] == "gd_987654321"


def test_parse_jobview_remote_and_none():
    jv = _sample_jobview()
    jv["header"]["locationName"] = "Remote"
    jv["header"]["locationType"] = "S"
    jv["header"]["ageInDays"] = 0
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "United States")
    assert card["work_mode"] == "Remote"
    assert card["is_new"] is True

    assert bff.parse_jobview({}, "https://www.glassdoor.com", "") is None


def test_parse_jobview_hybrid_from_location_name():
    jv = _sample_jobview()
    jv["header"]["locationName"] = "Hybrid - Madrid"
    jv["header"]["locationType"] = "O"
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "Spain")
    assert card["work_mode"] == "Hybrid"


def test_parse_jobview_onsite_when_office_type():
    jv = _sample_jobview()
    jv["header"]["locationName"] = "Madrid, Comunidad de Madrid"
    jv["header"]["locationType"] = "O"
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "Spain")
    assert card["work_mode"] == "On-site"


def test_parse_jobview_remote_in_title():
    jv = _sample_jobview()
    jv["header"]["locationName"] = "Madrid"
    jv["header"]["locationType"] = "C"
    jv["header"]["jobTitleText"] = "Data Engineer - 100% remoto"
    jv["job"]["jobTitleText"] = "Data Engineer - 100% remoto"
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "Spain")
    assert card["work_mode"] == "Remote"


def test_parse_jobview_company_name_never_rating():
    jv = _sample_jobview()
    jv["header"]["employerNameFromSearch"] = "4,0"
    jv["header"]["employer"] = {"id": "1001", "name": "4,0"}
    jv["header"]["rating"] = 4.0
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "United States")
    assert card["company_name"] == ""
    assert card["company_rating"] == "4.0"


def test_parse_jobview_salary_period_normalized():
    jv = _sample_jobview()
    jv["header"]["payPeriod"] = "ANNUAL"
    card = bff.parse_jobview(jv, "https://www.glassdoor.com", "United States")
    assert card["salary_period"] == "YEAR"


def test_parse_location_raw():
    assert bff.parse_location_raw("New York, NY, United States") == \
        ("New York", "NY", "United States")
    assert bff.parse_location_raw("Remote") == ("Remote", "", "")
    assert bff.parse_location_raw("") == ("", "", "")


def _jobview_with_pay(p10, p90, period="MONTHLY", currency="USD", source=None):
    jv = _sample_jobview()
    jv["header"]["payPeriod"] = period
    jv["header"]["payCurrency"] = currency
    jv["header"]["payPeriodAdjustedPay"] = {"p10": p10, "p50": None, "p90": p90}
    if source is not None:
        jv["header"]["salarySource"] = source
    return jv


def test_parse_jobview_salary_is_estimated_by_default():
    # payPeriodAdjustedPay es un ajuste ESTIMADO de Glassdoor: sin salarySource
    # de empleador se marca como estimado.
    card = bff.parse_jobview(_jobview_with_pay(8000.0, 8000.0, source=""),
                             "https://www.glassdoor.com", "United States")
    assert card["salary_min"] == 8000.0
    assert card["salary_max"] == 8000.0
    assert card["salary_source"] == ""
    assert card["salary_is_estimated"] is True


def test_parse_jobview_salary_employer_provided_not_estimated():
    card = bff.parse_jobview(_jobview_with_pay(8000.0, 8000.0, source="EMP"),
                             "https://www.glassdoor.com", "United States")
    assert card["salary_is_estimated"] is False


def test_parse_jobview_salary_range_inverted_rejected():
    # min > max: dato no fiable -> se descarta (no alimenta el pipeline).
    card = bff.parse_jobview(_jobview_with_pay(120000.0, 80000.0),
                             "https://www.glassdoor.com", "United States")
    assert card["salary_min"] is None
    assert card["salary_max"] is None
    assert card["salary_raw"] == ""
    assert card["salary_raw_original"] != ""


def test_parse_jobview_salary_non_positive_rejected():
    card = bff.parse_jobview(_jobview_with_pay(-100.0, 50000.0),
                             "https://www.glassdoor.com", "United States")
    assert card["salary_min"] is None
    assert card["salary_max"] is None
    assert card["salary_raw"] == ""


def test_parse_jobview_salary_raw_original_conserva_valor_glassdoor():
    card = bff.parse_jobview(_jobview_with_pay(8000.0, 12000.0),
                             "https://www.glassdoor.com", "United States")
    assert card["salary_raw"] == "USD 8,000 - 12,000 per month"
    assert card["salary_raw_original"] == "USD 8,000 - 12,000 per month"
    assert card["salary_period"] == "MONTH"