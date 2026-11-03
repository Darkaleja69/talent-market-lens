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
| Event log de Spark | trazas, tareas, streaming progress | 0 € (bytes en ADLS) | **configurado en el bundle (T-06)**; escritura real → T-08 | T-06 (decisión medida) |
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

### 1.6 Event log: estado y qué falta por medir

- **Estado: configurado en T-06.** El `spark_conf` del job cluster lleva
  `spark.eventLog.enabled: "true"`, `spark.eventLog.dir:
  abfss://${var.container}@${var.storage_account}.dfs.core.windows.net/_eventlogs/`
  (resuelto por el bundle a `abfss://landing@joboffershns.dfs.core.windows.net/_eventlogs/`)
  y `spark.eventLog.rolling.enabled: "true"`, junto al
  `spark.sql.shuffle.partitions: "8"` que ya existía (`resources/elt.job.yml`).
  `bundle validate -t dev` → `Validation OK!` y `bundle summary -t dev -o json`
  muestra las cuatro claves con la ruta expandida; el job conserva sus **9 tareas**.
- La ruta es alcanzable: el clúster ya escribe en `abfss://landing@…` desde los
  notebooks de export. `landing/_eventlogs/` estaba **vacío** (0 ficheros, listado
  con AzCopy, 0 €) antes de T-06 y lo llena el primer run. **dev y prod comparten
  esa ruta** (no hay prefijo por target), así que T-08 tendrá que separar los
  bytes por run al medir.
- Las claves se declaran en YAML **con los valores como cadenas** (`"true"`), como
  el resto de `spark_conf`.
- **No se puede saber sin un run:** tamaño en bytes por ejecución, si el log
  recoge el progreso de streaming con el detalle que hace falta, retención real en
  ADLS y el valor de retenerlo más allá de la ventana de la Spark UI (~60 días tras
  el run). **Tamaño y utilidad se miden en T-08** (coste en bytes de
  `landing/_eventlogs/`), antes de decidir si el event log se queda; hasta entonces
  el contenido real del directorio sigue `[NO VERIFICADO]`.
- Si tras el primer run `landing/_eventlogs/` queda vacío o solo con ficheros
  `.inprogress` (el driver puede destruirse al terminar), la alternativa soportada
  es **compute log delivery**; se decide en T-08 con los bytes medidos.

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
| Contenido y tamaño del event log | configurado en T-06; medirlo exige un run | T-08 |
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

### 3.1 Implementación (T-04, 0 €)

`databricks_notebooks/Run_Metrics.py` (389 líneas) implementa el contrato anterior:

- **Una sola definición de columnas** (`_COLUMN_TYPES`) genera `COLUMNS`, el
  `TABLE_SCHEMA` de Spark y el DDL de `ensure_table`: no hay tres listas que puedan
  desincronizarse.
- `build_run_row` es **puro** (`now` inyectable): normaliza instantes a UTC, calcula
  `duration_s` con suelo 0, `bronze_rows` como **suma de `num_input_rows_total`** (con
  fallback por stream a `num_input_rows`) y `quarantine_rows` como suma de
  `quarantined_rows` (el resumen y la columna no comparten nombre). `data_age_days`
  se mide contra la fecha UTC de `now` y puede ser negativo si el dato es futuro.
- `checks_json` / `metrics_json` son estables byte a byte (`sort_keys`, separadores
  compactos, `allow_nan=False`, no finitos → `null`). **Los `checks` deben llevar
  tipos JSON nativos** (`str`/`int`/`bool`/`None`/listas/dicts): un `datetime` o un
  `Decimal` lanzan, y T-05 los construye con esos tipos.
- `merge_sql` casa por `run_id` y tiene las dos ramas; `ensure_table` crea **solo la
  tabla** (`CREATE TABLE IF NOT EXISTS … USING DELTA`), nunca el esquema.
- `record(spark, table, row, logger=None)` **nunca propaga**: valida presencia de las
  13 columnas y que `run_id`/`job_id` no sean nulos ni vacíos, ignora claves extra,
  y devuelve `{"status": "ok"|"failed", "error": …}` registrando el fallo.

El **esquema `ops`** se creó por CLI el **2026-11-03** (gratis) en `job_offers_dev`
(`36f4fdc8-2fbb-4d23-bda7-4b05c3b1b73b`) y `job_offers`
(`f4c405a5-f330-4582-b627-943a5c3d03df`). La **tabla** la crea el primer `record`
real: hoy ambos esquemas están vacíos (`databricks tables list`, 0 €), así que el
`DESCRIBE` de la tabla queda para **T-08**, con el primer run instrumentado.

Tests: **30 passed** (26 puros, incluidos 4 parametrizados de clave nula, + 4 de
integración) en ~7 min; los 4 de integración se ejecutan en aislamiento con Delta
local y demuestran que dos escrituras del mismo `run_id` dejan **una** fila y gana la
segunda.
`[NO VERIFICADO]`: `tableExists` de tres partes, DDL `USING DELTA` en Unity Catalog y
`MERGE … UPDATE SET *` en **DBR 17.3**; se cierran en T-08.

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

### 4.1 Implementación (T-05, 0 €)

`databricks_notebooks/run_summary.ipynb` (8 celdas) solo coordina; la lógica vive en
`Run_Metrics.py` (`pending_files`, `build_checks`, `failed_checks`, `parse_data_date`)
y los tests puros son **58 passed** en 0,5 s:

1. valida `run_id`, `job_id`, `started_at` y `storage_account` (sin `run_id` no hay
   fila; falla con mensaje en español);
2. lee `taskValues.get("Bronze_Ingestion", "bronze_summary", default=None)` (la API
   real necesita la tarea productora) y tolera que no exista;
3. lee `meta.json` con `dbutils.fs.head` y normaliza la fecha con
   `parse_data_date`, que **nunca lanza**: un valor vacío o corrupto se publica como
   `null` y lo caza el check de frescura (así la fila siempre se escribe);
4. lista `landing/<fuente>/` y `commits/` del checkpoint **solo con metadatos**
   (`fs.listFiles` y `dbutils.fs.ls`), nunca lee Parquet;
5. calcula `pending = pending_files(files, commits, run_start_ms)` con
   `commit < modification_ms < run_start_ms`: lo que llega **durante** el run no
   cuenta. Si el listado falla, `listing_ok=False` y el check falla (no hay verde
   falso);
6. construye `checks` (JSON nativo) y la fila, y llama a `record` **con logger**;
7. `raise RuntimeError` si algún check no es `True` o `record` devolvió `failed`,
   **después** de escribir; `dbutils.notebook.exit` va al final (un `exit` antes del
   `raise` se tragaría el fallo).

Contrato de `checks`:

```json
{"bronze_summary": {"ok": true, "sources": ["indeed", "infojobs", "linkedin", "multi_site"]},
 "bronze_sources": {"ok": true, "expected": 4, "present": 4},
 "freshness": {"ok": true, "data_date": "2026-10-31", "data_age_days": 2, "max_age_days": 8},
 "pending_files": {"ok": true, "listing_ok": true, "total": 0, "by_source": {}, "bronze_rows": 135}}
```

Reglas: `freshness` exige fecha, no futura y ≤ 8 días; `pending_files` solo falla si
hay pendientes **y** `bronze_rows == 0`; `bronze_sources` exige las 4 fuentes.
`status` es el **estado del cierre** (los cuatro checks), no el agregado de las
tareas: los estados por tarea viven en `system.lakeflow` y no se duplican (§3). El
último commit del checkpoint se lee **al cerrar**; en un run de 0 filas no hay commit
nuevo (medido con Spark 4.2 local: `batchId = 1`, `numInputRows = 0`, `commits/` sin
cambios), así que el backlog previo se detecta.

Job: novena tarea `Publish_Run_Summary` con `run_if: ALL_DONE`, `depends_on` de las 8,
`job_cluster_key: tm-lens-job` y 7 parámetros (catálogo, storage, contenedor,
`export_prefix` y los dinámicos `{{job.run_id}}`, `{{job.id}}`,
`{{job.start_time.iso_datetime}}`). `bundle validate -t dev` → `Validation OK!` y el
grafo resuelto muestra las 9 tareas.

`[NO VERIFICADO]` hasta T-08: `taskValues.get` real, `dbutils.fs.head`/`ls` y
`fs.listFiles` sobre ABFS, el formato exacto de `{{job.start_time.iso_datetime}}`, la
escritura en `ops.run_metrics` en DBR 17.3 y que la fila quede cuando falla otra tarea
(`ALL_DONE` real).

## 5. Umbrales de alerta

| Alerta | Umbral | Superficie | Estado |
|---|---|---|---|
| Fallo del run | cualquier tarea fallida | email `on_failure` | ya existe |
| Duración anómala | `RUN_DURATION_SECONDS` **> 1500 s** (por debajo del `timeout_seconds: 1800`) | health rule del job + email `on_duration_warning_threshold_exceeded` | **configurada en el bundle y validada con `bundle validate`**; el deploy llega en T-08/T-13 |
| Frescura publicada | `data_date` > **8 días** | workflow del dashboard (falla sin desplegar) | ya existe |
| Frescura del run | `data_date` > **8 días** | tarea final → `checks` `failed` | implementada en el bundle (T-05); efectiva tras el deploy (T-08/T-13) |
| Ficheros pendientes | pendientes anteriores al inicio del run con lote vacío | tarea final → `checks` `failed` | implementada en el bundle (T-05); efectiva tras el deploy (T-08/T-13) |
| Event log | sin umbral: se escribe en **cada** run (`spark.eventLog.*` del job cluster, con rolling) | `landing/_eventlogs/` en ADLS; **dev y prod comparten la ruta** (sin prefijo por target) | **configurado en el bundle en T-06**; su coste en bytes se mide en T-08 antes de decidir si se queda |
| Backlog de streaming | `STREAMING_BACKLOG_*` **no se usan**: los streams son por lotes (`availableNow`), sin backlog continuo que vigilar; además la métrica está en **Public Preview** y el cierre ya detecta pendientes | — | descartado, documentado |
| Alertas SQL programadas | — | cada despertar ~0,43 € y es recurrente | **rechazado** por coste |

Matiz verificado en el esquema del bundle (CLI 1.21.0, `databricks bundle
schema`): la clave YAML es **`health.rules[]`** con `metric` / `op` / `value`; no
existe `health_rules` en el esquema. `RUN_DURATION_SECONDS` es **GA**;
`STREAMING_BACKLOG_BYTES`, `_RECORDS`, `_SECONDS` y `_FILES` están en **Public
Preview**. Esto corrige la nota del 2026-11-01 que hablaba de `health_rules`.
