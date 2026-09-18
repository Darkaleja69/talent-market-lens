# =============================================================================
#  cleanup_old_data.ps1
#  Limpieza preventiva de datos acumulados de los scrapers para evitar que el
#  disco se llene. Disenado para ejecutarse CADA 10 DIAS unos minutos ANTES de
#  la run nocturna (00:00) para no pisar procesos.
#
#  Que borra (SOLO ficheros sin uso por el codigo o ya subidos a ADLS):
#    - Checkpoints antiguos  progress_*.csv   (conserva state.json y el ultimo)
#    - Snapshots parquet con fecha en el nombre (conserva el ultimo + jobs.csv/
#      jobs.parquet / jobs_unified.* / last_run.json)
#
#  Seguridad:
#    - NUNCA borra ficheros modificados en las ultimas 24h (la run nocturna
#      puede estar en curso; la pipeline de upload usa RunStart como filtro).
#    - NUNCA borra state.json (es el estado de reanudacion) ni los ficheros
#      canonicos de salida (jobs.parquet, jobs_unified.parquet, ...).
#    - Conserva siempre el fichero mas reciente de cada carpeta como respaldo.
#
#  Registro en el scheduler (cada 10 dias a las 23:45):
#    schtasks /create /tn "Scrapers_Cleanup_Old_Data" /sc DAILY /mo 10 /st 23:45 /f ^
#      /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<ruta>\scrapers-pipeline\cleanup_old_data.ps1\""
# =============================================================================

$ErrorActionPreference = "Continue"

$ProjectsRoot = Split-Path -Parent $PSScriptRoot
$LogDir       = Join-Path $ProjectsRoot "scrapers-pipeline\logs"
$LogFile      = Join-Path $LogDir "cleanup.log"

# Margen de seguridad: solo se borra lo que no se ha modificado en >= 24h.
$Cutoff = (Get-Date).AddHours(-24)

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-CleanupLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

Write-CleanupLog "=== INICIO LIMPIEZA (cutoff: $($Cutoff.ToString('yyyy-MM-dd HH:mm'))) ==="

$deletedFiles = 0
$deletedBytes = 0

# -----------------------------------------------------------------------------
#  1) Checkpoints progress_*.csv  (estado transitorio; solo se usa state.json)
# -----------------------------------------------------------------------------
$checkpointDirs = @(
    (Join-Path $ProjectsRoot "linkedin_jobs_scraper\data\checkpoints"),
    (Join-Path $ProjectsRoot "multi_site_job_scraper\data\irishjobs\checkpoints"),
    (Join-Path $ProjectsRoot "multi_site_job_scraper\data\stepstone_nl\checkpoints"),
    (Join-Path $ProjectsRoot "multi_site_job_scraper\data\glassdoor\checkpoints"),
    (Join-Path $ProjectsRoot "multi_site_job_scraper\data\jobs_ch\checkpoints"),
    (Join-Path $ProjectsRoot "indeed_jobs_scraper\output\checkpoints")
)

foreach ($dir in $checkpointDirs) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }

    # Conservar SIEMPRE el progress_*.csv mas reciente (respaldo)
    $latest = Get-ChildItem -LiteralPath $dir -File -Filter "progress_*.csv" |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1

    $candidates = Get-ChildItem -LiteralPath $dir -File -Filter "progress_*.csv" |
        Where-Object {
            $_.LastWriteTime -lt $Cutoff -and
            (-not $latest -or $_.FullName -ne $latest.FullName)
        }

    foreach ($f in $candidates) {
        try {
            $deletedBytes += $f.Length
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
            $deletedFiles++
        } catch {
            Write-CleanupLog "  AVISO: no se pudo borrar $($f.Name): $($_.Exception.Message)"
        }
    }
    if ($candidates.Count -gt 0) {
        Write-CleanupLog "  checkpoints $dir : borrados $($candidates.Count) progress_*.csv"
    }
}

# -----------------------------------------------------------------------------
#  2) Snapshots parquet con fecha en el nombre (restos de runs previos)
#     - linkedin: data\output\jobs_<fechas>.parquet
#     - multi:    data\merged\jobs_unified_<fechas>.parquet
#  Se conserva jobs.csv / jobs.parquet / jobs_unified.* / last_run.json y el
#  snapshot con fecha mas reciente.
# -----------------------------------------------------------------------------
$snapshotSpecs = @(
    @{
        Dir      = Join-Path $ProjectsRoot "linkedin_jobs_scraper\data\output"
        Pattern  = "jobs_*.parquet"
        Keep     = @("jobs.csv", "jobs.parquet")
    },
    @{
        Dir      = Join-Path $ProjectsRoot "multi_site_job_scraper\data\merged"
        Pattern  = "jobs_unified_*.parquet"
        Keep     = @("jobs_unified.csv", "jobs_unified.parquet", "last_run.json")
    }
)

foreach ($spec in $snapshotSpecs) {
    $dir = $spec.Dir
    if (-not (Test-Path -LiteralPath $dir)) { continue }

    $latest = Get-ChildItem -LiteralPath $dir -File -Filter $spec.Pattern |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1

    $candidates = Get-ChildItem -LiteralPath $dir -File -Filter $spec.Pattern |
        Where-Object {
            $_.LastWriteTime -lt $Cutoff -and
            (-not $latest -or $_.FullName -ne $latest.FullName)
        }

    foreach ($f in $candidates) {
        try {
            $deletedBytes += $f.Length
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
            $deletedFiles++
        } catch {
            Write-CleanupLog "  AVISO: no se pudo borrar $($f.Name): $($_.Exception.Message)"
        }
    }
    if ($candidates.Count -gt 0) {
        Write-CleanupLog "  snapshots $dir : borrados $($candidates.Count) parquet historicos"
    }
}

$freedGB = [math]::Round($deletedBytes / 1GB, 2)
Write-CleanupLog "=== FIN LIMPIEZA: $deletedFiles archivo(s), $freedGB GB liberados ==="