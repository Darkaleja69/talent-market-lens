# Investigación web — glassdoor (run 2026-10-12, rama repair/glassdoor-20261012)

- **Fuente:** glassdoor (multi_site, portal primario fallido; outcome error, exit=75 watchdog).
- **Run de origen:** 2026-10-12. **Investigación:** 2026-10-12 (hora local +02).
- **Alcance:** el run de origen + sonda acotada de glassdoor.ie / glassdoor.es (requisito nuevo de la persona).
- **Modo:** solo lectura de código; sin sesión real (no se usó browser-cdp ni se cerró el Chrome de la persona); sondas HTTP/navegador con perfil propio y pocas peticiones. Nada de scrapers completos.

---

## 1. Hechos observados

### 1.1. Cronología verificada del run 2026-10-12

| Hora local (+02) | Hecho | Fuente |
|---|---|---|
| 00:00:16 | El pipeline lanza multi_site (PID 9752, wrapper run_all.ps1) | scrapers-pipeline/logs/upload-2026-10-12.log (L12) |
| 00:00:22.9 | Arranque del wrapper (RunStart); ancla del watchdog de glassdoor | multi_site_job_scraper/data/glassdoor/.watchdog.json (started_at) |
| ~00:05 | glassdoor (6.o de 6, stagger 60 s) arranca (inferencia de run_all.ps1 L106-133; coherente con banner 00:07:54) | run_all.ps1 |
| 00:07:54 | Primer log del proceso (`=== Glassdoor USA Scraper ===`) | data/glassdoor/run.log |
| 00:10:12 | Lanza Chromium headful | run.log |
| 00:17:37 | "playwright-stealth aplicado" | run.log |
| 00:17:43 → 00:18:17 | Homepage: "Page.goto: Timeout 30000ms exceeded", sin CSRF | run.log |
| 00:19:12 | findPopularLocationAjax: timeout 20 s → fallback (1, COUNTRY) | run.log |
| 00:19:16 | SERP 'Data Analyst' página 1 | run.log |
| 00:21:18-19 | BFF /graph devuelve 30 ofertas (total=137) → última línea escrita del log | run.log |
| 00:41:13 | Watchdog: decisión blocked (inactividad ≥ 40 min) y stop del árbol de procesos (exit 75) | .watchdog.json (mtime, stopped=true) |
| 01:32:20 | El wrapper multi_site termina exit=1; last_run.json: glassdoor=75 | upload-2026-10-12.log (L100-101); data/merged/last_run.json |
| 01:32:24 | Filtro de merge: 36 filas | 1 nueva | 35 ya subidas (no incluye el output de glassdoor, no actualizado este run) | upload-2026-10-12.log (L104) |

Estado persistido del watchdog (hecho): `last_parsed=0`, `last_progress_at` = ancla de arranque, `stopped=true`, `stop_reason=watchdog_no_progress`. Es decir: **el watchdog nunca vio un latido de glassdoor**.

### 1.2. Latidos PROGRESS: hecho central

- En todo `data/glassdoor/run.log` (14.516 líneas) hay **9 líneas `PROGRESS source=glassdoor`**, todas de los runs del 6-7 de octubre; la última (línea 14377) es `PROGRESS source=glassdoor parsed=240 new=239 ts=2026-10-06T22:19:18+00:00`.
- Los runs del **8, 9, 10, 11 y 12 de octubre no emiten ni un solo latido**. Cada noche el log se detiene tras 10-20 tarjetas (checkpoints `progress_..._p1_10.csv` / `p1_20.csv`) sin llegar a cerrar la página.
- `multi_site` terminó `exit=1` las noches del 8, 9, 10, 11 y 12 (upload-*.log). Recurrencia del mismo fallo.

### 1.3. Por qué no hay latido (código, hechos)

- `src/glassdoor/scraper.py`, `_process_role` (L256-409): `self._emit_progress(...)` se invoca **solo al final del bucle de tarjetas** (L382), una vez por página completa.
- Dentro del bucle (L356-368), por cada oferta nueva: `bff.fetch_description(...)` (timeout 30 s por intento; hasta 2 intentos → 60 s) + `time.sleep(0.8-1.8 s)`.
- **Cota máxima de silencio por página: 30 × (2×30 s + 1.8 s) ≈ 31 min.** En la noche del 12-oct el silencio entre la última línea (00:21:19) y el stop (00:41:13) fue de **19 min 54 s**, sin llegar a completar la página 1.
- Watchdog: umbral por defecto **40 min** (`scrapers-pipeline/verification/progress.py` L46; `INACTIVITY_OVERRIDES` vacío, L56). Bloquea si `parsed` no crece en ese tiempo; el ancla es el arranque declarado.
- **Agravante del wrapper:** `run_all.ps1` pasa `--started-at $RunStart` **global** (L168-169), aunque el arranque real del proceso se registra por portal en L125 (`StartedAt=(Get-Date)`, no usado). glassdoor es el 6.o lanzado (~5 min después del RunStart), de modo que su presupuesto real antes del primer latido era ~35 min y no 40.

### 1.4. Volúmenes y presupuesto actuales (config_gd.py + código)

- `date_filter="7"` (fromAge=7); 6 roles; `max_pages=15`; BFF `numJobsToShow=30` (el `jobs_per_search=200` no aplica al BFF, solo al DOM); `target=1000`; `fetch_descriptions=True`; `max_description_jobs=2000`; `do_detail=False`; `max_detail_jobs=0`.
- Reintentos: `search_jobs` = 3 intentos × 2 endpoints × 30 s (+4/7 s backoff) → hasta ~191 s por página; `fetch_description` = 2 intentos × 30 s sin backoff.
- Delays: between_pages 2-4 s; between_searches 6-10 s; por descripción 0.8-1.8 s.

### 1.5. Ritmo medido con el perfil persistente real (noches previas, no fase 2)

- Noche sana (7-oct, run.log): la página BFF de 30 tarjetas con sus descripciones tarda **55-72 s** (latidos consecutivos: parsed 90→120→150→180→210→240 entre 00:12:10 y 00:19:18); ≈ **1.8-2.4 s/tarjeta**; SERP→BFF 9-45 s.
- Noche 12-oct (degradada): SERP→BFF **122 s**; 0 latidos; 0 checkpoints.
- El tiempo aislado por descripción **con la sesión real no se ha medido** (queda para fase 2 si se autoriza): las mediciones actuales son del conjunto página+descripciones.

### 1.6. Hipótesis del playbook §6.5 (confirmadas / refutadas / pendientes)

| # | Hipótesis | Veredicto | Fundamento |
|---|---|---|---|
| H1 | Falta latido PROGRESS en el bucle de descripciones | **Confirmada** | §1.2-1.3: `_emit_progress` solo tras el bucle; `last_parsed=0`; silencio de 19m54s |
| H2 | El watchdog mata un trabajo que avanza | **Confirmada** | El BFF devolvió 30 ofertas; checkpoints `p1_10`/`p1_20` prueban avance en noches 8-11; el stop es por inactividad, no por error del scraper |
| H3 | 30 × 60 s de descripciones sin latido | **Confirmada como cota** | 2 intentos × 30 s + sleep (bff.py L629-682; scraper.py L356-368) |
| H4 | ¿Descripciones en lote o bajo demanda? | **Refutada hoy la vía lote** | JobDetailQuery toma un único `$jl` (bff.py L567-580); la búsqueda pide `job.description` pero en la práctica llega vacío (por eso se fetchea 1 a 1 y el bucle tarda ~2 s/tarjeta). Batch por alias GraphQL: no implementado y no verificado |
| H5 | ¿El presupuesto `max_description_jobs` es la causa? | **No es la causa** | El presupuesto (2000) supera el target (1000); el stop fue por silencio. Conviene declararlo y acotarlo, pero no explica el kill |
| H6 | Recurrencia desde el 8-oct | **Confirmada** | §1.2: cero latidos y multi_site exit=1 las noches 8-12 |
| H7 | /graph y salario EUR en .ie/.es | **Pendiente (no verificada)** | El SERP de ambos dominios quedó bloqueado por Cloudflare con perfil limpio, antes de cualquier XHR; requiere sesión real (fase 2) |

### 1.7. Requisito nuevo: búsquedas de Irlanda y España (sonda acotada)

Hechos:

- **glassdoor.ie** sirve un sitio regional en-IE: homepage 200 en navegador con perfil propio, URL final `https://www.glassdoor.ie/index.htm`, título "Glassdoor | Job Search and Career Community", bundles `en-IE`.
- **glassdoor.es** sirve sitio es-ES: título "Búsqueda de empleo en Glassdoor | Te mereces una empresa que te valore", URL final `https://www.glassdoor.es/index.htm`.
- **SERP bloqueado con perfil limpio (sin sesión):** `https://www.glassdoor.ie/Job/jobs.htm?sc.keyword=Data%20Analyst` → HTTP 403 + challenge Cloudflare y página "Humans only". `https://www.glassdoor.es/Empleo/empleos.htm?sc.keyword=Data%20Analyst` → HTTP 403 + challenge y página "Solo personas reales". No se intentó resolver ningún challenge.
- `curl` sin JS sobre ambas homepages: 403 "Security | Glassdoor" (anti-bot estándar a clientes no navegador).
- **No se pudo observar `/graph` por dominio** (el challenge corta antes del render de la SPA) ni las XHR de búsqueda; tampoco datos de salario de IE/ES. Sin sesión real (restricción de esta fase) la respuesta queda abierta.
- `robots.txt` por dominio (cabeceras "# Ireland (en-IE)" / "# Spain (es-ES)"): reglas equivalentes a .com, con rutas en español en .es (`/Empleo/`, `/Opiniones/`, `/Sueldos/`, `/Explorar/`, `/Compara/`).
- Ritmo observado en el histórico: el 23-jul el scraper ya operó contra `.es` (`/Empleo/data-analyst-empleos-SRCH_KO0,12.htm`) con extracción DOM (run_console.log antiguo). No hay evidencia reciente de que eso funcione hoy.

Parametrización necesaria (de código y sonda):

- `config_gd.py` ya tiene `base_url`, `country`, `country_name`, `currency`, `locale`, `timezone`, `profile_name`. Faltan por mercado: `search_path` (.ie usa `/Job/jobs.htm`; .es usa `/Empleo/empleos.htm`), resolución de país (el fallback actual `(1, "COUNTRY")` es el país US de glassdoor.com), marcadores anti-bot/landing en español ("solo personas reales", "sistemas de seguridad avanzados"), trato del interruptor `countryRedirect` (hoy se bloquea siempre, pensado para US), rutas de salida y perfil por mercado.
- El catálogo de fuentes (`scrapers-pipeline/verification/sources.py`, `MULTI_SITE_SITE_IDS`) tiene un único id `glassdoor`: decidir si IE y ES son fuentes nuevas o submódulos de una misma fuente afecta al diagnóstico (decisión del orquestador).

---

## 2. Evidencias

### 2.1. Evidencias versionadas (`repairs/20261012-glassdoor/evidence/`)

| Fichero | Contenido | Saneado |
|---|---|---|
| `investigation-report.md` | Este informe | Rutas <HOME>/<REPO> |
| `watchdog-state-20261012-saneado.txt` | Estado del watchdog (last_parsed=0, stop_reason, mtime 00:41:13) | Rutas <HOME> |
| `run-log-glassdoor-20261012-extract.txt` | Extracto del tramo del run 12-oct (desde el último banner hasta EOF) | Cookie de sesión redactada; rutas <REPO> |
| `progress-history-glassdoor.txt` | Los 9 latidos históricos y las últimas líneas de cada run posterior | Rutas <REPO> |
| `robots-com.txt`, `robots-ie.txt`, `robots-es.txt` | robots.txt de los tres dominios (consulta única) | Público |
| `robots-com.headers.txt`, `robots-ie.headers.txt`, `robots-es.headers.txt` | Cabeceras de respuesta de robots.txt | Set-cookie redactada |
| `ie-serp-network-summary.txt`, `es-serp-network-summary.txt` | Resumen de red de las sondas SERP (método/status/tipo/URL; sin cabeceras ni valores de cookies) | Solo nombres de cookies y URLs |
| `browser-probe-summary.txt` | Síntesis de las sondas IE/ES y de la revisión de TOS | IP oculta; sin tokens |
| `terms-of-use-clause.txt` | Cláusula de TOS sobre scraping | Texto público |

### 2.2. Capturas (`.opencode/.agent-screenshots/`)

- `20261012-gd-ie-home.png` — homepage de glassdoor.ie (200).
- `20261012-100724-gd-es-home.png` — homepage de glassdoor.es (200).
- `20261012-100643-gd-ie-serp-challenge-sanitized.png` — bloqueo "Humans only" de glassdoor.ie (IP ocultada).
- `20261012-100847-gd-terms.png` — revisión única de Terms of Use de glassdoor.com.

Nota: las capturas del challenge de .es y .ie sin sanear se eliminaron; queda la versión con IP ocultada.

### 2.3. Fuentes locales consultadas (sin modificar)

- `multi_site_job_scraper/data/glassdoor/run.log` (14.516 líneas), `.watchdog.json`, `run_console.log` (antiguo, 23-jul), `checkpoints/*.csv`, `output/jobs.csv`.
- `multi_site_job_scraper/data/merged/last_run.json`.
- `scrapers-pipeline/logs/upload-2026-10-12.log`, `transcript-2026-10-12.log`, `diagnostic_last.json`, `logs/upload-2026-10-0{8,9,10,11}.log`.
- Código: `src/glassdoor/{scraper,bff,serp,detail,config_gd,__main__}.py`, `src/core/progress.py`, `src/core/browser.py`, `run_all.ps1`, `verification/{progress,supervisor,sources}.py`.
- `repairs/20261012-glassdoor/{context.json,plan.md,quality_before.json}`, `repairs/history.json` (sin entrada previa de glassdoor).

### 2.4. Peticiones realizadas (presupuesto)

3 robots.txt (curl) + 2 homepages (curl, 403) + 5 vistas de navegador (2 homepages, 2 SERP, 1 TOS) + las XHR propias de esas páginas. Ninguna ejecución de scraper; ningún challenge resuelto.

---

## 3. Causa probable y su fundamento

**Causa raíz (confirmada):** el latido `PROGRESS` se emite solo al cerrar cada página (scraper.py L382), mientras que el trabajo real (descripciones, una llamada de hasta 60 s por oferta nueva) ocurre antes, dentro del bucle. El watchdog (40 min de inactividad, anclado en el arranque) no puede distinguir "avanzando en descripciones" de "colgado" y a las 00:41:13 detuvo el árbol con exit 75. Fundamentos: (1) `last_parsed=0` en el estado del watchdog; (2) cero líneas PROGRESS en el tramo del 12-oct y en los runs 8-11; (3) el BFF sí devolvió 30 ofertas y las noches 8-11 dejaron checkpoints de 10-20 tarjetas (el trabajo avanzaba); (4) cota de silencio de 31 min > presupuesto restante.

**Agravantes (confirmados):**
1. La noche del 12-oct el arranque fue lento y degradado (Chromium 00:10, homepage timeout 30 s, location timeout 20 s, SERP→BFF 122 s): el primer latido posible quedó a merced del bucle de descripciones.
2. `run_all.ps1` ancla el watchdog en el RunStart global, no en el arranque real de cada portal (hasta ~5 min antes), reduciendo el margen real.
3. Bajo degradación del BFF, cada descripción puede costar ~61,8 s y `desc_fetched` no avanza si la respuesta viene vacía: el bucle sigue intentando sin límite de fallos (solo el presupuesto de 2000).

**Consecuencia observada:** exit 75, `output_merged=False` para glassdoor, run diagnosticado como `error`; el pipeline subió solo las ofertas del merge parcial de otros portales (1 nueva de 36 filas).

---

## 4. Auditoría de cobertura de campos

Muestra de referencia local (ante la ausencia de datos del run fallido): checkpoint del run 6-7 oct, 249 ofertas, rol Data Analyst, US, `fetch_descriptions=True` (`data/glassdoor/checkpoints/progress_20261007_002024_gd_Data_Analyst_p9_10.csv`). `quality_before.json` tiene `current_pct=null` para todos los campos.

| Campo | ¿Disponible en web/API? | ¿Lo extrae el scraper hoy? | Brecha | Extracción recomendada | Base legal (robots/TOS) |
|---|---|---|---|---|---|
| id | Sí — BFF `job.listingId` | Sí (`gd_<id>`); 249/249 | Ninguna | Mantener | BFF /graph: Disallow en robots de .com/.ie/.es; TOS exige permiso escrito → escalado a la persona (RF-6) |
| title | Sí — `jobTitleText` | Sí; 249/249 | Ninguna | Mantener | Ídem BFF |
| company | Sí — `employerNameFromSearch` / `employer.name` | Sí; 249/249 | Ninguna (defensa anti-rating ya implementada) | Mantener | Ídem BFF |
| description | Sí — completa vía JobDetailQuery; la búsqueda trae `job.description` vacío en la práctica | Sí; 249/249 (longitud media 5.767 car.; min 296, máx 22.123) | Coste: 1 llamada/oferta y silencio sin latido; en modo DOM solo hay snippet | Mantener on-demand + latido; batch por alias GraphQL no verificado (fase 2) | Ídem BFF; el snippet DOM vendría del SERP (ruta no listada en Disallow, pero Cloudflare la bloquea con perfil limpio) |
| salary | Parcial — `payCurrency`, `payPeriod`, `payPeriodAdjustedPay{p10,p90}`, `salarySource` | Sí; `salary_raw` 230/249 (92,4%), moneda 249/249 (USD); estimado salvo `salarySource` de empleador | No es salario publicado (a menudo estimación); IE/ES sin verificar | Mantener; no prometer IE/ES hasta fase 2 | Ídem BFF |
| skills | No viene del portal; derivado de la descripción (`extract_skills`) | Sí; 193/249 (77,5%) | Depende de que la descripción llegue | Mantener | Derivado de description (misma base) |
| work_mode | Parcial — `locationType` S/O + texto de `locationName`/título | Sí; 44/249 (17,7%) | Dato escaso en el BFF; los contains textuales no cubren todo | Mantener heurística; no prometer más (do_detail encarece y no garantiza) | Ídem BFF |
| location | Sí — `locationName` (+ `parse_location_raw`) | Sí; 249/249 | Ninguna | Mantener | Ídem BFF |
| posted_date | Sí/derivado — `ageInDays` → fecha calculada | Sí; 249/249 (precisión de días) | Ninguna relevante | Mantener | Ídem BFF |

Nota adicional: `employment_type` = 0/249 en la muestra (el BFF de búsqueda no lo trae con `do_detail=False`); no es un campo del perfil de calidad, pero condiciona work_mode/contrato si algún día se piden.

---

## 5. Meta de calidad propuesta

Ámbito: prueba acotada §6.5 (1 rol, 1 página, ~30 ofertas), con BFF sano y `fetch_descriptions=True`. Referencia: muestra local 7-oct (documentada arriba); `quality_before.json` no tiene datos del run fallido.

| Campo | Obligatorio | Meta propuesta | Razonamiento |
|---|---|---|---|
| id | no | 100% | `listingId` siempre presente en jobview válido |
| title | sí | 100% | 249/249 en muestra; el BFF lo garantiza |
| company | sí | 100% | 249/249; defensa anti-rating ya implementada |
| description | sí | 100% no vacía (y verificar longitud razonable: muestra min 296, media ~5.700) | 249/249 con JobDetailQuery; **no prometer en modo DOM** (solo snippet) |
| salary | no | ≥90% en US (muestra 92,4%); IE/ES sin promesa | `payPeriodAdjustedPay` es estimación; Glassdoor no publica salario en toda oferta |
| skills | no | ≥75% (muestra 77,5%) | Derivado de descripción |
| work_mode | no | ≥15% (muestra 17,7%) | Dato estructuralmente escaso en el BFF; no bajar de lo actual |
| location | no | 100% | 249/249 |
| posted_date | no | 100% | 249/249 (derivado de ageInDays) |

Regla "nunca por debajo de lo actual": las metas igualan o superan la muestra de referencia; los opcionales sin cobertura del portal (p. ej. salario IE/ES) se documentan y no se prometen.

---

## 6. Cambio recomendado

**A. Núcleo del fix (§6.5) — latido y presupuesto**

1. `multi_site_job_scraper/src/glassdoor/scraper.py`
   - En `_process_role`, emitir latido **dentro del bucle de tarjetas** (p. ej. por tarjeta o cada 5): `self._emit_progress(1, 1 if is_new else 0)` → el hueco máximo entre latidos pasa de ~31 min a segundos.
   - Emitir un latido inmediatamente después de parsear la página (antes del bucle), para que la llegada de la página quede marcada aunque el bucle sea lento.
   - Mantener el latido de fin de página (L382) tal cual (semántica `parsed` acumulado, duplicados incluidos, según RF-15).
   - Las líneas van al logger "glassdoor" → `run.log`, que es el stream que lee el supervisor.
2. `multi_site_job_scraper/src/glassdoor/config_gd.py`
   - Declarar el presupuesto con criterio: mantener `max_description_jobs >= target` (2000 ≥ 1000) para no perder cobertura, y documentarlo.
   - Opcional (decisión del orquestador): parametrizar `fetch_description.timeout` y el nº de intentos, y añadir un umbral de fallos consecutivos o un presupuesto de tiempo de la fase de descripciones para noches degradadas (sin bajar la cobertura objetivo).
   - Nota: el presupuesto no es la causa del fallo (ver §3); es endurecimiento.
3. `multi_site_job_scraper/run_all.ps1` (agravante evidenciado)
   - Pasar `--started-at` con el arranque **por proceso** (`$e.StartedAt`, L125) en lugar del `$RunStart` global (L168-169), para que el ancla del watchdog coincida con el arranque real de cada portal.

**B. Multi-país IE/ES (requisito nuevo) — decisión previa obligatoria (ver §8)**

- Parametrizar por mercado en `config_gd.py` (o nueva estructura `markets`): `base_url`, `search_path` (`/Job/jobs.htm` vs `/Empleo/empleos.htm`), `country`/`country_name`, `currency` (EUR), `locale` (en-IE/es-ES), `timezone`, `profile_name` por mercado, `location_name` y resolución de país por dominio (el fallback `(1,"COUNTRY")` es US), `countryRedirect` condicionado al mercado, marcadores anti-bot/landing en español, y rutas de output por mercado.
- `scraper.py`: añadir marcadores del challenge en español ("solo personas reales", "sistemas de seguridad avanzados") para que `_check_blocked_or_landing` pare en vez de degradarse; revisar `_warmup_homepage` (warning hardcodeado a .es/.co.uk/USA) y `_card_to_offer`/`serp.parse_location` (país por defecto US).
- Decisión de alcance para el orquestador: fuente nueva por mercado (`sources.py`, watchdog por unidad en `run_all.ps1`) o submódulos del mismo id `glassdoor`.
- **Salario IE/ES:** sin verificar; no se promete. Requiere fase 2 con sesión real (observar la XHR de búsqueda y los campos pay* por dominio), y la autorización legal correspondiente.

**C. Nivel de escalera §6.0**

- Mantener el nivel actual: Playwright/patchright **headful** + perfil persistente real + warmup + pacing humano + reutilización de `cf_clearance` en vigencia + horas valle. El challenge invisible lo resuelve el navegador real; no se propone solver ni servicio de pago.
- Endurecer: reducir concurrencia (4 scrapers en paralelo degradaron el arranque: 7 min hasta Chromium y 7 min hasta stealth), y no limpiar el perfil.
- **No escalar** en esta reparación (proxies/servicios gestionados) sin decisión de la persona.

---

## 7. Comprobación manual propuesta

**1) Reproducir la evidencia del fallo (local, sin red):**

```powershell
# Latidos: 9, todos <=7-oct; la última parsed=240
Select-String -Path multi_site_job_scraper\data\glassdoor\run.log -Pattern 'PROGRESS source=glassdoor'
# Estado: last_parsed=0, stopped=true, watchdog_no_progress; mtime 2026-10-12 00:41:13
Get-Content multi_site_job_scraper\data\glassdoor\.watchdog.json
# El tramo del 12-oct termina en "BFF page 1 'Data Analyst': 30 ofertas"
Get-Content multi_site_job_scraper\data\glassdoor\run.log -Tail 30
Get-Content multi_site_job_scraper\data\merged\last_run.json
```

**2) Prueba en vivo acotada (la ejecuta el implementador tras el fix; alcance §6.5):**

```powershell
# Desde multi_site_job_scraper (1 rol, 1 página; la orden del playbook añade --role)
python -m src.glassdoor --role "Data Analyst" --max-pages 1
```

Criterio de éxito: aparecen líneas `PROGRESS source=glassdoor parsed=...` **intercaladas durante el procesado de tarjetas** (entre "BFF page 1" y "Page 1 ... procesadas"), con `parsed` creciendo. En paralelo, sin detener nada:

```powershell
# Desde scrapers-pipeline (PYTHONPATH apuntando a scrapers-pipeline), sustituir <PID> y la hora de arranque
python -m verification.supervisor --source glassdoor --pid <PID> --stream ..\multi_site_job_scraper\data\glassdoor\run.log --state ..\multi_site_job_scraper\data\glassdoor\.watchdog.json --started-at <ISO> --no-stop
```

Debe imprimir `state=keep` con `idle_seconds` pequeño mientras dure el bucle (antes del fix, el hueco se acercaba a 20-40 min).

**3) Calidad:** contar filas del output del test y cobertura por campo según §5 (obligatorios al 100% en el alcance).

**Aviso legal:** la prueba en vivo usa `/graph`, ruta en `Disallow` de robots en los tres dominios y sujeta a TOS ("express written permission", §8). No ejecutar sin la autorización de la persona.

---

## 8. Riesgos y límites

**Legales (RF-6) — requieren decisión de la persona:**

- `robots.txt` de glassdoor.com, .ie y .es **prohíbe** `/graph` (el endpoint del que dependen las ofertas y las descripciones), `/api/`, `/api-web/`, `/autocomplete`, `/searchsuggest`, `/findCityAjax.htm`, `/jobview/` y `*/*Ajax.htm` (que cubre `/findPopularLocationAjax.htm`, usado por `resolve_location`). El SERP simple (`/Job/jobs.htm?sc.keyword=...` y `/Empleo/empleos.htm?sc.keyword=...`) no está listado.
- Los **Terms of Use** prohíben, entre otras conductas, "Introduce software or automated agents to the services, or access the services ... to scrape, strip, or mine data from the services **without our express written permission**".
- Conclusión: la vía actual de datos (BFF) **no cumple robots/TOS**; la reparación del latido no cambia la vía, pero su prueba en vivo la ejercita, y la extensión a IE/ES multiplicaría la exposición. **No se propone ni se ejecuta** nada que aumente el acceso hasta que la persona decida (p. ej. permiso escrito, cambio de estrategia o asumir el riesgo de forma explícita).

**Bloqueo observado y su causa:** el challenge de Cloudflare se disparó en los SERP de .ie/.es con **perfil limpio y sin historial** (misma señal que el "humans only" intermitente en .com). Técnicas preventivas aplicables (§6.0): perfil persistente real y caliente, headful, warmup de homepage, reutilización de `cf_clearance` dentro de su vigencia (el perfil del proyecto ya lo hace), fingerprint coherente (sin UA fijo, ya implementado), ritmo humano, throttling (≤30 req/min/IP) y ejecución en horas valle. Los challenges invisibles los resuelve el navegador real solo; no se intentó ninguno visible.

**Sin verificar / límites:**

- No se usó la sesión real (restricción de fase): no se observaron `/graph` ni las XHR de búsqueda por dominio; no se confirmó el endpoint BFF en .ie/.es; no se midió el tiempo por descripción aislado ni el salario EUR de IE/ES. Si la persona autoriza, la fase 2 (browser-cdp con su Chrome) puede responderlo.
- El batch de descripciones (alias GraphQL) no está implementado ni probado; no se propone como parte del fix.
- El instante exacto del kill (00:41:13) se deduce del mtime del estado del watchdog y de la regla de 40 min; el stdout del wrapper run_all.ps1 no se conserva en fichero (ventana propia), por lo que no hay log directo del stop.
- Presupuesto de peticiones de esta investigación: 3 robots.txt + 2 homepages (curl) + 5 vistas de navegador + XHR propias. Sin sesiones, sin credenciales y sin cookies en evidencias (IP ocultada en capturas; rutas de usuario como <HOME>).
