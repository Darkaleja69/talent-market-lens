# run_nightly.ps1
# Wrapper resistente para ejecutar el scraper LinkedIn Jobs de forma
# desatendida durante horas (típico: noche).
#
# Características:
#  - Reanudación: usa `python -m src.main --append` para acumular con CSV/Parquet
#    previo y saltar detalles ya procesados (gracias a data/checkpoints/state.json).
#  - Anti-concurrencia: filelock simple (data/.nightly.lock). Si una run previa
#    sigue activa, aborta nueva invocacion para no duplicar trabajo.
#  - Reintentos: si python termina con exit !=0, reintenta hasta $MaxAttempts
#    veces con pausa de $RetryPauseSec. Cada reintento reanuda desde state.json.
#  - Timeout de seguridad: mata el proceso si excede $SafetyTimeoutHours.
#  - Log propio en data/run_nightly.log (separado del run.log del scraper).
#
# Uso manual:
#   powershell -ExecutionPolicy Bypass -File run_nightly.ps1
# Programación (ver README o comando schtasks al final de este fichero).

# ----------------------- Configuracion -----------------------
$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $ProjectRoot

$MaxAttempts        = 5            # reintentos totales si el scraper muere
$RetryPauseSec      = 300          # 5 min entre intentos
$SafetyTimeoutHours = 24           # kill si excede 24h (cap diario completo)
$GuestMode          = $true        # $true = scraping publico SIN login (--guest).
                                   # Necesario desde el bloqueo de la cuenta.
                                   # Poner $false para volver al flujo autenticado.
$LockFile           = Join-Path $ProjectRoot "data\.nightly.lock"
$NightlyLog         = Join-Path $ProjectRoot "data\run_nightly.log"

# ----------------------- Helpers -----------------------
function Write-NLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
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

# ----------------------- Main -----------------------
$NightlyLogDir = Split-Path $NightlyLog
$null = New-Item -ItemType Directory -Force -Path $NightlyLogDir -ErrorAction SilentlyContinue

Write-NLog "=== INICIO RUN NOCTURNA ==="
if (-not (Test-AndAcquireLock)) { exit 0 }

$attempt = 0
$exitCode = 1
while ($attempt -lt $MaxAttempts) {
    $attempt++
    Write-NLog "Intento ${attempt}/${MaxAttempts} - lanzando scraper con --append..."

    $start = Get-Date
    $procArgs = @("-m", "src.main", "--append")
    if ($GuestMode) {
        $procArgs += "--guest"
        # En modo invitado no hace falta ventana visible (el parseo usa la API
        # publica). HEADLESS=true evita dejar un navegador abierto durante horas.
        $env:HEADLESS = "true"
    }
    # Logs por intento (unico) para diagnosticar sin mezclar runs.
    $stdoutFile = Join-Path $ProjectRoot "data\nightly_stdout_attempt${attempt}.log"
    $stderrFile = Join-Path $ProjectRoot "data\nightly_stderr_attempt${attempt}.log"
    $proc = Start-Process -FilePath "python" `
        -ArgumentList $procArgs `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile `
        -PassThru

    $waited = 0
    $timeoutMs = $SafetyTimeoutHours * 60 * 60 * 1000
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 30
        $waited += 30
        if ($waited -ge ($timeoutMs / 1000)) {
            Write-NLog "TIMEOUT: matando proceso tras $SafetyTimeoutHours h."
            try { $proc | Stop-Process -Force } catch {}
            break
        }
    }
    # Esperar a que el proceso libere handles y refrescar el objeto antes de
    # leer ExitCode. En PowerShell 5.1, ExitCode puede quedar desactualizado
    # ($null o -1) si no se hace Refresh() tras Wait-Process.
    if ($proc) {
        try { $proc | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue } catch {}
        try { $proc.Refresh() } catch {}
    }
    $exitCode = if ($proc -and $null -ne $proc.ExitCode) { $proc.ExitCode } else { -1 }
    $elapsed = (Get-Date) - $start
    $mins = [int]$elapsed.TotalMinutes
    $secs = $elapsed.Seconds

    Write-NLog ("Intento {0} finalizado exit={1} tiempo={2}m{3}s" `
        -f $attempt, $exitCode, $mins, $secs)

    $stdoutRaw = Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue
    $stderrRaw = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue

    # Senales reales de exito/fallo. Un "=== RESUMEN ===" NO basta: una run con
    # login bloqueado y 0 ofertas tambien imprime RESUMEN (falso positivo que
    # oculto los fallos anteriores). Se exige al menos una combinacion con
    # estado=ok (es decir, con tarjetas extraidas).
    $hasResumen   = $stdoutRaw -match "=== RESUMEN ==="
    $okCombos     = ([regex]::Matches($stdoutRaw, "estado=ok")).Count
    $failedCombos = ([regex]::Matches($stdoutRaw, "estado=(unknown|empty)")).Count

    # Errores NO reintentables: limite de billing de Apify / challenge de login.
    # Reintentar solo gastaria horas en vano y multiplicaria los logs.
    $noRetryPattern = "billing cycle|Monthly usage hard limit|maximum usage for your current billing|Challenge real durante login"
    if (($stdoutRaw + $stderrRaw) -match $noRetryPattern) {
        Write-NLog "Error permanente detectado (limite Apify / challenge de login). NO se reintenta esta noche."
        break
    }

    if ($hasResumen -and $okCombos -gt 0) {
        Write-NLog "Run completada: $okCombos combinacion(es) con tarjetas OK (exit=$exitCode)."
        $exitCode = 0
        break
    }
    if ($hasResumen -and $okCombos -eq 0) {
        Write-NLog "RESUMEN sin ninguna combinacion OK (fallidas/vacias=$failedCombos). Se trata como fallo y se reintenta."
    } elseif ($exitCode -eq 0) {
        Write-NLog "Run completada OK (exit 0). Saliendo del bucle de reintentos."
        break
    }

    # Volcar las ultimas lineas de stderr para diagnosticar el fallo.
    if (Test-Path -LiteralPath $stderrFile) {
        Write-NLog "Stderr (ultimas 15 lineas):"
        Get-Content -LiteralPath $stderrFile -Tail 15 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-NLog "  STDERR: $_" }
    }

    if ($attempt -lt $MaxAttempts) {
        Write-NLog "Pausando $RetryPauseSec s antes de reintentar (reanudara con state.json)."
        Start-Sleep -Seconds $RetryPauseSec
    }
}

Release-Lock
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$marker = Join-Path $ProjectRoot "data\last_nightly_run.txt"
"last_run=$ts exit=$exitCode attempts=$attempt" | `
    Set-Content -LiteralPath $marker -Encoding UTF8
Write-NLog "=== FIN RUN NOCTURNA exit=$exitCode ==="
exit $exitCode