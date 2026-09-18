# =============================================================================
#  generate_sas.ps1
#  Genera un SAS token para el contenedor 'landing' del Storage Account
#  configurado en config.ps1 y lo guarda en la env var LANDING_SAS_TOKEN.
#
#  Ejecutar UNA vez (y renovar anualmente).
#
#    powershell.exe -NoProfile -ExecutionPolicy Bypass -File generate_sas.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir "config.ps1")

# --- 1. Instalar modulo Az.Storage si falta -----------------------------------
if (-not (Get-Module -ListAvailable -Name Az.Storage)) {
    Write-Host "Instalando modulo Az.Storage (puede tardar un par de minutos)..." -ForegroundColor Cyan
    Install-Module -Name Az.Storage -Scope CurrentUser -Repository PSGallery -Force -AllowClobber
}

# Cargar modulo
Import-Module Az.Storage

# --- 2. Login interactivo -----------------------------------------------------
#  IMPORTANTE: NO cierres esta ventana de PowerShell. El login abre un popup
#  del navegador; cuando termine, cierra SOLO el popup, NO esta consola.
$ctx = Get-AzContext -ErrorAction SilentlyContinue
if (-not $ctx) {
    Write-Host ""
    Write-Host ">>> NO cierres esta ventana de PowerShell. Solo el popup del navegador." -ForegroundColor Yellow
    Write-Host ">>> Abriendo popup de login de Azure..." -ForegroundColor Cyan
    try {
        Connect-AzAccount | Out-Null
    } catch {
        Write-Host "Login con popup fallo. Intentando con device code flow..." -ForegroundColor Yellow
        Connect-AzAccount -UseDeviceAuthentication | Out-Null
    }

    $subs = @(Get-AzSubscription)
    if ($subs.Count -gt 1) {
        Write-Host "Tienes varias suscripciones:" -ForegroundColor Yellow
        $subs | Select-Object Name, Id | Format-Table
        $subscriptionId = Read-Host "Pega el ID de la suscripcion donde esta el Storage Account"
        Set-AzContext -SubscriptionId $subscriptionId | Out-Null
    }
}

# --- 3. Descubrir el Resource Group del Storage Account -----------------------
Write-Host "Buscando Storage Account '$StorageAccount'..." -ForegroundColor Cyan
$acct = @(Get-AzStorageAccount | Where-Object { $_.StorageAccountName -eq $StorageAccount })
if ($acct.Count -eq 0) {
    Write-Host "No se encontro Storage Account '$StorageAccount' en las suscripciones accesibles." -ForegroundColor Red
    Write-Host "Verifica el nombre en config.ps1 y tu acceso." -ForegroundColor Red
    exit 1
}
if ($acct.Count -gt 1) {
    Write-Host "Hay varios Storage Accounts con ese nombre en distintas suscripciones/RG. Usando el primero." -ForegroundColor Yellow
}
$rg = $acct[0].ResourceGroupName
Write-Host "Encontrado en Resource Group: $rg" -ForegroundColor Green

# --- 4. Obtener la clave primaria del Storage Account -------------------------
$key = (Get-AzStorageAccountKey -ResourceGroupName $rg -StorageAccountName $StorageAccount)[0].Value
$ctxSa = New-AzStorageContext -StorageAccountName $StorageAccount -StorageAccountKey $key

# --- 5. Generar SAS del contenedor 'landing' ----------------------------------
# Permisos: Read(r) List(l) Add(a) Create(c) Write(w) -> "racwl"
# Caducidad en 1 ano. El cmdlet puede devolver el token con o sin '?' inicial
# segun la version del modulo; normalizamos para garantizarlo.
$expiry = (Get-Date).AddYears(1).ToUniversalTime()
$sasToken = New-AzStorageContainerSASToken -Name $Container -Permission "racwl" `
    -ExpiryTime $expiry -Context $ctxSa

# Normalizar: asegurar que EMPIEZA con '?' y NO tiene uno extra
if ($sasToken.StartsWith('?')) {
    $sasToken = $sasToken.Substring(1)
}
$sasToken = '?' + $sasToken

Write-Host ""
Write-Host "SAS generado correctamente:" -ForegroundColor Green
Write-Host "  Longitud:        $($sasToken.Length) chars"
Write-Host "  Caducidad:       $($expiry.ToString('yyyy-MM-dd'))"
Write-Host "  Primeros 4:     $($sasToken.Substring(0,4))"
Write-Host "  Empezado con ?: $($sasToken.StartsWith('?'))"
Write-Host ""

# --- 6. Guardar como env var del usuario -------------------------------------
#  Hay que entrecomillar el valor en setx por si el token contiene '&' (es el
#  caso: SAS siempre los lleva). Si no, cmd interpreta '&' como separador.
setx LANDING_SAS_TOKEN ("`"$sasToken`"") | Out-Null
Write-Host "Env var LANDING_SAS_TOKEN guardada (afectara a nuevos shells)." -ForegroundColor Yellow
Write-Host "Cierra y reabre PowerShell antes de ejecutar run_scrapers_and_upload.ps1" -ForegroundColor Yellow

# --- 7. Test rapido en esta misma sesion -------------------------------------
#  Asignamos en la sesion actual tambien para no tener que reabrir.
$env:LANDING_SAS_TOKEN = $sasToken

#  Construimos la URL en una variable primero para poder inspeccionarla.
$testUrl = "https://$StorageAccount.blob.core.windows.net/$Container$env:LANDING_SAS_TOKEN"
Write-Host ""
Write-Host "Test: azcopy list ..." -ForegroundColor Cyan
Write-Host "URL construida (primeros 80 chars): $($testUrl.Substring(0,[Math]::Min(80,$testUrl.Length)))" -ForegroundColor DarkGray
& $AzCopyPath list $testUrl 2>&1 | Select-Object -First 10