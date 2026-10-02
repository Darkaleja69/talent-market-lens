# =============================================================================
#  recover_and_upload.ps1
#  Sube datos de ejecuciones pasadas que nunca llegaron a ADLS porque los
#  scrapers fallaron antes de que el pipeline los subiera.
#
#  Para cada scraper, busca el parquet mas reciente de cada dia y lo sube a
#  landing/<scraper>/dia=YYYY-MM-DD/ usando azcopy.
#
#  Uso historico (sin -Date ni -PlanJson): barrido completo de los restos.
#    .\recover_and_upload.ps1
#    .\recover_and_upload.ps1 -DryRun    (solo muestra lo que subiria)
#
#  Uso planificado (T-11; RF-2, RF-3): recupera UN run concreto.
#    .\recover_and_upload.ps1 -Date 2026-10-01
#    .\recover_and_upload.ps1 -PlanJson C:\...\plan.json
#  Con -Date se genera el plan con `python -m verification.recovery plan`
#  (--state-dir aplica la idempotencia del estado del run); con -PlanJson se
#  consume un plan ya generado. Cada item recuperable pasa por staging,
#  filtrado OnlyNewOffers, validacion completa (--coherence/--fingerprint-source),
#  subida con --as-subdir=false, manifest con `remote` sin BOM y actualizacion
#  de uploaded_keys SOLO tras exito. Los rechazados se cuentan y nunca se
#  suben. No escribe _READY ni el cierre del log (T-12).
#
#  Overrides de testabilidad (vacio = valor de config.ps1): -AzCopyPath,
#  -PythonExe, -MergeScript, -ProjectsRoot, -LogDir, -StateDir,
#  -UploadedKeysDir, -QuarantineDir.
# =============================================================================

param(
    [string]$Date = "",
    [string]$PlanJson = "",
    [switch]$DryRun,
    [string]$AzCopyPath = "",
    [string]$PythonExe = "python",
    [string]$MergeScript = "",
    [string]$ProjectsRoot = "",
    [string]$LogDir = "",
    [string]$StateDir = "",
    [string]$UploadedKeysDir = "",
    [string]$QuarantineDir = ""
)

# config.ps1 defines the same variables, so keep the caller's overrides.
$OverrideAzCopyPath      = $AzCopyPath
$OverridePythonExe       = $PythonExe
$OverrideMergeScript     = $MergeScript
$OverrideProjectsRoot    = $ProjectsRoot
$OverrideLogDir          = $LogDir
$OverrideStateDir        = $StateDir
$OverrideUploadedKeysDir = $UploadedKeysDir
$OverrideQuarantineDir   = $QuarantineDir

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Today     = Get-Date -Format "yyyy-MM-dd"
$LogDate   = $Today
if (-not [string]::IsNullOrWhiteSpace($Date)) { $LogDate = $Date }
$LogDir    = Join-Path $ScriptDir "logs"
$LogFile   = Join-Path $LogDir "recover-$LogDate.log"

if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log {
    param([string]$Message, [ValidateSet("INFO","WARN","ERROR")] [string]$Level = "INFO")
    $line = "{0}  [{1}]  {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message
    Add-Content -LiteralPath $LogFile -Value $line
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host $line -ForegroundColor Yellow }
    else { Write-Host $line }
}

# --- Cargar configuracion ---
. (Join-Path $ScriptDir "config.ps1")

# --- Overrides de testabilidad (despues de config, que define los mismos) ---
if (-not [string]::IsNullOrWhiteSpace($OverrideAzCopyPath)) { $AzCopyPath = $OverrideAzCopyPath }
if (-not [string]::IsNullOrWhiteSpace($OverridePythonExe)) { $PythonExe = $OverridePythonExe }
if (-not [string]::IsNullOrWhiteSpace($OverrideProjectsRoot)) { $ProjectsRoot = $OverrideProjectsRoot }
if (-not [string]::IsNullOrWhiteSpace($OverrideLogDir)) {
    $LogDir = $OverrideLogDir
    $LogFile = Join-Path $LogDir "recover-$LogDate.log"
}
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
if (-not [string]::IsNullOrWhiteSpace($OverrideStateDir)) {
    $script:RecoveryStateDir = $OverrideStateDir
} else {
    $script:RecoveryStateDir = Join-Path $LogDir "run_state"
}
if (-not [string]::IsNullOrWhiteSpace($OverrideUploadedKeysDir)) {
    $script:UploadedKeysDir = $OverrideUploadedKeysDir
} else {
    $script:UploadedKeysDir = Join-Path $ScriptDir "uploaded_keys"
}
if (-not [string]::IsNullOrWhiteSpace($OverrideQuarantineDir)) { $QuarantineRoot = $OverrideQuarantineDir }
if (-not [string]::IsNullOrWhiteSpace($OverrideMergeScript)) {
    $MergeScript = $OverrideMergeScript
} else {
    $MergeScript = Join-Path $ProjectsRoot "multi_site_job_scraper\merge.py"
}

if ([string]::IsNullOrWhiteSpace($SasToken)) {
    Write-Log "LANDING_SAS_TOKEN no definida. Ejecuta generate_sas.ps1." -Level ERROR
    exit 2
}

$azc = Get-Command $AzCopyPath -ErrorAction SilentlyContinue
if (-not $azc) {
    Write-Log "AzCopy no encontrado en PATH ($AzCopyPath)." -Level ERROR
    exit 2
}

# =============================================================================
#  T-11: RECUPERACION PLANIFICADA (plan-driven; RF-2, RF-3)
# =============================================================================

# Obtiene el plan: de -PlanJson o generandolo con el CLI local.
function Get-RecoveryPlan {
    param([string]$RunDate, [string]$PlanFile)
    if (-not [string]::IsNullOrWhiteSpace($PlanFile)) {
        if (-not (Test-Path -LiteralPath $PlanFile)) {
            throw "no existe el plan indicado: $PlanFile"
        }
        try {
            return (Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json)
        } catch {
            throw "plan JSON ilegible ($PlanFile): $($_.Exception.Message)"
        }
    }

    $stamp = (Get-Date).Ticks
    $planFile = Join-Path $env:TEMP "recovery-plan-$RunDate-$stamp.json"
    $stderrFile = Join-Path $env:TEMP "recovery-plan-$RunDate-$stamp.err"
    $cliExit = 1
    $previousEap = $ErrorActionPreference
    try {
        # Native stderr must not throw under ErrorActionPreference=Stop.
        $ErrorActionPreference = "Continue"
        Push-Location $ScriptDir
        try {
            # Capture stdout so the CLI's "path" line never leaks into this
            # function's return value (it must be the plan object only).
            $cliOut = & $PythonExe -m verification.recovery plan --run-date $RunDate `
                --projects-root $ProjectsRoot --state-dir $script:RecoveryStateDir `
                --out $planFile 2> $stderrFile
            $cliExit = $LASTEXITCODE
            foreach ($line in @($cliOut)) { Write-Log "plan: $line" }
        } finally { Pop-Location }
    } finally {
        $ErrorActionPreference = $previousEap
        if (Test-Path -LiteralPath $stderrFile) {
            try {
                $stderrText = ([System.IO.File]::ReadAllText($stderrFile)).Trim()
                if ($stderrText) { Write-Log "plan: aviso del CLI: $stderrText" -Level WARN }
            } catch {}
            Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
        }
    }
    if ($cliExit -ne 0 -or -not (Test-Path -LiteralPath $planFile)) {
        throw "el CLI del plan fallo (exit $cliExit)"
    }
    try {
        return (Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json)
    } catch {
        throw "plan JSON ilegible ($planFile): $($_.Exception.Message)"
    }
}

# Actualiza uploaded_keys/<fuente>.json SOLO tras exito de la subida.
function Update-UploadedKeys {
    param([string]$Source, [string]$NewKeysFile)
    $stateFile = Join-Path $script:UploadedKeysDir "$Source.json"
    $stateDir = Split-Path -Parent $stateFile
    if (-not (Test-Path -LiteralPath $stateDir)) { New-Item -ItemType Directory -Path $stateDir -Force | Out-Null }
    $known = @()
    if (Test-Path -LiteralPath $stateFile) {
        try {
            $known = @((Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).keys)
        } catch { $known = @() }
    }
    try {
        $nk = Get-Content -LiteralPath $NewKeysFile -Raw | ConvertFrom-Json
        foreach ($k in @($nk.keys)) { $known += [string]$k }
    } catch {
        Write-Log "[$Source] no se pudo leer $NewKeysFile : $($_.Exception.Message)" -Level WARN
    }
    $known = @($known | Where-Object { $_ } | Sort-Object -Unique)
    @{ updated_at = (Get-Date).ToString("o"); count = $known.Count; keys = $known } |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Log "[$Source] uploaded_keys actualizado: $($known.Count) claves"
    Remove-Item -LiteralPath $NewKeysFile -Force -ErrorAction SilentlyContinue
}

# Reconstruye jobs_unified.parquet con merge.py SIN pisar el canonico: respalda
# y restaura jobs_unified.{parquet,csv} y last_run.json en el finally.
function Invoke-MergeReconstruction {
    param($Item)
    $mergedDir = Join-Path $ProjectsRoot "multi_site_job_scraper\data\merged"
    $canonicalParquet = Join-Path $mergedDir "jobs_unified.parquet"
    $canonicalCsv = Join-Path $mergedDir "jobs_unified.csv"
    $lastRun = Join-Path $mergedDir "last_run.json"
    $backupDir = Join-Path $env:TEMP ("recovery-merge-backup-" + (Get-Date).Ticks)
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $tracked = @($canonicalParquet, $canonicalCsv, $lastRun)
    $backedUp = @{}
    foreach ($path in $tracked) {
        if (Test-Path -LiteralPath $path) {
            Copy-Item -LiteralPath $path -Destination $backupDir -Force
            $backedUp[$path] = $true
        }
    }
    try {
        $mergeArgs = @($MergeScript)
        $mergeSince = [string]$Item.merge_since
        if (-not [string]::IsNullOrWhiteSpace($mergeSince)) { $mergeArgs += @("--since", $mergeSince) }
        $mergeOut = & $PythonExe @mergeArgs 2>&1
        foreach ($line in $mergeOut) { Write-Log "[multi_site] merge: $line" }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "[multi_site] merge.py fallo (exit $LASTEXITCODE)" -Level ERROR
            return $null
        }
        if (-not (Test-Path -LiteralPath $canonicalParquet)) {
            Write-Log "[multi_site] merge.py no genero jobs_unified.parquet" -Level ERROR
            return $null
        }
        $reconstructed = Join-Path $env:TEMP ("recovery-merged-$($Item.day)-" + (Get-Date).Ticks + ".parquet")
        Copy-Item -LiteralPath $canonicalParquet -Destination $reconstructed -Force
        return $reconstructed
    } finally {
        foreach ($path in $tracked) {
            if ($backedUp.ContainsKey($path)) {
                Copy-Item -LiteralPath (Join-Path $backupDir (Split-Path -Leaf $path)) `
                    -Destination $path -Force -ErrorAction SilentlyContinue
            } elseif (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
        Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Procesa un item recuperable: staging, filtro, validacion, subida, manifest.
function Invoke-PlanItem {
    param($Item, [switch]$DryRun)
    $source = [string]$Item.source
    $day = [string]$Item.day
    $result = @{ Status = "failed"; Uploaded = 0; Rejected = 0; Message = "" }
    $stageDir = $null
    $manifestFile = $null
    $newKeysFile = $null
    $reconstructed = $null
    $manifestFailed = $false

    try {
        # Dry-run de merge: nunca ejecuta merge.py ni toca el canonico.
        if ([string]$Item.mode -eq "merge" -and $DryRun) {
            $inputCount = 0
            if ($null -ne $Item.inputs) { $inputCount = @($Item.inputs).Count }
            $result.Status = "dry_run"
            $result.Message = "dry run (merge): reconstruiria y subiria dia=$day desde $inputCount salida(s) por portal"
            return $result
        }
        if ([string]$Item.mode -eq "merge") {
            $reconstructed = Invoke-MergeReconstruction -Item $Item
            $localPath = $reconstructed
        } else {
            $localPath = [string]$Item.path
        }
        if ([string]::IsNullOrWhiteSpace($localPath) -or -not (Test-Path -LiteralPath $localPath)) {
            $result.Message = "no existe el fichero a recuperar: $localPath"
            return $result
        }
        if ($DryRun) {
            $result.Status = "dry_run"
            $result.Message = "dry run ($($Item.mode)): $localPath -> dia=$day"
            return $result
        }

        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $stageDir = Join-Path $env:TEMP ("recovery-$source-$day-" + (Get-Date).Ticks)
        New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
        Copy-Item -LiteralPath $localPath -Destination $stageDir -Force
        $staged = @(Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet")
        if ($staged.Count -ne 1) {
            $result.Message = "se esperaba 1 parquet en el staging, hay $($staged.Count)"
            return $result
        }

        # Filtro OnlyNewOffers sobre snapshots acumulativos.
        if ([bool]$Item.only_new) {
            $filterScript = Join-Path $ScriptDir "filter_new_offers.py"
            if (-not (Test-Path -LiteralPath $filterScript)) {
                $result.Message = "falta filter_new_offers.py"
                return $result
            }
            $stateFile = Join-Path $script:UploadedKeysDir "$source.json"
            $newKeysFile = Join-Path $env:TEMP ("recovery_newkeys_$source-" + (Get-Date).Ticks + ".json")
            $filteredOut = Join-Path $stageDir ($staged[0].BaseName + "_new_" + $stamp + ".parquet")
            $filterOut = & $PythonExe $filterScript $staged[0].FullName ([string]$Item.key_column) `
                $stateFile $filteredOut --new-keys-file $newKeysFile 2>&1
            foreach ($line in $filterOut) { Write-Log "[$source] filter: $line" }
            if ($LASTEXITCODE -ne 0) {
                $result.Message = "filter_new_offers fallo (exit $LASTEXITCODE)"
                return $result
            }
            Remove-Item -LiteralPath $staged[0].FullName -Force -ErrorAction SilentlyContinue
            $newTotal = 0
            if (Test-Path -LiteralPath $newKeysFile) {
                try {
                    $nk = Get-Content -LiteralPath $newKeysFile -Raw | ConvertFrom-Json
                    $newTotal = [int]$nk.total
                } catch { $newTotal = 0 }
            }
            if ($newTotal -eq 0) {
                Remove-Item -LiteralPath $filteredOut -Force -ErrorAction SilentlyContinue
                $result.Status = "omitted"
                $result.Message = "sin ofertas nuevas (ya publicadas)"
                return $result
            }
        }

        # Validacion completa (rechazados a cuarentena; nunca se suben).
        $valid = @(Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet")
        if ($valid.Count -eq 0) {
            $result.Message = "el staging no contiene parquet valido"
            return $result
        }
        $compatScript = Join-Path $ScriptDir "ensure_compatible.py"
        $manifestFile = Join-Path $env:TEMP ("recovery_manifest_$source-" + (Get-Date).Ticks + ".json")
        $quarantineDir = Join-Path $QuarantineRoot $source
        $pyArgs = @($compatScript, $stageDir, "--manifest", $manifestFile, "--quarantine-dir", $quarantineDir)
        $requiredCols = @()
        if ($null -ne $Item.required_cols) { $requiredCols = @($Item.required_cols) }
        if ($requiredCols.Count -gt 0) { $pyArgs += @("--required-cols", ($requiredCols -join ",")) }
        if (-not [string]::IsNullOrWhiteSpace([string]$Item.coherence)) {
            $pyArgs += @("--coherence", [string]$Item.coherence)
        }
        if (-not [string]::IsNullOrWhiteSpace([string]$Item.fingerprint_source)) {
            $pyArgs += @("--fingerprint-source", [string]$Item.fingerprint_source)
        }
        $compatOut = & $PythonExe @pyArgs 2>&1
        foreach ($line in $compatOut) { Write-Log "[$source] compat: $line" }
        $validatorExit = $LASTEXITCODE

        $rejected = 0
        if (Test-Path -LiteralPath $manifestFile) {
            try {
                $m = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
                $rejected = @($m.files | Where-Object { $_.status -eq "bad" }).Count
            } catch {}
        }
        $result.Rejected = $rejected
        $valid = @(Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet")
        if ($valid.Count -eq 0) {
            if ($rejected -gt 0) {
                $result.Status = "rejected"
                $result.Message = "validacion rechazo $rejected archivo(s); nada valido que subir"
            } else {
                $result.Message = "validacion fallo (exit $validatorExit) sin archivos validos"
            }
            return $result
        }

        # Subida de solo los validos (--as-subdir=false: claves planas).
        $dest = "https://$StorageAccount.blob.core.windows.net/$Container/$source/dia=$day/$SasToken"
        $azOut = & $AzCopyPath copy $stageDir $dest --overwrite=true --recursive --as-subdir=false --log-level=ERROR 2>&1
        foreach ($line in $azOut) { Write-Log "[$source] azcopy: $line" }
        if ($LASTEXITCODE -ne 0) {
            $result.Message = "azcopy fallo (exit $LASTEXITCODE)"
            return $result
        }
        $result.Uploaded = $valid.Count

        # uploaded_keys SOLO tras exito de azcopy.
        if ([bool]$Item.only_new -and $newKeysFile -and (Test-Path -LiteralPath $newKeysFile)) {
            Update-UploadedKeys -Source $source -NewKeysFile $newKeysFile
        }

        # Manifest con `remote`, sin BOM, subido tras los datos.
        if (Test-Path -LiteralPath $manifestFile) {
            try {
                $m = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
                foreach ($fe in @($m.files)) {
                    if ($null -ne $fe) {
                        $fe | Add-Member -NotePropertyName "remote" `
                            -NotePropertyValue "dia=$day/$($fe.file)" -Force
                    }
                }
                [System.IO.File]::WriteAllText($manifestFile, ($m | ConvertTo-Json -Depth 6),
                    [System.Text.UTF8Encoding]::new($false))
            } catch {
                Write-Log "[$source] no se pudo anotar 'remote' en el manifest: $($_.Exception.Message)" -Level WARN
            }
            $manifestDest = "https://$StorageAccount.blob.core.windows.net/$Container/_manifests/$source/$stamp.json$SasToken"
            $mOut = & $AzCopyPath copy $manifestFile $manifestDest --overwrite=true --log-level=ERROR 2>&1
            foreach ($line in $mOut) { Write-Log "[$source] manifest: $line" }
            if ($LASTEXITCODE -ne 0) {
                # Sin manifest la publicacion no queda auditada: cuenta como
                # fallo del item y el run no puede darse por recuperado.
                $manifestFailed = $true
                Write-Log "[$source] fallo subiendo el manifest (exit $LASTEXITCODE)" -Level ERROR
            }
        }

        $problems = @()
        if ($rejected -gt 0) { $problems += "$rejected rechazado(s)" }
        if ($manifestFailed) { $problems += "manifest no subido" }
        if ($validatorExit -ne 0 -and $problems.Count -eq 0) { $problems += "validacion con avisos" }
        if ($problems.Count -gt 0) {
            $result.Status = "partial"
            $result.Message = "$($valid.Count) subido(s), $($problems -join ', ')"
        } else {
            $result.Status = "published"
            $result.Message = "$($valid.Count) subido(s)"
        }
        return $result
    } catch {
        $result.Message = $_.Exception.Message
        return $result
    } finally {
        if ($stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue }
        if ($manifestFile) { Remove-Item -LiteralPath $manifestFile -Force -ErrorAction SilentlyContinue }
        if ($newKeysFile) { Remove-Item -LiteralPath $newKeysFile -Force -ErrorAction SilentlyContinue }
        if ($reconstructed) { Remove-Item -LiteralPath $reconstructed -Force -ErrorAction SilentlyContinue }
    }
}

# Recorre el plan, registra por fuente y devuelve el codigo de salida.
function Invoke-RecoveryPlanMode {
    param([string]$RunDate, [string]$PlanFile, [switch]$DryRun)
    $label = if (-not [string]::IsNullOrWhiteSpace($RunDate)) { $RunDate } else { "plan" }
    Write-Log "====  Inicio recuperacion planificada  ($label) ===="
    try {
        $plan = Get-RecoveryPlan -RunDate $RunDate -PlanFile $PlanFile
    } catch {
        Write-Log "No se pudo obtener el plan: $($_.Exception.Message)" -Level ERROR
        return 1
    }
    $items = @()
    if ($null -ne $plan -and $null -ne $plan.items) { $items = @($plan.items) }
    if ($items.Count -eq 0) {
        Write-Log "El plan no contiene items." -Level WARN
        return 0
    }
    $failures = 0
    $summary = @()
    foreach ($item in $items) {
        $source = [string]$item.source
        if (-not [bool]$item.recoverable) {
            Write-Log "[$source] omitido: $($item.reason)"
            $summary += "$source=omitido"
            continue
        }
        $result = Invoke-PlanItem -Item $item -DryRun:$DryRun
        switch ([string]$result.Status) {
            "published" { Write-Log "[$source] publicado: $($result.Message)"; $summary += "$source=publicado" }
            "omitted"   { Write-Log "[$source] omitido: $($result.Message)"; $summary += "$source=omitido" }
            "dry_run"   { Write-Log "[$source] dry run: $($result.Message)"; $summary += "$source=dry_run" }
            default     { Write-Log "[$source] fallo: $($result.Message)" -Level ERROR; $failures++; $summary += "$source=fallo" }
        }
    }
    Write-Log "Resumen recuperacion: $($summary -join ', ')"
    if ($failures -gt 0) {
        Write-Log "Recuperacion con $failures fallo(s)." -Level ERROR
        return 1
    }
    return 0
}

if (-not [string]::IsNullOrWhiteSpace($Date) -or -not [string]::IsNullOrWhiteSpace($PlanJson)) {
    exit (Invoke-RecoveryPlanMode -RunDate $Date -PlanFile $PlanJson -DryRun:$DryRun)
}

# =============================================================================
#  MODO HISTORICO (sin -Date ni -PlanJson; comportamiento original intacto)
# =============================================================================
Write-Log "====  Inicio recuperacion de datos historicos  ===="

# =============================================================================
#  DEFINICION DE FUENTES DE DATOS POR DIA
# =============================================================================
#  Cada entrada: Name, Path (ruta a archivo parquet), Date (YYYY-MM-DD)
#
#  Estrategia: para cada scraper, agrupamos los parquets por fecha
#  (usando LastWriteTime o fecha en nombre de archivo) y subimos EL MAS
#  RECIENTE de cada dia (que contiene todos los datos acumulados hasta ese
#  momento).
# =============================================================================

$ProjectsRoot = $ProjectsRoot

# --- LinkedIn: archivo unico acumulativo ---
$linkedinParquet = "$ProjectsRoot\linkedin_jobs_scraper\data\output\jobs.parquet"

# --- Indeed: archivos timestamped ---
$indeedDir = "$ProjectsRoot\indeed_jobs_scraper\output"

# --- InfoJobs: archivos timestamped (offers_YYYYMMDD_HHMMSS.parquet) ---
$infojobsDir = "$ProjectsRoot\infojobs_jobs_scraper\data"

# --- Multi-site: merge primero, luego subir ---
$multiOutDir = "$ProjectsRoot\multi_site_job_scraper\data\merged"

# =============================================================================
#  FUNCION: Validar y subir un archivo a ADLS
# =============================================================================
#  Antes de subir se valida con ensure_compatible.py (mismo criterio que el
#  pipeline diario): lectura real, columnas obligatorias, minimo de filas,
#  timestamps [ns]->[us]. Si el fichero es invalido NO se sube y se mueve a
#  cuarentena local. Se sube tambien el manifest a _manifests/<scraper>/.
function Invoke-UploadFile {
    param([string]$LocalPath, [string]$ScraperName, [string]$Day)
    if (-not (Test-Path -LiteralPath $LocalPath)) {
        Write-Log "[$ScraperName] Archivo no encontrado: $LocalPath" -Level ERROR
        return 1
    }
    $savedNewKeys = ""
    $sizeMB = [math]::Round((Get-Item -LiteralPath $LocalPath).Length / 1MB, 2)
    $dest = "https://$StorageAccount.blob.core.windows.net/$Container/$ScraperName/dia=$Day/$SasToken"
    Write-Log "[$ScraperName] Subiendo $((Split-Path -Leaf $LocalPath)) (${sizeMB}MB) -> dia=$Day"

    if ($DryRun) {
        Write-Log "[$ScraperName] DRY RUN: no se sube realmente. Destino: $Container/$ScraperName/dia=$Day/"
        return 0
    }

    # --- Validacion previa (solo en modo real) ---
    $compatScript = Join-Path $ScriptDir "ensure_compatible.py"
    $uploadStage = $false
    $savedManifest = ""
    $manifestStamp = ""
    if (-not (Test-Path -LiteralPath $compatScript)) {
        Write-Log "[$ScraperName] AVISO: ensure_compatible.py no encontrado; no se valida." -Level WARN
    } else {
        $stageDir = Join-Path $env:TEMP ("recover-$ScraperName-" + (Get-Date).Ticks)
        if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
        New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
        Copy-Item -LiteralPath $LocalPath -Destination $stageDir -Force

        # --- Solo ofertas NUEVAS (LinkedIn / multi_site) ---------------------
        #  Los snapshots acumulativos se filtran contra uploaded_keys/: solo
        #  se suben las claves que aun no se habian subido. Si no hay nuevas,
        #  se omite el fichero (no se sube el snapshot completo).
        $cfgOnly = $Scrapers | Where-Object { $_.Name -eq $ScraperName } | Select-Object -First 1
        if ($cfgOnly -and $cfgOnly.ContainsKey("OnlyNewOffers") -and $cfgOnly.OnlyNewOffers) {
            $filterScript = Join-Path $ScriptDir "filter_new_offers.py"
            if (-not (Test-Path -LiteralPath $filterScript)) {
                Write-Log "[$ScraperName] ERROR: falta $filterScript; no se sube." -Level ERROR
                Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
                return 1
            }
            $stateDir = Join-Path $ScriptDir "uploaded_keys"
            New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
            $stateFile = Join-Path $stateDir "$ScraperName.json"
            $keyCol = if ($cfgOnly.ContainsKey("KeyColumn")) { $cfgOnly.KeyColumn } else { "job_id" }
            $stagedFile = Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet" | Select-Object -First 1
            if ($stagedFile) {
                $filteredOut = Join-Path $stageDir ($stagedFile.BaseName + "_new.parquet")
                $newKeysFile = Join-Path $env:TEMP ("recover_newkeys_${ScraperName}_" + (Get-Date).Ticks + ".json")
                $pyOut = & python @($filterScript, $stagedFile.FullName, $keyCol, $stateFile,
                                    $filteredOut, "--new-keys-file", $newKeysFile) 2>&1
                foreach ($line in $pyOut) { Write-Log "[$ScraperName] filter: $line" }
                $filterExit = $LASTEXITCODE
                Remove-Item -LiteralPath $stagedFile.FullName -Force -ErrorAction SilentlyContinue
                if ($filterExit -eq 0 -and (Test-Path -LiteralPath $newKeysFile)) {
                    $nk = Get-Content -LiteralPath $newKeysFile -Raw | ConvertFrom-Json
                    if ($nk.total -gt 0) {
                        $savedNewKeys = $newKeysFile
                    } else {
                        Write-Log "[$ScraperName] Sin ofertas nuevas en $($stagedFile.Name); se omite." -Level WARN
                        Remove-Item -LiteralPath $filteredOut -Force -ErrorAction SilentlyContinue
                        Remove-Item -LiteralPath $newKeysFile -Force -ErrorAction SilentlyContinue
                        Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
                        return 0
                    }
                } else {
                    Remove-Item -LiteralPath $newKeysFile -Force -ErrorAction SilentlyContinue
                    Write-Log "[$ScraperName] ERROR filtrando $($stagedFile.Name)" -Level ERROR
                    Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
                    return 1
                }
            }
        }

        $stamp        = Get-Date -Format "yyyyMMdd_HHmmss"
        $manifestFile = Join-Path $env:TEMP "recover_manifest_${ScraperName}_${stamp}.json"
        $quarantineDir = Join-Path $QuarantineRoot "$ScraperName"
        $scraperCfg   = $Scrapers | Where-Object { $_.Name -eq $ScraperName } | Select-Object -First 1
        $requiredCols = ""
        if ($scraperCfg -and $scraperCfg.ContainsKey("RequiredColumns") -and $scraperCfg.RequiredColumns.Count -gt 0) {
            $requiredCols = ($scraperCfg.RequiredColumns -join ",")
        }
        $pyArgs = @($compatScript, $stageDir, "--manifest", $manifestFile,
                    "--quarantine-dir", $quarantineDir)
        if ($requiredCols) { $pyArgs += @("--required-cols", $requiredCols) }
        $pyOut = & python @pyArgs 2>&1
        foreach ($line in $pyOut) { Write-Log "[$ScraperName] compat: $line" }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "[$ScraperName] Validacion fallo para $((Split-Path -Leaf $LocalPath)). No se sube." -Level ERROR
            Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $manifestFile -Force -ErrorAction SilentlyContinue
            return 1
        }
        # Subir desde staging (contiene solo el fichero ya validado)
        $LocalPath = $stageDir
        $uploadStage = $true
        $savedManifest = $manifestFile
        $manifestStamp = $stamp
    }

    & $AzCopyPath copy $("`"$LocalPath`"") $dest --overwrite=true --log-level=ERROR 2>&1 |
        ForEach-Object { Write-Log "[$ScraperName] azcopy: $_" }
    $azExit = $LASTEXITCODE

    if ($azExit -ne 0) {
        Write-Log "[$ScraperName] azcopy fallo (exit $azExit)" -Level ERROR
        if ($uploadStage) {
            Remove-Item -LiteralPath $LocalPath -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $savedManifest -Force -ErrorAction SilentlyContinue
        }
        return 1
    }
    Write-Log "[$ScraperName] Subida completada OK"

    # Solo ofertas nuevas: registrar las claves subidas SOLO tras exito azcopy
    if ($savedNewKeys -and (Test-Path -LiteralPath $savedNewKeys)) {
        $stateDir = Join-Path $ScriptDir "uploaded_keys"
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $stateFile = Join-Path $stateDir "$ScraperName.json"
        $known = @()
        if (Test-Path -LiteralPath $stateFile) {
            try {
                $known = @((Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json).keys)
            } catch {
                $known = @()
            }
        }
        try {
            $nk = Get-Content -LiteralPath $savedNewKeys -Raw | ConvertFrom-Json
            foreach ($k in @($nk.keys)) { $known += [string]$k }
        } catch {
            Write-Log "[$ScraperName] No se pudo leer $savedNewKeys" -Level WARN
        }
        $known = @($known | Where-Object { $_ } | Sort-Object -Unique)
        @{ updated_at = (Get-Date).ToString("o"); count = $known.Count; keys = $known } |
            ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $stateFile -Encoding UTF8
        Write-Log "[$ScraperName] Estado de subidas actualizado: $($known.Count) claves registradas"
        Remove-Item -LiteralPath $savedNewKeys -Force -ErrorAction SilentlyContinue
    }

    # Subir el manifest de auditoria
    if ($uploadStage -and (Test-Path -LiteralPath $savedManifest)) {
        # Anotar la ruta REMOTA de cada fichero (dia=YYYY-MM-DD/<nombre>)
        # para que Databricks pueda cruzar manifiesto vs archivos ingeridos.
        try {
            $m = Get-Content -LiteralPath $savedManifest -Raw | ConvertFrom-Json
            foreach ($fe in @($m.files)) {
                if ($null -ne $fe) {
                    $fe | Add-Member -NotePropertyName "remote" `
                        -NotePropertyValue "dia=$Day/$($fe.file)" -Force
                }
            }
            # Write JSON without BOM: PS 5.1 Set-Content -Encoding UTF8 adds one.
            $json = $m | ConvertTo-Json -Depth 6
            [System.IO.File]::WriteAllText($savedManifest, $json, [System.Text.UTF8Encoding]::new($false))
        } catch {
            Write-Log "[$ScraperName] No se pudo anotar 'remote' en el manifest: $($_.Exception.Message)" -Level WARN
        }
        $manifestDest = "https://$StorageAccount.blob.core.windows.net/$Container/_manifests/$ScraperName/$manifestStamp.json$SasToken"
        & $AzCopyPath copy $("`"$savedManifest`"") $manifestDest --overwrite=true --log-level=ERROR 2>&1 |
            ForEach-Object { Write-Log "[$ScraperName] manifest: $_" }
        if ($LASTEXITCODE -ne 0) {
            Write-Log "[$ScraperName] Fallo subir manifest (exit $LASTEXITCODE)" -Level WARN
        } else {
            Write-Log "[$ScraperName] Manifest subido: _manifests/$ScraperName/"
        }
        Remove-Item -LiteralPath $LocalPath -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $savedManifest -Force -ErrorAction SilentlyContinue
    }
    return 0
}

# =============================================================================
#  1. LINKEDIN
# =============================================================================
Write-Log "--- Procesando LinkedIn ---"
if (Test-Path -LiteralPath $linkedinParquet) {
    $day = (Get-Item -LiteralPath $linkedinParquet).LastWriteTime.ToString("yyyy-MM-dd")
    # Subir el parquet actual y tambien los de dias anteriores si hay versiones
    Invoke-UploadFile -LocalPath $linkedinParquet -ScraperName "linkedin" -Day $day

    # LinkedIn tambien guarda copias historicas con timestamp en el nombre
    $linkedinDir = Split-Path $linkedinParquet
    $historicFiles = Get-ChildItem -LiteralPath $linkedinDir -Filter "jobs_*.parquet" -File |
        Where-Object { $_.Name -match 'jobs_(\d{8})_' } |
        Sort-Object LastWriteTime -Descending
    foreach ($f in $historicFiles) {
        if ($f.Name -match 'jobs_(\d{8})_') {
            $fileDay = $matches[1] -replace '(\d{4})(\d{2})(\d{2})', '$1-$2-$3'
            Invoke-UploadFile -LocalPath $f.FullName -ScraperName "linkedin" -Day $fileDay
        }
    }
} else {
    Write-Log "[linkedin] No se encontro jobs.parquet" -Level WARN
}

# =============================================================================
#  2. INDEED
# =============================================================================
Write-Log "--- Procesando Indeed ---"
# Agrupar parquets por fecha (del nombre: indeed_jobs_YYYYMMDD_*.parquet)
$indeedFiles = Get-ChildItem -LiteralPath $indeedDir -Filter "indeed_jobs_*.parquet" -File
$indeedByDay = @{}
foreach ($f in $indeedFiles) {
    if ($f.Name -match 'indeed_jobs_(\d{8})_') {
        $fileDay = $matches[1] -replace '(\d{4})(\d{2})(\d{2})', '$1-$2-$3'
        if (-not $indeedByDay.ContainsKey($fileDay)) {
            $indeedByDay[$fileDay] = @()
        }
        $indeedByDay[$fileDay] += $f
    }
}

foreach ($day in $indeedByDay.Keys | Sort-Object) {
    $latest = $indeedByDay[$day] | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Invoke-UploadFile -LocalPath $latest.FullName -ScraperName "indeed" -Day $day
}

# =============================================================================
#  3. INFOJOBS
# =============================================================================
Write-Log "--- Procesando InfoJobs ---"
# Agrupar por fecha del nombre: offers_YYYYMMDD_*.parquet
$ijFiles = Get-ChildItem -LiteralPath $infojobsDir -Filter "offers_*.parquet" -File
$ijByDay = @{}
foreach ($f in $ijFiles) {
    if ($f.Name -match 'offers_(\d{8})_') {
        $fileDay = $matches[1] -replace '(\d{4})(\d{2})(\d{2})', '$1-$2-$3'
        if (-not $ijByDay.ContainsKey($fileDay)) {
            $ijByDay[$fileDay] = @()
        }
        $ijByDay[$fileDay] += $f
    }
}

foreach ($day in $ijByDay.Keys | Sort-Object) {
    # El ultimo archivo del dia tiene la mayor cantidad de ofertas acumuladas
    $latest = $ijByDay[$day] | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Invoke-UploadFile -LocalPath $latest.FullName -ScraperName "infojobs" -Day $day
}

# =============================================================================
#  4. MULTI-SITE
# =============================================================================
Write-Log "--- Procesando Multi-site ---"

# Primero ejecutamos merge.py para generar el parquet unificado desde
# los outputs individuales (algunos sub-scrapers pueden haber terminado OK)
$mergeScript = "$ProjectsRoot\multi_site_job_scraper\merge.py"
$mergedOutput = "$multiOutDir\jobs_unified.parquet"

if (Test-Path -LiteralPath $mergeScript) {
    Write-Log "Ejecutando merge.py para reconstruir jobs_unified.parquet..."

    if (-not $DryRun) {
        $mergeResult = python $mergeScript 2>&1
        $mergeExit = $LASTEXITCODE
        foreach ($line in $mergeResult) {
            Write-Log "[multi_site] merge.py: $line"
        }
        if ($mergeExit -ne 0) {
            Write-Log "[multi_site] merge.py fallo (exit $mergeExit)" -Level WARN
        }
    }
} else {
    Write-Log "[multi_site] merge.py no encontrado en $mergeScript" -Level WARN
}

# Subir el parquet unificado de cada dia
if (Test-Path -LiteralPath $mergedOutput) {
    $day = (Get-Item -LiteralPath $mergedOutput).LastWriteTime.ToString("yyyy-MM-dd")
    Invoke-UploadFile -LocalPath $mergedOutput -ScraperName "multi_site" -Day $day
}

# Tambien subir versiones historicas con timestamp
$historicMerged = Get-ChildItem -LiteralPath $multiOutDir -Filter "jobs_unified_*.parquet" -File |
    Sort-Object LastWriteTime -Descending
foreach ($f in $historicMerged) {
    if ($f.Name -match 'jobs_unified_(\d{8})_') {
        $fileDay = $matches[1] -replace '(\d{4})(\d{2})(\d{2})', '$1-$2-$3'
        Invoke-UploadFile -LocalPath $f.FullName -ScraperName "multi_site" -Day $fileDay
    }
}

# =============================================================================
#  FINAL
# =============================================================================
Write-Log "====  Fin recuperacion de datos historicos  ===="

# Escribir trigger file si estamos en modo real (no dry run)
if (-not $DryRun) {
    $readyDest = "https://$StorageAccount.blob.core.windows.net/$Container/_READY/dia=$Today.txt$SasToken"
    Write-Log "Escribiendo trigger file _READY/dia=$Today.txt para Databricks..."
    $tmpReady = Join-Path $env:TEMP "ready_$Today.txt"
    "RECOVERED $Today $(Get-Date -Format o)" | Out-File -LiteralPath $tmpReady -Encoding utf8
    & $AzCopyPath copy $("`"$tmpReady`"") $readyDest --overwrite=true --log-level=ERROR 2>&1 |
        ForEach-Object { Write-Log "azcopy: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Fallo la subida del trigger file (exit $LASTEXITCODE)" -Level ERROR
    } else {
        Write-Log "Trigger file escrito. Databricks puede arrancar con los datos recuperados."
    }
    Remove-Item -LiteralPath $tmpReady -Force -ErrorAction SilentlyContinue
}

Write-Log "Listo. Revisa el log: $LogFile"
