"""Configuracion para Nationale Vacaturebank (Paises Bajos).

Los textos se guardan en holandes (idioma de origen); la traduccion a ingles
se aplica en el merge (src/core/translate.py).
"""

DEFAULT_CONFIG = {
    "site": "nvb",
    "country": "NL",
    "country_name": "Netherlands",
    "base_url": "https://www.nationalevacaturebank.nl",
    "api_url": "https://api.nationalevacaturebank.nl/api/jobs/v3/sites/nationalevacaturebank.nl/jobs",
    "currency": "EUR",
    "locale": "nl-NL",
    "timezone": "Europe/Amsterdam",
    # Akamai exige cookies de sesion -> se usa navegador (patchright).
    "profile_name": "nvb",

    # Consultas (los terminos holandeses amplian cobertura).
    "queries": [
        "data analyst",
        "data analist",
        "data engineer",
        "data scientist",
        "data science",
        "analytics engineer",
        "business intelligence",
        "bi analyst",
        "bi developer",
        "machine learning",
        "data architect",
        "power bi",
        "etl",
    ],

    # Filtro de titulo (ampliado: los titulos holandeses no casan la frase exacta).
    "title_filter": True,
    "title_filter_broad": True,

    # Antiguedad maxima (dias) segun startDate.
    "max_age_days": 30,
    # Paginacion (la API acepta limit hasta 100).
    "page_size": 100,
    "max_pages_per_query": 10,
    "max_total_jobs": 3000,

    "delays": {
        "between_requests": [1.5, 3.5],
        "post_goto": [1.0, 2.0],
    },
    "retry": {
        "max_retries": 3,
        "retry_delay": [2.0, 5.0],
    },

    "output": {
        "csv": "data/nvb/output/jobs.csv",
        "parquet": "data/nvb/output/jobs.parquet",
        "checkpoint_dir": "data/nvb/checkpoints",
    },
}
