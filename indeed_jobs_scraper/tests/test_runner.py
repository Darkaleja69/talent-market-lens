"""Tests offline del runner: deduplicacion con fusion de campos (2026-10-18).

Un job_key repetido puede haberse enriquecido en la SERP donde aparecio de
segundo (descripcion, beneficios, condiciones); `_dedup` conserva el primer
objeto y ahora fusiona en el los campos que tenga vacios. Caso real: 4 ofertas
del run 2026-10-18 tenian entrada en el cache intra-run pero el parquet exporto
el objeto pobre.
"""
from __future__ import annotations

from scraper.models import JobOffer
from scraper.runner import ScraperRunner


def _runner(tmp_path) -> ScraperRunner:
    return ScraperRunner("ES", ["Madrid"], ["data"], 1, tmp_path)


def _offer(jk: str) -> JobOffer:
    return JobOffer(job_key=jk, title="Data Engineer", company="Acme")


def test_dedup_keeps_first_offer_and_merges_late_enriched_duplicate(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")
    first.snippet = "snippet de la tarjeta"
    duplicate = _offer("jk1")
    desc_html = "<p>" + ("Descripcion completa del puesto " * 5) + "</p>"
    duplicate.description_html = desc_html
    duplicate.description_text = "Descripcion completa del puesto " * 5
    duplicate.benefits = "Seguro medico"
    duplicate.contract_type = "Jornada completa"
    duplicate.schedule = "turno de tarde"
    duplicate.workplace_type = "hybrid"

    unique = runner._dedup([first, duplicate])

    assert unique == [first]
    assert first.description_html == desc_html
    assert first.description_text
    assert first.snippet == "snippet de la tarjeta"  # no se pisa lo existente
    assert first.benefits == "Seguro medico"
    assert first.contract_type == "Jornada completa"
    assert first.schedule == "turno de tarde"
    assert first.workplace_type == "hybrid"


def test_dedup_does_not_overwrite_existing_fields(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")
    first.description_html = "<p>" + ("desc original " * 10) + "</p>"
    duplicate = _offer("jk1")
    duplicate.description_html = "<p>" + ("desc duplicada " * 10) + "</p>"

    unique = runner._dedup([first, duplicate])

    assert len(unique) == 1
    assert "desc original" in unique[0].description_html


def test_dedup_derives_snippet_from_merged_description(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")
    duplicate = _offer("jk1")
    duplicate.description_html = "<p>" + ("abc " * 30) + "</p>"
    duplicate.description_text = "texto de la descripcion"

    unique = runner._dedup([first, duplicate])

    assert unique[0].snippet == "texto de la descripcion"[:500]


def test_dedup_drops_offers_without_job_key(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")

    unique = runner._dedup([JobOffer(), first])

    assert unique == [first]


# --- Reparacion 2026-10-18: coherencia de is_remote al fusionar ---


def test_dedup_adopts_remote_signal_from_duplicate(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")
    duplicate = _offer("jk1")
    duplicate.workplace_type = "remote"
    duplicate.is_remote = True

    unique = runner._dedup([first, duplicate])

    assert unique == [first]
    assert first.workplace_type == "remote"
    assert first.is_remote is True


def test_dedup_never_degrades_existing_remote_signal(tmp_path):
    runner = _runner(tmp_path)
    first = _offer("jk1")
    first.is_remote = True
    duplicate = _offer("jk1")
    duplicate.workplace_type = "hybrid"
    duplicate.is_remote = False  # sin senal positiva: no degrada el True previo

    unique = runner._dedup([first, duplicate])

    assert unique == [first]
    assert first.workplace_type == "hybrid"
    assert first.is_remote is True
