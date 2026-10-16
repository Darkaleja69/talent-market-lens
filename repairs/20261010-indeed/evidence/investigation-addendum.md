# Addendum de investigación — Indeed (T-32, iteración 2) — 2026-10-11

- **Alcance**: SERP NL `https://nl.indeed.com/jobs?q=data&l=Rotterdam&radius=25&fromage=7`
  (la misma URL del run en vivo del 11-10), abierta en una **pestaña nueva** (t2)
  del Chrome 9222 de la persona (sesión real, sin challenge en el momento de la
  prueba). 2 clics acotados + 1 recarga + 1 vuelco de HTML. Chrome no se cerró ni
  reinició; la pestaña original de la persona (t1, `es.indeed.com/?vjk=...`) quedó
  intacta.
- **Hora local**: 15:11–15:24 aprox.
- **Herramientas**: `agent-browser --cdp 9222` (get/eval/click/network/screenshot) y
  ejecución **offline** de las funciones del parser contra el HTML volcado.
- **Resultado en una línea**: la descripción SÍ está disponible (embebida en el
  HTML de la SERP y en el panel tras el clic), pero el scraper mira rutas/selectores
  jubilados; el `snippet` no lo publica el portal hoy. No hubo bloqueo.

## 1. Hechos observados

### 1.1 La SERP carga en primer plano, sin challenge
- Título normal ("Data - vacatures in Rotterdam - 11 oktober 2026 | Indeed"); sin
  "Security Check"/Turnstile; `errors` de página vacío; `redirectCount 0`.
- DOM: 12 `div.cardOutline` / 12 `a[data-jk]`; el JSON `mosaic-data` trae **11
  resultados** (el jk extra `a1b2c3d4e5f67890` es un placeholder que no está en el
  JSON). El parser extrae las 11 correctamente.

### 1.2 `snippet`: vacío en la página real (no es un fallo del scraper)
- En `mosaic-provider-jobcards.results`: clave `snippet` presente y **"" en 11/11**;
  no hay `jobSnippet`; en el DOM no existe ningún `[data-testid=belowJobSnippet]`.
- El "snippet vacío en 11/11" del export del run es **fiel a la página**: la
  variante de SERP servida hoy no publica extracto en las tarjetas. (El 06-10 el
  export mostraba snippet 100 %; el portal cambió/varió, no el scraper.)

### 1.3 El JSON auto-abierto SÍ está, con otra forma: causa principal de la descripción al 0 %
- `autoOpenTwoPaneViewjobResponse` está **presente** en el HTML (bundle inline de
  ~206 KB dentro del HTML SSR; `redirectCount 0`).
- Su `body` ya **no** tiene `hostQueryExecutionResult.data.jobData.results` (la ruta
  que navega `_descriptions_from_auto_open`). `hostQueryExecutionResult` **no
  aparece** en ninguna parte de la página (1,17 MB de HTML).
- La descripción vive ahora en
  `body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription` (HTML; 3.037
  caracteres para `c74ad662adfe7b9f`, "Fullstack BI-Specialist").
- **Reproducción offline con el propio parser** (ver
  `indeed-20261011-offline-parser-check.txt`): `_extract_jobcards_results` → 11;
  `_extract_initial_data_descriptions` → **0**; `_iter_auto_open_responses` → 2
  candidatos válidos con la descripción en la ruta nueva.
- La función falla **en silencio** (devuelve `{}`), por eso no aparece la línea
  "descripciones from _initialData": **la ausencia de esa línea NO prueba que el
  contenedor no se sirviera**.

### 1.4 El panel derecho SÍ hidrata tras el clic, con testids nuevos
- Clic en `b7041f40c1315659` → panel con "Product data specialist/PIM Specialist",
  "Volledige vacaturetekst" y ~2.767 chars de descripción (contenedor total ~4.266
  chars); tarjeta activa (`vjs-highlight`).
- Clic en `c74ad662adfe7b9f` (la auto-abierta) → "Fullstack BI-Specialist"; tarjeta
  activa. Recarga: mismo resultado.
- En los tres estados los selectores del scraper dan **0**: `#jobDescriptionText`,
  `[data-testid=jobDescriptionText]`, `[data-testid=jobsearch-JobInfoHeader-title]`.
  La UI two-pane actual usa `viewjob-main-content`, `vj-job-title`,
  `viewjob-job-content`, `vj-job-description-heading` (lista completa en
  `indeed-20261011-panel-selectors.json`).
- Red del clic (`indeed-20261011-network-clicks.txt`): el contenido del panel llega
  con `GET /viewjob?jk=<jk>&from=vjs&viewtype=embedded` (200) que dispara la propia
  página; los `POST apis.indeed.com/graphql` observados son solo
  `SaveJobButton`/`RecordJobSeen`.

### 1.5 Comparación de escenarios (Q4)
- (a) Carga nueva de la misma URL en primer plano: descripción disponible (embebida
  + panel), `snippet` vacío en 11/11.
- (b) Pestaña original de la persona: era su home ES con detalle `vjk` (descripción
  renderizada; allí tampoco existen `#jobDescriptionText`/`jobsearch-*`).
- (c) No se hizo una tercera carga (alcance mínimo); la recarga de t2 reprodujo lo mismo.
- (d) Pestaña en segundo plano: **no comprobada** (ver hipótesis H9).

## 2. Hipótesis (rankeadas)

| # | hipótesis | estado | fundamento |
|---|---|---|---|
| H3' | El panel hidrata pero el scraper espera selectores jubilados (`jobsearch-*`, `#jobDescriptionText`) | **Confirmada** | 0 coincidencias en los 3 estados; contenido visible bajo testids `vj-*`/`viewjob-*` |
| H4' | El contenedor `autoOpen` sigue en el HTML pero su ruta interna cambió; el parser devuelve `{}` en silencio | **Confirmada** | Reproducción offline con el parser real: 0 descripciones; ruta nueva localizada |
| H8 | "autoOpen ausente en el run" era una inferencia no observada | **Probable** | La línea de log solo se emite si `filled>0`; hoy el contenedor está presente en la misma URL y sesión |
| H9 | Un posible efecto de "pestaña en segundo plano" (throttling del fetch/hidratación) en el run | **No comprobada; no necesaria como causa** | En el run los clics sí activaron `vjs-highlight` (JS vivo); con los selectores correctos el fallo queda explicado sin visibilidad. No se replicó t2 en segundo plano; mitigación barata: esperar el selector de red/DOM adecuado |
| H5/H6 | Página 2 con login / `--limit` con Cloudflare | fuera de alcance | sin cambios en esta iteración |

## 3. Causa probable

1. **Descripción al 0 %**: doble rotura en `scraper/parser.py` —
   (a) `_descriptions_from_auto_open` navega una ruta que ya no existe
   (`hostQueryExecutionResult...`) en vez de
   `jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription`;
   (b) `_wait_for_right_panel`/`_read_panel_description` solo conocen los selectores
   del layout antiguo. El portal sí sirve la descripción (embebida y vía panel al
   clicar).
2. **Snippet vacío**: el portal no publica extracto en esta variante de SERP
   (`snippet` "" 11/11; sin `belowJobSnippet`). No era una regresión del scraper.
3. **`autoOpen` "ausente"**: reformulado — el contenedor estaba en el HTML; la
   extracción fallaba en silencio por la ruta obsoleta.

## 4. Auditoría de cobertura de campos (perfil de la puerta de calidad)

| campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal |
|---|---|---|---|---|---|
| `description` | Sí: (a) embebida en el HTML de la SERP para la oferta auto-abierta (`autoOpenTwoPaneViewjobResponse.body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription`); (b) panel derecho tras clic, servido por `GET /viewjob?...&viewtype=embedded` que dispara la página | **No (0 %)** | total | (a) corregir la ruta del parser (fallback a la antigua); (b) añadir selectores `viewjob-job-content`/`vj-*` al panel; (c) **decisión de la persona** sobre depender del fetch interno a `/viewjob` | (a) SERP permitida; (b) `/viewjob` está `Disallow` para `*`: el clic es la vía del propio UI, pero la decisión legal queda para la persona |
| `snippet` | No publicado hoy: `snippet` = "" en 11/11 del JSON y sin `belowJobSnippet` en DOM | Sí se lee, pero vacío | no es fallo | mantener lectura; no prometerlo | SERP permitida |
| `title` / `company` / `id` / `location` / `posted_date` | Sí (JSON de tarjetas) | Sí (100 %) | — | mantener | SERP permitida |
| `salary` | Sí cuando el portal lo publica (`salarySnippet`/`extractedSalary`) | Sí (90,9 %) | portal no publica el resto | mantener | SERP permitida |
| `work_mode` | Parcial (`remoteWorkModel`/atributos; el resto, en el panel: `structured-job-summary`) | Parcial (54,5 %) | se completará al reparar el panel | leer condiciones de `structured-job-summary`/`viewjob-job-content` tras el clic | panel vía UI |
| `skills` | No hay campo dedicado; solo derivable de la descripción | No | — | no prometer (no exigido) | — |

## 5. Meta de calidad propuesta

- `description`: **100 % en el alcance de prueba** (1 SERP) combinando (a) embebida
  + (b) panel reparado. Si la persona veta la vía del panel por el `Disallow` de
  `/viewjob`, el mínimo garantizado es **1/SERP** (la auto-abierta) y no se promete
  el 100 %.
- `title`, `company`: 100 % (requisito duro; ya se cumple).
- `id`, `location`, `posted_date`: 100 % (ya se cumple).
- `salary`: ≥ 90,9 % (actual; el 100 % no depende del scraper).
- `work_mode`: ≥ 54,5 % actual, aspirando a más con las condiciones del panel.
- `snippet`: no está en la puerta; documentar que el portal no lo publica hoy.
- Ninguna meta baja respecto a la cobertura actual.

## 6. Cambio recomendado (concreto, sin editar)

1. `scraper/parser.py` · `_descriptions_from_auto_open`: conservar la ruta antigua
   como fallback y añadir la nueva:
   `body["jobInfoWrapperModel"]["jobInfoModel"]["sanitizedJobDescription"]`
   (y `body["jobKey"]` como clave). Mantener el `json.loads` si `body` es string.
2. `scraper/parser.py` · `_PANEL_HEADER_SELECTOR`/`_PANEL_DESC_SELECTORS`: cadena
   nueva primero — título `[data-testid="vj-job-title"]`; descripción
   `[data-testid="viewjob-job-content"]` y, como respaldo, el contenedor padre de
   `[data-testid="vj-job-description-heading"]`; conservar los `jobsearch-*` y
   `#jobDescriptionText` al final como fallback.
3. `scraper/parser.py` · `_read_panel_description`: aceptar los contenedores nuevos
   (el umbral `_MIN_DESC_HTML_LEN` sigue valiendo: `viewjob-job-content` ronda 8 KB
   de HTML).
4. `scraper/parser.py` · condiciones: `[data-testid="structured-job-summary"]`
   añadido a `cond_selectors` (atributos del panel nuevo).
5. **Legal (decide la persona)**: el panel se rellena con un fetch interno a
   `/viewjob` (`viewtype=embedded`), ruta `Disallow` para `*` en robots.txt. El
   scraper solo clica y lee DOM; si se quiere mantener estrictamente fuera de
   rutas prohibidas, la vía (a) embebida no toca `/viewjob`.
6. Opcional (no necesario para el fix): si al implementador le consta que el panel
   no carga con la pestaña en segundo plano, `page.bring_to_front()`/espera de red;
   hacerlo con cuidado (roba el foco a la persona). No se pudo verificar H9.
7. Sin cambios en `--limit`/página 2 (`/viewjob` y `/graphql` siguen vetados como
   llamadas directas del scraper).

## 7. Comprobación manual propuesta

1. `agent-browser --cdp 9222 get url` → confirmar la SERP (t2 o la que abra el run).
2. `agent-browser --cdp 9222 eval "document.querySelectorAll('[data-testid=viewjob-job-content]').length"` → 1 (panel auto-abierto) en la UI nueva.
3. Reproducir la reproducción offline: volcar el HTML y ejecutar
   `<TEMP>\check_parser_offline.py`; con el fix, `parser_initial_descriptions_count`
   debe ser ≥ 1 y la ruta nueva debe leerse.
4. Tras el cambio, repetir el run acotado de RF-9:
   `python main.py --country NL --cities Rotterdam --term data --pages 1 --fromage 7 --cdp 9222 --enrich --enrich-rate 1.0 --enrich-max 15`
   y comprobar en el log: "descripciones from _initialData (autoOpen embedded JSON):
   ≥1/11" y `enriquecidas 11/11 ... con descripcion: > 0`; sin `CAPTCHA detectado`.
5. Correr la puerta de calidad sobre el parquet nuevo: `description` debe cumplir
   la meta del apartado 5.
6. Regresión: `python -m pytest tests -q` en `indeed_jobs_scraper` (fixtures).

## 8. Riesgos y límites

- **`/viewjob` en robots**: el fetch que rellena el panel es un `Disallow` de
  robots.txt para `*` (aunque lo dispare la UI). No hice ninguna petición directa a
  `/viewjob` ni `/graphql`; la decisión de mantener la vía panel (con clic) o
  limitarse a la descripción embebida es de la persona vía orquestador. **Fuera de
  medios locales**: es una decisión de TOS/robots.
- **H9 no comprobada**: no se replicó el run en pestaña de fondo; la causa
  principal (selectores/rutas) está probada y es independiente de ese efecto.
- **Variabilidad**: la forma del bundle inline y de los testids puede cambiar de
  nuevo; usar rutas tolerantes con fallback y validar en el pre-flight si es viable.
- **Volumen**: solo 2 clics de prueba; el enrich completo (11 clics) mantiene su
  coste anti-bot, sin cambios.
- **Sesión**: se usó la sesión real de la persona (logueada); no se capturaron
  cookies ni tokens en las evidencias (saneadas; rutas como HOME/TEMP).
- **Challenge**: no apareció ninguno durante esta prueba; si aparece, parar (política).
- **Pestaña t2 dejada abierta** (SERP) y t1 intacta; no se usó `close`.

## 9. Evidencias

| fichero | contenido |
|---|---|
| `indeed-20261011-serp-live-probe.json` | hechos en vivo: DOM, mosaic JSON (snippet 11/11 vacío), autoOpen (ruta nueva), panel tras clic, recarga |
| `indeed-20261011-offline-parser-check.txt` | salida de las funciones reales del parser sobre el HTML volcado (0 descripciones, 2 candidatos autoOpen, ruta nueva) |
| `indeed-20261011-network-clicks.txt` | red de los 2 clics (`/viewjob?viewtype=embedded` 200; GraphQL solo SaveJobButton/RecordJobSeen) |
| `indeed-20261011-panel-selectors.json` | selectores viejos (0) vs testids actuales y mapeo recomendado |
| `indeed-20261011-mosaic-text-keys.txt` | claves de texto del JSON de tarjetas (sin extracto alternativo) |
| `.opencode/.agent-screenshots/20261011-151157-indeed-es-home-vjk-detail.png` | pestaña original t1 (home ES con detalle `vjk`) |
| `.opencode/.agent-screenshots/20261011-151746-indeed-nl-serp-panel-after-click.png` | SERP NL con panel tras clic 2 |
| `.opencode/.agent-screenshots/20261011-152318-indeed-nl-serp-after-reload.png` | SERP tras recarga (auto-abierta) |

Saneado: sin cookies/tokens/credenciales; rutas locales como HOME/TEMP; sin BOM.
Nota de saneado: la evidencia previa `indeed-nightly-20261011-cdp-crash.txt` contenía una ruta local sin sanear (`C:\Users\...`); se sustituyó por `<HOME>` el 2026-10-11 durante esta investigación.
