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
    'view.salaryInsights.avgByRole.title': 'Salario medio por categoría de rol',
    'view.salaryInsights.avgByRole.caption':
      'Media del salario mid anual en EUR de las ofertas con salario válido, por categoría de rol.',
    'view.salaryInsights.avgByWorkMode.title': 'Salario medio por modalidad',
    'view.salaryInsights.avgByWorkMode.caption':
      'Media del salario mid anual en EUR de las ofertas con salario válido, por modalidad de trabajo.',
    'view.salaryInsights.topSkills.title': 'Skills mejor pagadas',
    'view.salaryInsights.topSkills.caption':
      'Top 15 skills por mediana del salario mid (mínimo 5 ofertas). La demanda cuenta todas las ofertas de cada skill.',
    'view.salaryInsights.topSkills.skill': 'Skill',
    'view.salaryInsights.topSkills.category': 'Categoría',
    'view.salaryInsights.topSkills.medianSalary': 'Salario mediano (EUR)',
    'view.salaryInsights.topSkills.demand': 'Demanda (ofertas)',
    'view.salaryInsights.map.title': 'Ofertas por país',
    'view.salaryInsights.map.caption':
      'Ofertas por país (GeoCountry). Los países en gris claro no tienen ofertas con los filtros actuales; pasa el cursor por encima para ver el detalle.',
    'view.salaryInsights.map.tooltip': '{country}: {offers} ofertas',
    'view.salaryInsights.map.noDataTooltip': '{country}: sin datos con los filtros actuales',
    'view.salaryInsights.map.more': 'Más',
    'view.salaryInsights.map.less': 'Menos',
    'view.salaryInsights.map.outsideCaption':
      'Valores fuera del mapa europeo, sin geometría en el GeoJSON; se listan para no perder información:',
    'view.salaryInsights.map.outsideCountry': 'País o etiqueta',
    'view.salaryInsights.map.outsideOffers': 'Ofertas',
    'view.salaryInsights.mapUnavailable':
      'No se pudo cargar el GeoJSON del mapa europeo; el resto de la vista sigue disponible.',
    'view.salaryInsights.noData': 'Sin datos para esta vista con los filtros actuales.',
    'view.salaryInsights.notAvailable': 'N/D',
    'view.salaryInsights.chartsUnavailable':
      'Los gráficos y el mapa no están disponibles porque ECharts no se pudo cargar desde el CDN; la tabla de skills sigue calculada con los datos cargados.',
    'view.opportunityExplorer.search.label': 'Buscar ofertas',
    'view.opportunityExplorer.search.placeholder': 'Busca por título o empresa…',
    'view.opportunityExplorer.search.clear': 'Limpiar búsqueda',
    'view.opportunityExplorer.search.active': 'Búsqueda activa: «{term}»',
    'view.opportunityExplorer.loading': 'Cargando ofertas…',
    'view.opportunityExplorer.count': 'Mostrando {from}–{to} de {total}',
    'view.opportunityExplorer.noData':
      'No hay ofertas que coincidan con la búsqueda o los filtros actuales.',
    'view.opportunityExplorer.openLink':
      'Abrir la oferta original: {title} (se abre en otra pestaña)',
    'view.opportunityExplorer.notAvailable': 'N/D',
    'view.opportunityExplorer.table.caption':
      'Ofertas individuales con campos públicos (sin descripciones ni columnas internas). La búsqueda revisa título y empresa.',
    'view.opportunityExplorer.table.jobTitle': 'Título',
    'view.opportunityExplorer.table.company': 'Empresa',
    'view.opportunityExplorer.table.location': 'Ubicación',
    'view.opportunityExplorer.table.workMode': 'Modalidad',
    'view.opportunityExplorer.table.role': 'Rol',
    'view.opportunityExplorer.table.medianSalary': 'Salario mediano anual',
    'view.opportunityExplorer.table.postedDate': 'Fecha',
    'view.opportunityExplorer.table.source': 'Fuente',
    'view.opportunityExplorer.pagination.label': 'Paginación de la tabla de ofertas',
    'view.opportunityExplorer.pagination.previous': 'Anterior',
    'view.opportunityExplorer.pagination.next': 'Siguiente',
    'view.opportunityExplorer.pagination.page': 'Página {page} de {pages}',
    'view.methodology.sources.title': 'Fuentes de datos',
    'view.methodology.sources.lead':
      'Los datos proceden de ofertas de empleo publicadas en portales y agregadores, recopiladas a diario por scrapers propios. Cada oferta conserva la fuente de la que se descargó (campo source_scraper).',
    'view.methodology.sources.caption': 'Ofertas por fuente en el export publicado.',
    'view.methodology.sources.source': 'Fuente',
    'view.methodology.sources.postings': 'Ofertas',
    'view.methodology.sources.total': 'Total',
    'view.methodology.sources.rawNote':
      'Las ofertas se muestran tal cual llegan de los portales, sin traducir: títulos, empresas y ubicaciones conservan el idioma original (normalmente inglés).',
    'view.methodology.pipeline.title': 'Traza del pipeline',
    'view.methodology.pipeline.lead':
      'Así viaja cada oferta desde el portal hasta tu navegador:',
    'view.methodology.pipeline.step1':
      'Cada noche, los scrapers recorren los portales de empleo y descargan las ofertas publicadas.',
    'view.methodology.pipeline.step2':
      'Las ofertas crudas se guardan en Azure Data Lake Storage (ADLS), la zona de aterrizaje del proyecto, sin transformar.',
    'view.methodology.pipeline.step3':
      'Databricks limpia, normaliza y modela los datos en las tablas Delta de la capa Gold, la misma fuente que usa el informe de Power BI.',
    'view.methodology.pipeline.step4':
      'Una tarea final del job genera el export Parquet y un fichero meta.json con la frescura, los recuentos y la versión del export.',
    'view.methodology.pipeline.step5':
      'GitHub Actions descarga ese export cada día con una credencial de solo lectura.',
    'view.methodology.pipeline.step6':
      'El sitio se despliega como web estática en GitHub Pages; los datos no se guardan en el repositorio.',
    'view.methodology.pipeline.step7':
      'Tu navegador descarga el export y ejecuta las consultas en local con DuckDB-WASM: no hay servidor y tus consultas no salen de tu equipo.',
    'view.methodology.pipeline.note':
      'El sitio solo conoce el contrato de ficheros del export; no depende de Databricks ni de Power BI para funcionar.',
    'view.methodology.salary.title': 'Normalización salarial',
    'view.methodology.salary.lead':
      'Las ofertas llegan con formatos, monedas y periodicidades distintos. El pipeline las normaliza a valores anuales en euros:',
    'view.methodology.salary.point1':
      'Salario mínimo y máximo anualizados (SalaryMinAnnual_EUR y SalaryMaxAnnual_EUR), con las conversiones de moneda y periodo que correspondan.',
    'view.methodology.salary.point2':
      'El dashboard usa el punto medio del rango (SalaryMidAnnual_EUR): es la base de la media, la mediana y los percentiles.',
    'view.methodology.salary.point3':
      'Solo cuentan los salarios mayores que cero, menores o iguales a 1.000.000 € y con mínimo ≤ máximo; el resto se descarta por no ser fiable.',
    'view.methodology.salary.point4':
      'La tasa de salario publicado mide cuántas ofertas del contexto traen un salario válido.',
    'view.methodology.salary.point5':
      'Las ofertas sin salario quedan fuera de las métricas salariales, igual que en el modelo de Power BI; la tasa de publicación lo refleja.',
    'view.methodology.dates.title': 'Procedencia de fechas',
    'view.methodology.dates.lead':
      'Las fechas del dashboard proceden de la fecha de publicación de cada oferta:',
    'view.methodology.dates.point1':
      'posted_date es la fecha en la que la oferta se publicó en su portal.',
    'view.methodology.dates.point2':
      'No todas las ofertas traen una fecha fiable: el pipeline conserva un indicador interno de validez (IsValidPostingDate) para auditar esta limitación.',
    'view.methodology.dates.point3':
      'La frescura usa la fecha de publicación máxima del export; es la fecha de datos que aparece en la cabecera.',
    'view.methodology.freshness.title': 'Frescura del export',
    'view.methodology.freshness.lead':
      'Todo lo que sigue viene del fichero meta.json que acompaña al export, no de cálculos de la web:',
    'view.methodology.freshness.dataDate': 'Fecha de datos',
    'view.methodology.freshness.generatedAt': 'Export generado',
    'view.methodology.freshness.mode': 'Modo',
    'view.methodology.freshness.modeFull':
      'Export completo: las vistas consultan todas las tablas publicadas.',
    'view.methodology.freshness.modeAggregated':
      'Export con agregados: para mantener el sitio ligero, las vistas usan tablas agregadas y el explorador queda limitado a un alcance documentado.',
    'view.methodology.freshness.exportVersion': 'Versión del export',
    'view.methodology.freshness.size': 'Tamaño total',
    'view.methodology.freshness.tablesTitle': 'Recuento por tabla',
    'view.methodology.freshness.sourcesTitle': 'Recuento por fuente',
    'view.methodology.metaMissing':
      'No se pudo cargar meta.json: la fecha de datos, la versión, el tamaño y los recuentos del export no están disponibles ahora mismo. El resto de la metodología sigue visible.',
    'view.methodology.limitations.title': 'Limitaciones conocidas',
    'view.methodology.limitations.lead':
      'Estas cifras son una foto realista, no una verdad absoluta; conviene leerlas con estas cautelas:',
    'view.methodology.limitations.point1':
      'El dashboard refleja solo las ofertas publicadas en los portales seguidos, no todo el mercado laboral.',
    'view.methodology.limitations.point2':
      'Solo se muestran skills del catálogo del proyecto; las skills de texto libre que no entran en el catálogo quedan fuera de las vistas, igual que en el informe.',
    'view.methodology.limitations.point3':
      'Las cifras del sitio pueden no coincidir con capturas del informe Power BI de otra fecha: los datos se rellenan hacia atrás y se corrigen con el tiempo.',
    'view.methodology.limitations.point4':
      'El filtro de país usa GeoCountry, un campo normalizado del pipeline; es una aproximación al mapeo geográfico del informe, que agrupa por región.',
    'view.methodology.limitations.point5':
      'Las medianas de skills con pocas ofertas con salario pueden ser inestables y moverse entre actualizaciones.',
    'view.methodology.limitations.point6':
      'La tendencia mensual agrupa por mes de publicación; el informe Power BI pinta la evolución por día.',
    'view.methodology.limitations.detail':
      'El detalle completo de estas diferencias (fechas, muestras pequeñas y aproximaciones de texto) está documentado en la cabecera del módulo de consultas (queries.js) del proyecto.',
    'view.methodology.legal.title': 'Aviso legal',
    'view.methodology.legal.point1':
      'El dashboard muestra ofertas de empleo públicas recopiladas de portales y agregadores de empleo.',
    'view.methodology.legal.point2':
      'Solo se publican campos públicos de cada oferta: no hay descripciones completas ni datos personales.',
    'view.methodology.legal.point3':
      'Cada fila enlaza a la oferta original; la oferta puede haber caducado, pero era real en su fecha.',
    'view.methodology.legal.point4':
      'Los datos se ofrecen sin garantía de exactitud ni de disponibilidad; úsalos como orientación.',
    'view.methodology.legal.point5':
      'Las marcas y portales mencionados pertenecen a sus titulares; este proyecto no está afiliado a ellos.',
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
    'kpi.avgMidSalary': 'Salario medio (mid)',
    'kpi.medianMidSalary': 'Salario mediano (mid)',
    'kpi.p25MidSalary': 'P25 salarial (mid)',
    'kpi.p75MidSalary': 'P75 salarial (mid)',
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
    'view.salaryInsights.avgByRole.title': 'Average Salary by Role Category',
    'view.salaryInsights.avgByRole.caption':
      'Average annual mid salary in EUR of postings with a valid salary, by role category.',
    'view.salaryInsights.avgByWorkMode.title': 'Average Salary by Work Mode',
    'view.salaryInsights.avgByWorkMode.caption':
      'Average annual mid salary in EUR of postings with a valid salary, by work mode.',
    'view.salaryInsights.topSkills.title': 'Top Paying Skills',
    'view.salaryInsights.topSkills.caption':
      'Top 15 skills by median mid salary (at least 5 postings). Demand counts every posting of each skill.',
    'view.salaryInsights.topSkills.skill': 'Skill',
    'view.salaryInsights.topSkills.category': 'Category',
    'view.salaryInsights.topSkills.medianSalary': 'Median salary (EUR)',
    'view.salaryInsights.topSkills.demand': 'Demand (postings)',
    'view.salaryInsights.map.title': 'Postings by Country',
    'view.salaryInsights.map.caption':
      'Postings per country (GeoCountry). Light grey countries have no postings under the current filters; hover for details.',
    'view.salaryInsights.map.tooltip': '{country}: {offers} postings',
    'view.salaryInsights.map.noDataTooltip': '{country}: no data with the current filters',
    'view.salaryInsights.map.more': 'More',
    'view.salaryInsights.map.less': 'Fewer',
    'view.salaryInsights.map.outsideCaption':
      'Values outside the European map (no geometry in the GeoJSON), listed so no information is lost:',
    'view.salaryInsights.map.outsideCountry': 'Country or label',
    'view.salaryInsights.map.outsideOffers': 'Postings',
    'view.salaryInsights.mapUnavailable':
      'The European map GeoJSON could not be loaded; the rest of the view keeps working.',
    'view.salaryInsights.noData': 'No data for this view with the current filters.',
    'view.salaryInsights.notAvailable': 'N/A',
    'view.salaryInsights.chartsUnavailable':
      'Charts and the map are unavailable because ECharts could not be loaded from the CDN; the skills table is still computed from the loaded data.',
    'view.opportunityExplorer.search.label': 'Search postings',
    'view.opportunityExplorer.search.placeholder': 'Search by job title or company…',
    'view.opportunityExplorer.search.clear': 'Clear search',
    'view.opportunityExplorer.search.active': 'Active search: “{term}”',
    'view.opportunityExplorer.loading': 'Loading postings…',
    'view.opportunityExplorer.count': 'Showing {from}–{to} of {total}',
    'view.opportunityExplorer.noData':
      'No postings match the current search or filters.',
    'view.opportunityExplorer.openLink':
      'Open the original posting: {title} (opens in a new tab)',
    'view.opportunityExplorer.notAvailable': 'N/A',
    'view.opportunityExplorer.table.caption':
      'Individual postings with public fields (no descriptions or internal columns). The search looks at job title and company.',
    'view.opportunityExplorer.table.jobTitle': 'Job title',
    'view.opportunityExplorer.table.company': 'Company',
    'view.opportunityExplorer.table.location': 'Location',
    'view.opportunityExplorer.table.workMode': 'Work mode',
    'view.opportunityExplorer.table.role': 'Role category',
    'view.opportunityExplorer.table.medianSalary': 'Annual median salary',
    'view.opportunityExplorer.table.postedDate': 'Posted date',
    'view.opportunityExplorer.table.source': 'Source',
    'view.opportunityExplorer.pagination.label': 'Job postings table pagination',
    'view.opportunityExplorer.pagination.previous': 'Previous',
    'view.opportunityExplorer.pagination.next': 'Next',
    'view.opportunityExplorer.pagination.page': 'Page {page} of {pages}',
    'view.methodology.sources.title': 'Data sources',
    'view.methodology.sources.lead':
      'The data comes from job postings published on job boards and aggregators, collected daily by our own scrapers. Every posting keeps the source it was downloaded from (source_scraper column).',
    'view.methodology.sources.caption': 'Postings per source in the published export.',
    'view.methodology.sources.source': 'Source',
    'view.methodology.sources.postings': 'Postings',
    'view.methodology.sources.total': 'Total',
    'view.methodology.sources.rawNote':
      'Postings are shown exactly as they arrive from the portals, without translation: titles, companies and locations keep their original language (usually English).',
    'view.methodology.pipeline.title': 'Pipeline trace',
    'view.methodology.pipeline.lead':
      'This is how every posting travels from the portal to your browser:',
    'view.methodology.pipeline.step1':
      'Every night the scrapers crawl the job boards and download the published postings.',
    'view.methodology.pipeline.step2':
      'The raw postings are stored untransformed in Azure Data Lake Storage (ADLS), the project landing zone.',
    'view.methodology.pipeline.step3':
      'Databricks cleans, normalises and models the data into the Delta tables of the Gold layer, the same source used by the Power BI report.',
    'view.methodology.pipeline.step4':
      'A final job task produces the Parquet export and a meta.json file with freshness, row counts and the export version.',
    'view.methodology.pipeline.step5':
      'GitHub Actions downloads that export every day with a read-only credential.',
    'view.methodology.pipeline.step6':
      'The site is deployed as a static website on GitHub Pages; the data is never stored in the repository.',
    'view.methodology.pipeline.step7':
      'Your browser downloads the export and runs the queries locally with DuckDB-WASM: there is no server and your queries never leave your device.',
    'view.methodology.pipeline.note':
      'The site only knows the export file contract; it does not depend on Databricks or Power BI to work.',
    'view.methodology.salary.title': 'Salary normalisation',
    'view.methodology.salary.lead':
      'Postings arrive in different formats, currencies and periods. The pipeline normalises them into annual values in euros:',
    'view.methodology.salary.point1':
      'Annualised minimum and maximum salary (SalaryMinAnnual_EUR and SalaryMaxAnnual_EUR), with the applicable currency and period conversions.',
    'view.methodology.salary.point2':
      'The dashboard uses the midpoint of the range (SalaryMidAnnual_EUR): it is the base of the average, median and percentiles.',
    'view.methodology.salary.point3':
      'Only salaries above zero, at or below €1,000,000 and with minimum ≤ maximum count; the rest are discarded as unreliable.',
    'view.methodology.salary.point4':
      'The salary disclosure rate measures how many postings in the context carry a valid salary.',
    'view.methodology.salary.point5':
      'Postings without a salary stay out of the salary metrics, exactly as in the Power BI model; the disclosure rate reflects it.',
    'view.methodology.dates.title': 'Date provenance',
    'view.methodology.dates.lead':
      'The dashboard dates come from each posting publication date:',
    'view.methodology.dates.point1':
      'posted_date is the date the posting was published on its portal.',
    'view.methodology.dates.point2':
      'Not every posting carries a reliable date: the pipeline keeps an internal validity indicator (IsValidPostingDate) to audit this limitation.',
    'view.methodology.dates.point3':
      'Freshness uses the export maximum publication date; it is the data date shown in the header.',
    'view.methodology.freshness.title': 'Export freshness',
    'view.methodology.freshness.lead':
      'Everything below comes from the meta.json file shipped with the export, not from calculations in the web:',
    'view.methodology.freshness.dataDate': 'Data date',
    'view.methodology.freshness.generatedAt': 'Export generated',
    'view.methodology.freshness.mode': 'Mode',
    'view.methodology.freshness.modeFull':
      'Full export: the views query every published table.',
    'view.methodology.freshness.modeAggregated':
      'Aggregated export: to keep the site light, the views use aggregate tables and the explorer is limited to a documented scope.',
    'view.methodology.freshness.exportVersion': 'Export version',
    'view.methodology.freshness.size': 'Total size',
    'view.methodology.freshness.tablesTitle': 'Row count by table',
    'view.methodology.freshness.sourcesTitle': 'Row count by source',
    'view.methodology.metaMissing':
      'Could not load meta.json: the data date, version, size and export row counts are unavailable right now. The rest of the methodology stays visible.',
    'view.methodology.limitations.title': 'Known limitations',
    'view.methodology.limitations.lead':
      'These figures are a realistic snapshot, not absolute truth; read them with these caveats:',
    'view.methodology.limitations.point1':
      'The dashboard reflects only the postings published on the tracked job boards, not the whole job market.',
    'view.methodology.limitations.point2':
      'Only skills from the project catalogue are shown; free-text skills outside the catalogue stay out of the views, exactly as in the report.',
    'view.methodology.limitations.point3':
      'Figures on the site may not match Power BI report captures from another date: data is backfilled and corrected over time.',
    'view.methodology.limitations.point4':
      'The country filter uses GeoCountry, a normalised pipeline column; it approximates the report geographical mapping, which groups by region.',
    'view.methodology.limitations.point5':
      'Medians for skills with few salaried postings can be unstable and move between refreshes.',
    'view.methodology.limitations.point6':
      'The monthly trend groups by publication month; the Power BI report plots the day-by-day evolution.',
    'view.methodology.limitations.detail':
      'The full detail of these differences (dates, small samples and text approximations) is documented in the header of the project queries module (queries.js).',
    'view.methodology.legal.title': 'Legal notice',
    'view.methodology.legal.point1':
      'The dashboard shows public job postings collected from job boards and aggregators.',
    'view.methodology.legal.point2':
      'Only public fields of each posting are published: no full descriptions and no personal data.',
    'view.methodology.legal.point3':
      'Every row links to the original posting; it may have expired, but it was real on its date.',
    'view.methodology.legal.point4':
      'The data is provided with no guarantee of accuracy or availability; use it as guidance.',
    'view.methodology.legal.point5':
      'The brands and portals mentioned belong to their owners; this project is not affiliated with them.',
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
    'kpi.avgMidSalary': 'Avg Mid Salary EUR',
    'kpi.medianMidSalary': 'Median Mid Salary EUR',
    'kpi.p25MidSalary': 'P25 Mid Salary EUR',
    'kpi.p75MidSalary': 'P75 Mid Salary EUR',
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

/**
 * Null/blank values never become a date: `new Date(null)` is the Unix epoch,
 * which used to format as "1 January 1970" (T-21).
 */
const toDate = (value) => {
  if (value == null || value === '') return null;
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
  if (value == null || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat(localeTag(language), { useGrouping: true }).format(number);
}

/**
 * Localised size of a byte count with one decimal (`2,9 MB` / `2.9 MB`).
 * Values below 1 MB keep two decimals; null/blank returns empty (it used to
 * invent "0.0 MB") and non-finite input passes through as text (T-21).
 */
export function formatBytes(value, language) {
  if (value == null || value === '') return '';
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return String(value);
  const megabytes = bytes / (1024 * 1024);
  const digits = megabytes < 1 && megabytes > 0 ? 2 : 1;
  const number = new Intl.NumberFormat(localeTag(language), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(megabytes);
  return `${number} MB`;
}

/** Localised annual EUR amount without decimals (`30.000 €` / `€30,000`). */
export function formatSalary(value, language) {
  if (value == null || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
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
