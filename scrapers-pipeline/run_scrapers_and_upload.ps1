# =============================================================================
#  run_scrapers_and_upload.ps1
#  Pipeline diario:
#    1) Ejecuta cada scraper
#    2) VALIDA los parquet generados (legibles, columnas obligatorias, no
#       vacios, timestamps compatibles con Spark) y rechaza los invalidos
#    3) Sube SOLO los validos a ADLS Gen2 (landing/scraper_X/dia=YYYY-MM-DD/)
#    4) Sube un manifest de auditoria a landing/_manifests/<scraper>/
#    5) Escribe el trigger file _READY segun la politica configurada
#
#  Uso (Task Scheduler, diariamente a las 00:00):
#    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\run_scrapers_and_upload.ps1"
#
#  -Supervised (T-10; RF-4): solo lo pasa run_pipeline_supervised.ps1. Antes
#  de lanzar los scrapers reconcilia, con recover_and_upload.ps1 -Date, los
#  runs truncados/pendientes anteriores a hoy que ya no tengan wrappers vivos;
#  un fallo ahi nunca aborta el run que empieza. La tarea programada actual NO
#  pasa -Supervised, de modo que la recuperacion automatica no cambia el flujo
#  normal hasta T-14.
# =============================================================================

param(
    [switch]$Supervised
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

# --- Preparar log (ANTES de cargar config, para diagnosticar fallos de arranque)
#  Si config.ps1 define $LogDir distinto, se respeta mas abajo reabriendo el log.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Today     = Get-Date -Format "yyyy-MM-dd"
$LogDir    = Join-Path $ScriptDir "logs"
$LogFile   = Join-Path $LogDir "upload-$Today.log"
$StartTime = Get-Date
if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log {
    param([string]$Message, [ValidateSet("INFO","WARN","ERROR")] [string]$Level = "INFO")
    $line = "{0}  [{1}]  {2}" -f (Get-Date -Format "HH:mm:ss"), $Level, $Message
    Add-Content -LiteralPath $LogFile -Value $line
    if ($Level -eq "ERROR") { Write-Host $line -ForegroundColor Red }
    elseif ($Level -eq "WARN") { Write-Host $line -ForegroundColor Yellow }
    else { Write-Host $line }
}

Write-Log "====  Inicio pipeline scrapers  ($Today) ===="

# --- Cargar configuracion ----------------------------------------------------
#  OJO: $LogDir definido aqui arriba es solo fallback; config.ps1 puede
#  sobrescribirlo. En ese caso, reubicamos el archivo de log.
. (Join-Path $ScriptDir "config.ps1")
if ($LogFile -notlike "$LogDir*") {
    $LogFile = Join-Path $LogDir "upload-$Today.log"
    if (-not (Test-Path -LiteralPath $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
}

# --- Validar SAS token (aqui, no en config.ps1, para no romper generate_sas.ps1)
if ([string]::IsNullOrWhiteSpace($SasToken)) {
    Write-Log "LANDING_SAS_TOKEN no definida o vacia. Ejecuta generate_sas.ps1." -Level ERROR
    exit 2
}

# --- Validar azcopy ----------------------------------------------------------
$azc = Get-Command $AzCopyPath -ErrorAction SilentlyContinue
if (-not $azc) {
    Write-Log "AzCopy no encontrado en PATH ($AzCopyPath). Instala desde https://aka.ms/azcopy" -Level ERROR
    exit 2
}

# --- Estado global del run ----------------------------------------------------
#  $script:RunStart: instante en que arranca el pipeline. Se usa para filtrar
#  los archivos de cada scraper: solo se suben los modificados DESDE este
#  instante, nunca archivos de un run manual previo del mismo dia.
$script:RunStart = $StartTime

#  $script:PendingNewKeys: claves nuevas pendientes de marcar como subidas
#  (scrapers con OnlyNewOffers). Se confirman en el estado SOLO tras el
#  exito de azcopy; si la subida falla, el proximo run las reintenta.
$script:PendingNewKeys = @{}

#  $script:QuarantineRoot: carpeta local donde se mueven los parquet invalidos.
if ([string]::IsNullOrWhiteSpace($QuarantineRoot)) {
    $script:QuarantineRoot = Join-Path $ScriptDir "quarantine"
} else {
    $script:QuarantineRoot = $QuarantineRoot
}

#  $script:GlobalFailures: contador de fallos (scraper fallido, subida fallida,
#  ficheros rechazados en validacion, proceso matado por timeout).
$script:GlobalFailures = 0

#  $script:PublishResults: resultado de publicacion por scraper. Cada entrada:
#    Status   = ok | partial | failed | no_data | killed
#    Uploaded = numero de ficheros subidos
#    Rejected = numero de ficheros rechazados por validacion
#    ExitCode = exit code del wrapper (si termino)
$script:PublishResults = @{}

# --- Estado persistente del run (RF-1) ---------------------------------------
#  Hito 1 (inicio): se crea DESPUES de validar config/SAS/AzCopy (un arranque
#  que sale con exit 2 antes de lanzar nada no debe dejar un estado "pending"
#  que la recuperacion interprete como run truncado con trabajo pendiente; el
#  log general ya registra ese aborto para el diagnostico). El started_at es el
#  instante real de arranque ($StartTime), no el momento de escribir el fichero.
#  Fichero: logs/run_state/<run_date>.json. Un fallo al guardar el estado NUNCA
#  aborta el pipeline (RF-9): se registra WARN y se continua.
. (Join-Path $ScriptDir "run_state.ps1")
$script:RunStateDir = Join-Path $LogDir "run_state"
$script:RunState = $null

#  Persiste en disco el estado en memoria. Nunca aborta el pipeline.
function Save-RunState {
    if ($null -eq $script:RunState) { return }
    try {
        Write-RunState -State $script:RunState -StateDir $script:RunStateDir | Out-Null
    } catch {
        Write-Log "No se pudo guardar el estado del run: $($_.Exception.Message)" -Level WARN
    }
}

#  Hito 2 (por fuente): registra el resultado de una fuente y persiste. Se
#  invoca en TODOS los caminos que asignan $script:PublishResults para que
#  ninguna fuente quede sin estado. $Result puede no traer Uploaded/Rejected
#  (p. ej. wrapper inexistente o timeout global).
function Update-RunSourceState {
    param([string]$Name, [hashtable]$Result)
    if ($null -eq $script:RunState) { return }
    try {
        $uploaded = 0
        $rejected = 0
        if ($Result.ContainsKey("Uploaded")) { $uploaded = [int]$Result.Uploaded }
        if ($Result.ContainsKey("Rejected")) { $rejected = [int]$Result.Rejected }
        Set-RunSourceState -State $script:RunState -Source $Name -Status ([string]$Result.Status) `
            -Uploaded $uploaded -Rejected $rejected
    } catch {
        Write-Log "[$Name] No se pudo actualizar su estado en el run: $($_.Exception.Message)" -Level WARN
        return
    }
    Save-RunState
}

try {
    $script:RunState = New-RunState -RunDate $Today -StartedAt $StartTime.ToString("o")
} catch {
    $script:RunState = $null
    Write-Log "No se pudo crear el estado del run: $($_.Exception.Message)" -Level WARN
}
if ($null -ne $script:RunState) {
    Save-RunState
    if (Test-Path -LiteralPath (Join-Path $script:RunStateDir "$Today.json")) {
        Write-Log "Estado del run persistido en $($script:RunStateDir)\$Today.json"
    }
}

# --- Reconciliacion de runs anteriores (RF-4; solo con -Supervised) ----------
#  Descubre (CLI local de verification.recovery) los runs truncados/pendientes
#  ANTERIORES a hoy y recupera los que ya no tengan wrappers vivos. Un fallo
#  aqui nunca aborta el run que empieza: se registra y se continua (RF-4).
#  Sin -Supervised no se ejecuta nada de esto (RF-9).
if ($Supervised) {
    . (Join-Path $ScriptDir "reconcile_pending_runs.ps1")
    try {
        $reconciliation = Invoke-PendingReconciliation `
            -ProjectsRoot $ProjectsRoot `
            -LogsDir $LogDir `
            -StateDir $script:RunStateDir `
            -Before $Today `
            -RecoveryScript (Join-Path $ScriptDir "recover_and_upload.ps1") `
            -Logger { param($Message, $Level) Write-Log $Message -Level $Level }
        Write-Log ("Reconciliacion: {0} pendiente(s), {1} recuperado(s), {2} con wrappers vivos, {3} fallo(s)" -f `
            $reconciliation.discovered, $reconciliation.invoked.Count, `
            $reconciliation.skipped_live.Count, $reconciliation.failures.Count)
    } catch {
        Write-Log "La reconciliacion de pendientes fallo (se continua con el run): $($_.Exception.Message)" -Level WARN
    }
}

# --- Captura fiable del exit code de un proceso ------------------------------
#  Bug conocido de PS 5.1: tras Start-Process -PassThru, $proc.ExitCode puede
#  devolver $null o un valor obsoleto (-1) si no se refresca el objeto Process.
#  Esta funcion hace WaitForExit + Refresh y clasifica el resultado.
function Get-ProcessResult {
    param($Proc, [switch]$WasKilled)
    if ($WasKilled) { return @{ ExitCode = $null; Outcome = "killed" } }
    try { [void]$Proc.WaitForExit(0) } catch {}
    try { $Proc.Refresh() } catch {}
    $code = $null
    try { $code = $Proc.ExitCode } catch {}
    if ($null -eq $code) { return @{ ExitCode = -1; Outcome = "unknown" } }
    return @{ ExitCode = $code; Outcome = "exited" }
}

# --- Pre-flight Chrome CDP para Indeed (auto-lanzamiento) --------------------
#  Indeed requiere un Chrome activo con --remote-debugging-port=9222. Si no,
#  el wrapper aborta con exit=3. Aqui lo detectamos y levantamos Chrome
#  automaticamente con el perfil persistente que ya usa Indeed.
function Test-CdpAlive { param([int]$Port = 9222)
    try { Invoke-WebRequest -Uri "http://localhost:$Port/json/version" -UseBasicParsing -TimeoutSec 5 | Out-Null; return $true }
    catch { return $false }
}
function Start-ChromeCdp { param([int]$Port = 9222)
    $chromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"
    if (-not (Test-Path -LiteralPath $chromeExe)) {
        $chromeExe = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    }
    if (-not (Test-Path -LiteralPath $chromeExe)) {
        $c = Get-Command "chrome.exe" -ErrorAction SilentlyContinue
        if ($c) { $chromeExe = $c.Source }
    }
    if (-not (Test-Path -LiteralPath $chromeExe)) { return $false }
    $profile = Join-Path $ProjectsRoot "indeed_jobs_scraper\output\chrome_cdp_profile"
    if (-not (Test-Path -LiteralPath $profile)) { New-Item -ItemType Directory -Force -Path $profile | Out-Null }
    try {
        Start-Process -FilePath $chromeExe -ArgumentList @("--remote-debugging-port=$Port","--user-data-dir=`"$profile`"") -PassThru | Out-Null
    } catch { return $false }
    # Esperar hasta 15s a que CDP responda
    for ($i=0; $i -lt 30; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-CdpAlive -Port $Port) { return $true }
    }
    return $false
}
Write-Log "Pre-flight: comprobar Chrome CDP en puerto 9222..."
if (Test-CdpAlive) {
    Write-Log "Pre-flight OK: Chrome CDP responde."
} else {
    Write-Log "Pre-flight: CDP no responde. Auto-lanzando Chrome..." -Level WARN
    if (Start-ChromeCdp) {
        Write-Log "Pre-flight OK: Chrome CDP levantado automaticamente."
        Write-Log "RECUERDA: logueate en indeed.com en esa ventana si aun no lo hiciste."
    } else {
        Write-Log "Pre-flight: no se pudo levantar Chrome CDP. Indeed fallara con exit=3." -Level WARN
        Write-Log "Lanza Chrome a mano: C:\Program Files\Google\Chrome\Application\chrome.exe --remote-debugging-port=9222" -Level WARN
    }
}

# =============================================================================
#  EJECUCION PARALELA DE SCRAPERS
# =============================================================================
#  Cada scraper corre en un proceso PowerShell independiente (Start-Process),
#  de manera que un scraper lento (LinkedIn puede tardar 10h) NO bloquee al
#  resto (Indeed termina en minutos). El script monitoriza los procesos cada
#  60s, y a medida que cada uno termina OK, sube sus archivos a ADLS. Cuando
#  todos terminan (o se alcanza el timeout global), escribe el trigger file.

# --- Actualizar el estado de claves subidas (OnlyNewOffers) ------------------
#  Se ejecuta SOLO tras el exito de azcopy: las claves del fichero subido se
#  anaden a uploaded_keys/<scraper>.json para que el proximo run no las
#  vuelva a subir. Si la subida falla, el estado NO se toca (reintento).
function Update-UploadedState {
    param([string]$ScraperName)
    $pending = $script:PendingNewKeys[$ScraperName]
    if (-not $pending -or $pending.Count -eq 0) { return }
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
    foreach ($nkf in $pending) {
        try {
            $nk = Get-Content -LiteralPath $nkf -Raw | ConvertFrom-Json
            foreach ($k in @($nk.keys)) { $known += [string]$k }
        } catch {
            Write-Log "[$ScraperName] No se pudo leer ${nkf}: $($_.Exception.Message)" -Level WARN
        }
        Remove-Item -LiteralPath $nkf -Force -ErrorAction SilentlyContinue
    }
    $known = @($known | Where-Object { $_ } | Sort-Object -Unique)
    $state = @{ updated_at = (Get-Date).ToString("o"); count = $known.Count; keys = $known }
    $state | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Log "[$ScraperName] Estado de subidas actualizado: $($known.Count) claves registradas"
    $script:PendingNewKeys[$ScraperName] = @()
}

# --- Funcion: validar y subir los archivos de un scraper ---------------------
#  Flujo:
#   1. Seleccionar SOLO los archivos de este run (copia timestampada o
#      modificados despues de $script:RunStart).
#   2. Copiar a staging temporal y validarlos con ensure_compatible.py:
#      lectura real (detecta parquet corrupto), columnas obligatorias, minimo
#      de filas, timestamps [ns]->[us]. Los invalidos se mueven a cuarentena
#      local y NUNCA se suben.
#   3. azcopy del staging (solo quedan los validos) a
#      landing/<scraper>/dia=YYYY-MM-DD/.
#   4. Subir el manifest JSON a landing/_manifests/<scraper>/<stamp>.json.
#  Devuelve un hashtable @{ Status; Uploaded; Rejected; Message }.
function Invoke-ScraperUpload {
    param($Scraper)
    $name = $Scraper.Name
    $result = @{ Status = "failed"; Uploaded = 0; Rejected = 0; Message = "" }

    if (-not (Test-Path -LiteralPath $Scraper.OutDir)) {
        $result.Message = "OutDir no existe: $($Scraper.OutDir)"
        Write-Log "[$name] $($result.Message)" -Level ERROR
        return $result
    }
    # IMPORTANT: sin -Recurse. Solo los archivos DIRECTOS del OutDir.
    # Motivo: infojobs tiene OutDir=data/ que ademas contiene la subcarpeta
    # delta_table/ con part-00000-*.parquet internos del scraper. Sin -Recurse
    # excluimos automaticamente todo lo que este en subcarpetas (delta_table,
    # profile/, checkpoints/, etc.) y subimos solo offers_*.parquet.
    $files = @(Get-ChildItem -LiteralPath $Scraper.OutDir -File |
             Where-Object { $_.Extension -eq ".$($Scraper.Ext)" })

    # Solo ofertas NUEVAS: LinkedIn y multi_site suben snapshots acumulativos
    # (jobs.parquet / jobs_unified.parquet). Con OnlyNewOffers=$true se filtra
    # contra uploaded_keys/<name>.json y NUNCA se sube el snapshot completo.
    $onlyNew = ($Scraper.ContainsKey("OnlyNewOffers") -and $Scraper.OnlyNewOffers)
    $keyCol  = if ($Scraper.ContainsKey("KeyColumn")) { $Scraper.KeyColumn } else { "job_id" }

    # Aplicar renombrado a timestamp si el scraper sobreescribe fichero fijo.
    # IMPORTANTE: solo se copia el archivo CANONICO del scraper (CanonicalFile,
    # p.ej. jobs.parquet / jobs_unified.parquet). Antes se copiaban TODOS los
    # *.parquet del OutDir, incluidas las copias historicas *_YYYYMMDD_HHMMSS
    # de runs anteriores, lo que multiplicaba los archivos exponencialmente
    # (2->4->8->...->4083) y podia subir datos VIEJOS o llenar el disco.
    if ($Scraper.ContainsKey("RenameToTimestamp") -and $Scraper.RenameToTimestamp) {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $canonicalNames = @()
        if ($Scraper.ContainsKey("CanonicalFile") -and $Scraper.CanonicalFile) {
            $canonicalNames = @($Scraper.CanonicalFile)
        } else {
            # Fallback: excluir copias historicas (jobs_YYYYMMDD_HHMMSS.parquet)
            # y quedarse con los ficheros fijos del wrapper.
            $canonicalNames = @($files | ForEach-Object { $_.Name } |
                Where-Object { $_ -notmatch '_\d{8}_\d{6}\.parquet$' })
        }
        $canonical = @($files | Where-Object { $canonicalNames -contains $_.Name })
        if ($onlyNew) {
            # Modo solo ofertas nuevas: NO se crea copia timestampada local
            # del snapshot; el filtrado se hace en staging (paso posterior).
            $files = @($canonical)
            Write-Log "[$name] Modo 'solo ofertas nuevas': $($files.Count) snapshot(s) a filtrar"
        } else {
            foreach ($f in $canonical) {
                $newName = [IO.Path]::GetFileNameWithoutExtension($f.Name) + "_" + $stamp + $f.Extension
                $newPath = Join-Path $f.DirectoryName $newName
                try {
                    Copy-Item -LiteralPath $f.FullName -Destination $newPath -Force -ErrorAction Stop
                } catch {
                    Write-Log "[$name] ERROR copiando $($f.Name): $($_.Exception.Message)" -Level ERROR
                }
            }
            # Filtrar SOLO las copias recien creadas (sufijo exacto de este run).
            $files = @($canonical | ForEach-Object {
                $newName = [IO.Path]::GetFileNameWithoutExtension($_.Name) + "_" + $stamp + $_.Extension
                Join-Path $_.DirectoryName $newName
            } | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Get-Item -LiteralPath $_ })
            Write-Log "[$name] Creadas $($files.Count) copia(s) con sufijo $stamp"
        }
    } else {
        # Scrapers timestamped: solo archivos modificados DESDE el arranque del
        # pipeline (evita subir datos de un run manual anterior del mismo dia).
        $files = @($files | Where-Object { $_.LastWriteTime -ge $script:RunStart })
    }

    if ($files.Count -eq 0) {
        $result.Status = "no_data"
        $result.Message = "No se generaron archivos .$($Scraper.Ext) en este run. Se omite subida."
        Write-Log "[$name] $($result.Message)" -Level WARN
        return $result
    }
    Write-Log "[$name] Seleccionados $($files.Count) archivo(s) de este run"

    # --- Staging temporal ---
    # Staging temporal con un nombre unico (usamos .Ticks; evita el bug del
    # cast [int](Get-Date -UFormat %s) en locales con coma decimal).
    $stageDir = Join-Path $env:TEMP ("scrapers-pipeline-$name-$Today-" + (Get-Date).Ticks)
    if (Test-Path -LiteralPath $stageDir) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
    $stagingFailures = 0
    foreach ($f in $files) {
        try {
            Copy-Item -LiteralPath $f.FullName -Destination $stageDir -Force -ErrorAction Stop
        } catch {
            $stagingFailures++
            Write-Log "[$name] ERROR copiando a staging $($f.Name): $($_.Exception.Message)" -Level ERROR
        }
    }
    if ($stagingFailures -gt 0) {
        Write-Log "[$name] AVISO: $stagingFailures archivo(s) no llegaron al staging (disco lleno?)" -Level WARN
    }

    # --- Solo ofertas NUEVAS: filtrar el snapshot contra el estado subido ----
    #  El snapshot completo se queda SOLO en staging; se reemplaza por un
    #  parquet con las ofertas cuya clave aun no esta en uploaded_keys/.
    if ($onlyNew) {
        $filterScript = Join-Path $ScriptDir "filter_new_offers.py"
        if (-not (Test-Path -LiteralPath $filterScript)) {
            Write-Log "[$name] ERROR: falta $filterScript; no se sube el snapshot completo por seguridad." -Level ERROR
            Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
            $result.Message = "filter_new_offers.py no encontrado"
            return $result
        }
        $stateDir = Join-Path $ScriptDir "uploaded_keys"
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $stateFile = Join-Path $stateDir "$name.json"
        $fStamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $pending = @()
        foreach ($sf in @(Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet")) {
            $filteredOut = Join-Path $stageDir ($sf.BaseName + "_new_" + $fStamp + ".parquet")
            $newKeysFile = Join-Path $env:TEMP ("newkeys_${name}_" + (Get-Date).Ticks + ".json")
            $pyOut = & python @($filterScript, $sf.FullName, $keyCol, $stateFile,
                                $filteredOut, "--new-keys-file", $newKeysFile) 2>&1
            foreach ($line in $pyOut) { Write-Log "[$name] filter: $line" }
            $filterExit = $LASTEXITCODE
            Remove-Item -LiteralPath $sf.FullName -Force -ErrorAction SilentlyContinue
            if ($filterExit -eq 0 -and (Test-Path -LiteralPath $newKeysFile)) {
                $nk = Get-Content -LiteralPath $newKeysFile -Raw | ConvertFrom-Json
                if ($nk.total -gt 0) {
                    $pending += $newKeysFile
                } else {
                    Remove-Item -LiteralPath $filteredOut -Force -ErrorAction SilentlyContinue
                    Remove-Item -LiteralPath $newKeysFile -Force -ErrorAction SilentlyContinue
                    Write-Log "[$name] Sin ofertas nuevas en $($sf.Name); no se sube." -Level WARN
                }
            } else {
                Remove-Item -LiteralPath $newKeysFile -Force -ErrorAction SilentlyContinue
                Write-Log "[$name] ERROR filtrando $($sf.Name) (exit $filterExit)" -Level ERROR
            }
        }
        if ($pending.Count -eq 0) {
            $result.Status = "no_data"
            $result.Message = "Sin ofertas nuevas (todas ya estaban subidas)"
            Write-Log "[$name] $($result.Message). Se omite la subida." -Level WARN
            Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
            return $result
        }
        $script:PendingNewKeys[$name] = $pending
        Write-Log "[$name] Filtrado OK: se subira(n) $($pending.Count) fichero(s) solo con ofertas nuevas"
    }

    # --- Validacion de integridad + compatibilidad con Spark ----------------
    #  ensure_compatible.py lee cada parquet (detecta corruptos), comprueba
    #  columnas obligatorias y minimo de filas, convierte timestamp[ns] ->
    #  timestamp[us] y mueve los invalidos a cuarentena local. Ademas genera
    #  el manifest JSON de auditoria.
    $stamp        = Get-Date -Format "yyyyMMdd_HHmmss"
    $manifestFile = Join-Path $env:TEMP "manifest_${name}_${stamp}.json"
    $quarantineDir = Join-Path $script:QuarantineRoot "$name"
    $compatScript = Join-Path $ScriptDir "ensure_compatible.py"
    $validatorOk  = $true

    if (Test-Path -LiteralPath $compatScript) {
        $requiredCols = ""
        if ($Scraper.ContainsKey("RequiredColumns") -and $Scraper.RequiredColumns.Count -gt 0) {
            $requiredCols = ($Scraper.RequiredColumns -join ",")
        }
        $pyArgs = @($compatScript, $stageDir, "--manifest", $manifestFile,
                    "--quarantine-dir", $quarantineDir)
        if ($requiredCols) { $pyArgs += @("--required-cols", $requiredCols) }
        if ($Scraper.ContainsKey("CoherenceSite") -and $Scraper.CoherenceSite) {
            $pyArgs += @("--coherence", $Scraper.CoherenceSite)
        }
        # Huella de fuentes/busquedas efectivas en el manifest (RF-7).
        $pyArgs += @("--fingerprint-source", $name)
        $pyOut = & python @pyArgs 2>&1
        foreach ($line in $pyOut) { Write-Log "[$name] compat: $line" }
        if ($LASTEXITCODE -ne 0) { $validatorOk = $false }
    } else {
        # Fail-closed: sin validador no podemos garantizar que los ficheros
        # sean validos, asi que NO se sube nada (mejor no subir que subir errores).
        Write-Log "[$name] ERROR: $compatScript no encontrado; no se puede validar. No se sube nada." -Level ERROR
        Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $manifestFile -Force -ErrorAction SilentlyContinue
        $result.Message = "ensure_compatible.py no encontrado; subida cancelada por seguridad"
        return $result
    }

    # --- Solo quedan en staging los ficheros VALIDOS -------------------------
    $remaining = @(Get-ChildItem -LiteralPath $stageDir -File -Filter "*.parquet")
    if (-not $validatorOk) {
        $bad = @()
        if (Test-Path -LiteralPath $manifestFile) {
            try {
                $m = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
                $bad = @($m.files | Where-Object { $_.status -eq "bad" } | ForEach-Object { $_.file })
            } catch {
                Write-Log "[$name] Manifest JSON ilegible: $($_.Exception.Message)" -Level WARN
            }
        }
        $result.Rejected = $bad.Count
        $result.Message = "Validacion rechazo $($bad.Count) archivo(s): $($bad -join ', ')"
        Write-Log "[$name] $($result.Message)" -Level ERROR
        if ($remaining.Count -eq 0) {
            Write-Log "[$name] Sin archivos validos que subir." -Level ERROR
            Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $manifestFile -Force -ErrorAction SilentlyContinue
            return $result
        }
        $result.Status = "partial"
    } else {
        $result.Status = "ok"
    }
    Write-Log "[$name] Subiendo $($remaining.Count) archivo(s) .$($Scraper.Ext) validados"

    # --- Subir a landing/scraper_X/dia=YYYY-MM-DD/ ---
    # --as-subdir=false: upload the staging *contents* directly under the day
    # folder, so the real key matches the manifest's `remote`
    # (dia=YYYY-MM-DD/<file>) instead of an intermediate staging folder.
    $dest = "https://$StorageAccount.blob.core.windows.net/$Container/$name/dia=$Today/$SasToken"
    Write-Log "[$name] Destino: $Container/$name/dia=$Today/"
    & $AzCopyPath copy $stageDir $dest --overwrite=true --recursive --as-subdir=false --log-level=ERROR 2>&1 |
        ForEach-Object { Write-Log "[$name] azcopy: $_" }
    $azExit = $LASTEXITCODE

    if ($azExit -ne 0) {
        Write-Log "[$name] azcopy fallo (exit $azExit)" -Level ERROR
        $result.Status = "failed"
        $result.Message = "azcopy fallo (exit $azExit)"
    } else {
        Write-Log "[$name] Subida completada OK"
        $result.Uploaded = $remaining.Count
        # Solo ofertas nuevas: marcar las claves como subidas SOLO ahora que
        # azcopy ha terminado con exito.
        if ($onlyNew -and $script:PendingNewKeys.ContainsKey($name)) {
            Update-UploadedState -ScraperName $name
        }
        # --- Manifest de auditoria en landing/_manifests/<scraper>/<stamp>.json
        #  (fuera del path que vigila Autoloader; no lo ingiere como parquet).
        if (Test-Path -LiteralPath $manifestFile) {
            # Anotar la ruta REMOTA de cada fichero (dia=YYYY-MM-DD/<nombre>)
            # para que Databricks pueda cruzar manifiesto vs archivos ingeridos.
            try {
                $m = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
                foreach ($fe in @($m.files)) {
                    if ($null -ne $fe) {
                        $fe | Add-Member -NotePropertyName "remote" `
                            -NotePropertyValue "dia=$Today/$($fe.file)" -Force
                    }
                }
                # Write JSON without BOM: PS 5.1 Set-Content -Encoding UTF8 adds one.
                $json = $m | ConvertTo-Json -Depth 6
                [System.IO.File]::WriteAllText($manifestFile, $json, [System.Text.UTF8Encoding]::new($false))
            } catch {
                Write-Log "[$name] No se pudo anotar 'remote' en el manifest: $($_.Exception.Message)" -Level WARN
            }
            $manifestDest = "https://$StorageAccount.blob.core.windows.net/$Container/_manifests/$name/$stamp.json$SasToken"
            & $AzCopyPath copy $("`"$manifestFile`"") $manifestDest --overwrite=true --log-level=ERROR 2>&1 |
                ForEach-Object { Write-Log "[$name] manifest: $_" }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "[$name] Fallo subir manifest (exit $LASTEXITCODE)" -Level WARN
            } else {
                Write-Log "[$name] Manifest subido: _manifests/$name/$stamp.json"
            }
        }
    }

    # Limpiar staging y manifest temporal
    Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $manifestFile -Force -ErrorAction SilentlyContinue
    return $result
}

# --- Lanzar todos los scrapers en background ---------------------------------
#  Usamos Start-Process -File $wrapper directamente (sin -Command anidado) para
#  minimizar problemas de escapado. Cada wrapper gestiona su propio cwd.
$runningJobs = @{}
Write-Log "Lanzando $($Scrapers.Count) scrapers en paralelo..."
foreach ($s in $Scrapers) {
    $name = $s.Name
    if (-not (Test-Path -LiteralPath $s.Wrapper)) {
        Write-Log "[$name] Wrapper no existe: $($s.Wrapper)" -Level ERROR
        $script:GlobalFailures++
        $script:PublishResults[$name] = @{ Status = "failed"; ExitCode = -1; Message = "Wrapper no existe" }
        Update-RunSourceState -Name $name -Result $script:PublishResults[$name]
        continue
    }
    try {
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$s.Wrapper) `
            -PassThru -WindowStyle Normal
    } catch {
        Write-Log "[$name] No se pudo lanzar el proceso: $($_.Exception.Message)" -Level ERROR
        $script:GlobalFailures++
        $script:PublishResults[$name] = @{ Status = "failed"; ExitCode = -1; Message = $_.Exception.Message }
        Update-RunSourceState -Name $name -Result $script:PublishResults[$name]
        continue
    }
    $runningJobs[$name] = @{ Proc=$proc; Scraper=$s; StartedAt=(Get-Date); Logged30=$false }
    Write-Log "[$name] Lanzado PID $($proc.Id) (wrapper: $(Split-Path -Leaf $s.Wrapper))"
}

if ($runningJobs.Count -eq 0) {
    Write-Log "No se pudo lanzar ningun scraper. Abortando." -Level ERROR
    exit 1
}

# --- Bucle de monitorizacion: cada 60s revisa procesos ----------------------
#  Timeout global de seguridad = 36h (algo mas que el limite interno del
#  wrapper de LinkedIn, que es 24h). Si se alcanza, mata lo que quede.
$globalTimeoutHours = 36
$globalDeadline = (Get-Date).AddHours($globalTimeoutHours)
$lastProgressLog = (Get-Date)

while ($runningJobs.Count -gt 0) {
    Start-Sleep -Seconds 60

    $finishedNames = @()
    foreach ($entry in $runningJobs.GetEnumerator()) {
        $name = $entry.Key
        $job  = $entry.Value
        $p    = $job.Proc

        if ($p.HasExited) {
            $res = Get-ProcessResult -Proc $p
            $exitCode = $res.ExitCode
            $outcome  = $res.Outcome
            $elapsed  = (Get-Date) - $job.StartedAt
            $mins     = [int]$elapsed.TotalMinutes

            if ($outcome -eq "killed") {
                Write-Log "[$name] Proceso terminado exit=-1 (matado por timeout, ${mins}min)" -Level WARN
            } else {
                Write-Log "[$name] Proceso terminado exit=$exitCode (PID $($p.Id), ${mins}min)"
            }
            $finishedNames += $name

            if ($outcome -eq "killed") {
                # Aunque el wrapper se haya matado, puede haber dejado ficheros
                # VALIDOS a medio run. Se validan y se suben SOLO los validos
                # (los corruptos/incoherentes van a cuarentena). Si no queda
                # ningun fichero valido, no se sube nada.
                Write-Log "[$name] Validando los ficheros generados antes del timeout (solo se subiran los validos)..." -Level WARN
                $up = Invoke-ScraperUpload -Scraper $job.Scraper
                if ($up.Status -eq "ok" -or $up.Status -eq "partial") {
                    Write-Log "[$name] Datos validos subidos: $($up.Status) ($($up.Uploaded) subidos, $($up.Rejected) rechazados)"
                } elseif ($up.Status -eq "no_data") {
                    Write-Log "[$name] Sin datos validos que subir; no se sube nada."
                } else {
                    Write-Log "[$name] No se pudo subir ningun dato valido: $($up.Message)" -Level ERROR
                }
                $script:GlobalFailures++
                # Si se subieron datos validos, reflejarlo para la politica del
                # trigger (_READY); si no, queda como 'killed' (sin datos).
                $killedStatus = if ($up.Status -eq "ok" -or $up.Status -eq "partial") { $up.Status } else { "killed" }
                $script:PublishResults[$name] = @{ Status = $killedStatus; ExitCode = -1; Uploaded = $up.Uploaded; Rejected = $up.Rejected; Message = $up.Message }
                Update-RunSourceState -Name $name -Result $script:PublishResults[$name]
                continue
            }

            if ($exitCode -eq 0) {
                $up = Invoke-ScraperUpload -Scraper $job.Scraper
                if ($up.Status -eq "ok" -or $up.Status -eq "no_data") {
                    Write-Log "[$name] Resultado: $($up.Status) ($($up.Uploaded) subidos)"
                } else {
                    Write-Log "[$name] Resultado: $($up.Status). $($up.Message)" -Level ERROR
                    $script:GlobalFailures++
                }
            } else {
                Write-Log "[$name] Scraper fallo (exit $exitCode). Se validan y suben SOLO los ficheros validos ya generados..." -Level WARN
                # Mensaje especial para Indeed (exit=3 = CDP Chrome no responde)
                if ($name -eq "indeed" -and $exitCode -eq 3) {
                    Write-Log "[$name] CAUSA: Chrome con --remote-debugging-port=9222 no responde." -Level ERROR
                    Write-Log "[$name] SOLUCION: lanza un Chrome con:" -Level ERROR
                    Write-Log "[$name]   chrome.exe --remote-debugging-port=9222" -Level ERROR
                    Write-Log "[$name] y logueate en indeed.com. Luego vuelve a ejecutar el pipeline." -Level ERROR
                }
                $up = Invoke-ScraperUpload -Scraper $job.Scraper
                if ($up.Status -eq "failed") {
                    Write-Log "[$name] Subida de parciales fallo: $($up.Message)" -Level ERROR
                } else {
                    Write-Log "[$name] Parciales subidos: $($up.Status) ($($up.Uploaded) subidos, $($up.Rejected) rechazados)"
                }
                $script:GlobalFailures++
            }
            $script:PublishResults[$name] = $up
            Update-RunSourceState -Name $name -Result $script:PublishResults[$name]
            continue
        }

        # Log de progreso cada ~30 min para los que siguen corriendo
        $elapsed = (Get-Date) - $job.StartedAt
        if (-not $job.Logged30 -and $elapsed.TotalMinutes -ge 30) {
            Write-Log "[$name] Aun corriendo... (PID $($p.Id), $([int]$elapsed.TotalMinutes)min)"
            $job.Logged30 = $true
        }
    }

    foreach ($n in $finishedNames) { [void]$runningJobs.Remove($n) }

    # Timeout global
    if ((Get-Date) -gt $globalDeadline) {
        Write-Log "Timeout global (${globalTimeoutHours}h) alcanzado. Matando $($runningJobs.Count) scraper(s) pendientes." -Level ERROR
        foreach ($entry in $runningJobs.GetEnumerator()) {
            $nm = $entry.Key
            try { Stop-Process -Id $entry.Value.Proc.Id -Force -ErrorAction Stop } catch {}
            $script:GlobalFailures++
            $script:PublishResults[$nm] = @{ Status = "killed"; ExitCode = -1 }
            Update-RunSourceState -Name $nm -Result $script:PublishResults[$nm]
        }
        $runningJobs.Clear()
        break
    }

    # Resumen de estado cada 15 min
    if (((Get-Date) - $lastProgressLog).TotalMinutes -ge 15) {
        $lastProgressLog = Get-Date
        $stillName = ($runningJobs.Keys -join ", ")
        Write-Log "Pendientes: $($runningJobs.Count) ($stillName). Fallos acumulados: $script:GlobalFailures"
    }
}

# --- Resumen ----------------------------------------------------------------
$Elapsed = (Get-Date) - $StartTime
Write-Log "====  Fin pipeline. Fallos: $script:GlobalFailures  Duracion: $([math]::Round($Elapsed.TotalSeconds))s ===="
foreach ($pr in $script:PublishResults.GetEnumerator()) {
    Write-Log "  [$($pr.Key)] status=$($pr.Value.Status) subidos=$($pr.Value.Uploaded) rechazados=$($pr.Value.Rejected)"
}

# --- Cierre del estado persistente del run (RF-1) ----------------------------
#  Hito 3 (cierre): tras "Fin pipeline" y el resumen por fuente, el run queda
#  "closed" con finished_at. Si el proceso muere antes, el estado se queda
#  "pending" y el log sin "Fin pipeline" lo delata (RF-1). Un fallo al guardar
#  NUNCA aborta el pipeline (RF-9).
if ($null -ne $script:RunState) {
    try {
        Close-RunState -State $script:RunState
    } catch {
        Write-Log "No se pudo cerrar el estado del run: $($_.Exception.Message)" -Level WARN
    }
    Save-RunState
}

# --- Notificar a Databricks que los datos del dia estan listos ----------------
#  El job de Databricks esta configurado con File Arrival sobre este archivo.
#  La politica la define $ReadyPolicy en config.ps1:
#    'all'       -> solo si NO hubo fallos (todos los scrapers OK)
#    'any_valid' -> si al menos un scraper publico datos validos
$publishedCount = @($script:PublishResults.GetEnumerator() |
    Where-Object { $_.Value.Status -eq "ok" -or $_.Value.Status -eq "partial" }).Count

$shouldTrigger = $false
if ($ReadyPolicy -eq "all") {
    $shouldTrigger = ($script:GlobalFailures -eq 0)
    Write-Log "Politica _READY = 'all': trigger solo si 0 fallos."
} elseif ($ReadyPolicy -eq "any_valid") {
    $shouldTrigger = ($publishedCount -gt 0)
    Write-Log "Politica _READY = 'any_valid': fuentes publicadas con datos validos: $publishedCount"
} else {
    Write-Log "ReadyPolicy desconocido ('$ReadyPolicy'). Se usa 'all'." -Level WARN
    $shouldTrigger = ($script:GlobalFailures -eq 0)
}

if ($shouldTrigger) {
    $readyDest = "https://$StorageAccount.blob.core.windows.net/$Container/_READY/dia=$Today.txt$SasToken"
    Write-Log "Escribiendo trigger file _READY/dia=$Today.txt ..."
    $tmpReady = Join-Path $env:TEMP "ready_$Today.txt"
    "OK $Today $(Get-Date -Format o)" | Out-File -LiteralPath $tmpReady -Encoding utf8
    & $AzCopyPath copy $("`"$tmpReady`"") $readyDest --overwrite=true --log-level=ERROR 2>&1 |
        ForEach-Object { Write-Log "azcopy: $_" }
    if ($LASTEXITCODE -ne 0) {
        Write-Log "Fallo la subida del trigger file (exit $LASTEXITCODE)" -Level ERROR
        exit 3
    }
    Write-Log "Trigger file escrito. Databricks puede arrancar."
    Remove-Item -LiteralPath $tmpReady -Force -ErrorAction SilentlyContinue
} else {
    Write-Log "Hubo fallos, NO se escribe trigger file. Databricks no correra hoy." -Level WARN
}

if ($script:GlobalFailures -gt 0) { exit 1 }
exit 0