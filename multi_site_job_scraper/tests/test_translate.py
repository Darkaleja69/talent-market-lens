"""Tests del traductor NL->EN (sin red: se mockea la llamada a Google)."""
from __future__ import annotations

import pandas as pd

from src.core.translate import Translator, _chunks, _looks_english


def test_looks_english():
    assert _looks_english("We are looking for a Data Engineer to join our team.")
    assert _looks_english("Data Analyst")
    assert _looks_english("Senior Data Engineer - Amsterdam")
    assert _looks_english("")
    assert not _looks_english(
        "Wij zoeken een data analist voor ons team in Amsterdam.")
    assert not _looks_english("Data Analist")
    assert not _looks_english("Ervaren beheerder gezocht")


def test_chunks_respeta_tamano():
    text = ". ".join(["frase de prueba"] * 500)
    parts = _chunks(text, size=200)
    assert all(len(p) <= 200 for p in parts)
    assert "".join(parts).replace(" ", "") != ""


def test_traduccion_categorica_y_texto():
    t = Translator(backend="google", cache_path=None)
    calls = []

    def fake_google(text):
        calls.append(text)
        return "TR:" + text

    t._google = fake_google  # type: ignore[assignment]
    df = pd.DataFrame({
        "work_mode": ["Hybride", "Remote"],
        "employment_type": ["Vast", "Full-Time"],
        "experience_level": ["Ervaren", "Senior"],
        "title": ["Data Analist", "Data Analyst"],
        "description_full": [
            "Wij bieden een uitstekend salaris en pensioen.",
            "We offer an excellent salary and pension.",
        ],
    })
    out = t.translate_dataframe(df)
    assert out["work_mode"].tolist() == ["Hybrid", "Remote"]
    assert out["employment_type"].tolist() == ["Permanent", "Full-Time"]
    assert out["experience_level"].tolist() == ["Experienced", "Senior"]
    # Titulo holandes -> traducido; titulo ingles -> intacto.
    assert out["title"].tolist() == ["TR:Data Analist", "Data Analyst"]
    # Descripcion holandesa -> traducida; inglesa -> intacta.
    assert out["description_full"].iloc[0].startswith("TR:")
    assert out["description_full"].iloc[1] == "We offer an excellent salary and pension."
    # Solo se llamo a Google para lo holandes (title[0] y description[0]).
    assert len(calls) == 2


def test_cache_evita_repetir():
    t = Translator(backend="google", cache_path=None)
    calls = []
    t._google = lambda text: (calls.append(text) or "TR:" + text)  # type: ignore
    assert t.translate_text("Wij zoeken een analist") == "TR:Wij zoeken een analist"
    assert t.translate_text("Wij zoeken een analist") == "TR:Wij zoeken een analist"
    assert len(calls) == 1
