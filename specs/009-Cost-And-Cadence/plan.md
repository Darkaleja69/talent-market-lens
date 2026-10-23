# Plan técnico: coste y cadencia del pipeline

## 1. Objetivo y límites

Bajar el coste mensual de Azure a ≤ 3 €/mes manteniendo el run semanal en verde
y el informe Power BI funcionando, sin cambiar de plataforma ni de cuenta. Se
tocan cuatro superficies: la programación del job, el export de la capa Gold, las
particiones M del modelo semántico y el compute del job. No se toca la lógica de
la ELT (spec 008), ni los scrapers, ni la landing, ni la web pública más allá de
su cron.

## 2. Estado de partida (medido, no estimado)

Histórico real del job (API `jobs list-runs`, 22-oct-2026):

| Fecha | Job | Duración | Estado |
|---|---|---|---|
| 22-oct 23:00 | Git Based ELT (prod) | 19,2 min | SUCCESS |
| 21-oct 23:00 | Git Based ELT (prod) | 18,9 min | SUCCESS |
| 21-oct 23:20 | Git Based ELT **Dev** | 13,6 min | SUCCESS |
| 20-oct 23:00 | Git Based ELT (prod) | 20,2 min | SUCCESS |
| 17-oct 18:40 | Git Based ELT **Dev** | **337,7 min** | CANCELED |
| 16-oct 22:12 | Git Based ELT **Dev** | **114,1 min** | CANCELED |

Desglose del run de producción del 22-oct (`jobs get-run`):

| Tarea | Tiempo |
|---|---|
| `Bronze_Ingestion` | **6,9 min de aprovisionamiento** + 1,6 min de ejecución |
| `Silver_Infojobs` | **8,9 min** (cuello de botella) |
| `Silver_Indeed` / `Linkedin` / `Multi_site` | 4,1 / 4,7 / 4,2 min (en paralelo) |
| `Gold_Complete_Catalog` | 1,2 min |
| `Build_for_Web-Dashboard` | 0,5 min |

El 80 % del run son el arranque del cluster (6,9 min) y la silver de InfoJobs
(8,9 min). Los picos de factura son los dos runs de dev cancelados, ya acotados
por `timeout_seconds: 1800` desde el commit `a66b835` (18-oct).

Modelo de coste (tarifa de lista Premium, oct-2026; se confirma en T-01 con
`system.billing.usage`):

| Concepto | Cálculo | Coste |
|---|---|---|
| Job clásico | 19,3 min de vida del cluster (23:00:56–23:20:12) × (2 DBU/h × 0,30 $ + VM ~0,23 $) ≈ 0,75 €/h | **0,24 €** |
| Despertar del warehouse | 10 min × **12 DBU/h** (Small serverless) × 0,70 $/DBU | **1,29 €** |
| Día con job + un refresh | 0,24 + 1,29 (+ IVA) | **≈ 1,85 €** |
| Run de dev colgado (337 min) | 5,6 h × 0,75 €/h | **≈ 2,75 €** |

El warehouse cuesta **~5× el job**. Con `auto_stop_mins = 10` ya está en el
mínimo facturable; la única forma de quitar ese suelo es que el informe deje de
despertarlo.

Los días de ~2 € de la persona cuadran con job + un despertar (1,85 € con IVA);
los de 5 €, con 3–4 despertares separados más de 10 min. **Los ~0,80 € de los
días «solo job» no cuadran con la tarifa de lista**: o el job consume más DBU de
las estimadas, o esos días hubo también un despertar corto. T-01 lo resuelve con
`system.billing.usage` en vez de conjeturas.

## 3. Modelo de coste objetivo

| Concepto | Hoy (diario) | Después (semanal) |
|---|---|---|
| Job (19 min) | 0,24 €/día ≈ 7 €/mes | 4,3 runs/mes ≈ 1 €/mes |
| Warehouse | 1,29 €/despertar; hasta ≈ 39 €/mes si se despierta a diario | 0 € (solo consultas ad hoc) |
| Runs de dev colgados | hasta 4,2 €/incidente | tope 0,38 € (timeout 30 min) |
| Storage + discos + IP | céntimos | céntimos |
| **Total** | **~50–65 €/mes** | **~1,5–2,5 €/mes** |

Con serverless en modo Standard (hasta −70 % frente a performance-optimized) o
`D2ds_v4`, el job baja a ~0,5–1 €/mes. El techo de 3 €/mes deja margen para un
run extra y una consulta manual al mes.

## 4. Arquitectura objetivo

```text
scrapers locales (diario, gratis)
        │
        ▼
ADLS landing/<fuente>/            (sin cambios)
        │
        ▼
Job Databricks (semanal, domingo 23:00 Europe/Madrid)
  Bronze → 4 Silver → Gold ──┬─→ gold/web_export   (público, contrato 006)
                             └─→ gold/pbi_export   (informe, full, nuevo)
        │
        ├─→ GitHub Actions (lunes 01:00 UTC) → GitHub Pages
        └─→ Power BI (Import) ← Parquet de ADLS   (sin SQL warehouse)
```

## 5. Contrato del export del informe

Prefijo: `gold/pbi_export` en prod y `gold/pbi_export_dev` en dev (variable de
bundle `pbi_export_prefix`).

Ficheros fijos, uno por tabla, escritos con `coalesce(1)` e idempotentes:

| Fichero | Origen en Gold | Columnas |
|---|---|---|
| `fact_offers.parquet` | `gold.fact_offers` | **31** = 29 de Gold (18 físicas públicas + 11 internas) + `GeoCountry`/`GeoRegion` derivadas |
| `fact_offer_skills.parquet` | `gold.fact_offer_skills` | `JobID`, `Skill` |
| `dim_skill_list.parquet` | `gold.dim_skill_list` | `SkillName`, `SkillCategory` |
| `dim_calendar.parquet` | `gold.dim_calendar` | 14 |
| `dim_companies.parquet` | `gold.companies_complete_catalog` | 7 |
| `dim_currency.parquet` | `gold.dim_currency` | 5 |
| `meta.json` | — | versión, `data_date`, `generated_at`, `mode: full`, `audience: pbi`, conteo por tabla |

Diferencias deliberadas con el export público:

- **No** aplica el umbral de 25 MiB ni el modo `aggregated`: el informe necesita
  el detalle completo.
- **Sí** incluye las columnas internas que el modelo usa
  (`description_clean`, `skills`, `salary_quality`, `skills_source`,
  `experience_level_source`, `posted_date_raw`, `posted_date_source`,
  `salary_currency`, `salary_period`, `work_mode`, `is_salary_available`), que
  el export público excluye por diseño.
- **Calcula** `GeoCountry`/`GeoRegion` reutilizando `Prepare_Web_Export`
  (`REGION_MAP` y la normalización de país), porque **no existen en Gold**: son
  derivadas y el export público ya las calcula con tests. Así el Parquet del
  informe tiene las 31 columnas que espera el modelo.
- Escribe `skills` como texto (`concat_ws`), porque en Gold es
  `ARRAY<string>` y el modelo la declara `string`. La columna no se usa en
  ningún visual ni medida (comprobado en el informe), así que su formato no
  altera resultados; se documenta la representación elegida.

## 6. Cambios en Power BI

Particiones M (las seis dejan de usar `Databricks.Catalogs`):

```m
let
    Origen = AzureStorage.DataLake(
        "https://<storage>.dfs.core.windows.net/<container>/gold/pbi_export",
        [HierarchicalNavigation = true]),
    Fichero = Origen{[Name = "fact_offers.parquet"]}[Content],
    Tabla = Parquet.Document(Fichero)
in
    Tabla
```

- `Fact_Offers`: se elimina la lógica M de `GeoCountry`/`GeoRegion` (vienen
  calculadas en el export del informe, no en Gold) y el `NestedJoin` contra
  `Dim_RegionMap`. La tabla calculada `Dim_RegionMap` se conserva aunque quede
  sin uso, para no tocar más el modelo.
- `Dim_Calendar`: conserva el filtro `Year` entre 2025 y 2027.
- `Dim_Companies`: lee `dim_companies.parquet`.
- `Dim_Currency`, `Dim_SkillList`, `Fact_OfferSkills`: lectura directa.
- **Credenciales:** la URL no lleva SAS. La credencial (SAS de solo lectura
  acotada a `gold/pbi_export`, o identidad) se configura en las credenciales del
  origen de datos del dataset, fuera del `.pbip`. Rotación documentada en el
  RUNBOOK, igual que `WEB_EXPORT_SAS`.
- **Refresh:** el modelo es Import; el refresh no abre conexión a Databricks.

## 7. Compute del job

Orden de preferencia, con medición en cada paso:

1. **Serverless en modo Standard** (`performance_target: STANDARD`): elimina el
   aprovisionamiento de 6,9 min y factura por ejecución real. Riesgo: serverless
   no soporta init scripts ni rutas relativas; hay que comprobar `cloudFiles`,
   los imports de módulos del repo y `%load_ext autoreload`.
2. **Plan B: `Standard_D2ds_v4`** single node (mitad de DBU y VM) y limpieza de
   `kind: CLASSIC_PREVIEW` en la definición del cluster. Riesgo: 2 vCPU para
   cuatro silvers en paralelo puede alargar el run; se mide.
3. Descartado: *instance pools* (VM encendida 24/7, ~150 €/mes), spot en
   single-node (el driver es la única VM: una expulsión tumba el run) y Photon
   (sube DBU).

## 8. Cadencia y workflows

- Job prod: `quartz_cron_expression: 50 0 23 ? * SUN`, `Europe/Madrid`.
- Dashboard: `cron: '0 1 * * MON'` (01:00 UTC del lunes).
- El job dev sigue sin programación y en pausa.
- La programación vive en `databricks.yml`; el job es `UI_LOCKED` y solo cambia
  con `bundle deploy` (producción, manual y validada).

## 9. Medición y presupuesto

Consulta versionada en `docs/costs/billing_by_sku.sql`:

```sql
SELECT usage_date,
       sku_name,
       billing_origin_product,
       SUM(usage_quantity) AS dbus
FROM system.billing.usage
WHERE usage_date >= current_date() - 30
GROUP BY ALL
ORDER BY usage_date DESC, dbus DESC;
```

El coste de VM y almacenamiento no está en esa tabla: se cruza con Azure Cost
Analysis agrupado por recurso. `docs/costs.md` registra método, línea base y
medición posterior con el mismo criterio. Presupuesto mensual de 15 € con aviso
al 50 % y al 100 %.

## 10. Estructura de archivos

```text
databricks.yml                                  cron semanal + variable pbi_export_prefix
resources/elt.job.yml                           tarea de export del informe + compute
databricks_notebooks/Prepare_PBI_Export.py      contrato del export (nuevo)
databricks_notebooks/report_export_build.ipynb  tarea del job (nuevo)
databricks_notebooks/tests/test_pbi_export.py   tests (nuevo)
Job_Offers_Dashboard.SemanticModel/definition/tables/*.tmdl   6 particiones
.github/workflows/dashboard.yml                 cron semanal
docs/costs.md + docs/costs/billing_by_sku.sql   medición (nuevo)
docs/RUNBOOK.md                                 cadencia, export, credenciales
specs/009-Cost-And-Cadence/                     esta spec
```

## 11. Estrategia de comprobaciones

- **Unitarias:** contrato de columnas del export del informe, idempotencia,
  `meta.json`, y que el export público no cambia.
- **Bundle:** `databricks bundle validate -t dev` y `-t prod`.
- **Run dev:** export escrito en `gold/pbi_export_dev` con el mismo número de
  filas que
  Gold y `meta.json` válido.
- **Informe:** refresh completo con el warehouse `STOPPED` y comparación de las
  medidas principales antes/después (ofertas, skills, salario medio).
- **Coste:** `system.billing.usage` antes/después y Azure Cost Analysis.
- **No regresión:** suites `databricks_notebooks` y `scrapers-pipeline`; web
  pública con `meta.json` correcto.

## 12. Riesgos y mitigación

| Riesgo | Impacto | Mitigación |
|---|---|---|
| Serverless no soporta el patrón (cloudFiles, imports, autoreload) | Alto | Spike en dev (T-11); plan B `D2ds_v4` |
| El refresh del informe no autentica en el Service | Alto | Probar credenciales antes del cutover; rollback al conector Databricks documentado |
| El export del informe crece (> 25 MiB) | Medio | Modo `full` por diseño; medir tamaño y tiempo |
| SAS caduca | Medio | Caducidad ≤ 1 año, rotación documentada y probada |
| La 008 cambia el tiempo del job | Bajo | El techo es absoluto; repetir la medición al fusionar |
| Cruce con la 008 (`AGENTS.md`, deploy-dev) | Bajo | Resolver a mano al fusionar |
| `D2ds_v4` alarga el run por 2 vCPU | Medio | Medir; si empeora el coste total, volver a `D4ds_v4` |
| Un run semanal falla y el dashboard publica datos viejos | Bajo | `meta.json` muestra la fecha; el workflow no publica a medias |

## 13. Decisiones y alternativas descartadas

- **Migrar de cuenta/suscripción:** descartado. Misma tarifa por SKU y región;
  solo aporta créditos temporales y obliga a recrear workspace, metastore,
  storage, external locations, secretos y bundle.
- **Databricks Free Edition:** descartado. Serverless-only, un workspace,
  cuotas, uso no comercial y storage gestionado; rompe la constitución 5.
- **Salir de Databricks (GitHub Actions + PySpark/Delta o DuckDB):** es la vía
  para llegar a ~0 €, pero exige reescribir Bronze (Auto Loader no existe fuera
  de Databricks) y saca Databricks del portfolio. Se documenta como opción
  futura si el techo de 3 €/mes no bastara.
- **DBCU prepago o reservas:** descartado; compromete más gasto del actual.
- **Apagar el warehouse del todo:** se conserva parado; borrarlo impide
  consultas ad hoc y no ahorra nada mientras esté `STOPPED`.

## 14. Secuencia de implementación

1. Medición, inventario y hoja de ruta (T-01–T-02b). Punto de parada.
2. Cadencia semanal (T-03–T-04). Punto de parada.
3. Export del informe (T-05–T-07). Punto de parada.
4. Informe sin warehouse (T-08–T-10). Punto de parada.
5. Compute y presupuesto (T-11–T-13). Punto de parada.
6. Deploy a producción y cierre (T-14–T-15).

## 15. Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T-03, T-04, T-14 |
| RF-2 | T-08, T-09, T-10 |
| RF-3 | T-05, T-06, T-07 |
| RF-4 | T-09, T-10 |
| RF-5 | T-11, T-12 |
| RF-6 | T-01, T-02b, T-13, T-15 |
| RF-7 | T-02, T-07, T-10, T-15 |
| RF-8 | T-02b, T-04, T-13, T-15 |

## 16. Cumplimiento de la constitución

- **Stack simple (constitución 1):** sin dependencias nuevas; conector Parquet
  nativo.
- **Spec y código (constitución 2):** la spec se actualiza con el código en la
  misma rama.
- **Lógica e interfaz (constitución 3):** el contrato del export vive en
  `Prepare_PBI_Export.py` con tests; el notebook solo coordina.
- **Tests (constitución 4):** tests nuevos + suites existentes en verde.
- **Persistencia (constitución 5):** el crudo sigue en ADLS; el export es
  derivado.
- **Idioma (constitución 6):** código e identificadores en inglés; documentos en
  español.

## 17. Verificación local-first y presupuesto de desarrollo

El techo de 3 €/mes es de **producción**; el desarrollo también cuesta y puede
superarlo. Medido: un run de dev en `D4ds_v4` cuesta ~0,24 € (0,13 € en
`D2ds_v4`) y **una sesión de SQL cuesta ~1,29 €**. Un run corto es ~5× más
barato que una consulta: esa relación ordena toda la política del track.

**Tres entornos, tres usos:**

| Entorno | Uso | Coste |
|---|---|---|
| Local (Windows) | Tests, lógica de los notebooks, exports y entrenamiento de modelos pequeños sobre el Parquet | 0 € |
| Databricks Free Edition | Laboratorio: práctica del temario y de la UI, notebooks, Unity Catalog, jobs serverless, MLflow | 0 € (no comercial, cuotas) |
| Workspace pagado (Azure) | Pipeline real: job semanal, datos en ADLS, dashboard | ≤ 3 €/mes |

Free Edition es serverless-only: no cubre clusters clásicos, autoscaling, spot,
Photon, pools, cluster policies, init scripts ni storage externo, y no tiene
GPU. **Tampoco tiene Spark UI** (serverless no lo expone; la documentación
remite al Query Profile), así que stages, shuffles, skew y executors se
practican en cluster clásico. Esos temas se practican en el workspace pagado
creando un cluster pequeño y terminándolo sin ejecutar nada (céntimos), con
sesiones cortas de all-purpose `D2ds_v4` (~0,41 €/20 min) o con runs de dev
(~0,13 €), o en el laboratorio del curso si lo incluye. La cuenta del
laboratorio usa un perfil de CLI propio (`free`) y nunca apunta a los targets
del bundle del proyecto; el PAT del workspace pagado no se copia a un equipo
ajeno.

**ML/MLOps (futuro, tras el examen):** entrenar en local o en el laboratorio,
registrar en Unity Catalog del laboratorio y **puntuar en batch** dentro del job
semanal (coste marginal ~0,01 €). Un endpoint de Model Serving encendido 24/7
costaría ~0,75 €/h y rompería el presupuesto: si se enseña serving, con
scale-to-zero y demo corta.

**Qué se prueba en local (gratis):**

- Funciones puras y transformaciones con pyspark `local[1]` y fixtures pequeñas
  (ya existe en `databricks_notebooks/tests`).
- El SQL generado por el upsert/MERGE y sus condiciones, como aserciones sobre
  el texto (patrón ya usado en la 008, T-02).
- Idempotencia de los exports con ficheros temporales.
- `databricks bundle validate` (gratis) para todo cambio de configuración.

**Qué exige Databricks (y hay que racionar):**

- `cloudFiles` (Auto Loader) real, widgets, tablas de Unity Catalog y el
  comportamiento del job cluster.
- `bundle deploy` (gratis, no ejecuta) y un run de dev por hito.
- `DESCRIBE HISTORY`, CDF y `system.*`: son SQL; se agrupan en **una sola
  sesión** (<10 min) para pagar un único despertar.
- Grants y metadatos: por CLI/API de Unity Catalog (gratis), no por SQL.

**Presupuesto por spec (máximo, justificado en `tasks.md`):**

| Spec | Runs de dev | Sesiones SQL | Coste máx. |
|---|---|---|---|
| Coste y cadencia | 2 | 0–1 | ≤ 1,8 € |
| Delta e incrementalidad | 3 | 1 | ≤ 2,0 € |
| Rendimiento y layout | 2 | 1 | ≤ 1,8 € |
| Streaming y observabilidad | 3 | 1 | ≤ 2,0 € |
| Seguridad y gobernanza | 0 | 1 | ≤ 1,3 € |
| **Total del track** | **10** | **4–5** | **≤ 9 €** |

Cada run de dev se anota en `tasks.md` con su motivo; si una tarea necesita más,
se justifica y se descuenta de la siguiente. Para SQL ad hoc, preferir un run
corto de un notebook en el job dev (0,03–0,13 €) antes que el SQL Editor
(1,29 €). Dev usa `D2ds_v4` o serverless Standard para que cada run cueste la
mitad.

**Dependencia nueva:** ninguna. `delta-spark` para ejecutar MERGE en local queda
como opción solo si las aserciones sobre el SQL se vuelven frágiles, y
requeriría justificación explícita (constitución 1).
