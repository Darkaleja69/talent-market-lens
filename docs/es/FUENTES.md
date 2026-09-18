# Fuentes de datos y contrato de entrada/salida

Documento de referencia de las fuentes que alimentan el pipeline Medallion.
Está alineado con las **versiones vigentes** de los notebooks de
`databricks_notebooks/` (no con documentación antigua del repositorio).

---

## 1. Visión general

```
Scraper local (PC)            ADLS Gen2            Delta (Unity Catalog)
─────────────────             ─────────            ─────────────────────
indeed_jobs_scraper      ─┐
linkedin_jobs_scraper    ─┤   landing/<fuente>/    bronze.<fuente>
infojobs_jobs_scraper    ─┼─▶  (*.parquet)      ─▶  (Auto Loader,
multi_site_job_scraper   ─┘                         append + mergeSchema)
                                                    │
                                                    ▼
                                              silver.offers_<fuente>
                                              silver.companies_<fuente>
                                              (enriquecimiento + normalización + dedup)
                                                    │
                                                    ▼
                                              gold.fact_offers
                                              gold.fact_offer_skills
                                              gold.dim_currency
                                              gold.dim_skill_list
                                              gold.dim_calendar
                                              gold.companies_complete_catalog
```

- Una **tabla Bronze por fuente** (`job_offers.bronze.{source}`), ingesta
  incremental con Auto Loader (`cloudFiles`), Parquet de entrada, Delta de
  salida, `mergeSchema` y `trigger(availableNow=True)`.
- **Un notebook Silver por fuente** (`Transformacion Plata {Fuente}.ipynb`) que
  normaliza, enriquece y deduplica. Escribe `silver.offers_<fuente>` y
  `silver.companies_<fuente>`.
- Un único notebook **Oro** (`Transformacion Oro Complete_Catalog.ipynb`) que
  unifica las cuatro Silver con `unionByName(allowMissingColumns=True)`,
  deduplica y construye las tablas Gold.

---

## 2. Fuentes implementadas

| # | Fuente | Scraper local | Bronze | Silver | País foco | Formato |
|---|--------|---------------|--------|--------|-----------|---------|
| 1 | Indeed | `indeed_jobs_scraper/` | `bronze.indeed` | `offers_indeed`, `companies_indeed` | ES | Parquet |
| 2 | LinkedIn | `linkedin_jobs_scraper/` | `bronze.linkedin` | `offers_linkedin`, `companies_linkedin` | Multi | Parquet |
| 3 | InfoJobs | `infojobs_jobs_scraper/` | `bronze.infojobs` | `offers_infojobs`, `companies_infojobs` | ES | Parquet |
| 4 | Multi-site | `multi_site_job_scraper/` | `bronze.multi_site` | `offers_multi_site`, `companies_multi_site` | IE / NL / CH / US | Parquet |

**Multi-site** agrega cuatro portales en un solo fichero
`jobs_unified.parquet`: `irishjobs`, `stepstone_nl`, `jobs_ch` y `glassdoor`.

> Estado: las **cuatro fuentes están implementadas** en Bronze y Silver.

---

## 3. Contrato común de Silver (esquema destino)

Todas las fuentes se proyectan a un mismo esquema común antes de enriquecer:

| Columna | Tipo | Descripción |
|---|---|---|
| `job_id` | string | Identificador único de la oferta. |
| `job_url` | string | URL de la publicación original. |
| `title` | string | Título del puesto. |
| `company_name` | string | Empresa (normalizada a Title Case + nombre canónico). |
| `location_city` | string | Ciudad (normalizada al nombre canónico de `Dim_Geo`). |
| `location_region` | string | Región / comunidad / estado. |
| `location_country` | string | País normalizado a inglés canónico. |
| `posted_date` | date | Fecha de publicación (`posted_datetime`, relativa o `scraped_at`). |
| `work_mode` | string | Modalidad (`Remote` / `Hybrid` / `On-site` / vacío). |
| `is_salary_available` | boolean | `True` si `salary_raw` no está vacío. |
| `salary_currency` | string | Divisa ISO (`EUR`, `USD`, ...). |
| `salary_min` / `salary_max` | double | Rango salarial original (si existe). |
| `salary_period` | string | Periodo original; en Silver se sustituye por el normalizado `YEAR/MONTH/WEEK`. |
| `salary_raw` | string | Texto bruto del salario. |
| `experience_level` | string | Seniority inferido (título → descripción). |
| `description_clean` | string | Descripción en texto plano. |
| `skills` | string/array | Skills detectadas (catálogo canónico). |
| `source_scraper` | string | Origen (`Indeed`, `Linkedin`, ...). |
| `search_role` | string | Keyword de búsqueda del scraper. |
| `scraped_at` | timestamp | Fecha de scraping (se elimina en la salida Silver). |
| `_ingest_date` | date | Fecha de ingesta en Bronze (se elimina en la salida Silver). |

El enriquecimiento (columna adicional) viene de `Enrich_Job_Offers_Dataframes.enrich()` y
añade, entre otras: `role_category`, `employment_type`, `salary_min_annual`,
`salary_max_annual`, `salary_quality`, `salary_period_norm`, `work_mode_norm`,
`salary_source`, `work_mode_source`, `experience_level_source`,
`skills_source`, `posted_date_raw`, `posted_date_source`.

### Diferencias de columnas entre Silver

No todas las fuentes conservan las mismas columnas al final (se documenta tal
cual y `unionByName(allowMissingColumns=True)` lo tolera):

- **Indeed**: elimina `salary_min` y `salary_max` (solo conserva el salario
  anual calculado).
- **LinkedIn / InfoJobs / Multi-site**: conservan `salary_min` y `salary_max`.

---

## 4. Mapeo de columnas Bronze → Silver por fuente

### 4.1 Indeed

| Bronze | Silver |
|---|---|
| `job_key` | `job_id` |
| `viewjob_url` | `job_url` |
| `company` | `company_name` |
| `city` | `location_city` |
| `state` | `location_region` |
| `country` | `location_country` |
| `description_text` | `description_clean` |
| `salary_text` | `salary_raw` |
| `salary_type` | `salary_period` |
| `workplace_type` | `work_mode` |
| `search_term` | `search_role` |
| `ingest_date` | `_ingest_date` |

Fijos: `salary_currency = "EUR"`, `source_scraper = "Indeed"`.

### 4.2 InfoJobs

| Bronze | Silver |
|---|---|
| `id_oferta` | `job_id` |
| `url_oferta` | `job_url` |
| `titulo` | `title` |
| `empresa` | `company_name` |
| `ciudad` | `location_city` |
| `provincia` | `location_region` |
| `pais` | `location_country` |
| `fecha_publicacion` | `posted_date` |
| `modalidad` | `work_mode` |
| `moneda` | `salary_currency` |
| `salario_min` / `salario_max` | `salary_min` / `salary_max` |
| `salario_raw` | `salary_raw` |
| `periodo` | `salary_period` |
| `descripcion_snippet` | `description_clean` |
| `jornada` | `employment_type` |
| `fecha_scraped` | `scraped_at` |
| `fuente` | `source_scraper` |
| `keyword_buscada` | `search_role` |

Además aplica `normalize_location_codes()`: códigos ISO de región/provincia
(`MD`, `CT`, ...) y país (`ES`, `IE`, ...) se convierten a nombre legible.

### 4.3 LinkedIn

| Bronze | Silver |
|---|---|
| `description_full` | `description_clean` |
| `posted_datetime` / `posted_relative` | `posted_date` (relativa calculada sobre `scraped_at`) |

Mantiene `company_size`, `company_industry`, `company_description` en
`companies_linkedin`. Es la única fuente con `skills` estructuradas de origen.

### 4.4 Multi-site

| Bronze | Silver |
|---|---|
| `description_snippet` | `description_clean` |
| `site` | `source_scraper` |
| `salary_disclosed` | `is_salary_available` |
| `posted_relative` | `posted_date` (relativa calculada sobre `scraped_at`) |

---

## 5. Tablas Gold

| Tabla | Contenido | Construida por |
|---|---|---|
| `gold.fact_offers` | Ofertas unificadas con salario en EUR, `WorkModeBucket`, `PostedYearMonth`, `IsValidPostingDate`, rol, seniority y skills. | `Prepare_Gold.build_fact_offers()` |
| `gold.fact_offer_skills` | Una fila por oferta + skill (puente). | `build_fact_offer_skills()` |
| `gold.dim_currency` | Divisas + `UnitsPerEUR` (snapshot `FX_SNAPSHOT_DATE = 2026-09-04`). | `dim_currency()` |
| `gold.dim_skill_list` | Catálogo `SkillName` + `SkillCategory`. | `dim_skill_list()` |
| `gold.dim_calendar` | Calendario diario con etiquetas ES. | `dim_calendar()` |
| `gold.companies_complete_catalog` | Empresas de las 4 fuentes deduplicadas. | Notebook Oro (celda final). |

> El **mapa** (`GeoCountry`, `GeoRegion`, `Dim_Geo`, `Dim_RegionMap`,
> coordenadas, TopoJSON) se mantiene en Power BI y **no** se genera en Gold.

---

## 6. Calidad de datos

La calidad se gestiona de forma explícita mediante banderas, para que el
consumidor conozca siempre la procedencia y fiabilidad de cada campo:

- **Salarios** — cada valor se clasifica con `salary_quality` (`ok` / `missing` /
  `unknown_period` / `outlier_review` / `invalid`) y se convierte a EUR anual
  solo cuando el periodo es conocido.
- **Fechas** — el origen de cada fecha se conserva en `posted_date_source`
  (`posted` / `scraped`) y el valor original en `posted_date_raw`.
- **Geografía** — normalizada contra catálogos canónicos de los países
  soportados.
- **Divisas** — convertidas mediante un snapshot versionado en `dim_currency`.
- **Skills** — emparejadas contra un catálogo canónico, consistente entre
  fuentes.

## 7. Ampliar las fuentes

Añadir un portal nuevo consiste en crear un notebook Silver que reutilice
`Enrich_Job_Offers_Dataframes.enrich()` y escriba el esquema común de §3.
