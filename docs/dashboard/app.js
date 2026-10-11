/**
 * app.js — bootstrap and shell of the public Talent Market Lens dashboard.
 *
 * Scope of this file (T-08, RF-3/RF-6/RF-14/RF-16): load `meta.json` and the
 * four Parquet files with plain `fetch`, register them in DuckDB-WASM with
 * `registerFileBuffer` (no range requests), create the contract views
 * (`fact_offers`, `fact_offer_skills`, `dim_skill_list`, `dim_calendar`), run a
 * smoke query and render the hash-routed shell with visible freshness. View
 * contents (KPIs and charts) arrive in T-10–T-14; the shell only shows a
 * localised placeholder. The SQL itself lives in `queries.js` (T-07).
 *
 * Degradation (RF-14): missing files, unsupported WASM, CDN failures and a
 * non-`full` export mode never leave a blank page; each case raises a visible
 * notice with the last known data date, and the status bar reflects the state.
 *
 * CDN dependencies are pinned through the import map in `index.html`
 * (DuckDB-WASM 1.32.0, ECharts 6.1.0). Both are imported dynamically so a CDN
 * failure can be caught and reported.
 *
 * Identifiers and comments are in English (constitution 6); user texts live in
 * i18n.js (Spanish/English).
 */

import {
  LANGUAGES,
  applyDocumentTranslations,
  formatCount,
  formatDate,
  formatDateTime,
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
};

const elements = {
  status: null,
  freshness: null,
  notices: null,
  view: null,
  navLinks: [],
  languageSelect: null,
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

function renderActiveView() {
  const view = activeView();
  if (elements.view) {
    elements.view.dataset.view = view.id;

    const title = document.createElement('h2');
    title.className = 'view__title';
    title.id = 'view-title';
    title.textContent = t(view.titleKey);

    const badge = document.createElement('span');
    badge.className = 'view__badge';
    badge.textContent = t('view.pending');

    const header = document.createElement('div');
    header.className = 'view__header';
    header.append(title, badge);

    // The localised description doubles as placeholder until T-10–T-14 fill
    // the view with KPIs and charts; no figures are shown meanwhile.
    const placeholder = document.createElement('p');
    placeholder.className = 'view__placeholder';
    placeholder.textContent = t(view.descriptionKey);

    elements.view.replaceChildren(header, placeholder);
  }
  if (elements.navLinks.length > 0) {
    const activeHash = `#/${view.id}`;
    elements.navLinks.forEach((link) => {
      if (link.getAttribute('href') === activeHash) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }
  document.title = `${t(view.titleKey)} · ${t('app.title')}`;
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

  state.language = resolveInitialLanguage({
    storage: safeStorage(),
    navLanguages: navigator.languages || [navigator.language],
  });
  applyDocumentTranslations(document, state.language);
  bindLanguageSelector();
  window.addEventListener('hashchange', applyRoute);
  applyRoute();
  renderFreshness();
  renderStatus();

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
    return;
  }

  const degraded = state.missingFiles.length > 0 ||
    state.unreadableFiles.length > 0 ||
    state.smokeCount == null;
  if (degraded) {
    setStatus('status.degraded', 'warn');
  } else {
    setStatus('status.ready', 'ok');
  }
}

/**
 * Small read-only surface for local verification and the next tasks
 * (T-09 composes the global filters on top of `query`). Browser console:
 * `await tml.query('SELECT COUNT(*) FROM fact_offers')`.
 */
window.tml = {
  getState: () => state,
  query: (sql) => runQuery(sql),
  translate: (key, vars) => t(key, vars),
};

boot();
