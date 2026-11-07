# Evidencia — gobernanza (spec 012)

> **Fecha de las comprobaciones:** 2026-11-06 (T-01, T-02 y T-04) y 2026-11-07
> (T-03, ejecución real en Free Edition).
> **Tareas:** T-01 (inventario, solo lectura), T-02 (ensayo de grant/revoke en
> dev y preflight de prod), T-03 (laboratorio de row filter y column mask
> **ejecutado el 2026-11-07 en Free Edition**, ruta B) y T-04 (ensayo de herencia
> en dev, RUNBOOK de gobernanza y consulta de auditoría preparada).
> **Modo:** CLI/API de Databricks (`--profile vs_code`) y lectura local de código;
> T-03 lo ejecutó la persona a mano en Databricks Free Edition.
> **0 runs, 0 sentencias SQL del proyecto, 0 €.** No se ejecutó SQL en el
> workspace pagado: el laboratorio de T-03 vive en **Free Edition** (§8). Los
> únicos cambios de recursos del proyecto son los grants temporales de T-02 y
> T-04, aplicados, verificados y revocados en el mismo acto (§5 y §7); los
> objetos sintéticos de T-03 se crearon y se retiraron dentro de Free Edition
> (§8).
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
