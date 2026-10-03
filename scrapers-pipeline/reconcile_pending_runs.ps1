# =============================================================================
#  reconcile_pending_runs.ps1
#  Startup reconciliation of previous truncated runs (T-10; RF-2, RF-4).
#
#  Dot-source it from run_scrapers_and_upload.ps1 (only with -Supervised):
#      . (Join-Path $ScriptDir "reconcile_pending_runs.ps1")
#
#  Invoke-PendingReconciliation discovers, through the local Python CLI
#  (`python -m verification.recovery pending --before <date>`), the truncated
#  or pending runs older than the run that is starting and eligible for
#  automatic recovery (T-21: on/after 2026-10-01 and not superseded), and
#  invokes the recovery executor once per date when no wrapper is alive. Runs
#  excluded by the automatic scope are only recorded with their reason; the
#  historical ones stay covered by the manual recovery. The executor is
#  only invoked when its param(...) declares -Date (AST guard): a legacy
#  executor would otherwise receive the argument in $args and silently sweep
#  the whole history. It never throws and never touches the current run's
#  files: every failure is returned in the result and logged, so the new run
#  always continues (RF-4). It accesses no Azure; the executor it calls does.
#  `-PythonExe` (default `python`) is injectable for offline tests.
# =============================================================================

. (Join-Path $PSScriptRoot "wrapper_locks.ps1")
. (Join-Path $PSScriptRoot "recovery_support.ps1")

function Invoke-PendingReconciliation {
    param(
        [Parameter(Mandatory)][string]$ProjectsRoot,
        [Parameter(Mandatory)][string]$LogsDir,
        [Parameter(Mandatory)][string]$StateDir,
        [Parameter(Mandatory)][string]$Before,
        [Parameter(Mandatory)][string]$RecoveryScript,
        [string]$PythonExe = "python",
        [scriptblock]$Logger = $null
    )

    $result = @{
        discovered    = 0
        skipped_scope = @()
        invoked       = @()
        skipped_live  = @()
        failures      = @()
    }

    # The injected logger must never leak into this function's success stream,
    # or the caller would receive its messages mixed with the result hashtable.
    $log = {
        param([string]$Message, [string]$Level = "INFO")
        if ($Logger) { & $Logger $Message $Level | Out-Null }
        else { Write-Host "[$Level] $Message" }
    }

    # --- Discover previous pending/truncated runs (local Python CLI) ---------
    #  stdout carries only the JSON document; stderr (Python/pyarrow warnings)
    #  is captured apart so it can never corrupt the parse.
    $raw = $null
    $exit = 1
    $stderrFile = Join-Path ([System.IO.Path]::GetTempPath()) (
        "recovery-cli-" + [System.Guid]::NewGuid().ToString("N") + ".err"
    )
    $previousEap = $ErrorActionPreference
    try {
        # Native stderr must never become a terminating error: with
        # ErrorActionPreference=Stop, PS 5.1 raises NativeCommandError even
        # when stderr is redirected. The caller's preference is restored.
        $ErrorActionPreference = "Continue"
        Push-Location $PSScriptRoot
        try {
            $raw = & $PythonExe -m verification.recovery pending `
                --logs-dir $LogsDir --state-dir $StateDir --before $Before 2> $stderrFile
            $exit = $LASTEXITCODE
        } finally { Pop-Location }
    } catch {
        & $log "no se pudo ejecutar el descubrimiento de pendientes: $($_.Exception.Message)" "WARN"
        $result.failures += "descubrimiento: $($_.Exception.Message)"
        return $result
    } finally {
        $ErrorActionPreference = $previousEap
        if (Test-Path -LiteralPath $stderrFile) {
            try {
                $stderrText = ([System.IO.File]::ReadAllText($stderrFile)).Trim()
                if ($stderrText) {
                    & $log "aviso del descubrimiento: $stderrText" "WARN"
                }
            } catch {}
            Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
        }
    }
    if ($exit -ne 0) {
        & $log "el descubrimiento de pendientes fallo (python exit $exit)" "WARN"
        $result.failures += "descubrimiento: python exit $exit"
        return $result
    }

    $payload = $null
    try {
        $payload = ($raw -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        & $log "la salida del descubrimiento no es JSON valido: $($_.Exception.Message)" "WARN"
        $result.failures += "descubrimiento: JSON invalido"
        return $result
    }
    $runs = @()
    if ($null -ne $payload -and $payload.PSObject.Properties.Name -contains "runs") {
        $runs = @($payload.runs)
    }
    $result.discovered = $runs.Count

    # --- Record the runs excluded by the automatic scope (T-21) --------------
    #  The CLI already filters them (historical before 2026-10-01 or already
    #  superseded) and reports each one with its reason; the general log keeps
    #  the motive even though they are never invoked.
    $skipped = @()
    if ($null -ne $payload -and $payload.PSObject.Properties.Name -contains "skipped") {
        $skipped = @($payload.skipped)
    }
    foreach ($skippedRun in $skipped) {
        $skippedDate = [string]$skippedRun.run_date
        $skippedReason = [string]$skippedRun.reason
        $result.skipped_scope += $skippedDate
        & $log "[$skippedDate] no se recupera automaticamente: $skippedReason" "INFO"
    }

    if ($runs.Count -eq 0) { return $result }

    # --- Skip while any wrapper is still alive -------------------------------
    #  Liveness is not date-scoped: a live wrapper means some run's data is not
    #  final yet, so recovery waits for a later start (RF-2). Nothing is killed.
    $live = @(Get-LiveWrappers -ProjectsRoot $ProjectsRoot | Where-Object { $_.alive })
    if ($live.Count -gt 0) {
        foreach ($run in $runs) { $result.skipped_live += [string]$run.run_date }
        & $log "hay $($live.Count) wrapper(s) vivo(s); se pospone la recuperacion de $($result.skipped_live -join ', ')" "WARN"
        return $result
    }

    # --- Recover each previous run; a failure never aborts the new run -------
    foreach ($run in $runs) {
        $date = [string]$run.run_date
        if ([string]::IsNullOrWhiteSpace($date)) {
            $result.failures += "run sin fecha"
            continue
        }
        if (-not (Test-Path -LiteralPath $RecoveryScript)) {
            $result.failures += "$date : ejecutor no encontrado"
            & $log "[$date] ejecutor de recuperacion no encontrado: $RecoveryScript" "ERROR"
            continue
        }
        if (-not (Test-RecoveryScriptSupportsDate -Path $RecoveryScript)) {
            # A legacy executor without -Date would receive the argument in
            # $args and silently sweep the whole history: never invoke it.
            $result.failures += "$date : el ejecutor aun no soporta -Date (T-11)"
            & $log "[$date] el ejecutor aun no soporta -Date; recuperacion pendiente (T-11)" "WARN"
            continue
        }
        $recoveryLog = Join-Path $LogsDir "recovery-$date.log"
        $code = -1
        $previousEap = $ErrorActionPreference
        try {
            # Same reason as the discovery CLI: native stderr must not throw
            # under ErrorActionPreference=Stop.
            $ErrorActionPreference = "Continue"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RecoveryScript -Date $date *> $recoveryLog
            $code = [int]$LASTEXITCODE
        } catch {
            & $log "[$date] fallo lanzando la recuperacion: $($_.Exception.Message)" "ERROR"
            $result.failures += "$date : $($_.Exception.Message)"
            continue
        } finally {
            $ErrorActionPreference = $previousEap
        }
        if ($code -eq 0) {
            $result.invoked += $date
            & $log "[$date] recuperacion completada" "INFO"
        } else {
            $result.failures += "$date : exit $code"
            & $log "[$date] la recuperacion fallo (exit $code); se reintentara en el proximo arranque" "WARN"
        }
    }
    return $result
}
