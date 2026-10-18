<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  indeed    id de la fuente en inglés (p. ej. infojobs)
  2026-10-18  fecha del run de origen, YYYY-MM-DD
  repair/indeed-20261018    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación indeed — run 2026-10-18

<!-- record:section:source -->
## Fuente y run

- **Fuente:** indeed
- **Run de origen:** 2026-10-18
- **Rama del fix:** repair/indeed-20261018
- **Registro creado:** 2026-10-18
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** primary (fuente fallida) — tipo: direct
- **Resultado (outcome):** ok
- **Resumen:** ok: run completed (1 diagnostic failure reason(s))
- **Motivos del diagnóstico:** «campo obligatorio 'description' por debajo del umbral»
- **Ofertas del run:** 116
- **Ofertas del snapshot:** no aplica
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: ok (subidos=2, rechazados=0)»
  - «resultado de la ejecución: correcto (ok)»
  - «detalle: metadata=scrape_metadata_20261018_0219.json finished_at=2026-10-18T04:09:32.052006»
  - «intento 1/2»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `indeed_jobs_scraper/output/scrape_metadata_20261003_0006.json`
  - `indeed_jobs_scraper/output/scrape_metadata_20261003_1247.json`
  - `indeed_jobs_scraper/output/nightly_stderr_IE_attempt1.log`
  - `indeed_jobs_scraper/output/log_20261003_*.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-18.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.2 — Indeed - description at 0% and second page
- **Receta:** enrich_success is 0/53-0/72: the right-panel wait times out and the code continues before the /viewjob fallback; page 2 asks for login and --limit triggers Cloudflare. Check the real panel, /viewjob with the real session, page-2 behaviour and mosaic-data. Reorder the fallback, update selectors, emit progress during enrichment and resolve or document login and --limit.
- **Alcance de prueba propuesto:** 1 country x 1 term x 1 page
- **Restricciones:** no Azure upload; no landing update; no merge
- **Investigación completada:** 2026-10-18 (offline + navegador propio; sin ejecutar el scraper).
- **Causa raíz (combinada):** 13 de las 14 descripciones perdidas las saltó el muestreo `rate=0.9` (`int()` + mínimo forzado saltan 1-2 candidatas por SERP pequeña); 1 clic agotó su timeout sin reintento (`jk=3a8c91f14177f18a`); 4 duplicados enriquecidos después no llegaron al parquet porque `_dedup` no fusionaba campos. El panel derecho y el auto-open sí traen descripción de todas las ofertas observadas.
- **Auditoría de campos:** `salary` = exactamente lo publicado por el portal (36,2 %; el resto es "Pay information not provided"); `work_mode` parcial y con `REMOTE_HYBRID` mal mapeado a `remote`; `skills` no existe como campo publicado (se deriva de la descripción en Databricks).
- **Meta de calidad propuesta:** `description` 100 % del alcance (obligatoria); `salary` y `work_mode` sin meta (`target_pct: null`) + regla de no-regresión (el portal solo los publica parcialmente); `skills` sin meta.
- **Evidencias y detalle:** `evidence/investigation-report.md` (§3 causa raíz, §4 auditoría, §6 cambio recomendado) y `evidence/indeed-20261018-*.txt|md|csv` (aritmética del enrich, extracto del log, 14 filas perdidas, observaciones en vivo y robots/TOS).
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **`scraper/parser.py`**
  - Enriquecimiento: con `rate >= 1.0` se intentan todas las candidatas (sin
    recorte aleatorio); con `rate < 1` se registran las saltadas (INFO); al
    morder `max_enrich` se emite WARNING con las pendientes y el recorte es
    determinista (prefijo en orden de tarjetas).
  - Reintento de clic (`_click_card_with_retry`): 2 intentos (5 s; si falla,
    scroll + espera 1–1,9 s + 10 s); el `jk` solo se registra como fallido tras
    agotar ambos y no se cosechan condiciones de otra oferta. Cierre
    best-effort del banner OneTrust antes del bucle.
  - `work_mode`: HYBRID se comprueba antes que REMOTE (`REMOTE_HYBRID` →
    `hybrid`, `REMOTE_WORK_FROM_HOME`/`REMOTE` → `remote`); `remoteWorkModel.text`
    como respaldo; heurístico solo como último recurso, sin pisar un modelo
    explícito.
  - `job_type`: `translatedAttributes` primero; si no aporta, primer `jobTypes[]`
    (string u objeto). Sin cambio de esquema.
- **`scraper/runner.py`**: `_dedup` fusiona campos del duplicado cuando el
  objeto conservado los tiene vacíos (`description_html/text`, `snippet`,
  `benefits`, `contract_type`, `schedule`, `workplace_type`) y adopta la señal
  remota positiva (`is_remote`) sin degradarla; deriva `snippet`. Cubre los 4
  duplicados enriquecidos después (cache intra-run).
- **`scraper/config.py`**: toda URL con `start` contiene `&start=N&` (página 1
  `&start=0`), alineada con `Allow: /*&start=N&` de robots.txt (RFC 9309);
  páginas >1 sin cambio.
- **`main.py`**: ayuda de `--enrich-rate` actualizada (1.0 = todas hasta el
  tope).
- **`run_nightly_indeed.ps1`**: `$EnrichRate = 1.0`.
- **`start_chrome_cdp.ps1`**: ejemplo final con `--enrich-rate 1.0`.
- **`quality_before.json`**: `salary` y `work_mode` sin meta (el portal solo los
  publica parcialmente; plan §9 / RF-16), mismo criterio que la reparación de
  2026-10-10.
- **Dependencias nuevas:** ninguna (se mantienen patchright, Playwright y
  stdlib; pytest solo en tests).
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

Desde `indeed_jobs_scraper`: `python -m pytest tests -q` → **126 passed**
(98 → 126; nuevos/actualizados en `tests/test_enrich.py`, `tests/test_parser.py`,
`tests/test_runner.py` y `tests/test_config.py`; sensibilidad de los tests nuevos
comprobada contra el commit base). Desde la raíz:
`python -m pytest scrapers-pipeline/tests -q` → **1133 passed**. Verificación
independiente del diff (2026-10-18): **APTO** en dos rondas (diff completo y
delta), sin bloqueantes; auditoría de higiene `records.audit_records()` → 0
findings.
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

Comando: `python main.py --country IE --cities Dublin --term data --pages 1
--fromage 7 --cdp 9222 --enrich --enrich-rate 1.0 --enrich-max 15` (desde
`indeed_jobs_scraper`); 1 país × 1 término × 1 página, Chrome CDP 9222 con perfil
dedicado (handshake OK antes y después; sin CAPTCHA/challenge), sin `--limit`,
sin Azure, sin landing ni merge.

- **2026-10-18 18:55:32–18:56:47 (+02), 74,2 s, exit 0**; **15 ofertas únicas**
  frente al **umbral 12** (mejor verificado 11 + 1): alcanzado.
- `description_filled 15/15`: 1 auto-open embebido + 13 desde desc-cache (0
  clics) + 1 clic de panel; `enrich 1/1` (rate=100 %, `max_enrich` no recortó);
  latido PROGRESS presente; único WARNING `fake_useragent`.
- Descripciones en el parquet 15/15, longitud mín./media/máx.
  1.551/5.364,5/7.137 caracteres.
- Calidad: `quality_after.json` medido con `repair.cli quality`; veredicto
  **OK** (obligatorios 100 %; sin regresión: salary 36,2 → 80,0 %, work_mode
  16,4 → 26,7 %; skills sin meta).
- Evidencia: `evidence/live-test-20261018.md` y
  `evidence/run-log-indeed-live-test-20261018-extract.txt`; artefactos
  operativos (gitignored) en `indeed_jobs_scraper/output/`
  (`log_20261018_1855.log`, `scrape_metadata_20261018_1855.json`,
  `indeed_jobs_20261018_1855.parquet`).
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` → `quality_after.json`):

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| title | sí | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| company | sí | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| description | sí | 87.9 % | 100.0 % | +12.1 pp | 100.0 % | cumple |
| salary | no | 36.2 % | 80.0 % | +43.8 pp | sin meta | no aplica |
| skills | no | 0.0 % | 0.0 % | +0.0 pp | sin meta | no aplica |
| work_mode | no | 16.4 % | 26.7 % | +10.3 pp | sin meta | no aplica |
| location | no | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |
| posted_date | no | 100.0 % | 100.0 % | +0.0 pp | 100.0 % | cumple |

Veredicto de `quality.py`: **OK**.
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** Reparación probada: 15 ofertas verificadas (umbral 12 = mayor verificado 11 + 1) en la prueba en vivo acotada (1 país × 1 término × 1 página, IE/Dublin/data), con description al 100 % en el alcance y puerta de calidad OK sin regresión. Causa raíz del 87,9 %: el muestreo --enrich-rate 0.9 saltaba candidatas por SERP (13 en el run), 1 clic sin reintento y 4 duplicados enriquecidos sin fusionar; ahora se intentan todas las candidatas (rate 1.0), el clic se reintenta y la deduplicación fusiona campos.
- **Ofertas verificadas:** 15
- **Validación del push:** pendiente de la persona (RF-12)
- **Estado:** probado
<!-- /record:section:result -->
