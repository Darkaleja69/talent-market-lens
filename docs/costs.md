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

## Pendiente

- Detalle por recurso del resource group gestionado (VMs y discos) para cerrar
  el desglose de la parte clásica.
- Confirmar qué genera `PREMIUM_JOBS_SERVERLESS_COMPUTE` (~1 $/semana): si todos
  los runs son clásicos, ese SKU no debería aparecer.
