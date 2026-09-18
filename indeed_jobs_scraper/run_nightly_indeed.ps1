# run_nightly_indeed.ps1
# Wrapper para ejecutar el scraper de Indeed durante la noche.
# Caracteristicas:
#  - Anti-concurrencia: filelock (output/.nightly.lock)
#  - Multi-pais: recorre $Targets (ES, NL, IE) secuencialmente con enfriamiento
#    entre paises; si un pais topa con challenge (exit 3) se PARA toda la run.
#  - Reintentos SOLO ante errores tecnicos (exit != 0 y != 3); ante un
#    challenge de Cloudflare (exit 3) NO se reintenta la misma noche.
#  - Timeout de seguridad: 8h por proceso (deja holgura para que termine)
#  - Log propio en output/nightly.log (separado del run.log del scraper)
#  - CDP pre-flight: verifica que Chrome responde antes de lanzar
#
# Uso manual (despues de haber abierto Chrome con start_chrome_cdp.ps1):
#   powershell -ExecutionPolicy Bypass -File run_nightly_indeed.ps1
# Programacion (admin):
#   schtasks /create /tn "IndeedJobsNightly" /tr "powershell -ExecutionPolicy Bypass -File <ruta>\indeed_jobs_scraper\run_nightly_indeed.ps1" /sc daily /st 00:00

# ----------------------- Configuracion -----------------------
$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $ProjectRoot

$MaxAttempts        = 2           # 1 reintento SOLO si es error tecnico (no Cloudflare)
$RetryPauseSec      = 300         # 5 min entre intentos del mismo pais
$SafetyTimeoutHours = 8           # kill si un proceso excede 8h
$LockFile           = Join-Path $ProjectRoot "output\.nightly.lock"
$NightlyLog         = Join-Path $ProjectRoot "output\nightly.log"
$CdpPort            = 9222

# Terminos y ajustes comunes a todos los paises.
# 4 terminos x 2 ciudades x 2 paginas = 16 SERPs por pais (32 total NL+IE).
$SearchTerms        = "data,data engineer,data analyst,data scientist"
$Pages              = 2
$Fromage            = 7
$EnrichRate         = 0.9         # objetivo ~80-90% de ofertas con detalle completo
$EnrichMax          = 15          # tope de clics por SERP (mas bajo = mas seguro; el resto desde cache)
$CooldownSec        = 300         # 5 min de enfriamiento entre paises
$StallTimeoutSec    = 1200        # watchdog: si no hay actividad en 20 min, matar y reintentar

# Paises objetivo y sus ciudades.
# ES (Madrid/Barcelona) se excluye: ya esta muy mirado y en cache.
$Targets = @(
    @{ Country = "NL"; Cities = "Amsterdam,Rotterdam" },
    @{ Country = "IE"; Cities = "Dublin,Cork" }
)

# ----------------------- Helpers -----------------------
function Write-NLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $NightlyLog) -ErrorAction SilentlyContinue
    Add-Content -LiteralPath $NightlyLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-AndAcquireLock {
    if (Test-Path -LiteralPath $LockFile) {
        $pidStr = Get-Content -LiteralPath $LockFile -ErrorAction SilentlyContinue
        if ($pidStr -match '^\d+$') {
            $procId = [int]$pidStr
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-NLog "ABORT: ya hay run nocturna activa (PID $procId). Saliendo."
                return $false
            }
        }
        Write-NLog "Lock stale encontrado; eliminando."
        Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    }
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $LockFile) -ErrorAction SilentlyContinue
    $PID_GLOBAL = $PID
    Set-Content -LiteralPath $LockFile -Value $PID_GLOBAL -Encoding UTF8
    return $true
}

function Release-Lock {
    if (Test-Path -LiteralPath $LockFile) {
        Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    }
}

function Test-CdpAlive {
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:$CdpPort/json" -UseBasicParsing -TimeoutSec 5
        return $true
    } catch {
        return $false
    }
}

# ----------------------- Main -----------------------
$TargetsDesc = ($Targets | ForEach-Object { "$($_.Country)($($_.Cities))" }) -join " "
Write-NLog "=== INICIO RUN NOCTURNA INDEED ==="
Write-NLog "Terms: $SearchTerms | Pages: $Pages | Fromage: $Fromage | EnrichRate: $EnrichRate | EnrichMax: $EnrichMax"
Write-NLog "Targets: $TargetsDesc | Cooldown entre paises: ${CooldownSec}s"

if (-not (Test-AndAcquireLock)) { exit 0 }

if (-not (Test-CdpAlive)) {
    Write-NLog "FATAL: Chrome CDP en puerto $CdpPort no responde."
    Write-NLog "1) Ejecuta .\start_chrome_cdp.ps1"
    Write-NLog "2) Inicia sesion en Indeed en esa ventana y resuelve el CAPTCHA a mano."
    Write-NLog "3) Vuelve a ejecutar este script."
    Write-NLog "Abortando nightly run."
    Release-Lock
    exit 3
}
Write-NLog "CDP pre-flight OK. Chrome responde en puerto $CdpPort."

$overallExit = 0
$abortedChallenge = $false

for ($i = 0; $i -lt $Targets.Count; $i++) {
    $country = $Targets[$i].Country
    $cities = $Targets[$i].Cities
    $targetExit = 1
    $attempt = 0

    while ($attempt -lt $MaxAttempts) {
        $attempt++
        Write-NLog "[$country] Intento ${attempt}/${MaxAttempts} - lanzando python main.py --country $country ..."

        $start = Get-Date
        $stdoutFile = Join-Path $ProjectRoot "output\nightly_stdout_${country}_attempt${attempt}.log"
        $stderrFile = Join-Path $ProjectRoot "output\nightly_stderr_${country}_attempt${attempt}.log"

        $pythonArgs = @(
            "-u",
            "main.py",
            "--country", "$country",
            "--cdp", "$CdpPort",
            "--terms=`"$SearchTerms`"",
            "--cities=`"$cities`"",
            "--pages", "$Pages",
            "--fromage", "$Fromage",
            "--enrich-rate", "$EnrichRate",
            "--enrich-max", "$EnrichMax",
            "--pause-every", "2",
            "--pause-min", "6",
            "--pause-max", "10"
        )

        $proc = Start-Process -FilePath "python" `
            -ArgumentList $pythonArgs `
            -WorkingDirectory $ProjectRoot `
            -RedirectStandardOutput $stdoutFile `
            -RedirectStandardError $stderrFile `
            -NoNewWindow -PassThru

        $waited = 0
        $timeoutSec = $SafetyTimeoutHours * 60 * 60
        $stalled = $false
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 30
            $waited += 30
            if ($waited -ge $timeoutSec) {
                Write-NLog "[$country] TIMEOUT: matando proceso Python tras $SafetyTimeoutHours h."
                try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
                Start-Sleep -Seconds 5
                try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
                break
            }

            # Watchdog de inactividad: si el scraper no escribe nada (ni stdout
            # ni su log) durante $StallTimeoutSec, se considera colgado y se mata
            # para que el reintento lo relance. Evita runs de 8h sin avanzar.
            $activityTime = $null
            try {
                if (Test-Path -LiteralPath $stdoutFile) {
                    $activityTime = (Get-Item -LiteralPath $stdoutFile).LastWriteTime
                }
            } catch {}
            try {
                $newestLog = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "output") -Filter "log_*.log" -File -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($newestLog -and (-not $activityTime -or $newestLog.LastWriteTime -gt $activityTime)) {
                    $activityTime = $newestLog.LastWriteTime
                }
            } catch {}
            if ($activityTime -and ((Get-Date) - $activityTime).TotalSeconds -ge $StallTimeoutSec) {
                Write-NLog "[$country] WATCHDOG: sin actividad en ${StallTimeoutSec}s. Matando proceso y reintentando."
                try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
                Start-Sleep -Seconds 5
                try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
                $stalled = $true
                break
            }
        }

        if ($proc) {
            try { $proc | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue } catch {}
            # Refresh del objeto Process: evita leer un ExitCode obsoleto (-1) en PS 5.1
            try { $proc.Refresh() } catch {}
        }
        $exitCode = if ($proc -and $null -ne $proc.ExitCode) { $proc.ExitCode } else { -1 }
        $elapsed = (Get-Date) - $start
        $mins = [int]$elapsed.TotalMinutes
        $secs = $elapsed.Seconds

        Write-NLog ("[$country] Intento {0} finalizado exit={1} tiempo={2}m{3}s" -f $attempt, $exitCode, $mins, $secs)

        # Marcador de exito: "Scrape completado" significa que el run termino bien;
        # un exit != 0 despues de eso suele ser un crash al liberar recursos.
        $stdoutRaw = Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue
        if ($exitCode -ne 0 -and $stdoutRaw -match "Scrape completado:") {
            Write-NLog "[$country] Scrape completado detectado en stdout aunque exit=$exitCode. Se considera OK."
            $exitCode = 0
        }

        # Fallo de conexion CDP (Chrome presente pero la sesion no acepta conexion):
        # reintentar no arregla nada. Se trata como exit 3 y NO se reintenta.
        $stderrRaw = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
        if ($exitCode -ne 0 -and $stderrRaw -match "connect_over_cdp") {
            Write-NLog "[$country] CDP roto (connect_over_cdp). Se trata como exit 3; NO se reintenta."
            $exitCode = 3
        }

        $targetExit = $exitCode

        if ($exitCode -eq 0) {
            Write-NLog "[$country] Run completada OK."
            break
        }

        if ($exitCode -eq 3) {
            Write-NLog "[$country] Exit 3: challenge anti-bot o Chrome no disponible. NO se reintenta."
            break
        }

        Write-NLog "[$country] Fallo tecnico detectado. Stderr (ultimas lineas):"
        if (Test-Path -LiteralPath $stderrFile) {
            $stderrLines = Get-Content -LiteralPath $stderrFile -Tail 15 -ErrorAction SilentlyContinue
            foreach ($line in $stderrLines) {
                Write-NLog "  STDERR: $line"
            }
        } else {
            Write-NLog "  (sin stderr capturado - posible error de Start-Process)"
        }

        if ($attempt -lt $MaxAttempts) {
            Write-NLog "[$country] Pausando $RetryPauseSec s antes de reintentar (reanudara con state.json)."
            Start-Sleep -Seconds $RetryPauseSec
        }
    }

    if ($targetExit -ne 0) { $overallExit = $targetExit }

    # Ante un challenge, parar TODA la run (no tocar los siguientes paises
    # para no escalar el bloqueo). Esperar 24h.
    if ($targetExit -eq 3) {
        $abortedChallenge = $true
        break
    }

    # Enfriamiento entre paises (no tras el ultimo).
    if ($i -lt ($Targets.Count - 1)) {
        Write-NLog "Enfriando ${CooldownSec}s antes del siguiente pais..."
        Start-Sleep -Seconds $CooldownSec
    }
}

Release-Lock
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$countries = ($Targets | ForEach-Object { $_.Country }) -join ","
$marker = Join-Path $ProjectRoot "output\last_nightly_run_indeed.txt"
"last_run=$ts exit=$overallExit terms=$SearchTerms countries=$countries pages=$Pages" | `
    Set-Content -LiteralPath $marker -Encoding UTF8
if ($abortedChallenge) {
    Write-NLog "=== FIN RUN NOCTURNA INDEED exit=$overallExit (challenge anti-bot: espera 24h) ==="
} else {
    Write-NLog "=== FIN RUN NOCTURNA INDEED exit=$overallExit ==="
}
exit $overallExit
