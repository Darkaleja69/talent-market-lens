/**
 * queries.js — DuckDB SQL for the public Talent Market Lens dashboard.
 *
 * Pure ES module: no dependencies, no DOM and no browser globals, so it can be
 * imported as-is by Node (local verification harness) and by the browser
 * (DuckDB-WASM). Identifiers and comments are in English (constitution 6).
 *
 * Data contract (fixed view names, created by the app from the Parquet export):
 *   fact_offers(job_id, job_url, title, company_name, location_city,
 *     location_region, location_country, GeoCountry, GeoRegion, posted_date,
 *     PostedYearMonth, IsValidPostingDate, WorkModeBucket,
 *     SalaryMinAnnual_EUR, SalaryMaxAnnual_EUR, SalaryMidAnnual_EUR,
 *     experience_level, role_category, employment_type, source_scraper)
 *   fact_offer_skills(JobID, Skill)
 *   dim_skill_list(SkillName, SkillCategory)
 *   dim_calendar(Date, ...)
 *
 * ---------------------------------------------------------------------------
 * Global-filter injection point (RF-9, implemented in T-09)
 * ---------------------------------------------------------------------------
 * Every builder accepts a `where` option: a trusted SQL boolean expression
 * composed by the app over the aliases `f` (fact_offers) and `fs`
 * (fact_offer_skills, only in builders that join the bridge). Do not include
 * the WHERE keyword; omit it ('' ) for no filters. The fragment is routed
 * through `applyWhere()`, the single injection point of this module, so the
 * app composes the global filters once and every measure honours them.
 *   - `offersLast7Days` / `offersLast30Days` interpolate the fragment twice
 *     and keep the salary range OUT of the anchor CTE: DAX computes
 *     LastValid = CALCULATE(MAX(posted_date)) without the what-if range
 *     (Metrics.tmdl L54-55/L65-66), so the window anchor depends only on
 *     `where`. Pass the range through `minSalary`/`maxSalary` (never inside
 *     `where`) to keep this fidelity; T-09 must do the same.
 *   - `skillShare` interpolates the fragment twice (per-skill demand +
 *     denominator); that fragment must reference `f` only, because the skill
 *     filter is deliberately excluded from the denominator (see the measure).
 *
 * ---------------------------------------------------------------------------
 * Business semantics ported from the Power BI model
 * ---------------------------------------------------------------------------
 * Source of truth: Job_Offers_Dashboard.SemanticModel/definition/tables/
 * Metrics.tmdl (line references per measure below). Power BI `DIVIDE` maps to
 * `x * 1.0 / NULLIF(denominator, 0)` (NULL when the denominator is 0) and DAX
 * comparisons treat BLANK as 0, hence `COALESCE(..., 0)` on salary columns.
 *
 * Skill rows come from `dim_skill_list`: the report tables and the category
 * donut group by Dim_SkillList[SkillName]/[SkillCategory], so only catalogued
 * skills are ever displayed. Joining the catalog reproduces that row set (and
 * provides the category). Known difference: skills present in the bridge but
 * absent from the catalog (~2 900 free-text skills) are not shown, exactly as
 * in the report.
 *
 * ---------------------------------------------------------------------------
 * Local verification (T-07, 2026-10-10; snapshot data_date 2026-10-09)
 * ---------------------------------------------------------------------------
 * The real SQL of this module was executed against the local snapshot
 * (docs/data/: 22 973 offers, 7 196 with a valid mid salary; 39 829 bridge
 * rows = 39 829 distinct offer+skill pairs) and compared with the captures in
 * docs/images (data date 2026-09-19, Salary Insights additionally filtered to
 * Spain by its slicer). Known differences:
 *   - Dates differ (snapshot 10-09 vs captures 09-19). Re-running with
 *     `f.posted_date <= DATE '2026-09-19'` still drifts because Gold kept
 *     backfilling offers and salaries after the capture: total 19 112 vs
 *     18 174 (+5.2 %); companies 8 039 vs 7 494 (+7.3 %); roles drift up to
 *     +8.7 % (Data Analyst & BI 3 841 vs 3 534).
 *   - Strong agreement (date-restricted): Spain P25 23 500 EUR (=), median
 *     31 000 vs 31 500, P75 40 500 vs 40 000, disclosure 14.7 % vs 14.9 %;
 *     skill medians Deep Learning 133 368 (=) and PyTorch 131 367 (=);
 *     countries hiring 8 (=); category donut within ~0.6 pp (max +0.54 pp on
 *     BI & Analytics). Top Paying Skills (Spain): GitLab 45 000 (=),
 *     PyTorch/TensorFlow 42 500 (=), demand column counting every offer of
 *     the skill (GCP 257 vs 232, GitLab 66 vs 62).
 *   - The Spain cotejo uses `f.GeoCountry = 'Spain'`; the report page filters
 *     Dim_Geo[Country] through FactOffers_to_DimGeo_Region (GeoRegion), a
 *     documented approximation. Under the faithful GeoRegion-based context
 *     the median matches exactly (31 500 EUR).
 *   - The report's monthly line chart is daily (by posted_date); this module
 *     groups by PostedYearMonth as planned in §7.
 *   - 'Skill Demand by Category' counts offer+skill pairs (see the measure):
 *     the capture shows 29.14/27.98/25.96/13.23/3.69 %, this port
 *     29.5/27.6/25.5/13.8/3.6 %.
 *   - In the Spain context (<= 2026-09-19) some Top Paying medians move a lot
 *     on tiny samples: LLM 38 000 vs 42 788 (-11.2 %, 9 salary offers) and
 *     Looker 35 500 vs 42 500 (-16.5 %, 4 salary offers), so both leave the
 *     top 15; Spain Data Scientist 46 791 vs 54 000 and ML&AI 62 153 vs
 *     48 000 also shift with small samples.
 *   - Top Paying Skills: the PBIR does not serialise the condition of its
 *     advanced 'Skill Demand' filter; the port uses demand >= 5, overridable
 *     via `minDemand`.
 *   - Chart total semantics: the report's 'Top Roles' bar sums exactly to the
 *     capture total (18 174), confirming the salary-range context used here.
 *   - Case-insensitive text: the hiring counts fold case with LOWER(TRIM(...))
 *     to approximate the es-ES collation (cities 2 912 -> 2 905; companies
 *     and countries unchanged); accent folding is NOT replicated.
 */

/** 'Salary Min' what-if default (Metrics.tmdl L7-11). */
export const DEFAULT_SALARY_MIN = 0;
/** 'Salary Max' what-if default (Metrics.tmdl L12-16). */
export const DEFAULT_SALARY_MAX = 20_000_000;
/** Quality cap shared by every salary measure (Metrics.tmdl L110-112, L133, L188-190). */
export const MID_SALARY_QUALITY_MAX = 1_000_000;
/** TopN 15 of the 'Top Paying Skills' visual (visual.json filterConfig). */
export const TOP_PAYING_SKILLS_LIMIT = 15;
/**
 * Minimum demand for the 'Top Paying Skills' visual. The PBIR serializes the
 * advanced filter on 'Skill Demand' without its condition (value stripped), so
 * the port keeps a threshold that excludes statistically meaningless medians;
 * the caller can override it via `minDemand`.
 */
export const TOP_PAYING_SKILLS_MIN_DEMAND = 5;

const asFiniteNumber = (value, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

const asCount = (value, fallback) => {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
};

/**
 * Single injection point for the global filters (see module header).
 * Accepts `''`/null (no filters → TRUE) or a SQL boolean fragment.
 */
const applyWhere = (where) => {
  const fragment = where == null ? '' : String(where).trim();
  return fragment ? `(${fragment})` : 'TRUE';
};

/** Salary-range filter of 'Total Job Offers' (BLANK→0 in DAX comparisons). */
const salaryRangeFilter = (minSalary, maxSalary) => {
  const min = asFiniteNumber(minSalary, DEFAULT_SALARY_MIN);
  const max = asFiniteNumber(maxSalary, DEFAULT_SALARY_MAX);
  return (
    `COALESCE(f.SalaryMaxAnnual_EUR, 0) >= ${min} ` +
    `AND COALESCE(f.SalaryMaxAnnual_EUR, 0) <= ${max}`
  );
};

/** Salary quality filter for every mid salary measure (Metrics.tmdl L188-190). */
const midSalaryQualityFilter =
  `f.SalaryMidAnnual_EUR > 0 AND f.SalaryMidAnnual_EUR <= ${MID_SALARY_QUALITY_MAX} ` +
  'AND COALESCE(f.SalaryMinAnnual_EUR, 0) <= COALESCE(f.SalaryMaxAnnual_EUR, 0)';

const limitClause = (limit) => {
  if (limit == null) return '';
  const n = asCount(limit, 0);
  return n > 0 ? `LIMIT ${n}` : '';
};

const BRIDGE_JOIN = 'JOIN fact_offers AS f ON fs.JobID = f.job_id';
const CATALOG_JOIN = 'JOIN dim_skill_list AS s ON s.SkillName = fs.Skill';

/* ========================================================================== *
 * 1-11. Market Pulse measures
 * ========================================================================== */

/**
 * 1. Total Job Offers — Metrics.tmdl L5-27.
 * COUNTROWS(Fact_Offers) under the global salary range on
 * SalaryMaxAnnual_EUR (defaults: min 0, max 20 000 000). DAX treats BLANK as
 * 0, so offers without salary count as long as 0 is inside the range; hence
 * COALESCE(..., 0). This is the "Total" context base of measures 5-11.
 */
export function totalJobOffers(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT COUNT(*) AS total_job_offers
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}`;
}

/**
 * 2-4. Companies / Countries / Cities Hiring — Metrics.tmdl L29/L37/L45.
 * DISTINCTCOUNT over the column, excluding blanks: DAX drops BLANK and the
 * model also trims empty strings (TRIM(...) <> ""). The tabular engine uses
 * an es-ES collation where text comparisons are case-insensitive while DuckDB
 * compares raw strings, so LOWER(TRIM(...)) approximates it. Known
 * difference: accent folding is NOT replicated (e.g. "Bogotá" vs "Bogota"
 * stay distinct).
 */
const distinctCounting = (column, alias, where) => `SELECT COUNT(DISTINCT LOWER(TRIM(f.${column}))) AS ${alias}
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.${column}, '')) <> ''
  AND ${applyWhere(where)}`;

/** 2. Companies Hiring — Metrics.tmdl L29-35. */
export function companiesHiring({ where = '' } = {}) {
  return distinctCounting('company_name', 'companies_hiring', where);
}

/** 3. Countries Hiring — Metrics.tmdl L37-43. */
export function countriesHiring({ where = '' } = {}) {
  return distinctCounting('location_country', 'countries_hiring', where);
}

/** 4. Cities Hiring — Metrics.tmdl L45-51. */
export function citiesHiring({ where = '' } = {}) {
  return distinctCounting('location_city', 'cities_hiring', where);
}

/**
 * 5-6. Offers Last 7/30 Days — Metrics.tmdl L53-60 / L64-73.
 * DAX: [Total Job Offers] inside a window > LastValid - N days, where
 * LastValid = CALCULATE(MAX(posted_date)) ignores the what-if salary range
 * (that range lives inside 'Total Job Offers', L18-25). Faithful port: the
 * anchor CTE uses only `where`; the count keeps range + `where` + window.
 */
const windowedOffers = (days, alias, { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = {}) => {
  const range = salaryRangeFilter(minSalary, maxSalary);
  const context = applyWhere(where);
  return `WITH context AS (
  SELECT MAX(f.posted_date) AS last_posting_date
  FROM fact_offers AS f
  WHERE ${context}
)
SELECT COUNT(*) AS ${alias}
FROM fact_offers AS f, context
WHERE ${range} AND ${context}
  AND f.posted_date > context.last_posting_date - INTERVAL ${days} DAY`;
};

/** 5. Offers Last 7 Days — Metrics.tmdl L53-62 ('Total' + window > max - 7). */
export function offersLast7Days(options = {}) {
  return windowedOffers(7, 'offers_last_7_days', options);
}

/** 6. Offers Last 30 Days — Metrics.tmdl L64-73 ('Total' + window > max - 30). */
export function offersLast30Days(options = {}) {
  return windowedOffers(30, 'offers_last_30_days', options);
}

/** 7-10. Work-mode shares — Metrics.tmdl L79-105. DIVIDE → NULLIF(total, 0). */
const workModeShare = (modes, alias, { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = {}) => {
  const list = modes.map((mode) => `'${mode}'`).join(', ');
  return `SELECT COUNT(*) FILTER (WHERE f.WorkModeBucket IN (${list})) * 1.0
       / NULLIF(COUNT(*), 0) AS ${alias}
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}`;
};

/** 7. Remote Share = Remote Offers / Total Job Offers — Metrics.tmdl L79, L87-89. */
export function remoteShare(options = {}) {
  return workModeShare(['Remote'], 'remote_share', options);
}

/** 8. Hybrid Share = Hybrid Offers / Total Job Offers — Metrics.tmdl L83, L91-93. */
export function hybridShare(options = {}) {
  return workModeShare(['Hybrid'], 'hybrid_share', options);
}

/** 9. On-site Share — Metrics.tmdl L95-101. */
export function onSiteShare(options = {}) {
  return workModeShare(['On-site'], 'on_site_share', options);
}

/** 10. Remote-Friendly Share = (Remote + Hybrid) / Total — Metrics.tmdl L103-105. */
export function remoteFriendlyShare(options = {}) {
  return workModeShare(['Remote', 'Hybrid'], 'remote_friendly_share', options);
}

/**
 * 11. Salary Disclosure Rate — Metrics.tmdl L117-119 with 'Offers With Salary'
 * L552-560: numerator = Total context with a valid mid salary (mid > 0,
 * mid <= 1 000 000, min <= max; BLANK→0 via COALESCE). DIVIDE → NULLIF(total, 0).
 */
export function salaryDisclosureRate(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT COUNT(*) FILTER (WHERE ${midSalaryQualityFilter}) * 1.0
       / NULLIF(COUNT(*), 0) AS salary_disclosure_rate
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}`;
}

/* ========================================================================== *
 * 12-18. Roles & Skills and Salary Insights measures
 * ========================================================================== */

/**
 * 12. Skill Demand — Metrics.tmdl L174-176.
 * Distinct offers per skill (COUNT(DISTINCT fs.JobID); the bridge has one row
 * per offer+skill). The salary range does NOT apply, as in the model, and the
 * global filters propagate through the offers join.
 */
export function skillDemand({ where = '', limit = null } = {}) {
  return `SELECT fs.Skill AS skill,
       s.SkillCategory AS skill_category,
       COUNT(DISTINCT fs.JobID) AS skill_demand
FROM fact_offer_skills AS fs
${BRIDGE_JOIN}
${CATALOG_JOIN}
WHERE ${applyWhere(where)}
GROUP BY fs.Skill, s.SkillCategory
ORDER BY skill_demand DESC, skill
${limitClause(limit)}`.trim();
}

/**
 * 13. Skill Share — Metrics.tmdl L179-181.
 * Skill Demand / Total Job Offers of the context excluding the skill filter
 * (DAX: ALLSELECTED(Fact_Offers) + ALL(Dim_SkillList)). The denominator is
 * the Total measure (salary range included) without any skill restriction;
 * the caller must NOT put the skill filter inside `where` (see module header).
 */
export function skillShare(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  const context = applyWhere(where);
  const total = `(SELECT COUNT(*) FROM fact_offers AS f WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${context})`;
  return `SELECT fs.Skill AS skill,
       s.SkillCategory AS skill_category,
       COUNT(DISTINCT fs.JobID) AS skill_demand,
       COUNT(DISTINCT fs.JobID) * 1.0 / NULLIF(${total}, 0) AS skill_share
FROM fact_offer_skills AS fs
${BRIDGE_JOIN}
${CATALOG_JOIN}
WHERE ${context}
GROUP BY fs.Skill, s.SkillCategory
ORDER BY skill_demand DESC, skill`;
}

/**
 * 14. Skill Median Salary EUR — Metrics.tmdl L183-193.
 * MEDIAN(SalaryMidAnnual_EUR) over the offers of each skill that pass the mid
 * salary quality filter. Skills without qualifying offers are absent (the
 * report shows them blank); the visual layer can fill the gap.
 */
export function skillMedianSalary({ where = '' } = {}) {
  return `SELECT fs.Skill AS skill,
       s.SkillCategory AS skill_category,
       MEDIAN(f.SalaryMidAnnual_EUR) AS skill_median_salary_eur
FROM fact_offer_skills AS fs
${BRIDGE_JOIN}
${CATALOG_JOIN}
WHERE ${midSalaryQualityFilter} AND ${applyWhere(where)}
GROUP BY fs.Skill, s.SkillCategory
ORDER BY skill`;
}

/** 15-18. Global salary statistics — same quality filter, no salary range. */
const midSalaryStatistic = (expression, alias, { where = '' } = {}) =>
  `SELECT ${expression} AS ${alias}
FROM fact_offers AS f
WHERE ${midSalaryQualityFilter} AND ${applyWhere(where)}`;

/** 15. Avg Mid Salary EUR — Metrics.tmdl L133-135 (AVERAGE). */
export function avgMidSalary(options = {}) {
  return midSalaryStatistic('AVG(f.SalaryMidAnnual_EUR)', 'avg_mid_salary_eur', options);
}

/** 16. Median Mid Salary EUR — Metrics.tmdl L151-153 (MEDIAN). */
export function medianMidSalary(options = {}) {
  return midSalaryStatistic('MEDIAN(f.SalaryMidAnnual_EUR)', 'median_mid_salary_eur', options);
}

/** 17. P25 Mid Salary EUR — Metrics.tmdl L157-159 (PERCENTILE.INC 0.25 = quantile_cont). */
export function p25MidSalary(options = {}) {
  return midSalaryStatistic('quantile_cont(f.SalaryMidAnnual_EUR, 0.25)', 'p25_mid_salary_eur', options);
}

/** 18. P75 Mid Salary EUR — Metrics.tmdl L163-165 (PERCENTILE.INC 0.75 = quantile_cont). */
export function p75MidSalary(options = {}) {
  return midSalaryStatistic('quantile_cont(f.SalaryMidAnnual_EUR, 0.75)', 'p75_mid_salary_eur', options);
}

