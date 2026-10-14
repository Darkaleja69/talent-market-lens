<#
.SYNOPSIS
    Descarga la instantanea del export web (gold/web_export) a docs/data/ y,
    opcionalmente, sirve docs/ en local con las mismas rutas que GitHub Pages.

.DESCRIPTION
    Herramienta de desarrollo (RF-11): reproduce el contrato de ficheros del
    sitio publico sin pasar por el workflow de CI. Descarga los 5 ficheros de
    nombre fijo del prefijo gold/web_export con la SAS de solo lectura y
    AzCopy ya existentes (LANDING_SAS_TOKEN / config.local.ps1, igual que el
    pipeline), sobrescribiendo docs/data/ (gitignored).

    NUNCA sube ni publica nada: las unicas operaciones remotas son `azcopy
    list` (dry run) y `azcopy copy` blob -> local. La SAS no se imprime en
    ningun mensaje ni log (se enmascara como <SAS oculta>).

.PARAMETER DryRun
    Muestra el plan (origen enmascarado, destino y tamanos remotos si se
    pueden listar) sin descargar nada. Funciona aunque falten la SAS, la
    storage account o AzCopy, para poder revisar el plan en cualquier maquina.

.PARAMETER Serve
    Tras la descarga (o el dry run), arranca un servidor estatico de Python
    sobre docs/ con el mismo puerto que usa el proyecto. El servidor corre en
    primer plano: se detiene con Ctrl+C o cerrando la consola; no queda ningun
    proceso huerfano.

.PARAMETER Port
    Puerto del servidor estatico (por defecto 8123, el que usa el proyecto).

.PARAMETER Help
    Muestra la ayuda (tambien con -?).

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\export_web_snapshot.ps1 -DryRun

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\export_web_snapshot.ps1

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\export_web_snapshot.ps1 -Serve

.NOTES
    El contrato remoto (parametros por defecto del notebook web_export_build):
    cinco ficheros fijos en <container>/gold/web_export/. El destino local es
    <repo>/docs/data/ y la pagina dashboard/ referencia ../data/<fichero>,
    igual en local que en Pages.

    Como parar el servidor: corre en primer plano en esta consola; pulsa
    Ctrl+C o cierra la ventana. Si se mata solo el proceso de PowerShell, el
    hijo de Python quedaria huerfano: usa Ctrl+C (o cierra la consola) para
    parar ambos.
#>
param(
    [switch]$DryRun,
    [switch]$Serve,
    [int]$Port = 8123,
    [Alias("?")]
    [switch]$Help,
    [string]$AzCopyPath = "",
    [string]$StorageAccount = "",
    [string]$Container = "",
    [string]$ExportPrefix = "",
    [string]$DocsDir = "",
    [string]$DataDir = "",
    [string]$PythonExe = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

# Fixed contract (plan section 5.1): one file per table plus meta.json.
$ExportFiles = @(
    "fact_offers.parquet",
    "fact_offer_skills.parquet",
    "dim_skill_list.parquet",
    "dim_calendar.parquet",
    "meta.json"
)

function Write-Message {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")] [string]$Level = "INFO"
    )
    $line = "{0}  [{1}]  {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host $line -ForegroundColor Yellow }
    else { Write-Host $line }
}

# The SAS must never reach stdout/stderr/logs: mask it before printing.
function Remove-SasFromText {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    if ([string]::IsNullOrEmpty($SasToken)) { return $Text }
    $masked = $Text.Replace($SasToken, "<SAS oculta>")
    $escapedSas = [System.Uri]::EscapeDataString($SasToken)
    if ($escapedSas -ne $SasToken) { $masked = $masked.Replace($escapedSas, "<SAS oculta>") }
    return $masked
}

function Format-SizeBytes {
    param([long]$Bytes)
    if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N2} KB" -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Show-Usage {
    Write-Host @"
export_web_snapshot.ps1 - instantanea local del export web (solo lectura)

Uso:
  .\export_web_snapshot.ps1 [-DryRun] [-Serve] [-Port 8123]

Describe los 5 ficheros de gold/web_export con la SAS/AzCopy existentes a
docs/data/ (gitignored) y, con -Serve, sirve docs/ en local con las mismas
rutas que GitHub Pages. No sube nada ni publica.

Parametros:
  -DryRun        Muestra el plan (origen enmascarado, destino y tamanos) sin
                 descargar nada. Funciona aunque falten la SAS o AzCopy.
  -Serve         Arranca un servidor estatico de Python sobre docs/.
  -Port <n>      Puerto del servidor (por defecto 8123, el del proyecto).
  -Help / -?     Muestra esta ayuda.

Detener el servidor:
  Corre en primer plano; se detiene con Ctrl+C (o cerrando la consola).
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

# Caller overrides win over config.ps1, which defines the same variables.
$OverrideAzCopyPath     = $AzCopyPath
$OverrideStorageAccount = $StorageAccount
$OverrideContainer      = $Container

# --- Cargar configuracion (mismo patron que el pipeline) ---
. (Join-Path $ScriptDir "config.ps1")

if (-not [string]::IsNullOrWhiteSpace($OverrideAzCopyPath)) { $AzCopyPath = $OverrideAzCopyPath }
if (-not [string]::IsNullOrWhiteSpace($OverrideStorageAccount)) { $StorageAccount = $OverrideStorageAccount }
if (-not [string]::IsNullOrWhiteSpace($OverrideContainer)) { $Container = $OverrideContainer }
if ([string]::IsNullOrWhiteSpace($ExportPrefix)) { $ExportPrefix = "gold/web_export" }
$ExportPrefix = $ExportPrefix.Trim("/")

if ([string]::IsNullOrWhiteSpace($DocsDir)) { $DocsDir = Join-Path $RepoRoot "docs" }
if ([string]::IsNullOrWhiteSpace($DataDir)) { $DataDir = Join-Path $DocsDir "data" }

# --- Requisitos: SAS de solo lectura, storage account y AzCopy ---
$hasSas = -not [string]::IsNullOrWhiteSpace($SasToken)
$hasAccount = -not [string]::IsNullOrWhiteSpace($StorageAccount)
$azCommand = $null
if (-not [string]::IsNullOrWhiteSpace($AzCopyPath)) {
    $azCommand = Get-Command $AzCopyPath -ErrorAction SilentlyContinue
}
$hasAzCopy = $null -ne $azCommand

$missingPrereqs = @()
if (-not $hasSas) { $missingPrereqs += "LANDING_SAS_TOKEN (SAS de solo lectura)" }
if (-not $hasAccount) { $missingPrereqs += "storage account (config.local.ps1 o LANDING_STORAGE_ACCOUNT)" }
if (-not $hasAzCopy) { $missingPrereqs += "AzCopy ($AzCopyPath)" }

if (-not $DryRun -and $missingPrereqs.Count -gt 0) {
    foreach ($missingItem in $missingPrereqs) { Write-Message "Falta $missingItem." -Level ERROR }
    Write-Message "No se puede descargar la instantanea. Prueba -DryRun para ver el plan." -Level ERROR
    exit 2
}
if ($DryRun -and $missingPrereqs.Count -gt 0) {
    foreach ($missingItem in $missingPrereqs) {
        Write-Message "Dry run: falta $missingItem; el plan se muestra igualmente." -Level WARN
    }
}

$remoteBase = "https://$StorageAccount.blob.core.windows.net/$Container/$ExportPrefix"
if (-not $hasAccount) {
    $remoteBase = "https://<storage-account>.blob.core.windows.net/<container>/$ExportPrefix"
}

# Remote sizes for the dry-run plan: read-only `azcopy list`, no download.
function Get-RemoteExportSizes {
    param([string]$BaseUrl)
    $sizes = @{}
    if (-not ($hasSas -and $hasAccount -and $hasAzCopy)) { return $sizes }
    $listOut = & $AzCopyPath list "$BaseUrl$SasToken" --machine-readable 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Message "No se pudo listar el export remoto (exit $LASTEXITCODE); tamanos no disponibles." -Level WARN
        return $sizes
    }
    foreach ($line in @($listOut)) {
        $text = Remove-SasFromText ([string]$line)
        if ($text -match '^\s*(?:INFO:\s*)?([^;]+);\s*Content Length:\s*(\d+)') {
            $sizes[$matches[1].Trim()] = [long]$matches[2]
        }
    }
    return $sizes
}

function Write-DownloadPlan {
    param([string]$BaseUrl, [string]$Destination, [hashtable]$Sizes)
    Write-Message "Modo dry run: no se descarga ni se modifica nada."
    $maskedBase = "$BaseUrl/"
    if ($hasSas) { $maskedBase = "$BaseUrl/?<SAS oculta>" }
    Write-Message "Origen:  $maskedBase"
    Write-Message "Destino: $Destination"
    foreach ($file in $ExportFiles) {
        $localPath = Join-Path $Destination $file
        $localInfo = "no existe"
        if (Test-Path -LiteralPath $localPath) {
            $localInfo = "actual {0} (se sobrescribira)" -f (Format-SizeBytes ([long](Get-Item -LiteralPath $localPath).Length))
        }
        $remoteInfo = "tamano remoto desconocido"
        if ($Sizes.ContainsKey($file)) { $remoteInfo = Format-SizeBytes ([long]$Sizes[$file]) }
        Write-Message ("  {0,-26} {1}  ->  {2}, {3}" -f $file, $remoteInfo, $localPath, $localInfo)
    }
}

# Downloads the fixed contract into the destination, overwriting each file.
function Invoke-Download {
    param([string]$BaseUrl, [string]$Destination)
    if (-not (Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }
    $failures = 0
    $downloaded = @()
    foreach ($file in $ExportFiles) {
        $source = "$BaseUrl/$file$SasToken"
        $target = Join-Path $Destination $file
        Write-Message "Descargando $file ..."
        # Quiet output on success; the script prints its own progress.
        $copyOut = & $AzCopyPath copy $source $target --overwrite=true --log-level=ERROR --output-level quiet 2>&1
        $copyExit = $LASTEXITCODE
        if ($copyExit -ne 0) {
            $failures++
            Write-Message "azcopy fallo al descargar $file (exit $copyExit)." -Level ERROR
            foreach ($line in @($copyOut)) {
                $text = Remove-SasFromText ([string]$line)
                if (-not [string]::IsNullOrWhiteSpace($text)) { Write-Message "  azcopy: $text" -Level WARN }
            }
            continue
        }
        $entry = Get-Item -LiteralPath $target -ErrorAction SilentlyContinue
        if ($null -eq $entry) {
            $failures++
            Write-Message "azcopy termino pero no aparece $target." -Level ERROR
            continue
        }
        $downloaded += $entry
        Write-Message ("OK  {0,-26} {1}" -f $file, (Format-SizeBytes ([long]$entry.Length)))
    }
    return @{ Failures = $failures; Files = $downloaded }
}

function Write-Summary {
    param([object[]]$Files, [string]$Destination)
    Write-Message "Resumen de la instantanea en $Destination"
    $total = [long]0
    foreach ($entry in $Files) {
        $total += [long]$entry.Length
        Write-Message ("  {0,-26} {1}" -f $entry.Name, (Format-SizeBytes ([long]$entry.Length)))
    }
    Write-Message ("  Total: {0}" -f (Format-SizeBytes $total))
    $metaPath = Join-Path $Destination "meta.json"
    if (-not (Test-Path -LiteralPath $metaPath)) {
        Write-Message "meta.json no esta presente; no se puede leer data_date." -Level WARN
        return
    }
    try {
        $meta = Get-Content -LiteralPath $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Message ("  data_date: {0}  (generado: {1}, modo: {2})" -f `
            ([string]$meta.data_date), ([string]$meta.generated_at), ([string]$meta.mode))
    } catch {
        Write-Message "meta.json no es JSON valido: $($_.Exception.Message)" -Level WARN
    }
}

# Foreground static server: Ctrl+C stops both the script and the server.
function Start-StaticServer {
    param([string]$DocsPath, [int]$ServerPort)
    if (-not (Test-Path -LiteralPath (Join-Path $DocsPath "dashboard\index.html"))) {
        Write-Message "Aviso: no se encuentra dashboard\index.html bajo $DocsPath." -Level WARN
    }
    if (-not (Test-Path -LiteralPath (Join-Path (Join-Path $DocsPath "data") "meta.json"))) {
        Write-Message "Aviso: no hay instantanea en $(Join-Path $DocsPath 'data'); la web avisara de que faltan datos." -Level WARN
    }
    Write-Message "Servidor estatico en primer plano (Ctrl+C para detenerlo):"
    Write-Message "  Dashboard: http://127.0.0.1:$ServerPort/dashboard/"
    Write-Message "  Datos:     http://127.0.0.1:$ServerPort/data/meta.json"
    & $PythonExe -m http.server $ServerPort --directory $DocsPath
    $serveExit = $LASTEXITCODE
    Write-Message "Servidor detenido (exit $serveExit)."
    return $serveExit
}

# --- Flujo principal ---
if ($DryRun) {
    $remoteSizes = Get-RemoteExportSizes -BaseUrl $remoteBase
    Write-DownloadPlan -BaseUrl $remoteBase -Destination $DataDir -Sizes $remoteSizes
    if ($Serve) {
        $serveExit = Start-StaticServer -DocsPath $DocsDir -ServerPort $Port
        if ($serveExit -ne 0) { exit 1 }
    }
    exit 0
}

$result = Invoke-Download -BaseUrl $remoteBase -Destination $DataDir
Write-Summary -Files $result.Files -Destination $DataDir
$exitCode = 0
if ($result.Failures -gt 0) {
    Write-Message "$($result.Failures) fichero(s) fallaron; la instantanea puede estar incompleta." -Level ERROR
    $exitCode = 1
}
if ($Serve) {
    $serveExit = Start-StaticServer -DocsPath $DocsDir -ServerPort $Port
    if ($serveExit -ne 0) { $exitCode = 1 }
}
exit $exitCode
