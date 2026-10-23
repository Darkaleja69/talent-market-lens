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
- Los dos runs de dev cancelados (16 y 17-oct) son los picos de factura. El
  `timeout_seconds: 1800` del commit `a66b835` (18-oct) los acota a ~0,38 €.

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
| Run de dev colgado (337 min) | 5,6 h × 0,75 €/h | **≈ 2,75 €** |

El warehouse cuesta **~5× el job**. Los días de ~2 € cuadran con job + un
despertar; los de 5 €, con 3–4 despertares separados más de 10 min. **Los
~0,80 € de los días «solo job» no cuadran con la tarifa de lista**: o el job
consume más DBU de las estimadas, o esos días hubo también un despertar corto.
Lo resuelve T-01 con `system.billing.usage`.

## T-00 — Contrato del informe (base para T-02)

| Tabla del modelo | Origen Gold | Columnas usadas |
|---|---|---|
| `Fact_Offers` | `gold.fact_offers` | 31 (20 públicas + `work_mode`, `is_salary_available`, `salary_currency`, `salary_period`, `description_clean`, `skills`, `salary_quality`, `skills_source`, `experience_level_source`, `posted_date_raw`, `posted_date_source`, más `GeoCountry`/`GeoRegion`) |
| `Fact_OfferSkills` | `gold.fact_offer_skills` | 2 |
| `Dim_SkillList` | `gold.dim_skill_list` | 2 |
| `Dim_Calendar` | `gold.dim_calendar` (filtro `Year` 2025–2027) | 14 |
| `Dim_Companies` | `gold.companies_complete_catalog` | 7 |
| `Dim_Currency` | `gold.dim_currency` | 5 |

El export público excluye 11 columnas de `fact_offers` por diseño; el export
del informe debe incluirlas, en un prefijo propio y siempre en modo `full`.

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
