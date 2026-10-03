# =============================================================================
#  run_pipeline_supervised.ps1
#  Supervises the daily pipeline (RF-7): launches
#  run_scrapers_and_upload.ps1 as a child with a console transcript, captures
#  its exit code and, when the run did not close with "Fin pipeline", writes
#  logs/run_state/<run_date>.abort.json with the available evidence (exit code,
#  transcript paths, last general-log lines, live wrappers, system events).
#
#  It does NOT load config.ps1 and needs no SAS/Azure/azcopy: only local
#  processes and files, so it can be tested offline.
#
#  Anomalous closure (T-10; RF-2, RF-4): after writing abort.json the
#  supervisor waits up to -WaitHours (default 6, poll every -PollSeconds) for
#  the date's live wrappers to disappear. If they disappear it invokes the
#  recovery executor (-RecoveryScript -Date <run_date>); if the cap is reached
#  the run is left pending and the abort evidence records the timeout and the
#  wrappers still alive. It never kills a process. Since T-14 the scheduled
#  task runs this supervisor (the pipeline is no longer launched directly).
#
#  Usage:
#    powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_pipeline_supervised.ps1
#    ... -RunDate 2026-10-01 -PipelineScript <path> -LogDir <dir> -StateDir <dir>
#    ... -ProjectsRoot <dir> -RecoveryScript <path> -WaitHours 6 -PollSeconds 60
#
#  Exit codes:
#    0   normal closure with exit 0, or anomalous closure recovered by the
#        executor (recovery exit 0)
#    N   normal closure with exit N (propagated)
#    1   anomalous closure and the wrapper wait timed out (run left pending)
#    2   pre-flight/evidence failure: the pipeline script does not exist, the
#        child could not be launched, or abort.json could not be written.
#        No abort.json is written when the pipeline never ran.
#    3   anomalous closure, wrappers gone, but the recovery executor was
#        missing, failed, or does not declare -Date yet (T-11); the error is
#        recorded and success is never invented
# =============================================================================

param(
    [string]$RunDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$PipelineScript = (Join-Path $PSScriptRoot "run_scrapers_and_upload.ps1"),
    [string]$LogDir = (Join-Path $PSScriptRoot "logs"),
    [string]$StateDir = "",
    [string]$ProjectsRoot = "",
    [string]$RecoveryScript = (Join-Path $PSScriptRoot "recover_and_upload.ps1"),
    [double]$WaitHours = 6,
    [int]$PollSeconds = 60
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($StateDir)) { $StateDir = Join-Path $LogDir "run_state" }
if ([string]::IsNullOrWhiteSpace($ProjectsRoot)) { $ProjectsRoot = Split-Path -Parent $PSScriptRoot }

# Shared atomic JSON writer (T-03), wrapper liveness (T-10) and the recovery
# executor guard (T-10); the supervisor never writes run state itself.
. (Join-Path $PSScriptRoot "run_state.ps1")
. (Join-Path $PSScriptRoot "wrapper_locks.ps1")
. (Join-Path $PSScriptRoot "recovery_support.ps1")

$transcript    = Join-Path $LogDir "transcript-$RunDate.log"
$transcriptErr = Join-Path $LogDir "transcript-$RunDate.err.log"
$generalLog    = Join-Path $LogDir "upload-$RunDate.log"
$abortPath     = Join-Path $StateDir "$RunDate.abort.json"

function Write-SupervisorLog {
    param([string]$Message, [ValidateSet("INFO","WARN","ERROR")][string]$Level = "INFO")
    $line = "{0}  [{1}]  {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host $line -ForegroundColor Yellow }
    else { Write-Host $line }
}

# Returns $true when the LAST "Inicio pipeline scrapers" block of the general
# log also carries a "Fin pipeline" line (same semantics as
# run_evidence.parse_pipeline_log: a Fin from an earlier block does not close
# the current run).
function Test-PipelineClosed {
    param([string]$LogPath)
    if (-not (Test-Path -LiteralPath $LogPath)) { return $false }
    try { $text = [System.IO.File]::ReadAllText($LogPath) } catch { return $false }
    $lines = @($text -split "\r?\n")
    $lastInicio = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match 'Inicio pipeline scrapers') { $lastInicio = $i }
    }
    if ($lastInicio -lt 0) { return $false }
    for ($i = $lastInicio; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '====\s*Fin pipeline') { return $true }
    }
    return $false
}

function Get-ProcessExitCode {
    param($Proc)
    if ($null -eq $Proc) { return -1 }
    try { [void]$Proc.WaitForExit(0) } catch {}
    try { $Proc.Refresh() } catch {}
    $code = $null
    try { $code = $Proc.ExitCode } catch {}
    if ($null -eq $code) { return -1 }
    return [int]$code
}

function Get-LastLogLines {
    param([string]$LogPath, [int]$Count = 25)
    if (-not (Test-Path -LiteralPath $LogPath)) { return @() }
    try {
        # Cast to [string]: Get-Content -Tail decorates the lines with ETS
        # properties (PSPath, ReadCount...) that ConvertTo-Json would turn
        # into objects instead of plain strings.
        return @(Get-Content -LiteralPath $LogPath -Tail $Count -ErrorAction Stop |
            ForEach-Object { [string]$_ })
    } catch { return @() }
}

# Reads the run state file (T-03/T-04) as the "last state" evidence. Missing
# or unreadable files never break the abort evidence: they are reported with
# state_exists/state_error.
function Get-JsonProperty {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Get-RunStateSnapshot {
    param([string]$Path)
    $snapshot = @{
        state_path       = $Path
        state_exists     = $false
        state_status     = $null
        state_started_at = $null
        state_finished_at = $null
        state_sources    = $null
        state_error      = $null
    }
    if (-not (Test-Path -LiteralPath $Path)) { return $snapshot }
    $snapshot.state_exists = $true
    try {
        $payload = [System.IO.File]::ReadAllText($Path) | ConvertFrom-Json
    } catch {
        $snapshot.state_error = "estado ilegible: $($_.Exception.Message)"
        return $snapshot
    }
    foreach ($field in @("status", "started_at", "finished_at")) {
        $value = Get-JsonProperty $payload $field
        if ($null -ne $value) {
            switch ($field) {
                "status"      { $snapshot.state_status = [string]$value }
                "started_at"  { $snapshot.state_started_at = [string]$value }
                "finished_at" { $snapshot.state_finished_at = [string]$value }
            }
        }
    }
    $sources = Get-JsonProperty $payload "sources"
    if ($null -ne $sources) { $snapshot.state_sources = $sources }
    return $snapshot
}

# Query one event filter; a missing channel/permission or an empty result
# yields a Spanish note instead of an exception.
function Get-EventsForFilter {
    param(
        [string]$Category,
        [hashtable]$Filter,
        [int]$Limit = 15,
        [string]$ProviderPattern = ""
    )
    $events = @()
    try {
        $found = @(Get-WinEvent -FilterHashtable $Filter -MaxEvents $Limit -ErrorAction Stop)
    } catch {
        return @{
            events = @()
            note = "sin eventos o consulta no disponible para $Category ($($_.Exception.Message))"
        }
    }
    foreach ($ev in $found) {
        $provider = [string]$ev.ProviderName
        if ($ProviderPattern -and $provider -notmatch $ProviderPattern) { continue }
        $message = ""
        try { $message = [string]$ev.Message } catch {}
        if ($message.Length -gt 500) { $message = $message.Substring(0, 500) + "..." }
        $events += @{
            category = $Category
            provider = $provider
            id       = [int]$ev.Id
            time     = $ev.TimeCreated.ToString("o")
            message  = $message
        }
    }
    return @{ events = $events; note = $null }
}

function Get-SystemEvents {
    param([datetime]$WindowStart, [datetime]$WindowEnd)
    $events = @()
    $notes = @()

    $kernelPower = Get-EventsForFilter -Category "kernel_power" -Filter @{
        LogName = "System"; ProviderName = "Microsoft-Windows-Kernel-Power";
        StartTime = $WindowStart; EndTime = $WindowEnd; Id = @(41, 109)
    }
    $events += $kernelPower.events
    if ($kernelPower.note) { $notes += $kernelPower.note }

    $network = Get-EventsForFilter -Category "network" -Filter @{
        LogName = "System"; StartTime = $WindowStart; EndTime = $WindowEnd
    } -Limit 60 -ProviderPattern "WLAN|NetworkProfile|NCSI|Netwtw|Dhcp"
    $events += $network.events
    if ($network.note) { $notes += $network.note }

    $powerShell = Get-EventsForFilter -Category "powershell" -Filter @{
        LogName = "Microsoft-Windows-PowerShell/Operational";
        StartTime = $WindowStart; EndTime = $WindowEnd; Id = @(400, 600)
    }
    $events += $powerShell.events
    if ($powerShell.note) { $notes += $powerShell.note }

    $taskScheduler = Get-EventsForFilter -Category "task_scheduler" -Filter @{
        LogName = "Microsoft-Windows-TaskScheduler/Operational";
        StartTime = $WindowStart; EndTime = $WindowEnd
    } -Limit 30
    $events += $taskScheduler.events
    if ($taskScheduler.note) { $notes += $taskScheduler.note }

    $events = @($events | Sort-Object { $_.time } -Descending | Select-Object -First 50)
    return @{
        window_start = $WindowStart.ToString("o")
        window_end   = $WindowEnd.ToString("o")
        events       = $events
        notes        = $notes
    }
}

# --- Launch the child pipeline ------------------------------------------------
if (-not (Test-Path -LiteralPath $PipelineScript)) {
    Write-SupervisorLog "El script del pipeline no existe: $PipelineScript" -Level ERROR
    exit 2
}
if ($PollSeconds -le 0) {
    Write-SupervisorLog "El intervalo de sondeo (-PollSeconds) debe ser mayor que 0 segundos (recibido $PollSeconds)." -Level ERROR
    exit 2
}
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $StateDir)) {
    New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
}

Write-SupervisorLog "Supervisando el pipeline del $RunDate (hijo: $PipelineScript)"
$proc = $null
try {
    # No -Wait: it would also wait for the pipeline's descendants (the wrapper
    # processes), delaying the abort evidence when the main process dies first.
    # In PS 5.1 a -PassThru process with redirected output loses its ExitCode
    # unless EnableRaisingEvents keeps the handle, so it is set right away.
    # -Supervised enables the startup reconciliation of previous pending runs
    # (T-10/RF-4) inside the pipeline; the scheduled task does not pass it yet.
    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PipelineScript`"","-Supervised") `
        -PassThru `
        -RedirectStandardOutput $transcript -RedirectStandardError $transcriptErr
    try { $proc.EnableRaisingEvents = $true } catch {}
} catch {
    Write-SupervisorLog "No se pudo lanzar el pipeline: $($_.Exception.Message)" -Level ERROR
    exit 2
}

$pipelinePid = $proc.Id
Write-SupervisorLog "Pipeline lanzado PID $pipelinePid (transcripcion: $transcript)"

# Wait only for the direct child; the wrapper descendants are T-10's concern.
while (-not $proc.HasExited) { Start-Sleep -Seconds 1 }

$exitCode = Get-ProcessExitCode -Proc $proc
$closed = Test-PipelineClosed -LogPath $generalLog

# --- Normal closure: propagate the child's exit code -------------------------
if ($closed) {
    Write-SupervisorLog "El pipeline cerro con 'Fin pipeline' (exit $exitCode)."
    exit $exitCode
}

# --- Anomalous closure: write the abort evidence -----------------------------
Write-SupervisorLog "Cierre anomalo: el log general no tiene 'Fin pipeline' (exit $exitCode)." -Level ERROR
$now = Get-Date
$logExists = Test-Path -LiteralPath $generalLog

# Run window for the system events: from the general log's LAST "Inicio" time
# (fallback: midnight of the run date), capped to the present. The last block
# is the one being supervised; an earlier same-day block must not widen the
# window.
$windowStart = [datetime]::ParseExact(
    "$RunDate 00:00:00", "yyyy-MM-dd HH:mm:ss",
    [System.Globalization.CultureInfo]::InvariantCulture
)
if ($logExists) {
    try {
        $text = [System.IO.File]::ReadAllText($generalLog)
        $matches = [regex]::Matches(
            $text, '(?m)^(\d{2}:\d{2}:\d{2}).*Inicio pipeline scrapers'
        )
        if ($matches.Count -gt 0) {
            $lastMatch = $matches[$matches.Count - 1]
            $parsed = [datetime]::ParseExact(
                "$RunDate $($lastMatch.Groups[1].Value)", "yyyy-MM-dd HH:mm:ss",
                [System.Globalization.CultureInfo]::InvariantCulture
            )
            if ($parsed -le $now) { $windowStart = $parsed }
        }
    } catch {}
}
if ($windowStart -gt $now) { $windowStart = $now.AddHours(-1) }

$stateSnapshot = Get-RunStateSnapshot -Path (Join-Path $StateDir "$RunDate.json")

$payload = @{
    schema_version    = 1
    run_date          = $RunDate
    detected_at       = $now.ToString("o")
    reason            = "el log general no tiene 'Fin pipeline' (cierre anomalo)"
    exit_code         = $exitCode
    pipeline_pid      = $pipelinePid
    log_path          = $generalLog
    log_exists        = $logExists
    last_log_lines    = @(Get-LastLogLines -LogPath $generalLog -Count 25)
    transcripts       = @{
        stdout        = $transcript
        stderr        = $transcriptErr
        stdout_exists = (Test-Path -LiteralPath $transcript)
        stderr_exists = (Test-Path -LiteralPath $transcriptErr)
    }
    state_path        = $stateSnapshot.state_path
    state_exists      = $stateSnapshot.state_exists
    state_status      = $stateSnapshot.state_status
    state_started_at  = $stateSnapshot.state_started_at
    state_finished_at = $stateSnapshot.state_finished_at
    state_sources     = $stateSnapshot.state_sources
    state_error       = $stateSnapshot.state_error
    live_wrappers     = @(Get-LiveWrappers -ProjectsRoot $ProjectsRoot)
    system_events     = (Get-SystemEvents -WindowStart $windowStart -WindowEnd $now)
}

try {
    Write-JsonAtomic -Path $abortPath -InputObject $payload | Out-Null
} catch {
    Write-SupervisorLog "No se pudo escribir la evidencia $abortPath : $($_.Exception.Message)" -Level ERROR
    exit 2
}
Write-SupervisorLog "Evidencia escrita en $abortPath" -Level WARN

# --- Bounded wait for the run's live wrappers (T-10; RF-2) -------------------
#  The abort evidence is written first, so it survives a supervisor kill during
#  the wait. The wrappers are never stopped, only observed.
$waitStart = Get-Date
$waitResult = @{
    timed_out      = $false
    waited_seconds = 0
    live_wrappers  = @()
}
$liveNow = @(Get-LiveWrappers -ProjectsRoot $ProjectsRoot | Where-Object { $_.alive })
if ($liveNow.Count -eq 0) {
    Write-SupervisorLog "No hay wrappers vivos para $RunDate."
} elseif ($WaitHours -le 0) {
    $waitResult.timed_out = $true
    $waitResult.live_wrappers = $liveNow
    Write-SupervisorLog "Espera desactivada (-WaitHours $WaitHours) con $($liveNow.Count) wrapper(s) vivo(s)." -Level WARN
} else {
    $deadline = $waitStart.AddHours($WaitHours)
    Write-SupervisorLog "Esperando hasta $WaitHours h a que terminen $($liveNow.Count) wrapper(s) vivo(s)..."
    while ($true) {
        if ((Get-Date) -ge $deadline) {
            $waitResult.timed_out = $true
            $waitResult.live_wrappers = @(
                Get-LiveWrappers -ProjectsRoot $ProjectsRoot | Where-Object { $_.alive }
            )
            Write-SupervisorLog "Tope de espera alcanzado; el run queda pending." -Level WARN
            break
        }
        Start-Sleep -Seconds $PollSeconds
        $liveNow = @(Get-LiveWrappers -ProjectsRoot $ProjectsRoot | Where-Object { $_.alive })
        if ($liveNow.Count -eq 0) {
            Write-SupervisorLog "Los wrappers terminaron; se puede recuperar."
            break
        }
    }
}
$waitResult.waited_seconds = [int]((Get-Date) - $waitStart).TotalSeconds

if ($waitResult.timed_out) {
    $payload["wait"] = $waitResult
    try {
        Write-JsonAtomic -Path $abortPath -InputObject $payload | Out-Null
    } catch {
        Write-SupervisorLog "No se pudo actualizar $abortPath : $($_.Exception.Message)" -Level ERROR
        exit 2
    }
    exit 1
}

# --- Recovery executor (T-11 completes -Date/-PlanJson and the closure) ------
$recoveryLog = Join-Path $LogDir "recovery-$RunDate.log"
$recovery = @{
    script    = $RecoveryScript
    invoked   = $false
    exit_code = $null
    log       = $recoveryLog
    error     = $null
}
if (-not (Test-Path -LiteralPath $RecoveryScript)) {
    $recovery.error = "el ejecutor de recuperacion no existe"
    Write-SupervisorLog "No existe el ejecutor de recuperacion: $RecoveryScript" -Level ERROR
} elseif (-not (Test-RecoveryScriptSupportsDate -Path $RecoveryScript)) {
    # A legacy executor without -Date would receive the argument in $args and
    # silently sweep the whole history: never invoke it for a single run.
    $recovery.error = "el ejecutor aun no soporta -Date; recuperacion pendiente (T-11)"
    Write-SupervisorLog "El ejecutor $RecoveryScript aun no soporta -Date; recuperacion pendiente (T-11)." -Level WARN
} else {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RecoveryScript -Date $RunDate *> $recoveryLog
        $recovery.invoked = $true
        $recovery.exit_code = [int]$LASTEXITCODE
    } catch {
        $recovery.error = $_.Exception.Message
        Write-SupervisorLog "Fallo lanzando la recuperacion: $($_.Exception.Message)" -Level ERROR
    }
}
$payload["wait"] = $waitResult
$payload["recovery"] = $recovery
try {
    Write-JsonAtomic -Path $abortPath -InputObject $payload | Out-Null
} catch {
    Write-SupervisorLog "No se pudo actualizar $abortPath : $($_.Exception.Message)" -Level ERROR
    exit 2
}

if ($recovery.invoked -and $recovery.exit_code -eq 0) {
    Write-SupervisorLog "Recuperacion completada (exit 0)."
    exit 0
}
Write-SupervisorLog "La recuperacion no termino correctamente; el run sigue pendiente." -Level ERROR
exit 3
