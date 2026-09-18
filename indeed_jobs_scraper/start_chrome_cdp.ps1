# start_chrome_cdp.ps1
# Abre un Chrome REAL con perfil dedicado y remote-debugging activado,
# para que TU te loguees en Indeed a mano (resolviendo el CAPTCHA humano).
# El scraper se conectara a este Chrome via CDP (--cdp 9222) SIN lanzar
# otro navegador ni tocar UA/headers/fingerprints.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File start_chrome_cdp.ps1
#
# Despues de iniciar sesion:
#   powershell -ExecutionPolicy Bypass -File run_nightly_indeed.ps1
#   (o directamente: python main.py --cdp 9222)

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSCommandPath
$ProfileDir = Join-Path $ProjectRoot "output\chrome_cdp_profile"
$CdpPort = 9222
$IndeedUrl = "https://es.indeed.com/"

# ----------------------- Verificacion previa -----------------------
$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromeExe = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chromeExe) {
    Write-Host "[ERROR] No se encontro Chrome instalado." -ForegroundColor Red
    exit 1
}

# Comprobar que no hay otro Chrome CDP usando el puerto
try {
    $r = Invoke-WebRequest -Uri "http://localhost:$CdpPort/json" -UseBasicParsing -TimeoutSec 3
    Write-Host "[WARN] Ya hay un Chrome CDP respondiendo en el puerto $CdpPort." -ForegroundColor Yellow
    Write-Host "       Usa ese Chrome (inicia sesion si aun no lo has hecho) o cierralo antes."
    $alreadyRunning = $true
} catch {
    $alreadyRunning = $false
}

# ----------------------- Lanzamiento -----------------------
if (-not $alreadyRunning) {
    $null = New-Item -ItemType Directory -Force -Path $ProfileDir
    Write-Host "Lanzando Chrome con perfil dedicado y remote-debugging-port=$CdpPort ..." -ForegroundColor Cyan
    Start-Process -FilePath $chromeExe -ArgumentList @(
        "--remote-debugging-port=$CdpPort",
        "--user-data-dir=`"$ProfileDir`"",
        "--start-maximized",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        $IndeedUrl
    )

    # esperar a que el CDP responda (hasta 30s)
    $ok = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-WebRequest -Uri "http://localhost:$CdpPort/json" -UseBasicParsing -TimeoutSec 2
            $ok = $true
            break
        } catch {}
    }
    if (-not $ok) {
        Write-Host "[ERROR] Chrome no respondio en el puerto $CdpPort en 30s." -ForegroundColor Red
        exit 1
    }
    Write-Host "Chrome OK (CDP activo en puerto $CdpPort)." -ForegroundColor Green
}

# ----------------------- Instrucciones para el usuario -----------------------
Write-Host ""
Write-Host "=" * 62 -ForegroundColor Cyan
Write-Host "  LOGIN MANUAL EN INDEED (unica vez o cuando expira la sesion)"
Write-Host "=" * 62 -ForegroundColor Cyan
Write-Host " 1. En la ventana de Chrome que se ha abierto, ve a https://es.indeed.com/"
Write-Host " 2. Inicia sesion en Indeed (email/password o 'Continuar con Google')."
Write-Host " 3. Si aparece un CAPTCHA o 'Verificacion adicional', resuelvelo a mano."
Write-Host " 4. Navega un par de minutos por Indeed (abre 2-3 ofertas) para"
Write-Host "    que la sesion tenga actividad real."
Write-Host " 5. DEJA Chrome abierto y minimizado."
Write-Host ""
Write-Host "  IMPORTANTE: NO cierres esta ventana de Chrome mientras scrapea."
Write-Host "  IMPORTANTE: NO uses VPN ni proxy en esta conexion."
Write-Host ""
Write-Host "  Cuando estes listo, ejecuta:"
Write-Host "    powershell -ExecutionPolicy Bypass -File .\run_nightly_indeed.ps1"
Write-Host "  o directamente:"
Write-Host "    python main.py --cdp $CdpPort --term data --cities Madrid,Barcelona --pages 2 --fromage 7 --enrich-rate 0.9"
Write-Host "=" * 62 -ForegroundColor Cyan
Write-Host ""
exit 0