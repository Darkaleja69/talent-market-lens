# Tareas: rendimiento del incremental, layout físico y compute

> Borrador del **2026-10-28**, nacido del bloqueo de T-10 en la 008. Formato de
> trabajo según `AGENTS.md`: **la persona decide** el alcance y revisa; el
> asistente prepara las tareas, implementa, explica y commitea tras la
> verificación independiente. Ninguna tarea se marca como hecha sin su
> comprobación.
>
> **Base de la rama:** creada desde `spec/008-delta-incremental` porque el
> incremental aún no está en `main`; se rebasa sobre `main` al fusionar o
> descopear la 008. El trabajo de código no empieza hasta ese rebase.
>
> **Aprobación previa (AGENTS.md):** antes de tocar
> `databricks_notebooks/**/*.ipynb`, un módulo `*.py` de
> `databricks_notebooks/` o cualquier `.yml`, se presenta fichero, celdas/claves
> afectadas, diff propuesto y motivo, y se espera el OK. Los **tests quedan
> fuera** de esta regla.
>
> **Presupuesto:** **3 runs de dev** (~0,72 €) y **0 sesiones SQL**, con techo
> de 1,0 € (el exceso de ~0,72 € de la 008 ya está descontado). El run de
> reserva no se gasta sin aprobación. Cada run se anota en `docs/costs.md`.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `perf(silver): T-03 quitar pasadas redundantes del incremental`).
- Suites: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio.
  La suite completa es gate de la spec (T-10), no de cada tarea.
- Evidencia: una fila por comprobación en `evidence.md` (fecha, tarea,
  comprobación, resultado).

## 0. Decisión pendiente (persona)

- **Descopear el bloque B de la 008** (dejarla cerrable con el bloque A) o
  mantenerla abierta. Si se descopea, **T-09** de esta spec cierra también
  T-10/T-11 de la 008; si no, esta spec entrega la evidencia y la 008 ejecuta
  sus tareas. Nada de código depende de esta decisión; solo el dueño de los
  checkboxes y del cierre.

## 1. Diagnóstico y banco de pruebas

- [ ] **T-01 — Diagnóstico del coste, sin runs** (~60 min)
  - **Objetivo:** atribuir el tiempo del incremental por etapa y por operador
    con evidencia, no con hipótesis. Escribir `design-performance.md` con la
    tabla de costes, las hipótesis confirmadas/descartadas y la lista de
    cambios justificados.
  - **Fuentes gratis:** Spark UI de B2 (`1056455747512203`) y B3
    (`1015448868873733`) —SQL por consulta, stages, tareas por stage, shuffle,
    spill, GC, memoria pico, fracción cacheada—; `explain("formatted")` y
    `explain("codegen")` en local; inventario de acciones de `_run_once`
    (`plan.md` §2.2); `DESCRIBE DETAIL` para layout.
  - **Preguntas guía:** ¿qué stage se lleva el tiempo y cuántas veces se repite
    la cadena de `build_offers`? ¿la caché de `recompute` aguanta con cuatro
    silvers a la vez? ¿cuánto código genera la proyección (64 KB)? ¿el MERGE
    reescribe toda la tabla? ¿cuántas tareas por stage hay con 4 vCPU?
  - **Hecho cuando:** `design-performance.md` con la atribución del coste
    (tabla por paso y por stage), las hipótesis H1–H6 de `plan.md` §4 marcadas
    como confirmadas/descartadas con su evidencia, y la lista priorizada de
    cambios; revisado por `verifier`.
  - **Evidencia:** capturas o cifras de la Spark UI, planes, tiempos locales y
    fila en `evidence.md`. **0 €.**

- [ ] **T-02 — Instrumentación del resumen y banco local** (~60 min)
  - **Objetivo:** que el siguiente run diga dónde se va el tiempo sin añadir
    pasadas: `timings_s` por paso en el resumen de `Silver_Incremental.run` y
    `merge_metrics` leídas de `DESCRIBE HISTORY` en el clúster del job. Banco
    local reproducible con dos escenarios (ventana ancha y ventana ≤1 %) y
    tiempos antes/después.
  - **Hecho cuando:** el resumen conserva los 16 campos y añade `timings_s` y
    `merge_metrics`; **no lanza acciones nuevas** (demostrado con el número de
    jobs del plan); tests de contrato del resumen y del banco en verde;
    `explain("codegen")` del banco midiendo el tamaño del código.
  - **Aprobación previa:** módulo `Silver_Incremental.py` (diff y motivo).
  - **Evidencia:** salida de la suite, tabla de tiempos del banco y fila en
    `evidence.md`. **0 €.**

## 2. Optimización

- [ ] **T-03 — Camino incremental: pasadas, caché y codegen** (~90 min)
  - **Objetivo:** aplicar solo los cambios que T-01 justifique (H1/H2/H5):
    quitar acciones redundantes, ajustar la caché/`localCheckpoint`, partir la
    proyección que rompe el codegen y decidir la concurrencia de las silvers.
    Sin cambiar la semántica ni el contrato.
  - **Hecho cuando:** el banco local baja del criterio de `plan.md` §3
    (`T_inc ≤ T_full` con ventana ancha y `≤ T_full/3` con ventana pequeña);
    los tests de equivalencia e idempotencia del bloque B siguen en verde; el
    diff de notebooks/módulo está aprobado y no toca celdas de negocio.
  - **Evidencia:** tiempos antes/después por paso, `explain` del plan nuevo y
    fila en `evidence.md`. **0 €.**

- [ ] **T-04 — MERGE real: medir y decidir** (~45 min)
  - **Objetivo:** cerrar H3 con números: duración y filas reescritas del
    `MERGE`, y decidir si compensa materializar `_key` en silver (o una tabla
    de stage) para convertir el `ON` en igualdad de columna.
  - **Preguntas guía:** ¿el `MERGE` copia toda la tabla? ¿cuánto pesa frente al
    rebuild completo? ¿la poda por estadísticas es posible sin `_key`? ¿qué
    rompe una columna interna en `companies_*`, oro y exports?
  - **Hecho cuando:** la decisión (materializar o no) está escrita con la
    medición que la sostiene; si se materializa, contrato, proyecciones
    explícitas, `RESTORE` y tests actualizados; si no, queda documentado el
    motivo y el coste aceptado. **Aprobación previa** del diff y de la
    migración.
  - **Evidencia:** `operationMetrics` del MERGE (paso 14 de `plan.md` §2.2),
    comparación de tiempos y fila en `evidence.md`. **0 €** (se mide en T-07/
    T-08).

- [ ] **T-05 — Layout físico: medir y decidir** (~30 min)
  - **Objetivo:** medir `numFiles`, `sizeInBytes` y tamaño medio por tabla
    (bronce, silver, oro) y decidir `OPTIMIZE`, `liquid clustering` o **no
    cambiar**, con el efecto en oro y exports si se toca.
  - **Hecho cuando:** la decisión está escrita con la tabla de ficheros y el
    motivo (incluido el «no cambiar»); si se aplica `OPTIMIZE`, su coste y su
    efecto están medidos.
  - **Evidencia:** `DESCRIBE DETAIL` por tabla y fila en `evidence.md`. **0 €**
    (lectura en el job, sin warehouse).

- [ ] **T-06 — Compute y concurrencia: decisión** (~30 min)
  - **Objetivo:** decidir el compute de producción con la evidencia de T-01 y
    T-07/T-08: `D4ds_v4` single node, `D8ds_v4`, multi-nodo o serverless;
    paralelo o secuencial; y el `timeout_seconds` definitivo (sin subirlo como
    solución).
  - **Hecho cuando:** la tabla de opciones de `plan.md` §7.2 tiene una decisión
    con medida, coste por run y motivo de descarte del resto; si cambia el
    `.yml`, diff aprobado y `bundle validate` en verde.
  - **Evidencia:** tiempos y spill por configuración, coste estimado y fila en
    `evidence.md`. **0 €** (sin runs extra; la reserva exige aprobación).

## 3. Validación y producción

- [ ] **T-07 — Run A (reconstrucción completa) en dev** (~20 min)
  - **Hecho cuando:** 8/8 tareas en verde, silvers ≤ **3 m 46 s** (A2), run
    completo ≤ **22 m 30 s**, exports escritos con `meta.json` válido y SHA-256
    de los 12 Parquet registrado, y `timings_s`/`merge_metrics` en la salida.
  - **Ejecución (run 1 de 3):** `databricks bundle deploy -t dev --profile vs_code`
    (gratis) y `databricks bundle run elt -t dev --profile vs_code`. Sin SQL.
  - **Evidencia:** id del run, tabla de tiempos por tarea y por paso, SHA-256,
    coste en `docs/costs.md` y fila en `evidence.md`.

- [ ] **T-08 — Run B (incremental + repetición) en dev** (~25 min)
  - **Hecho cuando:** el run cierra 8/8 con `backfill_from`/`backfill_to` y
    `repeat_window=true`; **el MERGE escribe** (métricas de filas insertadas/
    actualizadas/borradas); los exports son **idénticos byte a byte** a los de
    A; la repetición deja el mismo contenido; y el tiempo cumple el criterio de
    `plan.md` §3.
  - **Ejecución (run 2 de 3):** `bundle run elt -t dev --profile vs_code
    --params backfill_from=...,backfill_to=...,repeat_window=true`. La ventana
    exacta se anota en el resumen.
  - **Evidencia:** id del run, `timings_s` por paso, `merge_metrics`, SHA-256
    A/B, conteos, coste y fila en `evidence.md`.

- [ ] **T-09 — Herencia de la 008: producción** (~30 min)
  - **Hecho cuando:** tras cerrar T-06, `bundle deploy -t prod` con rollback de
    bundle y `RESTORE` de las silvers disponible; el **primer run programado**
    reconstruye (migración) y cierra 8/8; el **domingo siguiente** el run es
    incremental real y el dashboard vuelve a publicar el lunes; SQL warehouse
    `STOPPED` antes y después; bitácora del roadmap actualizada.
  - **Nota:** no añade runs manuales (cadencia de la 009). Si la 008 conserva
    el bloque B, esta tarea se limita a entregar la evidencia y la ejecuta la
    008.
  - **Evidencia:** ids de deploy y runs, conteos, versiones de `RESTORE`,
    estado del warehouse y fila en `evidence.md`.

## 4. Cierre

- [ ] **T-10 — Cierre de la spec**
  - Nota `docs/databricks-de/notes/01-spark-performance.md` escrita con la
    plantilla del índice (qué se practicó, concepto, comandos, errores, dudas).
  - Calendario y bitácora de `docs/databricks-de/roadmap.md` al día; suites
    completas en verde; comprobación real registrada; `docs/costs.md` con todos
    los runs (dentro del techo de 1,0 €).
  - **Hecho cuando:** merge a `main` y tag (`spec-010`) solo con RF-2/RF-3/RF-4
    verificados en dev y RF-8 cerrado (o entregado a la 008).
