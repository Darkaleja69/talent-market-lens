"""Tests unitarios de parse_detail, models.normalize_experience_level y Store.

Verifica (sin navegador, sin red):
 - Regex de salario _RE_SALARY_IN_TEXT exige moneda o periodo (F2-12).
 - normalize_experience_level mapea textos comunes (F2-13).
 - _collect_skills extrae de un bloque de descripcion con bullets (F2-10).
 - Store.add hace dedup y enriquece; Store.already_processed y load_from_csv
   funcionan como base de reanudacion/idempotencia (F1-6, F1-7).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.models import (
    JobOffer,
    EXPERIENCE_LEVEL_MAP,
    normalize_experience_level,
    now_utc_iso,
)
from src.parse_detail import (
    _RE_SALARY_IN_TEXT,
    _collect_skills,
    _extract_salary_from_description,
)
from src.store import Store


# ---------- _RE_SALARY_IN_TEXT (F2-12) ----------

@pytest.mark.parametrize("text,expect_match", [
    # Salarios validos (deben matchear)
    ("Salario: 40.000-55.000 euros/año", True),
    ("€40.000 - €50.000 al año", True),
    ("$17.00/hora", True),
    ("Salary: $80,000 - $100,000 per year", True),
    ("45.000€ anual bruto", True),
    ("Rango salarial: 30000-40000 EUR / mes", True),
    # Falsos positivos a EVITAR (no deben matchear sin contexto de moneda/periodo)
    ("Equipo de 5-10 personas", False),
    ("Proyecto 2024-2025", False),
    ("Direccion: Calle Mayor, 25-30", False),
    ("Requisitos: 3-5 años de experiencia", False),
])
def test_salary_regex_requires_currency_or_period(text: str, expect_match: bool):
    """La regex de salario debe exigir moneda o periodo para evitar falsos
    positivos con rangos numericos que no son salario."""
    m = _RE_SALARY_IN_TEXT.search(text)
    if expect_match:
        assert m is not None, f"Se esperaba match en: {text!r}"
    else:
        assert m is None, (
            f"Falso positivo en: {text!r} -> matcheo: {m.group(0)!r}"
        )


def test_extract_salary_from_description_euros_year():
    smin_str = "40.000-55.000 euros/año"
    raw, smin, smax, cur, period = _extract_salary_from_description(smin_str)
    assert raw  # algo se extrae
    assert smin is not None and smin == 40000.0
    assert smax is not None and smax == 55000.0
    assert cur == "EUR"
    assert period == "year"


def test_extract_salary_from_description_symbol_dollar_hour():
    raw, smin, smax, cur, period = _extract_salary_from_description("$17.00/hora")
    assert raw
    assert smin == 17.0
    assert cur == "USD"
    assert period == "hour"


def test_extract_salary_from_description_empty():
    assert _extract_salary_from_description("") == ("", None, None, "", "")


def test_extract_salary_from_description_no_salary():
    """Texto sin info de salario no debe extraer nada."""
    raw, smin, smax, cur, period = _extract_salary_from_description(
        "Buscamos ingeniero con 3-5 años de experiencia."
    )
    assert raw == ""
    assert smin is None and smax is None


# ---------- normalize_experience_level (F2-13) ----------

@pytest.mark.parametrize("raw,expected", [
    ("Entry level", "Entry"),
    ("Associate", "Associate"),
    ("Mid-Senior level", "Mid-Senior"),
    ("Director", "Director"),
    ("Internship", "Practicas"),
    ("Prácticas", "Practicas"),
    ("", ""),
    ("Senior", "Mid-Senior"),  # alias comun
    ("NoNivelDesconocido", "NoNivelDesconocido"),  # passthrough limpio
])
def test_normalize_experience_level(raw: str, expected: str):
    assert normalize_experience_level(raw) == expected


def test_experience_level_map_covers_canonical_levels():
    """Los niveles canonicos aparecen como valores del mapa."""
    for canonical in ("Entry", "Associate", "Mid-Senior", "Director",
                      "Executive", "Practicas"):
        assert canonical in EXPERIENCE_LEVEL_MAP.values(), (
            f"Falta nivel canonico: {canonical}"
        )


# ---------- _collect_skills (F2-10) ----------

class _FakeQuerySelector:
    """Stab de Page para _collect_skills. query_selector devuelve None para
    forzar el path de regex sobre el texto de descripcion."""

    def query_selector(self, sel):
        return None


def test_collect_skills_from_description_bullets_es():
    desc = (
        "Buscamos un perfil con:\n"
        "Competencias:\n"
        "- Python\n"
        "* SQL\n"
        "• AWS\n"
        "- Comunicacion efectiva\n"
        "\n"
        "Ofrecemos: cafeteria y horario flexible."
    )
    skills = _collect_skills(_FakeQuerySelector(), desc)
    # Keyword extraction captura Python, SQL, AWS (en orden de la lista
    # curada DATA_SKILLS_KEYWORDS). "Comunicacion efectiva" no es keyword
    # tecnica, asi que se excluye correctamente.
    assert "Python" in skills
    assert "SQL" in skills
    assert "AWS" in skills
    assert "Comunicacion efectiva" not in skills


def test_collect_skills_from_description_en():
    desc = (
        "Skills:\n"
        "- Python\n"
        "- Pandas\n"
        "- Docker\n"
    )
    skills = _collect_skills(_FakeQuerySelector(), desc)
    assert skills == ["Python", "Pandas", "Docker"]


def test_collect_skills_no_section_returns_empty():
    desc = "Buscamos un ingeniero de datos. Ofrecemos buen salario."
    assert _collect_skills(_FakeQuerySelector(), desc) == []


def test_collect_skills_filters_long_and_numeric():
    """Filtra lineas largas (>80) o puramente numericas."""
    desc = (
        "Skills:\n"
        "- 12345\n"
        "- Python\n"
        "- " + ("x" * 200) + "\n"
    )
    skills = _collect_skills(_FakeQuerySelector(), desc)
    assert skills == ["Python"]


def test_collect_skills_caps_at_40(tmp_path):
    """Safety cap: no mas de 40 skills incluso si todas las keywords aparecen."""
    from src.models import DATA_SKILLS_KEYWORDS
    # Construir texto que contiene CADA keyword de la lista curada (62 total),
    # separadas por comas para que \b funcione.
    all_kws = ", ".join(DATA_SKILLS_KEYWORDS)
    desc = f"Requisitos tecnicos: {all_kws} y mas cosas."
    skills = _collect_skills(_FakeQuerySelector(), desc)
    assert len(skills) == 40  # cap at 40
    assert "Python" in skills


# ---------- Store: dedup, enriquecimiento, reanudacion (F1-6, F1-7) ----------

def _make_config(tmp_path: Path) -> dict[str, Any]:
    """Config minimo para Store apuntando a tmp_path."""
    return {
        "output": {
            "csv": str(tmp_path / "jobs.csv"),
            "parquet": str(tmp_path / "jobs.parquet"),
            "checkpoint_dir": str(tmp_path / "checkpoints"),
        }
    }


def _make_offer(job_id: str, with_detail: bool = False) -> JobOffer:
    o = JobOffer(
        job_id=job_id,
        job_url=f"https://linkedin.com/jobs/view/{job_id}/",
        title=f"Title {job_id}",
        company_name="ACME",
        search_role="Data Analyst",
        search_city="Madrid",
        source="local",
        scraped_at=now_utc_iso(),
    )
    if with_detail:
        o.description_full = "Una descripcion detallada de la oferta."
        o.experience_level = "Mid-Senior"
        o.skills = ["Python", "SQL"]
    return o


def test_store_add_new_offer(tmp_path):
    store = Store(_make_config(tmp_path))
    assert len(store) == 0
    assert store.add(_make_offer("100")) is True
    assert len(store) == 1


def test_store_add_dedup_same_job_id(tmp_path):
    store = Store(_make_config(tmp_path))
    store.add(_make_offer("100"))
    # Misma job_id sin detalle: se descarta como duplicada
    assert store.add(_make_offer("100")) is False
    assert len(store) == 1


def test_store_enriches_existing_when_new_has_detail(tmp_path):
    """Si llega una version con detalle de una ya almacenada sin detalle,
    se enriquece preservando el origen original."""
    store = Store(_make_config(tmp_path))
    base = _make_offer("100", with_detail=False)
    store.add(base)
    # Llega el mismo job_id con detalle
    enriched = _make_offer("100", with_detail=True)
    store.add(enriched)
    final = store.all()[0]
    # Origen preservado
    assert final.search_role == "Data Analyst"
    assert final.source == "local"
    # Detalle aplicado
    assert final.description_full != ""
    assert final.experience_level == "Mid-Senior"
    assert final.skills == ["Python", "SQL"]


def test_store_state_load_populates_processed_ids(tmp_path):
    """Store.__init__ lee state.json y marca job_ids como procesados."""
    cfg = _make_config(tmp_path)
    # Pre-crear state.json con job_ids
    state_p = Path(cfg["output"]["checkpoint_dir"]) / "state.json"
    state_p.parent.mkdir(parents=True, exist_ok=True)
    state_p.write_text(json.dumps({
        "total": 2,
        "job_ids": ["111", "222"],
        "updated_at": "2026-07-16T10:00:00+00:00",
    }), encoding="utf-8")
    store = Store(cfg)
    assert store.already_processed("111")
    assert store.already_processed("222")
    assert not store.already_processed("333")


def test_store_load_from_csv(tmp_path):
    """--append carga un CSV previo en el store."""
    cfg = _make_config(tmp_path)
    store = Store(cfg)
    store.add(_make_offer("100", with_detail=True))
    store.add(_make_offer("101"))
    store.write_csv()
    # Nuevo store, simula la siguiente run con --append
    store2 = Store(cfg)
    # state.json tambien fue escrito por checkpoint; cargaria 100 y 101 como
    # processed. Para aislar el test de load_from_csv, lo ignoramos y
    # forzamos la carga solo del CSV.
    store2._processed_ids.clear()
    n = store2.load_from_csv()
    assert n == 2
    assert len(store2) == 2
    assert "100" in {o.job_id for o in store2.all()}


def test_store_load_from_csv_missing_returns_zero(tmp_path):
    """Si no existe CSV previo, load_from_csv devuelve 0 sin error."""
    store = Store(_make_config(tmp_path))
    assert store.load_from_csv() == 0


def test_store_warning_counter_starts_zero(tmp_path):
    """Nuevo Store arranca con contador de warnings en 0."""
    store = Store(_make_config(tmp_path))
    assert store.warning_count == 0
    store.warning_inc("test")
    store.warning_inc("test2")
    assert store.warning_count == 2


def test_collect_skills_normalizes_aliases():
    """PowerBI -> Power BI, Google Cloud -> GCP, Scikit learn -> Scikit-learn."""
    desc = "Buscamos perfil con experiencia en PowerBI, Google Cloud y Scikit learn."
    skills = _collect_skills(_FakeQuerySelector(), desc)
    assert "Power BI" in skills
    assert "GCP" in skills
    assert "Scikit-learn" in skills


def test_collect_skills_non_keywords_not_extracted():
    """Palabras comunes o de negocio no deben aparecer como skills."""
    desc = "Ofrecemos horario flexible, buen ambiente de trabajo y crecimiento."
    skills = _collect_skills(_FakeQuerySelector(), desc)
    # Ninguna de las frases anteriores esta en DATA_SKILLS_KEYWORDS
    assert skills == []


def test_collect_skills_dedups_across_methods():
    """Una keyword que aparece multiples veces solo se cuenta una."""
    desc = "Python, Python, Python es necesario. Tambien SQL y SQL Server."
    skills = _collect_skills(_FakeQuerySelector(), desc)
    assert skills.count("Python") == 1
    assert "SQL" in skills


# ---------- Snapshots de auth_detail.html (opcional) ----------

def test_auth_detail_fixture_has_expected_hooks():
    """Smoke test sobre el snapshot auth_detail.html (si esta disponible)."""
    p = Path(__file__).resolve().parent / "fixtures" / "auth_detail.html"
    if not p.exists() or p.stat().st_size < 1000:
        pytest.skip("No se encontro fixture auth_detail.html.")
    html = p.read_text(encoding="utf-8", errors="replace")
    # Hooks estables que usamos en parse_detail
    assert 'data-testid="expandable-text-box"' in html or "expandable-text-box" in html
    assert "JobDetails_AboutTheJob" in html or "componentkey" in html