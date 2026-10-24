# Cutover de producción — spec 008 (bloque A)

Procedimiento de **T-06** para llevar a producción el bronce deduplicado
(`foreachBatch` + MERGE) y la one-off que normaliza el histórico. Sigue el
mecanismo de la 007 (`specs/007-ELT-As-Code/cutover.md`) y la cadencia de la
009 (job semanal, domingo 23:00 `Europe/Madrid`).

## Por qué la ventana importa

- El job de producción corre **una vez por semana** (domingo 23:00) y el
  dashboard publica el lunes 01:00 UTC. Un fallo semanal deja la web con datos
  viejos (y no despliega pasados 8 días): no hay «reintentar mañana».
- La one-off de prod debe ejecutarse **antes del primer lote del ingest nuevo**:
  si el job ingiere primero, el MERGE actualiza las copias históricas y la
  one-off ya no puede distinguirlas con los conteos previos.
- Con `max_concurrent_runs: 1`, una ejecución manual no se solapa con la
  programada, pero sí puede adelantar el lote. Por eso la ventana es en sábado
  (o antes del domingo) y el job se deja **pausado** durante la one-off.

## Requisitos previos

- T-05 cerrada: ciclo dev en verde, 1 fila por clave e idempotencia demostrada.
- CI del bundle en verde (`databricks bundle validate -t prod`).
- Ventana acordada con la persona y **sin runs manuales** en curso.
- `RESTORE` a mano: la one-off imprime la versión previa de cada tabla.

## Pasos

1. **Pausar producción.** En `databricks.yml` (target `prod`),
   `pause_status: PAUSED`; commit; `databricks bundle deploy -t prod --profile <perfil>`.
   Comprobar con `databricks bundle summary -t prod --profile <perfil>`.
2. **Desplegar el código nuevo.** El mismo deploy del paso 1 sube
   `bronze_ingest.ipynb` (upsert), `oneoff_dedupe_bronze.ipynb` y el job
   `dedupe_oneoff`. El deploy **no ejecuta** nada.
3. **One-off de prod.**
   `databricks bundle run dedupe_oneoff -t prod --profile <perfil>`
   (~7 min, ~0,05–0,13 €). Ejecuta las cuatro fuentes en cadena. Anotar:
   id del run, conteos después y versiones de `RESTORE` de las cuatro tablas
   (el resumen JSON de cada tarea se lee con
   `databricks jobs get-run-output <task_run_id>`).
4. **Comprobar el bronce de prod** con la salida de la one-off: una fila por
   clave y filas sin clave conservadas. Comparar con los conteos previos
   (a 22-oct: indeed 9.552/2.959, infojobs 45.044/2.587, linkedin 17.080/14.084,
   multi_site 12.159/11.086) y anotar el efecto del run del 23-oct.
5. **Reanudar producción.** `pause_status: UNPAUSED`; commit;
   `databricks bundle deploy -t prod --profile <perfil>`.
6. **Comprobación real: el run programado del domingo.** No se lanza un run
   manual. Verificar el lunes:
   - run del domingo en verde, 8/8 tareas y `Bronze_Ingestion` con el sink nuevo;
   - `gold/web_export` y `gold/pbi_export` actualizados (`meta.json` con la fecha
     del domingo y conteos);
   - dashboard publicado el lunes con `data_date` correcto;
   - SQL warehouse `STOPPED` (ninguna consulta del informe).
7. **Bitácora** en `docs/databricks-de/roadmap.md` y fila en `evidence.md` con
   ids, tiempos, conteos y coste.

## Rollback

| Fallo | Acción |
|---|---|
| El deploy va mal o el run del domingo falla por el código nuevo | Redeploy del commit anterior del bundle (procedimiento de la 007) y pausa si hace falta |
| La one-off deja una tabla inconsistente | `RESTORE TABLE <tabla> TO VERSION AS OF <n>` con la versión que imprimió la one-off (es SQL: única excepción al presupuesto, una ventana de 10 min, ~0,43 €, aprobada por la persona) |
| El job semanal falla y el lunes no publica | El dashboard sigue con los datos de la semana anterior y su fecha; corregir y esperar al domingo siguiente |

El job manual pausado (`704156530681034`) se conserva como vía de vuelta, igual
que en la 007.

## Coste

- One-off de prod: ~0,05–0,13 € (run corto, clúster `D4ds_v4`).
- Run del domingo: el semanal ya presupuestado en la 009 (~0,24 €/run).
- Sin runs manuales extra y sin despertar el SQL warehouse.
