# Observaciones en vivo — Indeed IE (2026-10-18) — saneado

- **Fecha/hora local:** 2026-10-18, 17:15–17:23 (Europe/Madrid/UTC+2).
- **Herramienta:** `agent-browser 0.38.1`, sesión propia `default` (navegador propio, perfil fresco; **no** se usó el Chrome del usuario: el puerto CDP 9222 estaba caído y no se cerró ni arrancó ningún Chrome).
- **Alcance de la prueba:** 1 SERP IE `https://ie.indeed.com/jobs?q=data&l=Dublin&radius=25&fromage=7` (misma URL que el run nocturno, página 1) + 2 clics en tarjetas.
- **Resultado en una línea:** la SERP y el panel funcionan sin challenge; la descripción completa está disponible por la UI (clic), el salario solo cuando el portal lo publica (si no, muestra "Pay information not provided"), `work_mode` se expone solo para remotas/híbridas y **no existe ningún campo estructurado de skills**.
- **Saneado:** sin cookies, tokens, `ctk`, api keys ni rutas de perfil (la sesión de agent-browser vive en `<HOME>\.agent-browser`). Las capturas referenciadas están fuera de git.

## 1. Carga de la SERP (hecho)

- Título servido: `105 Data jobs, employment in Dublin, County Dublin 18 October 2026 (16 new openings) | Indeed`.
- URL final tras auto-open: `...&vjk=f0fa7506b1b51b1a` (Indeed selecciona una oferta y embebe su detalle).
- Sin "Security Check"/Turnstile/`Additional Verification Required`. No apareció CAPTCHA en ningún momento.
- DOM: 16 `div.cardOutline` / 16 `a[data-jk]`; JSON embebido `mosaic-data` con **15 resultados** (el jk extra `cdef0123456789ab` es un marcador de plantilla que no está en el JSON, igual que en la investigación del 11-10).
- Captura: `.opencode/.agent-screenshots/20261018-171548-indeed-ie-serp-autoopen-panel.png`.

## 2. Marcadores del DOM (eval en vivo)

```json
{"cards":16,"jk":16,"mosaicScript":true,"autoOpenMarker":true,"jobInfoWrapperModel":true,
 "sanitizedJobDescription":true,"vjc":1,"vjTitle":1,"structuredSummary":1,
 "oldJobDescText":0,"oldTestidDesc":0,"title":"105 Data jobs, ... | Indeed"}
```

- El layout actual del panel sigue siendo `vj-*`/`viewjob-*`: `[data-testid=viewjob-job-content]`=1, `[data-testid=vj-job-title]`=1, `[data-testid=structured-job-summary]`=1.
- Selectores del layout antiguo (`#jobDescriptionText`, `[data-testid=jobDescriptionText]`): **0**. El parser actual ya apunta a los selectores nuevos (51/52 clics con éxito en el run).

## 3. JSON auto-open embebido (descripción del job auto-abierto)

- `autoOpenTwoPaneViewjobResponse` presente; wrapper `["status","body"]`.
- `body.jobKey = f0fa7506b1b51b1a`; `body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription` = **4.336 chars** de HTML.
- `hasLegacyPath` (`hostQueryExecutionResult`) = **false**: la ruta antigua no está; la ruta nueva del parser (T-32, iteración 3) es la vigente.
- Claves relevantes del `body` (muestra): `jobInfoWrapperModel`, `salaryInfoModel`, `benefitsModel`, `jobDuties`, `jobOccupations`, `structuredDataJobSummaryModel`, `viewJobDisplay`, `jobKey`, `jobTitle`, `jobLocation`… **no hay ninguna clave de skills**.
- `salaryInfoModel` del job auto-abierto: `{currency: EUR, salaryMin: 74000, salaryMax: 75500, salaryText: "€74,000–€75,500 a year", salaryType: "YEARLY", salarySource: "EXTRACTION"}` (solo para el job auto-abierto; la pestaña del panel trae su propio `salaryInfo`).

## 4. Tarjetas (`mosaic-provider-jobcards`): salary / work_mode / jobTypes

- 15 items; campos confirmados por item: `jobkey`, `displayTitle`, `company`, `formattedLocation`, `salarySnippet.text`, `extractedSalary{min,max,type}`, `remoteWorkModel{type,text}`, `jobTypes[]`, `snippet` (HTML, presente en esta variante), `translatedAttributes[]` (**vacío en 15/15**).
- Salario: 6/15 items publican `salarySnippet` (€74k–75.5k/year, €25–28/hour, €50k–60k/year, €31,934–49,416/year, €100k–120k/year, €90k–115k/year). El resto: sin salario en el portal.
- `remoteWorkModel` en 3/15: `type="REMOTE_HYBRID"`, `text="Hybrid work"` (Power BI Consultant, Data Protection Analyst, Pharmacovigilance Administrator). Las demás no traen marca de modalidad.
- `jobTypes`: `["Contract"]`, `["Permanent"]`, `["Full-time","Permanent"]`… con valores reales disponibles (el scraper hoy los ignora y usa `translatedAttributes`, que viene vacío → `job_type` quedó 0/116).
- **skills: no existe campo ni clave** (`itemKeysSample` revisado; tampoco en el body auto-open ni en la query `Viewjob` del panel; la única aparición de "skills" es en un fragmento de analíticas de candidatos para usuarios logueados, no en el job).

## 5. Panel derecho tras clic (reproducción del flujo del scraper)

Clic 1 — `a[data-jk=197ba927db2dfa13]` (Data Protection Analyst, Hays Recruitment, híbrida, con salario):

- `t0=17:20:00.5` → clic; a `T+3s` el panel ya mostraba el job clicado (sin esperas de 8 s).
- `viewjob-job-content` (13.411 chars de HTML) texto: `Job details €50,000–€60,000 a year Fixed term Hybrid work in Dublin, County Dublin Full job description Data Protection Analyst Location: Dublin (Hybrid - 2 days onsite per week) Contract: 12-15 Month Contract Rate: €241.45 Daily Rate …`.
- `structured-job-summary`: `Job details €50,000–€60,000 a year Fixed term Hybrid work in Dublin, County Dublin`.

Clic 2 — `a[data-jk=80073c4e437994fb]` (Power BI Consultant, Version 1, híbrida, **sin salario en la tarjeta**):

- `t0=17:22:54.3` → clic; a `T+3s` el panel mostraba el nuevo job.
- Texto del panel: `Job details Pay information not provided Employee discount, Gym membership, Employee assistance program, Bike to work scheme Full-time Hybrid work in Millennium House, Millennium Walkway, Dublin, County Dublin Full job description …`.
- **"Pay information not provided"** = el portal confirma que el salario no existe (no es un fallo de extracción). Además el panel publica beneficios y `Full-time` (fuentes de `benefits`/`job_type`).
- Captura: `.opencode/.agent-screenshots/20261018-172331-indeed-ie-panel-pay-not-provided.png`.

Dato operativo: en el perfil fresco de agent-browser el banner de cookies OneTrust (`#onetrust-group-container`) cubría las tarjetas y el primer clic falló con "Element is covered". Se aceptó con `#onetrust-accept-btn-handler` y los clics funcionaron. En el perfil persistente del nightly el consentimiento ya está guardado (51/52 clics OK), pero un clic que falle por overlay es un caso a cubrir con reintento.

## 6. Red del clic (hecho)

- `POST https://apis.indeed.com/graphql` con `operationName: "Viewjob"` disparado **por la propia página** tras el clic; dentro del payload, `detailsInput.viewjobUrl` incluye `https://ie.indeed.com/viewjob?jk=197ba927db2dfa13&from=vjs&…&viewtype=embedded&…`.
- También `POST apis.indeed.com/graphql` con `SaveJobButton`/`ApplyLink`.
- **No** se observó ninguna petición GET directa a `/viewjob` desde el scraper (no la hicimos): el contenido del panel llega por el POST de la página a `apis.indeed.com` (host distinto).
- No se capturaron cookies/tokens en esta evidencia; los headers con `ctk`/api key no se copian.

## 7. Campos de la query `Viewjob` (para la auditoría)

- `job.compensation { formattedText, baseSalary.range {min,max} }`, `details.remoteWorkModel {text,type}`, `details.jobTypeAndShiftSchedule { jobTypes, shiftAndSchedule }`, `details.benefit.benefits[]`, `details.salaryInfo {min,max}`, `job.attributes[]`.
- **No hay campo de skills del puesto** (solo `skills` dentro de `ApplicantDemographicsFields`, que es analítica de candidatos con sesión iniciada; no aplica).
