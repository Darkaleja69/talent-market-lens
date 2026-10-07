<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  infojobs    id de la fuente en inglés (p. ej. infojobs)
  2026-10-05  fecha del run de origen, YYYY-MM-DD
  repair/infojobs-20261005    rama del fix, repair/<fuente>-<YYYYMMDD>
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

# Reparación infojobs — run 2026-10-05

<!-- record:section:source -->
## Fuente y run

- **Fuente:** infojobs
- **Run de origen:** 2026-10-05
- **Rama del fix:** repair/infojobs-20261005
- **Registro creado:** 2026-10-05
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Rol:** primary (fuente fallida) — tipo: direct
- **Resultado (outcome):** blocked
- **Resumen:** blocked: captcha/anti-bot (1 diagnostic failure reason(s))
- **Motivos del diagnóstico:** «sin evidencia suficiente para confirmar la fuente»
- **Ofertas del run:** 0
- **Ofertas del snapshot:** no aplica
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Evidencia del diagnóstico:**
  - «estado del pipeline: no_data (subidos=0, rechazados=0)»
  - «resultado de la ejecución: bloqueado (blocked)»
  - «error registrado: captcha block detected»
  - «detalle: blocked=true»
  - «intento 1/3»
- **Rutas locales de referencia** (relativas a la raíz del repositorio):
  - `infojobs_jobs_scraper/data/run_nightly.log`
  - `infojobs_jobs_scraper/data/nightly_stdout_attempt1.log`
  - `infojobs_jobs_scraper/data/logs/run_20261003_*.log`
  - `scrapers-pipeline/logs/diagnostic_last.json`
  - `scrapers-pipeline/logs/upload-2026-10-05.log`
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.1 — InfoJobs - CAPTCHA block
- **Receta:** Blocked by a visible CAPTCHA on open (Distil) with 0 offers. Check which detector marker fires, whether the real user session gets the SERP, and whether an allowed internal JSON endpoint exists. Design to avoid the challenge: real session and challenge-token reuse, coherent fingerprint, human headers and pace; log the block reason and support an assisted pause. Red lines: no CAPTCHA solvers, no identity checks, no paid services.
- **Alcance de prueba propuesto:** 1 keyword x 1 city x 1 page
- **Restricciones:** no Azure upload; no landing update; no merge
- **Pendiente:** comprobar robots.txt y términos de uso, separar hechos de hipótesis y confirmar la causa probable (RF-3).
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **Causa (T-27):** bloqueo por sesión/reputación (Distil/Imperva + GeeTest),
  no por parser. El perfil propio del scraper navegaba en frío sin un token de
  challenge válido; el perfil real (con `reese84`) recibe la SERP SSR completa.
  Diseño preventivo §6.0, sin solvers ni servicios de pago.
- **Técnica contra el challenge:** warm-up humano (home → banner de cookies
  Didomi best-effort → búsqueda), contexto persistente con perfil configurable
  (reutilizable con una copia de la sesión real, mismo dispositivo/red),
  fingerprint coherente vía `patchright` (drop-in de Playwright; no se parchea
  `navigator.webdriver` a mano) y ritmo actual (5–9 s con jitter). Si aparece un
  challenge visible se **aborta** y se registra el marcador, la URL y la
  presencia de `reese84`; sin pausa larga ni recargas ciegas (política de la
  persona, 2026-10-08). La renovación de sesión queda fuera del scraper.
- **Módulos cambiados:**
  - `scraper/navigator.py`: `detect_captcha` → `captcha_marker` (marcadores
    reales: URL `/distil/`/`captcha`, canonical, h1 «eres humano»/«un robot»
    normalizado sin acentos, `geetest`/`initGeetest`, iframe `distil` e imagen
    `sherlock` residual); `handle_captcha` ya no recarga ni espera: devuelve
    `(False, marcador)` para abortar; se añaden `session_has_reese84` y el
    warm-up (`warm_up`/`accept_cookies`).
  - `scraper/browser.py`: `patchright` como drop-in con fallback a Playwright
    (aviso en log), contexto persistente y perfil configurable.
  - `scraper/main.py`: warm-up antes de la primera SERP; al detectar challenge
    registra `marcador`, `url` y `reese84` en el log y en `bloqueos` y sale con
    `blocked=true reason=<marcador>`; el flag `--unattended` se conserva por
    compatibilidad del wrapper (la política de aborto es la única).
  - `scraper/config.py`: `PROFILE_DIR` configurable con `INFOJOBS_PROFILE_DIR`
    (por defecto `data/profile`); se retiran `CAPTCHA_TIMEOUT` y
    `CAPTCHA_MAX_ATTEMPTS` (ya no hay espera); `ROBOTS_DISALLOWED` se conserva.
  - `scraper/parser.py`: el delta real es `_clean_offer_url` (elimina query y
    fragmento de la URL de la oferta, fuera `?applicationOrigin...`, patrón en
    `Disallow`), más los tests que fijan el comportamiento. La extracción del
    texto completo del párrafo de la tarjeta
    (`p.ij-OfferCardContent-description-description`) ya existía en HEAD y no
    cambia: queda cubierta por tests (descripción idéntica al párrafo de la
    tarjeta y URLs limpias). `salary` «Más de X €» se mantiene tal cual (sin
    meta y sin periodo inventado).
  - `scraper/requirements.txt`: `patchright>=1.49`.
  - `tests/test_navigator.py` (nuevo) y `tests/test_parser.py`: cobertura
    offline de cada marcador real, aborto sin recargas y descripción completa
    con el fixture actual.
- **Dependencias nuevas justificadas:** `patchright>=1.49` — drop-in de
  Playwright ya usado en Indeed/LinkedIn/multi-site; parchea a nivel binario
  las señales de automatización y reduce la probabilidad de que Distil sirva el
  challenge, sin solvers. Si no está instalado, hay fallback a Playwright con
  aviso.
- **Meta de calidad reconciliada:** `salary` pasa a `target_pct: null` (sin
  meta) en `context.json` y `quality_before.json`: el portal publica «Salario
  no disponible» en parte de las ofertas y su ausencia no es fallo; se evita el
  falso `target_not_reached` de la puerta `repair.quality`. `skills` ya estaba
  sin meta (se derivan en Databricks). Obligatorios al 100 % en la muestra:
  `title`, `company` y `description` (texto de tarjeta). No se tocó el núcleo
  `scrapers-pipeline/repair/`.
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
| skills | no | sin dato | pendiente | pendiente | sin meta | pendiente |
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
