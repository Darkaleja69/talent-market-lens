"""Configuracion para Glassdoor (USA/ES).

Flujo (2026): el scraper obtiene las ofertas del endpoint GraphQL/BFF
(/graph) con paginacion por cursor. El SERP solo trae un resumen de la
descripcion; la descripcion COMPLETA se obtiene con JobDetailQuery
(1 llamada extra por oferta nueva, ver fetch_descriptions).
"""

DEFAULT_CONFIG = {
    "site": "glassdoor",
    "country": "US",
    "country_name": "United States",
    "base_url": "https://www.glassdoor.com",
    "search_path": "/Job/jobs.htm",
    "currency": "USD",
    "locale": "en-US",
    "timezone": "America/New_York",
    "profile_name": "glassdoor",
    "browser_type": "chromium",

    "roles": [
        "Data Analyst",
        "Data Scientist",
        "Data Engineer",
        "Analytics Engineer",
        "Machine Learning Engineer",
        "Business Intelligence",
    ],

    # Ubicacion objetivo (nombre para resolve_location). Vacio = country_name.
    "location_name": "",

    "date_filter": "7",       # solo ofertas de los ultimos 7 dias (fromAge)
    "jobs_per_search": 200,
    "max_detail_jobs": 0,
    "do_detail": False,
    "max_pages": 15,

    # Descripciones completas via JobDetailQuery (1 llamada por oferta nueva).
    # Habilitado: la descripcion es la fuente principal para extraer skills,
    # work_mode y experience_level en la capa de enriquecimiento (enrich()).
    "fetch_descriptions": True,
    "max_description_jobs": 2000,

    "delays": {
        "between_scrolls": [1.0, 2.0],
        "between_pages": [2.0, 4.0],
        "between_details": [3.0, 6.0],
        "between_searches": [6.0, 10.0],
        "scroll_step_px": [200, 400],
        "post_goto": [2.0, 4.0],
    },

    "output": {
        "csv": "data/glassdoor/output/jobs.csv",
        "parquet": "data/glassdoor/output/jobs.parquet",
        "checkpoint_dir": "data/glassdoor/checkpoints",
    },
}