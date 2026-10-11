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
| description | sí | 90.2 % | pendiente | pendiente | 100.0 % | pendiente |
| salary | no | 17.8 % | pendiente | pendiente | 100.0 % | pendiente |
| skills | no | 82.1 % | pendiente | pendiente | 100.0 % | pendiente |
| work_mode | no | 42.6 % | pendiente | pendiente | 100.0 % | pendiente |
| location | no | 100.0 % | pendiente | pendiente | 100.0 % | pendiente |
| posted_date | no | 96.4 % | pendiente | pendiente | 100.0 % | pendiente |

_(Pendiente: veredicto de `quality.py` y `quality_after.json`.)_
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** (pendiente)
- **Estado:** planificado (al crear, `planificado`; al terminar, `probado`,
  `descartado` o `escalado`)
- **Validación del push:** pendiente de la persona (RF-12)
<!-- /record:section:result -->
