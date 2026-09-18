# Run IrishJobs scraper overnight - targets 1000 data job offers
# Strategy: 13 roles x 13 locations = 169 combos
# Nationwide searches first (most pages/results), then city-specific

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$start = Get-Date
$logFile = "data\irishjobs\run.log"

Write-Host "============================================"
Write-Host "  IrishJobs Scraper - Overnight Run"
Write-Host "  Inicio: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "  Roles: Data Analyst/Scientist/Engineer/Analytics Engineer + variants"
Write-Host "  Ciudades: 13 (nationwide + 12 cities)"
Write-Host "  Target: 1000 ofertas"
Write-Host "============================================"

python -m src.irishjobs --max-total 1000 --verbose

$end = Get-Date
$duration = $end - $start

Write-Host "============================================"
Write-Host "  Finalizado: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "  Duracion: $([math]::Round($duration.TotalMinutes, 1)) minutos"
Write-Host "============================================"

# Quick summary from output
if (Test-Path "data\irishjobs\output\jobs.csv") {
    Write-Host ""
    Write-Host "=== Resumen rapido ==="
    python -c @"
import pandas as pd
df = pd.read_csv('data/irishjobs/output/jobs.csv', dtype=str, keep_default_na=False)
print(f'Total ofertas: {len(df)}')
targets = ['Data Analyst','Data Scientist','Data Engineer','Analytics Engineer']
for t in targets:
    c = (df['search_role'] == t).sum()
    print(f'  {t}: {c}')
print(f'Con requirements: {(df.requirements != \"\").sum()}/{len(df)}')
print(f'Con salario: {((df.salary_min != \"\") | (df.salary_max != \"\")).sum()}/{len(df)}')
"@
}
