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
    'view.error': 'No se pudieron calcular los datos de esta vista. Inténtalo de nuevo.',
    'view.marketPulse.trend.title': 'Ofertas por mes',
    'view.marketPulse.trend.caption': 'Ofertas por mes de publicación (PostedYearMonth).',
    'view.marketPulse.topRoles.title': 'Roles con más ofertas',
    'view.marketPulse.topRoles.caption': 'Top 10 categorías de rol por número de ofertas.',
    'view.marketPulse.topCompanies.title': 'Empresas con más ofertas',
    'view.marketPulse.topCompanies.caption': 'Top 10 empresas por número de ofertas.',
    'view.marketPulse.experience.title': 'Reparto por experiencia',
    'view.marketPulse.experience.caption': 'Reparto de ofertas por nivel de experiencia.',
    'view.marketPulse.noData': 'Sin datos para este gráfico con los filtros actuales.',
    'view.marketPulse.chartsUnavailable':
      'Los gráficos no están disponibles porque ECharts no se pudo cargar desde el CDN; los indicadores superiores siguen calculados con los datos cargados.',
    'view.rolesSkills.bubble.title': 'Demanda frente a salario',
    'view.rolesSkills.bubble.caption':
      'Cada burbuja es una skill con mediana de salario; el tamaño representa su demanda. Las skills sin salario publicado no aparecen.',
    'view.rolesSkills.bubble.xAxis': 'Demanda (ofertas)',
    'view.rolesSkills.bubble.yAxis': 'Salario mediano (EUR)',
    'view.rolesSkills.bubble.tooltip':
      '{skill} ({category}) · Demanda: {demand} · Salario mediano: {salary}',
    'view.rolesSkills.category.title': 'Demanda de skills por categoría',
    'view.rolesSkills.category.caption':
      'Reparto de pares oferta-skill por categoría del catálogo (Skill Demand by Category).',
    'view.rolesSkills.benchmark.title': 'Tabla comparativa de skills',
    'view.rolesSkills.benchmark.caption':
      'Todas las skills del catálogo con ofertas, ordenadas por demanda descendente. Desplázate dentro de la tabla para ver el resto.',
    'view.rolesSkills.table.skill': 'Skill',
    'view.rolesSkills.table.category': 'Categoría',
    'view.rolesSkills.table.demand': 'Demanda (ofertas)',
    'view.rolesSkills.table.share': 'Cuota',
    'view.rolesSkills.table.medianSalary': 'Salario mediano (EUR)',
    'view.rolesSkills.notAvailable': 'N/D',
    'view.rolesSkills.noData': 'Sin datos para esta vista con los filtros actuales.',
    'view.rolesSkills.chartsUnavailable':
      'Los gráficos no están disponibles porque ECharts no se pudo cargar desde el CDN; la tabla sigue calculada con los datos cargados.',
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
    'filters.title': 'Filtros globales',
    'filters.active': 'Filtros activos',
    'filters.clearAll': 'Limpiar todo',
    'filters.none': 'Sin filtros activos',
    'filters.remove': 'Quitar filtro: {field}',
    'filter.any': 'Todos',
    'filter.field.country': 'País',
    'filter.field.experience': 'Experiencia',
    'filter.field.role': 'Rol',
    'filter.field.workMode': 'Modalidad',
    'filter.field.company': 'Empresa',
    'filter.field.skill': 'Skill',
    'filter.field.source': 'Fuente',
    'filter.field.salary': 'Salario',
    'filter.field.salaryMin': 'Salario mínimo (anual)',
    'filter.field.salaryMax': 'Salario máximo (anual)',
    'filter.salary.hint': 'Rango sobre el salario máximo anual de la oferta. Vacío = sin límite.',
    'filter.salary.placeholder': 'Sin límite',
    'filter.salary.from': 'Desde {value}',
    'filter.salary.upTo': 'Hasta {value}',
    'filter.salary.between': '{min} – {max}',
    'filters.summary.title': 'Resumen con los filtros actuales',
    'filters.summary.note':
      'Comprobación provisional: estas cifras usan las mismas consultas que las vistas.',
    'filters.summary.pending': 'Esperando los datos…',
    'filters.summary.unavailable': 'Resumen no disponible: datos incompletos.',
    'filters.summary.error': 'No se pudo calcular el resumen.',
    'filters.summary.empty': 'Sin resultados con los filtros actuales.',
    'filters.summary.total': 'Ofertas (total)',
    'filters.summary.monthly': 'Ofertas con mes de publicación',
    'filters.summary.skillDemand': 'Ofertas con la skill «{skill}»',
    'kpi.totalJobOffers': 'Total de ofertas',
    'kpi.companiesHiring': 'Empresas contratando',
    'kpi.countriesHiring': 'Países contratando',
    'kpi.citiesHiring': 'Ciudades contratando',
    'kpi.offersLast7Days': 'Ofertas últimos 7 días',
    'kpi.offersLast30Days': 'Ofertas últimos 30 días',
    'kpi.remoteShare': 'Cuota de trabajo remoto',
    'kpi.hybridShare': 'Cuota de trabajo híbrido',
    'kpi.onSiteShare': 'Cuota de trabajo presencial',
    'kpi.remoteFriendlyShare': 'Cuota remoto o híbrido',
    'kpi.salaryDisclosureRate': 'Tasa de salario publicado',
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
    'view.error': 'Could not compute the data for this view. Please try again.',
    'view.marketPulse.trend.title': 'Job Postings by Month',
    'view.marketPulse.trend.caption': 'Postings per publication month (PostedYearMonth).',
    'view.marketPulse.topRoles.title': 'Top Roles',
    'view.marketPulse.topRoles.caption': 'Top 10 role categories by number of postings.',
    'view.marketPulse.topCompanies.title': 'Top Companies',
    'view.marketPulse.topCompanies.caption': 'Top 10 companies by number of postings.',
    'view.marketPulse.experience.title': 'Experience Level',
    'view.marketPulse.experience.caption': 'Share of postings by experience level.',
    'view.marketPulse.noData': 'No data for this chart with the current filters.',
    'view.marketPulse.chartsUnavailable':
      'Charts are unavailable because ECharts could not be loaded from the CDN; the indicators above are still computed from the loaded data.',
    'view.rolesSkills.bubble.title': 'Demand vs. Salary',
    'view.rolesSkills.bubble.caption':
      'Each bubble is a skill with a median salary; size represents its demand. Skills without a disclosed salary are not shown.',
    'view.rolesSkills.bubble.xAxis': 'Demand (postings)',
    'view.rolesSkills.bubble.yAxis': 'Median salary (EUR)',
    'view.rolesSkills.bubble.tooltip':
      '{skill} ({category}) · Demand: {demand} · Median salary: {salary}',
    'view.rolesSkills.category.title': 'Skill Demand by Category',
    'view.rolesSkills.category.caption':
      'Share of offer+skill pairs per catalogue category (Skill Demand by Category).',
    'view.rolesSkills.benchmark.title': 'Skills Benchmark',
    'view.rolesSkills.benchmark.caption':
      'All catalogued skills with postings, ranked by demand (highest first). Scroll inside the table to see the rest.',
    'view.rolesSkills.table.skill': 'Skill',
    'view.rolesSkills.table.category': 'Category',
    'view.rolesSkills.table.demand': 'Demand (postings)',
    'view.rolesSkills.table.share': 'Share',
    'view.rolesSkills.table.medianSalary': 'Median salary (EUR)',
    'view.rolesSkills.notAvailable': 'N/A',
    'view.rolesSkills.noData': 'No data for this view with the current filters.',
    'view.rolesSkills.chartsUnavailable':
      'Charts are unavailable because ECharts could not be loaded from the CDN; the table is still computed from the loaded data.',
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
    'filters.title': 'Global filters',
    'filters.active': 'Active filters',
    'filters.clearAll': 'Clear all',
    'filters.none': 'No active filters',
    'filters.remove': 'Remove filter: {field}',
    'filter.any': 'All',
    'filter.field.country': 'Country',
    'filter.field.experience': 'Experience',
    'filter.field.role': 'Role',
    'filter.field.workMode': 'Work mode',
    'filter.field.company': 'Company',
    'filter.field.skill': 'Skill',
    'filter.field.source': 'Source',
    'filter.field.salary': 'Salary',
    'filter.field.salaryMin': 'Minimum salary (annual)',
    'filter.field.salaryMax': 'Maximum salary (annual)',
    'filter.salary.hint': "Range over the posting's annual maximum salary. Empty = no limit.",
    'filter.salary.placeholder': 'No limit',
    'filter.salary.from': 'From {value}',
    'filter.salary.upTo': 'Up to {value}',
    'filter.salary.between': '{min} – {max}',
    'filters.summary.title': 'Summary with the current filters',
    'filters.summary.note':
      'Temporary check: these figures run the same queries as the views.',
    'filters.summary.pending': 'Waiting for the data…',
    'filters.summary.unavailable': 'Summary unavailable: incomplete data.',
    'filters.summary.error': 'Could not compute the summary.',
    'filters.summary.empty': 'No results with the current filters.',
    'filters.summary.total': 'Postings (total)',
    'filters.summary.monthly': 'Postings with a publication month',
    'filters.summary.skillDemand': 'Postings with the “{skill}” skill',
    'kpi.totalJobOffers': 'Total Job Offers',
    'kpi.companiesHiring': 'Companies Hiring',
    'kpi.countriesHiring': 'Countries Hiring',
    'kpi.citiesHiring': 'Cities Hiring',
    'kpi.offersLast7Days': 'Offers Last 7 Days',
    'kpi.offersLast30Days': 'Offers Last 30 Days',
    'kpi.remoteShare': 'Remote Share',
    'kpi.hybridShare': 'Hybrid Share',
    'kpi.onSiteShare': 'On-site Share',
    'kpi.remoteFriendlyShare': 'Remote-Friendly Share',
    'kpi.salaryDisclosureRate': 'Salary Disclosure Rate',
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

/**
 * Localised integer/quantity with thousands separators in both languages
 * (`22.973` / `22,973`). `useGrouping: true` forces grouping of 4-digit
 * Spanish counts (`9.100`), which `es-ES` would otherwise leave as `9100`,
 * keeping KPI cards coherent with the Power BI report.
 */
export function formatCount(value, language) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? '');
  return new Intl.NumberFormat(localeTag(language), { useGrouping: true }).format(number);
}

/** Localised annual EUR amount without decimals (`30.000 €` / `€30,000`). */
export function formatSalary(value, language) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? '');
  return new Intl.NumberFormat(localeTag(language), {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(number);
}

/**
 * Localised percentage with exactly one decimal from a 0–1 ratio
 * (`33,5 %` in Spanish, `33.5%` in English). Null/blank passes through empty.
 */
export function formatPercent(value, language) {
  if (value == null || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat(localeTag(language), {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(number);
}

/**
 * Localised short month for a `YYYY-MM` key (`abr 2026` / `Apr 2026`).
 * Anything that does not match the `YYYY-MM` shape is returned as-is so
 * malformed data stays visible instead of becoming an invented date.
 */
export function formatMonth(value, language) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value ?? '').trim());
  if (!match) return String(value ?? '');
  const month = Number(match[2]);
  if (month < 1 || month > 12) return String(value);
  const date = new Date(Date.UTC(Number(match[1]), month - 1, 1));
  return new Intl.DateTimeFormat(localeTag(language), {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

/**
 * Applies the dictionary to the static shell: `<html lang>`, `[data-i18n]`
 * text content and `[data-i18n-aria-label]` / `[data-i18n-title]` /
 * `[data-i18n-placeholder]` attributes.
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
  doc.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
    element.setAttribute('placeholder', translate(language, element.getAttribute('data-i18n-placeholder')));
  });
}
