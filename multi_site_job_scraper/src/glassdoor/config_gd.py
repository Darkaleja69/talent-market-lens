"""Configuracion para Glassdoor (mercado Irlanda, glassdoor.ie).

Mercado (decision de la persona, 2026-10-12): el run del 2026-10-12 fallo
por watchdog durante las descripciones (ver reparacion en
`repairs/20261012-glassdoor/`); la busqueda pasa de EE. UU. a Irlanda y EE. UU.
deja de capturarse. Espana no entra en esta reparacion. Las rutas de salida no
cambian: el mercado sustituye, no anade.

Flujo (2026): el scraper obtiene las ofertas del endpoint GraphQL/BFF
(/graph) con paginacion por cursor. El SERP solo trae un resumen de la
descripcion; la descripcion COMPLETA se obtiene con JobDetailQuery
(1 llamada extra por oferta nueva, ver fetch_descriptions).
"""

DEFAULT_CONFIG = {
    "site": "glassdoor",
    "country": "IE",
    "country_name": "Ireland",
    "base_url": "https://www.glassdoor.ie",
    "search_path": "/Job/jobs.htm",  # SERP de .ie (sonda 2026-10-12)
    "currency": "EUR",
    "locale": "en-IE",
    "timezone": "Europe/Dublin",
    # Perfil persistente unico y caliente: el mercado lo determina el dominio
    # (base_url); no se abre un perfil por pais. Se mantiene "glassdoor".
    "profile_name": "glassdoor",
    "browser_type": "chromium",
    # Geolocalizacion coherente con el mercado (Dublin) para no mezclar
    # ficha del navegador de EE. UU. con el dominio .ie.
    "geolocation": {"latitude": 53.3498, "longitude": -6.2603},

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

    # Fallback si findPopularLocationAjax no responde: id/tipo del pais del
    # dominio. En .com el pais US es (1, "COUNTRY"); en .ie el id de Ireland
    # es (70, "COUNTRY"), confirmado con la sesion real del 2026-10-15 (estado
    # embebido del sitio y getJobAlertStatusQuery: locationId 70, COUNTRY).
    # La ruta de autocompletado esta retirada (404 en sesion real; 403 de
    # Cloudflare con el perfil del scraper el 2026-10-12), de modo que el
    # fallback cubre su retirada sin buscar en el pais equivocado.
    "location_fallback": {"id": 70, "type": "COUNTRY"},

    "date_filter": "7",       # solo ofertas de los ultimos 7 dias (fromAge)
    "jobs_per_search": 200,
    "max_detail_jobs": 0,
    "do_detail": False,
    "max_pages": 15,

    # Descripciones completas via JobDetailQuery (1 llamada por oferta nueva).
    # Habilitado: la descripcion es la fuente principal para extraer skills,
    # work_mode y experience_level en la capa de enriquecimiento (enrich()).
    # Presupuesto declarado: cubre de sobra el maximo de tarjetas por rol
    # (30 por pagina x 15 paginas = 450) y supera el target (2000 >= 1000),
    # por lo que no recorta cobertura. El timeout, los intentos y el tope de
    # fallos consecutivos son el endurecimiento ante un BFF degradado (T-35).
    "fetch_descriptions": True,
    "max_description_jobs": 2000,
    "description_timeout_ms": 30000,
    "description_attempts": 2,
    "max_description_consecutive_failures": 5,

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
