# Plan técnico: reparación de scrapers caídos

## 1. Objetivo y límites

Construir un proceso que, a partir del diagnóstico diario (spec 001), convierta
cada fuente fallida en una reparación probada y con la **máxima información y
calidad obtenibles**: investigar la web/API real, identificar la causa, reparar
el scraper, pasar los tests y demostrar en vivo que vuelven a obtenerse ofertas
por encima del umbral incremental **sin regresión de campos**. Cada reparación
vive en su rama `repair/<fuente>-<fecha>` y deja un registro.

**Límites.** El proceso no sube datos a Azure, no modifica la landing, no
modifica el código ni el contrato del diagnóstico (su salida se regenera en el
paso 0) ni sus umbrales, y no implementa resolución automática de CAPTCHAs: se
diseña para no recibirlos. La única ejecución de
scrapers permitida es la prueba en vivo acotada de RF-9. El push de una rama de
fix solo ocurre tras la validación de la persona (RF-12). Nada de `specs/` se
modifica durante una reparación (RF-15).

**Calidad como parte del resultado.** "Reparado" no es solo "vuelven a salir
ofertas": los campos obligatorios (`title`, `company`, `description`) deben
quedar al 100 % en la muestra del alcance de prueba, los campos ya cubiertos no
pueden caer (tolerancia 5 puntos porcentuales, `QUALITY_REGRESSION_TOLERANCE_PP`)
y cada campo objetivo debe mejorar hasta lo que la web/API permita, con
evidencia. La medición reutiliza el módulo `verification.completeness` de la
001; no se inventan métricas nuevas. Los campos opcionales que el portal no
publica (salario, skills) no son un fallo por su ausencia: solo penalizan si el
scraper los extraía y deja de hacerlo. La `description` completa es la
prioridad, porque Databricks deriva las skills de ella.

## 2. Punto de partida: el run real

Esta fotografía es la del run vigente al escribir el plan y sirve de ejemplo;
**no es una lista cerrada**: el proceso recalcula los objetivos con el último
diagnóstico en cada ejecución, incluidas las fuentes que se añadan después.

### 2.1 Paso 0 — refrescar el diagnóstico antes de seleccionar

`scrapers-pipeline/logs/diagnostic_last.json` puede quedar desfasado: el
ejecutable se lanza a mano y el pipeline nocturno no lo regenera. Antes de
seleccionar objetivos, el orquestador ejecuta desde `scrapers-pipeline/`:

```
python -m verification.verify_run --offline
```

(Escritura atómica en `logs/diagnostic_last.json`; el modo `--offline` omite la
landing, que no forma parte de la reparación). El refresco no modifica la 001:
solo regenera su salida sobre el último run terminado. Si el diagnóstico
resultante siguiera siendo más antiguo que el último `upload-*.log`, `targets`
lo avisa (INFO) para que la persona decida si hay un run incompleto.

### 2.2 Fuentes del run de referencia (2026-10-03, `diagnostic_last.json`)

| Fuente | kind | status | outcome | ofertas | Fallo o brecha principal |
|---|---|---|---|---|---|
| `indeed` | direct | failed | ok | 57 | `description` 0 % (obligatorio) |
| `linkedin` | direct | failed | ok | 1590 (snapshot 8387) | `description` 88,4 % (~970 filas históricas) |
| `infojobs` | direct | failed | blocked | 0 | CAPTCHA al abrir (Distil) |
| `irishjobs` | multi_site | failed | error | — | `exit=75` watchdog sin progreso |
| `glassdoor` | multi_site | failed | error | — | `exit=75` watchdog sin progreso |
| `stepstone_nl` | multi_site | ok | ok | 3 | salary/skills/work_mode 0 % (secundaria) |
| `devitjobs` | multi_site | ok | ok | 29 | sin brechas |
| `nvb` | multi_site | ok | ok | 444 | work_mode 0 % (55 inválidos) |
| `jobs_ch` | multi_site | ok | ok | 104 | salary 40,4 %, skills 32,7 %, work_mode 11,5 % |

Investigaciones secundarias del JSON (fuente, campo):
`indeed` (salary, skills, work_mode), `linkedin` (salary, work_mode),
`stepstone_nl` (salary, skills, work_mode), `nvb` (work_mode),
`jobs_ch` (salary, skills, work_mode).

### 2.3 Evidencia de partida (rutas locales; `logs/`, `output/` y `data/` están en `.gitignore`)

| Fuente | Evidencia principal |
|---|---|
| indeed | `indeed_jobs_scraper/output/scrape_metadata_20261003_0006.json` (`enrich_success_rate: 0/53`) y `..._1247.json` (`0/72`), `output/nightly_stderr_IE_attempt1.log`, `output/log_20261003_*.log` |
| linkedin | `linkedin_jobs_scraper/data/nightly_stdout_attempt1.log`, `data/output/jobs.parquet`, `data/run.log` |
| infojobs | `infojobs_jobs_scraper/data/run_nightly.log` (`blocked=True` desde 2026-09-18), `data/nightly_stdout_attempt1.log` (CAPTCHA), `data/logs/run_20261003_*.log` |
| multi-site | `multi_site_job_scraper/data/merged/last_run.json` (`watchdog_blocked`, `watchdog_no_progress`), `data/irishjobs/run.log`, `data/glassdoor/run.log` |
| diagnóstico | `scrapers-pipeline/logs/diagnostic_last.json` (refrescado), `scrapers-pipeline/logs/upload-2026-10-03.log` |

Los registros (`repairs/`) versionan **extractos saneados**; los artefactos
pesados (parquet, logs completos, capturas) quedan fuera de git y se referencian
por ruta.

### 2.4 Hipótesis iniciales (hechos observados vs. hipótesis)

El `web-inspector` debe confirmar o refutar cada hipótesis con evidencia; el plan
no las da por ciertas.

| Fuente | Hecho observado | Hipótesis a comprobar | Código señalado |
|---|---|---|---|
| indeed | `enrich_success 0/53–0/72`; el `wait_for_selector` del panel derecho expira y hace `continue` antes del fallback `/viewjob`; página 2 redirige a login | Selectores del panel desactualizados o clic sin efecto; el fallback es inalcanzable; `--limit` provoca Cloudflare | `parser.py:427-634` (fallback en `561-589`, `continue` en `527-536`), `runner.py:186-199` |
| linkedin | ~970 filas sin `description` con `scraped_at` ≤ 17-09-2026; cobertura local ~100 % desde 18-09; salary 16,3 % (regex), work_mode 42,4 % | Es un artefacto del snapshot histórico, no del código actual; el detalle SDUI podría traer salary/work_mode estructurados | `parse_detail.py:148-173`, `parse_serp.py:58-67`, `store.py` |
| infojobs | `blocked=True` continuo desde 18-09; los 4 marcadores de CAPTCHA no registran cuál dispara; `robots.txt` bloquea detalle, permite SERP | Challenge de reputación (IP/perfil), no de parser; puede evitarse con sesión/cookies reales, cabeceras y ritmo, o con un endpoint interno permitido | `navigator.py:23-91`, `config.py:64-75` |
| irishjobs | 0 tarjetas en todos los combos, ninguna línea `PROGRESS`, watchdog 75; `stepstone_nl` usa la misma clase y funciona | Bloqueo anti-bot o cambio de HTML específico de IE sin detección ni volcado | `src/irishjobs/serp.py:63-373`, `src/irishjobs/scraper.py:91-172` |
| glassdoor | El BFF devuelve 30 ofertas; el bucle de descripciones (hasta 30 × 60 s) no emite `PROGRESS`; watchdog 75 | El watchdog mata un trabajo que avanza: falta latido de progreso y/o sobra presupuesto de descripciones | `src/glassdoor/scraper.py:344-382`, `bff.py:567-682` |

### 2.5 Brecha de calidad a cerrar

- Obligatorios sin cubrir: `description` en `indeed` (0 %) y las filas históricas
  de `linkedin` (agregado 88,4 %).
- Opcionales muy bajos o en 0 %: `salary` (indeed 35,1 %, linkedin 16,3 %,
  stepstone_nl 0 %, jobs_ch 40,4 %), `skills` (indeed no existe el campo,
  stepstone_nl 0 %, jobs_ch 32,7 %), `work_mode` (indeed 17,5 %, linkedin 42,4 %,
  stepstone_nl 0 %, nvb 0 %, jobs_ch 11,5 %).
- La auditoría de cobertura de campos del `web-inspector` (§8) decide qué es
  extraíble de la web/API y con qué base legal; lo no disponible se documenta y
  no se promete.

## 3. Piezas del proceso

### 3.1 Núcleo determinista (Python)

La lógica que decide **qué** se repara, **cuándo** una prueba es suficiente y
**cuánta** calidad se ha ganado es determinista y testeable; los agentes hacen
la investigación y el cambio de código. El paquete vivirá en
`scrapers-pipeline/repair/`, hermano de `verification/`, con tests en
`scrapers-pipeline/tests/`.

| Módulo | Responsabilidad | RF |
|---|---|---|
| `repair/targets.py` | Leer y validar el diagnóstico, avisar de desfase, extraer fuentes fallidas, objetivos secundarios y perfil de calidad por campo. Funciona con cualquier `id` del diagnóstico, incluidas fuentes añadidas después de esta spec. | RF-1 |
| `repair/brief.py` | Construir el contexto de un objetivo (JSON en inglés) para el especialista y el implementador, con evidencia, playbook y metas de calidad. | RF-1, RF-3 |
| `repair/threshold.py` | Regla pura del umbral incremental y lectura/actualización de `repairs/history.json`. | RF-10 |
| `repair/quality.py` | Perfil de calidad y delta: mide el parquet de la prueba en vivo con `verification.completeness`, lo compara con el diagnóstico y decide si hay regresión u objetivo incumplido. | RF-7, RF-8, RF-11, RF-16 |
| `repair/records.py` | Crear y completar `repairs/<fecha>-<fuente>/plan.md`, actualizar el índice y el historial. | RF-2, RF-11 |
| `repair/cli.py` | CLI delgada (`python -m repair.cli targets\|brief\|threshold\|quality\|record`) que acepta la ruta del diagnóstico. | RF-13 |

La CLI se ejecuta desde `scrapers-pipeline/`, como `verification.verify_run`. La
cola de grupos `targets` se muestra en español para la persona; `brief` y el
contenido de `history.json` se serializan en inglés.

### 3.2 Agentes

| Pieza | Papel |
|---|---|
| Orquestador (ya existe) | Refresca el diagnóstico, ejecuta el flujo de §5, elige la skill adecuada (§7), abre rama y registro, y pide la validación del push. |
| `web-inspector` (nuevo subagente) | Investiga la fuente real y devuelve el informe de §8 (incluida la auditoría de campos y la meta de calidad). Escribe solo evidencias bajo el registro. |
| Implementador (ya existe) | Repara el scraper, añade tests, ejecuta la prueba en vivo acotada y mide la calidad. |
| Verificador (ya existe) | Revisa el diff y ejecuta las suites; no usa red. |

`web-inspector` se define en `.opencode/agent/web-inspector.md` con
`mode: subagent`, `edit: deny` y `bash: allow`. No modifica código ni lanza
scrapers completos; las capturas y extractos que produce se copian a
`repairs/<fecha>-<fuente>/evidence/` (y a `.opencode/.agent-screenshots/`, que
está fuera de git).

### 3.3 Skills (versionadas en `.opencode/skills/`)

| Skill | Uso |
|---|---|
| `scraping-expert` | Doctrina de diagnóstico: API JSON interna, anti-bot, escalera de herramientas, selectores, taxonomía de errores, `robots.txt`/TOS. Se carga al abrir cada investigación. |
| `browser-cdp` | Arranca Chrome con el perfil real del usuario (sesión iniciada) en el puerto 9222. Aviso: cierra las ventanas de Chrome. |
| `agent-browser` | Automatiza el navegador: snapshots, clics, red, consola, cookies, evaluaciones y capturas. Contra el Chrome de `browser-cdp` se usa `agent-browser --cdp 9222`. |

La CLI `agent-browser` (global) y su Chrome quedan instalados en la máquina; la
skill `browser-cdp` solo necesita Node y Chrome. Ni el proceso ni los scrapers
añaden dependencias de pago.

### 3.4 Registros y ramas

```
repairs/
  README.md              # índice: fecha, fuente, estado, rama, resultado, calidad
  history.json           # historial de recuentos verificados por fuente (inglés)
  20261003-infojobs/
    plan.md              # plan + resultado (español)
    context.json         # brief generado (inglés)
    evidence/            # capturas, recortes de log, respuestas y resúmenes saneados
    quality_before.json  # perfil de calidad del diagnóstico (inglés)
    quality_after.json   # medición del test en vivo y delta (inglés)
```

- Rama por fix: `repair/<fuente>-<YYYYMMDD>`, creada desde `main`; durante el
  primer caso real (cierre de la 004) se crea desde la rama de la spec 004,
  porque el proceso vive en ella. Las reparaciones posteriores (§12 paso 10) se
  ramifican desde `main` con el proceso ya fusionado.
- El registro se crea al abrir la rama (`planificado`) y se completa al
  terminar. Se versiona en la rama del fix y llega a `main` con su merge.
- La plantilla incluye una sección de **calidad** con la tabla antes/después por
  campo, la meta declarada y el veredicto de `quality.py`.

## 4. Contrato con el diagnóstico de la 001

- Entrada: `scrapers-pipeline/logs/diagnostic_last.json` (`schema_version: 1`),
  con `run`, `global_status`, `sources[]` (`id`, `kind`, `status`, `outcome`,
  `offers_current_run`, `offers_snapshot`, `completeness[]`, `publication`,
  `failures`, `evidence`), `trend` e `investigations[]`.
- **Refresco (paso 0, §2.1).** El proceso regenera el diagnóstico antes de
  seleccionar. `targets` avisa si sigue siendo más antiguo que el último
  `upload-*.log`.
- El diagnóstico se consume en modo lectura: la 004 no toca su código ni su
  contrato; el paso 0 regenera su salida con el CLI de la 001. Si el
  `schema_version` no es el esperado, el proceso se detiene con un mensaje claro
  en vez de interpretar mal los datos.
- Objetivos primarios: fuentes con `status: failed`. Objetivos secundarios:
  `investigations[]` (`required_field_below_target` y
  `optional_field_at_or_below_threshold`), priorizados después.
- `global_status` distinto de `partial`/`failed` (p. ej. `inconclusive`) no
  produce objetivos (RF-1).
- Semántica que el brief debe respetar:
  - `outcome: blocked` = CAPTCHA/anti-bot; `error` = fallo técnico o exit ≠ 0.
  - En Multi-site, `exit=75` es el watchdog de progreso
    (`verification/supervisor.py`, `WATCHDOG_EXIT_CODE`), no un exit del portal;
    `evidence` detalla `watchdog_blocked`/`watchdog_no_progress`.
  - El snapshot de completitud puede ser **agregado histórico** (caso LinkedIn):
    el brief incluye ofertas del run, ofertas del snapshot y la antigüedad de
    las filas sin campo, para no confundir un fallo activo con datos viejos.
- El run de origen queda fijado en el registro; un diagnóstico nuevo durante la
  reparación no cambia el objetivo.
- Rutas con nombre de usuario se sanean en los fixtures de test (`<HOME>`), y el
  brief no incluye credenciales ni rutas de perfiles de navegador.

## 5. Flujo operativo por reparación

0. El orquestador refresca el diagnóstico (§2.1) y lanza la CLI `targets`; la
   persona elige o limita el alcance (fuente o lista priorizada).
1. El orquestador crea la rama `repair/<fuente>-<fecha>` y el registro con
   `records.py` (estado `planificado`), y genera el brief con `brief.py`.
2. El orquestador delega la investigación en `web-inspector` con el brief y las
   skills (§7); el informe incluye auditoría de campos y meta de calidad.
3. Con el informe, el orquestador da instrucciones al implementador: causa,
   cambio recomendado, alcance, metas de calidad y restricciones. El
   implementador repara, añade tests y actualiza el registro con la causa y los
   cambios. Toda dependencia nueva se justifica en el registro.
4. El orquestador delega la verificación en `verifier`: suites del scraper
   afectado y del diagnóstico, revisión del diff contra la spec 004, el playbook
   (§6) y el registro. Con bloqueantes, vuelve al paso 3.
5. El implementador ejecuta la prueba en vivo acotada (§9) y mide la calidad con
   `quality.py`; anota alcance, recuento, tabla de calidad y evidencia.
6. `threshold.py` y `quality.py` comprueban las dos puertas: recuento ≥ umbral y
   sin regresión + metas alcanzadas. Si alguna falla, se itera desde el paso 3
   dentro de la misma rama; si se agotan las opciones locales (bloqueo, TOS,
   servicios de pago), se **escala a la persona** con opciones y se registra.
7. Con el fix probado, se completa el registro, se actualiza el índice y el
   historial, y se presenta el resumen en español.
8. El orquestador pide a la persona la validación del push (RF-12). No hay push
   ni merge sin esa validación.

## 6. Playbooks por fuente

Cada playbook es la entrada operativa del objetivo. El brief lo referencia; el
`web-inspector` confirma o refuta sus hipótesis y propone la meta de calidad
final con evidencia. El orden recomendado de reparación es: InfoJobs (caso de
cierre de la spec), Indeed, LinkedIn, IrishJobs y Glassdoor; la persona puede
cambiar el orden o limitar el alcance. Los playbooks §6.1–§6.6 son las recetas
conocidas del run vigente; para cualquier otra fuente (nueva o caída en el
futuro) se usa el playbook genérico §6.7.

### 6.0 Política anti-bloqueos (aplica a todos los playbooks)

El objetivo es **no recibir el challenge**: un CAPTCHA visible significa que los
niveles anteriores de la escalera anti-bot fallaron. Orden de diseño:

1. **API JSON interna** siempre que exista y `robots.txt`/TOS lo permitan.
2. **Sesión y cookies reales**: perfil del usuario vía `browser-cdp`; nada de
   contextos limpios por petición; storage persistente.
3. **Replay de tokens de challenge** (p. ej. `cf_clearance`, `_abck`,
   `datadome`): el navegador real resuelve el challenge invisible una vez y el
   scraper reutiliza la cookie dentro de su vigencia, con la misma IP y UA.
4. **Fingerprint coherente**: patchright (ya usado) o Camoufox (open source, a
   justificar como dependencia); nunca parchear `navigator.webdriver` a mano.
5. **Humanización y ritmo**: delays gaussianos, scroll/hover, flujo
   homepage → búsqueda → detalle, banners de cookies, sesiones que persisten,
   ≤ 30 req/min/IP en objetivos sensibles y ejecución en horas valle.
6. **Challenges invisibles** (Turnstile, reCAPTCHA v3, proof-of-work): los
   resuelve el navegador real por sí solo; no requieren código extra.

Líneas rojas (no se implementan): resolución automática de CAPTCHAs visibles,
servicios de resolución, verificación de identidad, cuentas ajenas, paywalls y
patrones de decepción del verificador. Si pese al diseño aparece un CAPTCHA
visible, el scraper pausa y avisa a la persona (asistido) y continúa con esa
sesión; el informe lo registra como señal de que la configuración debe
endurecerse.

### 6.1 InfoJobs — bloqueo por CAPTCHA (caso de cierre)

- **Disparador:** `blocked`, `captcha block detected`, 0 ofertas.
- **Preguntas al inspector:** ¿qué marcador de `detect_captcha` dispara y qué
  HTML/challenge llega? ¿La SERP responde igual con el perfil real del usuario
  (`browser-cdp` + `agent-browser --cdp 9222`)? ¿Existe un endpoint JSON interno
  de búsqueda y `robots.txt` lo permite? ¿Cambia con cabeceras/ritmo humano?
- **Líneas rojas:** no resolver CAPTCHAs de forma automática, no verificación de
  identidad, no servicios de pago; si solo hay vía incompatible con
  `robots.txt`/TOS, parar y consultar (RF-6).
- **Cambio probable (§6.0):** perfil real vía `browser-cdp`, reutilización de
  cookies/clearance tokens, cabeceras del perfil, flujo y ritmo humanos; registro
  del **motivo** del bloqueo; pausa asistida para la persona como contingencia.
  Rediseño permitido si mejora la calidad y evita el challenge.
- **Meta de calidad:** ≥ 1 oferta; obligatorios al 100 % en la muestra según el
  contrato de la fuente (la descripción completa solo si la vía permitida la
  ofrece: el detalle está bloqueado por `robots.txt`, así que un snippet cuenta
  como `description` y se documenta).
- **Alcance de prueba:** 1 keyword × 1 ciudad × 1 página; sin Azure, landing ni
  merge.

### 6.2 Indeed — descripción 0 % y segunda página

- **Hechos:** `enrich_success 0/72`; `continue` tras el timeout del panel antes
  del fallback `/viewjob`; página 2 exige login y se omite; `--limit` dispara
  Cloudflare según la skill.
- **Preguntas al inspector:** ¿el clic abre el panel derecho con el layout real?
  ¿Qué devuelve `/viewjob?jk=` con el perfil real? ¿La página 2 siempre exige
  login o depende del país? ¿`mosaic-data` trae la descripción en otra clave?
- **Cambio probable:** reordenar el fallback para que `/viewjob` sea alcanzable,
  actualizar selectores, emitir progreso durante el enriquecimiento y resolver o
  documentar el login de la página 2 y el conflicto de `--limit`.
- **Meta de calidad:** `description` completa en el 100 % del alcance (base de
  las skills que Databricks deriva de ella); sin regresión; `salary` y
  `work_mode` se mejoran solo si el sitio los expone; `skills` no se exige como
  campo del scraper.
- **Alcance de prueba:** 1 país (NL o IE) × 1 término × 1 página.

### 6.3 LinkedIn — descripción histórica y campos opcionales

- **Hechos:** ~970 filas sin `description` con fecha ≤ 17-09-2026; cobertura
  local ~100 % desde el 18-09; `salary` por regex (16,3 %, 68 inválidos);
  `work_mode` 42,4 %.
- **Preguntas al inspector:** ¿el detalle SDUI o la API guest exponen
  salario/workplace que no se parsean? ¿El store reintenta filas sin descripción
  en cada run? ¿Cuál es la vía menos costosa para completar el histórico?
- **Cambio probable:** backfill de descripciones del histórico (o justificación
  de por qué no procede) y extracción estructurada de `salary`/`work_mode` si la
  web la ofrece.
- **Meta de calidad:** descripción agregada al 100 %; `salary` y `work_mode`
  según lo observado; sin regresión.
- **Alcance de prueba:** 1 rol × 1 ciudad en modo guest (y/o auth); incluye la
  medición del store tras el backfill.

### 6.4 IrishJobs — 0 tarjetas y watchdog 75

- **Hechos:** 0 tarjetas en todos los combos, ningún `PROGRESS`, exit 75;
  `stepstone_nl` comparte clase y funciona.
- **Preguntas al inspector:** ¿qué devuelve la SERP de IE en navegador real
  (challenge, HTML distinto, geo/rate)? ¿Existe API interna?
- **Cambio probable:** actualizar selectores/parser, detectar el bloqueo con
  motivo y emitir `PROGRESS` por página (0 tarjetas también es avance) para no
  morir por watchdog.
- **Meta de calidad:** ≥ 1 oferta; campos de tarjeta/detalle según lo expuesto.
- **Alcance de prueba:** `python -m src.irishjobs --max-pages 1` con 1 rol; sin
  merge.

### 6.5 Glassdoor — watchdog por descripciones

- **Hechos:** el BFF procesa 30 ofertas; hasta 30 descripciones de 60 s sin
  `PROGRESS`; exit 75; sesión real disponible.
- **Preguntas al inspector:** ¿cuánto tarda una página con la sesión del usuario?
  ¿Se pueden pedir descripciones en lote o bajo demanda? ¿Qué presupuesto de
  descripciones mantiene la calidad?
- **Cambio probable:** emitir `PROGRESS`/latido dentro del bucle, limitar
  `max_description_jobs` o el timeout por descripción, sin perder cobertura.
- **Meta de calidad:** ≥ 1 oferta sin exit 75; descripción según presupuesto
  declarado; sin regresión.
- **Alcance de prueba:** `python -m src.glassdoor --max-pages 1` con 1 rol;
  vigilar tiempo y `PROGRESS`; sin merge.

### 6.6 Objetivos secundarios de completitud (opcionales)

| Fuente | Campo | Situación | Vía de mejora probable |
|---|---|---|---|
| `stepstone_nl` | salary, skills, work_mode (0 %) | Detalle JSON-LD/DOM | Extraer salario/modalidad del detalle; `skills` solo si el portal los expone |
| `nvb` | work_mode (0 %, 55 inválidos) | API con `workingPlace`/`contractType` | Corregir mapeo/validación del campo contra la API |
| `jobs_ch` | salary 40,4 %, skills 32,7 %, work_mode 11,5 % | Detalle JSON-LD y cabeceras | Ampliar extracción del detalle |
| `indeed` | salary, skills, work_mode | Ver §6.2 | Auditoría de campos decide |
| `linkedin` | salary, work_mode | Ver §6.3 | Detalle estructurado |

Los secundarios son investigaciones del diagnóstico (RF-1). Su reparación sigue
el mismo flujo, con prueba en vivo sobre una muestra representativa y la misma
puerta de calidad. La ausencia de un campo opcional que el portal no publica no
es fallo: si el sitio no despliega salario o skills, la prioridad es la
`description` completa (las skills se derivan en Databricks).

### 6.7 Playbook genérico para fuentes nuevas o fallos futuros

Cualquier `id` que aparezca en `sources[]` o `investigations[]` es reparable sin
tocar la spec: el brief se genera con lo que traiga el diagnóstico y el
`web-inspector` aplica §6.0 y §7 sin recetas previas. Plantilla:

- **Disparador:** `status: failed` u objetivo secundario, con su `outcome`,
  ofertas, `failures` y `evidence` (run de origen fijado).
- **Evidencia de partida:** rutas resolubles del propio diagnóstico
  (`evidence`), logs del scraper y snapshot.
- **Preguntas al inspector:** ¿API JSON interna? ¿anti-bot (vendor)? ¿contenido
  servidor o cliente? ¿sesión/cookies necesarias? ¿`robots.txt`/TOS? ¿por qué
  no llegan ofertas o falta el campo? (misma auditoría de campos de §8).
- **Cambio recomendado:** el que se derive del informe, con el nivel de la
  escalera §6.0 que evite el challenge.
- **Meta de calidad:** la que permita la web/API, nunca por debajo de la
  cobertura actual.
- **Alcance de prueba:** la configuración mínima representativa de esa fuente,
  documentada y repetible; en Multi-site, solo el portal afectado.
- **Umbral:** 1 oferta la primera vez y `mayor verificado + 1` después
  (`history.json`).

## 7. Cuándo usar cada skill (guía para el orquestador)

Regla de orden: primero `scraping-expert` decide **qué** se va a cambiar;
después las skills de navegador permiten **ver y evidenciar**; el implementador
cambia; el verificador comprueba; la prueba en vivo la hace el implementador.

| Situación | Skill y receta |
|---|---|
| Estrategia: API interna, anti-bot, ruta de menor coste, límites legales | `scraping-expert` (`diagnostic.md`, `hidden-apis.md`, `legal-ethics.md`) al abrir cada investigación |
| Diseñar para que el CAPTCHA no se dispare | `scraping-expert` (`anti-bot-strategies.md`) y aplicar §6.0: sesión/tokens reales, fingerprint coherente, ritmo y flujo humanos |
| Reproducir el fallo: DOM, red, consola, cookies, capturas | `agent-browser open <url> --headed`; `snapshot -i`; `network requests --json`; `console --json`; `errors --json`; `screenshot .opencode/.agent-screenshots/YYYYMMDD-HHMMSS-descripcion.png` |
| La fuente exige la sesión del usuario o su fingerprint (LinkedIn, Glassdoor, InfoJobs) | `node .opencode/skills/browser-cdp/scripts/setup-cdp-chrome.js` (aviso: cierra Chrome; `--dry-run` antes) y `agent-browser --cdp 9222 <comando>` |
| Descubrir la API interna | `scraping-expert` (`hidden-apis`) + `agent-browser network requests --json --filter ""` |
| Comprobar `robots.txt`/TOS y límites de la reparación | `scraping-expert` (`legal-ethics`) + abrir `robots.txt` con `agent-browser` |
| Guardar evidencia en el registro | captura/recorte y copia saneada a `repairs/<fecha>-<fuente>/evidence/`; nunca perfiles ni credenciales |

**Reglas de higiene:** peticiones acotadas y pocas (esto no es recolección en
volumen); `agent-browser close` borra cookies, no cerrar si hay sesión; no
resolver CAPTCHAs de forma automática (diseño preventivo y pausa asistida); no
usar servicios de pago; no tocar acciones de cuenta.

## 8. Contrato del subagente `web-inspector`

**Entrada:** brief del objetivo (fuente, run de origen, fallo y evidencia,
playbook, alcance de prueba propuesto y metas de calidad iniciales) más las
rutas de logs/parsers relevantes.

**Salida (informe en español):**

- Hechos observados, separados de hipótesis.
- Evidencias: rutas de capturas, recortes de red/respuestas y URLs.
- Causa probable y su fundamento.
- **Auditoría de cobertura de campos:** tabla `campo | ¿disponible en web/API? |
  ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal`.
- **Meta de calidad propuesta:** por campo y con lo que la web/API permite,
  nunca por debajo de la cobertura actual.
- Cambio recomendado (módulo/ficheros y qué hacer).
- Comprobación manual propuesta.
- Riesgos y límites: bloqueo, `robots.txt`/TOS, credenciales usadas.

**Restricciones:** no edita código; solo escribe evidencias bajo
`repairs/<fecha>-<fuente>/evidence/` y `.opencode/.agent-screenshots/`; no
ejecuta scrapers completos; no resuelve CAPTCHAs; no usa servicios de pago; no
deja credenciales en el informe. Comprueba `robots.txt`/TOS antes de proponer
cambios y para si la única vía incumple (RF-6).

## 9. Prueba en vivo, umbral y puerta de calidad

**Alcance de la prueba.** La configuración más pequeña representativa de la
fuente que produzca un recuento comparable entre reparaciones (por ejemplo, una
búsqueda/región). El alcance se documenta en el registro y se repite igual en
las comparaciones. En Multi-site se ejecuta solo el portal afectado y **no** el
merge.

**Límites.** Sin subida a Azure, sin landing, sin merge; pocos intentos por
reparación (los de la propia iteración del fix); se ejecuta cuando la persona
lance el proceso. No se repite por rutina: la ejecución del proceso es el
disparador (RF-9).

**Evidencia.** Recuento de ofertas parseadas (los contadores de progreso que ya
existen), log recortado, fecha/hora y alcance, guardados en `evidence/`; más el
resumen de calidad (`quality_after.json`).

**Umbral (RF-10).** `umbral(fuente) = max(1, best_verified_offers(fuente) + 1)`,
donde `best_verified_offers` es el mayor recuento obtenido en pruebas en vivo
**dadas por buenas** en reparaciones anteriores de esa fuente, conservado en
`repairs/history.json`. El historial nunca decrece. Sin historial, el umbral es
1.

**Puerta de calidad (RF-16).** `quality.py` mide el parquet de la prueba en
vivo y exige:

1. obligatorios (`title`, `company`, `description`) al 100 % en la muestra;
2. sin regresión: ningún campo ya cubierto cae más de
   `QUALITY_REGRESSION_TOLERANCE_PP = 5` puntos porcentuales respecto al
   perfil del run de origen;
3. metas declaradas: los campos objetivo alcanzan la meta fijada en el brief
   con la auditoría del inspector, solo cuando la web/API los expone.

La ausencia de campos opcionales que el portal no publica (p. ej. `salary` o
`skills`) no se considera fallo; solo cuenta si el scraper los extraía y deja de
hacerlo. La `description` completa es prioritaria aunque no exista `skills`,
porque las skills se derivan de la descripción en Databricks; se mide según el
contrato de cada fuente (cuenta la mejor descripción que la vía permitida pueda
almacenar: completa cuando el portal la exponga, o el texto que mida el contrato
de la 001 si el detalle está bloqueado). Si el recuento o
la calidad no alcanzan, la reparación no está probada y se itera dentro de la
misma rama; si se agotan las opciones locales, se escala a la persona con
opciones (proxies, servicios) sin aplicarlas.

## 10. Estrategia de tests

### Unitarios offline (`scrapers-pipeline/tests/test_repair_*.py`)

- `targets`: diagnóstico real saneado (copia del 2026-10-03), fallo simple,
  parcial, correcto, inconcluso, sin fichero, `schema_version` desconocido,
  desfase frente a `upload-*.log`, `blocked`/`error`/`empty`, portales Multi-site
  (incluido `exit=75` con `watchdog_no_progress`) y objetivos secundarios.
- `brief`: estructura, claves en inglés, evidencia y playbook referenciados,
  metas de calidad, sin credenciales, priorización.
- `threshold`: sin historial → 1; historial con 2 → 3; nunca decrece; fuentes
  independientes; historial corrupto o ausente.
- `quality`: sin regresión, regresión > 5 pp, obligatorio < 100 %, meta
  alcanzada/incumplida, opcional ausente por el portal que no bloquea, opcional
  degradado que sí bloquea, muestra vacía o ilegible; reutiliza
  `verification.completeness` con parquet sintético.
- `records`: creación de `plan.md` desde plantilla, actualización del índice y
  del historial, calidad antes/después, estados (`planificado`, `probado`,
  `descartado`, `escalado`).
- `cli`: subcomandos, ruta `--diagnostic`, códigos de salida y mensajes.

### Integración offline

Un fixture con el diagnóstico del 2026-10-03 (saneado) recorre
`targets → brief → registro → umbral → calidad` sin red y comprueba que las
cinco fuentes fallidas quedan detectadas con su evidencia, que las secundarias
se priorizan después y que las reparaciones quedan aisladas por fuente. Un test
añade una fuente desconocida al fixture y comprueba que también produce
objetivo, brief y registro (proceso genérico, sin listas fijas).

### Regresión

- `python -m pytest scrapers-pipeline/tests -q` después de cada tarea.
- Suites del scraper modificado: Indeed, LinkedIn, InfoJobs y Multi-site según
  el caso (`python -m pytest tests -q` desde la carpeta del scraper; InfoJobs
  además Ruff y mypy).

### Comprobación real

El primer caso (InfoJobs) se repara en su rama, con prueba en vivo, puerta de
calidad y validación de la persona. El resultado se registra en `repairs/` y
cierra la spec (junto a la suite en verde). La prueba en vivo no se ejecuta
dentro de pytest.

## 11. Decisiones técnicas y alternativas descartadas

1. **Núcleo determinista mínimo + agentes para investigación y código.**
   **Descartado:** CLI puramente determinista (no puede investigar) y flujo
   100 % libre del modelo (decisiones no reproducibles).
2. **Refrescar el diagnóstico antes de seleccionar.** El JSON puede quedar
   desfasado (así está hoy). **Descartado:** confiar en `diagnostic_last.json`
   sin comprobar el último run terminado.
3. **La calidad se mide con el módulo de la 001.** `quality.py` reutiliza
   `verification.completeness`; un umbral de ofertas no garantiza información.
   **Descartado:** métricas nuevas o umbrales de calidad inventados.
4. **Los registros viven en el repositorio, con historial legible por máquina.**
   **Descartado:** almacén externo en Azure para esta historia.
5. **Umbral incremental simple sobre pruebas de reparación.** Mismo método,
   mismo alcance; sin estadística. **Descartado:** umbral fijo y umbral por
   tendencia.
6. **La prueba en vivo la ejecuta el implementador.** **Descartado:** dar red al
   verificador o automatizar la prueba en pytest.
7. **Primer fix real desde la rama de la spec 004**, con el run 2026-10-03 como
   referencia. Las reparaciones posteriores se ramifican desde `main` con el
   proceso ya fusionado. **Descartado:** fusionar la 004 sin ninguna reparación
   real.
8. **Sin resolución automática de CAPTCHAs ni servicios de pago.** El scraper se
   diseña para no disparar el challenge (§6.0); si aparece uno visible, se pausa
   y lo resuelve la persona. **Descartado:** proxies de pago, solvers, APIs
   gestionadas y cualquier elusión de verificación de identidad.
9. **Multi-site por portal.** Cada portal es un objetivo independiente.
   **Descartado:** tratar Multi-site como una sola fuente.
10. **Skills, agentes y comandos versionados en `.opencode/`** (solo
    dependencias locales y capturas de investigación quedan fuera). El proceso
    documenta su instalación.
11. **El `web-inspector` no edita código, pero sí escribe evidencias.** Un
    informe sin capturas en el registro no es verificable. **Descartado:**
    inspector totalmente efímero y modo lectura absoluto.
12. **Reparaciones posteriores planificadas, sin bloquear el cierre.** El cierre
    mínimo de la spec es InfoJobs (RF-12 y criterios de finalización); Indeed,
    LinkedIn, IrishJobs, Glassdoor y las secundarias se ejecutan después con el
    proceso en `main`. **Descartado:** alargar el cierre de la 004 hasta reparar
    todas las fuentes.
13. **No se exigen campos que el portal no publica.** Salario o skills ausentes
    por diseño del sitio no son fallo; la prioridad es la `description` completa
    (las skills se derivan en Databricks). **Descartado:** metas de calidad sobre
    campos que la web no ofrece.

## 12. Secuencia de implementación

1. Núcleo: `targets.py` (lectura, desfase, objetivos, perfil de calidad) y
   `brief.py`, con tests (RF-1, RF-3).
2. Umbral e historial: `threshold.py` (RF-10).
3. Puerta de calidad: `quality.py` (RF-7, RF-8, RF-11).
4. Registros: `records.py`, plantilla, índice y evidencia saneada (RF-2, RF-11).
5. CLI delgada y contrato de entrada, incluido el paso 0 de refresco (RF-13).
6. `web-inspector` (contrato v2), comando `/repair` y guía de skills (RF-3,
   RF-4, RF-6).
7. Flujo de reparación documentado para el orquestador (gates, ramas,
   validación del push) (RF-7, RF-8, RF-9, RF-12, RF-14, RF-15).
8. Integración offline con el diagnóstico real y regresión completa.
9. Primer caso real: rama `repair/infojobs-<fecha>`, investigación,
   implementación, verificación, prueba en vivo con umbral y calidad, y
   validación (cierre real). Documentar el comando de tests en `AGENTS.md` (con
   visto bueno previo).
10. Reparaciones posteriores (cada una en su rama desde `main`, con su playbook):
    Indeed, LinkedIn, IrishJobs y Glassdoor; y las mejoras de completitud
    secundarias que la persona seleccione (stepstone_nl, nvb, jobs_ch).

## 13. Trazabilidad RF

| RF | Partes del plan |
|---|---|
| RF-1 | `repair/targets.py`, `brief.py`; refresco §2.1; fixtures del diagnóstico real; perfil de calidad §2.5; cualquier `id` del diagnóstico, sin listas fijas. |
| RF-2 | `records.py`; ramas `repair/`; flujo paso 1. |
| RF-3 | `web-inspector`; §6 playbooks; §7 skills; §8 contrato. |
| RF-4 | §6.0 y §7: diseño preventivo (sesión/tokens reales, fingerprint, ritmo y flujo humanos); doctrina `scraping-expert`; pausa asistida como contingencia. |
| RF-5 | §6 (líneas rojas) y §9 (escalado); sin servicios de pago. |
| RF-6 | `web-inspector` y regla legal §7/§8; flujo paso 2. |
| RF-7 | Implementador; flujo paso 3; metas de calidad §6. |
| RF-8 | `verifier`; flujo paso 4; `quality.py`; suites de regresión. |
| RF-9 | Flujo paso 5; §9 alcance y límites. |
| RF-10 | `repair/threshold.py`; `history.json`. |
| RF-11 | `records.py`; índice, plantilla y `quality_after.json`; flujo paso 7. |
| RF-12 | Flujo paso 8; validación del push. |
| RF-13 | `repair/cli.py`; contrato §4; comando `/repair`; refresco paso 0. |
| RF-14 | Ramas y registros por fuente; §6.6 y §12 paso 10; fixtures por portal. |
| RF-15 | §1 límites; prueba acotada §9. |
| RF-16 | `repair/quality.py`; §9 puerta de calidad; registros §3.4 y §4. |

## 14. Cumplimiento de la constitución

- **Stack simple:** núcleo en biblioteca estándar (pytest para tests); sin
  dependencias nuevas en los scrapers salvo justificación en el registro. La
  arquitectura de agentes, comandos y skills se versiona en `.opencode/`; la
  CLI `agent-browser` y Chrome son tooling local, no runtime, y las capturas
  de investigación quedan fuera de git.
- **Spec y código:** este plan implementa RF-1–RF-16; lo que exceda el alcance
  se propone como spec nueva o actualización de la 004.
- **Lógica e interfaz:** selección, umbral, calidad y registros son módulos
  probados; la CLI y los agentes solo coordinan.
- **Tests:** unitarios e integración offline del núcleo; suites de cada scraper
  modificado; bits de red solo en la prueba en vivo acotada, fuera de pytest.
- **Persistencia:** no se escribe en Azure ni en la landing; las evidencias
  locales son operativas y los resúmenes van al repositorio.
- **Idioma:** identificadores y artefactos de máquina en inglés; mensajes,
  informes y registros para la persona, en español.
