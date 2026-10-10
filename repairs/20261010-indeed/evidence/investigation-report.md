# Informe de investigación — Indeed (repairs/20261010-indeed)

- **Fecha**: 2026-10-10 (hora local), 19:38–19:45 aprox.
- **Alcance (T-32, playbook §6.2 "Indeed - description at 0% and second page")**: SERP NL `q=data&l=Rotterdam&fromage=7` sobre la pestaña ya abierta del Chrome 9222 de la persona; runs de origen 2026-10-10 (outcome error) y hechos del run 2026-10-03 (enrich 0/53–0/72).
- **Herramientas**: `agent-browser --cdp 9222` (get/eval/screenshot/reload + lectura del endpoint CDP `/json/list`). Nunca se usó `open` como primer paso, nunca se cerró la sesión ni se reinició Chrome, no se ejecutaron scrapers completos, no se usaron solvers ni servicios de pago. Una única recarga acotada de la misma pestaña.
- **Aviso**: durante toda la observación estuvo activo un **challenge interactivo de Cloudflare (Turnstile)** en la pestaña de la SERP. Por política (RF-4/RF-5) **no se intentó resolver**. Gran parte de la verificación estructural en vivo quedó por tanto **bloqueada** y se documenta como pendiente.

## 1. Hechos observados

### 1.1 Estado en vivo de la sesión (2026-10-10, ~19:39)
- `agent-browser --cdp 9222 get url` responde y devuelve `https://nl.indeed.com/jobs?q=data&l=Rotterdam&fromage=7`.
- El **contenido servido** en esa pestaña es un bloqueo de Cloudflare, no la SERP: título real `Security Check - Indeed.com`; texto "**Verificación adicional requerida**"; `div.cardOutline: 0`, `a[data-jk]: 0`, sin `<script id="mosaic-data">`; `loginRedirect: false` (la URL no cambia).
- Ray IDs: `a486110d5ccc0391` (estado inicial) y, tras una recarga de la misma pestaña, `a48768f3ff7a80ae`. El bloqueo **persiste** en recarga.
- El endpoint CDP `/json/list` muestra un target iframe de `https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/g/turnstile/f/av0/rch/d32ty/0x***/light/...` → **Turnstile interactivo activo** en esa pestaña.
- La captura muestra el botón "Verifique que es un ser humano" y el chrome de Indeed en apariencia **no logueada** ("Iniciar sesión" en la cabecera). No se interactuó con ningún elemento de cuenta.
- Evidencias: `indeed-serp-probe-01.json`, `indeed-serp-probe-02-challenge-markers.json`, `indeed-serp-probe-03-post-reload.json`, `indeed-cdp-targets-9222.json`, `indeed-reload-01.txt`, captura `indeed-20261010-cf-challenge.png` (y la original en `.opencode/.agent-screenshots/20261010-194002-indeed-cf-before-reload.png`).

### 1.2 Conectividad CDP (hechos fijados; no repetidos)
- patchright 1.60.1 → CDP 9222: falla el handshake (`ProtocolError: Network.setCacheDisabled ... session closed`) o agota timeout (20 s en sonda; **180 s en el run nocturno**). Control contra 9333: conecta en 0,7 s.
- `agent-browser --cdp 9222` sí opera contra el mismo Chrome: **no es que CDP 9222 esté caído, es el cliente patchright quien no completa la conexión**.
- Run nocturno 2026-10-10: 4 intentos (NL×2, IE×2), todos abortados en `connect_over_cdp` (log 00:00→00:03). Ninguna página se llegó a cargar; el marcador queda `last_run=20261010_003408 exit=-1 terms=data,data engineer,data analyst,data scientist countries=NL,IE pages=2`. El pre-flight del wrapper (`/json`) pasó: Chrome respondía.
- Evidencias: `indeed-cdp-probe-9222.txt`, `indeed-cdp-probe-9333.txt`, `indeed-nightly-20261010-cdp-timeout.txt`, `indeed-nightly-20261010-config.txt`.

### 1.3 Run 2026-10-03 (último con scraping real; breve)
- La SERP sí se parseó entonces por `mosaic-data` ("parseadas 15/6/… ofertas via JSON embebido").
- Enriquecimiento: todas las SERPs registran `enriquecidas 0/N clics` -> metadata `enrich_success 0/53` y `description_filled 0/57` (run 00:06); `enrich_success 0/72` y `description_filled 3/68` (run 12:47; esas 3 proceden del **desc-cache**, log `desc-cache: N ofertas rellenadas sin clics`, no de extraccion). En ambos: cdp 9222, "logged_in": true, limit 0, pages 2.
- Página 2 **siempre** redirigió a login ("Indeed redirige a login en pagina 2… Se omite esta pagina"), pese a que `_verify_cdp_session` (start=10) se supone que había validado la sesión.
- CAPTCHA CF real a las 01:03 (data analyst/Dublin): `iframe[src*="challenges.cloudflare.com"]`, título `Security Check - Indeed.com`.
- Lineas `descripciones from _initialData`: **0 en ambos runs de octubre** (la via embebida no extrajo nada en esos logs).
- Evidencias: `indeed-run-20261003-excerpt.txt`, `indeed-metadata-20261003-0006.json`.

### 1.4 Histórico de enriquecimiento (logs locales)
- 2026-06-21: `enriquecidas 15/15 ofertas via panel derecho` (código antiguo).
- 2026-07-23/24: parciales `1–4/15` (rate 30 %) y `_initialData: 1/15`.
- Octubre: 0. `via /viewjob` **no aparece nunca** en ningún log (matiz: la línea de éxito de `/viewjob` es `log.debug` y el logging de producción es INFO, así que su ausencia no prueba que nunca funcionara; sí prueba que nunca sumó a los contadores de éxito en octubre).
- Git del repo: un único commit (`2026-09-19`), sin historia que feche la reescritura del enrich.

### 1.5 Cobertura real del último export correcto (2026-10-06, 54 ofertas)
Medida sobre `indeed_jobs_20261006_0000.parquet` (evidencia `indeed-field-coverage-20261006.txt`):

| campo | cobertura |
|---|---|
| job_key / title / company / location / posted_date / snippet | 100 % |
| description_html / description_text (>50 chars) | **0 %** |
| salary_text / salary_min / salary_max / salary_type | 61 % |
| workplace_type | 37 % (valores: `''`, `'remote'`) |
| benefits / contract_type / schedule / job_type | 0 % |
| `skills` | no existe columna |

### 1.6 robots.txt (ficheros ya descargados)
Para `User-agent: *` (NL e IE idénticos en lo relevante):
- `Allow: /*&start=0&` … `/*&start=90&` → la SERP y su paginación por URL están **permitidas**.
- `Disallow: /viewjob` y `/m/viewjob` → **prohibido** el detalle directo (el fallback que usa `parser.py:561-589`).
- `Disallow: /graphql` → **prohibido** el endpoint interno GraphQL.

### 1.7 Código revisado (solo lectura)
- `parser.py:314-355` `parse_serp`: fuente principal `mosaic-data` → `providerData["mosaic-provider-jobcards"]` (funciona; 15 ofertas/SERP en octubre).
- `parser.py:180-226` `_extract_initial_data_descriptions`: busca `window._initialData =` en el HTML crudo para sacar la descripción del panel auto-abierto. En octubre no produce nada (0 líneas de log).
- `parser.py:427-634` `enrich_offers_via_right_panel`: clic en `a[data-jk]` → `wait_for_selector(#jobDescriptionText…)` **8 s** → si expira, `continue` (527-536) que **salta el fallback**; el fallback `/viewjob` (561-589) solo corre si la espera encontró algo pero ningún selector de descripción dio texto. Condiciones del panel → `_map_conditions_to_offer` (591-618).
- `parser.py:638-714` fallback HTML: `div.cardOutline`, `a[data-jk]`/`a.jcs-JobTitle`, `[data-testid=company-name]`, `[data-testid=text-location]`, `[data-testid*=salary-snippet-container]`, `[data-testid=belowJobSnippet]`.
- `main.py:74-78` `--limit`: el help afirma que `--limit 50` "evita el login" de la paginación; `config.py:43-45` añade `&limit=`. La skill local `indeed-anti-bot` documenta lo contrario: `&limit=25/50` dispara "Security Check — Additional Verification Required" de **Cloudflare** (con Ray ID), justo la página capturada hoy.
- `runner.py:186-199`: página 2 con usuario no logueado → detecta redirect a login y **omite la página** con aviso.
- `run_nightly_indeed.ps1`: args del run nocturno = `--cdp 9222 --pages 2 --fromage 7 --enrich-rate 0.9 --enrich-max 15 --pause-every 2` (6–10 min), 4 términos × 2 ciudades × 2 páginas = 16 SERPs/país, **sin `--limit`**. `MAX_SERPS_PER_RUN=50`.
- Dump histórico junio (`diag_ES_Madrid_1_20260620_1651.html`): el panel derecho existía con `jobsearch-JobInfoHeader-title` y `jobDescriptionText`; coherente con los 15/15 de junio.

## 2. Hipótesis (explícitas)

| # | hipótesis | estado | fundamento |
|---|---|---|---|
| H1 | El fallo del 10-10 se debe al handshake CDP de patchright (misma causa que la sonda) | **Confirmada** | Los 4 intentos abortan en `connect_over_cdp` 180 s; ninguna página carga |
| H2 | La pestaña/sesión está (además) bajo challenge de Cloudflare | **Confirmada** | Bloqueo "Additional Verification Required" + Turnstile en `/json/list`; persiste tras recarga |
| H3 | Los selectores del panel (`#jobDescriptionText`) siguen vigentes pero el panel no carga en 8 s | **Pendiente** (bloqueo impide verificar en vivo) | Logs muestran 0/N consistentes; histórico junio/julio sí funcionaba |
| H4 | `window._initialData =` ya no existe con ese nombre en el HTML (extracción obsoleta) | **Probable, no verificable en vivo** | 0 lineas `descripciones from _initialData` en ambos runs de octubre (los 3/68 del run 12:47 vienen del desc-cache); la skill local ya avisa de que Indeed borra variables y solo quedan scripts embebidos |
| H5 | Página 2 exige login real; el perfil CDP no está logueado de verdad | **Confirmada en logs (10-03); compatible con la vista actual** | Redirect a login en todas las páginas 2; cabecera "Iniciar sesión" en la vista actual |
| H6 | `--limit` dispara Cloudflare (contradice el help de `main.py`) | **No re-testado** (el nightly no lo usa; el bloqueo actual coincide con su firma) | Skills local + captura del bloqueo de hoy |
| H7 | `/viewjob` devuelve descripción completa | **No probado por política**: robots lo prohíbe para `*`; no se ejecutó ninguna petición a `/viewjob` | `Disallow: /viewjob` en NL/IE |

## 3. Evidencias

Rutas versionadas en `repairs/20261010-indeed/evidence/` (capturas fuera de git en `.opencode/.agent-screenshots/`):

| fichero | contenido |
|---|---|
| `indeed-serp-probe-01.json` | estado de la pestaña: challenge, 0 cards |
| `indeed-serp-probe-02-challenge-markers.json` | título CF, "Verificación adicional requerida", Ray ID |
| `indeed-serp-probe-03-post-reload.json` | bloqueo persiste tras recarga (nuevo Ray ID) |
| `indeed-cdp-targets-9222.json` | targets CDP: iframe Turnstile, pestaña SERP, "Nueva pestaña" |
| `indeed-reload-01.txt`, `indeed-screenshot-cf-01.txt` | salidas de reload y screenshot |
| `indeed-20261010-cf-challenge.png` | captura del bloqueo (copia; original en `.opencode/.agent-screenshots/20261010-194002-indeed-cf-before-reload.png`) |
| `indeed-nightly-20261010-cdp-timeout.txt` | log saneado del run 10-10 (timeout CDP) |
| `indeed-nightly-20261010-config.txt` | marcador del run + args del wrapper |
| `indeed-run-20261003-excerpt.txt` | extracto del run 10-03 (0/N enrich, page-2 login, CAPTCHA) |
| `indeed-metadata-20261003-0006.json` | metadata: enrich 0/53, desc 0/57, cdp 9222, limit 0 |
| `indeed-field-coverage-20261006.txt` | cobertura por campo del último export correcto |
| `indeed-robots-nl.txt`, `indeed-robots-ie.txt` | robots.txt |
| `indeed-cdp-probe-9222.txt`, `indeed-cdp-probe-9333.txt` | sondas patchright (previas) |

## 4. Causa probable

1. **Fallo del run 10-10 (outcome error)**: incompatibilidad/handshake roto entre patchright 1.60.1 y el CDP del Chrome 9222 (posiblemente por versión de Chrome 154). El scraper muere en la conexión, sin llegar a scrapear. El pre-flight actual (HTTP `/json`) no detecta este caso.
2. **Descripción al 0 %** (persistente en 10-03/10-06, cuando sí había sesión operativa):
   - el clic en la tarjeta no produce `#jobDescriptionText` en 8 s y el `continue` (parser.py:527-536) **aborta la oferta sin pasar al fallback**;
   - la extracción del panel auto-abierto (`window._initialData =`) devuelve 0 en los runs de octubre (contenedor renombrado o fuera de ese patrón); los unicos rellenos salen del desc-cache;
   - el único fallback restante (`/viewjob`) está **prohibido por robots.txt** y, además, en el diseño actual es inalcanzable por el orden del código.
3. **Página 2**: requiere sesión autenticada real de Indeed; el perfil del 9222 no parece logueado (redirect a login en 10-03; cabecera "Iniciar sesión" al ver el bloqueo hoy).
4. **`--limit`**: no lo usa el nightly actual (limit 0), pero si se reintrodujera para "evitar el login", activa Cloudflare según la skill local — mismo bloqueo capturado hoy.
5. **Challenge interactivo activo ahora mismo**: la sesión/IP está siendo desafiada; **sin que la persona lo resuelva (o sin esperar a que caduque), ninguna re-ejecución sobre esta IP puede funcionar**.

## 5. Auditoría de cobertura de campos

| campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal |
|---|---|---|---|---|---|
| `id` | Sí: `jobkey`/`data-jk` en SERP | Sí (100 %) | — | mantener | SERP permitida (`/jobs&start=0..90&`) |
| `title` | Sí: `displayTitle` SERP | Sí (100 %) | — | mantener | SERP permitida |
| `company` | Sí: `company` SERP | Sí (100 %) | — | mantener | SERP permitida |
| `description` | Parcial inherente: descripción del job auto-abierto va embebida en el HTML de la SERP; el resto, vía panel derecho de la SERP o detalle `/viewjob` (prohibido) | **No (0 %)** | total | 1) corregir extracción del panel auto-abierto desde scripts embebidos (buscar `autoOpenTwoPaneViewjobResponse`, no `window._initialData =`); 2) eliminar el `continue` prematuro y convertir el wait del panel en cadena de esperas/selectores con reintento; 3) **no** depender de `/viewjob` | SERP y su panel: permitido; `/viewjob` y `/graphql`: **prohibidos** para `*` |
| `salary` | Sí cuando el portal lo publica (`salarySnippet`/`extractedSalary`, ~3/16 por SERP) | Sí (61 %) | portal no publica el resto | mantener mapeo actual; dado que el portal no publica salario en todas, el 100 % es inalcanzable | SERP permitida |
| `skills` | No hay campo dedicado; solo podría derivarse de la descripción | No (no existe columna ni requisito) | n/a | no exigido por el brief; no prometer | — |
| `work_mode` | Parcial: `remoteWorkModel` (SERP) para una minoría; el resto, en condiciones del detalle/panel | Parcial (37 % `workplace_type`; `is_remote` 100 % booleano) | 63 % | mapear `remoteWorkModel` y derivar de `translatedAttributes`/snippet; completar con texto del panel cuando se repare; documentar lo no publicado | SERP permitida; panel dentro de SERP permitida |
| `location` | Sí: `formattedLocation` | Sí (100 %) | — | mantener | SERP permitida |
| `posted_date` | Sí: `pubDate`/`createDate` | Sí (100 %) | — | mantener | SERP permitida |

## 6. Meta de calidad propuesta (alcance de prueba: 1 país × 1 término × 1 página)

- `title`, `company`: **100 %** (requisito duro).
- `description` (texto completo): **100 % en el alcance de prueba** (1 SERP). Mínimo garantizado por diseño: 1 descripción del panel auto-abierto embebida en el HTML; el resto por clic si el panel vuelve a responder.
- `id`, `location`, `posted_date`: **100 %** (ya se cumple).
- `salary`: **≥ 61 %** (cobertura actual del portal; el 100 % no depende del scraper).
- `work_mode`: **≥ 37 %** actual, aspirando a más con el panel reparado; no se promete el 100 % porque el portal no lo publica siempre.
- `skills`: sin meta (campo no exigido y sin columna).
- Ninguna meta baja respecto a la cobertura actual del último export correcto.

## 7. Cambio recomendado (por módulo/función; el orquestador decide)

1. **`scraper/browser.py` (conexión CDP)**: hacer el handshake verificable y con recuperación: probe explícito de `connect_over_cdp` (con `page` de prueba) antes del run; si falla, (a) reintentar con backoff y timeout mayor, (b) probar actualización de patchright/playwright compatible con Chrome 154, y (c) fallback documentado a navegador propio headed para runs de 1 página (sin depender de login). Nivel de escalera §6.0: navegador real con el perfil/sesión de la persona (nada de headless, nada de solvers).
2. **`scraper/parser.py` · `_extract_initial_data_descriptions`**: sustituir el patrón `window._initialData\s*=` por una búsqueda tolerante del contenedor real (`autoOpenTwoPaneViewjobResponse` / claves JSON equivalentes) en los `<script>` embebidos, con balanceo de llaves (ya existe `_balance_braces`). Esto da ≥1 descripción/SERP por la vía permitida (HTML de la SERP).
3. **`scraper/parser.py` · `enrich_offers_via_right_panel`**: eliminar el `continue` tras el timeout del panel; convertir el flujo en cadena: esperar `[data-testid=jobsearch-JobInfoHeader-title]` + `#jobDescriptionText` con timeouts escalonados (p. ej. 4 s → 8 s), verificar que la tarjeta quedó activa (`vjs-highlight`), y solo tras agotar selectores registrar la oferta como fallida (sin `continue` silencioso). Emitir progreso del enrich (el playbook lo pide). **No** reactivar `/viewjob` sin decisión explícita de la persona (robots lo prohíbe).
4. **`scraper/runner.py`**: en modo CDP, si el handshake falla, no abortar en seco: log claro + fallback a 1 página con navegador propio, o salida controlada con causa CDP; en el alcance de reparación usar `pages=1` (evita login de página 2 y el debate de `--limit`).
5. **`main.py` / `scraper/config.py`**: corregir el texto de `--limit` (hoy promete "evitar el login"; en realidad dispara Cloudflare) y mantener `limit=0`; documentar en la skill `indeed-structure` que la vía para más resultados por SERP es `&start=` permitido con sesión real, no `&limit=`.
6. **`run_nightly_indeed.ps1`**: el pre-flight solo comprueba que `/json` responde; añadir probe de handshake CDP real (o moverlo al scraper). No tocar volumen hasta que la persona decida.

## 8. Comprobación manual propuesta (acotada y repetible)

1. **Persona** (necesario, no automatizable): en la ventana del Chrome 9222, resolver el challenge visible ("Verifique que es un ser humano") y confirmar que la SERP carga con tarjetas; si no, esperar 24 h antes de reintentar. No cerrar esa ventana.
2. Verificar sesión: en la misma ventana, comprobar en Indeed que aparece el estado logueado (para página 2). Para el alcance de reparación no es imprescindible (1 página).
3. Reproducir el caso base desde `indeed_jobs_scraper`:
   `python main.py --country NL --cities Rotterdam --term data --pages 1 --fromage 7 --cdp 9222 --enrich --enrich-rate 1.0 --enrich-max 15`
4. Comprobar en el log: `parseadas ~15 ofertas … via JSON embebido`; en el enrich, al menos 1 línea `descripciones from _initialData` o `enriquecidas X/Y clics` con **X ≥ 1**; ausencia de `CAPTCHA detectado`.
5. Comprobar el export (`indeed_jobs_<ts>.parquet`): `description_html` no vacío (>50 chars) en las ofertas clicadas; cobertura de `title/company/location/posted_date` al 100 %.
6. Regresión: repetir la comprobación con 1 SERP y `--limit` ausente (nunca añadirlo en la prueba).

## 9. Riesgos y límites

- **Challenge interactivo activo**: el perfil/IP está desafiado ahora mismo; sin resolución humana (o espera), cualquier re-ejecución fallará. No se resolvió ni se intentó (política). El reload acotado evidencia persistencia; no se hicieron más peticiones.
- **Verificación estructural en vivo bloqueada**: no se pudo comprobar `#jobDescriptionText` tras clic, ni red del panel, ni página 2, ni JSON-LD del detalle. Queda como pendiente la re-verificación tras despejar el challenge. Las conclusiones de estructura se apoyan en logs históricos (junio/julio) y en el código, señaladas como tales.
- **patchright ↔ CDP 9222**: causa raíz exacta no determinada (fallo en `Network.setCacheDisabled`/handshake con Chrome 154); se documenta como hecho. Si no se corrige, el modo CDP nocturno sigue siendo frágil.
- **Legales**: `/viewjob` y `/graphql` están prohibidos por robots.txt para `User-agent: *`; el fallback actual de `parser.py` choca con esa regla. **No se ejecutó ninguna petición a esas rutas**. La decisión de mantener, gatear o retirar el fallback (y cualquier uso bajo TOS) queda para la persona vía orquestador.
- **Volumen**: 16 SERPs/país y hasta 15 clics/SERP por noche exceden la recomendación sin proxy de la skill local (≤6 SERPs); no se ha cambiado nada, pero es factor de riesgo de challenge.
- **Login**: estado real de sesión del perfil incierto; la página 2 dependía de él y falló el 10-03 aun asumiendo "logged_in": true.
- **No comprobado**: endpoints de red del panel, JSON-LD del detalle, comportamiento de `&start=10` con sesión válida, `--limit` en vivo. Todo ello por el bloqueo.