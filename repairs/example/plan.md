# Reparación infojobs — run 2026-10-03 (ejemplo didáctico)

> **Esto es un ejemplo, no un registro real.** Muestra cómo queda un `plan.md`
> completo para el caso de InfoJobs del run 2026-10-03 (bloqueo por CAPTCHA al
> abrir), con datos del diagnóstico saneado
> `scrapers-pipeline/tests/fixtures/diagnostic_2026-10-03.sanitized.json`. El
> registro real se abre en la rama `repair/infojobs-<fecha>` durante el cierre
> de la spec 004. Los ficheros `context.json`, `quality_before.json` y
> `quality_after.json` de esta carpeta también son ilustrativos.

<!-- record:section:source -->
## Fuente y run

- **Fuente:** infojobs
- **Run de origen:** 2026-10-03
- **Rama del fix:** repair/infojobs-20261003
- **Registro creado:** 2026-10-04
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

- **Estado del diagnóstico:** failed
- **Resultado (outcome):** blocked (CAPTCHA/anti-bot al abrir)
- **Ofertas del run:** 0
- **Snapshot:** no aplica
- **Motivos:** «sin evidencia suficiente para confirmar la fuente»
- **Evidencia del diagnóstico:** «resultado de la ejecución: bloqueado
  (blocked)», «error registrado: captcha block detected», «detalle:
  blocked=true», «intento 1/3»
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

- **Diagnóstico:** `scrapers-pipeline/logs/diagnostic_last.json` (refrescado
  con `python -m verification.verify_run --offline`).
- **Rutas locales** (fuera de git):
  - `<HOME>\Documents\projects\infojobs_jobs_scraper\data\run_nightly.log`
  - `<HOME>\Documents\projects\infojobs_jobs_scraper\data\nightly_stdout_attempt1.log`
  - `<HOME>\Documents\projects\infojobs_jobs_scraper\data\logs\run_20261003_*.log`
- **Extractos saneados:**
  - `run_nightly.log`: `blocked=True` de forma continua desde 2026-09-18.
  - `nightly_stdout_attempt1.log`: `captcha block detected` al abrir la SERP,
    sin llegar a parsear tarjetas.
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

- **Playbook:** §6.1 InfoJobs — bloqueo por CAPTCHA.
- **Hechos observados:** `blocked=True` desde 2026-09-18; 0 ofertas; los cuatro
  marcadores de CAPTCHA no registran cuál dispara.
- **Hipótesis a comprobar:** el challenge es de reputación (IP/perfil) y no del
  parser; una sesión real con cookies y ritmo humano puede evitarlo;
  `robots.txt` permite la SERP y bloquea el detalle.
- **Comprobaciones:** navegador real con el perfil del usuario (`browser-cdp` +
  `agent-browser --cdp 9222`), respuesta de la SERP, marcador de CAPTCHA que
  dispara, endpoint JSON interno permitido y `robots.txt`/TOS.
- **Causa probable:** challenge de reputación por sesión no persistente.
- **Cambio recomendado:** reutilizar la sesión y las cookies/clearance tokens
  reales, cabeceras y ritmo humanos, registrar el motivo del bloqueo y dejar
  una pausa asistida como contingencia; sin solvers ni servicios de pago.
- **Meta de calidad:** ≥ 1 oferta; obligatorios al 100 % en la muestra según el
  contrato de la fuente (la descripción de la SERP cuenta como `description`
  porque el detalle está bloqueado por `robots.txt`, y se documenta).
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

- **Módulos:** `infojobs_jobs_scraper/navigator.py` (sesión persistente con el
  perfil real, reutilización de cookies de clearance y ritmo humano),
  `infojobs_jobs_scraper/config.py` (cabeceras del perfil y presupuesto de
  peticiones) y tests del scraper.
- **Dependencias nuevas:** ninguna (se reutilizan patchright y la biblioteca
  estándar).
- **Diseño:** el scraper deja de arrancar un contexto limpio por ejecución;
  reutiliza la sesión validada una vez y registra el marcador de bloqueo que
  dispara.
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

- `infojobs_jobs_scraper`: `python -m pytest tests -q` → OK; `ruff check
  scraper tests` → OK; `mypy scraper` → OK.
- `scrapers-pipeline`: `python -m pytest tests -q` → OK.
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

- **Alcance:** 1 keyword × 1 ciudad × 1 página; sin subir datos a Azure, sin
  landing y sin merge.
- **Comando:** ejecución acotada del scraper de InfoJobs.
- **Recuento:** 3 ofertas.
- **Umbral:** 1 (primera reparación de la fuente).
- **Evidencia:** recorte de log con el recuento de tarjetas parseadas y
  `quality_after.json`.
- **CAPTCHA:** no apareció challenge visible en la prueba.
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

Tabla antes/después por campo (`quality_before.json` → `quality_after.json`):

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| id | no | sin dato | 100.0 % | sin dato | 100 % | cumple |
| title | sí | sin dato | 100.0 % | sin dato | 100 % | cumple |
| company | sí | sin dato | 100.0 % | sin dato | 100 % | cumple |
| description | sí | sin dato | 100.0 % | sin dato | 100 % | cumple |
| salary | no | sin dato | 100.0 % | sin dato | 100 % | cumple |
| skills | no | sin dato | 0.0 % | sin dato | sin meta | no aplica |
| work_mode | no | sin dato | 100.0 % | sin dato | 100 % | cumple |
| location | no | sin dato | 100.0 % | sin dato | 100 % | cumple |
| posted_date | no | sin dato | 100.0 % | sin dato | 100 % | cumple |

Veredicto de `quality.py`: **OK** (obligatorios al 100 %, sin regresión y metas
alcanzadas; `skills` no es un campo del scraper y no bloquea).
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** fuente reparada; prueba en vivo con 3 ofertas (umbral 1) y
  puerta de calidad superada.
- **Estado:** probado.
- **Validación del push:** pendiente de la persona (RF-12).
<!-- /record:section:result -->
