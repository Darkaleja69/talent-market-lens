# Tareas: bronce deduplicado e ingesta incremental

> Detalle del **bloque A**, afinado al arrancar la 008 (2026-10-21) y
> **actualizado el 2026-10-24** con la herencia de la 009 (coste y cadencia) y
> con el **bloque B planificado** (§2) mientras T-06 espera el run programado
> del domingo.
> Formato de trabajo según `AGENTS.md`: **la persona decide** el alcance y
> revisa; el asistente prepara las tareas, implementa el código y lo commitea
> tras la verificación independiente. Ninguna tarea se marca como hecha sin su
> comprobación.
>
> **Presupuesto (009, `plan.md` §17):** 3 runs de dev y 0 sesiones SQL para el
> **bloque A**. Reparto: T-04 (run corto one-off), T-05 (ciclo completo) y
> T-05 (repetición por idempotencia). Los 2 runs de T-03 son anteriores a la
> política. Prod se verifica con el run programado del domingo, no con un run
> manual. El **bloque B tiene presupuesto propio aprobado el 2026-10-24: 2 runs
> de dev y 0 sesiones SQL** (~0,48 €). Cada run se anota en `docs/costs.md` con
> motivo, id y coste estimado.

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
  - **Estado:** **one-off de producción hecha el 2026-10-24.** Deploy `-t prod`
    con el ingest nuevo y el job `dedupe_oneoff`; run `164560238285705` en verde,
    4/4 tareas, 9 min 11 s, ~0,11 €: 53.353 filas duplicadas eliminadas (indeed
    9.769→3.039, infojobs 45.171→2.617, linkedin 17.285→14.289, multi_site
    12.169→11.096), `RESTORE` a 19/20/60/50. **Pendiente la comprobación real:**
    el run programado del domingo y el dashboard del lunes.

## 2. Bloque B — Silvers incrementales

> Planificado el **2026-10-24** (aprovechando la espera del run de prod del
> domingo). Diseño en `plan.md` §10. Alcance decidido: **silver de ofertas
> incremental** por grupo semántico; `companies_*` y **oro completos**.
>
> **2026-10-28: bloque B bloqueado por rendimiento.** T-07–T-09 están cerradas
> (diseño, módulo y notebooks con tests en verde), pero T-10 no se puede
> validar: tres runs incrementales seguidos (`TIMEDOUT` a los ~30 min) sin
> escribir nada. El incremental cuesta ≥4× la reconstrucción completa y el
> `MERGE` real sigue sin medirse. **T-11 no se toca.** El trabajo de
> rendimiento pasa a la **spec 010**, junto con la repetición de la validación
> A/B; la decisión de descopear el bloque B (y cerrar la 008 con el bloque A)
> queda en manos de la persona.

- [x] **T-07 — Diseño del incremental** (~30 min)
  - **Objetivo:** fijar en `design-incremental.md` la unidad de incremental
    (grupo semántico `title|company_name|location_city`), el conjunto afectado
    (`keys_new` de bronce ∪ `keys_old` de silver por `job_id` cambiado), el
    watermark (`max(_ingest_date)` − `lookback_days`, `force_full` en la primera
    ejecución), la columna de control `_ingest_date` en silver, la escritura por
    MERGE (con `WITH SCHEMA EVOLUTION`), el encaje con oro y exports, el
    backfill por rango de fechas, y el plan de pruebas.
  - **Preguntas guía:** ¿qué pasa si una oferta cambia de ciudad o de título
    (migra de grupo)? ¿y si una fila de bronce se reescribe con `_ingest_date`
    más antigua que el watermark? ¿qué se hace con una ventana vacía? ¿cómo se
    detecta la primera ejecución? ¿qué columnas internas pueden ver oro y los
    exports? ¿cómo se demuestra equivalencia con la reconstrucción completa?
  - **Hecho cuando:** `design-incremental.md` con el mapa de decisión, el
    pseudocódigo del conjunto afectado, los casos límite y el plan de pruebas;
    revisado por `verifier`.
  - **Evidencia:** nota en la spec y fila en `evidence.md`.
  - **Estado:** **cerrada el 2026-10-25.** `design-incremental.md` (5
    revisiones) fija la **clave exacta sobre bronce**
    (`location_city_normalized_column` + normalizador de empresa por fuente), sin
    superconjunto ni guardas; MERGE con marcadores y `WITH SCHEMA EVOLUTION`,
    `as_bool`, `repeat_window` y ventana abierta en tabla vacía con
    `_ingest_date`. Verificación independiente **PASA** en la cuarta vuelta;
    comprobaciones locales: 281/281 ciudad, 10/10 clave bronce↔silver, contrato
    31/33 y repetición por contenido. `[NO VERIFICADO]`: MERGE real de Delta,
    `WITH SCHEMA EVOLUTION` y aislamiento de vistas (T-10/T-11).

- [x] **T-08 — Módulo `Silver_Incremental.py` + tests** (~60 min)
  - **Objetivo:** lógica **genérica** comprobable: `key_expression`,
    `changed_since`, `affected_groups`, `recompute_groups`,
    `merge_into_silver`, `window_bounds` y `force_full`, con tests pytest +
    Spark local. **No mueve ni unifica las cadenas de transformación de cada
    fuente**: recibe el DataFrame de bronce recalculado y devuelve el
    DataFrame; el transform sigue en cada notebook. Reutiliza la semántica de
    `keep_latest_by` (007) sin modificar ese módulo. Incluye una función
    columnar nueva en `Enrich_Job_Offers_Dataframes.py`
    (`location_city_normalized_column`) que `enrich` pasa a reutilizar (una sola
    definición, sin UDF) y el paso de la normalización de empresa como
    callable; el cambio del módulo requiere **aprobación previa del diff**.
  - **Hecho cuando:** tests en verde cubriendo: primera ejecución completa;
    tabla vacía con `_ingest_date` → incremental con ventana abierta e inserción
    por MERGE; incremental con clave nueva; captura más reciente gana; fila
    vieja no pisa;
    **migración de grupo sin fila huérfana**; ventana vacía no escribe; repetir
    la ventana deja el mismo contenido (P21); tabla vacía con `_ingest_date`
    (P1b); backfill por rango; contrato de columnas con
    `_ingest_date`; evolución de esquema con MERGE. **Gate de equivalencia:**
    el resultado incremental debe ser igual al de la reconstrucción completa
    sobre el mismo fixture, con una **cadena de referencia** construida con las
    funciones de módulo (`de.enrich`, `de.location_city_normalized_column`,
    `de.apply_company_canonical`) **más `apply_company_normalization`
    (`initcap`) copiada del notebook antes del canónico**, e
    `inc.key_expression()`, con el rename/esquema copiados del notebook
    (**P15b**). La cadena real de los notebooks no es importable: se valida en
    T-10 con el **run B incremental** sobre datos reales, y el diff de T-09 es
    el control de deriva de la copia. Incluye **P16b**: equivalencia entre
    `de.location_city_normalized_column` y la columna `location_city` de
    `enrich` (con la función pura como referencia; acentos, países, ciudades,
    nulos; sin fijar un número de valores) y la clave calculada sobre bronce
    contra la de la silver. `enrich` se llama con `add_location_fill=True` y
    `normalize_location_city=True`; cualquier otra combinación no es válida para
    la clave. El test de contrato se parametriza por `target.columns`
    (**indeed 31**; **linkedin/infojobs/multi_site 33**, que conservan
    `salary_min` y `salary_max`), nunca por una lista global. `python -m pytest
    databricks_notebooks/tests -q` en verde.
  - **Evidencia:** salida de la suite y fila en `evidence.md`.
  - **Estado:** **cerrada el 2026-10-25.** `Silver_Incremental.py` +
    `test_silver_incremental.py` (**32 tests**, P1–P21 con P1b, P15, P15b, P16,
    P16b y P17–P21) y `location_city_normalized_column` en `Enrich` (solo el
    diff aprobado). Verificación independiente **PASA**: 32 passed (10 m 47 s)
    en el test de la tarea y 29 passed/1 skipped en los de `Enrich`; suite
    `databricks_notebooks` **171 passed, 1 skipped**. Desviaciones aceptadas:
    `require_unique_groups` extra y conteos antes del MERGE. `[NO VERIFICADO]`:
    MERGE real, `WITH SCHEMA EVOLUTION`, `USING` sobre vista y escritura del modo
    `full` (T-10/T-11).

- [x] **T-09 — Notebooks silver en modo incremental (dev)** (~60 min)
  - **Objetivo:** en cada silver, **reestructurar las celdas en dos funciones**
    (`prepare_bronze` y `build_offers`) y sustituir la escritura por `inc.run`.
    No se reescribe la lógica: el diff debe demostrar que renames, derivación de
    `posted_date`, flags por fuente, enrich, normalización de empresa,
    normalización de localización y `drop` son los mismos, movidos con
    indentación y `return`. Cambios de texto permitidos y solo estos: la línea
    de `_key` pasa a `inc.key_expression()` (solo la de ofertas; la
    `add_semantic_key` de empresas de indeed se conserva), quitar
    `_ingest_date` de la lista del `drop`, quitar las llamadas sueltas de
    normalización de las celdas de definiciones, el widget `repeat_window` con
    su envoltorio de resumen, y la procedencia de
    `companies_infojobs`/`multi_site`. En el reparto:
    `prepare_bronze` devuelve el frame renombrado **antes** del cast/select (lo
    necesitan `companies_indeed` y `companies_linkedin`); el cast/select va en
    `build_offers`. Las definiciones de funciones (empresa canónica, `_key`)
    quedan a nivel del notebook y solo su llamada entra en `build_offers`.
    `companies_indeed` y `companies_linkedin` siguen leyendo el frame preparado;
    `companies_infojobs` y `companies_multi_site` leen
    `silver.offers_<fuente>.company_name` + `dropDuplicates()`, **sin reaplicar
    normalización** (el valor ya es canónico), y se escriben después de las
    ofertas. Widgets
    `lookback_days`, `from_date`, `to_date`, `force_full` y `repeat_window`;
    resumen JSON por `dbutils.notebook.exit` (modo, ventana y conteos por
    fuente; con `repeat_window` se devuelve `{"repeat_window": true,
    "runs": [...]}`) para leer evidencia sin SQL. Desplegar a dev
    (`databricks bundle deploy -t dev --profile vs_code`; el deploy no ejecuta
    el job).
  - **Aprobación previa (AGENTS.md):** presentar por notebook fichero, celdas
    afectadas, diff y motivo, y esperar el OK; el diff debe demostrar que no
    se ha tocado ninguna celda de negocio.
  - **Hecho cuando:** diff aprobado, notebooks desplegados en dev, contrato de
    oro/exports sin cambios (`_ingest_date` no aparece en `gold/*` ni en los
    Parquet) y `docs/RUNBOOK.md` + `docs/es/RUNBOOK.md` con el procedimiento de
    backfill (parámetros, ventana, coste y rollback). Los parámetros de job
    `backfill_from`, `backfill_to` y `repeat_window` (vacíos por defecto) se
    añaden a `resources/elt.job.yml` y se pasan a las cuatro tareas silver con
    `{{job.parameters.*}}`; también requieren aprobación previa y no cambian el
    comportamiento por defecto (ni en prod).
  - **Evidencia:** diff aprobado, id de deploy y fila en `evidence.md`.
  - **Estado:** **cerrada el 2026-10-25.** Cuatro notebooks reestructurados
    (26→15, 24→14, 19→9, 18→10 celdas) con la lógica de negocio idéntica salvo la
    lista cerrada; `resources/elt.job.yml` con `backfill_from`, `backfill_to` y
    `repeat_window` (default vacío) en las cuatro tareas silver; runbook EN/ES.
    `bundle validate` y `bundle deploy -t dev` en verde (7 ficheros,
    `Updated jobs.elt`; producción intacta). Verificación independiente
    **PASA**: contrato con `inc.run`, widgets, `.yml` y celdas de negocio
    comprobados. `[NO VERIFICADO]`: efectos reales (MERGE, primera ejecución
    completa, equivalencia de `companies_*` y exports) en T-10/T-11.

- [ ] **T-10 — Validación en dev (runs 1 y 2 de 2)** (~30 min)
  - **Hecho cuando:** **run A** cierra 8/8 tareas y es la migración (tabla sin
    `_ingest_date` → reconstrucción completa con `overwrite`), con silver
    ≤ 2,8–3,6 min de T-05 y total ≤ 13 m 16 s, y exports con `meta.json`
    válido. **Run B es un backfill incremental** con `from_date`/`to_date`
    sobre bronce existente (sin ficheros nuevos): **ejecuta el MERGE, los
    marcadores y el conjunto afectado**, y repite la misma ventana una segunda
    vez dentro del notebook (widget de repetición) para demostrar idempotencia.
    Sus exports deben quedar **idénticos byte a byte** a los de A (equivalencia
    en datos reales). La repetición se compara por **contenido** (`target_rows`,
    conteos de oro y exports); `affected_groups`, `deleted_groups`, `merge_rows`
    y `recomputed_groups` pueden bajar en la segunda pasada (porque la primera ya
    limpió un grupo migrado, o porque el grupo antiguo conserva filas): el
    contenido manda y esos conteos no son criterio de idempotencia.
  - **Ejecución:** `bundle deploy -t dev` (gratis) y
    `bundle run elt -t dev --profile vs_code` dos veces; toda la evidencia sale
    de las salidas JSON y de los `meta.json`; **ninguna consulta SQL**. El run B
    pasa `backfill_from`/`backfill_to`/`repeat_window` por parámetros del job
    (`--params`, soportado por la CLI instalada — verificado el 2026-10-25). La
    ventana exacta queda en el resumen. El lote del run A incluye los ficheros
    del landing desde el run de T-05 (24-oct): se anota el tamaño para que la
    comparación de tiempos con T-05 sea honesta.
  - **Coste:** 2 runs (~0,48 €), anotados en `docs/costs.md`.
  - **Evidencia:** ids, tabla comparativa de tiempos y exports A/B, fila en
    `evidence.md`.
  - **Estado (2026-10-28): BLOQUEADA por rendimiento.** La validación se
    intentó con **5 runs de dev** (~1,20 €) y no se cerró:
    - **A** `263658658061220`: **SUCCESS**, 8/8, **22 min 18 s**, migración
      confirmada (`mode="full"`, `mode_reason="missing_ingest_date"` en las
      cuatro silvers) y lote de **+56 ofertas** en el export (26.057 → 26.113).
      Las silvers tardaron **9,8–11,8 min** por un `count()` de más en el modo
      `full` (3,2–3,5× T-05).
    - **B** `186230462263213` (`backfill_from=2026-10-24`,
      `backfill_to=2026-10-25`, `repeat_window=true`): **TIMEDOUT** a los
      30 min 46 s (silvers canceladas a los ~27 min; oro y exports `SKIPPED`);
      sin resumen ni comparación A/B; los exports quedaron en el estado de A.
    - **A2** `207866361330883`: **SUCCESS**, 8/8, **14 min 39 s**, con el modo
      `full` en una sola pasada; silvers **2 m 47 s–3 m 46 s** (línea base
      T-05). Es la referencia funcional y de exports del bloque B.
    - **B2** `1056455747512203` (backfill 25–27, `repeat_window`):
      **TIMEDOUT** a los 30 min 37 s. **No escribió nada**: las versiones Delta
      siguen en las de A2 (indeed v11 3.096 filas, infojobs v12 2.527,
      linkedin v8 12.504, multi_site v11 10.068). El bronce de indeed solo
      ingirió **190 filas** el 27-oct, así que el tiempo no lo explica el
      volumen.
    - **B3** `1015448868873733` (repetición con `keyed` compartido):
      **TIMEDOUT** a los 30 min 30 s (bronce 8 m 06 s; silvers cortadas a los
      22 min 04 s); tampoco escribió. El fix de `keyed` no era el cuello: la
      medición local da **~45 s por pase** con el aviso de codegen «Code grows
      beyond 64 KB» en `build_offers`, y el único paso no medido es el `MERGE`.
    - **Lectura:** con una ventana de 7 días que cubre casi todo el bronce, el
      camino incremental cuesta **≥4× la reconstrucción completa** (2 m 47 s–
      3 m 46 s por silver en A2) y ni siquiera termina. No es volumen: es
      coste fijo por pases y por el `MERGE`. **No se lanza un cuarto run.**
  - **Por qué bloquea:** T-11 desplegaría a producción un mecanismo que no
    cabe en la ventana de 1800 s (el run de prod ya gasta ~19 min) y la 008 no
    puede demostrar RF-7 (equivalencia), RF-8 (idempotencia) ni RF-10 (exports
    A/B idénticos) aunque los tests locales estén en verde.
  - **Qué la desbloquea:** la **spec 010** (rendimiento, layout y compute)
    debe medir el coste real (Spark UI de B2/B3 + instrumentación por pasos),
    dejar el incremental por debajo de la reconstrucción completa y repetir la
    validación A/B. Si la persona decide **descopear el bloque B**, T-10 y T-11
    pasan como tareas heredadas a la 010 y la 008 cierra con el bloque A.
  - **Coste:** 5 runs (~1,20 €) de los 2 aprobados (~0,48 €); el exceso
    (~0,72 €) se descuenta del presupuesto de la 010. 0 sesiones SQL.

- [ ] **T-11 — Producción (ventana previa a domingo)** (~30 min)
  - **Hecho cuando:** tras cerrar T-06, `bundle deploy -t prod` con rollback de
    bundle y `RESTORE` de las silvers disponible; el **primer run programado**
    reconstruye las silvers completas (migración, sin one-off) y cierra 8/8 con
    los exports actualizados y el dashboard publicado el lunes; el **domingo
    siguiente** el run es incremental real (la ventana no está vacía solo si hay
    ficheros nuevos) y el dashboard vuelve a publicar; SQL warehouse `STOPPED`
    antes y después. Bitácora del roadmap actualizada.
  - **Nota:** los runs semanales ya están en la cadencia de la 009; esta tarea
    no añade runs manuales.
  - **Dependencia (2026-10-28):** **no se despliega a producción** hasta que la
    010 deje el incremental por debajo de la ventana de 1800 s y repita la
    validación A/B. El procedimiento (`cutover.md`) sigue siendo el de la 007.
  - **Evidencia:** ids de deploy y de los dos runs, conteos, versiones de
    `RESTORE`, estado del warehouse y fila en `evidence.md`.

- [ ] **T-12 — Cierre de la spec**
  - Notas `docs/databricks-de/notes/02-delta.md` y
    `04-incremental-architecture.md` escritas con la plantilla del índice.
  - Calendario y bitácora de `docs/databricks-de/roadmap.md` al día; suites
    completas en verde; comprobación real registrada; merge a `main` y tag
    (`spec-008`) solo con el bloque A y el B verificados.
  - **Estado (2026-10-28):** el bloque B no está verificado (T-10 bloqueada y
    T-11 sin ejecutar), así que la 008 **no puede cerrarse completa**. Si la
    persona descopea el bloque B, la 008 cierra con el bloque A y la 010
    hereda T-10/T-11; si no, sigue abierta hasta que la 010 desbloquee. Las
    notas `02-delta.md` y `04-incremental-architecture.md` se escriben al
    cerrar, no antes.

## 3. Cierre de la spec

- Notas `docs/databricks-de/notes/02-delta.md` y
  `04-incremental-architecture.md` escritas con la plantilla del índice.
- Calendario y bitácora de `docs/databricks-de/roadmap.md` al día.
