# register_nightly_indeed.ps1
# Crea la tarea programada IndeedJobsNightly (diaria 2:00 AM).
# Ejecutar como ADMINISTRADOR.

$ErrorActionPreference = "Stop"

$TaskName = "IndeedJobsNightly"
$ScriptPath = Join-Path $PSScriptRoot "run_nightly_indeed.ps1"

if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Error "No se encuentra $ScriptPath."
    exit 1
}

# Verificar si ya existe
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "La tarea '$TaskName' ya existe. Eliminando..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At "02:00"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Scraper nocturno de Indeed. Corre diario a las 2 AM con 1 termino x 2 ciudades x 2 paginas (4 SERPs, enriquecimiento ~90%, ~20-40 ofertas/noche)." `
    -Force

Write-Host "Tarea '$TaskName' registrada correctamente."
Write-Host "  Horario: diario 02:00 AM"
Write-Host "  Script: $ScriptPath"
Write-Host "  Log: output/nightly.log"
Write-Host ""
Write-Host "IMPORTANTE: Antes de la 1a ejecucion, lanza Chrome con CDP y logueate:"
Write-Host '  powershell -ExecutionPolicy Bypass -File .\start_chrome_cdp.ps1'
Write-Host "Luego inicia sesion en Indeed en esa ventana (resuelve el CAPTCHA a mano)."
Write-Host "El Chrome debe quedarse abierto para que la tarea nocturna funcione."
