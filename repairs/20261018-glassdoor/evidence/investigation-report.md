# Investigación web — glassdoor (run 2026-10-18, rama repair/glassdoor-20261018)

- **Fuente:** glassdoor (multi_site; run de origen 2026-10-18, outcome `empty`,
  exit=0, `output_merged=False`; registro en `repairs/20261018-glassdoor/`).
- **Investigación realizada:** 2026-10-18, por la tarde (hora local +02), como
  fase acotada del `implementer` (plan §5 paso 2, §7 y §8).
- **Modo:** solo lectura de código; sin ejecutar el scraper completo ni el
  merge; sin Azure/landing; sin sesión real de Chrome de la persona; sondas
  web con perfil propio (agent-browser) y, de forma acotada y documentada, con
  el perfil persistente del scraper (2 ejecuciones de una sonda mínima, 3
  cargas de página). No se resolvió ningún CAPTCHA (no apareció ninguno).
- **Aviso de trabajo:** no se modificó código ni `specs/`; no hay commit ni
  push. En el árbol de trabajo solo aparece la carpeta de evidencia de este
  registro (`git status`: `?? repairs/20261018-glassdoor/evidence/`).

---

## 1. Hechos observados

### 1.1. Cronología del run fallido (extraída del log)

Fuente: `multi_site_job_scraper/data/glassdoor/run.log`, líneas 18186-18258
(extracto completo saneado en `evidence/run-log-glassdoor-20261018-extract.txt`).

| Hora (+02) | Hecho |
|---|---|
| 00:06:36 | Banner `=== Glassdoor Scraper (Ireland) ===`; objetivo 1000; store reanuda 105 job_ids |
| 00:06:37 | `rebuild_state()` reescribe `state.json` con el store vacío (job_ids 105→0; ver §1.5) |
| 00:09:02 | `Forzando modo headful` + `Lanzando Chromium ... profile=glassdoor` (2 min 25 s después del banner: saturación de arranque) |
| 00:13:43 / 00:14:24 | stderr del navegador: `Network service crashed or was terminated, restarting service` (x2) |
| 00:14:07 | stderr: `CompositorAnimationObserver is active for too long (92 s) location=Button@...` (x3) |
| 00:14:26 | stderr: `Registration URL fetching failed` (GCM) |
| 00:14:29 | `launch_context intento 1/3 fallo: launch_persistent_context: Timeout 300000ms exceeded` (5 min) |
| 00:16:58 | Segundo intento de arranque OK; `playwright-stealth aplicado` |
| 00:17:29 | Homepage `https://www.glassdoor.ie/`: `Page.goto: Timeout 30000ms exceeded`; CSRF token ausente (homepage sin respuesta) |
| 00:17:59 | `resolve_location` (findPopularLocationAjax): `Timeout 20000ms exceeded` → fallback (70, COUNTRY) |
| 00:18-00:25 | Los 6 roles: SERP `Page.goto: Timeout 60000ms exceeded`; cada rol "completado: 0 ofertas nuevas" |
| 00:27:30 | `GLASSDOOR DONE: 0 ofertas nuevas, 0 total en store`; el CSV/Parquet previos se conservan (mtime 17-oct 01:03) |

El run terminó con **exit 0**. El wrapper `run_all.ps1` registró `glassdoor: 0`
en `data/merged/last_run.json`, `watchdog_blocked: []` y excluyó a glassdoor de
`outputs_merged` ([irishjobs, stepstone_nl, devitjobs]). Los dos runs
anteriores del mismo portal y mercado funcionaron (15-oct prueba en vivo: 23;
16-oct: 149; 17-oct: 105).

### 1.2. Misma ventana horaria: otros portales

(Síntesis en `evidence/cross-portal-failures-20261018.txt`.)

- **nvb:** goto de la homepage con timeout 60 s a las 00:09:38 → error.
- **jobs_ch:** errores de navegación a SERP (timeout 60 s) entre 00:11 y 00:22;
  `JOBS_CH DONE: 0` a las 00:25:56.
- **stepstone_nl:** "SERP fallido tras 3 intentos" a las 00:10:19 y 00:28:55
  (y 01:14:20); aun así acabó a las 01:57:52 con 4 ofertas.
- **irishjobs:** timeouts de página de detalle (30 s) a las 00:07:59, 00:09:07,
  00:13:13, 00:14:26, 00:17:47; terminó con exit 0 y salida válida.
- **devitjobs** (API HTTP sin navegador): funcionó con normalidad (00:04:44-
  00:05:41; 16 ofertas).

Matiz: **nvb y jobs_ch ya fallaban de forma crónica** (última salida válida:
10-oct y 05-oct respectivamente), así que sus fallos no prueban por sí solos un
incidente de esa noche; sí lo refuerzan junto a stepstone_nl/irishjobs, que
sufrieron timeouts intermitentes y se recuperaron.

### 1.3. Evidencia de sistema del equipo (Windows)

(Detalle en `evidence/system-events-20261018.txt`.)

- **18/10 00:29:12 — Tcpip Id 4231 (Advertencia):** "Error en una solicitud para
  asignar un número de puerto efímero del espacio de puertos TCP global porque
  todos los puertos están en uso" → **agotamiento de puertos efímeros TCP**.
- **18/10 00:58:02 — Tcpip Id 4266:** ídem para el espacio UDP.
- Historial: 4231 también el 12/10 22:37; 4266 los días 10, 11, 13 y 15/10 →
  **presión de puertos recurrente** en este equipo.
- Wi-Fi Intel (Netwtw08): `Roam Complete` x3 a las 00:31:21; otros roams esa
  noche (23:29, 23:32) y a las 02:17. Sin eventos DHCP/DNS en la ventana.
- Equipo: 8,37 GB de RAM (≈648 MB libres en la consulta de hoy, con 32
  `chrome.exe` del Chrome de la persona + sesión agent-browser), arrancado el
  15/10. Rango dinámico TCP/UDP por defecto (49152 + 16384; sin `MaxUserPort`
  ni `TcpTimedWaitDelay`).
- No hay eventos `Application Error`/WER de chrome.exe o python en la ventana:
  la caída del "Network service" del navegador solo quedó en el stderr que
  recoge Playwright.

### 1.4. Semántica del código (por qué el fallo salió como "empty" y no "error")

- `src/glassdoor/scraper.py::_process_role` captura el fallo de `page.goto` de
  la SERP, lo registra como `Error navegando a SERP` y hace `break` → devuelve 0
  para ese rol; el bucle de roles sigue con el siguiente. Si fallan los 6, el
  run termina "limpio".
- `src/glassdoor/__main__.py::main` devuelve 0 salvo excepción (entonces 1);
  `run()` solo devuelve `total_new`.
- `scrapers-pipeline/verification/run_evidence.py::_multi_site_portal_outcome`
  clasifica un portal con `exit=0` y sin salida fresca como `OUTCOME_EMPTY`
  ("sin ofertas"). El diagnóstico del 18-oct concluyó: `status failed`,
  `outcome empty`, motivo «sin evidencia suficiente para confirmar la fuente».
- Consecuencia: **un fallo total de navegación (ni una SERP cargada) es
  indistinguible de un portal sin ofertas**, y el watchdog no interviene
  (no hay inactividad anómala: el proceso sale solo con exit 0).

### 1.5. Efecto lateral en `state.json` (store)

- `__main__.py` llama a `store.rebuild_state()` justo al arrancar. Como
  `_load_state()` solo rellena `_processed_ids` (dedup) y `_by_id` está vacío,
  `rebuild_state()` **reescribe `state.json` con `total: 0` y `job_ids: []`**
  (verificado hoy: `checkpoints/state.json`, `updated_at` 2026-10-17T22:06:37Z).
- El parquet/CSV de salida no se toca (se conservan los 105 del 17-oct). El
  impacto es de reanudación/dedup entre noches (el filtro de subida del
  pipeline ya deduplica contra la landing), no de pérdida de datos.
- El aviso `CSV: run sin datos; se conserva el output previo` confirma que el
  diseño protege el output ante un run vacío.

### 1.6. Sin cambios de versión ni de arquitectura entre el 17 y el 18

- Chromium empaquetado `ms-playwright/chromium-1223` instalado el 20/06/2026
  (versión 148.0.7778.96, según `Last Version` del perfil); patchright 1.60.1
  (20/06), playwright 1.59.0 (03/05), playwright-stealth 2.0.3 (03/05): sin
  cambios.
- Git: el único cambio en `src/` cercano es `d3b0c95` (17-oct 11:34, T-34 de
  irishjobs), que eliminó de `core/browser.py` el parche manual de
  `navigator.webdriver` (doctrina §6.0: no parchear a mano); no afecta a la red
  y hoy la sonda funciona con ese mismo código. El parámetro `channel`
  (irishjobs) se añadió el 18-oct 10:51, después del run. Sin cambios en
  `src/glassdoor/` desde el 15-oct.

### 1.7. Estado actual (sondas de hoy)

- **curl** (sin JS): homepage y SERP → **403 Cloudflare** (`Cf-Mitigated:
  challenge`, `Server: cloudflare`, CF-RAY ...-MAD) en ~0,14 s (challenge
  rápido, **no** timeout); `robots.txt` → 200. Sin rastro de DataDome.
  Detalle: `evidence/http-probes-20261018.txt`.
- **Navegador real (agent-browser, perfil propio)**: homepage OK; SERP de
  `Data+Analyst&fromAge=7` OK, título "187 Data analyst jobs in Ireland", 30
  tarjetas en el DOM; POST `/graph` x4 → 200; cookie `cf_clearance` presente;
  sin CAPTCHA ni textos de challenge; solo errores de consola del framework
  (React #418). Detalle y captura:
  `evidence/browser-probe-summary-20261018.txt` y
  `.opencode/.agent-screenshots/20261018-1155-gd-ie-serp-data-analyst.png`.
- **Perfil persistente del scraper** (`auth/profile/glassdoor`): sin ficheros
  `Singleton*`/`lock*`; 121,6 MB; sonda mínima con `launch_context` + perfil
  real: **launch 2,1-4,0 s**, homepage 1,4 s, SERP 2,0 s (187 ofertas), cierre
  limpio y 0 procesos residuales. No reproduce ningún timeout. Detalle:
  `evidence/profile-probe-20261018.txt`.
- **robots.txt**: sin cambios; `/graph`, `/api/`, `/api-web/`, `/autocomplete`
  y `*/*Ajax.htm` siguen en `Disallow`; el SERP `/Job/jobs.htm?sc.keyword=`
  no está en `Disallow` (`evidence/robots-ie-20261018.txt` y `.headers.txt`).
- **TOS**: la cláusula que exige permiso escrito para "scrape, strip, or mine
  data" sigue vigente; sin cambio nuevo
  (`evidence/terms-of-use-recheck-20261018.txt`). **La decisión de la persona
  del 2026-10-12 (RF-6, opción A: continuar con la vía BFF `/graph` asumiendo
  el conflicto) se mantiene y esta investigación no propone nada que amplíe su
  alcance.**
- **Muestra con datos más reciente** (17-oct, 105 ofertas): obligatorios al
  100 %; `salary` 66,7 %; `skills` 79,0 %; `work_mode` 8,6 %
  (`evidence/completeness-glassdoor-20261017.txt`).

---

## 2. Hipótesis y veredicto

| # | Hipótesis | Veredicto | Fundamento |
|---|---|---|---|
| H1 | **Incidente transitorio del entorno** (agotamiento de recursos de red del host: puertos efímeros TCP/UDP + saturación de un equipo de 8 GB con varios navegadores) degradó el arranque y la red del Chromium de glassdoor en la ventana 00:05-00:31 | **Sostenida (causa principal)** | §1.1 (launch 5 min, Network service crashed, todas las navegaciones por timeout), §1.2-§1.3 (otros portales con timeouts en la misma franja; evento Tcpip 4231 a las 00:29; roams Wi-Fi 00:31), §1.7 (hoy el mismo perfil/binario/red funcionan en segundos; las dos noches previas funcionaron) |
| H2 | Bloqueo anti-bot específico de Glassdoor (challenge/CAPTCHA) | **Refutada** | Los fallos del run son timeouts sin respuesta (no 403/challenge); en la misma franja fallaron dominios ajenos (nvb, jobs_ch); el challenge de Cloudflare, cuando aparece, responde en ~0,14 s y el navegador real lo pasa (hoy: SERP con 187 ofertas, `/graph` 200, `cf_clearance`); el 18-oct no se registró ningún marcador anti-bot |
| H3 | Perfil persistente corrupto o bloqueado (`SingletonLock`) | **Refutada** | Sin locks; sonda con el perfil real OK (launch 2 s, homepage/SERP); el perfil se escribió correctamente durante el propio run |
| H4 | Cambio de versión de Chromium/patchright o de configuración de lanzamiento entre el 17 y el 18 | **Refutada** | Sin actualizaciones (20/06, 03/05); el único cambio de código es la retirada del parche manual de `navigator.webdriver` (inocua para red) y el canal opt-in de irishjobs (posterior al run); hoy con ese mismo código la sonda funciona |
| H5 | El scraper no distingue "no pude navegar" de "no hay ofertas" y sale 0; el diagnóstico lo lee como `empty` | **Confirmada (brecha de robustez/diagnóstico, no causa del fallo)** | `scraper.py` atrapa el fallo por rol y sale 0; `run_evidence._multi_site_portal_outcome` → `OUTCOME_EMPTY`; diagnóstico `empty` |
| H6 | El run vacío deja el `state.json` sin los job_ids de reanudación | **Confirmada (efecto lateral menor)** | `rebuild_state()` al arrancar con `_by_id` vacío; `state.json` hoy: `total 0`, `job_ids []` |

---

## 3. Evidencias escritas

Todas bajo `repairs/20261018-glassdoor/evidence/` (saneadas: rutas `<HOME>`/`<REPO>`,
sin cookies/valores ni credenciales) y capturas fuera de git:

| Fichero | Contenido |
|---|---|
| `run-log-glassdoor-20261018-extract.txt` | Extracto completo del run fallido (líneas 18186-18258 del `run.log`) |
| `cross-portal-failures-20261018.txt` | Timelines de nvb/jobs_ch/stepstone_nl/irishjobs/devitjobs y `last_run.json` |
| `system-events-20261018.txt` | Eventos Windows (Tcpip 4231/4266, Wi-Fi, Windows Update, RAM/equipo) |
| `http-probes-20261018.txt` | curl homepage/SERP/robots (403 challenge, latencias, cabeceras, DNS) |
| `browser-probe-summary-20261018.txt` | Sonda agent-browser (homepage, SERP 187 ofertas, red, consola, cookies por nombre) |
| `profile-probe-20261018.txt` | Estado del perfil del scraper + sonda mínima ejecutada y su script |
| `robots-ie-20261018.txt` + `.headers.txt` | robots.txt de glassdoor.ie y cabeceras saneadas |
| `terms-of-use-recheck-20261018.txt` | Re-verificación de la cláusula de TOS |
| `completeness-glassdoor-20261017.txt` | Cobertura por campo de la muestra de 105 ofertas (módulo `verification.completeness`) |
| `.opencode/.agent-screenshots/20261018-1155-gd-ie-serp-data-analyst.png` | Captura de la SERP cargada (fuera de git) |

Presupuesto de peticiones: 1 DNS + 3 curl + 3 cargas de página de navegador
propio + 3 cargas con el perfil del scraper + XHR propias de esas páginas.
Ningún CAPTCHA resuelto; ninguna acción de cuenta; no se usó ni cerró el Chrome
de la persona.

---

## 4. Causa probable y fundamento

**Causa próxima del run vacío:** el navegador de glassdoor arrancó degradado
(primer intento: 5 min y timeout; stderr con caídas del *network service*) y,
ya arrancado en el segundo intento, **ninguna navegación de red funcionó**
(homepage, autocompletado y las 6 SERP agotaron su timeout; sin 403 ni
challenge). Es un fallo de conectividad del proceso/navegador, no de la web.

**Causa raíz probable (entorno):** incidente transitorio de recursos del
equipo en la franja 00:05-00:31, con evidencia directa de **agotamiento de
puertos efímeros TCP** (evento Tcpip 4231 a las 00:29:12; UDP a las 00:58) y
saturación de un equipo de 8 GB con varios navegadores en paralelo (4 scrapers
del pipeline + hasta 6 portales de multi_site, con Chromium empaquetado,
patchright y Chrome CDP). En esa misma franja fallaron por timeouts otros
portales (nvb, jobs_ch) y otros sufrieron timeouts intermitentes (stepstone_nl,
irishjobs); devitjobs, que no usa navegador, funcionó hasta las 00:05:41. La
presión de puertos es recurrente en este equipo (4231 el 12-oct; 4266 varios
días). La investigación de hoy descarta que el fallo sea de Glassdoor, del
perfil o de las versiones: el mismo perfil y binario cargan la web en segundos
y la SERP devuelve 187 ofertas.

**Por qué el diagnóstico lo llamó `empty` y no `error` (brecha secundaria):**
el scraper convierte "todas las navegaciones fallidas" en "0 ofertas por rol" y
termina con exit 0; el diagnóstico de multi_site solo distingue error por exit
≠0/timeout o bloqueo por watchdog. Esta brecha oculta el fallo real a la
persona y al proceso de reparación.

**Descartado:** bloqueo anti-bot de Glassdoor (H2), corrupción/bloqueo del
perfil (H3), cambio de versión/configuración (H4). Ver §2.

---

## 5. Auditoría de cobertura de campos

Muestra de referencia: parquet del run 17-oct (105 ofertas, mismo mercado y
config; medición con `verification.completeness`). Referencia cruzada: prueba
en vivo del 15-oct (23 ofertas; registro 20261012).

| Campo | ¿Disponible en web/API? | ¿Lo extrae el scraper hoy? | Brecha | Extracción recomendada | Base legal |
|---|---|---|---|---|---|
| id | Sí — BFF `listingId` | Sí; 100 % (105/105 y 23/23) | Ninguna | Mantener | Vía BFF `/graph` en `Disallow` de robots.txt y TOS con permiso escrito → cubierto por la decisión de la persona (opción A, 2026-10-12) |
| title | Sí — `jobTitleText` | Sí; 100 % | Ninguna | Mantener | Ídem BFF |
| company | Sí — `employerNameFromSearch`/`employer.name` (+ defensa anti-rating ya implementada) | Sí; 100 % | Ninguna | Mantener | Ídem BFF |
| description | Sí — completa vía `JobDetailQuery` (1 llamada/oferta; el search trae la descripción vacía en la práctica) | Sí; 100 % (105/105; mínimo 296 car., media ~5.700 en muestras previas) | Coste: 1 petición/oferta; ya hay latido y tope de fallos (T-35) | Mantener on-demand con latido; no hay vía lote verificada | Ídem BFF; el snippet DOM vendría del SERP (ruta no listada en `Disallow`, pero Cloudflare corta con challenge a clientes sin JS) |
| salary | Parcial — `payCurrency`/`payPeriod`/`payPeriodAdjustedPay`/`salarySource` (a menudo estimación) | Sí; 66,7 % (70/105; 65,2 % en la muestra de 23) | No es salario publicado en toda oferta; no prometer 100 % | Mantener; sin meta (es lo que expone el portal) | Ídem BFF |
| skills | No lo publica el portal; derivado de la descripción (`extract_skills`) | Sí; 79,0 % (83/105; 91,3 % en 23) | Depende de que llegue la descripción | Mantener | Derivado de `description` (misma base) |
| work_mode | Parcial — `locationType` S/O + texto de `locationName`/título | Sí; 8,6 % (9/105; 13,0 % en 23) | Dato estructuralmente escaso en el BFF | Mantener heurística; no prometer más (no poner meta) | Ídem BFF |
| location | Sí — `locationName` (+ `parse_location_raw`) | Sí; 100 % | Ninguna | Mantener | Ídem BFF |
| posted_date | Sí/derivado — `ageInDays` → fecha calculada | Sí; 100 % | Precisión de días | Mantener | Ídem BFF |

Nota: `employment_type` sigue a 0 en las muestras (`do_detail=False`); no es
campo del perfil, pero condiciona `work_mode`/contrato si algún día se piden
detalles.

---

## 6. Meta de calidad propuesta

Ámbito: prueba acotada §6.5 (1 rol × 1 página, ~30 ofertas) con BFF sano y
`fetch_descriptions=True`; sin regresión frente a las muestras anteriores
(tolerancia 5 pp, `QUALITY_REGRESSION_TOLERANCE_PP`).

| Campo | Obligatorio | Meta propuesta | Fundamento |
|---|---|---|---|
| id | no | 100 % | 105/105 y 23/23 |
| title | sí | 100 % | 105/105 y 23/23 |
| company | sí | 100 % | 105/105 y 23/23 |
| description | sí | 100 % no vacía | 105/105 y 23/23 con `JobDetailQuery` |
| salary | no | Sin meta fija; referencia 65-67 % (no bajar >5 pp) | El portal publica salario/estimación en ~2/3 de las ofertas |
| skills | no | Sin meta fija; referencia 79-91 % (no bajar >5 pp) | Derivado de la descripción |
| work_mode | no | Sin meta fija; referencia 9-13 % (no bajar >5 pp) | Dato escaso en el BFF; no prometer más |
| location | no | 100 % | 105/105 y 23/23 |
| posted_date | no | 100 % | 105/105 y 23/23 |

Observación para la fase de implementación: `quality_before.json` de este
registro fija `target_pct: 100` para **todos** los campos (incluidos los
opcionales). Conforme al plan §9, los opcionales no publicados por el portal no
deben ser un incumplimiento; conviene ajustar la meta de `salary`/`skills`/
`work_mode` a "lo que expone el portal / sin regresión" al completar el
registro.

---

## 7. Cambio recomendado

### 7.1. Núcleo: tratar el fallo total de navegación como error (no como "empty")

Ficheros: `multi_site_job_scraper/src/glassdoor/scraper.py` y
`multi_site_job_scraper/src/glassdoor/__main__.py`.

1. **Contabilidad de navegación por run:** contar los roles cuyo `page.goto` de
   la SERP termina bien y los que terminan en bloqueo/landing detectado
   (`_check_blocked_or_landing`). Hoy solo se registra el error y se sigue.
2. **Regla explícita:** si **ninguna** SERP llega a cargarse en todo el run
   (todos los roles con timeout/excepción de navegación), el scraper debe
   terminar con **exit ≠ 0** (p. ej. `raise RuntimeError(...)` al final del run;
   `__main__` ya mapea cualquier excepción a exit 1) y registrar un motivo
   claro (`ERROR ... navigation_failed: N/6 roles sin SERP (motivo)`), de modo
   que `_multi_site_portal_outcome` lo clasifique como `error` (fallo técnico)
   y no como `empty`.
3. **Abortar antes:** cortar tras 2-3 roles consecutivos sin navegación (en vez
   de agotar los 6 a ~1 min por intento), dejando señal en el log antes.
4. **Bloqueo anti-bot:** si los roles acaban con marcadores anti-bot/landing
   (ya existen en `_check_blocked_or_landing`), el mismo camino de exit ≠ 0
   aplica; si se quiere señal semántica, añadir la convención
   `BLOCKED source=glassdoor reason=challenge:...` que ya usa irishjobs. El
   diagnóstico de multi_site no lee marcadores de portal (su `blocked` viene del
   watchdog), así que lo que cambia el resultado es el exit ≠ 0; no se propone
   tocar el diagnóstico (contrato de la 001).
5. **Comportamiento que debe conservarse:** SERP cargada sin ofertas, o
   ofertas todas duplicadas → exit 0 y `empty`/`ok` legítimos.
6. **Reintento acotado:** si hay ≥2 fallos de navegación con el contexto vivo
   (hoy solo se relanza el navegador para `TargetClosedError`), un único
   relanzamiento del contexto antes de declarar el fallo.

### 7.2. Endurecimiento de la causa raíz (decisión de la persona; fuera del scraper)

El fallo del 18-oct fue del entorno. No es un cambio de código de glassdoor,
pero sin mitigación puede repetirse (4231/4266 llevan registrándose desde el
10-oct):

- Reducir/navegadores simultáneos por noche (más escalonado, menos portales a
  la vez) o ejecutar los portales con navegador en dos tandas.
- Opcional de sistema (Windows, no repo): ampliar el rango dinámico de puertos
  o reducir `TcpTimedWaitDelay` (requiere decisión de la persona).
- El watchdog de progreso no protege de este fallo (el portal sale solo); la
  señal honesta del punto 7.1 es la que lo hará visible.

### 7.3. Secundario (a decisión del orquestador): `state.json`

`__main__.main` llama a `store.rebuild_state()` con el store vacío y borra los
`job_ids` de reanudación. Opciones mínimas y testeables: no reescribir cuando
`_by_id` está vacío, o fusionar `_processed_ids` en el estado escrito. Impacto
actual bajo (el filtro de subida deduplica), pero degrada la reanudación y el
recuento del banner. No es la causa del fallo.

### 7.4. Tests (fase de implementación, sin red)

- `tests/` del scraper: un `page` simulado cuyo `goto` lanza `TimeoutError`
  para 1..6 roles → el run termina con exit ≠ 0 y registra el motivo; un SERP
  válido sin ofertas → exit 0 (empty legítimo); bloqueo detectado en todos los
  roles → exit ≠ 0 con marcador.

---

## 8. Comprobación manual propuesta

1. **Reproducir los hechos locales (sin red):**
   ```powershell
   Select-String -Path multi_site_job_scraper\data\glassdoor\run.log -Pattern 'Error navegando a SERP' | Select-Object -Last 6
   Get-Content multi_site_job_scraper\data\merged\last_run.json
   Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Tcpip'; StartTime=(Get-Date '2026-10-17 23:00'); EndTime=(Get-Date '2026-10-18 02:00')} | Select-Object TimeCreated, Id
   ```
2. **Sonda del entorno/perfil** (script en `evidence/profile-probe-20261018.txt`):
   verifica que el perfil sigue caliente; hoy da launch ≈2-4 s y SERP ≈2 s.
3. **Prueba en vivo §6.5 tras el fix** (la ejecuta el implementador, sin
   merge/Azure/landing): desde `multi_site_job_scraper`,
   `python -m src.glassdoor --role "Data Analyst" --max-pages 1`.
   Criterios: exit 0, latido `PROGRESS` intercalado, recuento ≥ umbral
   (**24**, por `history.json`: mayor verificado 23 + 1, RF-10), calidad con
   `quality.py`.
4. **Nuevo comportamiento de fallo:** test offline (7.4). No se propone forzar
   un fallo de red real en vivo; si el orquestador quisiera, basta apuntar
   `base_url` a un host inexistente en una sonda manual acotada (no
   automatizada).

---

## 9. Riesgos y límites

- **Recurrencia:** si no se mitiga el entorno (7.2), el agotamiento de puertos
  puede repetirse; el fix 7.1 no lo evita, solo lo hace visible y honesto.
- **Sondas en horario diurno con red sana:** confirman que el fallo no es de
  Glassdoor/perfil/versión, pero no reproducen la causa; los challenges de
  Cloudflare son intermitentes (el 12-oct el SERP con perfil limpio recibió
  challenge; hoy no).
- **Sin telemetría de RAM/CPU de la noche del fallo** (solo eventos de sistema
  y logs); la parte de "saturación" es inferida del patrón (arranque 2,5 min
  antes del launch, timeouts simultáneos, 8 GB).
- **Legal (RF-6):** sin cambios; la opción A de la persona sigue vigente. El
  cambio recomendado no altera la vía de datos (`/graph`) ni amplía el alcance.
- **Credenciales/sesión:** no se usó la sesión real de Chrome ni se cerró
  ninguna ventana de la persona; la sonda con el perfil del scraper usó el
  perfil persistente del proyecto (no se copiaron cookies a la evidencia).
  No fue necesaria ninguna intervención de la persona (sin CAPTCHA, sin login).
- **Incidencia menor de la propia investigación:** el binario empaquetado se
  invocó una vez con `--version` (Chromium no soporta ese flag de Playwright:
  abrió una ventana que se cerró sola); sin efecto en el perfil del scraper ni
  en los demás navegadores, y sin procesos residuales (verificado).
- Ningún fichero quedó pendiente de saneado.
