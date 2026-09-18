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
while ($attempt -lt $MaxAttempts) {
    $attempt++
    Write-NLog "Intento ${attempt}/${MaxAttempts} - lanzando python $($ScrapArgs -join ' ') ..."

    $start = Get-Date
    $stdoutFile = Join-Path $ProjectRoot "data\nightly_stdout_attempt${attempt}.log"
    $stderrFile = Join-Path $ProjectRoot "data\nightly_stderr_attempt${attempt}.log"

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

    Write-NLog ("Intento {0} finalizado exit={1} tiempo={2}m{3}s" -f $attempt, $exitCode, $mins, $secs)

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

    if ($exitCode -eq 0) {
        Write-NLog "Run completada OK (total=$resTotal). Saliendo del bucle."
        break
    }

    if ($null -ne $resBlocked) {
        if (-not $resBlocked) {
            # Resumen valido: el run termino y volco datos aunque el proceso
            # saliera con codigo != 0 (p.ej. crash al liberar recursos).
            Write-NLog "Resumen valido (blocked=false, total=$resTotal); run completada."
            $exitCode = if ($resTotal -gt 0) { 0 } else { 1 }
            break
        }
        Write-NLog "BLOQUEO por CAPTCHA (exit=$exitCode, total=$resTotal)."
    } else {
        Write-NLog "Fallo sin RESULT (exit=$exitCode). Stderr (ultimas 15 lineas):"
        if (Test-Path -LiteralPath $stderrFile) {
            Get-Content -LiteralPath $stderrFile -Tail 15 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-NLog "  STDERR: $_" }
        }
    }

    if ($attempt -lt $MaxAttempts) {
        Write-NLog "Pausando $RetryPauseSec s antes de reintentar."
        Start-Sleep -Seconds $RetryPauseSec
    }
}

Release-Lock
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$marker = Join-Path $ProjectRoot "data\last_nightly_run.txt"
"last_run=$ts exit=$exitCode attempts=$attempt" | Set-Content -LiteralPath $marker -Encoding UTF8
if ($exitCode -ne 0 -and $exitCode -ne 1) {
    Send-Alert "InfoJobs scraper: run NO completada (exit=$exitCode, intentos=$attempt). Revisa data\run_nightly.log"
}
Write-NLog "=== FIN RUN NOCTURNA INFOJOBS exit=$exitCode ==="
exit $exitCode