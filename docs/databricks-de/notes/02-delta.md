# Delta Lake: transacciones, MERGE y evolución de esquema

Nota de estudio de la **spec 008** (bronce deduplicado), cerrada el 2026-11-01.
El bloque B (silvers incrementales) terminó en la 010. Material de repaso, no
documentación operativa: lo operativo está en `docs/RUNBOOK.md` y en
`specs/008-Delta-Incremental/{plan,cutover}.md`.

## Qué he practicado

- **Upsert del bronce con `MERGE`** dentro del `foreachBatch` de Auto Loader, por
  clave de cada fuente (`job_id`; `site + job_id` en multi_site) y ganando la
  captura más reciente por su columna temporal (T-02/T-03; run dev
  `339441526455403`, 13 m 39 s, bronze 5 m 11 s).
- **Cuarentena en Delta**: las filas con clave o temporal nula, en blanco o
  imparseable van a `bronze.<fuente>_quarantine` con motivo, fecha y fichero, sin
  abortar el lote (`missing_key` / `missing_scraped_at`; T-02, 24 tests).
- **Migración con sobrescritura y `RESTORE`**: one-off `dedupe_oneoff` que
  reescribe cada tabla con una fila por clave y deja la versión anterior a un
  `RESTORE` (dev `328818148400464`, versiones 7/7/14/8; prod `164560238285705`,
  **53.353 filas duplicadas eliminadas**, versiones 19/20/60/50).
- **Idempotencia comprobada con dos runs seguidos**: A `274846748795055` y B
  `683591523413230` producen exports idénticos (T-05).
- **`MERGE` real medido en producción** (010 T-09): `updated` 316/850/1.788/728,
  `inserted = deleted = 0` y `target_rows` idénticos entre el run completo y el
  incremental.
- **Evolución de esquema** con `MERGE ... WITH SCHEMA EVOLUTION` y con
  `mergeSchema`/`_rescued_data` en la ingesta: cubierta por tests, **no**
  ejercitada de punta a punta porque el landing es compartido con producción
  (T-03, limitación aceptada).

## Conceptos clave (en mis palabras)

- **El transaction log (`_delta_log`) es la tabla.** Cada operación deja un
  commit con ficheros añadidos y retirados; la tabla es «el log aplicado», y por
  eso `RESTORE` no deshace historia: crea una versión nueva con los datos de una
  anterior.
- **`MERGE` no deduplica lo que ya estaba.** Casa cada fila de origen con **todas**
  las del destino que cumplen la condición, así que actualiza las copias
  históricas. Por eso hizo falta el one-off: el `MERGE` arreglaba el futuro, no
  el pasado (evidencia del 22-oct: indeed 9.430 filas / 2.959 claves).
- **`MERGE` no reescribe la tabla entera.** En las 8 pasadas medidas de la 010,
  `numTargetRowsCopied = 0` y `numTargetFilesAdded = 1`: escribe un fichero con
  las filas afectadas. El coste está en el `ON` (la expresión de la clave) y en
  el escaneo del origen, no en recopiar el destino.
- **Deletion vectors (DV).** Con `delta.enableDeletionVectors` activo (lo está en
  las 18 tablas del catálogo dev, 010 T-05), un `MERGE` que actualiza marca filas
  borradas en un DV en vez de reescribir el fichero. El primer pase del run de
  dos pases deja **1 DV por tabla** y el segundo no añade ninguno; el **número de
  DVs vigentes no se puede leer** con `DESCRIBE DETAIL` (`[NO VERIFICADO]`).
- **`OPTIMIZE` y liquid clustering:** no se han usado. La 010 midió el layout y
  decidió no tocarlo (`fact_offers` ya está en **1 fichero de 19,15 MB**).
- **Idempotencia = clave + condición temporal, no «no escribir».** El `MERGE`
  vuelve a escribir aunque el valor sea el mismo; lo que se demuestra es que el
  **contenido** no cambia (conteos y `target_rows` idénticos al repetir).
- **El escritor no es determinista.** `coalesce(1)` no garantiza el orden de
  filas entre ejecuciones: dos exports con el mismo contenido pueden no coincidir
  byte a byte. Conclusión de la 010: comparar **contenido**, y ordenar de forma
  total (`orderBy(*df.columns)`) si se quiere estabilidad.
- **ANSI cambia el fallo.** Un `to_timestamp` sobre un valor imparseable no
  devuelve nulo en silencio: con ANSI activo lanza. Por eso la temporal se valida
  antes de fusionar y las inválidas van a cuarentena.

## Comandos y trucos

```powershell
# Lectura de un run sin abrir la UI ni gastar SQL
databricks jobs get-run 636564838129029 --profile vs_code --output json
databricks jobs get-run-output <task-run-id> --profile vs_code --output json
databricks api get "/api/2.0/jobs/runs/export?run_id=<task-run-id>" --profile vs_code
databricks warehouses list --profile vs_code --output json   # state: STOPPED

# Bundle (sube definición, no ejecuta)
databricks bundle validate -t prod --profile vs_code
databricks bundle deploy   -t prod --profile vs_code
```

- En SQL: `DESCRIBE HISTORY <tabla>`, `RESTORE TABLE <tabla> TO VERSION AS OF
  <n>`, `DESCRIBE DETAIL <tabla>` (filas, ficheros, bytes y propiedades; **no**
  DVs vigentes).
- Los epoch ms de la API se pasan a hora local con
  `[DateTimeOffset]::FromUnixTimeMilliseconds(...).ToOffset(...)`; el 25-oct (fin
  del horario de verano) Madrid es **+01:00**.
- Leer un `meta.json` de ADLS con la SAS (**solo lectura**) sirve para comprobar
  el export sin lanzar nada: `Invoke-WebRequest` o `azcopy` con el token
  normalizado (`TrimStart('?')`).
- **Truco de coste:** el resumen por `dbutils.notebook.exit` se lee con
  `get-run-output`, así que la evidencia de un run **no necesita** despertar el
  SQL warehouse.

## Errores que he cometido y qué aprendí

- **`TABLE_OR_VIEW_NOT_FOUND __batch_indeed`.** El `foreachBatch` entrega el lote
  con la sesión de la query; hay que capturar
  `session = batch_df.sparkSession` y usar esa sesión para la vista temporal y el
  `MERGE`. Aprendido a golpe de run fallido (`339441526455403` fue el verde).
- **Creer que el `MERGE` limpiaba el histórico.** No lo hace: actualiza todas las
  filas que casan. La deduplicación del pasado es una migración explícita, no un
  efecto del upsert.
- **Un `count()` de más cuesta 3×.** El modo `full` con un `count()` sobre el
  frame cacheado y otro sobre la tabla llevó las silvers de 2,8–3,6 min a
  9,8–11,8 min (run A `263658658061220`, 22 m 18 s). En una pasada volvió a
  14 m 39 s (A2 `207866361330883`): menos acciones, no más núcleos.
- **Comparar exports byte a byte medía el escritor.** El A/B de la 010 falló por
  hashes distintos con el mismo tamaño en las dimensiones; la causa era
  `coalesce(1)` sin orden total. Lección: definir qué se compara antes de medir.
- **Un empate sin desempate es un bug latente.** El mismo bronce dio una fila
  distinta en dos runs por un empate en `_keep_best` (descripción, salario,
  fecha). Arreglado con desempate `source_scraper`/`job_id` asc, **probado en
  local**: el desempate hace el resultado determinista por construcción, pero
  está pendiente de validarse en el run del 8-nov.
- **Suponer que `_ingest_date` era la fecha de captura.** Es la fecha del **lote**
  (ver `04-incremental-architecture.md`): cambia por completo cómo se lee una
  ventana de reproceso.

## Dudas pendientes

- Cuántos **DVs vigentes** deja cada `MERGE` y qué hace un `OPTIMIZE` con ellos
  (no medible con las APIs usadas: `DESCRIBE DETAIL` no los informa).
- **CDF** (change data capture): no se usa; queda abierto si compensa para
  historial de cambios, que la spec dejó fuera de alcance.
- **`MERGE ... USING` una vista** y `WITH SCHEMA EVOLUTION` en producción: van
  cubiertos por tests, pero no se han ejercitado de punta a punta con un fichero
  real que añada una columna (el landing es compartido con producción).
- Por qué las **dimensiones de oro** cambian de hash con idéntico tamaño en cada
  escritura (medido en la 010; si el contenido coincide está **`[NO VERIFICADO]`**,
  solo se compararon tamaño y hash).
- `VACUUM`: no se ha tocado; sin política decidida, el histórico de ficheros
  retirados se conserva (lo que mantiene vivo el `RESTORE`).
