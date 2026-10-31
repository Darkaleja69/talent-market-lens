# Especificación funcional: rendimiento del incremental, layout físico y compute

> **Borrador (2026-10-28).** Nace del bloqueo de **T-10** de la spec 008: el
> incremental de las cuatro silvers está implementado y con los tests en verde,
> pero **no cabe en la ventana de producción** (tres runs `TIMEDOUT` a los
> ~30 min sin escribir nada). Esta spec mide el coste real, lo optimiza con
> evidencia y repite la validación funcional del bloque B; el roadmap le asigna
> además el **dominio 1** (Spark performance, layout de Delta) y la **decisión
> final de compute** que la 009 dejó abierta.
>
> **Base de la rama:** `spec/010-performance-and-layout` se crea desde
> `spec/008-delta-incremental` (commit `af22467`) porque el código del
> incremental **aún no está en `main`**. Al fusionar o descopear la 008, esta
> rama se rebasa sobre `main` (`git rebase --onto main spec/008-delta-incremental
> spec/010-performance-and-layout`) y el plan se revisa antes de tocar código.

## Contexto y objetivo

La 008 convirtió el bronce en estado actual y las cuatro silvers en
incrementales por grupo semántico. La migración (modo `full`) funciona: run A2
`207866361330883`, **14 min 39 s**, 8/8 tareas, silvers en **2 m 47 s–3 m 46 s**.
El incremental no:

| Run | Caso | Resultado | Tiempo |
|---|---|---|---|
| `186230462263213` (B) | backfill 24–25, `repeat_window` | **TIMEDOUT**, sin escrituras | 30 m 46 s |
| `1056455747512203` (B2) | backfill 25–27, `repeat_window` | **TIMEDOUT**, sin escrituras | 30 m 37 s |
| `1015448868873733` (B3) | B2 + `keyed` compartido | **TIMEDOUT**, sin escrituras | 30 m 30 s |

El bronce dev son ~31k filas y el 27-oct entraron **190** en indeed; aun así el
incremental tarda **≈6–7×** lo que la reconstrucción completa de la misma tabla
y no termina. Ojo a la lectura: `_ingest_date` es la **fecha del lote**, no la
de captura, así que la ventana de 7 días recalcula casi todo el bronce y B2/B3
son el **peor caso**, no un cambio pequeño. La producción tiene
`timeout_seconds: 1800` y el run semanal ya gasta ~19 min: **el mecanismo, tal
cual, no es desplegable**, y el `MERGE` real de Delta sigue sin medirse.

**Objetivo (resultado único):** que el incremental de silver **quepa en la
ventana de producción con equivalencia demostrada** —medido, más barato que
reconstruir, y con el bloque B validado de punta a punta—. La decisión de
compute y la de layout físico son **condiciones de cierre** de ese resultado
(la 009 difirió el compute a esta spec y el dominio 1 exige el layout medido),
no objetivos paralelos.

Y el encaje con el examen: el dominio 1 (shuffles, joins, broadcast, skew,
particionado, tamaño de ficheros, *query plans*, Spark UI, Query Profile,
*data skipping*) se practica **sobre este problema real**, no con juguetes.

## Herencia de la 008 (2026-10-28)

La 008 **no se reabre**: se hereda su estado y se respeta su diseño.

- **Lo cerrado se queda cerrado:** T-01–T-09 (diseño, módulo, notebooks en dev,
  runbook). Esta spec no cambia la semántica del incremental ni el contrato de
  negocio de silver, oro o exports.
- **El bloque A queda pendiente de registro:** T-06 se confirmó en verde el
  2026-10-28 (run programado del domingo 25-oct y dashboard del lunes), pero su
  id y sus tiempos aún no están anotados en `evidence.md`. No es de esta spec,
  pero condiciona el cierre de la 008.
- **T-10 queda bloqueada** por rendimiento: B, B2 y B3 `TIMEDOUT` sin
  escrituras; las cuatro silvers siguen en la versión de A2 (conteos 3.096 /
  2.527 / 12.504 / 10.068).
- **T-11 no se toca** hasta que esta spec deje el incremental por debajo de la
  ventana de 1800 s y repita la validación A/B. El procedimiento de producción
  sigue siendo el `cutover.md` de la 007.
- **Decisión (2026-10-28): el bloque B se descopea de la 008.** T-10 y T-11
  pasan a esta spec: la validación A/B es T-08 (**superado el 2026-10-31**: la
  equivalencia de exports se valida en T-09; T-08 mide tiempo, `MERGE` e
  idempotencia) y el despliegue a producción es T-09. La 008 se cerrará con el
  bloque A, previo registro de la comprobación real de T-06.
- **Coste:** el bloque B gastó **~1,6 €** (5 runs por duración) y la 008 queda en
  ~2,07 € frente a su techo de 2,0 €, con la sesión SQL renunciada: desvío
  marginal, sin presupuesto extra para esta spec.

## Decisiones (2026-10-28)

- **Diagnóstico antes de optimizar.** No se lanza ningún run hasta tener el
  coste por etapa y por operador medido (Spark UI de B2/B3, plan y
  `explain`, banco local). Se acabaron las hipótesis: B3 ya demostró que
  compartir `keyed` no bastaba.
- **El incremental debe ser más barato que reconstruir.** Es el invariante de
  diseño, no un extra: con la misma carga (ventana que cubre casi todo el
  bronce) no puede superar el tiempo del modo `full`; con ventana pequeña debe
  ser al menos 3× más rápido. Un incremental que cuesta como un rebuild no
  justifica el mecanismo.
- **La ventana de producción manda.** El criterio no es «algún día termina»:
  el run completo debe caber con **≥25 % de margen** sobre los 1800 s y anotar
  el tamaño del lote.
- **Compute con evidencia.** `D4ds_v4` single node sigue siendo la base hasta
  que una medición justifique cambiarla (`D2ds_v6` no completó la 009; el spike
  de serverless está pendiente). Ningún cambio de compute sin números.
- **Layout: decidir, aunque sea «no cambiar».** A 3k–12k filas, `OPTIMIZE` y
  `liquid clustering` pueden no pagar; se miden tamaño y número de ficheros y se
  documenta la decisión con su motivo.
- **Observabilidad sin SQL.** Tiempos por acción y métricas del `MERGE` salen
  del resumen del notebook (`dbutils.notebook.exit`) y de las
  `operationMetrics` de Delta leídas por el propio job; **0 sesiones del SQL
  warehouse**.
- **Local-first.** Todo lo que no exige el workspace (expresiones, planes,
  número de acciones, equivalencia) se mide y se prueba en local; los runs se
  reservan a lo que solo el workspace puede demostrar (MERGE real, memoria y
  concurrencia reales, exports A/B).

## Requisitos funcionales

### RF-1 — Diagnóstico medido antes de optimizar
**Criterio (EARS):** Cuando se aborde el rendimiento del incremental, el coste
deberá estar atribuido por etapa (`keyed`, ventana, conjunto afectado,
recomputación, marcadores, `merge_src`, `MERGE`) y por operador (stage, shuffle,
spill, codegen, tareas) con evidencia de Spark UI, planes y banco local; ninguna
optimización podrá justificarse solo con una hipótesis.

### RF-2 — Invariante de coste del incremental
**Criterio (EARS):** Cuando la ventana del incremental cubra prácticamente todo
el bronce, el tiempo de cada silver no deberá superar **3 m 46 s** (extremo
superior de la reconstrucción completa de A2); cuando la ventana sea pequeña
(≤1 % del bronce), el incremental deberá tardar como máximo **75 s** (un tercio
del mismo extremo).

### RF-3 — Caber en la ventana de producción
**Criterio (EARS):** Cuando el job corra en dev con el código optimizado, deberá
cerrar las 8 tareas en verde en **≤22 m 30 s** (25 % de margen sobre los 1800 s
de producción) y, con un lote semanal real, no deberá acercarse al timeout; el
tamaño del lote y los tiempos por etapa deberán quedar registrados.

### RF-4 — Equivalencia funcional del bloque B
**Criterio (EARS):** Cuando se ejecute la validación, los exports de los runs A
(reconstrucción completa) y B (incremental) deberán ser **idénticos en
contenido**: mismos conteos por tabla y los mismos `sha256` en `files` de
`meta.json`, con la escritura **ordenada de forma total por todas las columnas**
para que el hash sea estable. El `MERGE` deberá escribir realmente (filas
insertadas, actualizadas o borradas distintas de cero) y las tablas oro y los
dos exports deberán conservar su contrato, sin columnas internas (`_key`,
`_ingest_date`).

> **Nota (2026-10-31):** el criterio «idénticos byte a byte» se descartó tras
> medir los runs A `221895134926434` y B `549237053478569`. Las dimensiones
> tienen **idéntico tamaño y hash distinto** (`dim_calendar` 31.107 B,
> `dim_currency` 2.745 B, `dim_companies` 483.093 B y `dim_skill_list`
> 2.412 B) y `fact_offers` crece **+19.342 B** (web) y **+22.459 B** (PBI); el
> contenido de esas dimensiones no depende de las filas que toca el `MERGE`, así
> que la diferencia mide el **escritor** (el orden de filas de `coalesce(1)` no
> es determinista), no la lógica incremental. El criterio de contenido se valida
> en **T-09** con **dos runs manuales de producción sobre el mismo bronce**
> (A `full` y B incremental); el run programado del domingo se pausa esa semana
> y se reanuda el lunes siguiente.

### RF-5 — Idempotencia y watermark
**Criterio (EARS):** Cuando se repita la misma ventana dentro del notebook, el
contenido deberá quedar igual (conteos y exports), pudiendo bajar los conteos de
grupos afectados o borrados; el watermark deberá seguir derivándose de
`max(_ingest_date)` y una ventana vacía no deberá escribir.

### RF-6 — Compute decidido con evidencia
**Criterio (EARS):** Cuando se cierre la spec, la configuración de compute de
producción (tipo de nodo, single node o multi-nodo, autoscaling, Photon,
serverless) deberá quedar decidida y documentada con la medición que la
justifica, el coste por run y el motivo de descarte de las alternativas, dentro
del techo de dev de la spec y del presupuesto de producción de la 009.

### RF-7 — Layout físico evaluado
**Criterio (EARS):** Cuando se cierre la spec, el tamaño y número de ficheros de
bronce, silver y oro deberán estar medidos y la decisión sobre `OPTIMIZE`,
`liquid clustering` o particionado deberá estar tomada y documentada, aunque la
decisión sea no cambiar nada.

### RF-8 — Herencia de la 008
**Criterio (EARS):** Cuando el incremental quepa y la equivalencia esté
demostrada, el despliegue a producción de T-11 de la 008 deberá quedar cerrado:
deploy con rollback, runs de producción 8/8 (A/B manual en T-09 y el siguiente
programado), dashboard publicado y SQL warehouse `STOPPED` antes y después.

### RF-9 — Observabilidad y presupuesto
**Criterio (EARS):** Cuando termine cada run, el resumen del notebook deberá
exponer los tiempos por etapa y las métricas del `MERGE` sin pasadas
adicionales, la evidencia se leerá por la API de runs y el gasto de dev no podrá
superar **3 runs y 0 sesiones SQL** (~0,72 €) dentro del techo de **1,8 €** de
la 009; cada run se anotará en `docs/costs.md` con motivo, id y coste.

## Requisitos no funcionales

- **Stack simple:** PySpark y Delta existentes; sin dependencias nuevas.
- **Lógica e interfaz:** la lógica medible y optimizable (ventana, conjunto
  afectado, `MERGE`, resumen) vive en `Silver_Incremental.py`; los notebooks solo
  coordinan. Cada optimización añade o ajusta tests.
- **Sin cambio de semántica:** cualquier optimización debe pasar el gate de
  equivalencia del bloque B (mismo resultado que la reconstrucción completa).
- **Coste:** verificación local-first; 0 sesiones del SQL warehouse; cada
  despertar cuesta ~0,43 € y cada run de dev ~0,24 €.
- **Idioma:** identificadores y código en inglés; documentos y mensajes en
  español.
- **Presupuesto heredado:** el techo de 3 €/mes es de producción; el gasto de
  desarrollo se cuenta aparte con el límite del track (9 €).

## Fuera de alcance

- Cambiar el mecanismo incremental (grupo semántico, watermark, marcadores) o
  las columnas de negocio de silver, oro y exports. **Excepción controlada:** si
  T-04 demuestra que hace falta, se podrá añadir la columna interna `_key` a
  silver **con aprobación previa de la persona**, excluida de oro y exports por
  proyección explícita y con `RESTORE` disponible.
- Incremental de oro y de `companies_*` (siguen completos; 008).
- Historial de cambios (CDF), expiración técnica y dedup de negocio con ventana
  temporal.
- Streaming, Lakeflow y observabilidad con *system tables* (spec 011).
- Seguridad y gobernanza (spec 012).
- Cadencia semanal y presupuesto mensual (spec 009).
- Scrapers y landing (specs 004/005).

## Criterios de finalización

- Diagnóstico de rendimiento con evidencia: coste por etapa antes/después,
  hipótesis confirmadas o descartadas, y decisión de compute y layout
  documentadas.
- Run A (full) y run B (incremental + repetición) en dev: 8/8, RF-2 y RF-3
  cumplidos, `MERGE` con métricas de escritura e idempotencia demostrada.
- **RF-4 (equivalencia de contenido de los exports) se valida en T-09**, con
  **dos runs manuales de producción sobre el mismo bronce** (A `full` y B
  incremental), comparando `files` del segundo contra `previous_files`.
- Ventana vacía sin escritura.
- Suites `databricks_notebooks` y `scrapers-pipeline` en verde (la suite
  completa es gate de la spec, no de cada tarea).
- Producción desplegada y verificada con los runs A/B, el dashboard publicado
  con `workflow_dispatch` y el warehouse `STOPPED`.
- Nota `docs/databricks-de/notes/01-spark-performance.md` escrita, roadmap y
  `docs/costs.md` al día, y comprobación real registrada.
