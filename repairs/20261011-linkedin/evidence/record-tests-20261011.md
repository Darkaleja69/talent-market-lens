Desde `linkedin_jobs_scraper/`:

- `python -m pytest tests -q` → **152 passed** (104 antes de T-33). Tests
  nuevos: `tests/test_store_pending.py` (helpers `detail_pending`,
  persistencia en `state.json` y carga retrocompatible con la clave ausente o
  entradas rotas), `tests/test_main_pending_retry.py` (ventana por ciudad
  7/30 días con tope, selección pura con presupuesto/orden/attempts,
  recuperación con PROGRESS, fallo con WARNING y tope de intentos, aborto si la
  página está cerrada), `tests/test_search_backoff.py` (429 con y sin
  `Retry-After`, tope de 60 s, reintento acotado y no-429 sin reintentos) y
  `tests/test_recompute_derived.py` (regla «solo si falta o es inválido»,
  salario válido intacto, limpieza de inválidos, relleno multilingüe, filas
  sin descripción y `skills` intactas, flujo completo con copias y recuento
  invariante). En `tests/test_parse_detail.py`: capturas sin dígito
  (`", libra"`, `". €"`, `"., Euro"`) no generan salario y la modalidad cubre
  ES/EN/NL/DE sin falsos positivos graves.

Desde la raíz del repositorio:

- `python -m pytest scrapers-pipeline/tests -q` → **1126 passed** (incluye la
  auditoría de higiene de `repairs/`).

Todo offline, sin red ni navegador. La prueba en vivo (1 rol × 1 ciudad guest)
la coordina el orquestador después; quedan pendientes el umbral y la puerta de
calidad.
