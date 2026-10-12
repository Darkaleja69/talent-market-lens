<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  glassdoor    id de la fuente en inglés (p. ej. infojobs)
  2026-10-12  fecha del run de origen, YYYY-MM-DD
  repair/glassdoor-20261012    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación glassdoor — run 2026-10-12

<!-- record:section:source -->
## Fuente y run

- **Fuente:** glassdoor
- **Run de origen:** 2026-10-12
- **Rama del fix:** repair/glassdoor-20261012
- **Registro creado:** 2026-10-12
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
  - `multi_site_job_scraper/data/glassdoor/run.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-12.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.5 — Glassdoor - watchdog during descriptions
- **Receta:** The BFF returns 30 offers; up to 30 x 60 s descriptions run without PROGRESS and the watchdog stops the job (exit=75). Check page timing with the real session, whether descriptions can be batched or fetched on demand, and the right description budget. Emit a heartbeat inside the loop and bound max_description_jobs or the timeout without losing coverage.
- **Alcance de prueba propuesto:** affected portal only, 1 role, one page, watching time and PROGRESS
- **Restricciones:** only the affected portal; no merge; no Azure upload; no landing update
- **Comando propuesto:** `python -m src.glassdoor --max-pages 1`
- **Investigación:** completada el 2026-10-12; informe en
  `evidence/investigation-report.md`. Hechos clave: 9 latidos `PROGRESS`
  históricos y ninguno desde el 8-oct; el BFF devolvió 30 ofertas el 12-oct y
  el log quedó mudo 19 min 54 s antes del stop (last_parsed=0).
- **Causa confirmada:** el latido se emitía solo al cerrar cada página
  (`_process_role`), mientras las descripciones (hasta 30 × 60 s) se pedían
  antes, dentro del bucle; el watchdog (40 min de inactividad) no podía
  distinguir avance de cuelgue. Agravante: `run_all.ps1` anclaba el watchdog
  en el `$RunStart` global del wrapper y no en el arranque real de cada
  portal (~5 min de margen perdidos en glassdoor, el 6.º lanzado).
- **Hallazgo legal (RF-6):** `robots.txt` de .com/.ie/.es prohíbe `/graph` (y
  `/findPopularLocationAjax.htm`); los TOS exigen permiso escrito para extraer
  datos. El SERP simple (`/Job/jobs.htm?sc.keyword=...`) no está en `Disallow`.
- **Decisión de la persona (RF-6, 2026-10-12):** opción A — continuar con la
  vía actual (BFF `/graph`) asumiendo explícitamente el conflicto con
  `robots.txt`/TOS. Queda registrado.
- **Mercado:** decisión de la persona (2026-10-12): las búsquedas pasan de
  EE. UU. (`glassdoor.com`) a Irlanda (`glassdoor.ie`); EE. UU. deja de
  capturarse y España no entra en esta reparación.
- **Hipótesis del playbook:** H1-H3 y H6 confirmadas; H4 (descripciones en
  lote) refutada hoy; H5 (presupuesto) no era la causa; H7 (salario EUR y
  `/graph` en .ie/.es) pendiente de la prueba en vivo.
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **`multi_site_job_scraper/src/glassdoor/scraper.py`**
  - Latido de progreso dentro del bucle de tarjetas (una línea `PROGRESS` por
    tarjeta) y latido inmediato tras parsear la página, antes del bucle de
    descripciones; el latido de cierre de página se conserva y completa el
    conteo de las tarjetas no iteradas. `parsed`/`new` conservan su semántica
    acumulada (duplicados incluidos).
  - Descripciones: `timeout`, número de intentos y tope de fallos consecutivos
    parametrizados; al alcanzar el tope se desactivan las descripciones del
    rol para no encadenar esperas de ~60 s con el BFF degradado. No cambia la
    cobertura objetivo (el presupuesto 2000 cubre de sobra el máximo por rol).
  - Ubicación: fallback nacional declarado por mercado (`location_fallback`);
    sin fallback el run aborta con error en vez de buscar en el país
    equivocado (`.ie` no hereda el `(1, "COUNTRY")` de `.com`).
  - Mercado genérico: el chequeo de dominio regional de `_warmup_homepage`
    compara contra `base_url` (sin hardcodes de `.es`/`.co.uk`/USA), la
    geolocalización del navegador sale de config y los comentarios del bloqueo
    de `countryRedirect` dejan de asumir EE. UU.
- **`multi_site_job_scraper/src/glassdoor/bff.py`**: `fetch_description` acepta
  `timeout` y `attempts` (por defecto, el comportamiento anterior).
- **`multi_site_job_scraper/src/glassdoor/config_gd.py`**: mercado Irlanda
  (`base_url` .ie, `country` IE, `country_name` Ireland, `currency` EUR,
  `locale` en-IE, `timezone` Europe/Dublin, `search_path` `/Job/jobs.htm` y
  geolocalización de Dublín); presupuesto de descripciones declarado
  (2000 ≥ target 1000); claves nuevas `location_fallback`,
  `description_timeout_ms`, `description_attempts` y
  `max_description_consecutive_failures`. `profile_name` se mantiene
  (`glassdoor`: perfil persistente caliente; el mercado lo marca el dominio) y
  las rutas de salida no cambian (el mercado sustituye, no añade).
- **`multi_site_job_scraper/src/glassdoor/serp.py`**: el fallback DOM genera
  las URLs con el dominio real de la página (antes fijo a `.com`), reconoce
  Irlanda como ubicación y `parse_location` acepta el país por defecto del
  llamante (antes asumía Estados Unidos).
- **`multi_site_job_scraper/src/glassdoor/__main__.py` y `auth.py`**: docstring,
  banner y ayuda del scraper en el mercado Irlanda; el ayudante manual de
  sesión abre el dominio/locale/geolocalización configurados.
- **`multi_site_job_scraper/run_all.ps1`**: el watchdog de cada portal se
  ancla en el arranque real de su proceso (`$e.StartedAt`, fijado al lanzarlo)
  y no en el `$RunStart` global del wrapper; el resto de flags no cambia.
- **Tests**: `tests/test_glassdoor_heartbeat.py` (latido en el bucle con BFF
  simulado, semántica de cierre de página y tope de fallos de descripciones),
  `tests/test_glassdoor_market.py` (config IE, fallback de ubicación y
  detección de challenge/landing) y ampliación de `tests/test_glassdoor_bff.py`
  (intentos de `fetch_description`). Sin red.
- **Dependencias nuevas:** ninguna (biblioteca estándar; pytest solo en tests).
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
