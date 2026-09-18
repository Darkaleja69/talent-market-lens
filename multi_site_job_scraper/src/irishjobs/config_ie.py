"""Configuracion por defecto para IrishJobs.
Sobreescribible via CLI args.

Estrategia: nationwide primero (mas paginas, mas resultados),
luego city-specific para ofertas locales que no aparecen en nationwide.
"""
from __future__ import annotations

DEFAULT_CONFIG = {
    "site": "irishjobs",
    "country": "IE",
    "country_name": "Ireland",
    "base_url": "https://www.irishjobs.ie",
    "search_path": "/jobs/{role_slug}",
    "currency": "EUR",
    "locale": "en-GB",
    "timezone": "Europe/Dublin",
    "profile_name": "irishjobs",

    "roles": [
        "Data Analyst",
        "Data Analytics",
        "Data Scientist",
        "Data Science",
        "Data Engineer",
        "Analytics Engineer",
        "BI Analyst",
        "BI Developer",
        "Python Data",
        "SQL Analyst",
        "Machine Learning Engineer",
        "Data Architect",
        "Business Intelligence",
    ],

    "cities": {
        "Ireland (nationwide)": {"text": "", "nationwide": True},
        "Dublin":    {"text": "Dublin"},
        "Cork":      {"text": "Cork"},
        "Limerick":  {"text": "Limerick"},
        "Galway":    {"text": "Galway"},
        "Waterford": {"text": "Waterford"},
        "Sligo":     {"text": "Sligo"},
        "Drogheda":  {"text": "Drogheda"},
        "Dundalk":   {"text": "Dundalk"},
        "Kilkenny":  {"text": "Kilkenny"},
        "Athlone":   {"text": "Athlone"},
        "Navan":     {"text": "Navan"},
        "Wexford":   {"text": "Wexford"},
    },

    "date_filter": "7",
    "jobs_per_search": 50,
    "max_detail_jobs": 750,
    "max_total_jobs": 1000,
    "do_detail": True,
    "title_filter": True,
    "max_pages": 15,

    "delays": {
        "between_scrolls": [1.5, 3.0],
        "between_pages": [2.0, 5.0],
        "between_details": [3.0, 7.0],
        "between_searches": [5.0, 10.0],
        "scroll_step_px": [200, 400],
        "post_goto": [2.0, 4.0],
    },

    "output": {
        "csv": "data/irishjobs/output/jobs.csv",
        "parquet": "data/irishjobs/output/jobs.parquet",
        "checkpoint_dir": "data/irishjobs/checkpoints",
    },

    "retry": {
        "max_retries": 3,
        "retry_delay": [3.0, 8.0],
    },
}
