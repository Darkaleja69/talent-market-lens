# Runbook — ejecución del pipeline desde cero

Guía para reproducir el pipeline completo (scrapers → ADLS → Bronze → Silver →
Gold → Power BI) en una cuenta nueva, sin credenciales en el repositorio.

> Este runbook asume las **versiones vigentes** de `databricks_notebooks/`.
> La configuración de almacenamiento, catálogo, esquemas y checkpoints está
> **externalizada** (widgets de Databricks / variables de entorno): no hay
> cuentas, rutas ni tokens hard-coded en el código.

---

## 1. Requisitos

| Componente | Versión / nota |
|---|---|
| Python (local) | 3.11+ (para tests y scrapers) |
| PySpark | 3.5 (lo aporta el Databricks Runtime; en local vía `requirements.txt`) |
| Databricks | Workspace con **Unity Catalog** habilitado |
| Azure | Suscripción con **ADLS Gen2** (Hierarchical Namespace activado) |
| Power BI | Power BI Desktop (formato PBIP) |
| AzCopy | Para subir los Parquet al landing (opcional según orquestación) |

Instala dependencias locales:

```powershell
pip install -r requirements.txt
```

---

## 2. Secretos y variables de entorno

**Regla: 0 secretos en el repositorio.** Ningún token, SAS, clave de cuenta ni
cadena de conexión se versiona.

| Secreto / parámetro | Dónde vive | Cómo se pasa |
|---|---|---|
| `LANDING_SAS_TOKEN` | Variable de entorno del usuario (local) | `setx LANDING_SAS_TOKEN "<SAS del contenedor landing>"` antes de subir con AzCopy. |
| `storage_account` | Widget de Databricks | Parámetro de la tarea / widget en el notebook Bronze. |
| `container` | Widget de Databricks | Por defecto `landing`. |
| `catalog`, `bronze_schema`, `silver_schema`, `gold_schema` | Widget de Databricks | Por defecto `job_offers`, `bronze`, `silver`, `gold`. |
| `checkpoints_root` | Widget de Databricks | Opcional; por defecto `<STORAGE_BASE>/_checkpoints`. |
| Acceso de Databricks al Data Lake | **Unity Catalog Storage Credential + External Location** (Managed Identity o Service Principal) | Configuración del workspace, nunca en el repo. |

`.gitignore` excluye `.env`, logs, `quarantine/`, backups, caches y datos
Parquet para que nada de esto acabe en Git.

---

## 3. Preparar Unity Catalog y el landing

1. **Storage**: crea el contenedor `landing` en ADLS Gen2.
2. **Credencial de almacenamiento** (Storage Credential) y **External Location**
   en Unity Catalog apuntando al contenedor. Dales permisos a los usuarios/grupos
   que ejecutarán los notebooks.
3. **Catálogo y esquemas**:

   ```sql
   CREATE CATALOG IF NOT EXISTS job_offers;
   CREATE SCHEMA IF NOT EXISTS job_offers.bronze;
   CREATE SCHEMA IF NOT EXISTS job_offers.silver;
   CREATE SCHEMA IF NOT EXISTS job_offers.gold;
   ```

   > Si usas otros nombres, no edites el código: pásalos por los widgets
   > `catalog`, `bronze_schema`, `silver_schema`, `gold_schema`.

4. **Landing por fuente**: cada scraper deja sus Parquet en
   `landing/<fuente>/` (`indeed/`, `linkedin/`, `infojobs/`, `multi_site/`).

---

## 4. Subir los módulos Python a Databricks

Los notebooks Silver y Oro importan módulos del repositorio:

- `Enrich_Job_Offers_Dataframes.py` → `import Enrich_Job_Offers_Dataframes as de`
- `Prepare_Gold.py` → `import Prepare_Gold as dg`

Súbelos al workspace (Git folder, Repos o Workspace files) y asegúrate de que
están en el `sys.path` del notebook (el notebook Oro ya usa
`%load_ext autoreload`). **Nota:** si en tu workspace los módulos tienen otro
nombre, ajusta el import en la celda de configuración de cada notebook.

---

## 5. Orden de ejecución

Todos los notebooks leen su configuración de widgets y, si no se pasan,
resuelven por variable de entorno o usan el default. En Databricks Workflows,
define los parámetros a nivel de tarea.

### 5.1 Bronze — `bronze_ingest.ipynb`

Por cada fuente, Auto Loader con `trigger(availableNow=True)`:

```
cloudFiles (parquet) → Delta job_offers.bronze.<fuente>
```

Detalles: `schemaLocation` y `checkpointLocation` bajo
`<checkpoints_root>/<fuente>/{schema,ckpt}`, `mergeSchema=true`,
`_ingest_date` y `_source_file` añadidos.

**Parámetros:** `storage_account` (obligatorio), `container`, `catalog`,
`bronze_schema`, `checkpoints_root`.

### 5.2 Silver — un notebook por fuente

`silver_indeed.ipynb`, `silver_infojobs.ipynb`,
`silver_linkedin.ipynb`, `silver_multisite.ipynb`.

Cada uno: lee `bronze.<fuente>`, aplica la cadena de la fuente (rename,
`enrich`, normalización de empresa/localización, dedup) y escribe
`silver.offers_<fuente>` por `MERGE` sobre la clave semántica
(`Silver_Incremental.run`); `companies_<fuente>` se sigue reconstruyendo. El
primer run con este código reconstruye la silver completa (la tabla no tiene
`_ingest_date`); los siguientes recalculan solo los grupos afectados.

**Parámetros:** `catalog`, `bronze_schema`, `silver_schema`, `lookback_days`,
`from_date`, `to_date`, `force_full`, `repeat_window` (ver §9.5).

### 5.3 Gold — `gold_build.ipynb`

Une las cuatro Silver (`unionByName(allowMissingColumns=True)`), deduplica y
construye las tablas Gold con `Prepare_Gold.build_gold()` +
`write_gold()`. Escribe además `companies_complete_catalog`.

**Parámetros:** `catalog`, `silver_schema`, `gold_schema`.

### 5.4 Encadenado recomendado

```
Bronze (4 fuentes) → Silver Indeed / InfoJobs / LinkedIn / MultiSite (paralelo)
                   → Oro → Power BI
```

Recomendaciones de orquestación: tareas independientes por fuente, reintentos,
fuente, reintentos, sensor de llegada de ficheros y registro `success/partial/failed`.

La orquestación real de producción está versionada como código (Databricks Asset
Bundle): ver §9.

---

## 6. Tests

```powershell
python -m pytest databricks_notebooks/tests -q
```

Dos suites:

- `test_enrich_pure.py` — funciones puras de normalización (roles, modalidad
  multilingüe, periodos salariales, parseo numérico y catálogo de skills).
- `test_integration_spark.py` — integración con Spark:
  `enrich()`, `build_fact_offers()`, conversión de divisas,
  `build_fact_offer_skills()`, tabla vacía, reejecución idempotente,
  `dim_calendar()`, `build_gold()` y `write_gold()`.

Notas:

- Requiere Java 17/21 y `pyspark` (ver `requirements.txt`).
- La suite completa está pensada para ejecutarse en Databricks o en CI Linux.

---

## 7. Power BI

Abre `Job_Offers_Dashboard.pbip` (formato PBIP: informe PBIR + modelo TMDL) y
conecta el modelo a las tablas `job_offers.gold.*`. El modelo usa
`Fact_Offers`, `Fact_OfferSkills`, `Dim_Calendar`, `Dim_Companies`,
`Dim_SkillList`, `Dim_Currency` y las tablas geográficas del mapa.

---

## 8. Checklist rápida desde cero

1. `pip install -r requirements.txt`
2. Crear catálogo + schemas + External Location en Unity Catalog.
3. Subir `Enrich_Job_Offers_Dataframes.py` y `Prepare_Gold.py` al workspace.
4. Lanzar Bronze con `storage_account` (y resto de widgets) → 4 tablas Bronze.
5. Lanzar los 4 Silver → `silver.offers_*` + `silver.companies_*`.
6. Lanzar Oro → `gold.fact_offers`, `fact_offer_skills`, dims y `companies_complete_catalog`.
7. Abrir el PBIP y validar el informe.
8. `pytest` en verde antes de publicar.
9. Desplegar el job ELT programado como Asset Bundle (§9) y dejar que corra a diario.

---

## 9. La ELT como código: Databricks Asset Bundle, CI/CD y cutover

El job ELT de producción —**un job con 7 tareas**: Bronze → 4 Silver (en
paralelo) → Oro → export web— está **versionado como código** en el repositorio y
se despliega con la CLI de Databricks. `databricks.yml` contiene el bundle, sus
targets y la programación (la sobreescribe el target `prod`);
`resources/elt.job.yml`, la definición del job (tareas, `depends_on`, compute,
timeout y notificaciones). Los notebooks se referencian desde los ficheros
sincronizados del bundle, no desde una Git folder.

### 9.1 Targets y variables

Misma definición para ambos entornos; solo cambian las variables:

| Variable | `dev` | `prod` |
|---|---|---|
| `catalog` | `job_offers_dev` | `job_offers` |
| `checkpoints_root` | `<storage>/_checkpoints_dev` | `<storage>/_checkpoints` |
| `export_prefix` | `gold/web_export_dev` | `gold/web_export` |
| `job_name` | `Git Based ELT Dev` (el bundle antepone `[dev <usuario>]` al desplegar) | `Git Based ELT` |
| Programación | ninguna (solo runs manuales) | `50 0 23 * * ?`, `Europe/Madrid`, `UNPAUSED` |

El `sync` está acotado a `databricks_notebooks`, así que un deploy sube solo lo
que ejecuta el job.

### 9.2 Deploy

```powershell
databricks bundle validate -t dev  --profile <perfil>
databricks bundle deploy   -t dev  --profile <perfil>
databricks bundle summary  -t dev  --profile <perfil>   # comprobar paths y variables

databricks bundle validate -t prod --profile <perfil>
databricks bundle deploy   -t prod --profile <perfil>
```

`bundle deploy` **no ejecuta** el job: solo sube ficheros y actualiza la
definición. Los despliegues de producción son siempre manuales y validados; el
target de producción exige `run_as` y `permissions` (production mode).

### 9.3 Integración continua

- `.github/workflows/databricks-bundle-validate.yml`: ejecuta
  `databricks bundle validate -t dev` en `pull_request` / `push` que tocan
  `databricks.yml`, `resources/**`, `databricks_notebooks/**` o el propio
  workflow. Solo valida; nunca despliega. Secretos de repo: `DATABRICKS_HOST`,
  `DATABRICKS_TOKEN` (alcance dev).
- `.github/workflows/databricks-bundle-deploy-dev.yml`: deploy manual
  (`workflow_dispatch`) **solo a dev**, detrás del entorno `dev` de GitHub
  (*required reviewers*). El job dev no tiene programación, así que no correrá
  solo tras un deploy.

### 9.4 Programación, monitorización y rollback

La programación vive en el target `prod` (`pause_status` es un override, así que
pausar desde la UI no basta: el siguiente `bundle deploy -t prod` restauraría el
valor de la definición). Para cambiar el estado, se edita `databricks.yml`, se
commitea, se mergea y se despliega.

- Comprobación diaria: `databricks jobs list-runs --job-id <id del job prod> --limit 5`
  (esperado: exactamente un run al día, a las 23:00, `SUCCESS`).
- Fallos: el job notifica por correo (`on_failure` en `resources/elt.job.yml`).
- Qué significa cada alerta (fallo, duración anómala, frescura, ficheros
  pendientes) y cómo diagnosticar un run sin el SQL warehouse: §9.6.
- **Rollback** (volver al job manual anterior, conservado en pausa como
  respaldo): poner `pause_status: PAUSED` en el target `prod`, commit, merge,
  deploy (`bundle deploy -t prod`), verificar que el job del bundle queda
  `PAUSED` y reanudar la programación del job manual. Esperado: pausa efectiva
  < 5 min; reconciliación completa (git + deploy) < 10 min.
- El procedimiento completo de cutover/rollback, los IDs y el registro de
  ejecución de la spec 007 viven en `specs/007-ELT-As-Code/cutover.md`. Los ids
  de los jobs en uso están ahí; el job manual pausado no se borra mientras sea
  la vía de rollback.

### 9.5 Silver incremental y backfill (spec 008)

Los cuatro notebooks de ofertas escriben por `MERGE` sobre la clave semántica
(`Silver_Incremental.py`). El primer run con este código encuentra la tabla sin
`_ingest_date` y la reconstruye completa; los siguientes recalculan solo los
grupos afectados desde `max(_ingest_date) - lookback_days` (7 días).

Parámetros (widgets y parámetros de job `backfill_from`, `backfill_to`,
`repeat_window` y `force_full`, vacíos por defecto):

- `from_date` / `to_date` (`YYYY-MM-DD`): fuerzan una ventana (backfill).
- `force_full=true`: reconstrucción completa. Obligatorio tras un `RESTORE`,
  rewrite o borrado de bronce (incluido un cambio de `job_id`/`site`) y tras
  cambiar los patrones de empresa. Se puede fijar como widget o como parámetro
  de job `force_full`.
- `repeat_window=true`: ejecuta dos veces la misma ventana y devuelve
  `{"repeat_window": true, "runs": [...]}`; sirve para demostrar idempotencia.
- `lookback_days`: tamaño de la ventana, 7 por defecto.

Backfill en dev: `databricks bundle run elt -t dev --profile <perfil> --params
backfill_from=2026-10-01,backfill_to=2026-10-25,repeat_window=true`. Coste: lee
el bronce completo (~31k filas) para seleccionar los grupos candidatos pero solo
enriquece los afectados; sin SQL warehouse.

Evidencia: cada notebook devuelve un resumen JSON (modo, ventana, conteos y
tiempo) que se lee con `databricks jobs get-run-output <task_run_id>`.

Rollback: redeploy del commit anterior del bundle; si una tabla silver queda
inconsistente, `RESTORE TABLE silver.offers_<fuente> TO VERSION AS OF <n>` (SQL:
agruparlo en una sola ventana de 10 min y pedirlo antes). En prod solo en una
ventana previa a un domingo y nunca un run manual completo fuera de la
programación.

> **Deuda conocida:** `resources/elt.job.yml` versiona las direcciones de
> notificación — `on_failure` y `on_duration_warning_threshold_exceeded`, dos
> claves con el mismo correo. Es previo al bundle y el repositorio es público;
> queda como deuda aceptada (decisión del 2026-10-19). Pasarlas a un secreto está
> pendiente.

### 9.6 Alertas, consultas versionadas y diagnóstico sin warehouse (spec 011)

Hoy hay **dos superficies activas**: el correo de fallo (`on_failure` en
`resources/elt.job.yml`) y la comprobación de frescura publicada del workflow del
dashboard. El resto llega **con el deploy** (T-08/T-13): la regla de duración, el
event log y la novena tarea `Publish_Run_Summary`, que deja sus `checks` en
`ops.run_metrics`. Ese deploy está pendiente — los jobs que corren hoy (dev
`320947022571947`, prod `916863262593632`) siguen con **8 tareas**, y
`ops.run_metrics` la crea el primer `record`. Ninguna de estas superficies
necesita el SQL warehouse. Los umbrales viven en el código; esta sección dice qué
significa cada uno, con qué consulta se responde y qué hacer.

#### 9.6.1 Qué vigila cada alerta y qué hacer

| Alerta | Qué vigila | Síntoma | Primera comprobación | Acción |
|---|---|---|---|---|
| Fallo (correo `on_failure`) | cualquier tarea que acaba en `FAILED`. Una vez desplegada, `Publish_Run_Summary` corre igual (`run_if: ALL_DONE`), así que el run escribe su fila antes de fallar | llega el correo y el run está en rojo | `databricks jobs get-run <run_id>` y `databricks jobs get-run-output <task_run_id>` (§9.6.3) para el `task_key` que falla | leer la salida de esa tarea (traza o `state_message`), corregir la causa y relanzar el job; aquí no hace falta SQL |
| Duración anómala (regla `health.rules[]` + correo `on_duration_warning_threshold_exceeded`; **actúa al desplegarse**, T-08/T-13) | `RUN_DURATION_SECONDS` **> 1500 s** (25 min); el job se cancela a los `timeout_seconds: 1800` (30 min) | el correo de duración llega **con el run aún vivo** | `databricks jobs get-run <run_id>` (gratis, segundos después de arrancar) y la consulta 2 (§9.6.2) para las duraciones por tarea. Las columnas `*_duration_seconds` de `job_run_timeline` quedan a `0` en jobs multi-tarea: no usarlas | es un aviso, no un fallo: anotar la tarea y abrir reparación si se repite. A los 1800 s el run se cancela y se trata como un fallo |
| Frescura publicada (workflow del dashboard) | `.github/workflows/dashboard.yml` (lunes 01:00 UTC) aborta **sin desplegar** si `meta.json.data_date` tiene más de 8 días | el workflow está en rojo y la web no se actualiza | `databricks jobs list-runs --job-id <id del job prod> --limit 5`: ¿hubo run del domingo y salió bien? Después, `jobs get-run`, consulta 1 y, una vez desplegada, `checks.freshness` | si el job no corrió (pausado o rollback en curso), reanudarlo; si corrió en verde, los scrapers no trajeron nada nuevo: relanzarlos y después el job |
| Frescura del run (tarea final; **activa al desplegarse**, T-08/T-13) | `Publish_Run_Summary` falla cuando `checks.freshness.ok` es falso: sin `data_date`, fecha futura o **> 8 días** | la novena tarea está en rojo con las ocho de negocio en verde | `databricks jobs get-run-output <task_run_id>`: con la tarea **en verde** devuelve el JSON de cierre con `checks`; si la tarea **falla**, devuelve la **traza de error** (los `checks` van en el mensaje del `RuntimeError`), no el JSON. `jobs get-run` muestra el `state_message`; `ops.run_metrics` guarda la fila en Delta cuando exista | si `data_age_days > 8`, la entrada está vieja: relanzar los scrapers y luego el job. Una fecha `null` significa `meta.json` corrupto: revisar la salida de `Build_for_Web-Dashboard` |
| Ficheros pendientes (tarea final; **activa al desplegarse**, T-08/T-13) | `Publish_Run_Summary` falla cuando `checks.pending_files.ok` es falso: ficheros anteriores al inicio del run que ningún lote consumió **y** cero filas de bronce, **o** `listing_ok` falso (no se pudo listar la landing o el checkpoint, así que no hay prueba de backlog vacío) | la novena tarea está en rojo y `checks.pending_files` muestra pendientes en `by_source`, o `listing_ok: false` | `databricks jobs get-run-output <task_run_id>`: verde ⇒ JSON de cierre con `checks`; fallo ⇒ **traza de error**, con los `checks` en el mensaje del `RuntimeError`. Después, el listado barato de metadatos de `landing/<fuente>/` y del `commits/` del checkpoint | pendientes: el Auto Loader no vio los ficheros — revisar la ruta y el checkpoint (`checkpoints_root`), corregir y relanzar; una vez consistente el bronce, el silver necesita `force_full=true` (§9.5). `listing_ok: false`: arreglar el listado (ruta, credenciales) y relanzar — el run está en rojo a propósito, no hay verde falso |

#### 9.6.2 Consultas versionadas

`:job_id` y `:run_id` son parámetros de consulta: en un editor SQL se enlazan y en
una tarea se sustituyen por valores literales. Se **validan en T-07** (una única
sesión SQL) y hasta entonces son `[NO VERIFICADO]`. Dentro de un run, como tarea en
el clúster del job, la consulta **no despierta el SQL warehouse** (consume los DBUs
del propio run). En el editor SQL además despierta el warehouse (~0,43 € por
despertar), así que solo cuando un run no sea opción y agrupando las consultas en
una sola ventana de 10 minutos.

**1. Últimos runs del job, con su duración real** (`system.lakeflow.job_run_timeline`):

```sql
SELECT run_id, job_id, trigger_type,
       max_by(result_state, period_end_time)       AS result_state,
       max_by(termination_code, period_end_time)   AS termination_code,
       min(period_start_time) AS run_start,
       max(period_end_time)  AS run_end,
       sum(unix_timestamp(period_end_time) - unix_timestamp(period_start_time)) AS run_seconds
FROM system.lakeflow.job_run_timeline
WHERE workspace_id = current_workspace_id()
  AND job_id = :job_id
  AND period_start_time >= current_timestamp() - INTERVAL 30 DAYS
GROUP BY run_id, job_id, trigger_type
ORDER BY run_start DESC
```

`workspace_id = current_workspace_id()` no es decorativo: `job_id` solo es único
**dentro de un workspace**, y la tabla sirve a todos los workspaces de la cuenta.
El `GROUP BY` usa `run_id`, `job_id` y `trigger_type` porque `result_state` y
`termination_code` son **NULL** en las filas intermedias de runs de más de 1 h;
agrupar por ellas partiría un run en varias filas, así que se agregan con
`max_by(…, period_end_time)`. Las columnas
`setup/queue/execution/run/cleanup_duration_seconds` **solo se rellenan en jobs de
una tarea creados con las APIs legacy**; este job es `MULTI_TASK` (9 tareas), así
que devuelven `0` y la duración se suma por periodos. La tabla puede tardar **hasta
~1 h** en reflejar un run y, para un run en curso, la fuente es
`databricks jobs get-run` (gratis).

**2. Tareas de un run** (`system.lakeflow.job_task_run_timeline`). Aquí `run_id` es
el run de la **tarea**; el run del job es `job_run_id`:

```sql
SELECT task_key,
       result_state,
       setup_duration_seconds,
       execution_duration_seconds,
       cleanup_duration_seconds,
       period_start_time,
       period_end_time
FROM system.lakeflow.job_task_run_timeline
WHERE workspace_id = current_workspace_id()
  AND job_run_id = :job_run_id
ORDER BY period_start_time
```

`:job_run_id` es el run del **job**; en esta tabla `run_id` es el run de la
**tarea**, así que filtrar por `run_id` mezclaría tareas de otros workspaces.

**3. Uso (DBUs) por run y SKU** (`system.billing.usage`, ventana de 30 días). El
campo `usage_metadata.job_run_id` se valida en T-07:

```sql
SELECT usage_metadata.job_id     AS job_id,
       usage_metadata.job_run_id AS job_run_id,
       sku_name,
       sum(usage_quantity)       AS dbus
FROM system.billing.usage
WHERE workspace_id = current_workspace_id()
  AND usage_date >= current_date() - INTERVAL 30 DAYS
  AND usage_metadata.job_id = :job_id
GROUP BY usage_metadata.job_id, usage_metadata.job_run_id, sku_name
ORDER BY dbus DESC
```

El mismo filtro `workspace_id`, por el mismo motivo: `job_id` se repite entre
workspaces. Son **DBUs, no euros**: para dinero, multiplicar por el precio del SKU
en `system.billing.list_prices`; ese cruce también se valida en T-07.

#### 9.6.3 Diagnóstico sin warehouse, en tres pasos

1. **Runs**: `databricks jobs list-runs --job-id <id del job prod> --limit 5 --profile <perfil>`
   — estado, trigger y tiempos de los últimos runs (0 €).
2. **Un run**: `databricks jobs get-run <run_id> --profile <perfil>` (o
   `databricks api get "/api/2.1/jobs/runs/get?run_id=<run_id>"`) da el JSON con
   cada tarea, su `result_state` y `state_message`. Y
   `databricks jobs get-run-output <task_run_id>` da lo que publicó la tarea: con
   la tarea **en verde**, el JSON de `dbutils.notebook.exit` — para
   `Publish_Run_Summary`, los `checks` de cierre; si la tarea **falló**, la
   **traza de error** (los `checks` van en el mensaje del `RuntimeError`). El
   comportamiento en fallo está documentado por la API, pero aún no se ha
   ejercitado en este repositorio (`[NO VERIFICADO]` hasta el primer run
   instrumentado). Ambos
   comandos son gratis y son la vía más rápida a «qué tarea y por qué»; una vez
   desplegado, `ops.run_metrics` guarda la misma fila en Delta para el histórico.
3. **Event log**: una vez desplegado, el clúster del job lo escribe en
   `abfss://<container>@<storage_account>.dfs.core.windows.net/_eventlogs/`
   (`spark.eventLog.enabled`, `spark.eventLog.dir` y rolling en
   `resources/elt.job.yml`), que resuelve a `landing/_eventlogs/`. **dev y prod
   comparten esa ruta** (no hay prefijo por target), así que T-08 tendrá que
   separar los bytes por run al medir. No sustituye a la Spark UI, que sigue
   disponible ~60 días tras el run: aporta retención más allá de esa ventana, un
   fichero que se puede compartir y análisis offline. Para reproducir un run, el
   notebook *Event Log Replay* en un clúster single-node — la pestaña **Event
   log** del cómputo es el log de ciclo de vida del clúster, no este fichero.
   **T-08** mide su tamaño en bytes, y si compensa, antes de decidir que se queda.
   Si tras el primer run `landing/_eventlogs/` queda vacío o solo con ficheros
   `.inprogress` (el driver puede destruirse al terminar), la alternativa
   soportada es **compute log delivery**, y T-08 decide con los bytes medidos.

#### 9.6.4 Descartado y por qué

- **Métricas `STREAMING_BACKLOG_*`** (`_BYTES`, `_RECORDS`, `_SECONDS`,
  `_FILES`): estos streams corren por lotes (`availableNow`), así que no hay un
  backlog continuo que vigilar; además la métrica está en **Public Preview** y la
  tarea final ya falla si hay ficheros pendientes.
- **Alertas SQL programadas**: cada evaluación **despierta el warehouse** (~0,43 €)
  y es recurrente. Las alertas del propio job (`on_failure` hoy; la regla de
  duración y los `checks` de la tarea final al desplegarse) cubren lo mismo gratis.
