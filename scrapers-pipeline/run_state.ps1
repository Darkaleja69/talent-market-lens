# =============================================================================
#  run_state.ps1  -  Persistent run state for the scrapers pipeline (RF-1)
#
#  Shared helper. Dot-source it from the scripts that need it:
#      . (Join-Path $ScriptDir "run_state.ps1")
#
#  It depends only on the local filesystem: no config.ps1 and no Azure. It is
#  safe to load under `Set-StrictMode -Version Latest` and `$ErrorActionPreference
#  = "Stop"`; errors are raised as Spanish messages for the operator.
#
#  ---------------------------------------------------------------------------
#  FILE
#  ---------------------------------------------------------------------------
#  Default location: logs/run_state/<run_date>.json
#  (the caller passes the state directory, e.g. Join-Path $LogDir "run_state").
#
#  ---------------------------------------------------------------------------
#  JSON SCHEMA (keys in English; timestamps are ISO-8601, .NET "o" format)
#  ---------------------------------------------------------------------------
#  {
#    "schema_version": 1,
#    "run_date": "2026-10-01",                 # logical run day (YYYY-MM-DD)
#    "started_at": "2026-10-01T00:00:04.1+02:00",
#    "finished_at": null,                      # filled by Close-RunState
#    "status": "pending",                      # run level: pending | closed
#    "sources": {
#      "indeed": {
#        "status": "ok",                       # source level, see below
#        "uploaded": 1,                        # files uploaded after validation
#        "rejected": 0,                        # files rejected by validation
#        "last_activity_at": "2026-10-01T01:59:29.1+02:00"
#      }
#    }
#  }
#
#  RUN STATUS
#    pending -> the run is still open (or died before closing; a "pending"
#               state whose general log has no "Fin pipeline" is a truncated
#               run, RF-1).
#    closed  -> the run reached a definitive end (normal or recovered).
#
#  SOURCE STATUS
#    pending -> no result recorded yet (scraper still running / not started).
#    ok      -> all valid files were published.
#    partial -> some files were published, others were rejected.
#    failed  -> nothing could be published.
#    no_data -> the source produced no files (or no new offers) this run.
#    killed  -> the wrapper hit its timeout and was stopped.
#
#  ---------------------------------------------------------------------------
#  ATOMIC WRITE
#  ---------------------------------------------------------------------------
#  Write-JsonAtomic serializes to a temporary file in the SAME directory and
#  publishes it with File.Replace (existing destination) or File.Move (new
#  destination): a same-volume rename, so a reader never sees a half-written
#  file. Publishers of the same path are serialized with a named mutex and the
#  replacement is retried briefly (5 x 100 ms) because it can fail transiently
#  while the destination is locked (antivirus, indexer, an open reader without
#  FILE_SHARE_DELETE). Output has no BOM and ends with a newline.
# =============================================================================

# -----------------------------------------------------------------------------
#  New-RunState
#  Builds the in-memory state of a run: pending, no sources yet, finished_at
#  null. Returns a hashtable with the documented schema (not yet on disk).
# -----------------------------------------------------------------------------
function New-RunState {
    param(
        [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
        [string]$RunDate = (Get-Date -Format "yyyy-MM-dd"),
        [string]$StartedAt
    )
    if ([string]::IsNullOrWhiteSpace($StartedAt)) { $StartedAt = (Get-Date).ToString("o") }
    return @{
        schema_version = 1
        run_date       = $RunDate
        started_at     = $StartedAt
        finished_at    = $null
        status         = "pending"
        sources        = @{}
    }
}

# -----------------------------------------------------------------------------
#  Set-RunSourceState
#  Upserts one source entry (status, uploaded, rejected, last_activity_at).
#  Mutates the state in place; last_activity_at defaults to now. Each call
#  replaces the whole source entry, so pass the counters of that milestone.
# -----------------------------------------------------------------------------
function Set-RunSourceState {
    param(
        [Parameter(Mandatory)][hashtable]$State,
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)]
        [ValidateSet("pending", "ok", "partial", "failed", "no_data", "killed")]
        [string]$Status,
        [int]$Uploaded = 0,
        [int]$Rejected = 0,
        [string]$LastActivityAt
    )
    if ([string]::IsNullOrWhiteSpace($LastActivityAt)) { $LastActivityAt = (Get-Date).ToString("o") }
    $sources = $State["sources"]
    if (-not ($sources -is [hashtable])) {
        # Missing key or a damaged container (e.g. a JSON round-trip):
        # reinitialize so the update never fails on a null index.
        $sources = @{}
        $State["sources"] = $sources
    }
    $sources[$Source] = @{
        status           = $Status
        uploaded         = $Uploaded
        rejected         = $Rejected
        last_activity_at = $LastActivityAt
    }
}

# -----------------------------------------------------------------------------
#  Close-RunState
#  Marks the run as closed with its finish time. Mutates the state in place;
#  no parameters are returned so the caller can keep the same object.
# -----------------------------------------------------------------------------
function Close-RunState {
    param(
        [Parameter(Mandatory)][hashtable]$State,
        [string]$FinishedAt
    )
    if ([string]::IsNullOrWhiteSpace($FinishedAt)) { $FinishedAt = (Get-Date).ToString("o") }
    $State["status"]      = "closed"
    $State["finished_at"] = $FinishedAt
}

# -----------------------------------------------------------------------------
#  Write-JsonAtomic
#  Writes any object as JSON to $Path without BOM. Serializes to a temporary
#  file in the same directory and replaces the destination atomically; on
#  failure the temporary file is removed. Creates the parent directory if
#  needed. Returns the destination path.
# -----------------------------------------------------------------------------
function Write-JsonAtomic {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$InputObject,
        [int]$Depth = 6
    )
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        try {
            New-Item -ItemType Directory -Path $dir -Force -ErrorAction Stop | Out-Null
        } catch {
            throw "No se pudo crear el directorio '$dir': $($_.Exception.Message)"
        }
    }
    $tmp = "$Path.$([System.Guid]::NewGuid().ToString('N')).tmp"
    try {
        $json = $InputObject | ConvertTo-Json -Depth $Depth
        # PS 5.1 Set-Content -Encoding UTF8 adds a BOM; WriteAllText does not.
        [System.IO.File]::WriteAllText($tmp, $json + [Environment]::NewLine,
            [System.Text.UTF8Encoding]::new($false))
    } catch {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
        throw "No se pudo escribir el temporal '$tmp': $($_.Exception.Message)"
    }
    # Publish the temp file atomically (same-volume rename): File.Replace when
    # the destination already exists, File.Move when it does not. Concurrent
    # publishers of the same path are serialized with a named mutex, because
    # two simultaneous ReplaceFile calls can race and leave the destination
    # briefly missing for readers. Both operations can still fail transiently
    # while the destination is locked (antivirus, indexer, an open reader
    # without FILE_SHARE_DELETE), so retry briefly before giving up.
    $mutexName = "TML-json-write-" + ($Path.ToLowerInvariant() -replace '[^a-z0-9]', '_')
    $mutex = [System.Threading.Mutex]::new($false, $mutexName)
    $hasLock = $false
    try {
        try {
            $hasLock = $mutex.WaitOne(10000)
        } catch [System.Threading.AbandonedMutexException] {
            # The previous owner died without releasing: ownership is granted.
            $hasLock = $true
        }
        if (-not $hasLock) {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
            throw "No se pudo obtener el bloqueo para escribir '$Path' (otro proceso lo retiene)."
        }
        $maxAttempts  = 5
        $retryDelayMs = 100
        for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
            try {
                if ([System.IO.File]::Exists($Path)) {
                    [System.IO.File]::Replace($tmp, $Path, [NullString]::Value)
                } else {
                    [System.IO.File]::Move($tmp, $Path)
                }
                return $Path
            } catch {
                if ($attempt -ge $maxAttempts) {
                    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
                    throw "No se pudo reemplazar '$Path' de forma atomica tras $maxAttempts intentos: $($_.Exception.Message)"
                }
                Start-Sleep -Milliseconds $retryDelayMs
            }
        }
        return $Path
    } finally {
        if ($hasLock) { [void]$mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

# -----------------------------------------------------------------------------
#  Write-RunState
#  Persists the in-memory run state to <StateDir>/<run_date>.json using the
#  atomic JSON writer. Creates StateDir if it does not exist. Returns the
#  destination path.
# -----------------------------------------------------------------------------
function Write-RunState {
    param(
        [Parameter(Mandatory)][hashtable]$State,
        [Parameter(Mandatory)][string]$StateDir
    )
    $runDate = [string]$State["run_date"]
    if ($runDate -notmatch '^\d{4}-\d{2}-\d{2}$') {
        throw "El estado del run no tiene un run_date valido ('$runDate'); se espera YYYY-MM-DD."
    }
    $path = Join-Path $StateDir "$runDate.json"
    return Write-JsonAtomic -Path $path -InputObject $State
}
