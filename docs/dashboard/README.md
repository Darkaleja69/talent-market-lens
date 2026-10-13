# Dashboard — pinned CDN versions and data contract

The public dashboard is a static, no-build site: `index.html` + native ES modules
(`app.js`, `filters.js`, `queries.js`, `charts.js`, `i18n.js`) + `styles.css`.
This file fixes what a reviewer needs to reproduce it: the pinned CDN versions
and their licenses, the GeoJSON provenance and the exact data contract the app
expects (files, columns and `meta.json`).

## Pinned CDN dependencies

The import map in `index.html` is the source of truth. All three packages come
from jsDelivr with **exact versions** (no ranges) and are imported dynamically
so a CDN failure is caught and reported in the UI (never a blank page).

| Package | Version | License | Module |
|---|---|---|---|
| DuckDB-WASM | 1.32.0 | MIT | `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/dist/duckdb-browser.mjs` |
| Apache Arrow (bare `apache-arrow` import required by DuckDB-WASM) | 17.0.0 | Apache-2.0 | `https://cdn.jsdelivr.net/npm/apache-arrow@17.0.0/+esm` |
| Apache ECharts | 6.1.0 | Apache-2.0 | `https://cdn.jsdelivr.net/npm/echarts@6.1.0/dist/echarts.esm.min.js` |

DuckDB-WASM resolves its own WASM bundles and workers (`duckdb-eh.wasm` /
`duckdb-mvp.wasm` and the matching `duckdb-browser-*.worker.js`) from the same
`1.32.0` package. **Changing a version means updating the import map and this
table together.**

## GeoJSON assets

- `assets/europe.geojson` — country polygons for the Europe choropleth; each
  feature carries only a `name` property.
- `assets/regions.geojson` — per-country region polygons for the country
  selector; each feature carries `name` and `country`.

Neither file includes license metadata inside it (no `license` or `source`
property). The project plan records this boundary material as free
license / public domain ("licencia libre", "dominio público"); no further
provenance is stated in the repository, so its origin should be verified before
redistributing these files outside the project.

## Data contract

The site expects the five export files, with fixed names, next to the dashboard
in a `data/` folder:

- **Deploy (GitHub Pages):** `_site/` = `docs/` + the export copied into
  `_site/data/`; the dashboard page references `../data/<file>`.
- **Local:** the snapshot lives in `docs/data/` (gitignored) and `docs/` is
  served statically, so the same relative paths work.

**One Parquet file per table** (`coalesce`, single file, written with
`overwrite`; re-runs are idempotent). The files are downloaded in full and
registered in DuckDB-WASM with `registerFileBuffer` (no HTTP range requests);
`app.js` creates one view per table with exactly the columns below and
`queries.js` documents the same contract.

### `fact_offers.parquet`

| Group | Columns |
|---|---|
| Identity | `job_id`, `job_url`, `title`, `company_name` |
| Location | `location_city`, `location_region`, `location_country`, `GeoCountry`, `GeoRegion` |
| Dates | `posted_date`, `PostedYearMonth`, `IsValidPostingDate` |
| Work mode | `WorkModeBucket` |
| Salary (annual EUR) | `SalaryMinAnnual_EUR`, `SalaryMaxAnnual_EUR`, `SalaryMidAnnual_EUR` |
| Classification | `experience_level`, `role_category`, `employment_type`, `source_scraper` |

Deliberately excluded from the export: `description_clean` (size and possible
personal data), the raw `skills` text (it lives in the bridge table),
`salary_quality`, `skills_source`, `experience_level_source`, `posted_date_raw`,
`posted_date_source`, `salary_currency` and `salary_period` (not used by the
~18 ported measures).

### `fact_offer_skills.parquet`

`JobID`, `Skill` — one row per offer + skill (the exact row count is published
in `meta.json`).

### `dim_skill_list.parquet`

`SkillName`, `SkillCategory` — canonical skill catalog. Only catalogued skills
are displayed, exactly as in the Power BI report.

### `dim_calendar.parquet`

`Date`, `DateKey`, `Year`, `MonthNumber`, `MonthName`, `ShortMonth`,
`YearMonth`, `Quarter`, `YearQuarter`, `WeekISO`, `Day`, `WeekDay`, `DayName`,
`IsWeekend` — used for date labels and ordering; the Market Pulse trend uses
`Date` as the day spine of its six-month window (days without offers read as 0).

### `meta.json`

Freshness and export metadata, written after the Parquet files:

| Field | Meaning |
|---|---|
| `export_version` | Contract version of the export (currently `1`). |
| `data_date` | `max(posted_date)` of the export (`YYYY-MM-DD`); the date the dashboard shows as freshness. |
| `generated_at` | Job execution timestamp (ISO-8601 UTC). |
| `mode` | `full` or `aggregated`, decided against the ~25 MB size threshold. The site currently requires `full`: with any other mode it shows a visible warning and stops instead of loading half the data (aggregate files are pending, RF-4/T-19). |
| `tables` | Row count per contract table. |
| `sources` | Row count per `source_scraper`. |
| `size_bytes` | Total size of the four Parquet files. |

### Degradation behavior

The app never invents figures and never leaves a blank page: each failure
raises a visible notice (with the last known `data_date` for export problems,
and freshness in the header whenever `meta.json` loaded), and an incomplete
export never renders figures (views stay off).

| Case | Visitor sees | Workflow guarantee |
|---|---|---|
| `meta.json` missing | "No hay datos publicados"; freshness falls back to "Sin datos disponibles" (no date is knowable). | Download/validation fail; `deploy` never runs. |
| Old export | Freshness header shows `data_date`/`generated_at` from `meta.json`; stale data is never silent. | Freshness only moves forward with a new run. |
| Unreadable Parquet | "Fichero de datos ilegible" warning naming the file and the last data date; status degraded, filters off. | Not detected in CI (only non-empty + `meta.json` JSON are validated); caught by the app. |
| Missing Parquet | "Datos incompletos" warning naming the file and the last data date. | `curl --fail` + non-empty check fail `build`. |
| CDN down | "No se pudieron cargar las librerías" error, status "Datos no disponibles"; freshness still visible. | n/a (client-side; pinned versions above). |
| `mode: "aggregated"` | "Modo de datos no soportado" warning with the last data date; nothing loads (does not apply today: ~3 MB < 25 MB, `mode: "full"`). | Not enforced in CI; the app refuses to load anything but `full` (site contract). |
| SAS without permission | n/a (no visitor). | `curl --fail` fails `build`; `deploy` (`needs: build`) skipped: no partial deploy. |

**Local verification:** the app has no test framework (simple stack, by design);
`filters.js` is a pure module that can also run under Node, and every view is
checked locally against this snapshot with the same files and paths as Pages.
