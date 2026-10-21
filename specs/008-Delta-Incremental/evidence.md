# Evidencia — spec 008: bronce deduplicado e ingesta incremental

Registro cronológico de las comprobaciones de cada tarea. **Nunca**
credenciales, tokens ni SAS.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-21 | T-01 | `design-upsert.md` con el mapa de columnas por fuente (§D0), condición de merge, cuarentena y checkpoint; tres rondas de verificación independiente | 1.ª ronda: 3 bloqueantes (columnas `job_key`/`id_oferta`/`fecha_scraped`, tipos de la temporal y `to_timestamp` con ANSI). 2.ª: B1–B3 cerrados y B4 (sintaxis de `MERGE WITH SCHEMA EVOLUTION`). 3.ª: **PASA**, con `_scraped_ts` auxiliar anotado para T-02. Corregido en `spec.md`/`plan.md`/`tasks.md` |
| 2026-10-21 | T-01 | Auditoría de negocio (silver, dev): grupos con misma localización y >1 clave (`job_ids > 1`), top 25 | **25 grupos** (el `LIMIT 25` corta: hay más). **Todos cross-site** (2–3 fuentes: linkedin+indeed, linkedin+multisite, y un caso con las tres). `posted_dates` 1 (duplicado el mismo día) o 2; separación entre capturas desde 1 día (`careerwise recruitment`, 09-30 → 10-01) hasta ~9 meses (`sd worx`, 2025-10-20 → 2026-07-28). Detalle abajo |
| 2026-10-21 | T-01 | Claves nulas/blancas en indeed e infojobs; temporales nulas/blancas/imparseables; capturas del mismo día por clave; offsets (guardia de T-03) | pendiente de pegar; consultas en `design-upsert.md` §Evidencia |
| 2026-10-21 | T-02 | `databricks_notebooks/Upsert_Job_Offers.py` + `tests/test_upsert_job_offers.py`: mapa por fuente, split a cuarentena, `_row_hash`, dedup del lote y SQL del MERGE | verificación independiente PASA, sin bloqueantes: **24 passed** (1 m 51 s, pyspark 4.2, ANSI activo, fixture UTC); `merge_sql` parseado en Spark 4.2 para las 4 fuentes. Sin ejecución real del MERGE (T-03) |

## T-01 — Duplicados de negocio (silver dev, 2026-10-21)

Consulta: agrupación por `title_norm + company_norm + location_city +
location_region + location_country` sobre la unión de las cuatro
`silver.offers_*`, `HAVING count(DISTINCT job_id) > 1`, `ORDER BY job_ids DESC
LIMIT 25`.

| title_norm | company_norm | ciudad | país | job_ids | fechas | 1.ª | última | fuentes |
|---|---|---|---|---|---|---|---|---|
| dba oracle / sql server | inetum | Bilbao | Spain | 3 | 3 | 2026-07-21 | 2026-09-22 | linkedin, indeed, infojobs |
| data scientist | bizaway | Barcelona | Spain | 2 | 2 | 2026-06-25 | 2026-09-20 | linkedin, indeed |
| global cs analytics lead (remote in barcelona/madrid) | docplanner | Madrid | Spain | 2 | 1 | 2026-08-20 | 2026-08-20 | linkedin, indeed |
| senior manager, product data analyst | contentsquare | Barcelona | Spain | 2 | 1 | 2026-07-13 | 2026-07-13 | indeed, linkedin |
| data analyst i | civitatis | Madrid | Spain | 2 | 2 | 2026-07-21 | 2026-09-22 | indeed, linkedin |
| senior data engineer | adikteev | Barcelona | Spain | 2 | 2 | 2026-06-10 | 2026-10-08 | indeed, linkedin |
| software integration engineer | pm group | Cork | Ireland | 2 | 2 | 2026-08-28 | 2026-10-10 | linkedin, multisite |
| data engineer | sd worx | Barcelona | Spain | 2 | 2 | 2025-10-20 | 2026-07-28 | indeed, linkedin |
| data scientist (10-month contract) | careerwise recruitment | Galway | Ireland | 2 | 2 | 2026-09-30 | 2026-10-01 | linkedin, multisite |
| senior power bi consultant | keyrus | Alcobendas | Spain | 2 | 1 | 2026-09-10 | 2026-09-10 | indeed, linkedin |
| intern bi engineer(f/m/d) | axpo group | Madrid | Spain | 2 | 2 | 2026-06-29 | 2026-07-22 | indeed, linkedin |
| data analyst intern | morningstar | Madrid | Spain | 2 | 1 | 2026-07-21 | 2026-07-21 | linkedin, indeed |
| senior full stack engineer - oliver wyman | marsh | Dublin | Ireland | 2 | 2 | 2026-08-11 | 2026-08-17 | linkedin, multisite |
| data analyst | optum | Dublin | Ireland | 2 | 2 | 2026-09-30 | 2026-10-14 | multisite, linkedin |
| business intelligence engineer, data and analytics | amazon | Barcelona | Spain | 2 | 2 | 2026-06-08 | 2026-08-19 | linkedin, indeed |
| senior data engineer | thunes | Barcelona | Spain | 2 | 2 | 2026-09-01 | 2026-09-23 | indeed, linkedin |
| forensic technology senior manager | deloitte | Dublin | Ireland | 2 | 2 | 2026-08-17 | 2026-08-28 | linkedin, multisite |
| commercial performance and insights manager | storio group | Barcelona | Spain | 2 | 2 | 2026-09-09 | 2026-10-06 | linkedin, indeed |
| data analyst intern | civitatis | Madrid | Spain | 2 | 1 | 2026-07-21 | 2026-07-21 | linkedin, indeed |
| full stack developer (.net/react) | ust | Madrid | Spain | 2 | 2 | 2026-06-22 | 2026-08-17 | indeed, linkedin |
| ai tech lead | belastingdienst | Utrecht | Netherlands | 2 | 2 | 2026-09-23 | 2026-09-27 | multisite, linkedin |
| senior data scientist | gartner | Barcelona | Spain | 2 | 1 | 2026-07-25 | 2026-07-25 | linkedin, indeed |
| database reliability engineer | rover.com | Barcelona | Spain | 2 | 2 | 2026-04-30 | 2026-08-20 | linkedin, indeed |
| business analysis, data science & ai specialist | marsh | Dublin | Ireland | 2 | 2 | 2026-07-21 | 2026-10-08 | multisite, linkedin |
| information systems & data analytics co-op placements 2027 - cork | stryker | Cork | Ireland | 2 | 2 | 2026-09-08 | 2026-09-17 | linkedin, multisite |

Lectura: el duplicado de negocio es **cross-portal** (no intra-fuente: cada
`job_id` es único en su tabla) y la localización coincide en todos los casos, así
que la clave de oro (`title_norm` + `company_norm` + `salary_filled`, **sin**
localización) está cazando estos duplicados a costa de colapsar también
vacantes distintas de la misma empresa en ciudades diferentes. Las separaciones
de hasta 9 meses indican que hace falta una ventana temporal en la spec futura.
