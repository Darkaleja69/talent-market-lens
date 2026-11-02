# Tareas: mecanismo de ingesta en streaming y observabilidad del run

> Borrador del **2026-11-01**. Rama `spec/011-streaming-observability` (desde
> `main`, `c9c4a19`), worktree `talent-market-lens-011`. Formato de trabajo según
> `AGENTS.md`: **la persona decide** el alcance y revisa; el asistente prepara las
> tareas, implementa, explica y commitea tras la verificación independiente.
> Ninguna tarea se marca como hecha sin su comprobación.
>
> **Aprobación previa (AGENTS.md):** antes de tocar
> `databricks_notebooks/**/*.ipynb`, un módulo `*.py` de `databricks_notebooks/`,
> cualquier `.yml` del bundle o un recurso nuevo, se presenta fichero, celdas/claves
> afectadas, diff propuesto y motivo, y se espera el OK. Los **tests quedan fuera**
> de esta regla.
>
> **Presupuesto (009 §17):** **3 runs de dev** (~0,51–0,72 €) y **1 sesión SQL**
> (~0,43 €) → **~0,95–1,15 €** dentro del techo de **2,0 €**. La demo de Lakeflow se
> hace en una cuenta **Free Edition** aparte con un extracto CSV: **0 €** (solo
> egress). La migración a producción, si la hay, va al presupuesto de producción y
> **exige aprobación explícita**. No hay run de reserva. Cada gasto se anota en
> `docs/costs.md`.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `feat(streaming): T-03 resumen medido del bronce`).
- Suites: `python -m pytest databricks_notebooks/tests -q`,
  `python -m pytest scrapers-pipeline/tests -q` y
  `python -m pytest tests -q` desde la raíz. La suite completa es gate de la spec
  (T-14), no de cada tarea.
- Evidencia: una fila por comprobación en `evidence.md` (fecha, tarea,
  comprobación, resultado).
- Al cerrar el bloque de observabilidad (T-03–T-07) y el de evaluación
  (T-08–T-10), **parar** y revisar `git log -p` del grupo antes de seguir.

## 0. Decisiones tomadas (2026-11-01)

- **Resultado = decisión medida.** «No cambiar» es un resultado válido; la carga de
  la prueba la tiene el candidato declarativo.
- **Lakeflow se evalúa en una cuenta Free Edition aparte, con un extracto CSV**
  (decisión del 2026-11-02): no se crea ningún pipeline `serverless` de pago. Free
  Edition es serverless, pero gratuito: se evita el DBU. La demo valida semántica y
  contrato, **no** tiempo ni coste de producción.
- **El kit de la demo vive en el repositorio** (`candidate/`): CSVs generados desde
  un Parquet real del landing, manifiesto con conteos esperados, pipeline
  declarativo, mecanismo actual adaptado al mismo CSV e instrucciones. Reproducible
  y sin secretos.
- **Riesgo asumido:** el extracto no cubre el 100 % del esquema ni el volumen real;
  la paridad total se valida en el cutover (T-12) y el coste de producción queda
  como estimación sujeta a aprobación.
- **Observabilidad con un solo escritor:** `ops.run_metrics` (una fila por run,
  `MERGE` por `run_id`); las duraciones por tarea salen de `system.lakeflow`.
- **Alertas nativas y frescura en CI; alertas SQL descartadas** por coste
  recurrente (~0,43 €/despertar).
- **Deuda heredada de la 010:** `target prod` está `PAUSED` en `databricks.yml`
  (T-09 de la 010). Se repone en T-01; sin eso no hay run que valide la 011.

## 1. Reconocimiento y criterios

- [x] **T-01 — Reconocimiento gratuito y criterios de decisión** (~75 min)
  - **Objetivo:** dejar escritos los criterios de `plan.md` §3 con sus umbrales y
    el estado real de las piezas que condicionan el diseño, **sin gastar un céntimo**.
  - **Comprobaciones (todas gratis):**
    - Grants efectivos sobre `system.lakeflow`, `system.billing` y `system.compute`
      (API de Unity Catalog); tabla por tabla, si se puede leer o no.
    - Que `system.lakeflow.job_run_timeline` y `job_task_run_timeline` contienen
      runs de `320947022571947` (dev) y del job de producción, con su latencia.
    - Soporte de `resources.pipelines` y de `health.rules[]` en el bundle (CLI
      1.21.0; la clave es `health`, no `health_rules`) y estado del perfil `free`
      en este equipo (la demo se ejecuta en otra cuenta; aquí solo se documenta
      cómo se sube el kit).
    - Configuración actual del event log del job cluster y retención.
    - Línea base de ficheros del landing y de checkpoints: lo que se pueda listar
      sin coste; lo que exija clúster se marca para T-08.
    - **Reponer `pause_status: UNPAUSED`** en el `target prod` (aprobación de la
      persona el 2026-11-02; deploy ejecutado por el asistente) y confirmar el
      estado del job.
  - **Preguntas guía:** ¿qué tablas de sistema son legibles y con qué latencia?
    ¿el event log actual sobrevive al clúster? ¿cuántos ficheros hay pendientes por
    fuente ahora mismo? ¿qué necesita el kit para correr en Free Edition sin tocar
    el workspace pagado?
  - **Hecho cuando:** `design-streaming.md` tiene el estado de partida y la tabla de
    criterios con umbrales, `design-observability.md` tiene el inventario de fuentes
    de observabilidad disponibles (con lo que **no** se puede), `prod` está
    `UNPAUSED` y cada afirmación lleva su fuente o `[NO VERIFICADO]`.
  - **Evidencia:** salidas de CLI/API, capturas y fila en `evidence.md`. **0 €.**
  - **Estado (2026-11-02):** **hecha con 0 €** (0 runs, 0 clústeres, 0 SQL, 0
    pipelines en el workspace pagado). Escritos `design-streaming.md` (§1 estado de partida con landing y
    checkpoints medidos por listado, §2 números de la 010 citados, §3 criterios de
    `plan.md` §3 con sus umbrales, §8 Free Edition + CSV) y
    `design-observability.md` (inventario de fuentes, permisos tabla por tabla,
    límites sin SQL/runs, `ops.run_metrics`, tarea final y umbrales).
    **Producción repuesta:** `bundle validate -t prod` OK + `bundle deploy -t prod`
    (24 ficheros, 1 recurso cambiado); job del bundle **`916863262593632`**
    `UNPAUSED`, cron `50 0 23 ? * SUN`, `Europe/Madrid`, 8 tareas, 0 runs creados;
    job manual `704156530681034` sigue `PAUSED`. El deploy de prod se hizo **desde
    la rama de la spec** con contenido idéntico a `main` (solo cambia
    `pause_status`); merge pendiente en T-14. **Pendientes de comprobación
    gratuita:** ninguna. **`[NO VERIFICADO]`:** lectura real de `system.*`, si las
    tablas contienen runs del proyecto y con qué latencia/retención (→ **T-07**);
    tamaño y contenido del event log (→ T-06/T-08); `AUTO CDC` y límites de Free
    Edition (→ T-09/T-09b). **Nota para T-06:** la clave del bundle es
    `health.rules[]` (no existe `health_rules`; CLI 1.21.0,
    `databricks bundle schema`).

## 2. Práctica local de streaming (dominio 3)

- [x] **T-02 — Banco local: checkpoint, microbatch, watermark y estado** (~90 min)
  - **Objetivo:** demostrar con código y tests propios la semántica que explica el
    run: qué garantiza el checkpoint, qué es un microbatch, qué hace el watermark
    con un evento tardío, dónde vive el estado y cuándo hay exactly-once.
  - **Entregable:** `databricks_notebooks/streaming_lab.py` (didáctico, **no** se
    engancha al job) y `databricks_notebooks/tests/test_streaming_lab.py` con los
    cinco escenarios de `plan.md` §5, en `local[2]`, sumideros en `tmp` y sin Delta.
  - **Preguntas guía:** ¿qué pasa si muere el proceso entre el commit del offset y
    la escritura? ¿por qué `availableNow` no es «una sola pasada»? ¿qué fila
    descarta el watermark y por qué? ¿qué aporta `stateOperators` en `lastProgress`?
  - **Hecho cuando:** los cinco escenarios pasan en local, la tabla de tiempos y la
    comparación `availableNow` / `once` / `processingTime` están en
    `design-streaming.md` §5, y el test no supera ~3 min.
  - **Evidencia:** salida de pytest, tabla de tiempos y fila en `evidence.md`. **0 €.**
  - **Estado (2026-11-02):** **hecha con 0 €** (0 runs, 0 clústeres, 0 SQL). Banco en
    `databricks_notebooks/streaming_lab.py` (didáctico, sin enganche al job) con
    `tests/test_streaming_lab.py`, `tests/_local_spark.py` y `tests/conftest.py`:
    **6 passed** en **98,1 s**
    (proceso 99,7 s; entre ejecuciones del host, **96,9–169,2 s** según la carga), con
    los cinco escenarios ejecutados **una sola vez** por módulo.
    Números y comparación de triggers en `design-streaming.md` §4–§5. Prerrequisito de
    host: `winutils.exe`/`hadoop.dll` en `%LOCALAPPDATA%\hadoop\bin` (fuera del
    repositorio; en Linux/CI no hace falta y los tests saltan con instrucciones). El
    `conftest.py` global de `tests/` llama a `configure_hadoop_home()` al importar,
    antes de la primera `SparkSession` (el JVM cachea `hadoop.home.dir`), para que el
    orden de la suite no deje a T-02 sin winutils y para que los módulos que ramifican
    por `HADOOP_HOME` usen el camino real; verificado con `test_web_export.py`,
    `test_pbi_export.py` y `test_integration_spark.py`. El `MERGE` de Delta local
    (necesario en T-04) queda comprobado con `delta-spark` 4.4.1 y
    `spark.sql.extensions`/`spark.sql.catalog.spark_catalog` explícitos (ver
    `evidence.md`).
    **Desviaciones medidas y documentadas:** `once` ignora `maxFilesPerTrigger` en
    Spark 4.2; el watermark usa `outputMode("update")` + `foreachBatch` a Parquet
    porque `append` no emite ninguna ventana en la primera ejecución; exactly-once usa
    `foreachBatch` para que el checkpoint (y no el `_spark_metadata` del file sink) sea
    la única garantía. **`[NO VERIFICADO]`:** el comportamiento en DBR 17.3 (solo se
    ejecutó PySpark 4.2.0 local) y la muerte del proceso entre el commit del offset y
    la escritura (se explica en el docstring, no se simula).

## 3. Observabilidad del streaming y del run (dominio 6)

- [ ] **T-03 — Resumen medido del bronce** (~75 min)
  - **Objetivo:** cerrar el hueco de la 008: que la tarea de bronce publique por
    fuente `numInputRows`, `batchId`, duración del microbatch y del trigger,
    backlog (si el motor lo expone), filas nuevas en cuarentena y estado del
    checkpoint, sin pasadas nuevas sobre la landing.
  - **Entregable:** módulo `databricks_notebooks/Bronze_Summary.py` con funciones
    puras (`build_stream_summary`, `merge_summaries`) y tests; `bronze_ingest.ipynb`
    llama al módulo, publica `dbutils.notebook.exit` **y**
    `dbutils.jobs.taskValues.set("bronze_summary", ...)` (lo consume T-05).
  - **Restricciones:** el conteo de cuarentena se hace sobre el frame pequeño ya
    cacheado; **prohibido** `count()` del lote, `DESCRIBE HISTORY` por fuente y
    lecturas nuevas de la landing. Test de contrato: mismas acciones que hoy.
  - **Hecho cuando:** el resumen sale con las cuatro fuentes y `null` explícito en
    lo que el motor no exponga; los tests cubren un progreso sintético (batch con y
    sin filas, con y sin cuarentena) y el notebook no lanza acciones nuevas.
  - **Aprobación previa:** módulo nuevo + celda del notebook de bronce.
  - **Evidencia:** salida de tests, JSON de ejemplo y fila en `evidence.md`. **0 €.**

- [ ] **T-04 — Tabla `ops.run_metrics` y su módulo** (~60 min)
  - **Objetivo:** que cada run deje una fila consultable en Delta con sus métricas
    de negocio y frescura, idempotente y sin depender del warehouse.
  - **Entregable:** `databricks_notebooks/Run_Metrics.py`
    (`build_run_row`, `checks_json`, `merge_sql`, `record`) con tests; esquema
    `<catalog>.ops` creado por CLI (gratis) en dev y en prod; contrato de columnas
    de `plan.md` §7.1.
  - **Diseño:** `MERGE` por `run_id`; `record` nunca propaga errores (los registra
    en el log y devuelve el estado); `metrics` y `checks` son JSON serializados de
    forma estable (claves ordenadas).
  - **Hecho cuando:** los tests cubren la fila completa, la idempotencia del
    `MERGE` (dos escrituras del mismo `run_id` ⇒ una fila), el JSON estable y el
    caso de error no propagado; el esquema existe en dev y en prod.
  - **Aprobación previa:** módulo nuevo.
  - **Evidencia:** tests, `DESCRIBE` de la tabla y fila en `evidence.md`. **0 €.**

- [ ] **T-05 — Tarea final: consolidación y frescura** (~75 min)
  - **Objetivo:** que el run termine con una comprobación explícita y fallo visible
    si los datos no son frescos, y que la fila de `ops.run_metrics` quede escrita
    incluso cuando otra tarea falle.
  - **Entregable:** notebook `databricks_notebooks/run_summary.ipynb` + tarea
    `Publish_Run_Summary` en `resources/elt.job.yml` con `run_if: ALL_DONE` y
    `depends_on` de las ocho tareas; lógica en `Run_Metrics.py`.
  - **Comprobaciones del cierre:** resumen de bronce presente (task value),
    `data_date` del `meta.json` del export web ≤ 8 días, y ficheros pendientes
    **anteriores al inicio del run** (listado de landing y de `checkpoint/commits`,
    ignorando lo que llega durante el run): si hay pendientes y `numInputRows = 0`,
    falla.
  - **Hecho cuando:** el `bundle validate` está en verde, el grafo del job incluye
    la tarea final, los tests cubren cada check (incluido el fallo por frescura con
    dato inyectado) y el estado del run queda en `ops.run_metrics`.
  - **Nota:** `system.lakeflow` puede tardar; el cierre **no** depende de ella.
  - **Aprobación previa:** notebook nuevo + `.yml` del job.
  - **Evidencia:** `bundle validate`, tests y fila en `evidence.md`. **0 €.**

- [ ] **T-06 — Health rules, event log y RUNBOOK** (~60 min)
  - **Objetivo:** que un fallo o una duración anómala avisen solos y que la
    monitorización quede escrita con consultas versionadas.
  - **Entregable:** `health.rules[]` (`health` + `rules`, no `health_rules`;
    `RUN_DURATION_SECONDS` > **1500 s**) y
    `on_duration_warning_threshold_exceeded` en `resources/elt.job.yml`; event log
    a ADLS (`landing/_eventlogs/`) en el job cluster, con coste en bytes medido en
    T-08 y decisión de mantenerlo o no; sección nueva del RUNBOOK (9.8) con las
    consultas de `system.lakeflow`/`system.billing`, los umbrales y qué hacer en
    cada alerta.
  - **Descartado y documentado:** `STREAMING_BACKLOG_*` (media de 10 min; estas
    consultas duran segundos) y alertas SQL programadas (~0,43 €/despertar).
  - **Hecho cuando:** el `.yml` valida, las alertas están descritas con destinatario
    y umbral, y el RUNBOOK permite diagnosticar un run sin abrir el warehouse.
  - **Aprobación previa:** `.yml` del job.
  - **Evidencia:** `bundle validate`, extracto del RUNBOOK y fila en `evidence.md`.
    **0 €.**

- [ ] **T-07 — Sesión SQL única: `system.*` y Query Profile** (~30 min)
  - **Objetivo:** validar los permisos de las tablas de sistema, dejar las consultas
    de monitorización y coste probadas y practicar Query Profile, **una sola vez**.
  - **Ejecución (la persona):** abrir el warehouse (2X-Small), ejecutar las
    consultas del RUNBOOK (`job_run_timeline`, `job_task_run_timeline`,
    `billing.usage` por run), capturar el Query Profile de una de ellas, cerrar y
    confirmar `STOPPED`.
  - **Hecho cuando:** las tres consultas devuelven filas de los jobs del proyecto,
    el coste por run del último run de producción está registrado, el Query Profile
    está capturado y el warehouse queda `STOPPED` (antes y después).
  - **Evidencia:** resultados saneados, captura del Query Profile, coste en
    `docs/costs.md` y fila en `evidence.md`. **~0,43 €** (1 sesión, < 10 min).

## 4. Evaluación del mecanismo de ingesta

> **Reordenación (2026-11-02, a petición de la persona):** **T-09 se adelanta**
> para poder probar el kit en Free Edition en paralelo; **T-09b** la ejecuta la
> persona cuando pueda y **T-10 no se cierra hasta entonces**.
>
> **Secuencia (2026-11-02):** **T-10 se cerró antes que T-08** porque la comparación
> se hizo con la demo de Free Edition (T-09/T-09b) y los datos de la 010; **T-08 pasa
> a ser la primera medición del resumen del bronce en dev, después de T-03–T-06**, y
> sigue consumiendo **1 de los 3 runs** (el presupuesto no cambia).

- [ ] **T-08 — Run 1: mecanismo actual medido (A)** (~20 min de persona)
  - **Objetivo:** tener la línea base real del bronce con el resumen nuevo y las
    métricas del run.
  - **Ejecución (run 1 de 3):** `bundle deploy -t dev` + `bundle run elt -t dev`
    (sin parámetros), con ficheros nuevos en el landing si el pipeline local los ha
    subido desde el último run de dev; si no hay ninguno, se anota y el run mide
    idempotencia.
  - **Hecho cuando:** las 9 tareas (8 de negocio + cierre) en verde; resumen por
    fuente con `numInputRows` y cuarentena; fila en `ops.run_metrics`; tiempos por
    tarea de `system.lakeflow`; bronce ≤ **134 s** de ejecución y run ≤ **1350 s**;
    coste anotado.
  - **Evidencia:** id del run, resumen de bronce, `ops.run_metrics`, tiempos, coste
    en `docs/costs.md` y fila en `evidence.md`. **~0,20 €.**

- [x] **T-09 — Kit de la demo para Free Edition (CSV + dos mecanismos)** (~90 min)
  - **Objetivo:** dejar un kit reproducible y sin secretos que permita ejecutar en
    otra cuenta Free Edition los **dos mecanismos sobre los mismos datos** y traer
    evidencia comparable.
  - **Entregable en `specs/011-Streaming-And-Observability/candidate/`:**
    - `data/<fuente>.csv` por fuente, generados desde **un Parquet real del landing**
      (descarga con `LANDING_SAS_TOKEN` + AzCopy, 0 €), con todas las columnas del
      Parquet (textos largos truncados y declarado en el manifiesto), muestreo
      acotado y **filas inválidas inyectadas** por fuente (al menos una
      `missing_key` y una `missing_scraped_at`).
    - `manifest.json`: ficheros, filas, columnas, filas válidas e inválidas por
      motivo, y el `sha256` de cada CSV.
    - `schema.json`: tipología Spark por columna y fuente, base del esquema de
      lectura y de las claves y la columna de captura.
    - `pipeline_declarative.py`: streaming tables desde los CSV, `expectations` y
      `AUTO CDC` SCD1 con claves por fuente y `sequence_by` por instante de captura.
    - `mechanism_current.py`: el mecanismo actual adaptado al CSV (Structured
      Streaming + `foreachBatch` + `MERGE`), reutilizando `Upsert_Job_Offers.py`.
    - `README.md`: cómo subir los CSV a un Volume, crear cada pipeline, ejecutarlo
      dos veces (segunda sin ficheros nuevos) y qué capturas traer.
    - `generate_dataset.py`: el script que genera los CSV y el manifiesto (para
      repetirlo con otro lote).
  - **Restricciones:** no se sube nada al workspace pagado ni se crea ningún
    pipeline de pago; ningún secreto en el kit; los CSV no superan ~1 MB en total y
    **no se versionan** (`candidate/data/` está en `.gitignore`): solo se versionan
    el generador, `schema.json`, el manifiesto, el código y el README. Los CSV son
    datos públicos del landing y **pueden contener datos de contacto públicos** (en
    este extracto aparecen un correo y un nombre), así que la carpeta no se comparte
    tal cual.
  - **Hecho cuando:** el script regenera los CSV de forma determinista, el manifiesto
    cuadra (filas válidas + inválidas = filas del CSV), y el kit se puede ejecutar
    en Free Edition sin tocar el proyecto pagado.
  - **Evidencia:** salida del script, manifiesto y fila en `evidence.md`. **0 €**
    (solo egress de la descarga).
  - **Estado (2026-11-02):** **hecha con 0 €** (0 runs, 0 clústeres, 0 SQL, 0
    pipelines en el workspace pagado). Kit en `candidate/`: generador, `schema.json`, manifiesto, CSV de
    **288 filas** (**280 válidas** + **8 inválidas inyectadas**: 4 `missing_key` + 4
    `missing_scraped_at`) y **471.643 B** de CSV (≤ 1 MB); `pipeline_declarative.py`,
    `mechanism_current.py`, README y tests locales (**8 passed**). Regeneración
    **byte a byte** y `sha256` del manifiesto comprobados. Los CSV **no están
    versionados** (`candidate/data/` en `.gitignore`). **Incidente T-09b
    (2026-11-02):** la primera ejecución en Free Edition falló con
    `[CONFIG_NOT_AVAILABLE.WITHOUT_SUGGESTION] Configuration landing_root is not
    available. SQLSTATE: 42K0I`: el kit usaba `spark.conf` como bolsa de parámetros
    y el módulo se ejecutó como notebook. Corrección: `run()` con argumentos
    **keyword-only** y fallback a widgets, sin `spark.conf` como bolsa de
    parámetros ni `__main__` (**0 €**). **Primera ejecución real del mecanismo
    actual en Free Edition (2026-11-02):** **77,7 s** la primera y **7,2 s** la
    segunda, `status` «Stopped» en las cuatro fuentes y `query_id` estable entre
    runs (id persistido en el checkpoint). Faltaba la cifra de filas: hasta esta
    mejora el resumen solo tenía `source`, `query_id` y `status`.
    `mechanism_current.stream_result` añade `num_input_rows`
    (`lastProgress["numInputRows"]`, **0** si no hubo lote), `batch_id`
    (`lastProgress["batchId"]`, **`None`** si no hubo lote) y `batches`
    (`len(query.recentProgress)`), leyendo solo el progreso del motor; la segunda
    ejecución debe mostrar `num_input_rows: 0` y `batch_id: None` **si no hubo
    ningún microbatch**; si Spark ejecuta un microbatch vacío, saldrá
    `num_input_rows: 0` con su `batch_id`. En ambos casos los conteos del destino no
    cambian, que es la evidencia de exactly-once. Tests del kit: **16 passed**.
    **0 €**. **`[NO VERIFICADO]`:** las capturas de `candidate/evidence/` y la
    duración total del pipeline; **ver el Estado de T-09b** (2026-11-02).

- [x] **T-09b — Ejecutar la demo en Free Edition y traer evidencia** (~60 min, la persona)
  - **Objetivo:** obtener la evidencia real de los dos mecanismos en el mismo
    entorno y con los mismos datos.
  - **Ejecución (la persona, en la cuenta Free Edition):** subir los CSV, crear los
    dos pipelines (o notebooks), ejecutar cada uno **dos veces** (la segunda sin
    ficheros nuevos) y capturar: grafo/linaje, event log, métricas de expectativas,
    tabla resultante (conteos por fuente, cuarentena por motivo), segunda ejecución
    con 0 filas, y duración de cada ejecución.
  - **Hecho cuando:** las dos ejecuciones de cada mecanismo están en verde, la
    segunda no ingiere filas ni mueve los conteos, la cuarentena coincide con el
    manifiesto y constan en `evidence.md` los **conteos de destino y cuarentena**,
    las **filas de la repetición** (`num_input_rows`/`batch_id` o «0 registros»),
    las **duraciones** de cada ejecución y el **`query_id`/`batch_id`** por fuente.
    Las **capturas** de `evidence/` del kit son **evidencia complementaria
    opcional**, marcada `[NO VERIFICADO]` mientras no estén; cuando se aporten,
    deben guardarse saneadas (sin rutas locales ni correos).
  - **Evidencia:** conteos, filas de la repetición, duraciones, `query_id`/`batch_id`
    y fila en `evidence.md`; capturas si se aportan. **0 €.**
  - **Nota:** si `AUTO CDC` no está disponible en Free Edition, se aplica la
    contingencia de `plan.md` §8.3 y se documenta.
  - **Estado (2026-11-02):** **hecha con 0 €** en la cuenta **Free Edition** de la
    persona (0 runs de dev, 0 clústeres, 0 SQL, 0 pipelines en el workspace pagado).
    **Mecanismo declarativo** (`workspace.bronze_lab`, **2 ejecuciones**): run 1
    **completado**, con «2 expectations» fallidas por fuente; por fuente
    `_raw`/`_valid`: `indeed` **103/101**, `infojobs` **124/122**, `linkedin`
    **49/47**, `multi_site` **12/10**; `_quarantine` **2** por fuente y tablas SCD1
    `indeed`/`infojobs`/`linkedin`/`multi_site` con **101/122/47/10** filas
    «updated»; run 2 **completado sin registros** en todas las tablas («Output
    records -»). Conteos posteriores: destino **101/122/47/10** (`indeed` 101,
    `infojobs` 122, `linkedin` 47, `multi_site` 10); cuarentena **2 por fuente**
    (1 `missing_key` + 1 `missing_scraped_at`). **Mecanismo actual**
    (`workspace.bronze_current`, **3 ejecuciones**): run 1 **77,7 s** y run 2
    **7,2 s**, cuatro streams «Stopped»; run 3 (con el resumen nuevo)
    `num_input_rows = 0`, `batch_id = 1`, `batches = 1` en las cuatro fuentes
    —microbatch vacío, el caso previsto en la nota de T-09— y `query_id` estable
    entre ejecuciones (id persistido en el checkpoint). Conteos: destino
    **101/122/47/10**; cuarentena **1 `missing_key` + 1 `missing_scraped_at`** por
    fuente.
    **Conclusión:** los dos mecanismos dan **paridad con el manifiesto** del kit
    (101/122/47/10 de destino y 2 de cuarentena por fuente, una por motivo) y
    **exactly-once** en ambos (el run de repetición no añade filas ni duplica: 0
    registros en el declarativo y `num_input_rows = 0` sin cambios de conteo en el
    actual). La demo valida **semántica, conteos y cuarentena**; el contrato de
    columnas queda pendiente del diff entre esquemas. **No** mide tiempo ni coste
    de producción: Free Edition es serverless gratuito y sus duraciones no son
    comparables con el run real; el coste de producción sigue siendo estimación
    sujeta a aprobación.
    **`[NO VERIFICADO]`:** los ficheros de `candidate/evidence/`
    (`declarative-run1-graph.png`, `declarative-run1-expectations.png`,
    `declarative-run1-eventlog.png`, `declarative-run2-zero.png`,
    `current-run1-eventlog.png`, `current-run2-zero.png`, `counts.sql.txt`,
    `timings.md`), la **duración total del run del pipeline** y la **comparación de
    columnas entre los dos esquemas** (se pedirá aparte). **La decisión
    (adoptar / adoptar parcialmente / no cambiar) se escribe en T-10**, no aquí.

- [x] **T-10 — Decisión medida y cierre de la evaluación** (~60 min)
  - **Objetivo:** decidir **adoptar / adoptar parcialmente / no cambiar** con la
    tabla de `plan.md` §3 y §8.2, sin margen para la opinión.
  - **Hecho cuando:** la tabla tiene las 8 puntuaciones y las 4 puertas duras con su
    evidencia; la decisión, las piezas que se adoptan (si alguna) y la **estimación
    de coste de producción** (marcada `[NO VERIFICADO]`) están escritas en
    `design-streaming.md`; la persona la revisa y aprueba el coste si la decisión es
    adoptar.
  - **Evidencia:** tabla de decisión, revisión de la persona y fila en
    `evidence.md`. **0 €.**
  - **Estado (2026-11-02):** **hecha con 0 €** (0 runs, 0 SQL, 0 pipelines en el
    workspace pagado; T-09b creó **2 pipelines** en Free Edition). Puertas
    duras: **contrato ✓** (diff de `workspace.bronze_current.<fuente>` contra
    `workspace.bronze_lab.<fuente>`: `solo current`, `solo lab` y `tipo distinto`
    **vacíos en las cuatro fuentes**); **exactly-once ✓** (declarativo: segunda
    ejecución sin registros; actual: `num_input_rows = 0` y conteos sin mover);
    **cuarentena ✓** (destino 101/122/47/10 y 1 `missing_key` + 1
    `missing_scraped_at` por fuente en ambos); **rollback `[NO VERIFICADO]`** (no
    ensayado; no aplicable al no migrar). Puntuación resumida:

    | # | Criterio | Ganador |
    |---|---|---|
    | 1 | Tiempo del bronce | sin ganador |
    | 2 | Coste por ejecución | A |
    | 3 | Código propio | **B** |
    | 4 | Observabilidad | **B** |
    | 5 | Simplicidad operativa | A |
    | 6 | Madurez del runtime | A |
    | 7 | Riesgo de migración | A |
    | 8 | Reversibilidad | A |

    **B gana 2 de 8** (código propio y observabilidad; en el criterio 3 cumple la
    rama «elimina el `MERGE` manual», aunque la reducción de líneas sea ≈ 45 %), por
    debajo de las 5 exigidas: **no se migra** el pipeline declarativo. **Decisión:
    adoptar parcialmente** (mantener el mecanismo actual y adoptar la observabilidad
    de T-03–T-06). T-12 se cierra como **no aplicable** y T-13 despliega **solo
    observabilidad**. La tabla completa con umbrales y evidencia está en
    `design-streaming.md` §9. Tiempo y coste de producción del declarativo:
    `[NO VERIFICADO]`, no aprobado. **0 €.** **Aprobada por la persona el
    2026-11-02** (en conversación): mantener el mecanismo actual y adoptar la
    observabilidad; no migrar. **Umbral de coste (2026-11-02):** el **1,00 €/mes** se
    mantiene como referencia y aviso, no como regla dura; superarlo abre revisión y
    no bloquea, y el techo real es el **presupuesto de producción de 3 €/mes de la
    009**.

## 5. Validación y producción

- [ ] **T-11 — Runs 2 y 3 en dev: repetición e idempotencia** (~30 min de persona)
  - **Objetivo:** demostrar exactly-once y que las métricas no se duplican ni
    mienten: un run con lote nuevo y otro inmediato sin ficheros.
  - **Hecho cuando:** el run 2 ingiere lo pendiente con `numInputRows > 0` en al
    menos una fuente; el run 3 deja `numInputRows = 0` por fuente (sin `MERGE` de
    bronce) y sin cambios de conteos en silver, oro ni exports —la idempotencia de
    silver ya está probada en la 010—, con **una sola fila** por run en
    `ops.run_metrics`; la tarea final no falla; run ≤ 1350 s; coste anotado.
  - **Evidencia:** ids de los dos runs, resúmenes, `ops.run_metrics` (dos filas
    distintas), tiempos, coste y fila en `evidence.md`. **~0,40 €.**

- [x] **T-12 — (Condicional) Migración del mecanismo ganador en producción** (~60 min + ventana)
  - **Objetivo:** si T-10 decide adoptar, llevar el mecanismo a producción con
    cutover verificado y vuelta atrás; si decide no cambiar, cerrar con el motivo.
  - **Si se adopta:** backup/`RESTORE` disponible, `pause_status: PAUSED`, deploy con
    el pipeline en `resources/`, migración de las cuatro fuentes y conmutación
    (tablas nuevas gestionadas + `bronze_legacy` + ruta canónica), verificación de
    conteos, reanudación y run programado. Rollback probado en dev y documentado en
    `cutover.md` propio.
  - **Si no se adopta:** la tarea se cierra con el motivo medido, **sin gasto** y sin
    tocar producción.
  - **Aprobación previa:** el `.yml`, el plan de migración y el **coste de
    producción** (fuera del techo de dev).
  - **Hecho cuando:** el mecanismo decidido está en producción con rollback
    disponible y el run programado en verde, o la decisión «no cambiar» está
    registrada.
  - **Evidencia:** deploy, ids, conteos, rollback ensayado y fila en `evidence.md`.
  - **Estado (2026-11-02): no aplicable.** T-10 decide **no migrar** el pipeline
    declarativo (B gana 2 de 8; ver `design-streaming.md` §9), así que no hay
    cutover que ejecutar. Se cierra con el motivo medido, **sin gasto** (0 €) y sin
    tocar producción; el texto condicional de arriba se conserva por si una revisión
    futura cambia la decisión.

- [ ] **T-13 — Producción: run programado, dashboard y warehouse** (~30 min)
  - **Objetivo:** comprobación real de la spec: el run semanal se explica solo.
  - **Hecho cuando:** el primer run programado tras el deploy cierra las 9 tareas
    (8 de negocio + cierre), deja su fila en `ops.run_metrics` con `data_date`
    correcta y `checks` en verde; el dashboard publica con la fecha correcta; el SQL
    warehouse queda `STOPPED` antes y después; las health rules están activas en el
    job desplegado.
  - **Evidencia:** id del run, fila de `ops.run_metrics`, id del workflow del
    dashboard, estado del warehouse, coste en `docs/costs.md` y fila en
    `evidence.md`. **0 € de dev** (coste de producción, dentro de la cadencia).

## 6. Cierre

- [ ] **T-14 — Cierre de la spec**
  - Notas `docs/databricks-de/notes/03-streaming.md` y `06-observability.md`
    escritas con la plantilla del índice (qué se practicó, conceptos, comandos,
    errores, dudas) y el índice actualizado.
  - Roadmap (calendario, bitácora y estado de la semana 8) y `docs/costs.md` al día;
    RUNBOOK con la sección de monitorización.
  - Suites completas en verde; comprobación real registrada (T-13).
  - **Hecho cuando:** decisión medida cerrada (adoptar/no cambiar) y verificada,
    run programado en verde, gasto de dev ≤ 2,0 € y merge `--no-ff` a `main` con tag
    `spec-011`.
