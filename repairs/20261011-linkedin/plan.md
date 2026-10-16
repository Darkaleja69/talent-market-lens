<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  linkedin    id de la fuente en inglés (p. ej. infojobs)
  2026-10-11  fecha del run de origen, YYYY-MM-DD
  repair/linkedin-20261011    rama del fix, repair/<fuente>-<YYYYMMDD>
  planificado    estado del registro: planificado al crearlo; probado,
                descartado o escalado al terminar

Actualización sin perder lo escrito (T-14). Cada sección está delimitada por
dos comentarios HTML estables, invisibles al renderizar:

  - apertura: un comentario HTML cuyo contenido es  record:section:<id>
  - cierre:   un comentario HTML cuyo contenido es  /record:section:<id>

`complete_record` localiza ambos comentarios de la sección que actualiza y
sustituye solo el contenido que queda entre ellos; el resto del fichero se
conserva tal cual. No cambies ni reordenes los marcadores: la actualización es
determinista y depende de ellos. Los ids de sección, en inglés, son: source,
failure, evidence, investigation, changes, tests, live_test, quality, result.

Idioma y saneado: los ids de fuente, los códigos de máquina y los nombres de
fichero van en inglés; el texto para la persona, en español. No incluyas
credenciales, tokens ni rutas de perfiles de navegador (RF-11): las rutas
locales de usuario se escriben como <HOME>.
-->

# Reparación linkedin — run 2026-10-11

<!-- record:section:source -->
## Fuente y run

- **Fuente:** linkedin
- **Run de origen:** 2026-10-11
- **Rama del fix:** repair/linkedin-20261011
- **Registro creado:** 2026-10-11
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** secondary (investigación de completitud) — tipo: direct
- **Trigger:** required_field_below_target
- **Campo:** `description` (90.2 %)
- **Resumen:** required field 'description' below target (90.15073301672517%)
- **Ofertas del run:** 690
- **Ofertas del snapshot:** 9686 (agregado; alcance: historical_snapshot)
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: ok (subidos=1, rechazados=0)»
  - «resultado de la ejecución: correcto (ok)»
  - «detalle: stdout=nightly_stdout_attempt1.log»
  - «intento 1/5»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `linkedin_jobs_scraper/data/nightly_stdout_attempt1.log`
  - `linkedin_jobs_scraper/data/output/jobs.parquet`
  - `linkedin_jobs_scraper/data/run.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-11.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.3 — LinkedIn - historical description and optional fields
- **Receta:** About 970 rows without description with scraped_at <= 17-09-2026; local coverage is ~100% since 18-09; salary 16.3% (regex) and work_mode 42.4%. Check whether the SDUI detail or the guest API expose structured salary/workplace and whether the store retries rows without description. Backfill the history (or justify why not) and extract salary/work_mode when the site exposes them.
- **Alcance de prueba propuesto:** 1 role x 1 city in guest mode
- **Restricciones:** no Azure upload; no landing update; no merge
- **Pendiente:** comprobar robots.txt y términos de uso, separar hechos de hipótesis y confirmar la causa probable (RF-3).
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **Causa raíz atendida (doble):** (1) **Origen:** los runs anteriores al
  18-09-2026 tuvieron una fase de detalle parcialmente fallida que persistió
  filas solo-SERP; los intentos que terminaban **vacíos no dejaban rastro**
  (las 2 filas del 10-10 se intentaron y quedaron sin descripción sin ningún
  error ni aviso). (2) **Fijación:** el flujo solo reintenta el detalle de una
  oferta **si vuelve a aparecer en el SERP del combo** dentro de su ventana
  (7 días ES / 30 días IE-NL) y sus ~50 tarjetas; al caer fuera, las ~950 filas
  quedaron congeladas para siempre. Evidencia: `evidence/investigation-report.md`
  (§1, §3) y `evidence/run_2026-10-11_extract.log`.
- **Justificación de no-backfill:** la vía de recuperación del histórico
  (1 GET por `job_id` a `jobPosting/<id>` para 954 filas) está **deshabilitada
  por `robots.txt` y los TOS** de LinkedIn (`User-agent: *` → `Disallow: /`;
  User Agreement §8.2 y Crawling Terms prohíben el scraping sin permiso
  expreso). Evidencia: `evidence/robots_linkedin_extract_2026-10-11.txt`,
  `evidence/robots_linkedin_2026-10-11.txt` y
  `evidence/tos_linkedin_extract_2026-10-11.txt`. Cifras del snapshot (9686):
  **954 filas sin descripción, 950 con `scraped_at` ≤ 17-09-2026** (813 del
  17-09) según `evidence/parquet_analysis_2026-10-11.txt`. No se ejecutó
  ninguna petición nueva a rutas de ofertas; la decisión de solicitar permiso
  (`whitelist-crawl@linkedin.com`) o usar una vía licenciada queda para la
  persona (informe §8).
- **Garantía a partir de ahora:** ninguna descripción vacía pasa desapercibida
  (WARNING + contador `detail_desc_empty` + resumen del run) y las pendientes
  se reintentan **mientras su ventana siga abierta** (máx. 3 intentos,
  presupuesto 25/run por defecto), sin rutas/endpoints nuevos y con volumen
  acotado (≤ 25 GET/run al endpoint de detalle ya usado).
- **Módulos y funciones:**
  - `src/main.py`: `_scrape_one_local` registra cada detalle sin descripción
    (`log.warning`, `store.warning_inc("detail_desc_empty")`,
    `store.pending_detail_add`) y devuelve su contador; `_print_summary` imprime
    «Detalles sin descripcion (telemetria)». Nuevas `_pending_window_days`,
    `_parse_iso_utc`,     `_select_pending_for_retry` (pura: descripción vacía +
    ventana por ciudad + `attempts < 3` + más antigua primero + presupuesto) y
    `_retry_pending_details` (solo guest, tras el barrido de combos; limpia al
    inicio los pendientes ya recuperados por otra vía; éxito →
    `pending_detail_remove` + PROGRESS; fallo → `pending_detail_increment` +
    WARNING; bloqueo → checkpoint y aborto de la fase, sin incrementar;
    checkpoint final).
  - `src/store.py`: `detail_pending` en `state.json`
    (`{job_id: {first_seen, attempts}}`), carga retrocompatible y helpers
    `detail_pending`, `pending_detail_get/add/increment/remove`.
  - `src/parse_detail.py`: guarda de dígitos en
    `_extract_salary_from_description` (sin dígito no hay importe: fuera
    `", libra"`, `". €"`, `"., Euro"`) e inferencia multilingüe
    (`\bhybrid(e|es)?\b`; `\bthuiswerk(en)?\b` → remoto, con límites de palabra
    para no marcar compuestos como «thuiswerkvergoeding»).
  - `src/search.py`: ante HTTP 429 se honra `Retry-After` (segundos, tope
    60 s) y se reintenta de forma acotada (máx. 2) con backoff + jitter;
    agotado, devuelve `""` como antes. El ritmo no sube (≤ 30 req/min).
  - `tools/recompute_derived.py` (nuevo): recomputo local sin red de los
    derivados del snapshot (`salary_raw` vacío o sin dígito; `work_mode`
    vacío), con copia previa de `jobs.csv`/`jobs.parquet` a `backup-<ts>-...`,
    informe antes/después y recuento de filas invariante.
- **Dependencias nuevas:** ninguna (biblioteca estándar y stack existente;
  pytest solo para tests).
- **Nivel §6.0:** se mantiene el **nivel 1** (endpoint guest por HTTP y
  parseo) con ritmo humano (delays, ≤ 30 req/min); **sin escalar** a sesión
  navegada ni credenciales (la señal del run es 429 de ritmo, no un challenge).
  Sin solvers, proxies de pago ni rutas/endpoints nuevos; la extracción JSON-LD
  queda reservada a una segunda ronda con fixture guest real.
- **Alcance de campos:** `salary` y `work_mode` se mejoran hasta lo que el
  portal exponga, **sin prometer el 100 %** (no siempre publica salario ni
  modalidad); `skills` es derivado de la descripción y no se exige como campo.
- **Recomputo local (una vez, sin red):** 9686 filas antes/después (sin
  cambios); `salary_raw` válido 1670 → 2339 (+669 completados), inválidos
  133 → 0 (limpiados); `work_mode` 4129 → 4752 (+623 recuperados). Copias
  previas en `data/output/backup-20261011_204449-jobs.*` (fuera de git).
  Evidencia: `evidence/derived-recompute-20261011.txt`.
- **Calidad (reconciliación de `quality_before.json`):** siguiendo el
  precedente de Indeed, `salary`, `work_mode` y `skills` quedan **sin meta**
  (`target_pct: null`) porque el portal no los publica de forma fiable y su
  ausencia no es fallo; se comparará la cobertura obtenida sin regresión
  (tolerancia 5 pp). `description` mantiene meta 100 % (obligatoria y foco) y
  `id`, `title`, `company`, `location` y `posted_date` siguen a 100 %.
  `context.json` conserva el brief original (metas teóricas al 100 %); el
  perfil efectivo de la puerta es el `quality_before.json` reconciliado.
- **Nota de proceso:** el registro se actualizó por edición directa de esta
  sección porque `python -m repair.cli record --record ... --changes ...`
  exige también `--status` y `--result` (estados finales) y la prueba en vivo
  aún no se ha ejecutado; el texto íntegro de cambios y tests queda en
  `evidence/record-changes-20261011.md` y `evidence/record-tests-20261011.md`
  para la completación final tras la prueba en vivo.
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

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
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

Alcance definitivo y comparable (§9): **1 rol × 1 ciudad × 50 tarjetas** en modo
guest (volumen por defecto del config para Madrid); sin sesión navegada ni CDP
(no aplica en guest). Umbral exigido: **1** (sin historial de `linkedin`).

- **Comando:** `python -m src.main --guest --role "AI Engineer" --city Madrid --max-jobs-per-search 50 --no-apify --append` (desde `linkedin_jobs_scraper/`).
- **Iteración de medición:** la ronda 1 (10 tarjetas, muestra n=9) dio puerta NO
  OK por regresión de `work_mode` (-9,3 pp); la sonda de detalle
  (`evidence/live-sample-detail-probe-20261011.txt`) demostró que el portal no
  publica modalidad en esas filas (0 señales, 0 JSON-LD), así que el problema
  era el tamaño de muestra. La ronda 2 usa el alcance definitivo (50 tarjetas,
  muestra n=34).
- **Recuento (ronda 2):** inicio `2026-10-11T20:01:01+00:00`, fin ~`20:05:50Z`
  (4,7 min), **exit 0**; 50 tarjetas (5 páginas × 10), 50 ofertas base (34
  nuevas; 15 detalles saltados por ya procesados), **35/35 detalles
  completados**, 0 descripciones vacías, fase de reintento de pendientes sin
  candidatos, 0 bloqueos, 0 desafíos y 0 HTTP 429; store total 9729.
- **Muestra y puerta:** 34 ofertas únicas
  (`evidence/live-test-sample-20261011b.parquet`) ≥ umbral 1; `quality.py` →
  **OK** (exit 0): obligatorios al 100 %; `description` 90,2 → 100 %,
  `work_mode` 42,6 → 61,8 %, `skills` 82,1 → 97,1 %, `salary` 17,8 → 14,7 %
  (dentro de tolerancia); sin regresión. Veredicto en `quality_after.json`;
  ronda 1 conservada en `evidence/quality-after-run1-20261011.json`.
- **Restricciones:** sin subida a Azure, sin landing, sin merge; sin `--limit`;
  perfil guest del scraper intacto; ninguna acción de cuenta.
- **Evidencia:** `evidence/linkedin-20261011-live-test.txt` (extracto de ambas
  rondas), `evidence/linkedin-20261011-quality-gate.txt` (rondas 2 y 1),
  fixture saneada
  `linkedin_jobs_scraper/tests/fixtures/guest_detail_live_20261011.html` y
  captura completa `linkedin_jobs_scraper/data/live_test_20261011b.log` (fuera
  de git).
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` → `quality_after.json`):

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| title | sí | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| company | sí | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| description | sí | 90.2 % | 100.0 % | +9.8 pp | 100.0 % | cumple |
| salary | no | 17.8 % | 14.7 % | -3.1 pp | sin meta | no aplica |
| skills | no | 82.1 % | 97.1 % | +14.9 pp | sin meta | no aplica |
| work_mode | no | 42.6 % | 61.8 % | +19.1 pp | sin meta | no aplica |
| location | no | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| posted_date | no | 96.4 % | 100.0 % | +3.6 pp | 100.0 % | cumple |

Veredicto de `quality.py`: **OK**.
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** Reparación probada: 34 ofertas verificadas (muestra ≥ umbral 1) en la prueba en
vivo acotada (AI Engineer × Madrid, modo guest, 1 rol × 1 ciudad × 50
tarjetas), con obligatorios al 100 % y puerta de calidad OK, sin regresión
(description 90,2 → 100 %, work_mode 42,6 → 61,8 %, skills 82,1 → 97,1 %;
salary 17,8 → 14,7 %, dentro de tolerancia). Histórico de 954 filas sin
descripción: sin backfill, justificación registrada (robots.txt/TOS).
- **Ofertas verificadas:** 34
- **Validación del push:** validada por la persona (RF-12)
- **Estado:** probado
<!-- /record:section:result -->
