# Diseño — observabilidad del run

> **Estado (2026-11-02, T-01):** inventario de fuentes cerrado con comprobaciones
> **gratuitas** (API de Unity Catalog, API de Jobs, listado de AzCopy y metadatos
> del warehouse). **0 runs, 0 clústeres, 0 consultas SQL, 0 pipelines.** Lo que
> exige leer una tabla de sistema se aplaza a **T-07** (una única sesión SQL) y va
> marcado `[NO VERIFICADO]`.
>
> Rama `spec/011-streaming-observability`, worktree `talent-market-lens-011`.
> Cada afirmación lleva su fuente (comando o fichero).

## 1. Inventario de fuentes de observabilidad disponibles

| Fuente | Qué aporta | Coste | Estado hoy (2026-11-02) | Uso en la 011 |
|---|---|---|---|---|
| `system.lakeflow.job_run_timeline` | una fila por rebanada de run: estado, trigger, duraciones de setup/cola/ejecución/limpieza | 0 € desde el clúster del job | existe; `SELECT` efectivo por metadatos; **sin consultar** | diagnóstico del run (T-07, RUNBOOK) |
| `system.lakeflow.job_task_run_timeline` | una fila por rebanada de **tarea**: `task_key`, estado, duraciones | 0 € desde el clúster del job | existe; `SELECT` efectivo por metadatos; **sin consultar** | duraciones y estados por tarea (no se duplican en `ops`) |
| `system.billing.usage` | consumo por SKU y ventana (`usage_quantity`, `usage_metadata`) | 0 € | existe; `SELECT` efectivo; **sin consultar** | coste por run (T-07) |
| `system.compute.*` | clústeres, nodos y warehouses (`clusters`, `node_timeline`, `warehouses`, …) | 0 € | existe; `SELECT` efectivo; **sin consultar** | atribución de coste y de arranque (T-07) |
| Email de fallo del job | aviso inmediato de run fallido | 0 € | activo (`on_failure`) | alerta base (ya existe) |
| API de Jobs (`jobs list-runs`, `jobs get-run`) | historial y JSON del run con tiempos por tarea | 0 € | en uso (010) | diagnóstico puntual |
| Workflow del dashboard (GitHub) | frescura publicada: falla si `data_date` > 8 días | 0 € | activo | frescura en CI |
| Event log de Spark | trazas, tareas, streaming progress | 0 € (bytes en ADLS) | **no configurado** | T-06 (decisión medida) |
| `ops.run_metrics` (Delta) | una fila por run con negocio, frescura y `checks` | 0 € | **no existe** | T-04/T-05 |

### 1.1 Permisos de `system.*` (API de Unity Catalog, 0 €)

Comandos usados (`databricks` CLI **1.21.0**, perfil `vs_code`):

```powershell
databricks api get "/api/2.1/unity-catalog/catalogs/system" --profile vs_code
databricks api get "/api/2.1/unity-catalog/tables?catalog_name=system&schema_name=<esquema>" --profile vs_code
databricks grants list-effective catalog system --profile vs_code -o json
databricks grants list-effective schema  system.<esquema> --profile vs_code -o json
databricks grants list-effective table   system.<esquema>.<tabla> --profile vs_code -o json
```

Resultado:

- El catálogo `system` existe (`catalog_type: SYSTEM_CATALOG`, owner `System user`,
  metastore `9be7f9ac-…`, `provisioning_info.state: ACTIVE`).
- A nivel de catálogo, el llamante tiene `USE_CATALOG` por **dos** vías:
  `account users` y `DB - RESERVED - account admins`.
- A nivel de los tres esquemas (`lakeflow`, `billing`, `compute`), la lista
  efectiva del llamante resuelve a **`DB - RESERVED - account admins`** con
  `SELECT` + `USE_SCHEMA`. Es decir, el permiso de lectura procede del rol de
  *account admin*; el llamante (`<usuario>`, grupos de workspace `admins` y
  `users`) obtiene la pertenencia a ese grupo reservado.
- La lista efectiva no muestra ningún privilegio para `account users` en los
  esquemas: `account users` solo aporta `USE_CATALOG` a nivel de catálogo.

**Tabla por tabla** (`grants list-effective`; «base» = dónde se verificó el grant):

| Esquema | Tabla | Grant efectivo | Base | Estado T-01 |
|---|---|---|---|---|
| `lakeflow` | `job_run_timeline` | `SELECT` (heredado de `system.lakeflow`) | tabla | **legible** |
| `lakeflow` | `job_task_run_timeline` | `SELECT` (heredado de `system.lakeflow`) | tabla | **legible** |
| `lakeflow` | `jobs` | `SELECT` (heredado de `system.lakeflow`) | tabla | **legible** |
| `lakeflow` | `job_tasks` | `SELECT` (heredado de `system.lakeflow`) | tabla | **legible** |
| `lakeflow` | `pipelines` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `lakeflow` | `pipeline_update_timeline` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `lakeflow` | `zerobus_ingest` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `lakeflow` | `zerobus_stream` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `billing` | `usage` | `SELECT` (heredado de `system.billing`) | tabla | **legible** |
| `billing` | `attributed_usage` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `billing` | `list_prices` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `compute` | `clusters` | `SELECT` (heredado de `system.compute`) | tabla | **legible** |
| `compute` | `node_timeline` | `SELECT` (heredado de `system.compute`) | tabla | **legible** |
| `compute` | `instance_events` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `compute` | `instance_pools` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `compute` | `node_types` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `compute` | `warehouse_events` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |
| `compute` | `warehouses` | `SELECT` + `USE_SCHEMA` | esquema | **legible** |

Ninguna tabla queda en **no legible** ni en **no comprobable sin SQL**: Unity
Catalog devuelve el grant efectivo. Lo que **no** está verificado es la **lectura
real** (que una consulta devuelva filas): eso es T-07, con una única sesión SQL.
Por eso todas las filas van con la advertencia de §2.

### 1.2 Existencia y esquema de las tablas de `lakeflow` (metadatos, sin consultarlas)

`databricks api get /api/2.1/unity-catalog/tables/system.lakeflow.<tabla>`:

- **`job_run_timeline`** — `MANAGED`, owner `System user`, **22 columnas**. Claves
  para el diagnóstico: `job_id`, `run_id`, `period_start_time`,
  `period_end_time`, `trigger_type`, `result_state`, `run_type`, `run_name`,
  `termination_code`, `termination_type`, `compute_ids`, `compute`,
  `setup_duration_seconds`, `queue_duration_seconds`,
  `execution_duration_seconds`, `run_duration_seconds`,
  `cleanup_duration_seconds`.
- **`job_task_run_timeline`** — `MANAGED`, owner `System user`, **18 columnas**.
  Claves: `job_id`, `run_id`, `job_run_id`, `parent_run_id`, `task_key`,
  `period_start_time`, `period_end_time`, `result_state`, `termination_code`,
  `termination_type`, `compute_ids`, `compute`, `task_parameters`,
  `setup_duration_seconds`, `cleanup_duration_seconds`,
  `execution_duration_seconds`.
- El comentario de ambas remite a la documentación de *Lakeflow System Tables* y
  describe «rebanadas» de runs en curso (`period_start_time`/`period_end_time`):
  una misma ejecución puede aparecer en varias filas mientras avanza.

`[NO VERIFICADO]`: si ya contienen los runs de dev `320947022571947` y del job de
producción `916863262593632`, con qué **latencia** aparecen y qué **retención**
tienen. Requiere consulta → T-07. El plan asume latencia de «unos minutos»
(`plan.md` §7.2) y por eso el cierre **no** depende de `system.lakeflow`.

### 1.3 API de Jobs (gratis y ya usada)

`databricks jobs list-runs --job-id <id> --limit N` devuelve estado, trigger y
tiempos. Comprobado hoy en el job de producción `916863262593632`:

| Run | Trigger | Resultado | Inicio → fin (UTC+1) |
|---|---|---|---|
| `317888374535056` | `ONE_TIME` | `SUCCESS` | 2026-10-31 18:15:53 → 18:28:17 |
| `321262594455396` | `ONE_TIME` | `SUCCESS` | 2026-10-31 17:58:47 → 18:13:55 |
| `636564838129029` | `PERIODIC` | `SUCCESS` | 2026-10-25 23:00:51 → 23:14:52 |

No hay run del domingo 2026-11-01: el job estaba `PAUSED` (repuesto el
2026-11-02, `design-streaming.md` §1.7). El deploy de hoy **no** ha creado ningún
run. En la 010 esta API fue la fuente de los tiempos por tarea
(`specs/010-Performance-And-Layout/evidence.md` T-07/T-08).

### 1.4 Notificación de fallo

`resources/elt.job.yml` L167–170: `email_notifications.on_failure` a la persona y
`no_alert_for_skipped_runs: false`; `notification_settings` con
`no_alert_for_canceled_runs: false`. Es la única alerta activa hoy.

### 1.5 Frescura del dashboard

`.github/workflows/dashboard.yml` L137: `MAX_AGE_DAYS = 8`; el workflow corre los
lunes a las 01:00 UTC y **aborta sin desplegar** si `meta.json.data_date` supera
8 días (L159–163). Cubre la frescura publicada, no la del job.

### 1.6 Event log: estado actual y qué haría falta

- Hoy **no hay ninguna clave `spark.eventLog.*`** en el bundle: el `spark_conf`
  del job cluster solo contiene `spark.sql.shuffle.partitions: "8"`
  (`resources/elt.job.yml` L25–26). Por tanto no se persiste event log y el
  diagnóstico depende de la Spark UI en vivo y del JSON del run.
- Haría falta, en `job_clusters[0].new_cluster.spark_conf`:
  `spark.eventLog.enabled: true` y `spark.eventLog.dir` apuntando a ADLS (candidato
  `abfss://landing@joboffershns.dfs.core.windows.net/_eventlogs/`), y
  opcionalmente `spark.eventLog.rolling.enabled`/`maxFileSize`. El clúster ya
  escribe en `abfss://landing@…` desde los notebooks de export, así que la ruta es
  alcanzable; hoy `landing/_eventlogs/` está **vacío** (0 ficheros, listado con
  AzCopy, 0 €) y su puesta en marcha va con T-06.
- **No se puede saber sin un run:** tamaño en bytes por ejecución, si el log
  recoge el progreso de streaming con el detalle que hace falta, retención real en
  ADLS y utilidad del history server. Todo eso queda `[NO VERIFICADO]` hasta T-06
  (configuración) y T-08 (medición).

### 1.7 SQL warehouse (metadatos, sin despertarlo)

`databricks warehouses list --profile vs_code` → `Serverless Starter Warehouse`
(id `53d8e3e91af6749c`), `state: STOPPED`, `num_clusters: 0`, `auto_stop_mins: 10`,
2X-Small. La consulta es de metadatos: **no lo despierta** y no genera coste.
Queda `STOPPED` antes y después del reconocimiento (RF-6).

## 2. Lo que no se puede hacer sin SQL o sin runs

| Queda `[NO VERIFICADO]` | Por qué | Se cierra en |
|---|---|---|
| Lectura real de `system.lakeflow`/`billing`/`compute` | exige una consulta SQL | T-07 (1 sesión) |
| Latencia de `system.lakeflow` y retención de las tablas | idem | T-07 |
| Coste por run (`billing.usage`) | idem | T-07 |
| Que `system.*` sea legible **desde el clúster del job** | requiere un run | T-08 |
| `numInputRows`, `batchId`, duración del microbatch, backlog | `query.lastProgress` solo existe dentro de un run | T-03/T-08 |
| Salud del checkpoint (offsets, esquema, commits) | hoy solo se ha listado `commits/`; leerlo exige run o acceso a ficheros | T-03/T-08 |
| Contenido y tamaño del event log | no está configurado | T-06/T-08 |
| Query Profile | requiere la sesión SQL | T-07 |

## 3. Diseño de `ops.run_metrics`

Esquema `<catalog>.ops` (se crea por CLI en T-04, 0 €), **una tabla y un
escritor**, `MERGE` por `run_id`. Contrato de `plan.md` §7.1 (sin campos nuevos):

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

Reglas de diseño:

- **Idempotente** por `run_id` (repetir la escritura no duplica la fila).
- **Un solo escritor**: la tarea final; ningún notebook de negocio escribe en
  `ops` (así no hay contención ni ocho sitios que mantener).
- **Best-effort**: un fallo al escribir la métrica se registra y **no** tumba una
  tarea de negocio (decisión de `spec.md`). La que **sí falla** es la tarea final,
  por sus `checks`.
- Las **duraciones y estados por tarea no se duplican** aquí: son la fuente de
  verdad de la plataforma y salen de `system.lakeflow.job_task_run_timeline`
  (§1.2), consultada desde el clúster del job.
- `ops` es derivado y reconstruible (no es la copia canónica de nada).

## 4. Tarea final `Publish_Run_Summary`

De `plan.md` §7.2, sin cambios: `run_if: ALL_DONE` y dependencia de las **8
tareas** de negocio (bronce + 4 silver + oro + 2 exports).

1. lee el resumen del bronce de `dbutils.jobs.taskValues` (lo publica T-03; si no
   está, el check falla);
2. lee `meta.json` del export web (el mismo `data_date` que usa el dashboard);
3. calcula frescura y busca ficheros pendientes **anteriores al inicio del run**
   (listado barato del landing y del directorio `commits/` del checkpoint, sin
   leer Parquet; un fichero que llega durante el run no cuenta);
4. escribe/actualiza `ops.run_metrics` con `checks`;
5. **falla** si falta el resumen de bronce, si `data_date` > 8 días o si hay
   ficheros pendientes sin ingerir con lote vacío.

`system.lakeflow` puede tardar unos minutos en reflejar el run en curso: el cierre
**no** depende de ella, así que su latencia no puede bloquear ni tumbar el run.
La línea base de pendientes medida hoy (28 ficheros, `design-streaming.md` §1.6)
es la referencia de lo que el primer run tras la reanudación debería consumir.

## 5. Umbrales de alerta

| Alerta | Umbral | Superficie | Estado |
|---|---|---|---|
| Fallo del run | cualquier tarea fallida | email `on_failure` | ya existe |
| Duración anómala | `RUN_DURATION_SECONDS` **> 1500 s** (por debajo del `timeout_seconds: 1800`) | health rule del job + email `on_duration_warning_threshold_exceeded` | a desplegar en T-06 |
| Frescura publicada | `data_date` > **8 días** | workflow del dashboard (falla sin desplegar) | ya existe |
| Frescura del run | `data_date` > **8 días** | tarea final → `checks` `failed` | T-05 |
| Ficheros pendientes | pendientes anteriores al inicio del run con lote vacío | tarea final → `checks` `failed` | T-05 |
| Backlog de streaming | `STREAMING_BACKLOG_*` **no se usan**: se calculan sobre una media de 10 min y estas consultas duran segundos | — | descartado, documentado |
| Alertas SQL programadas | — | cada despertar ~0,43 € y es recurrente | **rechazado** por coste |

Matiz verificado en el esquema del bundle (CLI 1.21.0, `databricks bundle
schema`): la clave YAML es **`health.rules[]`** con `metric` / `op` / `value`; no
existe `health_rules` en el esquema. `RUN_DURATION_SECONDS` es **GA**;
`STREAMING_BACKLOG_BYTES`, `_RECORDS`, `_SECONDS` y `_FILES` están en **Public
Preview**. Esto corrige la nota del 2026-11-01 que hablaba de `health_rules`.
