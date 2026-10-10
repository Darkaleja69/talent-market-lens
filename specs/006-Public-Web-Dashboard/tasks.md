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

- [ ] **T-07 — Portar las ~18 medidas a `queries.js`** (~25 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-06.
  - **Hecho cuando:** `queries.js` implementa las 18 medidas de §7 del plan con
    los filtros de calidad, ventanas y cuotas del modelo, cada una con su
    medida DAX de origen documentada; sobre la instantánea local (T-16 puede
    adelantarse con una descarga manual) los valores ejecutados coinciden con
    el informe o sus capturas, y las diferencias conocidas quedan anotadas.

- [ ] **T-08 — Shell, carga con DuckDB-WASM e i18n** (~25 min)
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

- [ ] **T-09 — Filtros globales coherentes** (~25 min)
  - **RF:** RF-9.
  - **Depende de:** T-08.
  - **Hecho cuando:** el estado de filtros (país, experiencia, rol, modalidad,
    empresa, skill, fuente, rango salarial) se compone en un `WHERE` común que
    todas las consultas respetan, se muestran los filtros activos, se pueden
    limpiar y los valores de una vista cambian de forma coherente al filtrar.

## 4. Vistas

- [ ] **T-10 — Vista Market Pulse** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra los KPIs 1–11, la tendencia por
    `PostedYearMonth`, top roles, top empresas y reparto por experiencia, con
    formato legible, textos ES/EN y verificada en local contra el
    informe/capturas.

- [ ] **T-11 — Vista Roles & Skills** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra demanda y cuota por skill, salario
    mediano por skill, burbuja demanda vs. salario, donut por categoría y tabla
    benchmark, coherentes con el modelo, resistentes a skills sin salario y con
    textos ES/EN.

- [ ] **T-12 — Vista Salary Insights** (~25 min)
  - **RF:** RF-7, RF-8, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la vista muestra media, mediana, P25 y P75, medias por
    categoría de rol y modalidad, top paying skills y el mapa coroplético por
    país con el GeoJSON versionado; los países sin dato se distinguen, con
    textos ES/EN y la vista funciona en móvil.

- [ ] **T-13 — Vista Opportunity Explorer** (~25 min)
  - **RF:** RF-7, RF-10, RF-16.
  - **Depende de:** T-09.
  - **Hecho cuando:** la tabla permite buscar y filtrar, muestra los campos
    públicos (título, empresa, ubicación, modalidad, rol, salario mediano,
    fecha, fuente), abre la oferta original en otra pestaña, pagina o limita el
    volumen, no incluye descripciones ni columnas internas y sus textos salen
    del diccionario ES/EN.

- [ ] **T-14 — Vista Metodología** (~20 min)
  - **RF:** RF-7, RF-13, RF-16.
  - **Depende de:** T-08.
  - **Hecho cuando:** la vista explica fuentes, traza del pipeline,
    normalización salarial, procedencia de fechas, frescura (fecha de datos y
    recuento por fuente desde `meta.json`), limitaciones conocidas y aviso
    legal, en español e inglés.

## 5. Publicación y documentación

- [ ] **T-15 — Portada y READMEs** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-10–T-14.
  - **Hecho cuando:** `docs/index.html` presenta el proyecto y enlaza al
    dashboard y a la evidencia de Power BI; `README.md` y `docs/es/README.md`
    documentan la URL pública, la actualización diaria, el export y que Power BI
    queda como evidencia; `docs/dashboard/README.md` fija las versiones del CDN
    y el contrato de datos.

- [ ] **T-16 — Instantánea local reproducible** (~25 min)
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

- [ ] **T-19 — Degradación y robustez** (~20 min)
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
