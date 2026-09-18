from src.glassdoor import serp


def test_is_job_href():
    assert serp._is_job_href("/job-listing/data-analyst-acme-JV_KO0,12.htm?jl=123")
    assert serp._is_job_href("/partner/jobListing.htm?jobListingId=456")
    assert serp._is_job_href("https://www.glassdoor.com/job-listing/x.htm?jl=789")
    assert not serp._is_job_href("/Job/data-analyst-jobs-SRCH_KO0,12.htm")
    assert not serp._is_job_href("/Empleos/Acme-Jobs-E139052.htm")
    assert not serp._is_job_href("/index.htm")


def test_listing_id_from_href():
    assert serp._listing_id_from_href("/job-listing/x.htm?jl=1010177474176") == \
        "1010177474176"
    assert serp._listing_id_from_href(
        "/partner/jobListing.htm?jobListingId=1010155232017") == "1010155232017"
    assert serp._listing_id_from_href("/job-listing/x.htm") == ""


def test_extract_company_avoids_rating():
    # El bug historico: el rating "4,1" se guardaba como empresa
    text = (
        "Data Scientist\n"
        "4,1\n"
        "Product Madness\n"
        "Barcelona\n"
        "Anuncio 4 días\n"
        "3086 € (Proporcionado por la empresa)"
    )
    company = serp._extract_company(text, "Data Scientist")
    assert company == "Product Madness", f"empresa incorrecta: {company!r}"


def test_extract_company_rating_english():
    text = (
        "Senior Data Analyst\n"
        "3.8\n"
        "Crum & Forster\n"
        "New York, NY\n"
        "3 days ago\n"
        "$80K - $134K (Employer provided)"
    )
    company = serp._extract_company(text, "Senior Data Analyst")
    assert company == "Crum & Forster"


def test_extract_location_avoids_title_and_rating():
    text = (
        "Data Engineer\n"
        "4.1\n"
        "ACME\n"
        "Chicago, IL\n"
        "5 days ago"
    )
    location = serp._extract_location(text)
    assert location == "Chicago, IL"
    assert location != "Data Engineer"


def test_extract_salary_usd():
    text = "$80K - $134K (Employer provided) 3 days ago"
    sal = serp._extract_salary(text)
    assert "$80K" in sal


def test_has_no_next_with_footer_buttons():
    # El footer tiene botones show-more que NO deben contar como paginacion:
    # el parser ya no consulta esos selectores genericos.
    class FakePage:
        def query_selector(self, sel):
            return None

        def query_selector_all(self, sel):
            return []

    assert serp.has_next_page(FakePage()) is False


def test_has_next_with_real_next_link():
    class FakeLink:
        def get_attribute(self, name):
            return "/Job/data-analyst-jobs-SRCH_KO0,12_IP2.htm"

    class FakePage:
        def query_selector(self, sel):
            return FakeLink() if sel == 'link[rel="next"]' else None

    assert serp.has_next_page(FakePage()) is True