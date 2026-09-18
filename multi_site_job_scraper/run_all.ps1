# run_all.ps1 - Lanza los 6 scrapers en paralelo (Start-Process directo a python)
# Uso: .\run_all.ps1
#
# Cada subscraper se lanza DIRECTAMENTE como python.exe (sin wrapping en
# powershell.exe -Command) para maximizar visibilidad: el output del scraper
# se VE en vivo en su propia ventana de consola. Adicionalmente, cada scraper
# escribe su log interno a data/<site>/run.log via el modulo logging de Python.
#
# Al terminar TODOS (o agotar su timeout), ejecuta merge.py para unificar en
# data/merged/. El merge es INCONDICIONAL: se ejecuta SIEMPRE, acabe como acabe
# el run (fallos, timeouts, excepciones o Ctrl+C). Si ningun subscraper genero
# output nuevo en este run, se hace un merge COMPLETO como respaldo.
#
# Robustez:
#  - Timeout por subscraper ($PerScraperTimeoutMin): si un subscraper se cuelga
#    mas de ese tiempo se mata y se continua con el resto.
#  - Resultados: escribe data/merged/last_run.json con el estado de cada
#    subscraper (para auditoria).
#  - Merge GARANTIZADO (bloque finally): merge PARCIAL con los outputs validos
#    generados en ESTE run y, si no hay ninguno, merge COMPLETO de todo lo
#    disponible. El merge nunca se omite.

$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

# Instante de arranque: solo se mergean outputs modificados a partir de aqui
# (evita incluir el snapshot viejo de un subscraper que fallo este run).
$RunStart = Get-Date

# Timeout por subscraper en minutos (0 = sin timeout / esperar indefinidamente).
# Debe superar la duracion normal del subscraper mas lento (irishjobs puede
# tardar varias horas). Si se agota, se mata el proceso y se hace merge parcial.
$PerScraperTimeoutMin = 1080

Write-Host "=== Multi-Site Job Scraper - Paralelo ===" -ForegroundColor Cyan
Write-Host "Lanzando 6 scrapers en procesos independientes..." -ForegroundColor Yellow

$defs = @(
    @{ Name="irishjobs";  Module="src.irishjobs"   },
    @{ Name="stepstone_nl"; Module="src.stepstone_nl" },
    @{ Name="devitjobs";  Module="src.devitjobs"   },
    @{ Name="nvb";        Module="src.nvb"         },
    @{ Name="jobs_ch";    Module="src.jobs_ch"     },
    @{ Name="glassdoor";  Module="src.glassdoor"   }
)

# --- Cargar el runner de merge (logica de merge garantizado) -----------------
#  Se aísla en merge_runner.ps1 para poder probarlo por separado. Si por algun
#  motivo no se pudiera cargar, el bloque finally define un respaldo inline.
$mergeRunner = Join-Path $root "merge_runner.ps1"
if (Test-Path -LiteralPath $mergeRunner) {
    try {
        . $mergeRunner
    } catch {
        Write-Host ("  No se pudo cargar merge_runner.ps1: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
} else {
    Write-Host "  merge_runner.ps1 no encontrado; se usara respaldo inline." -ForegroundColor Yellow
}

$procs = @()
$results = @{}
$outputs = @()
$mergeInfo = @{ Exit = 0; Ran = $false; Mode = "" }
$globalExit = 0

try {
    for ($i = 0; $i -lt $defs.Count; $i++) {
        $d = $defs[$i]
        # Asegurar que existe la carpeta de logs del scraper
        $dataDir = Join-Path $root "data\$($d.Name)"
        if (-not (Test-Path -LiteralPath $dataDir)) {
            New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
        }
        # Lanzar python directamente. Inicia como nueva ventana de consola donde
        # el output del scraper se ve en vivo. No redirigimos stdout/stderr a
        # ficheros porque cada scraper escribe su propio log a data/<site>/run.log
        # (configurado internamente via logging.FileHandler).
        Write-Host ("  Lanzando {0} ..." -f $d.Name) -ForegroundColor Gray
        $p = Start-Process -FilePath "python" `
            -ArgumentList @("-m", $d.Module) `
            -WorkingDirectory $root `
            -WindowStyle Normal `
            -PassThru
        $procs += @{ Name=$d.Name; Proc=$p; StartedAt=(Get-Date); TimedOut=$false }
        Write-Host ("  [{0}] PID {1}" -f $d.Name, $p.Id) -ForegroundColor Green
        # Escalonado: lanzar 4 Chromium a la vez puede colgar el arranque
        # (timeout de launch). Dejamos que cada navegador arranque en solitario.
        if ($i -lt $defs.Count - 1) {
            Write-Host "  Esperando 60s antes del siguiente scraper..." -ForegroundColor DarkGray
            Start-Sleep -Seconds 60
        }
    }

    Write-Host ""
    Write-Host "Esperando a que terminen todos los scrapers..." -ForegroundColor Yellow
    Write-Host "Cada scraper se ve en su propia ventana. Log interno de cada uno:"
    foreach ($d in $defs) {
        Write-Host ("    Get-Content -LiteralPath '{0}\data\{1}\run.log' -Wait" -f $root, $d.Name) -ForegroundColor Gray
    }
    Write-Host ""

    # Polling: cada 30s hasta que todos terminen o agoten su timeout.
    $lastProgress = (Get-Date)
    while ($true) {
        $allExited = $true
        foreach ($e in $procs) {
            if ($e.TimedOut) { continue }
            if (-not $e.Proc.HasExited) {
                if ($PerScraperTimeoutMin -gt 0) {
                    $elapsed = (Get-Date) - $e.StartedAt
                    if ($elapsed.TotalMinutes -ge $PerScraperTimeoutMin) {
                        Write-Host ("  [{0}] TIMEOUT tras {1} min. Matando proceso (PID {2})..." -f $e.Name, [int]$elapsed.TotalMinutes, $e.Proc.Id) -ForegroundColor Red
                        if ($null -ne $e.Proc) {
                            try { Stop-Process -Id $e.Proc.Id -Force -ErrorAction SilentlyContinue } catch {}
                        }
                        $e.TimedOut = $true
                        continue
                    }
                }
                $allExited = $false
            }
        }
        if ($allExited) { break }
        Start-Sleep -Seconds 30
        # Resumen de progreso cada 2 min
        if (((Get-Date) - $lastProgress).TotalMinutes -ge 2) {
            $lastProgress = Get-Date
            $pending = ($procs | Where-Object { -not $_.TimedOut -and -not $_.Proc.HasExited } | ForEach-Object { $_.Name }) -join ", "
            Write-Host ("Pendientes: {0}" -f $pending) -ForegroundColor DarkGray
        }
    }

    Write-Host ""
    Write-Host "Resultados:" -ForegroundColor Cyan
    foreach ($e in $procs) {
        if ($e.TimedOut) {
            Write-Host ("  [{0}] timeout (matado por el wrapper)" -f $e.Name) -ForegroundColor Red
            $results[$e.Name] = "timeout"
            $globalExit = 1
            continue
        }
        try { $e.Proc | Wait-Process -Timeout 5 -ErrorAction SilentlyContinue } catch {}
        try { $e.Proc.Refresh() } catch {}
        $ec = if ($null -ne $e.Proc.ExitCode) { $e.Proc.ExitCode } else { -1 }
        $color = if ($ec -eq 0) { "Green" } else { "Red" }
        Write-Host ("  [{0}] exit={1}" -f $e.Name, $ec) -ForegroundColor $color
        $results[$e.Name] = $ec
        if ($ec -ne 0) { $globalExit = 1 }
    }

    # Merge PARCIAL: solo se tienen en cuenta los outputs generados/actualizados
    # en ESTE run. El de un subscraper que fallo antes de escribir se ignora.
    foreach ($d in $defs) {
        $csv = Join-Path $root "data\$($d.Name)\output\jobs.csv"
        if (-not (Test-Path -LiteralPath $csv)) {
            Write-Host ("  [{0}] sin output; se excluye del merge." -f $d.Name) -ForegroundColor Yellow
            continue
        }
        $fi = Get-Item -LiteralPath $csv
        if ($fi.Length -le 0) {
            Write-Host ("  [{0}] output vacio; se excluye del merge." -f $d.Name) -ForegroundColor Yellow
            continue
        }
        if ($fi.LastWriteTime -lt $RunStart) {
            Write-Host ("  [{0}] output no actualizado en este run; se excluye del merge." -f $d.Name) -ForegroundColor Yellow
            continue
        }
        $outputs += $d.Name
    }
}
catch {
    Write-Host ("ERROR inesperado en run_all: {0}" -f $_.Exception.Message) -ForegroundColor Red
    $globalExit = 1
}
finally {
    # --- MERGE GARANTIZADO ---------------------------------------------------
    #  Se ejecuta SIEMPRE: aunque fallen/timeout todos los subscrapers, aunque
    #  no haya outputs nuevos o aunque el run se interrumpa. El unificado
    #  data/merged/jobs_unified.* debe existir para que el nightly lo suba.
    Write-Host ""
    Write-Host "=== Merge (garantizado) ===" -ForegroundColor Cyan
    try {
        if (Get-Command Invoke-Merge -ErrorAction SilentlyContinue) {
            $mergeInfo = Invoke-Merge -RunStart $RunStart -Outputs $outputs -Root $root
        } else {
            # Respaldo inline si merge_runner.ps1 no estaba disponible.
            $mergeScript = Join-Path $root "merge.py"
            if ($outputs.Count -gt 0) {
                python $mergeScript --since ($RunStart.ToString('yyyy-MM-dd"T"HH:mm:ss'))
                $mergeInfo = @{ Exit = $LASTEXITCODE; Ran = $true; Mode = "parcial (inline)" }
                if ($mergeInfo.Exit -ne 0) {
                    python $mergeScript
                    $mergeInfo = @{ Exit = $LASTEXITCODE; Ran = $true; Mode = "completo (inline)" }
                }
            } else {
                python $mergeScript
                $mergeInfo = @{ Exit = $LASTEXITCODE; Ran = $true; Mode = "completo (inline)" }
            }
        }
    } catch {
        Write-Host ("  Merge fallo con excepcion: {0}" -f $_.Exception.Message) -ForegroundColor Red
        $mergeInfo = @{ Exit = 1; Ran = $true; Mode = "exception" }
    }

    if ($mergeInfo.Exit -eq 0) {
        Write-Host ("Merge OK ({0})." -f $mergeInfo.Mode) -ForegroundColor Green
    } else {
        Write-Host ("Merge FALLO (exit {0}, modo {1})." -f $mergeInfo.Exit, $mergeInfo.Mode) -ForegroundColor Red
        $globalExit = $mergeInfo.Exit
    }

    # Registrar resultados del run para auditoria
    $lastRun = @{
        run_at   = (Get-Date -Format o)
        per_scraper_timeout_min = $PerScraperTimeoutMin
        results  = $results
        outputs_merged = $outputs
        merge_ran  = [bool]$mergeInfo.Ran
        merge_mode = $mergeInfo.Mode
        merge_exit = [int]$mergeInfo.Exit
        global_exit = $globalExit
    }
    $mergedDir = Join-Path $root "data\merged"
    if (-not (Test-Path -LiteralPath $mergedDir)) { New-Item -ItemType Directory -Force -Path $mergedDir | Out-Null }
    try {
        $lastRun | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $mergedDir "last_run.json") -Encoding UTF8
        Write-Host "Resultados guardados en data/merged/last_run.json" -ForegroundColor Gray
    } catch {
        Write-Host ("  No se pudo escribir last_run.json: {0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host ("=== Completado (exit={0}) ===" -f $globalExit) -ForegroundColor Cyan
Write-Host "Output unificado: data/merged/jobs_unified.csv" -ForegroundColor White
Write-Host "Output unificado: data/merged/jobs_unified.parquet" -ForegroundColor White

exit $globalExit
