# Tareas: bronce deduplicado e ingesta incremental (borrador)

> **Borrador.** Detalle del **bloque A**; se refinará al arrancar la 008.
> Formato de aprendizaje guiado: **la persona implementa**; el asistente
> prepara las tareas, orienta y verifica (tests y revisión del diff antes del
> commit). Ninguna tarea se marca como hecha sin su comprobación.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `feat(ingest): T-03 upsert con foreachBatch y MERGE`).
- Suites: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio.

## 1. Bloque A — Bronce upsert

- [ ] **T-01 — Diseño del upsert** (~25 min)
  - **Objetivo:** fijar claves, condición de actualización y tratamiento de
    llegadas desordenadas y claves nulas.
  - **Preguntas guía:** ¿qué clave por fuente? ¿qué condición decide que una
    captura reemplaza a la guardada? ¿qué pasa si vuelve un fichero viejo?
  - **Hecho cuando:** nota de diseño con 2–3 casos de ejemplo resueltos.
  - **Evidencia:** nota en la spec.

- [ ] **T-02 — Módulo de dedup/upsert + tests** (~35 min)
  - **Objetivo:** lógica comprobable (condición de merge y dedup del lote)
    con tests pytest + Spark local.
  - **Hecho cuando:** tests en verde cubriendo: gana el más reciente, empate,
    desorden y clave nula.

- [ ] **T-03 — Ingesta con `foreachBatch` + MERGE (dev)** (~35 min)
  - **Objetivo:** cambiar `bronze_ingest` manteniendo Auto Loader y el
    checkpoint; probar en dev.
  - **Hecho cuando:** un lote nuevo inserta/actualiza correctamente y
    reprocesar no cambia nada.

- [ ] **T-04 — Migración del bronce dev** (~25 min)
  - **Hecho cuando:** las tablas dev quedan en el universo único con conteos
    verificables por fuente.

- [ ] **T-05 — Validación en dev** (~30 min)
  - **Hecho cuando:** ciclo dev en verde, más rápido, con idempotencia y
    conteos comparados con prod para la misma ventana.

- [ ] **T-06 — Prod con el mecanismo seguro de la 007** (~30 min)
  - **Hecho cuando:** ventana ejecutada con rollback disponible y noche real
    verificada.

## 2. Bloque B — Silvers/gold incrementales

- Por detallar al cerrar el bloque A (reproceso idempotente por fecha y
  fuente; dominios 2 y 4 del temario).
