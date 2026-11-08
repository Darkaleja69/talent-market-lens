# Matriz de acceso — inventario y objetivo (spec 012, T-01, T-02 y T-04)

> **Fecha:** 2026-11-06. **Tarea:** T-01, T-02 y T-04 (actualizado 2026-11-06)
> de `specs/012-Security-And-Governance`.
> **Método:** T-01 fue solo lectura por Databricks CLI/API (`--profile vs_code`)
> y lectura de código; T-02 hizo un grant temporal en dev, verificado y revertido
> en el mismo acto; T-04 hizo el ensayo de **herencia** (grant temporal en el
> esquema dev, verificado con `get-effective` y revocado en el mismo acto).
> **Sin SQL, sin abrir el SQL warehouse, sin runs, sin cambios en prod.**
> **Coste:** 0 €. Evidencia saneada en `docs/governance/evidence/`.
>
> Este documento tiene dos partes que no deben confundirse:
> 1. **Estado actual medido** (lo que la API devuelve hoy).
> 2. **Objetivo y rollback** (ensayado en dev en T-02 y T-04; comandos de prod
>    preparados para T-09, **sin aplicar en prod**).
>
> **No se afirma least privilege.** El estado actual es «sin grants explícitos y
> todo por ownership»: la persona que ejecuta el job es propietaria de todos los
> objetos y miembro de `admins`, así que su privilegio efectivo es máximo. Ese
> exceso se mide y se registra abajo como limitación/desviación aprobada (D-01).

## 1. Resumen de hallazgos

| # | Hallazgo | Consecuencia |
|---|---|---|
| H1 | **No hay ningún grant directo en los objetos del proyecto**: `job_offers_dev`, `job_offers`, sus 8 esquemas de capa, sus 39 tablas, la Storage Credential ni la External Location (`{}` en todas las llamadas; control positivo con `get-effective` sobre `samples`). **El metastore sí tiene ACL** (H10). | El acceso a los datos no se ha repartido: depende solo de ownership. No hay «lectores reales» de datos que revocar. |
| H2 | **El owner de los 2 catálogos, de los 8 esquemas de capa y de las 39 tablas es la persona** (la misma que ejecuta el job). | El `run_as` del job dispone de **todas las capacidades por ownership** en cada objeto. Reducirlo exige migrar `run_as` a otra identidad: decisión separada, fuera de esta spec. |
| H3 | **La Storage Credential es Azure Managed Identity**, no service principal: `azure_managed_identity.access_connector_id = access-connector-joboffers` (RG `projects-workspace`). | No hay service principal que reducir; el estándar de least privilege se aplica a la Managed Identity y a su Azure RBAC. |
| H4 | La identidad de almacenamiento es **dedicada al proyecto** por nombre y resource group: un único access connector (`access-connector-joboffers`, RG `projects-workspace`) sirve a dev y prod; el workspace de sandbox usa otro (`unity-catalog-access-connector`, RG del sandbox). | «Dedicada» en el sentido de la spec: es la MI del proyecto y no la del workspace de sandbox. Dev y prod **sí comparten** la misma MI y el mismo contenedor. Que ningún otro recurso ajeno al proyecto reutilice ese connector es `[NO VERIFICADO]` (requiere Azure). |
| H5 | **Hay 5 Storage Credentials del proyecto y solo 1 la usa la External Location**; las otras 4 no están referenciadas por ninguna EL. | Higiene de gobernanza: credenciales duplicadas del mismo access connector. Candidatas a limpieza en T-09 **tras verificar que no las usa nada más**. |
| H6 | La External Location `landing-loc` cubre **la raíz del contenedor** (`abfss://landing@joboffershns.dfs.core.windows.net/`), no un prefijo del pipeline. | READ/WRITE FILES alcanza crudo, checkpoints, almacenamiento gestionado de UC y ambos exports. Exceso de alcance real; ver §7. |
| H7 | **No existen las tablas `bronze.<fuente>_quarantine`** (8 comprobaciones, dev y prod: «does not exist»). | El esquema de cuarentena no es verificable por metadatos; se deriva del código. Ver `quality-inventory.md`. |
| H8 | El job tiene **9 tareas**, `run_as` persona y `edit_mode: UI_LOCKED`; ACL: persona `IS_OWNER`, grupo `admins` `CAN_MANAGE` heredado de `/jobs/`. | El job corre con privilegios de owner sobre todos los objetos del catálogo del entorno. La ACL no añade nadie más. |
| H9 | Power BI y el dashboard público leen ADLS por **SAS de solo lectura por prefijo** (`gold/pbi_export`, `gold/web_export`), fuera de Unity Catalog. | No necesitan ni deben recibir grants UC. La comprobación de esa vía es independiente (T-09/T-10). |
| H10 | **El metastore sí tiene grants**: `account users` → `USE_MARKETPLACE_ASSETS`; el grupo de cuenta de administradores del workspace → 9 `CREATE_*` (catálogo, clean room, connection, external location, provider, recipient, service credential, share, storage credential). | Son privilegios **administrativos**: no dan `SELECT`/`MODIFY` ni heredan a los objetos de datos. La capa de administración no está «sin ACL»; lo que no hay es acceso a datos por grant. |
| H11 | El grupo `admins` tiene **dos miembros activos** (`141486372609033` y `146517773436349`) con el mismo `externalId` y el mismo nombre: dos registros SCIM de la misma identidad de Entra. | Ambos heredan `CAN_MANAGE` sobre los 3 jobs. Ninguno de los dos tiene grants UC directos. La decisión sobre el registro canónico y una eventual retirada del duplicado queda **fuera de la 012** (hoy el ownership y el `run_as` están en `141486372609033`). |

## 2. Identidades y grupos

| Principal | Tipo | ID | Estado actual | Evidencia |
|---|---|---|---|---|
| `<cuenta-personal>` | Usuario | `141486372609033` | Miembro directo de `admins`, `users` y `users-clone-2026-08-05-2234-UTC (created by Databricks)`. Owner de catálogos, esquemas, tablas, storage credential, external location y de los 3 jobs ELT. | `evidence/t01-current-user.txt`, `t01-catalogs.txt`, `t01-schemas-and-tables.txt`, `t01-storage.txt`, `t01-jobs.txt` |
| `<persona>` (segundo registro) | Usuario | `146517773436349` | Miembro directo de `admins` y `users-clone-…`; **mismo `externalId` (`b5a26491-…`) y mismo nombre** que `141486372609033`: duplicado de la misma identidad de Entra. No es owner de ningún objeto del proyecto, no tiene grants UC directos y no aparece en el `run_as` de ningún job. | `evidence/t01-identities.txt` |
| `admins` | Grupo de workspace | `151347827406338` | **Dos miembros activos** (`141486372609033` y `146517773436349`). `CAN_MANAGE` **heredado** sobre los 3 jobs desde `/jobs/`. **Recibe los 9 `CREATE_*` en el metastore** (vía el grupo de cuenta de administradores del workspace). Sin grants en catálogos/esquemas/tablas del proyecto. | `evidence/t01-jobs.txt`, `t01-grants-*`, `t01-identities.txt`, `t01-metastore-grants.txt` |
| `users` | Grupo de workspace | `150981975206808` | Sin grants en los objetos del proyecto. | `evidence/t01-grants-direct.txt` |
| `users-clone-2026-08-05-2234-UTC (created by Databricks)` | Grupo de workspace | `2126387033071238` | Sin grants en los objetos del proyecto. Grupo creado por la plataforma; no se usa para el pipeline. | `evidence/t01-objects-extra.txt` |
| `access-connector-joboffers` | Managed Identity de Azure (no es principal UC) | credential id `d3be66a9-21c6-42eb-9ff7-afc99c8eb550` y `d937de4b-7d28-45b9-a78d-3216c59fb183` | Autoriza la Storage Credential usada por `landing-loc`. No aparece en ACL de UC. | `evidence/t01-storage.txt` |
| UUID `9e033aa4-…`, `f4ae815d-…`, `421bd159-…` | Principal no resuelto | — | Aparecen como `updated_by` de catálogos/esquemas, `creator_name` del warehouse y `updated_by` de tablas/credenciales. | `evidence/t01-catalogs.txt`, `t01-table-metadata-detail.txt`, `t01-objects-extra.txt` |

**Riesgo abierto (H11): identidad duplicada.** Los dos registros activos de
`admins` comparten `externalId` (`b5a26491-86cd-4adc-a86f-61717bf8463b`) y nombre:
son la **misma identidad de Entra** representada dos veces en el workspace (una
con `userName` `#ext#@…onmicrosoft.com` y otra con el correo directo). Hoy los
owners, el `run_as` y los 3 jobs están en `141486372609033`; el otro registro no
tiene ownership ni grants, pero **sí hereda `CAN_MANAGE` sobre los 3 jobs** por
pertenecer a `admins`. Decisión pendiente y **fuera de la 012**: confirmar cuál
es el registro canónico y si el duplicado se retira o se deja documentado como
legado (no se toca en T-01 ni en T-02).

**`[NO VERIFICADO]`:** a qué principal corresponden esos UUID (posible service
principal de plataforma). La API de workspace no los resuelve a un nombre legible
y no se ha consultado la Account Console ni Microsoft Entra. No se ha leído
ninguna credencial.

## 3. Inventario de objetos Unity Catalog

### 3.1 Metastore

| Principal | Privilegios en el metastore | ¿Da acceso a datos? | Fuente |
|---|---|---|---|
| `account users` | `USE_MARKETPLACE_ASSETS` | No | `evidence/t01-metastore-grants.txt` |
| `_workspace_admins_dbw_dataengineering_sandbox_weurope_001_7405619889654446` | `CREATE_CATALOG`, `CREATE_CLEAN_ROOM`, `CREATE_CONNECTION`, `CREATE_EXTERNAL_LOCATION`, `CREATE_PROVIDER`, `CREATE_RECIPIENT`, `CREATE_SERVICE_CREDENTIAL`, `CREATE_SHARE`, `CREATE_STORAGE_CREDENTIAL` | **No** (son privilegios administrativos; los grants de metastore no heredan a catálogos/esquemas/tablas) | `evidence/t01-metastore-grants.txt` |

Metastore `metastore_azure_westeurope` (`9be7f9ac-2bf3-4f70-9e81-4e754169af3d`):
owner `System user`, `external_access_enabled: false`, `delta_sharing_scope: INTERNAL`,
`privilege_model_version: 1.0` (`evidence/t01-objects-extra.txt`).

**Lectura correcta:** *sí* hay ACL en el metastore, y la capa de administración del
workspace recibe los `CREATE_*` (incluidos `CREATE_EXTERNAL_LOCATION` y
`CREATE_STORAGE_CREDENTIAL`, que son capacidades de administración de storage).
Esos privilegios **no conceden `SELECT`/`MODIFY`** sobre los catálogos del
proyecto ni heredan hacia abajo. `account users` solo puede usar assets de
Marketplace.

*Precisión sobre `admins`:* la persona pertenece al grupo de workspace `admins`
(`151347827406338`), y el metastore concede los `CREATE_*` al grupo de cuenta de
administradores de este workspace
(`_workspace_admins_dbw_dataengineering_sandbox_weurope_001_7405619889654446`).
La API demuestra la concesión al grupo de cuenta, pero **no expande la
pertenencia** al consultar `get-effective` con el principal: devuelve las
asignaciones del metastore sin resolver quién es miembro. Por tanto, la capacidad
administrativa se registra como **la que la ACL demuestra para el rol de
administrador del workspace**, no como un privilegio personal enumerado.

### 3.2 Catálogos

| Objeto | Tipo | Owner | Grants directos | `storage_root` | Evidencia |
|---|---|---|---|---|---|
| `job_offers_dev` | `MANAGED_CATALOG`, `OPEN` | `<cuenta-personal>` | ninguno (`{}`) | `abfss://landing@joboffershns.dfs.core.windows.net/managed/_unitystorage/catalogs/19bc1d62-…_dev` | `t01-catalogs.txt` |
| `job_offers` | `MANAGED_CATALOG`, `OPEN` | `<cuenta-personal>` | ninguno (`{}`) | `abfss://landing@joboffershns.dfs.core.windows.net/managed` | `t01-catalogs.txt` |

### 3.3 Esquemas

| Catálogo | Esquema | Owner | Grants directos | Filas de tabla |
|---|---|---|---|---|
| `job_offers_dev` | `bronze`, `silver`, `gold`, `ops` | `<cuenta-personal>` | ninguno | 4 / 8 / 6 / 1 |
| `job_offers_dev` | `default` | `<cuenta-personal>` | ninguno | 0 |
| `job_offers_dev` | `information_schema` | `System user` | no aplica (sistema) | — |
| `job_offers` | `bronze`, `silver`, `gold`, `ops` | `<cuenta-personal>` | ninguno | 4 / 9 / 6 / 1 |
| `job_offers` | `default` | `<cuenta-personal>` | ninguno | 0 |
| `job_offers` | `information_schema` | `System user` | no aplica (sistema) | — |

Fuente: `evidence/t01-schemas-and-tables.txt`.

### 3.4 Tablas

**39 tablas** (19 en dev, 20 en prod), **todas `MANAGED`, formato `DELTA`, owner
`<cuenta-personal>`, sin grants directos**. Sin funciones ni volumes en **los 12
esquemas** de ambos catálogos (8 de capa + `default` + `information_schema`)
(`evidence/t01-objects-extra.txt`).

| Capa | Dev | Prod |
|---|---|---|
| `bronze` | `indeed`, `infojobs`, `linkedin`, `multi_site` | igual |
| `silver` | `offers_*` (4), `companies_*` (4) | `offers_*` (4), `companies_*` (4) + `linkedin_companies` (legado) |
| `gold` | `fact_offers`, `fact_offer_skills`, `dim_calendar`, `dim_currency`, `dim_skill_list`, `companies_complete_catalog` | igual |
| `ops` | `run_metrics` | `run_metrics` |

Detalle por tabla (tipo, formato, owner, constraints, filas de estadística) en
`evidence/t01-schemas-and-tables.txt`; tipos de columna clave en
`evidence/t01-table-metadata-detail.txt`.

### 3.5 Almacenamiento

| Objeto | Identidad | Ámbito | Grants directos | Observación |
|---|---|---|---|---|
| Storage Credential `landing-loc_azuremanagedidentity_1791406770615` (`2f54fa2c-…`) | **Azure Managed Identity**, access connector `access-connector-joboffers` (`projects-workspace`) | Contenedor `landing` (RBAC `[NO VERIFICADO]`) | ninguno | Es la **única** referenciada por la External Location. `read_only: false`, `isolation_mode: OPEN` |
| Storage Credentials `job-offers-access_…14439`, `landing-loc_…375118`, `landing_loc_…562230`, `landing_loc_…657039` | misma Managed Identity (`d3be66a9`/`d937de4b`) | Contenedor `landing` (RBAC `[NO VERIFICADO]`) | ninguno | **No referenciadas por ninguna EL**: duplicados (H5) |
| Storage Credential `dbw_dataengineering_sandbox_weurope_001` | Managed Identity, connector `unity-catalog-access-connector` | Almacenamiento del workspace de sandbox | ninguno | Ajena al pipeline |
| External Location `landing-loc` | credential `landing-loc_azuremanagedidentity_1791406770615` | **raíz** `abfss://landing@joboffershns.dfs.core.windows.net/` | ninguno | `fallback: false`, `enable_file_events: false` |

Fuente: `evidence/t01-storage.txt`.

**Observación de consistencia:** el mismo access connector
(`access-connector-joboffers`) aparece con **dos `credential_id` distintos**
(`d3be66a9-…` en `job-offers-access_…`/`landing_loc_…` y `d937de4b-…` en
`landing-loc_…`). Puede ser un connector recreado o dos identidades asociadas.
Cuál de las dos tiene el RBAC efectivo sobre el contenedor es `[NO VERIFICADO]`
(requiere Azure) y debe comprobarse antes de retirar credenciales en T-09.

**Clasificación pedida por la spec:**

- **Service principal: NO existe** para el almacenamiento. La identidad es
  **Azure Managed Identity** (campo `azure_managed_identity`).
- **Dedicada o compartida: dedicada al proyecto.** Un solo access connector
  (`access-connector-joboffers`, RG `projects-workspace`) con nombre propio del
  proyecto; el sandbox usa otro connector distinto
  (`unity-catalog-access-connector`). Que el RG se comparta con otros proyectos no
  cambia la identidad, pero que **otro recurso reutilice este connector** es
  `[NO VERIFICADO]`. Está **compartida entre dev y prod** del mismo proyecto
  (misma MI, mismo contenedor, misma EL).
- **Azure RBAC de la MI: `[NO VERIFICADO]`.** Requiere leer asignaciones de rol
  en Azure (`az role assignment list` sobre el access connector y el storage
  account) y T-01 solo está autorizada a Databricks con `--profile vs_code`. No
  se ha leído ni copiado ninguna clave.

## 4. Job: `run_as`, ACL y tareas

| Job | ID | Nombre | `run_as` | Tareas | Schedule | Mode |
|---|---|---|---|---|---|---|
| Producción | `916863262593632` | `Git Based ELT` | **persona** (`run_as_user_name` y `settings.run_as.user_name`) | **9** | `50 0 23 ? * SUN`, `Europe/Madrid`, `UNPAUSED` | Bundle, `UI_LOCKED` |
| Dev | `320947022571947` | `[dev …] Git Based ELT Dev` | **persona** (`run_as_user_name`) | **9** | sin schedule | Bundle, `UI_LOCKED` |
| Rollback (legado) | `704156530681034` | `Git Based ELT` | persona (implícito en `run_as_user_name`) | 7 | `PAUSED` | manual, pausado |

**Tareas (9, ambas):** `Bronze_Ingestion`, `Silver_Indeed`, `Silver_Infojobs`,
`Silver_Linkedin`, `Silver_Multi_site`, `Gold_Complete_Catalog`,
`Build_for_Web-Dashboard`, `Build_Report_Export`, `Publish_Run_Summary`.

**ACL de los tres jobs** (`evidence/t01-jobs.txt`):

| Principal | Nivel | Origen |
|---|---|---|
| `<cuenta-personal>` | `IS_OWNER` | directo (`inherited: false`) |
| `admins` | `CAN_MANAGE` | **heredado** de `/jobs/` (`inherited: true`) |

Cluster del job: `DATA_SECURITY_MODE_DEDICATED` (single user), `Standard_D4ds_v4`,
runtime `17.3.x-scala2.13`. No hay grants de UC ligados al job: el job usa el
principal `run_as`, de ahí que su ACL no conceda datos.

## 5. Matriz por principal y objeto

> **Columnas «privilegio objetivo» y «rollback»: referencia para una futura
> identidad de automatización (y para la decisión de storage de T-09); no
> aplicadas en prod.** T-02 ensayó un grant en dev y lo revirtió; todo grant nuevo
> tiene como inverso su `REVOKE`, que devuelve el objeto al estado inventariado
> aquí (`{}`). No hay nada que restaurar hoy porque no hay grants.

### 5.1 Persona `run_as` (hoy también administradora)

| Objeto | Estado actual | Operación que lo justifica | Privilegio objetivo mínimo de la operación | Fuente | Rollback |
|---|---|---|---|---|---|
| `job_offers_dev` / `job_offers` | owner ⇒ ALL | Contexto de todas las tareas | `USE CATALOG` | `catalogs get`, `grants get` | `ALTER CATALOG … OWNER TO <persona>` (no se toca) |
| `bronze` (catálogo del entorno) | owner ⇒ ALL | Bronze lee/escribe bronce y cuarentena | `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` | `bronze_ingest.ipynb`, `Upsert_Job_Offers.merge_sql` | `REVOKE` del grant añadido |
| `silver` | owner ⇒ ALL | Silver lee bronce y hace MERGE/overwrite en silver | `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` | `silver_*.ipynb`, `Silver_Incremental.merge_sql` | ídem |
| `gold` | owner ⇒ ALL | Gold lee silver y sobrescribe gold | `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` | `gold_build.ipynb`, `Prepare_Gold.write_gold` | ídem |
| `ops` | owner ⇒ ALL | `run_metrics` se crea si falta y se hace MERGE | `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` | `run_summary.ipynb`, `Run_Metrics.ensure_table/merge_sql` | ídem |
| 39 tablas | owner ⇒ ALL | lectura y escritura de cada capa | `SELECT` (+ `MODIFY` solo en las tablas que escribe el job) | `t01-grants-tables.txt`, código | ídem |
| External Location `landing-loc` | owner ⇒ ALL | Auto Loader lee el landing y escribe checkpoints; exports escriben Parquet | `READ FILES` + `WRITE FILES` (READ es obligatorio también para escribir) | `bronze_ingest.ipynb`, `run_summary.ipynb`, `Prepare_Web_Export`/`Prepare_PBI_Export`, [privileges reference](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference) | `ALTER EXTERNAL LOCATION … OWNER TO <persona>` / `REVOKE` |
| Storage Credential | owner | Solo se necesita para **crear** EL/tablas externas; el job no la usa por nombre | ninguno en runtime (se conserva al owner como vía de administración) | `t01-storage.txt` | `ALTER STORAGE CREDENTIAL … OWNER TO <persona>` |
| Jobs ELT (3) | `IS_OWNER` | ejecutar/editar el pipeline | `IS_OWNER` (vía de administración) | `t01-jobs.txt` | `jobs set-permissions` con la **ACL directa** capturada (la persona `IS_OWNER`). `admins` `CAN_MANAGE` es **heredado de `/jobs/`** (`inherited: true`) y **no se fija ni se retira** con esa llamada |

**Exceso de esta fila:** la persona **no** tiene el mínimo de la operación: tiene
ALL por ownership (H2). No se reduce en esta spec porque el `run_as` actual no se
migra sin decisión explícita (RF-2). Queda como **limitación/desviación aprobada**
(§7).

### 5.2 Grupo `admins` (dos miembros activos)

| Objeto | Estado actual | Operación que lo justifica | Privilegio objetivo | Fuente | Rollback |
|---|---|---|---|---|---|
| Jobs ELT | `CAN_MANAGE` **heredado de `/jobs/`** para **ambos miembros** (`141486372609033` y `146517773436349`) | administración del workspace | se conserva (no es un grant del proyecto y no se puede revocar por job) | `t01-jobs.txt`, `t01-identities.txt` | no aplica: no se modifica |
| Metastore | 9 `CREATE_*` sobre el grupo de cuenta de administradores del workspace; `account users` solo `USE_MARKETPLACE_ASSETS` | administración del workspace (crear catálogos, EL, storage credentials…) | se conserva como vía de administración; no da acceso a datos | `t01-metastore-grants.txt` | `REVOKE … ON METASTORE FROM …` (solo si se decidiera reducir; hoy no se toca) |
| Catálogos/esquemas/tablas del proyecto | **sin grants** | — | ninguno | `t01-grants-*` | no aplica |
| Grants UC directos de `146517773436349` | **ninguno** (no es owner de nada) | — | ninguno | `t01-identities.txt`, `t01-grants-*` | no aplica |

Lo que la API **sí demuestra**: el metastore concede los `CREATE_*` al grupo de
cuenta de administradores del workspace, y la persona pertenece al grupo de
workspace `admins`. Lo que **no** demuestra: la expansión de esa pertenencia al
consultar `get-effective` (devuelve las asignaciones sin resolver miembros). No se
afirma un privilegio personal enumerado; se registra la capacidad administrativa
del rol y la expansión de la membresía efectiva queda `[NO VERIFICADO]` (la API
no la resuelve y T-02 no consultó Azure/Account Console); la existencia de la ACL
sí está medida.

### 5.3 Grupos `users` y `users-clone-…`

| Objeto | Estado actual | Operación que lo justifica | Privilegio objetivo | Fuente | Rollback |
|---|---|---|---|---|---|
| Todos los objetos del proyecto | **sin grants** | ninguno | ninguno (no se añaden) | `t01-grants-direct.txt`, `t01-grants-tables.txt` | no aplica |

### 5.4 Managed Identity de almacenamiento

| Objeto | Estado actual | Operación que lo justifica | Privilegio objetivo | Fuente | Rollback |
|---|---|---|---|---|---|
| Storage Credential + External Location | MI con acceso al contenedor `landing` (RBAC `[NO VERIFICADO]`) | leer crudo + checkpoints, escribir checkpoints y exports, servir el almacenamiento gestionado de UC | `READ FILES`/`WRITE FILES` limitados a los prefijos del pipeline; sin `MANAGE`, sin `CREATE MANAGED STORAGE` salvo al crear catálogos | `t01-storage.txt` | recrear la EL con la misma URL y credential; `DROP` de lo creado |

**Exceso de esta fila:** la EL cubre la raíz del contenedor y la MI es la misma
para dev y prod, crudo, checkpoints, managed y exports (H4/H6). Reducirlo a
prefijos exige **crear External Locations por prefijo** y, para el RBAC de Azure,
contenedores separados o condiciones ABAC: Azure RBAC no se asigna por prefijo
con una asignación estándar. Es un cambio de configuración de storage, no un
grant: se decide en T-09 y hoy queda como **desviación medida**.

### 5.5 Lectores reales

| Lector | Objeto | Estado actual | Observación |
|---|---|---|---|
| Power BI (`Job_Offers_Dashboard.pbip`) | `gold/pbi_export` en ADLS | SAS de solo lectura en credenciales del dataset | **No usa Unity Catalog**; no requiere grant UC |
| Workflow `dashboard.yml` (GitHub Pages) | `gold/web_export` en ADLS | secret `WEB_EXPORT_SAS`, SAS de solo lectura por prefijo | **No usa Unity Catalog**; no requiere grant UC |
| Otros usuarios del workspace | catálogos del proyecto | sin grants | No hay lectores UC que inventariar |

### 5.6 Mapa de operaciones reales del código por capa

Privilegios **mínimos que exige cada operación** (referencia de privilegios de
Unity Catalog). `MODIFY` exige además `SELECT` y los `USE` de los contenedores.
Hoy el owner los tiene todos; esta columna es la referencia de privilegios
mínimos para una futura identidad de automatización (ver `grants-plan.md` §4).

| Tarea / módulo | Operación real | Objetos | Privilegio mínimo |
|---|---|---|---|
| `bronze_ingest.ipynb` + `Upsert_Job_Offers.py` | Auto Loader lee Parquet de `landing/<fuente>/` y escribe schema/checkpoint en `_checkpoints*/<fuente>/` | ADLS | `READ FILES` + `WRITE FILES` en la External Location (READ es obligatorio también para escribir) |
| ídem | `MERGE WITH SCHEMA EVOLUTION INTO bronze.<fuente>` (la tabla **debe existir**; el código falla si no) | `bronze.<fuente>` | `SELECT`, `MODIFY` |
| ídem | `append` + `mergeSchema` a `bronze.<fuente>_quarantine` (la crea la primera vez) | `bronze.<fuente>_quarantine` | `CREATE TABLE` (esquema `bronze`), `SELECT`, `MODIFY` |
| `Bronze_Summary.py` | Resume `lastProgress`; **no lee tablas ni landing** | — | ninguno (solo memoria) |
| `silver_*.ipynb` + `Silver_Incremental.py` + `Enrich…py` + `Deduplicate_Job_Offers.py` | Lee bronce y publica `silver.offers_<fuente>` con `MERGE WITH SCHEMA EVOLUTION`; reconstrucción completa con `saveAsTable(overwriteSchema=true)` | `bronze.<fuente>` (S), `silver.offers_<fuente>` (S+M) | `SELECT`, `MODIFY`; `CREATE TABLE` si la tabla no existe |
| ídem | Recalcula `silver.companies_<fuente>` con `saveAsTable(overwriteSchema=true)` | `silver.offers_<fuente>` (S), `silver.companies_<fuente>` (S+M) | `SELECT`, `MODIFY`; `CREATE TABLE` si no existe |
| `gold_build.ipynb` + `Prepare_Gold.py` | Lee las 4 silver de ofertas y las 4 de compañías; escribe 5 tablas gold + `companies_complete_catalog` con `overwriteSchema=true` | `silver.*` (S), `gold.*` (S+M) | `SELECT`, `MODIFY`; `CREATE TABLE` si no existe |
| `web_export_build.ipynb` + `Prepare_Web_Export.py` | Lee gold y escribe/renombra/borra Parquet + `meta.json` en `gold/web_export[_dev]/` | `gold.*` (S), ADLS (W) | `SELECT`; `READ FILES` + `WRITE FILES` |
| `report_export_build.ipynb` + `Prepare_PBI_Export.py` | Ídem en `gold/pbi_export[_dev]/` (6 Parquet + `meta.json`) | `gold.*` (S), ADLS (W) | `SELECT`; `READ FILES` + `WRITE FILES` |
| `run_summary.ipynb` + `Run_Metrics.py` | Lee `meta.json` con `dbutils.fs.head`, lista `landing/<fuente>/` y `_checkpoints*/<fuente>/ckpt/commits`, y hace `MERGE` en `ops.run_metrics` (la crea si falta) | ADLS (R), `ops.run_metrics` (S+M+CREATE) | `READ FILES`; `SELECT`, `MODIFY`, `CREATE TABLE` en `ops` |
| **Ninguna** tarea | `CREATE SCHEMA` | — | **No se usa**: los esquemas se crearon fuera del job (el `ops` lo crea la CLI, según `Run_Metrics.ensure_table`) |
| Power BI (`Job_Offers_Dashboard.pbip`) | Lee `gold/pbi_export/*.parquet` por `AzureStorage.DataLake` con `PBI_EXPORT_SAS` (read-only) | ADLS | **Ninguno en UC**: vía SAS independiente |
| Workflow `dashboard.yml` | Descarga `gold/web_export/*` con `WEB_EXPORT_SAS` (read-only) y publica en Pages | ADLS | **Ninguno en UC**: vía SAS independiente |

Notas de precisión:

- `MERGE WITH SCHEMA EVOLUTION` se ejecuta con `MODIFY` sobre la tabla destino;
  que la evolución de esquema no exija un privilegio adicional con un principal
  **no owner** sigue `[NO VERIFICADO]`: no hay principal no owner con el que
  probarlo y se comprobará en la spec futura de identidad; hoy solo lo garantiza
  el ownership.
- `saveAsTable(..., overwriteSchema=true)` sobre una tabla existente requiere
  `MODIFY`; si la tabla no existe, `CREATE TABLE` en el esquema. El
  comportamiento exacto con un principal no owner sigue `[NO VERIFICADO]` hasta
  que exista ese principal (spec futura).
- `CREATE SCHEMA` no lo necesita ninguna tarea del job; los privilegios de
  creación de catálogo/esquema son de administración, no del pipeline.

## 6. Herencia: qué hereda quién hoy (medido en T-04 el 2026-11-06)

**Grants heredan hacia abajo; medido.** Ensayo de ida y vuelta en dev, autorizado
y con coste 0 €: `SELECT` temporal de `account users` sobre el **esquema**
`job_offers_dev.ops` (no sobre una tabla).

| Momento | `grants get` tabla `run_metrics` | `get-effective` tabla de `account users` | Evidencia |
|---|---|---|---|
| Antes | `{}` | `{}` | `evidence/t04-before-grants.txt` |
| Con el `SELECT` en el esquema | `{}` (el grant es del esquema, no de la tabla) | `SELECT` con `inherited_from_name: job_offers_dev.ops` y `inherited_from_type: SCHEMA` | `evidence/t04-grant-applied-inherited.txt` |
| Control en **otro** esquema (`bronze.indeed`) | `{}` | `{}` | `evidence/t04-grant-applied-inherited.txt` |
| Tras el `REVOKE` | `{}` | `{}` | `evidence/t04-rollback-after.txt`, `t04-final-state.txt` |

Lectura del ensayo:

- `grants get` devuelve **solo lo asignado explícitamente**; `grants
  get-effective` devuelve **asignado + heredado**. Para inventariar hacen falta
  los dos: mirar solo `get` oculta la herencia y mirar solo `get-effective`
  oculta de dónde viene.
- Que `get-effective` incluya `SELECT` **no demuestra que el principal pueda
  leer**: el ensayo no concedió `USE CATALOG`/`USE SCHEMA` (solo midió la
  herencia del `SELECT`) y, por la regla de UC, sin esos `USE` la lectura
  seguiría fallando. Los `USE` no dan datos y los datos no se leen sin los
  `USE`.
- El grant en esquema alcanza a **todos los hijos presentes y futuros** de ese
  esquema (incluida `run_metrics`) y a ninguno de otro esquema. Por eso el
  proyecto concede en tabla salvo `USE CATALOG`/`USE SCHEMA`; `CREATE TABLE` en
  un esquema solo se plantea si las cuarentenas no se pre-crean fuera del job
  (`grants-plan.md` §4).
- El inverso exacto (`remove` del mismo privilegio en el mismo objeto) devuelve
  el estado a `{}` sin residuo: el rollback de un grant de UC es simétrico.
- El ensayo concede **a propósito en el esquema** para poder medir la herencia:
  es la excepción controlada a la regla de RF-3 de conceder en la tabla. La
  regla operativa no cambia: en tabla salvo `USE CATALOG`/`USE SCHEMA` (y el
  `CREATE TABLE` justificado de las cuarentenas).

**Qué hereda una tabla nueva.** Una tabla nueva no nace con grants directos:
hereda los privilegios vigentes en su catálogo/esquema y el principal que la
crea pasa a ser su owner. Consecuencia práctica: conceder `SELECT` en un esquema
también publica las tablas futuras, y crear una tabla con una identidad de
automatización la deja a nombre de esa identidad (habría que transferir el
ownership o crear las tablas fuera del job).

**Ownership no es `MANAGE`** (contrastado con la documentación oficial de Unity
Catalog el 2026-11-06; el detalle que T-01 dejó abierto queda resuelto):

| Capacidad | Owner | `MANAGE` |
|---|---|---|
| Todas las capacidades del objeto | Sí, **implícitas**; la API no las devuelve como `ALL PRIVILEGES` | No: cada privilegio se concede aparte (quien lo tiene puede autoconcederse) |
| Conceder/revocar, transferir ownership, borrar | Sí | Sí |
| Gestionar los objetos hijos | Sí, implícito sobre todos los hijos | Sí, heredado al concederlo en un contenedor |
| Número de principales | **Uno solo** | Tantos como haga falta |
| Privilegios de uso (`USE`) | No sobre el objeto propio | No en el nivel donde se tiene, sí en los contenedores superiores |
| Incluido en `ALL PRIVILEGES` | No aplica | **No** |

- **Ownership no hereda hacia abajo**: el owner de un catálogo no pasa a ser
  owner de sus esquemas ni tablas, pero **sí obtiene la capacidad de gestionar
  todos los hijos** (equivalente funcional a `MANAGE`, no concedido de forma
  explícita). Para este inventario es irrelevante: la persona es owner **directo
  en los tres niveles** (2 catálogos, 8 esquemas, 39 tablas).
- Por eso `get-effective` de la persona devuelve `{}`: **no es falta de acceso**,
  es que el ownership no se enumera como privilegio. `{}` no prueba least
  privilege.
- `MANAGE` sí es un grant explícito, **hereda a los hijos** cuando se concede en
  un contenedor, **no concede datos por sí mismo** y no está incluido en
  `ALL PRIVILEGES`. No se reparte en este proyecto.
- **Grants de metastore no heredan** a catálogos/esquemas/tablas (la excepción
  documentada es `READ METADATA`). Los 9 `CREATE_*` del grupo de administradores
  son administrativos y no dan acceso a datos (H10).
- **ACL de jobs**: `admins` recibe `CAN_MANAGE` por la ACL de `/jobs/` (nivel
  workspace), no por una concesión del bundle. Cambiar la ACL directa del job no
  quita ese permiso heredado; el rollback con `jobs set-permissions` restaura
  **solo la ACL directa**.
- **External Location**: un grant de `READ FILES`/`WRITE FILES` en `landing-loc`
  alcanza **todo** el contenedor, presente y futuro (H6).
- Las SAS de Power BI y del dashboard son **independientes** de UC: no heredan ni
  reciben grants; su alcance es el prefijo y los permisos `r`/`l` de la firma.

## 7. Exceso medido y no reducible (limitación / desviación aprobada o pendiente)

1. **La cuenta personal es owner de todo y miembro de `admins`.** El job corre
   como ella. Aunque cada operación necesite solo `SELECT`/`MODIFY`, su efectivo
   es «todas las capacidades del owner» (la API no lo devuelve como
   `ALL PRIVILEGES`). **No se afirma least privilege.** Migrar `run_as` a un service
   principal dedicado exige crear una identidad y cambiar el job: fuera de esta
   spec (RF-2), decisión separada y con su propio presupuesto.
   **Aprobado como limitación por D-01 (2026-11-06):** no se migra `run_as`, no
   se crean identidades y el exceso se documenta sin afirmar que quedó mínimo.
2. **La identidad de almacenamiento es una MI dedicada al proyecto pero con
   alcance de contenedor completo**, compartida dev/prod. La EL no está
   particionada por prefijo y el RBAC de Azure de la MI no es comprobable con las
   credenciales de esta tarea (`[NO VERIFICADO]`). Reducir el alcance exige
   cambios de storage (nuevas EL por prefijo y, para Azure, contenedores
   separados o condiciones ABAC); se registra como desviación, no como mínimo.
   **D-02 (2026-11-08) la aprueba como desviación medida** para cerrar T-09; no
   se cambia el storage en la 012.
3. **Cuatro Storage Credentials duplicadas** de la misma MI, sin EL que las
   referencie. No es una escalada (misma identidad), pero amplía la superficie de
   administración. **T-02 no las retira** (D-01): solo deja el preflight
   preparado en `grants-plan.md` §5. **D-02 (2026-11-08) decide dejarlas
   documentadas**: el preflight demuestra que ninguna EL de este workspace las
   referencia, pero no puede excluir otro recurso fuera del workspace; no se
   borra nada en producción. El rollback, si se retiraran, sería recrearlas con
   el mismo access connector.
4. **El warehouse `Serverless Starter Warehouse`** (`53d8e3e91af6749c`,
   2X-Small, `PRO`, serverless) está `STOPPED`. No se ha abierto (T-02 lo
   reverificó el 2026-11-06). No forma parte de la matriz de datos, pero su
   creador es el UUID `9e033aa4-…` (`[NO VERIFICADO]`).
5. **Hay dos registros de usuario de la misma identidad en `admins`** (H11). El
   duplicado no tiene ownership ni grants, pero hereda `CAN_MANAGE` de los jobs.
   No es un exceso de datos; es una decisión de identidad pendiente (§8.6) que
   D-01 no decide ni T-02 toca.

**Estado de aprobación (2026-11-08):** el punto 1 queda **aprobado como
limitación** por D-01; el punto 2 (EL en la raíz) queda **aprobado como
desviación medida** por D-02; el punto 3 (credenciales duplicadas) queda
**documentado y sin retirar** por D-02; los puntos 4 y 5 son inventario y no
requieren cambio. T-02 no cierra afirmando que las identidades quedaron mínimas.

## 8. Qué hizo T-02 y qué queda preparado para T-09

1. **No crear identidades nuevas** (RF-2): no hay service principal y no se crea
   uno solo para el temario. Se conserva `run_as` persona y su camino de
   administración (D-01).
2. **No hay grants de datos que revocar** en los objetos del proyecto (los del
   metastore son administrativos y se conservan). El trabajo de T-02 era
   *definir y ensayar en dev* el grant y su inverso: se ejecutó el **2026-11-06**
   con `SELECT` de `account users` sobre `job_offers_dev.ops.run_metrics`,
   verificado con `grants get`/`get-effective` y **revocado en el mismo acto**;
   el estado volvió a `{}` (`grants-plan.md` §2,
   `evidence/t02-rollback-after.txt`). No se aplicó ningún grant permanente en
   dev. Los comandos de una futura identidad de automatización quedan en
   `grants-plan.md` §4, **sin ejecutar**.
3. **Preflight por operación** antes de cualquier cambio: el de prod está
   preparado en `grants-plan.md` §3, con los valores esperados de la matriz §5 y
   el estado `{}` como punto de restauración.
4. **Cualquier cambio de storage** (EL por prefijo, retirada de credenciales
   duplicadas) se trata como cambio aparte, con su propio rollback y sin tocar la
   SAS de Power BI ni la del dashboard. T-02 solo prepara el preflight de las 4
   credenciales duplicadas (`grants-plan.md` §5); **D-02 (2026-11-08) decide
   dejarlas documentadas** y aprueba la EL en la raíz como desviación medida
   antes de T-09.
5. **Comprobación de no regresión:** el primer run programado (T-10) debe cerrar
   las 9 tareas; si falla por permisos, se aplica el inverso documentado.
6. **Decisión sobre la identidad duplicada (H11):** no se decide en la 012. Se
   mantiene como está: el duplicado no es owner ni `run_as` de nada y no tiene
   grants; si una tarea futura lo retirara, se dejaría constancia en el RUNBOOK.
7. **T-09 no aplicará ningún grant en prod con el modelo actual** (D-01): no hay
   nada que revocar y no se crea identidad. Su paso de grants será una
   **verificación de no regresión** (todo `{}`, owners intactos, job de 9 tareas,
   storage sin cambios).

### 8.1 T-04 — ensayo de herencia y reversión (2026-11-06)

Autorizado por la persona y ejecutado con CLI/API, **0 €, sin SQL, sin runs y sin
tocar prod**:

1. **Estado de partida comprobado `{}`** en `grants get schema
   job_offers_dev.ops`, `grants get table job_offers_dev.ops.run_metrics` y
   `get-effective` de `account users` (`evidence/t04-before-grants.txt`).
2. **Grant temporal en el esquema** (el nivel intermedio, no la tabla):
   `SELECT` de `account users` sobre `job_offers_dev.ops`. La tabla siguió con
   `{}` en `grants get`, pero `get-effective` de `run_metrics` devolvió `SELECT`
   con `inherited_from_name: job_offers_dev.ops` y `inherited_from_type: SCHEMA`;
   el control en `bronze.indeed` siguió `{}`
   (`evidence/t04-grant-applied-inherited.txt`).
3. **Revocación inmediata** con el inverso exacto; `get` de esquema, tabla y
   catálogo y `get-effective` volvieron a `{}`
   (`evidence/t04-rollback-after.txt`, `t04-final-state.txt`).
4. **Prod sin cambios** (`job_offers` catálogo/esquema/tabla en `{}`) y
   **warehouse `STOPPED`** (`evidence/t04-no-touch.txt`).
5. **Operativa publicada:** sección §11 del `docs/RUNBOOK.md` (matriz,
   herencia, comandos, SAS y diagnóstico UC vs ADLS) y consulta de auditoría
   `t04-audit-query.sql` **preparada para T-08, sin ejecutar**. `docs/es/RUNBOOK.md`
   sigue desincronizado y queda pendiente.

La lección queda en §6: `grants get` no muestra la herencia, `get-effective` sí,
y el inverso deja el objeto exactamente como estaba.

## 9. Comandos de verificación (solo lectura, 0 €)

```powershell
databricks current-user me --profile vs_code -o json
databricks catalogs get job_offers_dev --profile vs_code -o json
databricks schemas list job_offers_dev --profile vs_code -o json
databricks tables list job_offers_dev bronze --profile vs_code --omit-columns -o json
databricks grants get catalog job_offers_dev --profile vs_code -o json          # esperado: {}
databricks grants get table job_offers.gold.fact_offers --profile vs_code -o json # esperado: {}
databricks grants get-effective catalog job_offers_dev --principal <persona> --profile vs_code -o json
databricks jobs get 916863262593632 --profile vs_code -o json
databricks jobs get-permissions 916863262593632 --profile vs_code -o json
databricks storage-credentials list --profile vs_code -o json
databricks external-locations list --profile vs_code -o json
databricks grants get metastore 9be7f9ac-2bf3-4f70-9e81-4e754169af3d --profile vs_code -o json
databricks groups get 151347827406338 --profile vs_code -o json
databricks warehouses list --profile vs_code -o json                            # esperado: STOPPED
```

**Qué debe verse:** owners = la persona; `{}` en todos los `grants get` de los
objetos del proyecto; **dos asignaciones en el metastore** (`account users` →
`USE_MARKETPLACE_ASSETS` y el grupo de admins del workspace → 9 `CREATE_*`);
`azure_managed_identity` (no `azure_service_principal`) en las credenciales; una
sola EL (`landing-loc`) con URL en la raíz del contenedor; job de 9 tareas con
`run_as` persona; **dos miembros** en el grupo `admins`; warehouse `STOPPED`.

**Herencia y auditoría (T-04):** el patrón de ida y vuelta con `grants get`,
`grants get-effective` y `grants update … --json` está en
`docs/RUNBOOK.md` §11.3, y su resultado medido en §8.1 de este documento. La
consulta de auditoría `t04-audit-query.sql` **no se ha ejecutado**: se reserva
para la sesión única de T-08.

## 10. Pendientes y verificados

> La tabla mantiene los pendientes abiertos y la fila de **Free Edition**,
> verificada el 2026-11-07 en T-03 (ruta B).

| Tema | Motivo |
|---|---|
| Azure RBAC de la Managed Identity (roles y ámbito sobre `joboffershns`) | Requiere Azure CLI/Portal; T-01/T-02/T-04 solo están autorizadas a Databricks. |
| Cuál de los dos `credential_id` del access connector tiene el RBAC efectivo | Mismo motivo; el connector aparece con `d3be66a9…` y `d937de4b…`. |
| Expansión de la pertenencia de `admins` al grupo de cuenta que recibe los `CREATE_*` del metastore | La ACL del metastore sí está medida (H10); lo que la API no hace es expandir miembros al consultar `get-effective`. Queda `[NO VERIFICADO]`: T-02 no consultó la Account Console. |
| Principal real de los UUID `9e033aa4-…`, `f4ae815d-…`, `421bd159-…` | La API de workspace no los resuelve. |
| Privilegios exactos del owner enumerados por la API | En los objetos del proyecto `grants get-effective` devuelve `{}`: UC no enumera los privilegios implícitos por ownership (en el metastore sí devuelve las asignaciones, pero sin expandir miembros). |
| Row filters/column masks y perfiles de Free Edition | **Verificado el 2026-11-07** en Free Edition (catálogo `workspace`, esquema `governance_lab`): soporta filtros y máscaras, pero **no permite conceder permisos a grupos** (ruta A no viable). La demo se hizo con la **ruta B** (dos usuarios reales), coste 0 €; detalle en `evidence.md` §8. |
| Necesidad exacta de privilegios para `MERGE WITH SCHEMA EVOLUTION` y `overwriteSchema` con un principal no owner | Hoy lo garantiza el ownership; queda `[NO VERIFICADO]` y se validará en la spec futura de identidad con un principal no owner **solo en dev**. |
| Disponibilidad de `system.access.audit`, permisos, retención y nombre exacto del evento de cambio de ownership en UC | T-04 no ejecuta SQL; la consulta queda preparada (`t04-audit-query.sql`) y se comprueba en la sesión única de T-08. `changeSecurableOwner` es del metastore Hive legado (`sqlPermissions`), no de UC. |
