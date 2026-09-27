# run_infojobs_nightly.ps1
# Wrapper desatendido para el scraper de InfoJobs.
# Patron coherente con run_nightly_indeed.ps1 y run_nightly.ps1:
#   - Anti-concurrencia con lock file (data/.nightly.lock)
#   - Reintentos con pausa
#   - Timeout de seguridad (InfoJobs suele tardar <2h)
#   - Log propio en data/run_nightly.log
#
# Uso:
#   powershell -ExecutionPolicy Bypass -NoProfile -File run_infojobs_nightly.ps1
# Programacion (admin), dentro del pipeline general del Task Scheduler a las 00:00.

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSCommandPath
Set-Location -LiteralPath $ProjectRoot

$MaxAttempts        = 3
$RetryPauseSec      = 180
$SafetyTimeoutHours = 4
$LockFile           = Join-Path $ProjectRoot "data\.nightly.lock"
$NightlyLog         = Join-Path $ProjectRoot "data\run_nightly.log"

# Watchdog de progreso (RF-15, T-19): la decision la toma el supervisor Python
# (scrapers-pipeline/verification/supervisor.py) sobre el contador PROGRESS, no
# sobre la actividad generica del log. $WatchdogExitCode (75) no colisiona con
# los exit codes de este scraper (0/1/2).
$WatchdogExitCode   = 75
$WatchdogReason     = "watchdog_no_progress"

# Opciones del scraper (igual que el README: python -m scraper.main --login)
#   --login pausa en el navegador para login manual antes de scrapear.
#   --paginas / --ciudades / --keywords: dejas los defaults del scraper.
#   --unattended: si aparece un CAPTCHA no espera (no hay humano); aborta y avisa.
$ScrapArgs = @("-m", "scraper.main", "--unattended")

# ----------------------- Helpers -----------------------
function Write-NLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    $dir = Split-Path $NightlyLog
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
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
    # LIMITACION: si el CLI de politica no esta disponible, el fallback es
    # 'retry' (comportamiento previo a T-21..T-23); en ese caso una detencion
    # por watchdog no se evitara. El fallback es deliberado y no decide reglas.
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

function Send-Alert([string]$msg) {
    # Envia aviso por Telegram/SMTP usando scraper/notifier.py (lee .env).
    # El mensaje se transporta en base64 para evitar problemas de comillas.
    try {
        $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($msg))
        $code = "import base64;from dotenv import load_dotenv;load_dotenv();" +
                "from scraper.notifier import notify;" +
                "notify(base64.b64decode('$b64').decode('utf-8'))"
        & python -c $code 2>$null | Out-Null
    } catch {}
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
    $dir = Split-Path $LockFile
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -LiteralPath $LockFile -Value $PID -Encoding UTF8
    return $true
}

function Release-Lock {
    if (Test-Path -LiteralPath $LockFile) {
        Remove-Item -LiteralPath $LockFile -Force -ErrorAction SilentlyContinue
    }
}

# ----------------------- Main -----------------------
Write-NLog "=== INICIO RUN NOCTURNA INFOJOBS ==="

if (-not (Test-AndAcquireLock)) { exit 0 }

$attempt = 0
$exitCode = 1
$watchdogReason = ""
while ($attempt -lt $MaxAttempts) {
    $attempt++
    Write-NLog "Intento ${attempt}/${MaxAttempts} - lanzando python $($ScrapArgs -join ' ') ..."

    $start = Get-Date
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
        -ArgumentList $ScrapArgs `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile `
        -NoNewWindow -PassThru

    $waited = 0
    $timeoutSec = $SafetyTimeoutHours * 3600
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 30
        $waited += 30
        if ($waited -ge $timeoutSec) {
            Write-NLog "TIMEOUT: matando proceso Python tras $SafetyTimeoutHours h."
            try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
            Start-Sleep -Seconds 5
            try { $proc | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
            break
        }

        # Watchdog de progreso (RF-15): la decision la toma el supervisor
        # Python a partir del contador PROGRESS, no de la actividad generica
        # del log. Si esta bloqueada, el supervisor ya detuvo el arbol.
        $decision = Get-WatchdogDecision -Source "infojobs" -ProcessId $proc.Id `
            -Stream $stdoutFile -State $watchdogState -StartedAt $startedAtIso
        if ($decision -match 'state=blocked') {
            Write-NLog "WATCHDOG: sin progreso en el periodo; arbol de procesos detenido. reason=$WatchdogReason"
            $watchdogReason = $WatchdogReason
            $watchdogBlocked = $true
            break
        }
    }

    if ($proc) {
        try { $proc | Wait-Process -Timeout 15 -ErrorAction SilentlyContinue } catch {}
        # Refresh del objeto Process: evita leer un ExitCode obsoleto (-1) en PS 5.1
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

    Write-NLog ("Intento {0} finalizado exit={1} tiempo={2}m{3}s" -f $attempt, $exitCode, $mins, $secs)

    # Cortocircuito RF-15 (blindaje): el watchdog ya detuvo el arbol; se sale
    # del bucle de reintentos directamente, sin depender de la politica Python.
    if ($watchdogBlocked) {
        Write-NLog "WATCHDOG: sin progreso; no se reintenta el run (exit=$WatchdogExitCode)."
        break
    }

    # El scraper imprime una linea maquina-legible antes de salir:
    #   RESULT total=N incidencias=M blocked=true|false
    # Distingue "sin ofertas nuevas" (ok) de "bloqueado por CAPTCHA".
    $stdoutRaw = Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue
    $resTotal = $null
    $resBlocked = $null
    if ($stdoutRaw -match "RESULT total=(\d+) incidencias=(\d+) blocked=(true|false)") {
        $resTotal = [int]$Matches[1]
        $resBlocked = ($Matches[3] -eq "true")
        Write-NLog "RESULT: total=$resTotal incidencias=$($Matches[2]) blocked=$resBlocked"
    }

    if (-not $watchdogBlocked -and $exitCode -eq 0) {
        Write-NLog "Run completada OK (total=$resTotal). Saliendo del bucle."
        break
    }

    if (-not $watchdogBlocked -and $null -ne $resBlocked) {
        if (-not $resBlocked) {
            # Resumen valido: el run termino y volco datos aunque el proceso
            # saliera con codigo != 0 (p.ej. crash al liberar recursos).
            Write-NLog "Resumen valido (blocked=false, total=$resTotal); run completada."
            $exitCode = if ($resTotal -gt 0) { 0 } else { 1 }
            break
        }
        Write-NLog "BLOQUEO por CAPTCHA (exit=$exitCode, total=$resTotal)."
    } elseif (-not $watchdogBlocked) {
        Write-NLog "Fallo sin RESULT (exit=$exitCode). Stderr (ultimas 15 lineas):"
        if (Test-Path -LiteralPath $stderrFile) {
            Get-Content -LiteralPath $stderrFile -Tail 15 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-NLog "  STDERR: $_" }
        }
    }

    # T-23: la politica Python decide si se relanza. Un watchdog o un
    # RESULT blocked=true termina el run sin consumir otro intento.
    $policyLine = Get-RetryDecision -Source "infojobs" -WatchdogBlocked $watchdogBlocked `
        -Blocked $resBlocked -ExitCode $exitCode
    if ($policyLine -match 'decision=stop') {
        Write-NLog "No se reintenta: $policyLine"
        break
    }

    if ($attempt -lt $MaxAttempts) {
        Write-NLog "Pausando $RetryPauseSec s antes de reintentar."
        Start-Sleep -Seconds $RetryPauseSec
    }
}

Release-Lock
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$marker = Join-Path $ProjectRoot "data\last_nightly_run.txt"
$watchdogField = if ($watchdogReason) { "watchdog=$watchdogReason" } else { "watchdog=none" }
"last_run=$ts exit=$exitCode attempts=$attempt $watchdogField" | Set-Content -LiteralPath $marker -Encoding UTF8
if ($exitCode -ne 0 -and $exitCode -ne 1) {
    Send-Alert "InfoJobs scraper: run NO completada (exit=$exitCode, intentos=$attempt). Revisa data\run_nightly.log"
}
Write-NLog "=== FIN RUN NOCTURNA INFOJOBS exit=$exitCode ==="
exit $exitCode