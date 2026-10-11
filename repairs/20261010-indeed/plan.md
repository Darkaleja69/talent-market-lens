<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  indeed    id de la fuente en inglés (p. ej. infojobs)
  2026-10-10  fecha del run de origen, YYYY-MM-DD
  repair/indeed-20261010    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación indeed — run 2026-10-10

<!-- record:section:source -->
## Fuente y run

- **Fuente:** indeed
- **Run de origen:** 2026-10-10
- **Rama del fix:** repair/indeed-20261010
- **Registro creado:** 2026-10-10
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** primary (fuente fallida) — tipo: direct
- **Resultado (outcome):** error
- **Resumen:** error: technical failure or non-zero exit (1 diagnostic failure reason(s))
- **Motivos del diagnóstico:** «sin evidencia suficiente para confirmar la fuente»
- **Ofertas del run:** sin dato
- **Ofertas del snapshot:** no aplica
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: no_data (subidos=0, rechazados=0)»
  - «resultado de la ejecución: error técnico (error)»
  - «error registrado: technical failure detected»
  - «intento 2/2»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `indeed_jobs_scraper/output/scrape_metadata_20261003_0006.json`
  - `indeed_jobs_scraper/output/scrape_metadata_20261003_1247.json`
  - `indeed_jobs_scraper/output/nightly_stderr_IE_attempt1.log`
  - `indeed_jobs_scraper/output/log_20261003_*.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-10.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.2 — Indeed - description at 0% and second page
- **Receta:** enrich_success is 0/53-0/72: the right-panel wait times out and the code continues before the /viewjob fallback; page 2 asks for login and --limit triggers Cloudflare. Check the real panel, /viewjob with the real session, page-2 behaviour and mosaic-data. Reorder the fallback, update selectors, emit progress during enrichment and resolve or document login and --limit.
- **Alcance de prueba propuesto:** 1 country x 1 term x 1 page
- **Restricciones:** no Azure upload; no landing update; no merge
- **Pendiente:** comprobar robots.txt y términos de uso, separar hechos de hipótesis y confirmar la causa probable (RF-3).
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **Causa raíz atendida:** el run moría en `connect_over_cdp` (handshake roto
  entre patchright 1.60.1 y el CDP del Chrome 9222, timeout de 180 s) y la
  `description` quedaba al 0 % porque la espera del panel derecho hacía
  `continue` y abortaba la oferta, la extracción del JSON embebido buscaba
  `window._initialData =` (ya inexistente) y el fallback a `/viewjob` está
  prohibido por `robots.txt` y era inalcanzable por el orden del código.
- **Técnica (§6.0):** sesión y fingerprint reales; nada de solvers, proxies ni
  servicios de pago. La conexión CDP se verifica con una operación real mínima;
  si el handshake no se logra, el run degrada a un navegador propio headed
  (mismo patchright ya usado) y limita la ejecución a 1 página sin sesión, en
  lugar de morir en seco. El fallback a `/viewjob`/`/graphql` (prohibidos por
  `robots.txt` para `User-agent: *`) queda detrás de un interruptor explícito
  desactivado por defecto, nunca activo en la ejecución normal.
- **Módulos y funciones:**
  - `scraper/browser.py`: `probe_cdp_handshake()` (abre y cierra una página de
    prueba en el navegador conectado), `open_cdp_browser()` (3 intentos,
    timeout de conexión 20 s, backoff breve, cierra la conexión fallida) y
    `BrowserSession` (`connected_via_cdp`, `cdp_fallback`, `_launch_owned()`
    reutilizado como fallback; `_close()` solo se desconecta cuando el
    handshake se verificó).
  - `scraper/cdp_probe.py` (nuevo): CLI `python -m scraper.cdp_probe --port 9222`
    que imprime `CDP_HANDSHAKE_OK`/`CDP_HANDSHAKE_FAIL` y sale 0/1; lo usa el
    pre-flight del nightly.
  - `scraper/parser.py`: `_extract_initial_data_descriptions()` busca ahora de
    forma tolerante la clave `autoOpenTwoPaneViewjobResponse` en los `<script>`
    (respuesta como objeto o `body` como cadena JSON) con el balanceo de llaves
    existente, sin depender de `window._initialData =`;
    `enrich_offers_via_right_panel()` elimina el `continue` del timeout,
    encadena esperas (encabezado 4 s → `#jobDescriptionText` y variantes 8 s),
    comprueba `vjs-highlight`, registra la oferta como fallida solo tras agotar
    los selectores, emite latido de progreso por oferta vía `progress_cb` y deja
    `/viewjob` detrás de `allow_viewjob_fallback=False`.
  - `scraper/runner.py`: `_apply_session_mode()` (si el handshake CDP falla,
    limita `pages` a 1 y no valida la sesión de página 2),
    `_emit_enrich_progress()` (latido PROGRESS durante el enriquecimiento) y
    `logged_in` refleja el CDP verificado, no el puerto pedido.
  - `main.py` / `scraper/config.py`: texto de `--limit` corregido (dispara el
    challenge de Cloudflare y NO evita el login; el nightly no lo usa); `limit`
    sigue en 0.
  - `run_nightly_indeed.ps1`: `Test-CdpHandshake` ejecuta el probe real tras el
    `/json` y, si falla, avisa y deja seguir al scraper con el fallback; args y
    volumen del nightly sin cambios.
- **Dependencias nuevas:** ninguna (patchright ya estaba en el stack).
- **Página 2:** se mantiene el comportamiento actual (se omite con aviso);
  sigue exigiendo una sesión autenticada real. Alcance de reparación:
  1 país × 1 término × 1 página.
- **Calidad:** `quality_before.json` reconciliado: `salary` y `work_mode`
  quedan sin meta (`target_pct: null`) porque el portal no los publica en todas
  las ofertas y la muestra de 1 página no permite exigir porcentajes; se
  registrará la cobertura obtenida y se comparará a mano con el export
  2026-10-06 (61 % / 37 %) al cerrar. `title`, `company`, `description`, `id`,
  `location` y `posted_date` siguen a 100 %; `skills` ya estaba sin meta.
  `context.json` conserva el brief original (metas teóricas al 100 % en
  `salary`/`work_mode`); el perfil efectivo de la puerta es este
  `quality_before.json` reconciliado.
- **Iteración 2 — run nocturno 2026-10-11 (T-32):** el pre-flight del wrapper
  detectó `CDP_HANDSHAKE_FAIL` a las 00:00:53 y, aun así, el run siguió con
  `--cdp 9222`; en la fase CDP el intento 1 agotó 20 s, el intento 2 murió con
  `Connection closed while reading from the driver` (el driver node de
  patchright crasheó con `ProtocolError: Network.setCacheDisabled ... session
  closed`). Las dos fases del run (NL 00:01→00:41 e IE 00:46→01:26) quedaron
  colgadas ~40 min cada una sin progreso hasta que el watchdog las mató
  (marcador `watchdog=none`; detalle `watchdog_no_progress` en
  `.watchdog_IE_attempt1.json`; `exit=75` en el marcador del run); el fallback a
  navegador propio nunca llegó a ejecutarse. Evidencia:
  `repairs/20261010-indeed/evidence/indeed-nightly-20261011-cdp-crash.txt`.
  Cambios: `open_cdp_browser()` detecta el driver muerto
  (`TargetClosedError`/`ProtocolError`/«Connection closed while reading from
  the driver») y aborta la fase CDP sin más intentos; `BrowserSession._start()`
  descarta la instancia Playwright usada para CDP (`stop()` protegido en el
  hilo propietario; la API sync no admite `stop()` desde otro hilo) y arranca
  una instancia NUEVA para el fallback, con error rápido si el fallback también
  falla; `run_nightly_indeed.ps1` omite `--cdp`
  (y usa `--pages 1`) cuando `Test-CdpHandshake` falla, registrando el motivo
  en `nightly.log`. La sonda con Chrome fresco (puerto 9224) confirma que
  patchright 1.60.1 sí conecta: el fallo es del estado de la instancia/perfil
  del 9222, cuyo remedio operativo sigue siendo reiniciarlo con
  `start_chrome_cdp.ps1`. No se añaden latidos PROGRESS artificiales: la fase
  CDP queda acotada (~1 min si los intentos agotan su timeout) y el watchdog
  sigue matando runs realmente atascados.
- **Iteración 3 — rutas/selectores del portal (2026-10-11):** la prueba en vivo
  de la iteración 2 pasó el recuento (11 ofertas) pero falló la calidad:
  `description` al 0 % porque el portal cambió de layout y el parser fallaba en
  silencio — en `autoOpenTwoPaneViewjobResponse` la ruta
  `hostQueryExecutionResult...` ya no existe (la descripción vive en
  `body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription`, con la clave
  en `body.jobKey`) y el panel derecho pasó a los testids `vj-*`/`viewjob-*`
  (el `snippet` vacío, en cambio, es del portal: no publica extracto en esta
  variante de SERP). Cambios (detalle y evidencia en
  `repairs/20261010-indeed/evidence/investigation-addendum.md`):
  `scraper/parser.py` · `_descriptions_from_auto_open` acepta la ruta nueva y
  conserva la antigua como respaldo; `_PANEL_HEADER_SELECTOR`/
  `_PANEL_DESC_SELECTORS` priorizan `vj-job-title`, `viewjob-job-content` y el
  contenedor padre de `vj-job-description-heading`, con los selectores antiguos
  al final; `cond_selectors` añade `structured-job-summary`. Decisión de la
  persona (RF-6): se mantiene el recorrido de UI por clic en el panel (el fetch
  interno a `/viewjob` lo dispara la propia página) y el fallback directo
  `/viewjob` sigue desactivado por defecto (`allow_viewjob_fallback=False`).
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

Desde `indeed_jobs_scraper/`:

- `python -m pytest tests -q` → **98 passed**. Incluye los tests nuevos
  `tests/test_browser_cdp.py` (probe del handshake, reintentos de
  `open_cdp_browser`, timeouts normales que siguen reintentando, aborto
  inmediato ante driver muerto detectado por excepción o por el detalle del
  probe, descarte seguro de la instancia Playwright (stop protegido), fallback
  de `BrowserSession` con instancia nueva tras el driver muerto, fallo rápido
  del fallback y modo sin CDP/1 página), `tests/test_enrich.py` (timeout del
  panel que ya no abandona la oferta, cadena de esperas, `vjs-highlight`,
  latido de progreso y `/viewjob` desactivado por defecto) y
  `tests/test_config.py` (URL de paginación por `start=` y texto de
  `--limit`), además de los casos de extracción tolerante del JSON embebido en
  `tests/test_parser.py`. Iteración 3 (2026-10-11): `tests/test_parser.py`
  añade la ruta nueva del autoOpen
  (`jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription` + `jobKey`,
  también con `body` como cadena JSON), la ausencia de ambas rutas (`{}`) y la
  ruta antigua conservada como respaldo; `tests/test_enrich.py` añade el layout
  nuevo (cabecera `vj-job-title` + contenedor `viewjob-job-content`, sin
  selectores antiguos), la lectura del contenedor padre del marcador
  `vj-job-description-heading`, las condiciones vía `structured-job-summary` y
  la regresión del layout antiguo; el caso de timeout comprueba que los testids
  nuevos van primero en la cadena y los antiguos se conservan de respaldo.
- El scraper no usa Ruff/mypy: `requirements.txt` no los declara y la carpeta no
  tiene configuración de lint.

Desde la raíz del repositorio:

- `python -m pytest scrapers-pipeline/tests -q` → **1126 passed** (incluye la
  auditoría de higiene de `repairs/`).

Todo offline, sin red ni navegador. La prueba en vivo de la iteración 2 ya se
ejecutó (11 ofertas ≥ umbral 1, sin challenge) y falló la puerta de calidad por
`description` (ver `evidence/indeed-20261011-live-test-run.txt`); la prueba de
esta iteración 3 queda pendiente (sección de prueba en vivo).
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

_(Pendiente: alcance acotado y repetible, comando, recuento de ofertas, umbral
exigido, restricciones respetadas —sin Azure, landing ni merge— y evidencia.)_
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` listo; `quality_after.json`
pendiente de la prueba en vivo). `salary` y `work_mode` quedan sin meta (el
portal no los publica en todas las ofertas y la muestra de 1 página no permite
exigir porcentajes); la puerta no evalúa la regresión sin dato previo, así que
la comparación con el export 2026-10-06 (61 % / 37 %) se registrará a mano al
cerrar. El perfil efectivo de la puerta es `quality_before.json`, no las metas
teóricas de `context.json`:

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| title | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| company | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| description | sí | sin dato | pendiente | pendiente | 100.0 % | pendiente |
| salary | no | sin dato | pendiente | pendiente | sin meta | pendiente |
| skills | no | sin dato | pendiente | pendiente | sin meta | pendiente |
| work_mode | no | sin dato | pendiente | pendiente | sin meta | pendiente |
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
