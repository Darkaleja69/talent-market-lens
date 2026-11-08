# Plan de grants — ensayo en dev y preflight de prod (spec 012, T-02)

> **Fecha:** 2026-11-06. **Tarea:** T-02 de `specs/012-Security-And-Governance`.
> **Perfil:** Databricks CLI `vs_code`. **Coste:** 0 € (API/CLI, sin SQL, sin runs).
> **Estado del modelo:** `run_as` es la persona, owner de todos los objetos y
> miembro de `admins`. **No existe identidad de automatización ni service
> principal** y T-02 no crea ninguna (RF-2). La decisión que aprueba el exceso
> como limitación es D-01 (`decisions.md`).
>
> Este documento contiene:
> 1. el **ensayo ya ejecutado** en dev (grant y revoke de un privilegio no
>    crítico), con su evidencia;
> 2. el **preflight de prod** (solo lectura, sin ejecutar cambios);
> 3. los **comandos hipotéticos** de una futura identidad de automatización, con
>    su inverso;
> 4. el **preflight de las 4 Storage Credentials duplicadas**, cuya retirada se
>    decide en T-09;
> 5. el **rollback y los criterios de parada** de producción.

## 1. Alcance y estado de partida

- **dev (`job_offers_dev`) y prod (`job_offers`):** catálogo, 4 esquemas de capa
  y 39 tablas con owner `<cuenta-personal>` y **ningún grant directo** (`{}` en
  todas las llamadas de `grants get`; ver `access-matrix.md` §1 y §3).
- **No hay grants de datos que revocar** ni privilegios que conceder de forma
  permanente con el modelo actual: reducir el privilegio efectivo exige migrar
  `run_as` a otra identidad, decisión separada y fuera de esta spec (RF-2, D-01).
- **T-02 no toca prod:** no concede, no revoca, no modifica ownership, storage,
  job ni bundle. Los comandos de **cambio** de §4–§6 quedan **preparados, sin
  ejecutar**; las lecturas de §3 y §5 se ejecutaron para la línea base (solo
  metadatos).
- **Único cambio ejecutado (autorizado):** `SELECT` temporal a `account users`
  sobre la tabla dev `job_offers_dev.ops.run_metrics`, verificado y revocado en
  el mismo acto (§2). No queda ningún grant residual.

## 2. Ensayo de ida y vuelta en dev (ejecutado el 2026-11-06)

Objeto no crítico: `job_offers_dev.ops.run_metrics` (tabla de métricas del run,
la escribe la tarea de cierre). Principal: `account users` (grupo de cuenta ya
existente; no se creó ninguna identidad). Privilegio: `SELECT` solo.

```powershell
# Estado de partida (esperado y obtenido: {} en los tres niveles)
databricks grants get table  job_offers_dev.ops.run_metrics --profile vs_code -o json
databricks grants get schema job_offers_dev.ops              --profile vs_code -o json
databricks grants get catalog job_offers_dev                 --profile vs_code -o json
databricks grants get-effective table job_offers_dev.ops.run_metrics `
  --principal "account users" --profile vs_code -o json

# Grant temporal (se usa @fichero para evitar problemas de quoting en PowerShell;
# la CLI v1.21 acepta --json inline o @fichero)
'{"changes":[{"principal":"account users","add":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-grant.json
databricks grants update table job_offers_dev.ops.run_metrics `
  --profile vs_code --json "@$env:TEMP\uc-grant.json"

# Comprobación inmediata: get y get-effective devuelven SELECT para account users
databricks grants get table job_offers_dev.ops.run_metrics --profile vs_code -o json
databricks grants get-effective table job_offers_dev.ops.run_metrics `
  --principal "account users" --profile vs_code -o json

# Reversión inmediata (inverso exacto)
'{"changes":[{"principal":"account users","remove":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-revoke.json
databricks grants update table job_offers_dev.ops.run_metrics `
  --profile vs_code --json "@$env:TEMP\uc-revoke.json"

# Confirmación final: {} en tabla, esquema, catálogo y get-effective
```

| Momento | `grants get` tabla | `get-effective` de `account users` | Evidencia |
|---|---|---|---|
| Antes | `{}` | `{}` | `evidence/t02-before-grants.txt` |
| Con el grant | `account users` → `SELECT` | `SELECT` | `evidence/t02-grant-applied.txt` |
| Tras el revoke | `{}` | `{}` | `evidence/t02-rollback-after.txt`, `t02-final-state.txt` |

**Lección del ensayo:** el grant se aplica en el nivel más específico (tabla
concreta), el privilegio mínimo cubre la operación y el inverso deja el objeto
exactamente en el estado inventariado (`{}`). `get-effective` refleja el grant
del principal; en cambio, para el owner devuelve `{}` porque el ownership no se
enumera como privilegio (ver §7).

## 3. Preflight de prod (solo lectura; ejecutar antes de T-09)

Se ejecuta **antes** de cualquier paso de T-09 y de nuevo si se aplicara algún
cambio. No abre el warehouse ni ejecuta SQL. Los valores esperados son los
medidos por T-01 el 2026-11-06 (`evidence/t02-prod-baseline.txt`).

```powershell
# Identidad y contenedores
databricks current-user me --profile vs_code -o json
databricks catalogs get job_offers --profile vs_code -o json
databricks schemas list job_offers --profile vs_code -o json

# Tablas por capa (nombre, tipo, formato, owner, constraints)
foreach ($s in 'bronze','silver','gold','ops') {
  databricks tables list job_offers $s --profile vs_code --omit-columns -o json
}

# Grants directos: catálogo, 4 esquemas y las 20 tablas (deben ser {})
databricks grants get catalog job_offers --profile vs_code -o json
foreach ($s in 'bronze','silver','gold','ops') {
  databricks grants get schema "job_offers.$s" --profile vs_code -o json
  $tables = databricks tables list job_offers $s --profile vs_code --omit-columns -o json | ConvertFrom-Json
  foreach ($t in $tables) {
    $full = "job_offers.$s.$($t.name)"
    "$full -> " + (databricks grants get table $full --profile vs_code -o json)
  }
}

# Storage: credencial referenciada y grants (deben ser {})
databricks external-locations list --profile vs_code -o json
databricks grants get external-location landing-loc --profile vs_code -o json
databricks grants get storage-credential `
  landing-loc_azuremanagedidentity_1791406770615 --profile vs_code -o json

# Job de producción: 9 tareas, run_as persona, schedule intacto
databricks jobs get 916863262593632 --profile vs_code -o json
databricks jobs get-permissions 916863262593632 --profile vs_code -o json

# Warehouse: debe seguir STOPPED (no despertarlo)
databricks warehouses list --profile vs_code -o json
```

**Valores esperados (medidos el 2026-11-06):**

| Comprobación | Esperado | Si no coincide |
|---|---|---|
| Grants de catálogo, esquemas y tablas | `{}` | **Parar**: hay un cambio no inventariado; no aplicar nada y reportar |
| Grants de `landing-loc` y de la storage credential | `{}` | **Parar** |
| Owner de catálogo/esquemas/tablas | `<cuenta-personal>` | **Parar**: se habría perdido la vía de administración |
| External Location del proyecto | `landing-loc`, raíz del contenedor, credential `…1791406770615` | **Parar** si cambia la URL o la credencial |
| Job `916863262593632` | 9 tareas, `run_as` persona, `50 0 23 ? * SUN` `Europe/Madrid`, `UNPAUSED` | **Parar** ante cualquier diferencia |
| ACL del job | persona `IS_OWNER` en la **ACL directa**; `admins` `CAN_MANAGE` **heredado de `/jobs/`** (no es ACL directa) | **Parar** si el owner cambia |
| Warehouse | `53d8e3e91af6749c` `STOPPED` | **No continuar**: no despertarlo |

**Caveat:** `grants get-effective` sobre el owner devuelve `{}` porque el
ownership no se enumera. Su ausencia **no** demuestra least privilege; la
limitación se registra en `access-matrix.md` §7 y D-01.

## 4. Grants mínimos de una identidad de automatización (hipotéticos)

> **No aplicar en esta spec.** No se crea el principal y no se migra `run_as`.
> Estos comandos son el punto de partida de una spec futura que decida migrar
> `run_as` a un service principal (D-01). Se escriben ya para que el cambio sea
> reproducible y reversible.

Supuestos de la simulación: principal `<sp-elt>` (service principal dedicado,
**no existe**), catálogo del entorno `<entorno>` (`job_offers_dev` o
`job_offers`). Los grants se conceden en el objeto más específico que cubre la
operación; solo `USE CATALOG`/`USE SCHEMA` y `CREATE TABLE` (si hace falta) van
al contenedor (§5.6 de `access-matrix.md`).

| Capa / tarea | Objeto | Privilegio mínimo | Motivo |
|---|---|---|---|
| Catálogo | `<entorno>` | `USE CATALOG` | contexto de todas las tareas |
| Todas las tareas | esquemas `bronze`, `silver`, `gold`, `ops` | `USE SCHEMA` | contexto de cada capa que toca |
| `Bronze_Ingestion` | `bronze.<fuente>` (4) | `SELECT`, `MODIFY` | `MERGE WITH SCHEMA EVOLUTION` |
| `Bronze_Ingestion` | `bronze.<fuente>_quarantine` (4) | `SELECT`, `MODIFY` | append y consulta de cuarentena |
| `Bronze_Ingestion` | esquema `bronze` | `CREATE TABLE` **solo si** las cuarentenas no se pre-crean fuera del job | alternativa preferida: pre-crear las 4 tablas y no conceder `CREATE TABLE` |
| `Silver_*` | `bronze.<fuente>` (4) | `SELECT` | lectura de origen |
| `Silver_*` | `silver.offers_<fuente>` y `silver.companies_<fuente>` (8) | `SELECT`, `MODIFY` | `MERGE`/`overwriteSchema` |
| `Silver_*` | esquema `silver` | `CREATE TABLE` **solo si** las 8 tablas no existen | las tablas existen: no se concede |
| `Gold_Complete_Catalog` | `silver.*` (8) | `SELECT` | lectura de origen |
| `Gold_Complete_Catalog` | `gold.*` (6) | `SELECT`, `MODIFY` | escritura con `overwriteSchema` |
| `Gold_Complete_Catalog` | esquema `gold` | `CREATE TABLE` **solo si** las 6 tablas no existen | las tablas existen: no se concede |
| `Publish_Run_Summary` | `ops.run_metrics` | `SELECT`, `MODIFY` | `MERGE` de métricas |
| `Publish_Run_Summary` | esquema `ops` | `CREATE TABLE` **solo si** `run_metrics` no existe | existe: no se concede |
| Auto Loader y exports | External Location `landing-loc` | `READ FILES`, `WRITE FILES` | leer crudo y escribir checkpoints/exports; `READ FILES` es obligatorio también para escribir |
| Job ELT | job `916863262593632` (y el de dev) | `CAN_MANAGE_RUN` | que el principal pueda ejecutar el job |

**Comandos (patrón reproducible):**

```powershell
$entorno = 'job_offers'          # usar job_offers_dev para ensayar en dev
$sp      = '<sp-elt>'            # hipotético: no crear en esta spec
$body    = Join-Path $env:TEMP 'uc-grant.json'

function Set-UcGrant([string]$type, [string]$name, [string[]]$add, [string[]]$remove) {
  $change = @{ principal = $sp }
  if ($add.Count)    { $change.add = $add }
  if ($remove.Count) { $change.remove = $remove }
  @{ changes = @($change) } | ConvertTo-Json -Depth 5 -Compress |
    Set-Content -Encoding ascii $body
  databricks grants update $type $name --profile vs_code --json "@$body"
}

# Contenedores
Set-UcGrant 'catalog' $entorno @('USE_CATALOG') @()
foreach ($s in 'bronze','silver','gold','ops') { Set-UcGrant 'schema' "$entorno.$s" @('USE_SCHEMA') @() }

# Bronce
$fuentes = 'indeed','infojobs','linkedin','multi_site'
foreach ($f in $fuentes) {
  Set-UcGrant 'table' "$entorno.bronze.$f"           @('SELECT','MODIFY') @()
  Set-UcGrant 'table' "$entorno.bronze.${f}_quarantine" @('SELECT','MODIFY') @()
}

# Silver (origen bronce + destino silver)
foreach ($f in $fuentes) {
  Set-UcGrant 'table' "$entorno.bronze.$f"           @('SELECT') @()
  Set-UcGrant 'table' "$entorno.silver.offers_$f"    @('SELECT','MODIFY') @()
  Set-UcGrant 'table' "$entorno.silver.companies_$f" @('SELECT','MODIFY') @()
}

# Gold (origen silver + destino gold)
$silver = @()
foreach ($f in $fuentes) { $silver += "offers_$f"; $silver += "companies_$f" }
foreach ($t in $silver) { Set-UcGrant 'table' "$entorno.silver.$t" @('SELECT') @() }
foreach ($t in 'fact_offers','fact_offer_skills','dim_calendar','dim_currency','dim_skill_list','companies_complete_catalog') {
  Set-UcGrant 'table' "$entorno.gold.$t" @('SELECT','MODIFY') @()
}

# Ops y almacenamiento
Set-UcGrant 'table' "$entorno.ops.run_metrics" @('SELECT','MODIFY') @()
Set-UcGrant 'external-location' 'landing-loc' @('READ FILES','WRITE FILES') @()

# ACL del job (no es un grant de UC). Se captura la ACL actual y se separa la
# DIRECTA (la persona con IS_OWNER) de la heredada: `admins` CAN_MANAGE viene de
# /jobs/ (`inherited: true`) y no se fija con jobs set-permissions.
databricks jobs get-permissions 916863262593632 --profile vs_code -o json |
  Set-Content -Encoding ascii $env:TEMP\job-acl-before.json
$acl = Get-Content $env:TEMP\job-acl-before.json -Raw | ConvertFrom-Json
$direct = @($acl.access_control_list | ForEach-Object {
  $entry = $_
  foreach ($p in $entry.all_permissions) {
    if (-not $p.inherited) {
      $e = @{ permission_level = $p.permission_level }
      if ($entry.user_name) { $e.user_name = $entry.user_name }
      if ($entry.group_name) { $e.group_name = $entry.group_name }
      if ($entry.service_principal_name) { $e.service_principal_name = $entry.service_principal_name }
      $e
    }
  }
})
@{ access_control_list = $direct } | ConvertTo-Json -Depth 5 -Compress |
  Set-Content -Encoding ascii $env:TEMP\job-acl-direct.json
@{ access_control_list = $direct + @(@{ service_principal_name = '<sp-elt>'; permission_level = 'CAN_MANAGE_RUN' }) } |
  ConvertTo-Json -Depth 5 -Compress | Set-Content -Encoding ascii $env:TEMP\job-acl.json
databricks jobs set-permissions 916863262593632 --profile vs_code --json "@$env:TEMP\job-acl.json"
```

**Inverso exacto (mismo orden, `remove` en lugar de `add`):**

```powershell
# En la misma sesión (o redefinir $fuentes y $silver como arriba).
# ACL del job: el inverso restaura SOLO la ACL directa guardada en
# job-acl-direct.json (la persona con IS_OWNER). `admins` CAN_MANAGE es heredado
# de /jobs/ y no forma parte de la ACL directa, así que no se fija con
# jobs set-permissions. Después, los grants de UC en orden inverso:
databricks jobs set-permissions 916863262593632 --profile vs_code --json "@$env:TEMP\job-acl-direct.json"
Set-UcGrant 'external-location' 'landing-loc' @() @('READ FILES','WRITE FILES')
Set-UcGrant 'table' "$entorno.ops.run_metrics" @() @('SELECT','MODIFY')
foreach ($t in 'fact_offers','fact_offer_skills','dim_calendar','dim_currency','dim_skill_list','companies_complete_catalog') {
  Set-UcGrant 'table' "$entorno.gold.$t" @() @('SELECT','MODIFY')
}
foreach ($t in $silver) { Set-UcGrant 'table' "$entorno.silver.$t" @() @('SELECT') }
foreach ($f in $fuentes) {
  Set-UcGrant 'table' "$entorno.silver.offers_$f"    @() @('SELECT','MODIFY')
  Set-UcGrant 'table' "$entorno.silver.companies_$f" @() @('SELECT','MODIFY')
  Set-UcGrant 'table' "$entorno.bronze.$f"           @() @('SELECT')
  Set-UcGrant 'table' "$entorno.bronze.$f"           @() @('MODIFY')
  Set-UcGrant 'table' "$entorno.bronze.${f}_quarantine" @() @('SELECT','MODIFY')
}
foreach ($s in 'bronze','silver','gold','ops') { Set-UcGrant 'schema' "$entorno.$s" @() @('USE_SCHEMA') }
Set-UcGrant 'catalog' $entorno @() @('USE_CATALOG')
```

**Comprobaciones antes de una migración real (hoy `[NO VERIFICADO]`):**

- `MERGE WITH SCHEMA EVOLUTION` y `saveAsTable(overwriteSchema=true)` con un
  principal **no owner**: confirmar en dev/Free Edition que `MODIFY` basta.
- `CREATE TABLE` en las cuarentenas: preferible pre-crearlas (T-06/T-09) y no
  conceder `CREATE TABLE` de esquema, que heredaría a todos los hijos futuros.
- El `run_as` migrado debe seguir pudiendo ejecutar el job: ACL `CAN_MANAGE_RUN`
  o `IS_OWNER`, y `run_as` del job cambiado de forma explícita (hoy no se toca).
- Un `get-effective` del service principal debe demostrar exactamente los
  privilegios de la tabla anterior; si aparece cualquiera de más, no se aplica.

## 5. Storage Credentials duplicadas (preflight ahora; retirada en T-09)

Medido el 2026-11-06: hay **5 credenciales del proyecto** que usan el access
connector `access-connector-joboffers` y **solo una** está referenciada por la
única External Location del proyecto:

| Storage Credential | ID | ¿Referenciada por una EL? |
|---|---|---|
| `landing-loc_azuremanagedidentity_1791406770615` | `2f54fa2c-5e5d-42be-92eb-ef571657d0f4` | **Sí**: `landing-loc` |
| `job-offers-access_azuremanagedidentity_1784834714439` | `0e78f15f-3ff5-4756-bca5-69d9474170bf` | No (duplicada) |
| `landing-loc_azuremanagedidentity_1791406375118` | `15b01141-9b25-4ab0-be85-334308536c89` | No (duplicada) |
| `landing_loc_azuremanagedidentity_1784832562230` | `73515c4d-700c-4379-9639-749818ebffcd` | No (duplicada) |
| `landing_loc_azuremanagedidentity_1784834657039` | `62442f4d-ec94-4a8f-9e45-4d5323be7d1f` | No (duplicada) |

**Preflight (solo lectura, ejecutar en T-09):**

```powershell
# 1) Inventario actual y referencia de cada credencial
databricks storage-credentials list --profile vs_code -o json
databricks external-locations list --profile vs_code -o json

# 2) Cada duplicada debe seguir sin EL que la referencie y sin grants
foreach ($c in 'job-offers-access_azuremanagedidentity_1784834714439',
               'landing-loc_azuremanagedidentity_1791406375118',
               'landing_loc_azuremanagedidentity_1784832562230',
               'landing_loc_azuremanagedidentity_1784834657039') {
  databricks storage-credentials get $c --profile vs_code -o json
  databricks grants get storage-credential $c --profile vs_code -o json
}
```

**Retirada (solo en T-09, con aprobación explícita y preflight en verde):**

```powershell
databricks storage-credentials delete job-offers-access_azuremanagedidentity_1784834714439 --profile vs_code
# ... una por una, comprobando que no está referenciada ...
```

**Rollback:** recrear con el mismo nombre y el `access_connector_id` exacto que
devuelva el preflight (no cambia; aquí aparece saneado como `<suscripcion>`). El
`id` de la credencial cambia; no se usa para nada del pipeline.

```powershell
$connector = (databricks storage-credentials get `
  landing-loc_azuremanagedidentity_1791406770615 --profile vs_code -o json |
  ConvertFrom-Json).azure_managed_identity.access_connector_id
@{ name = '<nombre>'; azure_managed_identity = @{ access_connector_id = $connector } } |
  ConvertTo-Json -Depth 5 -Compress | Set-Content -Encoding ascii $env:TEMP\storage-credential.json
databricks storage-credentials create --profile vs_code --json "@$env:TEMP\storage-credential.json"
```

**Criterio de parada:** si una duplicada está referenciada por una EL, tiene
grants o el preflight no puede demostrar que está libre, **no se borra**; se
documenta como `[NO VERIFICADO]` y se deja para otra tarea. La credencial en uso
(`…1791406770615`) no se toca nunca.

## 6. Rollback de prod y criterios de parada

Con el modelo actual **el conjunto de cambios de grants en prod es vacío**: no
hay grants que revocar y no se crea identidad, así que no hay inverso que
aplicar. Si en el futuro se aplicara §4, el rollback es el `REVOKE` exacto de
cada grant y la retirada de la entrada del principal en la ACL directa del job,
en orden inverso al de concesión.

| Paso | Comando de vuelta atrás |
|---|---|
| Grant de tabla | `grants update table … --json @remove.json` con `{"changes":[{"principal":"…","remove":["…"]}]}` |
| Grant de esquema/catálogo | ídem con `schema`/`catalog` |
| External Location | `grants update external-location landing-loc --json @remove.json` con `remove: ["READ FILES","WRITE FILES"]` |
| ACL del job | `jobs set-permissions … --json @job-acl-direct.json`: restaura **solo la ACL directa** (la persona con `IS_OWNER`). `admins` `CAN_MANAGE` es **heredado de `/jobs/`** y no se fija con `set-permissions`. |
| Credencial duplicada | recrearla con el mismo connector (§5) |

**Criterios de parada (no aplicar nada):**

1. Cualquier `grants get` de prod devuelve algo distinto de `{}` sin explicación.
2. El owner de un catálogo, esquema o tabla no es la persona.
3. El job no tiene 9 tareas, `run_as` no es la persona, el schedule cambió o está
   `PAUSED`.
4. El warehouse no está `STOPPED` (no se despierta para comprobarlo).
5. Una credencial duplicada está en uso o tiene grants.
6. Se necesita un token, SAS o permiso de Azure que no está disponible.
7. El preflight no puede demostrar el estado esperado por cualquier otro motivo.

**Último recurso de producción:** el job manual pausado `704156530681034`
descrito en `specs/007-ELT-As-Code/cutover.md`.

## 7. Qué hará T-09 con grants (con el modelo actual)

- **No aplicará ningún grant ni revocación en prod.** No hay nada que revocar
  (todo `{}`), no se crea identidad y no se migra `run_as` (D-01). El paso de
  grants de T-09 será una **verificación de no regresión**: repetir §3 y
  comprobar que dev y prod siguen sin grants, con los owners intactos y el job
  con 9 tareas.
- **No se afirmará least privilege** para la cuenta personal: su exceso por
  ownership y rol `admins` queda aprobado como limitación en D-01.
- **Storage:** **D-02 (2026-11-08) decide dejar las 4 credenciales duplicadas
  documentadas** tras el preflight de §5: ninguna EL de este workspace las
  referencia, pero no se puede excluir otro recurso fuera de él. No se borra
  nada en producción.
- **Fuera de esta spec:** migración de `run_as` a un service principal dedicado
  (spec futura) y reducción de la External Location a prefijos, **aprobada como
  desviación medida por D-02**.
