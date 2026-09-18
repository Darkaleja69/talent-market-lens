"""Configuracion para DevITJobs.nl (Paises Bajos).

Fuente: API JSON publica de DevITJobs.nl (no requiere navegador).
"""

DEFAULT_CONFIG = {
    "site": "devitjobs",
    "country": "NL",
    "country_name": "Netherlands",
    "base_url": "https://devitjobs.nl",
    "currency": "EUR",
    "locale": "nl-NL",
    "timezone": "Europe/Amsterdam",

    # Roles de datos (se usan para filtrar por titulo, ademas de techCategory).
    "roles": [
        "Data Analyst",
        "Data Analytics",
        "Data Scientist",
        "Data Science",
        "Data Engineer",
        "Analytics Engineer",
        "BI Analyst",
        "BI Developer",
        "Business Intelligence",
        "Machine Learning Engineer",
        "Data Architect",
        "Python Data",
        "SQL Analyst",
    ],

    # Categorias de tecnologia de DevITJobs que se incluyen siempre.
    "tech_categories": ["Data", "Machine-Learning"],
    # Incluir tambien ofertas cuyo TITULO case con los roles (aunque la
    # techCategory sea otra, p.ej. "Data Architect" dentro de Architect).
    "include_title_match": True,
    # Descarta titulos claramente no-data aunque su categoria sea "Data"
    # (la categoria de DevITJobs es a veces imprecisa).
    "exclude_title_patterns": [
        "java developer", "front-end", "frontend", "full stack", "fullstack",
        "full-stack", "php", ".net", "wordpress", "c#",
    ],

    # Antiguedad maxima de la oferta (dias) segun activeFrom/createdAt.
    # El tablon es pequeno (~200 ofertas), por eso 30 dias da mejor cobertura.
    "max_age_days": 30,

    # Detalle completo (1 llamada extra por oferta): descripcion, requisitos,
    # responsabilidades, perks, etc.
    "fetch_detail": True,
    "max_detail_jobs": 0,  # 0 = sin limite

    # Delays humanos entre llamadas a la API (evita patrones bruscos).
    "delays": {
        "between_requests": [0.6, 1.6],
        "between_details": [0.5, 1.4],
    },

    "retry": {
        "max_retries": 3,
        "retry_delay": [2.0, 5.0],
    },

    "output": {
        "csv": "data/devitjobs/output/jobs.csv",
        "parquet": "data/devitjobs/output/jobs.parquet",
        "checkpoint_dir": "data/devitjobs/checkpoints",
    },
}
