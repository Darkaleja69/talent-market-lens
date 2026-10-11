/**
 * i18n.js — bilingual (Spanish/English) dictionary for the public dashboard.
 *
 * Pure ES module: no DOM or browser globals are touched at import time, so the
 * same file can be imported from a browser or from Node during local checks.
 * Every user-facing string of the shell lives here (RF-16); the data itself is
 * shown as it arrives (English), as decided in the spec.
 *
 * Language resolution (applied by app.js at boot):
 *   1. stored preference (localStorage, key `tml.language`),
 *   2. browser language (`es*` → Spanish, anything else → English).
 */

export const LANGUAGES = ['es', 'en'];
export const DEFAULT_LANGUAGE = 'en';
export const STORAGE_KEY = 'tml.language';

/** Shell messages. Both dictionaries share exactly the same keys. */
export const MESSAGES = {
  es: {
    'app.title': 'Talent Market Lens',
    'app.subtitle': 'Panorama público de ofertas de empleo reales',
    'a11y.skipToContent': 'Saltar al contenido',
    'nav.label': 'Vistas',
    'nav.marketPulse': 'Pulso del mercado',
    'nav.rolesSkills': 'Roles y skills',
    'nav.salaryInsights': 'Perspectivas salariales',
    'nav.opportunityExplorer': 'Explorador de ofertas',
    'nav.methodology': 'Metodología',
    'view.marketPulse.description':
      'Indicadores clave del mercado: volumen de ofertas, empresas, países, modalidad de trabajo, tendencia mensual y experiencia.',
    'view.rolesSkills.description':
      'Demanda y salario por skill, categorías de skills y tabla comparativa.',
    'view.salaryInsights.description':
      'Distribución salarial (media, mediana, P25 y P75), salarios por rol y modalidad, y mapa por país.',
    'view.opportunityExplorer.description':
      'Buscador de ofertas individuales con filtros y enlace a la oferta original.',
    'view.methodology.description':
      'Fuentes, proceso de datos, normalización salarial, frescura y limitaciones.',
    'view.pending': 'Próximamente',
    'status.initializing': 'Iniciando…',
    'status.loadingMeta': 'Cargando metadatos…',
    'status.loadingCdn': 'Cargando librerías…',
    'status.loadingData': 'Descargando datos ({done}/{total})…',
    'status.buildingViews': 'Preparando consultas…',
    'status.ready': 'Listo · {count} ofertas cargadas',
    'status.degraded': 'Datos incompletos · faltan {files}',
    'status.unavailable': 'Datos no disponibles',
    'freshness.label': 'Datos',
    'freshness.updated': 'Actualizado',
    'freshness.unknown': 'Sin datos disponibles',
    'freshness.title': 'Fecha de los datos y última actualización',
    'language.label': 'Idioma',
    'notice.errorLabel': 'Error',
    'notice.warningLabel': 'Aviso',
    'notice.metaMissing.title': 'No hay datos publicados',
    'notice.metaMissing.message':
      'No se pudo cargar la información de frescura (meta.json). Inténtalo de nuevo más tarde.',
    'notice.modeNotFull.title': 'Modo de datos no soportado',
    'notice.modeNotFull.message':
      'La instantánea publicada usa el modo «{mode}» y esta versión aún no lo soporta. Última fecha de datos: {date}.',
    'notice.noWasm.title': 'Navegador no compatible',
    'notice.noWasm.message':
      'Tu navegador no soporta WebAssembly, necesario para consultar los datos en local. Prueba con una versión reciente de Chrome, Firefox, Edge o Safari.',
    'notice.cdnFailed.title': 'No se pudieron cargar las librerías',
    'notice.cdnFailed.message':
      'El CDN de DuckDB-WASM no respondió. Comprueba tu conexión y recarga la página.',
    'notice.echartsFailed.title': 'Gráficos no disponibles',
    'notice.echartsFailed.message':
      'ECharts no se pudo cargar desde el CDN; los gráficos no se mostrarán, pero el resto de la página sigue disponible.',
    'notice.fileMissing.title': 'Datos incompletos',
    'notice.fileMissing.message':
      'No se pudo descargar {file}. Se muestra la fecha del último dato disponible: {date}.',
    'notice.fileUnreadable.title': 'Fichero de datos ilegible',
    'notice.fileUnreadable.message':
      'No se pudo leer {file}. Se muestra la fecha del último dato disponible: {date}.',
    'footer.note': 'Datos de ofertas públicas; sin datos personales ni credenciales.',
  },
  en: {
    'app.title': 'Talent Market Lens',
    'app.subtitle': 'Public overview of real job postings',
    'a11y.skipToContent': 'Skip to content',
    'nav.label': 'Views',
    'nav.marketPulse': 'Market Pulse',
    'nav.rolesSkills': 'Roles & Skills',
    'nav.salaryInsights': 'Salary Insights',
    'nav.opportunityExplorer': 'Opportunity Explorer',
    'nav.methodology': 'Methodology',
    'view.marketPulse.description':
      'Key market indicators: posting volume, companies, countries, work mode, monthly trend and experience.',
    'view.rolesSkills.description':
      'Skill demand and salary, skill categories and a benchmark table.',
    'view.salaryInsights.description':
      'Salary distribution (average, median, P25 and P75), salaries by role and work mode, and a country map.',
    'view.opportunityExplorer.description':
      'Individual posting explorer with filters and links to the original posting.',
    'view.methodology.description':
      'Sources, data process, salary normalisation, freshness and limitations.',
    'view.pending': 'Coming soon',
    'status.initializing': 'Starting…',
    'status.loadingMeta': 'Loading metadata…',
    'status.loadingCdn': 'Loading libraries…',
    'status.loadingData': 'Downloading data ({done}/{total})…',
    'status.buildingViews': 'Preparing queries…',
    'status.ready': 'Ready · {count} postings loaded',
    'status.degraded': 'Incomplete data · missing {files}',
    'status.unavailable': 'Data unavailable',
    'freshness.label': 'Data',
    'freshness.updated': 'Updated',
    'freshness.unknown': 'No data available',
    'freshness.title': 'Data date and last update',
    'language.label': 'Language',
    'notice.errorLabel': 'Error',
    'notice.warningLabel': 'Warning',
    'notice.metaMissing.title': 'No published data',
    'notice.metaMissing.message':
      'Could not load the freshness information (meta.json). Please try again later.',
    'notice.modeNotFull.title': 'Unsupported data mode',
    'notice.modeNotFull.message':
      'The published snapshot uses mode “{mode}”, which this version does not support yet. Last data date: {date}.',
    'notice.noWasm.title': 'Unsupported browser',
    'notice.noWasm.message':
      'Your browser does not support WebAssembly, which is required to query the data locally. Try a recent version of Chrome, Firefox, Edge or Safari.',
    'notice.cdnFailed.title': 'Could not load the libraries',
    'notice.cdnFailed.message':
      'The DuckDB-WASM CDN did not respond. Check your connection and reload the page.',
    'notice.echartsFailed.title': 'Charts unavailable',
    'notice.echartsFailed.message':
      'ECharts could not be loaded from the CDN; charts will not be shown, but the rest of the page keeps working.',
    'notice.fileMissing.title': 'Incomplete data',
    'notice.fileMissing.message':
      'Could not download {file}. Showing the last available data date: {date}.',
    'notice.fileUnreadable.title': 'Unreadable data file',
    'notice.fileUnreadable.message':
      'Could not read {file}. Showing the last available data date: {date}.',
    'footer.note': 'Public job posting data; no personal data and no credentials.',
  },
};

/** Replaces `{name}` placeholders; unknown placeholders are left untouched. */
const interpolate = (template, vars) =>
  String(template).replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );

/**
 * Returns the message for `key` in `language`, falling back to the default
 * language and finally to the key itself (visible signal of a missing entry).
 */
export function translate(language, key, vars = {}) {
  const table = MESSAGES[language] || MESSAGES[DEFAULT_LANGUAGE];
  const template = table[key] ?? MESSAGES[DEFAULT_LANGUAGE][key] ?? key;
  return interpolate(template, vars);
}

/** `es*` → Spanish; anything else (or missing) → English. */
export function detectLanguage(navLanguages = []) {
  const list = Array.isArray(navLanguages) ? navLanguages : [navLanguages];
  const prefersSpanish = list.some(
    (tag) => typeof tag === 'string' && tag.toLowerCase().startsWith('es'),
  );
  return prefersSpanish ? 'es' : DEFAULT_LANGUAGE;
}

/** Reads the stored preference; returns null when absent, invalid or blocked. */
export function storedLanguage(storage) {
  try {
    const value = storage ? storage.getItem(STORAGE_KEY) : null;
    return LANGUAGES.includes(value) ? value : null;
  } catch {
    return null;
  }
}

/** Stored preference first, then browser language, then English. */
export function resolveInitialLanguage({ storage = null, navLanguages = [] } = {}) {
  return storedLanguage(storage) || detectLanguage(navLanguages);
}

/** Persists the preference, ignoring environments where storage is blocked. */
export function persistLanguage(storage, language) {
  try {
    if (storage && LANGUAGES.includes(language)) {
      storage.setItem(STORAGE_KEY, language);
    }
  } catch {
    /* private mode or storage disabled: keep the in-memory preference only */
  }
}

const localeTag = (language) => (language === 'es' ? 'es-ES' : 'en-US');

const toDate = (value) => {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** Localised long date of an ISO day (`2026-10-09`), always in UTC. */
export function formatDate(value, language) {
  const date = toDate(value);
  if (!date) return String(value ?? '');
  return new Intl.DateTimeFormat(localeTag(language), {
    dateStyle: 'long',
    timeZone: 'UTC',
  }).format(date);
}

/** Localised date + time of an ISO timestamp (`...Z`), always in UTC. */
export function formatDateTime(value, language) {
  const date = toDate(value);
  if (!date) return String(value ?? '');
  return new Intl.DateTimeFormat(localeTag(language), {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date);
}

/** Localised integer/quantity (thousands separator per language). */
export function formatCount(value, language) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? '');
  return new Intl.NumberFormat(localeTag(language)).format(number);
}

/**
 * Applies the dictionary to the static shell: `<html lang>`, `[data-i18n]`
 * text content and `[data-i18n-aria-label]` / `[data-i18n-title]` attributes.
 */
export function applyDocumentTranslations(doc, language) {
  if (!doc || !LANGUAGES.includes(language)) return;
  doc.documentElement.setAttribute('lang', language);
  doc.querySelectorAll('[data-i18n]').forEach((element) => {
    element.textContent = translate(language, element.getAttribute('data-i18n'));
  });
  doc.querySelectorAll('[data-i18n-aria-label]').forEach((element) => {
    element.setAttribute('aria-label', translate(language, element.getAttribute('data-i18n-aria-label')));
  });
  doc.querySelectorAll('[data-i18n-title]').forEach((element) => {
    element.setAttribute('title', translate(language, element.getAttribute('data-i18n-title')));
  });
}
