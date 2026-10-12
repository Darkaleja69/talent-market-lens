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

/* ========================================================================== *
 * Chart aggregations (§7 of the plan; no new business logic)
 * ========================================================================== */

/**
 * Job Postings by Month (Market Pulse line chart, 'Total Job Offers' per
 * month). Ordered oldest first; months without data are absent.
 *
 * `months` limits the result to the last N months **with data in the current
 * context**: the N most recent `PostedYearMonth` values that keep offers after
 * the global filters and the salary range, so a gap inside the window does not
 * shorten the line. The rows are picked with a descending subquery (LIMIT N)
 * and re-ordered oldest first. Without `months` (null or non-positive) the
 * behaviour is unchanged: every month with data.
 */
export function monthlyTrend(options = {}) {
  const {
    where = '',
    months = null,
    minSalary = DEFAULT_SALARY_MIN,
    maxSalary = DEFAULT_SALARY_MAX,
  } = options;
  const monthsCount = months == null ? null : asCount(months, 0);
  const base = `SELECT f.PostedYearMonth AS year_month,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE f.PostedYearMonth IS NOT NULL
  AND ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY f.PostedYearMonth`;
  if (monthsCount == null || monthsCount <= 0) {
    return `${base}
ORDER BY year_month`;
  }
  return `SELECT year_month, job_offers
FROM (
  ${base}
  ORDER BY year_month DESC
  LIMIT ${monthsCount}
) AS recent_months
ORDER BY year_month`;
}

/** Top Roles (Market Pulse, clustered bar, TopN 10 by 'Total Job Offers'). */
export function topRoles(options = {}) {
  const { where = '', limit = 10, minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT f.role_category AS role_category,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE f.role_category IS NOT NULL
  AND ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY f.role_category
ORDER BY job_offers DESC, role_category
${limitClause(limit)}`.trim();
}

/** Top Companies (Market Pulse, clustered bar, TopN 10, blanks excluded). */
export function topCompanies(options = {}) {
  const { where = '', limit = 10, minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT f.company_name AS company_name,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.company_name, '')) <> ''
  AND ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY f.company_name
ORDER BY job_offers DESC, company_name
${limitClause(limit)}`.trim();
}

/** Experience Level donut (Market Pulse, 'Total Job Offers' share by bucket). */
export function experienceBreakdown(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT COALESCE(f.experience_level, '(Not specified)') AS experience_level,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY 1
ORDER BY job_offers DESC, experience_level`;
}

/** Avg Salary by Role Category (Salary Insights, 'Avg Mid Salary EUR'). */
export function avgMidSalaryByRole({ where = '' } = {}) {
  return `SELECT COALESCE(f.role_category, '(Not specified)') AS role_category,
       AVG(f.SalaryMidAnnual_EUR) AS avg_mid_salary_eur
FROM fact_offers AS f
WHERE ${midSalaryQualityFilter} AND ${applyWhere(where)}
GROUP BY 1
ORDER BY 1`;
}

/** Avg Salary by Work Model (Salary Insights, 'Avg Mid Salary EUR'). */
export function avgMidSalaryByWorkMode({ where = '' } = {}) {
  return `SELECT COALESCE(f.WorkModeBucket, '(Not specified)') AS work_mode,
       AVG(f.SalaryMidAnnual_EUR) AS avg_mid_salary_eur
FROM fact_offers AS f
WHERE ${midSalaryQualityFilter} AND ${applyWhere(where)}
GROUP BY 1
ORDER BY 1`;
}

/**
 * Top Paying Skills (Salary Insights table). Ports the visual filters:
 * TopN 15 by 'Skill Median Salary EUR' descending plus a minimum 'Skill Demand'
 * threshold (see TOP_PAYING_SKILLS_MIN_DEMAND). The median only looks at offers
 * with a valid mid salary (the measure's own filters), while 'Skill Demand'
 * counts every offer of the skill, exactly like two independent DAX measures
 * in the same table (verified against the Spain capture: GCP 43 077 EUR /
 * 232 offers). Skills without any valid salary are excluded (advanced filter
 * on the median), not ranked with a blank.
 */
export function topPayingSkills(options = {}) {
  const {
    where = '',
    limit = TOP_PAYING_SKILLS_LIMIT,
    minDemand = TOP_PAYING_SKILLS_MIN_DEMAND,
  } = options;
  const demand = asCount(minDemand, TOP_PAYING_SKILLS_MIN_DEMAND);
  const medianExpression = `MEDIAN(f.SalaryMidAnnual_EUR) FILTER (WHERE ${midSalaryQualityFilter})`;
  return `SELECT fs.Skill AS skill,
       s.SkillCategory AS skill_category,
       ${medianExpression} AS skill_median_salary_eur,
       COUNT(DISTINCT fs.JobID) AS skill_demand
FROM fact_offer_skills AS fs
${BRIDGE_JOIN}
${CATALOG_JOIN}
WHERE ${applyWhere(where)}
GROUP BY fs.Skill, s.SkillCategory
HAVING COUNT(DISTINCT fs.JobID) >= ${demand}
  AND ${medianExpression} IS NOT NULL
ORDER BY skill_median_salary_eur DESC NULLS LAST, skill_demand DESC
${limitClause(limit)}`.trim();
}

/** Offers by GeoCountry for the choropleth map (Market Pulse/ Salary Insights). */
export function offersByCountry(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT COALESCE(f.GeoCountry, '(Not specified)') AS country,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY 1
ORDER BY job_offers DESC, country`;
}

/**
 * Offers by GeoRegion for the country-mode choropleth of Salary Insights
 * (T-25): one row per normalized region of the current context plus the
 * `(Not specified)` bucket when GeoRegion is NULL. The country arrives through
 * the global `where` fragment (the chip sets `filters.country`), so this query
 * only shapes the per-region counts; `(Country)`/`(Other)` rows keep their
 * label and have no geometry on the region map.
 */
export function offersByRegion(options = {}) {
  const { where = '', minSalary = DEFAULT_SALARY_MIN, maxSalary = DEFAULT_SALARY_MAX } = options;
  return `SELECT COALESCE(f.GeoRegion, '(Not specified)') AS region,
       COUNT(*) AS job_offers
FROM fact_offers AS f
WHERE ${salaryRangeFilter(minSalary, maxSalary)} AND ${applyWhere(where)}
GROUP BY 1
ORDER BY job_offers DESC, region`;
}

/**
 * Skill Demand by Category donut (Roles & Skills, 'Skill Demand' per catalog
 * category). The DAX measure counts Fact_OfferSkills rows (one row per offer
 * + skill), so an offer mentioning two skills of one category weighs twice;
 * COUNT(*) reproduces it. Verified against the capture: the pair-based shares
 * (29.5/27.6/25.5/13.8/3.6 %) match the report (29.14/27.98/25.96/13.23/3.69 %),
 * while distinct offers would not (32.4/22.0/22.8/16.7/6.2 %).
 */
export function skillDemandByCategory({ where = '' } = {}) {
  return `SELECT s.SkillCategory AS skill_category,
       COUNT(*) AS skill_demand
FROM fact_offer_skills AS fs
${BRIDGE_JOIN}
${CATALOG_JOIN}
WHERE ${applyWhere(where)}
GROUP BY s.SkillCategory
ORDER BY skill_demand DESC, skill_category`;
}

/**
 * Median Salary by Skill Category (Roles & Skills, 'Median Salary by Skill
 * Category' bar chart of the report page; capture in
 * docs/images/powerbi_roles_skills.png). Ports the measure the visual binds,
 * 'Skill Median Salary EUR' (Metrics.tmdl L183-193), grouped by
 * Dim_SkillList[SkillCategory]: DAX aggregates MEDIAN over the Fact_Offers
 * rows of the context, so every offer weighs once per category even when it
 * mentions several skills of that category. The DISTINCT bridge subquery
 * reproduces that weighting (a direct bridge join would count the offer once
 * per matching row), under the same salary quality filter as measure 14
 * (Metrics.tmdl L188-190). Categories without any valid salary keep a NULL
 * median and the visual layer drops them; the salary range does NOT apply, as
 * in measures 12-14. Ordered by median salary, highest first.
 *
 * Snapshot cotejo (data_date 2026-10-09): Data & AI 92 290 EUR, Cloud & DevOps
 * 88 769, Programming Languages 86 044, BI & Analytics 72 336, Frameworks &
 * Web 66 000, ERP & Platforms 64 533, Methods & Practices 60 942 (the last one
 * from a single Kanban offer); dates drift as documented in the module header.
 */
export function medianSalaryBySkillCategory({ where = '' } = {}) {
  return `SELECT d.SkillCategory AS skill_category,
       MEDIAN(f.SalaryMidAnnual_EUR) AS median_mid_salary_eur
FROM (SELECT DISTINCT fs.JobID, s.SkillCategory
      FROM fact_offer_skills AS fs
      ${CATALOG_JOIN}) AS d
JOIN fact_offers AS f ON d.JobID = f.job_id
WHERE ${midSalaryQualityFilter} AND ${applyWhere(where)}
GROUP BY d.SkillCategory
ORDER BY median_mid_salary_eur DESC NULLS LAST, skill_category`;
}

/* ========================================================================== *
 * Opportunity Explorer (T-13, RF-10): public-column table, search + paging
 * ========================================================================== */

/**
 * Case-insensitive substring search over the job title and the company name.
 * Single quotes are doubled and the LIKE metacharacters (`\`, `%`, `_`) are
 * escaped with an explicit `ESCAPE '\'`, so a typed `%` matches a literal
 * percent sign instead of every row. Empty/blank terms add no predicate.
 */
const explorerSearchFilter = (search) => {
  const term = String(search ?? '').trim();
  if (!term) return '';
  const escaped = term
    .replace(/[\\%_]/g, (character) => `\\${character}`)
    .replace(/'/g, "''");
  const pattern = `'%${escaped}%'`;
  return `(f.title ILIKE ${pattern} ESCAPE '\\' OR f.company_name ILIKE ${pattern} ESCAPE '\\')`;
};

/**
 * Whitelisted sorts of the explorer (callers never inject raw SQL). Every
 * option keeps `job_id` as a stable tie-breaker so paging is deterministic.
 */
const EXPLORER_ORDER_BY = {
  posted_date: 'f.posted_date DESC NULLS LAST, f.job_id',
  title: 'f.title ASC NULLS LAST, f.job_id',
  company_name: 'f.company_name ASC NULLS LAST, f.job_id',
  salary: 'f.SalaryMidAnnual_EUR DESC NULLS LAST, f.job_id',
};

/**
 * One page of the Opportunity Explorer table (RF-10), the public projection
 * the Power BI page shows (docs/images/powerbi_opportunity_explorer.png):
 * title, company, location, work mode, role category, annual median salary,
 * posted date and source, plus `job_id`/`job_url`/`location_region` as keys.
 * No description, quality or provenance column is ever selected.
 *
 * Newest postings first by default. `limit`/`offset` accept only non-negative
 * integers (`null` limit → no LIMIT, `0` offset → no OFFSET); the app always
 * passes a fixed page size, so a single query never returns more than one
 * page. The global filters arrive through `where` (T-09) and the salary range
 * as `minSalary`/`maxSalary`, exactly like the measures.
 */
export function explorerOffers(options = {}) {
  const {
    where = '',
    search = '',
    limit = null,
    offset = 0,
    orderBy = 'posted_date',
    minSalary = DEFAULT_SALARY_MIN,
    maxSalary = DEFAULT_SALARY_MAX,
  } = options;
  const searchFilter = explorerSearchFilter(search);
  const order = EXPLORER_ORDER_BY[orderBy] || EXPLORER_ORDER_BY.posted_date;
  const limitValue = limit == null ? null : Math.max(0, asCount(limit, 0));
  const offsetValue = Math.max(0, asCount(offset, 0));
  const lines = [
    'SELECT f.job_id, f.job_url, f.title, f.company_name,',
    '       f.location_city, f.location_region, f.location_country,',
    '       f.WorkModeBucket, f.role_category, f.SalaryMidAnnual_EUR,',
    '       f.posted_date, f.source_scraper',
    'FROM fact_offers AS f',
    `WHERE ${salaryRangeFilter(minSalary, maxSalary)}`,
    `  AND ${applyWhere(where)}`,
  ];
  if (searchFilter) lines.push(`  AND ${searchFilter}`);
  lines.push(`ORDER BY ${order}`);
  if (limitValue != null) lines.push(`LIMIT ${limitValue}`);
  if (offsetValue > 0) lines.push(`OFFSET ${offsetValue}`);
  return lines.join('\n');
}

/**
 * Total rows of the explorer under the same context as `explorerOffers`
 * (global filters, salary range and search term), so the visible counter and
 * the page clamp share one source of truth.
 */
export function explorerOffersCount(options = {}) {
  const {
    where = '',
    search = '',
    minSalary = DEFAULT_SALARY_MIN,
    maxSalary = DEFAULT_SALARY_MAX,
  } = options;
  const searchFilter = explorerSearchFilter(search);
  const lines = [
    'SELECT COUNT(*) AS total_offers',
    'FROM fact_offers AS f',
    `WHERE ${salaryRangeFilter(minSalary, maxSalary)}`,
    `  AND ${applyWhere(where)}`,
  ];
  if (searchFilter) lines.push(`  AND ${searchFilter}`);
  return lines.join('\n');
}
