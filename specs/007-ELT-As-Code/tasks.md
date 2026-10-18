# Tareas: ELT como código (Asset Bundles) y entorno dev

Tareas ordenadas por dependencia y dimensionadas para sesiones de ~20–35
minutos. Formato de aprendizaje guiado: **objetivo → preguntas guía → pistas y
documentación → hecho cuando → evidencia**. La persona implementa; el asistente
orienta con preguntas y revisa (tests y revisión independiente del diff) antes
de cada commit. Ninguna tarea se marca como hecha sin ejecutar su comprobación.

Las tareas de preparación (T-02–T-04) pueden hacerse ya. El código del bundle
(T-05 en adelante) arranca cuando la spec 006 esté cerrada, su rama mergeada a
`main` y esta rama puesta al día (`git merge main`), para capturar el job
completo —incluida la tarea de export web— en el bundle.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación, con
  mensaje convencional y referencia a la tarea
  (p. ej. `chore(bundle): T-05 esqueleto del bundle`).
- Antes del commit, el verificador revisa el diff de la tarea contra esta spec
  y el plan; no se commitea con hallazgos `BLOQUEANTE`.
- El commit local es un checkpoint; no se hace push sin revisión previa de la
  persona.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`).
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec antes de
  continuar.
- Comandos de comprobación: `databricks bundle validate` y las suites existentes
  (`python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio).

## 1. Preparación e investigación (sin tocar la ELT)

- [x] **T-01 — Hoja de ruta y notas inicializadas** (~15 min)
  - **RF:** RF-8.
  - **Hecho cuando:** existen `docs/databricks-de/roadmap.md` (calendario, mapa
    de dominios y bitácora) y `docs/databricks-de/notes/README.md` (índice y
    plantilla), versionados en la rama de la spec.
  - **Evidencia:** commit `docs(spec): T-01 ...`; hecho por el asistente al
    arrancar el track.

- [x] **T-02 — CLI y perfil de Databricks** (~25 min)
  - **RF:** RF-3.
  - **Objetivo:** tener la CLI instalada y autenticada contra el workspace, y
    confirmar la edición de la suscripción.
  - **Preguntas guía:** ¿qué edición tiene el workspace (Premium?) y por qué
    importa para el resto del track (system tables, row filters, column
    masks)? ¿Con qué identidad trabajarás en dev?
  - **Pistas y docs:** la extensión de VS Code dejó un perfil ya válido
    (`vs_code`, con PAT): el track usa `--profile vs_code`. `databricks auth
    profiles` lista lo configurado; si algún día no apareciera, basta con
    recrear el perfil en `%USERPROFILE%\.databrickscfg` o usar
    `DATABRICKS_HOST`/`DATABRICKS_TOKEN` para la sesión. La edición **no**
    depende de cómo te autentiques: es el SKU del recurso en Azure (portal →
    Databricks workspace → Overview → Pricing tier). Comprobación práctica con
    el PAT: `databricks api get /api/2.1/unity-catalog/catalogs --profile
    vs_code` lista los catálogos de Unity Catalog (incluido `job_offers`; en
    Azure, UC exige Premium o Enterprise).
  - **Hecho cuando:** el perfil responde con tu usuario y la edición queda
    anotada (SKU del portal o inferida por UC).
  - **Resultado:** perfil `vs_code` con `Valid: Yes` y listado de catálogos de
    UC obtenido correctamente ⇒ Unity Catalog activo ⇒ edición
    Premium/Enterprise (inferida; confirmar el SKU en el portal si hay
    acceso).
  - **Evidencia:** salidas registradas en `evidence.md` (sin tokens).

- [x] **T-03 — Inventario del job actual** (~30 min)
  - **RF:** RF-1.
  - **Depende de:** T-02.
  - **Objetivo:** registrar la definición exacta del job `Git Based ELT` como
    referencia para el bundle.
  - **Preguntas guía:** ¿cuántas tareas hay y con qué dependencias? ¿qué compute
    usa cada una? ¿cuál es la programación exacta (cron y zona horaria) o el
    trigger si lo hubiera? ¿`max_concurrent_runs` y timeouts? ¿Qué campo conecta
    con la Git folder (`git_source`, `source`)?
  - **Pistas y docs:** `databricks jobs list --profile vs_code` y
    `databricks jobs get <job-id> --profile vs_code`; en la UI, el job tiene una
    vista JSON. Guarda el resultado saneado como
    `specs/007-ELT-As-Code/job-actual.json`.
  - **Hecho cuando:** JSON guardado y resumen (tareas, dependencias, compute,
    programación) anotado en `evidence.md`.
  - **Resultado:** 7 tareas (bronze → 4 silver → gold → export web), job cluster
    `tm-lens-job` (Standard_D4ds_v4), `max_concurrent_runs: 1`, sin timeout y
    sin `git_source` (los notebooks se referencian por ruta); **programación
    diaria `50 0 23 * * ?` en `Europe/Madrid`** (no hay File Arrival: el plan
    inicial lo asumía y se corrige). `job-actual.json` (saneado) en la spec;
    existe otro job antiguo (`Proceso Completo`) observado y fuera de alcance.
  - **Evidencia:** `job-actual.json` + fila en `evidence.md`.

- [x] **T-04 — Radiografía de la Git folder** (~25 min)
  - **RF:** RF-9.
  - **Objetivo:** saber exactamente cómo llega el código al job hoy y qué
    implica hasta el cutover.
  - **Preguntas guía:** ¿qué repo y qué ref sigue la Git folder? ¿Se actualiza
    sola cuando hay push? ¿Desde qué ruta del workspace ejecuta el job? ¿Qué
    pasa en producción si esta semana se mergea algo a la rama que sigue?
  - **Pistas y docs:** UI → Workspace → Repos/Git folders; `databricks repos
    list --profile vs_code` y `databricks repos get <id> --profile vs_code`.
  - **Hecho cuando:** nota con la respuesta a cada pregunta y captura de la
    configuración, con las implicaciones para el cutover anotadas.
  - **Resultado:** no aparece en `repos list` (son Git folders nuevas, fuera de
    esa API); en la UI, la carpeta del usuario tiene notebooks antiguos sueltos
    y la Git folder `talent-market-lens`, **asociada hoy a la rama de la 006**
    y que **se actualiza solo con pull manual** en el workspace. El job ejecuta
    las 7 tareas desde esa carpeta, así que producción corre hoy el código
    sincronizado de la 006; tras su merge, la carpeta pasará a `main`.
    Implicación: hasta el cutover, un merge a `main` no llega a producción sin
    pull manual; por eso la 007 no toca notebooks (solo la definición del job).
  - **Evidencia:** fila en `evidence.md` + las 7 rutas verificadas en
    `job-actual.json` (todas dentro de la Git folder).

## 2. Bundle y entorno dev (arranca tras el cierre de la 006)

- [x] **T-05 — Esqueleto del bundle** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** cierre de la 006 y `git merge main` en esta rama.
  - **Objetivo:** crear la raíz del bundle y dejarla validando en verde.
  - **Preguntas guía:** ¿qué campos mínimos necesita `databricks.yml` (nombre,
    host, target dev)? ¿Qué artefactos locales del bundle no deben
    versionarse?
  - **Pistas y docs:** guía de Asset Bundles y referencia de `databricks.yml`;
    `databricks bundle validate`; añadir `.databricks/` a `.gitignore`.
  - **Hecho cuando:** `databricks bundle validate` pasa (aún sin job) y el
    estado local del bundle queda ignorado.
  - **Resultado:** bundle `talent-market-lens` con target dev (`mode:
    development`, host del workspace), aún sin job (llega en T-06);
    `.databricks/` ignorado en `.gitignore`; `Validation OK!` con la CLI
    v1.21.0 (winget, en PATH).
  - **Evidencia:** salida de validate en `evidence.md`.

- [x] **T-06 — Capturar el job con `bundle generate`** (~30 min)
  - **RF:** RF-1.
  - **Depende de:** T-03, T-05.
  - **Objetivo:** convertir el job real en recurso del bundle como punto de
    partida.
  - **Preguntas guía:** ¿coincide task a task con `job-actual.json`? ¿Qué
    detalle no supo capturar (programación, política de cluster, librerías)? ¿A
    dónde apuntan los `notebook_path` generados?
  - **Pistas y docs:** `databricks bundle generate job --existing-job-id
    <id> --key elt --profile vs_code`; comparar el recurso generado con el JSON.
  - **Hecho cuando:** existe el recurso en `resources/`, validate pasa y las
    diferencias con el job manual están listadas.
  - **Resultado:** `resources/elt.job.yml` con las 7 tareas y sus `depends_on`,
    job cluster `tm-lens-job`, `schedule` (`50 0 23 * * ?`, `Europe/Madrid`,
    UNPAUSED), `max_concurrent_runs: 1` y notificación de fallo; validate
    verde. Diferencias vs `job-actual.json` en `evidence.md`: `notebook_path`
    apuntan a copias descargadas en `src/` (T-07 los repunta a
    `databricks_notebooks/` y elimina `src/`), sin `run_as` explícito,
    `base_parameters` incompletos (T-07) y `include` añadido por `generate`.
  - **Evidencia:** diff/resumen en `evidence.md`.

- [x] **T-07 — Parametrizar por target** (~30 min)
  - **RF:** RF-1, RF-3.
  - **Depende de:** T-06.
  - **Objetivo:** mismas tareas para dev y prod, con variables por target.
  - **Preguntas guía:** ¿qué cambia entre dev y prod (catálogo, checkpoints,
    export, nombre)? ¿Cómo llegan las variables a los widgets de cada notebook
    (`base_parameters`)? ¿Los `notebook_path` apuntan a
    `${workspace.file_path}/databricks_notebooks/...`? ¿Está el `sync` acotado
    para no subir el repo entero?
  - **Pistas y docs:** referencia de variables y targets de bundles; valores en
    `plan.md` §6.
  - **Hecho cuando:** validate pasa, `databricks bundle summary -t dev`
    muestra los valores dev y no quedan rutas de producción hardcodeadas en el
    recurso (dev pausado, sin programación).
  - **Resultado:** variables por target (plan §6; dev `Git Based ELT Dev`);
    `notebook_path` a `${workspace.file_path}/databricks_notebooks/...`;
    `base_parameters` `${var.*}` en las 7 tareas; dev sin programación y prod
    con `schedule` PAUSED (override de target); `sync` =
    `databricks_notebooks/**`; `src/` eliminado; validate dev/prod en verde y
    `summary -t dev` con los valores dev.
  - **Evidencia:** `bundle summary` en `evidence.md`.

- [x] **T-08 — Catálogo dev** (~20 min)
  - **RF:** RF-2.
  - **Depende de:** T-02.
  - **Objetivo:** crear el destino aislado de dev.
  - **Preguntas guía:** ¿tiene el metastore almacenamiento por defecto? ¿Quién
    debe ser el owner del catálogo (tú o un service principal)? ¿Qué grants
    necesitas para escribir?
  - **Pistas y docs:** SQL Editor o Catalog Explorer:
    `CREATE CATALOG IF NOT EXISTS job_offers_dev;` y schemas `bronze`,
    `silver`, `gold`. Si el metastore no tiene default storage, se indica una
    managed location propia (misma storage, prefijo dev).
  - **Hecho cuando:** `SHOW SCHEMAS IN job_offers_dev` devuelve los tres
    schemas y puedes escribir en ellos.
  - **Resultado:** `job_offers_dev` creado con `MANAGED LOCATION` paralela a la
    de prod (misma storage, sufijo `_dev`, plan §8) y schemas `bronze`,
    `silver` y `gold`; `SHOW SCHEMAS` los devuelve y la prueba de escritura
    (`_scratch`) funciona.
  - **Evidencia:** salida + captura.

- [x] **T-09 — Deploy dev e inspección** (~25 min)
  - **RF:** RF-2.
  - **Depende de:** T-07, T-08.
  - **Objetivo:** primer despliegue real del bundle, sin ejecutar nada.
  - **Preguntas guía:** ¿dónde quedaron los ficheros sincronizados? ¿En qué se
    diferencia el job dev del manual (además de las variables)? ¿Subió el sync
    solo lo necesario (tamaño razonable)?
  - **Pistas y docs:** `databricks bundle deploy -t dev --profile vs_code`;
    `databricks bundle summary -t dev --profile vs_code`; `databricks jobs get`
    del job dev.
  - **Hecho cuando:** el job dev existe, está pausado y las diferencias con el
    manual están revisadas (solo variables, paths y programación).
  - **Resultado:** job dev `[dev …] Git Based ELT Dev` (id `320947022571947`)
    desplegado y sin programación; 7 tareas con paths al bundle y parámetros
    dev. Hallazgo corregido: `sync.include` no restringe (el primer deploy
    subió el repo entero) → `sync.paths: [databricks_notebooks]`; redeploy en
    limpio (solo notebooks).
  - **Evidencia:** captura + resumen.

- [x] **T-10a — Arreglo de rendimiento de las silvers de LinkedIn y multisite (dedup temprano por `job_id`)** (~35 min)
  - **RF:** RF-4 (desbloquea T-10).
  - **Depende de:** T-09; **precede a** T-10.
  - **Modo de trabajo (persona, 2026-10-17):** la implementa el asistente
    (`implementer`) a petición expresa de la persona; la persona revisa e
    interioriza el código (se le imprime en la sesión) y la verificación es
    independiente (`verifier`).
  - **Contexto (diagnóstico, 2026-10-17):** el bronce dev acumula snapshots
    del landing leídos por el checkpoint nuevo. LinkedIn: 9.569.896 filas para
    13.376 `job_id` distintos (~715 copias por oferta) y silver dev >92,9 min
    (cancelada); multisite: 225.796 filas para 10.489 `job_id` (~21 copias).
    En prod, LinkedIn (16.371 filas) tarda 5,9 min y multisite 11.562 filas.
  - **Objetivo:** que las silvers de LinkedIn y multisite conserven solo la
    última captura de cada oferta (`job_id`; `site` + `job_id` en multisite)
    antes de `enrich()` y de la tabla de compañías, sin cambiar el contrato de
    columnas de salida ni la dedup semántica final por `_key`.
  - **Decisiones (persona, 2026-10-17, tras auditar el bronce por SQL):**
    - Módulo importable `databricks_notebooks/Deduplicate_Job_Offers.py` con
      `keep_latest_by(df, keys, order_by=("scraped_at", "_ingest_date"))`:
      `Window` + `row_number` + `desc_nulls_last` (no `dropDuplicates`, que
      conserva una fila arbitraria).
    - Llamadas: LinkedIn `["job_id"]`; multisite `["site", "job_id"]`, justo
      tras leer el bronce (antes del rename de columnas).
    - Filas con clave nula o vacía: passthrough sin tocar (auditoría:
      0 nulos y 0 vacíos en las 4 tablas; queda como guarda preventiva y se
      cubre con test).
    - Orden por `scraped_at` (string ISO-8601, offset único `+00:00` ⇒ el
      orden lexicográfico equivale al cronológico) y `_ingest_date`; en
      empates exactos, cualquiera de las filas empatadas es válida.
    - La dedup final por `_key` y el contrato de columnas no se tocan.
      La decisión de fondo «append vs carga completa diaria» sigue en 008/009.
  - **Resultado (2026-10-17):** módulo `databricks_notebooks/Deduplicate_Job_Offers.py`
    (9 tests nuevos en `databricks_notebooks/tests/test_dedup_job_offers.py`;
    suite `databricks_notebooks`: 81 passed, 1 skipped preexistente de Windows)
    integrado en ambas silvers; dos rondas de revisión independiente sin
    bloqueantes (commit `a9e9d15`). Tiempo de la silver: pendiente del run dev
    de T-10.
  - **Hecho cuando:** la lógica vive en un módulo importable con tests, ambos
    notebooks la usan, las suites existentes siguen verdes y el diff pasa la
    revisión independiente.
  - **Evidencia:** diff impreso y revisado por la persona, salida de tests y
    tiempo de la silver en el run dev de T-10.

- [x] **T-10 — Run dev end-to-end** (~35 min)
  - **RF:** RF-4.
  - **Depende de:** T-09, T-10a (y datos recientes en la landing).
  - **Objetivo:** ejecutar la ELT completa contra el entorno dev.
  - **Preguntas guía:** ¿todas las tareas en verde? ¿Los conteos por tabla
    cuadran con producción para los mismos datos de origen? ¿Se escribieron los
    checkpoints en `_checkpoints_dev`? ¿El export quedó en `web_export_dev` sin
    tocar el de producción?
  - **Pistas y docs:** lanzar desde la UI o `databricks bundle run -t dev
    <key>`; guardar capturas de Spark UI de la primera ejecución (serán
    útiles en la spec 009).
  - **Resultado (2026-10-18):** run `540330014336242` SUCCESS en 17 m 43 s
    (bronze 7 m 36 s, indeed 4 m 50 s, infojobs 8 m 46 s, linkedin 6 m 11 s,
    multi_site 5 m 24 s, gold 55 s, export 26 s), tras la normalización one-off
    del bronce dev (LinkedIn 9,57 M filas y multi_site 0,23 M → 1 por clave; el
    dedup de la silver de LinkedIn pasó de 12 GB/22 GB a 32 MB/43 MB). Conteos
    dev vs prod: ≤1 % salvo indeed (+3,8 % / +3,7 %) y `fact_offer_skills`
    (+1,7 %), coherentes con las ofertas nuevas del 18-oct; dimensiones y
    contrato de columnas idénticos; el desfase de un ciclo (dev hasta el
    18-oct, prod hasta el 17-oct) explica el resto. Tabla y detalle en
    `evidence.md`.
  - **Hecho cuando:** run verde + tabla de conteos dev vs prod para la misma
    fecha + evidencia registrada.
  - **Evidencia:** run id, duración, tabla de conteos, capturas.

- [x] **T-11 — Ajustes de compute y límites** (~20 min)
  - **RF:** RF-2, RF-3.
  - **Depende de:** T-10.
  - **Objetivo:** dejar el entorno dev barato y seguro.
  - **Preguntas guía:** ¿mismo compute que prod o uno más pequeño/serverless?
    ¿Timeout razonable? ¿`max_concurrent_runs: 1`? ¿Hay política de clusters
    disponible?
  - **Hecho cuando:** cambios aplicados, validate verde y una tarea de prueba
    vuelve a correr bien (si cambió el compute).
  - **Resultado (2026-10-18):** compute de dev sin cambios (mismo job cluster
    `tm-lens-job`, `Standard_D4ds_v4` single node; serverless descartado por
    alcance y la única policy de jobs prohíbe el single-node, así que no se
    aplica); `timeout_seconds` del job de `0` a **1800 s (30 min)** en el
    recurso base → dev **y** prod, con las 7 tareas a `0` y
    `max_concurrent_runs: 1` intacto; validate dev/prod en verde. Desviación
    deliberada de prod (el job manual no tenía timeout) a listar en T-15.
  - **Evidencia:** diff + nota de coste en `evidence.md`.

## 3. Validación continua (CI)

- [x] **T-12 — Workflow de validación** (~30 min)
  - **RF:** RF-3, RF-5.
  - **Objetivo:** validar el bundle automáticamente en cada cambio.
  - **Preguntas guía:** ¿qué secrets necesita el runner y con qué alcance
    mínimo? ¿Qué eventos disparan (PR, push)?
  - **Pistas y docs:** `.github/workflows/databricks-bundle-validate.yml`:
    instalar la CLI en el runner, `databricks bundle validate -t dev` con
    `DATABRICKS_HOST` y `DATABRICKS_TOKEN` de dev; `concurrency` para no
    solapar.
  - **Hecho cuando:** el workflow pasa en un push real de esta rama.
  - **Resultado (2026-10-18):** workflow `.github/workflows/databricks-bundle-validate.yml`
    (CLI 1.21.0 fijada; dispara en `pull_request`/`push` con filtro de rutas
    —`databricks.yml`, `resources/**`, `databricks_notebooks/**` y el propio
    workflow— más `workflow_dispatch`; `concurrency` y `contents: read`; solo
    valida dev, sin deploy). Secretos de repo `DATABRICKS_HOST`/`DATABRICKS_TOKEN`;
    el PAT apareció en claro al crear el fichero (nunca commiteado) y se rotó;
    endurecido moviendo el PAT al step de validación y con `timeout-minutes: 10`.
    Run verde en un push real; verificación independiente sin bloqueantes.
  - **Evidencia:** URLs de los runs en `evidence.md`.

- [x] **T-13 — Probar rojo y verde** (~20 min)
  - **RF:** RF-5.
  - **Depende de:** T-12.
  - **Objetivo:** comprobar que el CI detecta un bundle inválido.
  - **Pistas y docs:** PR con un error intencionado (variable inexistente),
    ver el rojo, revertir y ver el verde.
  - **Hecho cuando:** quedan registrados los dos runs (rojo y verde).
  - **Resultado (2026-10-18):** PR #1 desde `test/t13-bundle-invalido` con error
    intencionado (`resources/elt.job.yml` → `${var.job_name_t13_typo}`): la CI
    falló en `pull_request` (rojo); revertido con `git revert` y run en verde.
    Nota: al revertir, el diff de la PR queda vacío en las rutas filtradas por
    `paths`, así que el verde lo produce el trigger `push` (el filtro de
    `pull_request` no dispara si no hay cambios netos en esas rutas). La rama de
    la spec quedó restaurada (el merge anula el error) y la rama de prueba se
    borró.
  - **Evidencia:** URLs en `evidence.md`.

- [x] **T-14 — [extra] Deploy dev por `workflow_dispatch`** (~25 min)
  - **RF:** RF-5.
  - **Depende de:** T-12.
  - **Objetivo (opcional, si el tiempo lo permite):** practicar automatización
    de despliegue solo a dev.
  - **Preguntas guía:** ¿Cómo proteges el entorno? ¿Qué permisos tiene el token
    de CI?
  - **Hecho cuando:** un dispatch despliega dev (y solo dev) con el workflow en
    verde.
  - **Resultado (2026-10-18):** workflow `.github/workflows/databricks-bundle-deploy-dev.yml`
    (`workflow_dispatch`, solo dev: `bundle deploy -t dev`; guard de rama
    `main`/`spec/007-elt-as-code`; `concurrency` sin cancelar; `timeout-minutes:
    15`; PAT acotado al step). Entorno de GitHub **`dev`** con *required
    reviewers* → el dispatch espera aprobación antes de desplegar. Para que el
    botón «Run workflow» aparezca hubo que mergear la rama a `main` (checkpoint
    `ada90e4`, con `AGENTS.md` de `main` restaurado). El deploy no arranca
    compute: solo sube ficheros y actualiza la definición del job dev (aplica el
    timeout de T-11).
  - **Evidencia:** URL en `evidence.md`.

## 4. Producción y cutover

- [x] **T-15 — Target prod** (~30 min)
  - **RF:** RF-2, RF-6.
  - **Depende de:** T-10, T-13.
  - **Objetivo:** dejar producción lista para el cutover, pero pausada.
  - **Preguntas guía:** ¿La expresión cron (`50 0 23 * * ?`) y la zona horaria
    (`Europe/Madrid`) coinciden exactamente con las del job manual? ¿El
    owner/`run_as` seguirá siendo el mismo? ¿Nombre final del job?
  - **Pistas y docs:** valores en `plan.md` §6; `databricks bundle summary -t
    prod`; comparar con `job-actual.json`. **No habilitar nada todavía.**
  - **Hecho cuando:** validate verde, job prod desplegado **en pausa** y
    diferencias con el manual documentadas.
  - **Resultado (2026-10-18):** target prod con `run_as` y `permissions` de la
    persona (production mode exige ambos; se mantiene el owner/run_as actual y
    el service principal queda como mejora documentada). `bundle validate -t
    prod` OK; `bundle deploy -t prod` crea el job `916863262593632` (`Git Based
    ELT`), **PAUSED**, misma cron/TZ, 7 tareas con paths al bundle y variables
    de prod, `timeout_seconds: 1800`, `max_concurrent_runs: 1` y **0 runs**. El
    job manual `704156530681034` queda intacto y UNPAUSED. Diferencias con el
    manual en `evidence.md`.
  - **Evidencia:** `job-prod-bundle.json` saneado + `bundle summary -t prod` +
    `jobs list-runs` vacío; tabla en `evidence.md`.

- [x] **T-16 — Backup y plan de rollback** (~25 min)
  - **RF:** RF-6.
  - **Depende de:** T-15.
  - **Objetivo:** escribir `specs/007-ELT-As-Code/cutover.md` con precondiciones,
    pasos exactos (comandos/UI), ids, ventana, verificación de la noche,
    rollback con tiempos y criterios de aborto, y el estado final de la Git
    folder (en `main`, solo consulta: el job ya no la usa).
  - **Hecho cuando:** el plan queda revisado y aprobado explícitamente por la
    persona.
  - **Resultado (2026-10-18):** `cutover.md` escrito con precondiciones, IDs
    (manual `704156530681034`, bundle `916863262593632`, dev
    `320947022571947`), resumen de deploys/runs, pasos exactos, verificación de
    la noche, criterios de aborto, rollback con tiempos y estado final de la
    Git folder (ya en `main`, 74 commits por detrás). Rollback por definición +
    deploy (el job es `UI_LOCKED`). **Aprobado por la persona el 2026-10-18
    ≈22:40** con la Opción B: dry-run T-17 esta noche (18-oct, 23:05) y cutover
    a continuación, tras el run manual; primer run del bundle el lunes
    19-oct a las 23:00 y T-19 el martes 20. Verificación independiente sin
    bloqueantes.
  - **Evidencia:** `cutover.md` (§11 con el OK) + fila en `evidence.md`.

- [x] **T-17 — Dry-run del cutover** (~25 min)
  - **RF:** RF-6, RF-9.
  - **Objetivo:** desplegar prod en pausa y comprobar que no dispara.
  - **Preguntas guía:** ¿El job en pausa no se dispara a su hora? ¿Tareas y
    dependencias coinciden con el manual?
  - **Hecho cuando:** tras unas horas no hay ningún run del job del bundle.
  - **Resultado (2026-10-18):** el deploy en pausa ya lo hizo T-15 (job
    `916863262593632`, `PAUSED`, cron `50 0 23 * * ?`/`Europe/Madrid`). A las
    **23:05**, pasada la hora programada, `jobs list-runs` del bundle devuelve
    `[]` (**0 runs**); el job manual `704156530681034` lanzó su run periódico
    `364055615164423` a las 23:00:51 (RUNNING). No se desplegó ni ejecutó nada
    nuevo para esta comprobación.
  - **Evidencia:** salida de `jobs list-runs` (bundle vacío y run manual) + fila
    en `evidence.md`.

- [ ] **T-18 — Cutover real** (~30 min)
  - **RF:** RF-6.
  - **Depende de:** T-17.
  - **Objetivo:** el bundle pasa a ser la fuente de verdad del job de producción.
  - **Pasos:** deshabilitar el job manual → habilitar el del bundle → registrar
    hora e ids.
  - **Hecho cuando:** solo el job del bundle está activo con la programación; no
    queda ningún job manual habilitado.
  - **Evidencia:** captura + ids + hora en `evidence.md`.

- [ ] **T-19 — Noche real en verde** (~20 min, a la mañana siguiente)
  - **RF:** RF-6.
  - **Objetivo:** verificar el primer ciclo real.
  - **Preguntas guía:** ¿Hubo un único run? ¿Verde? ¿Las tablas avanzaron
    (conteos antes/después)? ¿El export web se actualizó (`meta.json` nuevo)?
    ¿El pipeline local cerró con `Fin pipeline`?
  - **Hecho cuando:** evidencia completa del ciclo. Si algo falla: rollback
    (T-20) y análisis del fallo dentro de esta spec.
  - **Evidencia:** run id, hora, duración, conteos, `meta.json`.

- [ ] **T-20 — Rollback en seco** (~20 min)
  - **RF:** RF-6.
  - **Depende de:** T-19.
  - **Objetivo:** probar que volver atrás es rápido y seguro (en ventana sin
    riesgo, por la mañana).
  - **Pasos:** deshabilitar el job del bundle → rehabilitar el manual →
    volver al estado de cutover. Cronometrar.
  - **Hecho cuando:** procedimiento ejecutado sin sorpresas y tiempos
    actualizados en `cutover.md`.
  - **Evidencia:** registro con tiempos.

## 5. Cierre

- [ ] **T-21 — Documentación operativa y nota del dominio** (~35 min)
  - **RF:** RF-7, RF-8.
  - **Objetivo:** dejar el proyecto documentado y la nota `05-cicd.md` escrita.
  - **Hecho cuando:** RUNBOOK y README documentan bundle, targets, variables,
    deploy y cutover/rollback; la propuesta de `AGENTS.md` está presentada (sin
    aplicar sin OK); y `docs/databricks-de/notes/05-cicd.md` está escrita con lo
    aprendido.
  - **Evidencia:** commits + nota.

- [ ] **T-22 — Evidencia y comprobación real** (~25 min)
  - **RF:** RF-8, RF-9.
  - **Objetivo:** cerrar la spec con todo verificable.
  - **Pasos:** consolidar `evidence.md`; suites existentes en verde; revisar
    `git log -p` del último grupo; actualizar la bitácora del roadmap; solicitar
    la revisión final de la persona (push/merge solo con su OK).
  - **Merge a `main` (excepción):** la sección «Trabajo guiado de las specs» de
    `AGENTS.md` es local de este worktree; al fusionar la rama (checkpoint o
    cierre), restaurar el fichero de `main` antes de cerrar el merge
    (`git checkout main -- AGENTS.md`).
  - **Hecho cuando:** suites verdes + evidencia completa + revisión solicitada.
  - **Evidencia:** salidas + revisión.
