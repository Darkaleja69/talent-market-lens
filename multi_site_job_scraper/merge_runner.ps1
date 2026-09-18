# merge_runner.ps1 - Ejecucion del merge del multi-site, SIEMPRE disponible.
#
# Uso:
#   . "$PSScriptRoot\merge_runner.ps1"
#   $info = Invoke-Merge -RunStart $RunStart -Outputs $outputs -Root $root
#
# Estrategia (nunca lanza excepciones):
#   1) Si hay outputs actualizados en este run -> merge PARCIAL (--since).
#   2) Si el merge parcial no encuentra CSVs (exit != 0) o no hay outputs
#      nuevos -> merge COMPLETO como respaldo.
#
# Devuelve un hashtable @{ Exit; Ran; Mode }:
#   Exit : exit code del ultimo merge.py ejecutado (0 = OK)
#   Ran  : $true si se intento ejecutar merge.py
#   Mode : "parcial" | "completo (fallback)" | "completo (sin outputs nuevos)"
#          | "missing" | "exception"

function Invoke-Merge {
    param(
        [Parameter(Mandatory=$true)] [datetime] $RunStart,
        [string[]] $Outputs,
        [Parameter(Mandatory=$true)] [string] $Root
    )
    $mergeScript = Join-Path $Root "merge.py"
    $info = @{ Exit = 0; Ran = $false; Mode = "" }

    if (-not (Test-Path -LiteralPath $mergeScript)) {
        Write-Host "  merge.py no encontrado en $mergeScript" -ForegroundColor Red
        $info.Exit = 1
        $info.Mode = "missing"
        return $info
    }

    if ($null -eq $Outputs) { $Outputs = @() }
    $info.Ran = $true

    if ($Outputs.Count -gt 0) {
        $info.Mode = "parcial"
        Write-Host ("Ejecutando merge.py (merge parcial) con {0} sitio(s): {1}" -f `
            $Outputs.Count, ($Outputs -join ", ")) -ForegroundColor Yellow
        python $mergeScript --since ($RunStart.ToString('yyyy-MM-dd"T"HH:mm:ss'))
        $info.Exit = $LASTEXITCODE

        if ($info.Exit -ne 0) {
            Write-Host ("  Merge parcial sin datos (exit {0}). Reintentando merge COMPLETO..." -f `
                $info.Exit) -ForegroundColor Yellow
            python $mergeScript
            $info.Exit = $LASTEXITCODE
            if ($info.Exit -eq 0) { $info.Mode = "completo (fallback)" }
        }
    } else {
        $info.Mode = "completo (sin outputs nuevos)"
        Write-Host "Sin outputs validos de subscrapers. Ejecutando merge COMPLETO..." -ForegroundColor Yellow
        python $mergeScript
        $info.Exit = $LASTEXITCODE
    }
    return $info
}
