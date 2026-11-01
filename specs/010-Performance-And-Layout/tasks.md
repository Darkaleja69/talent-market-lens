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
> **Ampliación (2026-10-31): 2 runs más** para T-07/T-08 (total 5; ~1,08–1,10 €,
> techo 1,8 €; 0 SQL).

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

- [x] **T-03 — Camino incremental: pasadas, caché y codegen** (~90 min)
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
  - **Sub-bloque T-03b (2026-10-30): aplicado y medido en local; verificado.**
    Correcciones aprobadas por la persona el 2026-10-29:
    - **C1 — 7 pasos cronometrados** en vez de 11 acciones: `changed.isEmpty()` +
      `count` → un `count`; los dos guards y el conteo de `recompute` → **una**
      agregación (`recompute_check`: filas, `_ingest_date` no nulo y
      `countDistinct` de la clave, con los **mismos** mensajes de error);
      `merge_src.count()` desaparece (`merge_rows = recomputadas + marcadores`);
      `deleted_groups = markers_count`.
    - **C2 — corte de linaje**: `localCheckpoint(eager=True)` explícito en
      `keyed` y `recompute`. **No reduce consultas** (Spark paga la
      materialización con su propia ejecución): el pase queda en **9 ejecuciones
      de datos + 1 de metadatos** con el MERGE de Delta. Lo que compra es el
      plan del MERGE: paso `merge` de referencia **12,4 s → ~1,5 s**.
    - **C3 — un escaneo del destino**: `target` cacheado una vez y liberado con
      `unpersist(blocking=True)` **antes** del MERGE, para que `target_count` no
      lea el snapshot pre-MERGE.
    - **C4 — `merge_metrics` cronometrado** como paso propio.
    - **Banco local (`after` frente a `before`)**: wide **98,2 / 88,3 → 53–56 s**;
      small **68,9 / 65,6 → 30–36 s**; el control `full` (código sin cambios)
      también bajó (56,9 → 31–45 s), así que **la ganancia absoluta no es
      atribuible entera** a T-03b; la diferencial sí: `merge_source_count` +
      `merge` = **16,2 s → 2,1 s**. Ratios: wide/full ~1,5–1,8 (RF-2a no se
      demuestra en local), small/full **~1,0–1,2** (RF-2b sí).
    - **Verificación independiente: PASA** (dos rondas; corregidos el `eager`
      por defecto y los docstrings) y **PASA** en el cierre. 37 tests del
      fichero en verde.
    - **Pendiente:** el run instrumentado (3.º y último del presupuesto) decide
      RF-2a en clúster; `_key` materializada (T-04) y el corte del codegen
      (T-03a) quedan como palancas con evidencia.
  - **Cierre (2026-10-30): T-03 CERRADA con RF-2a cumplido.** Run
    `879269828668729` (SUCCESS 8/8, **927 s**, 15 m 27 s) con la ventana
    2026-10-24…2026-10-28 y `repeat_window=true`:
    - **Pases por silver: 123,8–138,2 s** (frío) y **89,4–112,3 s** (caliente),
      frente a **249–318 s** y **173–249 s** del mismo run antes de T-03b:
      **2,0–2,6×** menos. El criterio `T_inc ≤ 226 s` se cumple con **39 %** de
      margen en frío y **50–60 %** en caliente; contra el rebuild de A
      (117–169 s de módulo, 167–204 s de tarea) el pase frío queda dentro de la
      banda y el caliente por debajo, con 55–458 filas en vez de 31k.
    - **RF-3 cumplido**: 927 s ≤ 1350 s (31 % de margen). Descomposición:
      bronce **383 s de arranque del clúster + 134 s de ejecución = 56 % del
      run**, silvers 255–295 s (dos pases + `companies_*`), oro 56 s y exports
      92 s. **El incremental ya no es el cuello del run**: el coste dominante es
      arrancar el clúster y el bronce (T-06).
    - **Integridad**: `target_rows` idénticos a A (3.112 / 2.527 / 12.543 /
      10.072), `deleted_groups=0`, `inserted=0`, repetición idempotente.
      `config` confirma **AQE activo** y 8 particiones (cierra el
      `[NO VERIFICADO]` de T-01).
    - **Lo que queda por paso** (frío, s): `recompute_check` **26,1–50,4** (la
      cadena de `build_offers` para **55 filas**: es compilación/codegen, no
      datos), `merge` 19,2–28,9, `changed_count` (keyed del bronce)
      11,2–25,1, `affected_count` 6,5–11,5, `window` 5,7–6,3 y el resto 9,7–18,2.
    - **Fallo de proceso (mío):** los SHA-256 de los exports no se capturaron
      antes del run; la equivalencia queda por conteos, `meta.json` y la
      idempotencia del segundo pase. Mitigación propuesta: que los exports
      escriban el `sha256` de cada fichero en su `meta.json` (spec nueva o
      T-10), y anotarlo en la lista de T-08.
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

- [x] **T-04 — MERGE real: medir y decidir** (~45 min)
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
    comparación de tiempos y fila en `evidence.md`. **0 €** en esta tarea: las
    métricas son del resumen del run `879269828668729` de T-03, leído por la API
    de runs, y **T-08 confirmará** la decisión con el run B.
  - **Estado (2026-10-30): CERRADA — decisión «no materializar `_key`»**, con la
    medición por delante y el plan local `[NO VERIFICADO]`. Detalle en
    `design-performance.md` §9. **Evidencia** (run `879269828668729` de T-03,
    cuatro silvers × dos pases = **8 `MERGE` reales**): `numTargetRowsCopied = 0`
    en las ocho pasadas, `numTargetFilesAdded = 1` por pase y
    `numTargetBytesRemoved` del pase 2 = `numTargetBytesAdded` del pase 1
    (453.383 / 66.087 / 656.076 / 41.133 B) ⇒ **el `MERGE` no reescribe la tabla
    entera**: escribe un fichero con las 55–458 filas afectadas y el segundo
    pase es consistente con retirar el fichero del primero (la ruta no se mide).
    `executionTimeMs` 5,9–23,3 s, `scanTimeMs` 2,1–6,8 s,
    `materializeSourceTimeMs` 0,7–3,4 s, `rewriteTimeMs` 2,9–13,0 s; paso
    `merge` **10,5–28,9 s** por pase (**19,1–28,9 s** en frío y **10,5–21,4 s**
    en el segundo), **entre los tres pasos más caros de cada pase**: primero en
    `offers_infojobs` (los dos pases), segundo en `offers_indeed` y en el segundo
    pase de `offers_multi_site`, y tercero en `offers_linkedin` (los dos pases)
    y en el primer pase de `offers_multi_site`. `inserted = deleted = 0`,
    `updated = affected` (55–458), `target_rows` idénticos a A y
    `deleted_groups = 0`: idempotente **en conteos**; el contenido de los
    exports se cierra en **T-09** (decisión del 2026-10-31, RF-4 por
    contenido). El pase 1 deja **1 deletion vector** por
    tabla; el pase 2 no añade ninguno (`delta.enableDeletionVectors` activa en
    las tablas del catálogo dev medidas; salida literal en
    `docs/media/performance/2026-10-30-describe-detail-dev.txt`). **H3**: la
    parte «reescribe el destino entero» queda descartada; queda el coste del
    `ON` con expresión, sin plan que lo explique. **Coste aceptado:**
    10,5–28,9 s por pase; alternativas descartadas con motivo (columna `_key`
    materializada: no arregla un problema que no existe, su techo es el paso
    `merge` completo y toca contrato/RESTORE; tabla de stage: añade una escritura
    por pase y su limpieza; partir la clave: cambia el modelo de dedup).
    **`[NO VERIFICADO]` el plan interno del `MERGE`**: el
    `explain` local arranca con Spark 4.2.0 y Delta desde JARs locales, pero
    este host Windows no tiene `winutils.exe`/`hadoop.dll` y falla en
    `listStatus` → `NativeIO$Windows.access0` (`UnsatisfiedLinkError`); **no se
    instaló nada**. El plan interno del `MERGE` **no** es accesible sin
    ejecutarlo (`EXPLAIN MERGE` solo devuelve el comando). El recuento de DVs y
    el proxy del join del `ON` son **`[NO VERIFICADO]`** y **no medibles**: el
    recuento de DVs no es accesible (la ruta la gestiona Unity Catalog y
    `dbutils.fs.ls` la rechaza con `INVALID_PARAMETER_VALUE`) y el proxy del
    plan degenera en `HashAggregate + Exchange SinglePartition` sobre
    `LocalTableScan <empty>`; las celdas se retiraron el 2026-10-31 para no
    añadir trabajo al run. Las
    `operationMetrics` del `DESCRIBE HISTORY` son la evidencia principal; la
    poda por fichero y si el destino se escanea completo siguen sin medirse.
    **0 €** (sin run ni sesión SQL; solo lectura gratuita de la API de runs).
    **T-08 confirmará** la decisión con el run B.

- [x] **T-05 — Layout físico: medir y decidir** (~30 min)
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
  - **Estado (2026-10-30): CERRADA — «no cambiar»**, sin `OPTIMIZE`, sin
    `liquid clustering` y sin particionado. Detalle en
    `design-layout-compute.md`. **Evidencia** (`DESCRIBE DETAIL` de las **18
    tablas** del catálogo `job_offers_dev` en un clúster all-purpose de lectura,
    salida literal en `docs/media/performance/2026-10-30-describe-detail-dev.txt`;
    filas = `numRecords` de Delta, no un `COUNT(*)` independiente): **42
    ficheros / 98.369.519 B** en total, con **bronce 18 ficheros**
    (`infojobs` **5 × 0,43 MB** = 2,14 MB, la media por fichero más baja de
    bronce; `indeed` 1, `linkedin` 6, `multi_site` 6), `offers_*` **2 ficheros**
    cada una (0,76–7,96 MB de media), `companies_*` **1** (12,3–236,7 kB) y oro
    **1 fichero** por tabla salvo `dim_skill_list` y `dim_currency` con **4
    diminutos** (1,55/2,33 kB de media; 6,2/9,3 kB en total). Rango de filas:
    **19** (`dim_currency`) a **44.928** (`fact_offer_skills`).
    **`partitionColumns` y `clusteringColumns` = `[]` en las 18** y
    **`delta.enableDeletionVectors` activa en las 18** (propiedad de tabla; no
    dice cuántos DVs hay). **Se descarta `OPTIMIZE`** porque `fact_offers` ya es
    **1 fichero** (26.302 filas / 19,15 MB), en las tablas de 2 ficheros
    compactar **reescribe 1,5–15,9 MB** para bajar de 2 a 1 y en las dos
    dimensiones compactaría **6,2/9,3 kB** (ganancia no medible); además
    `OPTIMIZE` reescribe solo los ficheros que compacta (con **1 fichero no
    reescribe nada**: solo actúa donde hay ≥2) y se supone que, con DVs activos,
    materializa los borrados (`[NO VERIFICADO]`).
    **Se descarta `liquid clustering`** porque ninguna consulta de oro o de los
    exports filtra por una columna que pueda podarse (leen la tabla completa),
    el paso `window` cuesta 5,7–6,3 s y clusterizar no podaría nada con **1
    fichero de 19,15 MB** (habría que partirlo en varios para aprovechar la
    poda, justo lo que `OPTIMIZE` evita); cada `MERGE` pagaría el reparto por la
    clave. **Se descarta particionar** porque ninguna tabla supera 44.928 filas
    ni 33,09 MB y no hay filtro dominante por fecha en oro/exports (particionar
    por `_ingest_date` crearía una partición por lote). **DVs**: el pase 1 deja
    **1** y el segundo pase es **consistente con** reescribir el fichero del
    primero (T-04; la ruta no se mide), así que no se acumulan más allá de un
    pase; `DESCRIBE DETAIL` no informa del número vigente, que queda
    **`[NO VERIFICADO]`** y **no medible**: el recuento de DVs no es accesible
    (la ruta la gestiona Unity Catalog y `dbutils.fs.ls` la rechaza con
    `INVALID_PARAMETER_VALUE`) y el proxy del plan degenera en
    `HashAggregate + Exchange SinglePartition` sobre `LocalTableScan <empty>`;
    las celdas se retiraron el 2026-10-31 para no añadir trabajo al run. Por
    eso no se justifica `OPTIMIZE` solo por
    purgarlos.
    **Los `MERGE` dejan el recuento estacionario en el run de dos pases**
    (`filesAdded=1`, `filesRemoved=0/1`): los **2 ficheros** de cada `offers_*`
    son **consistentes con** base + el fichero del último pase (la ruta no se
    mide). **Efecto en oro y exports: ninguno**, **un fichero por tabla** en
    `pbi_export_dev` (7 ficheros / 34.241.722 B) y `web_export_dev` (5 /
    3.689.553 B), que se escriben desde la tabla lógica y no copiando ficheros
    Delta. **Revisión** si una tabla pasa de **50 ficheros** (o de **10** en una
    `offers_*`), si la media por fichero baja de **1 MB** en una tabla de más de
    **10 MB**, si aparece un **filtro selectivo dominante** (≥90 % de filas
    descartadas) en oro/exports, si el volumen crece **≥10×** o si se ven
    **decenas de DVs** vigentes. **`[NO VERIFICADO]`**: número de DVs vigentes;
    **no medible**: el recuento de DVs no es accesible (la ruta la gestiona
    Unity Catalog y `dbutils.fs.ls` la rechaza con `INVALID_PARAMETER_VALUE`) y
    el proxy del plan degenera en `HashAggregate + Exchange SinglePartition`
    sobre `LocalTableScan <empty>`; las celdas se retiraron el 2026-10-31 para
    no añadir trabajo al run,
    `minFileBytes`/`maxFileBytes` (`None` en la salida), tiempos por tabla de oro
    y exports (solo el de tarea: oro 56 s, exports 92 s), la causa de los 4
    ficheros de las dos dimensiones y el efecto real de un `OPTIMIZE` (no
    ejecutado: escritura física con aprobación previa). **Coste: céntimos**
    (clúster all-purpose ~2–3 min, duración exacta no registrada). **T-06**
    añadirá la sección de compute al mismo documento.

- [x] **T-06 — Compute y concurrencia: decisión** (~30 min)
  - **Objetivo:** decidir el compute de producción con la evidencia de T-01 y
    T-07/T-08: `D4ds_v4` single node, `D8ds_v4`, multi-nodo o serverless;
    paralelo o secuencial; la **config definitiva de particiones** (`.yml` del
    clúster o código, con el valor que haya ganado en T-03) y el
    `timeout_seconds` (sin subirlo como solución).
  - **Hecho cuando:** la tabla de opciones de `plan.md` §7.2 tiene una decisión
    con medida, coste por run y motivo de descarte del resto; si cambia el
    `.yml`, diff aprobado y `bundle validate` en verde (el validate y los
    deploys los ejecuta la persona, según `AGENTS.md`).
  - **Estado (2026-10-30): CERRADA — se mantiene la configuración actual**, sin
    tocar ningún `.yml`. **Decisión**: `Standard_D4ds_v4` **single node**
    (4 vCPU / 16 GiB), `CLASSIC_PREVIEW`, `17.3.x-scala2.13`,
    `runtime_engine: STANDARD` (Photon **fuera**), `PERFORMANCE_OPTIMIZED`,
    `max_concurrent_runs: 1` y **cuatro silvers en paralelo** tras el bronce.
    **Particiones**: `spark.sql.shuffle.partitions: "8"` en el job cluster (ya
    estaba, commit `61da8c5`) y AQE con el valor por defecto; la config efectiva
    del run es
    `{"shuffle_partitions":"8","adaptive_enabled":"true","overrides":{"shuffle_partitions":"8"}}`.
    **`timeout_seconds: 1800`** a nivel de job **se queda y no se sube**
    (prohibido por la spec); las tareas siguen con `timeout_seconds: 0` porque
    el tope del job acota el run. **No se ha tocado ningún
    `.yml`**, así que **no hay `bundle validate` ni aprobación
    previa pendiente**: la única clave de compute del `.yml`
    (`shuffle_partitions`) se aprobó y aplicó en T-03.
    **Evidencia** (run `879269828668729`, 2026-10-30, leído por la API de runs,
    sin run nuevo ni SQL): **927,1 s** (15 m 27 s), 8/8 en verde ⇒ **RF-3
    cumplido** (927 s ≤ 1350 s: **31,3 % de margen sobre el gate** y **48,5 %
    sobre los 1800 s** de producción). **Camino crítico** (ventanas de tarea,
    relativas al inicio): bronce **517,5 s** (**383 s de `setup_duration` +
    134 s de ejecución**), silvers en paralelo **298,5 s** de ventana (máximo de
    ejecución **295 s**; linkedin 295, indeed 290, multi_site 288, infojobs 255),
    oro **58,7 s** de ventana (56 s de ejecución) y exports en paralelo
    **49,8 s** de ventana (47 s de ejecución). **Aritmética reproducible**:
    517,5 + 298,5 + 58,7 + 49,8 = **924,5 s**; + **1,3 s** de huecos entre tareas
    = **925,8 s** de la primera a la última; + **1,3 s** de arranque/cierre =
    **927,1 s** medidos. **Coste: 0,17–0,23 € por run** según duración
    (808–974 s); la referencia de **~0,24 €/run** de `plan.md` §7.2 corresponde a
    15–19 min y es solo orden de magnitud. **El arranque es el 41 % del reloj**
    (383 s) y se **supone** independiente del tamaño del nodo
    (`[NO VERIFICADO]`). **Descartadas**: `D2ds_v6`
    (0,13 €; en la 009 **no completó**: `DRIVER_NOT_RESPONDING` a los 18,2 min,
    run `954013261343017`; que con la mitad de núcleos sea más lento es
    **inferencia**, no medida), `D8ds_v4` (0,48 €; **estimación** de ahorro
    ≤200 s —398 s paralelizables de 927— por 2× coste, sin medición y con los
    2 runs nuevos asignados a T-07/T-08 (decisión del 2026-10-31)), multi-nodo
    (mismo argumento y más superficie) y
    serverless (DBU sin medir; spike pendiente). **Revisión**: en **T-07/T-08**
    (runs A y B en dev) con los mismos criterios; la revisión en T-07/T-08
    **exige presupuesto aprobado**: a 2026-10-31 están aprobados **2 runs más**
    (total 5) para esa validación, y se reabre si el run pasa de **1350 s**, si
    un pase de silver pasa de **226 s** o si la Spark UI muestra
    **spill/memoria**.
    **`[NO VERIFICADO]`**: tiempos de `D8ds_v4`/multi-nodo/serverless en este
    repo, DBU de serverless, estabilidad de los 383 s de arranque (medidos **una
    vez**, un run), independencia del arranque respecto al tamaño del nodo (un
    solo run, con `D4ds_v4`), spill del run y factura por run. Detalle en
    `design-layout-compute.md` §9.
  - **Evidencia:** camino crítico, `config` y coste del run `879269828668729`
    (API de runs), `timings_s`/`merge_metrics` del resumen y tabla de opciones de
    `plan.md` §7.2; fila en `evidence.md`. **0 €** (sin runs extra; la reserva
    exige aprobación).

- [ ] **T-06b — `sha256` de los exports dentro de `meta.json`** (~45 min)
  - **Objetivo:** que la equivalencia de contenido de RF-4 deje de depender de copiar ficheros a
    mano: `Prepare_PBI_Export` y `Prepare_Web_Export` añaden a su `meta.json` la lista de ficheros
    publicados con `{name, bytes, sha256}`.
  - **Motivo (fallo de proceso del 2026-10-30):** en el run `879269828668729` no se capturaron
    los SHA-256 de los exports antes de que el run los sobrescribiera (`gold/pbi_export_dev`,
    `gold/web_export_dev`) y la equivalencia de RF-4 quedó sostenida solo por conteos,
    `meta.json` e idempotencia. Con el hash dentro del propio `meta.json`, cada run deja la huella
    del anterior sin intervención humana.
  - **Por qué es barato y seguro:** el hash se calcula sobre los ficheros ya publicados: añade una
    lectura local por Parquet publicado para calcular el hash (34 MB en PBI, 4 MB en web), sin
    tocar tablas ni el warehouse; el cambio es **aditivo** en el contrato (Power BI lee las claves
    que ya existen y las nuevas `previous_files`/`previous_generated_at` no cambian la semántica de
    ninguna otra) y el `meta.json` no se hashea a sí mismo (evita recursión).
  - **Hecho cuando:** los dos `meta.json` incluyen `files: [{name, bytes, sha256}]` y, desde la
    segunda ejecución, `previous_files` (la lista `files` del run anterior, copiada una sola vez,
    sin anidar ni acumular) y `previous_generated_at` (su `generated_at`); `meta.json` no aparece
    en ninguna de las dos listas y las claves previas conservan su valor. Los tests de
    `test_web_export.py` y `test_pbi_export.py` comprueban, contra los ficheros escritos en disco,
    que hay una entrada por tabla (con `name` `<tabla>.parquet`), que `bytes` y `sha256`
    corresponden a los bytes reales, que la primera ejecución no deja claves `previous_*`, que la
    segunda copia exactamente el `files`/`generated_at` de la primera y que la tercera copia los de
    la segunda (sin anidar); **aprobación previa** de los dos módulos (diff y motivo), porque tocan
    el contrato de los exports.
  - **Verificación real:** el primer run que publique exports lo deja escrito; se comprueba en
    **T-07/T-08** (dev) y queda confirmado en T-09 (producción programada, coste ya en la cadencia
    de la 009). `[NO VERIFICADO]` hasta entonces.
  - **Evidencia:** salida de los tests, `meta.json` con los hashes y fila en `evidence.md`.
    **0 €.**

## 3. Validación y producción

- [x] **T-07 — Run A (reconstrucción completa) en dev** (~20 min)
  - **Hecho cuando:** 8/8 tareas en verde, silvers ≤ **3 m 46 s** (A2), run
    completo ≤ **22 m 30 s**, exports escritos con `meta.json` válido y los
    SHA-256 de los Parquet **registrados en `meta.json`**; en A se leen de
    `previous_files` del `meta.json` de B (T-08, T-06b), y
    `timings_s`/`merge_metrics` en la salida.
  - **Ejecución (run 4 de 5):** `databricks bundle deploy -t dev --profile vs_code`
    (gratis) y `databricks bundle run elt -t dev --profile vs_code
    --params force_full=true` (reconstrucción completa); los ejecuta la persona.
    Sin SQL.
  - **Evidencia:** id del run, tabla de tiempos por tarea y por paso, el
    `meta.json` de cada export con `files` (nombre, bytes y SHA-256 de cada
    Parquet) leído del `notebook_output` (`dbutils.notebook.exit`) de las dos
    tareas de export, coste en `docs/costs.md` y fila en `evidence.md`.
    **No medibles** (celdas retiradas el 2026-10-31): el recuento de DVs
    vigentes y el plan del `MERGE`; motivos en T-04/T-05.
  - **Estado (2026-10-31): CERRADA** — run **`221895134926434`** (job
    `320947022571947`, perfil `vs_code`, `force_full=true`), **SUCCESS 8/8**,
    **816,0 s (13 m 36 s)** según la API de runs. **RF-3**: 816 s ≤ 1350 s con
    **39,6 % de margen** (y **54,7 %** sobre los 1800 s del `timeout_seconds`).
    **Umbral de 226 s**: los silvers de módulo (`elapsed_s`) quedan en
    **114,5–182,0 s**, con holgura, pero como **sanity check del rebuild**;
    **RF-2a es el criterio del incremental con ventana ancha** y se cerró en
    T-03.
    **Tiempos por tarea** (s, inicio → fin relativo al run; setup / ejecución):
    bronce 0,0 → 495,5 (**382,0** / 113,0), indeed 495,8 → 683,0 (2,0 / 185,0),
    infojobs 495,8 → 677,7 (1,0 / 180,0), linkedin 495,9 → 711,0 (1,0 / 214,0),
    multi_site 496,0 → 671,1 (1,0 / 173,0), oro 711,2 → 771,1 (1,0 / 58,0),
    `Build_Report_Export` 771,4 → 815,4 (1,0 / 42,0) y
    `Build_for_Web-Dashboard` 771,5 → 809,8 (1,0 / 37,0). **Resumen de los
    cuatro silvers** (`mode: full`, `mode_reason: force_full`, `written: true`,
    `merge_metrics: null` porque no hay `MERGE`): `target_rows`
    **3.213 / 2.558 / 13.063 / 10.250** (indeed / infojobs / linkedin /
    multi_site), `elapsed_s` **147,9 / 124,8 / 182,0 / 114,5**, `full_write`
    **86,9 / 84,5 / 156,0 / 79,0** s y `target_count` **36,1 / 18,9 / 2,1 /
    11,5** s; `window` **8,8–9,3 s**; `config`
    `{"shuffle_partitions":"8","adaptive_enabled":"true","overrides":{"shuffle_partitions":"8"}}`.
    **El arranque del clúster (382 s) es el 46,8 % del run** y coincide con los
    383 s de T-06: segunda medida con `D4ds_v4`, así que la estabilidad del
    arranque ya no descansa en un solo run (ambos del mismo tamaño de nodo). Los
    dos exports quedaron escritos con `meta.json` (42 s y 37 s).
    **`[NO VERIFICADO]` los SHA-256 de los exports de A**: no se capturaron en
    este run porque el `dbutils.notebook.exit` que los publica se desplegó
    después; se recuperan en **T-08** desde `previous_files` del `meta.json` de
    B (T-06b), no es fallo de esta tarea. **T-06b sigue abierta**: su
    verificación real (que A escribiera `files` en `meta.json` con el código
    desplegado) se confirma en T-08; si A no lo tuviera, `previous_files` vendrá
    vacío y habrá que capturar los hashes de otra forma antes de comparar.
    El recuento de DVs y el proxy del plan del `MERGE` quedan
    **`[NO VERIFICADO]`** y son **no medibles**: el recuento de DVs no es
    accesible (la ruta la gestiona Unity Catalog y `dbutils.fs.ls` la rechaza
    con `INVALID_PARAMETER_VALUE`) y el proxy del plan degenera en
    `HashAggregate + Exchange SinglePartition` sobre `LocalTableScan <empty>`;
    las celdas se retiraron el 2026-10-31 para no añadir trabajo al run.
    **Coste ~0,20 €** (anotado en
    `docs/costs.md`). Verificación independiente pendiente.

- [ ] **T-08 — Run B (incremental + repetición) en dev: cierra T-10 de la 008** (~25 min)
  - **Hecho cuando:** el run cierra 8/8 con `backfill_from`/`backfill_to` y
    `repeat_window=true`; **el `MERGE` escribe** (métricas de filas insertadas/
    actualizadas/borradas distintas de cero); la repetición deja el mismo
    contenido y el tiempo cumple el criterio de `plan.md` §3. La **equivalencia
    de contenido de los exports se valida en T-09**.
  - **Ejecución (run 5 de 5):** `bundle run elt -t dev --profile vs_code
    --params backfill_from=...,backfill_to=...,repeat_window=true`; lo ejecuta la
    persona. **Ya no hace falta copiar los hashes**: el `meta.json` de B deja
    los de A en `previous_files` (T-06b). Se comparan `files` de A
    (`previous_files` de B) con `files` de B. La ventana exacta se anota en el
    resumen.
  - **Evidencia:** id del run, `timings_s` por paso, `merge_metrics` (evidencia
    principal del `MERGE`), `files` de A (`previous_files` de B) frente a
    `files` de B leídos del `notebook_output` (`dbutils.notebook.exit`) de las
    dos tareas de export, conteos, coste y fila en `evidence.md`.
    **No medibles** (celdas retiradas el 2026-10-31): el recuento de DVs
    vigentes y el plan del `MERGE`; motivos en T-04/T-05.
  - **Estado (2026-10-31): run B ejecutado; RF-4 se valida en T-09, no aquí.**
    Run B **`549237053478569`**. La comparación de `files` de B con
    `previous_files` (run A `221895134926434`) **no deja ningún fichero con el
    mismo hash**: las dimensiones tienen **idéntico tamaño y hash distinto**
    (`dim_calendar` 31.107 B, `dim_currency` 2.745 B, `dim_companies` 483.093 B
    y `dim_skill_list` 2.412 B); `fact_offer_skills` también cambia de hash
    (**+5 B** en web y PBI); y `fact_offers` crece **+19.342 B** (web) y
    **+22.459 B** (PBI), un crecimiento cuya causa es una **hipótesis
    `[NO VERIFICADO]`** (el orden de filas no lo explica y no se ha atribuido).
    Las dimensiones no dependen de las filas que toca el `MERGE`, así que su
    diferencia mide el **escritor** (el orden de filas de `coalesce(1)` no es
    determinista), no la lógica incremental: por eso el criterio pasa a
    **equivalencia de contenido** y la escritura se ordena de forma total por
    todas las columnas. La escritura ordenada se implementa después de este run
    y **no se repite el A/B en dev** (no hay run nuevo autorizado): **RF-4 se
    valida en T-09** con **dos runs manuales de producción sobre el mismo
    bronce** (A `full` y B incremental), no con dos domingos programados.

- [x] **T-09 — Herencia de la 008: producción (cierra T-11)** (~30 min)
  - **Hecho cuando:** tras cerrar T-06, `bundle deploy -t prod` con rollback de
    bundle y `RESTORE` de las silvers disponible; **dos runs manuales de
    validación en producción, con el mismo bronce** (A `force_full=true` y B
    incremental con `backfill_from`/`backfill_to` y `repeat_window=true`),
    cierran 8/8; se comparan los `sha256` de `files` de B con `previous_files`
    (A) —hash igual implica además los mismos bytes— y los **conteos** con los
    `tables` del `notebook_output` de cada run; el dashboard se publica con
    `workflow_dispatch` tras B y el cron del lunes lo republica; SQL warehouse
    `STOPPED` antes y después; bitácora del roadmap actualizada.
  - **Desviación aprobada el 2026-10-31:** se sustituye el A/B de dos domingos
    programados por **A/B manual el mismo día**, porque RF-4 exige el **mismo
    bronce** y el run programado del domingo ingiere un lote nuevo. El run
    programado de ese domingo se **pausa** (`pause_status: PAUSED`) y se
    reanuda el lunes siguiente; no es gate de esta tarea. Coste: 2 runs de
    producción (~0,20–0,23 € cada uno) y 1 run programado menos esa semana;
    el gasto va al presupuesto de producción de la 009, no al de dev (5/5
    consumidos).
  - **Nota:** los comandos de bundle los ejecuta la persona. Al cerrar, se
    actualizan las filas de T-10 y T-11 en
    `specs/008-Delta-Incremental/{tasks,evidence}.md`.
  - **Evidencia:** ids de deploy y runs, conteos, hashes, estado del warehouse,
    id del workflow del dashboard y fila en `evidence.md`.
  - **Estado (2026-10-31): CERRADA CON DESVIACIÓN APROBADA.** Deploy `-t prod`
    con `pause_status: PAUSED` y
    dos runs manuales: **A `321262594455396`** (SUCCESS 8/8, **915 s**, silvers
    `full` 149,8–190,7 s, exports con `files`) y **B `317888374535056`**
    (SUCCESS 8/8, **747 s**, incremental con `backfill_from=2026-10-18`,
    `backfill_to=2026-10-31`, `repeat_window=true`; `MERGE` con `updated`
    316/850/1.788/728, `inserted=deleted=0`, `target_rows` idénticos a A).
    **RF-3 y la parte de `MERGE`/idempotencia se cumplen**; T-06b también
    (`previous_files` de B = `files` de A).
    **RF-4 no se cumple:** los `sha256` de fichero no son estables (las
    dimensiones tienen el mismo tamaño y hash distinto) y el contenido difiere
    en una fila (`fact_offer_skills` 46.375→46.374; Indeed 2.768→2.767,
    glassdoor 6.368→6.369). **No hubo lote nuevo**: el landing y el bronce no
    cambiaron entre A y B. La causa es un empate no determinista en
    `_keep_best` de `gold_build.ipynb`; corregido con
    `Prepare_Gold.keep_best` y desempate `source_scraper`/`job_id` asc, con
    test local (**22 passed, 1 skipped**). **Decisión de la persona
    (2026-10-31): no se repite el A/B**; el fix se despliega y el run
    programado del 8-nov lo usará, pero **RF-4 queda `[NO VERIFICADO]` en
    producción**. Dashboard publicado con `workflow_dispatch` (confirmado por
    la persona; id no capturado) y SQL warehouse `STOPPED` (Serverless Starter
    Warehouse, 0 clústeres).

## 4. Cierre

- [ ] **T-10 — Cierre de la spec**
  - Nota `docs/databricks-de/notes/01-spark-performance.md` escrita con la
    plantilla del índice (qué se practicó, concepto, comandos, errores, dudas).
  - Calendario y bitácora de `docs/databricks-de/roadmap.md` al día; suites
    completas en verde; comprobación real registrada; `docs/costs.md` con todos
    los runs (dentro del techo de 1,8 €).
  - **Desviación de RF-4 (2026-10-31):** el A/B de producción no se repite.
    El fix del empate está probado en local; RF-4 queda `[NO VERIFICADO]` en
    producción. El cierre de T-10 requiere la aceptación explícita de la
    persona y se registra así en `evidence.md`.
  - **Hecho cuando:** merge a `main` y tag (`spec-010`) con **RF-2 y RF-3
    verificados en dev**; **RF-4 cerrado con la desviación aprobada**; y RF-8
    cerrado.
