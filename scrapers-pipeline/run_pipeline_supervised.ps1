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
#  Usage:
#    powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_pipeline_supervised.ps1
#    ... -RunDate 2026-10-01 -PipelineScript <path> -LogDir <dir> -StateDir <dir>
#
#  Exit codes:
#    0   the child closed with "Fin pipeline" and exited 0
#    N   the child closed with "Fin pipeline" and exited N (propagated)
#    1   anomalous closure (no "Fin pipeline"): abort.json was written
#    2   pre-flight/evidence failure: the pipeline script does not exist, the
#        child could not be launched, or abort.json could not be written.
#        No abort.json is written when the pipeline never ran.
# =============================================================================

param(
    [string]$RunDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$PipelineScript = (Join-Path $PSScriptRoot "run_scrapers_and_upload.ps1"),
    [string]$LogDir = (Join-Path $PSScriptRoot "logs"),
    [string]$StateDir = ""
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($StateDir)) { $StateDir = Join-Path $LogDir "run_state" }
$ProjectsRoot = Split-Path -Parent $PSScriptRoot

# Shared atomic JSON writer (T-03); the supervisor never writes run state.
. (Join-Path $PSScriptRoot "run_state.ps1")

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

# One entry per wrapper with both liveness signals: the .nightly.lock PID and
# the powershell.exe processes whose command line mentions the wrapper (the
# only signal for Multi-site, which has no lock). Every access is guarded so a
# missing lock or an unavailable CIM query never breaks the evidence.
function Get-LiveWrappers {
    $defs = @(
        @{ Name = "indeed";     Wrapper = (Join-Path $ProjectsRoot "indeed_jobs_scraper\run_nightly_indeed.ps1");      Lock = (Join-Path $ProjectsRoot "indeed_jobs_scraper\output\.nightly.lock") },
        @{ Name = "linkedin";   Wrapper = (Join-Path $ProjectsRoot "linkedin_jobs_scraper\run_nightly.ps1");           Lock = (Join-Path $ProjectsRoot "linkedin_jobs_scraper\data\.nightly.lock") },
        @{ Name = "multi_site"; Wrapper = (Join-Path $ProjectsRoot "multi_site_job_scraper\run_all.ps1");              Lock = $null },
        @{ Name = "infojobs";   Wrapper = (Join-Path $ProjectsRoot "infojobs_jobs_scraper\run_infojobs_nightly.ps1");  Lock = (Join-Path $ProjectsRoot "infojobs_jobs_scraper\data\.nightly.lock") }
    )

    $powerShellProcs = @()
    try {
        $powerShellProcs = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction Stop |
            Select-Object ProcessId, CommandLine)
    } catch { $powerShellProcs = @() }

    $result = @()
    foreach ($def in $defs) {
        $lockExists = $false
        $lockPid = $null
        $lockAlive = $false
        if ($def.Lock -and (Test-Path -LiteralPath $def.Lock)) {
            $lockExists = $true
            try {
                $raw = ([System.IO.File]::ReadAllText($def.Lock) -replace '^\uFEFF', '').Trim()
                if ($raw -match '^\d+$') {
                    $lockPid = [int]$raw
                    $lockAlive = $null -ne (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)
                }
            } catch {}
        }
        $cmdPids = @()
        foreach ($proc in $powerShellProcs) {
            $cmd = [string]$proc.CommandLine
            if ($cmd -and $cmd -like "*$($def.Wrapper)*") {
                $cmdPids += [int]$proc.ProcessId
            }
        }
        $result += @{
            name              = $def.Name
            wrapper           = $def.Wrapper
            lock_path         = $def.Lock
            lock_exists       = $lockExists
            lock_pid          = $lockPid
            lock_alive        = $lockAlive
            command_line_pids = $cmdPids
            alive             = ($lockAlive -or $cmdPids.Count -gt 0)
        }
    }
    return $result
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
    $proc = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","`"$PipelineScript`"") `
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
    live_wrappers     = @(Get-LiveWrappers)
    system_events     = (Get-SystemEvents -WindowStart $windowStart -WindowEnd $now)
}

try {
    Write-JsonAtomic -Path $abortPath -InputObject $payload | Out-Null
} catch {
    Write-SupervisorLog "No se pudo escribir la evidencia $abortPath : $($_.Exception.Message)" -Level ERROR
    exit 2
}
Write-SupervisorLog "Evidencia escrita en $abortPath" -Level WARN
exit 1
