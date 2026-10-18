"""Tests del detalle de IrishJobs (T-34, spec 004).

Sin red ni navegador: prioridad de la descripcion JSON-LD sobre el teaser del
DOM, mapeo `employmentType` -> `work_mode` y `datePosted` -> `posted_datetime`.
"""
from __future__ import annotations

import types
from pathlib import Path

from fake_dom import FakePage
from src.irishjobs import detail

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _offer():
    return types.SimpleNamespace(
        job_id="108057919",
        job_url="https://www.irishjobs.ie/job/x-job108057919",
    )


def test_json_ld_description_is_not_overwritten_by_dom():
    page = FakePage(_fixture("irishjobs_detail_jsonld.html"))

    result = detail.parse_detail(page, _offer())

    desc = result["description_full"]
    assert "ingestion pipelines" in desc   # descripcion completa del JSON-LD
    assert "Sign in to see" not in desc    # el teaser del DOM no la pisa
    assert len(desc) > 100


def test_employment_type_maps_to_work_mode():
    result: dict = {}
    detail._parse_json_ld_detail(_fixture("irishjobs_detail_jsonld.html"), result)

    assert result["work_mode"] == "Remote"
    assert "REMOTE_WORKING" in result["employment_type"]


def test_employment_type_hybrid_maps_to_hybrid():
    html = ('<script type="application/ld+json">'
            '{"@type": "JobPosting", "title": "X", '
            '"employmentType": ["HYBRID"]}</script>')
    result: dict = {}
    detail._parse_json_ld_detail(html, result)

    assert result["work_mode"] == "Hybrid"


def test_date_posted_from_json_ld():
    result: dict = {}
    detail._parse_json_ld_detail(_fixture("irishjobs_detail_jsonld.html"), result)

    assert result["posted_datetime"] == "2026-09-29T13:08:04.08Z"


def test_dom_description_used_when_json_ld_has_none():
    html = ("<html><body><div class='job-description'>DOM only description "
            "with Python and SQL details.</div></body></html>")
    page = FakePage(html)

    result = detail.parse_detail(page, _offer())

    assert "DOM only description" in result.get("description_full", "")
