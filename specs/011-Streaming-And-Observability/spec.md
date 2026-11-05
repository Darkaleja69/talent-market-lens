# Especificación funcional: mecanismo de ingesta en streaming y observabilidad del run

> **Borrador (2026-11-01).** Rama `spec/011-streaming-observability` creada desde
> `main` (`c9c4a19`) en el worktree `talent-market-lens-011`. El roadmap asigna a
> esta spec los **dominios 3 (streaming)** y **6 (observabilidad)** del temario, con
> ventana del **30 nov al 6 dic**; el trabajo se adelanta a petición de la persona.
> El presupuesto que la 009 le asigna es **3 runs de dev + 1 sesión SQL ≤ 2,0 €**.
>
> Nace de dos huecos medidos: (a) el mecanismo de ingesta del bronce **nunca se ha
> comparado** con la alternativa declarativa de la plataforma, y (b) el bronce **no
> publica resumen**, así que `numInputRows` y la salud del checkpoint no son
> legibles (hueco anotado en la 008 T-05 «para la 010» y **no cerrado allí**).

## Contexto y objetivo

Hoy el bronce son **cuatro consultas de Auto Loader independientes** —una por
fuente— con `trigger(availableNow=True)`, checkpoint y `schemaLocation` en
`abfss://landing/_checkpoints{_dev}/<fuente>/`, `foreachBatch` con dedup por clave en
Python, cuarentena en tabla Delta y `MERGE WITH SCHEMA EVOLUTION` por lote. La 010
midió su coste dentro del run: **bronce 113–134 s de ejecución** más **382–383 s
de arranque** del clúster (el 41–47 % del run), con lotes de **decenas a ~2.000
filas** por fuente. El mecanismo funciona, es idempotente y está probado, pero no
se ha medido frente a la alternativa declarativa (Lakeflow pipelines: streaming
tables, `expectations`, `AUTO CDC`) y su observabilidad es pobre: el run solo se
explica abriendo la API de runs o la Spark UI.

**Objetivo (resultado único):** que la ingesta semanal sea **el mejor mecanismo
disponible con evidencia, en producción**, y que **cada run se explique y se vigile
solo**, sin despertar el SQL warehouse. «Mejor» y «no cambiar» se demuestran con la
misma tabla de criterios; la decisión se toma antes de tocar producción y, si el
candidato gana, se migra producción dentro de esta spec con cutover y rollback.

Las dos mitades son un solo resultado: un mecanismo que no se puede medir no se
puede decidir, y unas métricas sin un mecanismo que las explique son solo
contadores. Además, el checkpoint, el microbatch, el estado y el exactly-once del
dominio 3 **son** la explicación de esas métricas; no es teoría aparte.

Y el encaje con el examen: el dominio 3 se practica **sobre esta ingesta y un
banco local**, y el dominio 6 sobre **este run** (event log, system tables, Query
Profile, health rules y alertas), no con juguetes.

## Estado de partida

### 2.1. Mecanismo actual (verificado en código)

| Pieza | Estado |
|---|---|
| Fuentes | 4 (`indeed`, `infojobs`, `linkedin`, `multi_site`), Parquet en `landing/<fuente>/` |
| Lectura | `cloudFiles` (Auto Loader) con `schemaLocation` por fuente |
| Trigger | `availableNow=True` (batch-like: consume lo pendiente y termina) |
| Checkpoint | `abfss://landing@joboffershns.../_checkpoints{_dev}/<fuente>/ckpt` |
| Escritura | `foreachBatch` → dedup por clave en Python → `MERGE` con evolución de esquema |
| Cuarentena | `<catalog>.bronze.<fuente>_quarantine`, append-only, dedup por `_row_hash` |
| Cierre | `awaitTermination` de las cuatro consultas (un fallo de MERGE tumba la tarea) |
| Resumen | **ninguno**: la tarea no llama a `dbutils.notebook.exit` |

### 2.2. Coste medido (010)

- Bronce: **113–134 s de ejecución** y **382–383 s de arranque** de clúster (dos runs).
- Run completo: **816–985 s en dev** y **747–915 s en producción**, con
  `timeout_seconds: 1800` y `max_concurrent_runs: 1`.
- Lote real de producción (T-09 de la 010): `MERGE` con **316 / 850 / 1.788 / 728**
  filas actualizadas y `inserted = deleted = 0`.
- Layout: 18 ficheros en bronce; `infojobs` es la fuente con más ficheros
  pequeños (5 × 0,43 MB).
- Coste: run de dev **0,17–0,24 €**; run de producción **0,20–0,24 €**; sesión SQL
  **~0,43 €** (2X-Small, 10 min facturables). Cero sesiones SQL en la 010.

### 2.3. Observabilidad actual

| Pieza | Estado |
|---|---|
| Fallo del job | email `on_failure` (`resources/elt.job.yml`) |
| Historial de runs | API de runs / UI de Jobs |
| Frescura publicada | workflow del dashboard: falla si `data_date` > **8 días** |
| Métricas por tarea | solo el JSON de las silvers y de los exports; **bronce sin resumen** |
| Tabla de métricas | **no existe** |
| Health rules | **ninguna** |
| `system.*` | **0 usos** en el código; `system.billing.usage` aparece solo en documentos |

### 2.4. Reconocimiento gratuito (2026-11-01, sin runs ni SQL)

- El catálogo `system` **existe y está poblado** en el workspace: esquemas
  `access`, `billing`, `compute`, `lakeflow`, `query`, `storage`, `serving`,
  `sharing`, `mlflow`, `information_schema` (lectura por API de Unity Catalog, 0 €).
- CLI **v1.21.0**; el esquema del bundle **admite `health.rules[]`** (clave
  `health` con la lista `rules`; corregido en T-01: no existe `health_rules`) con
  `RUN_DURATION_SECONDS` y los `STREAMING_BACKLOG_*` (estos últimos en *public
  preview* y pensados para streams continuos).
- PySpark local **4.2.0** (banco local viable para streaming y funciones puras; el
  `MERGE` local no es viable en este host por falta de `winutils.exe`, 010 T-04).
  *Actualización tras T-02: el host tiene `winutils.exe`/`hadoop.dll` en
  `%LOCALAPPDATA%\hadoop\bin` (fuera del repositorio), así que el banco local escribe
  Parquet y checkpoints. Con `HADOOP_HOME` y `bin` en el `PATH`, `delta-spark` 4.4.1 y
  `spark.sql.extensions`/`spark.sql.catalog.spark_catalog` explícitos, el `MERGE` de
  Delta también funciona en local (dos escrituras del mismo `run_id` dejan una fila).
  En Linux/CI no hace falta.*
- **Deuda heredada, no de esta spec:** el `target prod` de `databricks.yml` tenía
  `pause_status: PAUSED` desde el A/B de la 010 (T-09) (repuesto el 2026-11-02 en
  T-01). Sin reponerlo, el run del **8-nov** (comprobación posterior no bloqueante
  de la 010) no habría ocurrido y el job semanal quedaba parado.

## Decisiones (2026-11-01)

- **El resultado es una decisión medida, no un cambio.** «No cambiar» es un
  resultado válido y se documenta con el mismo rigor que una migración. La
  decisión se toma **con los umbrales fijados antes de medir**.
- **El contrato de bronce manda.** Mismas columnas, mismas claves por fuente
  (`job_key` en indeed, `id_oferta` en infojobs, `job_id` en linkedin y
  `site + job_id` en multi_site), mismo significado (gana la captura más reciente
  por instante de captura, con `_ingest_date` como desempate) y misma cuarentena
  (`missing_key`, `missing_scraped_at`). Cualquier cambio de mecanismo conserva ese
  contrato o no se adopta.
- **Lakeflow se evalúa en una cuenta Free Edition aparte, con un extracto CSV.**
  Decisión del 2026-11-02: no se crea ningún pipeline `serverless` en el workspace
  pagado (evita DBU de serverless). Free Edition **también es serverless**, pero es
  gratuita: lo que se evita es el coste, no el serverless. La demo valida
  **semántica, contrato y observabilidad**, **no** el tiempo ni el coste de
  producción; esos siguen siendo una estimación sujeta a aprobación.
- **El extracto CSV es el lote de comparación.** Se genera desde un Parquet real
  del landing por fuente (descarga con la SAS, 0 €), con filas inválidas inyectadas
  (`missing_key`, `missing_scraped_at`) y un manifiesto de conteos esperados. El
  kit incluye el mecanismo actual adaptado al mismo CSV para que la comparación sea
  en el mismo entorno y con los mismos datos.
- **`AUTO CDC` exige serverless o edición Pro/Advanced.** Free Edition es
  serverless por definición, así que es la vía natural; en el workspace pagado no
  se crea nada. Si en Free Edition `AUTO CDC` no estuviera disponible, se evalúa con
  flujo `append` + dedup posterior y se documenta la limitación.
- **Observabilidad en Delta, no en el warehouse.** Las métricas se escriben desde
  el propio job; `system.*` se lee desde el clúster del job (gratis) y se reserva
  **una única sesión SQL** para validar permisos y practicar Query Profile.
- **Las métricas no pueden tumbar el negocio.** La escritura de métricas es
  *best-effort* (un fallo se registra y no rompe la tarea); la **tarea final sí
  falla** si falta el resumen del bronce o si la frescura se rompe.
- **Alertas nativas y frescura en CI; alertas SQL descartadas.** Cada alerta SQL
  programada despierta el warehouse (~0,43 € por despertar) y rompe el techo de
  producción de 3 €/mes.
- **Coste acotado:** 3 runs de dev (~0,51–0,72 €) + 1 sesión SQL (~0,43 €) →
  **~0,95–1,15 €** dentro del techo de **2,0 €**; la demo de Free Edition y el
  extracto CSV cuestan **0 €** (solo egress de storage). La migración a producción,
  si la hay, se paga con el presupuesto de producción y requiere aprobación
  explícita.

## Requisitos funcionales

### RF-1 — Diagnóstico medido antes de decidir
**Criterio (EARS):** Cuando se aborde la mejora de la ingesta, el coste y el
comportamiento deberán estar atribuidos **por fuente y por fase** (listado de
ficheros, dedup del lote, cuarentena, `MERGE`, commit del checkpoint) con
evidencia del resumen instrumentado, del event log/Spark UI y del banco local;
ningún candidato podrá justificarse solo con una hipótesis.

### RF-2 — Resumen del streaming en la tarea de bronce
**Criterio (EARS):** Cuando termine la tarea de bronce, su salida deberá exponer
**por fuente** `numInputRows`, `batchId`, duración del microbatch y del trigger,
ficheros pendientes/backlog (si el motor lo expone), filas nuevas en cuarentena
del lote y estado del checkpoint, **sin lanzar acciones nuevas sobre la landing
ni agregaciones sobre el lote completo** (el conteo de cuarentena es de orden
O(filas inválidas)); el número de lecturas de la landing y el tiempo del bronce no
podrán crecer.

### RF-3 — Métricas del run persistidas en Delta
**Criterio (EARS):** Cuando se ejecute el job, deberá quedar **una fila por run** en
`<catalog>.ops.run_metrics` con `run_id`, `job_id`, marcas de tiempo, duración,
métricas de bronce por fuente (filas ingeridas y en cuarentena), `data_date` de los
exports y su antigüedad, y el resultado de las comprobaciones; **idempotente** por
`run_id` (repetir el run no duplica la fila) y escrita de forma que un fallo de la
propia métrica no pueda tumbar una tarea de negocio. Las **duraciones y estados por
tarea** deberán ser consultables desde `system.lakeflow` **sin** despertar el SQL
warehouse.

### RF-4 — Cierre del run y frescura
**Criterio (EARS):** Cuando termine el job, la tarea final deberá escribir la fila
de `ops.run_metrics` y **fallar** si el resumen de bronce no está disponible, si
`data_date` de los exports supera **8 días** o si hay ficheros pendientes en la
landing **anteriores al inicio del run** sin ingerir (lo que llega durante el run
no cuenta); cuando el job falle, la tarea final deberá ejecutarse igualmente
(`run_if: ALL_DONE`) y dejar constancia del fallo.

### RF-5 — Alertas y salud del run sin warehouse
**Criterio (EARS):** Cuando un run falle o supere el umbral de duración, deberá
notificarse **sin despertar el SQL warehouse**: health rules del job
(`health.rules[]` del bundle, T-01; `RUN_DURATION_SECONDS` con umbral ≤ **1500 s**,
por debajo del `timeout_seconds` de 1800) más la notificación de fallo existente;
la frescura seguirá cubierta por el workflow del dashboard. Las alertas SQL
programadas quedarán descartadas con su coste documentado.

### RF-6 — System tables consultables con coste acotado
**Criterio (EARS):** Cuando se consulten tablas `system.*`, deberá hacerse en
**una sola sesión** de SQL (< 10 min, ≤ 0,43 €) o desde el clúster del job, con
las consultas versionadas en el RUNBOOK y el resultado registrado; el SQL
warehouse deberá quedar **`STOPPED` antes y después**.

### RF-7 — Decisión medida sobre el mecanismo de ingesta
**Criterio (EARS):** Cuando se compare el mecanismo actual con el candidato
declarativo, la decisión —**adoptar**, **adoptar parcialmente** o **no cambiar**—
deberá sostenerse en una tabla con, como mínimo: contrato de tabla, exactly-once,
cuarentena, tiempo, coste por ejecución, líneas de código propio, observabilidad
nativa, riesgo de migración y rollback; con los umbrales fijados en `plan.md` §3
**antes** de medir. La comparación se hará **en Free Edition y sobre el mismo
extracto CSV** con los dos mecanismos; el tiempo y el coste de producción se
marcarán como **estimación `[NO VERIFICADO]`** y su adopción exigirá aprobación
explícita del coste.

### RF-8 — Si el candidato gana, producción migrada; si no, motivo escrito
**Criterio (EARS):** Cuando la decisión sea adoptar, el mecanismo deberá quedar
desplegado en producción con cutover verificado (pausa, deploy, migración o
backfill, reanudación y rollback disponible), el **run programado en verde**, el
dashboard publicado y el warehouse `STOPPED`; cuando la decisión sea no cambiar, la
tarea deberá cerrarse con el motivo medido y **sin gasto**.

### RF-9 — Presupuesto
**Criterio (EARS):** Cuando se cierre la spec, el gasto de dev no deberá superar
**3 runs + 1 sesión SQL** (~0,95–1,15 €) dentro del techo de **2,0 €** de la 009;
la demo en Free Edition y el extracto CSV no deberán costar nada (solo egress); la
migración a producción, si la hay, irá al presupuesto de producción con aprobación
explícita; cada gasto se anotará en `docs/costs.md` con motivo, id y coste.

### RF-10 — Práctica del temario y notas
**Criterio (EARS):** Cuando se cierre la spec, deberán existir
`docs/databricks-de/notes/03-streaming.md` y `06-observability.md`, escritas desde
lo medido, cubriendo checkpoint, trigger, watermark, estado, microbatch,
exactly-once, Structured Streaming frente a Lakeflow, y Spark UI, Query Profile,
system tables, event logs y monitorización del job.

## Requisitos no funcionales

- **Stack simple:** PySpark y Delta existentes; Lakeflow pipelines es una función
  de la plataforma, no una dependencia nueva del repositorio.
- **Lógica e interfaz:** las reglas medibles —construcción de la fila de métricas,
  consolidación del run, comprobación de frescura— viven en módulos `*.py` con
  tests; los notebooks y la tarea final solo coordinan.
- **Tests:** cada cambio añade o ajusta tests; las suites
  `databricks_notebooks/tests` y `scrapers-pipeline/tests` son gate de la spec.
- **Persistencia:** el crudo sigue en ADLS y el bronce en Delta; `ops` es derivado
  y reconstruible.
- **Sin cambio de semántica:** silver, oro y exports no cambian de contrato.
- **Idioma:** identificadores y código en inglés; documentos y mensajes en español.
- **Coste:** verificación local-first; 0 sesiones SQL recurrentes; el warehouse no
  se usa para monitorizar.

## Fuera de alcance

- Cambiar silver, oro, exports o el dashboard (specs 006/008/010).
- Streaming continuo 24/7 en producción: el trigger `availableNow` se mantiene
  salvo que la comparación demuestre lo contrario, y el coste de un clúster
  permanente queda descartado de entrada.
- Alertas SQL programadas y cualquier solución que despierte el warehouse de forma
  recurrente.
- Crear pipelines `serverless` **de pago** en el workspace del proyecto: la demo de
  Lakeflow se hace en Free Edition.
- Historial de cambios (CDF) del bronce, expiración técnica y dedup de negocio
  (008 y backlog del roadmap).
- Gobernanza, ACLs, row filters y column masks (012).
- Scrapers, landing y reparaciones (004/005).
- Cadencia y presupuesto mensual (009).

## Criterios de finalización

- **Decisión medida** (adoptar / adoptar parcialmente / no cambiar) escrita en
  `design-streaming.md` con la tabla de criterios y los umbrales de `plan.md` §3, a
  partir de la demo en **Free Edition** con el extracto CSV y con el coste de
  producción marcado como estimación.
- **Un run en dev** con el resumen del streaming completo por fuente, métricas en
  `ops` y salud del checkpoint; **una repetición** con `numInputRows = 0` y sin
  escrituras de negocio.
- **Frescura y alertas** probadas: la tarea final falla con `data_date` caducado
  (prueba con dato inyectado) y las health rules están desplegadas.
- **Una sesión SQL** con las consultas de `system.*` versionadas, Query Profile
  capturado, coste anotado y warehouse `STOPPED`.
- **Producción** con el mecanismo decidido y el run programado en verde, dashboard
  publicado con `data_date` correcta y warehouse `STOPPED`; o, si la decisión es no
  cambiar, el estado actual con la observabilidad desplegada y verificada.
- Suites en verde y **comprobación real** registrada.
- Notas `03-streaming.md` y `06-observability.md`, roadmap y `docs/costs.md` al día.
