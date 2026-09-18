"""Configuracion para StepStone NL (Holanda).

Misma plataforma que IrishJobs. Solo cambian URLs, ciudades y locale.
Optimizada para conseguir 1000+ ofertas en roles de datos.
"""

DEFAULT_CONFIG = {
    "site": "stepstone_nl",
    "country": "NL",
    "country_name": "Netherlands",
    "base_url": "https://www.stepstone.nl",
    "search_path": "/vacatures/{role_slug}",
    "currency": "EUR",
    "locale": "nl-NL",
    "timezone": "Europe/Amsterdam",
    "profile_name": "stepstone_nl",

    "roles": [
        "Data Analyst",
        "Data Scientist",
        "Data Engineer",
        "Analytics Engineer",
    ],

    "cities": {
        "Netherlands":       {"text": "", "nationwide": True},
        "Amsterdam":         {"text": "Amsterdam"},
        "Rotterdam":         {"text": "Rotterdam"},
        "Utrecht":           {"text": "Utrecht"},
        "Eindhoven":         {"text": "Eindhoven"},
        "Den Haag":          {"text": "Den Haag"},
        "Groningen":         {"text": "Groningen"},
        "Maastricht":        {"text": "Maastricht"},
        "Leiden":            {"text": "Leiden"},
        "Nijmegen":          {"text": "Nijmegen"},
        "Tilburg":           {"text": "Tilburg"},
        "Arnhem":            {"text": "Arnhem"},
        "Haarlem":           {"text": "Haarlem"},
        "Enschede":          {"text": "Enschede"},
        "Delft":             {"text": "Delft"},
        "Breda":             {"text": "Breda"},
        "Apeldoorn":         {"text": "Apeldoorn"},
        "Zwolle":            {"text": "Zwolle"},
        "Amersfoort":        {"text": "Amersfoort"},
        "Den Bosch":         {"text": "Den Bosch"},
        "Almere":            {"text": "Almere"},
    },

    "date_filter": "7",
    "jobs_per_search": 25,
    "max_detail_jobs": 150,
    "max_total_jobs": 1000,
    "do_detail": True,
    "max_pages": 10,
    # StepStone NL busca por texto completo y devuelve titulos genericos:
    # se usa el filtro ampliado (senal de datos) en vez de la frase exacta.
    "title_filter": True,
    "title_filter_broad": True,

    "delays": {
        "between_scrolls": [1.0, 2.5],
        "between_pages": [1.5, 3.5],
        "between_details": [2.0, 6.0],
        "between_searches": [3.0, 8.0],
        "scroll_step_px": [200, 400],
        "post_goto": [1.5, 3.0],
    },

    "output": {
        "csv": "data/stepstone_nl/output/jobs.csv",
        "parquet": "data/stepstone_nl/output/jobs.parquet",
        "checkpoint_dir": "data/stepstone_nl/checkpoints",
    },

    "retry": {
        "max_retries": 3,
        "retry_delay": [3.0, 8.0],
    },
}
