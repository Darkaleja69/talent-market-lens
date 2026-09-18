# Runbook — running the pipeline from scratch

Guide to reproduce the full pipeline (scrapers → ADLS → Bronze → Silver → Gold →
Power BI) in a new account, with no credentials in the repository.

> This runbook targets the **current versions** of `databricks_notebooks/`.
> Storage, catalog, schemas and checkpoints are **externalized** (Databricks
> widgets / environment variables): there are no hard-coded accounts, paths or
> tokens in the code.

---

## 1. Requirements

| Component | Version / note |
|---|---|
| Python (local) | 3.11+ (for tests and scrapers) |
| PySpark | 3.5 (provided by the Databricks Runtime; locally via `requirements.txt`) |
| Databricks | Workspace with **Unity Catalog** enabled |
| Azure | Subscription with **ADLS Gen2** (Hierarchical Namespace enabled) |
| Power BI | Power BI Desktop (PBIP format) |
| AzCopy | To upload the Parquet files to the landing zone (optional depending on orchestration) |

Install local dependencies:

```powershell
pip install -r requirements.txt
```

---

## 2. Secrets and environment variables

**Rule: 0 secrets in the repository.** No token, SAS, account key or connection
string is versioned.

| Secret / parameter | Where it lives | How it is provided |
|---|---|---|
| `LANDING_SAS_TOKEN` | Local user environment variable | `setx LANDING_SAS_TOKEN "<container SAS>"` before uploading with AzCopy. |
| `storage_account` | Databricks widget | Task parameter / widget in the Bronze notebook. |
| `container` | Databricks widget | Defaults to `landing`. |
| `catalog`, `bronze_schema`, `silver_schema`, `gold_schema` | Databricks widgets | Default to `job_offers`, `bronze`, `silver`, `gold`. |
| `checkpoints_root` | Databricks widget | Optional; defaults to `<STORAGE_BASE>/_checkpoints`. |
| Databricks access to the Data Lake | **Unity Catalog Storage Credential + External Location** (Managed Identity or Service Principal) | Workspace configuration, never in the repository. |

`.gitignore` excludes `.env`, logs, `quarantine/`, backups, caches and Parquet
data so none of it ends up in Git.

---

## 3. Set up Unity Catalog and the landing zone

1. **Storage**: create the `landing` container in ADLS Gen2.
2. **Storage Credential** and **External Location** in Unity Catalog pointing to
   the container. Grant access to the users/groups that will run the notebooks.
3. **Catalog and schemas**:

   ```sql
   CREATE CATALOG IF NOT EXISTS job_offers;
   CREATE SCHEMA IF NOT EXISTS job_offers.bronze;
   CREATE SCHEMA IF NOT EXISTS job_offers.silver;
   CREATE SCHEMA IF NOT EXISTS job_offers.gold;
   ```

   > If you use other names, don't edit the code: pass them through the
   > `catalog`, `bronze_schema`, `silver_schema`, `gold_schema` widgets.

4. **Landing per source**: each scraper writes its Parquet files under
   `landing/<source>/` (`indeed/`, `linkedin/`, `infojobs/`, `multi_site/`).

---

## 4. Upload the Python modules to Databricks

The Silver and Gold notebooks import repository modules:

- `Enrich_Job_Offers_Dataframes.py` → `import Enrich_Job_Offers_Dataframes as de`
- `Prepare_Gold.py` → `import Prepare_Gold as dg`

Upload them to the workspace (Git folder, Repos or Workspace files) and make sure
they are on the notebook's `sys.path` (the Gold notebook already enables
`%load_ext autoreload`). **Note:** if your workspace uses different module names,
adjust the import in each notebook's configuration cell.

---

## 5. Execution order

Every notebook reads its configuration from widgets and, if none are passed,
resolves via environment variable or falls back to the default. In Databricks
Workflows, define the parameters at the task level.

### 5.1 Bronze — `bronze_ingest.ipynb`

For each source, Auto Loader with `trigger(availableNow=True)`:

```
cloudFiles (parquet) → Delta job_offers.bronze.<source>
```

Details: `schemaLocation` and `checkpointLocation` under
`<checkpoints_root>/<source>/{schema,ckpt}`, `mergeSchema=true`, plus
`_ingest_date` and `_source_file`.

**Parameters:** `storage_account` (required), `container`, `catalog`,
`bronze_schema`, `checkpoints_root`.

### 5.2 Silver — one notebook per source

`silver_indeed.ipynb`, `silver_infojobs.ipynb`,
`silver_linkedin.ipynb`, `silver_multisite.ipynb`.

Each one: reads `bronze.<source>`, renames columns, applies
`Enrich_Job_Offers_Dataframes.enrich()`, normalizes company/location, de-duplicates
and writes `silver.offers_<source>` and `silver.companies_<source>`.

**Parameters:** `catalog`, `bronze_schema`, `silver_schema`.

### 5.3 Gold — `gold_build.ipynb`

Unions the four Silver tables (`unionByName(allowMissingColumns=True)`),
de-duplicates and builds the Gold tables with `Prepare_Gold.build_gold()` +
`write_gold()`. It also writes `companies_complete_catalog`.

**Parameters:** `catalog`, `silver_schema`, `gold_schema`.

### 5.4 Recommended chaining

```
Bronze (4 sources) → Silver Indeed / InfoJobs / LinkedIn / MultiSite (parallel)
                   → Gold → Power BI
```

Orchestration recommendations: independent tasks per source, retries,
retries, file-arrival sensors and a `success/partial/failed` run log.

---

## 6. Tests

```powershell
python -m pytest databricks_notebooks/tests -q
```

Two suites:

- `test_enrich_pure.py` — pure normalization functions (roles, multilingual work
  mode, salary periods, numeric parsing and the skill catalog).
- `test_integration_spark.py` — Spark integration: `enrich()`, `build_fact_offers()`,
  currency conversion, `build_fact_offer_skills()`, empty table, idempotent
  re-runs, `dim_calendar()`, `build_gold()` and `write_gold()`.

Notes:

- Requires Java 17/21 and `pyspark` (see `requirements.txt`).
- The full suite is intended to run on Databricks or Linux CI.

---

## 7. Power BI

Open `Job_Offers_Dashboard.pbip` (PBIP format: PBIR report + TMDL semantic model)
and connect the model to the `job_offers.gold.*` tables. The model uses
`Fact_Offers`, `Fact_OfferSkills`, `Dim_Calendar`, `Dim_Companies`,
`Dim_SkillList`, `Dim_Currency` and the geographic/map tables.

---

## 8. From-scratch checklist

1. `pip install -r requirements.txt`
2. Create the catalog + schemas + External Location in Unity Catalog.
3. Upload `Enrich_Job_Offers_Dataframes.py` and `Prepare_Gold.py` to the workspace.
4. Run Bronze with `storage_account` (and the other widgets) → 4 Bronze tables.
5. Run the 4 Silver notebooks → `silver.offers_*` + `silver.companies_*`.
6. Run Gold → `gold.fact_offers`, `fact_offer_skills`, the dimensions and `companies_complete_catalog`.
7. Open the PBIP and validate the report.
8. `pytest` green before publishing.
