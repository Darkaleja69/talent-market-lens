# Plan técnico: dashboard web público del portfolio

## 1. Objetivo y límites

Publicar en el portfolio un dashboard web interactivo que permita a cualquier
visitante explorar los datos reales **sin cuenta, sin licencia y sin coste**,
manteniendo Power BI como evidencia de la capa BI (`.pbip` + capturas + GIF). El
informe de Power BI deja de ser el canal de consulta pública; el sitio estático
lo sustituye, y un embed temporal de Power BI queda como extra opcional fuera de
esta spec.

La cadena completa es: **Databricks (Gold) → export Parquet + `meta.json` en
ADLS → GitHub Actions (descarga + ensamblado + deploy) → GitHub Pages →
DuckDB-WASM + ECharts en el navegador**. Sin servidor y sin datos en git: el
repositorio no crece y el sitio se actualiza solo.

**Límites.**

- No se toca `scrapers-pipeline/repair/**` ni sus tests (spec 004 en curso), ni
  `.opencode/**`, ni las specs ajenas. El único script de `scrapers-pipeline/`
  que se añade es la instantánea local de desarrollo (RF-11).
- No se modifica la landing, los manifests, `_READY` ni el pipeline nocturno.
- No hay backend, login, RLS ni Power BI de pago.
- No se replican las 58 medidas: solo las ~18 clave.
- Los datos no se commitean: se inyectan en el deploy desde ADLS.
- Sin credenciales en el repositorio: la descarga usa un secreto de GitHub
  (`WEB_EXPORT_SAS`) de solo lectura y ámbito mínimo, nunca la SAS de la landing.

**Decisiones tomadas.**

- Privacidad: el explorador muestra título, empresa y URL reales (ofertas
  públicas); sin descripciones ni columnas internas de calidad.
- Actualización: diaria vía GitHub Actions (`schedule` + `workflow_dispatch`).
- App sin build (CDN), coherente con "stack simple" de la constitución;
  Vite/TS como mejora futura solo si crece.
- UI bilingüe español/inglés con selector; los datos se muestran tal cual
  llegan (inglés).
- Repo público verificado, requisito de GitHub Pages gratuito.
- Trabajo en worktree y rama propios desde `main`, paralelo a la 004.

## 2. Arquitectura

```
Databricks Gold (Delta)                                  [ya existe]
  └─ task final: Prepare_Web_Export → Parquet + meta.json → ADLS <container>/gold/web_export/
       └─ GitHub Actions (cron diario + workflow_dispatch)
            ├─ descarga con curl usando SAS solo-lectura (secret WEB_EXPORT_SAS)
            ├─ ensambla _site/ = docs/ + data/
            └─ deploy GitHub Pages (gratis, repo público)
                 └─ visitante: DuckDB-WASM ejecuta SQL sobre los Parquet
                    en su navegador + ECharts pinta las 4 vistas
Power BI (.pbip + capturas en docs/images/) → evidencia, sin licencia viva
```

| Pieza | Responsabilidad | Lenguaje/medio |
|---|---|---|
| Módulo de export | Proyección, geografía, metadatos, decisión de tamaño y escritura Parquet | Python/PySpark (`databricks_notebooks/Prepare_Web_Export.py`) |
| Notebook del job | Widgets, llamada al módulo y registro | `databricks_notebooks/web_export_build.ipynb` |
| Workflow | Descarga, ensamblado y deploy | `.github/workflows/dashboard.yml` |
| App | Carga de datos, consultas y gráficos en el navegador | HTML + ES modules + CDN |
| Instantánea local | Reproducir el contrato del sitio sin CI | `scrapers-pipeline/export_web_snapshot.ps1` |

El módulo se testea con pytest (puro y con Spark, como `Prepare_Gold.py`); el
notebook es una cáscara de configuración (widgets) igual que `gold_build.ipynb`.
La app no conoce nada del pipeline: solo el contrato de ficheros de §6.

## 3. Stack

| Capa | Tecnología | Justificación |
|---|---|---|
| Export | PySpark existente → Parquet + `meta.json` | Sin dependencias nuevas; `df.write.parquet` |
| Descarga CI | `curl` con SAS solo-lectura | Evita AzCopy en el runner; archivos y nombres fijos |
| Cliente | DuckDB-WASM + ECharts por CDN | SQL completo en el navegador; mapas ECharts |
| App | HTML + ES modules (sin build) | Sin npm; código legible para revisores |
| Hosting/CI | GitHub Pages + GitHub Actions | Gratis en repo público; deploy diario |
| Tests | pytest (patrón existente) | Funciones puras + integración Spark del export |

Las versiones del CDN (DuckDB-WASM y ECharts) y del GeoJSON se fijan en el
momento de implementar y se documentan en `docs/dashboard/README.md`, junto a
sus licencias (MIT / Apache-2.0 / dominio público). No se añaden dependencias
al núcleo ni al entorno local.

## 4. Estructura de archivos

```
databricks_notebooks/
  Prepare_Web_Export.py            # módulo: funciones puras + build + escritura
  web_export_build.ipynb           # notebook fino: widgets → módulo → ADLS
  tests/test_web_export.py         # tests puros + integración Spark
README.md                          # sección "Live dashboard" (EN)
docs/es/README.md                  # sección "Live dashboard" (ES)
docs/
  index.html                       # portada pública del portfolio
  dashboard/
    index.html                     # shell de la app
    app.js                         # bootstrap, estado y render
    queries.js                     # SQL DuckDB de las medidas y vistas
    charts.js                      # configuración ECharts
    i18n.js                        # diccionario ES/EN y preferencia de idioma
    styles.css                     # estilos responsive
    assets/europe.geojson          # mapa coroplético versionado (licencia libre)
    README.md                      # versiones CDN, licencias y contrato de datos
docs/data/                         # instantánea local (gitignored; no versionar)
.github/workflows/dashboard.yml
scrapers-pipeline/export_web_snapshot.ps1
specs/006-Public-Web-Dashboard/{spec.md,plan.md,tasks.md,evidence-<fecha>.md}
```

El notebook existe además del módulo para mantener el patrón del proyecto
(`gold_build.ipynb` + `Prepare_Gold.py`): el `.py` concentra la lógica
comprobable y el `.ipynb` solo resuelve widgets y llama. El `.gitignore` ya
cubre `data/` y `*.parquet`, así que la instantánea local no ensucia el
repositorio.

## 5. Export de datos (Databricks)

### 5.1 Contrato de salida

Ruta por defecto `abfss://<container>/gold/web_export/` (el contenedor y el
prefijo son widgets; por defecto el contenedor de la landing y el prefijo
`gold/web_export`). Ficheros con nombre fijo, un solo archivo por tabla
(`coalesce`), para que `curl` descargue sin listar directorios:

| Fichero | Contenido |
|---|---|
| `fact_offers.parquet` | Ofertas proyectadas (ver columnas abajo) |
| `fact_offer_skills.parquet` | `JobID`, `Skill` (una fila por oferta + skill) |
| `dim_skill_list.parquet` | `SkillName`, `SkillCategory` |
| `dim_calendar.parquet` | Calendario (`Date`…`IsWeekend`), para etiquetas y orden |
| `meta.json` | Frescura, recuentos, versión y modo |

Columnas de `fact_offers` (públicas y necesarias para las vistas):

```
job_id, job_url, title, company_name,
location_city, location_region, location_country, GeoCountry, GeoRegion,
posted_date, PostedYearMonth, IsValidPostingDate,
WorkModeBucket,
SalaryMinAnnual_EUR, SalaryMaxAnnual_EUR, SalaryMidAnnual_EUR,
experience_level, role_category, employment_type, source_scraper
```

**Excluidas a propósito:** `description_clean` (peso y posibles datos
personales), `skills` en texto (está en `fact_offer_skills`),
`salary_quality`, `skills_source`, `experience_level_source`,
`posted_date_raw`, `posted_date_source`, `salary_currency` y `salary_period`
(no se usan en las ~18 medidas). La proyección se prueba para que ninguna
columna interna se cuele.

### 5.2 Módulo y funciones

`Prepare_Web_Export.py` (inglés en identificadores y columnas):

| Función | Responsabilidad | RF |
|---|---|---|
| `geo_country(value)` | Normalización de país del modelo (trim, `CA/FL/PA`, `Alemania`, `Austria y Suiza`/`Austria and Switzerland`, `Oriente Medio y África`, vacío → `(Not specified)`) | RF-2 |
| `geo_region(country, region)` | Mapeo contra `REGION_MAP` (constante de `Dim_RegionMap`) con `(Other)` de fallback | RF-2 |
| `project_fact_offers(df)` | Selección y cálculo de `GeoCountry`/`GeoRegion`; solo columnas del contrato | RF-1 |
| `project_offer_skills(df)`, `project_skill_list(df)` | Proyección de las tablas puente/catálogo | RF-1 |
| `build_meta(...)` | Construye el `meta.json` | RF-3 |
| `export_exceeds_limit(path|sizes)` | Decide full vs. agregados contra el umbral | RF-4 |
| `build_web_export(spark, fact_offers, fact_offer_skills, dim_skill_list, dim_calendar)` | Proyecta las tablas Gold (geografía incluida) y devuelve el dict | RF-1 |
| `collect_export_stats(tables)` | Recuentos por tabla y fuente, y fecha de datos para el `meta.json` | RF-3 |
| `write_web_export(spark, tables, dest, meta=None, size_provider=None)` | Escribe un fichero Parquet por tabla (`overwrite`, idempotente), calcula el tamaño, deriva el modo y escribe el `meta.json`; devuelve el meta actualizado | RF-1, RF-3, RF-4 |

Las funciones puras (`geo_country`, `geo_region`, `build_meta`,
`export_exceeds_limit`) no importan Spark y se testean sin clúster; las de
proyección/escritura se cubren con los tests de integración Spark.

### 5.3 Geografía portada del modelo

`Dim_Geo` vive solo en Power BI y la partición M de `Fact_Offers`
(`Fact_Offers.tmdl:279-284`) no se genera en Databricks (excepción documentada
en `Prepare_Gold.py`). Para que el mapa funcione sin Power BI:

1. `geo_country` porta la normalización M: `{"CA","FL","PA"}` → `United
   States` (son estados usados como país en la fuente), `Alemania` → `Germany`,
   `{"Austria y Suiza","Austria and Switzerland"}` → `Austria`, `Oriente Medio
   y África` → `Middle East & Africa`, vacío o `(Not specified)` → `(Not
   specified)`.
2. `REGION_MAP` versiona la tabla `Dim_RegionMap` completa (ciudades/provincias
   → región y fallback a país `(Country)`), con el mismo `(Other)` cuando no hay
   coincidencia.
3. Si el modelo cambia su mapeo, la constante se actualiza en esta spec
   (comparación documentada en los tests).

### 5.4 `meta.json`

```json
{
  "export_version": 1,
  "data_date": "YYYY-MM-DD",      // max(posted_date) del export
  "generated_at": "ISO-8601Z",     // ejecución del job
  "mode": "full",
  "tables": {"fact_offers": 0, "fact_offer_skills": 0,
             "dim_skill_list": 0, "dim_calendar": 0},
  "sources": {"<source_scraper>": 0},
  "size_bytes": 0
}
```

La web muestra `data_date` y `generated_at` (RF-3) y usa `mode` para saber si
debe leer agregados (RF-4).

### 5.5 Control de tamaño

Se mide el export real en la tarea de implementación. Objetivo: que la visita
descargue el sitio en un tiempo razonable (~25 MB de Parquet como umbral
orientativo). Si se supera:

- `mode: "aggregated"` y ficheros añadidos (`agg_by_month_role_country`, etc.)
  para las vistas analíticas;
- el explorador se limita a un alcance documentado (por ejemplo, últimos 90
  días o un tope de filas), explicado en la metodología.

La decisión se registra en `meta.json` y en la evidencia de la tarea; no se
implementan agregados si la medición no los necesita.

### 5.6 Tarea en el job de Databricks

`web_export_build.ipynb` resuelve widgets (`storage_account`, `container`,
`catalog`, `gold_schema`, `export_prefix`), lee las tablas Gold, proyecta con
`build_web_export`, recoge los recuentos con `collect_export_stats` y escribe el
export con `write_web_export`. La tarea se añade **al final** del job (manual,
una vez), es idempotente (`overwrite`) y no altera las tareas existentes. Si el
clúster no tiene permiso de escritura, el job falla de forma explícita.

## 6. Contrato del sitio

El sitio espera los ficheros en `data/`, junto al dashboard:

- Deploy: `_site/` = copia de `docs/` + descarga del export en `_site/data/`.
- Local: instantánea en `docs/data/` y servidor estático sobre `docs/`.

Con esa convención, la página `dashboard/` referencia `../data/fact_offers.parquet`
igual en local y en Pages (`https://darkaleja69.github.io/talent-market-lens/dashboard/`).

Carga: se descarga cada Parquet completo y se registra con
`registerFileBuffer` en DuckDB-WASM (RF-6), sin depender de *range requests*
(206) de Pages; después se crean vistas/vistas materializadas y `queries.js`
las consulta. Si `meta.json.mode` es `aggregated`, se cargan los agregados y se
habilitan solo las vistas compatibles, indicándolo en pantalla.

## 7. Vistas y medidas portadas

Medidas con semántica equivalente al modelo (RF-8). Filtros de calidad
salarial siempre que se use salario: `SalaryMinAnnual_EUR > 0`,
`SalaryMaxAnnual_EUR > 0`, ambos `≤ 1 000 000` y
`SalaryMinAnnual_EUR ≤ SalaryMaxAnnual_EUR`; para la mediana/media *mid*,
`SalaryMidAnnual_EUR > 0` y `≤ 1 000 000` con `min ≤ max`.

| # | Medida | Semántica portada (`Metrics.tmdl`) |
|---|---|---|
| 1 | Total Job Offers | `COUNTROWS` con el filtro de rango salarial global (`SalaryMaxAnnual_EUR` entre min y max) |
| 2 | Companies Hiring | `DISTINCTCOUNT(company_name)` no vacío |
| 3 | Countries Hiring | `DISTINCTCOUNT(location_country)` no vacío |
| 4 | Cities Hiring | `DISTINCTCOUNT(location_city)` no vacío |
| 5 | Offers Last 7 Days | `Total` con `posted_date > MAX(posted_date) - 7` |
| 6 | Offers Last 30 Days | Ídem a 30 días |
| 7 | Remote Share | `WorkModeBucket = 'Remote'` / Total |
| 8 | Hybrid Share | `WorkModeBucket = 'Hybrid'` / Total |
| 9 | On-site Share | `WorkModeBucket = 'On-site'` / Total |
| 10 | Remote-Friendly Share | (Remote + Hybrid) / Total |
| 11 | Salary Disclosure Rate | Ofertas con salario mid válido / Total |
| 12 | Skill Demand | `COUNT` de ofertas por skill (`fact_offer_skills`) |
| 13 | Skill Share | Skill Demand / Total del contexto |
| 14 | Skill Median Salary EUR | Mediana de `SalaryMidAnnual_EUR` de las ofertas con esa skill |
| 15 | Avg Mid Salary EUR | `AVERAGE` de `SalaryMidAnnual_EUR` con filtros de calidad |
| 16 | Median Mid Salary EUR | `MEDIAN` de `SalaryMidAnnual_EUR` |
| 17 | P25 Mid Salary EUR | `PERCENTILE.INC(..., 0.25)` (DuckDB: `quantile_cont`) |
| 18 | P75 Mid Salary EUR | `PERCENTILE.INC(..., 0.75)` |

Además, agregaciones directas para los gráficos (sin nueva lógica de negocio):
tendencia por `PostedYearMonth`; top roles (`role_category`), top empresas
(`company_name`) y reparto por `experience_level`; media de `SalaryMidAnnual_EUR`
por `role_category` y por `WorkModeBucket`; top skills pagadas y ofertas por
`GeoCountry` para el mapa.

**Vistas:**

- **Market Pulse:** medidas 1–11 + agregaciones ejecutivas.
- **Roles & Skills:** medidas 12–14 + burbuja demanda vs. salario, donut por
  categoría y tabla benchmark.
- **Salary Insights:** medidas 15–18 + medias por rol/modalidad + top paying
  skills + mapa por país.
- **Opportunity Explorer:** tabla de ofertas con búsqueda y filtros, campos
  públicos y enlaces reales (RF-10), con paginación/tope.
- **Metodología:** texto (sin SQL) con fuentes, pipeline, normalización,
  frescura (`meta.json` y recuento por fuente), limitaciones y aviso legal
  (RF-13).

**Filtros globales (RF-9):** país → `GeoCountry`; experiencia →
`experience_level`; rol → `role_category`; modalidad → `WorkModeBucket`;
empresa → `company_name`; skill → existencia en `fact_offer_skills` para el
`job_id`; fuente → `source_scraper`; rango salarial → `SalaryMaxAnnual_EUR`
como en el modelo. `queries.js` compone el `WHERE` común y cada vista re-renderiza.

## 8. Aplicación web

| Fichero | Responsabilidad |
|---|---|
| `index.html` | Metadatos, cabecera con frescura, navegación de vistas, contenedores, import map del CDN |
| `app.js` | Estado de filtros, arranque (carga de Parquet + `meta.json`), mensajes de error/degradación, render de la vista activa |
| `queries.js` | SQL de cada medida y agregación, documentado con la medida DAX de origen; sin reglas propias de negocio |
| `charts.js` | Configuración ECharts (líneas, barras, burbuja, donut, mapa coroplético con GeoJSON) |
| `i18n.js` | Diccionario español/inglés, selección y persistencia del idioma |
| `styles.css` | Layout responsive, accesible y sin dependencia de hover |

La app no usa frameworks ni build: ES modules nativos con import map a CDN
(RF-6). Todos los textos de la interfaz viven en `i18n.js` (español/inglés, con
la preferencia del visitante persistida). La portada `docs/index.html` presenta
el proyecto, enlaza al dashboard y a la evidencia de Power BI.

## 9. Publicación, instantánea local y configuración manual

### Workflow (`dashboard.yml`)

- Disparadores: `schedule` (por defecto `0 1 * * *`, 01:00 UTC, posterior al job
  de Databricks de las 23:30 y a su tarea de export) y `workflow_dispatch`.
- Permisos: `contents: read`, `pages: write`, `id-token: write`; concurrencia
  de Pages serializada.
- Pasos: checkout → `curl` de los cinco ficheros a `_site/data/` con
  `WEB_EXPORT_SAS` del entorno (nunca impresa en logs) → copia de `docs/` a
  `_site/` → `upload-pages-artifact` → `deploy-pages`.
- Si la descarga o el ensamblado fallan, el job falla y no hay deploy: el sitio
  anterior queda intacto (RF-5, RF-14).
- El cron solo corre en la rama por defecto; durante el desarrollo se usa
  `workflow_dispatch`.

### Configuración manual única

1. Crear el secreto `WEB_EXPORT_SAS`: SAS de solo lectura, ámbito
   `gold/web_export`, rotación documentada; nunca la SAS de la landing ni
   permisos de escritura.
2. En Settings → Pages, elegir **GitHub Actions** como origen.
3. En Databricks, añadir la tarea `web_export_build` al final del job (una vez):
   mismo job compute que las demás tareas, `depends on` la última (Gold) y el
   parámetro `storage_account` con la cuenta del contenedor del landing.

### Instantánea local (`export_web_snapshot.ps1`)

Descarga el export con la SAS/AzCopy ya existentes a `docs/data/` (gitignored)
y opcionalmente sirve `docs/` con un servidor estático de Python; mismos
nombres y rutas que en Pages (RF-11). No sube nada ni publica.

## 10. Flujo git en paralelo con la 004

- `git worktree add ..\talent-market-lens-006 -b spec/006-public-web-dashboard main`
  (desde `main`, que ya incluye la 005; **no** desde la 004).
- El worktree nuevo no hereda el trabajo en curso de la 004 (sus ramas
  `spec/`/`repair/` y ficheros locales se quedan en el repositorio original);
  la 006 solo ve `main`.
- La 004 no solapa archivos con la 006: repair/tests/.opencode frente a
  notebooks/docs/.github/scrapers-pipeline (script nuevo); el orden de merge es
  indiferente.
- Commits atómicos por tarea en cada rama; sin commits en `main`; el push se
  valida con la persona (AGENTS.md).
- La carpeta `specs/006-Public-Web-Dashboard/` (hoy sin trackear) se lleva al
  worktree en T-01 y se versiona allí.
- El cron del sitio empieza a actualizar de verdad cuando la spec se fusione a
  `main`; hasta entonces, `workflow_dispatch`.

## 11. Estrategia de tests y comprobaciones

### Unitarios puros (`databricks_notebooks/tests/test_web_export.py`)

- `geo_country`: `CA/FL/PA` → `United States`, `Alemania` → `Germany`,
  `Austria y Suiza`/`Austria and Switzerland` → `Austria`, `Oriente Medio y
  África` → `Middle East & Africa`, trims, vacío y `(Not specified)`, valor
  desconocido tal cual.
- `geo_region`: ciudades del catálogo (Madrid, Barcelona→Catalonia, Dublín,
  Zúrich, ámsterdam…), fallback a país `(Country)`, región desconocida →
  `(Other)` y sensibilidad de mayúsculas/espacios.
- `project_fact_offers`: columnas exactas del contrato y ausencia de las
  excluidas (`description_clean`, `salary_quality`, `skills`, …).
- `build_meta`: claves, recuentos, `data_date`, `mode` y `export_version`.
- `export_exceeds_limit`: por debajo, en el umbral y por encima.

### Integración Spark (mismo fichero, patrón `test_integration_spark.py`)

- `build_web_export` sobre un DataFrame sintético: tablas resultantes,
  `GeoCountry`/`GeoRegion`, recuentos y tipos.
- `write_web_export` en un directorio temporal: Parquet legible, un solo
  fichero por tabla y reejecución idempotente sin duplicar ni corromper.

### Front y comprobaciones manuales

La app no tiene framework de tests (stack simple): cada tarea de vista se
comprueba en local con la instantánea y una lista de verificación documentada
(la vista carga, los filtros se aplican, los valores son coherentes). Las
cifras se cotejan con el informe o sus capturas en la comprobación real.

### Regresión

- `python -m pytest databricks_notebooks/tests -q` tras cada tarea (local,
  con pyspark de `requirements.txt`).
- `python -m pytest scrapers-pipeline/tests -q` en el cierre.

### Comprobación real (AGENTS.md)

- URL pública en incógnito (escritorio y móvil): las 4 vistas cargan; filtros,
  búsqueda y enlaces funcionan.
- Cotejo de 4–5 KPIs contra las capturas de Power BI para la misma fecha.
- Un ciclo completo del workflow (`dispatch` tras un export nuevo) y
  comprobar que `meta.json` avanza; después, el cron.
- Evidencia registrada en `specs/006-Public-Web-Dashboard/evidence-<fecha>.md`.

## 12. Decisiones técnicas y alternativas descartadas

1. **App sin build (CDN + ES modules).** Coherente con la constitución y
   legible para revisores. **Descartado:** Vite/TS ahora (solo si el sitio
   crece).
2. **DuckDB-WASM en el navegador** frente a precalcular JSON en el deploy o
   montar un backend: el mismo SQL del modelo portado sobre Parquet da
   flexibilidad y filtros sin servidor. **Descartado:** exportar solo JSONs
   agregados y un backend/API.
3. **Export Parquet desde Gold** (no desde Power BI ni desde un extracto
   manual): misma fuente que el informe y reproducible. **Descartado:** extraer
   del modelo semántico o de capturas.
4. **Datos fuera de git, inyectados en el deploy** con SAS de solo lectura:
   repo estable y fresco. **Descartado:** commitear Parquet y regenerar el
   sitio con cada run.
5. **Geografía portada a funciones puras** con `Dim_RegionMap` como constante.
   **Descartado:** depender de Power BI o de una tabla externa para el mapa.
6. **Solo las ~18 medidas clave.** El objetivo es un dashboard público, no
   clonar el informe; lo demás vive en Power BI (evidencia). **Descartado:**
   replicar las 58 medidas y las 5 páginas.
7. **GitHub Pages + Actions** por coste cero y repo ya público. **Descartado:**
   Netlify/Vercel/Cloudflare (cuentas extra) y Pages de pago.
8. **Fallback local con AzCopy/SAS existentes** para desarrollar sin CI.
   **Descartado:** depender solo del workflow para probar.
9. **Tarea del export al final del job, idempotente y manual de añadir.**
   **Descartado:** modificar la definición del job desde el repo (fuera de
   alcance) o exportar en una tarea intermedia.
10. **UI bilingüe español/inglés con selector, datos en inglés.** Coherente con
    la constitución. **Descartado:** más idiomas y un framework de
    internacionalización pesado.

## 13. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Volumen de datos (Parquet pesado) | Medición real en T-06; agregados y límite del explorador si supera ~25 MB |
| *Range requests* en Pages | Cargar el Parquet completo y `registerFileBuffer`, sin depender de 206 |
| SAS en GitHub | Solo lectura, ámbito mínimo, rotación; nunca la SAS de la landing; no se imprime en logs |
| Fallo del workflow | El deploy no ocurre; el sitio anterior queda intacto y la web muestra la última fecha |
| Job diario | Tarea nueva al final, idempotente; no se tocan las existentes |
| Deriva del mapeo geográfico | `REGION_MAP` versionada y con tests; si el modelo cambia, se actualiza la constante |
| Caché de Pages | Frescura visible vía `meta.json`; sin datos viejos silenciosos |
| CDN caído o versión cambiada | Versiones fijadas y documentadas; aviso visible si no carga |
| Privacidad | Export sin descripciones ni columnas internas; solo campos públicos |
| Cruce con la 004 | Archivos disjuntos y worktree propio; cualquier cambio en el pipeline se consulta |
| Trial de Power BI | Irrelevante para el sitio; sin dependencia |

## 14. Secuencia de implementación

1. Preparación: rama/worktree desde `main` y suites de base en verde (T-01).
2. Funciones puras del export: geografía (`REGION_MAP`), proyección,
   metadatos y tamaño, con tests (T-02–T-04).
3. `build_web_export`/`write_web_export` con integración Spark (T-05).
4. Notebook, tarea al final del job, ejecución real y medición de tamaño
   (T-06).
5. `queries.js` con las ~18 medidas y su semántica documentada (T-07).
6. Shell de la app, carga DuckDB-WASM, i18n ES/EN y filtros globales
   (T-08–T-09).
7. Vistas Market Pulse, Roles & Skills, Salary Insights y Explorer, más
   metodología (T-10–T-14).
8. Portada, README, instantánea local y workflow con Pages (T-15–T-17).
9. Ciclo completo (`dispatch` + frescura) y degradación visible (T-18–T-19).
10. Comprobación real con la URL pública, paridad con Power BI, suites en
    verde, evidencia y validación de la persona (T-20).

## 15. Trazabilidad RF

| RF | Partes del plan | Tareas |
|---|---|---|
| RF-1 | §5.1, §5.2, §5.6 | T-03, T-05, T-06 |
| RF-2 | §5.2, §5.3 | T-02, T-05 |
| RF-3 | §5.4 | T-04–T-06, T-08 |
| RF-4 | §5.5 | T-04, T-06, T-19 |
| RF-5 | §9 (workflow) | T-17, T-18 |
| RF-6 | §3, §6, §8 | T-08 |
| RF-7 | §7 | T-10–T-14 |
| RF-8 | §7 (tabla de medidas) | T-07, T-20 |
| RF-9 | §7 (filtros), §8 | T-09 |
| RF-10 | §7 (vista), §8 | T-13 |
| RF-11 | §9 (instantánea) | T-16 |
| RF-12 | §1 límites, §10 | T-01 |
| RF-13 | §7 (metodología) | T-14 |
| RF-14 | §6, §9 | T-08, T-19 |
| RF-15 | §9, §14 | T-15, T-20 |
| RF-16 | §4, §8 (`i18n.js`) | T-08, T-10–T-14, T-20 |

## 16. Cumplimiento de la constitución

- **Stack simple:** el export reutiliza PySpark y pytest; el sitio carga
  DuckDB-WASM y ECharts por CDN con versión fijada (sin `npm` ni build); el CI
  usa `curl`. Ninguna dependencia nueva del núcleo.
- **Spec y código:** todo el trabajo vive en `specs/006-Public-Web-Dashboard/`
  y su rama; lo que exceda el alcance se propone como spec nueva.
- **Lógica e interfaz:** proyección, geografía, metadatos y decisión de tamaño
  son módulos comprobables; notebook, workflow y web solo coordinan.
- **Tests:** unitarios puros + integración Spark del export; listas de
  verificación manuales de las vistas; suites existentes en verde antes de
  cerrar.
- **Persistencia:** los datos crudos siguen en Azure; el export es derivado y
  no se versiona; la web es de solo lectura.
- **Idioma:** identificadores, columnas y artefactos de máquina en inglés;
  interfaz del sitio en español e inglés; mensajes y documentación para la
  persona, en español.
