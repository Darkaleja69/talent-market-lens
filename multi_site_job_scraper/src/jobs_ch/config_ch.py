"""Configuracion para jobs.ch (Suiza).

Nota (2026-08): el SERP de jobs.ch ignora el parametro "place" para filtrar
(devuelve la misma lista nacional). Por tanto el scraper usa una busqueda
nacional por rol y atribuye search_city comparando la ubicacion de cada
oferta con las ciudades configuradas (sin busquedas duplicadas por ciudad).
"""

DEFAULT_CONFIG = {
    "site": "jobs_ch",
    "country": "CH",
    "country_name": "Switzerland",
    "base_url": "https://www.jobs.ch",
    "search_path": "/en/vacancies/",
    "currency": "CHF",
    "locale": "en-GB",
    "timezone": "Europe/Zurich",
    "profile_name": "jobs_ch",

    "roles": [
        "Data Analyst",
        "Data Scientist",
        "Data Engineer",
        "Data Architect",
        "Business Intelligence",
        "Machine Learning Engineer",
    ],

    # Ciudades usadas para ATRIBUIR search_city por ubicacion (no para URL).
    "cities": {
        "Zurich":    {"text": "Zürich"},
        "Geneva":    {"text": "Genève"},
        "Basel":     {"text": "Basel"},
        "Bern":      {"text": "Bern"},
        "Lausanne":  {"text": "Lausanne"},
        "Luzern":    {"text": "Luzern"},
        "St_Gallen": {"text": "St. Gallen"},
        "Winterthur": {"text": "Winterthur"},
    },

    "jobs_per_search": 50,
    "max_detail_jobs": 50,
    "max_total_jobs": 0,
    "do_detail": True,
    "max_pages": 40,
    "date_filter": 7,

    "delays": {
        "between_scrolls": [1.5, 3.5],
        "between_pages": [2.0, 5.0],
        "between_details": [3.0, 8.0],
        "between_searches": [5.0, 12.0],
        "scroll_step_px": [200, 400],
        "post_goto": [2.0, 4.0],
    },

    "output": {
        "csv": "data/jobs_ch/output/jobs.csv",
        "parquet": "data/jobs_ch/output/jobs.parquet",
        "checkpoint_dir": "data/jobs_ch/checkpoints",
    },
}
