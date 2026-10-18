# Evidencia — spec 007: ELT como código (Asset Bundles)

Registro cronológico de las comprobaciones de cada tarea. Las capturas y
salidas saneadas se guardan junto a este fichero o en las rutas indicadas.
**Nunca** credenciales, tokens ni SAS.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-12 | T-01 | `docs/databricks-de/roadmap.md` y `docs/databricks-de/notes/README.md` versionados en `spec/007-elt-as-code` | hecho |
| 2026-10-12 | T-02 | `databricks auth profiles`: perfil `vs_code` con `Valid: Yes`; `databricks api get /api/2.1/unity-catalog/catalogs --profile vs_code` devuelve los catálogos del metastore | hecho; UC activo ⇒ edición Premium/Enterprise (inferida) |
| 2026-10-12 | T-03 | `jobs get` del job `704156530681034`: 7 tareas (bronze → 4 silver → gold → export web), job cluster `tm-lens-job` (Standard_D4ds_v4), `max_concurrent_runs: 1`, sin timeout ni `git_source`; programación `50 0 23 * * ?` (`Europe/Madrid`); `job-actual.json` saneado en la spec | hecho; corrige el supuesto de File Arrival |
| 2026-10-12 | T-04 | UI: Git folder `talent-market-lens` en la rama de la 006, actualización por pull manual (no visible en `repos list`); las 7 tareas de `job-actual.json` ejecutan desde esa carpeta | hecho; tras el merge de la 006 la carpeta pasará a `main` |
| 2026-10-15 | T-05 | `databricks bundle validate -t dev --profile vs_code` → `Validation OK!` (bundle `talent-market-lens`); el estado local `.databricks/` queda ignorado por git | hecho; CLI v1.21.0 instalada con winget y en PATH (sustituye a la v1.20.0 de la extensión, que no estaba en PATH) |
| 2026-10-15 | T-06 | `databricks bundle generate job --existing-job-id 704156530681034 --key elt --profile vs_code` → `resources/elt.job.yml`; `bundle validate -t dev` → OK; cotejo con `job-actual.json`: 7 tareas con `depends_on` idénticos, job cluster `tm-lens-job` (Standard_D4ds_v4), `schedule` UNPAUSED `50 0 23 * * ?`/`Europe/Madrid`, `max_concurrent_runs: 1` y notificación de fallo capturados | diferencias: (1) `notebook_path` = copias descargadas en `src/` (`../src/*.ipynb`, `source: WORKSPACE`) en vez de la Git folder → se repunta en T-07 a `${workspace.file_path}/databricks_notebooks/...` y se elimina `src/`; (2) sin `run_as` explícito (se mantiene el owner, plan §7); (3) `base_parameters` solo en Bronze y export, solo `storage_account` → los completa T-07 desde variables; (4) `generate` añadió `include: resources/*.yml` a `databricks.yml` |
| 2026-10-15 | T-07 | `databricks bundle validate -t dev` y `-t prod` → OK; `bundle summary -t dev`: path `.bundle/talent-market-lens/dev` y job `[dev …] Git Based ELT Dev` | hecho; variables por target (plan §6); `notebook_path` = `${workspace.file_path}/databricks_notebooks/…`; `base_parameters` con `${var.*}` en las 7 tareas; dev sin programación y prod con `schedule` PAUSED (override de target, se activa en el cutover); `sync` acotado a `databricks_notebooks/**`; `src/` eliminado; nombre dev `Git Based ELT Dev` acordado y plan §3/§6 actualizados |
| 2026-10-15 | T-08 | SQL Editor: `CREATE CATALOG job_offers_dev` con `MANAGED LOCATION 'abfss://landing@joboffershns.dfs.core.windows.net/managed/_unitystorage/catalogs/19bc1d62-8039-4e42-9416-4f52f80c422c_dev'` + schemas `bronze`/`silver`/`gold`; `SHOW SCHEMAS IN job_offers_dev` → bronze, silver, gold (+ `information_schema` y `default`, automáticos de UC); `CREATE TABLE`/`DROP TABLE` en `bronze._scratch` → OK | hecho; prefijo `_dev` paralelo al de prod en la misma storage (plan §8); owner: la persona |
| 2026-10-15 | T-09 | `databricks bundle deploy -t dev --profile vs_code`: job `[dev …] Git Based ELT Dev` (id `320947022571947`); `jobs get`: 7 tareas, `notebook_path` en `.bundle/talent-market-lens/dev/files/databricks_notebooks/…`, `base_parameters` dev (catalog `job_offers_dev`, `_checkpoints_dev`, `gold/web_export_dev`), sin `schedule`, `max_concurrent_runs: 1`; files dir del workspace → solo `databricks_notebooks` (7 notebooks + `tests/` y `.py` heredados) | hallazgo corregido: `sync.include` no restringe (solo re-incluye lo ignorado por `.gitignore`; el primer deploy subió el repo entero) → `sync.paths: [databricks_notebooks]`, redesplegado y el deploy limpió lo sobrante; plan §5/§12 actualizados |
| 2026-10-17 | T-10a | Auditoría del bronce (solo lectura, SQL) de `job_id` y `scraped_at` en `linkedin` y `multi_site` (prod y dev) | `job_id`: 0 nulos y 0 vacíos en las 4 tablas; filas / `job_id` distintos: LinkedIn prod 16.371/13.376, dev 9.569.896/13.376; multi_site prod 11.562/10.489, dev 225.796/10.489; `scraped_at` string ISO-8601 con offset único `+00:00` ⇒ orden lexicográfico = cronológico |
| 2026-10-17 | T-10a | Implementación asistida (a petición de la persona): módulo `Deduplicate_Job_Offers.py` + 9 tests; integración en `silver_linkedin` y `silver_multisite`; suites `databricks_notebooks`: 9 passed (focalizada) y 81 passed/1 skipped (completa) | commit `a9e9d15`; revisión independiente en dos rondas sin bloqueantes; pendiente el tiempo de la silver en el run dev de T-10 |
| 2026-10-17 | T-10a | `site` en `multi_site` (bronce, prod y dev): presente y poblado | 6 valores sin nulos (devitjobs, glassdoor, irishjobs, jobs_ch, nvb, stepstone_nl); la clave compuesta `site` + `job_id` usa la columna tal cual llega del bronce, antes del rename a `source_scraper` |
| 2026-10-18 | T-10 | Normalización one-off del bronce dev (linkedin y multi_site; mitigación prevista en el plan §12): LinkedIn 9.569.896 filas / 13.376 claves → 1 fila por clave; multi_site 225.796 / 10.489 → 1 por clave. Efecto medido en el dedup de la silver de LinkedIn: 12 GB de input / 22 GB de shuffle → 32 MB / 43 MB | hecho; los intentos previos se cancelaron (código anterior a T-10a: 5,6 h; bronce inflado: 18,7 min) |
| 2026-10-18 | T-10 | Run dev end-to-end `540330014336242` (11:16:06–11:33:49, hora local): SUCCESS, 17 m 43 s — bronze 7 m 36 s, indeed 4 m 50 s, infojobs 8 m 46 s, linkedin 6 m 11 s, multi_site 5 m 24 s, gold 55 s, export web 26 s | verde; el checkpoint dev ingirió solo los ficheros nuevos (max `_ingest_date` 2026-10-18); job dev sin programación (en pausa por defecto) |
| 2026-10-18 | T-10 | Conteos dev vs prod (silver y gold) y contrato de columnas: diferencias ≤1 % salvo indeed (+3,8 % en ofertas, +3,7 % en empresas) y `fact_offer_skills` (+1,7 %), coherentes con las ofertas nuevas del 18-oct; dimensiones idénticas y contratos (nombres y tipos) idénticos en las 14 tablas; claves solo-dev del bronce = ofertas nuevas del 18-oct (linkedin +36, indeed +110, infojobs +16, multi_site +56); solo-prod: 0 en linkedin/indeed/infojobs y 29 pares residuales en multi_site | detalle en «T-10 — Conteos dev vs prod»; prod aún no procesó el landing del 18-oct (run de anoche `642340624740960`, SUCCESS) |
| 2026-10-18 | T-10a | Pendiente cerrado: silver de LinkedIn en el run dev de T-10 = 6 m 11 s (bronce normalizado); sobre el bronce inflado el dedup medía 12 GB/22 GB y el intento se canceló a los ~19 min | cerrado |
| 2026-10-18 | T-11 | `resources/elt.job.yml`: `timeout_seconds` **a nivel de job** (recurso base → dev y prod) de `0` a `1800` (30 min); las 7 tareas siguen a `0` y `max_concurrent_runs: 1` intacto; compute sin cambios (`tm-lens-job`, `Standard_D4ds_v4` single node). `databricks bundle validate -t dev` y `-t prod` → `Validation OK!`; verificación independiente sin bloqueantes; suites `databricks_notebooks` 81 passed/1 skipped y `scrapers-pipeline` 768 passed | hecho; nota de coste: compute igual a prod y dev pausado, el timeout solo acota runs colgados (habría cortado el run de 5 h del 2026-10-17); sin `policy_id` porque la policy «Job Compute» prohíbe el single-node. Desviación deliberada de prod (el manual no tenía timeout) a listar en T-15 |
| 2026-10-18 | T-12 | `.github/workflows/databricks-bundle-validate.yml`: CI que ejecuta `databricks bundle validate -t dev` (CLI 1.21.0 fijada) en `pull_request`/`push` con filtro de rutas (`databricks.yml`, `resources/**`, `databricks_notebooks/**`, el propio workflow) y `workflow_dispatch`; `concurrency` + `contents: read`; sin deploy. Secretos de repo `DATABRICKS_HOST`/`DATABRICKS_TOKEN` (PAT rotado al detectarse en claro en el fichero, sin llegar a commitearse); endurecido con el PAT acotado al step de validación (`timeout-minutes: 10`) | runs verdes en push real: `https://github.com/Darkaleja69/talent-market-lens/actions/runs/39131570631` (commit `23bbe9c`) y `https://github.com/Darkaleja69/talent-market-lens/actions/runs/39132271179` (commit `9b3c7ba`, tras el endurecimiento); verificación independiente sin bloqueantes |
| 2026-10-18 | T-13 | Prueba rojo/verde del CI con la PR #1 (rama `test/t13-bundle-invalido`) y un error intencionado en `resources/elt.job.yml` (`${var.job_name}` → `${var.job_name_t13_typo}`): la CI falla en `pull_request`; revertido con `git revert` y run en verde. Gotcha del filtro `paths`: al revertir, el diff de la PR queda vacío en las rutas filtradas y no se dispara otro run de `pull_request`, así que el verde lo captura el trigger `push`. La línea quedó restaurada en la rama de la spec (el merge anula el error) y la rama de prueba se borró | rojo: `https://github.com/Darkaleja69/talent-market-lens/actions/runs/39137910107` (`event: pull_request`, `conclusion: failure`, commit `9e9573d`); verde: `https://github.com/Darkaleja69/talent-market-lens/actions/runs/39138005258` (`event: push`, `conclusion: success`, commit `57de0a1`) |
| 2026-10-18 | T-14 | `.github/workflows/databricks-bundle-deploy-dev.yml`: deploy manual (`workflow_dispatch`) que ejecuta `databricks bundle deploy -t dev` (solo dev, hardcoded); guard de rama (`main`/`spec/007-elt-as-code`), `concurrency` sin cancelar, `timeout-minutes: 15`, PAT acotado al step. Entorno de GitHub `dev` con *required reviewers* (el dispatch espera aprobación). Para que «Run workflow» aparezca hubo que mergear la rama a `main` (checkpoint `ada90e4`, `AGENTS.md` de `main` restaurado). `deploy` no arranca compute: solo sube ficheros y actualiza la definición del job dev (aplica el timeout de T-11) | run verde: `https://github.com/Darkaleja69/talent-market-lens/actions/runs/39147636829` (`event: workflow_dispatch`, `conclusion: success`, rama `main`, step `Deploy bundle (dev)` OK) |
| 2026-10-18 | T-15 | Target `prod` con `run_as` y `permissions` de la persona (production mode exige ambos; se mantiene el owner/run_as actual y el service principal queda como mejora documentada). `bundle validate -t prod` → `Validation OK!`; `bundle deploy -t prod` crea el job `916863262593632` (`Git Based ELT`), **PAUSED**, `50 0 23 * * ?`/`Europe/Madrid`, 7 tareas con paths al bundle y variables de prod, `timeout_seconds: 1800`, `max_concurrent_runs: 1`; `jobs list-runs` vacío (0 runs). El job manual `704156530681034` sigue intacto y UNPAUSED | hecho; diferencias con el manual en la tabla de «T-15 — Job prod del bundle vs job manual»; ACL: owner `IS_OWNER`, admins `CAN_MANAGE` heredado |
| 2026-10-18 | T-16 | `cutover.md` con precondiciones, IDs, resumen de deploys/runs, pasos exactos, verificación de la noche, criterios de aborto, rollback con tiempos (pausa efectiva <5 min; reconciliación <10 min) y estado final de la Git folder (ya en `main`, 74 commits por detrás). Rollback por definición + deploy porque el job es `UI_LOCKED`. `bundle validate -t prod` OK; verificación independiente del documento sin bloqueantes | **hecho; plan aprobado por la persona el 2026-10-18 ≈22:40 (Opción B): dry-run T-17 esta noche a las 23:05 y cutover a continuación, tras el run manual; primer run del bundle el lunes 19-oct a las 23:00 y T-19 el martes 20** |

## T-10 — Conteos dev vs prod (2026-10-18)

Consulta única en el warehouse (count(*) por tabla; «dif» = dev − prod). Origen:
la landing; dev con los ficheros hasta el 18-oct y prod hasta el 17-oct —su run
programado de anoche—. La comparación se ancla a los datos comunes (todo el
bronce de prod está contenido en el de dev, salvo 29 pares de multi_site) y el
delta restante son exactamente los ficheros nuevos del 18-oct: caso límite de
la spec (diferencias por datos nuevos entre runs). Claves solo-dev del bronce:
linkedin +36, indeed +110, infojobs +16, multi_site +56; solo-prod: 0 en
linkedin/indeed/infojobs y 29 pares residuales en multi_site.

| Capa | Tabla | dev | prod | dif |
|---|---|---:|---:|---:|
| silver | offers_linkedin | 11.715 | 11.709 | +6 |
| silver | offers_multi_site | 9.510 | 9.495 | +15 |
| silver | offers_indeed | 2.748 | 2.647 | +101 |
| silver | offers_infojobs | 2.287 | 2.271 | +16 |
| silver | companies_linkedin | 3.881 | 3.878 | +3 |
| silver | companies_multi_site | 4.312 | 4.309 | +3 |
| silver | companies_indeed | 1.311 | 1.264 | +47 |
| silver | companies_infojobs | 956 | 953 | +3 |
| gold | fact_offers | 24.540 | 24.403 | +137 |
| gold | fact_offer_skills | 42.653 | 41.933 | +720 |
| gold | companies_complete_catalog | 9.454 | 9.415 | +39 |
| gold | dim_currency | 19 | 19 | 0 |
| gold | dim_skill_list | 95 | 95 | 0 |
| gold | dim_calendar | 2.907 | 2.907 | 0 |

- Lectura: diferencias ≤1 % salvo indeed (ofertas +3,8 %, empresas +3,7 %) y
  `fact_offer_skills` (+1,7 %), coherentes con las ofertas nuevas del 18-oct;
  el resto ≤1 % (linkedin +0,05 %); dimensiones idénticas. El residuo más allá
  del desfase de un ciclo es inherente al diseño actual (dev deduplica desde el
  histórico completo re-leído; prod desde su acumulación desde ~sep) y lo
  resolverá el upsert del bronce (008).
- Bronce (contexto): dev max `_ingest_date` 2026-10-18 / prod 2026-10-17;
  ventana de prod desde 2026-07-27 (multi_site), 2026-09-01 (linkedin) y
  2026-09-16 (indeed, infojobs).
- Capturas Spark UI del intento sobre el bronce inflado: `capturas/`.

## T-15 — Job prod del bundle vs job manual (2026-10-18)

`bundle summary -t prod`:
`https://adb-7405619889654446.6.azuredatabricks.net/jobs/916863262593632`.
Definición desplegada saneada en `job-prod-bundle.json` (sin correos ni rutas de
usuario reales). `jobs list-runs --job-id 916863262593632` devuelve `[]`: el
deploy no ha lanzado ningún run y el job queda pausado hasta T-17/T-18.

| Aspecto | Manual `704156530681034` | Bundle `916863262593632` |
|---|---|---|
| Nombre | `Git Based ELT` | `Git Based ELT` (mismo nombre; se distinguen por ID hasta el cutover) |
| Programación | `UNPAUSED`, `50 0 23 * * ?`, `Europe/Madrid` | **`PAUSED`** (deliberado hasta T-18), misma cron y zona |
| Tareas | 7 (bronze → 4 silver → gold → export) | idénticas: mismos `task_key` y `depends_on` (el orden del JSON cambia, no la topología) |
| Notebooks | Git folder `…/talent-market-lens/databricks_notebooks/…` (sin extensión) | bundle prod `…/.bundle/talent-market-lens/prod/files/databricks_notebooks/….ipynb` |
| `base_parameters` | Bronze y export solo `storage_account`; silvers/gold sin parámetros | las 7 con variables de prod (`catalog`, `storage_account`, `container`, `checkpoints_root`, `export_prefix`); valores efectivos idénticos a los defaults actuales |
| Timeout del job | `0` | `1800` (desviación deliberada de T-11) |
| `run_as` | usuario (implícito en el owner) | usuario (explícito); `run_as_user_name` idéntico |
| ACL | owner usuario | owner `IS_OWNER`; `admins` `CAN_MANAGE` heredado; la declaración `permissions` de target satisface la validación de producción sin cambiar el owner |
| Compute | `tm-lens-job`, `Standard_D4ds_v4`, single node | idéntico (`num_workers: 0` explícito) |
| `max_concurrent_runs` | `1` | `1` |
| Notificación de fallo | email de la persona | idéntica |
| `git_source` | no | no |
| Metadatos | — | `deployment.kind: BUNDLE`, `edit_mode: UI_LOCKED` (gestionado por el bundle) |

El job manual sigue habilitado y con su programación; el job del bundle está
pausado. No hay riesgo de doble ejecución en esta fase.

Nota para T-16/T-18: `pause_status: PAUSED` vive en el override del target
`prod`, así que habilitar el job en el cutover exige cambiar el override a
`UNPAUSED` y redesplegar; des-pausarlo solo por la UI no sirve (el siguiente
`bundle deploy -t prod` lo volvería a pausar). El orden seguro es deshabilitar
el job manual primero y después desplegar el bundle des-pausado.
