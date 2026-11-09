# Observabilidad del run: métricas en Delta, checks y alertas sin warehouse

Nota de estudio del dominio 6, escrita al cierre documental de la spec 011
(2026-11-05). La práctica fue sobre el run real del job, no con juguetes: una
tabla de métricas en Delta, una novena tarea de cierre, cuatro comprobaciones,
health rules y consultas a `system.*` en dos ventanas de SQL (una por sesión,
~0,43 € cada una). El detalle de cada medición está en
[design-observability.md](../../../specs/011-Streaming-And-Observability/design-observability.md)
y en
[evidence.md](../../../specs/011-Streaming-And-Observability/evidence.md);
aquí resumo lo que aprendí.

## Qué he practicado

- **Una fila por run en Delta** (T-04): `Run_Metrics.py` con **30 tests** en
  T-04 (26 puros + 4 de integración con Delta local). La tabla
  `ops.run_metrics` tiene **13 columnas**: `run_id`, `job_id`, `started_at`,
  `finished_at`, `duration_s`, `bronze_rows`, `quarantine_rows`, `data_date`,
  `data_age_days`, `status`, `checks`, `metrics` y `recorded_at`. Una sola
  definición genera el DDL, el esquema de Spark y la lista de columnas.
- **Idempotencia por `run_id`** (T-04): dos escrituras del mismo `run_id` dejan
  **una** fila y gana la segunda; `record` nunca propaga errores y `ensure_table`
  crea solo la tabla. El esquema `ops` se creó por CLI en dev y prod (0 €).
- **La novena tarea `Publish_Run_Summary`** (T-05): `run_if: ALL_DONE`,
  `depends_on` de las ocho tareas de negocio y 7 parámetros, incluidos
  `{{job.run_id}}`, `{{job.id}}` y `{{job.start_time.iso_datetime}}`. La lógica
  vive en `Run_Metrics.py` (`pending_files`, `build_checks`, `failed_checks`,
  `parse_data_date`); el notebook solo coordina. **65 tests puros** del cierre
  —**65 actuales** (58 al cerrar T-05, 2026-11-03)—.
- **Cuatro `checks` con nombre** (T-05): `bronze_summary` (hay resumen y qué
  fuentes trae), `bronze_sources` (4 esperadas / 4 presentes), `freshness`
  (`max_age_days 8`) y `pending_files` (falla si hay pendientes con
  `bronze_rows == 0` o si `listing_ok` no es `True`; el texto del error viaja en
  `listing_error`).
- **La fila se escribe antes del `raise`** (T-08): en el run
  `1048410431217707` las 8 tareas de negocio terminaron en verde, pero el cierre
  falló por su propio `listing_ok: false`; aun así quedó una fila con
  `status failed`, `duration_s 907.36`, `bronze_rows 2283`,
  `data_date 2026-11-02`, `data_age_days 1` y `recorded_at 2026-11-03T08:36:25Z`.
- **Cierre en verde con y sin ingesta** (T-08/T-11): el run
  `1015457552352783` cerró 9/9 en 862 s con **0 filas**
  (`duration_s 849.21`); el run A `826958114241409` cerró en 884 s con **4442
  filas**; el run B `410084266136659`, inmediato y sin ficheros nuevos, cerró en
  576 s con **0 filas**; el run manual de producción `1050766241907151` cerró
  9/9 en 643 s.
- **System tables en dos ventanas** (T-07/T-08): la primera, de **6 min 44 s**
  y 8 ejecuciones (1 fallida + 7 correctas); la segunda, de ~10 min, para leer
  las filas de `ops` y la latencia. `job_run_timeline` dio 29 filas en la
  ventana de 30 días (una por run al agrupar) y un alcance de **106 filas / 84
  runs / 6 jobs** (≈67 días); `job_task_run_timeline` dio duraciones por tarea
  distintas de 0. El cruce `billing.usage` × `list_prices` dio **0,1531877 DBU ×
  0,30 USD = 0,045956 USD** para un run de 744 s. Query Profile de la consulta
  1: **2,603 s**, 29 filas devueltas de 43 leídas, 15,06 KB, 1 fichero, 0 % de
  caché, Photon 58 % y un scan de 17 ms.
- **Latencia y retención de `system.lakeflow`** (T-08): el run de producción
  terminó a las 07:15:58Z y a las 07:23:35Z seguía sin aparecer → latencia
  **> 7 min 37 s**. La retención observada es de **≈67 días** y no es una
  garantía.
- **Alertas desplegadas** (T-06/T-13): una health rule de duración —avisa a
  partir de `RUN_DURATION_SECONDS > 1500`, con margen frente al
  `timeout_seconds: 1800`— manda correo por
  `on_duration_warning_threshold_exceeded` y está activa en dev y en prod.
  Producción corre con **9 tareas**, cron `50 0 23 ? * SUN`, `Europe/Madrid` y
  `UNPAUSED`.
- **Run programado real** (T-13, 2026-11-09): `762969079133747`, **9/9** tareas
  en **14 min 40 s**, con **11.710 filas** de bronce y **0** en cuarentena.
  `Publish_Run_Summary` escribió la fila de `ops.run_metrics` (`record.status
  ok`) con los cuatro `checks` en verde y `data_date 2026-11-07`; el dashboard
  `42581592569` publicó esa misma fecha y el warehouse quedó `STOPPED` antes y
  después. La health rule de 1500 s no se disparó, como se esperaba.
- **El event log se quedó fuera** (T-06/T-08): con `spark.eventLog.dir` en
  `abfss://` el clúster no llegaba a arrancar; `landing/_eventlogs/` siguió con
  **0 ficheros y 0 B**, así que se quitaron las tres claves.

## Conceptos clave (en mis palabras)

- **La fila responde «qué hizo el run»; la plataforma, «cuándo y dónde».** En
  `ops.run_metrics` van las métricas de negocio, la frescura y los `checks`; las
  duraciones y estados por tarea no se duplican porque su fuente es
  `system.lakeflow.job_task_run_timeline`. `ops` es derivado y reconstruible.
- **Un solo escritor.** Solo la tarea final escribe en `ops`; ningún notebook de
  negocio lo hace. Menos contención y un único sitio que mantener.
- **Best-effort y fail-fast no se mezclan.** `record` nunca propaga: un fallo de
  la métrica no puede tumbar una tarea de negocio. La que sí falla es la tarea
  final, por sus `checks`. Y escribe la fila **antes** del `raise`, para que un
  cierre en rojo deje evidencia.
- **`run_if: ALL_DONE`.** El cierre se lanza aunque otra tarea falle. Se probó
  el lanzamiento con 7 tareas `UPSTREAM_FAILED` en `873077346042958`; lo que no
  se ha observado es la combinación «fila escrita con **otra** tarea en rojo»
  (`[NO VERIFICADO]`). Lo observado es la fila con el **propio** cierre en rojo.
- **Checks con nombre y JSON estable.** `pending_files` exige `listing_ok is
  True` (no hay verde falso) y compara `commit < mtime < inicio del run`: lo
  que llega durante el run no cuenta. `freshness` usa el `data_date` del
  `meta.json` (el mismo que el dashboard) y 8 días. `parse_data_date` no lanza:
  una fecha vacía se publica como `null` y la caza el check.
- **`system.lakeflow` es la fuente de duraciones, con letra pequeña.** Las
  columnas `*_duration_seconds` a nivel de run son **0 en jobs multi-tarea**; la
  duración se calcula sumando `period_end_time - period_start_time`. La
  retención observada (≈67 días) es el alcance de hoy, no una promesa. Y la
  latencia para un run recién terminado es **> 7 min 37 s**, así que el cierre no
  puede depender de ella.
- **DBU no es euro.** `billing.usage` × `list_prices` da **0,045956 USD** de DBU
  a precio de lista para 744 s: sin VMs, discos, storage ni egress, y en USD.
  Frente a la estimación por duración (0,17–0,23 €/run) es **≈4× menor**, así
  que el método por duración queda como cota superior. Un despertar del
  warehouse 2X-Small son 10 min facturables ≈**0,43 €**.
- **Alertar sin despertar el warehouse.** Health rule de duración + email de
  fallo + frescura en el workflow del dashboard. Las alertas SQL programadas se
  descartaron porque cada despertar cuesta ~0,43 € y es recurrente.
  `STREAMING_BACKLOG_*` no se usa: está en Public Preview y los streams son por
  lotes.
- **El event log no es inocuo.** `spark.eventLog.dir` en `abfss://`
  dejó el clúster sin arrancar (`SparkMisconfiguration`, `fs.azure.account.key`).
  La Spark UI sigue siendo la superficie de trazas; `compute log delivery` queda
  como alternativa documentada y no implementada.
- **Query Profile mide el plan, no el reloj.** La consulta 1 tardó 2,603 s y
  devolvió 29 filas de 43 leídas: es una consulta de metadatos pequeña, con
  Photon al 58 % y un scan de 17 ms. El coste dominante es la ventana del
  warehouse, no el scan.

## Comandos y trucos

```powershell
# Estado del run sin SQL (gratis)
databricks jobs list-runs --job-id 916863262593632 --limit 3 --profile vs_code
databricks jobs get-run <run-id> --profile vs_code
databricks jobs get-run-output <task-run-id> --profile vs_code --output json

# Metadatos del warehouse: no lo despierta
databricks warehouses list --profile vs_code
```

- `system.lakeflow.job_run_timeline`: agrupar por `run_id, job_id,
  trigger_type` para tener una fila por run; duración = suma de
  `period_end_time - period_start_time`.
- `current_workspace_id()` **no resuelve** en este workspace
  (`[UNRESOLVED_ROUTINE]`); las consultas del RUNBOOK usan el literal
  `7405619889654446` y advierten de que es específico de este workspace.
- `billing.usage` se cruza con `list_prices` por `sku_name` y
  `price_end_time IS NULL`; `pricing.default` puede no reflejar descuentos
  (`pricing.effective_list` existe).
- El resumen del bronce viaja como cadena JSON en
  `dbutils.jobs.taskValues` y se publica también con `notebook.exit`.
- `dbutils.fs.head`/`ls` sobre ABFS funcionan; la API Java de Hadoop no tiene
  credenciales en el notebook. Para listar sin leer: `format("binaryFile")` con
  `recursiveFileLookup` y seleccionar solo `path` y `modificationTime`.
- Health rules: la clave es `health.rules[]` con `metric`/`op`/`value`; no
  existe `health_rules`.
- Para leer `ops.run_metrics` sin abrir una ventana de SQL, usa el clúster del
  job o un notebook en él; el warehouse, solo para la validación puntual.

## Errores que he cometido y qué aprendí

- **Configurar `spark.eventLog.dir` contra `abfss://`.** Aquello no arrancó
  (`SparkMisconfiguration: Invalid configuration value detected for
  fs.azure.account.key`): el bronce se pasó 472 s preparando el clúster y las 7
  tareas que dependían de él acabaron en `UPSTREAM_FAILED`. Lo que en teoría
  era solo diagnóstico dejó el pipeline sin ejecutar; se quitaron las tres
  claves.
- **Usar `spark._jsc`/`FileSystem.listFiles` para listar el landing.** Desde el
  notebook no tiene credenciales de ABFS y falló en vivo. La API DataFrame
  (`format("binaryFile")` con `recursiveFileLookup`) sí comparte las
  credenciales del job.
- **Confiar en `current_workspace_id()`.** No resuelve en este workspace
  (`[UNRESOLVED_ROUTINE]`, 4,8 s); las consultas del RUNBOOK pasaron al literal.
  Una función «portable» no lo es en todas las versiones.
- **Lanzar el ciclo desde un worktree sin `config.local.ps1`.** La URL salió
  como `https://.blob.core.windows.net/...` y AzCopy reintentó **~16 min por
  fuente** antes de fallar. Los originales quedaron en el `OutDir`, pero el run
  se perdió. El entorno local es parte del run.
- **Rechazar un fichero entero por 16 filas incoherentes de 1569.** La
  validación de `multi_site` tiró las 1569; ahora `ensure_compatible.py` separa
  las malas a un CSV y sube las válidas (T-15). Un gate de fichero convierte 16
  filas malas en 1569 perdidas.
- **Sumar las columnas `*_duration_seconds` del run.** En jobs multi-tarea son
  **0**; hay que sumar las rebanadas de `period_start_time`/`period_end_time`.
  Lo corregí en T-06 y lo validé con la consulta de T-07.
- **Esperar que `system.lakeflow` reflejara el run en minutos.** Para un run
  recién terminado la latencia es **> 7 min 37 s**; la fuente inmediata es
  `databricks jobs get-run`, no la tabla de sistema.

## Dudas pendientes

- **La entrega del correo de duración** no se ha podido probar: ningún run
  superó los 1500 s. La configuración está desplegada; se vería en un incidente
  real.
- **La cifra exacta de latencia de `system.lakeflow`**: sigue `[NO VERIFICADO]`.
  El run programado del 2026-11-08 ya existe, pero no se abrió una tercera
  sesión SQL para acotarla; se mantiene la cota inferior **> 7 min 37 s**. El
  cierre no depende de esa tabla.
- **El tiempo del bronce con ingesta**: 144–146 s en dos runs y 121 s en otro,
  sin causa aislada. La persona lo aceptó como despreciable y el umbral no se
  ajustó.
- **Los checks no avisan de que una fuente se quede sin lote del día.**
  `bronze_sources` solo comprueba que estén las cuatro; no distingue «presente
  con 0 filas» de «sin lote».
- **Fila con otra tarea en rojo:** solo se ha visto con el **propio** cierre en
  rojo; la combinación con otra tarea fallida sigue `[NO VERIFICADO]`. Tampoco
  está probada en la nube la idempotencia del mismo `run_id` en DBR 17.3 (solo
  en Delta local) ni la lectura de `system.*` desde el clúster del job.
- **El importe exacto de las dos ventanas SQL** sigue pendiente del Account
  Console: **≈0,86 € nominales** (2 × 0,43 €) y el warehouse `STOPPED` antes y
  después.
- **`compute log delivery`** queda documentada y no implementada; solo se
  valoraría si hiciera falta retención propia de trazas.
