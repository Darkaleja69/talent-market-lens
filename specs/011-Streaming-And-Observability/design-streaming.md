# Diseño — mecanismo de ingesta en streaming

> **Estado (2026-11-02, T-01):** reconocimiento gratuito cerrado. Este documento
> fija el **estado de partida**, los **umbrales** y el **marco de evaluación**; la
> **decisión** (adoptar / adoptar parcialmente / no cambiar) se escribe en T-10,
> después de la demo en Free Edition. Las secciones 4–7 (banco local,
> instrumentación, comparación y decisión) se completan en T-02, T-03 y T-10.
>
> Rama `spec/011-streaming-observability`, worktree `talent-market-lens-011`.
> Cada afirmación lleva su fuente (fichero y línea, o comando) o va marcada
> `[NO VERIFICADO]`. **Coste del reconocimiento: 0 €** —0 runs, 0 clústeres,
> 0 consultas SQL, 0 pipelines—; solo API de Unity Catalog, API de Jobs y listado
> de AzCopy.

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
