"""Tests for the source catalog (T-01, T-02, T-03; RF-2, RF-4, RF-5)."""
from __future__ import annotations

import pytest

from verification import sources

DIRECT_IDS = ("indeed", "linkedin", "infojobs")
MULTI_SITE_IDS = (
    "irishjobs",
    "stepstone_nl",
    "devitjobs",
    "nvb",
    "jobs_ch",
    "glassdoor",
)
ALL_IDS = DIRECT_IDS + MULTI_SITE_IDS

CANONICAL_FIELDS = (
    "id",
    "title",
    "company",
    "description",
    "salary",
    "skills",
    "work_mode",
    "location",
    "posted_date",
)

DISPLAY_NAMES = {
    "indeed": "Indeed",
    "linkedin": "LinkedIn",
    "infojobs": "InfoJobs",
    "irishjobs": "IrishJobs",
    "stepstone_nl": "StepStone NL",
    "devitjobs": "DevITjobs",
    "nvb": "NVB",
    "jobs_ch": "Jobs.ch",
    "glassdoor": "Glassdoor",
}

# Deduplication key used by each source's run output.
DEDUP_KEYS = {
    "indeed": "job_key",
    "linkedin": "job_id",
    "infojobs": "id_oferta",
    "irishjobs": "job_id",
    "stepstone_nl": "job_id",
    "devitjobs": "job_id",
    "nvb": "job_id",
    "jobs_ch": "job_id",
    "glassdoor": "job_id",
}

# canonical field -> source columns actually produced by each scraper.
MULTI_SITE_ALIASES = {
    "id": ("job_id",),
    "title": ("title",),
    "company": ("company_name",),
    "description": ("description_full", "description_snippet"),
    "salary": ("salary_raw", "salary_min", "salary_max"),
    "skills": ("skills",),
    "work_mode": ("work_mode",),
    "location": (
        "location_raw",
        "location_city",
        "location_region",
        "location_country",
    ),
    "posted_date": ("posted_datetime",),
}

FIELD_ALIASES = {
    "indeed": {
        "id": ("job_key",),
        "title": ("title",),
        "company": ("company",),
        "description": ("description_text",),
        "salary": ("salary_text", "salary_min", "salary_max"),
        "skills": (),
        "work_mode": ("workplace_type",),
        "location": ("location", "city", "state", "country"),
        "posted_date": ("posted_date",),
    },
    "linkedin": {
        "id": ("job_id",),
        "title": ("title",),
        "company": ("company_name",),
        "description": ("description_full",),
        "salary": ("salary_raw", "salary_min", "salary_max"),
        "skills": ("skills",),
        "work_mode": ("work_mode",),
        "location": (
            "location_raw",
            "location_city",
            "location_region",
            "location_country",
        ),
        "posted_date": ("posted_datetime",),
    },
    "infojobs": {
        "id": ("id_oferta",),
        "title": ("titulo",),
        "company": ("empresa",),
        "description": ("descripcion_snippet",),
        "salary": ("salario_raw", "salario_min", "salario_max"),
        "skills": (),
        "work_mode": ("modalidad",),
        "location": ("ciudad", "provincia", "pais"),
        "posted_date": ("fecha_publicacion",),
    },
    "irishjobs": MULTI_SITE_ALIASES,
    "stepstone_nl": MULTI_SITE_ALIASES,
    "devitjobs": MULTI_SITE_ALIASES,
    "nvb": MULTI_SITE_ALIASES,
    "jobs_ch": MULTI_SITE_ALIASES,
    "glassdoor": MULTI_SITE_ALIASES,
}


def test_all_nine_sources_are_registered():
    assert sources.source_ids() == ALL_IDS
    assert len(sources.all_sources()) == 9


def test_direct_sources_are_grouped_as_direct():
    for source_id in DIRECT_IDS:
        source = sources.get_source(source_id)
        assert source.group == "direct"
        assert source.site is None


def test_multi_site_portals_are_grouped_and_expose_their_site():
    for source_id in MULTI_SITE_IDS:
        source = sources.get_source(source_id)
        assert source.group == "multi_site"
        assert source.site == source_id


def test_multi_site_sites_match_real_run_values():
    assert sources.MULTI_SITE_SITES == MULTI_SITE_IDS
    assert tuple(s.id for s in sources.multi_site_portals()) == MULTI_SITE_IDS
    # each portal is measurable independently
    assert len(set(sources.MULTI_SITE_SITES)) == 6


def test_display_names():
    for source_id, name in DISPLAY_NAMES.items():
        assert sources.get_source(source_id).display_name == name


def test_deduplication_keys():
    for source_id, key in DEDUP_KEYS.items():
        assert sources.dedup_key(source_id) == key


def test_every_source_maps_all_canonical_fields():
    for source_id in ALL_IDS:
        field_map = sources.field_map(source_id)
        assert set(field_map) == set(CANONICAL_FIELDS)


def test_every_defined_alias_is_covered_with_exact_columns():
    for source_id in ALL_IDS:
        field_map = sources.field_map(source_id)
        expected = FIELD_ALIASES[source_id]
        for field in CANONICAL_FIELDS:
            assert tuple(field_map[field]) == expected[field], (source_id, field)


def test_empty_skills_alias_is_explicit_for_sources_without_skills():
    # Indeed and InfoJobs do not produce a skills column at origin.
    assert sources.field_aliases("indeed", "skills") == ()
    assert sources.field_aliases("infojobs", "skills") == ()
    # ...but the field is still part of every map (measurable as absent).
    assert "skills" in sources.field_map("indeed")
    assert "skills" in sources.field_map("infojobs")


def test_field_aliases_lookup_by_field():
    assert sources.field_aliases("indeed", "company") == ("company",)
    assert sources.field_aliases("linkedin", "posted_date") == ("posted_datetime",)
    assert sources.field_aliases("infojobs", "title") == ("titulo",)
    assert sources.field_aliases("glassdoor", "id") == ("job_id",)


def test_field_aliases_without_field_returns_full_map():
    assert sources.field_aliases("linkedin") == sources.field_map("linkedin")


def test_unknown_source_raises():
    with pytest.raises(KeyError):
        sources.get_source("unknown_source")


def test_every_source_has_evidence_locations():
    for source_id in ALL_IDS:
        source = sources.get_source(source_id)
        assert source.evidence, source_id
        assert source.evidence.get("general_log")
        assert source.evidence.get("run_log")
        assert source.evidence.get("checkpoints")
        assert source.evidence.get("snapshot")
        assert source.evidence.get("manifest")


def test_multi_site_evidence_is_per_portal():
    for source_id in MULTI_SITE_IDS:
        evidence = sources.get_source(source_id).evidence
        assert source_id in evidence["run_log"]
        assert source_id in evidence["checkpoints"]
        assert source_id in evidence["snapshot"]
    # all six share the merged run result and the multi_site manifest
    assert "last_run.json" in sources.get_source("glassdoor").evidence["result"]
    assert sources.get_source("nvb").evidence["manifest"].endswith("multi_site")
