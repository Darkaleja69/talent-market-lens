# Plan técnico: bronce deduplicado e ingesta incremental

> Detalle del **bloque A**. Actualizado al arrancar la spec (2026-10-21, tras
> mergear `main` con el cierre de la 007). El bloque B (silvers/gold
> incrementales) se planifica al cerrar el bloque A.

## 1. Objetivo y límites

Bronce = estado actual por oferta (upsert idempotente); el landing y las
silvers no cambian en este bloque. El bloque B (silvers/gold incrementales) se
planificará después.

## 2. Estado de partida (tras la 007)

- `databricks_notebooks/bronze_ingest.ipynb`: **un** notebook con cuatro
  secciones (indeed, linkedin, infojobs, multi_site). Cada una: Auto Loader
  (`cloudFiles`, parquet, sin notificaciones) + `checkpointLocation`
  `<checkpoints_root>/<fuente>/ckpt` + `schemaLocation .../schema` +
  `mergeSchema=true` + `trigger(availableNow=True)` + `.toTable(...)` (append).
  Añade `_ingest_date` y `_source_file` a partir de `_metadata`.
- Bundle de la 007 como vía de despliegue y validación: `databricks.yml` con
  targets `dev` (catalog `job_offers_dev`, checkpoints `_checkpoints_dev`,
  `export_prefix gold/web_export_dev`, job `Git Based ELT Dev`, sin
  programación) y `prod` (job `Git Based ELT` `916863262593632`, cron
  `50 0 23 * * ?`, `timeout_seconds: 1800`). El deploy **no ejecuta** el job.
- Auditoría del bronce (007, T-10a, solo lectura): `job_id` con **0 nulos y 0
  vacíos** en las cuatro tablas; `scraped_at` es string ISO-8601 con offset
  único `+00:00` (orden lexicográfico = cronológico); en `multi_site` la columna
  `site` llega poblada (6 valores) y la clave compuesta se forma **antes** del
  rename a `source_scraper`.
- `databricks_notebooks/Deduplicate_Job_Offers.py` (007, T-10a) ya tiene
  `keep_latest_by(df, keys, order_by=("scraped_at", "_ingest_date"))` con
  ventana `row_number()` y 9 tests: dedup del **lote** reutilizable por el
  upsert; las filas con clave nula o en blanco pasan sin reducir.
- Bronce dev ya está normalizado a 1 fila por clave (one-off de la 007, T-10):
  linkedin 13.376 y multi_site 10.489 claves; prod sigue acumulado (16.371 y
  11.562 filas).
- Línea base de tiempos (para comparar en T-05): run dev `540330014336242`
  17 m 43 s (bronze 7 m 36 s, 2026-10-18); primer run programado de prod
  `471695262858572` 19 m 45 s (bronze 9 m 03 s, 2026-10-19).

## 3. Arquitectura objetivo

```text
landing (cruda, snapshots acumulativos)
  → Auto Loader (checkpoint; ver §4 «checkpoint»)
  → foreachBatch: dedup del lote por clave + MERGE a bronze (upsert)
  → silvers (sin cambios en este bloque)

bronce (estado actual: 1 fila por clave)
  + una única ejecución one-off que deja prod en ese estado (RF-2)
```

Claves por fuente (una tabla por fuente, sin cambios de nombre):

| Fuente | Tabla bronce | Clave |
|---|---|---|
| indeed | `bronze.indeed` | `job_id` |
| linkedin | `bronze.linkedin` | `job_id` |
| infojobs | `bronze.infojobs` | `job_id` |
| multi_site | `bronze.multi_site` | `site` + `job_id` |

## 4. Diseño del upsert (a fijar en T-01)

- Clave por fuente: la tabla de §3. Se compara con `job_id` tal cual llega
  (string), sin normalizar: es la misma clave que ya usa la silver.
- Condición de actualización: la fila entrante gana si su `scraped_at` es
  **estrictamente mayor** que el de la guardada; empate → gana `_ingest_date`
  como desempate y, si también empata, no se actualiza (idempotencia). El
  orden lexicográfico es válido porque el offset es único (`+00:00`, T-10a);
  **confirmarlo en T-01** antes de apoyarse en él.
- Dedup del lote **antes** del MERGE con `keep_latest_by` (evita que dos
  ficheros del mismo microbatch se pisen en orden arbitrario).
- Claves nulas o en blanco: hoy no existen (auditoría T-10a), pero el diseño
  debe fijar qué ocurre para no insertar duplicados sin clave en cada lote.
  **Decidido (D4):** van a `bronze.<fuente>_quarantine` con motivo
  (`missing_key` / `missing_scraped_at`, `_quarantine_at` y `_source_file`),
  escritas en el mismo `foreachBatch` y **deduplicadas por huella de fila
  (`sha2` de las columnas) + motivo + fichero** para que un reproceso no las
  repita (por clave no vale: las filas sin clave no tienen clave). Se
  descartan del MERGE.
  Columnas: las de la fuente + las tres de control; tabla append-only con
  `mergeSchema`.
- Columnas de control: `_ingest_date` y `_source_file` se actualizan al ganar
  la fila nueva (reflejan la última captura que escribió la fila).
- Evolución de esquema: `mergeSchema` del stream intacto; en T-01 comprobar si
  el `MERGE` del runtime 17.3 evoluciona columnas nuevas solo
  (`spark.databricks.delta.schema.autoMerge.enabled`) o hay que activarlo, y
  qué pasa con `_rescued_data`. Ojo: con `foreachBatch`, el
  `.option("mergeSchema","true")` del `writeStream` ya no actúa sobre el sink.
- **Checkpoint (decidido, D5):** plan A, conservar
  `<checkpoints_root>/<fuente>/ckpt`. No se deduplica lo ya escrito, así que la
  one-off de migración (§5) es obligatoria; a cambio no se relee el landing
  (14,4 GB / 247 ficheros en dev). Plan B si el cambio de sink falla en T-03:
  `ckpt_v2` (relectura completa) + `drop` y reconstrucción.

## 5. Migración del bronce acumulado (RF-2)

- **dev:** ya está normalizado por la 007; T-04 verifica conteos y, si algún
  lote nuevo ha vuelto a duplicar, aplica la misma one-off.
- **prod:** una única ejecución de migración sobre la tabla existente
  (dedup por clave con la misma condición del upsert), en ventana, con conteos
  antes/después por fuente. Alternativa a comparar en T-01: `drop` + reingesta
  desde landing (más lento y depende del checkpoint) frente a one-off sobre la
  tabla (barata, conserva `_delta_log`).
- Rollback: redeploy del commit anterior del bundle (mecanismo de
  `specs/007-ELT-As-Code/cutover.md`) y, si la tabla queda inconsistente,
  `RESTORE`/time travel a la versión previa del `_delta_log`.

## 6. Comprobaciones

- Tests del módulo (pytest + Spark local): gana el más reciente, empate,
  desorden, clave nula/en blanco, idempotencia y contrato de columnas
  (mismo enfoque que los 9 tests de `test_dedup_job_offers.py`), más el
  enrutado a cuarentena (motivo y no duplicación al reprocesar).
- Ciclo dev en verde y **más rápido** que la línea base del §2, con
  idempotencia demostrada (segunda ejecución sin cambios de filas) y conteos
  comparados con prod para la misma ventana.
- Suites existentes en verde: `databricks_notebooks` y `scrapers-pipeline`.
- Prod: ventana acordada, rollback disponible, noche real verificada y
  bitácora del roadmap actualizada; notas `02-delta.md` y
  `04-incremental-architecture.md` escritas antes de cerrar.
- Evidencia por tarea en `specs/008-Delta-Incremental/evidence.md` (mismo
  formato que la 007: fecha, tarea, comprobación, resultado).

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| MERGE con esquema evolucionando | Probar con `mergeSchema` y una columna nueva en dev (T-03) |
| Llegada desordenada o ficheros viejos re-subidos | Condición por `scraped_at` + tests de desorden (T-02) |
| Checkpoint reutilizado no reprocesa el histórico | One-off de migración (§5) separada del ingest |
| MERGE por lote sobre tablas grandes en dev | Medir en T-03/T-05 con la línea base del §2 |
| Migración de prod en caliente | Ventana, conteos antes/después y `RESTORE` documentado |
| CI: el deploy de dev solo admite `main`/`spec/007-elt-as-code` | Decidir en T-03: ampliar el guard a `spec/008-delta-incremental` o desplegar por CLI local (`--profile vs_code`) |

## 8. Secuencia

1. **Bloque A:** T-01–T-06 (este documento).
2. **Bloque B:** silvers/gold incrementales (por detallar al cerrar el bloque A).
