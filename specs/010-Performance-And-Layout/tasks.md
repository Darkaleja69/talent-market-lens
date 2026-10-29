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
> **Presupuesto (aprobado el 2026-10-28):** **3 runs de dev** (~0,72 €) y
> **0 sesiones SQL**, dentro del techo de **1,8 €** que la 009 asigna a esta
> spec. La propuesta cambia el reparto de la 009 (2 runs + 1 sesión SQL) por
> 3 runs y 0 SQL: ahorra ~1,29 € de sesión y añade ~0,24 € de run. El run de
> reserva no se gasta sin aprobación. Cada run se anota en `docs/costs.md`.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `perf(silver): T-03 quitar pasadas redundantes del incremental`).
- Suites: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio.
  La suite completa es gate de la spec (T-10), no de cada tarea.
- Evidencia: una fila por comprobación en `evidence.md` (fecha, tarea,
  comprobación, resultado).

## 0. Decisiones tomadas (2026-10-28)

- **El bloque B se descopea de la 008.** T-10 (validación A/B) y T-11
  (producción) se cierran aquí: **T-08** y **T-09**. La 008 se cerrará con el
  bloque A, previo registro de la comprobación real de T-06.
- **Presupuesto aprobado:** 3 runs de dev + 0 sesiones SQL dentro del techo de
  1,8 € de la 009 (sustituye al reparto 2 runs + 1 SQL).
- **Fix de `keyed`:** el cambio que se probó en B3 se revirtió en la 008 y se
  re-evalúa en T-03 (parche guardado en
  `%LOCALAPPDATA%\Temp\opencode\keyed-share.patch` durante el descope).
- **T-06 (bloque A) confirmada en verde** el 2026-10-28; falta anotar el id del
  run y los tiempos en `specs/008-Delta-Incremental/evidence.md`.

## 1. Diagnóstico y banco de pruebas

- [x] **T-01 — Diagnóstico del coste, sin runs** (~60 min)
  - **Objetivo:** atribuir el tiempo del incremental por etapa y por operador
    con evidencia, no con hipótesis. Escribir `design-performance.md` con la
    tabla de costes, las hipótesis confirmadas/descartadas y la lista de
    cambios justificados.
  - **Fuentes gratis:** Spark UI de B2 (`1056455747512203`) y B3
    (`1015448868873733`) —SQL por consulta, stages, tareas por stage, shuffle,
    spill, GC, memoria pico, fracción cacheada—; `explain("formatted")` y
    `explain("codegen")` en local; inventario de acciones de `_run_once`
    (`plan.md` §2.2); `DESCRIBE DETAIL` para layout.
  - **Primero de todo:** capturar y archivar la Spark UI/event log de B2 y B3;
    su retención no está garantizada y sin ella el diagnóstico se queda sin
    fuente.
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
  - **Estado (2026-10-28):** **cerrada.** `design-performance.md` (4 revisiones,
    14 marcas `[NO VERIFICADO]`) y 9 capturas archivadas en
    `docs/media/performance/`. Lo medido: A2 **57 consultas de 15 s** frente a
    49–55 de 1,4–1,6 min del incremental (5,6–6,4× por consulta, con **más**
    consultas en el modo completo); stages de A2 con **1–4 tareas** frente a
    **200** del incremental y **10.790 tareas** en B3; 0,70–0,87 s por tarea;
    `MERGE` 1,1–2,4 min; **≈6–7×** el rebuild. **H4 confirmada en lo medido**,
    H1/H2/H3 parciales, **H6 descartada como causa principal** (pocos ficheros)
    y la caché se usó (508 stages *skipped*). **P1 = particiones de `shuffle`**
    (200 → 8–16): 22 min → ~2–3 min estimados. Verificación independiente
    **PASA CON MENORES** (cuatro avisos corregidos).

- [x] **T-02 — Instrumentación del resumen y banco local** (~60 min)
  - **Objetivo:** que el siguiente run diga dónde se va el tiempo sin añadir
    pasadas: `timings_s` por **acción** en el resumen de
    `Silver_Incremental.run`, `merge_metrics` leídas de `DESCRIBE HISTORY` en
    el clúster del job y la **config efectiva** de
    `spark.sql.shuffle.partitions` y `spark.sql.adaptive.enabled`. Banco local
    reproducible con dos escenarios (ventana ancha y ventana ≤1 %) y tiempos
    antes/después del cambio de T-03b.
  - **Método (aprobado el 2026-10-28):** reloj de pared **alrededor de cada
    acción** (`time.monotonic()` envolviendo la llamada que dispara el job).
    Las transformaciones son perezosas, pero las acciones son síncronas: medir
    alrededor de la acción sí atribuye el tiempo, y **no** hace falta
    `SparkListener`. Sustituye al `setJobGroup` + listener de `plan.md` §5 (se
    sincroniza el plan al cerrar la tarea).
  - **Contrato:** los 16 campos actuales se conservan; se añaden `timings_s`,
    `merge_metrics` y `config` (aditivo). `merge_metrics` nunca rompe el run:
    si `DESCRIBE HISTORY` no está disponible (local sin Delta), vale `None`.
  - **Desvío respecto a `tasks.md` original:** `DESCRIBE DETAIL` de las 8
    tablas **no** entra en `_run_once` (serían consultas nuevas por silver y
    rompería el «0 acciones nuevas»): se hace en **T-05**, que es su sitio.
  - **Nota:** `DESCRIBE HISTORY` es SQL de **Spark** en el clúster del job, no
    del SQL warehouse (0,43 € por despertar). Ninguna sesión de SQL Editor.
  - **Hecho cuando:** el resumen conserva los 16 campos de negocio con los
    mismos valores y añade los 3 nuevos; el número de acciones no crece
    (test de contrato: mismas claves de `timings_s`, mismos conteos y mismo
    número de llamadas al `MERGE`); tests en verde; banco local con tabla de
    tiempos por paso y tamaño del código de `explain("codegen")`.
  - **Aprobación previa:** **dada el 2026-10-28** (fichero
    `Silver_Incremental.py`, zonas `SUMMARY_FIELDS`/`_run_once`/`_summary`,
    diff esquemático presentado).
  - **Evidencia:** salida de la suite, tabla de tiempos del banco y fila en
    `evidence.md`. **0 €.**
  - **Estado (2026-10-29): CERRADA**, verificación independiente **PASA**
    (2 rondas: 4 menores corregidos). 37 tests del fichero en verde
    (864,85 s). El banco local (`local[4]`, 8 particiones, `--label before`):
    **full 56,9 s / 42,5 s**, **wide 98,2 / 88,3 s**, **small 68,9 / 65,6 s**;
    en wide, `changed_empty` (keyed del bronce completo) **26,7 s**,
    `recompute_nulls` (primera materialización de la cadena) **26,6 s**,
    `merge` (referencia, sin Delta) **12,4 s**, resto de acciones **12,6 s** y
    **~20 % del reloj fuera de los pasos** (planificación, `cache`/`unpersist`,
    `DESCRIBE HISTORY`, `_config`). En small, `changed_empty` sigue costando
    **24,6 s** aunque solo cambien 310 filas. **H1 confirmada como condición
    presente**: `explain("codegen")` = **2.640.741 caracteres, 8 subtrees** y
    aviso «Code grows beyond 64 KB» **disparado** (del log4j del JVM).
    `[NO VERIFICADO]` el MERGE real y la escritura física del `full`.
    **Siguiente:** T-03b con estos números.

## 2. Optimización

- [ ] **T-03 — Camino incremental: pasadas, caché y codegen** (~90 min)
  - **Objetivo:** aplicar solo los cambios que T-01 justifique. Por orden:
    **(1) P1, particiones de *shuffle*** (`spark.sql.shuffle.partitions` de 200 a
    8–16, con la config efectiva ya leída en T-02), **(2)** coste por tarea y
    codegen (partir la proyección que rompe el codegen) y **(3)** menos
    acciones, que T-01 ha degradado a bajo valor. Sin cambiar la semántica ni el
    contrato.
  - **Nota de secuencia:** P1 mueve la aguja (tareas por silver de ~2.700 a
    ~150–250 y silver de 22 min a ~2–3 min estimados); si se deja para T-06, el
    resto de la optimización se mediría sin la palanca principal. Aquí se
    **experimenta**; la config definitiva (`.yml` del clúster o código) se fija
    en T-06.
  - **Estado (2026-10-28): primer cambio aplicado y medido; T-03 sigue abierta.**
    `resources/elt.job.yml` fija `spark.sql.shuffle.partitions: "8"` (una línea,
    aprobada, revertible con deploy). Runs con el cambio:
    - **A** `485578578869927` (`force_full=true`): **SUCCESS**, 8/8, **808 s**;
      silvers 117–169 s de módulo (tareas 167–204 s), como A2.
    - **B** `269508392317481` (ventana 24–28, `repeat_window=true`):
      **SUCCESS**, 8/8, **974 s**; dos pases de **4 m 09 s–5 m 18 s** y
      **2 m 53 s–4 m 09 s**; `MERGE` con 55–458 filas; conteos finales idénticos
      a A (3.112 / 2.527 / 12.543 / 10.072).
    - **El run ya cabe** en la ventana de producción (16 m 14 s frente a 1800 s;
      46 % de margen) y el incremental dejó de expirar (antes, >22 min por
      silver y `TIMEDOUT`). **Pero RF-2a no se cumple todavía**: el pase
      incremental cuesta ~2× el rebuild completo (1 m 57 s–2 m 49 s), no ≤ 3 m 46 s.
    - **Lo que queda** ya no es volumen ni particiones: es el **coste fijo por
      consulta** (11 acciones × ~20–30 s) y el `MERGE`. Siguiente paso: T-02
      (medir por acción) y, con eso, recortar acciones (P3) y/o el coste por
      tarea (P2). Consume 2 runs de los 3; el abortado gastó ~0,02 €.
  - **Commits:** **dos**, uno por familia de cambio (codegen; pasadas y caché),
    para poder revertir una sin la otra. El fix de `keyed` compartido que se
    revirtió en la 008 (caché del frame `keyed` entre los dos pases de
    `repeat_window`) se re-evalúa aquí, con test y medición; no entra por
    inercia.
  - **Hecho cuando:** el banco local baja del criterio de `plan.md` §3
    (`T_inc ≤ 3 m 46 s` con ventana ancha y `≤ 75 s` con ventana pequeña); los
    tests de equivalencia e idempotencia del bloque B siguen en verde; el diff
    está aprobado y, si el corte del codegen cae en una celda del notebook, se
    demuestra en el diff que **no cambia ninguna expresión de negocio** (solo
    dónde se materializa).
  - **Evidencia:** tiempos antes/después por etapa, `explain` del plan nuevo y
    fila en `evidence.md`. **0 €.**

- [ ] **T-04 — MERGE real: medir y decidir** (~45 min)
  - **Objetivo:** cerrar H3 con números: duración y filas reescritas del
    `MERGE`, y decidir si compensa materializar `_key` en silver (o una tabla
    de stage) para convertir el `ON` en igualdad de columna.
  - **Preguntas guía:** ¿el `MERGE` copia toda la tabla? ¿cuánto pesa frente al
    rebuild completo? ¿la poda por estadísticas es posible sin `_key`? ¿qué
    rompe una columna interna en `companies_*`, oro y exports?
  - **Hecho cuando:** la decisión (materializar o no) está escrita con la
    medición que la sostiene. La decisión **preliminar** se toma con `explain`
    local y el plan; la **confirmación** con las `operationMetrics` del run B
    (T-08). Si se materializa `_key`, se implementa con contrato, proyecciones
    explícitas, `RESTORE` y tests, y se valida con el **run de reserva**; si no,
    queda documentado el motivo y el coste aceptado. **Aprobación previa** del
    diff y de la migración.
  - **Evidencia:** `operationMetrics` del MERGE (paso 14 de `plan.md` §2.2),
    comparación de tiempos y fila en `evidence.md`. **0 €** en esta tarea; las
    métricas se leen en T-08.

- [ ] **T-05 — Layout físico: medir y decidir** (~30 min)
  - **Objetivo:** medir `numFiles`, `sizeInBytes` y tamaño medio por tabla
    (bronce, silver, oro) y decidir `OPTIMIZE`, `liquid clustering` o **no
    cambiar**, con el efecto en oro y exports si se toca.
  - **Hecho cuando:** la decisión está escrita con la tabla de ficheros y el
    motivo (incluido el «no cambiar»); si se aplica `OPTIMIZE`, su coste y su
    efecto están medidos y el cambio va **con aprobación previa** (toca datos
    físicos y ficheros).
  - **Evidencia:** `DESCRIBE DETAIL` por tabla (leído en el clúster del job, sin
    warehouse) y fila en `evidence.md`. **0 €** de runs; el clúster de lectura,
    si se abre uno, cuesta céntimos.

- [ ] **T-06 — Compute y concurrencia: decisión** (~30 min)
  - **Objetivo:** decidir el compute de producción con la evidencia de T-01 y
    T-07/T-08: `D4ds_v4` single node, `D8ds_v4`, multi-nodo o serverless;
    paralelo o secuencial; la **config definitiva de particiones** (`.yml` del
    clúster o código, con el valor que haya ganado en T-03) y el
    `timeout_seconds` (sin subirlo como solución).
  - **Hecho cuando:** la tabla de opciones de `plan.md` §7.2 tiene una decisión
    con medida, coste por run y motivo de descarte del resto; si cambia el
    `.yml`, diff aprobado y `bundle validate` en verde (el validate y los
    deploys los ejecuta la persona, según `AGENTS.md`).
  - **Evidencia:** tiempos y spill por configuración, coste estimado y fila en
    `evidence.md`. **0 €** (sin runs extra; la reserva exige aprobación).

## 3. Validación y producción

- [ ] **T-07 — Run A (reconstrucción completa) en dev** (~20 min)
  - **Hecho cuando:** 8/8 tareas en verde, silvers ≤ **3 m 46 s** (A2), run
    completo ≤ **22 m 30 s**, exports escritos con `meta.json` válido y SHA-256
    de los 12 Parquet registrado, y `timings_s`/`merge_metrics` en la salida.
  - **Ejecución (run 1 de 3):** `databricks bundle deploy -t dev --profile vs_code`
    (gratis) y `databricks bundle run elt -t dev --profile vs_code`; los ejecuta
    la persona. Sin SQL.
  - **Evidencia:** id del run, tabla de tiempos por tarea y por paso, SHA-256,
    coste en `docs/costs.md` y fila en `evidence.md`.

- [ ] **T-08 — Run B (incremental + repetición) en dev: cierra T-10 de la 008** (~25 min)
  - **Hecho cuando:** el run cierra 8/8 con `backfill_from`/`backfill_to` y
    `repeat_window=true`; **el MERGE escribe** (métricas de filas insertadas/
    actualizadas/borradas); los exports son **idénticos byte a byte** a los de
    A; la repetición deja el mismo contenido; y el tiempo cumple el criterio de
    `plan.md` §3.
  - **Ejecución (run 2 de 3):** `bundle run elt -t dev --profile vs_code
    --params backfill_from=...,backfill_to=...,repeat_window=true`; lo ejecuta la
    persona. **Antes de lanzar B**, copiar el SHA-256 de los 12 Parquet y los
    `meta.json` de los exports de A: B los sobrescribe y sin esa captura la
    comparación byte a byte se pierde (pasó el 2026-10-28). La ventana exacta se
    anota en el resumen.
  - **Evidencia:** id del run, `timings_s` por paso, `merge_metrics`, SHA-256
    A/B, conteos, coste y fila en `evidence.md`.

- [ ] **T-09 — Herencia de la 008: producción (cierra T-11)** (~30 min)
  - **Hecho cuando:** tras cerrar T-06, `bundle deploy -t prod` con rollback de
    bundle y `RESTORE` de las silvers disponible; el **primer run programado**
    reconstruye (migración) y cierra 8/8; el **domingo siguiente** el run es
    incremental real y el dashboard vuelve a publicar el lunes; SQL warehouse
    `STOPPED` antes y después; bitácora del roadmap actualizada.
  - **Nota:** no añade runs manuales (cadencia de la 009); los comandos de
    bundle los ejecuta la persona. Al cerrar, se actualizan las filas de T-10 y
    T-11 en `specs/008-Delta-Incremental/{tasks,evidence}.md`.
  - **Evidencia:** ids de deploy y runs, conteos, versiones de `RESTORE`,
    estado del warehouse y fila en `evidence.md`.

## 4. Cierre

- [ ] **T-10 — Cierre de la spec**
  - Nota `docs/databricks-de/notes/01-spark-performance.md` escrita con la
    plantilla del índice (qué se practicó, concepto, comandos, errores, dudas).
  - Calendario y bitácora de `docs/databricks-de/roadmap.md` al día; suites
    completas en verde; comprobación real registrada; `docs/costs.md` con todos
    los runs (dentro del techo de 1,8 €).
  - **Hecho cuando:** merge a `main` y tag (`spec-010`) solo con RF-2/RF-3/RF-4
    verificados en dev y RF-8 cerrado.
