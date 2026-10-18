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

_(Pendiente: módulos y ficheros modificados, diseño elegido y dependencias
nuevas justificadas, si las hay.)_
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

_(Pendiente: suites ejecutadas del scraper afectado y del diagnóstico, y su
resultado.)_
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

_(Pendiente: alcance acotado y repetible, comando, recuento de ofertas, umbral
exigido, restricciones respetadas —sin Azure, landing ni merge— y evidencia.)_
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` listo; `quality_after.json` pendiente de la prueba en vivo):

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |
| title | sí | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |
| company | sí | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |
| description | sí | 87.9 % | pendiente | pendiente | 100.0 % | pendiente |
| salary | no | 36.2 % | pendiente | pendiente | 100.0 % | pendiente |
| skills | no | 0.0 % | pendiente | pendiente | sin meta | pendiente |
| work_mode | no | 16.4 % | pendiente | pendiente | 100.0 % | pendiente |
| location | no | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |
| posted_date | no | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |

_(Pendiente: veredicto de `quality.py` y `quality_after.json`.)_
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** (pendiente)
- **Estado:** planificado (al crear, `planificado`; al terminar, `probado`,
  `descartado` o `escalado`)
- **Validación del push:** pendiente de la persona (RF-12)
<!-- /record:section:result -->
