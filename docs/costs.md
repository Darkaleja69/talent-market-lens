# Coste del proyecto — línea base y método

Documento vivo de la spec 009. Registra el método de medición, la línea base y
las decisiones de coste. Se actualiza al cerrar cada hito (T-01, T-10, T-13,
T-15).

## Línea base (2026-10-23)

### Azure Cost Analysis (EUR, 1–23 de octubre)

- **Coste real acumulado: 31,24 €.**
- **Previsión: 50,17 €** (inflada por el pico del 17-oct).
- Reparto diario aproximado, leído del gráfico:

| Días | Coste/día | Lectura |
|---|---|---|
| 1–5 oct | ~0,1 € | días sin warehouse |
| 6–14 oct | ~0,3–0,6 € | job + algún despertar |
| 15–16 oct | ~0,4–0,5 € | job |
| **17 oct** | **~10,6 €** | jornada de depuración con muchas sesiones SQL |
| 18 oct | ~4,5 € | varias sesiones SQL |
| 19–20 oct | ~1,5–2,5 € | job + sesiones |
| 21 oct | ~1,2 € | job + un despertar |
| 22 oct | ~2,7 € | varios runs + sesiones |
| 23 oct | ~0 € | sin uso |

### Databricks Account Console (USD, por semana)

| Semana | Total | `SERVERLESS_SQL` | `JOBS_SERVERLESS` | `JOBS_COMPUTE` | Otros |
|---|---|---|---|---|---|
| 28 sep | ~5,5 $ | ~1,7 $ | ~3,2 $ | ~0,1 $ | — |
| 05 oct | ~3,0 $ | ~2,8 $ | ~0,1 $ | ~0,1 $ | — |
| 12 oct | ~24,0 $ | **~20,5 $** | ~1,2 $ | ~0,8 $ | egress ~1,0 $ |
| 19 oct | ~10,5 $ | **~9,3 $** | ~0,5 $ | ~0,2 $ | egress ~0,4 $ |

> La Account Console está en **USD** y Azure en **EUR**: no se comparan
> directamente. La estructura (qué SKU pesa) sí es comparable.

**Conclusión: el 85–90 % del gasto es `PREMIUM_SERVERLESS_SQL_COMPUTE`, es
decir, el SQL warehouse.** El job clásico es la línea fina.

## Cómo se factura cada cosa

- **SQL warehouse (serverless)**: se factura por **tiempo encendido**, no por
  consulta. `Serverless Starter Warehouse` es **Small = 12 DBU/h**; el
  `auto_stop_mins` mínimo es 10, así que **cada despertar cuesta ~1,29 €**
  aunque la consulta dure tres segundos. Diez consultas separadas más de 10 min
  son diez despertares.
- **Jobs clásicos**: DBU + VM mientras vive el cluster (~0,75 €/h; el run de
  19 min del 22-oct ≈ **0,24 €**).
- **Networking serverless**: `PUBLIC_CONNECTIVITY_DATA_PROCESSED` e
  `INTERNET_EGRESS_EUROPE` (céntimos) más NAT Gateway.
- **Almacenamiento**: `General Block Blob` (céntimos).

## Por qué el pico del 17-oct fue del warehouse y no del job

El 17-oct se ejecutó un run de dev de 337 min, pero en la semana del 12-oct el
SKU de jobs suma ~2 $ y el de SQL ~20,5 $. El coste no vino del run colgado:
vino de **~15 despertares del warehouse** (20,5 $ / 1,4 $ por despertar ≈ 14,6),
típicos de una jornada de depuración con consultas sueltas: abrir el SQL
Editor, contar filas, mirar `DESCRIBE HISTORY`, refrescar Power BI, volver
media hora después y repetir. Cada hueco de más de 10 min reinicia la factura.

Los días sin ninguna consulta al warehouse quedan en **~0,2–0,5 €** (el job),
no en 0,80 €. El importe de 0,80 € citado en la spec corresponde a un día con
al menos un despertar.

## Qué se hace con esto (spec 009)

- ELT **semanal** (domingo 23:00): divide el coste del job por 7.
- **Power BI fuera del warehouse** (T-08/T-09): elimina el 85–90 % del gasto.
- **Warehouse a 2X-Small** (4 DBU/h): aplicado el 2026-10-23 (verificado
  `size=2X-Small`, `state=STOPPED`); cada despertar pasa de ~1,29 € a ~0,43 €.
- **SQL agrupado**: todas las sentencias de una sesión de trabajo en una sola
  ventana de 10 min; para grants y metadatos, API de Unity Catalog (gratis).
- **Presupuesto de 15 €/mes** con alertas al 50 % y 100 % (T-13).

## Método reproducible

1. **Azure Cost Analysis**: últimos 30 días, granularidad diaria, agrupar por
   producto y por recurso. Incluye el resource group gestionado
   (`databricks-rg-…`), donde viven las VMs y discos de los clusters.
2. **Account Console → Usage** (`accounts.azuredatabricks.net`): DBU por SKU y
   día/semana. Es la vista que separa jobs de SQL y de all-purpose.
3. **`docs/costs/billing_by_sku.sql`**: consulta versionada contra
   `system.billing.usage`. Cuesta ~1,29 € por despertar el warehouse, así que
   solo se usa si las dos vistas anteriores no bastan.

## Etiquetas y presupuesto (2026-10-24)

- Presupuesto `talent-market-lens-monthly`: **15 €/mes** en la suscripción, con
  alertas actuales al **50 %** y **100 %**.
- Etiqueta `project=talent-market-lens` en el storage `joboffershns`, el
  workspace de Databricks y el resource group (incluido el gestionado).
- Las etiquetas **no son retroactivas** (el gasto anterior a hoy queda sin
  etiquetar) y **no se heredan** del resource group a los recursos: el RG
  gestionado `databricks-rg-…` (VMs, discos, NAT) es la parte que sigue sin
  etiqueta. El presupuesto es de **suscripción**, así que la alerta cubre todo
  el gasto aunque el desglose por etiqueta esté incompleto.

## Runs de la spec 008 (en curso)

- **T-04 (2026-10-24), one-off del bronce dev:** run `328818148400464` del job
  `dedupe_oneoff` (`933759794529939`), **SUCCESS**, 4/4 tareas, **8 min 58 s**
  (6 min 22 s de arranque + 78 s de indeed + ~25 s por cada una de las otras
  tres). Clúster `D4ds_v4` single node a ~0,75 €/h → **~0,11 €**. Consume el
  **run 1 de 3** del presupuesto de la 008.
- Quedan 2 runs de dev (T-05 ciclo y repetición) y 0 sesiones SQL; gasto de dev
  de la 008 tras la política: ~0,11 € de los ~0,6 € previstos.
- **T-05 A (2026-10-24), ciclo dev:** run `274846748795055`, **SUCCESS**, 8/8
  tareas, **13 min 16 s** (bronce setup 6 m 22 s + exec 1 m 46 s) → **~0,24 €**.
  Consume el run 2 de 3.
- **T-05 B (2026-10-24), repetición por idempotencia:** run `683591523413230`,
  **SUCCESS**, 8/8, **8 min 43 s** (bronce setup 2 m 25 s + exec 1 m 04 s) →
  **~0,11 €**. Consume el run 3 de 3.
- **Cierre de dev de la 008:** 3 runs tras la política (0,11 + 0,24 + 0,11 =
  **~0,46 €**), 0 sesiones SQL; con los 2 runs hundidos de T-03, ~0,94 € en total
  frente al techo de 2,0 €.
- **T-06 one-off de prod (2026-10-24):** run `164560238285705`, **SUCCESS**, 4/4,
  **9 min 11 s** → **~0,11 €** (producción, fuera del presupuesto de dev).
  Deduplicó 53.353 filas históricas en las cuatro tablas de prod.
- **T-10 A (2026-10-25), migración de las silvers en dev:** run `263658658061220`,
  **SUCCESS**, 8/8 tareas, **22 min 18 s** (bronce setup 6 m 52 s + exec 1 m 57 s;
  silvers 9,8–11,8 min; oro 0,87 min; exports 0,53/0,58 min) → **~0,24 €**.
  Consume el **run 1 de 2** del bloque B.
- **T-10 B (2026-10-25), backfill incremental con `repeat_window`:** run
  `186230462263213`, **TIMEDOUT** por el `timeout_seconds: 1800` del job a los
  **30 min 46 s** (bronce en verde en 3 m 29 s; las cuatro silvers canceladas a
  los ~27 min; oro y exports `SKIPPED`) → **~0,24 €**. Consume el **run 2 de 2**
  del bloque B; no se lanza un tercero.
- **T-10 A2 (2026-10-27), repetición del run A con el modo `full` corregido:**
  run `207866361330883`, **SUCCESS**, 8/8 tareas, **14 min 39 s** (bronce 9 m 07 s;
  silvers 2 m 47 s–3 m 46 s; oro 0 m 59 s; exports 0 m 40 s/0 m 43 s) →
  **~0,24 €**. Las silvers vuelven a **2,8–3,8 min** (T-05: 2,8–3,6); el total
  queda 1,4 min por encima de T-05 por el lote de bronce de dos días, no por el
  código de silver.
- **T-10 B2 (2026-10-27), backfill incremental 25–27 con `repeat_window`:** run
  `1056455747512203`, **TIMEDOUT** a los **30 min 37 s** (bronce en verde; las
  cuatro silvers canceladas; oro y exports `SKIPPED`) → **~0,24 €**. **No escribió
  nada:** la última versión Delta de las cuatro silvers es la de A2 (indeed v11
  3.096 filas, infojobs v12 2.527, linkedin v8 12.504, multi_site v11 10.068).
  El bronce de indeed solo ingirió **190 filas** en el 27, así que el timeout no
  se explica por volumen: hay un **coste fijo/patología** del camino incremental
  (≈22 min con 190 filas cambiadas) que debe resolver la spec 010.
- **T-10 B3 (2026-10-27/28), repetición con `keyed` compartido:** run
  `1015448868873733`, **TIMEDOUT** a los **30 min 30 s** (bronce en verde en
  8 min 06 s; las cuatro silvers cortadas a los **22 min 04 s**; oro y exports
  `SKIPPED`) → **~0,24 €**. Tampoco escribió: las versiones Delta siguen en las
  de A2. El fix de `keyed` no era el cuello; la medición local de los pasos da
  ~45 s por pase (con aviso de codegen “Code grows beyond 64 KB” en
  `build_offers`), y el único paso no medido es el `MERGE`. **T-10 queda
  bloqueada por rendimiento; no se lanza otro run y T-11 no se toca.**
- **Cierre de dev del bloque B (actualizado):** 5 runs (A, B, A2, B2 y B3:
  0,24 × 5 = **~1,20 €**), 0 sesiones SQL; el techo de 2,0 € sigue holgado.
  **La validación no se cierra:** B2/B3 no dejaron resumen de las silvers ni
  comparación de exports; el incremental necesita rendimiento (spec 010) antes
  de T-11.

## Runs de la spec 010 (en curso)

- **T-03 (2026-10-28), intento abortado antes de medir:** run `16957686724897`,
  **CANCELED** a los 108 s (el job desplegado aún no tenía el `spark_conf`; el
  bronce estaba arrancando) → **~0,02 €**. No cuenta como evidencia.
- **T-03 (2026-10-28), run A (referencia `force_full`, ya con 8 particiones):**
  `485578578869927`, **SUCCESS**, 8/8, **808 s** (13 m 28 s); silvers 117–169 s
  de módulo (tareas 167–204 s); oro 54 s; exports 35/37 s → **~0,17 €**.
- **T-03 (2026-10-28), run B (incremental + `repeat_window`, ventana 24–28):**
  `269508392317481`, **SUCCESS**, 8/8, **974 s** (16 m 14 s); dos pases por
  silver: **249–318 s** el primero y **173–249 s** el segundo; el `MERGE`
  escribió 55–458 filas; conteos finales **idénticos a A** (3.112 / 2.527 /
  12.543 / 10.072) → **~0,20 €**.
- **T-03b (2026-10-30), run con el código de T-02/T-03b (incremental + repetición):**
  `879269828668729`, **SUCCESS**, 8/8, **927 s** (15 m 27 s); pases por silver
  **123,8–138,2 s** el primero y **89,4–112,3 s** el segundo (frente a
  249–318 s y 173–249 s del run anterior: **2,0–2,6×**); `MERGE` de
  55–458 filas con `numTargetRowsCopied=0`; conteos finales idénticos a A
  (3.112 / 2.527 / 12.543 / 10.072) y repetición idempotente; bronce **383 s de
  arranque + 134 s de ejecución** (**56 % del run**). → **~0,23 €**.
- **Total de la 010:** ~0,62 € de 1,8 € (3 runs de evidencia + 1 abortado).

## Cierre de la spec 009 (2026-10-24)

- Runs medidos: dev 16,31 min (~0,24 €), prod 18,93 min (~0,24 €) e intento
  fallido con `D2ds_v6` 18,2 min (~0,13 €). Total ~0,61 €.
- Un despertar del warehouse el 23-oct a las 20:35 (~0,43 €) por consultas
  internas de facturación; el refresh de Power BI **no** lo despertó (última
  consulta 20:35, refresh posterior).
- Gasto del periodo 1–23 oct antes del cambio: 31,24 € (previsión 50,17 €).
- Después del cambio: job semanal ~1 €/mes + storage/egress; objetivo ≤ 3 €/mes.
- Pendiente: captura de Azure Cost Analysis / Account Console tras una semana
  para confirmar la bajada.

## Pendiente

- Detalle por recurso del resource group gestionado (VMs y discos) para cerrar
  el desglose de la parte clásica.
- Confirmar qué genera `PREMIUM_JOBS_SERVERLESS_COMPUTE` (~1 $/semana): si todos
  los runs son clásicos, ese SKU no debería aparecer.
