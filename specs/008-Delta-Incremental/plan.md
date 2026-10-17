# Plan técnico (borrador): bronce deduplicado e ingesta incremental

> Borrador para el **bloque A**; se completa al arrancar la spec.

## 1. Objetivo y límites

Bronce = estado actual por oferta (upsert idempotente); el landing y las
silvers no cambian en este bloque. El bloque B (silvers/gold incrementales) se
planificará después.

## 2. Estado de partida

- `bronze_ingest.ipynb`: Auto Loader (`cloudFiles`) por fuente con checkpoint
  y `mergeSchema`, escritura append (`.toTable`).
- Diagnóstico 2026-10-17 (evidencia de la 007): duplicación por snapshots en
  linkedin (9,57M filas para 13.376 ofertas) y multi_site (225.796 frente a
  ~11,5k efectivas en prod); indeed e infojobs sin duplicación.
- Bundle dev/prod de la 007 como vía de despliegue y validación.

## 3. Arquitectura objetivo

```text
landing (cruda, snapshots acumulativos)
  → Auto Loader (checkpoint intacto)
  → foreachBatch: dedup del lote por clave + MERGE a bronze (upsert)
  → silvers (sin cambios en este bloque)
```

## 4. Diseño del upsert (a fijar en T-01)

- Clave por fuente: `job_id`; `site + job_id` en multi_site.
- Condición de actualización: la fila entrante gana si su `scraped_at` es más
  reciente; si no, se ignora (ficheros viejos re-subidos no rompen nada).
- Filas con clave nula: conservar o cuarentenar (decidir y probar).
- Esquema: `mergeSchema` del stream y evolución controlada en el MERGE;
  `_rescued_data` intacto.

## 5. Migración

- **dev:** reconstruir con la lógica nueva (drop y reingesta desde landing) o
  compactación one-off; validar conteos por fuente.
- **prod:** ventana acordada; rollback = volver a la versión anterior del
  notebook (el transaction log conserva la historia de la tabla).

## 6. Comprobaciones

- Tests del módulo (pytest + Spark local): upsert, idempotencia, orden.
- Ciclo dev en verde con conteos e idempotencia demostrada.
- Suites existentes en verde.
- Prod: ventana acordada y noche real verificada.

## 7. Riesgos (borrador)

| Riesgo | Mitigación |
|---|---|
| MERGE con esquema evolucionando | Probar con `mergeSchema` y columnas nuevas |
| Llegada desordenada de ficheros | Condición por `scraped_at` y tests |
| Migración de tablas grandes en dev | Hacerla en dev primero y medir |
| Cambio en prod antes del cutover | Usar el flujo de la 007 (CI y ventana con rollback) |

## 8. Secuencia

1. **Bloque A:** T-01–T-06 (este documento).
2. **Bloque B:** silvers/gold incrementales (por detallar).
