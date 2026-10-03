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
#  Retencion y runs pendientes (T-13; RF-2):
#    - La decision de caducidad vive en `python -m verification.recovery expire`
#      (funcion pura con tests): los ficheros de runs `pending` se conservan
#      dentro de la retencion (-RetentionDays, por defecto 7 dias) y solo
#      caducan cuando la superan; el resto caduca al superar la retencion.
#    - Si el CLI falla o su salida es ilegible, no se borra NADA (conservador)
#      y se registra en cleanup.log.
#
#  Seguridad:
#    - NUNCA borra ficheros modificados en las ultimas 24h (la run nocturna
#      puede estar en curso; la pipeline de upload usa RunStart como filtro).
#    - NUNCA borra state.json (es el estado de reanudacion) ni los ficheros
#      canonicos de salida (jobs.parquet, jobs_unified.parquet, ...).
#    - Conserva siempre el fichero mas reciente de cada carpeta como respaldo.
#
#  Overrides de testabilidad (vacio = valor por defecto): -ProjectsRoot,
#  -LogDir, -PythonExe.
#
#  Registro en el scheduler (cada 10 dias a las 23:45):
#    schtasks /create /tn "Scrapers_Cleanup_Old_Data" /sc DAILY /mo 10 /st 23:45 /f ^
#      /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"<ruta>\scrapers-pipeline\cleanup_old_data.ps1\""
# =============================================================================

param(
    [int]$RetentionDays = 7,
    [string]$PythonExe = "python",
    [string]$ProjectsRoot = "",
    [string]$LogDir = ""
)

$ErrorActionPreference = "Continue"

if ([string]::IsNullOrWhiteSpace($ProjectsRoot)) {
    $ProjectsRoot = Split-Path -Parent $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($LogDir)) {
    $LogDir = Join-Path $ProjectsRoot "scrapers-pipeline\logs"
}
$LogFile = Join-Path $LogDir "cleanup.log"

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-CleanupLog {
    param([string]$Message)
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

if ($RetentionDays -lt 0) {
    Write-CleanupLog "ERROR: -RetentionDays debe ser >= 0 (recibido $RetentionDays)."
    exit 2
}

# Margen de seguridad: solo se borra lo que no se ha modificado en >= 24h.
$Cutoff = (Get-Date).AddHours(-24)

# Ejecuta un subcomando del modulo local desde $PSScriptRoot y captura salida.
function Invoke-CleanupCli {
    param([string[]]$CliArgs)
    $previousEap = $ErrorActionPreference
    Push-Location $PSScriptRoot
    try {
        $ErrorActionPreference = "Continue"
        $output = & $PythonExe -m verification.recovery @CliArgs 2>&1
        return @{ exit_code = $LASTEXITCODE; output = @($output) }
    } finally {
        $ErrorActionPreference = $previousEap
        Pop-Location
    }
}

# Consulta los runs pendientes y decide que candidatos caducan. Devuelve un
# mapa path -> decision, o $null si el CLI falla (el llamador no borra nada).
function Get-ExpireDecisionMap {
    param([object[]]$Files)
    if ($Files.Count -eq 0) { return @{} }

    $stamp = (Get-Date).Ticks
    $pendingFile = Join-Path $env:TEMP "cleanup-pending-$stamp.json"
    $candidatesFile = Join-Path $env:TEMP "cleanup-candidates-$stamp.json"
    $decisionsFile = Join-Path $env:TEMP "cleanup-decisions-$stamp.json"

    try {
        $pendingCli = Invoke-CleanupCli -CliArgs @(
            "pending", "--logs-dir", $LogDir,
            "--state-dir", (Join-Path $LogDir "run_state"), "--out", $pendingFile
        )
        foreach ($line in $pendingCli.output) { Write-CleanupLog "  pending: $line" }
        if ($pendingCli.exit_code -ne 0 -or -not (Test-Path -LiteralPath $pendingFile)) {
            Write-CleanupLog "  AVISO: no se pudieron consultar los runs pendientes (exit $($pendingCli.exit_code)); no se borra nada."
            return $null
        }

        $candidates = @()
        foreach ($file in $Files) {
            $candidates += @{ path = $file.FullName; mtime = $file.LastWriteTime.ToString("o") }
        }
        [System.IO.File]::WriteAllText($candidatesFile,
            (@{ candidates = $candidates } | ConvertTo-Json -Depth 3),
            [System.Text.UTF8Encoding]::new($false))

        $expireCli = Invoke-CleanupCli -CliArgs @(
            "expire", "--candidates", $candidatesFile, "--pending", $pendingFile,
            "--retention-days", "$RetentionDays", "--out", $decisionsFile
        )
        foreach ($line in $expireCli.output) { Write-CleanupLog "  expire: $line" }
        if ($expireCli.exit_code -ne 0 -or -not (Test-Path -LiteralPath $decisionsFile)) {
            Write-CleanupLog "  AVISO: no se pudo decidir la caducidad (exit $($expireCli.exit_code)); no se borra nada."
            return $null
        }

        $decisions = $null
        try {
            $decisions = [System.IO.File]::ReadAllText($decisionsFile) | ConvertFrom-Json
        } catch {}
        if ($null -eq $decisions) {
            Write-CleanupLog "  AVISO: decisiones de caducidad ilegibles; no se borra nada."
            return $null
        }

        $map = @{}
        foreach ($decision in @($decisions.decisions)) {
            if ($null -ne $decision) { $map[[string]$decision.path] = $decision }
        }
        return $map
    } finally {
        foreach ($path in @($pendingFile, $candidatesFile, $decisionsFile)) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-CleanupLog "=== INICIO LIMPIEZA (cutoff: $($Cutoff.ToString('yyyy-MM-dd HH:mm')), retencion: $RetentionDays dias) ==="

$deletedFiles = 0
$deletedBytes = 0
$allCandidates = @()

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

    $candidates = @(Get-ChildItem -LiteralPath $dir -File -Filter "progress_*.csv" |
        Where-Object {
            $_.LastWriteTime -lt $Cutoff -and
            (-not $latest -or $_.FullName -ne $latest.FullName)
        })

    if ($candidates.Count -gt 0) {
        Write-CleanupLog "  checkpoints $dir : $($candidates.Count) candidato(s)"
        $allCandidates += $candidates
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

    $candidates = @(Get-ChildItem -LiteralPath $dir -File -Filter $spec.Pattern |
        Where-Object {
            $_.LastWriteTime -lt $Cutoff -and
            (-not $latest -or $_.FullName -ne $latest.FullName)
        })

    if ($candidates.Count -gt 0) {
        Write-CleanupLog "  snapshots $dir : $($candidates.Count) candidato(s)"
        $allCandidates += $candidates
    }
}

# -----------------------------------------------------------------------------
#  3) Decision de caducidad (Python) y borrado
# -----------------------------------------------------------------------------
if ($allCandidates.Count -eq 0) {
    Write-CleanupLog "  sin candidatos que puedan caducar"
} else {
    $decisionMap = Get-ExpireDecisionMap -Files $allCandidates
    if ($null -eq $decisionMap) {
        Write-CleanupLog "  CONSERVADOR: no se borra nada (decision de caducidad no disponible)"
    } else {
        foreach ($file in $allCandidates) {
            $decision = $null
            if ($decisionMap.ContainsKey($file.FullName)) {
                $decision = $decisionMap[$file.FullName]
            }
            if ($null -eq $decision) {
                Write-CleanupLog "  CONSERVA [sin decision] $($file.FullName)"
                continue
            }
            if ([string]$decision.action -eq "expire") {
                try {
                    $deletedBytes += $file.Length
                    Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
                    $deletedFiles++
                    Write-CleanupLog "  BORRA $($file.FullName): $($decision.reason)"
                } catch {
                    Write-CleanupLog "  AVISO: no se pudo borrar $($file.FullName): $($_.Exception.Message)"
                }
            } else {
                $tag = if ([bool]$decision.pending) { "pendiente" } else { "retencion" }
                Write-CleanupLog "  CONSERVA [$tag] $($file.FullName): $($decision.reason)"
            }
        }
    }
}

$freedGB = [math]::Round($deletedBytes / 1GB, 2)
Write-CleanupLog "=== FIN LIMPIEZA: $deletedFiles archivo(s), $freedGB GB liberados ==="
