# Tareas: dashboard web público del portfolio

Las tareas están ordenadas por dependencia y dimensionadas para una sesión de
aproximadamente 15–25 minutos. Ninguna se marca como hecha sin ejecutar su
comprobación. El trabajo vive en el worktree de la rama
`spec/006-public-web-dashboard`, creado desde `main` (paralelo a la 004).

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación
  (tests en verde), con mensaje convencional y referencia a la tarea
  (p. ej. `feat(web-dashboard): T-02 geografia portada a funciones puras`).
- Antes del commit, el verificador revisa el diff de la tarea contra esta spec y
  el plan; no se commitea con hallazgos `BLOQUEANTE`.
- El commit local es un checkpoint; no se hace push sin revisión previa de la
  persona.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`).
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec antes de
  continuar.
- Comando de comprobación del export:
  `python -m pytest databricks_notebooks/tests -q` (desde la raíz del
  repositorio). En el cierre se añade `python -m pytest scrapers-pipeline/tests -q`.

## 1. Preparación (paralelo a la 004)

- [x] **T-01 — Crear rama/worktree y baseline en verde** (~20 min)
  - **RF:** RF-12.
  - **Hecho cuando:** existe `..\talent-market-lens-006` como worktree de
    `spec/006-public-web-dashboard` creada desde `main` (sin la 004), con
    `specs/006-Public-Web-Dashboard/{spec.md,plan.md,tasks.md}` versionados en
    la rama nueva; `python -m pytest databricks_notebooks/tests -q` y
    `python -m pytest scrapers-pipeline/tests -q` pasan como línea base; nada
    del trabajo en curso de la 004 (ramas, commits o ficheros locales) se ha
    llevado al worktree.

## 2. Export de datos (Databricks)

- [x] **T-02 — Portar la geografía a funciones puras** (~25 min)
  - **RF:** RF-2.
  - **Hecho cuando:** `Prepare_Web_Export.py` define `REGION_MAP` (constante de
    `Dim_RegionMap`), `geo_country(value)` y `geo_region(country, region)` con
    la normalización M del modelo (`CA/FL/PA` → United States, `Alemania` →
    Germany, `Austria y Suiza`/`Austria and Switzerland` → Austria, `Oriente
    Medio y África` → Middle East & Africa, vacío → `(Not specified)`,
    fallback `(Other)`), y `test_web_export.py` cubre cada caso, incluidos
    trims, mayúsculas y ciudades conocidas; sin Spark en estos tests.

- [x] **T-03 — Fijar la proyección y el contrato de tablas** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-02.
  - **Hecho cuando:** existen las constantes de columnas del contrato (§5.1 del
    plan) y las funciones de proyección de `fact_offers` (con
    `GeoCountry`/`GeoRegion`), `fact_offer_skills`, `dim_skill_list` y
    `dim_calendar`; un test comprueba las columnas exactas y rechaza las
    excluidas (`description_clean`, `skills`, `salary_quality`,
    `skills_source`, `experience_level_source`, `posted_date_raw`,
    `posted_date_source`, `salary_currency`, `salary_period`).

- [x] **T-04 — Construir `meta.json` y el control de tamaño** (~20 min)
  - **RF:** RF-3, RF-4.
  - **Depende de:** T-03.
  - **Hecho cuando:** `build_meta` produce el JSON del contrato (§5.4:
    `export_version`, `data_date`, `generated_at`, `mode`, `tables`, `sources`,
    `size_bytes`) y `export_exceeds_limit` decide `full`/`aggregated` contra un
    umbral configurable (~25 MB por defecto); tests de claves, recuentos, fecha
    de datos y de los tres tramos del umbral.

- [x] **T-05 — Implementar el build y la escritura idempotente** (~25 min)
  - **RF:** RF-1, RF-2, RF-3.
  - **Depende de:** T-02–T-04.
  - **Hecho cuando:** `build_web_export(spark, fact_offers, fact_offer_skills,
    dim_skill_list, dim_calendar)` proyecta las tablas Gold y
    `collect_export_stats` produce los recuentos y la fecha de datos;
    `write_web_export(spark, tables, dest, meta=...)` escribe un solo fichero
    Parquet por tabla (`overwrite`), calcula el tamaño, deriva el modo y escribe
    `meta.json`; los tests de integración Spark (patrón
    `test_integration_spark.py`) escriben en un directorio temporal y verifican
    tablas, tipos, recuentos, fichero único por tabla y reejecución sin
    duplicados.

- [x] **T-06 — Notebook fino, tarea al final del job y ejecución real** (~25 min)
  - **RF:** RF-1, RF-3, RF-4.
  - **Depende de:** T-05.
  - **Hecho cuando:** `web_export_build.ipynb` resuelve widgets
    (`storage_account`, `container`, `catalog`, `gold_schema`,
    `export_prefix`) y llama al módulo sin lógica propia; la tarea se añade al
    final del job de Databricks (idempotente, sin alterar las existentes), se
    ejecuta de verdad y queda registrado el tamaño del export y la decisión
    `full`/`aggregated`; si hay que añadir la tarea al job, se documenta el paso
    manual.
  - **Evidencia:** ruta del export, tamaños por fichero y `meta.json` resultante.

## 3. Datos y consultas de la web

- [x] **T-07 — Portar las ~18 medidas a `queries.js`** (~25 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-06.
  - **Hecho cuando:** `queries.js` implementa las 18 medidas de §7 del plan con
    los filtros de calidad, ventanas y cuotas del modelo, cada una con su
    medida DAX de origen documentada; sobre la instantánea local (T-16 puede
    adelantarse con una descarga manual) los valores ejecutados coinciden con
    el informe o sus capturas, y las diferencias conocidas quedan anotadas.

- [x] **T-08 — Shell, carga con DuckDB-WASM e i18n** (~25 min)
  - **RF:** RF-3, RF-6, RF-14, RF-16.
  - **Depende de:** T-06.
  - **Hecho cuando:** `docs/dashboard/index.html`, `styles.css` y `app.js`
    cargan las versiones fijadas de DuckDB-WASM y ECharts por CDN, descargan
    los Parquet y `meta.json` con `registerFileBuffer`, crean las vistas y
    muestran la vista activa con la frescura visible; si falta un fichero, el
    WASM no está soportado o el CDN falla, aparece un aviso claro (nunca en
    blanco); `i18n.js` define el diccionario español/inglés, el selector visible
    y la preferencia recordada, y todas las vistas consumen sus textos;
    comprobado en local con la instantánea.

- [x] **T-09 — Filtros globales coherentes** (~25 min)
  - **RF:** RF-9.
  - **Depende de:** T-08.
  - **Hecho cuando:** el estado de filtros (país, experiencia, rol, modalidad,
    empresa, skill, fuente, rango salarial) se compone en un `WHERE` común que
    todas las consultas respetan, se muestran los filtros activos, se pueden
    limpiar y los valores de una vista cambian de forma coherente al filtrar.

## 4. Vistas

- [x] **T-10 — Vista Market Pulse** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra los KPIs 1–11, la tendencia por
    `PostedYearMonth`, top roles, top empresas y reparto por experiencia, con
    formato legible, textos ES/EN y verificada en local contra el
    informe/capturas.

- [x] **T-11 — Vista Roles & Skills** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra demanda y cuota por skill, salario
    mediano por skill, burbuja demanda vs. salario, donut por categoría y tabla
    benchmark, coherentes con el modelo, resistentes a skills sin salario y con
    textos ES/EN.

- [x] **T-12 — Vista Salary Insights** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra media, mediana, P25 y P75, medias por
    categoría de rol y modalidad, top paying skills y el mapa coroplético por
    país con el GeoJSON versionado; los países sin dato se distinguen, con
    textos ES/EN y la vista funciona en móvil.

- [x] **T-13 — Vista Opportunity Explorer** (~25 min)
  - **RF:** RF-7, RF-10, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la tabla permite buscar y filtrar, muestra los campos
    públicos (título, empresa, ubicación, modalidad, rol, salario mediano,
    fecha, fuente), abre la oferta original en otra pestaña, pagina o limita el
    volumen, no incluye descripciones ni columnas internas y sus textos salen
    del diccionario ES/EN.

- [x] **T-14 — Vista Metodología** (~20 min)
  - **RF:** RF-7, RF-13, RF-16.
  - **Depende de:** T-08.
  - **Hecho cuando:** la vista explica fuentes, traza del pipeline,
    normalización salarial, procedencia de fechas, frescura (fecha de datos y
    recuento por fuente desde `meta.json`), limitaciones conocidas y aviso
    legal, en español e inglés.

## 4.1 Refinamiento visual y de UX (revisión de la persona, 2026-10-11)

Se ejecuta tras T-14 y antes de la sección 5. La persona revisó las vistas y
pidió: estética más cuidada y compacta, filtros colapsables, los KPIs del
informe original en Market Pulse (tendencia a 6 meses), el gráfico de mediana
por categoría en Roles & Skills, el selector de país con mapa por regiones en
Salary Insights y tooltips siempre dentro de la pantalla. El código y la
spec/plan se actualizan junto con estas tareas.

- [x] **T-21 — Rediseño visual y tooltips contenidos** (~30 min)
  - **RF:** RF-7, RF-16.
  - **Depende de:** T-10–T-14.
  - **Hecho cuando:** el sitio usa un sistema visual más pulido y compacto
    (paleta, tipografía, tarjetas, sombras y grids revisados; gráficos y KPIs
    más pequeños, legibles de un vistazo), los tooltips de todos los gráficos
    quedan dentro del área visible (`confine`), la burbuja de Roles & Skills
    no solapa el nombre del eje Y con su escala y los hallazgos MENOR del
    grupo anterior quedan resueltos; las 5 vistas se revisan en local (ES/EN,
    escritorio y móvil) sin regresiones y con las suites en verde.

- [x] **T-22 — Filtros colapsables y retirada del resumen provisional** (~20 min)
  - **RF:** RF-9.
  - **Depende de:** T-21.
  - **Hecho cuando:** el panel de filtros se oculta tras un botón visible
    (con un texto breve que explica su uso), los filtros activos y su limpieza
    siguen accesibles con el panel cerrado, y el bloque provisional de
    «Resumen con los filtros actuales» (T-09) se retira; comprobado en local
    en ES/EN y móvil.

- [x] **T-23 — Market Pulse: KPIs del informe y tendencia a 6 meses** (~20 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-21.
  - **Hecho cuando:** Market Pulse muestra los 4 KPIs del informe original
    (ofertas únicas, empresas contratando, cuota remoto o híbrido y tasa de
    publicación de salario), la tendencia mensual se limita a los últimos 6
    meses del contexto y los gráficos quedan compactos y a la vista;
    cotejado en local con la captura del informe.

- [x] **T-24 — Roles & Skills: mediana por categoría y burbuja pulida** (~20 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-21.
  - **Hecho cuando:** la vista añade el gráfico de salario mediano por
    categoría de skill (agregación directa con el filtro de calidad salarial),
    coherente con la captura del informe, y la burbuja mantiene tooltips
    contenidos y etiquetas legibles; ES/EN.

- [x] **T-25 — Salary Insights: selector de país y mapa por regiones** (~35 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-21.
  - **Hecho cuando:** sobre el mapa aparecen los botones de país del informe
    (Europa, Irlanda, Países Bajos, España, Suiza y Estados Unidos); al elegir
    un país la vista se filtra y el mapa se centra en sus regiones (ofertas
    por `GeoRegion` con el `regions.geojson` versionado), al elegir Europa se
    ve el continente por países, los botones activos se distinguen, las
    regiones sin dato quedan diferenciadas y el resultado se comprueba en
    local en ES/EN y móvil.

## 4.2 Refinamiento visual, segunda iteración (revisión de la persona, 2026-10-12)

Tras revisar el grupo 4.1 (ya en la rama), la persona confirma que los cambios
pedidos están, pero el resultado visual todavía no le convence: el diseño sigue
pareciendo básico y grande. Pide una pasada más ambiciosa antes de la portada y
los READMEs (T-15). Objetivo: subir un escalón la identidad y la densidad
(tarjetas con jerarquía, gráficos con tema propio, todo más compacto y legible
de un vistazo), manteniendo intactas las funciones, los filtros, la
accesibilidad, los tooltips contenidos y el eje Y corregido, y sin dependencias
nuevas. Las capturas de referencia «antes» viven en
`.opencode/.agent-screenshots/20261012-121*` (desktop, ES).

- [x] **T-26 — Sistema visual v2 del shell** (~30 min)
  - **RF:** RF-7, RF-9, RF-16.
  - **Depende de:** T-21–T-25.
  - **Hecho cuando:** `styles.css` revisa los tokens (paleta con acento
    secundario usado con moderación, escala tipográfica con base ≤ 0.9rem,
    espaciados, radios y sombras) y la franja superior (cabecera, navegación,
    barra de estado y panel de filtros colapsado) no supera ~200 px de alto a
    1440×900; las tarjetas KPI ganan jerarquía (acento o icono, valor ≤ 1.5rem,
    altura ≤ 4.5rem) sin perder contraste AA, foco visible ni
    `prefers-reduced-motion`; capturas antes/después en ES y EN; sin cambios de
    comportamiento (filtros, vistas, i18n).

- [x] **T-27 — Tema ECharts común y acabado de los gráficos** (~30 min)
  - **RF:** RF-7, RF-16.
  - **Depende de:** T-26.
  - **Hecho cuando:** `charts.js` aplica un tema compartido (paleta, tipografía,
    ejes, splitLine suave, tooltip) a línea, barras, donut, burbuja y mapa;
    barras con esquinas redondeadas, línea con área sutil y donut sin etiquetas
    truncadas ni categorías al 0,0 %; la última etiqueta del eje X de la
    tendencia («oct 2026») ya no se corta; la leyenda y el eje de la burbuja
    siguen sin solapes; los tooltips contenidos y su envoltura en móvil se
    mantienen; 5 vistas revisadas en ES/EN.

- [x] **T-28 — Market Pulse de un vistazo** (~25 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-27.
  - **Hecho cuando:** a 1440×900 y con los filtros colapsados, los 4 KPIs del
    informe, la tendencia a 6 meses y los tres rankings se ven completos sin
    scroll, o con un scroll residual menor de media pantalla documentado con la
    medición de `agent-browser`; las alturas de los gráficos quedan reducidas y
    anotadas (p. ej., tendencia ≤ 11rem y rankings ≤ 10.5rem a ≥ 64rem); móvil
    (≤ 640 px) sin regresiones; cifras cotejadas con la captura del informe.

- [x] **T-29 — Roles & Skills y Salary Insights sin huecos ni cortes** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-27.
  - **Hecho cuando:** en Roles & Skills la burbuja (leyenda/eje legibles) y el
    donut (sin etiquetas cortadas) quedan pulidos y la mediana por categoría y
    la tabla se mantienen compactas; en Salary Insights ninguna tarjeta queda
    estirada con huecos (mapa y skills mejor pagadas reparten la fila), el mapa
    se ve sin recortes dentro de su tarjeta y no hay scroll horizontal a 1440;
    móvil y ES/EN sin regresiones.

- [x] **T-30 — Explorer, Metodología, estados y revisión visual final** (~25 min)
  - **RF:** RF-7, RF-9, RF-13, RF-16.
  - **Depende de:** T-28, T-29.
  - **Hecho cuando:** el explorador (que la persona valora) y la metodología
    adoptan el sistema visual sin perder densidad ni funcionalidad; avisos,
    estados vacíos y errores quedan coherentes con el nuevo sistema; se recorre
    el sitio completo en ES/EN, escritorio y móvil, se corrigen los defectos
    detectados y quedan capturas de evidencia del grupo en
    `.opencode/.agent-screenshots/`.

## 4.3 Correcciones de la persona (revisión, 2026-10-13)

Antes del cierre, la persona detecta tres puntos al revisar Market Pulse: los
nombres de empresa que son solo un número (valoración de la fuente) no deben
contarse ni rankearse; la tendencia de ofertas debe mostrar los días dentro de
cada mes (no el mes); y el scroll de las páginas debe quedar comprobado.

- [ ] **T-31 — Excluir empresas con nombre numérico** (~20 min)
  - **RF:** RF-7, RF-8, RF-13.
  - **Hecho cuando:** `Companies Hiring` y el top de empresas excluyen los
    nombres de empresa que son solo un número (`^[0-9]+([.,][0-9]+)?$` tras
    trim; p. ej. `4,0`, `42`; se conservan `2k`, `3m`, `12build`), el filtro de
    empresa no los ofrece, la metodología lo explica en ES/EN y las cifras
    cuadran con el dato local (9.164 → 9.134 empresas en la instantánea
    2026-10-10).

- [ ] **T-32 — Tendencia diaria en Market Pulse** (~25 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-31.
  - **Hecho cuando:** el gráfico de evolución usa el día de publicación
    (`posted_date`) dentro de los últimos 6 meses, con etiquetas de día y mes en
    ES/EN, tooltip con la fecha completa y título/caption actualizados, sin
    regresiones de encaje a 1440×900 (T-28) ni de temas (T-27).

- [ ] **T-33 — Scroll de las páginas del dashboard** (~20 min)
  - **RF:** RF-7, RF-16.
  - **Hecho cuando:** se reproduce el caso reportado por la persona (página,
    ventana y gesto) y el scroll funciona en todas las páginas, con la
    comprobación anotada. **Pendiente de concretar con la persona**: las
    mediciones locales dan scroll correcto con rueda (sobre y fuera de
    gráficos) y contenido que cabe entero en ventanas altas (Market Pulse,
    Roles & Skills y Salary Insights a 2160×1350).

## 5. Publicación y documentación

- [x] **T-15 — Portada y READMEs** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-10–T-14.
  - **Hecho cuando:** `docs/index.html` presenta el proyecto y enlaza al
    dashboard y a la evidencia de Power BI; `README.md` y `docs/es/README.md`
    documentan la URL pública, la actualización diaria, el export y que Power BI
    queda como evidencia; `docs/dashboard/README.md` fija las versiones del CDN
    y el contrato de datos.

- [x] **T-16 — Instantánea local reproducible** (~25 min)
  - **RF:** RF-11.
  - **Depende de:** T-06.
  - **Hecho cuando:** `scrapers-pipeline/export_web_snapshot.ps1` descarga el
    export a `docs/data/` (gitignored) con la SAS/AzCopy existentes, admite
    `-DryRun` y permite servir `docs/` en local con los mismos nombres y rutas
    que Pages; no sube nada ni publica; la web funciona con la instantánea.

- [ ] **T-17 — Workflow, secreto y Pages** (~25 min)
  - **RF:** RF-5.
  - **Depende de:** T-15.
  - **Hecho cuando:** `.github/workflows/dashboard.yml` descarga los cinco
    ficheros con `curl` usando `WEB_EXPORT_SAS` (solo lectura, sin imprimirla),
    ensambla `_site/` (`docs/` + `data/`) y despliega con
    `upload-pages-artifact`/`deploy-pages` con permisos y concurrencia
    correctos; el secreto y el origen Pages (GitHub Actions) quedan
    configurados; un `workflow_dispatch` real publica el sitio y la URL sirve
    las vistas sin login. Si la descarga falla, el job falla y no hay deploy
    parcial.

- [ ] **T-18 — Ciclo diario y frescura** (~20 min)
  - **RF:** RF-3, RF-5.
  - **Depende de:** T-17.
  - **Hecho cuando:** tras un export nuevo, un `dispatch` publica datos
    actualizados y `meta.json`/la fecha visible avanzan; el `schedule` diario
    (01:00 UTC, posterior al job de Databricks de las 23:30) queda configurado
    (empieza a correr al fusionar a `main`) y el flujo no versiona datos ni
    credenciales.

## 6. Verificación y cierre

- [x] **T-19 — Degradación y robustez** (~20 min)
  - **RF:** RF-4, RF-14.
  - **Depende de:** T-12, T-17.
  - **Hecho cuando:** se prueban (documentado) export ausente o antiguo, CDN
    caído, fichero Parquet ilegible y SAS sin permiso: la web avisa con la fecha
    del último dato y el workflow falla sin publicar a medias; el modo
    `aggregated` (si aplica) se comporta igual.

- [ ] **T-20 — Comprobación real y cierre de la spec** (~25 min)
  - **RF:** RF-5–RF-16.
  - **Depende de:** T-18, T-19.
  - **Hecho cuando:** la URL pública se abre en incógnito (escritorio y
    móvil), las 4 vistas cargan en ambos idiomas (selector funcional), los
    filtros, la búsqueda y los enlaces funcionan; 4–5 KPIs se cotejan contra el
    informe o sus capturas de la misma fecha; un ciclo completo del workflow
    actualiza `meta.json`; las suites existentes (`databricks_notebooks/tests`
    y `scrapers-pipeline/tests`) están en verde; el resultado se registra en
    `specs/006-Public-Web-Dashboard/evidence-<fecha>.md`; la persona valida el
    push/merge antes de cerrar.
