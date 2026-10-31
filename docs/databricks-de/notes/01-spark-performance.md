# Rendimiento de Spark: shuffles, planes, layout y compute

Nota de estudio del dominio 1, escrita al cerrar la spec 010 (T-10,
2026-11-01). Es material de repaso, no documentación operativa: cuenta lo que
practiqué midiendo el incremental real de las cuatro silvers y lo que aprendí
por el camino. El detalle de cada medición está en
[design-performance.md](../../../specs/010-Performance-And-Layout/design-performance.md)
(diagnóstico y `MERGE`),
[design-layout-compute.md](../../../specs/010-Performance-And-Layout/design-layout-compute.md)
(layout y compute) y
[evidence.md](../../../specs/010-Performance-And-Layout/evidence.md)
(comprobaciones). Aquí no repito sus tablas.

## Qué he practicado

- **Diagnóstico con la Spark UI, sin lanzar runs.** Comparé A2
  (`207866361330883`, rebuild en 14 m 39 s, 57 consultas de 15 s de media) con
  B2 (`1056455747512203`) y B3 (`1015448868873733`), los dos `TIMEDOUT` a los
  ~30 min con 49–55 consultas de 1,4–1,6 min de media. B3 mostró **10.790
  tareas** y stages de **200 tareas**: el diferencial no era el número de
  consultas (A2 hacía más), sino las tareas que pagaba cada una.
- **Instrumentación por acción.** En `Silver_Incremental.py`, reloj de pared
  (`time.monotonic()`) alrededor de cada acción de Spark y un resumen aditivo
  (`timings_s`, `merge_metrics`, `config`) publicado con
  `dbutils.notebook.exit`; ninguna consulta al SQL warehouse (T-02).
- **Banco local reproducible.** `python
  databricks_notebooks/tests/bench_silver_incremental.py --label before|after`
  con `local[4]` y 8 particiones, escenarios `full`, `wide` y `small`, dos
  pases por escenario. `before`: full 56,9 / 42,5 s, wide 98,2 / 88,3 s, small
  68,9 / 65,6 s; `after` (T-03b): wide 53–56 s, small 30–36 s. El control
  `full` también bajó (56,9 → 31–45 s), así que la ganancia absoluta no es
  atribuible entera a T-03b; la diferencial sí: `merge_source_count` + `merge`
  de **16,2 s a 2,1 s**.
- **P1: `spark.sql.shuffle.partitions` de 200 a 8.** Una línea en el job
  cluster (`resources/elt.job.yml`, commit `61da8c5`) y dos runs: A
  `485578578869927` (808 s) y B `269508392317481` (974 s). El incremental dejó
  de expirar, pero todavía costaba ~2× el rebuild.
- **T-03b: menos pasos, corte de linaje y `target` cacheado.** Siete pasos
  cronometrados en vez de once acciones, `localCheckpoint(eager=True)` en
  `keyed` y `recompute`, `target` cacheado una vez y liberado con
  `unpersist(blocking=True)` antes del `MERGE`, y `merge_metrics` como paso
  propio. Run `879269828668729`: **927 s**, RF-2a y RF-3 cumplidos.
- **T-04: `operationMetrics` del `MERGE` real.** Ocho pasadas de `MERGE`
  (cuatro silvers × dos pases de `repeat_window`) leídas con
  `DESCRIBE HISTORY` en el clúster del job. Decisión: no materializar `_key`.
- **T-05: layout físico con `DESCRIBE DETAIL`.** Las 18 tablas del catálogo
  `job_offers_dev` en un clúster all-purpose de lectura (**céntimos
  (estimación**; tarifa all-purpose no medida), sin SQL
  warehouse): **42 ficheros y 98.369.519 B** en total. Decisión: no cambiar
  nada.
- **T-06: decisión de compute.** Con el camino crítico del run de T-03:
  `D4ds_v4` single node, cuatro silvers en paralelo y `timeout_seconds` sin
  tocar. 0 €.
- **T-07 / T-08: A/B en dev.** Run A `221895134926434` (816,0 s, `force_full`)
  y run B `549237053478569` (985,3 s, incremental con `repeat_window`), 8/8.
- **T-09: A/B en producción.** A `321262594455396` (915 s) y B
  `317888374535056` (747 s), con el mismo bronce: RF-3, `MERGE` e idempotencia
  en verde; RF-4 quedó `[NO VERIFICADO]` por la desviación aprobada.

## Conceptos clave (en mis palabras)

- **Evaluación perezosa y acciones.** Una transformación (`select`, `join`,
  `when`…) solo construye un plan; el trabajo arranca cuando llega una acción
  (`count`, `isEmpty`, `write`, `MERGE`). Por eso cronometro **alrededor de la
  acción** y no de la línea: el tiempo que paga el run es el de la acción, que
  incluye planificación, lanzamiento de tareas y espera. En `_run_once` llegué
  a contar 11 acciones por pase; T-03b las agrupó en 7 pasos cronometrados sin
  cambiar la semántica.
- **Job, stage, tarea y particiones de *shuffle*.** Cada acción lanza uno o
  varios *jobs*; un *stage* termina donde hay un *shuffle*; cada stage reparte
  su trabajo en tareas, una por partición de *shuffle* en las etapas
  intermedias (la etapa final puede tener menos). Con el valor por defecto
  `spark.sql.shuffle.partitions=200`, cada *exchange* creaba 200 tareas aunque
  moviera 0,13–28,4 KiB por tarea: B3 acumuló **10.790 tareas** con **4
  núcleos**. Bajarlo a 8 bajó las particiones por *exchange* de 200 a 8 (~25×)
  y las tareas por silver aproximadamente un orden de magnitud (de ~2.700 a
  ~150–250, estimado en T-01), y sacó al incremental del `TIMEDOUT`.
- **AQE.** *Adaptive Query Execution* reoptimiza el plan con estadísticas de
  ejecución (por ejemplo, coalesce particiones de *shuffle*). En B3 el
  problema apareció igualmente: AQE no estaba desactivado (lo confirmó el
  `config` del run: `adaptive_enabled=true`, sin override), pero las 200
  particiones seguían ahí. La lección: AQE ayuda, no sustituye a mirar cuántas
  tareas paga una acción.
- **Coste por tarea y 4 vCPU.** Las cinco stages de ≥66 s costaron **0,70–0,87
  s por tarea**, con 0,13–28,4 KiB por tarea: el tiempo por tarea no dependía
  de los bytes. 10.790 × 0,7 s ≈ **7.553 s** frente a una capacidad de
  4 × 1.830 s = **7.320 s**: el run estaba saturado lanzando tareas, no
  moviendo datos (731,6 MiB leídos y 79,7 MiB de *shuffle* en total). Más
  núcleos no arregla eso; menos tareas, sí.
- **Codegen y el límite de 64 KB.** `explain("codegen")` de la cadena de
  `build_offers` generó **2.640.741 caracteres en 8 subtrees** y disparó el
  aviso «Code grows beyond 64 KB». Cuando el método generado pasa del límite
  de la JVM, Spark lo parte y parte del trabajo se vuelve interpretado (o se
  encarece por métodos enormes). Era una **condición presente**, pero no el
  diferencial: A2 ejecutaba la misma cadena y era 5,4× más rápido; después de
  T-03b, `recompute_check` seguía costando **26,1–50,4 s para 55 filas**, que
  es compilación, no datos.
- **Caché frente a `localCheckpoint`.** `cache()` guarda el resultado pero
  conserva el linaje: si hay presión de memoria, Spark puede recalcularlo.
  `localCheckpoint(eager=True)` materializa el frame **ya** en el ejecutor y
  **corta el linaje**, así que el plan de lo que viene después (el `MERGE`) es
  más pequeño. En T-03b el paso de referencia `merge` bajó de **12,4 s a
  ~1,5 s** y `merge_source_count` + `merge` de **16,2 s a 2,1 s**. No reduce
  consultas: Spark cobra la materialización con su propia ejecución; el pase
  quedó en **9 ejecuciones de datos + 1 de metadatos**. Cachear `target` una
  vez y soltarlo con `unpersist(blocking=True)` **antes** del `MERGE` evita
  que `target_count` lea el snapshot anterior.
- **`MERGE` de Delta y `operationMetrics`.** `DESCRIBE HISTORY <tabla> LIMIT
  2` devuelve, en el clúster del job, las métricas reales de la operación. En
  las ocho pasadas del run `879269828668729`: `numTargetRowsCopied=0` (no copia
  filas no afectadas), `numTargetFilesAdded=1` (41,1–656,1 KB),
  `numTargetRowsUpdated = affected` (55–458) e `inserted = deleted = 0`. El
  pase 2 retiró un fichero y `numTargetBytesRemoved` fue **exactamente**
  `numTargetBytesAdded` del pase 1 (453.383 / 66.087 / 656.076 / 41.133 B):
  reescribe el fichero de las filas cambiadas, no la tabla entera. El paso
  `merge` costó **10,5–28,9 s** por pase, con `executionTimeMs` 5,9–23,3 s,
  `scanTimeMs` 2,1–6,8 s y `materializeSourceTimeMs` 0,7–3,4 s.
- **Deletion vectors (DVs).** Están activos en las 18 tablas. El pase 1 del
  `MERGE` registra **`numDeletionVectorsAdded=1`** por tabla
  (`filesRemoved=0`): las filas actualizadas se marcan como borradas en el
  fichero base y se escriben en uno nuevo. El pase 2 registra
  **`numDeletionVectorsAdded=0`** y retira el fichero del pase 1
  (`filesRemoved=1`). El pase 2 es **consistente con** reescribir el fichero
  del pase 1 (la ruta no se mide); el número de DVs **vigentes** no es
  accesible y queda `[NO VERIFICADO]`.
- **Layout: por qué no toqué nada.** `DESCRIBE DETAIL` de las 18 tablas dio
  **42 ficheros / 98.369.519 B**, con `fact_offers` en **1 fichero de 19,15
  MB** (26.302 filas), las cuatro `offers_*` en 2 ficheros, `companies_*` en 1
  y las dos dimensiones de oro en 4 ficheros de 6,2 / 9,3 kB; en las 18,
  `partitionColumns` y `clusteringColumns` son `[]`. **`OPTIMIZE`** solo actúa
  donde hay ≥2 ficheros y reescribiría 1,5–15,9 MB para pasar de 2 a 1 (o
  6,2 / 9,3 kB donde la ganancia no se puede medir). **`liquid clustering`**
  exige consultas que filtren por la columna: oro y exports leen la tabla
  completa y el paso `window` cuesta 5,7–6,3 s. **Particionar** crearía una
  partición por lote de `_ingest_date` y ninguna tabla pasa de 44.928 filas ni
  de 33,09 MB. Decisión: no cambiar; se revisa si una tabla pasa de 50
  ficheros, si la media baja de 1 MB en una tabla >10 MB, si aparece un filtro
  selectivo dominante, si el volumen crece ≥10× o si hay decenas de DVs.
- **Camino crítico y arranque del clúster.** El run de 927,1 s se descompone
  en bronce **517,5 s** (**383 s de arranque + 134 s de ejecución**), silvers
  en paralelo **298,5 s** de ventana, oro **58,7 s** y exports **49,8 s**;
  517,5 + 298,5 + 58,7 + 49,8 = 924,5 s, más 1,3 s de huecos y 1,3 s de
  arranque/cierre = 927,1 s. El arranque se midió después en **382 s** (T-07)
  y **411 s** (T-08), todos con `D4ds_v4`: es el coste dominante del run
  (41–47 %), no el incremental. Optimizar código tiene un techo si el reloj se
  va en arrancar.
- **Coste por duración.** Con la tarifa de jobs clásicos de `docs/costs.md`,
  la banda **estimada** es **0,17–0,23 €/run**. Los importes anotados para los runs
  de 808, 974 y 927 s (**0,17, 0,20 y 0,23 €**) son **estimaciones por duración
  según `docs/costs.md`**, no una función exacta de los segundos ni una factura
  leída. La referencia de **~0,24 €/run** de `plan.md` §7.2 corresponde a
  **15–19 min** y es solo orden de magnitud. El A/B de producción de T-09 sumó
  **~0,44 €** (A 915 s ~0,24 €, B 747 s ~0,20 €), fuera del techo de dev.

## Comandos y trucos

```powershell
# Runs: duración, tiempos por tarea y salida del notebook
databricks jobs get-run <run-id> --profile vs_code --output json
databricks jobs get-run-output <run-id> --profile vs_code --output json

# Bundle: validate no ejecuta; deploy sube y actualiza; run ejecuta
databricks bundle validate -t dev --profile vs_code
databricks bundle deploy   -t dev --profile vs_code
databricks bundle run elt  -t dev --profile vs_code --params force_full=true
databricks bundle run elt  -t dev --profile vs_code `
  --params backfill_from=2026-10-24,backfill_to=2026-10-28,repeat_window=true

# Suites (desde la raíz del repositorio)
python -m pytest databricks_notebooks/tests -q
python -m pytest scrapers-pipeline/tests -q
```

```python
# Plan: exchanges y tamaños; código generado y aviso de 64 KB
df.explain("formatted")
df.explain("codegen")

# Configuración efectiva dentro del run
spark.conf.get("spark.sql.shuffle.partitions")

# Metadatos de Delta sin SQL warehouse (en el clúster del job)
spark.sql("DESCRIBE DETAIL silver.offers_indeed").show(truncate=False)
spark.sql("DESCRIBE HISTORY silver.offers_indeed LIMIT 2").show(truncate=False)

# Corte de linaje y liberación de caché
df.localCheckpoint(eager=True)
df.unpersist(blocking=True)
```

- `get-run` da `run_duration` y, por tarea, `setup_duration` y
  `execution_duration`: separa arranque de código. `get-run-output` trae el
  JSON de `dbutils.notebook.exit` (`timings_s`, `merge_metrics`, `config`,
  `files` / `previous_files`).
- `explain("formatted")` para ver los `Exchange` y el reparto;
  `explain("codegen")` para medir el tamaño del código y confirmar el aviso de
  64 KB.
- `DESCRIBE DETAIL` y `DESCRIBE HISTORY` se ejecutan en el clúster del job:
  son metadatos de Spark/Delta, no despiertan el SQL warehouse.
- `localCheckpoint(eager=True)` antes de un `MERGE` que consume un frame
  calculado; `unpersist(blocking=True)` cuando ya no se necesita.
- Para comparar exports, `sha256` del contenido con orden total
  (`orderBy(*df.columns)`) y no bytes de un `coalesce(1)` sin orden.

## Errores que he cometido y qué aprendí

- **Culpar al `keyed` compartido sin medir.** B3 probó a compartir `keyed`
  entre los dos pases de `repeat_window`: el run volvió a expirar con **22 m 04
  s** de silver y el parche ni siquiera llegó a completar el primer pase.
  Aprendí que una hipótesis sin Spark UI se paga a 0,24 € el intento; el
  cambio se revirtió y se re-evaluó en T-03 con medición.
- **Culpar al volumen.** El 27-oct el bronce de indeed solo ingirió **190
  filas** y el incremental seguía tardando ~22 min. `_ingest_date` es la
  **fecha del lote**, no la de captura: una ventana de 7 días recalcula casi
  todo el bronce y B2/B3 eran el peor caso, no un cambio pequeño.
- **Creer que subir `timeout_seconds` era una salida.** B, B2 y B3 murieron a
  los ~30 min sin escribir nada y el gate real es **≤1350 s** (RF-3), no
  «termina algún día». La spec lo prohíbe como solución y hace bien: el tope
  es la señal, no el problema.
- **No capturar los SHA-256 de los exports antes del run.** Ese run
  (`879269828668729`) sobrescribió `gold/pbi_export_dev` y
  `gold/web_export_dev` y la equivalencia quedó solo por conteos, `meta.json`
  e idempotencia. Lo arreglé en T-06b: cada `meta.json` guarda `files`
  (`name`, `bytes`, `sha256`) y, desde la segunda ejecución, `previous_files`
  con la huella del run anterior.
- **Intentar el `explain` local del `MERGE` en Windows.** Spark 4.2.0 con
  Delta desde JARs locales falla en `listStatus` →
  `NativeIO$Windows.access0` (`UnsatisfiedLinkError`) porque este host no
  tiene `winutils.exe` / `hadoop.dll`; no instalé nada. El plan del `MERGE`
  sigue `[NO VERIFICADO]` y la decisión se sostiene en las `operationMetrics`
  del run real.
- **Pretender contar deletion vectors.** `dbutils.fs.ls` rechaza la ruta que
  gestiona Unity Catalog (`INVALID_PARAMETER_VALUE`) y el proxy del plan
  degeneró en `HashAggregate + Exchange SinglePartition` sobre un
  `LocalTableScan <empty>`. Retiré las celdas el 2026-10-31: no era medible y
  solo añadía trabajo al run. Queda `[NO VERIFICADO]`.
- **Comparar exports byte a byte.** Las dimensiones tenían **idéntico tamaño y
  hash distinto**; el orden de filas de `coalesce(1)` no es determinista, así
  que el byte a byte medía el **escritor**, no la lógica incremental. Pasé a
  comparar **contenido** con escritura ordenada de forma total
  (`orderBy(*df.columns)` antes de `coalesce(1)`).
- **Un empate no determinista en `_keep_best` de oro.** En el A/B de
  producción, A y B eligieron fuentes distintas en un grupo con el mismo
  título, empresa, salario y fecha: `fact_offer_skills` 46.375 → 46.374
  (Indeed 2.768 → 2.767, glassdoor 6.368 → 6.369). No fue un lote nuevo: fue
  el desempate. Lo corregí en `Prepare_Gold.keep_best` con
  `source_scraper` / `job_id` asc, con test local (**22 passed, 1 skipped**).
  Lección: un dedup sin desempate total no es determinista aunque los datos
  sean los mismos.

## Dudas pendientes

- **DBU y coste de serverless.** No hay *spike* ni run medido en este repo; el
  «DBU superior» de `plan.md` no es una medición. `[NO VERIFICADO]`
- **Tiempos de `D8ds_v4` y multi-nodo.** No se han corrido. El ahorro ≤200 s
  (~20 %) de T-06 es aritmética sobre 398 s paralelizables, no una medida.
  `[NO VERIFICADO]`
- **Independencia del arranque respecto al tamaño del nodo.** Los 382–411 s se
  midieron con `D4ds_v4`; suponer que un nodo mayor no los mejora es un
  supuesto, no una comparación. `[NO VERIFICADO]`
- **Número de deletion vectors vigentes.** `DESCRIBE DETAIL` no lo informa y
  el recuento por `dbutils.fs.ls` no es accesible; no sé si se acumulan entre
  runs. `[NO VERIFICADO]`
- **Plan interno del `MERGE`.** El `explain` local falla en Windows y `EXPLAIN
  MERGE` solo devuelve el comando: no sé si el `ON` impide poda por fichero ni
  cuánto del paso `merge` es escaneo frente a materialización.
- **Estabilidad del arranque.** 383 s, 382 s y 411 s en tres runs con
  `D4ds_v4`; no sé si es una constante, si varía con el lote o con el nodo.
- **RF-4 en producción.** Queda `[NO VERIFICADO]` con desviación aprobada: el
  A/B no se repite y el run programado del 8-nov usará el fix del empate, pero
  no hay una comparación A/B posterior que lo demuestre.
- **Notas 02 y 04 de la 008.** `02-delta.md` y
  `04-incremental-architecture.md` siguen sin escribir; la 008 no cierra hasta
  T-12.
