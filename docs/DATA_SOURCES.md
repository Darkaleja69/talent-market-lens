# Data sources and input/output contract

Reference document for the sources feeding the Medallion pipeline. It is aligned
with the **current versions** of the notebooks in `databricks_notebooks/` (not
with older repository documentation).

---

## 1. Overview

```
Local scraper (PC)            ADLS Gen2            Delta (Unity Catalog)
─────────────────             ─────────            ─────────────────────
indeed_jobs_scraper      ─┐
linkedin_jobs_scraper    ─┤   landing/<source>/    bronze.<source>
infojobs_jobs_scraper    ─┼─▶  (*.parquet)      ─▶  (Auto Loader,
multi_site_job_scraper   ─┘                         append + mergeSchema)
                                                    │
                                                    ▼
                                              silver.offers_<source>
                                              silver.companies_<source>
                                              (enrich + normalize + dedup)
                                                    │
                                                    ▼
                                              gold.fact_offers
                                              gold.fact_offer_skills
                                              gold.dim_currency
                                              gold.dim_skill_list
                                              gold.dim_calendar
                                              gold.companies_complete_catalog
```

- **One Bronze table per source** (`job_offers.bronze.{source}`), incremental
  ingestion with Auto Loader (`cloudFiles`), Parquet input, Delta output,
  `mergeSchema` and `trigger(availableNow=True)`.
- **One Silver notebook per source** (`silver_<source>.ipynb`) that
  normalizes, enriches and de-duplicates. It writes `silver.offers_<source>` and
  `silver.companies_<source>`.
- A single **Gold notebook** (`gold_build.ipynb`) that
  unions the four Silver tables with `unionByName(allowMissingColumns=True)`,
  de-duplicates and builds the Gold tables.

---

## 2. Implemented sources

| # | Source | Local scraper | Bronze | Silver | Focus country | Format |
|---|--------|---------------|--------|--------|---------------|--------|
| 1 | Indeed | `indeed_jobs_scraper/` | `bronze.indeed` | `offers_indeed`, `companies_indeed` | ES | Parquet |
| 2 | LinkedIn | `linkedin_jobs_scraper/` | `bronze.linkedin` | `offers_linkedin`, `companies_linkedin` | Multi | Parquet |
| 3 | InfoJobs | `infojobs_jobs_scraper/` | `bronze.infojobs` | `offers_infojobs`, `companies_infojobs` | ES | Parquet |
| 4 | Multi-site | `multi_site_job_scraper/` | `bronze.multi_site` | `offers_multi_site`, `companies_multi_site` | IE / NL / CH / US | Parquet |

**Multi-site** aggregates four portals into a single `jobs_unified.parquet` file:
`irishjobs`, `stepstone_nl`, `jobs_ch` and `glassdoor`.

> Status: all **four sources are implemented** in Bronze and Silver.

---

## 3. Common Silver contract (target schema)

Every source is projected onto the same common schema before enrichment:

| Column | Type | Description |
|---|---|---|
| `job_id` | string | Unique offer identifier. |
| `job_url` | string | URL of the original posting. |
| `title` | string | Job title. |
| `company_name` | string | Company (Title Case + canonical name). |
| `location_city` | string | City (normalized to the canonical `Dim_Geo` name). |
| `location_region` | string | Region / community / state. |
| `location_country` | string | Country normalized to canonical English. |
| `posted_date` | date | Posting date (`posted_datetime`, relative, or `scraped_at`). |
| `work_mode` | string | Work mode (`Remote` / `Hybrid` / `On-site` / empty). |
| `is_salary_available` | boolean | `True` if `salary_raw` is not empty. |
| `salary_currency` | string | ISO currency (`EUR`, `USD`, ...). |
| `salary_min` / `salary_max` | double | Original salary range (if any). |
| `salary_period` | string | Original period; in Silver it is replaced by the normalized `YEAR/MONTH/WEEK`. |
| `salary_raw` | string | Raw salary text. |
| `experience_level` | string | Inferred seniority (title → description). |
| `description_clean` | string | Plain-text description. |
| `skills` | string/array | Detected skills (canonical catalog). |
| `source_scraper` | string | Origin (`Indeed`, `Linkedin`, ...). |
| `search_role` | string | Scraper search keyword. |
| `scraped_at` | timestamp | Scrape timestamp (dropped from the Silver output). |
| `_ingest_date` | date | Bronze ingest date (dropped from the Silver output). |

The enrichment (additional columns) comes from
`Enrich_Job_Offers_Dataframes.enrich()` and adds, among others: `role_category`,
`employment_type`, `salary_min_annual`, `salary_max_annual`, `salary_quality`,
`salary_period_norm`, `work_mode_norm`, `salary_source`, `work_mode_source`,
`experience_level_source`, `skills_source`, `posted_date_raw`,
`posted_date_source`.

### Column differences between Silver tables

Not every source keeps the same columns at the end (documented as-is;
`unionByName(allowMissingColumns=True)` tolerates it):

- **Indeed**: drops `salary_min` and `salary_max` (keeps only the computed annual
  salary).
- **LinkedIn / InfoJobs / Multi-site**: keep `salary_min` and `salary_max`.

---

## 4. Bronze → Silver column mapping per source

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

Fixed: `salary_currency = "EUR"`, `source_scraper = "Indeed"`.

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

It also applies `normalize_location_codes()`: ISO region/province codes (`MD`,
`CT`, ...) and country codes (`ES`, `IE`, ...) are converted to readable names.

### 4.3 LinkedIn

| Bronze | Silver |
|---|---|
| `description_full` | `description_clean` |
| `posted_datetime` / `posted_relative` | `posted_date` (relative, computed from `scraped_at`) |

It keeps `company_size`, `company_industry`, `company_description` in
`companies_linkedin`. It is the only source with structured `skills` at origin.

### 4.4 Multi-site

| Bronze | Silver |
|---|---|
| `description_snippet` | `description_clean` |
| `site` | `source_scraper` |
| `salary_disclosed` | `is_salary_available` |
| `posted_relative` | `posted_date` (relative, computed from `scraped_at`) |

---

## 5. Gold tables

| Table | Content | Built by |
|---|---|---|
| `gold.fact_offers` | Unified offers with EUR salary, `WorkModeBucket`, `PostedYearMonth`, `IsValidPostingDate`, role, seniority and skills. | `Prepare_Gold.build_fact_offers()` |
| `gold.fact_offer_skills` | One row per offer + skill (bridge). | `build_fact_offer_skills()` |
| `gold.dim_currency` | Currencies + `UnitsPerEUR` (snapshot `FX_SNAPSHOT_DATE = 2026-09-04`). | `dim_currency()` |
| `gold.dim_skill_list` | Catalog `SkillName` + `SkillCategory`. | `dim_skill_list()` |
| `gold.dim_calendar` | Daily calendar with localized labels. | `dim_calendar()` |
| `gold.companies_complete_catalog` | Companies from the 4 sources, de-duplicated. | Gold notebook (final cell). |

> The **map** (`GeoCountry`, `GeoRegion`, `Dim_Geo`, `Dim_RegionMap`,
> coordinates, TopoJSON) is maintained in Power BI and is **not** generated in
> Gold.

---

## 6. Data quality

Quality is handled explicitly through flags, so downstream consumers always know
the provenance and reliability of each field:

- **Salaries** — every value is classified by `salary_quality` (`ok` / `missing` /
  `unknown_period` / `outlier_review` / `invalid`) and converted to annual EUR
  only when the period is known.
- **Dates** — the origin of each posting date is kept in `posted_date_source`
  (`posted` / `scraped`) and the original value in `posted_date_raw`.
- **Geography** — normalized against canonical catalogs for the supported
  countries.
- **Currency** — converted through a versioned FX snapshot in `dim_currency`.
- **Skills** — matched against a canonical catalog, consistent across sources.

## 7. Extending the sources

Adding a new portal means creating a Silver notebook that reuses
`Enrich_Job_Offers_Dataframes.enrich()` and writes the common schema from §3.
