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
| 1. Spark performance: shuffles, joins, broadcast, skew, partitioning, file sizes, query plans, Spark UI, Query Profile, data skipping | 009 | Medir y optimizar la ELT con `explain`, Spark UI y Query Profile; tamaño de ficheros y layout de Delta |
| 2. Delta: transaction log, ACID, MERGE, CDF, deletion vectors, OPTIMIZE, liquid clustering, schema evolution | 008 | `DESCRIBE HISTORY` y `_delta_log`, MERGE idempotente, CDF y evolución de esquema en Bronze |
| 3. Streaming: checkpoint, trigger, watermark, state, microbatch, exactly-once, Structured Streaming vs Lakeflow | 010 | Auto Loader existente; demo controlada de Lakeflow Declarative Pipelines vs Structured Streaming |
| 4. Arquitectura incremental | 008 | Silver y Gold incrementales con reproceso idempotente por fecha y fuente |
| 5. CI/CD: Git, Asset Bundles, deployment, environments, parameters, automation | 007 | Job `Git Based ELT` como Asset Bundle; targets dev/prod, variables y validación en CI |
| 6. Observabilidad: Spark UI, Query Profile, system tables, event logs, job monitoring | 010 | System tables, event log del job, alertas y monitorización del run diario |
| 7. Seguridad y gobernanza: Unity Catalog, ACLs, herencia, row filters, column masks | 011 | Grants por capa, least privilege del service principal, row filter y column mask de demo |

## Backlog de prácticas (temario → proyecto)

Todo lo que se aprenda en el curso se aplica al proyecto; lo que no encaje en
una spec planificada se anota aquí y se encaja al planificar la siguiente.

- **Cuarentena en la ingesta (tablas Delta).** Guardar las filas con estructura
  o valores inválidos en `bronze.<fuente>_quarantine` (fuente, motivo, fecha) y
  tratarlas después (corregir o descartar con evidencia), en vez de dejarlas
  pasar o perderlas. Caso real: `company_name` con valores tipo `4,0` (ratings
  de empresa desplazados a esa columna). Ya existen cuarentenas en el pipeline
  local (scrapers) y de ficheros en el landing; falta la capa Delta y su
  tratamiento en la ELT. Candidata: 008 (ingesta e incrementalidad) y/o 011
  (calidad).
- **CHECK constraints de Delta (data quality).** Aplicar constraints en
  silver/gold (`job_id` no nulo, `salary_min <= salary_max`, fechas
  razonables…) y registrar/decidir qué se hace al incumplir (cuarentena,
  descarte o marca). Candidata: 008 (Delta) y/o 011 (gobernanza).
- **Bronce deduplicado en la ingesta (paso B).** Planificado como primera
  tarea de la 008: bronce = estado actual (upsert con `MERGE` por clave, gana
  la captura más reciente), sin historial de cambios y sin expiración técnica
  (el filtro de «último mes» es de negocio, en gold/BI). Claves: `job_id` y
  `site + job_id` (multi_site). Incluye migración/compactación de lo ya
  acumulado y validación en dev antes de prod.

## Calendario

| Semana | Fechas | Foco | Estado |
|---|---|---|---|
| 1 | 12–18 oct | **Spec 007 (preparación):** recon del workspace, roadmap y documentos | hecha |
| 2–3 | 19 oct – 1 nov | **Spec 007 (código):** bundle, entorno dev, CI y cutover a prod | hecha (cierre formal tras el OK de la persona) |
| 4–6 | 2–22 nov | **Spec 008:** Delta y arquitectura incremental (arranca con el bronce upsert, paso B) | pendiente |
| 7–8 | 23 nov – 6 dic | **Spec 009:** rendimiento y layout físico | pendiente |
| 9 | 7–13 dic | **Spec 010:** streaming y observabilidad | pendiente |
| 10 | 14–20 dic | **Spec 011:** seguridad y gobernanza + repaso | pendiente |
| 11 | 21–27 dic | Simulacros, repaso final y **examen** | pendiente |

Contingencias:

- Si la spec 006 se retrasa más de una semana, la 007 arranca igualmente en modo
  solo-dev (job de desarrollo desplegado desde el bundle, sin tocar el job
  manual de producción).
- Si diciembre va justo, la 011 se comprime a teoría + demo mínima (la
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
