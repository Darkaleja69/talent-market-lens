# Plan técnico: mecanismo de ingesta en streaming y observabilidad del run

> Borrador del **2026-11-01**, rama `spec/011-streaming-observability` (desde
> `main`, `c9c4a19`), worktree `talent-market-lens-011`.
>
> Regla de oro: **ningún cambio de mecanismo sin tabla de criterios y números**.
> El bronce ya funciona y está probado; sustituirlo cuesta una migración de tablas
> gestionadas por pipeline y un cutover. La carga de la prueba la tiene el
> candidato, no el statu quo.

## 1. Objetivo y límites

Dejar la ingesta semanal con el **mejor mecanismo medido** (adoptar, adoptar
parcialmente o no cambiar) y hacer que **cada run se explique y se vigile solo**,
sin despertar el SQL warehouse. No se tocan silver, oro, exports ni el dashboard;
el contrato de bronce (columnas, claves y significado) es intocable salvo que el
candidato lo conserve y el cutover esté probado.

## 2. Estado de partida

### 2.1. Mecanismo actual

| Fuente | Ficheros en landing | Checkpoint | Destino | Cuarentena |
|---|---|---|---|---|
| indeed | 1 Parquet por run | `_checkpoints{_dev}/indeed/ckpt` | `bronze.indeed` | `bronze.indeed_quarantine` |
| infojobs | muchos pequeños (1 por ciudad/keyword) | ídem | `bronze.infojobs` | `bronze.infojobs_quarantine` |
| linkedin | 1 acumulativo renombrado | ídem | `bronze.linkedin` | `bronze.linkedin_quarantine` |
| multi_site | 1 unificado renombrado | ídem | `bronze.multi_site` | `bronze.multi_site_quarantine` |

Cada consulta: `cloudFiles` (Parquet) → `_ingest_date = current_date()` →
`_source_file = _metadata.file_path` → `availableNow` → `foreachBatch` (dedup por
clave, cuarentena por `_row_hash`, `MERGE` con evolución de esquema) →
`awaitTermination` de las cuatro. **Sin resumen publicado.**

### 2.2. Números medidos (010)

- Bronce **113–134 s** de ejecución; **382–383 s** de arranque del clúster.
- Run completo **816–985 s** en dev y **747–915 s** en producción; timeout 1800 s.
- Lote real: `MERGE` con **316 / 850 / 1.788 / 728** filas actualizadas,
  `inserted = deleted = 0`.
- Coste: dev **0,17–0,24 €**; producción **0,20–0,24 €**; SQL **~0,43 €**/despertar.

### 2.3. Observabilidad

Email en fallo, historial por API, frescura del dashboard en CI (8 días) y nada
más: **sin resumen de bronce, sin tabla de métricas, sin health rules y sin
`system.*`**. El hueco de `numInputRows` está anotado desde la 008 T-05.

### 2.4. Reconocimiento gratuito (2026-11-01)

- Catálogo `system` disponible con `lakeflow`, `billing`, `compute`, `access`,
  `query` y `storage` (API de Unity Catalog, 0 €).
- CLI 1.21.0; el esquema del bundle admite `health.rules[]` (clave `health` con la
  lista `rules`, no `health_rules`; ver T-01)
  (`RUN_DURATION_SECONDS`, `STREAMING_BACKLOG_*`).
- PySpark local **4.2.0** (banco local viable); `delta-spark` está instalado como
  paquete, pero el **`MERGE` local no es viable** en este host (la 010 T-04 no pudo
  ni ejecutar el plan por falta de `winutils.exe`/`hadoop.dll`). El banco local
  prueba semántica de streaming y funciones puras, **no** Delta.
- `target prod` estaba en **`PAUSED`** desde la 010 T-09: había que reponerlo
  (repuesto el 2026-11-02 en T-01).

### 2.5. Lo que no se sabe

- Si la cuenta Free Edition permite `AUTO CDC`, qué límites de UI tiene y cuánto
  tarda una ejecución con el extracto.
- Si `AUTO CDC` reproduce exactamente el contrato (claves por fuente, secuencia por
  instante de captura, cuarentena con motivo y `_row_hash`).
- Cuánto pesa cada fase del bronce (listado de ficheros, dedup, cuarentena,
  `MERGE`, commit) y si el `MERGE` es el dominante o lo es el listado de Auto
  Loader.
- Si el event log del job persiste lo suficiente para diagnosticar sin Spark UI.
  Eso es exactamente T-01 y no se afirma nada antes.

## 3. Criterios de decisión (fijados antes de medir)

**Puertas duras (si falla una, no se adopta):**

| Puerta | Regla |
|---|---|
| Contrato | Mismas columnas de negocio, mismas claves por fuente (`job_key`; `id_oferta`; `job_id`; `site + job_id`) y mismo significado (gana la captura más reciente, `_ingest_date` como desempate); ningún campo nuevo obligatorio para silver/oro/exports |
| Exactly-once | Segunda ejecución sin ficheros nuevos ⇒ **0 filas ingeridas** y 0 escrituras de negocio |
| Cuarentena | Misma cobertura: mismas filas inválidas por fuente y motivo; **0 filas inválidas** llegan al destino |
| Rollback | Vuelta atrás probada en dev y documentada: `RESTORE`/backup + pausa y reanudación del job en **≤ 10 min** |

**Criterios puntuables (se adopta si gana ≥ 5 de 8 y no pierde ninguna puerta):**

| # | Criterio | Umbral |
|---|---|---|
| 1 | Tiempo del bronce | ≤ **134 s** de ejecución (extremo superior medido) y run total ≤ **1350 s** |
| 2 | Coste por ejecución | ≤ **0,24 €** en dev y ≤ **1,00 €/mes** en producción (dentro del techo de 3 €/mes) |
| 3 | Código propio | Reduce ≥ **50 %** las líneas del handler o elimina el `MERGE` manual |
| 4 | Observabilidad | Event log/métricas por flujo y grafo nativos, o paridad con el resumen instrumentado de T-03 |
| 5 | Simplicidad operativa | Un artefacto menos que mantener (sin `foreachBatch` propio) o igual |
| 6 | Madurez del runtime | Sin *preview* obligatorio para la ruta crítica |
| 7 | Riesgo de migración | Migración de las 4 fuentes en **≤ 1 ventana semanal**, sin tocar silver |
| 8 | Reversibilidad | Vuelta al mecanismo actual sin pérdida de datos ni de historial |

**Nota (T-01) al criterio 2:** el techo de **1,00 €/mes** es la referencia de
producción de la 009; con **0,20–0,24 €** por run semanal queda en
**0,87–1,04 €/mes**. Si la medición cae en la banda alta (> 1,00 €/mes), T-10 lo
decide con la persona.

Empate ⇒ **no cambiar**. «Adoptar parcialmente» solo si las piezas adoptadas
(expectativas, event log, métricas) se justifican por sí mismas y no arrastran la
migración de tablas.

## 4. Candidatos

### A — Actual (línea base)

Auto Loader + `foreachBatch`: dedup en Python, cuarentena en tabla y `MERGE`
manual. Ventaja: probado (008/010), control total, sin superficie nueva. Desventaja:
código propio, observabilidad manual, sin expectativas nativas.

### B — Lakeflow pipelines declarativo (Free Edition)

Pipeline en una cuenta Free Edition aparte, sobre el extracto CSV, con:

- lectura `cloud_files` sobre los CSV subidos a un Volume
  (`/Volumes/.../landing/<fuente>/`);
- `expectations` para registrar calidad (y split explícito para enrutar inválidas a
  `bronze_lab.<fuente>_quarantine`);
- `AUTO CDC` SCD1 con **claves por fuente** y `sequence_by` = el instante de
  captura de cada fuente (`scraped_at`, `fecha_scraped`), no `_ingest_date`
  (constante por lote; solo desempata);
- event log, grafo y métricas nativas por flujo.

**Límite técnico que pesa en la decisión:** las tablas destino de `AUTO CDC` son
**gestionadas por el pipeline**; no se puede aplicar sobre las `bronze.<fuente>`
actuales. La adopción exige tablas nuevas + conmutación (renombrar las actuales a
`bronze_legacy` y publicar la ruta canónica) y un cutover con rollback. `sinks` es
append-only, así que no sirve para conservar las tablas actuales.

### C — Híbrido (adopción parcial)

Mantener el mecanismo y adoptar solo lo declarativo que aporte sin migrar: semántica
de expectativas en el handler, event log propio (`spark.eventLog.enabled`) y las
métricas del run de este plan. Es el resultado natural de «no cambiar» + T-03/T-05.

### Descartados de entrada

- **Streaming continuo 24/7:** clúster permanente; rompe el techo de 3 €/mes.
- **Dedup con estado (`flatMapGroupsWithState`):** sustituye una tabla por estado;
  más frágil y sin beneficio a este volumen.
- **Sinks Delta:** append-only; no reproduce el upsert.
- **Notificaciones de ficheros (Event Grid):** menos listado, pero añade
  infraestructura de Azure para ahorrar segundos a este volumen.

## 5. Banco local y práctica del dominio 3

Módulo `databricks_notebooks/streaming_lab.py` (didáctico, **no** se engancha al
job) con escenarios locales en `local[2]` y sumideros en `tmp`:

1. **Checkpoint y reinicio:** matar y relanzar la consulta; el segundo arranque no
   reprocesa (`numInputRows = 0`).
2. **Microbatch:** `trigger(availableNow=True)` frente a `trigger(once=True)` y
   `processingTime`; qué batches genera cada uno.
3. **Watermark y estado:** `groupBy(window(...)).count()` con evento tardío; fila
   descartada por watermark y `stateOperators` en `lastProgress`.
4. **Exactly-once:** sumidero idempotente (clave natural) frente a append;
   duplicados al reprocesar sin checkpoint.
5. **`foreachBatch`:** por qué el `MERGE` del proyecto es idempotente y qué pasaría
   con `append`.

Cada escenario, con test en `databricks_notebooks/tests/test_streaming_lab.py` y
una tabla de tiempos local. **0 €.**

## 6. Instrumentación del streaming (sin pasadas extra)

- **Fuente de verdad:** `query.lastProgress` y `query.recentProgress` tras
  `awaitTermination`. Campos: `batchId`, `numInputRows`, `inputRowsPerSecond`,
  `processedRowsPerSecond`, `durationMs` (`addBatch`, `triggerExecution`,
  `walFetch`/`commit` si aparecen), `stateOperators` y `sources[].description`.
- **Backlog:** si el motor expone ficheros pendientes en `sources[].metrics`, se
  publica; si no, se documenta `null` y se mide el backlog por la diferencia entre
  ficheros del landing y `batchId` (solo en el diagnóstico, no en el run).
- **Cuarentena:** el handler ya escribe; se cuenta **antes** de escribir el frame
  pequeño (`new_rows` cacheado) — coste O(filas inválidas), no del lote.
- **Prohibido en el resumen:** `count()` del lote, `DESCRIBE HISTORY` por fuente ni
  lecturas nuevas de la landing. El test de contrato compara el número de acciones
  del camino actual y del nuevo (mismo lote de prueba).
- **Contrato:** `bronze_summary` = `{"streams": [{source, batch_id, num_input_rows,
  duration_ms, checkpoint, quarantined_rows}], "config": {...}}`; aditivo y
  versionado.

## 7. Observabilidad

### 7.1. Tabla `ops.run_metrics`

Esquema `<catalog>.ops` (creado por CLI, gratis) con **una tabla y un escritor**,
para no competir por escrituras ni tocar ocho notebooks:

| Columna | Tipo | Nota |
|---|---|---|
| `run_id` | STRING | id del run del job (clave del `MERGE`) |
| `job_id` | STRING | id del job |
| `started_at` / `finished_at` | TIMESTAMP | reloj del cierre |
| `duration_s` | DOUBLE | desde el inicio del run hasta el cierre |
| `bronze_rows` | BIGINT | filas ingeridas en el run (suma por fuente) |
| `quarantine_rows` | BIGINT | filas nuevas en cuarentena |
| `data_date` | DATE | `data_date` del `meta.json` del export web |
| `data_age_days` | INT | antigüedad frente a UTC |
| `status` | STRING | `success` / `failed` |
| `checks` | STRING | JSON con cada comprobación y su resultado |
| `metrics` | STRING | JSON del resumen de bronce por fuente |
| `recorded_at` | TIMESTAMP | auditoría |

`MERGE` por `run_id` (idempotente). **Duraciones y estados por tarea no se
duplican aquí:** salen de `system.lakeflow.job_task_run_timeline`, que es la
fuente de verdad de la plataforma y se consulta desde el clúster.

### 7.2. Tarea final

`Publish_Run_Summary` con `run_if: ALL_DONE`, depende de las 8 tareas:

1. lee el resumen del bronce de `dbutils.jobs.taskValues` (lo publica la tarea de
   bronce; si no está, el check falla);
2. lee `meta.json` del export web (el mismo `data_date` que usa el dashboard);
3. calcula frescura y busca ficheros pendientes **anteriores al inicio del run**
   (listado barato de landing y del directorio `commits/` del checkpoint, sin leer
   Parquet; un fichero que llega durante el run no cuenta como pendiente);
4. escribe/actualiza `ops.run_metrics` con `checks`;
5. **falla** si falta el resumen de bronce, si `data_date` > 8 días o si hay
   ficheros pendientes sin ingerir con lote vacío.

`system.lakeflow` puede tardar unos minutos en reflejar el run en curso: el cierre
**no** depende de ella (solo el diagnóstico y el RUNBOOK), así que su latencia no
puede tumbar ni bloquear el run.

### 7.3. Alertas

- `health.rules[]` (clave `health` del bundle, no `health_rules`; ver T-01):
  `RUN_DURATION_SECONDS` > **1500 s** (por debajo del timeout de 1800) con
  `on_duration_warning_threshold_exceeded` a la persona; `on_failure` ya existe.
- `STREAMING_BACKLOG_*` **no** se usan: se calculan sobre una media de 10 minutos y
  estas consultas duran segundos; se documenta el motivo.
- Frescura: workflow del dashboard (8 días) + tarea final (8 días) — dos superficies
  distintas, ninguna con warehouse.
- **Rechazado:** alertas SQL programadas (~0,43 € por despertar, recurrente).

### 7.4. System tables y Query Profile

- Grants y consultas por API/CLI (gratis) en T-01; lectura de `system.*` desde el
  clúster del job en el día a día.
- **Una** sesión SQL (< 10 min) para validar permisos, ejecutar las consultas
  versionadas (`system.lakeflow.job_run_timeline`,
  `system.lakeflow.job_task_run_timeline`, `system.billing.usage`) y capturar
  Query Profile de una de ellas. Warehouse `STOPPED` antes y después.

### 7.5. Event log

`spark.eventLog.enabled: true` con destino en ADLS (`landing/_eventlogs/`) para
conservar el historial más allá de la retención del clúster. Una línea de
`spark_conf` en el job cluster; se mide su coste (bytes) antes de fijarlo.

## 8. Evaluación del candidato declarativo (Free Edition + extracto CSV)

### 8.1. Dónde y cómo

- **Cuenta Free Edition aparte** (decisión del 2026-11-02), con perfil de CLI propio
  (`free`). No se crea ningún pipeline `serverless` en el workspace pagado. Free
  Edition es serverless por definición, pero **gratuita**: se evita el DBU, no el
  serverless. El PAT del workspace pagado **no** se copia a la cuenta de
  laboratorio.
- **Datos:** `specs/011-Streaming-And-Observability/candidate/data/<fuente>.csv`,
  generados desde **un Parquet real del landing** por fuente (descarga con la SAS,
  0 €), con muestreo, truncado de textos largos, filas inválidas inyectadas
  (`missing_key`, `missing_scraped_at`) y `manifest.json` con los conteos esperados.
  Se suben a un Volume de Free Edition.
- **Dos mecanismos, mismo entorno y mismos datos:** el declarativo (streaming table
  + expectativas + `AUTO CDC`) y el actual adaptado al CSV (Structured Streaming +
  `foreachBatch` + `MERGE`, reutilizando `Upsert_Job_Offers.py`). Así la comparación
  no mezcla entornos.
- Sin schedule; cada ejecución es manual y se para al terminar.

### 8.2. Qué se mide y qué no

| Dimensión | Cómo se mide en Free Edition | Límite |
|---|---|---|
| Contrato (columnas, claves, significado) | diff de esquema y conteos contra el CSV y la tabla del mecanismo actual | no cubre las 4 tablas de producción completas |
| Exactly-once | segunda ejecución sin ficheros nuevos ⇒ 0 filas | igual en ambos mecanismos |
| Cuarentena | conteos por motivo (`missing_key`, `missing_scraped_at`) | paridad sobre el extracto |
| Tiempo | duración de cada ejecución en Free Edition | **no extrapolable** a producción (datos y compute distintos) |
| Coste | 0 € (Free Edition) | el coste de producción queda como **estimación `[NO VERIFICADO]`** |
| Código propio | líneas del handler frente a líneas del pipeline | — |
| Observabilidad | event log, grafo, métricas de expectativas (capturas) | la UI de Free Edition puede limitar algunas vistas |
| Migración/rollback | plan escrito y ensayo en el extracto | ensayo real exige producción |

**Regla de adopción con esta limitación:** adoptar exige (a) las cuatro puertas
duras en verde en Free Edition, (b) una **estimación de coste de producción**
aprobada por la persona (tarifa de serverless o clúster equivalente, marcada
`[NO VERIFICADO]`) y (c) cutover con rollback en T-12. Si (b) no es creíble o (a)
falla, la decisión es «adoptar parcialmente» o «no cambiar», documentada.

### 8.3. Contingencias

- Si en Free Edition `AUTO CDC` no está disponible: se prueba `append` + dedup en
  tabla posterior; si tampoco, «adoptar parcialmente» (expectativas y event log) o
  «no cambiar».
- Si la cuota de Free Edition corta la sesión: se reanuda al día siguiente; no hay
  coste, pero se anota en la evidencia.
- Un fallo por defecto de código se corrige en local y se repite la ejecución
  (0 €).

## 9. Presupuesto

| Tarea | Runs dev | SQL | Free Edition | Coste |
|---|---|---|---|---|
| T-01 reconocimiento | 0 | 0 | 0 | 0 € |
| T-02 banco local | 0 | 0 | 0 | 0 € |
| T-03 resumen del bronce | 0 | 0 | 0 | 0 € |
| T-04 tabla `ops.run_metrics` | 0 | 0 | 0 | 0 € |
| T-05 tarea final y frescura | 0 | 0 | 0 | 0 € |
| T-06 health rules y runbook | 0 | 0 | 0 | 0 € |
| T-07 sesión SQL | 0 | **1** | 0 | ~0,43 € |
| T-08 run 1 (A, instrumentado) | **1** | 0 | 0 | ~0,20 € |
| T-09 kit CSV + demo (Free Edition) | 0 | 0 | **5 ejecuciones** (2 del declarativo + 3 del actual) | 0 € |
| T-10 decisión | 0 | 0 | 0 | 0 € |
| T-11 runs 2 y 3 (repetición) | **2** | 0 | 0 | ~0,40 € |
| T-12 migración (condicional) | 0 | 0 | 0 | presupuesto de producción |
| T-13 producción y comprobación | 0 | 0 | 0 | 0 € de dev |
| T-14 cierre | 0 | 0 | 0 | 0 € |
| **Total dev** | **3** | **1** | **5** | **~0,95–1,15 €** (techo 2,0 €) |

Reglas:

- Cada run y cada sesión se anota en `docs/costs.md` con motivo, id y coste. El
  extracto CSV solo cuesta el egress de la descarga (céntimos, no medido).
- La migración a producción (T-12) se paga con el presupuesto de producción de la
  009 y **exige aprobación explícita** antes de gastar.
- Un run fallido por defecto de código se corrige en local y consume presupuesto.
- 0 sesiones SQL recurrentes; solo T-07, agrupada en < 10 min.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Free Edition sin `AUTO CDC` o con la UI limitada | Se comprueba al crear el pipeline (0 €); si falla, `append` + dedup y decisión documentada |
| La demo en Free Edition no representa el tiempo ni el coste de producción | Se mide semántica y contrato; coste de producción como estimación aprobada antes de migrar |
| El extracto CSV no cubre el esquema completo de las cuatro tablas | El kit conserva todas las columnas del Parquet real (textos largos truncados) y el manifiesto lo declara; la paridad total se valida en el cutover |
| Cuota de Free Edition agota la sesión | Se reanuda al día siguiente; 0 € y se registra en la evidencia |
| `AUTO CDC` no reproduce el contrato (secuencia, cuarentena, `_row_hash`) | Paridad fila a fila en dev antes de decidir; si falla, adopción parcial o no cambiar |
| Migración de tablas gestionadas por pipeline rompe `RESTORE`/historial | Tablas nuevas + `bronze_legacy` + rollback probado en dev; cutover en ventana semanal |
| Las métricas añaden acciones y encarecen el run | Test de contrato de acciones; bronce ≤ 134 s medido en T-08/T-11 |
| `run_id`/`job_id` no accesibles desde el notebook | Fallback a widgets o `taskValues`; se verifica en T-04 |
| Health rules no cubren frescura | La tarea final y el workflow del dashboard sí; se documentan las dos superficies |
| Local (Spark 4.2.0) y DBR (17.3) difieren en streaming | El banco local prueba semántica; la validación real es T-08/T-11 |
| `target prod` sigue `PAUSED` y no hay run que valide | T-01 repone `UNPAUSED` con deploy; sin eso, T-13 no se puede cerrar |
| Presupuesto desbordado | Los 3 runs y el SQL están asignados por tarea; la reserva no existe |
| El candidato gana y la migración se come la ventana | Plan de cutover con pausa, backup y vuelta atrás; si no cabe, se aplaza a spec propia con la decisión ya tomada |

## 11. Secuencia

1. **Reconocimiento y criterios (T-01):** grants, `system.*`, soporte de
   pipelines/health rules, event log y línea base del bronce; reponer `UNPAUSED`.
   Revisión independiente.
2. **Banco local (T-02):** checkpoint, microbatch, watermark/estado y exactly-once
   con tests. 0 €.
3. **Observabilidad del streaming y del run (T-03–T-06):** resumen del bronce,
   `ops.run_metrics`, tarea final con frescura, health rules y runbook.
4. **Sesión SQL única (T-07):** `system.*`, Query Profile y coste.
5. **Evaluación (T-08–T-10):** run 1 instrumentado (A), candidato declarativo (B) y
   decisión con la tabla de criterios.
6. **Validación en dev (T-11):** repetición e idempotencia con el mecanismo decidido.
7. **Producción (T-12/T-13):** migración si procede, deploy, run programado,
   dashboard y warehouse `STOPPED`.
8. **Cierre (T-14):** notas `03-streaming.md` y `06-observability.md`, roadmap,
   coste, suites y comprobación real.
