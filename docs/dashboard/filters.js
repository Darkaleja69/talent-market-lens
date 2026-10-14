/**
 * filters.js — global filter state and WHERE composition (RF-9) for the public
 * Talent Market Lens dashboard.
 *
 * Pure ES module: no DOM, no browser globals and no side effects, so it can be
 * imported from the browser (app.js) and from Node during local verification.
 * Identifiers and comments are in English (constitution 6).
 *
 * `buildWhere()` is the single compositor of the trusted SQL boolean fragment
 * described in the header of `queries.js` (T-07): it references the alias `f`
 * (fact_offers) only, never includes the WHERE keyword and returns '' when no
 * filter is active. `queries.js` interpolates it through its single injection
 * point (`applyWhere`), so every measure honours the same global context.
 *
 * Field mapping (plan §7):
 *   country     → f.GeoCountry
 *   experience  → f.experience_level
 *   role        → f.role_category
 *   workMode    → f.WorkModeBucket
 *   company     → f.company_name
 *   source      → f.source_scraper
 *   skill       → EXISTS over fact_offer_skills for f.job_id (universal form,
 *                 valid in every builder, including those that do not join the
 *                 bridge)
 *
 * Salary range: deliberately NOT part of the fragment. It travels as
 * `minSalary`/`maxSalary` (see `salaryBounds`) because the 7/30-day windows
 * anchor the period without the what-if range (module header of queries.js).
 *
 * Skill Share: its denominator excludes the skill filter by design
 * (Metrics.tmdl L179-181), so its caller must request
 * `buildWhere(filters, { includeSkill: false })` and restrict the displayed
 * rows afterwards; the common fragment never uses the `fs` alias.
 */

import { DEFAULT_SALARY_MAX, DEFAULT_SALARY_MIN } from './queries.js';

/** Text filter keys in control order; each maps 1:1 to a fact_offers column. */
export const FILTER_KEYS = ['country', 'experience', 'role', 'workMode', 'company', 'skill', 'source'];

/** Numeric salary-range keys (applied to SalaryMaxAnnual_EUR, as the model). */
export const SALARY_KEYS = ['salaryMin', 'salaryMax'];

const COLUMN_FILTERS = {
  country: 'f.GeoCountry',
  experience: 'f.experience_level',
  role: 'f.role_category',
  workMode: 'f.WorkModeBucket',
  company: 'f.company_name',
  source: 'f.source_scraper',
};

/** Empty filter state: no categorical filters and the model's default range. */
export function createFilters() {
  return {
    country: '',
    experience: '',
    role: '',
    workMode: '',
    company: '',
    skill: '',
    source: '',
    salaryMin: '',
    salaryMax: '',
  };
}

/** True for null/undefined/blank values; those must never produce a predicate. */
export function isBlank(value) {
  return value == null || String(value).trim() === '';
}

/** SQL string literal: single quotes are doubled (`''`), the only escape needed. */
export function sqlStringLiteral(value) {
  return `'${String(value ?? '').replace(/'/g, "''")}'`;
}

/**
 * Skill predicate: the offer exists in fact_offer_skills. Uses the `sf` alias
 * (never `fs`) so it can be injected into builders that already join the bridge
 * under `fs`.
 */
const skillExists = (skill) =>
  'EXISTS (SELECT 1 FROM fact_offer_skills AS sf' +
  ` WHERE sf.JobID = f.job_id AND sf.Skill = ${sqlStringLiteral(skill)})`;

/**
 * Single compositor of the common WHERE fragment from the filter state.
 * Returns '' when there is no active text filter; blank values are ignored
 * (never `= ''`) and values are quote-escaped.
 *
 * `includeSkill: false` is for Skill Share, whose denominator excludes the
 * skill filter by design (see module header).
 */
export function buildWhere(filters = {}, { includeSkill = true } = {}) {
  const parts = [];
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (isBlank(value)) continue;
    if (key === 'skill') {
      if (includeSkill) parts.push(skillExists(value));
    } else {
      parts.push(`${COLUMN_FILTERS[key]} = ${sqlStringLiteral(value)}`);
    }
  }
  return parts.join(' AND ');
}

const parseSalaryBound = (value, fallback) => {
  if (isBlank(value)) return fallback;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
};

/** Salary range over SalaryMaxAnnual_EUR with the model's what-if defaults. */
export function salaryBounds(filters = {}) {
  return {
    minSalary: parseSalaryBound(filters.salaryMin, DEFAULT_SALARY_MIN),
    maxSalary: parseSalaryBound(filters.salaryMax, DEFAULT_SALARY_MAX),
  };
}

/** True when the range differs from the model defaults. */
export function hasSalaryFilter(filters = {}) {
  const { minSalary, maxSalary } = salaryBounds(filters);
  return minSalary !== DEFAULT_SALARY_MIN || maxSalary !== DEFAULT_SALARY_MAX;
}

/** True when any categorical filter or a non-default salary range is set. */
export function hasActiveFilters(filters = {}) {
  return FILTER_KEYS.some((key) => !isBlank(filters[key])) || hasSalaryFilter(filters);
}

/**
 * Merges a patch into the current state, keeping only known keys and
 * normalising every value to a string (both the form controls and the
 * console/`window.tml` surface feed it).
 */
export function mergeFilters(current = {}, patch = {}) {
  const next = createFilters();
  for (const key of [...FILTER_KEYS, ...SALARY_KEYS]) {
    const value = key in patch ? patch[key] : current[key];
    next[key] = value == null ? '' : String(value);
  }
  return next;
}

/**
 * Options object accepted by every builder of queries.js: the composed WHERE
 * plus the salary range (ignored by builders that do not take it).
 */
export function buildQueryOptions(filters = {}, { includeSkill = true } = {}) {
  return { where: buildWhere(filters, { includeSkill }), ...salaryBounds(filters) };
}
