# Diseño — observabilidad del run

> **Estado (2026-11-02, T-01):** inventario de fuentes cerrado con comprobaciones
> **gratuitas** (API de Unity Catalog, API de Jobs, listado de AzCopy y metadatos
> del warehouse). **0 runs, 0 clústeres, 0 consultas SQL, 0 pipelines.** Lo que
> exige leer una tabla de sistema se aplaza a **T-07** (una única sesión SQL) y va
> marcado `[NO VERIFICADO]`.
>
> **Estado (2026-11-03, T-07):** la sesión SQL única se ejecutó (**6 min 44 s**,
> **8 ejecuciones**, `STOPPED` antes y después; **~0,43 €** presupuestados, importe
> exacto del Account Console `[NO VERIFICADO]`). Cierra
> la **lectura real** de `system.lakeflow`/`system.billing` (§1.1), el **alcance
> observado** de `job_run_timeline` —**2026-08-25 → 2026-10-31**, **≈67 días**; no
> es una garantía de retención, §1.2— y el **coste por run** (**0,045956 USD** de DBU
> para el run de 744 s, `docs/costs.md`). Siguen `[NO VERIFICADO]` la **latencia**
> de `system.lakeflow` (→ T-08/T-13) y la lectura de `system.*` **desde el clúster
> del job** (→ T-08). `current_workspace_id()` no resuelve en este workspace: los
> RUNBOOK usan el literal `7405619889654446`.
>
> **Estado (2026-11-04, T-08/T-11/T-13):** primera ejecución real instrumentada.
> El deploy lleva la novena tarea a **dev y prod**; `ops.run_metrics` existe con
> **13 columnas** y filas reales; el **event log quedó retirado** (§1.6) y la
> **latencia de `system.lakeflow`** está medida como **> 7 min 37 s** (§1.8).
> T-11 y T-13 siguen **parciales** (falta el par «ingesta + 0 filas» en el mismo
> catálogo y el primer run programado), ver `tasks.md`.
>
> Rama `spec/011-streaming-observability`, worktree `talent-market-lens-011`.
> Cada afirmación lleva su fuente (comando o fichero).

## 1. Inventario de fuentes de observabilidad disponibles

| Fuente | Qué aporta | Coste | Estado (a fecha de 2026-11-04) | Uso en la 011 |
|---|---|---|---|---|
| `system.lakeflow.job_run_timeline` | una fila por rebanada de run: estado, trigger, duraciones de setup/cola/ejecución/limpieza | 0 € desde el clúster del job | existe; `SELECT` efectivo; **leída el 2026-11-03 (T-07): 29 filas, una por run** | diagnóstico del run (T-07, RUNBOOK) |
| `system.lakeflow.job_task_run_timeline` | una fila por rebanada de **tarea**: `task_key`, estado, duraciones | 0 € desde el clúster del job | existe; `SELECT` efectivo; **leída el 2026-11-03 (T-07): 8 tareas** | duraciones y estados por tarea (no se duplican en `ops`) |
| `system.billing.usage` | consumo por SKU y ventana (`usage_quantity`, `usage_metadata`) | 0 € | existe; `SELECT` efectivo; **leída el 2026-11-03 (T-07): 28 filas, `usage_metadata.job_run_id` poblado** | coste por run (T-07) |
| `system.compute.*` | clústeres, nodos y warehouses (`clusters`, `node_timeline`, `warehouses`, …) | 0 € | existe; `SELECT` efectivo; **sin consultar** | atribución de coste y de arranque (T-07) |
| Email de fallo del job | aviso inmediato de run fallido | 0 € | activo (`on_failure`) | alerta base (ya existe) |
| API de Jobs (`jobs list-runs`, `jobs get-run`) | historial y JSON del run con tiempos por tarea | 0 € | en uso (010) | diagnóstico puntual |
| Workflow del dashboard (GitHub) | frescura publicada: falla si `data_date` > 8 días | 0 € | activo | frescura en CI |
| Event log de Spark | trazas, tareas, streaming progress | 0 € (bytes en ADLS) | **retirado** (2026-11-03/04): el clúster no arranca con `spark.eventLog.dir` en `abfss://`; `landing/_eventlogs/` con **0 B** (§1.6) | T-06 (decisión medida) |
| `ops.run_metrics` (Delta) | una fila por run con negocio, frescura y `checks` | 0 € | **existe en dev y en prod**; **13 columnas**; filas reales desde el 2026-11-03 | T-04/T-05 |

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
Catalog devuelve el grant efectivo.

**Lectura real verificada (2026-11-03, T-07).** Una única sesión SQL confirmó que
las consultas del RUNBOOK devuelven filas del proyecto:
`system.lakeflow.job_run_timeline` (**29 filas**, 6,4 s),
`system.lakeflow.job_task_run_timeline` (**8 tareas**, 2,1 s),
`system.billing.usage` (**28 filas**, 3,2 s),
`DESCRIBE system.billing.list_prices` (0,8 s) y el coste por run (3,4 s). Ninguna
tabla dio error de permisos: el `SELECT` efectivo de la tabla de arriba se traduce
en lectura.

**Hallazgo de la sesión:** `current_workspace_id()` **no resuelve** en este
workspace —`[UNRESOLVED_ROUTINE] Cannot resolve routine 'current_workspace_id' on
search path [system.session, system.builtin, system.ai, job_offers_dev.bronze]`,
2026-11-03 07:46:39, 4,8 s—. Las tres consultas del RUNBOOK se corrigieron al
literal `7405619889654446` en los dos idiomas (§9.8.2 EN / §9.6.2 ES); el literal
es **específico de este workspace** (la forma portable sería la función donde
exista).

Por eso las filas de la tabla de arriba ya no llevan la advertencia de §2.

### 1.2 Existencia y esquema de las tablas de `lakeflow` (metadatos de T-01 y lectura real de T-07)

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

**Medido (2026-11-03, T-07):** las tablas **sí** contienen los runs de los dos
jobs (dev `320947022571947`, prod `916863262593632`): **29 filas** en la ventana
de 30 días y **una fila por run** (el `GROUP BY run_id, job_id, trigger_type`
agrupa las rebanadas). **Alcance de la tabla:** **106 filas**, **84 runs**, **6
jobs**, `oldest_period = 2026-08-25T18:51:19.492Z` y `newest_period =
2026-10-31T17:28:17.090Z` → **los datos de la tabla abarcan `2026-08-25` →
`2026-10-31` (≈67 días)**; hasta la fecha de la sesión son ≈70 días. **No es una
garantía de retención:** es el alcance observado hoy (`rows_all = 106`, `jobs = 6`,
`runs = 84`, **~1,26 filas por run**; las rebanadas son pocas, no una por
minuto). Las duraciones **por tarea no son 0** y las `*_duration_seconds` a nivel
de run **sí** lo son en este job multi-tarea. `[NO VERIFICADO]`: la **latencia**
(con qué retraso aparece un run), que exige un run fresco → **T-08/T-13**. El plan
asume latencia de «unos minutos» (`plan.md` §7.2) y por eso el cierre **no**
depende de `system.lakeflow`.

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

### 1.6 Event log: retirado (decisión medida)

**Estado: no está activo.** T-06 configuró `spark.eventLog.{enabled,dir,rolling}`
en el job cluster apuntando a
`abfss://landing@joboffershns.dfs.core.windows.net/_eventlogs/` y el primer run
instrumentado (`873077346042958`, **2026-11-03**) demostró que **el clúster no
arranca** con esa configuración: `SparkMisconfiguration: Invalid configuration
value detected for fs.azure.account.key`. El clúster no llegó a ejecutar el
pipeline —`Bronze_Ingestion` gastó **472 s de setup** y las **7** tareas
dependientes quedaron `UPSTREAM_FAILED`—, y las tres claves se **retiraron** en
`639d252` (2026-11-03). El motivo queda también junto a la configuración, en la
nota del `spark_conf` de `resources/elt.job.yml`.

**Medición (2026-11-04):** `landing/_eventlogs/` tiene **0 ficheros y 0 B**
(AzCopy). No hay coste en bytes que valorar: el log no llega a escribirse.

**Decisión medida: no mantenerlo.** El event log aportaba lo que la Spark UI no
da —retención más allá de ~60 días y un fichero que se puede compartir—, pero no
compensa un clúster que no arranca. La **Spark UI** sigue siendo la superficie de
trazas del run. **Compute log delivery** queda como alternativa **documentada y no
implementada**: enviaría los logs del driver y de los executors a un destino de la
cuenta sin `spark.eventLog.dir`, y solo se valoraría si hiciera falta retención
propia. Dev y prod comparten bundle, así que la retirada vale para los dos: el
`bundle deploy -t prod` del 2026-11-04 dejó `spark_conf` con **solo**
`spark.sql.shuffle.partitions`.

**`[NO VERIFICADO]`:** nada del event log —no se usa—. Lo que sigue abierto es la
**cifra exacta** de latencia de `system.lakeflow` (§1.8).

### 1.7 SQL warehouse (metadatos, sin despertarlo)

`databricks warehouses list --profile vs_code` → `Serverless Starter Warehouse`
(id `53d8e3e91af6749c`), `state: STOPPED`, `num_clusters: 0`, `auto_stop_mins: 10`,
2X-Small. La consulta es de metadatos: **no lo despierta** y no genera coste.
Queda `STOPPED` antes y después del reconocimiento (RF-6).

**Sesión de T-07 (2026-11-03):** una única ventana de **6 min 44 s** (consultas
**07:46:39 → 07:53:23**, hora local; **8 ejecuciones**, 1 fallida + 7 correctas)
en este warehouse, con `auto_stop_mins: 10`; `STOPPED` **antes y después**
(confirmado por CLI al terminar, y el histórico no tenía consultas desde el
2026-10-23). Coste: **~0,43 € presupuestados** por la ventana facturable de
10 min —el importe exacto llega del Account Console, `[NO VERIFICADO]`— y **0 € de
runs y de clústeres**. El **coste por run** medido en la misma sesión (`billing.usage` +
`list_prices`) es **0,045956 USD** de DBU para el run de 744 s, **solo DBU a
precio de lista**, con los caveats y la comparación en `docs/costs.md`.

**Segunda sesión (2026-11-04, T-08):** otra ventana de **~10 min**
(**07:13 → 07:23 UTC**), misma factura (~0,43 € nominales; importe exacto
`[NO VERIFICADO]`), con el literal `workspace_id = 7405619889654446` y el
warehouse **`STOPPED` antes y después** (`num_clusters: 0`). Sirvió para leer las
filas de `ops.run_metrics` (**1** en prod, **2** en dev), el **esquema de 13
columnas** y la **latencia** de `system.lakeflow` (§1.8). No se lanzó ningún run
desde la sesión.

### 1.8 Latencia medida de `system.lakeflow` (2026-11-04)

El run de producción `1050766241907151` terminó a las **07:15:58Z**. A las
**07:23:35Z**, en la misma ventana SQL, `system.lakeflow.job_run_timeline`
**seguía sin devolverlo**: `count(*) = 0` para su `run_id`, y el run más reciente
visible del job seguía siendo el `2026-10-31T17:28:17Z`. Por tanto la latencia es
**> 7 min 37 s**, no los «unos minutos» que suponía `plan.md` §7.2.

Consecuencias:

1. **Refuerza la decisión de T-05:** el cierre **no** depende de esa tabla, así
   que su latencia no puede bloquear ni tumbar un run.
2. Para un run recién terminado la fuente es `databricks jobs get-run` (gratis);
   esperar a `system.lakeflow` es una espera a ciegas.
3. La `job_task_run_timeline` hereda la misma incertidumbre: las duraciones por
   tarea de un run del día pueden no estar todavía.

**La cifra exacta queda `[NO VERIFICADO]`:** solo hay una **cota inferior**
(> 7 min 37 s). El run programado del **2026-11-08** permite acotarla con más
margen; hasta entonces, los RUNBOOK dicen explícitamente que un run recién
terminado **no** está en la tabla.

## 2. Lo que no se puede hacer sin SQL o sin runs

**Cerrado en la sesión SQL de T-07 (2026-11-03, ~0,43 €; importe exacto
`[NO VERIFICADO]` hasta el Account Console):** la **lectura real** de
`system.lakeflow` y `system.billing` (§1.1), el **alcance observado** de
`job_run_timeline` (**2026-08-25 → 2026-10-31**, **≈67 días**; no es una garantía
de retención, §1.2), el **coste por run** (`billing.usage` +
`list_prices`, `docs/costs.md`) y el **Query Profile**
(`evidence/t07-query-profile.png`: 2,603 s, 29 filas devueltas / 43 leídas, 15,06
KB, 0 % de caché, `Scan Table` 17 ms).

| Queda `[NO VERIFICADO]` | Por qué | Se cierra en |
|---|---|---|
| La **cifra exacta** de latencia de `system.lakeflow` | solo hay cota inferior: **> 7 min 37 s** (§1.8) | T-13 (run del 2026-11-08) |
| Que `system.*` sea legible **desde el clúster del job** | ningún notebook del job la consulta y el cierre no la necesita | sin plan (no bloquea) |
| Entrega real del **correo de duración** | exige un run de más de 1500 s | incidente real |
| Salud del checkpoint (offsets y esquema) | solo se ha listado `commits/`; los offsets no se leen | sin plan |

**Cerrado en vivo por los runs instrumentados (2026-11-03/04):**
`numInputRows` por fuente (el resumen llega con las 4 fuentes y `bronze_rows 2283`
en los runs con ingesta), la **escritura en `ops.run_metrics`** en DBR 17.3 (13
columnas; fila en verde y fila con `status: failed`), `dbutils.fs.head`/`ls` sobre
ABFS, `taskValues.get` y el `run_if: ALL_DONE` **en el lanzamiento** (el cierre
arrancó con dependencias fallidas); que la fila se escriba con **otra tarea en
rojo** sigue `[NO VERIFICADO]` (§4.1). El **event log ya no se mide**: está
retirado (§1.6).

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
(`f4c405a5-f330-4582-b627-943a5c3d03df`). La **tabla** la creó el primer `record`
real (2026-11-03): el **2026-11-04**, `system.information_schema.columns` de
`job_offers.ops.run_metrics` devuelve **13 columnas** con los tipos de la tabla de
arriba, y hay filas en los dos catálogos (**1** en `job_offers`, **2** en
`job_offers_dev`) — se cierra el `DESCRIBE` que T-04 dejó pendiente.

Tests: **30 passed** (26 puros, incluidos 4 parametrizados de clave nula, + 4 de
integración) en ~7 min; los 4 de integración se ejecutan en aislamiento con Delta
local y demuestran que dos escrituras del mismo `run_id` dejan **una** fila y gana la
segunda.
**Cerrado en vivo (2026-11-03/04):** la escritura real demuestra `tableExists` de
tres partes, el DDL `USING DELTA` en Unity Catalog y el `MERGE … UPDATE SET *` en
**DBR 17.3** (tres filas escritas: dos en dev y una en prod —una de ellas con
`status: failed`). La **idempotencia del mismo `run_id` en DBR 17.3** sigue probada
solo en local (Delta 4.4.1): en la nube no se ha repetido todavía un `run_id`.
**`[NO VERIFICADO]`:** esa repetición en DBR 17.3.

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

`system.lakeflow` puede tardar en reflejar el run en curso —medido el 2026-11-04:
**> 7 min 37 s**, §1.8—: el cierre **no** depende de ella, así que su latencia no
puede bloquear ni tumbar el run.
La línea base de pendientes medida hoy (28 ficheros, `design-streaming.md` §1.6)
es la referencia de lo que el primer run tras la reanudación debería consumir.

### 4.1 Implementación (T-05, 0 €)

`databricks_notebooks/run_summary.ipynb` (8 celdas) solo coordina; la lógica vive en
`Run_Metrics.py` (`pending_files`, `build_checks`, `failed_checks`, `parse_data_date`)
y los tests puros son **65 passed** en 0,9 s:

1. valida `run_id`, `job_id`, `started_at` y `storage_account` (sin `run_id` no hay
   fila; falla con mensaje en español);
2. lee `taskValues.get("Bronze_Ingestion", "bronze_summary", default=None)` (la API
   real necesita la tarea productora) y tolera que no exista;
3. lee `meta.json` con `dbutils.fs.head` y normaliza la fecha con
   `parse_data_date`, que **nunca lanza**: un valor vacío o corrupto se publica como
   `null` y lo caza el check de frescura (así la fila siempre se escribe);
4. lista `landing/<fuente>/` con la **API DataFrame** (`format("binaryFile")` con
   `recursiveFileLookup`, seleccionando **solo** `path` y `modificationTime`, sin pedir
   `content`: no se lee ningún fichero) y `commits/` del checkpoint con `dbutils.fs.ls`;
   la API Java de Hadoop del notebook no tiene credenciales en ABFS (falló en vivo el
   2026-11-03) y la de DataFrame sí las comparte con el resto del job;
5. calcula `pending = pending_files(files, commits, run_start_ms)` con
   `commit < modification_ms < run_start_ms`: lo que llega **durante** el run no
   cuenta. El `modificationTime` se convierte a epoch en ms fijando UTC
   (`replace(tzinfo=timezone.utc)`), sin `unix_timestamp`/`cast`, para que no dependa
   de `spark.sql.session.timeZone`. Si el listado de una fuente falla,
   `listing_ok=False` y el check falla (no hay verde falso), y el texto del error
   (una línea, recortado) viaja en `listing_error`; un fallo al listar `commits/`
   viaja en ese mismo texto pero **no** cambia `listing_ok`, porque un checkpoint
   que aún no existe es el primer run de la fuente;
6. construye `checks` (JSON nativo) y la fila, y llama a `record` **con logger**;
7. `raise RuntimeError` si algún check no es `True` o `record` devolvió `failed`,
   **después** de escribir; `dbutils.notebook.exit` va al final (un `exit` antes del
   `raise` se tragaría el fallo).

Contrato de `checks`:

```json
{"bronze_summary": {"ok": true, "sources": ["indeed", "infojobs", "linkedin", "multi_site"]},
 "bronze_sources": {"ok": true, "expected": 4, "present": 4},
 "freshness": {"ok": true, "data_date": "2026-10-31", "data_age_days": 2, "max_age_days": 8},
 "pending_files": {"ok": true, "listing_ok": true, "listing_error": null, "total": 0, "by_source": {}, "bronze_rows": 135}}
```

`listing_error` existe para diagnosticar desde la propia fila por qué el listado
falló: sin él, `listing_ok=false` solo dice que el run está en rojo. Es aditivo y
**no decide** nada (sigue mandando `listing_ok`); se publica `null` cuando no hubo
ningún error y, si lo hubo, una línea recortada a ~300 caracteres con el error del
landing (que además deja `listing_ok=false`) y/o el de `commits/`.

**Contrato real, medido (2026-11-04).** El JSON que publica el cierre es el de
arriba, con las claves ordenadas y **`listing_error` siempre presente** (`null` sin
error); en el run en verde `1015457552352783` llegó literalmente así:

```json
{"bronze_sources": {"expected": 4, "ok": true, "present": 4}, "bronze_summary": {"ok": true, "sources": ["indeed", "infojobs", "linkedin", "multi_site"]}, "freshness": {"data_age_days": 2, "data_date": "2026-11-02", "max_age_days": 8, "ok": true}, "pending_files": {"bronze_rows": 0, "by_source": {}, "listing_error": null, "listing_ok": true, "ok": true, "total": 0}}
```

y la fila correspondiente quedó en `job_offers_dev.ops.run_metrics` con
`duration_s 849.213635`, `bronze_rows 0`, `quarantine_rows 0`,
`data_date 2026-11-02`, `data_age_days 2`, `status success` y
`recorded_at 2026-11-04T07:03:57.620Z`.

**La fila se escribe antes del `raise` (2026-11-03).** En el run
`1048410431217707` las **8 tareas de negocio** terminaron en `SUCCESS` y el cierre
falló por su propio defecto del listado (**`listing_ok: false`**, corregido en
`808a3d7`); aun así, la fila quedó: `duration_s 907.360947`, `bronze_rows 2283`,
`quarantine_rows 0`, `data_date 2026-11-02`, `data_age_days 1`,
**`status failed`**, `recorded_at 2026-11-03T08:36:25.136Z`. Eso prueba que la
fila se escribe **antes** del `raise`, no la combinación con otra tarea en rojo.

**`run_if: ALL_DONE` está probado en el lanzamiento (2026-11-03).** En el intento
`873077346042958`, con **7 dependientes `UPSTREAM_FAILED`**, `Publish_Run_Summary`
**sí se lanzó** (nuevo intento del clúster del job, `1103-080127-yh3s5qcy`, a las
**09:01:27** local) y falló por la misma
`SparkMisconfiguration`; **no escribió fila** porque el clúster nunca llegó a
arrancar (event log, §1.6).

**`[NO VERIFICADO]`: la combinación «fila escrita cuando falla otra tarea».**
No se ha observado: en el único run con dependencias fallidas el clúster no
arrancó, y en el run que sí dejó fila en rojo el fallo era del propio cierre, con
las 8 tareas de negocio en verde. Se probará con un run que tenga una tarea de
negocio en rojo y un clúster sano.

Reglas: `freshness` exige fecha, no futura y ≤ 8 días; `pending_files` falla si
`listing_ok` no es `True`, o si hay pendientes **y** `bronze_rows == 0`;
`bronze_sources` exige las 4 fuentes.
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

**Cerrado en vivo (2026-11-03/04),** de la lista que quedaba abierta en T-05:
`taskValues.get("Bronze_Ingestion", …)` funciona; el formato de
`{{job.start_time.iso_datetime}}` se interpreta bien (`recorded_at`
**07:03:57.620Z** = 08:03:57 local menos el inicio del run 07:49:48 ≈ los
**849 s** de `duration_s`); la escritura en `ops.run_metrics` funciona en DBR 17.3
(13 columnas, ver T-04/T-08); `dbutils.fs.head`/`ls` sobre ABFS y la API DataFrame
funcionan; y `run_if: ALL_DONE` está probado **en el lanzamiento** (el cierre arrancó
con las 7 dependientes `UPSTREAM_FAILED`, arriba), no la combinación con fila escrita
(`[NO VERIFICADO]`). La API Java de Hadoop (`fs.listFiles`) sigue **sin credenciales**
en el notebook: por eso T-05 la sustituyó por `format("binaryFile")`, y esa es la que
corre.

**`[NO VERIFICADO]`:** la lectura de `system.*` **desde el clúster del job** (no hay
ningún notebook del job que la consulte y el cierre no la necesita) y la entrega
real del correo de duración.

## 5. Umbrales de alerta

| Alerta | Umbral | Superficie | Estado (2026-11-04) |
|---|---|---|---|
| Fallo del run | cualquier tarea fallida | email `on_failure` | **activo**, y ahora con cierre: `Publish_Run_Summary` corre igual (`run_if: ALL_DONE`; lanzamiento probado el 2026-11-03) y escribe su fila antes del `raise` (probado el 2026-11-03 con el fallo del propio cierre); la combinación con **otra** tarea en rojo sigue `[NO VERIFICADO]` (§4.1) |
| Duración anómala | `RUN_DURATION_SECONDS` **> 1500 s** (por debajo del `timeout_seconds: 1800`) | health rule del job + email `on_duration_warning_threshold_exceeded` | **desplegada en dev (2026-11-03) y en prod (2026-11-04)** (`bundle deploy`); el **correo** sigue `[NO VERIFICADO]` porque ningún run superó 1500 s (se observaría en un incidente real) |
| Frescura publicada | `data_date` > **8 días** | workflow del dashboard (falla sin desplegar) | ya existía |
| Frescura del run | `data_date` > **8 días** | tarea final → `checks` `failed` | **desplegada y en verde**: `data_date 2026-11-02`, `data_age_days 2`, `max_age_days 8` en dev y prod (2026-11-04) |
| Ficheros pendientes | pendientes anteriores al inicio del run con lote vacío, **o** `listing_ok` falso | tarea final → `checks` `failed` | **desplegada**; el rojo por `listing_ok: false` está medido (2026-11-03) y el verde con `total 0` también (2026-11-04) |
| Event log | sin umbral | `landing/_eventlogs/` en ADLS | **retirado** (commit `639d252`): el clúster no arranca con `abfss://` y el directorio tiene **0 ficheros, 0 B**; `compute log delivery` queda como alternativa documentada, no implementada (§1.6) |
| Backlog de streaming | `STREAMING_BACKLOG_*` **no se usan**: los streams son por lotes (`availableNow`), sin backlog continuo que vigilar; además la métrica está en **Public Preview** y el cierre ya detecta pendientes | — | descartado, documentado |
| Alertas SQL programadas | — | cada despertar ~0,43 € y es recurrente | **rechazado** por coste |
| Latencia de `system.lakeflow` | no es una alerta: es una **cota** para no esperar en balde | diagnóstico manual | **medida: > 7 min 37 s** (§1.8); la cifra exacta, del run programado del 2026-11-08 |

Matiz verificado en el esquema del bundle (CLI 1.21.0, `databricks bundle
schema`): la clave YAML es **`health.rules[]`** con `metric` / `op` / `value`; no
existe `health_rules` en el esquema. `RUN_DURATION_SECONDS` es **GA**;
`STREAMING_BACKLOG_BYTES`, `_RECORDS`, `_SECONDS` y `_FILES` están en **Public
Preview**. Esto corrige la nota del 2026-11-01 que hablaba de `health_rules`.
