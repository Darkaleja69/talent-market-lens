# Evidencia — spec 009 (coste y cadencia)

Registro de datos medidos y de las comprobaciones de cada tarea. Sin
credenciales, sin SAS y sin tokens.

## T-00 — Línea base del job (API de Jobs, 2026-10-23)

Fuente: `databricks jobs list-runs --limit 40 -o json` y
`databricks jobs get-run <id> -o json` (plano de control, no enciende compute).

| Fecha | Job | Duración | Estado |
|---|---|---|---|
| 2026-10-22 23:00 | Git Based ELT (prod) | 19,2 min | SUCCESS |
| 2026-10-21 23:20 | Git Based ELT Dev | 13,6 min | SUCCESS |
| 2026-10-21 23:00 | Git Based ELT (prod) | 18,9 min | SUCCESS |
| 2026-10-21 22:38 | Git Based ELT Dev | 9,2 min | FAILED |
| 2026-10-20 23:00 | Git Based ELT (prod) | 20,2 min | SUCCESS |
| 2026-10-19 23:00 | Git Based ELT (prod) | 19,8 min | SUCCESS |
| 2026-10-18 23:00 | Git Based ELT (prod) | 18,2 min | SUCCESS |
| 2026-10-18 11:16 | Git Based ELT Dev | 17,7 min | SUCCESS |
| 2026-10-17 18:40 | Git Based ELT Dev | **337,7 min** | CANCELED |
| 2026-10-16 22:12 | Git Based ELT Dev | **114,1 min** | CANCELED |
| 2026-10-07 23:00 | Git Based ELT (prod) | 18,2 min | SUCCESS |
| 2026-10-06 23:00 | Git Based ELT (prod) | 8,4 min | SUCCESS |
| 2026-10-05 23:00 | Git Based ELT (prod) | 8,0 min | SUCCESS |

Observaciones:

- El run de producción se estabilizó en ~19 min; antes del 7-oct duraba ~8 min.
- Los dos runs de dev cancelados (16 y 17-oct) **no** explican los picos: la
  Account Console muestra ~2 $ de jobs esa semana y ~20,5 $ de SQL. El timeout
  de 1800 s (`a66b835`) sigue siendo una red de seguridad, pero no era la causa.

### Desglose del run de producción `683506792777132` (22-oct)

| Tarea | Aprovisionamiento | Ejecución | Estado |
|---|---|---|---|
| `Bronze_Ingestion` | **6,9 min** | 1,6 min | SUCCESS |
| `Silver_Infojobs` | 0,02 min | **8,9 min** | SUCCESS |
| `Silver_Linkedin` | 0,03 min | 4,7 min | SUCCESS |
| `Silver_Multi_site` | 0,02 min | 4,2 min | SUCCESS |
| `Silver_Indeed` | 0,03 min | 4,1 min | SUCCESS |
| `Gold_Complete_Catalog` | 0,03 min | 1,2 min | SUCCESS |
| `Build_for_Web-Dashboard` | 0,02 min | 0,5 min | SUCCESS |

Las cuatro silvers corren en paralelo sobre un único nodo; el run lo marca
`Silver_Infojobs` (8,9 min). El 80 % del tiempo es aprovisionamiento (6,9 min) +
InfoJobs (8,9 min).

## T-00 — Configuración relevante

| Elemento | Valor |
|---|---|
| Compute del job | `Standard_D4ds_v4`, `is_single_node: true`, `DATA_SECURITY_MODE_DEDICATED`, `kind: CLASSIC_PREVIEW`, runtime `17.3.x-scala2.13` |
| Timeout | `1800 s` en dev y prod (commit `a66b835`, 18-oct-2026) |
| Programación | `50 0 23 * * ?`, `Europe/Madrid` (diaria) |
| Concurrencia | `max_concurrent_runs: 1` |
| SQL warehouse del informe | `53d8e3e91af6749c` (`Serverless Starter Warehouse`), serverless, **Small = 12 DBU/h**, Photon, `auto_stop_mins = 10`, `state: STOPPED` (comprobado 2026-10-23) |
| Tablas del informe que usan Databricks | `Fact_Offers`, `Fact_OfferSkills`, `Dim_SkillList`, `Dim_Calendar`, `Dim_Companies`, `Dim_Currency` |
| Export público | `gold/web_export`: 4 tablas, `full`/`aggregated` con umbral 25 MiB |

## T-00 — Modelo de coste (a confirmar en T-01)

Tarifa de lista Premium, oct-2026. El coste de VM y almacenamiento no está en
`system.billing.usage`: se cruza con Azure Cost Analysis.

| Concepto | Cálculo | Coste |
|---|---|---|
| Job clásico | 19,3 min de vida del cluster (23:00:56–23:20:12) × (2 DBU/h × 0,30 $ + VM ~0,23 $) ≈ 0,75 €/h | **0,24 €** |
| Despertar del warehouse | 10 min × **12 DBU/h** (Small serverless) × 0,70 $/DBU | **1,29 €** |
| Día con job + un refresh | 0,24 + 1,29 (+ IVA) | **≈ 1,85 €** |
| Run de dev colgado (337 min) | tope teórico 5,6 h × 0,75 €/h | ≈ 4,2 €, pero **no se facturó**: el SKU de jobs esa semana es ~2 $ |

El warehouse cuesta **~5× el job**. Los días de ~2 € cuadran con job + un
despertar; los de 5 €, con 3–4 despertares separados más de 10 min. **Los
~0,80 € de los días «solo job» no cuadran con la tarifa de lista**: o el job
consume más DBU de las estimadas, o esos días hubo también un despertar corto.
Lo resuelve T-01 con `system.billing.usage`.

## T-00 — Contrato del informe (base para T-02)

| Tabla del modelo | Origen Gold | Columnas usadas |
|---|---|---|
| `Fact_Offers` | `gold.fact_offers` | 31 = 29 de Gold (18 físicas públicas + 11 internas) + 2 derivadas (`GeoCountry`, `GeoRegion`) |
| `Fact_OfferSkills` | `gold.fact_offer_skills` | 2 |
| `Dim_SkillList` | `gold.dim_skill_list` | 2 |
| `Dim_Calendar` | `gold.dim_calendar` (filtro `Year` 2025–2027) | 14 |
| `Dim_Companies` | `gold.companies_complete_catalog` | 7 |
| `Dim_Currency` | `gold.dim_currency` | 5 |

El export público excluye 11 columnas de `fact_offers` por diseño; el export
del informe debe incluirlas, en un prefijo propio y siempre en modo `full`.

Inventario verificado columna a columna contra Unity Catalog en «T-02 —
Inventario del contrato del informe»: los conteos de esta tabla son correctos
(31/2/2/14/7/5), pero Gold tiene **29** columnas físicas en `fact_offers`: las
31 del modelo incluyen `GeoCountry` y `GeoRegion`, que se calculan en la
partición M y no existen en la tabla.

## T-02b — Hoja de ruta renumerada (2026-10-23)

- Ficheros: `docs/databricks-de/roadmap.md` (+55/−17), `notes/README.md` y
  `notes/05-cicd.md` (referencias cruzadas).
- Renumeración: rendimiento 009→010, streaming 010→011, gobernanza 011→012;
  coste y cadencia → 009 (transversal).
- Calendario: 009 del 23 oct al 1 nov; 008 del 2 al 22 nov; 010 del 23 al 29
  nov; 011 del 30 nov al 6 dic; 012 del 7 al 13 dic; repaso y examen del 14 al
  27 dic.
- Política: tres entornos (local, Free Edition como laboratorio, workspace
  pagado), presupuesto de runs (≤ 9 € en el track) y aviso de que Free Edition
  no tiene Spark UI.
- Verificación independiente: PASA CON OBSERVACIONES, sin bloqueantes.
  Corregidos los dos hallazgos `MENOR`: etiqueta de semana 10–11 y matiz de la
  cifra de 0,24 € (estimación, no medición).

## T-02 — Inventario del contrato del informe (2026-10-23)

Comprobación hecha **solo con la API de Unity Catalog** (plano de control,
gratis): no se usó SQL, ni el SQL Editor, ni el warehouse, ni
`databricks bundle run`; **no se encendió compute**. Comando ejecutado una vez
por tabla, con el perfil `vs_code`:

```text
databricks api get /api/2.1/unity-catalog/tables/job_offers.gold.<tabla> --profile vs_code -o json
```

Las seis llamadas devolvieron `exit 0`. Todas las tablas existen en `gold`, son
`MANAGED` y `DELTA`. Del JSON se leyeron `full_name`, `table_type`,
`data_source_format` y `columns[].name`, `columns[].type_name` y
`columns[].type_text` (salida
resumida; la respuesta es metadato y no contiene credenciales).

| Tabla Gold | `full_name` | Tipo | Formato | Columnas |
|---|---|---|---|---|
| `fact_offers` | `job_offers.gold.fact_offers` | MANAGED | DELTA | **29** |
| `fact_offer_skills` | `job_offers.gold.fact_offer_skills` | MANAGED | DELTA | **2** |
| `dim_skill_list` | `job_offers.gold.dim_skill_list` | MANAGED | DELTA | **2** |
| `dim_calendar` | `job_offers.gold.dim_calendar` | MANAGED | DELTA | **14** |
| `companies_complete_catalog` | `job_offers.gold.companies_complete_catalog` | MANAGED | DELTA | **7** |
| `dim_currency` | `job_offers.gold.dim_currency` | MANAGED | DELTA | **5** |

### Resumen por tabla

Columnas del modelo = `sourceColumn` del TMDL (columnas con `sourceColumn`; dos
son derivadas en M). Export
público = `WEB_FACT_COLUMNS`, `WEB_OFFER_SKILL_COLUMNS`,
`WEB_SKILL_LIST_COLUMNS` y `WEB_CALENDAR_COLUMNS` (el export público solo
escribe cuatro tablas).

`Fact_Offers.tmdl` declara 32 columnas: las 31 con `sourceColumn` más `MapKey`,
que es una columna DAX calculada y no se exporta. El inventario cuenta 31.

| Tabla del modelo | Origen Gold | Cols. en Gold | Cols. del modelo | En export público | Columnas del modelo que **no** están en el export público | Veredicto |
|---|---|---|---|---|---|---|
| `Fact_Offers` | `gold.fact_offers` | 29 | **31** | 20 | **11**: `work_mode`, `is_salary_available`, `salary_currency`, `salary_period`, `description_clean`, `skills`, `salary_quality`, `skills_source`, `experience_level_source`, `posted_date_raw`, `posted_date_source` | **Faltantes**: 2 de las 31 (`GeoCountry`, `GeoRegion`) no existen en Gold (se calculan en M); las otras 29 sí están |
| `Fact_OfferSkills` | `gold.fact_offer_skills` | 2 | **2** | 2 | — | Todas presentes |
| `Dim_SkillList` | `gold.dim_skill_list` | 2 | **2** | 2 | — | Todas presentes |
| `Dim_Calendar` | `gold.dim_calendar` (filtro `Year` 2025–2027) | 14 | **14** | 14 | — | Todas presentes |
| `Dim_Companies` | `gold.companies_complete_catalog` | 7 | **7** | 0 (no se publica) | **7**: `company_name`, `company_industry`, `company_description`, `company_size`, `company_review_count`, `company_rating`, `source` | Todas presentes |
| `Dim_Currency` | `gold.dim_currency` | 5 | **5** | 0 (no se publica) | **5**: `CurrencyCode`, `CurrencyName`, `AltCode`, `UnitsPerEUR`, `FXSnapshotDate` | Todas presentes |

Gold no tiene ninguna columna que el modelo no use: `fact_offers` son 29
físicas y las 31 del modelo son esas 29 más las 2 calculadas.

### Las 31 columnas de `fact_offers` (modelo) frente a Gold

| # | Columna | Tipo en Gold (Unity Catalog) | Tipo declarado en el modelo | ¿En el export público? |
|---|---|---|---|---|
| 1 | `job_id` | STRING | string | Sí |
| 2 | `job_url` | STRING | string | Sí |
| 3 | `title` | STRING | string | Sí |
| 4 | `company_name` | STRING | string | Sí |
| 5 | `location_city` | STRING | string | Sí |
| 6 | `location_region` | STRING | string | Sí |
| 7 | `location_country` | STRING | string | Sí |
| 8 | `posted_date` | DATE | dateTime (`UnderlyingDateTimeDataType = Date`) | Sí |
| 9 | `work_mode` | STRING | string | **No** |
| 10 | `is_salary_available` | BOOLEAN | boolean | **No** |
| 11 | `salary_currency` | STRING | string | **No** |
| 12 | `salary_period` | STRING | string | **No** |
| 13 | `experience_level` | STRING | string | Sí |
| 14 | `description_clean` | STRING | string | **No** |
| 15 | `skills` | **ARRAY\<string\>** | string | **No** |
| 16 | `source_scraper` | STRING | string | Sí |
| 17 | `employment_type` | STRING | string | Sí |
| 18 | `role_category` | STRING | string | Sí |
| 19 | `SalaryMinAnnual_EUR` | DOUBLE | double | Sí |
| 20 | `SalaryMaxAnnual_EUR` | DOUBLE | double | Sí |
| 21 | `SalaryMidAnnual_EUR` | DOUBLE | double | Sí |
| 22 | `PostedYearMonth` | STRING | string | Sí |
| 23 | `WorkModeBucket` | STRING | string | Sí |
| 24 | `salary_quality` | STRING | string | **No** |
| 25 | `skills_source` | STRING | string | **No** |
| 26 | `experience_level_source` | STRING | string | **No** |
| 27 | `posted_date_raw` | DATE | dateTime (`UnderlyingDateTimeDataType = Date`) | **No** |
| 28 | `posted_date_source` | STRING | string | **No** |
| 29 | `GeoCountry` | **no existe** (calculada en M) | string | Sí (el export público la calcula) |
| 30 | `GeoRegion` | **no existe** (calculada en M) | string | Sí (el export público la calcula) |
| 31 | `IsValidPostingDate` | BOOLEAN | boolean | Sí |

Recuento: 29 físicas + 2 calculadas = 31; 20 en el export público y **11** que
solo necesita el informe.

### Columnas de las cinco dimensiones (salida de la API)

| Tabla Gold | Columnas (`nombre: tipo`) |
|---|---|
| `fact_offer_skills` | `JobID: STRING`, `Skill: STRING` |
| `dim_skill_list` | `SkillName: STRING`, `SkillCategory: STRING` |
| `dim_calendar` | `Date: DATE`, `DateKey: INT`, `Year: INT`, `MonthNumber: INT`, `MonthName: STRING`, `ShortMonth: STRING`, `YearMonth: STRING`, `Quarter: INT`, `YearQuarter: STRING`, `WeekISO: INT`, `Day: INT`, `WeekDay: INT`, `DayName: STRING`, `IsWeekend: BOOLEAN` |
| `companies_complete_catalog` | `company_name: STRING`, `company_industry: STRING`, `company_description: STRING`, `company_size: STRING`, `source: STRING`, `company_rating: DOUBLE`, `company_review_count: LONG` |
| `dim_currency` | `CurrencyCode: STRING`, `CurrencyName: STRING`, `AltCode: STRING`, `UnitsPerEUR: DOUBLE`, `FXSnapshotDate: STRING` |

### Discrepancias encontradas

1. **Nombres:** ninguna. Las 6 tablas coinciden exactamente (incluido el
   uso de mayúsculas) con los `sourceColumn` del TMDL; no hay columnas del
   modelo sin origen identificado.
2. **`GeoCountry` y `GeoRegion` no existen en Gold.** El modelo las declara con
   `sourceColumn` pero las crea en la partición M
   (`Fact_Offers.tmdl:279-284`) a partir de `location_country`,
   `location_region` y `Dim_RegionMap`; el export público hace lo mismo en
   Python (`Prepare_Web_Export.project_fact_offers`). Por tanto **Gold tiene 29
   columnas, no 31**, y el export del informe no puede limitarse a proyectar 31
   columnas de `gold.fact_offers`: las deriva reutilizando `REGION_MAP` y la
   normalización de país de `Prepare_Web_Export` (no `project_fact_offers`, que
   proyecta solo las 20 columnas públicas).
3. **Tipo de `skills`: Gold es `ARRAY<string>` y el modelo declara `string`.**
   En Parquet la columna llegará como lista; el export del informe necesita
   convertirla a texto (o el modelo cambiar el tipo) para que el contrato se
   cumpla.
4. **Tipos equivalentes, sin discrepancia real:** `DATE` de Gold frente a
   `dateTime` con `UnderlyingDateTimeDataType = Date` en el modelo
   (`posted_date`, `posted_date_raw`, `Date` de `dim_calendar`); `INT`/`LONG`
   de Gold frente a `int64` en el modelo; `BOOLEAN` frente a `boolean`.
   `FXSnapshotDate` es `STRING` en Gold y `string` en el modelo: coinciden.
5. **Artefacto documental en T-00:** el paréntesis de la fila `Fact_Offers`
   sumaba 20 + 11 + `GeoCountry`/`GeoRegion` = 33, pero los 20 del export
   público ya incluyen esas dos columnas. El conteo de 31 es correcto.

### Comprobación de `dim_companies` y `dim_currency`

- `companies_complete_catalog` **existe** en `job_offers.gold` (`MANAGED`,
  `DELTA`) con **7** columnas, y cubre las **7** de `Dim_Companies` con el
  mismo nombre y tipo compatible (`company_review_count: LONG` ↔ `int64`,
  `company_rating: DOUBLE` ↔ `double`). No falta ninguna.
- `dim_currency` **existe** en `job_offers.gold` (`MANAGED`, `DELTA`) con **5**
  columnas, y cubre las **5** de `Dim_Currency` (`UnitsPerEUR: DOUBLE` ↔
  `double`). No falta ninguna.
- Ninguna de las dos se publica en `gold/web_export`: el export del informe será
  la primera vez que se escriben como Parquet.

### Nota de método y alcance

- Fuente: **API de Unity Catalog** (`GET /api/2.1/unity-catalog/tables/...`),
  que es plano de control y **gratis**. No se ejecutó SQL, no se abrió el SQL
  Editor y no se envió ninguna consulta al warehouse (que según T-00 estaba
  `STOPPED`); tampoco se lanzó ningún run de bundle ni de job: **no se encendió
  compute**.
- No se pidió `DESCRIBE TABLE` (habría costado ~1,29 € por despertar el
  warehouse) ni se usó `databricks bundle run`.
- Alcance: solo metadatos (nombres, tipos, existencia). Los conteos de filas y
  el contenido no se comprueban aquí; eso corresponde a T-07 contra datos
  reales de dev.
- Consecuencia: T-05 materializa `GeoCountry`/`GeoRegion` con `REGION_MAP` y la
  normalización de país (usando el par crudo `location_country`/`location_region`)
  y convierte `skills` a texto con `concat_ws(", ", coalesce(skills, array()))`;
  T-09 elimina la lógica M porque el export ya trae las dos columnas.

## T-01 — Línea base de coste (2026-10-23)

- Fuente: Azure Cost Analysis (EUR, 1–23 oct) y Databricks Account Console →
  Usage (USD, por semana). Capturas no versionadas.
- Azure: **31,24 €** reales; previsión **50,17 €** (inflada por el pico).
- SKU dominante: **`PREMIUM_SERVERLESS_SQL_COMPUTE` (85–90 % del gasto)**.
- Semana del 12-oct: ~20,5 $ de SQL frente a ~2,0 $ de jobs. El pico del
  17-oct (~10,6 €) son ~15 despertares de 1,29 € durante una jornada de
  depuración, no el run de dev de 337 min.
- Días sin warehouse: ~0,2–0,5 € (el job), no 0,80 €.
- Documento: `docs/costs.md`. Consulta reproducible:
  `docs/costs/billing_by_sku.sql`.
- Pendiente: detalle por recurso del resource group gestionado y origen de
  `PREMIUM_JOBS_SERVERLESS_COMPUTE` (~1 $/semana).

## T-03 — Cron semanal desplegado (2026-10-23)

- `databricks jobs get 916863262593632` (prod): `quartz_cron_expression =
  50 0 23 ? * SUN`, `timezone_id = Europe/Madrid`, `pause_status = UNPAUSED`.
- El fichero llegó a tener `50 0 23 * 7 ?` (mes de julio); se corrigió a
  `? * SUN` antes del deploy. Primer run programado: domingo 2026-10-25 23:00.
- Job manual `704156530681034` sigue `PAUSED`; dev `320947022571947` sin
  schedule.

## T-04 — Dashboard semanal y frescura (2026-10-23)

- `schedule.cron = 0 1 * * MON`; frescura: >8 días, sin `data_date` o fecha
  inválida → rc=1 con mensaje en español y sin desplegar.
- Verificación local (verifier): hoy 0, 8 días 0, 9/20 días 1, sin `data_date`
  1, inválido 1; YAML parseable; sin secretos; `set -euo pipefail` intacto.
- Pendiente: `workflow_dispatch` real (requiere push/merge); la URL del run se
  anota al publicar.

## T-13/T-14 — Warehouse y deploy (2026-10-23)

- Warehouse `53d8e3e91af6749c`: `cluster_size = 2X-Small`, `state = STOPPED`,
  `auto_stop_mins = 10`; despertar ~0,43 €.
- Deploy a prod ejecutado por la persona; ver T-03. Pendiente el primer run
  programado (domingo 25-oct) y el presupuesto con alertas.

## T-06 — Tarea del export en el job (2026-10-23)

- `bundle validate -t dev` y `-t prod`: **Validation OK**.
- `summary -o json`: `Build_Report_Export` con `depends_on:
  Gold_Complete_Catalog`, `run_if: ALL_SUCCESS`, `job_cluster_key: tm-lens-job`;
  params dev `{catalog: job_offers_dev, pbi_export_prefix: gold/pbi_export_dev}`
  y prod `{catalog: job_offers, pbi_export_prefix: gold/pbi_export}`.
- `Build_for_Web-Dashboard` sigue dependiendo solo de `Gold_Complete_Catalog`.
- Endurecido tras la verificación: `pbi_export_prefix` sin default y con
  validación, para que un run manual en dev no escriba en producción por
  olvido.
- Pendiente: escritura real, 6+1 ficheros y conteos (T-07).

## T-12 — Compute: `D2ds_v6` medido y descartado (2026-10-23)

- `Standard_D2ds_v4` no está en el catálogo del workspace; se probó
  `Standard_D2ds_v6` (2 vCPU/8 GB, disponible).
- `is_single_node: true` exige `kind: CLASSIC_PREVIEW`: se mantiene (la idea de
  quitarlo era errónea; `bundle validate` no lo detecta).
- Run dev `954013261343017`: **FAILED** a los 18,2 min. El driver dejó de
  responder (`DRIVER_NOT_RESPONDING`) y los kernels Python de las cuatro
  silvers no arrancaron; aprovisionamiento de 9,9 min. Coste ~0,13 €.
- Decisión: se vuelve a `Standard_D4ds_v4`; el ajuste de compute (serverless,
  `D2ds_v6`/`D4ds_v6`) pasa a la 010.
- `bundle validate -t dev/prod` con `D4ds_v4`: OK.

## T-07 — Run dev del export (intento 1, 2026-10-23)

- Warehouse antes/después: `STOPPED` (2X-Small), 0 clusters, 0 sesiones; no se
  despertó.
- Deploy dev OK; job dev con 8 tareas, `D2ds_v6` (intento),
  `pbi_export_prefix=gold/pbi_export_dev`.
- Run `954013261343017`: **FAILED**, 18,2 min. `Bronze_Ingestion` SUCCESS
  (3,4 min); las cuatro silvers `INTERNAL_ERROR` por timeout del kernel Python
  («The Python kernel did not start within the timeout...»); Gold, web y report
  export `SKIPPED`.
- `gold/pbi_export_dev`: 0 ficheros (el export no llegó a ejecutarse).
  `gold/web_export_dev` intacto (5 ficheros, mismos tamaños y fecha).
- Conclusión: fallo de compute, no de código; se repite en `D4ds_v4`.

## T-07 — Run dev del export (intento 2, SUCCESS) — 2026-10-23

- Run `18912486133844`: **SUCCESS**, 16,31 min, 8/8 tareas; cluster
  `Standard_D4ds_v4` single node (driver idéntico), aprovisionamiento ~6,4 min.
- Tareas: Bronze 382 s setup + 71 s; silvers 265–428 s; Gold 52 s;
  `Build_Report_Export` 38 s; `Build_for_Web-Dashboard` 36 s.
- `gold/pbi_export_dev`: 7 ficheros (6 Parquet + `meta.json`), 33 291 569 B.
- `meta.json`: `mode=full`, `audience=pbi`, `data_date=2026-10-22`, conteos
  `{fact_offers: 25 673, fact_offer_skills: 44 537, dim_calendar: 2 912,
  dim_companies: 9 801, dim_currency: 19, dim_skill_list: 95}` y
  `size_bytes` = suma exacta de los seis Parquet.
- `fact_offers.parquet`: 25 673 filas × **31 columnas** (20 públicas + 11
  internas + `GeoCountry`/`GeoRegion`).
- `gold/web_export_dev`: 5 ficheros regenerados con el mismo contrato
  (`fact_offers` 20 columnas, 25 673 filas; dimensiones idénticas).
- Warehouse antes/después: `STOPPED`, 0 clusters, 0 sesiones.
- Intento 1 (`954013261343017`, `D2ds_v6`): FAILED por compute (ver T-12).
- Nota: la comparación de conteos contra Gold por SQL no se hizo (habría
  despertado el warehouse); los conteos los produce el notebook al leer Gold y
  coinciden con `meta.json`.
