Desde `linkedin_jobs_scraper/`:

- `python -m pytest tests -q` → **156 passed** (104 antes de T-33; +52
  nuevos). Tests
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
  ES/EN/NL/DE sin falsos positivos graves. Añadido
  `tests/test_guest_detail_live_fixture.py` (2 tests sobre la fixture guest
  real `tests/fixtures/guest_detail_live_20261011.html`: descripción >1.000
  caracteres, los 3 criterios leídos y `work_mode` vacío sin señal de
  modalidad).

Desde la raíz del repositorio:

- `python -m pytest scrapers-pipeline/tests -q` → **1126 passed** (incluye la
  auditoría de higiene de `repairs/`).

Todo offline, sin red ni navegador. La prueba en vivo (1 rol × 1 ciudad × 50
tarjetas, guest) se completó con la puerta de calidad en OK (ver «Pruebas:
prueba en vivo»).
