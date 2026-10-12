/**
 * app.js — bootstrap and shell of the public Talent Market Lens dashboard.
 *
 * Scope of this file (T-08, RF-3/RF-6/RF-14/RF-16): load `meta.json` and the
 * four Parquet files with plain `fetch`, register them in DuckDB-WASM with
 * `registerFileBuffer` (no range requests), create the contract views
 * (`fact_offers`, `fact_offer_skills`, `dim_skill_list`, `dim_calendar`), run a
 * smoke query and render the hash-routed shell with visible freshness. View
 * contents arrive view by view (T-10–T-14): Market Pulse is implemented in
 * T-10 (KPIs + ECharts), Roles & Skills in T-11 (benchmark table + bubble +
 * category donut) plus the median salary by skill category chart (T-24),
 * Salary Insights in T-12 (KPIs 15-18, averages by role
 * and work mode, top paying skills and the choropleth with the versioned
 * GeoJSON) plus the country selector and per-region map (T-25), the
 * Opportunity Explorer in T-13 (searchable paginated
 * table) and the Methodology in T-14 (sources, pipeline trace, salary
 * normalisation, dates, freshness from `meta.json`, limitations and legal
 * notice; no SQL and no charts). Every view now has a renderer; the localised
 * placeholder only shows while the data layer is unavailable. The SQL itself
 * lives in `queries.js` (T-07) and the ECharts options in `charts.js` (T-10,
 * T-11).
 *
 * T-09 (RF-9): the global filter state lives here and is composed into one
 * WHERE fragment by `filters.js`; the categorical controls are populated once
 * with SELECT DISTINCT and the active filters are shown as removable chips.
 * T-22 collapses the control grid behind a visible toggle button (with a short
 * help text) while the chips and "clear all" stay reachable with the panel
 * closed. T-10 adds the Market Pulse renderer: a filter change re-renders the
 * active view (same debounce), every render disposes the previous ECharts
 * instances and a missing ECharts leaves the KPIs with a localised note where
 * charts would be.
 *
 * Degradation (RF-14): missing files, unsupported WASM, CDN failures and a
 * non-`full` export mode never leave a blank page; each case raises a visible
 * notice with the last known data date, the status bar reflects the state and
 * the filter controls stay disabled.
 *
 * CDN dependencies are pinned through the import map in `index.html`
 * (DuckDB-WASM 1.32.0, ECharts 6.1.0). Both are imported dynamically so a CDN
 * failure can be caught and reported.
 *
 * Identifiers and comments are in English (constitution 6); user texts live in
 * i18n.js (Spanish/English).
 */

import {
  FILTER_KEYS,
  buildQueryOptions,
  buildWhere,
  createFilters,
  hasActiveFilters,
  hasSalaryFilter,
  isBlank,
  mergeFilters,
  salaryBounds,
} from './filters.js';
import * as queries from './queries.js';
import {
  barChart,
  bubbleChart,
  choroplethOption,
  donutChart,
  lineChart,
} from './charts.js';
import {
  LANGUAGES,
  applyDocumentTranslations,
  formatBytes,
  formatCount,
  formatDate,
  formatDateTime,
  formatMonth,
  formatPercent,
  formatSalary,
  formatSalaryCompact,
  persistLanguage,
  resolveInitialLanguage,
  translate,
} from './i18n.js';

/* ========================================================================== *
 * Data contract (must match the header of queries.js)
 * ========================================================================== */

const DATA_DIR = '../data/';
const DATA_FILES = ['fact_offers', 'fact_offer_skills', 'dim_skill_list', 'dim_calendar'];
const SMOKE_QUERY = 'SELECT COUNT(*)::BIGINT AS n FROM fact_offers';

const FACT_OFFERS_COLUMNS = [
  'job_id', 'job_url', 'title', 'company_name',
  'location_city', 'location_region', 'location_country', 'GeoCountry', 'GeoRegion',
  'posted_date', 'PostedYearMonth', 'IsValidPostingDate',
  'WorkModeBucket',
  'SalaryMinAnnual_EUR', 'SalaryMaxAnnual_EUR', 'SalaryMidAnnual_EUR',
  'experience_level', 'role_category', 'employment_type', 'source_scraper',
];

const VIEW_COLUMNS = {
  fact_offers: FACT_OFFERS_COLUMNS,
  fact_offer_skills: ['JobID', 'Skill'],
  dim_skill_list: ['SkillName', 'SkillCategory'],
  dim_calendar: [
    'Date', 'DateKey', 'Year', 'MonthNumber', 'MonthName', 'ShortMonth',
    'YearMonth', 'Quarter', 'YearQuarter', 'WeekISO', 'Day', 'WeekDay',
    'DayName', 'IsWeekend',
  ],
};

/* ========================================================================== *
 * Views and routing
 * ========================================================================== */

const VIEWS = [
  { id: 'market-pulse', titleKey: 'nav.marketPulse', descriptionKey: 'view.marketPulse.description' },
  { id: 'roles-skills', titleKey: 'nav.rolesSkills', descriptionKey: 'view.rolesSkills.description' },
  { id: 'salary-insights', titleKey: 'nav.salaryInsights', descriptionKey: 'view.salaryInsights.description' },
  { id: 'opportunity-explorer', titleKey: 'nav.opportunityExplorer', descriptionKey: 'view.opportunityExplorer.description' },
  { id: 'methodology', titleKey: 'nav.methodology', descriptionKey: 'view.methodology.description' },
];
const DEFAULT_VIEW = VIEWS[0];

/** Views with a real renderer; the rest keep the localised placeholder. */
const VIEW_RENDERERS = {
  'market-pulse': renderMarketPulse,
  'roles-skills': renderRolesSkills,
  'salary-insights': renderSalaryInsights,
  'opportunity-explorer': renderOpportunityExplorer,
  'methodology': renderMethodology,
};

/**
 * Views rendered from `meta.json` and local text only (no DuckDB). They must
 * stay readable while the data layer is degraded (RF-13/RF-14), so
 * `renderActiveView` does not gate them behind `viewDataReady()`.
 */
const DATA_FREE_VIEWS = new Set(['methodology']);

/* ========================================================================== *
 * State
 * ========================================================================== */

const state = {
  language: 'en',
  meta: null,
  duckdb: null,
  echarts: null,
  db: null,
  conn: null,
  loadedTables: [],
  missingFiles: [],
  unreadableFiles: [],
  smokeCount: null,
  statusKey: 'status.initializing',
  statusState: 'pending',
  statusVars: {},
  notices: [],
  activeViewId: DEFAULT_VIEW.id,
  filters: createFilters(),
  filtersReady: false,
};

const elements = {
  status: null,
  freshness: null,
  notices: null,
  view: null,
  navLinks: [],
  languageSelect: null,
  filterControls: {},
  filterChips: null,
  clearFilters: null,
  filtersToggle: null,
  filtersBody: null,
  companyOptions: null,
};

const t = (key, vars) => translate(state.language, key, vars);

/* ========================================================================== *
 * Rendering helpers
 * ========================================================================== */

const safeStorage = () => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

function setStatus(key, statusState, vars = {}) {
  state.statusKey = key;
  state.statusState = statusState;
  state.statusVars = vars;
  renderStatus();
}

function renderStatus() {
  if (!elements.status) return;
  const vars = { ...state.statusVars };
  if (state.smokeCount != null) vars.count = formatCount(state.smokeCount, state.language);
  const failedFiles = [...state.missingFiles, ...state.unreadableFiles];
  if (failedFiles.length > 0) {
    vars.files = failedFiles.map((name) => `${name}.parquet`).join(', ');
  }
  elements.status.textContent = t(state.statusKey, vars);
  elements.status.dataset.state = state.statusState;
}

function renderFreshness() {
  if (!elements.freshness) return;
  const meta = state.meta;
  if (!meta) {
    elements.freshness.textContent = t('freshness.unknown');
    elements.freshness.dataset.state = 'pending';
    return;
  }
  const dataDate = formatDate(meta.data_date, state.language);
  const generatedAt = formatDateTime(meta.generated_at, state.language);
  elements.freshness.textContent =
    `${t('freshness.label')}: ${dataDate} · ${t('freshness.updated')}: ${generatedAt} UTC`;
  elements.freshness.title =
    `${t('freshness.title')} — data_date: ${meta.data_date}, generated_at: ${meta.generated_at}`;
  elements.freshness.dataset.state = 'ok';
}

/** Adds a notice (replacing the one with the same id) and re-renders them. */
function showNotice({ id, level, titleKey, messageKey, vars = {}, rawDate = null }) {
  const notice = { id, level, titleKey, messageKey, vars, rawDate };
  const index = state.notices.findIndex((item) => item.id === id);
  if (index >= 0) state.notices[index] = notice;
  else state.notices.push(notice);
  renderNotices();
}

function renderNotices() {
  if (!elements.notices) return;
  elements.notices.replaceChildren(...state.notices.map((notice) => {
    const box = document.createElement('div');
    box.className = `notice notice--${notice.level}`;
    box.dataset.notice = notice.id;
    box.setAttribute('role', 'alert');

    const title = document.createElement('p');
    title.className = 'notice__title';
    const label = document.createElement('span');
    label.className = 'visually-hidden';
    label.textContent =
      `${notice.level === 'error' ? t('notice.errorLabel') : t('notice.warningLabel')}: `;
    title.append(label, document.createTextNode(t(notice.titleKey)));

    const message = document.createElement('p');
    message.className = 'notice__message';
    const vars = { ...notice.vars };
    if (notice.rawDate) vars.date = formatDate(notice.rawDate, state.language);
    message.textContent = t(notice.messageKey, vars);

    box.append(title, message);
    return box;
  }));
}

function activeView() {
  return VIEWS.find((view) => view.id === state.activeViewId) || DEFAULT_VIEW;
}

/** Token of the latest view render; stale async renders discard themselves. */
let viewRenderId = 0;

function buildViewHeader(view, { pending = false } = {}) {
  const title = document.createElement('h2');
  title.className = 'view__title';
  title.id = 'view-title';
  title.textContent = t(view.titleKey);

  const header = document.createElement('div');
  header.className = 'view__header';
  header.append(title);
  if (pending) {
    const badge = document.createElement('span');
    badge.className = 'view__badge';
    badge.textContent = t('view.pending');
    header.append(badge);
  }
  return header;
}

/** Small localised paragraph (empty states, degradation notices, errors). */
function noteElement(key, className) {
  const note = document.createElement('p');
  note.className = className;
  note.textContent = t(key);
  return note;
}

/** True while the data layer can answer view queries (filters + DuckDB ready). */
function viewDataReady() {
  return state.filtersReady && state.conn != null && state.loadedTables.includes('fact_offers');
}

/** Badge + description shown while a view has no renderer or cannot query. */
function renderViewPlaceholder(view, { pending }) {
  if (!elements.view) return;
  // The localised description doubles as placeholder; no figures are shown
  // until the renderer can run with ready filters and a live DuckDB connection.
  const placeholder = document.createElement('p');
  placeholder.className = 'view__placeholder';
  placeholder.textContent = t(view.descriptionKey);
  elements.view.replaceChildren(buildViewHeader(view, { pending }), placeholder);
}

function updateNavigation(view) {
  if (elements.navLinks.length > 0) {
    const activeHash = `#/${view.id}`;
    elements.navLinks.forEach((link) => {
      if (link.getAttribute('href') === activeHash) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }
  document.title = `${t(view.titleKey)} · ${t('app.title')}`;
}

/**
 * Renders the active view: localised header plus either the real view
 * renderer (T-10–T-14) or the placeholder. Charts of the previous render are
 * disposed first; the request token discards stale async renders so rapid
 * navigation or filter changes never mix contents.
 */
async function renderActiveView() {
  const view = activeView();
  const requestId = ++viewRenderId;
  disposeCharts();
  updateNavigation(view);
  if (!elements.view) return;
  elements.view.dataset.view = view.id;

  const renderer = VIEW_RENDERERS[view.id];
  const needsData = !DATA_FREE_VIEWS.has(view.id);
  if (!renderer || (needsData && !viewDataReady())) {
    renderViewPlaceholder(view, { pending: !renderer });
    return;
  }

  const body = document.createElement('div');
  body.className = 'view__body';
  elements.view.replaceChildren(buildViewHeader(view), body);
  try {
    await renderer({ requestId, body });
  } catch (error) {
    if (requestId !== viewRenderId) return;
    console.error(`[tml] view "${view.id}" render failed:`, error);
    body.replaceChildren(noteElement('view.error', 'view__error'));
  }
}

function viewFromHash(hash) {
  const slug = String(hash || '')
    .replace(/^#\/?/, '')
    .replace(/\/+$/, '')
    .toLowerCase();
  return VIEWS.find((view) => view.id === slug) || null;
}

function applyRoute() {
  const view = viewFromHash(window.location.hash);
  state.activeViewId = (view || DEFAULT_VIEW).id;
  if (!view) {
    // Normalise unknown/missing hashes without polluting the history.
    window.history.replaceState(null, '', `#/${DEFAULT_VIEW.id}`);
  }
  renderActiveView();
}

function renderAll() {
  renderFreshness();
  renderStatus();
  renderNotices();
  renderActiveView();
  renderFilterChips();
}

/* ========================================================================== *
 * Loading helpers
 * ========================================================================== */

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  return response.json();
}

async function fetchArrayBuffer(url) {
  const response = await fetch(url, { cache: 'no-cache' });
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  return response.arrayBuffer();
}

function wasmSupported() {
  return (
    typeof WebAssembly === 'object' &&
    WebAssembly !== null &&
    typeof WebAssembly.instantiate === 'function'
  );
}

/** Downloads the four Parquet files as complete buffers (no range requests). */
async function downloadData() {
  const buffers = new Map();
  let done = 0;
  setStatus('status.loadingData', 'pending', { done, total: DATA_FILES.length });
  await Promise.all(DATA_FILES.map(async (table) => {
    try {
      const buffer = await fetchArrayBuffer(`${DATA_DIR}${table}.parquet`);
      buffers.set(table, new Uint8Array(buffer));
    } catch (error) {
      state.missingFiles.push(table);
      console.warn(`[tml] could not download ${table}.parquet:`, error);
    } finally {
      done += 1;
      setStatus('status.loadingData', 'pending', { done, total: DATA_FILES.length });
    }
  }));
  return buffers;
}

/** Opens DuckDB-WASM and registers every downloaded buffer. */
async function openDatabase(buffers) {
  const duckdb = state.duckdb;
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(new Blob(
    [`importScripts("${bundle.mainWorker}");`],
    { type: 'text/javascript' },
  ));
  const worker = new Worker(workerUrl);
  state.db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await state.db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  state.conn = await state.db.connect();
  for (const [table, buffer] of buffers) {
    await state.db.registerFileBuffer(`${table}.parquet`, buffer);
  }
}

/** Creates one named view per loaded table with the exact contract columns. */
async function createViews() {
  for (const table of DATA_FILES) {
    if (!state.loadedTables.includes(table)) continue;
    const columns = VIEW_COLUMNS[table]
      .map((column) => (/^[A-Z]/.test(column) ? `"${column}"` : column))
      .join(', ');
    try {
      await state.conn.query(
        `CREATE OR REPLACE VIEW ${table} AS SELECT ${columns} FROM parquet_scan('${table}.parquet')`,
      );
    } catch (error) {
      state.loadedTables = state.loadedTables.filter((name) => name !== table);
      if (!state.unreadableFiles.includes(table)) state.unreadableFiles.push(table);
      console.warn(`[tml] could not create view ${table}:`, error);
    }
  }
}

/** Turns an Arrow result into plain objects (BigInt → Number for counts). */
export function resultToObjects(result) {
  const fields = result.schema.fields.map((field) => field.name);
  return result.toArray().map((row) => {
    const record = {};
    fields.forEach((name) => {
      const value = row[name];
      record[name] = typeof value === 'bigint' ? Number(value) : value;
    });
    return record;
  });
}

async function runQuery(sql) {
  const result = await state.conn.query(sql);
  return resultToObjects(result);
}

async function runSmokeQuery() {
  if (!state.loadedTables.includes('fact_offers')) return;
  try {
    const rows = await runQuery(SMOKE_QUERY);
    state.smokeCount = rows.length > 0 ? rows[0].n : null;
  } catch (error) {
    console.warn('[tml] smoke query failed:', error);
    state.smokeCount = null;
  }
}

/* ========================================================================== *
 * Language
 * ========================================================================== */

function setLanguage(language) {
  if (!LANGUAGES.includes(language)) return;
  state.language = language;
  persistLanguage(safeStorage(), language);
  applyDocumentTranslations(document, language);
  if (elements.languageSelect) elements.languageSelect.value = language;
  renderAll();
}

function bindLanguageSelector() {
  if (!elements.languageSelect) return;
  elements.languageSelect.value = state.language;
  elements.languageSelect.addEventListener('change', (event) => {
    setLanguage(event.target.value);
  });
}

/* ========================================================================== *
 * Global filters (RF-9): one compositor, visible chips, collapsible panel
 * ========================================================================== */

const FILTER_CONTROL_IDS = {
  country: 'filter-country',
  experience: 'filter-experience',
  role: 'filter-role',
  workMode: 'filter-work-mode',
  company: 'filter-company',
  skill: 'filter-skill',
  source: 'filter-source',
  salaryMin: 'filter-salary-min',
  salaryMax: 'filter-salary-max',
};

const FILTER_INPUT_DEBOUNCE_MS = 250;

/**
 * One-time SELECT DISTINCT population of the categorical controls (RF-9).
 * Skills come from the catalogue joined to the bridge — the same row set the
 * views display (header of queries.js) — and companies have thousands of
 * values, so they feed an input + datalist instead of a select.
 */
const FILTER_OPTION_QUERIES = {
  country: `SELECT DISTINCT f.GeoCountry AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.GeoCountry, '')) <> ''
ORDER BY value`,
  experience: `SELECT DISTINCT f.experience_level AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.experience_level, '')) <> ''
ORDER BY value`,
  role: `SELECT DISTINCT f.role_category AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.role_category, '')) <> ''
ORDER BY value`,
  workMode: `SELECT DISTINCT f.WorkModeBucket AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.WorkModeBucket, '')) <> ''
ORDER BY value`,
  source: `SELECT DISTINCT f.source_scraper AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.source_scraper, '')) <> ''
ORDER BY value`,
  skill: `SELECT DISTINCT fs.Skill AS value
FROM fact_offer_skills AS fs
JOIN dim_skill_list AS s ON s.SkillName = fs.Skill
WHERE TRIM(COALESCE(fs.Skill, '')) <> ''
ORDER BY value`,
};

const COMPANY_OPTIONS_QUERY = `SELECT DISTINCT f.company_name AS value
FROM fact_offers AS f
WHERE TRIM(COALESCE(f.company_name, '')) <> ''
ORDER BY value`;

let filterInputTimer = null;
const pendingFilterPatches = new Map();

function newOptionElement(value) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = value;
  return option;
}

/** Runs the one-time DISTINCT population of every categorical control. */
async function populateFilterOptions() {
  await Promise.all(Object.entries(FILTER_OPTION_QUERIES).map(async ([key, sql]) => {
    const control = elements.filterControls[key];
    if (!control) return;
    const rows = await runQuery(sql);
    control.append(...rows.map((row) => newOptionElement(row.value)));
  }));
  if (elements.companyOptions) {
    const rows = await runQuery(COMPANY_OPTIONS_QUERY);
    elements.companyOptions.append(...rows.map((row) => newOptionElement(row.value)));
  }
}

function setFilterControlsEnabled(enabled) {
  for (const control of Object.values(elements.filterControls)) {
    if (control) control.disabled = !enabled;
  }
  if (elements.clearFilters) {
    elements.clearFilters.disabled = !enabled || !hasActiveFilters(state.filters);
  }
}

/** Localised chip value for the non-default salary range. */
function formatSalaryChip() {
  const { minSalary, maxSalary } = salaryBounds(state.filters);
  const hasMin = minSalary > queries.DEFAULT_SALARY_MIN;
  const hasMax = maxSalary < queries.DEFAULT_SALARY_MAX;
  const format = (value) => formatSalary(value, state.language);
  if (hasMin && hasMax) {
    return t('filter.salary.between', { min: format(minSalary), max: format(maxSalary) });
  }
  if (hasMin) return t('filter.salary.from', { value: format(minSalary) });
  return t('filter.salary.upTo', { value: format(maxSalary) });
}

function activeFilterChips() {
  const chips = [];
  for (const key of FILTER_KEYS) {
    if (isBlank(state.filters[key])) continue;
    chips.push({ key, field: t(`filter.field.${key}`), value: state.filters[key] });
  }
  if (hasSalaryFilter(state.filters)) {
    chips.push({ key: 'salary', field: t('filter.field.salary'), value: formatSalaryChip() });
  }
  return chips;
}

function renderFilterChips() {
  if (!elements.filterChips) return;
  const chips = activeFilterChips();
  if (elements.clearFilters) {
    elements.clearFilters.disabled = !state.filtersReady || chips.length === 0;
  }
  if (chips.length === 0) {
    const hint = document.createElement('p');
    hint.className = 'filters__hint';
    hint.textContent = t('filters.none');
    elements.filterChips.replaceChildren(hint);
    return;
  }
  const list = document.createElement('ul');
  list.className = 'chips';
  for (const chip of chips) {
    const item = document.createElement('li');
    item.className = 'chip';

    const label = document.createElement('span');
    label.className = 'chip__label';
    label.textContent = `${chip.field}:`;

    const value = document.createElement('span');
    value.className = 'chip__value';
    value.textContent = chip.value;

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chip__remove';
    remove.dataset.filterKey = chip.key;
    remove.setAttribute('aria-label', t('filters.remove', { field: chip.field }));
    remove.textContent = '×';

    item.append(label, value, remove);
    list.append(item);
  }
  elements.filterChips.replaceChildren(list);
}

function syncFilterControls() {
  for (const [key, control] of Object.entries(elements.filterControls)) {
    if (control) control.value = state.filters[key] ?? '';
  }
}

function applyFilterChange(filters) {
  state.filters = filters;
  // A new global context restarts the explorer pagination; its local search
  // term is view state and survives (see the Opportunity Explorer section).
  explorerState.page = 1;
  syncFilterControls();
  renderFilterChips();
  if (!state.filtersReady) return Promise.resolve();
  // Awaiting the active view keeps `window.tml.setFilters()` truthful for
  // local checks: when the promise resolves, the render reflects the new state.
  return renderActiveView();
}

function setFilters(patch) {
  return applyFilterChange(mergeFilters(state.filters, patch));
}

function clearFilters() {
  return applyFilterChange(createFilters());
}

/**
 * Debounces free-text inputs (company, salary bounds) without losing patches:
 * each control records its own pending patch and the timer fires them merged,
 * so two fields changed inside the same window both apply.
 */
function queueFilterPatch(key, value, immediate) {
  if (immediate) {
    pendingFilterPatches.clear();
    clearTimeout(filterInputTimer);
    setFilters({ [key]: value });
    return;
  }
  pendingFilterPatches.set(key, value);
  clearTimeout(filterInputTimer);
  filterInputTimer = setTimeout(() => {
    const patch = Object.fromEntries(pendingFilterPatches);
    pendingFilterPatches.clear();
    setFilters(patch);
  }, FILTER_INPUT_DEBOUNCE_MS);
}

function bindFilterControls() {
  for (const [key, control] of Object.entries(elements.filterControls)) {
    if (!control) continue;
    const immediate = control.tagName === 'SELECT';
    control.addEventListener(immediate ? 'change' : 'input', () => {
      queueFilterPatch(key, control.value, immediate);
    });
  }
  if (elements.clearFilters) {
    elements.clearFilters.addEventListener('click', () => clearFilters());
  }
  if (elements.filterChips) {
    elements.filterChips.addEventListener('click', (event) => {
      const button = event.target.closest('[data-filter-key]');
      if (!button) return;
      const key = button.dataset.filterKey;
      const patch = key === 'salary' ? { salaryMin: '', salaryMax: '' } : { [key]: '' };
      setFilters(patch);
    });
  }
}

/**
 * Shows/hides the collapsible control grid. The panel starts closed (the
 * `hidden` attribute in index.html) and `aria-expanded` stays in sync.
 */
function setFiltersPanelOpen(open) {
  if (!elements.filtersToggle || !elements.filtersBody) return;
  elements.filtersBody.hidden = !open;
  elements.filtersToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
}

/**
 * The toggle is a native `type="button"`: Enter/Space fire `click` without
 * extra key handling, and `aria-expanded`/`aria-controls` expose the state.
 */
function bindFiltersToggle() {
  if (!elements.filtersToggle) return;
  elements.filtersToggle.addEventListener('click', () => {
    setFiltersPanelOpen(elements.filtersToggle.getAttribute('aria-expanded') !== 'true');
  });
}

/** Marks the whole filter surface as unavailable (degraded boot paths). */
function markFiltersUnavailable() {
  state.filtersReady = false;
  setFilterControlsEnabled(false);
  renderFilterChips();
}

async function initializeFilters() {
  try {
    await populateFilterOptions();
  } catch (error) {
    console.error('[tml] could not populate the filter controls:', error);
    markFiltersUnavailable();
    return;
  }
  state.filtersReady = true;
  setFilterControlsEnabled(true);
  renderFilterChips();
  await renderActiveView();
}

/* ========================================================================== *
 * Charts (T-10): instance lifecycle
 * ========================================================================== */

/** Live ECharts instances of the active view; disposed on every re-render. */
const activeCharts = [];

function disposeCharts() {
  for (const chart of activeCharts) {
    try {
      chart.dispose();
    } catch (error) {
      console.warn('[tml] could not dispose a chart:', error);
    }
  }
  activeCharts.length = 0;
}

/** Initialises one ECharts instance on `container`; no-op without ECharts. */
function mountChart(container, option) {
  if (!state.echarts || !container) return false;
  const chart = state.echarts.init(container);
  chart.setOption(option);
  activeCharts.push(chart);
  return true;
}

function resizeCharts() {
  for (const chart of activeCharts) {
    try {
      chart.resize();
    } catch (error) {
      console.warn('[tml] could not resize a chart:', error);
    }
  }
}

/* ========================================================================== *
 * Market Pulse (T-10, RF-7/RF-8/RF-16): report KPIs and executive charts
 * ========================================================================== */

/**
 * Market Pulse KPI cards: the four measures of the original report page
 * (docs/images/powerbi_market_pulse.png) — measures 1, 2, 10 and 11 of §7 of
 * the plan. The builders of the other measures stay in `queries.js` (the spec
 * ports them), but the view no longer displays them (T-23).
 */
const MARKET_PULSE_KPIS = [
  { id: 'total', labelKey: 'kpi.totalJobOffers', builder: 'totalJobOffers', alias: 'total_job_offers', format: 'count' },
  { id: 'companies', labelKey: 'kpi.companiesHiring', builder: 'companiesHiring', alias: 'companies_hiring', format: 'count' },
  { id: 'remoteFriendly', labelKey: 'kpi.remoteFriendlyShare', builder: 'remoteFriendlyShare', alias: 'remote_friendly_share', format: 'percent' },
  { id: 'disclosure', labelKey: 'kpi.salaryDisclosureRate', builder: 'salaryDisclosureRate', alias: 'salary_disclosure_rate', format: 'percent' },
];

/** Monthly trend window of the report page: the last six months with data. */
const MARKET_PULSE_TREND_MONTHS = 6;

const EMPTY_VALUE = '—';

/** Localised KPI value: shares arrive as 0-1 ratios, counts as integers. */
function formatKpiValue(value, format) {
  if (value == null) return EMPTY_VALUE;
  if (format === 'percent') return formatPercent(value, state.language);
  if (format === 'salary') return formatSalary(value, state.language);
  return formatCount(value, state.language);
}

function renderKpiList(kpis) {
  const list = document.createElement('ul');
  list.className = 'kpi-grid';
  for (const kpi of kpis) {
    const item = document.createElement('li');
    item.className = 'kpi';
    // Presentation hook for the accent of each KPI card (T-26); carries no logic.
    item.dataset.kpi = kpi.id;
    const label = document.createElement('p');
    label.className = 'kpi__label';
    label.textContent = t(kpi.labelKey);
    const value = document.createElement('p');
    value.className = 'kpi__value';
    value.textContent = formatKpiValue(kpi.value, kpi.format);
    item.append(label, value);
    list.append(item);
  }
  return list;
}

/**
 * Runs the four KPI builders of the report page plus the four chart
 * aggregations in one batch with the current global filters (RF-9). The trend
 * is limited to the last `MARKET_PULSE_TREND_MONTHS` months. Every builder
 * comes from `queries.js`; this module only orchestrates calls and formats
 * results.
 */
async function fetchMarketPulseData(options) {
  const [kpis, trend, topRoles, topCompanies, experience] = await Promise.all([
    Promise.all(MARKET_PULSE_KPIS.map(async (kpi) => {
      const rows = await runQuery(queries[kpi.builder](options));
      return { ...kpi, value: rows.length > 0 ? rows[0][kpi.alias] : null };
    })),
    runQuery(queries.monthlyTrend({ ...options, months: MARKET_PULSE_TREND_MONTHS })),
    runQuery(queries.topRoles(options)),
    runQuery(queries.topCompanies(options)),
    runQuery(queries.experienceBreakdown(options)),
  ]);
  return { kpis, trend, topRoles, topCompanies, experience };
}

const formatCountCallback = (value) => formatCount(value, state.language);
const formatPercentCallback = (value) => formatPercent(value, state.language);
const formatSalaryCallback = (value) => formatSalary(value, state.language);
const formatCompactSalaryCallback = (value) => formatSalaryCompact(value, state.language);

function buildTrendOption(rows) {
  const categories = rows.map((row) => formatMonth(row.year_month, state.language));
  return lineChart({
    categories,
    values: rows.map((row) => Number(row.job_offers) || 0),
    seriesName: t('view.marketPulse.trend.title'),
    formatValue: formatCountCallback,
    rotateLabels: categories.length > 12,
  });
}

function buildRankingOption(rows, nameField, seriesNameKey) {
  const option = barChart({
    categories: rows.map((row) => row[nameField]),
    values: rows.map((row) => Number(row.job_offers) || 0),
    seriesName: t(seriesNameKey),
    horizontal: true,
    formatValue: formatCountCallback,
  });
  // T-28: in the compact Market Pulse ranking cards ECharts' automatic
  // category-label interval drops every other name once the band gets tight
  // (top roles/companies cover 7-10 bars). The names are the readable part of
  // the ranking, so force all of them and disable the overlap pass: measured
  // at 1440×900 with the 10rem bodies the glyphs stay separate and legible.
  option.yAxis.axisLabel.interval = 0;
  option.yAxis.axisLabel.hideOverlap = false;
  return option;
}

function buildExperienceOption(rows) {
  return donutChart({
    items: rows.map((row) => ({ name: row.experience_level, value: Number(row.job_offers) || 0 })),
    seriesName: t('view.marketPulse.experience.title'),
    formatValue: formatCountCallback,
    formatPercent: formatPercentCallback,
  });
}

/**
 * Figure with a localised title, accessible chart container and visible
 * caption. Returns a mount callback (or null without data): ECharts measures
 * the container at `init` time, so the caller must run it once the card is
 * attached to the document. Without data the canvas is replaced by a
 * localised empty note, so the card never shows a blank axes frame.
 */
function appendChartCard(grid, { titleKey, captionKey, wide = false, option, noDataKey = 'view.marketPulse.noData' }) {
  const figure = document.createElement('figure');
  figure.className = `chart-card${wide ? ' chart-card--wide' : ''}`;

  const title = document.createElement('h3');
  title.className = 'chart-card__title';
  title.textContent = t(titleKey);

  const body = document.createElement('div');
  body.className = 'chart-card__body';
  body.setAttribute('role', 'img');
  body.setAttribute('aria-label', `${t(titleKey)}. ${t(captionKey)}`);

  const caption = document.createElement('figcaption');
  caption.className = 'chart-card__caption';
  caption.textContent = t(captionKey);

  figure.append(title, body, caption);
  grid.append(figure);

  if (!option) {
    body.replaceWith(noteElement(noDataKey, 'chart-empty'));
    return null;
  }
  return () => mountChart(body, option);
}

/**
 * Market Pulse renderer (RF-7): the four KPI cards of the report page, the
 * monthly trend by `PostedYearMonth` limited to the last
 * `MARKET_PULSE_TREND_MONTHS` months, top 10 roles, top 10 companies and the
 * experience breakdown. Data, labels and formats come from the current
 * language and the global filters; this function only assembles DOM and mounts
 * charts.
 */
async function renderMarketPulse({ requestId, body }) {
  const options = buildQueryOptions(state.filters);
  const data = await fetchMarketPulseData(options);
  if (requestId !== viewRenderId) return;

  const content = document.createDocumentFragment();
  content.append(renderKpiList(data.kpis));

  if (!state.echarts) {
    content.append(noteElement('view.marketPulse.chartsUnavailable', 'chart-unavailable'));
    body.replaceChildren(content);
    return;
  }

  const grid = document.createElement('div');
  grid.className = 'chart-grid chart-grid--pulse';
  const mounts = [
    appendChartCard(grid, {
      titleKey: 'view.marketPulse.trend.title',
      captionKey: 'view.marketPulse.trend.caption',
      wide: true,
      option: data.trend.length > 0 ? buildTrendOption(data.trend) : null,
    }),
    appendChartCard(grid, {
      titleKey: 'view.marketPulse.topRoles.title',
      captionKey: 'view.marketPulse.topRoles.caption',
      option: data.topRoles.length > 0
        ? buildRankingOption(data.topRoles, 'role_category', 'view.marketPulse.topRoles.title')
        : null,
    }),
    appendChartCard(grid, {
      titleKey: 'view.marketPulse.topCompanies.title',
      captionKey: 'view.marketPulse.topCompanies.caption',
      option: data.topCompanies.length > 0
        ? buildRankingOption(data.topCompanies, 'company_name', 'view.marketPulse.topCompanies.title')
        : null,
    }),
    appendChartCard(grid, {
      titleKey: 'view.marketPulse.experience.title',
      captionKey: 'view.marketPulse.experience.caption',
      option: data.experience.length > 0 ? buildExperienceOption(data.experience) : null,
    }),
  ].filter(Boolean);
  content.append(grid);
  body.replaceChildren(content);
  // Mount only once the cards are attached: ECharts measures its container
  // at init time and a detached node would be measured as 0×0.
  for (const mount of mounts) mount();
}

/* ========================================================================== *
 * Roles & Skills (T-11/T-24, RF-7/RF-8/RF-16): benchmark table + bubble +
 * category donut + median salary by skill category
 * ========================================================================== */

/** Benchmark columns of the view; `numeric` right-aligns and formats values. */
const BENCHMARK_COLUMNS = [
  { key: 'skill', labelKey: 'view.rolesSkills.table.skill' },
  { key: 'category', labelKey: 'view.rolesSkills.table.category' },
  { key: 'demand', labelKey: 'view.rolesSkills.table.demand', numeric: true, format: 'count' },
  { key: 'share', labelKey: 'view.rolesSkills.table.share', numeric: true, format: 'percent' },
  { key: 'median', labelKey: 'view.rolesSkills.table.medianSalary', numeric: true, format: 'salary' },
];

/** Table cell text: N/D for skills without a median, localised elsewhere. */
function formatBenchmarkValue(row, column) {
  const value = row[column.key];
  if (value == null) return t('view.rolesSkills.notAvailable');
  if (column.format === 'count') return formatCount(value, state.language);
  if (column.format === 'percent') return formatPercent(value, state.language);
  if (column.format === 'salary') return formatSalary(value, state.language);
  return String(value);
}

/** Benchmark table with a localised caption and `th scope="col"` headers. */
function buildBenchmarkTable(rows) {
  const table = document.createElement('table');
  table.className = 'benchmark-table';

  const caption = document.createElement('caption');
  caption.className = 'benchmark-table__caption';
  caption.textContent = t('view.rolesSkills.benchmark.caption');
  table.append(caption);

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const column of BENCHMARK_COLUMNS) {
    const th = document.createElement('th');
    th.setAttribute('scope', 'col');
    if (column.numeric) th.className = 'benchmark-table__number';
    th.textContent = t(column.labelKey);
    headRow.append(th);
  }
  head.append(headRow);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const column of BENCHMARK_COLUMNS) {
      const td = document.createElement('td');
      if (column.numeric) td.className = 'benchmark-table__number';
      td.textContent = formatBenchmarkValue(row, column);
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(head, tbody);
  return table;
}

/**
 * Full-width benchmark card, the primary element of the view. Every catalogue
 * skill with demand is rendered (46 rows in the T-06 snapshot; the catalogue
 * lists 95): no pagination, a 24rem scroll container and sticky headers keep
 * long lists usable. The scroll region is keyboard reachable (role+tabindex).
 */
function appendBenchmarkCard(grid, rows) {
  const figure = document.createElement('figure');
  figure.className = 'chart-card chart-card--wide chart-card--table';

  const title = document.createElement('h3');
  title.className = 'chart-card__title';
  title.textContent = t('view.rolesSkills.benchmark.title');
  figure.append(title);

  if (rows.length === 0) {
    figure.append(noteElement('view.rolesSkills.noData', 'chart-empty'));
    grid.append(figure);
    return;
  }

  const scroll = document.createElement('div');
  scroll.className = 'table-scroll';
  scroll.tabIndex = 0;
  scroll.setAttribute('role', 'region');
  scroll.setAttribute('aria-label', t('view.rolesSkills.benchmark.title'));
  scroll.append(buildBenchmarkTable(rows));
  figure.append(scroll);
  grid.append(figure);
}

/**
 * Data of the view with the current global filters. `skillShare` runs with
 * `includeSkill: false` because its denominator excludes the skill filter by
 * design (Metrics.tmdl L179-181); an active skill filter only restricts the
 * displayed rows, never the share. Skills without a valid salary offer are
 * absent from `skillMedianSalary`; the caller fills those cells with N/D.
 * `medianSalaryBySkillCategory` is a direct aggregation whose rows may carry a
 * NULL median when a category has no valid salary (dropped by the chart).
 */
async function fetchRolesSkillsData() {
  const [shareRows, medianRows, categoryRows, medianByCategory] = await Promise.all([
    runQuery(queries.skillShare(buildQueryOptions(state.filters, { includeSkill: false }))),
    runQuery(queries.skillMedianSalary(buildQueryOptions(state.filters))),
    runQuery(queries.skillDemandByCategory(buildQueryOptions(state.filters))),
    runQuery(queries.medianSalaryBySkillCategory(buildQueryOptions(state.filters))),
  ]);
  const skillFilter = isBlank(state.filters.skill) ? null : state.filters.skill;
  const visibleShares = skillFilter
    ? shareRows.filter((row) => row.skill === skillFilter)
    : shareRows;
  const medianBySkill = new Map(medianRows.map((row) => [row.skill, row.skill_median_salary_eur]));
  const benchmark = visibleShares.map((row) => ({
    skill: row.skill,
    category: row.skill_category,
    demand: Number(row.skill_demand) || 0,
    share: row.skill_share == null ? null : Number(row.skill_share),
    median: medianBySkill.has(row.skill) ? Number(medianBySkill.get(row.skill)) : null,
  }));
  return { benchmark, categoryRows, medianByCategory };
}

/** Demand (x) vs. median salary (y) bubble; only skills with a median plot. */
function buildBubbleOption(items) {
  return bubbleChart({
    items,
    xName: t('view.rolesSkills.bubble.xAxis'),
    yName: t('view.rolesSkills.bubble.yAxis'),
    formatX: formatCountCallback,
    formatY: formatSalaryCallback,
    formatTooltip: (item) => t('view.rolesSkills.bubble.tooltip', {
      skill: item.name,
      category: item.category,
      demand: formatCount(item.size, state.language),
      salary: formatSalary(item.value[1], state.language),
    }),
  });
}

function buildSkillCategoryOption(rows) {
  return donutChart({
    items: rows.map((row) => ({
      name: row.skill_category,
      value: Number(row.skill_demand) || 0,
    })),
    seriesName: t('view.rolesSkills.category.title'),
    formatValue: formatCountCallback,
    formatPercent: formatPercentCallback,
  });
}

/**
 * Horizontal bars of the median salary per skill category (T-24). Rows whose
 * category has no valid salary (NULL median) are dropped by the caller: a zero
 * bar would read as a real salary. Values use the compact salary format
 * (`101 mil €` / `€101K`) because ECharts shares one value formatter between
 * the value axis and the tooltip, and full amounts would crowd the axis.
 */
function buildSkillCategoryMedianOption(rows) {
  return barChart({
    categories: rows.map((row) => row.skill_category),
    values: rows.map((row) => Number(row.median_mid_salary_eur) || 0),
    seriesName: t('view.rolesSkills.medianByCategory.axis'),
    horizontal: true,
    formatValue: formatCompactSalaryCallback,
  });
}

/**
 * Roles & Skills renderer (RF-7): benchmark table of demand/share/median per
 * skill, demand-vs-salary bubble (full width, it is the densest chart),
 * category donut and median salary by skill category. The table is plain DOM,
 * so it stays visible even when ECharts is unavailable; the charts are mounted
 * only after the cards are attached to the document.
 */
async function renderRolesSkills({ requestId, body }) {
  const { benchmark, categoryRows, medianByCategory } = await fetchRolesSkillsData();
  if (requestId !== viewRenderId) return;

  const content = document.createDocumentFragment();
  if (!state.echarts) {
    content.append(noteElement('view.rolesSkills.chartsUnavailable', 'chart-unavailable'));
  }

  const grid = document.createElement('div');
  grid.className = 'chart-grid';
  const mounts = [];
  if (state.echarts) {
    const bubbleItems = benchmark
      .filter((row) => row.median != null)
      .map((row) => ({
        name: row.skill,
        category: row.category,
        x: row.demand,
        y: row.median,
        size: row.demand,
      }));
    mounts.push(appendChartCard(grid, {
      titleKey: 'view.rolesSkills.bubble.title',
      captionKey: 'view.rolesSkills.bubble.caption',
      wide: true,
      noDataKey: 'view.rolesSkills.noData',
      option: bubbleItems.length > 0 ? buildBubbleOption(bubbleItems) : null,
    }));
    mounts.push(appendChartCard(grid, {
      titleKey: 'view.rolesSkills.category.title',
      captionKey: 'view.rolesSkills.category.caption',
      noDataKey: 'view.rolesSkills.noData',
      option: categoryRows.length > 0 ? buildSkillCategoryOption(categoryRows) : null,
    }));
    const medianItems = medianByCategory.filter((row) => row.median_mid_salary_eur != null);
    mounts.push(appendChartCard(grid, {
      titleKey: 'view.rolesSkills.medianByCategory.title',
      captionKey: 'view.rolesSkills.medianByCategory.caption',
      noDataKey: 'view.rolesSkills.noData',
      option: medianItems.length > 0 ? buildSkillCategoryMedianOption(medianItems) : null,
    }));
  }
  appendBenchmarkCard(grid, benchmark);
  content.append(grid);
  body.replaceChildren(content);
  for (const mount of mounts.filter(Boolean)) mount();
}

/* ========================================================================== *
 * Salary Insights (T-12/T-25, RF-7/RF-8/RF-16): KPIs 15-18, averages by role
 * and work mode, top paying skills and the country/region choropleth
 * ========================================================================== */

/** Salary Insights KPI cards mapped to measures 15-18 of §7 of the plan. */
const SALARY_INSIGHTS_KPIS = [
  { id: 'avg', labelKey: 'kpi.avgMidSalary', builder: 'avgMidSalary', alias: 'avg_mid_salary_eur', format: 'salary' },
  { id: 'median', labelKey: 'kpi.medianMidSalary', builder: 'medianMidSalary', alias: 'median_mid_salary_eur', format: 'salary' },
  { id: 'p25', labelKey: 'kpi.p25MidSalary', builder: 'p25MidSalary', alias: 'p25_mid_salary_eur', format: 'salary' },
  { id: 'p75', labelKey: 'kpi.p75MidSalary', builder: 'p75MidSalary', alias: 'p75_mid_salary_eur', format: 'salary' },
];

/**
 * Country selector of the map, mirroring the report slicer
 * (docs/images/powerbi_salary_insights.png) in its order: Europe keeps the
 * continental map and clears the global country filter; every other button
 * applies its `GeoCountry` through the global filters and switches the map to
 * the regions of that country (T-25).
 */
const MAP_COUNTRIES = [
  { id: 'europe', labelKey: 'view.salaryInsights.map.country.europe', country: '' },
  { id: 'ireland', labelKey: 'view.salaryInsights.map.country.ireland', country: 'Ireland' },
  { id: 'netherlands', labelKey: 'view.salaryInsights.map.country.netherlands', country: 'Netherlands' },
  { id: 'spain', labelKey: 'view.salaryInsights.map.country.spain', country: 'Spain' },
  { id: 'switzerland', labelKey: 'view.salaryInsights.map.country.switzerland', country: 'Switzerland' },
  { id: 'united-states', labelKey: 'view.salaryInsights.map.country.unitedStates', country: 'United States' },
];

/**
 * Versioned GeoJSON maps (Natural Earth, public domain; see
 * docs/dashboard/README.md): the Europe map registers once as `tml-europe`
 * and `regions.geojson` provides one map per selector country
 * (`tml-spain`, …). Region names are the canonical `GeoRegion` values the
 * export produces, which is what `offersByCountry`/`offersByRegion` return.
 */
const EUROPE_GEOJSON_URL = './assets/europe.geojson';
const REGIONS_GEOJSON_URL = './assets/regions.geojson';
const EUROPE_MAP_NAME = 'tml-europe';

/**
 * Features excluded from the automatic fit so the country fills the card:
 * Alaska, Hawaii and Puerto Rico would stretch the United States box across
 * the Pacific. They are still registered and drawn (clipped outside the
 * visible box, `roam` is off), and their data is simply not reachable on the
 * fitted map; documented in the T-25 report and the dashboard README.
 */
const MAP_FIT_EXCLUSIONS = {
  'united-states': new Set(['Alaska', 'Hawaii', 'Puerto Rico']),
};

let europeGeojsonPromise = null;
let europeMapNames = null;
let regionsGeojsonPromise = null;
let countryMaps = null;

/**
 * Loads (once) and registers the Europe GeoJSON, resolving to the set of
 * region names the map can draw, or null when the fetch fails. A failure is
 * reported inside the map card and the rest of the view keeps rendering.
 */
async function prepareEuropeMap() {
  if (!state.echarts) return null;
  if (europeMapNames) return europeMapNames;
  if (!europeGeojsonPromise) europeGeojsonPromise = fetchJson(EUROPE_GEOJSON_URL);
  try {
    const geojson = await europeGeojsonPromise;
    state.echarts.registerMap(EUROPE_MAP_NAME, geojson);
    europeMapNames = new Set(
      (geojson.features || [])
        .map((feature) => feature.properties && feature.properties.name)
        .filter(Boolean),
    );
    return europeMapNames;
  } catch (error) {
    europeGeojsonPromise = null; // allow a retry on the next render
    console.warn('[tml] could not load the Europe GeoJSON:', error);
    return null;
  }
}

/**
 * Bounding box of the features that must be visible, in ECharts order
 * (`[[minLng, maxLat], [maxLng, minLat]]`), with a small padding so the
 * country does not touch the card borders. Features listed in `excludedNames`
 * (detached territories) do not take part in the fit.
 */
function fitBoundingCoords(features, excludedNames = null) {
  let minLng = Infinity;
  let maxLng = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  const visit = (coords) => {
    if (typeof coords[0] === 'number') {
      const [lng, lat] = coords;
      if (lng < minLng) minLng = lng;
      if (lng > maxLng) maxLng = lng;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      return;
    }
    for (const child of coords) visit(child);
  };
  for (const feature of features) {
    if (excludedNames && feature.properties && excludedNames.has(feature.properties.name)) continue;
    if (feature.geometry && feature.geometry.coordinates) visit(feature.geometry.coordinates);
  }
  if (!Number.isFinite(minLng)) return null;
  const padLng = Math.max((maxLng - minLng) * 0.03, 0.02);
  const padLat = Math.max((maxLat - minLat) * 0.03, 0.02);
  return [[minLng - padLng, maxLat + padLat], [maxLng + padLng, minLat - padLat]];
}

/**
 * Loads (once) `regions.geojson` and registers one ECharts map per selector
 * country with only that country's features. Resolves to a `Map` of
 * `id → { mapName, names, boundingCoords }` or null when the fetch fails; the
 * map card then shows its localised "unavailable" note and the rest of the
 * view keeps working.
 */
async function prepareCountryMaps() {
  if (!state.echarts) return null;
  if (countryMaps) return countryMaps;
  if (!regionsGeojsonPromise) regionsGeojsonPromise = fetchJson(REGIONS_GEOJSON_URL);
  try {
    const geojson = await regionsGeojsonPromise;
    const maps = new Map();
    for (const entry of MAP_COUNTRIES) {
      if (!entry.country) continue;
      const features = (geojson.features || []).filter(
        (feature) => feature.properties && feature.properties.country === entry.country,
      );
      if (features.length === 0) continue;
      const mapName = `tml-${entry.id}`;
      state.echarts.registerMap(mapName, { type: 'FeatureCollection', features });
      maps.set(entry.id, {
        mapName,
        names: new Set(features.map((feature) => feature.properties.name)),
        boundingCoords: fitBoundingCoords(features, MAP_FIT_EXCLUSIONS[entry.id]),
      });
    }
    countryMaps = maps;
    return countryMaps;
  } catch (error) {
    regionsGeojsonPromise = null; // allow a retry on the next render
    console.warn('[tml] could not load the regions GeoJSON:', error);
    return null;
  }
}

/** Country of the selector matching `state.filters.country`, or null (Europe). */
function activeMapCountry() {
  return MAP_COUNTRIES.find(
    (entry) => entry.country && entry.country === state.filters.country,
  ) || null;
}

/**
 * Country selector chips above the map (T-25). The active button derives from
 * `state.filters.country` (Europe when no selector country is filtered), so
 * the chips and the filter-panel select stay synchronised; a click applies the
 * global country filter and the active view re-renders with the country map
 * fitted. `aria-pressed` exposes the active state without relying on colour.
 */
function buildMapSelector(activeId) {
  const wrapper = document.createElement('div');
  wrapper.className = 'map-selector';
  wrapper.setAttribute('role', 'group');
  wrapper.setAttribute('aria-label', t('view.salaryInsights.map.selector.label'));

  for (const entry of MAP_COUNTRIES) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'map-selector__button';
    button.setAttribute('aria-pressed', entry.id === activeId ? 'true' : 'false');
    button.textContent = t(entry.labelKey);
    button.addEventListener('click', () => {
      // Re-clicking the active country (or Europe with no country filter) is a
      // no-op; Europe with any other country filter clears it.
      if (entry.id === activeId && entry.id !== 'europe') return;
      if (entry.id === 'europe' && isBlank(state.filters.country)) return;
      setFilters({ country: entry.country });
    });
    wrapper.append(button);
  }

  const hint = document.createElement('p');
  hint.className = 'map-selector__hint';
  hint.textContent = t('view.salaryInsights.map.selector.hint');
  wrapper.append(hint);
  return wrapper;
}

/**
 * Small localised table for the Salary Insights cards (top paying skills and
 * countries outside the map). Cells arrive already formatted; `numeric`
 * right-aligns them and every header/caption key comes from i18n.js.
 */
function buildInsightsTable({ columns, rows, captionKey }) {
  const table = document.createElement('table');
  table.className = 'benchmark-table benchmark-table--compact';

  const caption = document.createElement('caption');
  caption.className = 'benchmark-table__caption';
  caption.textContent = t(captionKey);
  table.append(caption);

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const column of columns) {
    const th = document.createElement('th');
    th.setAttribute('scope', 'col');
    if (column.numeric) th.className = 'benchmark-table__number';
    th.textContent = t(column.labelKey);
    headRow.append(th);
  }
  head.append(headRow);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    for (const column of columns) {
      const td = document.createElement('td');
      if (column.numeric) td.className = 'benchmark-table__number';
      const value = row[column.key];
      td.textContent = value == null ? t('view.salaryInsights.notAvailable') : value;
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(head, tbody);
  return table;
}

const TOP_SKILLS_COLUMNS = [
  { key: 'skill', labelKey: 'view.salaryInsights.topSkills.skill' },
  { key: 'category', labelKey: 'view.salaryInsights.topSkills.category' },
  { key: 'median', labelKey: 'view.salaryInsights.topSkills.medianSalary', numeric: true },
  { key: 'demand', labelKey: 'view.salaryInsights.topSkills.demand', numeric: true },
];

/**
 * Scrollable top-paying-skills card; plain DOM, so it survives without ECharts.
 * `fill` is set when the card shares its row with the (taller) map card: the
 * scroll region then grows with the row height so the card is never stretched
 * with an empty gap below the table (T-29). Standalone (the degraded path
 * without ECharts) the card keeps its compact fixed height.
 */
function appendTopSkillsCard(grid, rows, { fill = false } = {}) {
  const figure = document.createElement('figure');
  figure.className = `chart-card chart-card--table${fill ? ' chart-card--fill' : ''}`;

  const title = document.createElement('h3');
  title.className = 'chart-card__title';
  title.textContent = t('view.salaryInsights.topSkills.title');
  figure.append(title);

  if (rows.length === 0) {
    figure.append(noteElement('view.salaryInsights.noData', 'chart-empty'));
    grid.append(figure);
    return;
  }

  const scroll = document.createElement('div');
  scroll.className = 'table-scroll';
  scroll.tabIndex = 0;
  scroll.setAttribute('role', 'region');
  scroll.setAttribute('aria-label', t('view.salaryInsights.topSkills.title'));
  scroll.append(buildInsightsTable({
    columns: TOP_SKILLS_COLUMNS,
    rows: rows.map((row) => ({
      skill: row.skill,
      category: row.skill_category,
      median: formatSalary(row.skill_median_salary_eur, state.language),
      demand: formatCount(row.skill_demand, state.language),
    })),
    captionKey: 'view.salaryInsights.topSkills.caption',
  }));
  figure.append(scroll);
  grid.append(figure);
}

/**
 * Choropleth card (T-25): the country selector chips sit above the chart. In
 * Europe mode the map colours countries by `offersByCountry` and lists the
 * labels without geometry below the map; in country mode it draws only that
 * country's regions (registered `regions.geojson` features) coloured by
 * `offersByRegion`, fitted to the country box through `boundingCoords`.
 * Regions/countries without a row keep the light grey "no data" fill and
 * tooltip.
 */
function appendMapCard(grid, { selected, countryMaps: maps, europeNames, rows, mounts }) {
  const countryMode = selected != null;
  const countryInfo = countryMode && maps ? maps.get(selected.id) : null;
  const mapName = countryMode ? (countryInfo ? countryInfo.mapName : null) : EUROPE_MAP_NAME;
  const regionNames = countryMode ? (countryInfo ? countryInfo.names : null) : europeNames;
  const countryName = countryMode ? t(selected.labelKey) : '';
  const titleKey = countryMode
    ? 'view.salaryInsights.map.regionsTitle'
    : 'view.salaryInsights.map.title';
  const captionKey = countryMode
    ? 'view.salaryInsights.map.regionsCaption'
    : 'view.salaryInsights.map.caption';

  const figure = document.createElement('figure');
  figure.className = 'chart-card chart-card--map';

  const title = document.createElement('h3');
  title.className = 'chart-card__title';
  title.textContent = t(titleKey, { country: countryName });
  figure.append(title);

  figure.append(buildMapSelector(countryMode ? selected.id : 'europe'));

  const body = document.createElement('div');
  body.className = 'chart-card__body';
  body.setAttribute('role', 'img');
  body.setAttribute('aria-label', `${title.textContent}. ${t(captionKey, { country: countryName })}`);
  figure.append(body);

  const caption = document.createElement('figcaption');
  caption.className = 'chart-card__caption';
  caption.textContent = t(captionKey, { country: countryName });
  figure.append(caption);

  if (!mapName || !regionNames) {
    body.replaceWith(noteElement('view.salaryInsights.mapUnavailable', 'chart-empty'));
    grid.append(figure);
    return;
  }

  const mapped = rows.filter((row) => regionNames.has(row.name));
  const outside = rows.filter((row) => !regionNames.has(row.name));
  if (rows.length === 0) {
    body.replaceWith(noteElement('view.salaryInsights.noData', 'chart-empty'));
  } else {
    const option = choroplethOption({
      mapName,
      rows: mapped,
      boundingCoords: countryInfo ? countryInfo.boundingCoords : null,
      seriesName: title.textContent,
      formatValue: formatCountCallback,
      formatTooltip: (name, value) => (countryMode
        ? t('view.salaryInsights.map.regionTooltip', {
            region: name,
            offers: formatCount(value, state.language),
          })
        : t('view.salaryInsights.map.tooltip', {
            country: name,
            offers: formatCount(value, state.language),
          })),
      formatNoData: (name) => t('view.salaryInsights.map.noDataTooltip', { country: name }),
      moreLabel: t('view.salaryInsights.map.more'),
      lessLabel: t('view.salaryInsights.map.less'),
    });
    mounts.push(() => mountChart(body, option));
  }

  // Labels without geometry only exist in Europe mode (e.g. "United States",
  // "Europe", "(Not specified)"); in country mode `(Country)`/`(Other)` are
  // aggregates of unmapped offers and are not listed (T-25).
  if (!countryMode && outside.length > 0) {
    const wrapper = document.createElement('div');
    wrapper.className = 'map-outside';
    wrapper.append(buildInsightsTable({
      columns: [
        { key: 'country', labelKey: 'view.salaryInsights.map.outsideCountry' },
        { key: 'offers', labelKey: 'view.salaryInsights.map.outsideOffers', numeric: true },
      ],
      rows: outside.map((row) => ({
        country: row.name,
        offers: formatCount(row.value, state.language),
      })),
      captionKey: 'view.salaryInsights.map.outsideCaption',
    }));
    figure.append(wrapper);
  }
  grid.append(figure);
}

/**
 * Data of the view with the current global filters (RF-9). All builders come
 * from queries.js; this function only orchestrates calls and normalises the
 * map rows to `{ name, value }` — the region or country drawn on the map and
 * its offer count.
 */
async function fetchSalaryInsightsData(options, selectedCountry) {
  const [kpis, byRole, byWorkMode, topSkills, mapRows] = await Promise.all([
    Promise.all(SALARY_INSIGHTS_KPIS.map(async (kpi) => {
      const rows = await runQuery(queries[kpi.builder](options));
      return { ...kpi, value: rows.length > 0 ? rows[0][kpi.alias] : null };
    })),
    runQuery(queries.avgMidSalaryByRole(options)),
    runQuery(queries.avgMidSalaryByWorkMode(options)),
    runQuery(queries.topPayingSkills(options)),
    selectedCountry
      ? runQuery(queries.offersByRegion(options))
      : runQuery(queries.offersByCountry(options)),
  ]);
  return {
    kpis,
    byRole,
    byWorkMode,
    topSkills,
    mapRows: mapRows.map((row) => ({
      name: selectedCountry ? row.region : row.country,
      value: row.job_offers,
    })),
  };
}

/** Horizontal bars of an average salary per category (role, work mode). */
function buildAverageSalaryOption(rows, nameField, seriesNameKey) {
  return barChart({
    categories: rows.map((row) => row[nameField]),
    values: rows.map((row) => Number(row.avg_mid_salary_eur) || 0),
    seriesName: t(seriesNameKey),
    horizontal: true,
    formatValue: formatSalaryCallback,
  });
}

/**
 * Salary Insights renderer (RF-7): KPI cards 15-18, average salary by role
 * category and work mode, top paying skills table and the choropleth. The map
 * mode derives from `state.filters.country` (T-25): the five selector
 * countries draw their regions fitted to the card, anything else draws the
 * European continent by country. GeoJSON failures only replace the map with a
 * localised note; the table is plain DOM and survives without ECharts.
 */
async function renderSalaryInsights({ requestId, body }) {
  const options = buildQueryOptions(state.filters);
  const selected = activeMapCountry();
  const [data, europeNames, maps] = await Promise.all([
    fetchSalaryInsightsData(options, selected),
    state.echarts ? prepareEuropeMap() : Promise.resolve(null),
    state.echarts ? prepareCountryMaps() : Promise.resolve(null),
  ]);
  if (requestId !== viewRenderId) return;

  const content = document.createDocumentFragment();
  content.append(renderKpiList(data.kpis));

  const grid = document.createElement('div');
  grid.className = 'chart-grid';
  const mounts = [];

  if (!state.echarts) {
    content.append(noteElement('view.salaryInsights.chartsUnavailable', 'chart-unavailable'));
    appendTopSkillsCard(grid, data.topSkills);
  } else {
    mounts.push(appendChartCard(grid, {
      titleKey: 'view.salaryInsights.avgByRole.title',
      captionKey: 'view.salaryInsights.avgByRole.caption',
      noDataKey: 'view.salaryInsights.noData',
      option: data.byRole.length > 0
        ? buildAverageSalaryOption(data.byRole, 'role_category', 'view.salaryInsights.avgByRole.title')
        : null,
    }));
    mounts.push(appendChartCard(grid, {
      titleKey: 'view.salaryInsights.avgByWorkMode.title',
      captionKey: 'view.salaryInsights.avgByWorkMode.caption',
      noDataKey: 'view.salaryInsights.noData',
      option: data.byWorkMode.length > 0
        ? buildAverageSalaryOption(data.byWorkMode, 'work_mode', 'view.salaryInsights.avgByWorkMode.title')
        : null,
    }));
    appendTopSkillsCard(grid, data.topSkills, { fill: true });
    appendMapCard(grid, {
      selected,
      countryMaps: maps,
      europeNames,
      rows: data.mapRows,
      mounts,
    });
  }

  content.append(grid);
  body.replaceChildren(content);
  for (const mount of mounts.filter(Boolean)) mount();
}

/* ========================================================================== *
 * Opportunity Explorer (T-13, RF-7/RF-10/RF-16): searchable, paginated table
 * of individual offers with public fields only
 * ========================================================================== */

/** Fixed page size (RF-10 volume limit: one page is ever rendered). */
const EXPLORER_PAGE_SIZE = 25;

/**
 * Local view state: the global filters live in `state.filters`, while the
 * explorer keeps its own search term and page so they survive re-renders. A
 * global filter change resets the page (see `applyFilterChange`) but never the
 * term; a language change keeps both.
 */
const explorerState = { search: '', page: 1 };

/** Table columns, matching the public projection of `queries.explorerOffers`. */
const EXPLORER_COLUMNS = [
  { key: 'title', labelKey: 'view.opportunityExplorer.table.jobTitle' },
  { key: 'company', labelKey: 'view.opportunityExplorer.table.company' },
  { key: 'location', labelKey: 'view.opportunityExplorer.table.location' },
  { key: 'workMode', labelKey: 'view.opportunityExplorer.table.workMode' },
  { key: 'role', labelKey: 'view.opportunityExplorer.table.role' },
  { key: 'salary', labelKey: 'view.opportunityExplorer.table.medianSalary', numeric: true },
  { key: 'date', labelKey: 'view.opportunityExplorer.table.postedDate' },
  { key: 'source', labelKey: 'view.opportunityExplorer.table.source' },
];

/** Trimmed text or null; drives the localised N/D fallback of every cell. */
const textOrNull = (value) => {
  if (value == null) return null;
  const text = String(value).trim();
  return text === '' ? null : text;
};

/** Only http(s) URLs become links; anything else stays plain text. */
const safeJobUrl = (value) => {
  const raw = typeof value === 'string' ? value.trim() : '';
  return /^https?:\/\//i.test(raw) ? raw : null;
};

/**
 * Maps a raw DuckDB row to display fields. A missing salary (null, blank or
 * ≤ 0) is not a disclosed salary and becomes null → localised N/D. The full
 * measure quality rule (≤ €1,000,000 and min ≤ max, RF-8) belongs to the
 * aggregate salary measures; this per-offer public field (RF-10) shows the
 * exported mid value as-is because `queries.explorerOffers` projects only
 * `SalaryMidAnnual_EUR`, so the min/max part of the rule cannot be checked
 * without changing the SQL (out of scope).
 */
function explorerRecord(row) {
  const salary = Number(row.SalaryMidAnnual_EUR);
  return {
    title: textOrNull(row.title),
    url: safeJobUrl(row.job_url),
    company: textOrNull(row.company_name),
    location: [row.location_city, row.location_country]
      .map(textOrNull)
      .filter(Boolean)
      .join(' · ') || null,
    workMode: textOrNull(row.WorkModeBucket),
    role: textOrNull(row.role_category),
    salary: Number.isFinite(salary) && salary > 0 ? salary : null,
    date: row.posted_date ?? null,
    source: textOrNull(row.source_scraper),
  };
}

/** Localised cell text (N/D when the offer has no value for the column). */
function explorerCellText(record, key) {
  const value = record[key];
  if (value == null) return t('view.opportunityExplorer.notAvailable');
  if (key === 'salary') return formatSalary(value, state.language);
  if (key === 'date') return formatDate(value, state.language);
  return String(value);
}

/** Title cell: a real link when the offer has an http(s) URL, text otherwise. */
function appendExplorerTitle(cell, record) {
  const label = record.title ?? t('view.opportunityExplorer.notAvailable');
  if (!record.url) {
    cell.textContent = label;
    return;
  }
  const link = document.createElement('a');
  link.href = record.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = label;
  link.setAttribute('aria-label', t('view.opportunityExplorer.openLink', { title: label }));
  cell.append(link);
}

/** Explorer table: localised caption, `th scope="col"` and one row per offer. */
function buildExplorerTable(rows) {
  const table = document.createElement('table');
  table.className = 'benchmark-table explorer-table';

  const caption = document.createElement('caption');
  caption.className = 'benchmark-table__caption';
  caption.textContent = t('view.opportunityExplorer.table.caption');
  table.append(caption);

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const column of EXPLORER_COLUMNS) {
    const th = document.createElement('th');
    th.setAttribute('scope', 'col');
    if (column.numeric) th.className = 'benchmark-table__number';
    th.textContent = t(column.labelKey);
    headRow.append(th);
  }
  head.append(headRow);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const record = explorerRecord(row);
    const tr = document.createElement('tr');
    for (const column of EXPLORER_COLUMNS) {
      const td = document.createElement('td');
      if (column.numeric) td.className = 'benchmark-table__number';
      if (column.key === 'title') appendExplorerTitle(td, record);
      else td.textContent = explorerCellText(record, column.key);
      tr.append(td);
    }
    tbody.append(tr);
  }
  table.append(head, tbody);
  return table;
}

/**
 * Previous/next controls and the page indicator. Both buttons are disabled at
 * their bound; a click delegates to `onNavigate(page)`, which re-renders only
 * the results region so the search box keeps working.
 */
function buildExplorerPagination({ page, pageCount, onNavigate }) {
  const nav = document.createElement('nav');
  nav.className = 'explorer__pagination';
  nav.setAttribute('aria-label', t('view.opportunityExplorer.pagination.label'));

  const previous = document.createElement('button');
  previous.type = 'button';
  previous.className = 'explorer__page-button';
  previous.textContent = t('view.opportunityExplorer.pagination.previous');
  previous.disabled = page <= 1;
  previous.addEventListener('click', () => onNavigate(page - 1));

  const label = document.createElement('p');
  label.className = 'explorer__page-label';
  label.textContent = t('view.opportunityExplorer.pagination.page', { page, pages: pageCount });

  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'explorer__page-button';
  next.textContent = t('view.opportunityExplorer.pagination.next');
  next.disabled = page >= pageCount;
  next.addEventListener('click', () => onNavigate(page + 1));

  nav.append(previous, label, next);
  return nav;
}

/**
 * Counter (aria-live polite, with the active term) plus the results region:
 * the table inside a horizontal scroll area and the page controls. Only the
 * current page is in the DOM; `total` comes from `explorerOffersCount` under
 * the same context as the rows.
 */
function renderExplorerResults({ status, results, data, onNavigate }) {
  const { rows, total, page, pageCount } = data;
  const term = explorerState.search;
  const from = total === 0 ? 0 : (page - 1) * EXPLORER_PAGE_SIZE + 1;
  const to = total === 0 ? 0 : from + rows.length - 1;

  const parts = [];
  if (term) parts.push(t('view.opportunityExplorer.search.active', { term }));
  parts.push(total === 0
    ? t('view.opportunityExplorer.noData')
    : t('view.opportunityExplorer.count', {
        from: formatCount(from, state.language),
        to: formatCount(to, state.language),
        total: formatCount(total, state.language),
      }));
  status.textContent = parts.join(' · ');

  if (rows.length === 0) {
    results.replaceChildren(noteElement('view.opportunityExplorer.noData', 'explorer__empty'));
    return;
  }

  const scroll = document.createElement('div');
  scroll.className = 'explorer__table-scroll';
  scroll.tabIndex = 0;
  scroll.setAttribute('role', 'region');
  scroll.setAttribute('aria-label', t('nav.opportunityExplorer'));
  scroll.append(buildExplorerTable(rows));

  results.replaceChildren(scroll, buildExplorerPagination({ page, pageCount, onNavigate }));
}

/**
 * One page of the explorer: the count first (to clamp the requested page
 * against the current context), then the page rows. Both queries share
 * `buildQueryOptions` (global filters) plus the local search term.
 */
async function fetchExplorerPage() {
  const options = buildQueryOptions(state.filters);
  const countRows = await runQuery(queries.explorerOffersCount({
    ...options,
    search: explorerState.search,
  }));
  const total = countRows.length > 0 ? Number(countRows[0].total_offers) || 0 : 0;
  const pageCount = Math.max(1, Math.ceil(total / EXPLORER_PAGE_SIZE));
  const page = Math.min(Math.max(1, explorerState.page), pageCount);
  explorerState.page = page;
  const rows = total === 0 ? [] : await runQuery(queries.explorerOffers({
    ...options,
    search: explorerState.search,
    limit: EXPLORER_PAGE_SIZE,
    offset: (page - 1) * EXPLORER_PAGE_SIZE,
  }));
  return { rows, total, page, pageCount };
}

/**
 * Opportunity Explorer renderer (RF-10): local search box (debounced 250 ms,
 * title/company), one paginated table of the public fields and a visible
 * counter. The global filters apply to table and counter through
 * `buildQueryOptions`. A search keystroke re-renders only the results region,
 * so the input keeps focus; request tokens discard stale async renders and a
 * navigation away abandons the pending search.
 */
async function renderOpportunityExplorer({ requestId, body }) {
  const searchId = 'explorer-search';

  const searchBox = document.createElement('div');
  searchBox.className = 'explorer-search';

  const label = document.createElement('label');
  label.className = 'explorer-search__label';
  label.setAttribute('for', searchId);
  label.textContent = t('view.opportunityExplorer.search.label');

  const input = document.createElement('input');
  input.type = 'search';
  input.id = searchId;
  input.className = 'explorer-search__input';
  input.value = explorerState.search;
  input.placeholder = t('view.opportunityExplorer.search.placeholder');
  input.autocomplete = 'off';

  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'explorer-search__clear';
  clear.textContent = t('view.opportunityExplorer.search.clear');
  clear.disabled = explorerState.search === '';

  searchBox.append(label, input, clear);

  const status = document.createElement('p');
  status.className = 'explorer__status';
  status.setAttribute('aria-live', 'polite');
  status.textContent = t('view.opportunityExplorer.loading');

  const results = document.createElement('div');
  results.className = 'explorer__results';
  results.tabIndex = -1;

  body.append(searchBox, status, results);

  let refreshId = 0;

  async function refresh({ focusResults = false } = {}) {
    const id = ++refreshId;
    try {
      const data = await fetchExplorerPage();
      if (requestId !== viewRenderId || id !== refreshId) return;
      renderExplorerResults({ status, results, data, onNavigate });
      if (focusResults) results.focus();
    } catch (error) {
      if (requestId !== viewRenderId || id !== refreshId) return;
      console.error('[tml] opportunity explorer query failed:', error);
      status.textContent = '';
      results.replaceChildren(noteElement('view.error', 'view__error'));
    }
  }

  async function onNavigate(targetPage) {
    explorerState.page = targetPage;
    await refresh({ focusResults: true });
  }

  let searchTimer = null;
  input.addEventListener('input', () => {
    clear.disabled = input.value.trim() === '';
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      if (requestId !== viewRenderId) return;
      explorerState.search = input.value.trim();
      explorerState.page = 1;
      refresh();
    }, FILTER_INPUT_DEBOUNCE_MS);
  });

  clear.addEventListener('click', () => {
    clearTimeout(searchTimer);
    input.value = '';
    clear.disabled = true;
    explorerState.search = '';
    explorerState.page = 1;
    input.focus();
    refresh();
  });

  await refresh();
}

/* ========================================================================== *
 * Methodology (T-14, RF-7/RF-13/RF-16): sources, pipeline trace, salary
 * normalisation, date provenance, freshness from meta.json, known limitations
 * and legal notice. Plain text and meta.json values only: no SQL, no charts
 * and no dependency on the data layer.
 * ========================================================================== */

/**
 * `[source, count]` pairs of `meta.sources`, descending by count (name as
 * tie-breaker). Non-finite counts are dropped so the view never shows an
 * invented figure (RF-14).
 */
function sortedSourceCounts(sources) {
  return Object.entries(sources || {})
    .map(([name, count]) => [name, Number(count)])
    .filter(([, count]) => Number.isFinite(count))
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
}

/**
 * `[name, count]` pairs of `meta.tables` with the four contract tables first
 * (the order of the export contract) and any extra table after them.
 */
function orderedTableCounts(tables) {
  const values = tables || {};
  const extra = Object.keys(values).filter((name) => !DATA_FILES.includes(name)).sort();
  return [...DATA_FILES, ...extra]
    .filter((name) => name in values)
    .map((name) => [name, Number(values[name])])
    .filter(([, count]) => Number.isFinite(count));
}

/** Section of the view with a localised `h3` title and a unique anchor id. */
function appendMethodologySection(body, id, titleKey) {
  const section = document.createElement('section');
  section.className = 'methodology__section';
  section.setAttribute('aria-labelledby', id);

  const title = document.createElement('h3');
  title.className = 'methodology__title';
  title.id = id;
  title.textContent = t(titleKey);

  section.append(title);
  body.append(section);
  return section;
}

/** Localised paragraph (same helper the views use for notes). */
function appendParagraph(parent, key, className = 'methodology__lead') {
  parent.append(noteElement(key, className));
}

/** List where every item is one i18n key; ordered for step-by-step traces. */
function appendKeyList(parent, keys, { className = 'methodology__points', ordered = false } = {}) {
  const list = document.createElement(ordered ? 'ol' : 'ul');
  list.className = className;
  for (const key of keys) {
    const item = document.createElement('li');
    item.textContent = t(key);
    list.append(item);
  }
  parent.append(list);
}

/** Definition list of already-localised `[term, value]` pairs. */
function buildDefinitionList(entries, className) {
  const list = document.createElement('dl');
  list.className = className;
  for (const [term, value] of entries) {
    const dt = document.createElement('dt');
    dt.textContent = term;
    const dd = document.createElement('dd');
    dd.textContent = value;
    list.append(dt, dd);
  }
  return list;
}

/** Sources table of the view: descending counts plus a total row. */
function buildSourcesTable(entries) {
  const table = document.createElement('table');
  table.className = 'benchmark-table methodology__table';

  const caption = document.createElement('caption');
  caption.className = 'benchmark-table__caption';
  caption.textContent = t('view.methodology.sources.caption');
  table.append(caption);

  const head = document.createElement('thead');
  const headRow = document.createElement('tr');
  for (const [labelKey, numeric] of [
    ['view.methodology.sources.source', false],
    ['view.methodology.sources.postings', true],
  ]) {
    const th = document.createElement('th');
    th.setAttribute('scope', 'col');
    if (numeric) th.className = 'benchmark-table__number';
    th.textContent = t(labelKey);
    headRow.append(th);
  }
  head.append(headRow);

  const body = document.createElement('tbody');
  for (const [name, count] of entries) {
    const row = document.createElement('tr');
    const nameCell = document.createElement('td');
    nameCell.textContent = name;
    const countCell = document.createElement('td');
    countCell.className = 'benchmark-table__number';
    countCell.textContent = formatCount(count, state.language);
    row.append(nameCell, countCell);
    body.append(row);
  }

  const foot = document.createElement('tfoot');
  const footRow = document.createElement('tr');
  const totalLabel = document.createElement('th');
  totalLabel.setAttribute('scope', 'row');
  totalLabel.textContent = t('view.methodology.sources.total');
  const totalValue = document.createElement('td');
  totalValue.className = 'benchmark-table__number';
  totalValue.textContent = formatCount(
    entries.reduce((sum, [, count]) => sum + count, 0),
    state.language,
  );
  footRow.append(totalLabel, totalValue);
  foot.append(footRow);

  table.append(head, body, foot);
  return table;
}

/**
 * Freshness block: meta.json fields, the mode explanation, the row count per
 * contract table and per source. Without `meta` (meta.json failed to load) a
 * localised warning replaces the block — never an empty section (RF-14).
 */
function appendFreshnessBlock(section) {
  const meta = state.meta;
  if (!meta) {
    section.append(noteElement('view.methodology.metaMissing', 'methodology__missing'));
    return;
  }
  appendParagraph(section, 'view.methodology.freshness.lead');

  const mode = meta.mode == null ? '' : String(meta.mode);
  const modeExplanationKey = mode === 'full'
    ? 'view.methodology.freshness.modeFull'
    : 'view.methodology.freshness.modeAggregated';
  section.append(buildDefinitionList([
    [t('view.methodology.freshness.dataDate'), formatDate(meta.data_date, state.language)],
    [t('view.methodology.freshness.generatedAt'), `${formatDateTime(meta.generated_at, state.language)} UTC`],
    [t('view.methodology.freshness.mode'), mode],
    [t('view.methodology.freshness.exportVersion'), formatCount(meta.export_version, state.language)],
    [t('view.methodology.freshness.size'), formatBytes(meta.size_bytes, state.language)],
  ], 'methodology__meta'));

  // A non-full export is not part of the site contract yet (see boot): the
  // mode explanation stays visible here even when the data layer is disabled.
  appendParagraph(section, modeExplanationKey, 'methodology__note');

  const tables = orderedTableCounts(meta.tables);
  if (tables.length > 0) {
    const subtitle = document.createElement('h4');
    subtitle.className = 'methodology__subtitle';
    subtitle.textContent = t('view.methodology.freshness.tablesTitle');
    section.append(subtitle, buildDefinitionList(
      tables.map(([name, count]) => [name, formatCount(count, state.language)]),
      'methodology__counts',
    ));
  }

  const sources = sortedSourceCounts(meta.sources);
  if (sources.length > 0) {
    const subtitle = document.createElement('h4');
    subtitle.className = 'methodology__subtitle';
    subtitle.textContent = t('view.methodology.freshness.sourcesTitle');
    section.append(subtitle, buildDefinitionList(
      sources.map(([name, count]) => [name, formatCount(count, state.language)]),
      'methodology__counts',
    ));
  }
}

/**
 * Methodology renderer (RF-13): the seven sections — sources, pipeline trace,
 * salary normalisation, date provenance, freshness, known limitations and
 * legal notice — assembled from i18n.js keys and the loaded `meta.json`. The
 * per-source table comes from `meta.sources`; without `meta` the section shows
 * a localised warning instead.
 */
function renderMethodology({ body }) {
  const sources = sortedSourceCounts(state.meta && state.meta.sources);

  const sourcesSection = appendMethodologySection(body, 'methodology-sources', 'view.methodology.sources.title');
  appendParagraph(sourcesSection, 'view.methodology.sources.lead');
  if (sources.length > 0) sourcesSection.append(buildSourcesTable(sources));
  appendParagraph(sourcesSection, 'view.methodology.sources.rawNote', 'methodology__note');

  const pipelineSection = appendMethodologySection(body, 'methodology-pipeline', 'view.methodology.pipeline.title');
  appendParagraph(pipelineSection, 'view.methodology.pipeline.lead');
  appendKeyList(pipelineSection, [
    'view.methodology.pipeline.step1',
    'view.methodology.pipeline.step2',
    'view.methodology.pipeline.step3',
    'view.methodology.pipeline.step4',
    'view.methodology.pipeline.step5',
    'view.methodology.pipeline.step6',
    'view.methodology.pipeline.step7',
  ], { className: 'methodology__steps', ordered: true });
  appendParagraph(pipelineSection, 'view.methodology.pipeline.note', 'methodology__note');

  const salarySection = appendMethodologySection(body, 'methodology-salary', 'view.methodology.salary.title');
  appendParagraph(salarySection, 'view.methodology.salary.lead');
  appendKeyList(salarySection, [
    'view.methodology.salary.point1',
    'view.methodology.salary.point2',
    'view.methodology.salary.point3',
    'view.methodology.salary.point4',
    'view.methodology.salary.point5',
  ]);

  const datesSection = appendMethodologySection(body, 'methodology-dates', 'view.methodology.dates.title');
  appendParagraph(datesSection, 'view.methodology.dates.lead');
  appendKeyList(datesSection, [
    'view.methodology.dates.point1',
    'view.methodology.dates.point2',
    'view.methodology.dates.point3',
  ]);

  const freshnessSection = appendMethodologySection(body, 'methodology-freshness', 'view.methodology.freshness.title');
  appendFreshnessBlock(freshnessSection);

  const limitationsSection = appendMethodologySection(body, 'methodology-limitations', 'view.methodology.limitations.title');
  appendParagraph(limitationsSection, 'view.methodology.limitations.lead');
  appendKeyList(limitationsSection, [
    'view.methodology.limitations.point1',
    'view.methodology.limitations.point2',
    'view.methodology.limitations.point3',
    'view.methodology.limitations.point4',
    'view.methodology.limitations.point5',
    'view.methodology.limitations.point6',
  ]);
  appendParagraph(limitationsSection, 'view.methodology.limitations.detail', 'methodology__note');

  const legalSection = appendMethodologySection(body, 'methodology-legal', 'view.methodology.legal.title');
  appendKeyList(legalSection, [
    'view.methodology.legal.point1',
    'view.methodology.legal.point2',
    'view.methodology.legal.point3',
    'view.methodology.legal.point4',
    'view.methodology.legal.point5',
  ]);
}

/* ========================================================================== *
 * Degradation helpers
 * ========================================================================== */

const knownDataDate = () => (state.meta ? state.meta.data_date : null);

function reportMissingFiles() {
  state.missingFiles.forEach((table) => {
    showNotice({
      id: `missing-${table}`,
      level: 'warning',
      titleKey: 'notice.fileMissing.title',
      messageKey: 'notice.fileMissing.message',
      vars: { file: `${table}.parquet` },
      rawDate: knownDataDate(),
    });
  });
}

function reportUnreadableFiles() {
  state.unreadableFiles.forEach((table) => {
    showNotice({
      id: `unreadable-${table}`,
      level: 'warning',
      titleKey: 'notice.fileUnreadable.title',
      messageKey: 'notice.fileUnreadable.message',
      vars: { file: `${table}.parquet` },
      rawDate: knownDataDate(),
    });
  });
}

/* ========================================================================== *
 * Boot
 * ========================================================================== */

async function boot() {
  elements.status = document.getElementById('status-bar');
  elements.freshness = document.getElementById('freshness');
  elements.notices = document.getElementById('app-notices');
  elements.view = document.getElementById('view-content');
  elements.navLinks = Array.from(document.querySelectorAll('[data-view-link]'));
  elements.languageSelect = document.getElementById('language-select');
  elements.filterChips = document.getElementById('filter-chips');
  elements.clearFilters = document.getElementById('clear-filters');
  elements.filtersToggle = document.getElementById('filters-toggle');
  elements.filtersBody = document.getElementById('filters-body');
  elements.companyOptions = document.getElementById('filter-company-options');
  elements.filterControls = Object.fromEntries(
    Object.entries(FILTER_CONTROL_IDS).map(([key, id]) => [key, document.getElementById(id)]),
  );

  state.language = resolveInitialLanguage({
    storage: safeStorage(),
    navLanguages: navigator.languages || [navigator.language],
  });
  applyDocumentTranslations(document, state.language);
  bindLanguageSelector();
  bindFilterControls();
  bindFiltersToggle();
  window.addEventListener('hashchange', applyRoute);
  window.addEventListener('resize', resizeCharts);
  applyRoute();
  renderAll();

  // 1. Freshness metadata (also the first degradation check).
  setStatus('status.loadingMeta', 'pending');
  try {
    state.meta = await fetchJson(`${DATA_DIR}meta.json`);
  } catch (error) {
    console.warn('[tml] could not load meta.json:', error);
    showNotice({
      id: 'meta-missing',
      level: 'error',
      titleKey: 'notice.metaMissing.title',
      messageKey: 'notice.metaMissing.message',
    });
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    renderFreshness();
    return;
  }
  renderFreshness();

  // The methodology view renders from meta.json without DuckDB: refresh it now
  // that freshness is known. Other views wait for the data layer instead of
  // re-rendering twice.
  if (state.activeViewId === 'methodology') await renderActiveView();

  // 2. Aggregated exports are not part of the site contract yet (RF-4/T-19).
  if (state.meta.mode !== 'full') {
    showNotice({
      id: 'mode',
      level: 'warning',
      titleKey: 'notice.modeNotFull.title',
      messageKey: 'notice.modeNotFull.message',
      vars: { mode: state.meta.mode },
      rawDate: state.meta.data_date,
    });
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    return;
  }

  // 3. WASM support and pinned CDN modules (dynamic imports → catchable).
  if (!wasmSupported()) {
    showNotice({
      id: 'no-wasm',
      level: 'error',
      titleKey: 'notice.noWasm.title',
      messageKey: 'notice.noWasm.message',
    });
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    return;
  }
  setStatus('status.loadingCdn', 'pending');
  try {
    state.duckdb = await import('@duckdb/duckdb-wasm');
  } catch (error) {
    console.warn('[tml] DuckDB-WASM CDN import failed:', error);
    showNotice({
      id: 'cdn',
      level: 'error',
      titleKey: 'notice.cdnFailed.title',
      messageKey: 'notice.cdnFailed.message',
    });
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    return;
  }
  try {
    state.echarts = await import('echarts');
  } catch (error) {
    console.warn('[tml] ECharts CDN import failed:', error);
    showNotice({
      id: 'echarts',
      level: 'warning',
      titleKey: 'notice.echartsFailed.title',
      messageKey: 'notice.echartsFailed.message',
    });
  }

  // 4. Full Parquet download and registration in DuckDB-WASM.
  const buffers = await downloadData();
  state.loadedTables = [...buffers.keys()];
  reportMissingFiles();
  if (state.loadedTables.length === 0) {
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    return;
  }

  try {
    setStatus('status.buildingViews', 'pending');
    await openDatabase(buffers);
    await createViews();
    reportUnreadableFiles();
    await runSmokeQuery();
  } catch (error) {
    console.error('[tml] could not initialise DuckDB-WASM:', error);
    showNotice({
      id: 'cdn',
      level: 'error',
      titleKey: 'notice.cdnFailed.title',
      messageKey: 'notice.cdnFailed.message',
    });
    setStatus('status.unavailable', 'error');
    markFiltersUnavailable();
    return;
  }

  const degraded = state.missingFiles.length > 0 ||
    state.unreadableFiles.length > 0 ||
    state.smokeCount == null;
  if (degraded) {
    setStatus('status.degraded', 'warn');
    markFiltersUnavailable();
  } else {
    setStatus('status.ready', 'ok');
    await initializeFilters();
  }
}

/**
 * Read-only surface for local verification and T-10–T-14. Browser console:
 * `await tml.query('SELECT COUNT(*) FROM fact_offers')`,
 * `await tml.setFilters({ country: 'Spain' })`,
 * `await tml.runBuilder('monthlyTrend')`.
 */
window.tml = {
  getState: () => state,
  query: (sql) => runQuery(sql),
  translate: (key, vars) => t(key, vars),
  getFilters: () => ({ ...state.filters }),
  setFilters: (patch) => setFilters(patch),
  clearFilters: () => clearFilters(),
  getWhere: () => buildWhere(state.filters),
  runBuilder: (name, overrides = {}, options = {}) => {
    const builder = queries[name];
    if (typeof builder !== 'function') {
      return Promise.reject(new Error(`Unknown query builder: ${name}`));
    }
    return runQuery(builder({ ...buildQueryOptions(state.filters, options), ...overrides }));
  },
};

boot();
