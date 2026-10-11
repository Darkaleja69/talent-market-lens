# Informe de investigación — T-33 LinkedIn (spec 004, playbook §6.3)

**Run:** 2026-10-11 · **Rama:** `repair/linkedin-20261011` · **Snapshot:** 9686 ofertas · **Foco:** `description` (90,15 %)
**Sesión usada:** Chrome real del usuario por CDP 9222 (solo lectura; sin acciones de cuenta). Solo se abrieron `robots.txt` y las páginas legales; **no se hicieron peticiones a rutas de ofertas** (ver §8, hallazgo legal).

---

## 1. Hechos observados (y contraste con las hipótesis del playbook)

### Hechos
1. **`robots.txt` de LinkedIn (consultado 2026-10-11, 18:36 local, con el navegador real):** tiene **un único bloque genérico** y está al final del fichero: `User-agent: *` → **`Disallow: /`**. La cabecera avisa: *«The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited»* y remite a `whitelist-crawl@linkedin.com` para pedir permiso. Los bloques de bots con permiso (Googlebot, Bingbot, etc.) incluyen además `Disallow: /jobs-guest/`, `Disallow: /api/jobPostings/jobs*`, `Disallow: /jobs?runSearch*`, `Disallow: /jobs/view/externalApply/` y `Disallow: /voyager/api`.
2. **TOS (User Agreement §8.2, consultado 2026-10-11, 19:52 local):** prohíbe *«use software, devices, scripts, robots or any other means... (such as crawlers...) to scrape or copy the Services»*. **Crawling Terms** (referenciadas por robots.txt): *«Automated Crawling & Indexing without the express permission of LinkedIn is strictly prohibited»*; el uso permitido se limita a indexación para buscadores públicos salvo aprobación separada.
3. **Snapshot local (`data/output/jobs.parquet`, 9686 filas):** **954 filas sin `description`** (vacía o URL), no ~970: **950 de ellas con `scraped_at` ≤ 17-09-2026** (107 el 09-09, 30 el 10-09, **813 el 17-09**) y solo **4 posteriores** (1 el 18-09, 1 el 29-09, 2 el 10-10). El diagnóstico oficial (90,15 % ⇒ 954 ausentes) cuadra con el parquet.
4. **`state.json` del store:** 9686 `job_ids` procesados (`updated_at` 2026-10-10T22:51Z). El wrapper nocturno corre `python -m src.main --append --guest` (config: `jobs_per_search: 50`, `detail_max_jobs: 0`, `f_TPR=r604800` en ES; 30 días en IE/NL).
5. **Mecanismo de reintento (código `src/main.py:239-248`):** en cada combo se detallan las ofertas del SERP actual cuyo `description_full` esté vacío en el store. Es decir, **solo se reintenta una fila sin descripción si el job vuelve a aparecer en el SERP de ese combo dentro de su ventana temporal** (7 días ES / 30 días IE-NL) y dentro de las ~50 tarjetas. `Store.already_processed()` es **código muerto** (solo lo usan tests); `--append` no programa reintentos.
6. **Run del 11-10 (00:00–00:52 local):** 20 combos, `Ofertas locales OK: 19`, `Bloqueos locales: 0`, **14 avisos HTTP 429 en la API guest de búsqueda** (p. ej. `Guest API search fallo (start=0/10/20…): 429`), 1 error de detalle por navegación interrumpida (`4478028552`). Las 2 filas del 10-10 sin descripción **sí se intentaron** ese run (`Detalle: [4478026529]`, `Detalle: [4477822505]`) y **quedaron vacías sin ningún error ni aviso** (fallo silencioso de extracción).
7. **17-09 (origen del grueso del hueco):** las 813 filas sin descripción de ese día se capturaron entre las 08:00 y 10:59 UTC (run diurno de 10:35–12:48 local, tras los pilotos guest de esa mañana); el run nocturno de esa noche (21–23 UTC) dejó 0 filas sin descripción. Hubo un piloto guest (`pilot_guest_fix.log`, 17-09) que ya combinaba SERP + detalle por API guest.
8. **Salario:** `salary_raw` no vacío = 1803 filas, de las que **123 son basura inválida sin importe** (p. ej. `", libra"`, `". €"`, `"., Euro"`); válido por algún alias = 1680 (17,34 % local; el diagnóstico oficial da 1722 = 17,78 %). `salary_min` presente en 1669, `salary_max` solo en 426. En 4331 de las 7883 filas sin `salary_raw`, la descripción menciona moneda (techo bruto con falsos positivos).
9. **`work_mode`:** 4129 (42,63 %): Remoto 2132 / Híbrido 1721 / Presencial 276; 5557 vacías. Entre las vacías hay señales léxicas no cubiertas por la inferencia actual (variantes NL/DE como *hybride*, *Hybrides Arbeitsmodell*, *thuiswerk*): ≥637 filas.
10. **`skills`:** 7954 (82,12 %). Es **100 % dependiente de la descripción**: 91,09 % de cobertura entre filas con descripción (7954/8732) y **0/954 entre las que no la tienen**. Residuo de 778 filas con descripción sin keyword de la lista curada.
11. **`posted_date`:** 9336 (96,39 %); las **350 ausencias son todas de los runs del 09-09/09-10** (legado); 213 de ellas tienen `posted_relative` textual. Cobertura del 100 % desde el 18-09.
12. **Fixture autenticada (`tests/fixtures/auth_detail.html`, junio 2026):** no contiene `ld+json`, `baseSalary`, `salary`, `workplace` ni `skills`; sí el bloque de descripción (`expandable-text`, `componentkey`). Confirma el comentario del propio parser: el HTML estático autenticado no trae salario/skills estructurados; los componentes SDUI requieren hidratación.
13. **Sin `GuestCap`/CAPTCHA visible** en esta investigación ni bloqueos en el run del 11-10 (`Bloqueos locales: 0`); la señal dominante es **rate limiting 429** en la API guest.

### Hipótesis del playbook: confirmadas o refutadas
- **«El store reintenta filas sin descripción en cada run» → REFUTADA** (en el sentido útil). Solo reintenta si la oferta reaparece en el SERP del combo dentro de la ventana (7 días ES). Las 950 filas ≤ 17-09 llevan > 3 semanas fuera de ventana y nunca se revisitan: quedaron congeladas. La prueba: cobertura ~100 % desde 18-09 y hueco estable en las filas antiguas.
- **«~970 filas sin descripción con fecha ≤ 17-09» → CONFIRMADA** con precisión: 950 de 954 (99,6 %); las otras 4 son fallos puntuales posteriores.
- **«El detalle SDUI o la API guest exponen salario/workplace no parseados» → NO VERIFICABLE en vivo bajo las restricciones actuales** (robots/TOS; ver §8). Evidencia estática: el parser **ya lee** el criterio guest «Workplace type» cuando existe (test `test_guest_detail.py` lo cubre) y espera la hidratación SDUI de skills/empresa; no hay JSON-LD en la fixture autenticada. La existencia real de `baseSalary`/`jobLocationType` (JSON-LD) en el HTML guest actual **queda como hipótesis a validar en la prueba autorizada**, no como hecho.
- **«La vía menos costosa para completar el histórico»** → técnicamente: 1 GET por `job_id` a `jobs-guest/jobs/api/jobPosting/<id>` (954 peticiones; ~40–60 min al ritmo guest actual de 1,5–4 s) + merge por `Store.add` + medición. **Legalidad: esa vía está deshabilitada por robots.txt y los TOS** (ver §8). No se ejecutó ningún sondeo.
- **Causa del 17-09**: la fase de detalle de los runs previos al 18-09 fue **parcial/silenciosamente fallida** (mismo patrón que el fallo silencioso observado el 10-10); no hay histórico de config (commit inicial único del 19-09) para fechar límites concretos.

---

## 2. Evidencias

Todas versionadas en `repairs/20261011-linkedin/evidence/`:

| Evidencia | Qué demuestra |
|---|---|
| `robots_linkedin_2026-10-11.txt` | Texto completo íntegro de robots.txt |
| `robots_linkedin_extract_2026-10-11.txt` | Bloque genérico `*` `Disallow: /`, aviso de cabecera, restricciones de rutas de empleo y email de whitelist |
| `tos_linkedin_extract_2026-10-11.txt` | Citas literales de User Agreement §8.2 y Crawling Terms |
| `parquet_analysis_2026-10-11.txt` | Todas las cifras del §1 (fechas, horas, salario, work_mode, skills, posted_date, ejemplos) |
| `run_2026-10-11_extract.log` | Resumen del run, 429 de la API guest, error de detalle, intentos fallidos silenciosos de los 2 job_id del 10-10 |
| `pilot_guest_2026-09-17_extract.log` | Piloto guest del 17-09 (saneado, `<PERFIL_BROWSER>`) |
| `20261011-183636-robots-linkedin.png` | Captura robots.txt |
| `20261011-195241-user-agreement-linkedin.png` | Captura User Agreement |
| `20261011-195251-crawling-terms-linkedin.png` | Captura Crawling Terms |

Capturas originales también en `.opencode/.agent-screenshots/` (el CLI `agent-browser 0.38.1` en modo CDP ignora la ruta destino y guarda en su tmp; se copiaron manualmente). Rutas de usuario saneadas (`<HOME>`/`<PERFIL_BROWSER>`); sin cookies, tokens ni credenciales.

---

## 3. Causa probable y su fundamento

**Causa raíz del hueco histórico (doble):**
1. **Origen:** los runs anteriores al 18-09-2026 (y parte del 09-09/09-10) tuvieron una **fase de detalle parcialmente fallida**, que persistió filas solo-SERP (`store.write_all` guarda lo capturado aunque el detalle falle). Evidencia: 813/1613 filas del run diurno del 17-09 sin descripción; patrón por horas (08–10 UTC = run diurno; la noche de ese día dejó 0). Mecanismos observados en logs equivalentes: 429 de la API guest e intentos de detalle que terminan **silenciosamente vacíos** (2 filas del 10-10; no hay línea de error).
2. **Fijación (por qué nunca se reparó):** el diseño **solo reintenta detalles de ofertas presentes en el SERP del momento** (ventana 7 días + tope ~50 por combo). `already_processed`/`state.json` no alimentan ningún reintento. Al caer fuera de ventana, las 950 filas quedan inalcanzables para el scraper operativo. Fundamento: `src/main.py:239-248`, `src/store.py:85-92` (uso solo en tests) y la propia distribución del hueco (fijo ≤ 17-09 frente a ~100 % después).

**Hueco residual activo (4 filas):** fallos puntuales de extracción silenciosa en el flujo guest (`fetch_guest_job_html` devuelve HTML pero `_get_description` no encuentra descripción; la ruta de fallback a `/jobs/view` puede acabar en authwall, que **no** se considera challenge — `login.py` solo marca `/checkpoint/` y `/uas/challenge` — y devuelve vacío sin aviso). Sin captura del HTML en disco, la causa exacta (¿posting con detalle tras login?, ¿layout distinto?) no se puede cerrar sin una prueba autorizada.

---

## 4. Auditoría de cobertura de campos

Medición del diagnóstico 2026-10-11 (snapshot 9686; contrato en `verification/field_contract.py`; alias en `verification/sources.py:179-194`).

| campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal |
|---|---|---|---|---|---|
| `id` | Sí (URN del SERP) | Sí (`job_id`) | 0 % | — | robots/TOS: **no permiten** la vía (`*` → `Disallow: /`); pendiente de decisión (whitelist/permiso) |
| `title` | Sí (SERP) | Sí | 0 % | — | ídem |
| `company` | Sí (SERP) | Sí | 0 % | — | ídem |
| `description` | Sí (detalle guest; API HTTP) | Sí, pero con hueco histórico y fallos silenciosos | **954 filas (9,85 %)**; 950 ≤ 17-09 | 1) histórico: backfill por `jobPosting/<id>` **solo si la persona lo autoriza**, o justificación registrada (ruta que la propia meta del objetivo admite); 2) operativo: telemetría de descripción vacía tras detalle + reintento acotado en ventana | robots: deshabilitado; TOS: prohibido sin permiso expreso (User Agreement 8.2; Crawling Terms) |
| `salary` | Solo parcial: prosa de descripción (la mayoría no publica salario); posible JSON-LD `baseSalary` en guest (**hipótesis sin verificar en vivo**) | Sí, best-effort por regex (1803 raw; **123 inválidos**; 17,34 % local / 17,78 % oficial) | Calidad: 123 basura; cobertura: techo real desconocido | Endurecer regex (exigir importe contiguo a moneda; descartar `", libra"`), y parsear `baseSalary` de JSON-LD **si la fixture autorizada lo confirma**; fallback actual | robots/TOS: deshabilitado; sin permiso |
| `skills` | No es campo publicado: derivado de keywords de la descripción (+ SDUI si hidrata) | Sí, derivado (`_collect_skills`) | 82,12 %; **0 % entre filas sin descripción** y 778 sin match de la lista | Con el backfill (si se autoriza) subiría a ~91 %; ampliar lista curada es opcional y no promete 100 % | robots/TOS: deshabilitado; sin permiso |
| `work_mode` | Parcial: criterio guest «Workplace type» (si existe), caption auth, texto de la descripción | Sí, parcial (criterios + inferencia ES/EN) | 42,63 %; ≥637 vacías con señal léxica (incl. NL/DE) | Parsear criterio/JSON-LD cuando exista y ampliar inferencia multilingüe (sin nuevas peticiones); sin promesa de 100 % (el portal no lo publica siempre) | robots/TOS: deshabilitado; sin permiso |
| `location` | Sí (SERP) | Sí | 0 % | — | ídem |
| `posted_date` | Sí (`<time datetime>` del SERP; a veces solo relativo) | Sí (`posted_datetime`) | 96,39 %; 350 ausencias **solo legado 09-09/09-10** | Sin acción (100 % en filas nuevas); el histórico sin `datetime` no es recuperable sin re-fetch (descartado) | ídem |

Nota transversal: `robots.txt` deshabilita para `*` **todas** las rutas de empleo usadas hoy (`/jobs-guest/...`, `/jobs/search`, `/jobs/view`, y `/voyager/api` explícitamente); no hay ninguna ruta de datos de la que pueda decirse «permitida». Las únicas rutas consultadas en esta investigación (`robots.txt` y páginas legales) se abrieron de forma puntual y documentada para verificación de cumplimiento.

---

## 5. Meta de calidad propuesta (por campo, nunca por debajo de lo actual)

| Campo | Meta propuesta | Justificación |
|---|---|---|
| `description` | **100 % en el alcance de prueba** (1 rol × 1 ciudad guest). Histórico: **justificación registrada de no-backfill** (ruta admitida por la meta del objetivo) **salvo que la persona autorice el backfill**; si se autoriza, meta = recuperar lo que siga publicado, **sin prometer 100 %** (antigüedad ≥ 3,5 semanas en 950 filas) | Casos de uso y legalidad; la descripción completa es prioridad |
| `salary` | **≥ 17,8 % actual, sin regresión**; medir la mejora tras limpiar los 123 inválidos y (si se confirma JSON-LD) la extracción estructurada. **No prometer 100 %** (el portal no lo publica en la mayoría) | Opcional; ausencia legítima |
| `skills` | **≥ 82,1 %**, sin regresión; techo ~91 % con la descripción al día | Derivado, no publicado |
| `work_mode` | **≥ 42,6 %**, sin regresión; mejora esperada con criterio/JSON-LD + inferencia multilingüe; **no prometer 100 %** | Opcional; publicado solo a veces |
| `posted_date` | Mantener **≥ 96,4 %** global; 100 % en filas nuevas (ya se cumple) | Legado no recuperable sin re-fetch |
| `id`/`title`/`company`/`location` | Mantener 100 % | Obligatorios |

---

## 6. Cambio recomendado (módulos y ficheros; nivel §6.0)

**A. Decisión previa obligatoria (persona, no el inspector):** todo acceso automatizado a LinkedIn está deshabilitado por `robots.txt` (`*` → `Disallow: /`) y prohibido por TOS sin permiso expreso. Antes de cualquier ampliación (backfill) o continuación formal debe decidirse: (1) solicitar permiso (`whitelist-crawl@linkedin.com`, vía declarada), (2) usar una vía licenciada, o (3) **no-backfill documentado**. No se ejecutó ni se propone ejecutar el backfill sin esa decisión.

**B. Ruta compliant por defecto (sin acceso nuevo):**
- Registrar en el registro de reparación la **justificación del histórico**: 950 filas ≤ 17-09 fuera de la ventana SERP, sin reintento posible en el diseño actual, y vía de re-fetch deshabilitada por robots/TOS.

**C. Endurecimiento del pipeline actual (sin peticiones nuevas; mismos endpoints ya usados):**
- `src/parse_detail.py`
  1. **Extracción JSON-LD** (`script[type="application/ld+json"]` → `JobPosting`): `baseSalary`, `employmentType`, `jobLocationType`, `datePosted` — **solo si existe**, con fallback a lo actual. Requiere fixture capturada en la prueba autorizada; unit test offline.
  2. **Regex de salario**: exigir importe contiguo a moneda y descartar salidas sin dígitos/símbolo (elimina los 123 inválidos) → `_extract_salary_from_description`.
  3. **Inferencia de modalidad multilingüe** (`híbrid`+acento, `hybride`, `hybrides Arbeitsmodell`, `thuiswerk`, `remote`, `presencial`…) → `_infer_work_mode`.
  4. **Telemetría de fallo silencioso**: cuando el detalle guest devuelva HTML pero `description_full` quede vacía, emitir `WARNING` y `store.warning_inc("detail_desc_empty")` (hoy es invisible; las 2 filas del 10-10 lo demuestran).
- `src/main.py` + `src/store.py`: persistir en `state.json` los `job_id` con detalle pendiente y **reintentarlos de forma acotada mientras su ventana siga abierta** (backoff; mismo endpoint; sin volumen nuevo neto). Esto habría rescatado las filas del 09-09/10-09. *(Sigue siendo acceso a rutas deshabilitadas: incluirlo en la decisión A.)*
- `src/search.py`: honrar `Retry-After` y backoff ante 429 (hoy simplemente rompe la paginación y pierde cobertura).

**Nivel §6.0 recomendado:** mantenerse en el **nivel 1 (endpoint guest por HTTP + parseo)** con **ritmo humano y ≤ 30 req/min**; **no escalar** a sesión/navegación autenticada ni tokens de challenge (no hay challenge que resolver: la señal es 429, y escalar solo añadiría superficie). Nada de navegación a `/jobs/view` en guest (termina en authwall silenciosa); si se autoriza continuar, el detalle guest HTTP es la ruta mínima.

---

## 7. Comprobación manual propuesta (acotada y repetible)

1. **Tests sin red** (scraper y pipeline):
   - `python -m pytest tests -q` en `linkedin_jobs_scraper`
   - `python -m pytest scrapers-pipeline/tests -q` en la raíz
2. **Reproducir la auditoría** (sin red; ya en evidencia): ejecutar el script de `parquet_analysis_2026-10-11.txt` y contrastar: `desc sin contenido = 954`, `≤ 17-09 = 950`, salario inválido `= 123`.
3. **Prueba en vivo (solo con autorización de la persona, por robots/TOS):** alcance del brief 1 rol × 1 ciudad guest:
   - `python -m src.main --guest --role "Data Analyst" --city Madrid --max-jobs-per-search 10 --no-apify` (medir descripción; capturar 1 HTML guest de detalle como fixture, sin guardar cookies)
   - Criterio: `title`/`company`/`description` al 100 % en la muestra; 0 bloqueos; registrar cualquier 429; sin subida a Azure/landing/merge.
4. **Validar el cambio de parseo** con la fixture capturada (test offline): JSON-LD→`salary`/`work_mode` si existe; regex limpia (0 inválidos nuevos); `work_mode` multilingüe.
5. **Medición de calidad** con `quality.py`/`quality_after.json` sobre el parquet resultante y comparación contra `quality_before.json` (sin regresión en ningún campo).

---

## 8. Challenge, riesgos y límites

**¿Hubo challenge?** **No**: ni CAPTCHA visible ni checkpoint en la investigación ni en el run del 11-10 (`Bloqueos locales: 0`). El motivo: (a) la investigación solo abrió páginas públicas de infraestructura/legales con el navegador real (fingerprint coherente, sin acciones de cuenta); (b) el flujo guest del scraper no navega a zonas de challenge: recibe **429 (rate limit)** de la API guest, que es una señal de ritmo, no de identidad. Técnicas preventivas §6.0 aplicables al fix: ritmo humano/backoff ≤ 30 req/min y horas valle; si se usara la vía autenticada, sesión/cookies reales y fingerprint coherente (patchright) — no hacen falta tokens de challenge porque no aparecen; **nunca** solvers ni servicios de pago.

**Riesgos:**
- **Legal (bloqueante)**: robots `Disallow: /` + TOS que prohíben el scraping sin permiso. Toda la operación actual y cualquier backfill están afectados; **decisión de la persona** (permiso/whitelist, vía licenciada o cese). Es la razón por la que no se hicieron sondeos de ofertas ni backfill.
- **429 frecuentes** en la API guest de búsqueda (14 avisos en un run de 52 min): pérdida de cobertura y de puntos de paginación; hoy no hay `Retry-After`/backoff.
- **Fallos silenciosos de detalle** (2 filas del 10-10): sin telemetría, invisibles; pueden repetirse.
- **Backfill incierto**: 950 filas con ≥ 3,5 semanas (algunas > 4): muchas ofertas probablemente expiradas; no verificado (no se sondeó).
- Menor: error de logging por emoji y codepage cp1252 en stderr (`nightly_stderr_attempt1.log`); no afecta a datos.

**Límites de la investigación (lo que no se pudo comprobar y por qué):**
- **Estructura real del HTML guest actual** (JSON-LD, `Workplace type`, salario estructurado): no se abrieron rutas de ofertas porque robots/TOS lo deshabilitan y el contrato ordena parar y consultar. No hay fixture guest guardada en el repo (solo `auth_detail.html` de junio y HTML de SERP).
- **Accesibilidad de los ~954 `job_id` antiguos** y coste real del backfill: no sondeado por el mismo motivo.
- **Histórico de configuración del scraper** (cuándo cambió `detail_max_jobs`/`jobs_per_search`): repo con commit inicial único (19-09), sin historial.

**Decisiones que exceden los medios locales (para la persona, vía orquestador):**
1. Continuar/ampliar el acceso automatizado a LinkedIn pese a robots/TOS, o solicitar permiso (`whitelist-crawl@linkedin.com`), o sustituir por vía licenciada.
2. Autorizar o descartar el backfill de las 950+4 filas (con su presupuesto de peticiones y ventana horaria).
3. Aceptar la justificación registrada de no-backfill como cierre del objetivo `description` histórico.
