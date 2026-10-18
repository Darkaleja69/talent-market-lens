# Investigación web — irishjobs (run 2026-10-17, rama repair/irishjobs-20261017)

- **Fuente:** irishjobs (multi_site, portal primario fallido; outcome error, exit=75 watchdog).
- **Run de origen:** 2026-10-17 (fijado en `context.json`; no se cambia aunque haya runs nuevos).
- **Investigación:** 2026-10-17 (~07:34–07:50 UTC), fase acotada del `implementer`.
- **Alcance:** run de origen + sondas de navegador real sobre `www.irishjobs.ie` (SERP nacional, SERP de ciudad del run, SERP vacía, una ficha de detalle, robots.txt y términos de uso).
- **Modo:** sin modificar código ni ejecutar scrapers; sin sesión de la persona (no se usó `browser-cdp`); un solo navegador (Chrome propio de `agent-browser`, headful) y pocas peticiones; sin credenciales; capturas y extractos saneados.
- **Skills:** `scraping-expert` (diagnostic, hidden-apis, legal-ethics, anti-bot-strategies) y `agent-browser`.

---

## 1. Hechos observados

### 1.1. El fallo del run (local, verificado)

| Hecho | Evidencia |
|---|---|
| 0 tarjetas en TODOS los combos (`Tarjetas encontradas: 0`) y ninguna línea `PROGRESS` | `data/irishjobs/run.log` (extracto en evidence) |
| El run muere a mitad del combo 107/169 a las 00:41:39, sin línea de cierre ni error de navegación | últimas 16 líneas de `run.log` |
| Watchdog: `last_parsed=0`, `last_progress_at` = arranque (00:01:16), `stopped=true`, `stop_reason=watchdog_no_progress` | `data/irishjobs/.watchdog.json` |
| Exit del portal 75 (código del watchdog, no error del sitio) | `verification/supervisor.py` (`WATCHDOG_EXIT_CODE=75`) |
| `last_run.json`: `irishjobs=75`, `stepstone_nl=75`, `outputs_merged=[devitjobs, glassdoor]` | copia saneada en evidence |
| El `page.goto` no falla: no hay WARNING/ERROR de navegación en el tramo del run | `run.log` |

Estado histórico del propio fichero (append-only, 92.865 líneas, 69 banners de run):

- La **última línea `PROGRESS source=irishjobs` real** es de **2026-10-01 04:47:15** local (`parsed=1166 new=92 ts=2026-10-01T02:47:15+00:00`); ese run terminó sano con 92 ofertas y 92 detalles.
- **Después de esa línea hay 15 banners de run y ninguna línea PROGRESS**: 15 noches consecutivas con 0 tarjetas y 0 latidos.
- El primer run afectado es el inmediatamente posterior al run sano (arranca a las 00:01:36, primer combo con `Tarjetas encontradas: 0`).

**No hubo cambio de código propio en el componente entre el último run sano y el fallo** (`git log` de `multi_site_job_scraper/src`: entre el 01-10 y el 17-10 solo se tocaron módulos de `glassdoor`; `src/irishjobs/*` y `src/core/progress.py` no se han tocado desde el 27-09, antes del run sano). El cambio es del lado del sitio.

### 1.2. Qué devuelve la SERP real en navegador normal (pregunta central del playbook)

Hechos de las sondas (Chrome de `agent-browser`, 200 en todas las URLs):

| Sonda | Resultado |
|---|---|
| `GET /jobs/python-data?age=7` (la del run) | Título "Python Data job vacancies"; h1 "188 Python Data jobs"; **25** `[data-testid="job-item"]`; HTML del servidor (972 KB) ya contiene las 25 tarjetas y los 25 títulos (**SSR**, sin depender de JS) |
| Contenedores/atributos reales | `[data-at="unified-resultlist"]`, `[data-at="search-jobs-count"]`, `[data-at="job-item-company-name"]`, `[data-at="job-item-location"]`, `[data-at="job-item-timeago"]`, `[data-at="job-item-salary-info"]`, `[data-at="jobcard-content"]` |
| `GET /jobs/bi-developer/in-dundalk?age=7` (URL exacta del run) | h1 "0 BI Developer jobs in Dundalk + 30 km": la búsqueda real tiene 0 resultados, pero el DOM trae 1 `job-item` sugerido (Adecco, Dublin) |
| `GET /jobs/zzzqqq-nonexistent-role-xyz?age=7` (control de vacío) | `unified-resultlist` presente, 0 `job-item`, texto "There is no job matching your search" |
| Banner de consentimiento | "We use cookies" + "Accept All" en el DOM principal; las tarjetas están en el DOM aunque no se descarte |
| Challenge/CAPTCHA visible | **Ninguno** en las 5 vistas; sin redirecciones |

**Conclusión de hechos:** el HTML que un navegador normal recibe hoy contiene tarjetas perfectamente parseables con los selectores actuales; el scraper, en cambio, no encuentra ninguna desde el 02-10. Los selectores de `serp.py` **no han rotado**.

### 1.3. Anti-bot presente en irishjobs.ie

- Cabeceras del documento: `server-timing: ak_p; desc=...` y `x-akamai-transformed: 9 - 0 pmb=mRUM,2`; en robots.txt también `ak_p`.
- Scripts y telemetría Akamai por carga: `/akam/13/4461d845`, `/akam/13/pixel_4461d845`, POST del sensor a ruta ofuscada `/jXY_/.../QVIQ3I` (201), `Ping 204` a `akstat.io`.
- Cookie `ak_bmsc` (Akamai Bot Manager) en el navegador de la sonda.
- Consola: avisos de un widget Turnstile sin widget ("Cannot find Widget cf-chl-widget-...") y errores de hidratación React de `unifiedResultlist.js`; nada de esto bloquea el render.

**Perfil del scraper** (lectura solo-lectura de su SQLite de cookies, sin valores):

- La última escritura "normal" de cookies es del **2026-10-01 02:47Z** (run sano). Durante las 15 noches fallidas el sitio no volvió a emitir/renovar `AMP_*`, `utag_main`, `_fbp`, etc.
- La única cookie creada en ese periodo es **`_abck` (Akamai Bot Manager), el 2026-10-11 22:40Z**, y nunca se actualizó (`updated == created`): el sensor no llegó a renovarla.
- El perfil no tiene `ak_bmsc` (la cookie "de primera visita" que sí obtuvo el Chrome de la sonda).

### 1.4. API interna de búsqueda

- **No existe** un endpoint JSON del listado: el SERP es SSR y lo hidrata `unifiedResultlist.js`; no se observó ninguna XHR con los resultados.
- Solo hay endpoints BFF de sesión/cabecera (`/header-footer-bff/candidate/auth`, `/candidate-auth/whoami`, `latest-search-track` 401, `saved-jobs-count` 401) y tracking (`/analytics/api/v2/tracking`).
- Además, `robots.txt` veta explícitamente `/JobSearch/*/api/` y `/jobsearch/*/api/`. La vía "API interna" del nivel 1 de §6.0 **no está disponible** aquí.

### 1.5. Paginación y robots.txt

- No existe `link[rel="next"]`. La paginación está en `nav[aria-label="pagination"]` con `href` propios `...?page=N&ag=7`.
- El scraper construye `...?page=2&action=paging_next&age=7`: `Disallow: /*?action=` y `/*&action=` de robots.txt **vetan ese parámetro `action`**. El enlace propio del sitio no lo lleva, así que es sustituible sin perder paginación.
- `Disallow: /*&page=` veta `page` precedido de `&`, no `?page=N`; la forma del sitio (`?page=N&ag=7`) es compatible.
- SERP (`/jobs/...`) y detalle (`/job/...`) del grupo `User-agent: *` no casan con ningún `Disallow`: permitidos.

### 1.6. Términos de uso

- No hay cláusula que prohíba explícitamente rastreo/scraping (no aparecen "robot", "spider", "crawl", "scrap", "harvest").
- Sí prohíben: interferir/sobrecargar el servicio (4.8.1) y usar o extraer contenido "in any manner deemed to be in competition with our business activities" (4.8.2/4.8.3). Texto completo en `evidence/tos-irishjobs-prohibited-use.txt`.

### 1.7. Detalle de oferta y calidad de datos (auditoría, con muestra real)

- La ficha de detalle trae **JSON-LD `JobPosting`** con `description` completa de **1.726 caracteres** (muestra), `datePosted` exacto, `employmentType` (`["CONTRACTOR","REMOTE_WORKING"]`) y `hiringOrganization`.
- Sin embargo, en el **último parquet sano (2026-10-01, 92 filas)**:
  - `description_full` tiene **máximo 295 caracteres** en las filas no vacías (23/92 vacías): es texto de un elemento DOM que **sobrescribe** la descripción JSON-LD en `detail.py` (líneas 57-59 y 217-222: primero JSON-LD, después se asigna `result["description_full"] = _text(desc_el)` sin condición).
  - `salary_raw/min/max`: 80/92 (87 %); `work_mode`: 58/92 (63 %); `employment_type`: 82/92; `skills`: `"[]"` en las 92 (no se extrae; Databricks la deriva de la descripción).
  - `description_snippet` (primeros 300 caracteres de la tarjeta) 92/92, con ruido (título, empresa, ubicación, salario).
- El campo `skills` del parquet está vacío por diseño del portal: irishjobs no expone skills estructuradas.
- `data-at="search-jobs-count"` y el contenedor `unified-resultlist` dan una vía fiable para distinguir "página vacía legítima" de "página bloqueada".

### 1.8. Hipótesis del playbook §6.4: veredictos

| # | Hipótesis | Veredicto | Fundamento |
|---|---|---|---|
| H1 | ¿Challenge/HTML distinto/geo en la SERP de IE? | **Parcial: bloqueo al navegador del scraper, contenido intacto al navegador normal** | Navegador normal: 200 + 25 tarjetas SSR; scraper: 0 tarjetas 15 noches con `goto` sin error (bloqueo silencioso o challenge no resuelto en su navegador) |
| H2 | ¿Existe API interna? | **Refutada** | Listado SSR/hidratación; solo BFF de sesión; robots veta `/JobSearch/*/api/` |
| H3 | ¿Cambiaron selectores/estructura? | **Refutada** | Todos los selectores de `serp.py` existen hoy en la SERP real (`job-item`, `job-item-title`, `job-card-content`, `data-at` de empresa/ubicación/timeago) |
| H4 | ¿Hay que emitir `PROGRESS` por página (0 tarjetas también es avance)? | **Confirmada** | Sin tarjetas no se llama a `_emit_progress` (`scraper.py` L268-312): `parsed` nunca crece y el watchdog mata a los 40 min |
| H5 | ¿Es la clase compartida el problema? | **Refutada** | `stepstone_nl` (misma clase) sí parsea tarjetas (1/5/2 por página) con el mismo navegador |
| H6 | ¿Falta un latido también cuando el filtro de título descarta todo? | **Confirmada (defecto adicional)** | El contador usa las tarjetas **filtradas** (`len(cards[:jobs_per_search])`): en stepstone.nl los descartes dejan `parsed=0` aunque haya tarjetas, y el watchdog también mata ese portal (75 en `last_run.json`) |
| H7 | ¿La descripción completa llega al parquet? | **Refutada** | JSON-LD tiene 1.726 car. en la muestra; el parquet guarda ≤295 car. porque el DOM sobrescribe el resultado del JSON-LD |

### 1.9. Presupuesto de peticiones de esta fase

robots.txt (1) + TOS (1) + 5 vistas de navegador (SERP nacional ×2, SERP Dundalk, SERP vacía, detalle; sus XHR propias) + 1 fetch same-origin del HTML ya cargado. Sin sesión de la persona, sin credenciales, sin resolución de challenges, sin servicios de pago.

---

## 2. Evidencias

### 2.1. Ficheros versionados (`repairs/20261017-irishjobs/evidence/`)

| Fichero | Contenido | Saneado |
|---|---|---|
| `investigation-report.md` | Este informe | Rutas de usuario como `<REPO>`/`<HOME>`; sin credenciales |
| `robots-irishjobs.txt` | robots.txt completo + lectura de reglas aplicables | Público |
| `robots-irishjobs.headers.txt` | Cabeceras de robots.txt (marcador Akamai `ak_p`) | IDs internos `x-amz-*` omitidos |
| `serp-document-headers.txt` | Cabeceras del documento SERP (`ak_p`, `x-akamai-transformed`) | Sin cookies |
| `serp-probe-facts.md` | Hechos de las 4 sondas de navegador (conteos, `data-at`, paginación, detalle JSON-LD, señales anti-bot) | Sin datos personales |
| `serp-network-summary.txt` | Resumen de red (método/status/tipo/URL) de irishjobs.ie | Sin valores de cookies |
| `run-log-irishjobs-20261017-extract.txt` | Extracto del `run.log`: último run sano, transición a 0, run de hoy, estadísticas | Rutas `<REPO>` |
| `watchdog-state-20261017.txt` | `.watchdog.json` y su lectura | Ruta `<REPO>` |
| `merged-last-run-20261017.json` | Copia saneada de `last_run.json` | Sin rutas |
| `profile-cookies-irishjobs-names-created.txt` | Nombres de cookies del perfil del scraper y fechas de creación (sin valores) | Sin valores |
| `tos-irishjobs-prohibited-use.txt` | Cláusula 4.8 de los términos + búsqueda de términos | Texto público |
| `stepstone-nl-context.txt` | Extracto del run de stepstone.nl (misma clase) | Rutas `<REPO>` |

### 2.2. Capturas

En crudo: `.opencode/.agent-screenshots/` (fuera de git). Copias saneadas en `evidence/`:

- `20261017-073500-irishjobs-serp-python-data-full.png` — SERP nacional completa con las 25 tarjetas y el banner de cookies.
- `20261017-073800-irishjobs-serp-python-data-viewport.png` — vista de la misma SERP.
- `20261017-074000-irishjobs-serp-dundalk.png` — SERP de Dundalk del run (0 resultados propios + 1 sugerida).

No hay capturas de bloqueo/credenciales: no se obtuvo ningún challenge visible ni se usó sesión.

### 2.3. Fuentes locales consultadas (sin modificar)

- `multi_site_job_scraper/data/irishjobs/{run.log,.watchdog.json,output/jobs.parquet,checkpoints/state.json}`.
- `multi_site_job_scraper/data/stepstone_nl/run.log`; `data/merged/last_run.json`.
- `multi_site_job_scraper/src/irishjobs/{scraper,serp,detail,config_ie}.py`, `src/core/{browser,progress}.py`, `src/stepstone_nl/__main__.py`.
- `scrapers-pipeline/verification/{progress,supervisor,sources}.py`; `logs/upload-2026-10-17.log`.
- `repairs/20261017-irishjobs/{context.json,plan.md,quality_before.json}`.

---

## 3. Causa probable y su fundamento

**Causa primaria (probable, con evidencia fuerte): desde la noche del 1 al 2 de octubre, irishjobs.ie sirve al navegador del scraper una respuesta mitigada (bloqueo silencioso) sin listado de resultados, mientras un navegador normal recibe el SERP completo por SSR.** Fundamentos:

1. El navegador normal obtiene 200 + HTML con 25 tarjetas y todos los `data-at` que usa el parser; el scraper obtiene páginas sin ninguna tarjeta en 15 noches consecutivas.
2. El `page.goto` del scraper no lanza error (no hay 403/timeout): el patrón "200 con contenido distinto/vacío" es el bloqueo silencioso típico (política de la skill: validar contenido, no confiar en el 200).
3. irishjobs.ie está tras Akamai Bot Manager (marcadores `ak_p`/`x-akamai-transformed`, scripts `/akam/...`, sensor ofuscado) y el perfil del scraper acumuló `_abck` el 11-10 sin renovarla, sin que el sitio volviera a emitir sus cookies normales tras el 01-10.
4. No hubo cambio propio de código en el componente entre el último run sano y el fallo.

El **cuerpo exacto** que recibe el navegador del scraper no se ha podido observar en esta fase (no se ejecutan scrapers): la reparación debe añadir un volcado saneado de diagnóstico (título/URL/status/marcadores) para confirmarlo en la prueba en vivo. Si en esa prueba apareciera un challenge visible, se para y se reporta (RF-4, modo asistido).

**Causa secundaria (defectos de diseño confirmados, que convierten "0 tarjetas" en muerte por watchdog):**

1. **Sin latido cuando no hay tarjetas:** `_process_combo` corta en `if not raw_cards: break` **antes** de `_emit_progress` (L268-271, L312); si ninguna página trae tarjetas, `parsed` nunca crece y el watchdog (40 min) mata el proceso. El playbook pide justo esto: "0 tarjetas también es avance".
2. **El contador ignora las tarjetas descartadas por el filtro de título:** `_emit_progress(len(cards[:jobs_per_search]), ...)` cuenta tarjetas **filtradas**; stepstone.nl demuestra el mismo síntoma (`parsed=0` con tarjetas en página).
3. **No hay detección ni registro de bloqueo:** el scraper no distingue "0 resultados" de "página mitigada"; no deja motivo, ni evidencia, ni para temprano tras N combos vacíos.
4. **Paginación no conforme a robots.txt:** `action=paging_next` está en `Disallow` (RF-6); el enlace propio del sitio no lo usa.
5. **`description_full` se degrada:** el DOM sobrescribe la descripción JSON-LD completa (1.726 car. → ≤295 car. observados).

---

## 4. Auditoría de cobertura de campos

Base: código actual + última muestra sana (parquet del 2026-10-01, 92 filas) + observación del detalle/servidor hoy. `quality_before.json` no tiene datos del run fallido (nulls).

| Campo | ¿Disponible en web/API? | ¿Lo extrae el scraper hoy? | Brecha | Extracción recomendada | Base legal (robots/TOS) |
|---|---|---|---|---|---|
| `id` | Sí — en la URL `/job/<slug>-job<id>` | Sí (regex), 92/92 | Ninguna | Mantener | `/job/` permitido para `*`; TOS sin prohibición explícita |
| `title` | Sí — tarjeta (`job-item-title`) y JSON-LD | Sí, 92/92 | Ninguna | Mantener | Ídem |
| `company` | Sí — `data-at="job-item-company-name"` y JSON-LD `hiringOrganization` | Sí, 92/92 | Ninguna | Mantener | Ídem |
| `description` | Sí — **completa** en el JSON-LD `JobPosting.description` del detalle | Parcial: JSON-LD se sobrescribe con texto DOM (máx. 295 car.); 23/92 vacías | **Prioritaria**: la descripción completa es la base de las skills en Databricks | Dar prioridad al JSON-LD y no sobrescribirlo con DOM; 1 visita de detalle por oferta nueva | Detalle público permitido por robots; TOS 4.8.1 (no sobrecargar) |
| `salary` | Parcial — tarjeta `job-item-salary-info` (o "€ Not Disclosed") y JSON-LD `baseSalary` a veces | Sí, 80/92 (87 %) | No prometer 100 %: el portal no lo publica en todas | Mantener regex de tarjeta + `baseSalary` del JSON-LD | Ídem |
| `skills` | No estructurado (solo derivable del texto) | No; `skills="[]"` (0 %) | No es fallo del scraper: Databricks las deriva de `description` | Mantener derivación en Databricks; no extraer aquí | Ídem |
| `work_mode` | Parcial — facetas/top-label "Work From Home", texto de la tarjeta y `employmentType` del JSON-LD (`REMOTE_WORKING`, etc.) | Sí, 58/92 (63 %) | `employmentType` estructurado sin mapear | Mapear `REMOTE_WORKING`/`TELECOMMUTE`→Remote y `HYBRID`→Hybrid desde el JSON-LD; conservar heurística de texto | Ídem |
| `location` | Sí — `data-at="job-item-location"` | Sí, 92/92 | Ninguna | Mantener | Ídem |
| `posted_date` | Sí — `job-item-timeago` (relativo) y JSON-LD `datePosted` (exacto) | Sí, 92/92 | Precisión mejorable | Preferir `datePosted` del JSON-LD cuando exista | Ídem |

Nota: los campos `requirements`/`responsibilities`/`benefits`/`company_description`/`company_industry` también salen del detalle (62/64/51/67/69 de 92) y no forman parte del perfil de calidad; su mejora llega de arrastre al arreglar la prioridad del JSON-LD y el detalle.

---

## 5. Meta de calidad propuesta

Ámbito: prueba acotada §6.4 (1 rol, 1 página, `python -m src.irishjobs --max-pages 1`), con detalle activo. Referencia "actual" = última muestra sana local (2026-10-01).

| Campo | Obligatorio | Meta propuesta | Razonamiento |
|---|---|---|---|
| `id` | no | 100 % | Siempre en la URL de la tarjeta (92/92) |
| `title` | sí | 100 % | 92/92; tarjeta SSR |
| `company` | sí | 100 % | 92/92; `data-at` estable |
| `description` | sí | **100 % con descripción completa** (longitud en el rango observado del JSON-LD; no el fragmento DOM de ~300 car.) | JSON-LD presente en el detalle (1.726 car. en muestra); la meta mide "la mejor descripción que la vía permitida puede almacenar" |
| `salary` | no | Mantener ≥ 85 % en muestras equivalentes (actual 87 %) | Solo cuando el anunciante lo publica; nunca prometer 100 % |
| `skills` | no | No se promete (derivadas en Databricks) | El portal no expone skills |
| `work_mode` | no | Mantener ≥ 60 % y mejorar con `employmentType` (actual 63 %) | Dato parcial del portal; el mapeo JSON-LD añade cobertura |
| `location` | no | 100 % | 92/92 |
| `posted_date` | no | 100 % (mejor con `datePosted` exacto) | 92/92 |

Regla "nunca por debajo de lo actual": las metas igualan o superan la muestra sana; los opcionales que el portal no publica (skills; salario cuando va "Not Disclosed") se documentan y no se prometen.

---

## 6. Cambio recomendado

### 6.1. Latido y supervivencia al watchdog (`src/irishjobs/scraper.py`, `src/core/progress.py`)

1. **Emitir `PROGRESS` por cada página SERP procesada**, incluida la de 0 tarjetas: adelantar el latido para que también cubra el caso `not raw_cards` (p. ej. contabilizar `max(1, len(raw_cards[:jobs_per_search]))` por página, o separar "páginas procesadas" del contador), de modo que `parsed` crezca al menos 1 por página visitada. Documentar en `core/progress.py` que `parsed` cuenta unidades de trabajo procesadas (páginas + tarjetas) y que una página vacía es progreso.
2. **Contar las tarjetas parseadas antes del filtro de título** (o `max(1, …)`), para que los descartes por filtro no congelen el contador (caso stepstone.nl).

### 6.2. Detección de bloqueo con motivo (`src/irishjobs/serp.py` + `scraper.py`)

3. Añadir a `serp.py` un `detect_block(page) -> (blocked, reason, markers)` que use marcadores fiables observados hoy:
   - `blocked`: no existe `[data-at="unified-resultlist"]`, ni `[data-at="search-jobs-count"]`, ni tarjetas, y/o el título/body contiene marcadores de challenge ("Access Denied", "Just a moment", "Pardon Our Interruption", "Attention Required", "captcha", "challenge", "forbidden"), o el status/URL no es el esperado.
   - `no_results` (vacío legítimo, cuenta como avance): `unified-resultlist` presente o texto "There is no job matching your search".
4. Cuando `blocked`: registrar una línea de máquina con motivo (`BLOCKED source=irishjobs reason=<...> url=<...> status=<...>`), volcar una evidencia saneada (título, URL, status y marcadores; opcionalmente un recorte del HTML sin cookies) y **parar el portal** con el motivo (parada temprana; no recorrer 169 combos). Si el bloqueo fuese un CAPTCHA visible: pausar y avisar (no resolver nunca).
5. Parada temprana adicional (recomendada): tras N combos consecutivos con página válida pero 0 resultados, terminar el run por "sin resultados" sin esperar al watchdog.

### 6.3. Flujo humano y huella (§6.0) (`src/irishjobs/scraper.py`, `src/core/browser.py`)

6. **Calentamiento de sesión**: antes de los combos, navegar una vez a la homepage, descartar el banner de consentimiento (ya existe `dismiss_cookie_banner`, que hoy solo se llama tras cada SERP) y, si es viable, lanzar la primera búsqueda desde el formulario; después reutilizar el mismo contexto para todo el run. Mantener perfil persistente y dominio de cookies (no limpiar).
7. **Quitar el parche manual de `navigator.webdriver`** (`src/core/browser.py` L94-96): patchright ya lo cubre; el parche añadido a mano es una inconsistencia de huella conocida (la doctrina de la skill lo desaconseja). Si se toca un módulo compartido, hacerlo sin romper glassdoor/jobs_ch (o parametrizarlo).
8. Mantener UA nativo, delays gaussianos, scroll y el límite de ritmo actual (≈2-4 s/página dentro del rango humano), y ejecutar en horas valle (ya ocurre).

### 6.4. Paginación conforme a robots (`src/irishjobs/serp.py`)

9. Dejar de construir `...?page=N&action=paging_next`: usar el `href` del enlace propio del sitio (`nav[aria-label="pagination"]`, forma `?page=N&ag=7`) o, en su defecto, `?page=N` sin `action`. Ajustar `has_next_page`/`go_to_next_page` para no depender de `link[rel=next]` (no existe) y conservar el fallback actual por clics.

### 6.5. Descripción completa (`src/irishjobs/detail.py`)

10. No sobrescribir `description_full` del JSON-LD con el texto del DOM: rellenar desde el DOM **solo si el JSON-LD no trajo descripción**. Igual criterio para el resto de campos (salario, fecha, industria) ya aplicado.

### 6.6. Tests (`multi_site_job_scraper/tests`)

11. Fixtures HTML saneadas del SERP real (con tarjetas y sin resultados) y del detalle (JSON-LD) para: `parse_cards` con los selectores actuales; `detect_block` (bloqueado vs vacío legítimo); latido `PROGRESS` con 0 tarjetas (el contador crece); construcción de URL de paginación sin `action`; prioridad de la descripción JSON-LD.
12. Suites: `python -m pytest tests -q` desde `multi_site_job_scraper` (y `scrapers-pipeline/tests` si se toca `core/progress.py` o el contrato del latido).

---

## 7. Por qué se disparó el bloqueo y técnicas preventivas (§6.0)

**Hipótesis del disparo (hechos que la sostienen):** un cambio del lado de irishjobs.ie alrededor del 01/02-10 (p. ej. activación/endurecimiento de Akamai Bot Manager) empezó a tratar al navegador del scraper como bot y a servirle una página mitigada. Contribuyen:

- **Flujo de navegación no humano:** 169 combos con URL directa a la SERP (sin pasar por la homepage ni el formulario de búsqueda), repitiendo cada ~20-40 s; la doctrina §6.0/anti-bot lo marca como señal de comportamiento.
- **Huella instrumentada:** Chromium de patchright con flags (`--disable-blink-features=AutomationControlled`, `--disable-features=IsolateOrigins,site-per-process`, `--no-sandbox`, `--disable-dev-shm-usage`) y el parche manual de `navigator.webdriver` (redundante y potencialmente inconsistente con patchright).
- **Perfil frío para Akamai:** el perfil no renovó cookies normales desde el 01-10 y no tiene `ak_bmsc`; el sensor no llegó a completar el ciclo (`_abck` sin actualizar).
- **Volumen/cadencia fijos** del run completo (≈1.000+ páginas/detalles por noche en ~40 min), aunque con delays gaussianos.

**Técnicas preventivas aplicables (en orden de la escalera, sin servicios de pago):**

1. API JSON interna: **no disponible** (listado SSR; BFF vetado por robots).
2. Sesión/cookies reales: mantener el perfil persistente y **calentarlo** (homepage + consentimiento + búsqueda) al inicio del run; no limpiar cookies.
3. Replay de tokens: el perfil conserva `_abck`/cookies de Akamai; con IP y UA estables se reutilizan dentro de su vigencia. No forzar su borrado.
4. Fingerprint coherente: patchright sin parches manuales (`navigator.webdriver`), UA nativo, y valorar actualizar la build del navegador si la prueba en vivo revela huella antigua.
5. Humanización/ritmo: warmup, consentimiento primero, delays gaussianos (ya), ≤30 req/min/IP (holgado hoy), parada temprana ante bloqueo/0, horas valle (ya).
6. Challenges invisibles: los resuelve el navegador real; no interferir. Un CAPTCHA visible ⇒ pausa asistida y reporte (nunca solver).

---

## 8. Comprobación manual propuesta

**1) Reproducir el fallo (offline, sin red):**

```powershell
# 0 tarjetas en los 15 runs posteriores al 2026-10-01; última PROGRESS el 01-10
Select-String -Path multi_site_job_scraper\data\irishjobs\run.log -Pattern 'PROGRESS source=irishjobs' | Select-Object -Last 1
Select-String -Path multi_site_job_scraper\data\irishjobs\run.log -Pattern 'IrishJobs Scraper ===' | Measure-Object
Get-Content multi_site_job_scraper\data\irishjobs\.watchdog.json
Get-Content multi_site_job_scraper\data\irishjobs\run.log -Tail 16
```

**2) Prueba en vivo acotada (tras el fix; alcance §6.4: 1 rol, 1 página, sin merge):**

```powershell
# Desde multi_site_job_scraper
python -m src.irishjobs --max-pages 1
```

Criterio de éxito: **≥ 1 oferta** (umbral 1; sin historial en `repairs/history.json`), líneas `PROGRESS source=irishjobs` **por página**, sin exit 75 y con `description_full` completa en la muestra. Si el portal sigue mitigado, la prueba debe producir la línea `BLOCKED ... reason=...` con el volcado de diagnóstico (eso confirma el bloqueo e informa la escalada). Durante la prueba puede observarse el watchdog sin parar nada:

```powershell
# Desde scrapers-pipeline
python -m verification.supervisor --source irishjobs --pid <PID> --stream ..\multi_site_job_scraper\data\irishjobs\run.log --state ..\multi_site_job_scraper\data\irishjobs\.watchdog.json --started-at <ISO> --no-stop
```

**3) Puerta de calidad:** `python -m repair.cli quality --record "../repairs/20261017-irishjobs" --parquet "multi_site_job_scraper/data/irishjobs/output/jobs.parquet"` (desde `scrapers-pipeline`), con obligatorios al 100 % y sin regresión.

---

## 9. Riesgos y límites

- **Bloqueo:** no se ha confirmado el cuerpo exacto que recibe el navegador del scraper (no se ejecutan scrapers en esta fase). La causa "bloqueo silencioso/Akamai" es la hipótesis con más fundamento, pero la prueba en vivo lo debe confirmar con el volcado. Si tras el fix el bloqueo persiste, las opciones locales se agotan y se escala a la persona (RF-5: proxies/otras, sin aplicarlas).
- **CAPTCHA visible:** si apareciese, no se resuelve: pausa asistida y reporte (RF-4). En las sondas de hoy no apareció ninguno.
- **robots.txt/TOS (RF-6):** SERP y detalle están permitidos para `User-agent: *`; la paginación actual con `action=paging_next` **no** es conforme y el fix la sustituye por el enlace del sitio. Los términos no prohíben el acceso automatizado de forma explícita, pero sí el abuso (4.8.1) y el uso "in competition" (4.8.2/4.8.3): mantener el volumen bajo (el scraper ya va con ritmo humano) y confirmar con la persona si el uso entra en esa valoración.
- **Credenciales/sesión:** no se usó la sesión de la persona ni credenciales; las evidencias no contienen valores de cookies, tokens ni rutas de perfil (solo nombres y fechas de cookies del perfil del scraper).
- **Alcance compartido:** `stepstone_nl` comparte clase y también sufre el defecto de latido (contador filtrado); **no se repara aquí**, pero el cambio de latido debe dejar el comportamiento correcto para ambos portales (mismo módulo).
- **Pendiente de verificar en implementación:** tipo exacto de respuesta mitigada; impacto de retirar el parche manual de `navigator.webdriver` (módulo compartido con glassdoor/jobs_ch); conveniencia de una parada temprana por N combos vacíos.
