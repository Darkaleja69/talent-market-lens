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

Con **190 filas nuevas** en el bronce de indeed (27-oct), el incremental tarda
≥4× lo que tarda la reconstrucción completa de la misma tabla y ni siquiera
termina. La producción tiene `timeout_seconds: 1800` y el run semanal ya gasta
~19 min, así que **el mecanismo, tal cual, no es desplegable**. Además, el
`MERGE` real de Delta sigue sin medirse: es el único paso sin tiempo observado.

Objetivo (resultado único): **que el incremental de silver sea viable en
producción** —medido, más barato que reconstruir, con la validación funcional
del bloque B cerrada (equivalencia, idempotencia, `MERGE` real)— y que el
**compute quede decidido con evidencia**, no por intuición.

Y el encaje con el examen: el dominio 1 (shuffles, joins, broadcast, skew,
particionado, tamaño de ficheros, *query plans*, Spark UI, Query Profile,
*data skipping*) se practica **sobre este problema real**, no con juguetes.

## Herencia de la 008 (2026-10-28)

La 008 **no se reabre**: se hereda su estado y se respeta su diseño.

- **Lo cerrado se queda cerrado:** T-01–T-09 (diseño, módulo, notebooks en dev,
  runbook) y el bloque A (bronce upsert, one-off en dev y prod). Esta spec no
  cambia la semántica del incremental ni el contrato de silver, oro o exports.
- **T-10 queda bloqueada** por rendimiento: B, B2 y B3 `TIMEDOUT` sin
  escrituras; las versiones Delta siguen en las de A2 (indeed v11 3.096,
  infojobs v12 2.527, linkedin v8 12.504, multi_site v11 10.068).
- **T-11 no se toca** hasta que esta spec deje el incremental por debajo de la
  ventana de 1800 s y repita la validación A/B. El procedimiento de producción
  sigue siendo el `cutover.md` de la 007.
- **Decisión pendiente de la persona:** descopear el bloque B de la 008 (y
  cerrarla con el bloque A) o dejarla abierta. Si se descopea, T-10 y T-11
  pasan a esta spec como tareas heredadas; si no, esta spec desbloquea y
  devuelve la evidencia a la 008.
- **Presupuesto:** los 5 runs del bloque B (~1,20 €) superan los 2 aprobados
  (~0,48 €); el exceso (~0,72 €) se descuenta del techo de esta spec (009,
  `plan.md` §17).

## Decisiones (2026-10-28)

- **Diagnóstico antes de optimizar.** No se lanza ningún run hasta tener el
  coste por etapa y por operador medido (Spark UI de B2/B3, plan y
  `explain`, banco local). Se acabaron las hipótesis: B3 ya demostró que
  compartir `keyed` no era el cuello.
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
  `liquid clustering` pueden no pagar; se mide el tamaño y número de ficheros y
  se documenta la decisión con su motivo.
- **Observabilidad sin SQL.** Tiempos por paso y métricas del `MERGE` salen del
  resumen del notebook (`dbutils.notebook.exit`) y de las `operationMetrics` de
  Delta leídas por el propio job; **0 sesiones del SQL warehouse**.
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
el bronce, el tiempo de cada silver no deberá superar el de una reconstrucción
completa de la misma tabla (2 m 47 s–3 m 46 s de A2); cuando la ventana sea
pequeña (≤1 % del bronce), el incremental deberá ser al menos **3× más rápido**
que la reconstrucción completa.

### RF-3 — Caber en la ventana de producción
**Criterio (EARS):** Cuando el job corra en dev con el código optimizado, deberá
cerrar las 8 tareas en verde con **≥25 % de margen** sobre los 1800 s de
producción (≤22 m 30 s) y, en el caso de un lote semanal real, sin acercarse al
timeout; el tamaño del lote y los tiempos por etapa deberán quedar registrados.

### RF-4 — Equivalencia funcional del bloque B
**Criterio (EARS):** Cuando se ejecute la validación, los exports de los runs A
(reconstrucción completa) y B (incremental) deberán ser **idénticos byte a
byte**, el `MERGE` deberá escribir realmente (filas insertadas/actualizadas/
borradas distintas de cero) y las tablas oro y los dos exports deberán conservar
su contrato, sin columnas internas (`_key`, `_ingest_date`).

### RF-5 — Idempotencia y watermark
**Criterio (EARS):** Cuando se repita la misma ventana dentro del notebook, el
contenido deberá quedar igual (conteos y exports), pudiendo bajar los conteos de
grupos afectados o borrados; el watermark deberá seguir derivándose de
`max(_ingest_date)` y una ventana vacía no deberá escribir.

### RF-6 — Compute decidido con evidencia
**Criterio (EARS):** Cuando se cierre la spec, la configuración de compute de
producción (tipo de nodo, single node o multi-nodo, autoscaling, Photon,
serverless) deberá quedar decidida y documentada con la medición que la
justifica y el motivo de descarte de las alternativas, sin aumentar el coste del
run por encima del presupuesto.

### RF-7 — Layout físico evaluado
**Criterio (EARS):** Cuando se cierre la spec, el tamaño y número de ficheros de
bronce, silver y oro deberán estar medidos y la decisión sobre `OPTIMIZE`,
`liquid clustering` o particionado deberá estar tomada y documentada, aunque la
decisión sea no cambiar nada.

### RF-8 — Herencia de la 008
**Criterio (EARS):** Cuando el incremental quepa y la equivalencia esté
demostrada, la validación funcional de T-10 y el despliegue de T-11 de la 008
deberán quedar cerrados: deploy a producción en la ventana previa a un domingo
con rollback, run programado 8/8, dashboard publicado el lunes y SQL warehouse
`STOPPED` antes y después. Si la 008 conserva el bloque B, esta spec solo
entrega la evidencia y la 008 ejecuta sus tareas.

### RF-9 — Observabilidad y presupuesto
**Criterio (EARS):** Cuando termine cada run, el resumen del notebook deberá
exponer los tiempos por paso y las métricas del `MERGE` sin pasadas adicionales,
la evidencia se leerá por la API de runs y el gasto de dev no podrá superar
**3 runs y 0 sesiones SQL** (techo 1,0 €, con el exceso de la 008 ya
descontado); cada run se anotará en `docs/costs.md` con motivo, id y coste.

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
  los contratos de silver, oro y exports.
- Incremental de oro y de `companies_*` (siguen completos; 008).
- Historial de cambios (CDF), expiración técnica y dedup de negocio con ventana
  temporal.
- Streaming, Lakeflow y observabilidad con *system tables* (spec 011).
- Seguridad y gobernanza (spec 012).
- Cadencia semanal y presupuesto mensual (spec 009).
- Scrapers y landing (specs 004/005).

## Criterios de finalización

- Diagonal de rendimiento con evidencia: coste por etapa antes/después,
  hipótesis confirmadas o descartadas y decisión de compute y layout
  documentadas.
- Run A (full) y run B (incremental + repetición) en dev: 8/8, silvers dentro
  del invariante de RF-2, exports A/B idénticos byte a byte y `MERGE` con
  métricas de escritura.
- Idempotencia demostrada y ventana vacía sin escritura.
- Suites `databricks_notebooks` y `scrapers-pipeline` en verde (la suite
  completa es gate de la spec, no de cada tarea).
- Producción desplegada y verificada con el run programado, el dashboard del
  lunes y el warehouse `STOPPED` (o evidencia entregada a la 008, si conserva
  el bloque B).
- Nota `docs/databricks-de/notes/01-spark-performance.md` escrita, roadmap y
  `docs/costs.md` al día, y comprobación real registrada.
