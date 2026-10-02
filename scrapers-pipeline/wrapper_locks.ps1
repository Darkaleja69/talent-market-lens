# =============================================================================
#  wrapper_locks.ps1
#  Shared wrapper-liveness detection (T-09/T-10; RF-2, RF-7).
#
#  Dot-source it from the scripts that need it:
#      . (Join-Path $PSScriptRoot "wrapper_locks.ps1")
#
#  Get-LiveWrappers reports one entry per wrapper (mirroring config.ps1) with
#  both signals available locally, without Azure and without killing anything:
#
#    - the .nightly.lock file (Indeed output\, LinkedIn/InfoJobs data\) with
#      its PID and whether that process is still alive;
#    - the powershell.exe processes whose command line mentions the wrapper
#      (the only signal for Multi-site, which has no lock).
#
#  Every access is guarded: a missing lock, a corrupt PID or an unavailable
#  CIM query yields a dead/empty signal instead of an exception.
# =============================================================================

function Get-LiveWrappers {
    param([Parameter(Mandatory)][string]$ProjectsRoot)

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
