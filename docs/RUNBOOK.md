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
| `PBI_EXPORT_SAS` | Power BI Desktop data source credentials (dataset) | Read-only SAS scoped to `gold/pbi_export` (`rl`, 1 year). Generated in Cloud Shell; never in the repository. Rotate before expiry. |
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

Each one: reads `bronze.<source>`, applies the source chain (rename, `enrich`,
company/location normalization, de-dup) and writes `silver.offers_<source>` by
`MERGE` on the semantic key (`Silver_Incremental.run`); `companies_<source>` is
still rebuilt. The first run with this code rebuilds silver completely (the
table has no `_ingest_date`); later runs recompute only the affected groups.

**Parameters:** `catalog`, `bronze_schema`, `silver_schema`, `lookback_days`,
`from_date`, `to_date`, `force_full`, `repeat_window` (see §9.7).

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

The real production orchestration is versioned as code (Databricks Asset
Bundle): see §9.

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
9. Deploy the scheduled ELT job as an Asset Bundle (§9); it runs weekly (Sundays 23:00 Europe/Madrid).

---

## 9. ELT as code: Databricks Asset Bundle, CI/CD and cutover

The production ELT job —**one job with 9 tasks**: Bronze → 4 Silver (parallel) →
Gold → web export + report export, plus the closing task — is **versioned as code**
in the repository and deployed with the Databricks CLI. `databricks.yml` holds the
bundle, its targets and the
schedule (the `prod` target overrides it); `resources/elt.job.yml` holds the job
definition (tasks, `depends_on`, compute, timeout and notifications). Notebooks
are referenced from the bundle's own synced files, not from a Git folder.

### 9.1 Targets and variables

Same job definition for both environments; only the variables change:

| Variable | `dev` | `prod` |
|---|---|---|
| `catalog` | `job_offers_dev` | `job_offers` |
| `checkpoints_root` | `<storage>/_checkpoints_dev` | `<storage>/_checkpoints` |
| `export_prefix` | `gold/web_export_dev` | `gold/web_export` |
| `pbi_export_prefix` | `gold/pbi_export_dev` | `gold/pbi_export` |
| `job_name` | `Git Based ELT Dev` (the bundle prefixes `[dev <user>]` on deploy) | `Git Based ELT` |
| Schedule | none (manual runs only) | `50 0 23 ? * SUN`, `Europe/Madrid`, `UNPAUSED` (Sundays 23:00) |

`sync` is limited to `databricks_notebooks`, so a deploy uploads only what the
job runs.

### 9.2 Deploy

```powershell
databricks bundle validate -t dev  --profile <profile>
databricks bundle deploy   -t dev  --profile <profile>
databricks bundle summary  -t dev  --profile <profile>   # check paths and variables

databricks bundle validate -t prod --profile <profile>
databricks bundle deploy   -t prod --profile <profile>
```

`bundle deploy` **does not run** the job: it only uploads files and updates the
job definition. Production deploys are always manual and validated; the
production target requires `run_as` and `permissions` (production mode).

### 9.3 Continuous integration

- `.github/workflows/databricks-bundle-validate.yml`: runs
  `databricks bundle validate -t dev` on `pull_request` / `push` touching
  `databricks.yml`, `resources/**`, `databricks_notebooks/**` or the workflow
  itself. Validates only; it never deploys. Repository secrets:
  `DATABRICKS_HOST`, `DATABRICKS_TOKEN` (dev scope).
- `.github/workflows/databricks-bundle-deploy-dev.yml`: manual
  (`workflow_dispatch`) deploy **to dev only**, behind the GitHub `dev`
  environment (required reviewers). The dev job has no schedule, so it will not
  run on its own after a deploy.

### 9.4 Schedule, monitoring and rollback

The schedule lives in the `prod` target (`pause_status` is an override, so
pausing in the UI is not enough: the next `bundle deploy -t prod` would restore
the definition's value). To change the state, edit `databricks.yml`, commit,
merge and deploy.

- Day-to-day check: `databricks jobs list-runs --job-id <prod job id> --limit 5`
  (expected: exactly one run per week, Sundays at 23:00, `SUCCESS`).
- Failures: the job notifies by email (`on_failure` in `resources/elt.job.yml`).
- What each alert means (failure, anomalous duration, freshness, pending
  files) and how to diagnose a run without the SQL warehouse: §9.8.
- **Rollback** (return to the previous manual job, kept paused as a fallback):
  set `pause_status: PAUSED` in the `prod` target, commit, merge, deploy
  (`bundle deploy -t prod`), verify the bundle job is `PAUSED` and resume the
  manual job's schedule. Expected: pause effective < 5 min, full reconciliation
  (git + deploy) < 10 min.
- The full cutover/rollback procedure, IDs and execution log of spec 007 live in
  `specs/007-ELT-As-Code/cutover.md`. The job ids in use are recorded there; the
  paused manual job must not be deleted while it is the rollback path.

### 9.5 Report export and Power BI

The `Build_Report_Export` task writes `gold/pbi_export` (6 Parquet + `meta.json`,
`mode: full`, `audience: pbi`, 31 columns in `fact_offers`) as a sibling of the
web export; it depends only on Gold and does not block the public dashboard.

The `Job_Offers_Dashboard.pbip` model reads those Parquet files with
`AzureStorage.DataLake` + `Parquet.Document`; the URL in the model has **no
SAS**. The read-only SAS (`PBI_EXPORT_SAS`, `rl`, scoped to `gold/pbi_export`)
lives in the dataset data source credentials in Power BI Desktop, never in the
repository. Rotation: generate a new SAS in Cloud Shell before expiry, update
the credential in Power BI Desktop and refresh.

Verify the report does not use the SQL warehouse: keep the warehouse `STOPPED`,
run a full refresh and check that it is still `STOPPED` and that
`/api/2.0/sql/history/queries?filter_by.warehouse_id=<id>` shows no query in the
refresh window.

### 9.6 Bronze dedupe one-off (spec 008)

`resources/oneoff.job.yml` defines a manual, unscheduled job (`dedupe_oneoff`)
with four chained tasks —one per source— over
`databricks_notebooks/oneoff_dedupe_bronze.ipynb`. It collapses bronze to one row
per key (`INSERT OVERWRITE`; the latest capture wins, `_ingest_date` breaks
ties) and keeps rows without a usable key. No schedule: it costs nothing unless
it is run.

- Dev: `databricks bundle run dedupe_oneoff -t dev --profile <profile>`
  (~9 min, ~0.11 €; it counts against the three dev runs budgeted for spec 008).
- Each task prints the before/after counts and the `RESTORE` line, and returns a
  JSON summary that can be read with
  `databricks jobs get-run-output <task_run_id>`.
- `dry_run=true` (widget) prints the counts without writing.
- Rollback: `RESTORE TABLE <table> TO VERSION AS OF <n>` with the version the
  one-off printed for that table. It is SQL: ~0.43 € per warehouse wake-up, so
  request it first and group it in a single 10-minute window.
- Prod: follow `specs/008-Delta-Incremental/cutover.md` (pause, deploy, one-off,
  unpause, verify the scheduled Sunday run).

### 9.7 Silver incremental and backfill (spec 008)

The four silver offer notebooks write by `MERGE` on the semantic key
(`Silver_Incremental.py`). The first run after deploying this code finds the
table without `_ingest_date` and rebuilds it completely; later runs recompute
only the affected groups from `max(_ingest_date) - lookback_days` (7 days).

Parameters (widgets and the job parameters `backfill_from`, `backfill_to`,
`repeat_window` and `force_full`, empty by default):

- `from_date` / `to_date` (`YYYY-MM-DD`): force a window (backfill).
- `force_full=true`: full rebuild. Required after a bronze `RESTORE`, rewrite or
  deletion (including a `job_id`/`site` change) and after changing the company
  alias patterns. It can be set as a widget or as the job parameter `force_full`.
- `repeat_window=true`: runs the same window twice and returns
  `{"repeat_window": true, "runs": [...]}`; used to prove idempotency.
- `lookback_days`: window size, default 7.

Dev backfill: `databricks bundle run elt -t dev --profile <profile> --params
backfill_from=2026-10-01,backfill_to=2026-10-25,repeat_window=true`. Cost: reads
the whole bronze (~31k rows) to select candidate groups but only enriches the
affected ones; no SQL warehouse.

Evidence: each notebook returns a JSON summary (mode, window, counts, elapsed)
readable with `databricks jobs get-run-output <task_run_id>`.

Rollback: redeploy the previous bundle commit; if a silver table is left
inconsistent, `RESTORE TABLE silver.offers_<source> TO VERSION AS OF <n>` (SQL:
group it in a single 10-minute window and request it first). Prod only in a
window before a Sunday, never a manual full run outside the schedule.

> **Known debt:** `resources/elt.job.yml` versions the notification addresses —
> `on_failure` and `on_duration_warning_threshold_exceeded`, two keys with the
> same email. It predates the bundle and the repository is public; it is an
> accepted debt (decision of 2026-10-19). Moving both to a secret is pending.

### 9.8 Alerts, versioned queries and diagnosis without the warehouse (spec 011)

**Deployed in dev on 2026-11-03 and in prod on 2026-11-04**: the duration health rule
(`RUN_DURATION_SECONDS > 1500`), the ninth task `Publish_Run_Summary` —which
records its `checks` in `ops.run_metrics`— and the **four** closing checks
(`bronze_summary`, `bronze_sources`, `freshness` and `pending_files`). Both jobs
now have **9 tasks** (dev `320947022571947`, prod `916863262593632`), and
`ops.run_metrics` exists in `job_offers_dev` and `job_offers` with the **13
columns** of the contract. Two surfaces were already
active before: the failure email (`on_failure` in `resources/elt.job.yml`) and the
published-freshness check of the dashboard workflow. **The Spark event log is NOT
active**: it was retired in `639d252` because this job cluster does not start with
`spark.eventLog.dir` pointing at `abfss://` (§9.8.3). None of these surfaces needs
the SQL warehouse. The thresholds live in the code; this section says what each one
means, which query answers it and what to do.

> **`system.lakeflow` is slow.** Measured on 2026-11-04: a run that had finished
> **7 min 37 s** earlier was still **not** in `job_run_timeline` (`count(*) = 0`
> for its `run_id`). Do not wait for a just-finished run there: use
> `databricks jobs get-run` (free) for the run you have just seen end. The exact
> latency is `[NO VERIFICADO]` until the scheduled run of 2026-11-08 brackets it.

#### 9.8.1 What each alert watches and what to do

| Alert | Watches | Symptom | First check | Action |
|---|---|---|---|---|
| Failure (`on_failure` email) | any task ending `FAILED`. `Publish_Run_Summary` is `run_if: ALL_DONE`: it has been seen starting with seven dependencies failed (2026-11-03), and it writes its row before the `raise` (proven 2026-11-03 by its own failure). The combination —row written with another task red— is `[NO VERIFICADO]` | the email arrives and the run is red | `databricks jobs get-run <run_id>` and `databricks jobs get-run-output <task_run_id>` (§9.8.3) for the failing `task_key` | read that task's output (traceback or `state_message`), fix the cause and re-run the job; nothing here needs SQL |
| Anomalous duration (health rule + `on_duration_warning_threshold_exceeded` email; **deployed in dev on 2026-11-03 and in prod on 2026-11-04**; the email itself is `[NO VERIFICADO]` because no run has passed 1500 s yet) | `RUN_DURATION_SECONDS` **> 1500 s** (25 min); the job itself is cancelled at `timeout_seconds: 1800` (30 min) | the duration email arrives while the run is **still alive** | `databricks jobs get-run <run_id>` (free, seconds after the run starts) and query 2 (§9.8.2) for the per-task durations. The `*_duration_seconds` columns of `job_run_timeline` stay `0` for multi-task jobs: do not use them | it is a warning, not a failure: note the task and open a repair if it repeats. At 1800 s the run is cancelled and it is triaged as a failure |
| Published freshness (dashboard workflow) | `.github/workflows/dashboard.yml` (Mondays 01:00 UTC) aborts **without deploying** when `meta.json.data_date` is older than 8 days | the workflow is red and the site is not updated | `databricks jobs list-runs --job-id <prod job id> --limit 5`: did the Sunday run happen and succeed? Then `jobs get-run`, query 1 and `checks.freshness` | if the job did not run (paused, or a rollback in progress) resume it; if it ran green, the scrapers delivered nothing new: re-run them and then the job |
| Run freshness (closing task; **deployed in dev 2026-11-03 and in prod 2026-11-04**) | `Publish_Run_Summary` fails when `checks.freshness.ok` is false: no `data_date`, a future date or **> 8 days** | the ninth task is red while the eight business tasks are green | `databricks jobs get-run-output <task_run_id>`: on a **green** task it returns the closing JSON with `checks`; on a **failed** one it returns the **error traceback** (the `checks` go in the `RuntimeError` message), not the JSON. `jobs get-run` shows the `state_message`; `ops.run_metrics` keeps the row in Delta —written **before** the task raises, so a red run still leaves its row (measured 2026-11-03) | if `data_age_days > 8` the input is stale: re-run the scrapers and then the job. A `null` date means `meta.json` is corrupt: check the `Build_for_Web-Dashboard` output |
| Pending files (closing task; **deployed in dev 2026-11-03 and in prod 2026-11-04**) | `Publish_Run_Summary` fails when `checks.pending_files.ok` is false: files older than the run start that no batch consumed **and** zero bronze rows in the run, **or** `listing_ok` false (the landing could not be listed, so an empty backlog is not proven; a failure listing `commits/` travels in `listing_error` and does **not** change `listing_ok`) | the ninth task is red and `checks.pending_files` shows pending files in `by_source`, or `listing_ok: false` | `databricks jobs get-run-output <task_run_id>`: green ⇒ closing JSON with `checks`; failed ⇒ the **error traceback**, with the `checks` in the `RuntimeError` message. Then the cheap metadata listing of `landing/<source>/` and of the checkpoint's `commits/` | pending files: the Auto Loader did not see them — check the path and the checkpoint (`checkpoints_root`), fix and re-run; once bronze is consistent, silver needs `force_full=true` (§9.7). `listing_ok: false`: fix the listing (path, credentials) and re-run — the run is red on purpose, there is no false green |

#### 9.8.2 Versioned queries

`:job_id` and `:run_id` are query parameters: bind them in a SQL editor, or
replace them with literal values in a task. The three queries are **validated
against the project workspace** (one SQL session, 2026-11-03, T-07 of spec 011).
Inside a run, as a task on the job cluster, the query **does not wake the SQL
warehouse** (it consumes the run's own DBUs). In the SQL editor it also wakes the
warehouse (~0.43 € per wake-up), so use it only when a run is not an option and
group the queries in one 10-minute window.

> **`current_workspace_id()` does not resolve in this workspace.** With it the
> query fails with `[UNRESOLVED_ROUTINE] Cannot resolve routine
> 'current_workspace_id' on search path [system.session, system.builtin,
> system.ai, job_offers_dev.bronze]` (2026-11-03). The three queries therefore use
> the literal workspace id `7405619889654446`. That literal is **specific to this
> workspace**: in another workspace it must be replaced (the portable form would
> be `current_workspace_id()` where the function exists). Here `workspace_id` is
> numeric; if it were a string, compare with
> `CAST(workspace_id AS STRING) = '<id>'`.

**1. Last runs of the job, with their real duration** (`system.lakeflow.job_run_timeline`):

```sql
SELECT run_id, job_id, trigger_type,
       max_by(result_state, period_end_time)       AS result_state,
       max_by(termination_code, period_end_time)   AS termination_code,
       min(period_start_time) AS run_start,
       max(period_end_time)  AS run_end,
       sum(unix_timestamp(period_end_time) - unix_timestamp(period_start_time)) AS run_seconds
FROM system.lakeflow.job_run_timeline
WHERE workspace_id = 7405619889654446
  AND job_id = :job_id
  AND period_start_time >= current_timestamp() - INTERVAL 30 DAYS
GROUP BY run_id, job_id, trigger_type
ORDER BY run_start DESC
```

`workspace_id = 7405619889654446` is not decorative: `job_id` is only unique
**within a workspace**, and the table serves every workspace of the account. That
literal is **this workspace's** id (see the note above); in another workspace it
has to be replaced. The
`GROUP BY` uses `run_id`, `job_id` and `trigger_type` because `result_state` and
`termination_code` are **NULL** in the intermediate rows of runs longer than 1 h;
grouping by them would split one run into several rows, so they are aggregated
with `max_by(…, period_end_time)`. The
`setup/queue/execution/run/cleanup_duration_seconds` columns are only filled for
**single-task jobs created with the legacy APIs**; this job is `MULTI_TASK`
(9 tasks). That the run-level `*_duration_seconds` read `0` is the **T-06** check
(`evidence.md`, 2026-11-03); what the T-07 session supports is the other half: in a
run of **8 tasks** the **per-task durations are non-zero** (query 2). Duration is
therefore summed over the run's periods. The table **lags behind reality**: on
2026-11-04 a run that had finished **7 min 37 s** earlier was still missing
(`count(*) = 0`), so the observed latency is **> 7 min 37 s** —the exact figure
comes from the scheduled run of 2026-11-08, `[NO VERIFICADO]`—. For a run that has
just finished (or is in progress) the source is `databricks jobs get-run` (free).

**2. Tasks of one run** (`system.lakeflow.job_task_run_timeline`). Here `run_id` is
the **task** run; the job run is `job_run_id`:

```sql
SELECT task_key,
       result_state,
       setup_duration_seconds,
       execution_duration_seconds,
       cleanup_duration_seconds,
       period_start_time,
       period_end_time
FROM system.lakeflow.job_task_run_timeline
WHERE workspace_id = 7405619889654446
  AND job_run_id = :job_run_id
ORDER BY period_start_time
```

`:job_run_id` is the **job** run id; in this table `run_id` is the **task** run id,
so filtering by `run_id` would mix tasks from other workspaces. Checked on run
`317888374535056` (2026-11-03, T-07): **8 rows**, one per task, all `SUCCEEDED`,
with `execution_duration_seconds` other than `0` —the **per-task** durations are
what T-07 settles; that the run-level `*_duration_seconds` of query 1 read `0` is
the **T-06** check.

**3. Usage (DBUs) per run and SKU** (`system.billing.usage`, 30-day window). The
`usage_metadata.job_run_id` field is **validated** (2026-11-03, T-07):

```sql
SELECT usage_metadata.job_id     AS job_id,
       usage_metadata.job_run_id AS job_run_id,
       sku_name,
       sum(usage_quantity)       AS dbus
FROM system.billing.usage
WHERE workspace_id = 7405619889654446
  AND usage_date >= current_date() - INTERVAL 30 DAYS
  AND usage_metadata.job_id = :job_id
GROUP BY usage_metadata.job_id, usage_metadata.job_run_id, sku_name
ORDER BY dbus DESC
```

Same `workspace_id` filter (literal, for the same reason: `job_id` repeats
across workspaces). These are **DBUs, not euros**: for money, multiply by the SKU
price in `system.billing.list_prices`. Both facts are **validated** (2026-11-03):
`usage_metadata.job_run_id` is populated, so it is the join key, and the money
join resolves with `pricing.default` (the list price; the table also carries
`pricing.effective_list` if the discount matters). The resulting amount covers
**DBUs only**: no VMs, disks, storage or egress.

#### 9.8.3 Diagnosis without the warehouse, in three steps

1. **Runs**: `databricks jobs list-runs --job-id <prod job id> --limit 5 --profile <profile>`
   — state, trigger and times of the last runs (0 €).
2. **One run**: `databricks jobs get-run <run_id> --profile <profile>` (or
   `databricks api get "/api/2.1/jobs/runs/get?run_id=<run_id>"`) gives the JSON
   with every task, its `result_state` and `state_message`. And
   `databricks jobs get-run-output <task_run_id>` gives what the task published:
   on a **green** task, the JSON from `dbutils.notebook.exit` — for
   `Publish_Run_Summary`, the closing `checks`; on a **failed** task, the **error
   traceback** instead (the `checks` travel in the `RuntimeError` message). The
   failed-task behaviour is API-documented but not yet exercised in this repo
   (`[NO VERIFICADO]`). Both
   commands are free and are the fastest path to "which task and why";
   `ops.run_metrics` (deployed in dev on 2026-11-03 and in prod on 2026-11-04) keeps the same row in Delta for
   the run history.
3. **Event log: not active, on purpose.** The job cluster does **not** write a
   Spark event log. T-06 configured `spark.eventLog.{enabled,dir,rolling}` to
   `abfss://<container>@<storage_account>.dfs.core.windows.net/_eventlogs/` and the
   job cluster **did not start**: `SparkMisconfiguration: Invalid configuration
   value detected for fs.azure.account.key`, with the bronze task stuck 472 s in
   setup and the seven dependent tasks `UPSTREAM_FAILED` (run `873077346042958`,
   2026-11-03). The three keys were removed in `639d252`; `landing/_eventlogs/`
   holds **0 files, 0 B** (measured 2026-11-04). Do not re-enable them without a
   different mechanism. The **Spark UI** stays available for ~60 days after the
   run and is the place to read stages, tasks and streaming progress; the compute's
   **Event log** tab is the cluster lifecycle log, not this file. If retention
   beyond that window is ever needed, the supported alternative is **compute log
   delivery** (driver/executor logs to an account destination, without
   `spark.eventLog.dir`): **documented, not implemented**.

#### 9.8.4 Discarded, and why

- **`STREAMING_BACKLOG_*` health metrics** (`_BYTES`, `_RECORDS`, `_SECONDS`,
  `_FILES`): these streams run as batches (`availableNow`), so there is no
  continuous backlog to watch; on top of that the metric is in **Public
  Preview** and the closing task already fails on pending files.
- **Scheduled SQL alerts**: each evaluation **wakes the warehouse** (~0.43 €) and
  is recurrent. The job's own alerts (`on_failure`; the duration rule and the
  closing task's `checks`, deployed in dev on 2026-11-03 and in prod on 2026-11-04) cover the same ground for
  free.

## 10. Local scrapers pipeline: quarantine and the config clone

### 10.1 Quarantine: rejected file vs separated rows

`ensure_compatible.py` runs before every upload and leaves two different things
in `scrapers-pipeline/quarantine/<source>/`:

- **Rejected file** — the whole `.parquet` is moved there. It happens when the
  file is **structurally invalid** (unreadable, missing required columns, empty
  or below `--min-rows`) or when **every row is incoherent** (a clean parquet
  would be empty). Nothing from that file reaches the landing; the manifest entry
  is `status: bad` with its `error`, and the run marks the source as failed when no
  valid file remains for it.
- **Separated rows** — `bad_rows_<stem>_<ts>.csv`, written in the source's
  quarantine folder (`<quarantine-dir>/<source>/`; the runner passes
  `scrapers-pipeline/quarantine/<source>`). When only some rows break the
  coherence rules (`--coherence <site>`), those rows go to the CSV, the staging
  parquet is rewritten with the valid ones (same schema, nulls preserved) and
  uploaded; the manifest entry is `status: partial` with `dropped_rows`, and the
  validator exits 0, so the runner uploads it and does not count the source as
  failed.

Review both, never delete them silently. To re-publish a whole rejected file,
copy it back into the scraper's `OutDir` and run the pipeline again (or
`recover_and_upload.ps1 -Date <date>`).

**Known limitation:** in sources with `OnlyNewOffers` (linkedin, multi_site) the
`uploaded_keys` state is updated from the keys computed **before** validation, so
the keys of the separated rows are marked as uploaded even though those rows
never reached the landing. A rerun will not bring them back by itself: the CSV is
the recovery path (fix the rule, then re-ingest the CSV by hand). The runner's
`status: partial` only counts whole rejected files, so a run with separated rows
reports `rejected 0`.

### 10.2 Delta quarantine: a rating in `company_name` and treatment by reason

The local coherence validator is the first defence (a rating in a text column
rejects the row or the file before the upload). The **Delta quarantine is the
second defence**: it catches what the local rules let through, without losing
the valid rows of the same batch.

- **Rule (`company_name_rating`).** A row goes to quarantine when
  `company_name` is **only** a decimal rating (`4,0`, `4.0`, ` 4,00 `), the
  signature of a shifted parser column. A legitimate name that contains digits
  (`24/7 Retail`, `4.0 Retail`, `ACME 4,0`) is **not** matched. A source without
  the column is never quarantined by this rule.
- **Precedence.** One reason per row: `missing_key` > `missing_scraped_at` >
  `company_name_rating`. The separation is in
  `databricks_notebooks/Upsert_Job_Offers.py::split_quarantine`; the contract
  and the generated statements live in `databricks_notebooks/Data_Quality.py`.
- **Where it lands.** `<catalog>.bronze.<source>_quarantine`, append-only, with
  identity `(_row_hash, _quarantine_reason, _source_file)`. Repeating the same
  file does not grow the table; the same invalid row in two landing files is two
  rows, so a corrected file can coexist with the original. The valid rows of the
  batch continue to the `MERGE`.
- **Bronze summary.** `ops.run_metrics`/the bronze summary still counts the new
  quarantine rows of the batch without scanning the table: an all-invalid batch
  shows `input > 0` and `quarantined = input`, not an empty bronze.

**Treatment A — correct and reprocess (preferred).** Never edit the raw file.
Fix the parser (or the coherence rule) and publish a corrected copy of the
batch; keep its reference to the original in the file name and in the report.
The corrected row has a different `_row_hash` and is valid, so it enters bronze;
the original stays quarantined. The `MERGE` keys on the source key and the
capture instant, so the corrected offer does not duplicate an existing one.
The raw landing remains the canonical copy.

**Treatment B — discard.** Before deleting anything, record the source file,
date, reason and count. Only then remove those rows from the quarantine table
(a SQL statement: it needs the warehouse and is **not** free — group it in a
single window or use the T-08 session). Never delete a whole quarantine table to
"clean up".

**Local proof of both paths.** The fixtures of
`databricks_notebooks/tests/test_upsert_job_offers.py` (spec 012) demonstrate
the separation, the precedence, the idempotency, the reconciliation
`input = valid + unique quarantined (+ repeated within the batch)` and the
`_source_file` identity. No historical quarantine is deleted or reprocessed by
the pipeline.

**Inventory for T-08.** `docs/governance/t08-profile-queries.sql` counts rows
per source and reason with the first and last `_quarantine_at` (no payloads).
T-01 measured that no `<source>_quarantine` table exists yet: run only the
blocks whose table the `information_schema` preflight returns.

### 10.3 Run it from the clone that has the config

The pipeline resolves the storage account from
`scrapers-pipeline/config.local.ps1` (git-ignored) or from the
`LANDING_STORAGE_ACCOUNT` environment variable. The 23:00 scheduled task runs
from `C:\Users\aleja\Documents\projects`, which has that file. **Launching the
pipeline from another clone** (for example a spec worktree) without it builds the
destination as `https://.blob.core.windows.net/landing/...` — an empty account
name: DNS does not resolve (`dial tcp: lookup .blob.core.windows.net: no such
host`, visible in AzCopy's own log under `%USERPROFILE%\.azcopy\`) and each
upload spends ~16 min retrying before it fails. **No data is lost:** the
originals stay in each scraper's `OutDir`, and `recover_and_upload.ps1 -Date
<date>` — run from the clone with the config — publishes them without re-running
the scrapers. On 2026-11-04 a local cycle launched from a worktree elapsed
**24968 s** and uploaded **0 files**: `infojobs` and `linkedin` failed for this
reason (the empty account name), while `multi_site` failed for a different one
—the coherence validator rejected its whole file (16 incoherent rows)— and
`indeed` had no data. The worktree did not have `config.local.ps1` during that
cycle; the file was created there at **19:10**, after the two failed uploads, and
both the 22:00 and 23:08 recoveries ran from the worktree.

---

## 11. Unity Catalog governance: privileges, inheritance and rollback

This section is the operating manual for access to the project's data. The
inventory, the per-object matrix and the approved deviations live in
[`docs/governance/access-matrix.md`](governance/access-matrix.md); the commands
for the dev trial, the production preflight and the inverse of every grant live
in [`docs/governance/grants-plan.md`](governance/grants-plan.md); the decisions
are in [`docs/governance/decisions.md`](governance/decisions.md). **Do not
duplicate a grant here without updating those files.** Grants, owners and ACLs
are inspected with the **CLI/API** (free); SQL is only used for `SHOW GRANTS`
or the audit query, and every warehouse wake-up costs ~0.43 EUR.

### 11.1 Privileges by layer and principal (operational summary)

| Principal | Scope | Current effective access (2026-11-06) | Operation that justifies it |
|---|---|---|---|
| `<cuenta-personal>` (`run_as` of the ELT jobs) | `job_offers_dev` and `job_offers`: catalog, `bronze`/`silver`/`gold`/`ops`, 39 tables; `landing-loc`; storage credential; the 3 jobs | **Owner at all three levels and member of `admins`**: all capabilities implied on each object, plus administrative `CREATE_*` on the metastore (via the workspace-admin account group; membership expansion `[NO VERIFICADO]`). The API does not enumerate them (`grants get` = `{}`). **Not least privilege: approved limitation D-01** | Runs the ELT pipeline and administers the workspace |
| `admins` | Jobs | `CAN_MANAGE` **inherited from `/jobs/`**; the 9 `CREATE_*` on the metastore are granted to the workspace-admin account group (membership expansion `[NO VERIFICADO]`) | Workspace administration; it does not grant data access |
| `account users` | Project objects | **No grant** (`{}`); on the metastore only `USE_MARKETPLACE_ASSETS` | None. It was only the trial principal of T-02/T-04 |
| `users`, `users-clone-2026-08-05-2234-UTC` | Project objects | No grant | None |
| Storage Managed Identity (`access-connector-joboffers`) | `landing` container via `landing-loc` | **No UC grants** (it is not a UC principal): it authorizes the external location, so access is the credential/path plus Azure RBAC (`[NO VERIFICADO]`). The job needs `READ FILES` + `WRITE FILES` over the **container root** | Auto Loader input/checkpoints and web/PBI exports |
| Power BI / dashboard workflow | `gold/pbi_export`, `gold/web_export` | Read-only **SAS per prefix, outside Unity Catalog** | Report refresh and public site; no UC grant |
| Future automation identity | per `grants-plan.md` §4 | **Does not exist**; not created in this spec | Separate spec |

What each layer needs if an identity is ever migrated (the job today has all of
it through ownership):

| Layer / task | Objects | Minimum privilege |
|---|---|---|
| Context | catalog and each layer schema | `USE CATALOG`, `USE SCHEMA` |
| `Bronze_Ingestion` | `bronze.<source>` (4) | `SELECT`, `MODIFY` (`MERGE WITH SCHEMA EVOLUTION`) |
| `Bronze_Ingestion` | `bronze.<source>_quarantine` (4) | `SELECT`, `MODIFY`; `CREATE TABLE` on `bronze` only if they are not pre-created |
| `Silver_*` | `bronze.<source>` (read) and `silver.offers_*`/`companies_*` (write) | `SELECT` on the source; `SELECT`, `MODIFY` on the target |
| `Gold_Complete_Catalog` | `silver.*` (read) and `gold.*` (write) | `SELECT` on the source; `SELECT`, `MODIFY` on the target |
| `Publish_Run_Summary` | `ops.run_metrics` | `SELECT`, `MODIFY` |
| Auto Loader and exports | `landing-loc` | `READ FILES`, `WRITE FILES` (`READ FILES` is mandatory even to write) |

No task needs `CREATE SCHEMA`; the schemas are created outside the job. The
per-table list and the rationale are in `access-matrix.md` §5.6 and
`grants-plan.md` §4.

### 11.2 Inheritance: what descends, what does not

**Grants descend, and it was measured on 2026-11-06 (T-04).** A temporary
`SELECT` granted on the schema `job_offers_dev.ops` to `account users`:

- did **not** appear in `grants get table ...run_metrics` (direct grants only:
  still `{}`);
- **did** appear in `get-effective table ...run_metrics --principal "account
  users"` with `inherited_from_name: job_offers_dev.ops` and
  `inherited_from_type: SCHEMA`;
- did **not** reach `job_offers_dev.bronze.indeed` (different schema, `{}`);
- disappeared from both endpoints after the `REVOKE` (all `{}`).

So the two endpoints answer different questions and both are needed:
`grants get` = explicitly assigned; `get-effective` = assigned + inherited. The
grant was reverted immediately; the sanitised output is in
`docs/governance/evidence/t04-*.txt`. The trial granted **on the schema on
purpose** to measure inheritance: it is the controlled exception to the
table-scoped rule. Operational grants stay table-scoped except `USE
CATALOG`/`USE SCHEMA`.

**A new table inherits the container grants.** Privileges granted on a catalog
or schema apply to all **current and future** children; the table starts with no
direct grants and the creating principal becomes its owner. That is why project
grants are table-scoped except `USE CATALOG`/`USE SCHEMA`, and why a `SELECT` on
a schema is a deliberate decision that also covers future tables.

**`USE CATALOG` / `USE SCHEMA` are not data access.** They are usage
prerequisites; reading still needs `SELECT` on the table (plus both usage
privileges on the parents). By the UC rule, without `USE SCHEMA` the read would
fail; the T-04 trial did not grant `USE` (it only measured that the `SELECT` is
inherited), so `get-effective` listing it is not proof of readability. `MODIFY`
additionally requires `SELECT`.

**Ownership is not `MANAGE`** (Unity Catalog permissions model, checked
2026-11-06):

| Capability | Owner | `MANAGE` |
|---|---|---|
| All capabilities on the object | Yes, implied; not listed as `ALL PRIVILEGES` | No; each privilege must be granted (the holder can self-grant) |
| Grant/revoke, transfer ownership, drop | Yes | Yes |
| Manage child objects | Yes, implied on all children | Yes, inherited when granted on a container |
| Number of principals | Exactly one | As many as needed |
| Usage privileges | Not on the owned object | Not at the level where it is held, but yes above it |
| Included in `ALL PRIVILEGES` | n/a | **No** |

- Ownership **does not inherit downward**: owning a catalog does not make you
  owner of its schemas or tables, but the owner can manage every child object
  (functionally `MANAGE`, not granted explicitly).
- Consequently `get-effective` returns `{}` for an owner: **`{}` does not mean
  "no access"** and does not prove least privilege. It only means "no explicit
  grant".
- `MANAGE` on a container inherits to its children; `MANAGE` does not give data
  access by itself (but the holder can grant it to themselves).

**Metastore grants do not descend** to catalogs/schemas/tables (the documented
exception is `READ METADATA`). That is why the 9 `CREATE_*` of the workspace
admin group are administrative and do not read data.

**External Location grants cover the whole path, present and future.** A grant
on `landing-loc` reaches the container root (raw, checkpoints, managed storage
and both exports); scoping it is a storage decision, not a UC one.

**SAS are independent of UC.** They neither inherit nor receive grants; see
§11.4.

### 11.3 Inspect, change, verify and revert

Pattern used in T-02/T-04: capture the before state, apply the smallest grant,
verify with both endpoints, revert with the exact inverse, verify `{}` again, and
save the sanitised output as evidence (date, command, result, identifier, 0 EUR)
in `docs/governance/evidence/`.

```powershell
$profile = 'vs_code'

# 1) Inspect. Direct grants vs effective (direct + inherited).
databricks grants get catalog job_offers_dev --profile $profile -o json
databricks grants get schema  job_offers_dev.ops --profile $profile -o json
databricks grants get table   job_offers_dev.ops.run_metrics --profile $profile -o json
databricks grants get-effective table job_offers_dev.ops.run_metrics `
  --principal "account users" --profile $profile -o json

# 2) Change. Use @file: PowerShell quoting and the CLI JSON both work better.
'{"changes":[{"principal":"account users","add":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-grant.json
databricks grants update schema job_offers_dev.ops `
  --profile $profile --json "@$env:TEMP\uc-grant.json"

# 3) Verify: get (direct) and get-effective (inherited).
databricks grants get schema job_offers_dev.ops --profile $profile -o json
databricks grants get-effective table job_offers_dev.ops.run_metrics `
  --principal "account users" --profile $profile -o json

# 4) Revert: same call, "remove" instead of "add".
'{"changes":[{"principal":"account users","remove":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-revoke.json
databricks grants update schema job_offers_dev.ops `
  --profile $profile --json "@$env:TEMP\uc-revoke.json"

# 5) Confirm {}. If the revoke fails, stop: do not leave the grant in place.
```

Inside a SQL session the same state can be read with:

```sql
SHOW GRANTS ON TABLE job_offers_dev.ops.run_metrics;
SHOW GRANTS ON SCHEMA job_offers_dev.ops;
SHOW GRANTS ON CATALOG job_offers_dev;
SHOW GRANTS `account users` ON TABLE job_offers_dev.ops.run_metrics;
```

`SHOW GRANTS` lists the privileges that affect the object **including inherited
and denied ones** (SQL reference, checked 2026-11-06), so it complements the two
API endpoints: it is the in-session view, while `grants get` shows explicit
grants only and `get-effective` shows explicit + inherited with the
`inherited_from_*` provenance.

**Jobs ACL.** `jobs get-permissions <job_id>` returns direct and inherited
entries; `inherited: true` identifies the ones that come from `/jobs/`.
Rollback of a job ACL **restores only the direct ACL** captured before the
change (the owner with `IS_OWNER`). The `admins` group `CAN_MANAGE` is inherited
from `/jobs/` and is **not** fixed or removed with `jobs set-permissions`:

```powershell
databricks jobs get-permissions 916863262593632 --profile vs_code -o json
databricks jobs set-permissions 916863262593632 --profile vs_code `
  --json "@$env:TEMP\job-acl-direct.json"
```

Minimal body of `job-acl-direct.json` (`jobs set-permissions` replaces the
**direct** ACL with this list):

```json
{
  "access_control_list": [
    { "user_name": "<cuenta-personal>", "permission_level": "IS_OWNER" }
  ]
}
```

Keep only entries with `inherited: false` from `jobs get-permissions`. Entries
can use `user_name`, `group_name` or `service_principal_name`; **do not add
`admins`**, because its `CAN_MANAGE` is inherited from `/jobs/` and is not part
of the direct ACL. The full production preflight, the hypothetical commands for
a future automation identity and the inverse of every grant are in
`grants-plan.md` §3-§6; do not improvise a grant outside that plan.

### 11.4 Power BI, the SAS and why it is not in the UC reader matrix

`Job_Offers_Dashboard.pbip` reads `gold/pbi_export/*.parquet` from ADLS with
`AzureStorage.DataLake` and a **read-only SAS** (`PBI_EXPORT_SAS`, `rl`) scoped
to that prefix. The public dashboard workflow does the same with
`WEB_EXPORT_SAS` on `gold/web_export`. Both bypass Unity Catalog: **no
`SELECT`, no `USE CATALOG`, no inheritance**, so Power BI is intentionally
absent from the UC matrix of §11.1 and from `access-matrix.md` §5.5.

Practical consequences:

- Do not "fix" a report with a UC grant: it has no effect on the SAS path.
- A UC grant or revoke cannot break the report by itself; what breaks it is the
  export task losing write access to `landing-loc`, a missing export, or the SAS
  expiring/being rotated without updating Power BI.
- The SAS lives in Power BI Desktop dataset credentials or in the GitHub
  secret, never in the repository, logs or evidence. Rotation: new SAS in Cloud
  Shell, update the credential, refresh.
- The report must not wake the warehouse: keep it `STOPPED`, refresh, and check
  that `/api/2.0/sql/history/queries?filter_by.warehouse_id=53d8e3e91af6749c`
  shows no query in the window.

### 11.5 A run failed "by permissions": UC or SAS/ADLS?

Classify before changing anything: **UC errors come from the table/schema
metadata; storage errors come from the path or the SAS.** Both are diagnosed
without SQL (`jobs get-run`, `jobs get-run-output`); the warehouse stays
`STOPPED`.

| What the task shows | Where | First check | Rollback / fix |
|---|---|---|---|
| `PERMISSION_DENIED`, `not authorized`, `USE CATALOG`/`USE SCHEMA`/`SELECT`/`MODIFY` in the message | Unity Catalog | `grants get` + `get-effective` of the failing object and of `run_as`; compare with `access-matrix.md` §5; if the audit table is available, run `t04-audit-query.sql` block 1 (all changes) and block 2 (filter by principal) in the T-08 session; block 3 is only for job ACLs | Apply the inverse documented in `grants-plan.md` §6 (re-grant or revoke), then re-run only with approval of the cost |
| `403`, `AuthorizationFailure`, `This request is not authorized`, `Signature not found`, `InvalidAuthenticationToken`, `BlobNotFound`/`PathNotFound` on Auto Loader or exports | ADLS / SAS / storage credential | `external-locations get landing-loc`, credential name, widget/path (`storage_account`, `container`, prefixes), SAS expiry; Azure RBAC is `[NO VERIFICADO]` | Fix the path or rotate the SAS/credential; never grant UC to solve a storage 403 |
| Power BI refresh fails while the job is green | SAS / export | Is `gold/pbi_export/` complete with `meta.json`? Did the SAS expire? | New SAS and update the dataset credential; no UC change |
| Cluster does not start / job fails before a task runs | Compute or storage credential | `jobs get-run` state and `state_message` | Do not touch grants |

**Who to notify:** the project owner/workspace admin, and the `on_failure`
email of the job if it is production. **Never apply a production grant or
revoke without the preflight of `grants-plan.md` §3 and the approval recorded
in `decisions.md`.** If the cause is a storage 403 and the Azure RBAC cannot be
read, record `[NO VERIFICADO]` and do not claim it was fixed.

### 11.6 Where the detail lives

- `docs/governance/access-matrix.md`: measured inventory, per-object matrix,
  measured excess and `[NO VERIFICADO]`.
- `docs/governance/grants-plan.md`: T-02/T-04 trial, production preflight,
  hypothetical grants of a future automation identity, duplicate storage
  credentials and stop criteria.
- `docs/governance/decisions.md`: D-01 (personal-account excess approved as a
  limitation; `run_as` not migrated).
- `docs/governance/evidence.md` + `docs/governance/evidence/`: one row per
  check with date, command, result, identifier and cost.
- `docs/governance/t04-audit-query.sql`: audit query for T-08 (prepared, not
  executed) and the alternative if `system.access.audit` is unavailable.
- `docs/es/RUNBOOK.md`: **out of sync** with this runbook; it does not contain
  §9 or this governance section. Do not use it as the operational source until
  it is updated (pending).

---

## 12. Data quality: contract, Delta constraints and the single SQL session

The quality rules have a single definition in
`databricks_notebooks/Data_Quality.py`: the stable `rule_id`, the layers, the
explicit **table-column map per layer** (silver `salary_min_annual` /
`salary_max_annual`, gold `SalaryMinAnnual_EUR` / `SalaryMaxAnnual_EUR`; the
names are never assumed to match), the predicate and its SQL expression, the
action and the evidence. The mediation rules that already existed are
**referenced**, not reimplemented: `missing_key`, `missing_scraped_at` and the
new `company_name_rating` live in `Upsert_Job_Offers.py`; the posting-date flag
lives in `Prepare_Gold.py`. The registry is Python (frozen dataclasses), not a
Delta table.

### 12.1 The rules and their action

| `rule_id` | Layers | Action | Why |
|---|---|---|---|
| `missing_key` | bronze | Quarantine | A row without a source key cannot be merged (existing). |
| `missing_scraped_at` | bronze | Quarantine | A row without a parseable capture instant cannot be ordered (existing). |
| `company_name_rating` | bronze | Quarantine | The rating spilled into the company column; second defence (§10.2). |
| `job_id_required` | silver, gold | Delta `CHECK` | `job_id` is not NULL and, as a STRING, not blank after `trim`. |
| `salary_range_order` | silver, gold | Delta `CHECK` | If both annualised amounts exist, `min <= max`. |
| `posted_date_contract` | gold | Mark (deferred) | `IsValidPostingDate` (2026..2089) is a flag today; a constraint needs the T-08 measurement. |

Nothing is changed in the semantics of a valid offer: an offer with an invalid
original salary keeps its row, `salary_quality = invalid` and NULL annualised
amounts; that is why no constraint is created over the original range.

### 12.2 How a `CHECK` handles NULL (measured, not assumed)

A Delta `CHECK` must be **true for each input row**. Measured locally with
Delta 4.4, `salary_min_annual <= salary_max_annual` rejected a row with both
amounts NULL (the result is NULL, not TRUE). Every expression therefore allows
the missing values explicitly:

```sql
CHECK (salary_min_annual IS NULL OR salary_max_annual IS NULL
       OR salary_min_annual <= salary_max_annual)
```

The same applies to the date rule (`posted_date IS NULL OR (year(...) ...)`).
`job_id` uses `IS NOT NULL AND trim(...) <> ''`: a NULL job_id must fail.

A violation is **not** a silent drop: the write fails and the task fails. Since
the ELT neutralises an invalid salary to NULL before silver/gold, the constraint
should never fire with the current data; it is the last defence against a
regression.

### 12.3 Prepare and apply (T-07 and the single session of T-08)

The statements are generated, never hand-edited:

- `docs/governance/constraints-ddl.sql`: preflight
  (`SHOW TBLPROPERTIES <table>`, all properties), the forward
  `ADD CONSTRAINT` for dev and prod, and the **commented** inverse
  `DROP CONSTRAINT`. Re-running the file never drops a constraint: uncomment
  the inverse only to roll back.
- `docs/governance/t08-profile-queries.sql`: one profile statement per table and
  rule (`rows_total`, `violating_rows`, NULL counts, date range) and the
  quarantine inventory per source and reason. Run each block on its own.

Order of the T-08 window: profile dev and prod → **only** add a constraint whose
`violating_rows` is 0 (or whose rows were treated with evidence) → verify by
metadata. `ADD CONSTRAINT` validates the rows already in the table: with
pending violations the statement fails and the table is left unchanged. If a
rule has violations it is not forced: record the count and the pending treatment
and mark it `[NO VERIFICADO]`. No second session is opened.

Idempotence: feed the property names read in the preflight back into
`Data_Quality.ddl_statements(catalog, existing_properties)`; the plan for an
already-deployed table is empty. The local Delta tests prove the whole cycle
(add → valid write → invalid rejection → empty second plan → inverse → forward
again) on temporary tables, without touching a real one:

```powershell
$env:TML_LOCAL_DELTA=1
python -m pytest databricks_notebooks/tests/test_constraints_delta.py -q
```

Run that file **in isolation**: it is opt-in with `TML_LOCAL_DELTA=1` because it
needs its own Delta session, and inside the shared suite an earlier module owns
the Spark session.

### 12.4 If a task fails with a constraint violation

1. Read the statement in the task log: the message names the constraint
   (`chk_<table>_<rule_id>`) and the row values.
2. Decide with the profile: is it an unexpected value (a regression) or data
   allowed by a rule that was wrong? Do not disable the constraint in a hurry.
3. Fix the ingestion/ELT and reprocess the batch idempotently, or roll back the
   constraint with the inverse of the DDL file.
4. Record the incident in `docs/governance/evidence.md`; if the rule changes,
   update `Data_Quality.py` and regenerate the two SQL files (the tests compare
   them with the module).

### 12.5 Verified in production (T-10, 2026-11-09)

The first scheduled run after the deploy (`762969079133747`) finished **9/9**
tasks in **SUCCESS** in 14 min 40 s. The bronze task ingested 11,710 rows,
quarantined **0** and created no `<source>_quarantine` table, so there was no
new quarantine row to reconcile or treat. `Publish_Run_Summary` wrote the
`ops.run_metrics` row (`record.status = ok`) with all four checks green, and
no write violated the 10 production constraints. Grants stayed at `{}`, the
job kept its 9 tasks, cron and `run_as`, and the warehouse was `STOPPED`
before and after. The dashboard workflow `42581592569` published the same run
export with `data_date 2026-11-07`.

The run proves **no regression and no permission failure**; it does **not**
prove idempotency (T-06 does, locally) or least privilege for the personal
account (its excess remains approved in D-01). If a future run fails here,
follow §12.4 and §11; do not open a second SQL session just to inspect the
constraints, because `tables get` already returns them.
