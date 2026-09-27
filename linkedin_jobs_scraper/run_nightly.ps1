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

# Watchdog de progreso (RF-15, T-19): la decision la toma el supervisor Python
# (scrapers-pipeline/verification/supervisor.py) sobre el contador PROGRESS, no
# sobre la actividad generica del log. $WatchdogExitCode (75) no colisiona con
# los exit codes de este scraper (0/4).
$WatchdogExitCode   = 75
$WatchdogReason     = "watchdog_no_progress"

# ----------------------- Helpers -----------------------
function Write-NLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -LiteralPath $NightlyLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Get-WatchdogDecision {
    # Consulta puntual al supervisor Python: lee la ultima linea PROGRESS del
    # stream y persiste su estado. Devuelve la linea legible o $null si el
    # supervisor no esta disponible (en ese caso se mantiene el proceso).
    param(
        [string]$Source,
        [int]$ProcessId,
        [string]$Stream,
        [string]$State,
        [string]$StartedAt
    )
    $supervisorDir = Join-Path (Split-Path -Parent $ProjectRoot) "scrapers-pipeline"
    if (-not (Test-Path -LiteralPath (Join-Path $supervisorDir "verification\supervisor.py"))) {
        return $null
    }
    $prevPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $supervisorDir
    try {
        $out = & python -m verification.supervisor `
            --source $Source --pid $ProcessId --stream $Stream `
            --state $State --started-at $StartedAt 2>$null
        return ($out | Select-Object -Last 1)
    } catch {
        return $null
    } finally {
        $env:PYTHONPATH = $prevPythonPath
    }
}

function Get-RetryDecision {
    # Consulta a la politica Python de reintentos (T-21..T-23). Los wrappers no
    # deciden si relanzan: solo actuan sobre 'decision=stop'/'decision=retry'.
    # Ante cualquier fallo al consultar, se mantiene el comportamiento previo
    # (reintentar).
    param(
        [string]$Source,
        [bool]$WatchdogBlocked = $false,
        [bool]$Permanent = $false,
        [bool]$Completed = $false,
        [int]$ExitCode = 0,
        $Blocked = $null
    )
    $policyDir = Join-Path (Split-Path -Parent $ProjectRoot) "scrapers-pipeline"
    if (-not (Test-Path -LiteralPath (Join-Path $policyDir "verification\retry_policy.py"))) {
        return "RETRYPOLICY source=$Source decision=retry reason=policy_unavailable"
    }
    $prevPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $policyDir
    try {
        $policyArgs = @("--source", $Source, "--exit-code", "$ExitCode")
        if ($WatchdogBlocked) { $policyArgs += "--watchdog-blocked" }
        if ($Permanent) { $policyArgs += "--permanent" }
        if ($Completed) { $policyArgs += "--completed" }
        if ($Blocked -eq $true) { $policyArgs += "--blocked" }
        elseif ($Blocked -eq $false) { $policyArgs += "--not-blocked" }
        $out = & python -m verification.retry_policy @policyArgs 2>$null
        return ($out | Select-Object -Last 1)
    } catch {
        return "RETRYPOLICY source=$Source decision=retry reason=policy_unavailable"
    } finally {
        $env:PYTHONPATH = $prevPythonPath
    }
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
$watchdogReason = ""
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
    # Estado del watchdog de este intento: el supervisor Python guarda aqui el
    # ultimo contador y su instante entre sondeos.
    $watchdogState = Join-Path $ProjectRoot "data\.watchdog_attempt${attempt}.json"
    # Cada intento parte de cero: descarta el estado de una ejecución previa.
    Remove-Item -LiteralPath $watchdogState -Force -ErrorAction SilentlyContinue
    $startedAtIso = $start.ToString("o")
    $watchdogBlocked = $false
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

        # Watchdog de progreso (RF-15): la decision la toma el supervisor
        # Python a partir del contador PROGRESS, no de la actividad generica
        # del log. Si esta bloqueada, el supervisor ya detuvo el arbol.
        $decision = Get-WatchdogDecision -Source "linkedin" -ProcessId $proc.Id `
            -Stream $stdoutFile -State $watchdogState -StartedAt $startedAtIso
        if ($decision -match 'state=blocked') {
            Write-NLog "WATCHDOG: sin progreso en el periodo; arbol de procesos detenido. reason=$WatchdogReason"
            $watchdogReason = $WatchdogReason
            $watchdogBlocked = $true
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
    if ($watchdogBlocked) {
        # La parada por watchdog no es un error tecnico reintentable.
        $exitCode = $WatchdogExitCode
    }
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

    if (-not $watchdogBlocked -and $hasResumen -and $okCombos -gt 0) {
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

    # T-22: la politica Python decide si se relanza. Una detencion por watchdog
    # o un patron permanente termina el run sin consumir otro intento.
    $permanent = (($stdoutRaw + $stderrRaw) -match $noRetryPattern)
    $policyLine = Get-RetryDecision -Source "linkedin" -WatchdogBlocked $watchdogBlocked `
        -Permanent $permanent -ExitCode $exitCode
    if ($policyLine -match 'decision=stop') {
        Write-NLog "No se reintenta: $policyLine"
        break
    }

    if ($attempt -lt $MaxAttempts) {
        Write-NLog "Pausando $RetryPauseSec s antes de reintentar (reanudara con state.json)."
        Start-Sleep -Seconds $RetryPauseSec
    }
}

Release-Lock
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$marker = Join-Path $ProjectRoot "data\last_nightly_run.txt"
$watchdogField = if ($watchdogReason) { "watchdog=$watchdogReason" } else { "watchdog=none" }
"last_run=$ts exit=$exitCode attempts=$attempt $watchdogField" | `
    Set-Content -LiteralPath $marker -Encoding UTF8
Write-NLog "=== FIN RUN NOCTURNA exit=$exitCode ==="
exit $exitCode