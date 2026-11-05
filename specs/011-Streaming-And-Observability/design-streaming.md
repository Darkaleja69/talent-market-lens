# Diseño — mecanismo de ingesta en streaming

> **Estado (2026-11-02, T-01):** reconocimiento gratuito cerrado. Este documento
> fija el **estado de partida**, los **umbrales** y el **marco de evaluación**; la
> **decisión** (adoptar / adoptar parcialmente / no cambiar) se escribe en T-10,
> después de la demo en Free Edition (**§9**, 2026-11-02). Las secciones 4–6 (banco
> local, comparación de triggers e instrumentación del bronce) están cerradas en T-02
> y T-03.
>
> Rama `spec/011-streaming-observability`, worktree `talent-market-lens-011`.
> Cada afirmación lleva su fuente (fichero y línea, o comando) o va marcada
> `[NO VERIFICADO]`. **Coste del reconocimiento: 0 €** —0 runs, 0 clústeres,
> 0 consultas SQL, 0 pipelines en el workspace pagado—; solo API de Unity Catalog,
> API de Jobs y listado de AzCopy. (La demo de T-09b creó **2 pipelines** en Free
> Edition, fuera del workspace pagado.)

## 1. Estado de partida del mecanismo (verificado en código)

### 1.1 Fuentes, claves y columna de captura

| Fuente | Prefijo en landing | Clave(s) en bronce | Columna de captura | Tabla destino | Cuarentena |
|---|---|---|---|---|---|
| `indeed` | `landing/indeed/` | `job_key` | `scraped_at` | `<catalog>.bronze.indeed` | `<catalog>.bronze.indeed_quarantine` |
| `infojobs` | `landing/infojobs/` | `id_oferta` | `fecha_scraped` | `<catalog>.bronze.infojobs` | `<catalog>.bronze.infojobs_quarantine` |
| `linkedin` | `landing/linkedin/` | `job_id` | `scraped_at` | `<catalog>.bronze.linkedin` | `<catalog>.bronze.linkedin_quarantine` |
| `multi_site` | `landing/multi_site/` | `site` + `job_id` | `scraped_at` | `<catalog>.bronze.multi_site` | `<catalog>.bronze.multi_site_quarantine` |

Fuente: `databricks_notebooks/Upsert_Job_Offers.py` (`SOURCE_SPECS`, L74–79) y las
cuatro celdas de `databricks_notebooks/bronze_ingest.ipynb`. Las claves y la
columna de captura **no se tocan** (contrato de bronce, spec §Decisiones).

### 1.2 Columnas de captura y control

Añadidas en la lectura del stream o en el handler del microbatch
(`bronze_ingest.ipynb`, celdas 1–4; `Upsert_Job_Offers.py`):

| Columna | Expresión | Nota |
|---|---|---|
| `_ingest_date` | `current_date()` | constante por lote; **solo desempata** |
| `_source_file` | `_metadata.file_path` | fichero de origen |
| `_scraped_ts` | `try_to_timestamp(<columna de captura>)` | auxiliar; se descarta antes del `MERGE` |
| `_row_hash` | `sha2(to_json(struct(<columnas de fuente>)), 256)` | huella de la fila para cuarentena |
| `_quarantine_reason` | `missing_key` \| `missing_scraped_at` | motivo; `missing_key` gana si concurren |
| `_quarantine_at` | `current_timestamp()` | instante de cuarentena |

Fuente: `Upsert_Job_Offers.py` L36–45, L124–179 y L182–203. La zona horaria de la
sesión se fija a `UTC` (`bronze_ingest.ipynb` L81) para que `_row_hash` sea estable.

### 1.3 Checkpoint y recuperación

- Un checkpoint **por fuente**: `{checkpoints_root}/<fuente>/ckpt` para
  `checkpointLocation` y `{checkpoints_root}/<fuente>/schema` para
  `cloudFiles.schemaLocation` (`bronze_ingest.ipynb` L217, L223 y análogos).
- Raíz por entorno (`databricks.yml`): dev `_checkpoints_dev` (L37), prod
  `_checkpoints` (L55), ambas en `abfss://landing@joboffershns.dfs.core.windows.net`.
- Auto Loader con `cloudFiles.useNotifications = false` (listado de directorio, no
  notificaciones) y formato `parquet`.
- Semántica: el checkpoint fija los ficheros ya consumidos; la idempotencia de
  negocio la aporta el `MERGE` por clave, no el checkpoint.

### 1.4 Cuarentena

- Tabla append-only `bronze.<fuente>_quarantine`, escritura con
  `mergeSchema = true` (`bronze_ingest.ipynb` L152–157).
- Identidad de una fila en cuarentena: `(_row_hash, _quarantine_reason,
  _source_file)`; el lote se deduplica por esa terna y se descartan las filas ya
  guardadas (`new_quarantine_rows`, `Upsert_Job_Offers.py` L291–311). La
  comprobación lee **solo los ficheros del lote**, no la tabla completa.
- Una fila va a cuarentena si alguna clave es `NULL`/vacía (`missing_key`) o si la
  columna de captura es `NULL`, vacía o no parseable (`missing_scraped_at`).
- Las filas inválidas **nunca** entran al `MERGE`.

### 1.5 Trigger, paralelismo y cierre

- `trigger(availableNow=True)` en las cuatro consultas: consumen lo pendiente y
  terminan (batch-like).
- Las cuatro consultas arrancan en paralelo en la misma tarea; cada una usa su
  propia vista temporal (`__batch_<fuente>`, `bronze_ingest.ipynb` L112).
- Cierre con `awaitTermination()` de las cuatro: un `MERGE` fallido **tumba la
  tarea** (`bronze_ingest.ipynb` L438–440).
- **No hay resumen publicado:** el notebook no llama a `dbutils.notebook.exit` ni
  a `dbutils.jobs.taskValues` (última celda del notebook). Es el hueco que cierra
  T-03.

### 1.6 Estado del landing y de los checkpoints (2026-11-02; solo listado, 0 €)

Inventario medido con `azcopy list` (listado de blobs; la SAS `LANDING_SAS_TOKEN`
vive en el entorno y **no se ha impreso**):

| Fuente | Ficheros Parquet | Tamaño total | Carpetas `dia=` | Modificación más antigua (UTC) | Fichero más reciente | Última modificación (UTC) |
|---|---|---|---|---|---|---|
| `indeed` | 85 | 36.277.853 B (34,6 MiB) | 43 | 2026-07-23 | `dia=2026-11-01/indeed_jobs_20261102_0033.parquet` | 2026-11-02T03:11:40Z |
| `infojobs` | 509 | 74.295.928 B (70,9 MiB) | 38 | 2026-07-23 | `dia=2026-11-01/offers_20261101_233012.parquet` | 2026-11-01T22:48:06Z |
| `linkedin` | 5.826 | 31.545.513.816 B (29,4 GiB) | 59 | 2026-07-27 | `dia=2026-11-01/jobs_new_20261102_000514.parquet` | 2026-11-01T23:05:18Z |
| `multi_site` | 236 | 662.984.909 B (632,3 MiB) | 51 | 2026-07-28 | `dia=2026-10-31/jobs_unified_new_20261101_033851.parquet` | 2026-11-01T02:38:56Z |
| **Total** | **6.656** | **32.319.072.506 B (30,1 GiB)** | — | — | — | — |

Comando (por fuente, con la SAS en el entorno):

```powershell
azcopy list "https://joboffershns.blob.core.windows.net/landing/<fuente>$env:LANDING_SAS_TOKEN" `
  --machine-readable --properties LastModifiedTime --output-type json
```

Hechos que se extraen del listado:

- El landing no es plano: cada scraper escribe en subcarpetas `dia=YYYY-MM-DD/`, y
  en `linkedin` además en subcarpetas de ejecución
  `scrapers-pipeline-linkedin-<fecha>-<ticks>/` (`scrapers-pipeline/recover_and_upload.ps1`
  L434 construye el destino `.../<source>/dia=<$day>/`). Auto Loader lee el prefijo
  de la fuente de forma recursiva (`.load(f"{STORAGE_BASE}/<fuente>/")`).
- La subida de cada día deja un marcador de cierre
  `landing/_READY/dia=YYYY-MM-DD.txt`, escrito en `recover_and_upload.ps1`
  L623–630 y `run_scrapers_and_upload.ps1` L756–760 (la comprobación de si ya
  existe es `Test-ReadyPublished`, `recover_and_upload.ps1` L506–521). Hoy hay
  **63 marcadores** y el último es `dia=2026-11-01.txt` (modificado
  2026-11-02T03:11:44Z): el lote del 2026-11-01 está **completo en el landing y
  pendiente de ingesta**.
- El plan describe «linkedin: 1 acumulativo renombrado» **por run**; el prefijo
  conserva **5.826 Parquet históricos** (29,4 GiB). Es el prefijo más pesado con
  diferencia y el que más justifica medir el coste del listado de Auto Loader.
- El último commit de los cuatro checkpoints es del **2026-10-31 17:07–17:08 UTC**
  (18:07–18:08 en Madrid), coherente con los dos runs `ONE_TIME` del A/B de la 010
  del 2026-10-31 (17:58–18:28 Madrid, API de Jobs).
- Desde entonces **no se ha ingerido nada** (prod quedó `PAUSED`; ver §1.7).

Ficheros pendientes respecto al último commit del checkpoint (mismo listado, 0 €;
un fichero con `LastModified` posterior al último commit no está en ningún lote
confirmado):

| Fuente | Último commit del checkpoint | Ficheros posteriores | Bytes | Pendiente más antiguo (UTC) |
|---|---|---|---|---|
| `indeed` | 2026-10-31T17:08:04Z | 5 | 2.870.543 | 2026-11-01T02:35:45Z |
| `infojobs` | 2026-10-31T17:07:41Z | 20 | 1.925.783 | 2026-10-31T22:53:19Z |
| `linkedin` | 2026-10-31T17:08:00Z | 2 | 543.856 | 2026-11-01T00:09:22Z |
| `multi_site` | 2026-10-31T17:07:41Z | 1 | 72.199 | 2026-11-01T02:38:56Z |
| **Total** | — | **28** | **5.412.381** | — |

Esto es exactamente la señal que la tarea final (T-05) calculará en el run, pero
allí con el **inicio del run** como corte (lo que llega durante el run no cuenta).
Hoy hay 28 ficheros pendientes porque el job de producción ha estado pausado; el
siguiente run programado (domingo 2026-11-08, 23:00 `Europe/Madrid`) los ingerirá.

### 1.7 Reposición del job de producción (2026-11-02)

- `databricks.yml`, `targets.prod.resources.jobs.elt.schedule.pause_status`:
  `PAUSED` → **`UNPAUSED`**. Único cambio del fichero.
- `databricks bundle validate -t prod --profile vs_code` → `Validation OK!`.
- `databricks bundle deploy -t prod --profile vs_code --auto-approve` → 24 ficheros
  subidos, `Resources: 0 created, 1 changed, 3 unchanged`. (El CLI escribe el
  progreso por `stderr` y PowerShell lo marca como error; el deploy terminó y el
  estado se verificó con `jobs get`.)
- Job del bundle `916863262593632` (`Git Based ELT`): **`UNPAUSED`**, cron
  `50 0 23 ? * SUN`, `Europe/Madrid`, `timeout_seconds: 1800`,
  `max_concurrent_runs: 1`, 8 tareas. Job manual `704156530681034`: **`PAUSED`**.
- No se creó ningún run con el deploy (el último run del job del bundle es del
  2026-10-31 y el último `PERIODIC`, del 2026-10-25; API de Jobs).
- **Desviación consciente (2026-11-02):** el deploy de prod se hace **desde la rama
  de la spec**; el contenido sincronizado es idéntico a `main` (`git status` sin
  cambios en `databricks_notebooks/` ni `resources/`; la rama no tiene commits
  propios) y lo único distinto es `pause_status`. Se hará merge de la rama en el
  cierre (T-14).
- **No volver a ejecutar `bundle deploy -t prod` desde esta rama hasta
  T-12/T-13:** cualquier deploy desde aquí subiría el código en curso (notebooks y
  módulos nuevos de la 011, aún sin validar). El siguiente deploy de prod debe
  hacerse desde `main`, con el código fusionado y validado.

### 1.8 Lo que este reconocimiento no puede cerrar

- `numInputRows`, `batchId`, duración del microbatch y backlog del motor: hacen
  falta un run instrumentado y `query.lastProgress` (T-03 y T-08).
- Contenido del checkpoint (offsets, esquema): solo se ha verificado su existencia
  y la fecha del último commit por listado; leerlo exige un run o acceso a los
  ficheros → `[NO VERIFICADO]`.
- Tiempo y coste reales del bronce: se citan los de la 010 (§2), no se recalculan.

## 2. Números medidos que se usan como línea base (010; citados, no recalculados)

De `spec.md` §2.2 y `plan.md` §2.2 (mediciones de la spec 010):

- Bronce: **113–134 s** de ejecución y **382–383 s** de arranque del clúster (dos
  runs); el arranque es el **41–47 %** del run.
- Run completo: **816–985 s en dev** y **747–915 s en producción**, con
  `timeout_seconds: 1800` y `max_concurrent_runs: 1`.
- Lote real de producción (010 T-09): `MERGE` con **316 / 850 / 1.788 / 728** filas
  actualizadas (`indeed` / `infojobs` / `linkedin` / `multi_site`) e
  `inserted = deleted = 0`.
- Layout: **18 ficheros** en bronce; `infojobs` es la fuente con más ficheros
  pequeños (**5 × 0,43 MB**).
- Coste: run de dev **0,17–0,24 €**; run de producción **0,20–0,24 €**; sesión SQL
  **~0,43 €** (2X-Small, 10 min facturables). Cero sesiones SQL en la 010.

Estos números **no se han vuelto a medir aquí** (0 runs). Cualquier comparación
posterior usa estos umbrales y se valida en T-08/T-11.

## 3. Criterios de decisión (fijados antes de medir)

Copiados de `plan.md` §3 (2026-11-01); **no se han cambiado** después de empezar a
medir.

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

Empate ⇒ **no cambiar**. «Adoptar parcialmente» solo si las piezas adoptadas
(expectativas, event log, métricas) se justifican por sí mismas y no arrastran la
migración de tablas.

## 4. Banco local y práctica del dominio 3 (T-02, 0 €)

`databricks_notebooks/streaming_lab.py` es un banco **didáctico**: ningún notebook
ni tarea del bundle lo importa y todos sus sumideros viven en directorios
temporales. Corre con PySpark local (`local[2]` en aislamiento; en la suite completa
puede reutilizar la sesión del módulo anterior, `local[1]`, y la semántica no depende
de los cores) y **sin Delta**: el banco prueba la semántica de Structured Streaming, no
la sintaxis de Delta. Los tests están en
`databricks_notebooks/tests/test_streaming_lab.py` (**6 passed**, 95–169 s de módulo
según la carga del host) y cada escenario devuelve los números observados, no los
esperados. Los cinco escenarios se ejecutan **una sola vez** por módulo de test y los
tests afirman sobre ese resultado.

Requisito de entorno en Windows: Hadoop necesita `winutils.exe` (y `hadoop.dll`)
para `chmod`; sin él Spark no escribe ni Parquet ni checkpoints. El helper
`tests/_local_spark.py` detecta `%LOCALAPPDATA%\hadoop\bin`, fija `HADOOP_HOME` y,
si falta, **salta** los tests con instrucciones (en Linux/CI no hace falta). El
`conftest.py` global de `databricks_notebooks/tests/` llama a
`configure_hadoop_home()` **al importar**, antes de que ningún módulo de test cree
una `SparkSession`: el JVM cachea `hadoop.home.dir` al arrancar y, si otro módulo
creaba la sesión primero, T-02 se saltaba y los módulos que ramifican por
`HADOOP_HOME` seguían por su fallback. El efecto buscado es el contrario: que
`test_web_export.py`, `test_pbi_export.py` y `test_integration_spark.py` usen el
camino real de Spark, que es el que se quiere verificar. Sin fixture `spark` ni
restauración de entorno: el proceso de pytest arranca limpio en cada ejecución.

| # | Escenario (`plan.md` §5) | Qué se observa | Números medidos |
|---|---|---|---|
| 1 | `checkpoint_restart` | El checkpoint reanuda en el offset confirmado: la segunda ejecución lee **solo** el fichero nuevo | run 1: 2 filas (`batchId` 0); run 2: 1 fila (`batchId` 1); sumidero 3 |
| 2 | `microbatch_triggers` | `availableNow`, `once` y `processingTime` generan microbatches distintos | `availableNow` 3 batches / 3 filas; `once` 1 batch / 1 fila (2 pendientes); `processingTime` sigue viva y acumula ≥ 2 batches |
| 3 | `watermark_late_event` | El checkpoint restaura el watermark y descarta el evento tardío; `stateOperators` sale en `lastProgress` | 2 ventanas → 3; la ventana 11:50 **no** aparece (el descarte se evidencia ahí); `numRowsDroppedByWatermark` presente y **0** |
| 4 | `exactly_once` | El checkpoint evita el reproceso; sin él el sumidero append duplica; el upsert por clave lo arregla | con checkpoint 3 filas y 0 en la 2.ª; sin checkpoint 6; upsert 3 |
| 5 | `foreach_batch_upsert` | `append` duplica el microbatch reprocesado; el upsert por clave deja una fila con la captura más reciente | append 6 filas; upsert 2 filas (2 claves) |

Lecciones que explican el run real:

- **Exactly-once = checkpoint + sumidero idempotente.** El checkpoint fija los
  ficheros ya consumidos, pero no es transaccional con la escritura: si el proceso
  muere entre el commit del offset y la escritura, el relanzamiento puede repetir ese
  lote (*at-least-once*). La garantía de negocio la da el `MERGE` por clave del
  bronce (`Upsert_Job_Offers.merge_sql`), no el checkpoint.
- **El watermark es estado, no una config del lote.** Vive en el checkpoint y se
  restaura al relanzar; por eso un evento tardío se descarta aunque llegue en otra
  ejecución.
- **`availableNow` no es «una sola pasada»:** procesa todo lo pendiente en **varios**
  microbatches y termina. De ahí que el resumen del bronce (T-03) publique el último
  microbatch y el total del run.
- **`stateOperators`** en `lastProgress` es la vista del estado del motor
  (`operatorName`, `numRowsTotal`, `numRowsUpdated`, `numRowsRemoved`,
  `numRowsDroppedByWatermark`): dice si hay estado y cuántas filas actualiza o
  elimina. `stateStoreSave` **no** es un campo de `stateOperators`: vive en
  `lastProgress.durationMs`. **Ojo:** `numRowsDroppedByWatermark` es el contador del
  motor y en este escenario vale **0**; el evento tardío se descarta antes de entrar
  al estado y se evidencia por la **ausencia de la ventana 11:50**, no por ese
  contador.

## 5. Comparación de triggers y tabla de tiempos (T-02, 0 €)

Medido en `local[2]` (PySpark 4.2.0, Hadoop 3.5.0) con `maxFilesPerTrigger = 1` y un
fichero JSON por microbatch:

| Trigger | Microbatches | Filas | Termina sola | Lo que no consume |
|---|---|---|---|---|
| `availableNow=True` | **3** (uno por fichero) | 3 | sí, tras drenar | nada: deja el checkpoint al día |
| `once=True` | **1** | 1 | sí, tras **un** batch | los ficheros publicados después; quedan pendientes |
| `processingTime="200 ms"` | ≥ 2 mientras corre | 4 (recoge un fichero añadido en marcha) | no: hay que pararla | nada, pero el clúster debe seguir vivo |

Nota de runtime: en Spark 4.2, `once` **ignora** `maxFilesPerTrigger` y lee en un
solo batch todos los ficheros presentes al arrancar (aviso del motor: *The read limit
MaxFiles: 1 ... is ignored when Trigger.Once is used*); lo que llega después queda
pendiente para la siguiente ejecución `once`. El test afirma ese comportamiento sin
fijar el número exacto de ficheros por batch.

Por qué el bronce usa `availableNow`: el job es semanal y batch-like, necesita drenar
todo lo pendiente en una ejecución y **terminar** (el clúster se apaga y el coste es
por run). `once` dejaría trabajo pendiente si el lote trae más ficheros de los que
caben en un trigger, y `processingTime` exigiría un clúster permanente, descartado en
`plan.md` §4 por el techo de coste.

Tabla de tiempos del banco (`run_all`, una sola ejecución por módulo de test):

| Escenario | ms |
|---|---:|
| `checkpoint_restart` | 11.553 |
| `microbatch_triggers` | 24.169 |
| `watermark_late_event` | 17.165 |
| `exactly_once` | 14.528 |
| `foreach_batch_upsert` | 14.363 |
| **Total de escenarios** | **81.778** |
| **Módulo completo** (arranque de Spark incluido) | **98,1 s** (proceso: 99,7 s) |

Medida de **una ejecución concreta** de este host. Depende mucho de la carga: en las
tres ejecuciones registradas los escenarios fueron **80,6 / 81,8 / 137,2 s** y el
módulo completo **96,9 / 98,1 / 169,2 s** (el criterio «~3 min» se cumple, con menos
margen en la máquina cargada). La cifra de 137,2 s es la del host con carga; el resto,
en reposo.

Caveat de medida: son tiempos de un host Windows con PySpark local; no representan el
runtime de Databricks (`17.3.x-scala2.13`) ni el bronce real. La validación de tiempos
del bronce es T-08/T-11.

## 6. Instrumentación del bronce (T-03, 0 €)

`databricks_notebooks/Bronze_Summary.py` es un módulo **puro** (solo `json`, sin
`pyspark`) que construye el resumen del streaming; el notebook solo coordina: recoge
`query.lastProgress`, `query.recentProgress` y `query.status` de las cuatro consultas,
llama al módulo y publica
`dbutils.jobs.taskValues.set("bronze_summary", <cadena JSON>)` y
`dbutils.notebook.exit(<cadena JSON>)`.

Contrato (aditivo y versionado sobre `plan.md` §6):

| Clave | Regla |
|---|---|
| `version` | `1` |
| `streams[]` | una por fuente, **ordenadas por `source`**; fuentes duplicadas ⇒ `ValueError` |
| `config` | `catalog`, `bronze_schema`, `checkpoints_root`, `trigger="availableNow"` |

Por fuente:

| Clave | Origen y regla |
|---|---|
| `source` | nombre de la fuente |
| `batch_id` | `lastProgress["batchId"]`; `null` si no hubo microbatch |
| `num_input_rows` | `lastProgress["numInputRows"]`; `0` en un lote vacío real, `null` sin progreso |
| `num_input_rows_total` | suma de `numInputRows` de `recentProgress` (todos los microbatches de la consulta); `0` si la lista está vacía, `null` sin progreso |
| `duration_ms` | `trigger_execution`, `add_batch`, `wal_commit`; las tres claves siempre, `null` si el motor no las expone |
| `input_rows_per_second` / `processed_rows_per_second` | del progreso; `NaN`/`±Infinity` se publican como `null` |
| `state_operators` | copia de `lastProgress["stateOperators"]`; `[]` si no está |
| `backlog` | solo si `sources[].metrics` expone `numFilesOutstanding`/`numBytesOutstanding`; si no, `null` |
| `checkpoint` | ruta del checkpoint (`…/<fuente>/ckpt`) |
| `quarantined_rows` | filas **nuevas** en cuarentena del run; suma por lote, idempotente ante reintentos |
| `status` | `query.status["message"]` (p. ej. `Stopped`) |
| `note` | solo si no hubo microbatch (`"no microbatch reported by the query"`); aditivo |

Contrato de acciones (RF-2):

- El handler sustituye `new_rows.isEmpty()` por `persist()` + `count()` + escritura +
  `unpersist()` sobre el **frame pequeño de cuarentena ya calculado**: **una acción por
  una**, coste O(filas inválidas).
- **Prohibido**: `count()` del lote, `DESCRIBE HISTORY` por fuente y lecturas nuevas de
  la landing.
- El conteo se indexa por `(source, batch_id)`: un reintento de microbatch sobrescribe
  su entrada en vez de sumar dos veces.
- Si `SOURCES` y `QUERIES` no cuadran (una quinta fuente o cualquier desajuste de
  longitud), la celda final **falla antes de publicar**: no hay resúmenes cruzados.

Ejemplo (sintético, 4 fuentes; recortado):

```json
{"config":{"bronze_schema":"bronze","catalog":"job_offers","checkpoints_root":"abfss://…","trigger":"availableNow"},
 "streams":[{"source":"indeed","batch_id":12,"num_input_rows":40,"num_input_rows_total":50,
   "duration_ms":{"trigger_execution":4000,"add_batch":3500,"wal_commit":12},
   "input_rows_per_second":null,"processed_rows_per_second":60.0,
   "state_operators":[],"backlog":{"files_outstanding":3,"bytes_outstanding":2048},
   "checkpoint":"abfss://…/indeed/ckpt","quarantined_rows":3,"status":"Stopped"},
  {"source":"multi_site","batch_id":4,"num_input_rows":0,"num_input_rows_total":0,
   "duration_ms":{"trigger_execution":null,"add_batch":null,"wal_commit":null},
   "input_rows_per_second":null,"processed_rows_per_second":null,
   "state_operators":[],"backlog":null,"checkpoint":"abfss://…/multi_site/ckpt",
   "quarantined_rows":0,"status":"Stopped"}],
 "version":1}
```

`[NO VERIFICADO]` hasta T-08: contenido real de `lastProgress`/`status` en DBR 17.3,
límite de 48 KiB de `taskValues`, y que el conteo no añada etapas (aquí solo hay
contraste estático). `walCommit` es la única clave de commit mapeada (`commitOffsets`
existe en el progreso real y no se publica). `recentProgress` retiene hasta 100
entradas (`spark.sql.streaming.numRecentProgressUpdates`), suficiente para el bronce:
**T-04 usa `num_input_rows_total` para `bronze_rows`**, no `num_input_rows`.

## 8. Evaluación del candidato declarativo (Free Edition + extracto CSV)

Decisión del **2026-11-02** (`plan.md` §8.1): el candidato Lakeflow se evalúa en
una **cuenta Free Edition aparte** con un **extracto CSV** generado desde un
Parquet real del landing. **No se crea ningún pipeline `serverless` en el
workspace pagado** (se evita el DBU; Free Edition ya es serverless y es gratuita).

Qué valida y qué no (regla de adopción de `plan.md` §8.2, sin cambios):

- **Valida** semántica y contrato: columnas y claves por fuente, secuencia por
  instante de captura, cuarentena por motivo, exactly-once en la segunda
  ejecución, líneas de código propio y observabilidad nativa (event log, grafo,
  métricas de expectativas).
- **No valida** tiempo ni coste de producción: el entorno y los datos no son los
  mismos. El coste de producción será una **estimación** sujeta a aprobación
  explícita, marcada `[NO VERIFICADO]`.
- Se ejecuta cada mecanismo **dos veces** con los mismos CSV (segunda sin ficheros
  nuevos) y se traen capturas saneadas (T-09/T-09b).

`[NO VERIFICADO]` a fecha de hoy (se cierra en T-09/T-09b/T-10):

- Si la cuenta Free Edition permite `AUTO CDC` y qué límites de UI tiene. `[NO VERIFICADO]`
- Si `AUTO CDC` reproduce exactamente el contrato (claves, secuencia, cuarentena
  con `_row_hash`). `[NO VERIFICADO]` — se comprueba con la paridad del extracto.
- Duración de una ejecución con el extracto y comportamiento de la cuota de Free
  Edition. `[NO VERIFICADO]`
- Estimación del coste de producción del candidato. `[NO VERIFICADO]`
- Cobertura del extracto sobre el esquema completo de las cuatro tablas: el kit
  (T-09) conserva todas las columnas del Parquet real y lo declara en su
  manifiesto; la paridad total se valida en el cutover (T-12). `[NO VERIFICADO]`

El plan local (T-02) prueba la semántica de streaming en `local[2]` (checkpoint,
microbatch, watermark, estado y exactly-once con sumidero idempotente) y **no**
depende de Free Edition; su tabla de tiempos se añadirá a este documento en §5.

## 9. Decisión (2026-11-02)

Cierra **T-10** con la evidencia de T-09b (dos mecanismos sobre los mismos CSV en
Free Edition) y el diff de esquemas. **0 €**: 0 runs en el workspace pagado, 0 SQL,
0 pipelines nuevos en el workspace pagado (T-09b creó **2 pipelines** en Free Edition).

### 9.1 Puertas duras

| Puerta | Resultado | Evidencia |
|---|---|---|
| Contrato | **✓** | Diff de `workspace.bronze_current.<fuente>` contra `workspace.bronze_lab.<fuente>` en las cuatro fuentes: `solo current`, `solo lab` y `tipo distinto` **vacíos** (mismas columnas y mismos tipos). Claves (`job_key`; `id_oferta`; `job_id`; `site + job_id`) y columna de captura conservadas por el kit. Límite: el diff es sobre el extracto, no sobre las tablas completas de producción. |
| Exactly-once | **✓** | Declarativo: segunda ejecución sin registros. Actual: tercera ejecución con `num_input_rows = 0`, `batch_id = 1` y `batches = 1`; los conteos del destino no se mueven (101/122/47/10). |
| Cuarentena | **✓** | En ambos mecanismos: destino **101/122/47/10** y cuarentena **1 `missing_key` + 1 `missing_scraped_at`** por fuente, igual al manifiesto del kit; ninguna fila inválida llega al destino. |
| Rollback | **`[NO VERIFICADO]`** | No ensayado (no hubo migración). **No aplicable** con la decisión de no migrar: no hay tablas nuevas ni vuelta atrás que probar. La puerta 4 de `plan.md` §3 queda como `[NO VERIFICADO]` en esta evaluación. |

### 9.2 Criterios puntuables

Regla de `plan.md` §3: se adopta si el candidato gana **≥ 5 de 8** y no pierde
ninguna puerta. La carga de la prueba la tiene el candidato (B).

| # | Criterio | Umbral (`plan.md` §3) | Ganador | Evidencia |
|---|---|---|---|---|
| 1 | Tiempo del bronce | ≤ **134 s** de ejecución y run ≤ **1350 s** | **sin ganador** | A: 113–134 s de ejecución en producción (010) + 382–383 s de arranque. B: sin duración total registrada; `_raw` de **4–7 s** por tabla en la UI de Free Edition **`[NO VERIFICADO]`** —proviene de la captura de la UI, que no está en `candidate/evidence/`—. Free Edition (actual **77,7 s** la primera con DDL y **7,2 s** la segunda) no es extrapolable y el tiempo de producción de B queda `[NO VERIFICADO]`. |
| 2 | Coste por ejecución | ≤ **0,24 €** en dev y ≤ **1,00 €/mes** en producción | **A** | A: 0,20–0,24 € por run de producción ⇒ **0,87–1,04 €/mes**; el ≤ **1,00 €/mes** es **referencia y aviso, no regla dura** desde la decisión de la persona del **2026-11-02**: superarlo **abre revisión, no bloquea**, y el techo real es el **presupuesto de producción de 3 €/mes de la 009** (`plan.md` §3, nota al criterio 2). B exigiría **serverless/Pro** en el workspace pagado, sin medir, y añade superficie; su coste de producción queda `[NO VERIFICADO]`. |
| 3 | Código propio | reduce ≥ **50 %** del handler **o** elimina el `MERGE` manual | **B** | Actual: `Upsert_Job_Offers.py` **311** líneas + ~**124** del handler de `bronze_ingest.ipynb` ≈ **435** líneas propias. Declarativo: `pipeline_declarative.py` **240** líneas, que además reimplementa el split y el hash ⇒ reducción ≈ **45 %**, **por debajo del 50 %** (primera rama incumplida). **Pero** elimina el `MERGE` manual: el pipeline usa `dp.create_auto_cdc_flow` y no contiene ningún `MERGE` ⇒ cumple la **segunda rama** del criterio **OR** y gana **B**. El split y el hash siguen siendo código propio. |
| 4 | Observabilidad | event log/métricas por flujo y grafo nativos, o paridad con el resumen de T-03 | **B** | Grafo, expectativas («2 expectations» fallidas por fuente), métricas por flujo y event log **sin instrumentar**; el run mostró tablas `_raw`/`_valid`/`_quarantine` y SCD1 por fuente. Capturas de `candidate/evidence/` pendientes (`[NO VERIFICADO]`), complementarias. |
| 5 | Simplicidad operativa | un artefacto menos que mantener o igual | **A** | Un job y un compute frente a **job + pipeline** (y su configuración, grafo y tablas gestionadas). |
| 6 | Madurez del runtime | sin *preview* obligatorio en la ruta crítica | **A** | **Juicio:** B no acredita *preview* obligatorio en la ruta crítica, pero tampoco aporta ventaja de runtime y añade una dependencia de plataforma (serverless y tablas gestionadas); A corre en el job cluster actual, sin preview. Se mantiene **A** por no mejorar B este criterio. |
| 7 | Riesgo de migración | las 4 fuentes en ≤ **1 ventana semanal**, sin tocar silver | **A** | B exige renombrar tablas, conmutar la ruta canónica y revisar la dependencia de silver; A no migra. |
| 8 | Reversibilidad | vuelta al mecanismo actual sin pérdida de datos ni historial | **A** | Las tablas gestionadas por el pipeline complican la vuelta atrás (`RESTORE`/historial y `bronze_legacy`); A no necesita vuelta. |

**Resultado: B gana 2 de 8** (código propio, criterio 3, y observabilidad, criterio
4). No cumple la regla de adopción (≥ 5 de 8), así que **no se adopta el mecanismo
declarativo**.

### 9.3 Decisión

**Adoptar parcialmente: se mantiene el mecanismo actual (A) y se adopta la
observabilidad** —resumen del bronce, `ops.run_metrics`, tarea final con frescura,
health rules y system tables (T-03–T-06)—. **No se migra** el pipeline declarativo.

Motivo: el declarativo mejora el código propio (elimina el `MERGE` manual, aunque
reduce las líneas solo ≈ 45 %) y la observabilidad, pero no el coste, la simplicidad
operativa, el riesgo ni la reversibilidad. El mecanismo actual
demuestra contrato y exactly-once sobre el extracto real y ya está probado en
producción (008/010). Las piezas que se adoptan se justifican por sí mismas y **no
arrastran la migración de tablas** (`plan.md` §3, «adoptar parcialmente»).

Consecuencias:

- **T-12 no aplica:** se cierra como **no aplicable** con el motivo medido, sin
  gasto y sin tocar producción.
- **T-13 despliega solo observabilidad:** el run programado valida el job actual con
  la tarea final, `ops.run_metrics` y las health rules; no hay pipeline nuevo ni
  cutover.
- **Coste del declarativo no aprobado:** no se crea ningún pipeline `serverless` de
  pago; el coste de producción sigue `[NO VERIFICADO]` y queda fuera de esta spec.

### 9.4 Lo que queda `[NO VERIFICADO]`

- **Tiempo de producción del declarativo:** no se ejecutó en el workspace pagado;
  Free Edition no es extrapolable. `[NO VERIFICADO]`
- **Coste de producción del declarativo:** exigiría serverless/Pro; no medido y no
  aprobado. `[NO VERIFICADO]`
- **Rollback (puerta 4):** no ensayado; no aplicable al no migrar. `[NO VERIFICADO]`
- **Contrato sobre las tablas completas de producción:** el diff se hizo sobre las
  tablas del extracto en Free Edition; no hay migración que exija paridad total.
- **Capturas de `candidate/evidence/` y duración total del pipeline:** evidencia
  complementaria; no cambian la puntuación. `[NO VERIFICADO]`
