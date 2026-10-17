<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  irishjobs    id de la fuente en inglés (p. ej. infojobs)
  2026-10-17  fecha del run de origen, YYYY-MM-DD
  repair/irishjobs-20261017    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación irishjobs — run 2026-10-17

<!-- record:section:source -->
## Fuente y run

- **Fuente:** irishjobs
- **Run de origen:** 2026-10-17
- **Rama del fix:** repair/irishjobs-20261017
- **Registro creado:** 2026-10-17
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** primary (fuente fallida) — tipo: multi_site
- **Resultado (outcome):** error
- **Resumen:** error: technical failure or non-zero exit (1 diagnostic failure reason(s))
- **Motivos del diagnóstico:** «sin evidencia suficiente para confirmar la fuente»
- **Ofertas del run:** sin dato
- **Ofertas del snapshot:** no aplica
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: ok (subidos=1, rechazados=0)»
  - «resultado de la ejecución: error técnico (error)»
  - «error registrado: exit=75»
  - «detalle: exit=75 merge_exit=0 output_merged=False»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `multi_site_job_scraper/data/merged/last_run.json`
  - `multi_site_job_scraper/data/irishjobs/run.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-17.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.4 — IrishJobs - 0 cards and watchdog exit
- **Receta:** 0 cards in every combination, no PROGRESS line and exit=75 watchdog; stepstone_nl shares the class and works. Check what the IE SERP returns in a real browser (challenge, different HTML, geo/rate) and whether an internal API exists. Update the selectors/parser, detect the block with a reason and emit PROGRESS per page (0 cards is also progress).
- **Alcance de prueba propuesto:** affected portal only, 1 role, one page
- **Restricciones:** only the affected portal; no merge; no Azure upload; no landing update
- **Comando propuesto:** `python -m src.irishjobs --max-pages 1`
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
| id | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| title | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| company | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| description | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| salary | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| skills | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| work_mode | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| location | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| posted_date | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |

_(Pendiente: veredicto de `quality.py` y `quality_after.json`.)_
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** (pendiente)
- **Estado:** planificado (al crear, `planificado`; al terminar, `probado`,
  `descartado` o `escalado`)
- **Validación del push:** pendiente de la persona (RF-12)
<!-- /record:section:result -->
