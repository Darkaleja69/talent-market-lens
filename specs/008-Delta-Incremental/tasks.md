# Tareas: bronce deduplicado e ingesta incremental

> Detalle del **bloque A**, afinado al arrancar la 008 (2026-10-21) y
> **actualizado el 2026-10-24** con la herencia de la 009 (coste y cadencia).
> Formato de trabajo según `AGENTS.md`: **la persona decide** el alcance y
> revisa; el asistente prepara las tareas, implementa el código y lo commitea
> tras la verificación independiente. Ninguna tarea se marca como hecha sin su
> comprobación.
>
> **Presupuesto (009, `plan.md` §17):** 3 runs de dev y 0 sesiones SQL. Reparto:
> T-04 (run corto one-off), T-05 (ciclo completo) y T-05 (repetición por
> idempotencia). Los 2 runs de T-03 son anteriores a la política. Prod se
> verifica con el run programado del domingo, no con un run manual. Cada run se
> anota en `docs/costs.md` con motivo, id y coste estimado.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `feat(ingest): T-03 upsert con foreachBatch y MERGE`).
- Suites: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio.
- Evidencia: una fila por comprobación en `evidence.md` de esta spec (formato
  de la 007: fecha, tarea, comprobación, resultado).

## 1. Bloque A — Bronce upsert

- [x] **T-01 — Diseño del upsert** (~30 min)
  - **Objetivo:** fijar claves, columnas y tipos por fuente, condición de
    actualización, tratamiento de llegadas desordenadas y de filas inválidas, y
    estrategia de checkpoint (§4 del plan).
  - **Preguntas guía:** ¿qué columna clave y temporal tiene cada tabla del
    bronce (no se llaman igual)? ¿qué condición exacta decide que una captura
    reemplaza a la guardada, y cómo se rompe un empate? ¿qué pasa con un blanco
    o un valor imparseable con ANSI activo? ¿qué pasa si vuelve un fichero
    viejo? ¿qué pasa con una clave o temporal nula (cuarentena, RF-6)? ¿se
    conserva el checkpoint actual (plan A) o se estrena `ckpt_v2`? ¿cómo se
    evoluciona el esquema con `foreachBatch`?
  - **Hecho cuando:** `design-upsert.md` con el mapa de columnas (§D0), los
    casos resueltos, las cinco decisiones cerradas, el esquema de cuarentena y
    el encaje con el dedup de oro (D1); revisado por `verifier`.
  - **Evidencia:** nota en la spec, resultado de las consultas de auditoría
    (duplicados de negocio y offsets) y fila en `evidence.md`.
  - **Estado:** diseño escrito y revisado en tres rondas (nombres de columna por
    fuente y riesgo de ANSI; sintaxis de `MERGE WITH SCHEMA EVOLUTION` y
    desajustes spec/diseño; `_scraped_ts` auxiliar). **Cerrada** el 2026-10-21
    con la auditoría de negocio pegada en `evidence.md` (25 grupos, todos
    cross-site). Quedan pendientes las consultas de claves/temporales de indeed
    e infojobs y los offsets, que se ejecutan en T-03.

- [x] **T-02 — Módulo de dedup/upsert + tests** (~45 min)
  - **Objetivo:** lógica comprobable (mapa de columnas por fuente, dedup del
    lote, condición de merge y enrutado a cuarentena) con tests pytest + Spark
    local. Reutilizar la semántica de `keep_latest_by` de
    `Deduplicate_Job_Offers.py` (007) sin modificar ese módulo.
  - **Hecho cuando:** tests en verde cubriendo: gana el más reciente, empate
    por `_ingest_date`, desorden, **misma clave con dos capturas del mismo día
    y horas distintas (gana la de hora posterior)**, clave nula/en blanco y
    temporal nula/blanca/imparseable (motivo correcto), clave nueva se inserta,
    `site` nulo en multi_site, huella de fila + motivo + fichero no se duplica
    al reprocesar, idempotencia y contrato de columnas.
    `python -m pytest databricks_notebooks/tests -q` en verde.
  - **Evidencia:** salida de la suite y fila en `evidence.md`.
  - **Estado:** hecho el 2026-10-21. `Upsert_Job_Offers.py` + 24 tests; verificación
    independiente PASA (24 passed en 1 m 51 s, SQL de las 4 fuentes parseado en
    Spark 4.2). Pendiente para T-03: ejecución real del MERGE, orden
    `split_quarantine` → `dedupe_batch` y `_source_file` siempre presente.

- [x] **T-03 — Ingesta con `foreachBatch` + MERGE (dev)** (~40 min)
  - **Objetivo:** cambiar `bronze_ingest` manteniendo Auto Loader, checkpoint
    (plan A: el actual) y el esquema de columnas; escribir la cuarentena en el
    mismo `foreachBatch`; probar en dev con el bundle
    (`databricks bundle deploy -t dev --profile vs_code`; el deploy no ejecuta
    el job). Decidir el guard de rama del workflow de deploy de dev (§7 del
    plan).
  - **Hecho cuando:** un lote nuevo inserta/actualiza correctamente, una
    columna nueva aparece sin romper, reprocesar no cambia nada y una fila
    inválida de prueba acaba en `bronze.<fuente>_quarantine` sin abortar el
    lote. Run dev y tiempos anotados.
  - **Evidencia:** id del run, tiempos y fila en `evidence.md`.
  - **Estado:** **cerrada el 2026-10-24.** Implementada y verificada en local el
    2026-10-21; run dev `339441526455403` en verde (13 m 39 s; bronze 5 m 11 s;
    7/7 tareas), tipos y offsets confirmados. El reproceso sin cambios se valida
    en T-05 (run B), no aquí. Quedan **no ejecutadas** dos comprobaciones de
    punta a punta: una columna nueva con evolución de esquema y una fila inválida
    en cuarentena. No se pueden hacer en dev sin inyectar ficheros en el landing,
    que es **compartido con producción** (mismo `container`), y el siguiente run
    de prod los ingeriría. **Aceptado por la persona el 2026-10-24 como
    limitación documentada:** la ruta está cubierta por los tests del MERGE y de
    la cuarentena, y el run semanal la ejercita de verdad cuando un scraper añade
    una columna (aviso por email si falla).
  - **Coste:** 2 runs de dev el 2026-10-21 (uno fallido y el verde), ~0,48 €,
    **anteriores a la política de la 009**; quedan registrados como hundidos y
    no consumen el presupuesto de 3 runs.

- [x] **T-04 — Verificación del bronce dev** (~25 min)
  - **Hecho cuando:** las tablas dev quedan en el universo único con conteos
    verificables por fuente, con la one-off aplicada donde hace falta. La
    migración de **prod no va aquí: va en T-06**, antes del primer lote nuevo.
  - **Ejecución (run 1 de 3):** job **`dedupe_oneoff`** (`resources/oneoff.job.yml`,
    sin programación y con clúster propio `D4ds_v4` single node) con cuatro
    tareas encadenadas, una por fuente, sobre
    `databricks_notebooks/oneoff_dedupe_bronze.ipynb` (`catalog`, `source`,
    `dry_run`): `databricks bundle run dedupe_oneoff -t dev`. ~9 min (lo domina
    el arranque del clúster; medido en 8 m 58 s), ~0,05–0,13 €; nada de SQL
    Editor. La one-off es idempotente y se reutiliza tal cual en T-06. Los
    conteos antes/después salen de la salida del propio notebook, junto con el
    `RESTORE` de la versión previa.
  - **Estado:** **cerrada el 2026-10-24.** `dedupe_overwrite_sql` +
    `valid_key_sql` en el módulo (35 tests en verde), notebook con resumen JSON
    de salida y job `dedupe_oneoff`. Run dev `328818148400464`: **SUCCESS**, 4/4
    tareas, 8 min 58 s, ~0,11 €. Resultado: indeed 3.039 filas/3.039 claves,
    infojobs 2.617/2.617, linkedin 14.289/14.289 y multi_site 11.067/11.067, con
    **0 filas sin clave** en las cuatro y `RESTORE` disponible a las versiones
    7/7/14/8. Las claves crecieron respecto al 22-oct (2.959/2.587/14.084/11.057)
    porque el run dev de la 009 (`18912486133844`, 23-oct) ingirió ficheros
    nuevos en dev con el sink *append-only* de su rama.
  - **Evidencia:** id del run, conteos después (el antes queda `[NO VERIFICADO]`),
    versiones de rollback, coste en `docs/costs.md` y fila en `evidence.md`.

- [x] **T-05 — Validación en dev** (~30 min)
  - **Hecho cuando:** ciclo dev en verde con las 8 tareas; **bronce: tiempo de
    ejecución ≤ 2 m 29 s** (T-03 `339441526455403`, sink *upsert*), excluyendo el
    arranque del clúster (2 m 42 s–6 m 52 s entre runs; no mide el código); run
    completo por debajo de los **16 m 19 s** del run de la 009 (8 tareas) y de
    los 17 m 43 s de la línea base *append* diaria; se anota el tamaño del lote
    (`numInputRows` y días desde el run anterior). Los dos exports
    (`gold/web_export_dev`, `gold/pbi_export_dev`) quedan escritos con `meta.json`
    válido y los conteos se comparan con prod para la misma ventana.
  - **Ejecución (runs 2 y 3 de 3):** `databricks bundle deploy -t dev --profile vs_code`
    (gratis; sube el estado actual de la rama) y
    `databricks bundle run elt -t dev --profile vs_code` **dos veces seguidas**:
    **run A**, ciclo completo con las 8 tareas; **run B**, repetición inmediata
    sin ficheros nuevos en el landing → `numInputRows = 0` por fuente, conteos de
    bronce y cuarentena sin cambios (idempotencia). Toda la evidencia sale de las
    métricas y salidas de los runs; **ninguna consulta al SQL warehouse**.
  - **Evidencia:** ids de los dos runs, tabla comparativa dev vs prod (bronce del
    one-off en T-04; gold de los `meta.json`; `numInputRows = 0` en el run B),
    coste de cada run en `docs/costs.md` y fila en `evidence.md`.
  - **Estado:** **cerrada el 2026-10-24.** Run A `274846748795055`: 8/8, 13 m 16 s
    (bronce exec 1 m 46 s, ≤ 2 m 29 s de T-03). Run B `683591523413230`: 8/8,
    8 m 43 s (bronce exec 1 m 04 s). Idempotencia demostrada: A y B producen
    `gold/pbi_export_dev` **idéntico** (33.790.833 B, `fact_offers` 26.057,
    `data_date` 2026-10-24; generados 15:23:23Z y 15:33:07Z), es decir, la
    reejecución no cambia datos. `numInputRows` no es legible por API porque
    `bronze_ingest` no devuelve resumen (hueco de observabilidad anotado para la
    010); la igualdad de los exports lo sustituye como prueba.

- [ ] **T-06 — Prod con el mecanismo seguro de la 007** (~40 min)
  - **Hecho cuando:** en la ventana **previa a un domingo**, la one-off de
    migración de prod se ejecuta **antes** del primer lote del ingest nuevo
    (conteos antes/después por fuente y `RESTORE` disponible), se hace
    `bundle deploy -t prod` con rollback disponible (§5 del plan), el **run
    programado del domingo** cierra 8/8 tareas con los exports actualizados, el
    dashboard publica el lunes con la fecha correcta y el SQL warehouse sigue
    `STOPPED` (sin consultas del informe); la bitácora del roadmap queda
    actualizada. El procedimiento paso a paso (pausa, deploy, one-off, reanudar,
    verificación y rollback) está en `cutover.md` de esta spec.
  - **Ejecución (0 runs de dev):** la one-off de prod se lanza como run corto en
    producción (~0,05–0,13 €, dentro del techo mensual de la 009) y la
    verificación real es el run programado del domingo, **sin run manual extra**.
    Si el domingo falla, el dashboard no publica pasados 8 días: se corrige y se
    espera al domingo siguiente, con la web mostrando la fecha real de los datos.
  - **Evidencia:** ids de deploy/one-off/run del domingo, conteos, versiones de
    `RESTORE`, estado final, coste en `docs/costs.md` y fila en `evidence.md`.

## 2. Bloque B — Silvers/gold incrementales

- Por detallar al cerrar el bloque A (reproceso idempotente por fecha y
  fuente; dominios 2 y 4 del temario).

## 3. Cierre de la spec

- Notas `docs/databricks-de/notes/02-delta.md` y
  `04-incremental-architecture.md` escritas con la plantilla del índice.
- Calendario y bitácora de `docs/databricks-de/roadmap.md` al día.
