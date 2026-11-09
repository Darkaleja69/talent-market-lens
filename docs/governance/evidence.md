# Evidencia — gobernanza (spec 012)

> **Fecha de las comprobaciones:** 2026-11-06 (T-01, T-02 y T-04), 2026-11-07
> (T-03, ejecución real en Free Edition; T-05, T-06 y T-07, local; T-08, sesión
> SQL única y verificación de constraints por API), 2026-11-08 (T-09, deploy de
> producción) y 2026-11-09 (T-10, run programado y dashboard; T-11, cierre).
> **Tareas:** T-01 (inventario, solo lectura), T-02 (ensayo de grant/revoke en
> dev y preflight de prod), T-03 (laboratorio de row filter y column mask
> **ejecutado el 2026-11-07 en Free Edition**, ruta B), T-04 (ensayo de herencia
> en dev, RUNBOOK de gobernanza y consulta de auditoría preparada), T-05
> (contrato de calidad y mapa tabla-columna), T-06 (cuarentena
> `company_name_rating` y tratamiento por motivo), T-07 (DDL versionado y
> pruebas Delta locales), T-08 (perfiles, constraints y auditoría en **una**
> sesión SQL), T-09 (validación, merge checkpoint y deploy de prod) y T-10
> (comprobación real: run programado, constraints, grants y dashboard).
> **Modo:** CLI/API de Databricks (`--profile vs_code`) y lectura local de código
> en T-01–T-04; T-03 lo ejecutó la persona a mano en Databricks Free Edition;
> T-05–T-07 son **locales** (Python, PySpark y Delta 4.4 en este equipo); T-08 lo
> ejecutó la persona en **una** sesión del warehouse y se verificó después por
> API gratuita; T-09 y T-10 se comprobaron por CLI/API y GitHub.
> **0 runs de dev; 1 sesión SQL; ~0,43 € de desarrollo; 1 run de producción
> ordinario (T-10).** El laboratorio de T-03 vive en **Free
> Edition** (§8) y T-07 probó el DDL en un warehouse local temporal: T-08 es la
> única sesión del proyecto sobre el warehouse pagado. Los grants de T-02 y T-04
> fueron temporales, aplicados y revocados en el mismo acto (§5 y §7); T-08
> aplicó **20 constraints** (10 dev + 10 prod) y las verificó por API (§12). Los
> objetos sintéticos de T-03 se crearon y se retiraron dentro de Free Edition
> (§8). El run de T-10 es el programado de producción del 2026-11-08 y se imputa
> al presupuesto ordinario de producción, no al techo de dev de la 012.
>
> Salidas saneadas en `docs/governance/evidence/`. Saneado aplicado: correos y
> nombres personales → `<cuenta-personal>` / `<persona>`; el id de suscripción de
> Azure → `<suscripcion>`. **No hay tokens, SAS, claves ni cabeceras de
> autenticación en ninguna salida.** Los UUID de objetos y de access connectors se
> conservan por ser identificadores, no secretos.

## 1. Comprobaciones

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 1 | 2026-11-06 | T-01 | `databricks current-user me --profile vs_code -o json` | Usuario `<cuenta-personal>`, id `141486372609033`; grupos `admins` (`151347827406338`), `users` (`150981975206808`), `users-clone-2026-08-05-2234-UTC` (`2126387033071238`) | user `141486372609033` | 0 € | `evidence/t01-current-user.txt` |
| 2 | 2026-11-06 | T-01 | `databricks catalogs get job_offers_dev` y `… job_offers` | Ambos `MANAGED_CATALOG`, `OPEN`, owner `<cuenta-personal>`, `storage_root` en `abfss://landing@joboffershns…/managed` | `job_offers_dev`, `job_offers` | 0 € | `evidence/t01-catalogs.txt` |
| 3 | 2026-11-06 | T-01 | `databricks schemas list job_offers_dev` / `job_offers` | 6 esquemas por catálogo: `bronze`, `silver`, `gold`, `ops`, `default`, `information_schema`; owner `<cuenta-personal>` salvo `information_schema` (`System user`) | 8 esquemas de capa | 0 € | `evidence/t01-schemas-and-tables.txt` |
| 4 | 2026-11-06 | T-01 | `databricks tables list <cat> <sch> --omit-columns` | **39 tablas** (19 dev, 20 prod), todas `MANAGED`/`DELTA`, owner `<cuenta-personal>` | 39 tablas | 0 € | `evidence/t01-schemas-and-tables.txt` |
| 5 | 2026-11-06 | T-01 | `databricks grants get catalog …` (2) | `{}` en los dos catálogos: **sin `privilege_assignments` directas** | 2 catálogos | 0 € | `evidence/t01-grants-direct.txt` |
| 6 | 2026-11-06 | T-01 | `databricks grants get schema …` (8) | `{}` en los 8 esquemas de capa | 8 esquemas | 0 € | `evidence/t01-grants-direct.txt` |
| 7 | 2026-11-06 | T-01 | `databricks grants get table …` (39) | `{}` en las 39 tablas, **listadas una a una** en la evidencia; 0 tablas con grants directos | 39 tablas | 0 € | `evidence/t01-grants-tables.txt` |
| 8 | 2026-11-06 | T-01 | `databricks grants get-effective …` (catálogo, 2 esquemas, 4 tablas) con `--principal`, y control `grants get-effective catalog samples --principal <persona>` | `{}` en los objetos del proyecto: el owner no aparece con privilegios enumerados (ownership no es grant). **Control positivo:** `get-effective` sobre `samples` devuelve `account users` con `SELECT`/`USE_CATALOG`/`USE_SCHEMA` | job_offers_dev, job_offers | 0 € | `evidence/t01-grants-effective.txt` |
| 9 | 2026-11-06 | T-01 | `databricks tables get <cat>.bronze.<fuente>_quarantine` (8) y `tables list … bronze` | Las 8 tablas **no existen** («does not exist»); en `bronze` solo están las 4 tablas de fuente | 8 rutas | 0 € | `evidence/t01-quarantine-tables.txt` |
| 10 | 2026-11-06 | T-01 | `databricks jobs list` + `jobs get 916863262593632` / `320947022571947` / `704156530681034` | Prod y dev con **9 tareas**, `run_as` = persona, `UI_LOCKED`, bundle; prod `UNPAUSED` (`50 0 23 ? * SUN`, Europe/Madrid); legado de 7 tareas `PAUSED` | `916863262593632`, `320947022571947`, `704156530681034` | 0 € | `evidence/t01-jobs.txt` |
| 11 | 2026-11-06 | T-01 | `databricks jobs get-permissions <id>` (3) | `<cuenta-personal>` `IS_OWNER` (directo); grupo `admins` `CAN_MANAGE` **heredado de `/jobs/`** | 3 jobs | 0 € | `evidence/t01-jobs.txt` |
| 12 | 2026-11-06 | T-01 | `databricks storage-credentials list` | 6 credenciales, todas con `azure_managed_identity` (**no service principal**); 5 del proyecto usan el access connector `access-connector-joboffers`; 1 del sandbox usa `unity-catalog-access-connector` | `access-connector-joboffers` | 0 € | `evidence/t01-storage.txt` |
| 13 | 2026-11-06 | T-01 | `databricks external-locations list` + `get landing-loc` | Una sola EL del proyecto: `landing-loc`, URL `abfss://landing@joboffershns.dfs.core.windows.net/` (**raíz del contenedor**), credential `landing-loc_azuremanagedidentity_1791406770615`, `fallback: false` | `landing-loc` | 0 € | `evidence/t01-storage.txt` |
| 14 | 2026-11-06 | T-01 | `databricks grants get storage-credential …` y `… external-location landing-loc` | `{}` en ambos: sin grants directos | `landing-loc` | 0 € | `evidence/t01-storage.txt` |
| 15 | 2026-11-06 | T-01 | `databricks metastores summary` | `metastore_azure_westeurope` (`9be7f9ac-2bf3-4f70-9e81-4e754169af3d`), owner `System user`, `external_access_enabled: false`, `privilege_model_version 1.0` | metastore `9be7f9ac…` | 0 € | `evidence/t01-objects-extra.txt` |
| 16 | 2026-11-06 | T-01 | `databricks volumes list` / `functions list` en los **12** esquemas (4 de capa ×2 + `default` + `information_schema` ×2) | Sin volumes y sin funciones en ninguno de los 12 esquemas | 12 esquemas | 0 € | `evidence/t01-objects-extra.txt` |
| 17 | 2026-11-06 | T-01 | `databricks groups list` | Grupos de workspace: `users`, `admins`, `users-clone-2026-08-05-2234-UTC` | 3 grupos | 0 € | `evidence/t01-objects-extra.txt` |
| 18 | 2026-11-06 | T-01 | `databricks warehouses list` antes y después | `Serverless Starter Warehouse` (`53d8e3e91af6749c`, 2X-Small, PRO, serverless) **`STOPPED` antes y después** | `53d8e3e91af6749c` | 0 € | `evidence/t01-objects-extra.txt`, `evidence/t01-warehouse-after.txt` |
| 19 | 2026-11-06 | T-01 | `databricks tables get` de 10 tablas clave | Tipos confirmados: bronce `job_key`/`job_id`/`id_oferta`/`site` `STRING`; silver `job_id` `STRING`, `salary_min_annual`/`salary_max_annual` `DOUBLE`, `posted_date` `DATE`; gold `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR` `DOUBLE`, `posted_date` `DATE` | 10 tablas | 0 € | `evidence/t01-table-metadata-detail.txt` |
| 20 | 2026-11-06 | T-01 | Metadatos de las 39 tablas (`tables get`) | **0** tablas con propiedades `delta.constraints.*`; **0** columnas con `nullable=false` (equivalente a `NOT NULL`) | 39 tablas | 0 € | `evidence/t01-table-metadata-detail.txt` |
| 21 | 2026-11-06 | T-01 | Grep de `ADD CONSTRAINT` / `CHECK (` / `CONSTRAINT ` / `NOT NULL` en `databricks_notebooks/`, `resources/`, `databricks.yml` | **Sin DDL de constraints**: 0 `ADD CONSTRAINT`, 0 `CHECK (`, 0 `CONSTRAINT <nombre>`; las 4 coincidencias son predicados `IS NOT NULL`/`isEmpty` y un docstring | repo | 0 € | `evidence/t01-repo-constraints-grep.txt` |
| 22 | 2026-11-06 | T-01 | Lectura de `Upsert_Job_Offers.py` | Motivos `missing_key` y `missing_scraped_at` con precedencia; identidad `(_row_hash, _quarantine_reason, _source_file)`; anti-join limitado a `_source_file` del lote | módulo `Upsert_Job_Offers.py` | 0 € | `quality-inventory.md` §3 |
| 23 | 2026-11-06 | T-01 | Lectura de `Enrich_Job_Offers_Dataframes.py` | `non_pos`, `inv_range`, `unknown_period`, `outlier_review`: marca `salary_quality` y neutraliza anuales; no rechaza | módulo `Enrich…py` l. 1582-1650 | 0 € | `quality-inventory.md` §5 |
| 24 | 2026-11-06 | T-01 | Lectura de `Prepare_Gold.py` + `gold_build.ipynb` | `IsValidPostingDate` 2026–2089 (flag, no filtro); filtro de rating en `companies_complete_catalog` (`^\d+[,.]\d{1,2}$`); dedup `job_id` y `title_norm+company_norm+salary_filled` | módulos/notebook | 0 € | `quality-inventory.md` §2, §6 |
| 25 | 2026-11-06 | T-01 | Estadística Delta de `job_offers.gold.fact_offers` (metadatos, sin SQL) | `posted_date` min `2018-10-13`, max `2026-11-02`, `nullCount 0`; `IsValidPostingDate` 2 valores distintos; `job_id nullCount 0`; `SalaryMinAnnual_EUR nullCount 18614` | `job_offers.gold.fact_offers` | 0 € | `evidence/t01-table-metadata-detail.txt` |
| 26 | 2026-11-06 | T-01 | Revisión de `scrapers-pipeline/coherence.py` | `RATING_RE = ^\d[,.]\d{1,2}$`; `company_name` con rating/URL es fila inválida en las 4 fuentes (defensa local previa) | `scrapers-pipeline/coherence.py` | 0 € | `quality-inventory.md` §2 |
| 27 | 2026-11-06 | T-01 | Búsqueda/documentación del informe y del dashboard | Power BI usa `PBI_EXPORT_SAS` (read-only, prefijo `gold/pbi_export`); workflow usa `WEB_EXPORT_SAS` (read-only, `gold/web_export`): ambos fuera de Unity Catalog | SAS (no leídas) | 0 € | `access-matrix.md` §5.5 |
| 28 | 2026-11-06 | T-01 | `databricks grants get metastore 9be7f9ac-…` y `get-effective metastore … --principal <persona>` | **Sí hay ACL de metastore**: `account users` → `USE_MARKETPLACE_ASSETS`; grupo de cuenta de admins del workspace → 9 `CREATE_*` (catálogo, clean room, connection, external location, provider, recipient, service credential, share, storage credential). No heredan a los objetos de datos | metastore `9be7f9ac…` | 0 € | `evidence/t01-metastore-grants.txt` |
| 29 | 2026-11-06 | T-01 | `databricks groups get 151347827406338` + `users get` de sus dos miembros | `admins` tiene **dos miembros activos**: `141486372609033` y `146517773436349`, con **el mismo `externalId`** (`b5a26491-…`) y nombre: dos registros SCIM de la misma identidad. Ambos heredan `CAN_MANAGE` de `/jobs/`; el segundo no es owner ni tiene grants UC | `151347827406338` | 0 € | `evidence/t01-identities.txt` |

## 2. Salidas que no se versionan

- La salida cruda de `tables list` (≈760 KB con todas las columnas) **no se
  conserva** en el repo: se sustituyó por el resumen compacto
  `evidence/t01-schemas-and-tables.txt` (nombre, tipo, formato, owner,
  constraints, filas de estadística) y por el detalle de columnas clave de
  `evidence/t01-table-metadata-detail.txt`.
- No se guarda ningún `databricks jobs get` completo porque incluye
  `email_notifications`; se guarda el extracto saneado de `evidence/t01-jobs.txt`.

## 3. Pendientes y verificados de esta tarea

> La tabla conserva los pendientes abiertos de T-01 y añade, marcada como
> **verificada**, la fila de Free Edition cerrada en T-03 (§8).

| Tema | Motivo | Cuándo se cierra |
|---|---|---|
| Azure RBAC de la Managed Identity (roles/ámbito sobre `joboffershns`) | Requiere Azure CLI/Portal; T-01 solo autoriza Databricks con `--profile vs_code` | T-02 (o se registra como desviación) |
| Cuál de los dos `credential_id` del access connector (`d3be66a9…`/`d937de4b…`) tiene el RBAC efectivo | El connector aparece con dos identidades registradas; requiere Azure | T-02, antes de retirar credenciales |
| Expansión de la pertenencia de `admins` al grupo de cuenta que recibe los `CREATE_*` del metastore | La ACL del metastore sí está medida (fila 28); la API no expande miembros en `get-effective` | T-02, si hace falta para prod |
| Cuál de los dos registros de la misma identidad (`141486372609033`/`146517773436349`) es el canónico y si el duplicado se retira | Mismo `externalId`; requiere Account Console/Entra | T-02/T-09 (decisión, no cambio en T-01) |
| Principal real de los UUID `9e033aa4-…`, `f4ae815d-…`, `421bd159-…` | La API de workspace no los resuelve | T-02, si afecta a la matriz |
| Privilegios implícitos por ownership enumerados por la API | En los objetos del proyecto `grants get-effective` devuelve `{}` para el owner (en el metastore sí devuelve asignaciones, sin expandir miembros) | No se cierra: se documenta como limitación de la API |
| Row filters / column masks y dos perfiles en Free Edition | **Verificado el 2026-11-07** (ya no pendiente): Free Edition soporta filtros y máscaras, pero **no permite conceder permisos a grupos**, así que la ruta A no es viable | Cerrado con la **ruta B** (dos usuarios reales) sobre `workspace.governance_lab`; evidencia en §8 |
| Esquema/conteos de `<fuente>_quarantine` | Las tablas no existen | T-06/T-08 |
| Conteos de violaciones (job_id vacío, min>max, fechas fuera de rango) | Prohibido SQL en T-01 | T-08 (sesión única) |

## 4. Declaración de no intervención (T-01)

- **No se ha abierto el SQL warehouse** (`state = STOPPED` antes y después) ni se
  ha ejecutado ninguna sentencia SQL.
- **No se ha creado, modificado ni revocado** ningún grant, ownership, constraint,
  job, cluster, credential ni external location.
- **No se ha ejecutado** `bundle validate`, `bundle deploy`, `bundle run` ni
  ningún run de job.
- **No se ha leído** ningún token, SAS, clave de storage ni cabecera de
  autenticación.
- **Coste:** 0 € (todas las llamadas son API/CLI de metadatos y lectura local).

## 5. T-02 — ensayo de grant/revoke en dev y preflight de prod

> **Modo:** CLI/API (`--profile vs_code`). **0 runs, 0 sentencias SQL, 0 €.**
> **Único cambio:** `SELECT` temporal de `account users` sobre
> `job_offers_dev.ops.run_metrics`, aplicado, verificado y **revocado** el
> 2026-11-06 (autorizado en D-01). Prod **no se tocó**.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 30 | 2026-11-06 | T-02 | `grants get table job_offers_dev.ops.run_metrics`, `… schema job_offers_dev.ops`, `… catalog job_offers_dev` y `get-effective … --principal "account users"` | **`{}` en los cuatro**: estado de partida sin grants | `job_offers_dev.ops.run_metrics` | 0 € | `evidence/t02-before-grants.txt` |
| 31 | 2026-11-06 | T-02 | `grants update table job_offers_dev.ops.run_metrics --json {"changes":[{"principal":"account users","add":["SELECT"]}]}` | Grant aplicado: `account users` → `SELECT`; `get-effective` del principal devuelve `SELECT` | `account users`, tabla dev | 0 € | `evidence/t02-grant-applied.txt` |
| 32 | 2026-11-06 | T-02 | `grants update … --json {"changes":[{"principal":"account users","remove":["SELECT"]}]}` | **Revocado**; `grants get` de tabla, esquema y catálogo y `get-effective` vuelven a **`{}`**. Sin residuo | `job_offers_dev.ops.run_metrics` | 0 € | `evidence/t02-rollback-after.txt`, `evidence/t02-final-state.txt` |
| 33 | 2026-11-06 | T-02 | Preflight de prod: `grants get` de catálogo, 4 esquemas, **las 20 tablas**, EL y storage credential; `catalogs get`/`schemas list`; `external-locations list`; `jobs get 916863262593632` | **`{}` en todos los grants**; owners `<cuenta-personal>` en el catálogo, los 4 esquemas y las 20 tablas; única EL `landing-loc` con credential `…1791406770615`; job de **9 tareas**, `run_as` persona, `50 0 23 ? * SUN` `Europe/Madrid`, `UNPAUSED` | `job_offers`, `landing-loc`, `916863262593632` | 0 € | `evidence/t02-prod-baseline.txt` |
| 34 | 2026-11-06 | T-02 | `databricks warehouses list` | `Serverless Starter Warehouse` (`53d8e3e91af6749c`, 2X-Small) **`STOPPED`** | `53d8e3e91af6749c` | 0 € | `evidence/t02-warehouse.txt` |
| 35 | 2026-11-06 | T-02 | `databricks jobs list-runs --start-time-from/--start-time-to` con epoch en **milisegundos** (ventana 2026-11-06 `Europe/Madrid`) | **0 runs** en la ventana; el más reciente es del 2026-11-04 | — | 0 € | `evidence/t02-job-runs-2026-11-06.txt` |
| 36 | 2026-11-06 | T-02 | `storage-credentials list` + `external-locations list` | **5 credenciales del proyecto** con el connector `access-connector-joboffers`; **4 sin EL que las referencie** (`0e78f15f…`, `15b01141…`, `73515c4d…`, `62442f4d…`). No se borra nada: preflight para **T-09** | `landing-loc_…1791406770615` en uso | 0 € | `evidence/t02-prod-baseline.txt`; `grants-plan.md` §5 |
| 37 | 2026-11-06 | T-02 | Registro de la decisión aprobada por la persona | **D-01**: exceso de la cuenta personal aprobado como limitación; `run_as` no se migra; prod no se toca; credenciales duplicadas a T-09; EL en la raíz pendiente de aprobación antes de T-09 | D-01 | 0 € | `decisions.md` |

**Preflight de prod preparado (sin ejecutar cambios):** comandos de verificación
en `grants-plan.md` §3, grants hipotéticos de una futura identidad de
automatización con su inverso en §4, preflight/retirada de las credenciales
duplicadas en §5 y rollback/criterios de parada en §6. **T-09 no aplicará grants
en prod con el modelo actual**: su paso de grants es una verificación de no
regresión.

**Declaración de no intervención de T-02:** no se ejecutó SQL ni se despertó el
warehouse; no se ejecutó ningún run ni `bundle deploy/run`; no se modificaron
catálogos, esquemas, tablas, storage, jobs ni bundles; no se tocó la SAS de Power
BI ni la del dashboard; no se crearon identidades ni grupos. El único cambio fue
el grant temporal de la fila 31, revertido en la fila 32. **Prod: 0 cambios.**

## 6. T-03 — laboratorio de row filter y column mask (preparación, 2026-11-06)

> **Modo:** preparación documental. **0 sentencias SQL, 0 objetos creados,
> 0 €.** Sin credenciales de Free Edition en este equipo en la preparación y sin
> usar el workspace pagado. **Ejecución real el 2026-11-07 en Free Edition: §8.**

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 38 | 2026-11-06 | T-03 | Preparación del SQL y de la guía del laboratorio; contraste con la documentación oficial de Databricks | **Preparado el 2026-11-06**; **ejecutado el 2026-11-07** en Free Edition (ruta B, §8). Esta fila conserva solo el estado de la preparación | `governance_lab` (objetos previstos, no creados el 2026-11-06) | 0 € | `docs/governance/lab-row-filter-column-mask.sql`, `docs/governance/lab-row-filter-column-mask.md` |

Lo que la documentación **sí** confirma: sintaxis y requisitos de row
filters/column masks, sus limitaciones (`MERGE`, time travel, clones) y que
`CREATE GROUP` crea grupos workspace-local **no utilizables en Unity Catalog**.
Lo que **no** está confirmado y decide el preflight: soporte de filtros/máscaras
en Free Edition, creación de grupos de cuenta y existencia de dos perfiles
(`lab-row-filter-column-mask.md` §2).

## 7. T-04 — ensayo de herencia en dev, RUNBOOK y auditoría preparada

> **Modo:** CLI/API (`--profile vs_code`). **0 runs, 0 sentencias SQL, 0 €.**
> **Único cambio:** `SELECT` temporal de `account users` sobre el **esquema**
> `job_offers_dev.ops` (padre de `run_metrics`), aplicado, verificado con
> `get-effective` y **revocado** el 2026-11-06. Prod **no se tocó**.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 39 | 2026-11-06 | T-04 | `grants get schema job_offers_dev.ops`, `grants get table job_offers_dev.ops.run_metrics` y `get-effective table … --principal "account users"` | **`{}` en los tres**: estado de partida sin grants | `job_offers_dev.ops` | 0 € | `evidence/t04-before-grants.txt` |
| 40 | 2026-11-06 | T-04 | `grants update schema job_offers_dev.ops --json {"changes":[{"principal":"account users","add":["SELECT"]}]}` | Grant aplicado en el **esquema**; `grants get table run_metrics` sigue **`{}`** (no hay grant directo en la tabla) | `account users`, esquema dev | 0 € | `evidence/t04-grant-applied-inherited.txt` |
| 41 | 2026-11-06 | T-04 | `grants get-effective table job_offers_dev.ops.run_metrics --principal "account users"` y control en `job_offers_dev.bronze.indeed` | **Herencia demostrada:** `SELECT` con `inherited_from_name: job_offers_dev.ops` y `inherited_from_type: SCHEMA`; el control en otro esquema devuelve **`{}`** | `job_offers_dev.ops.run_metrics` | 0 € | `evidence/t04-grant-applied-inherited.txt` |
| 42 | 2026-11-06 | T-04 | `grants update schema job_offers_dev.ops --json {"changes":[{"principal":"account users","remove":["SELECT"]}]}` | **Revocado** (respuesta `{}`); `grants get` de esquema, tabla y catálogo y `get-effective` vuelven a **`{}`**. Sin residuo | `job_offers_dev.ops` | 0 € | `evidence/t04-rollback-after.txt`, `evidence/t04-final-state.txt` |
| 43 | 2026-11-06 | T-04 | Lectura de `grants get` de `job_offers` (catálogo, esquema `ops`, tabla `run_metrics`) y `warehouses list` | **Prod intacto:** `{}` en los tres objetos; `Serverless Starter Warehouse` (`53d8e3e91af6749c`, 2X-Small) **`STOPPED`**. Sin SQL y sin runs | `job_offers`, `53d8e3e91af6749c` | 0 € | `evidence/t04-no-touch.txt` |
| 44 | 2026-11-06 | T-04 | Redacción de la sección §11 de `docs/RUNBOOK.md` y de `t04-audit-query.sql`; contraste con la documentación oficial de UC y de `system.access.audit` | Operativa publicada (matriz por capa/principal, herencia y ownership vs `MANAGE`, comandos/rollback, SAS de Power BI, diagnóstico UC vs ADLS). **Consulta de auditoría preparada y NO ejecutada**: se correrá en la sesión única de T-08 | `docs/RUNBOOK.md` §11; `t04-audit-query.sql` | 0 € | `access-matrix.md` §6 y §8.1; `docs/RUNBOOK.md` §11; `t04-audit-query.sql` |

Pendiente fuera de T-04: `docs/es/RUNBOOK.md` está **desincronizado** (no
contiene §9 ni la sección de gobernanza) y queda anotado como pendiente, sin
editarlo.

**Declaración de no intervención de T-04:** no se ejecutó SQL ni se despertó el
warehouse; no se ejecutó ningún run ni `bundle deploy/run`; no se crearon
identidades, grupos, storage, constraints ni recursos; no se tocó la SAS de
Power BI ni la del dashboard. **Prod: 0 cambios.** El único cambio fue el grant
temporal de la fila 40, revertido en la fila 42. La consulta de auditoría **no se
ha ejecutado**: `system.access.audit` sigue `[NO VERIFICADO]` hasta T-08.

## 8. T-03 — ejecución real en Free Edition (2026-11-07)

> **Modo:** ejecución manual de la persona en **Databricks Free Edition**,
> catálogo `workspace`, esquema `governance_lab`, **ruta B** (dos usuarios
> reales). **0 €, 0 runs, 0 sentencias SQL en el workspace pagado.** La ruta A
> (grupos de cuenta) **no es viable** en Free Edition: no se pueden conceder
> permisos a grupos.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 45 | 2026-11-07 | T-03 | Perfil autorizado: consulta de verificación sobre la tabla sintética con la política aplicada | **8 filas**; `contact_email` original | `workspace.governance_lab.offers` | 0 € | `evidence/t03-authorized-8-rows.png` |
| 46 | 2026-11-07 | T-03 | Perfil restringido: consulta de verificación con row filter y column mask | **4 filas**, solo `Barcelona`; `contact_email = '***'` | `workspace.governance_lab.offers` | 0 € | `evidence/t03-restricted-4-rows-mask.png` |
| 47 | 2026-11-07 | T-03 | Caso negativo: `REVOKE SELECT` al perfil restringido y consulta | **Error `[INSUFFICIENT_PERMISSIONS]`**: `Principal [redactado] does not have SELECT on Table 'workspace.governance_lab.offers'. SQLSTATE: 42501`; la política no concede acceso | `workspace.governance_lab.offers` | 0 € | `evidence/t03-negative-no-select.png` |
| 48 | 2026-11-07 | T-03 | Limpieza: retirada de políticas y objetos; comprobación del esquema y las bases | **Esquema y bases vacíos**: sin tabla ni objetos | `workspace.governance_lab` | 0 € | `evidence/t03-cleanup-schema-empty.png` |
| 49 | 2026-11-07 | T-03 | Limpieza: consulta a `information_schema.column_masks` | **`No rows returned`**: sin máscaras activas | `workspace.governance_lab` | 0 € | `evidence/t03-cleanup-mask-empty.png` |

**Notas de la ejecución (T-03):**

- **Entorno:** Databricks **Free Edition**, catálogo `workspace`, esquema
  `governance_lab`; **ruta B** con dos usuarios reales. La tabla sintética
  `governance_lab.offers` tenía las **8 filas** insertadas.
- **Ruta A no viable en Free Edition:** no se pueden conceder permisos a grupos.
  Se descartó sin forzar la demo y sin usar el workspace pagado.
- **No capturado:** los **query IDs**, el **preflight completo** (bloques 0A/0C)
  y el **`SHOW GRANTS` final**. No se deducen ni se rellenan: quedan como
  limitación de la evidencia visual.
- **Alcance de las capturas:** muestran la **rejilla de resultados**, no la
  consulta ni el **perfil/sesión** que la ejecutó.
- **Limpieza del esquema:** la captura de limpieza no muestra el **nombre del
  esquema** ni el SQL ejecutado.
- **Acceso base sin política:** el caso «acceso base sin política» **no se
  capturó** en esta ejecución.
- **Atribución del perfil autorizado:** la captura de **8 filas** se atribuye al
  perfil autorizado, pero **no se ve el usuario** en la imagen.
- **Herencia de grants:** no se repitió en el laboratorio; se demostró en **T-04
  en dev** (`evidence/t04-grant-applied-inherited.txt`, §7).
- **Saneado:** la captura del error de permisos conserva el mensaje completo
  (`Principal [redactado] does not have SELECT on Table 'workspace.governance_lab.offers'. SQLSTATE: 42501`);
  el correo real se **redactó** con un **rectángulo negro** antes de versionarla.
  **Ningún correo real aparece en esta documentación.**
- La ejecución la hizo la persona **a mano**; no se usó el warehouse del
  proyecto ni se lanzaron runs. **Coste: 0 €.**

## 9. T-05 — contrato de calidad comprobable (2026-11-07)

> **Modo:** local (Python + PySpark). **0 SQL, 0 runs, 0 cambios de recursos,
> 0 €.** El módulo no lo importa ningún notebook todavía: el job no cambia.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 50 | 2026-11-07 | T-05 | `python -m pytest databricks_notebooks/tests/test_data_quality.py -q` | **25 passed**: registro estable (6 reglas), mapa tabla-columna por capa, predicados válido/inválido/nulo/límite, expresiones SQL compartidas con el DDL, nombres, plan idempotente y guiones de T-08 | `databricks_notebooks/Data_Quality.py` | 0 € | `test_data_quality.py` |
| 51 | 2026-11-07 | T-05 | `python -m pytest databricks_notebooks/tests/test_integration_spark.py -q` | **27 passed** (23 de la línea base + 4 nuevos): rango invertido y salario no positivo (`0`, negativo y `max = 0`) ⇒ `salary_quality = invalid`, anuales a `NULL` y **rango original conservado** | `Enrich_Job_Offers_Dataframes.enrich` | 0 € | `test_integration_spark.py` |
| 52 | 2026-11-07 | T-05 | Contraste de la constraint con **Delta 4.4 local** (tabla temporal) | Un `CHECK` que evalúa a `NULL` **cuenta como violación**: `salary_min_annual <= salary_max_annual` rechazó la fila con ambos importes nulos. Las expresiones permiten el nulo de forma explícita (`IS NULL OR`) | `chk_t_salary_order` (local) | 0 € | `docs/RUNBOOK.md` §12.2 |

**Mapa tabla-columna fijado (sin suponer nombres iguales):** silver
(4 tablas × `job_id`, `salary_min_annual`, `salary_max_annual`) y
`gold.fact_offers` (`job_id`, `SalaryMinAnnual_EUR`, `SalaryMaxAnnual_EUR`,
`posted_date`). Las claves de bronce se leen de
`Upsert_Job_Offers.SOURCE_SPECS`. El único nulo explicable por diseño (salario
ausente o neutralizado) sigue siendo válido; la regla de fecha queda pospuesta
(§11, fila 60).

## 10. T-06 — cuarentena `company_name_rating` y tratamiento (2026-11-07)

> **Modo:** local (PySpark). **0 SQL, 0 runs, 0 €.** El crudo no se toca y no
> hay ninguna tabla de cuarentena real sobre la que actuar (T-01, fila 9).

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 53 | 2026-11-07 | T-06 | `python -m pytest databricks_notebooks/tests/test_upsert_job_offers.py -q` | **44 passed** (35 de la línea base + 9 nuevos): `4,0`/`4.0`/` 4,00 ` a cuarentena; `24/7 Retail`, `4.0 Retail`, `ACME 4,0`, `10,0` y nulos válidos; precedencia `missing_key` > `missing_scraped_at` > `company_name_rating`; lote mixto; todo inválido; repetición del lote; identidad por `_source_file`; corrección y reproceso | `databricks_notebooks/Upsert_Job_Offers.py` | 0 € | `test_upsert_job_offers.py` |
| 54 | 2026-11-07 | T-06 | Reconciliación del lote en los fixtures | `entrada = válidas + filas únicas enviadas a cuarentena` (5 = 2 + 2 + 1 repetida del lote); repetir el lote **no** crece la cuarentena; un lote 100 % inválido deja `válidas = 0` y `cuarentena = entrada`, sin bronce vacío aparente | `new_quarantine_rows` | 0 € | `test_upsert_job_offers.py` |
| 55 | 2026-11-07 | T-06 | Corrección/reproceso local | La fila corregida (misma clave, `company_name` real, captura más nueva) es **válida** y el `MERGE` actualiza la clave; la fila original **sigue en cuarentena** como evidencia. El descarte exige motivo, fecha, conteo y referencia al lote | `merge_sql` (clave `job_id`) | 0 € | `test_upsert_job_offers.py`; `RUNBOOK` §10.2 |
| 56 | 2026-11-07 | T-06 | Resumen de bronce y `ops.run_metrics` | **Sin cambios**: `QUARANTINE_COUNTS` cuenta solo las filas nuevas del lote por `(source, batch_id)`; no se añade ninguna acción sobre todo el lote | `Bronze_Summary.quarantine_total` | 0 € | `test_bronze_summary.py` (sin cambios) |

## 11. T-07 — DDL versionado y pruebas Delta locales (2026-11-07)

> **Modo:** local (Delta 4.4 sobre tablas temporales de `spark_catalog`).
> **Ningún DDL se aplicó a Databricks**, ningún grant ni recurso se tocó,
> 0 SQL contra el warehouse pagado, 0 €.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 57 | 2026-11-07 | T-07 | `$env:TML_LOCAL_DELTA=1; python -m pytest databricks_notebooks/tests/test_constraints_delta.py -q` (**en aislamiento**) | **12 passed**: el DDL se aplica y aparece en metadatos; la fila válida escribe; `job_id` nulo o en blanco se rechaza; rango invertido se rechaza; nulos salariales válidos; segundo plan **vacío** (idempotencia); el inverso retira y el forward reaplica; los perfiles cuentan; los dos ficheros SQL coinciden con el módulo | `spark_catalog.silver.*`, `spark_catalog.gold.fact_offers` | 0 € | `test_constraints_delta.py` |
| 58 | 2026-11-07 | T-07 | DDL generado e inverso | **10 `ADD CONSTRAINT` por catálogo** (5 `job_id_required` + 5 `salary_range_order`) para dev y prod, con los 10 `DROP CONSTRAINT` inversos **comentados**; ninguna constraint sobre el rango salarial original | `docs/governance/constraints-ddl.sql` | 0 € | módulo §12.3 |
| 59 | 2026-11-07 | T-07 | Consultas de perfil para T-08 | **11 perfiles por catálogo** (`rows_total`, `violating_rows`, nulos por columna y rango de fechas) + inventario de las 4 cuarentenas por catálogo, con preflight de existencia por `information_schema` | `docs/governance/t08-profile-queries.sql` | 0 € | módulo §12.3 |
| 60 | 2026-11-07 | T-07 | Regla `posted_date_contract` | **`[NO VERIFICADO]`**: `IsValidPostingDate` es un flag (2026–2089), no un filtro, y gold tiene `posted_date` desde 2018-10-13; **no genera DDL** y se mide en T-08 antes de decidir | `Prepare_Gold.add_posted_date_flags` | 0 € | `quality-inventory.md` §6 |
| 61 | 2026-11-07 | T-05–T-07 | `python -m pytest databricks_notebooks/tests -q` | **324 passed, 5 skipped** (329 recogidos): los 12 tests Delta de T-07 se ejecutan **en aislamiento** (fila 57, 12 passed) y en la suite quedan como 1 skip de módulo opt-in; las 4 integraciones Delta de `test_run_metrics` se saltan, como ya hacían, al no tener una sesión Delta compartida | repo | 0 € | este documento |

**Declaración de no intervención de T-05–T-07:** no se ejecutó SQL ni se
despertó el warehouse del proyecto (el Delta de T-07 corre en un warehouse
local temporal); no se ejecutó ningún run ni `bundle deploy/run`; no se
crearon, modificaron ni revocaron grants, owners, constraints ni recursos de
Databricks; no se tocó la SAS de Power BI ni la del dashboard; no se modificó el
crudo de ADLS; no se reprocesó ni borró histórico; no hay tokens, SAS ni
credenciales en la evidencia. **Coste: 0 €.**

## 12. T-08 — sesión SQL única: perfiles, constraints y auditoría (2026-11-07)

> **Modo:** **una** sesión SQL ejecutada a mano por la persona sobre
> `Serverless Starter Warehouse` (`53d8e3e91af6749c`, 2X-Small). **1 sesión,
> ~0,43 €** (10 minutos facturables). Warehouse **`STOPPED`** antes y
> **`STOPPED` después** (comprobado por API el 2026-11-07, sin SQL). Sin runs.
> **Capturas:** `docs/governance/screenshots/t08-*.png`, 33 imágenes de la
> rejilla de resultados. **Limitación registrada:** las capturas no muestran el
> SQL ni los *query IDs*; el coste es el estándar de 10 min facturables porque no
> se capturó la duración. No hay tokens, SAS ni correos en ellas.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 62 | 2026-11-07 | T-08 | 11 perfiles de `job_id_required` y `salary_range_order` en dev y prod | **0 violaciones en las 10 tablas**; nulos de salario masivos (gold prod 18.614 y 18.789) | 10 tablas | ~0,43 € (sesión) | §12.1; capturas `t08-*-job-id`, `t08-*-salary` |
| 63 | 2026-11-07 | T-08 | Perfil de `posted_date_contract` en `gold.fact_offers` | **138 violaciones** en dev y en prod; `min_date` 2018-10-13; 0 nulos | `job_offers[_dev].gold.fact_offers` | incluido | §12.2; `t08-*-gold-posted-date` |
| 64 | 2026-11-07 | T-08 | Preflight `SHOW TBLPROPERTIES` y `ALTER TABLE ... ADD CONSTRAINT` | **0 constraints previas**; **20 aplicadas** (10 dev + 10 prod) | 10 tablas | incluido | §12.3; `evidence/t08-constraints-api.txt`; `t08-*-preflight-properties*` |
| 65 | 2026-11-07 | T-08 | Inventario de `<fuente>_quarantine` | **0 tablas en dev y en prod**: sin histórico Delta para RF-6; no se creó ninguna a mano | `information_schema` | incluido | §12.4; `t08-dev-quarantine-inventory-empty.png` |
| 66 | 2026-11-07 | T-08 | `t04-audit-query.sql` (bloques 0–3) | **Bloque 0: 2.711 filas** (tabla disponible); bloques 1–3: **0 filas** con los filtros | `system.access.audit` | incluido | §12.5; `t08-audit-*` |
| 67 | 2026-11-07 | T-08 | `warehouses list` antes y después (API, sin SQL) | `Serverless Starter Warehouse` **`STOPPED`** antes y después | `53d8e3e91af6749c` | 0 € | §12.7 |
| 68 | 2026-11-07 | T-08 | Coste de la sesión | **~0,43 €** (1 sesión < 10 min); sin segunda sesión | — | ~0,43 € | §12.7; `docs/costs.md` |

### 12.1 Perfiles antes del DDL

| Catálogo | Tabla | `job_id_required`: filas / violaciones / nulos | `salary_range_order`: filas / violaciones / nulos min–max |
|---|---|---|---|
| dev | `silver.offers_indeed` | 3.414 / 0 / 0 | 3.414 / 0 / 2.359–2.359 |
| dev | `silver.offers_infojobs` | 2.741 / 0 / 0 | 2.741 / 0 / 1.667–1.668 |
| dev | `silver.offers_linkedin` | 13.461 / 0 / 0 | 13.461 / 0 / 13.362–13.411 |
| dev | `silver.offers_multi_site` | 10.259 / 0 / 0 | 10.259 / 0 / 3.420–3.552 |
| dev | `gold.fact_offers` | 27.742 / 0 / 0 | 27.742 / 0 / 18.915–19.093 |
| prod | `silver.offers_indeed` | 3.414 / 0 / 0 | 3.414 / 0 / 2.359–2.359 |
| prod | `silver.offers_infojobs` | 2.681 / 0 / 0 | 2.681 / 0 / 1.639–1.640 |
| prod | `silver.offers_linkedin` | 13.134 / 0 / 0 | 13.134 / 0 / 13.041–13.087 |
| prod | `silver.offers_multi_site` | 10.281 / 0 / 0 | 10.281 / 0 / 3.442–3.574 |
| prod | `gold.fact_offers` | 27.402 / 0 / 0 | 27.402 / 0 / 18.614–18.789 |

**Lectura:**

- `job_id` y orden salarial: **0 violaciones en las 10 tablas**. Ambas
  constraints eran aplicables.
- Los importes anuales nulos son masivos (gold prod: 18.614 y 18.789 de 27.402).
  Eso confirma que la forma explícita `IS NULL OR ...` es la que hace viable la
  constraint: la forma ingenua habría rechazado esas filas (§12.2 de RUNBOOK).
- Las tablas y los volúmenes son coherentes con T-01 (gold dev 27.742, prod
  27.402; las diferencias entre catálogos son de ingesta, no de la regla).

### 12.2 Regla de fecha (pospuesta)

| Catálogo | Tabla | Filas | Violaciones | Nulos | `min_date` | `max_date` |
|---|---|---|---:|---:|---|---|
| dev | `gold.fact_offers` | 27.742 | **138** | 0 | 2018-10-13 | 2026-11-04 |
| prod | `gold.fact_offers` | 27.402 | **138** | 0 | 2018-10-13 | 2026-11-02 |

**Decisión: la constraint de fecha no se aplica.** `IsValidPostingDate` es un
flag y las 138 filas anteriores a 2026 son legítimas para el diseño actual
(confirma Q7 de T-01). La regla queda **`[NO VERIFICADO]`** como constraint,
con medición y motivo; convertir el flag en rechazo exigiría una decisión de
producto y un tratamiento del histórico, no un `ADD CONSTRAINT` en esta spec.

### 12.3 Constraints aplicadas y verificadas

- **Preflight:** `SHOW TBLPROPERTIES` de las 5 tablas de cada catálogo **sin
  ninguna propiedad `delta.constraints.*`** (dev: capturas 1–5; prod: una
  captura, todas iguales). Es decir, el punto de partida era 0 constraints.
- **Aplicadas:** **20** (10 por catálogo; 5 `chk_<tabla>_job_id_required` y 5
  `chk_<tabla>_salary_range_order`), dev y prod, con el DDL de T-07.
- **Verificadas después por API gratuita** (sin despertar el warehouse):
  `docs/governance/evidence/t08-constraints-api.txt` lista las 20 propiedades y
  su expresión. **No se capturó el `SHOW TBLPROPERTIES` posterior**; la
  verificación autoritativa es la API.
- **La regla de fecha no generó DDL** y no hay nada que revertir por ella.
- Los `DROP CONSTRAINT` inversos siguen en `constraints-ddl.sql` (comentados)
  por si hay que revertir.

### 12.4 Cuarentenas históricas (RF-6)

- La consulta de existencia sobre `information_schema` devolvió **`No rows
  returned`**: no existe ninguna tabla `<fuente>_quarantine` en dev.
- En prod tampoco existen (comprobado por la persona; no se ejecutaron los
  bloques de inventario).
- Consecuencia: **no hay histórico Delta de cuarentena que inventariar**; RF-6
  parte de cero y la primera fila inválida creará la tabla con `mergeSchema`
  (ver `quality-inventory.md` §3.4). No se creó ninguna tabla a mano.

### 12.5 Auditoría de gobernanza (`t04-audit-query.sql`)

| Bloque | Resultado |
|---|---|
| 0 — disponibilidad | **`audit_rows_last_day = 2711`**: `system.access.audit` **está disponible**. |
| 1 — grants/ownership de `job_offers%`, `landing-loc%` y metastore | **0 filas** con los filtros y la ventana preparados. |
| 2 — grants recibidos/perdidos por un principal | **0 filas**. |
| 3 — ACL de los jobs | **0 filas**. |

**Interpretación:** la tabla de auditoría funciona (bloque 0), pero los filtros
no devolvieron eventos en la ventana: no se puede afirmar que los grants
temporales de T-02/T-04 aparezcan en ella (se hicieron el 2026-11-06 y la
ventana/parámetros no los capturaron). No se marca `[NO VERIFICADO]` la
disponibilidad —está medida—; queda registrado que **no hubo eventos
coincidentes**, sin inventar la causa. El bloque 4 (alternativa por CLI) no fue
necesario.

### 12.6 Matriz de reglas: aplicada o pospuesta

| `rule_id` | Perfil dev / prod | Acción | Estado tras T-08 |
|---|---|---|---|
| `missing_key` | no aplica (bronce) | Cuarentena | Activa (T-06); sin histórico Delta |
| `missing_scraped_at` | no aplica (bronce) | Cuarentena | Activa (T-06); sin histórico Delta |
| `company_name_rating` | no aplica (bronce) | Cuarentena | Activa (T-06); **0 filas históricas** (no hay tablas) |
| `job_id_required` | 0 / 0 | `CHECK` | **Aplicada**: 5 dev + 5 prod, verificada por API |
| `salary_range_order` | 0 / 0 | `CHECK` | **Aplicada**: 5 dev + 5 prod, verificada por API |
| `posted_date_contract` | 138 / 138 | Marca (flag) | **Pospuesta `[NO VERIFICADO]`**; sin DDL, histórico intacto |

### 12.7 Coste y límites

- **1 sesión SQL < 10 min: ~0,43 €** (anotado en `docs/costs.md`).
- Warehouse `STOPPED` antes y después (API, sin SQL).
- **No se abrió una segunda sesión**; no se despertó el warehouse para la
  verificación de constraints (API gratuita).
- Limitaciones: sin *query IDs* ni duración exacta en las capturas; sin captura
  del `SHOW TBLPROPERTIES` posterior; las capturas muestran la rejilla, no la
  sentencia. Ninguna de ellas invalida los conteos ni la verificación por API.

## 13. T-09 — validación, merge checkpoint y deploy de producción (2026-11-08)

> **Modo:** CLI/API (`--profile vs_code`), suite local y Git. **0 runs, 0 SQL,
> 0 €.** El deploy **no ejecuta** el job: la comprobación funcional es T-10.
> **Aprobación:** D-02 (2026-11-08) cubre el merge checkpoint, el deploy de
> prod, la EL en la raíz como desviación medida y la no retirada de las cuatro
> credenciales duplicadas.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 69 | 2026-11-08 | T-09 | `python -m pytest databricks_notebooks/tests -q` | **324 passed, 5 skipped** en 1709,64 s (28 min 29 s); los 12 Delta de T-07 siguen en aislamiento | repo | 0 € | `t09-prod-preflight.txt` |
| 70 | 2026-11-08 | T-09 | `databricks bundle validate -t dev` y `-t prod` desde la rama | **Validation OK** en los dos targets | bundle | 0 € | `t09-prod-preflight.txt` |
| 71 | 2026-11-08 | T-09 | Preflight de prod: grants, owners, job, constraints, storage y warehouse | Grants `{}` en catálogo/esquemas/tablas/EL/SC; owners persona; job **9 tareas**, `50 0 23 ? * SUN`, `Europe/Madrid`, `UNPAUSED`, `run_as` persona; **10 constraints**; warehouse `STOPPED`; 4 credenciales duplicadas sin EL | `job_offers`, `916863262593632`, `53d8e3e91af6749c` | 0 € | `t09-prod-preflight.txt` |
| 72 | 2026-11-08 | T-09 | `git merge --no-ff spec/012-security-governance` en `main` (clon principal) | Merge checkpoint **`9876765`**; sin tag, sin borrar rama ni worktree; el cambio local ajeno del fixture de infojobs no se tocó | `9876765` | 0 € | `git log main`; `t09-post-deploy.txt` |
| 73 | 2026-11-08 | T-09 | `databricks bundle validate -t prod` desde `main` | **Validation OK** | `main` | 0 € | `t09-post-deploy.txt` |
| 74 | 2026-11-08 | T-09 | `databricks bundle deploy -t prod` desde `main` | **36 ficheros subidos, 0 borrados; 4 recursos sin cambios**; job, cron, `run_as`, compute y tareas intactos. El wrapper de PowerShell devolvió 1 por la línea de progreso en stderr, no por fallo del deploy | `916863262593632` | 0 € | `t09-post-deploy.txt` |
| 75 | 2026-11-08 | T-09 | Verificación posterior por API (sin SQL) | `Data_Quality.py` y `Upsert_Job_Offers.py` desplegados (hash normalizado coincide); job **9 tareas** con cron y `run_as` intactos; **10 constraints**; grants `{}`; warehouse `STOPPED`; sin run nuevo | `job_offers`, `916863262593632` | 0 € | `t09-post-deploy.txt` |

**Qué queda desplegado y qué no se ha ejecutado:** el deploy sube el contrato de
calidad y la regla `company_name_rating`; **no ejecuta el job, no abre SQL y no
despierta el warehouse**. La comprobación real es T-10 (run programado del
**2026-11-08 23:00** `Europe/Madrid` y dashboard del **2026-11-09 01:00 UTC**).
No se ha creado tag ni se ha borrado rama/worktree.

**Declaración de no intervención de T-09:** no se aplicó ningún grant ni
revocación en prod; no se modificaron owners, storage, job, cron, compute ni
tareas; no se ejecutó ningún run; no se abrió el SQL warehouse; no se tocó la SAS
de Power BI ni la del dashboard. **Coste de desarrollo: 0 €.**

## 14. T-10 — comprobación real: run programado, permisos y dashboard (2026-11-09)

> **Modo:** run programado real, CLI/API de Databricks (`--profile vs_code`) y
> API pública de GitHub. **0 SQL, 0 runs de dev.** El run es el programado de
> producción del **2026-11-08 23:00** (`Europe/Madrid`); su coste va al
> presupuesto ordinario de producción. Sin cambios de grants, constraints,
> storage, job ni dashboard.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 76 | 2026-11-09 | T-10 | `databricks jobs list-runs --job-id 916863262593632 --limit 5 -o json` | Primero de la ventana: **`762969079133747`**, `PERIODIC`, `SUCCESS`, 9 tareas | `762969079133747` | producción | `t10-prod-run.txt` |
| 77 | 2026-11-09 | T-10 | `databricks jobs get-run 762969079133747 -o json` | Inicio `2026-11-08 22:00:51Z` (23:00:51 Madrid), fin `22:15:32Z`, **14 min 40 s**; **9/9 tareas SUCCESS**, luego la identidad `run_as` pudo leer/escribir todo lo necesario; cron, `run_as` persona y `UI_LOCKED` intactos | `762969079133747` | producción | `t10-prod-run.txt` |
| 78 | 2026-11-09 | T-10 | `jobs get-run-output` de `Bronze_Ingestion` (`413119331541619`) | **11.710 filas** de entrada (indeed 0, infojobs 922, linkedin 5.886, multi_site 4.902); **0 en cuarentena**; total = procesadas + cuarentena | `413119331541619` | producción | `t10-bronze-summary.txt` |
| 79 | 2026-11-09 | T-10 | `jobs get-run-output` de `Publish_Run_Summary` (`238014189201478`) | `record.status = ok`; `run_id` correcto; **4/4 checks en verde**: `bronze_summary`, `bronze_sources` (4/4), `freshness` (`data_date 2026-11-07`, edad 1 día), `pending_files` (`bronze_rows 11710`, `total 0`, `listing_ok true`) | `238014189201478` | producción | `t10-run-summary.txt` |
| 80 | 2026-11-09 | T-10 | `databricks tables get` de las 5 tablas de prod | **10 constraints presentes** (2 por tabla), con las expresiones de T-08; ninguna escritura del run las violó (9/9 SUCCESS) | 5 tablas | 0 € | `t10-constraints-and-grants.txt` |
| 81 | 2026-11-09 | T-10 | `grants get` de catálogo, 4 esquemas, 6 tablas, EL y SC; `jobs get` y `jobs get-permissions` | **`{}` en todos los grants**; job de **9 tareas**, cron y `run_as` intactos; persona `IS_OWNER` y `admins` `CAN_MANAGE` heredado de `/jobs/`; bronce sin tablas de cuarentena | `job_offers`, `916863262593632` | 0 € | `t10-constraints-and-grants.txt` |
| 82 | 2026-11-09 | T-10 | `databricks warehouses list -o json` | `53d8e3e91af6749c` **`STOPPED`** después; **`STOPPED`** antes en el preflight de T-09; sin SQL en T-10 | `53d8e3e91af6749c` | 0 € | `t10-warehouse.txt` |
| 83 | 2026-11-09 | T-10 | GitHub API: workflow run `42581592569` y sus jobs | **`success`**: build (checkout, ensamblado, descarga, validación, Pages, artefacto) y deploy; evento `schedule`; rama `main` | `42581592569` | 0 € | `t10-dashboard.txt` |
| 84 | 2026-11-09 | T-10 | Lectura pública de `https://darkaleja69.github.io/talent-market-lens/data/meta.json` | `data_date 2026-11-07`, `generated_at 2026-11-08T22:14:31Z`, `fact_offers 29.501` y `sha256` idénticos al export del run; 2 días de antigüedad (límite 8) | sitio publicado | 0 € | `t10-dashboard.txt` |
| 85 | 2026-11-09 | T-10 | Coste del run | Run ordinario de producción de **14 min 40 s**; **~0,18 €** estimados a 0,75 €/h; importe exacto del Account Console `[NO VERIFICADO]`; **0 € de dev** | `762969079133747` | ~0,18 € (producción) | `docs/costs.md`; `t10-prod-run.txt` |

**Lectura de T-10:**

- **Sin regresión:** las 9 tareas cierran en verde; el run no falla por ninguna
  constraint y `ops.run_metrics` recibe una fila con los cuatro checks en verde.
- **Conteos coherentes:** 11.710 filas de entrada, 11.710 procesadas y 0 en
  cuarentena; no se creó ninguna tabla `<fuente>_quarantine`, así que no hay
  histórico nuevo que reconciliar ni tratamiento que aplicar.
- **Gobernanza intacta:** los 10 constraints y el estado `{}` de grants siguen
  igual que tras T-08/T-09. El exceso de la cuenta personal sigue aprobado como
  limitación (D-01) y **no se afirma least privilege** sobre ella.
- **Dashboard:** el workflow publica el export del run; `data_date` es
  `2026-11-07`, dentro del límite de 8 días. El planificador de GitHub arrancó el
  workflow a las 07:48 UTC en vez de las 01:00 UTC del lunes, pero terminó en
  `success` el mismo día.
- **Limitación registrada:** `origin/main` seguía en `74f3c11` cuando corrió el
  workflow (el merge checkpoint `9876765` aún no se había publicado). El
  workflow no cambió y el dato se descarga de ADLS, por lo que la publicación es
  válida; el push de `main` se hace en el cierre de T-11.
- **Coste:** 0 SQL y 0 runs de dev; el run programado es ordinario de producción.

## 15. T-11 — cierre del track de certificación (2026-11-09)

> **Modo:** documentación, suite local y Git. **0 € adicionales.** La aprobación
> de la persona queda registrada en D-03; el cierre no se hace antes de T-10.

| # | Fecha | Tarea | Comando o prueba | Resultado | Identificador | Coste | Evidencia |
|---:|---|---|---|---|---|---:|---|
| 86 | 2026-11-09 | T-11 | `python -m pytest databricks_notebooks/tests -q` | **324 passed, 5 skipped** en 3380,65 s (56 min 20 s); los 12 Delta de T-07 siguen en aislamiento opt-in, como en T-09 | repo | 0 € | este documento |
| 87 | 2026-11-09 | T-11 | Nota, índice, roadmap, RUNBOOK §12.5, costes y D-03 | Documentación cerrada con el run real; roadmap con la 012 cerrada, bronce deduplicado 008/010 y dominio 7 con `run_as` persona y Managed Identity | `spec-012` | 0 € | `docs/databricks-de/notes/07-security-governance.md`; `roadmap.md`; `docs/RUNBOOK.md`; `docs/costs.md`; `decisions.md` |
| 88 | 2026-11-09 | T-11 | Merge final `--no-ff` a `main` desde `spec/012-security-governance`, tag anotado y retirada de rama/worktree | Merge final y tag `spec-012`; sin cambios de código pendientes; rama y worktree retirados según `AGENTS.md` | `spec-012` | 0 € | `git log`; este documento |

### 15.1 Mapa de requisitos funcionales

| RF | Estado | Evidencia o desviación |
|---|---|---|
| RF-1 | Cumplido | `access-matrix.md` (T-01/T-02/T-04): owners, grants directos/efectivos, operación y privilegio objetivo; consultas por API/CLI sin SQL. |
| RF-2 | Cumplido con desviación aprobada | Managed Identity dedicada inventariada; `run_as` persona y su exceso aprobados como limitación (D-01); Azure RBAC `[NO VERIFICADO]`; sin service principal. |
| RF-3 | Cumplido; auditoría con límite | Herencia y rollback ensayados en dev (T-04); `system.access.audit` disponible pero los filtros no devolvieron eventos de los grants temporales (`[NO VERIFICADO]` para esa relación). |
| RF-4 | Cumplido | Demo en Free Edition (T-03, §8): 8 filas autorizadas, 4 restringidas con `contact_email = '***'`, sin `SELECT` no hay acceso y limpieza verificada. |
| RF-5 | Cumplido | Contrato y cuarentena (`Data_Quality.py`, `Upsert_Job_Offers.py`); tests de T-05/T-06; run real con 11.710 filas y 0 en cuarentena. |
| RF-6 | Cumplido; sin histórico | Tratamiento por motivo en RUNBOOK §10.2; inventario de T-08 sin tablas de cuarentena; no se borró ni reprocesó histórico. |
| RF-7 | Cumplido con regla pospuesta | 20 constraints (10 dev + 10 prod) aplicadas y verificadas; `posted_date_contract` pospuesta `[NO VERIFICADO]` con 138 filas medidas. |
| RF-8 | Cumplido | Cuarentena/marca antes de la escritura (T-05/T-06/T-07); el run real no violó ninguna constraint y no falló. |
| RF-9 | Cumplido | T-09 deploy y T-10 run programado 9/9; dashboard publicado; warehouse `STOPPED`; sin regresión. |
| RF-10 | Cumplido | 0 runs de dev, 1 sesión SQL (~0,43 €), run de producción ordinario (~0,18 € estimados); todo en `docs/costs.md`. |
| RF-11 | Cumplido | Nota `07-security-governance.md`, índice y roadmap; dominio 7 con `run_as` persona y Managed Identity. |

**Hallazgos no bloqueantes:** `docs/es/RUNBOOK.md` sigue sin las secciones §11–§12
(desincronización ya anotada en T-04); no afecta a la operativa principal ni a
la comprobación real. El RBAC de Azure y la relación exacta de eventos de
auditoría siguen `[NO VERIFICADO]`; no se inventan.
