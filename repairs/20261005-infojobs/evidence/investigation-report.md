# Informe de investigación — InfoJobs (T-27, spec 004)

- **Fuente:** `infojobs` · **run de origen:** 2026-10-05 · **registro:** `repairs/20261005-infojobs/`
- **Playbook:** §6.1 — InfoJobs: bloqueo por CAPTCHA
- **Alcance de prueba previsto:** 1 keyword × 1 ciudad × 1 página; sin Azure, sin landing, sin merge
- **Sesión en vivo:** 2026-10-06 ~22:44–22:50 (Europe/Madrid), Chrome real con sesión iniciada vía CDP 9222
- **Sondas previas reutilizadas:** 2026-10-06 15:46–15:47 UTC (curl con UA de bot) ya registradas en `evidence/`
- **Investigador:** `web-inspector` (modo lectura de código; sin solvers, sin servicios de pago, sin acciones de cuenta)

## 1. Hechos observados

### 1.1 Sesión real del usuario (hechos nuevos de esta investigación)

Método: sesión real con `browser-cdp` + `agent-browser --cdp 9222` (marcadores, red y cookies), `robots.txt` y sondeo de endpoints JSON internos; sin solvers ni servicios de pago.

Se hicieron **2 navegaciones** a la fuente (home y SERP), sin reintentos y sin CAPTCHA visible:

| Paso | URL | Resultado |
|---|---|---|
| Home | `https://www.infojobs.net/` | HTTP 200, título «InfoJobs», h1 «Siempre a mejor», diálogo de cookies Didomi. Sin challenge. |
| SERP | `https://www.infojobs.net/ofertas-trabajo/madrid/madrid` | HTTP 200, título «Ofertas de trabajo en Madrid, Madrid», h1 «8,192 ofertas en Madrid». Sin challenge. |

Marcadores en la SERP (evaluados en el DOM real):

- `distil=false`, `geetest=false`, `captcha` en URL=false, ausencia de `initGeetest`/`gt.js`.
- `li.ij-OfferList-offerCardItem` = 10 elementos; de ellos **5 son ofertas reales** y el resto tarjetas promocionales sin título.
- `job-title-*` = 5; `a.ij-OfferCardContent-description-link` = 5; `[data-testid=sincedate-tag]` = 5; `salary-info` = 3; `salary-no-information` = 2; `p.ij-OfferCardContent-description-description` = 5.
- URL final sin salto a `/distil/`; `canonical = https://www-internal.infojobs.net/ofertas-trabajo/madrid/madrid` (ni `captcha` ni `distil`: no dispara el detector actual).
- Paginación visible 1..5 + «Siguiente».

Campos reales de las tarjetas (evaluación en vivo; muestra):

| Oferta | title | company | location | posted_date | salary | work_mode | description (tarjeta) |
|---|---|---|---|---|---|---|---|
| Store Manager Flagship - Moda Premium Caballero | sí | LUXETALENT | Madrid | «Hace 4h (Publicada de nuevo)» | «Salario no disponible» | Presencial | sí (~2.000+ car.) |
| Asesor/a inmobiliario/a | sí | Tecnocasa Consulting Inmovillaverde 2025 | Madrid | «Hace 4h» | «Más de 1200 €» | Presencial | sí |
| Odontólogo/a especialista en periodoncia. TOLEDO | sí | ASISA DENTAL | Madrid | «Hace 18m» | «Salario no disponible» | Presencial | sí |
| COMERCIAL PARA TIENDA TELEFONÍA MULTIMARCA | sí | ANCOTEL COMUNICACIONES SL | Madrid | «Hace 24m» | «15.000 € - 24.000 € Bruto/año» | Presencial | sí |

Estructura DOM observada por tarjeta: `h2#job-title-<hash>`; enlace `a.ij-OfferCardContent-description-link` → `/<ciudad>/<slug>/of-i<hash>?applicationOrigin=search-new...`; empresa en `h3 a.ij-OfferCardContent-description-subtitle-link`; ciudad en `span.ij-OfferCardContent-description-list-item-truncate`; fecha en `[data-testid=sincedate-tag]`; dos `ul.ij-OfferCardContent-description-list`: `ul[1]=[ciudad, modalidad, fecha]`, `ul[2]=[contrato, jornada, salario]`; descripción extensa en `p.ij-OfferCardContent-description-description`.

Red (documento y XHR de la sesión real; 268 peticiones capturadas, la mayoría scripts/anuncios/telemetría):

- El listado **llega renderizado en el HTML de la SERP** (SSR). No hay ningún XHR/fetch que devuelva el listado.
- Endpoints de datos observados: `candidate-web.gw.infojobs.net/1/location/ij-search-results/next` (carrusel de ubicaciones, `amount=4`) e `.../ij-home/next`; `www.infojobs.net/responsive-components-home/show-number-offers.xhtml`; los `adit.gw.infojobs.net/*` son anuncios/telemetría. **No existe endpoint JSON interno de búsqueda** usado por la web.
- El navegador real **no llama** a `/ofertas-trabajo/.../data` (la ruta que el bot recibió con 405 + challenge en la sonda previa).

Sesión/cookies (solo nombres; los valores no se registraron en ningún fichero del repositorio):

- Presentes: `reese84`, `JSESSIONID`, `AWSALB`, `IJUSERUID`, `ijreactcvskills`, `IJ_MCMID`, `didomi_token` y cookies analíticas (`_gcl_au`, `_sharedID*`, `ajs_*`, `ab.storage.sessionId.<uuid>`).
- **`reese84`** es el token de challenge de **Distil/Imperva** vigente en el perfil real; no aparecen `cf_clearance`, `_abck` ni `datadome`.
- Consola/errores: sin scripts de challenge; solo un `TypeError` propio de la web (widget `company_feedback`, sin relación).

### 1.2 Comportamiento del bot (evidencia ya reunida, no repetida)

- curl con UA `TalentMarketLensBot/0.1` a `https://www.infojobs.net/ofertas-trabajo/madrid/madrid/data` → **HTTP 405 + challenge**: canonical `https://www.infojobs.net/distil/distil/captcha.xhtml`, h1 «¿Eres humano o un robot?», 11 coincidencias de `geetest` y una llamada `initGeetest` (`static.geetest.com/static/tools/gt.js`); sin tarjetas.
- `robots.txt`: permite la SERP `/ofertas-trabajo/...`; bloquea el detalle (`/ver-oferta.xhtml`, `/visualizar_oferta.*`, `/*-i.xhtml$`, `/*?session_oferta=*`, `/*applicationOrigin=*`) y `/buscar.empleo/`, `/react`, `/webapp`.
- Logs nocturnos: `blocked=True` continuo desde 2026-09-18, `exit=-1`, 0 ofertas; desde el 28-09 `RETRYPOLICY source=infojobs decision=stop reason=blocked_antibot`. Los logs **no registran qué marcador de `detect_captcha` disparó**.
- Fixtures de listado de runs previos contienen los mismos selectores/campos que el DOM vivo (título, empresa, ciudad, fecha, salario, modalidad, descripción).

### 1.3 Hipótesis del plan §2.4 (confirmadas/refutadas)

| Hipótesis | Veredicto | Fundamento |
|---|---|---|
| «Challenge de reputación (IP/perfil), no de parser» | **Confirmada** | La sesión real recibe la SERP SSR completa con tarjetas; los selectores vivos coinciden con el parser y los fixtures. El bloqueo no depende del HTML. |
| «Puede evitarse con sesión/cookies reales, cabeceras y ritmo» | **Confirmada en lo observable; pendiente de reuso desde el scraper (T-30)** | El perfil real (con `reese84` y fingerprint de Chrome) pasa sin challenge; falta probar que el scraper reutiliza esa sesión de forma sostenida. |
| «Existe un endpoint interno permitido» | **Refutada para el listado** | No hay XHR de listado; el endpoint `/data` solo devolvió challenge al bot y la web no lo usa. Los endpoints internos vistos son carruseles/ubicaciones y anuncios. |

### 1.4 Limitaciones de esta pasada

- La **captura de la SERP no pudo guardarse**: `agent-browser screenshot` falló 2 veces seguidas con error local de conexión CDP (os error 10060); se respetó el límite de no reintentar más. Quedan como evidencia el volcado de marcadores, el resumen de red, consola/errores y la captura de home.
- No se forzó el challenge ni se repitió la sonda curl; las conclusiones sobre el challenge se apoyan en la evidencia previa del 15:46 UTC.
- **TOS («Uso del servicio»): limitación cerrada el 2026-10-08.** La persona revisó ella misma el texto y no encontró ninguna cláusula sobre scraping ni acceso automatizado (verificación humana; no constituye asesoramiento legal). La regla operativa se mantiene: `robots.txt` (SERP permitida) y datos públicos, sin acciones de cuenta.

## 2. Evidencias

Nuevas (esta investigación):

- `evidence/live-session-serp-markers.txt` — marcadores, campos y estructura de la SERP con la sesión real.
- `evidence/live-session-network-summary.txt` — red saneada (documento SSR, endpoints, conclusión de API interna).
- `evidence/live-session-cookies-names.txt` — nombres/atributos de cookies (sin valores), incluido `reese84`.
- `evidence/20261006-224500-infojobs-home.png` — captura de home OK (256 KB; copia de `.opencode/.agent-screenshots/20261006-224500-infojobs-home.png`).
- Captura de la SERP **no disponible** por fallo local de screenshot (ver 1.4).

Reutilizadas (previas, no repetidas):

- `evidence/infojobs-robots.txt`, `evidence/infojobs-robots.headers.txt` — robots y cabeceras.
- `evidence/infojobs-serp-probe-markers.txt`, `evidence/infojobs-serp-probe.html`, `evidence/infojobs-serp.headers.txt` — challenge 405 del bot.
- `evidence/infojobs-tos-probe.html`, `evidence/infojobs-tos.headers.txt` — segundo sondeo (también challenge; sin texto TOS).
- `evidence/offline-run_nightly-blocks.txt`, `evidence/offline-logs-captcha.txt`, `evidence/offline-state-watchdog.txt` — logs del scraper.
- `evidence/offline-code-markers.txt`, `evidence/offline-fixture-field-coverage.txt` — código y fixtures.
- `evidence/live-session-setup.txt` — transcripción saneada del arranque CDP (sin rutas locales; conserva plataforma, puerto 9222, versión de Chrome y `/json/version`).

## 3. Causa probable y su fundamento

**Causa probable:** el scraper nocturno usa un **perfil/sesión propios sin token de challenge válido** y navega en frío (directo a la SERP) con un fingerprint de automatización; Distil/Imperva lo clasifica como bot y le sirve el challenge (canonical `distil/captcha.xhtml`, GeeTest) en lugar del HTML. El perfil real del usuario mantiene `reese84` vigente y recibe la SERP renderizada con todas las tarjetas. No es un fallo de parser ni de selectores: es **bloqueo por sesión/reputación** (`blocked=True` sistémico desde 2026-09-18, `reason=blocked_antibot`).

**Marcador de `detect_captcha` que dispara:** con la variante observada (h1 «¿Eres humano o un robot?», canonical `.../distil/distil/captcha.xhtml`), el detector actual dispara por el **canonical** (`"captcha"`/`"distil"` en `link[rel=canonical]`). El chequeo de URL solo dispararía si el navegador acabara en una URL `/distil/...`; el chequeo de `h1` **no cubre** ese texto (busca «no podemos identificar tu navegador») y no hay chequeo de `geetest` ni de iframes `distil`. Como los logs no registran el marcador, no puede confirmarse cuál disparó en las noches reales; por la evidencia del challenge, el candidato principal es el canonical y, en segundo lugar, la URL.

**Por qué el curl/bot sí lo recibe:** UA declarado de bot + ausencia de cookies de sesión (`reese84`) + petición directa a un endpoint de datos sin contexto de navegador → Distil sirve 405 + challenge. El navegador real, mismo IP, con token y fingerprint de Chrome, pasa sin fricción.

## 4. Auditoría de cobertura de campos

| campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal |
|---|---|---|---|---|---|
| `id` | Sí: `h2#job-title-<hash>` y URL de tarjeta `of-i<hash>` | Sí (`_extract_offer_id_from_h2` + fallback URL) | Ninguna | Mantener; guardar la URL sin `?applicationOrigin=...` | SERP permitida por `robots.txt`; el detalle no se visita |
| `title` | Sí: `a.ij-OfferCardContent-description-title-link` / `span` | Sí | Ninguna | Mantener con fallback al `h2` | SERP permitida |
| `company` | Sí: `h3 a.ij-OfferCardContent-description-subtitle-link` | Sí | Ninguna | Mantener | SERP permitida |
| `description` | Sí, en la propia tarjeta: `p.ij-OfferCardContent-description-description` (texto extenso, ~600–2.000+ car.) | Sí, como snippet | No se garantiza el texto íntegro de la tarjeta y no hay registro de tarjetas sin párrafo | Extraer el texto completo del párrafo de la tarjeta; **no** abrir la ficha (bloqueada) | Detalle (`/ver-oferta.xhtml`, `/visualizar_oferta.*`) **bloqueado**; la tarjeta es la vía permitida |
| `salary` | Sí cuando el portal lo publica: `salary-info` (3/5 en la muestra; 2/5 «no disponible») | Sí, parcial (`_parse_salary`; «Más de X» deja `periodo=None`) | Normalización de «Más de X €» y periodicidad | Mantener y, opcionalmente, afinar «más de»/periodo por defecto | SERP permitida |
| `skills` | No: no aparece en la tarjeta ni hay meta | No (no existe campo) | No exigible: se derivan en Databricks | No extraer en el scraper | n/a |
| `work_mode` | Sí: `ul[1]` de la tarjeta («Presencial», «Híbrido», «Solo teletrabajo») | Sí (heurística por palabras clave) | Verificar en vivo que captura el valor real (T-30) | Mantener heurística; preferir el texto de `ul[1]` pos. 2 y validar contra el vocabulario del portal | SERP permitida |
| `location` | Sí: `span.ij-OfferCardContent-description-list-item-truncate` | Sí (ciudad); `provincia` queda vacía | `provincia_detectada=""` | Mantener ciudad; rellenar provincia desde el parámetro de búsqueda o dejarla derivada (no bloquea) | SERP permitida |
| `posted_date` | Sí: `[data-testid=sincedate-tag]` («Hace 4h», «Hace 18m», «Nueva») | Sí (fecha relativa aproximada) | Precisión: es una aproximación a la fecha del run | Mantener; documentar la aproximación y que «Nueva/Publicada de nuevo» no rompe el parseo | SERP permitida |

## 5. Meta de calidad propuesta

Con lo que la web permite (la ficha completa está bloqueada por `robots.txt`; la tarjeta es la vía permitida):

| campo | Meta propuesta | Justificación |
|---|---|---|
| `title` | **100 %** en el alcance de prueba | Presente en 5/5 tarjetas reales; obligatorio. |
| `company` | **100 %** | Presente en 5/5. |
| `description` | **100 %** con el texto de la tarjeta (no la ficha) | 5/5 tarjetas traen `p.ij-OfferCardContent-description-description` con texto extenso; el detalle está bloqueado por `robots.txt` y no se promete. |
| `id` | 100 % (no obligatorio) | `job-title-<hash>` en 5/5; fallback por URL `of-i<hash>`. |
| `salary` | Extraer cuando exista (muestra 3/5); sin meta fija | El portal publica «Salario no disponible» en parte de las ofertas; su ausencia no es fallo. |
| `work_mode` | **100 %** de las tarjetas que lo publican (muestra 5/5) | Modalidad en `ul[1]`; confirmar extracción real en T-30. |
| `location` | **100 %** ciudad (5/5); provincia derivada opcional | La tarjeta trae la ciudad; la provincia puede derivarse de la búsqueda. |
| `posted_date` | **100 %** con fecha relativa (5/5) | `sincedate-tag`; se documenta que es aproximada. |
| `skills` | **Sin meta** | No aparece en la tarjeta; se derivan de la descripción en Databricks. |

Sin regresión: al no haber ofertas válidas desde 2026-09-18 (0 ofertas; `blocked=True`), la cobertura actual es 0; las metas anteriores están por encima de la cobertura actual y no rebajan nada.

## 6. Cambio recomendado

Nivel de la escalera §6.0: **sesión y tokens reales (nivel 2–3) + fingerprint coherente (4) + flujo/ritmo humanos (5)**. No aplica el nivel 1 (no hay API JSON de listado) ni solvers (línea roja).

1. `scraper/navigator.py`
   - Sustituir `detect_captcha(page) -> bool` por una variante que **devuelva/registre el marcador** (`captcha_marker(page) -> str | None`) con los marcadores reales: URL (`/distil/`, `captcha`), canonical (ya), **h1 con «eres humano» / «un robot»** (normalizado, sin acentos), `script[src*="geetest"]`/`initGeetest` en el HTML e iframe `src*="distil"`; la imagen `sherlock` puede quedarse como marcador residual. No resolver el challenge.
   - `handle_captcha`: **abortar** devolviendo `False` con el marcador y el motivo (política de la persona, 2026-10-08); sin pausa larga por defecto y sin recargas ciegas (cada recarga puede renovar el challenge y empeorar la reputación).
2. `scraper/browser.py`
   - Mantener el contexto persistente (nunca contextos limpios por petición) y permitir **elegir el perfil** (p. ej. `INFOJOBS_PROFILE_DIR`/variable de entorno) para poder usar una **copia del perfil real con `reese84`** o un Chrome lanzado por la persona (misma IP/UA). No versionar ni copiar credenciales al repositorio.
   - Fingerprint coherente con el Chrome real (UA, `locale`, `timezone`, ventana maximizada ya presente); si el fingerprint de Playwright se sigue detectando, valorar `patchright` como drop-in (la spec lo cita) o Camoufox, justificándolo en el registro; nunca parchear `navigator.webdriver` a mano.
3. `scraper/main.py`
   - Antes de la primera SERP, **warm-up humano**: pasar por home, esperar y resolver el banner de cookies; después la búsqueda. Mantener el ritmo actual (5–9 s; muy por debajo de 30 req/min) con jitter y ejecución en horas valle.
   - Al detectar challenge: registrar **el marcador, la URL y el estado de la sesión** (p. ej. ¿existe `reese84`?) en el log y en `bloqueos`; salir con `blocked=True, reason=<marker>` (política de la persona, 2026-10-08: abortar y registrar, sin pausa larga por defecto, sin solvers ni reintentos ciegos).
   - No sondear `/data` ni endpoints de detalle: la vía permitida es la SERP.
4. `scraper/config.py`
   - Añadir la ruta de perfil configurable y, si se desea, la lista de marcadores; `ROBOTS_DISALLOWED` ya cubre el detalle (`/ver-oferta.xhtml`, `/visualizar_oferta.*`, etc.). Guardar la URL de la oferta **sin** `applicationOrigin` (esa ruta está en `Disallow`).
5. `scraper/parser.py`
   - Sin cambios obligatorios: los selectores vivos coinciden. Mejoras opcionales: extraer el texto completo del `p` de descripción y afinar `_parse_salary` para «más de X €» y periodicidad; la modalidad ya se localiza en `ul[1]` (verificar en T-30).

No se propone endpoint interno alguno: no existe para el listado y el único sondeo (`/data`) devolvió challenge al bot.

## 7. Comprobación manual propuesta

1. Reutilizar el Chrome ya abierto con la sesión real (sin relanzar `setup-cdp-chrome.js`, que cerraría las ventanas):
   `agent-browser --cdp 9222 open https://www.infojobs.net/ofertas-trabajo/madrid/madrid`
   y comprobar `agent-browser --cdp 9222 eval "({cards:document.querySelectorAll('.ij-OfferList-offerCardItem').length, geetest:/geetest/i.test(document.documentElement.outerHTML)})"` → `cards>0`, `geetest=false`.
2. Reproducir el bloqueo desde el perfil del scraper (sin bucle): lanzar una única vez `python -m scraper.main --ciudades madrid --keywords data --paginas 1` desde `infojobs_jobs_scraper/` y verificar que el log registra el **marcador** del challenge y el motivo, y que el scraper **aborta** sin recargas ciegas ni pausa larga.
3. Tras el fix (T-30): misma búsqueda de 1 página con la sesión real; comprobar ≥1 oferta, 0 challenges y la tabla de campos al 100 % en `title`/`company`/`description` (texto de tarjeta) con `quality.py`.
4. Comprobar que la sesión sigue viva antes de la prueba: confirmar en la ventana que `reese84` está presente (p. ej. `agent-browser --cdp 9222 cookies get --json`, comprobando **solo la presencia**); si caducó, la persona puede renovar la sesión manualmente antes de la prueba, fuera del scraper; ante un challenge visible, el scraper aborta y registra el marcador.

## 8. Riesgos y límites

- **Bloqueo:** el challenge es de reputación; reutilizar la sesión real es la mitigación observada, pero la vigencia de `reese84` no se ha medido (puede caducar entre runs). El scraper corre en el **mismo dispositivo y red** que la sesión real (misma IP), por lo que el reuso del token `reese84` (ligado a IP + fingerprint) es viable. Decisión operativa de la persona (2026-10-08): el diseño debe evitar el challenge; si aun así aparece uno visible, **abortar** y registrar el motivo, sin pausa larga por defecto y sin recargas ciegas.
- **`robots.txt`/TOS:** la SERP está permitida para `User-agent: *`; el detalle y `/*applicationOrigin=*` están prohibidos y no se visitan. La persona revisó el «Uso del servicio» de InfoJobs el 2026-10-08 y no encontró ninguna cláusula sobre scraping ni acceso automatizado (verificación humana, no asesoramiento legal); la regla operativa sigue siendo `robots.txt` y datos públicos.
- **Credenciales/sesiones:** se usó la sesión real del usuario vía CDP; no se registraron valores de cookies ni tokens, ni rutas de perfil (saneadas a `<HOME>`). No hubo acciones de cuenta.
- **Presupuesto:** 2 navegaciones a la fuente y 1 evaluación de campos; sin reintentos de red. La captura de la SERP falló 2 veces por un error local de CDP y no se reintentó (límite respetado).
- **No comprobado:** qué marcador disparó exactamente en los runs nocturnos (los logs no lo registran), la validez de `reese84` a lo largo del día y el comportamiento del perfil propio del scraper en hora valle.

## 9. Por qué se dispara y estrategia preventiva (§6.0)

**Por qué se dispara:** la sesión del scraper no presenta un token de challenge válido (`reese84`) y navega en frío con un fingerprint de automatización; Distil/Imperva (con GeeTest en el challenge visible) la clasifica como bot. El perfil real, con token vigente y fingerprint de Chrome, recibe la SERP SSR sin fricción.

**Estrategia preventiva aplicable:**

1. **API JSON interna:** no existe para el listado; el HTML SSR de la SERP es la vía permitida.
2. **Sesión y cookies reales:** reutilizar el perfil/sesión real (copia del perfil o Chrome de la persona con CDP), storage persistente; evitar contextos limpios.
3. **Replay de tokens de challenge:** mantener y reutilizar `reese84` dentro de su vigencia, con **la misma IP y UA**; validar su presencia antes de navegar.
4. **Fingerprint coherente:** UA/locale/timezone/ventana del Chrome real; `patchright`/Camoufox si hiciera falta; nunca parchear `navigator.webdriver` a mano.
5. **Flujo y ritmo humanos:** home → cookies → SERP, delays con jitter (5–9 s ya cumplen), horas valle y ≤30 req/min/IP.
6. **Challenge invisible:** lo resuelve el navegador real solo; no añadir código.
7. **Contingencia (decisión de la persona, 2026-10-08):** la prioridad es el diseño preventivo; si aparece un challenge visible, **abortar** y registrar el marcador y el motivo, sin pausa larga por defecto ni recargas ciegas. La renovación manual de la sesión por la persona queda como acción puntual fuera de la ejecución del scraper, nunca como espera automática dentro de él. Prohibido: solvers, servicios de pago, verificación de identidad o patrones de decepción.

## 10. Qué queda pendiente de verificar en la prueba en vivo T-30

- Que el scraper reparado obtiene **≥1 oferta** (umbral 1 en la primera reparación) con 1 keyword × 1 ciudad × 1 página **sin challenge visible** usando la sesión/token reales.
- Que los obligatorios quedan al **100 %** en la muestra (`title`, `company`, `description` de tarjeta) y sin regresión; los opcionales según lo expuesto (`work_mode`, `location`, `posted_date`; `salary` solo cuando exista).
- Que el log registra **qué marcador** de detección se usó y el motivo del bloqueo si este reaparece (hoy no se registra).
- La vigencia real de `reese84` y si basta con copiar la cookie o hace falta el perfil completo con el mismo UA/IP.
- Que el warm-up home → SERP funciona y que, ante un challenge visible, el scraper aborta registrando el marcador y el motivo, sin bucles de recarga (política de la persona, 2026-10-08).

## 11. Confirmaciones de la persona (2026-10-08)

Hechos confirmados hoy por la persona (2026-10-08); se registran como hechos, no como conclusiones de esta investigación:

1. **TOS («Uso del servicio»):** la persona revisó ella misma el texto de InfoJobs y no encontró ninguna cláusula sobre scraping ni acceso automatizado. Se registra como verificación humana con fecha 2026-10-08, sin convertirla en asesoramiento legal: la regla operativa sigue siendo `robots.txt` (SERP permitida) y datos públicos.
2. **Mismo dispositivo y red:** el scraper corre en el mismo dispositivo y red que la sesión real (misma IP), por lo que el reuso del token `reese84` (ligado a IP + fingerprint) es viable.
3. **Cuenta dedicada:** la sesión iniciada en el navegador corresponde a una cuenta de correo dedicada en exclusiva a este scraper («cuenta dedicada al scraper»); no se registra la dirección ni ningún otro dato de la cuenta.
4. **Política ante challenge (decisión operativa):** el diseño debe evitar el challenge; si aun así aparece uno visible, **abortar** y registrar el motivo, sin pausa larga por defecto y sin recargas ciegas. La prioridad es el diseño preventivo §6.0.
