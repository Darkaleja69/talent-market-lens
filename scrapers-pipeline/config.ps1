# =============================================================================
#  config.ps1  -  Configuracion del pipeline scrapers -> ADLS -> Databricks
#  Editar estos valores. NO commitear el SAS token a git.
# =============================================================================

# ----- Azure / ADLS Gen2 -----------------------------------------------------
# El nombre real de la Storage Account (sin .dfs.core.windows.net) NO se
# versiona. Se resuelve en este orden:
#   1) config.local.ps1  (gitignored, tu configuracion local)
#   2) variable de entorno LANDING_STORAGE_ACCOUNT
# Ver config.example.ps1 para crear el tuyo.
# IMPORTANTE: debe tener HNS (Hierarchical Namespace) activado (ADLS Gen2).
$StorageAccount = $null
$Container      = $null
$LocalConfig = Join-Path $PSScriptRoot "config.local.ps1"
if (Test-Path -LiteralPath $LocalConfig) { . $LocalConfig }

if (-not $StorageAccount) { $StorageAccount = $env:LANDING_STORAGE_ACCOUNT }
if (-not $Container)      { $Container      = "landing" }
if ([string]::IsNullOrWhiteSpace($StorageAccount)) {
    Write-Warning "Storage Account no configurada. Crea config.local.ps1 (ver config.example.ps1) o define LANDING_STORAGE_ACCOUNT."
}

# SAS token con permisos Read+Add+Create+Write+List sobre el contenedor 'landing'.
# Usar generate_sas.ps1 para generarlo; se guarda en esta env var.
#   setx LANDING_SAS_TOKEN "<SAS>"
#  Nota: la validacion estricta se hace en run_scrapers_and_upload.ps1, no aqui,
#  para que generate_sas.ps1 pueda hacer . config.ps1 sin romper el huevo-gallina.
$SasToken = $env:LANDING_SAS_TOKEN
if ([string]::IsNullOrWhiteSpace($SasToken)) {
    Write-Warning "LANDING_SAS_TOKEN no definida. Ejecuta generate_sas.ps1 (FASE 2-bis)."
}

# ----- Paths locales ---------------------------------------------------------
# Raiz del workspace de projects (carpeta padre de scrapers-pipeline).
$ProjectsRoot = Split-Path -Parent $PSScriptRoot
$LogDir       = Join-Path $PSScriptRoot "logs"

# Carpeta local donde se mueven los parquet INVALIDOS (corruptos, sin columnas
# obligatorias, vacios) antes de subir. Asi nunca llegan a ADLS/Databricks.
$QuarantineRoot = Join-Path $PSScriptRoot "quarantine"

# Politica del trigger file _READY para Databricks:
#   'all'        = solo si TODOS los scrapers publicaron datos validos
#                  (conservador; comportamiento original del pipeline)
#   'any_valid'  = si AL MENOS UNO publico datos validos (un fallo parcial
#                  no bloquea el procesamiento del resto)
$ReadyPolicy = "any_valid"

# ----- AzCopy ----------------------------------------------------------------
# AzCopy esta en C:\Tools\azcopy\azcopy.exe (ya anadido al PATH machine).
# Si por alguna razon no lo encontrase, descomenta la siguiente linea:
# $AzCopyPath   = "C:\Tools\azcopy\azcopy.exe"
$AzCopyPath   = "azcopy"

# ----- Definicion de scrapers ------------------------------------------------
# Cada scraper se lanza via su wrapper .ps1. Los wrappers gestionan sus propios
# timeouts/reintentos/locks (no se duplican con nuestro pipeline).
#
#   Name              : nombre logico (carpeta en ADLS y tabla Bronze)
#   Wrapper           : path absoluto al wrapper .ps1 (o "" para lanzar directo)
#   Format / Ext      : 'parquet' o 'csv'
#   OutDir            : carpeta donde el scraper deja los archivos del dia
#   RenameToTimestamp : $true si el wrapper sobreescribe un archivo fijo
#                       (jobs.parquet / jobs_unified.parquet). El pipeline hara
#                       una COPIA con sufijo de timestamp para que Autoloader la
#                       detecte como archivo nuevo.
#   CanonicalFile     : nombre del archivo fijo del wrapper (solo si
#                       RenameToTimestamp=$true). El pipeline copia UNICAMENTE
#                       este archivo, nunca las copias historicas *_YYYYMMDD_*,
#                       para evitar que se multipliquen exponencialmente.
#
# ATENCION Indeed: requiere que ANTES de lanzar el pipeline tengas un Chrome
# abierto con --remote-debugging-port=9222 y sesion iniciada en indeed.com.
# Si no, el wrapper abortara con exit=3.
#
#   RequiredColumns   : columnas que DEBEN existir en cada parquet del scraper.
#                       Si falta alguna, el fichero se rechaza y va a
#                       cuarentena (nunca se sube a ADLS).
#   CoherenceSite     : nombre del esquema para la validacion de coherencia de
#                       filas en ensure_compatible.py (--coherence). Si no se
#                       define, la validacion de coherencia se omite.
$Scrapers = @(
    # ---- 1) Indeed ----
    @{
        Name   = "indeed"
        Format = "parquet"
        Wrapper = "$ProjectsRoot\indeed_jobs_scraper\run_nightly_indeed.ps1"
        OutDir = "$ProjectsRoot\indeed_jobs_scraper\output"
        Ext    = "parquet"
        RequiredColumns = @("job_key","title","company","viewjob_url","scraped_at")
        CoherenceSite = "indeed"
    }

    # ---- 2) LinkedIn ----
    #  Sobreescribe data\output\jobs.parquet en cada run -> RenameToTimestamp.
    #  jobs.parquet es un SNAPSHOT ACUMULATIVO (todo el historico del run):
    #  con OnlyNewOffers=$true el pipeline filtra las ofertas ya subidas
    #  (uploaded_keys/linkedin.json) y sube SOLO las nuevas, en vez del
    #  snapshot completo (causa de millones de filas duplicadas en bronze).
    @{
        Name              = "linkedin"
        Format            = "parquet"
        Wrapper           = "$ProjectsRoot\linkedin_jobs_scraper\run_nightly.ps1"
        OutDir            = "$ProjectsRoot\linkedin_jobs_scraper\data\output"
        Ext               = "parquet"
        RenameToTimestamp = $true
        CanonicalFile     = "jobs.parquet"
        OnlyNewOffers     = $true
        KeyColumn         = "job_id"
        RequiredColumns   = @("job_id","job_url","title","company_name","scraped_at")
        CoherenceSite = "linkedin"
    }

    # ---- 3) Multi-site ----
    #  Lanza 4 subscrapers en paralelo y mergea a data\merged\jobs_unified.parquet.
    #  Sobreescribe jobs_unified.parquet en cada run -> RenameToTimestamp.
    #  Igual que LinkedIn: snapshot acumulativo -> OnlyNewOffers=$true para
    #  subir solo las ofertas no subidas previamente.
    @{
        Name              = "multi_site"
        Format            = "parquet"
        Wrapper           = "$ProjectsRoot\multi_site_job_scraper\run_all.ps1"
        OutDir            = "$ProjectsRoot\multi_site_job_scraper\data\merged"
        Ext               = "parquet"
        RenameToTimestamp = $true
        CanonicalFile     = "jobs_unified.parquet"
        OnlyNewOffers     = $true
        KeyColumn         = "job_id"
        RequiredColumns   = @("job_id","job_url","title","company_name","scraped_at")
        CoherenceSite = "multi_site"
    }

    # ---- 4) InfoJobs ----
    #  Wrapper propio con retry/lock/timeout (patron coherente con los demas).
    #  Genera data\offers_YYYYMMDD_HHMMSS.parquet (timestamped, no rename).
    @{
        Name   = "infojobs"
        Format = "parquet"
        Wrapper = "$ProjectsRoot\infojobs_jobs_scraper\run_infojobs_nightly.ps1"
        OutDir = "$ProjectsRoot\infojobs_jobs_scraper\data"
        Ext    = "parquet"
        RequiredColumns = @("id_oferta","titulo","empresa","url_oferta","fecha_scraped")
        CoherenceSite = "infojobs"
    }
)