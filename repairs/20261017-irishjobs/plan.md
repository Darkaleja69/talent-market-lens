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
- **Investigación completada (2026-10-17):** informe en `evidence/investigation-report.md`, con sondas de navegador real sobre `www.irishjobs.ie` (SERP nacional y de ciudad, SERP vacía, ficha de detalle, robots.txt y TOS).
  - **Hechos:** un navegador normal recibe el SERP completo por SSR (25 tarjetas con los selectores actuales, que **no** han rotado); el navegador del scraper lleva 15 noches con 0 tarjetas en todos los combos y sin ninguna línea `PROGRESS`, con `page.goto` sin error. El perfil del scraper no renovó cookies normales desde el run sano del 2026-10-01: solo acumuló `_abck` (Akamai) el 11-10 sin actualizarla. El sitio está tras Akamai Bot Manager (`ak_p`, sensor ofuscado).
  - **Causa primaria (probable):** bloqueo silencioso de Akamai al navegador del scraper (respuesta mitigada sin listado). **Causa secundaria (defectos confirmados):** sin latido si la página no trae tarjetas (el watchdog mata a los 40 min), contador basado en tarjetas post-filtro, sin detección de bloqueo, paginación con `action=paging_next` (vetada por robots.txt) y `description_full` del JSON-LD sobrescrita por el DOM.
  - **Iteración 2 (2026-10-17, sonda `channel="chrome"`):** nuevo hecho que afina la causa primaria: con el mismo equipo e IP y perfil scratch nuevo, el **Chrome real instalado (155)** recibe **200 con 25 tarjetas** en carga directa y tras el flujo de warmup; el Chromium empaquetado de patchright (148) seguía recibiendo 403 `challenge:access_denied` ese mismo día (11:38 y 11:47, también con perfil renovado). No es el perfil ni la IP: es el **binario/fingerprint del navegador empaquetado**. Evidencia: `evidence/probe-chrome-channel-20261017.md`.
  - **Veredictos de hipótesis (§6.4):** H1 parcial (contenido intacto al navegador normal; bloqueo al del scraper); H2 refutada (no hay API interna de listado y robots veta `/JobSearch/*/api/`); H3 refutada (selectores vigentes); H4 confirmada (latido por página, 0 tarjetas también avanza); H5 refutada (`stepstone_nl` comparte clase y sí parsea); H6 confirmada (los descartes del filtro de título congelan el contador); H7 refutada (la descripción completa está en el JSON-LD del detalle, pero el DOM la sobrescribe).
  - **robots.txt/TOS:** SERP (`/jobs/...`) y detalle (`/job/...`) permitidos para `User-agent: *`; `Disallow: /*?action=`/`/*&action=` y `/*&page=` vetan la paginación actual (se sustituye por el `href` propio del sitio); los TOS no prohíben el rastreo de forma explícita, sí interferir/sobrecargar (4.8.1) y el uso "in competition" (4.8.2/4.8.3) — se mantiene ritmo humano y volumen bajo.
  - **Riesgo:** el cuerpo exacto de la respuesta mitigada no se pudo observar en la investigación (no se ejecutan scrapers); la prueba en vivo lo confirmará con el volcado `blocked_*.txt` y la línea `BLOCKED`.
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **Módulos cambiados:**
  - `multi_site_job_scraper/src/irishjobs/scraper.py` — latido `PROGRESS` por
    página (incluida la de 0 tarjetas y el fallback de paginación), contador de
    tarjetas **pre-filtro de título** (`max(1, tarjetas)` por página), detección
    de bloqueo con motivo (`BLOCKED ...`) + evidencia saneada + parada del
    portal, parada temprana por N combos consecutivos vacíos (`EmptyComboGuard`,
    configurada a 25 en `config_ie.py`; desactivada por defecto si la clave no
    existe en la config) y calentamiento de sesión (portada + consentimiento).
  - `multi_site_job_scraper/src/irishjobs/serp.py` — `detect_block(page)`
    (bloqueo vs vacío legítimo) y paginación conforme a robots.txt: `?page=N`
    como primer parámetro, sin `action`; `next_page_href`/`has_next_page`/
    `go_to_next_page` usan el `href` propio del sitio
    (`nav[aria-label="pagination"]`, forma `?page=N&ag=7`) y conservan el
    fallback por clic.
  - `multi_site_job_scraper/src/irishjobs/detail.py` — la descripción del
    JSON-LD no se sobrescribe con el texto del DOM (solo se usa el DOM si el
    JSON-LD no trajo descripción); `employmentType` → `work_mode`
    (`REMOTE_WORKING`/`TELECOMMUTE` → Remote, `HYBRID` → Hybrid); `datePosted`
    prioritario para `posted_datetime` cuando existe (ya lo era).
  - `multi_site_job_scraper/src/irishjobs/config_ie.py` — `detect_block: true`,
    `max_consecutive_empty_combos: 25`, `warmup: true` y
    `browser_channel: "chrome"` (las cuatro claves solo las activa irishjobs;
    sin ellas, la clase compartida mantiene detección de bloqueo, guarda,
    warm-up y canal desactivados).
  - `multi_site_job_scraper/src/irishjobs/scraper.py` — `_launch_browser` lee
    `browser_channel` de la config y lo pasa a `launch_context`; el
    relanzamiento de `_ensure_browser_alive` pasa por ahí y conserva el canal.
  - `multi_site_job_scraper/src/core/progress.py` — solo docstring: `parsed`
    crece con las tarjetas parseadas de cada página o, como mínimo, 1 por
    página procesada (aunque no traiga tarjetas); una página vacía es progreso
    y el formato de la línea no cambia.
  - `multi_site_job_scraper/src/core/browser.py` — retirado el parche manual de
    `navigator.webdriver` (patchright ya lo cubre y era una inconsistencia de
    huella) y añadido el parámetro **opt-in** `channel`: se reenvía a
    `launch_persistent_context` **solo cuando no es `None`**, de modo que el
    resto de portales mantiene el binario empaquetado. Se activa para irishjobs
    (`browser_channel: "chrome"`) porque, según la sonda del mismo día
    (`evidence/probe-chrome-channel-20261017.md`, misma IP y equipo, perfil
    scratch nuevo), el Chromium empaquetado (148) es rechazado por Akamai con
    403 mientras el Chrome real instalado (155) recibe 200 con 25 tarjetas,
    tanto en carga directa como tras el warmup (3 de 6 cargas del presupuesto;
    variante B no necesaria).
  - `multi_site_job_scraper/tests/` — suites nuevas
    (`test_irishjobs_serp.py`, `test_irishjobs_heartbeat.py`,
    `test_irishjobs_detail.py`, `test_irishjobs_browser_channel.py`), helper
    `fake_dom.py` (lxml, ya en el stack) y 4 fixtures HTML saneadas en
    `tests/fixtures/`.
- **Diseño:** la detección de bloqueo, la guarda de combos vacíos y el warm-up
  solo se activan donde están auditados (claves `detect_block`, `warmup` y
  `max_consecutive_empty_combos` de la config, fijadas en `config_ie.py`).
  `stepstone_nl` comparte la clase (`src/stepstone_nl/__main__.py` instancia
  `IrishJobsScraper`) y hereda todo lo que vive en los módulos comunes: el
  latido (`PROGRESS` por página, incluida la vacía, y contador pre-filtro de
  título) y también la **paginación nueva** de `src/irishjobs/serp.py`
  (`?page=N` como primer parámetro, sin `action=`, basada en el `href` propio
  del sitio), porque la clase la usa sin distinguir el portal. Lo que sí queda
  desactivado por config para él es la detección de bloqueo, la parada por
  combos vacíos, el warm-up y el canal de navegador. Limitación honesta: la
  página 2+ de `stepstone_nl` no se ha ejercitado con la paginación nueva
  (queda para su propia reparación, T-36). El portal se detiene con el motivo
  en la primera página mitigada y vuelca `data/irishjobs/blocked_*.txt`
  (título, URL, status, marcadores, recorte de texto; sin cookies ni scripts).
  La guarda de combos vacíos queda en 25 para irishjobs, y solo cuenta un
  combo como vacío si ninguna de sus páginas trajo tarjetas. El calentamiento
  navega la portada y descarta el banner de consentimiento con el mismo perfil
  persistente; **no** se usa el formulario de búsqueda (su envío es JS y no se
  verificó en vivo: usarlo a ciegas añadiría fragilidad al arranque),
  justificación registrada aquí. El canal de navegador (`browser_channel`)
  sigue la misma regla de aislamiento: solo lo declara irishjobs y
  `stepstone_nl` no lo hereda (con test explícito).
- **Metas de calidad:** refinadas según la auditoría del informe
  (`quality_before.json`): salary 85.0, work_mode 60.0 y skills sin meta. El
  brief original proponía 100 y queda superado por `quality_before.json`, que
  es la fuente del gate.
- **Limitación conocida:** el canal `browser_channel: "chrome"` es un
  requisito operativo del host: exige Chrome instalado en la máquina; si no
  está, el lanzamiento de irishjobs falla (los reintentos de `launch_context`
  vuelven a aplicar el `channel` declarado y no caen al binario empaquetado).
  La parada por bloqueo no cambia el código de salida del proceso (sigue 0);
  la evidencia queda en la línea `BLOCKED` y en el volcado
  `multi_site_job_scraper/data/irishjobs/blocked_*.txt`.
- **Dependencias nuevas:** ninguna.
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
| salary | no | sin dato | pendiente | pendiente | 85.0 % | pendiente |
| skills | no | sin dato | pendiente | pendiente | sin meta | pendiente |
| work_mode | no | sin dato | pendiente | pendiente | 60.0 % | pendiente |
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
