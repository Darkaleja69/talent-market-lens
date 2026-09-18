from src.irishjobs.config_ie import DEFAULT_CONFIG
from src.irishjobs.title_filter import (
    filter_cards,
    is_broad_data_title,
    is_relevant_title,
    normalize_title,
    phrases_from_roles,
)

PHRASES = phrases_from_roles(DEFAULT_CONFIG["roles"])


def _relevant(title: str) -> bool:
    return is_relevant_title(title, PHRASES)


def test_titles_data_que_si_pasan():
    validos = [
        "Senior Data Analyst",
        "Data Analyst - Group Internal Audit",
        "Workplace Solutions - Marketing Data Analyst, Vice President",
        "Business Data Scientist, gUP",
        "Data Engineer",
        "Analytics Engineer",
        "AI Engineer",
        "AI and Data Analytics Specialist",
        "Machine Learning Engineer",
        "ML Engineer",
        "BI Analyst",
        "BI Developer",
        "Power BI Developer",
        "SQL Analyst",
        "Data Architect",
        "Business Intelligence Developer",
        "Data Analyst (SQL)",
        "Data-Analyst / BI",
    ]
    for t in validos:
        assert _relevant(t), f"deberia pasar: {t!r}"


def test_titles_no_data_que_no_pasan():
    invalidos = [
        "Operations Analyst",
        "Electronic Health Record (EHR) Application Support Analyst",
        "Financial Analyst",
        "Marketing Manager",
        "Data Protection Analyst",
        "Data Governance Analyst",
        "Principal Clinical Data Standards Consultant",
        "Data Entry Clerk",
        "Customer Support Analyst",
        "",
    ]
    for t in invalidos:
        assert not _relevant(t), f"no deberia pasar: {t!r}"


def test_normalize_title():
    assert normalize_title("Data Analyst - Group Internal Audit") == \
        "data analyst group internal audit"
    assert normalize_title("AI/ML Engineer") == "ai ml engineer"
    assert normalize_title("") == ""


def test_phrases_from_roles_incluye_roles_y_extra():
    assert "data analyst" in PHRASES
    assert "business intelligence" in PHRASES
    assert "ai engineer" in PHRASES
    assert "ml engineer" in PHRASES


def test_filter_cards_devuelve_kept_y_dropped():
    cards = [
        {"job_id": "1", "title": "Data Analyst"},
        {"job_id": "2", "title": "Operations Analyst"},
        {"job_id": "3", "title": "Senior Data Engineer"},
    ]
    kept, dropped = filter_cards(cards, PHRASES)
    assert [c["job_id"] for c in kept] == ["1", "3"]
    assert dropped == 1


def test_filter_cards_phrases_vacias_no_filtra():
    cards = [{"job_id": "1", "title": "Operations Analyst"}]
    assert filter_cards(cards, []) == (cards, 0)
    assert filter_cards([], PHRASES) == ([], 0)


def test_broad_mode_acepta_senal_data_y_rechaza_ruido():
    # StepStone NL sirve resultados genericos: el modo ampliado rescata los
    # titulos con senal de datos y descarta el ruido evidente.
    validos = [
        "Senior Strategic Insights Analyst",
        "Data Engineer",
        "Power BI Developer",
        "Business Intelligence Analyst",
    ]
    invalidos = [
        "FP&A Analyst",
        "Onsite Associate, Lab Services",
        "Data Entry Clerk - Football",
        "Warehouse Technician",
        "Buyer",
        "Marketing Intern",
        "HR adviseur Warehouse",
    ]
    for t in validos:
        assert is_broad_data_title(t), f"deberia pasar (broad): {t!r}"
    for t in invalidos:
        assert not is_broad_data_title(t), f"no deberia pasar (broad): {t!r}"


def test_filter_cards_broad_ignora_phrases():
    cards = [
        {"job_id": "1", "title": "Data Engineer"},
        {"job_id": "2", "title": "Warehouse Technician"},
        {"job_id": "3", "title": "FP&A Analyst"},
    ]
    kept, dropped = filter_cards(cards, PHRASES, broad=True)
    assert [c["job_id"] for c in kept] == ["1"]
    assert dropped == 2