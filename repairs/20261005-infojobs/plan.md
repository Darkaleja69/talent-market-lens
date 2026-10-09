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
- **Técnica contra el challenge (T-28):** warm-up humano (home → banner de
  cookies Didomi best-effort → búsqueda), contexto persistente con perfil
  configurable (`INFOJOBS_PROFILE_DIR`, por defecto `data/profile`),
  fingerprint coherente vía `patchright` (drop-in de Playwright) y ritmo actual
  (5–9 s con jitter). Si aparece un challenge visible se **aborta** y se
  registra el marcador, la URL y la presencia de `reese84`; sin pausa larga ni
  recargas ciegas. La renovación de sesión queda fuera del scraper.
- **Módulos (T-28):** `navigator.py` (marcadores reales y aborto),
  `browser.py` (patchright con fallback y perfil configurable), `main.py`
  (warm-up y registro del bloqueo), `config.py` (perfil configurable y
  retirada de la espera de CAPTCHA), `parser.py` (`_clean_offer_url`),
  `requirements.txt` (patchright) y tests `test_navigator.py`/`test_parser.py`.
- **Dependencias nuevas justificadas:** `patchright>=1.49` — drop-in de
  Playwright ya usado en Indeed/LinkedIn/multi-site; parchea a nivel binario
  las señales de automatización y reduce la probabilidad de que Distil sirva el
  challenge, sin solvers. Si no está instalado, hay fallback a Playwright con
  aviso.
- **Iteración T-30 (2026-10-10):** con el mismo perfil, Chrome lanzado por
  Playwright seguía recibiendo el challenge (`canonical_captcha`) incluso con
  `reese84` recién renovado; un Chrome lanzado **directamente** (sin flags de
  automatización) carga la SERP limpia (comprobado en vivo: `geetest=false`,
  canonical interno, 10 tarjetas). Se añade el **modo CDP**
  (`INFOJOBS_CDP_URL`): el scraper se conecta a un Chrome externo y al terminar
  se **desconecta sin cerrarlo** (`close_browser_context`); el modo persistente
  con patchright queda como fallback por defecto. El wrapper nocturno levanta
  el Chrome CDP (puerto 9333) si no responde, con perfil dedicado
  (`INFOJOBS_CDP_PROFILE`; por defecto la copia de la sesión real usada en la
  investigación, fuera del repositorio).
- **Meta de calidad reconciliada:** `salary` sin meta; obligatorios al 100 %
  en la muestra: `title`, `company` y `description` (texto de tarjeta).
  No se tocó el núcleo `scrapers-pipeline/repair/`.
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

- `python -m pytest tests/ -q` → **58 passed** (incluye `tests/test_browser.py`,
  nuevo: modo CDP reutiliza el contexto externo y el cierre **desconecta** sin
  cerrar el Chrome del usuario; modo persistente cierra su contexto; si la
  conexión CDP falla, el driver se detiene y el error se propaga).
- `python -m pytest scrapers-pipeline/tests -q` → **1124 passed** (suite del
  diagnóstico, exigida por T-29).
- `python -m ruff check scraper tests` → **All checks passed**.
- `python -m mypy scraper` → **Success: no issues found in 11 source files**.
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

- **Alcance:** 1 keyword × 1 ciudad × 1 página (madrid / comercial / p.1).
  Sin subida a Azure, sin landing, sin merge, sin solvers.
- **Modo:** CDP (`INFOJOBS_CDP_URL=http://127.0.0.1:9333`) sobre un Chrome
  lanzado directamente con la copia de la sesión real; el scraper abre su
  pestaña, hace warm-up y navega la SERP.
- **Comando:** `python -m scraper.main --ciudades madrid --keywords comercial
  --paginas 1` con `INFOJOBS_CDP_URL` (desde `infojobs_jobs_scraper/`).
- **Resultado:** `RESULT total=4 incidencias=0 blocked=false` (exit 0);
  5 tarjetas parseadas, 4 únicas (1 repetida en página), 0 challenges.
  Umbral exigido: 1 → **cumplido**.
- **Sesión:** `reese84` presente; SERP limpia antes de la prueba
  (`geetest=false`, canonical `www-internal.infojobs.net/.../comercial`,
  10 tarjetas / 5 títulos / 5 descripciones). La persona resolvió un challenge
  visible durante la preparación (modo asistido); el run acotado en sí no
  recibió ninguno.
- **Evidencia:** `evidence/20261010-live-test-run.txt` (log de la run; rutas
  saneadas a `<HOME>`), `evidence/20261010-live-test-summary.txt` (resumen con
  marcadores y comando) y `quality_after.json` (puerta de calidad).
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` → `quality_after.json`):

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| title | sí | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| company | sí | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| description | sí | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| salary | no | sin dato | 25.0 % | sin dato | sin meta | no aplica |
| skills | no | sin dato | 0.0 % | sin dato | sin meta | no aplica |
| work_mode | no | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| location | no | sin dato | 100.0 % | sin dato | 100.0 % | cumple |
| posted_date | no | sin dato | 100.0 % | sin dato | 100.0 % | cumple |

Veredicto de `quality.py`: **OK**.
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** 4 ofertas verificadas (umbral 1) en la prueba acotada; modo CDP validado: Chrome lanzado directo + conexión del scraper sin challenge
- **Ofertas verificadas:** 4
- **Validación del push:** pendiente de la persona (RF-12)
- **Estado:** probado
<!-- /record:section:result -->
