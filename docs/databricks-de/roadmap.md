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

## Calendario

| Semana | Fechas | Foco | Estado |
|---|---|---|---|
| 1 | 12–18 oct | **Spec 007 (preparación):** recon del workspace, roadmap y documentos | en curso |
| 2–3 | 19 oct – 1 nov | **Spec 007 (código):** bundle, entorno dev, CI y cutover a prod | pendiente |
| 4–6 | 2–22 nov | **Spec 008:** Delta + arquitectura incremental | pendiente |
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

- **Formato de tarea:** objetivo → preguntas guía → pistas y documentación →
  hecho cuando → evidencia. La persona implementa; el asistente pregunta,
  orienta y revisa (tests y revisión independiente del diff antes del commit).
- **Commits atómicos** por tarea (`T-XX`); checkpoint cada ~5 tareas; al cerrar
  cada spec, comprobación real y revisión, según `AGENTS.md`.
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
