/**
 * app.js — bootstrap and shell of the public Talent Market Lens dashboard.
 *
 * Scope of this file (T-08, RF-3/RF-6/RF-14/RF-16): load `meta.json` and the
 * four Parquet files with plain `fetch`, register them in DuckDB-WASM with
 * `registerFileBuffer` (no range requests), create the contract views
 * (`fact_offers`, `fact_offer_skills`, `dim_skill_list`, `dim_calendar`), run a
 * smoke query and render the hash-routed shell with visible freshness. View
 * contents arrive view by view (T-10–T-14): Market Pulse is implemented in
 * T-10 (KPIs + ECharts), the rest keep a localised placeholder. The SQL itself
 * lives in `queries.js` (T-07) and the ECharts options in `charts.js` (T-10).
 *
 * T-09 (RF-9): the global filter state lives here and is composed into one
 * WHERE fragment by `filters.js`; the categorical controls are populated once
 * with SELECT DISTINCT, the active filters are shown as removable chips and a
 * small coherence summary runs `queries.js` with the current state. T-10 adds
 * the Market Pulse renderer: a filter change re-renders the active view (same
 * debounce), every render disposes the previous ECharts instances and a
 * missing ECharts leaves the KPIs with a localised note where charts would be.
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
  donutChart,
  lineChart,
} from './charts.js';
import {
  LANGUAGES,
  applyDocumentTranslations,
  formatCount,
  formatDate,
  formatDateTime,
  formatMonth,
  formatPercent,
  formatSalary,
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
};

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
  summary: { status: 'pending', values: null },
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
  summaryBody: null,
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
  if (!renderer || !viewDataReady()) {
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
  renderSummary();
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
 * Global filters (RF-9): one compositor, visible chips, coherence summary
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

const SUMMARY_MESSAGE_KEYS = {
  pending: 'filters.summary.pending',
  unavailable: 'filters.summary.unavailable',
  empty: 'filters.summary.empty',
  error: 'filters.summary.error',
};

let filterInputTimer = null;
const pendingFilterPatches = new Map();
let summaryRequestId = 0;

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

function renderSummary() {
  if (!elements.summaryBody) return;
  const { status, values } = state.summary;
  if (status === 'ready') {
    const rows = [
      [t('filters.summary.total'), formatCount(values.total, state.language)],
      [t('filters.summary.monthly'), formatCount(values.monthly, state.language)],
    ];
    if (values.skillDemand != null) {
      rows.push([
        t('filters.summary.skillDemand', { skill: state.filters.skill }),
        formatCount(values.skillDemand, state.language),
      ]);
    }
    const list = document.createElement('dl');
    list.className = 'summary__list';
    for (const [term, value] of rows) {
      const dt = document.createElement('dt');
      dt.textContent = term;
      const dd = document.createElement('dd');
      dd.textContent = value;
      list.append(dt, dd);
    }
    elements.summaryBody.replaceChildren(list);
    return;
  }
  const message = document.createElement('p');
  message.className = `summary__${status}`;
  message.textContent = t(SUMMARY_MESSAGE_KEYS[status] || 'filters.summary.error');
  elements.summaryBody.replaceChildren(message);
}

/**
 * Runs the real queries of queries.js with the current state: the total
 * (measure 1) plus the monthly trend sum, which must agree with the total. A
 * skill filter adds the bridge demand of the selected skill. This is the
 * temporary coherence surface until T-10–T-14 render the view KPIs.
 */
async function refreshSummary() {
  if (!state.filtersReady) {
    state.summary = { status: 'pending', values: null };
    renderSummary();
    return;
  }
  const requestId = ++summaryRequestId;
  const options = buildQueryOptions(state.filters);
  try {
    const [totalRows, trendRows] = await Promise.all([
      runQuery(queries.totalJobOffers(options)),
      runQuery(queries.monthlyTrend(options)),
    ]);
    let skillDemand = null;
    if (!isBlank(state.filters.skill)) {
      const rows = await runQuery(queries.skillDemand(options));
      const match = rows.find((row) => row.skill === state.filters.skill);
      skillDemand = match ? match.skill_demand : 0;
    }
    if (requestId !== summaryRequestId) return;
    const total = totalRows.length > 0 ? totalRows[0].total_job_offers : 0;
    const monthly = trendRows.reduce((sum, row) => sum + (Number(row.job_offers) || 0), 0);
    state.summary = total === 0
      ? { status: 'empty', values: null }
      : { status: 'ready', values: { total, monthly, skillDemand } };
  } catch (error) {
    if (requestId !== summaryRequestId) return;
    console.error('[tml] filter summary query failed:', error);
    state.summary = { status: 'error', values: null };
  }
  renderSummary();
}

function syncFilterControls() {
  for (const [key, control] of Object.entries(elements.filterControls)) {
    if (control) control.value = state.filters[key] ?? '';
  }
}

function applyFilterChange(filters) {
  state.filters = filters;
  syncFilterControls();
  renderFilterChips();
  if (!state.filtersReady) return Promise.resolve();
  // The coherence summary and the active view share the same filter state;
  // awaiting both keeps `window.tml.setFilters()` truthful for local checks.
  return Promise.all([refreshSummary(), renderActiveView()]);
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

/** Marks the whole filter surface as unavailable (degraded boot paths). */
function markFiltersUnavailable() {
  state.filtersReady = false;
  state.summary = { status: 'unavailable', values: null };
  setFilterControlsEnabled(false);
  renderFilterChips();
  renderSummary();
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
  await refreshSummary();
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
 * Market Pulse (T-10, RF-7/RF-8/RF-16): KPIs 1-11 and executive charts
 * ========================================================================== */

/** Market Pulse KPI cards mapped to measures 1-11 of §7 of the plan. */
const MARKET_PULSE_KPIS = [
  { id: 'total', labelKey: 'kpi.totalJobOffers', builder: 'totalJobOffers', alias: 'total_job_offers', format: 'count' },
  { id: 'companies', labelKey: 'kpi.companiesHiring', builder: 'companiesHiring', alias: 'companies_hiring', format: 'count' },
  { id: 'countries', labelKey: 'kpi.countriesHiring', builder: 'countriesHiring', alias: 'countries_hiring', format: 'count' },
  { id: 'cities', labelKey: 'kpi.citiesHiring', builder: 'citiesHiring', alias: 'cities_hiring', format: 'count' },
  { id: 'last7', labelKey: 'kpi.offersLast7Days', builder: 'offersLast7Days', alias: 'offers_last_7_days', format: 'count' },
  { id: 'last30', labelKey: 'kpi.offersLast30Days', builder: 'offersLast30Days', alias: 'offers_last_30_days', format: 'count' },
  { id: 'remote', labelKey: 'kpi.remoteShare', builder: 'remoteShare', alias: 'remote_share', format: 'percent' },
  { id: 'hybrid', labelKey: 'kpi.hybridShare', builder: 'hybridShare', alias: 'hybrid_share', format: 'percent' },
  { id: 'onsite', labelKey: 'kpi.onSiteShare', builder: 'onSiteShare', alias: 'on_site_share', format: 'percent' },
  { id: 'remoteFriendly', labelKey: 'kpi.remoteFriendlyShare', builder: 'remoteFriendlyShare', alias: 'remote_friendly_share', format: 'percent' },
  { id: 'disclosure', labelKey: 'kpi.salaryDisclosureRate', builder: 'salaryDisclosureRate', alias: 'salary_disclosure_rate', format: 'percent' },
];

const EMPTY_VALUE = '—';

/** Localised KPI value: shares arrive as 0-1 ratios, counts as integers. */
function formatKpiValue(value, format) {
  if (value == null) return EMPTY_VALUE;
  return format === 'percent'
    ? formatPercent(value, state.language)
    : formatCount(value, state.language);
}

function renderKpiList(kpis) {
  const list = document.createElement('ul');
  list.className = 'kpi-grid';
  for (const kpi of kpis) {
    const item = document.createElement('li');
    item.className = 'kpi';
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
 * Runs the eleven KPI builders plus the four chart aggregations in one batch
 * with the current global filters (RF-9). Every builder comes from
 * `queries.js`; this module only orchestrates calls and formats results.
 */
async function fetchMarketPulseData(options) {
  const [kpis, trend, topRoles, topCompanies, experience] = await Promise.all([
    Promise.all(MARKET_PULSE_KPIS.map(async (kpi) => {
      const rows = await runQuery(queries[kpi.builder](options));
      return { ...kpi, value: rows.length > 0 ? rows[0][kpi.alias] : null };
    })),
    runQuery(queries.monthlyTrend(options)),
    runQuery(queries.topRoles(options)),
    runQuery(queries.topCompanies(options)),
    runQuery(queries.experienceBreakdown(options)),
  ]);
  return { kpis, trend, topRoles, topCompanies, experience };
}

const formatCountCallback = (value) => formatCount(value, state.language);
const formatPercentCallback = (value) => formatPercent(value, state.language);

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
  return barChart({
    categories: rows.map((row) => row[nameField]),
    values: rows.map((row) => Number(row.job_offers) || 0),
    seriesName: t(seriesNameKey),
    horizontal: true,
    formatValue: formatCountCallback,
  });
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
function appendChartCard(grid, { titleKey, captionKey, wide = false, option }) {
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
    body.replaceWith(noteElement('view.marketPulse.noData', 'chart-empty'));
    return null;
  }
  return () => mountChart(body, option);
}

/**
 * Market Pulse renderer (RF-7): KPI cards 1-11, monthly trend by
 * `PostedYearMonth`, top 10 roles, top 10 companies and the experience
 * breakdown. Data, labels and formats come from the current language and the
 * global filters; this function only assembles DOM and mounts charts.
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
  grid.className = 'chart-grid';
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
  elements.summaryBody = document.getElementById('summary-body');
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
