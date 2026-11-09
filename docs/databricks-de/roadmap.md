# Hoja de ruta — Certificación Databricks Data Engineer Professional

Objetivo: **aprobar el examen antes de fin de 2026** aplicando cada dominio del
temario al proyecto Talent Market Lens, no en abstracto.

- **Ritmo:** 10–15 h/semana.
- **Ventana de examen:** 21–27 de diciembre (tope: 30 de diciembre). Reserva
  pendiente (la paga la empresa); confirmar la fecha antes de finales de octubre.
- **Voucher:** 50 % de descuento; comprobar caducidad y condiciones antes de
  reservar.
- **Regla de oro:** cada dominio se aprende tocando el proyecto y se repasa con
  notas propias. El asistente orienta y revisa; la persona implementa.

## Mapa dominio → spec → práctica en el proyecto

| Dominio del examen | Spec | Dónde se practica |
|---|---|---|
| 1. Spark performance: shuffles, joins, broadcast, skew, partitioning, file sizes, query plans, Spark UI, Query Profile, data skipping | 010 | Medir y optimizar la ELT con `explain`, Spark UI y Query Profile; tamaño de ficheros y layout de Delta; spike de serverless y decisión final de compute (venido de la 009; `D2ds_v6` no completa el run con las cuatro silvers en paralelo) |
| 2. Delta: transaction log, ACID, MERGE, CDF, deletion vectors, OPTIMIZE, liquid clustering, schema evolution | 008 | `DESCRIBE HISTORY` y `_delta_log`, MERGE idempotente, CDF y evolución de esquema en Bronze |
| 3. Streaming: checkpoint, trigger, watermark, state, microbatch, exactly-once, Structured Streaming vs Lakeflow | 011 | Auto Loader existente; demo controlada de Lakeflow Declarative Pipelines vs Structured Streaming |
| 4. Arquitectura incremental | 008 | Silver y Gold incrementales con reproceso idempotente por fecha y fuente |
| 5. CI/CD: Git, Asset Bundles, deployment, environments, parameters, automation | 007 | Job `Git Based ELT` como Asset Bundle; targets dev/prod, variables y validación en CI |
| 6. Observabilidad: Spark UI, Query Profile, system tables, event logs, job monitoring | 011 | System tables, event log del job, alertas y monitorización del run diario |
| 7. Seguridad y gobernanza: Unity Catalog, ACLs, herencia, row filters, column masks | 012 | Inventario real de UC (owners, grants directos/efectivos, ACL del job), ensayo de grant/rollback en dev, herencia medida, row filter y column mask en Free Edition, contrato de calidad y CHECK constraints. **No hay service principal**: `run_as` es la persona y el storage es una Azure Managed Identity dedicada; el exceso de la cuenta personal se mide y se aprueba como limitación (D-01/D-02), sin afirmar least privilege |

**Transversal (no es dominio del examen):** coste y cadencia → spec 009.
Condiciona cómo se verifica el resto (local-first y presupuesto de runs).

## Backlog de prácticas (temario → proyecto)

Todo lo que se aprenda en el curso se aplica al proyecto; lo que no encaje en
una spec planificada se anota aquí y se encaja al planificar la siguiente.

- **Cuarentena en la ingesta (tablas Delta).** **Cerrada por la 012 (T-05/T-06).**
  `Upsert_Job_Offers.py` separa `missing_key`, `missing_scraped_at` y el motivo
  nuevo `company_name_rating` (`4,0`/`4.0` exactos, no `24/7 Retail`), con
  precedencia estable, identidad `(_row_hash, _quarantine_reason,
  _source_file)` y tratamiento documentado en el RUNBOOK §10.2. El run
  programado del 2026-11-08 (T-10) procesó 11.710 filas y **0 fueron a
  cuarentena**; no se creó ninguna tabla `<fuente>_quarantine` y no hay
  histórico Delta previo que inventariar.
- **CHECK constraints de Delta (data quality).** **Cerrada por la 012
  (T-05/T-07/T-08).** 20 constraints aplicadas y verificadas por API (10 dev +
  10 prod): `chk_<tabla>_job_id_required` y `chk_<tabla>_salary_range_order` en
  las 4 silver y `gold.fact_offers`, con `IS NULL OR` explícito. La constraint
  de fecha queda pospuesta (`[NO VERIFICADO]`): `IsValidPostingDate` es un flag
  y el histórico tiene fechas desde 2018.
- **Bronce deduplicado en la ingesta (paso B).** **Cerrada por la 008 y
  validada en la 010:** bronce es el estado actual (upsert con `MERGE` por
  clave, gana la captura más reciente), sin historial de cambios ni expiración
  técnica. Claves `job_id` y `site + job_id` (multi_site).
- **Dedup de negocio en oro sin localización (hallazgo 2026-10-21, spec 008).**
  **Pendiente**: requiere una spec con ese único resultado.
  `gold_build.ipynb` deduplica ofertas por `title_norm + company_norm +
  salary_filled` y **no** incluye localización ni fecha de publicación: dos
  anuncios legítimos de la misma empresa con el mismo título y salario en
  ciudades distintas se colapsan en uno, y una republicación con salario
  distinto no se colapsa. Candidata a spec nueva (un único resultado: dedup de
  negocio por oferta con localización y ventana temporal), no se corrige en la
  008.

## Calendario

| Semana | Fechas | Foco | Estado |
|---|---|---|---|
| 1 | 12–18 oct | **Spec 007 (preparación):** recon del workspace, roadmap y documentos | hecha |
| 2-3 | 19 oct - 1 nov | **Spec 007 (código)** y **Spec 009 (coste y cadencia)** | 007 y 009 hechas (009 cerrada el 24-oct) |
| 4–6 | 2–22 nov | **Spec 008:** Delta y arquitectura incremental (bloque A: bronce upsert) | 008 **cerrada el 1-nov** (merge `--no-ff` y tag `spec-008`); bloque B cerrado por herencia en la 010 (T-08/T-09) |
| 7 | 23-29 nov | **Spec 010:** rendimiento, layout físico y compute | hecha (28–31 oct 2026) |
| 8 | 30 nov – 6 dic | **Spec 011:** streaming y observabilidad | **hecha** (adelantada al 1–5 nov 2026); cerrada salvo el run programado del domingo 8-nov |
| 9 | 7–13 dic | **Spec 012:** seguridad y gobernanza | **cerrada el 9-nov 2026**: run programado 9/9, dashboard publicado y tag `spec-012` |
| 10–11 | 14–27 dic | Simulacros, repaso final y **examen** | pendiente |

Contingencias:

- Si la spec 006 se retrasa más de una semana, la 007 arranca igualmente en modo
  solo-dev (job de desarrollo desplegado desde el bundle, sin tocar el job
  manual de producción).
- Si diciembre va justo, la 012 se comprime a teoría + demo mínima (la
  gobernanza de Unity Catalog ya se usa a diario en el proyecto).

## Método de estudio

- **Aplicar el temario al proyecto:** cada práctica del curso se lleva al
  proyecto; si no encaja en una spec planificada, se anota en el backlog de
  prácticas y se encaja al planificar la siguiente.
- **Formato de tarea:** objetivo → preguntas guía → pistas y documentación →
  hecho cuando → evidencia. La persona implementa; el asistente pregunta,
  orienta y revisa (tests y revisión independiente del diff antes del commit).
- **Commits atómicos** por tarea (`T-XX`); checkpoint cada ~5 tareas; al cerrar
  cada spec, comprobación real y revisión, según `AGENTS.md`.
- **Ramas y despliegue (desde la 007):** cada spec se prueba con
  `bundle deploy -t dev` desde su worktree (job dev, catálogo
  `job_offers_dev`); producción se despliega desde `main`. La Git folder del
  workspace queda en `main` y no se conmuta por spec.
- **Notas por dominio** en `notes/`: se escriben al terminar cada tarea
  relevante y se revisan al cerrar la spec.
- **Repaso espaciado:** 5 minutos al abrir sesión releyendo la última nota; en
  la recta final, solo notas, evidencia y simulacros.
- **Coste y entornos (desde la 009):** verificación local-first, presupuesto de
  runs por spec y tres entornos (local, Free Edition como laboratorio, workspace
  pagado como producción). En la 009 el asistente implementa con revisión de la
  persona.

## Verificación local-first, entornos y presupuesto

El techo de coste del proyecto es de **producción**; el desarrollo también cuesta
y puede superarlo. Estimado en la spec 009: un run de dev de ~19 min en
`D4ds_v4` cuesta ~0,24 € (0,13 € en `D2ds_v4`) y una sesión de SQL ~1,29 €. Un
run corto es ~5× más barato que una consulta.

| Entorno | Uso | Coste |
|---|---|---|
| Local | Tests, lógica de notebooks, exports y modelos pequeños sobre el Parquet | 0 € |
| Databricks Free Edition | Laboratorio: temario, UI, notebooks, Unity Catalog, jobs serverless, MLflow | 0 € (no comercial, cuotas) |
| Workspace pagado | Pipeline real: job semanal, ADLS, dashboard | ≤ 3 €/mes |

Free Edition es serverless-only: no tiene Spark UI, ni clusters clásicos, pools,
policies, Photon, spot, init scripts, storage externo ni GPU. El Spark UI y el
compute clásico se practican en el laboratorio del curso o en el workspace
pagado con sesiones cortas (all-purpose `D2ds_v4` ~0,41 €/20 min; run de dev
~0,13 €). El PAT del workspace pagado no se copia a equipos ajenos y el
laboratorio usa un perfil de CLI propio (`free`).

Presupuesto por spec: máximo 3 runs de dev + 1 sesión SQL, anotados con su
motivo en `tasks.md`. La 009 tiene un límite propio de 2 runs y 1 sesión; el
detalle por spec está en `specs/009-Cost-And-Cadence/plan.md` §17. SQL agrupado
en una sesión (<10 min) y grants/metadatos por API de Unity Catalog (gratis).
Total del track: ≤ 9 €.

ML/MLOps (tras el examen): entrenar en local o en el laboratorio, registrar en
Unity Catalog del laboratorio y puntuar en batch dentro del job semanal
(~0,01 €); serving solo con scale-to-zero y demo corta.

## Recursos

- Guía oficial del examen (Data Engineer Professional):
  <https://www.databricks.com/learn/certification/data-engineer-professional>
- Cursos de Databricks Learning Academy ya realizados: repasar el módulo del
  dominio al empezar cada spec.
- Documentación de referencia por dominio: se enlaza desde cada `tasks.md`.

## Bitácora

| Fecha | Hito | Notas |
|---|---|---|
| 2026-10-12 | Arranca el track; spec 007 creada | Worktree `talent-market-lens-007`, rama `spec/007-elt-as-code` desde `main`; pendiente confirmar la reserva del examen |
| 2026-10-12 | Recon T-02–T-04 completado | Edición Premium/Enterprise inferida por UC; job: 7 tareas, schedule `50 0 23 * * ?` (Europe/Madrid), cluster `tm-lens-job`; Git folder con pull manual, hoy en la rama de la 006 |
| 2026-10-12 | 006 mergeada a `main`; rama 007 sincronizada | Merge `314b99c`; pendiente poner la Git folder del workspace en `main` (pull manual) |
| 2026-10-17 | Diagnóstico de rendimiento dev vs prod; arreglo T-10a aprobado en la 007 | Bronce dev LinkedIn: 9.569.896 filas para 13.376 `job_id` (~715 copias por snapshot acumulativo del landing); prod: 16.371 filas y run completo de 20,4 min. Dedup temprano en la silver dentro de la 007; semilla estructural (append vs carga completa) a 008/009 |
| 2026-10-17 | Backlog del temario anotado: cuarentena de ingesta y check constraints | La cuarentena local/landing ya existe (ficheros y filas en los scrapers); falta la capa Delta (`bronze.<fuente>_quarantine`) y su tratamiento en la ELT. CHECK constraints de Delta para data quality en silver/gold. Candidatas: 008/011 |
| 2026-10-17 | Decisiones de la 008: bronce upsert (paso B) como primera tarea | Sin historial (CDF) ni expiración técnica; claves `job_id` y `site + job_id`; gana el `scraped_at` más reciente; migración incluida. Orden acordado: la 007 estrena el mecanismo seguro y la 008 cambia la ingesta |
| 2026-10-18 | Cutover a producción: el job del bundle es la fuente de verdad | Manual `704156530681034` → `PAUSED` (23:21); bundle `916863262593632` → `UNPAUSED` (23:24) con la misma cron; merge `5635c89` y CI verde; sin runs nuevos hasta la noche siguiente |
| 2026-10-19 | Primer run programado del bundle en verde (T-19) | Run `471695262858572` SUCCESS (19 m 45 s, 7/7 tareas); 1 único run y manual sin runs; conteos al alza (`fact_offers` 24.563 → 24.992) y `meta.json` con `data_date` 19-oct |
| 2026-10-20 | Rollback en seco (T-20) y documentación operativa (T-21) | Ida y vuelta en 11 m 04 s sin runs nuevos ni sorpresas; RUNBOOK §9, README, nota `05-cicd.md`; propuesta de `AGENTS.md` presentada (pendiente de OK) |
| 2026-10-23 | Spec 009 (coste y cadencia) creada y priorizada sobre la 008 | Worktree `talent-market-lens-009`, rama `spec/009-cost-and-cadence` desde `main`; la 008 queda aparcada en su worktree con la rama respaldada |
| 2026-10-23 | Medición de coste | Run de prod 19,2 min ≈ 0,24 €; el 85–90 % del gasto es el SQL warehouse (`PREMIUM_SERVERLESS_SQL_COMPUTE`): Small ≈ 1,29 € por despertar. El pico del 17-oct son ~15 despertares, no los runs de dev; los días sin warehouse quedan en ~0,2–0,5 € |
| 2026-10-23 | Renumeración y política | Rendimiento 009→010, streaming 010→011, gobernanza 011→012; tres entornos y presupuesto de runs (≤ 9 € en todo el track) |
| 2026-10-23 | Spec 009: compute, warehouse y cadencia | Warehouse a 2X-Small (≈0,43 €/despertar); serverless se traslada a la 010; job prod desplegado con `50 0 23 ? * SUN` (domingos 23:00, `UNPAUSED`) |
| 2026-10-24 | Spec 009 cerrada | Merge `--no-ff` a `main` y tag `spec-009`; workflow del dashboard en verde con `data_date` 2026-10-22; presupuesto de 15 €/mes con alertas; primer run semanal el domingo 25-oct |
| 2026-10-28 | Spec 008: bronce cerrado, silvers descopeadas | Bloque A con el one-off de prod y la comprobación real de T-06 en verde (run programado `636564838129029` del domingo 25-oct y dashboard publicado por el workflow `40276725938`); la duración, los tiempos por tarea y el warehouse se registran el 1-nov. Bloque B (silvers incrementales) implementado con tests, pero **T-10 bloqueada**: tres runs `TIMEDOUT` a los ~30 min sin escribir, con el incremental costando 3,7–6× la reconstrucción completa. **Descopeado a la 010**; T-11 no se despliega. Fix de `keyed` revertido |
| 2026-10-28 | Spec 010 (rendimiento) creada | Worktree `talent-market-lens-010`, rama `spec/010-performance-and-layout` desde `spec/008-delta-incremental` (el incremental aún no está en `main`; rebase al fusionar o descopear la 008). Diagnóstico gratis antes de optimizar (Spark UI de B2/B3, local, instrumentación por acción); presupuesto aprobado 3 runs + 0 SQL dentro del techo de 1,8 € de la 009 |
| 2026-10-28 | Spec 010: diagnóstico medido y P1 aplicado | Spark UI de A2 `207866361330883` frente a B2 `1056455747512203` y B3 `1015448868873733`: 10.790 tareas y stages de 200 en B3 con 4 núcleos; el diferencial es el coste por consulta (15 s frente a 84–96 s), no el número de acciones. Se fija `spark.sql.shuffle.partitions: "8"` y se miden A `485578578869927` (808 s) y B `269508392317481` (974 s): el incremental deja de expirar, pero RF-2a aún no se cumple |
| 2026-10-30 | T-03b/T-04/T-05/T-06: incremental medido y decisiones sin cambiar código | Run `879269828668729` en **927 s**: RF-2a y RF-3 en verde (pases de 123,8–138,2 s en frío); `operationMetrics` del `MERGE` con `numTargetRowsCopied=0` y 1 fichero añadido por pase; `DESCRIBE DETAIL` de 18 tablas (42 ficheros / 98.369.519 B) y decisión de no tocar el layout; compute `D4ds_v4` single node con el arranque (383 s) como coste dominante |
| 2026-10-31 | A/B en dev (T-07/T-08) | A `221895134926434` (816,0 s, `force_full`) y B `549237053478569` (985,3 s, incremental + `repeat_window`), 8/8: RF-3, `MERGE` e idempotencia en verde; la equivalencia de exports pasa a T-09 por contenido (el byte a byte medía el escritor) |
| 2026-10-31 | A/B en producción (T-09) con desviación de RF-4 | A `321262594455396` (915 s) y B `317888374535056` (747 s), 8/8 sobre el mismo bronce: RF-3, `MERGE` e idempotencia en verde; RF-4 **no se cumple** por un empate no determinista de `_keep_best`, corregido con `source_scraper`/`job_id`; la persona aprueba no repetir el A/B y RF-4 queda `[NO VERIFICADO]` en producción. Dashboard publicado y warehouse `STOPPED` |
| 2026-11-01 | Spec 010 cerrada | Merge `--no-ff` a `main` y tag anotado `spec-010`; nota `01-spark-performance.md` escrita; 5 runs de dev (~1,05 €) frente al techo de 1,8 € y 0 sesiones SQL; producción de T-09 ~0,44 € fuera del techo de dev. RF-8 cierra con desviación aprobada (A/B manual de T-09 + dashboard + warehouse `STOPPED`); el run programado del 8-nov será la comprobación posterior y validará el fix de `_keep_best`. No se repite ningún run. |
| 2026-11-01 | Spec 008 cerrada | T-06 registrada con el run programado `636564838129029` (**8/8, 14 min 01 s**, dashboard por el workflow `40276725938`, warehouse `STOPPED`; los conteos de ese export quedan `[NO VERIFICADO]` porque su `meta.json` se sobrescribió y el contenedor no tiene versionado). T-10 y T-11 cerradas **por herencia** de la 010 (T-08/T-09), con el desvío del gate de T-10 (816 s frente a 13 m 16 s) cubierto por el RF-3 de la 010 (≤ 1350 s). Notas `02-delta.md` y `04-incremental-architecture.md` escritas; merge `--no-ff` a `main` con `AGENTS.md` restaurado de `main` y tag `spec-008` |
| 2026-11-05 | Spec 011: cierre documental (queda el run programado del 8-nov) | Decisión medida sobre el mismo extracto CSV en Free Edition: el declarativo gana **2 de 8** criterios (código propio y observabilidad) y **no se migra**; se adopta la observabilidad (resumen del bronce, `ops.run_metrics` de **13 columnas**, novena tarea `Publish_Run_Summary` con `run_if: ALL_DONE`, cuatro `checks` y health rule `RUN_DURATION_SECONDS > 1500` en dev y prod). Banco local `streaming_lab.py` con **6 tests**. Medido: **0,045956 USD de DBU** por run de 744 s (≈4× menos que la estimación por duración), retención de `system.lakeflow` ≈**67 días** y latencia **> 7 min 37 s**; event log retirado (el clúster no arranca con `abfss://`). T-15 (16 filas incoherentes de 1569) incluida por decisión explícita. Coste: **5 runs de dev** (uno perdido) y **2 sesiones SQL**, ≈**0,23 USD** de DBU y ≈**0,86 €** nominales de warehouse. Pendiente: run programado del domingo **2026-11-08 23:00** (`Europe/Madrid`), dashboard del lunes 01:00 UTC y merge/tag. Notas `03-streaming.md` y `06-observability.md` escritas |
| 2026-11-08 | Spec 012: gobernanza, calidad y publicación de T-09 | Inventario real de UC sin SQL: sin grants directos, owners en la persona, `run_as` persona, storage con **Azure Managed Identity** dedicada (no service principal) y 4 credenciales duplicadas sin EL. Ensayo de grant/rollback y herencia en dev; demo de row filter/column mask en **Free Edition** (ruta B, dos usuarios). Contrato de calidad en `Data_Quality.py`, cuarentena `company_name_rating` y **20 CHECK constraints** aplicadas en una única sesión SQL (~0,43 €). T-09: `bundle validate` dev/prod en verde, merge checkpoint a `main` y deploy de prod desde `main`; sin grants nuevos, EL en la raíz aprobada como desviación (D-02). Pendiente: run programado del **8-nov 23:00**, dashboard del 9-nov y tag `spec-012` |
| 2026-11-09 | Spec 012 cerrada | Run programado `762969079133747`: **9/9** tareas en **14 min 40 s**, 11.710 filas de bronce y **0 en cuarentena**; `ops.run_metrics` con `record.status ok` y los cuatro checks en verde; **10 constraints** y grants `{}` intactos; warehouse `STOPPED` antes y después. Dashboard publicado por el workflow `42581592569` con `data_date 2026-11-07` (2 días, límite 8). Suite `databricks_notebooks/tests` en verde; nota `07-security-governance.md`, índice, roadmap, RUNBOOK y costes actualizados; merge final `--no-ff` a `main` y tag `spec-012`. Coste: 1 sesión SQL (~0,43 €) + run ordinario de producción (~0,18 € estimados); **0 runs de dev** |
