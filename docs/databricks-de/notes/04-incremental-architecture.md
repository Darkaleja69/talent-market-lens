# Arquitectura incremental (silver por grupos semánticos)

Nota de estudio de la **spec 008** (ingesta incremental), cerrada el 2026-11-01.
El bloque B se descopeó a la **spec 010** el 2026-10-28 y se cerró allí
(T-08 dev, T-09 producción). Material de repaso: el diseño completo está en
`specs/008-Delta-Incremental/design-incremental.md` y la medición en
`specs/010-Performance-And-Layout/`.

## Qué he practicado

- **Diseñar antes de programar** (T-07): fijar la unidad de incremental, el
  conjunto afectado, el watermark, la escritura y el plan de pruebas en
  `design-incremental.md` (5 revisiones) antes de tocar una línea de código.
- **Sacar la lógica del notebook a un módulo** (T-08): `Silver_Incremental.py`
  con `key_expression`, `window_bounds`, `changed_since`, `select_candidates`,
  `recompute_groups`, `orphan_markers`, `merge_into_silver` y `run`, y **32
  tests** con Spark local (P1–P21), incluidos el gate de equivalencia (P15b) y la
  comparación de la clave bronce↔silver (P16b).
- **Reestructurar cuatro notebooks sin reescribir negocio** (T-09): celdas
  26→15, 24→14, 19→9 y 18→10, con `prepare_bronze` y `build_offers` y la
  escritura por `inc.run`; el diff tuvo que demostrar que renames, `posted_date`,
  flags, `enrich`, normalización y `drop` eran los mismos.
- **Reproceso por rango y repetición** con widgets (`lookback_days`, `from_date`,
  `to_date`, `force_full`, `repeat_window`) y resumen por
  `dbutils.notebook.exit`, más el procedimiento de backfill en el RUNBOOK (EN/ES).
- **Medir un incremental contra la reconstrucción completa** (T-10 en dev y
  T-09 en producción), con el `MERGE` real: en dev `updated` 55/226/458/121 y en
  producción 316/850/1.788/728, `inserted = deleted = 0` y `target_rows`
  idénticos entre el run completo y el incremental.

## Conceptos clave (en mis palabras)

- **La unidad de incremental es el grupo semántico, no la fila.** Una oferta es
  un grupo `title | company_name | location_city`; si cambia una de sus filas en
  bronce, se recalcula el grupo entero. Así no quedan filas huérfanas cuando una
  oferta migra de ciudad o de título.
- **Clave exacta, sin superconjunto.** La clave se calcula **sobre el bronce**
  con la ciudad normalizada del módulo y el normalizador de empresa de cada
  fuente, y los candidatos se seleccionan por **igualdad exacta**. Se probaron
  superconjuntos y guardas y se descartaron: costaban más de lo que protegían.
- **Conjunto afectado = claves nuevas ∪ claves cuyo `job_id` cambió.** Es la
  unión de lo que entra por bronce y lo que en silver apunta a una clave antigua.
- **Watermark = `max(_ingest_date)` − `lookback_days`.** Si la tabla no tiene
  `_ingest_date`, se reconstruye completa; si está vacía pero con la columna, la
  ventana se abre (`from_date` nulo) y el `MERGE` inserta todo.
- **`_ingest_date` es la fecha del lote, no la de captura.** Esta es la lección
  cara de la spec: una ventana «de 7 días» puede recalcular **casi todo el
  bronce** que se ingirió en esos días (la tabla de bronce es estado, no
  historial), así que el incremental no era pequeño: costaba **6–7×** la
  reconstrucción completa.
- **Escribir por `MERGE` con marcadores.** El pase calcula los grupos y los
  escribe con `MERGE ... WITH SCHEMA EVOLUTION`, sin reinsertar ni reescribir los
  grupos no afectados; los marcadores permiten borrar lo que ya no existe sin
  tocar el resto.
- **Idempotencia se mide por contenido.** Al repetir la ventana, los conteos de
  `affected_groups`, `deleted_groups` o `merge_rows` **pueden bajar** (la primera
  pasada ya limpió los grupos migrados); lo que manda es `target_rows` y los
  conteos de oro/exports.
- **Equivalencia full vs incremental = mismo contenido, no mismos bytes.** Con
  `coalesce(1)` el orden de filas no es determinista; se compara contenido (y se
  ordena de forma total si se quiere algo estable).
- **Sin medir, no hay incremental.** El cuello no era el volumen: eran las
  particiones de `shuffle` (200 → 8) y las pasadas encadenadas. La 010 bajó los
  pases de 249–318 s a **123,8–138,2 s** y el `MERGE` local de 12,4 s a 1,5 s con
  corte de linaje.
- **Oro y `companies_*` se quedan completos.** Su ventana es global y son
  pequeñas: no compensa incrementalizarlas (fuera de alcance, y lo sigue estando).

## Comandos y trucos

```powershell
# Backfill por rango con repetición, sin tocar el comportamiento por defecto
databricks bundle run elt -t dev --profile vs_code `
  --params backfill_from=2026-10-24,backfill_to=2026-10-28,repeat_window=true

# Resumen del notebook (modo, ventana y conteos) sin SQL
databricks jobs get-run-output <task-run-id> --profile vs_code --output json
```

- Los parámetros del job (`backfill_from`, `backfill_to`, `repeat_window`) llegan
  a las cuatro tareas silver con `{{job.parameters.*}}` y **vacíos por defecto**:
  el run programado no cambia de comportamiento.
- `repeat_window` ejecuta la misma ventana dos veces dentro del notebook y
  devuelve `{"repeat_window": true, "runs": [...]}`: idempotencia **sin gastar un
  segundo run**.
- El widget `force_full` es la vía de migración/recuperación; `mode` y
  `mode_reason` del resumen dicen por qué se eligió cada modo
  (`missing_ingest_date`, watermark, rango…).
- Para razonar antes de ejecutar: banco local
  `databricks_notebooks/tests/bench_silver_incremental.py` (31k de bronce / 12k
  de silver, escenarios `full`, `wide` y `small`), con `local[4]` y las mismas 8
  particiones del job.
- `spark.sql.shuffle.partitions = 8` en el clúster del job (medido en la 010):
  menos tareas diminutas, mismo resultado.

## Errores que he cometido y qué aprendí

- **Suponer que la ventana era pequeña.** Tres runs `TIMEDOUT` a los ~30 min (B,
  B2 y B3) por leer `_ingest_date` como fecha de captura. La ventana 25–27
  recalculaba casi todo el bronce. Antes de optimizar hay que saber qué datos
  entran en la ventana.
- **Optimizar a ciegas.** Compartir el `keyed` entre los dos pases (B3) no movió
  el cuello; el aviso de codegen «Code grows beyond 64 KB» era una señal, no la
  causa. Lo arregló la medición por etapa de la 010, no otra corazonada.
- **Mirar el reloj del run completo.** El arranque del clúster son **383–411 s
  (41–47 %)** del run: mezclarlo con el cómputo hace que cualquier comparación
  mienta. Hay que medir por tarea y por paso (`elapsed_s`, `timings_s`).
- **Añadir un `count()` «para comprobar».** Multiplicó por 3 el modo `full`
  (9,8–11,8 min por silver). Quitar esa acción devolvió las silvers a 2,8–3,6 min.
- **Creer que el A/B de exports era byte a byte.** En dev, los runs A y B sobre
  el mismo bronce dieron los mismos conteos y bytes distintos en las dimensiones
  (mismo tamaño, hash distinto); la repetición dentro de B sí dejó los mismos
  bytes. En producción, además, el contenido difirió en una fila por el empate de
  oro. Comparar bytes medía el escritor y el desempate, no la lógica incremental.

## Dudas pendientes

- **Cuándo compensa de verdad el incremental**: con 8 particiones y el corte de
  linaje cabe en la ventana, pero la 010 no demuestra que bata al rebuild en
  todos los casos (depende del tamaño de la ventana, es decir, de cuántos lotes
  entren).
- **CDF en bronce** como alternativa a derivar el watermark de `_ingest_date`:
  descartado en la 008 por coste y alcance; queda abierto si hace falta historial.
- **Incremental de oro y de `companies_*`**: fuera de alcance; hoy se
  reconstruyen completos.
- **Borrado de ofertas que desaparecen del landing**: el marcador borra lo que ya
  no está en el bronce, pero no hay expiración técnica de ofertas (decisión de la
  008: la antigüedad es un filtro de negocio en oro/BI).
- **Coste real por paso con las cuatro silvers en paralelo**: medido por tarea en
  la 010, no desagregado por contención del clúster.
