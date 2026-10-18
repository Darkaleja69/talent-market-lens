<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  glassdoor    id de la fuente en inglés (p. ej. infojobs)
  2026-10-18  fecha del run de origen, YYYY-MM-DD
  repair/glassdoor-20261018    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación glassdoor — run 2026-10-18

<!-- record:section:source -->
## Fuente y run

- **Fuente:** glassdoor
- **Run de origen:** 2026-10-18
- **Rama del fix:** repair/glassdoor-20261018
- **Registro creado:** 2026-10-18
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** primary (fuente fallida) — tipo: multi_site
- **Resultado (outcome):** empty
- **Resumen:** empty: no offers captured in the run (1 diagnostic failure reason(s))
- **Motivos del diagnóstico:** «sin evidencia suficiente para confirmar la fuente»
- **Ofertas del run:** sin dato
- **Ofertas del snapshot:** no aplica
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: ok (subidos=1, rechazados=0)»
  - «resultado de la ejecución: sin ofertas (empty)»
  - «detalle: exit=0 merge_exit=0 output_merged=False»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `multi_site_job_scraper/data/merged/last_run.json`
  - `multi_site_job_scraper/data/glassdoor/run.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-18.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.5 — Glassdoor - watchdog during descriptions
- **Receta:** The BFF returns 30 offers; up to 30 x 60 s descriptions run without PROGRESS and the watchdog stops the job (exit=75). Check page timing with the real session, whether descriptions can be batched or fetched on demand, and the right description budget. Emit a heartbeat inside the loop and bound max_description_jobs or the timeout without losing coverage.
- **Alcance de prueba propuesto:** affected portal only, 1 role, one page, watching time and PROGRESS
- **Restricciones:** only the affected portal; no merge; no Azure upload; no landing update
- **Comando propuesto:** `python -m src.glassdoor --max-pages 1`
- **Investigación:** completada el 2026-10-18; informe en
  `evidence/investigation-report.md` (evidencias clave: extracto del run
  `evidence/run-log-glassdoor-20261018-extract.txt`, fallos de otros portales
  `evidence/cross-portal-failures-20261018.txt`, eventos del equipo
  `evidence/system-events-20261018.txt`, sondas web y de perfil
  `evidence/http-probes-20261018.txt`, `evidence/browser-probe-summary-20261018.txt`
  y `evidence/profile-probe-20261018.txt`).
- **Causa confirmada:** incidente transitorio de recursos del host en la
  ventana 00:05-00:31 (agotamiento de puertos efímeros TCP — evento Tcpip 4231
  a las 00:29:12 — y saturación de un equipo de 8 GB con varios navegadores en
  paralelo). El Chromium de glassdoor arrancó degradado (5 min de timeout en
  el primer intento, `Network service crashed`) y después ninguna navegación
  funcionó (homepage y las 6 SERP con timeout). Hoy el mismo perfil, binario y
  red cargan la SERP (187 ofertas) en segundos.
- **Hipótesis refutadas:** H2 bloqueo anti-bot de Glassdoor (los fallos son
  timeouts sin 403/challenge y afectaron a otros portales en la misma franja),
  H3 perfil corrupto/bloqueado (sin locks; sonda con el perfil real OK) y H4
  cambio de versión/configuración (sin actualizaciones; el único cambio de
  código, la retirada del parche manual de `navigator.webdriver`, es inocuo
  para la red).
- **Brecha de señalización (a reparar):** con exit 0 y sin salida fresca el
  diagnóstico clasifica el portal como `empty`; un fallo total de navegación
  debe terminar con exit ≠ 0 (marcador `navigation_failed`) para leerse como
  `error` técnico.
- **Legal:** sin cambios; la decisión de la persona (2026-10-12, opción A:
  continuar con la vía BFF `/graph` asumiendo el conflicto con `robots.txt`/
  TOS) se mantiene; `robots.txt` y TOS re-comprobados hoy sin novedad.
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **`multi_site_job_scraper/src/glassdoor/scraper.py`**
  - Estados de rol nuevos (`ROLE_LOADED`, `ROLE_NAVIGATION_FAILED`,
    `ROLE_BLOCKED`, `ROLE_UNKNOWN`) registrados por `_process_role` a partir
    de la primera SERP: `goto` OK y `_check_blocked_or_landing` False →
    `loaded`; timeout/excepción de `goto` en la página 1 → `navigation_failed`;
    marcadores anti-bot/landing en la página 1 → `blocked`.
  - `MAX_CONSECUTIVE_NAVIGATION_FAILURES = 3` (constante del módulo): tras 3
    roles consecutivos sin SERP cargada se corta el bucle de roles (ya no se
    agotan 6 roles × ~70 s de timeouts).
  - Reintento acotado único: ante el primer `navigation_failed` con el
    contexto vivo se cierra y se relanza el navegador una sola vez por run
    (mismo patrón que el path de `TargetClosedError`) y se reintenta el rol;
    si el reintento carga la SERP, el run continúa con normalidad.
  - Si ningún rol terminó `loaded` y todos los intentados acabaron
    `navigation_failed`/`blocked`, se registra
    `ERROR ... navigation_failed: N/M roles sin SERP (motivos: ...)` y se
    lanza `NavigationFailure(RuntimeError)`; `__main__` ya mapea cualquier
    excepción a exit 1 (con `store.write_all()`), de modo que el diagnóstico
    lo lee como `error` técnico y no como `empty`.
  - Se conserva íntegro el comportamiento legítimo: SERP cargada sin ofertas
    o solo con duplicados → exit 0; roles fallidos junto a uno `loaded` →
    exit 0. El latido `PROGRESS` y el flujo de descripciones no cambian.
- **`multi_site_job_scraper/tests/test_glassdoor_navigation_failure.py`**
  (nuevo): 11 tests sin red que cubren el aborto temprano, el marcador
  `navigation_failed`, el relanzamiento único (y su ausencia en runs sanos y
  en bloqueo), el camino `blocked`, el caso `ROLE_UNKNOWN` (ver decisión más
  abajo), el run normal cuando un rol posterior (o el reintento) carga, la
  SERP cargada sin ofertas/duplicados y el exit 1 de `__main__` con
  `store.write_all()`.
- **`multi_site_job_scraper/src/glassdoor/__main__.py`:** sin cambios; su
  `except Exception` ya convierte `NavigationFailure` en exit 1 y guarda el
  store (cubierto por test).
- **Decisión sobre `ROLE_UNKNOWN`:** un rol interrumpido antes de navegar
  (p. ej. excepción genérica previa al `goto`) cuenta para la racha del aborto
  temprano, pero un run sin ningún rol `loaded` y con `unknown` **no** lanza
  `NavigationFailure` (exit 0 semántico, conducta previa mantenida): el fallo
  de navegación no está probado. Cubierto por test.
- **Nota del verificador (contadores de relanzamiento):** el relanzamiento por
  `navigation_failed` es independiente de los relanzamientos del path
  `TargetClosedError`; cada vía está acotada por su lado (uno por run / límite
  de crashes de navegador) y no comparten contador.
- **Dependencias nuevas:** ninguna (biblioteca estándar; pytest solo en tests).
- **Ajuste de metas de calidad (`quality_before.json`):** `target_pct` pasa a
  `null` en `salary`, `skills` y `work_mode` (igual que en
  `repairs/20261012-glassdoor/quality_before.json`), porque el portal no los
  publica al 100 % (muestra 17-oct: 66,7 % / 79,0 % / 8,6 %); conforme al plan
  §9 la ausencia de un opcional que el portal no expone no se penaliza. El
  resto de metas no cambia.
- **Fuera de alcance de este fix:** (1) el efecto lateral del `state.json`
  vaciado al arrancar (`rebuild_state()` con store vacío; §7.3 del informe) se
  propone como seguimiento independiente; (2) la mitigación de la causa raíz
  del entorno (§7.2: reducir navegadores simultáneos por noche o ajustar los
  puertos del sistema) es decisión de la persona y no se aplica aquí.
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

_(Pendiente: suites ejecutadas del scraper afectado y del diagnóstico, y su
resultado.)_
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

Comando: `python -m src.glassdoor --role "Data Analyst" --max-pages 1` (desde
`multi_site_job_scraper`); 1 rol × 1 página (mismo alcance que la prueba del
2026-10-15), solo glassdoor, navegador headful con el perfil persistente; sin
Azure, landing ni merge.

- **2026-10-18 15:49:12–15:50:31 (+02), 78,9 s, exit 0**; **24 ofertas nuevas**
  frente al **umbral 24** confirmado con `repair.cli threshold --source
  glassdoor` (mejor verificado 23 + 1): el umbral se alcanza.
- **Latido comprobado:** 26 líneas `PROGRESS source=glassdoor` intercaladas en
  el bucle de tarjetas (parsed 0→24, hueco máximo ≈ 2 s; primer latido 20 s
  tras el goto de la SERP). Sin `navigation_failed` y sin marcadores
  anti-bot/CAPTCHA; únicos WARNING: el autocompletado retirado (403) y el
  fallback de país declarado (70, COUNTRY).
- Descripciones 24/24 completas dentro del presupuesto (2000), longitud
  mín./media/máx. 1.560/5.405/8.592 caracteres (no snippets).
- Calidad: `quality_after.json` medido con `repair.cli quality`; veredicto
  **OK** (obligatorios 100 %; `id`/`location`/`posted_date` 100 %; `salary`
  66,7 %, `skills` 79,2 %, `work_mode` 4,2 % sin meta, solo medición).
- Evidencia: `evidence/live-test-20261018.md` y
  `evidence/run-log-glassdoor-live-test-20261018-extract.txt`; artefactos
  operativos (gitignored) en `multi_site_job_scraper/data/glassdoor/`
  (`live_test_20261018_stdout.txt`, `live_test_20261018_meta.txt`,
  `output/jobs.parquet` con 24 filas y checkpoints `..._p1*.csv`), con backup
  previo `*.bak-20261018-livepretest`.
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
