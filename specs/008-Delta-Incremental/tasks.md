# Tareas: bronce deduplicado e ingesta incremental

> Detalle del **bloque A**, afinado al arrancar la 008 (2026-10-21) con el
> estado que deja la 007. Formato de aprendizaje guiado: **la persona
> implementa**; el asistente prepara las tareas, orienta y verifica (tests y
> revisión del diff antes del commit). Ninguna tarea se marca como hecha sin su
> comprobación.

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

- [ ] **T-03 — Ingesta con `foreachBatch` + MERGE (dev)** (~40 min)
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

- [ ] **T-04 — Verificación del bronce dev** (~25 min)
  - **Hecho cuando:** las tablas dev quedan en el universo único con conteos
    verificables por fuente (partida: linkedin 13.376 y multi_site 10.489
    claves, T-10 de la 007) y con la one-off aplicada solo si hace falta. La
    migración de **prod no va aquí: va en T-06**, antes del primer lote nuevo.
  - **Evidencia:** conteos antes/después y fila en `evidence.md`.

- [ ] **T-05 — Validación en dev** (~30 min)
  - **Hecho cuando:** ciclo dev en verde y más rápido que la línea base
    (run `540330014336242`: 17 m 43 s, bronze 7 m 36 s), con idempotencia
    demostrada (segunda ejecución sin cambios de filas) y conteos comparados
    con prod para la misma ventana.
  - **Evidencia:** tabla comparativa dev vs prod y fila en `evidence.md`.

- [ ] **T-06 — Prod con el mecanismo seguro de la 007** (~40 min)
  - **Hecho cuando:** en la ventana acordada, la one-off de migración de prod
    se ejecuta **antes** del primer lote del ingest nuevo (conteos
    antes/después por fuente y `RESTORE` disponible), se hace
    `bundle deploy -t prod` con rollback disponible (§5 del plan), hay una noche
    real verificada y la bitácora del roadmap queda actualizada.
  - **Evidencia:** ids de deploy/run, conteos, estado final y fila en
    `evidence.md`.

## 2. Bloque B — Silvers/gold incrementales

- Por detallar al cerrar el bloque A (reproceso idempotente por fecha y
  fuente; dominios 2 y 4 del temario).

## 3. Cierre de la spec

- Notas `docs/databricks-de/notes/02-delta.md` y
  `04-incremental-architecture.md` escritas con la plantilla del índice.
- Calendario y bitácora de `docs/databricks-de/roadmap.md` al día.
