# T-10 — comprobación real: run programado, permisos y dashboard

> **Estado:** preparado el **2026-11-08**; se ejecuta **después** del run
> programado del domingo **2026-11-08 23:00** (`Europe/Madrid`) y del workflow
> del dashboard del lunes **2026-11-09 01:00 UTC**. No se marca T-10 como
> completada hasta tener todos los resultados.

## Ventana

| Elemento | Valor esperado |
|---|---|
| Job de producción | `916863262593632` (`Git Based ELT`) |
| Cron | `50 0 23 ? * SUN`, `Europe/Madrid`, `UNPAUSED` |
| Run esperado | el primero con `start_time` posterior a 2026-11-08 22:00 UTC |
| Tareas | **9/9** en `SUCCESS` |
| Dashboard | workflow `.github/workflows/dashboard.yml`, lunes 01:00 UTC |
| Warehouse | `53d8e3e91af6749c` **`STOPPED`** antes y después |
| Coste | ordinario de producción (no consume el presupuesto de dev de la 012) |

## 1. Localizar el run y comprobar las 9 tareas

```powershell
$profile = 'vs_code'
$runs = databricks jobs list-runs --job-id 916863262593632 --limit 10 `
  --profile $profile -o json | ConvertFrom-Json
$runs | Select-Object run_id, start_time, end_time,
  @{n='state';e={$_.state.result_state}} | Format-Table -AutoSize

# Elegir el run de la ventana (epoch en milisegundos; 2026-11-08 22:00 UTC)
$run = $runs | Where-Object { $_.start_time -gt 1794175200000 } |
  Sort-Object start_time -Descending | Select-Object -First 1
$runId = $run.run_id
"run: $runId"

$detail = databricks jobs get-run $runId --profile $profile -o json |
  ConvertFrom-Json
$detail.tasks | Select-Object task_key, run_id,
  @{n='state';e={$_.state.result_state}},
  @{n='life';e={$_.state.life_cycle_state}} | Format-Table -AutoSize
```

**Criterio:** `$detail.state.result_state -eq 'SUCCESS'` y las 9 tareas en
`SUCCESS`.

## 2. Leer el resumen del bronce y la cuarentena (sin SQL)

```powershell
$bronze = $detail.tasks | Where-Object task_key -eq 'Bronze_Ingestion'
$bronzeOut = databricks jobs get-run-output $bronze.run_id --profile $profile -o json |
  ConvertFrom-Json
$summary = $bronzeOut.notebook_output.result | ConvertFrom-Json
$summary.streams | Select-Object source, num_input_rows_total, quarantined_rows,
  status | Format-Table -AutoSize
```

**Criterio:** las 4 fuentes presentes; `quarantined_rows` coherente con las
filas inválidas del lote (0 si no hay ninguna); `num_input_rows_total` es el
total de entrada del lote.

## 3. Leer el cierre y los checks (sin SQL)

```powershell
$closing = $detail.tasks | Where-Object task_key -eq 'Publish_Run_Summary'
$closingOut = databricks jobs get-run-output $closing.run_id --profile $profile -o json |
  ConvertFrom-Json
$result = $closingOut.notebook_output.result | ConvertFrom-Json
$result | ConvertTo-Json -Depth 8
```

**Criterio:** `record.status = ok` (la fila de `ops.run_metrics` se escribió),
`run_id` correcto y los cuatro checks (`bronze_summary`, `bronze_sources`,
`freshness`, `pending_files`) con `ok = true`.

**Conteos coherentes:** `bronze_rows` del cierre = suma de
`num_input_rows_total` del bronce; `quarantine_rows` = suma de
`quarantined_rows`. La fila de `ops.run_metrics` tiene `bronze_rows` y
`quarantine_rows` derivados de ese resumen (la escritura se confirma con
`record.status = ok`; no hace falta abrir el warehouse).

## 4. Constraints y grants siguen presentes (API gratuita)

```powershell
$tables = @(
  'job_offers.silver.offers_indeed',
  'job_offers.silver.offers_infojobs',
  'job_offers.silver.offers_linkedin',
  'job_offers.silver.offers_multi_site',
  'job_offers.gold.fact_offers'
)
foreach ($t in $tables) {
  $o = databricks tables get $t --profile $profile -o json | ConvertFrom-Json
  "$t owner=$($o.owner)"
  $o.properties.PSObject.Properties |
    Where-Object Name -like 'delta.constraints.*' |
    ForEach-Object { "  $($_.Name)" }
}

databricks grants get catalog job_offers --profile $profile -o json
databricks grants get table job_offers.gold.fact_offers --profile $profile -o json
databricks grants get external-location landing-loc --profile $profile -o json
databricks jobs get 916863262593632 --profile $profile -o json
databricks warehouses list --profile $profile -o json
```

**Criterio:** las 10 constraints de prod siguen ahí; los grants siguen `{}`
(salvo la desviación aprobada); el job sigue con 9 tareas, el cron y `run_as`
intactos; el warehouse `STOPPED`.

## 5. Dashboard

```powershell
# Si no hay gh instalado, abrir la pestaña Actions del repositorio y filtrar
# por el workflow "dashboard" del 2026-11-09.
git log --oneline -3
```

**Criterio:** el workflow publica y el `meta.json.data_date` es la fecha
esperada (no supera los 8 días). El workflow falla sin desplegar si
`data_date` tiene más de 8 días.

## 6. Cierre de la evidencia

- Añadir una fila por comprobación a `docs/governance/evidence.md` (fecha, T-10,
  comando, resultado, id del run/tarea, coste).
- Anotar el coste ordinario del run en `docs/costs.md` (no es coste de dev).
- Si aparece una fila nueva en cuarentena: reconciliar el lote y registrar
  motivo y tratamiento en `evidence.md` y en el RUNBOOK.
- Si falla por permisos o calidad: **no cerrar**; aplicar rollback o corregir
  en esta misma spec y repetir solo con autorización de coste.
- Solo después de T-10 en verde: tag anotado `spec-012`, merge final si hay
  commits posteriores al checkpoint y retirada del worktree/rama según
  `AGENTS.md`.
