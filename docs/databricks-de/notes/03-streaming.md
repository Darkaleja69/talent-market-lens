# Streaming: checkpoint, microbatch, watermark y exactly-once

Nota de estudio del dominio 3, escrita al cierre documental de la spec 011
(2026-11-05). La práctica fue doble: un banco local que demuestra la semántica
(`databricks_notebooks/streaming_lab.py`, **6 tests**) y el resumen del bronce
real del job. La comparación con el mecanismo declarativo está en
[design-streaming.md](../../../specs/011-Streaming-And-Observability/design-streaming.md)
§9; aquí cuento lo que entendí, no repito sus tablas.

## Qué he practicado

- **Semántica de streaming en local, sin gastar un céntimo** (T-02): cinco
  escenarios en `local[2]` con sumideros en `tmp`, sin Delta y sin enganchar el
  módulo al job. Los **6 tests** tardan **98,1 s** (96,9–169,2 s según la carga
  del host), con los escenarios ejecutados una sola vez por módulo.
- **Checkpoint y reinicio** (`checkpoint_restart`): la segunda ejecución leyó
  solo el fichero nuevo —2 filas en el `batchId` 0, 1 fila en el `batchId` 1 y 3
  en el sumidero— en vez de releer la fuente.
- **Triggers** (`microbatch_triggers`): `availableNow` hizo 3 microbatches con 3
  ficheros, `once` 1 batch con 1 fila y 2 pendientes, y `processingTime` siguió
  vivo acumulando batches hasta pararlo.
- **Watermark y evento tardío** (`watermark_late_event`): la ventana de las
  11:50 desapareció tras el segundo run; el descarte se ve por la ausencia de la
  ventana, no por `numRowsDroppedByWatermark`, que valió 0.
- **Exactly-once y sumidero idempotente** (`exactly_once` y
  `foreach_batch_upsert`): con checkpoint, 3 filas y 0 en la repetición; sin
  checkpoint, 6; con upsert por clave, 2 filas para 2 claves.
- **Resumen del bronce real** (T-03): `Bronze_Summary.py`, módulo puro con 19
  tests, publica por fuente `num_input_rows`, `batch_id`, duraciones,
  cuarentena y estado del checkpoint, sin lanzar lecturas nuevas sobre la
  landing.
- **Comparación con Lakeflow** (T-10): los dos mecanismos sobre el mismo extracto
  CSV en Free Edition; contrato, exactly-once y cuarentena en verde, y decisión
  de **no migrar** porque el declarativo ganó 2 de 8 criterios.

## Conceptos clave (en mis palabras)

- **El checkpoint es el progreso por fuente.** Cada consulta tiene el suyo
  (`_checkpoints{_dev}/<fuente>/`) y guarda offsets, commits y estado. En el
  banco, borrarlo duplicó el sumidero append (3 → 6 filas): sin él, el motor no
  sabe qué ya consumió.
- **`availableNow` no es «una sola pasada».** Drena todo lo pendiente en
  **varios** microbatches y termina. En el banco, 3 ficheros dieron 3 batches;
  en el bronce real cada fuente trae su `batch_id` (12/12/18/15 en el run A y
  12/13/19/15 en el B). Por eso el resumen publica el último microbatch y el
  total del run (`num_input_rows_total`): mirar solo el último esconde los
  anteriores.
- **El microbatch es la unidad de commit.** Cada uno confirma offsets, escribe
  el sumidero y avanza estado y watermark. Un run sin datos puede ejecutar un
  microbatch vacío: en Free Edition salió `num_input_rows = 0` con
  `batch_id = 1`.
- **El watermark no se configura por lote: es estado que persiste.** Queda
  guardado en el checkpoint y vuelve al relanzar, de modo que un evento que
  llega en una ejecución posterior a su ventana también se descarta.
  `numRowsDroppedByWatermark` marcó 0 aquí porque el descarte ocurre antes de
  que el evento entre al estado; lo que lo demuestra es la ventana ausente, no
  el contador. Con `append` y ventanas de 10 min la primera ejecución no emite
  nada: hubo que pasar a `update` + `foreachBatch` para verlas.
- **El estado vive en memoria y se materializa en disco.** Mientras la consulta
  corre, las agregaciones con estado están en memoria; el checkpoint las guarda
  en el almacén (disco o DFS) y las restaura al relanzar. `stateOperators` de
  `lastProgress` enseña `operatorName`, `numRowsTotal`, `numRowsUpdated`,
  `numRowsRemoved` y `numRowsDroppedByWatermark`; `stateStoreSave` no está ahí,
  está en `durationMs`. En el resumen del bronce, `state_operators` copia
  `lastProgress` y, sin agregación con estado, publica `[]`; es una
  **inferencia** de cómo está montada la consulta (el dedup se hace en
  `foreachBatch`, no en el motor), no una medición: `[NO VERIFICADO]` en un run
  real.
- **El exactly-once de negocio lo pone la escritura idempotente, no el motor.**
  El checkpoint recuerda qué ficheros ya se consumieron, pero no forma una
  transacción con el sumidero: un proceso que muere entre confirmar el offset y
  escribir deja un lote que puede repetirse al relanzar (at-least-once). Quien
  garantiza una sola fila por clave es el `MERGE`
  (`Upsert_Job_Offers.merge_sql`). En el banco, el append pasó de 3 a 6 filas
  al reprocesar, mientras que el upsert por clave se quedó en 2 (2 claves) con
  la captura más reciente.
- **Structured Streaming frente a Lakeflow se decide con criterios, no con
  gusto.** El declarativo ganó **2 de 8**: código propio (elimina el `MERGE`
  manual, aunque reduce las líneas solo ≈45 %) y observabilidad nativa (grafo,
  expectativas y métricas por flujo). Perdió coste, simplicidad operativa,
  madurez, riesgo de migración y reversibilidad; las puertas duras —contrato,
  exactly-once y cuarentena— pasaron en los dos, y la de rollback no aplica al
  no haber migración (no se ensayó, `[NO VERIFICADO]`). Con la regla de ≥5 de
  8, se mantiene el mecanismo actual y se adopta la observabilidad.
- **El trigger encaja con la cadencia.** `once` procesa un batch y deja
  pendiente lo que llegue después; `availableNow` drena y termina;
  `processingTime` exige un clúster vivo. El job es semanal y el coste es por
  run, así que `availableNow` es el que corresponde.

## Comandos y trucos

```powershell
# Banco local: 5 escenarios, 6 tests, sin tocar el job ni Delta
python -m pytest databricks_notebooks/tests/test_streaming_lab.py -q -s

# Resumen real de una tarea de bronce, sin despertar el SQL warehouse
databricks jobs get-run-output <task-run-id> --profile vs_code --output json
```

- `query.lastProgress` es el último microbatch; `query.recentProgress` guarda
  hasta 100 entradas y de ahí sale `num_input_rows_total`.
- `query.status["message"]` da el estado (`Stopped`); `query.id` se mantiene
  entre ejecuciones porque el checkpoint lo persiste.
- Para contar la cuarentena sin una pasada nueva: `persist()` + `count()` sobre
  el frame pequeño ya calculado (una acción por una), no `isEmpty()`.
- `maxFilesPerTrigger=1` convierte cada fichero en un microbatch en el banco;
  `once` lo ignora en Spark 4.2 (el motor avisa: *The read limit MaxFiles: 1 …
  is ignored when Trigger.Once is used*).
- En Windows, `winutils.exe`/`hadoop.dll` en `%LOCALAPPDATA%\hadoop\bin`; sin
  eso Spark no escribe Parquet ni checkpoints. En Linux/CI no hace falta.
- `stateOperators` se lee de `lastProgress`; no confundir el contador
  `numRowsDroppedByWatermark` con la prueba del descarte.

## Errores que he cometido y qué aprendí

- **Creer que `availableNow` era una sola pasada.** El banco demostró 3
  microbatches para 3 ficheros y el bronce real tiene un `batch_id` distinto por
  fuente. Un resumen que solo publicara `lastProgress` habría escondido los
  batches anteriores; por eso existe `num_input_rows_total`.
- **Confundir el checkpoint con una transacción.** Sin sumidero idempotente, el
  mismo lote reprocesado duplica (6 filas). El motor da at-least-once; el
  exactly-once de negocio lo pone el `MERGE`.
- **Esperar que el contador del watermark probara el descarte.** Vale 0 porque
  el evento tardío se descarta antes de entrar al estado. La evidencia es la
  ausencia de la ventana 11:50. Y con `append` la primera ejecución no emite
  ninguna ventana: hay que cambiar a `update` para observarlas.
- **Buscar `stateStoreSave` dentro de `stateOperators`.** No está ahí: vive en
  `lastProgress.durationMs`. `stateOperators` solo describe el estado.
- **Suponer que el resumen del bronce tendría estado.** Es una inferencia, no
  una medición: estas consultas no agregan con estado y el dedup vive en
  `foreachBatch`, así que `state_operators` debería publicarse como `[]` en las
  cuatro fuentes. `[NO VERIFICADO]` en un run real.
- **Pensar que `once` respetaría `maxFilesPerTrigger`.** En Spark 4.2 lo ignora
  y lee en un batch todos los ficheros presentes al arrancar; lo que llega
  después queda pendiente.

## Dudas pendientes

- **La muerte del proceso entre el commit del offset y la escritura** está
  explicada en el docstring del banco, pero no simulada: no he visto el
  duplicado real de ese intervalo.
- **DBR 17.3 frente a PySpark 4.2 local.** El banco corre en local y el resumen
  real se validó en la nube, pero el número exacto de microbatches por fuente y
  el detalle de `lastProgress` en DBR no se repitieron fuera del run.
- **Estado con agregación en el bronce.** Hoy no hace falta; si algún día se
  añadiera, `state_operators` dejaría de publicar `[]` y habría que vigilar el
  tamaño del estado y el coste del checkpoint.
- **Backlog continuo.** `STREAMING_BACKLOG_*` está en Public Preview y los
  streams son `availableNow`; no se usa. Si se pasara a `processingTime`, habría
  que medirlo antes de confiar en él.
- **Tiempo y coste de producción del declarativo.** No se midieron: Free
  Edition no es extrapolable y el coste de serverless en el workspace pagado no
  estaba aprobado.
