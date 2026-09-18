# =============================================================================
#  recover_and_upload.ps1
#  Sube datos de ejecuciones pasadas que nunca llegaron a ADLS porque los
#  scrapers fallaron antes de que el pipeline los subiera.
#
#  Para cada scraper, busca el parquet mas reciente de cada dia y lo sube a
#  landing/<scraper>/dia=YYYY-MM-DD/ usando azcopy.
#
#  Uso:
#    .\recover_and_upload.ps1
#    .\recover_and_upload.ps1 -DryRun    (solo muestra lo que subiria)
# =============================================================================

param([switch]$DryRun)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Today     = Get-Date -Format "yyyy-MM-dd"
$LogDir    = Join-Path $ScriptDir "logs"
$LogFile   = Join-Path $LogDir "recover-$Today.log"

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

if ([string]::IsNullOrWhiteSpace($SasToken)) {
    Write-Log "LANDING_SAS_TOKEN no definida. Ejecuta generate_sas.ps1." -Level ERROR
    exit 2
}

$azc = Get-Command $AzCopyPath -ErrorAction SilentlyContinue
if (-not $azc) {
    Write-Log "AzCopy no encontrado en PATH ($AzCopyPath)." -Level ERROR
    exit 2
}

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
            $m | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $savedManifest -Encoding UTF8
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
