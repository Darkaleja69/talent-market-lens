# Plan técnico: rendimiento del incremental, layout físico y compute

> Borrador del **2026-10-28**, escrito tras bloquear T-10 de la 008. La rama
> sale de `spec/008-delta-incremental` (`af22467`) y se rebasa sobre `main`
> cuando la 008 se fusione o se descopee.
>
> Regla de oro: **ningún run hasta que el banco local y la Spark UI digan dónde
> se va el tiempo**. B3 ya costó 0,24 € por probar una hipótesis (compartir
> `keyed`) que no era el cuello.

## 1. Objetivo y límites

Dejar el incremental de las cuatro silvers **por debajo de la reconstrucción
completa** con la misma carga y **muy por debajo** con ventana pequeña, validar
el bloque B (equivalencia, idempotencia, `MERGE` real) y decidir el compute y el
layout con medidas. No se cambia la semántica del incremental, ni los contratos,
ni la cadencia.

## 2. Estado de partida

### 2.1 Runs del bloque B

| Run | Caso | Tareas | Resultado | Silvers | Escrituras |
|---|---|---|---|---|---|
| `263658658061220` (A) | migración `full` con `count()` de más | 8/8 | SUCCESS, 22 m 18 s | 9,8–11,8 min | sí |
| `186230462263213` (B) | backfill 24–25 + `repeat_window` | silvers cortadas | TIMEDOUT 30 m 46 s | ~27 min | **no** |
| `207866361330883` (A2) | migración `full` en una pasada | 8/8 | SUCCESS, 14 m 39 s | 2 m 47 s–3 m 46 s | sí |
| `1056455747512203` (B2) | backfill 25–27 + `repeat_window` | silvers cortadas | TIMEDOUT 30 m 37 s | sin duración registrada | **no** |
| `1015448868873733` (B3) | B2 con `keyed` compartido | silvers cortadas | TIMEDOUT 30 m 30 s | 22 m 04 s | **no** |

La columna **Silvers** es la **duración de la tarea** de silver (no un tiempo
run-relative): en B3, bronce 8 m 06 s + 22 m 04 s ≈ 30 m 10 s, a 20 s del total
del run; en B, 3 m 29 s + 27 min ≈ 30 m 29 s, a 17 s. B2 no la registró.

Datos de contexto: **bronce** dev ~31k filas (one-off: 3.039 / **2.617** / 14.289 /
11.096) y **silver** en A2: 3.096 / 2.527 / 12.504 / 10.068 (la diferencia es la
evolución de dev, no una fuente distinta). El 27-oct el bronce de indeed ingirió
**190 filas**, pero `_ingest_date` es la **fecha del lote**, así que la ventana
de 7 días abarca casi todo el bronce. Job `D4ds_v4` single node
(4 vCPU / 16 GiB), `CLASSIC_PREVIEW`, `17.3.x-scala2.13`, Photon fuera
(`runtime_engine: STANDARD`), `performance_target: PERFORMANCE_OPTIMIZED`,
`timeout_seconds: 1800`, `max_concurrent_runs: 1`, cuatro silvers en paralelo.

### 2.2 Inventario de acciones de `_run_once` (camino incremental)

Cada fila es una acción de Spark (un *job*) o un punto donde se materializa un
frame cacheado:

| # | Paso | Acción | Coste |
|---|---|---|---|
| 1 | `window_bounds` | `tableExists` + `read` + `max(_ingest_date)` | 1 job corto |
| 2 | `keyed` (empresa + ciudad + `_key`) + `cache()` | se materializa en la primera acción | pasada ancha sobre bronce |
| 3 | `changed` (filtro de ventana) + `cache()` | ídem | pasada ancha |
| 4 | `changed.isEmpty()` | job con `limit` | corto |
| 5 | `keys_new.distinct()` / `target ⋈ changed_ids` / `keys_old` / `affected.distinct()` | shuffles | medio |
| 6 | `candidates` (semi-join por `_key`) | shuffle | medio |
| 7 | **`recompute = transform(candidates)` + `cache()`** | cadena de negocio + `enrich` | **el caro** |
| 8 | `require_no_null_ingest_date` (`isEmpty`) | 1.ª pasada sobre `recompute` | caro si no hay caché |
| 9 | `require_unique_groups` (`groupBy(_key).count()`) | shuffle + 2.ª pasada | caro |
| 10 | `recompute_keys.distinct()` | 3.ª pasada | caro |
| 11 | `markers` (`target ⋈ affected` **left_semi** ⟕ `recompute_keys` **left_anti**) | 4.ª lectura de `recompute` | caro |
| 12 | `merge_src` (proyección + union con marcadores) | 5.ª lectura | caro |
| 13 | los 5 `count()` (`changed`, `affected`, `recompute`, `markers`, `merge_src`) | 3 pasadas nuevas sobre caché + 2 que ya existían | medio si la caché aguanta |
| 14 | **`MERGE WITH SCHEMA EVOLUTION`** sobre vista temporal | shuffle + escritura | **no medido** |
| 15 | `target.count()` tras el MERGE | 1 job | corto |

`keep()` cachea **seis frames** por silver (`changed`, `affected`, `recompute`,
`recompute_keys`, `markers`, `merge_src`) y `keyed` se cachea aparte: **siete en
total**. Con caché efectiva, la cadena de negocio se paga una vez; sin ella
(evicción por memoria compartida entre cuatro silvers), se paga una vez por cada
acción que la necesite. La reconstrucción completa la paga **una sola vez** y no
tiene MERGE: esa es la diferencia que hay que medir.

### 2.3 Lo que ya se sabe y lo que no

- Se sabe: el modo `full` cuesta 2 m 47 s–3 m 46 s por silver con el bronce
  completo y funciona; el incremental no termina: las silvers de B3 llegaron a
  **22 m 04 s** de tarea y las de B a **~27 min** antes del `TIMEDOUT`.
  Compartir `keyed` entre los dos pases de `repeat_window` no bastó (B3): el
  parche no llegó a ejecutarse porque ninguna silver terminó el primer pase.
- Se sabe: `_ingest_date` es la fecha del lote, así que la ventana de 7 días
  **cubre casi todo el bronce** en dev. B2/B3 no son un caso incremental
  pequeño: son el peor caso (recalcular casi todo). Eso no excusa el coste,
  pero cambia la lectura: el incremental debe costar como un rebuild **como
  máximo** en ese escenario.
- No se sabe: cuánto pesa el volumen, el codegen, la falta de caché, la
  concurrencia de cuatro silvers en 4 vCPU / 16 GiB y el `MERGE`. Eso es
  exactamente T-01, y hasta entonces no se afirma ninguna causa.

## 3. Criterio de aceptación (medible)

Con `T_full` = **3 m 46 s** (extremo superior de A2, por silver) y `T_inc` =
tiempo de la misma silver en modo incremental con la ventana cubriendo el bronce:

- **RF-2a:** `T_inc ≤ 3 m 46 s` (ventana que cubre casi todo).
- **RF-2b:** con ventana ≤1 % del bronce, `T_inc ≤ 75 s` (un tercio del mismo
  extremo).
- **RF-3:** run completo en dev ≤ **22 m 30 s** (25 % de margen sobre 1800 s) y,
  con lote semanal, sin acercarse al timeout.
- **RF-4/5:** exports A/B **idénticos en contenido** (mismos conteos y mismos
  `sha256` en `files` de `meta.json`, con la escritura ordenada de forma total
  por todas las columnas), `MERGE` con escrituras y repetición de la ventana sin
  cambio de contenido.

El criterio se mide con el resumen instrumentado (T-02), no con relojes
externos: el arranque del clúster (2–9 min según el run) no mide el código.

## 4. Hipótesis y experimentos (ordenados por coste)

| # | Hipótesis | Señal esperada | Medición (coste) | Si se confirma |
|---|---|---|---|---|
| H1 | **Codegen**: la proyección de `build_offers`/`enrich` supera 64 KB y cae a interpretado; el aviso «Code grows beyond 64 KB» lo delata | stage de `recompute` dominante, sin shuffle, con tiempo de compilación alto | `explain("codegen")`, tamaño del código generado, tiempos locales con la misma fixture (0 €) | partir la cadena en 2–3 proyecciones con `localCheckpoint`/`persist`, o reducir expresiones; medir antes/después |
| H2 | **Pasadas y caché**: `recompute` se materializa hasta 5–6 veces y la caché no aguanta 4 silvers en 16 GiB | storage tab con fracción cacheada baja, *spill* a disco, «Recompute» en el DAG | Spark UI de B2/B3 (0 €) + resumen por pasos instrumentado (1 run, ya previsto) | quitar acciones (métricas de Delta en vez de `count()`), `persist(MEMORY_AND_DISK)`/`localCheckpoint`, y decidir concurrencia |
| H3 | **MERGE**: el `ON` es una expresión calculada a los dos lados, sin `_key` almacenada; no hay poda por estadísticas y el destino se reescribe entero | stage del MERGE con shuffle completo y `numTargetRowsCopied` alto | `explain` del MERGE + `operationMetrics` de Delta (lectura en el job, 0 €) | valorar `_key` materializada en silver o tabla de stage; **decisión de contrato con la persona** |
| H4 | **Concurrencia y compute**: cuatro jobs independientes comparten 4 vCPU y 16 GiB; los stages vistos tienen **200 tareas** cada uno | 200 tareas por stage, ~0,7–0,9 s por tarea, duración proporcional al nº de tareas y no a los bytes | Spark UI de B2/B3 (0 €) + tasks/GC/memoria de un stage | reducir tareas por acción (particiones) y decidir concurrencia y compute (T-06) |
| H5 | **Ventana**: en dev el watermark hace que el incremental recalcule casi todo; el banco debe separar «ventana ancha» y «ventana pequeña» | `changed_rows` ≈ filas de bronce | resumen del run **B de T-08** (1 run) y cálculo local del watermark | fijar dos escenarios de benchmark y no extrapolar del caso ancho |
| H6 | **Layout**: los `MERGE`/`overwrite` dejan ficheros pequeños; a 3k–12k filas no debería ser el cuello | `numFiles` alto, `sizeInBytes` bajo | `DESCRIBE DETAIL` en el clúster del job (céntimos) o en un run ya previsto | decidir `OPTIMIZE`/layout **solo** si la medida lo justifica; documentar el «no cambiar» |

Orden de trabajo: H1, H2 y H6 son gratis (Spark UI + local); H3, H4 y H5 se
confirman en el run A/B de la fase de validación **sin gastar un run extra**:
las métricas del `MERGE` y de las tareas se leen en B, y el layout en A.

## 5. Instrumentación (sin pasadas extra)

- **Tiempos por acción, no por línea:** los pasos son perezosos, así que
  `monotonic()` alrededor de una línea mide ~0 s. Cada acción se etiqueta con
  `spark.sparkContext.setJobGroup(<paso>, <paso>)` y un `SparkListener`
  acumula `onJobEnd`/stages por grupo; el resumen devuelve `timings_s` por grupo
  (incluido el `MERGE`, que corre como SQL en el mismo grupo). No añade
  acciones: solo etiqueta las que ya existen.
- **Métricas del `MERGE`:** `numTargetRowsInserted`, `numTargetRowsUpdated`,
  `numTargetRowsDeleted`, `numTargetRowsCopied`, `numFilesAdded` y
  `numOutputRows`, leídas con `DESCRIBE HISTORY` **en el clúster del job**
  (gratis, no despierta el warehouse). `deleted_groups` y `merge_rows` pasan a
  salir de ahí; `target_rows` necesita un `count()` (se mantiene).
- **Qué conteos se quedan:** `changed_rows`, `affected_groups` y
  `recomputed_groups` no tienen equivalente en Delta y siguen siendo `count()`
  sobre frames ya cacheados. El objetivo de T-02 es bajar de **11 acciones de
  código (12–14 consultas en la UI)** a ≤6 sin perder ningún campo del resumen.
- **Contrato del resumen:** los 16 campos actuales se conservan; se añaden
  `timings_s` y `merge_metrics` (aditivo, sin romper a quien lo lea).
- **Evidencia:** `jobs get-run-output` + `meta.json` + SHA-256 de los Parquet.
  Cero consultas al SQL warehouse.

## 6. Optimizaciones candidatas (solo si el diagnóstico las justifica)

1. **Particiones de *shuffle*** (H4, P1 tras T-01): `spark.sql.shuffle.partitions`
   de 200 a 8–16 y leer la config efectiva y AQE. Convierte cada *exchange* de
   200 × 0,7 s ≈ 140 s en 8–16 × 0,7 s ≈ 6–11 s y baja las tareas por silver de
   ~2.700 a ~150–250. **Medido el 2026-10-28** (`design-performance.md` §8): el
   run pasa de `TIMEDOUT` (>22 min por silver) a **974 s con dos pases**
   (4–5 min por pase), cabe en la ventana; aún no bate al rebuild (RF-2a), así
   que quedan P2/P3. En T-03 se experimenta; en T-06 se fija la config
   definitiva (`.yml` del clúster o código) y se aprueba.
2. **Coste por tarea y codegen** (H1): partir la proyección de `build_offers`
   (enrich incluido) en etapas con una frontera materializada; alternativa:
   reducir expresiones anidadas. Se mide con `explain("codegen")` y con el
   tiempo del pase.
3. **Menos acciones sobre `recompute`** (H2, **bajo valor**: A2 hace más
   consultas y es más rápido): quitar los `count()` redundantes, tomar los del
   `MERGE` de Delta y no recalcular el frame para los marcadores si la clave se
   puede proyectar antes.
4. **Caché con criterio** (H2): `MEMORY_AND_DISK` o `localCheckpoint(eager=True)`
   en `recompute`, `unpersist` explícito tras el MERGE (ya existe), y **no**
   cachear frames estrechos que se leen una vez.
5. **MERGE** (H3): materializar `_key` en silver (columna interna, excluida de
   oro y exports por proyección explícita) o una tabla de stage por run; el
   `ON` pasa a igualdad de columna. **Requiere aprobación de contrato** y
   `RESTORE` disponible. Si no compensa, se documenta y se queda la expresión.
6. **Concurrencia** (H4): silvers en secuencia vs paralelo; depende de H4 y del
   compute. Cambiar el grafo del job exige aprobación del `.yml`.
7. **Timeout**: **no** se sube como solución. Si el resultado no cabe con
   margen, el problema sigue abierto.

Cada cambio va con tests (equivalencia y contrato) y con la medición
antes/después en el mismo banco.

## 7. Layout físico y compute

### 7.1 Layout

- Medir por tabla: `numFiles`, `sizeInBytes`, tamaño medio de fichero, y
  versiones/commits del bloque B.
- Decidir `OPTIMIZE` (compactación), `liquid clustering` (por qué columna y con
  qué evidencia) o **no cambiar**. El tamaño (3k–12k filas) hace probable el «no
  cambiar»; se documenta igual.
- Si se toca el layout, se mide el efecto en las consultas de oro y en los
  exports (no solo en silver).

### 7.2 Compute (decisión final, venida de la 009)

| Opción | Perfil | Coste relativo/run | Nota |
|---|---|---|---|
| `D4ds_v4` single node (actual) | 4 vCPU / 16 GiB | ~0,24 € | línea base; 4 silvers comparten nodo |
| `D2ds_v6` single node | 2 vCPU / 8 GiB | ~0,13 € | **no completó** en la 009 con 4 silvers en paralelo |
| `D8ds_v4` single node | 8 vCPU / 32 GiB | ~0,48 € | desbloquea CPU/memoria; duplica coste |
| Multi-nodo (driver + workers) | p. ej. 1–2 workers | según nodos | concurrencia real; más superficie y coste |
| Serverless (jobs) | gestionado | DBU superior | spike pendiente; practicar en Free Edition |

La decisión se toma con la medición de T-07/T-08 (tiempo, memoria, spill) y el
coste por run; se documentan las descartadas y el motivo. **Sin runs extra para
comparar compute** salvo aprobación explícita (se usaría el run de reserva).

## 8. Validación y producción

- **Run A (full):** con el código optimizado, 8/8, silvers ≤ línea base
  (2 m 47 s–3 m 46 s), exports escritos con `meta.json` válido y SHA-256
  registrado. Es la nueva referencia A (la de A2 no vale si el código cambia).
- **Run B (incremental + `repeat_window`):** mismo lote, backfill sobre bronce
  existente; debe ejecutar el `MERGE`, dejar `timings_s` por etapa y escribir.
  **El run B de dev se ejecutó antes del escritor ordenado**, así que la
  equivalencia de contenido de sus exports no se puede cerrar ahí: el gate de
  RF-4 **pasa a T-09** (dos runs manuales de producción sobre el mismo bronce,
  A `full` y B incremental). Si el run no cabe, el resumen dice exactamente en
  qué paso y se corrige en local antes de gastar el run de reserva.
- **Producción:** deploy con el `cutover.md` de la 007 (pausa, deploy,
  reanudar, rollback y `RESTORE`). **Decisión del 2026-10-31:** la validación
  A/B se hace con **dos runs manuales el mismo día y sobre el mismo bronce**
  (A `force_full=true`, B incremental con `backfill_from`/`backfill_to` y
  `repeat_window=true`), porque el run programado del domingo ingiere un lote
  nuevo y rompe la equivalencia de contenido. El run programado de ese domingo
  se **pausa** y se reanuda el lunes siguiente; el dashboard se publica con
  `workflow_dispatch` tras B y el cron del lunes lo republica. SQL warehouse
  `STOPPED` antes y después.

## 9. Presupuesto

| Tarea | Runs de dev | Coste |
|---|---|---|
| T-01 diagnóstico (Spark UI + local) | 0 | 0 € |
| T-02 instrumentación + banco local | 0 | 0 € |
| T-03 optimizaciones de código | 0 | 0 € |
| T-04 MERGE (local + revisión de contrato) | 0 | 0 € |
| T-05 layout | 0 | 0 € |
| T-06 compute (decisión con T-07/T-08) | 0 | 0 € |
| T-06b `sha256` de los exports en `meta.json` | 0 | 0 € |
| T-07 run A (full) | 1 (**nuevo**, aprobado el 2026-10-31) | ~0,24 € |
| T-08 run B (incremental + repetición) | 1 (**nuevo**, aprobado el 2026-10-31) | ~0,24 € |
| Reserva (reintento o comparación de compute, con aprobación) | 1 (**no autorizado**) | ~0,24 € |
| Producción (T-09) | 2 runs manuales de producción (A/B) | ~0,40–0,46 €; el programado de esa semana se pausa |
| **Total** | **5** (3 originales, **ya gastados** en T-03/T-03b, + 2 ampliados) | **~1,08–1,10 €** (techo 1,8 €; 0 SQL) |

Reglas y encaje con la 009:

- La 009 reserva a esta spec **2 runs de dev + 1 sesión SQL ≤ 1,8 €**. Reparto
  **aprobado el 2026-10-28**: **3 runs y 0 SQL**. Renunciar a la sesión ahorra
  ~1,29 € y el run extra cuesta ~0,24 €, así que el gasto previsto (~0,72 €)
  queda **por debajo del techo de 1,8 €**.
- **Historial — ampliación aprobada el 2026-10-31:** **2 runs más** para **T-07**
  (run A) y **T-08** (run B). Los 3 runs del reparto del 2026-10-28 **ya se
  gastaron** en T-03 A/B (`485578578869927`, 808 s; `269508392317481`, 974 s) y
  T-03b (`879269828668729`, 927 s), más un intento abortado (~0,62 € en total).
  La validación A/B de T-07/T-08 **no se puede hacer con esos runs porque el
  código cambió en T-03b**: el total pasa a **5 runs** con **~1,08–1,10 €**
  previstos y **0 SQL**, dentro del techo de 1,8 €; el run de reserva **sigue no
  autorizado**.
- **Desviación aprobada el 2026-10-31:** la validación A/B de T-09 se hace con
  **2 runs manuales de producción sobre el mismo bronce**; el run programado
  del domingo se pausa y se reanuda el lunes siguiente. Coste **~0,40–0,46 €**,
  dentro del presupuesto de producción de la 009; **no** consume el techo de
  dev de 1,8 €.
- El bloque B de la 008 se fue a **~1,6 €** (5 runs por duración) y la 008 queda
  en ~2,07 € frente a su techo de 2,0 €, con la sesión SQL renunciada: desvío
  marginal que se confirma en la Account Console y **no** consume el techo de la
  010.
- Un run fallido por defecto de código se corrige **en local** antes de
  relanzar y consume presupuesto.
- **0 sesiones SQL.** Cualquier consulta al warehouse exige aprobación aparte.
- El run de reserva no se gasta sin decisión de la persona.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Optimizar a ciegas y gastar runs | T-01 gratis antes de tocar código; el run B no se lanza hasta que el banco local pase el criterio |
| El diagnóstico apunta a memoria/concurrencia y se «arregla» subiendo el clúster | El compute se decide en T-06 con coste y medida; subir nodo no puede ser la única respuesta si el incremental sigue costando como un rebuild |
| `_key` materializada rompe el contrato | Columna interna excluida por proyección explícita; `RESTORE` disponible; aprobación previa del diff y de la migración |
| La instrumentación añade pasadas (como el `count()` del modo `full`) | Se etiquetan las acciones existentes con `setJobGroup` y un `SparkListener`; las métricas del MERGE salen de Delta; el resumen no puede lanzar acciones nuevas (test de contrato) |
| Subir el timeout y dar el problema por resuelto | Prohibido: el gate es 22 m 30 s en dev |
| Dev no reproduce el caso de prod (ventana ancha por datos jóvenes) | Dos escenarios de benchmark (ventana ancha y ventana ≤1 %) y anotar el tamaño del lote en cada run |
| Rebasar la rama sobre `main` tras la 008 genera conflictos | Se rebasa en cuanto la 008 se fusione; el trabajo de código empieza después del rebase |
| El primer run de prod (full) se acerca al timeout | Se mide en A y A2 (14–22 min); con el código optimizado debe quedar por debajo con margen |
| Domingo ocupado o fallido | El dashboard no publica si `data_date` supera 8 días; se corrige y se espera al domingo siguiente |
| El reparto de presupuesto de la 009 (2 runs + 1 SQL) no coincide con esta propuesta (3 runs + 0 SQL) | **Resuelto el 2026-10-28:** aprobado 3 runs + 0 SQL dentro del techo de 1,8 € |
| El bloque A de la 008 (T-06) sigue sin comprobación registrada | **Confirmado en verde el 2026-10-28**; falta anotar id y tiempos en `specs/008-*` antes de cerrar la 008 |

## 11. Secuencia

1. **Diagnóstico (T-01):** Spark UI de B2/B3, inventario de acciones, banco
   local y `design-performance.md` con la atribución del coste. Revisión
   independiente.
2. **Instrumentación y banco (T-02):** tiempos por etapa y métricas del MERGE,
   sin acciones extra; línea base local reproducible.
3. **Optimización (T-03/T-04):** aplicar solo lo que el diagnóstico justifica;
   tests de equivalencia y de contrato; diff aprobado.
4. **Layout y compute (T-05/T-06):** decidir con medidas, incluido el «no
   cambiar».
5. **Validación (T-07/T-08):** run A y run B; tiempos, `MERGE` e idempotencia.
6. **Equivalencia de exports (T-09):** comparación A/B de `meta.json` (`files`
   contra `previous_files`) con la escritura ordenada, en producción.
7. **Producción y cierre (T-09/T-10):** A/B manual en producción sobre el
   mismo bronce, pausa del run programado de ese domingo, dashboard por
   `workflow_dispatch`, notas y roadmap.
