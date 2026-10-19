# T-20 — Rollback en seco (ventana sin riesgo, antes de las 23:00)

Hoja de trabajo para ejecutar y cronometrar T-20. El procedimiento de referencia
es `cutover.md` §8.1 y §8.3; aquí están los pasos exactos, el registro de tiempos
y el estado al que hay que volver.

- **Objetivo:** comprobar que volver atrás es rápido y seguro, y dejar el
  sistema **como está ahora** (bundle `UNPAUSED` programado, manual `PAUSED`).
- **Ventana:** cualquier momento antes de las **23:00** del día elegido, con
  margen (si algo se tuerce, se puede dejar el cutover restaurado antes de la
  hora del run). Duración esperada ~20 min.
- **Quién:** la persona ejecuta los `bundle deploy` y los cambios de estado del
  job manual; el asistente prepara, cronometra y documenta. No se lanza ningún
  run de la ELT en esta prueba (los deploys no ejecutan).
- **Estado de partida (post-T-19):** bundle `916863262593632` `UNPAUSED`,
  cron `50 0 23 * * ?`/`Europe/Madrid`; manual `704156530681034` `PAUSED`;
  último run del bundle `471695262858572` SUCCESS (el 2026-10-19). Si la prueba
  se hace en otro día, el último run será el de la noche anterior: lo que
  importa es que haya exactamente uno por día.

## Fase 1 — Rollback (volver al job manual)

1. En la rama (`git switch spec/007-elt-as-code`), en `databricks.yml`, target
   `prod`: `pause_status: PAUSED`.
2. Commit:

   ```powershell
   git add databricks.yml
   git commit -m "chore(bundle): T-20 rollback en seco a prod pausado"
   ```

3. Merge a `main` y push (con el `AGENTS.md` de `main` restaurado):

   ```powershell
   git switch main
   git pull
   git merge --no-ff --no-commit spec/007-elt-as-code
   git checkout main -- AGENTS.md
   git status
   git commit -m "Merge spec/007-elt-as-code: T-20 rollback en seco"
   git push origin main
   ```

4. Validar y desplegar desde `main`:

   ```powershell
   databricks bundle validate -t prod --profile vs_code
   databricks bundle deploy   -t prod --profile vs_code
   ```

5. Verificar que el bundle quedó pausado y reanudar el manual (UI → job
   `Git Based ELT` (`704156530681034`) → Schedule → **Resume**):

   ```powershell
   databricks jobs get 916863262593632 --profile vs_code --output json   # PAUSED
   databricks jobs list-runs --job-id 916863262593632 --limit 1 --profile vs_code --output json
   databricks jobs get 704156530681034 --profile vs_code --output json   # UNPAUSED
   databricks jobs list-runs --job-id 704156530681034 --limit 1 --profile vs_code --output json
   ```

   No debe haber ningún run activo en ninguno de los dos.

## Fase 2 — Restaurar el cutover

6. **Primero** pausar el job manual en la UI (job `Git Based ELT`
   (`704156530681034`) → Schedule → **Pause**) y verificar que queda `PAUSED`.
   Se pausa **antes** de desplegar el `UNPAUSED` para no dejar ni un momento los
   dos jobs programados:

   ```powershell
   databricks jobs get 704156530681034 --profile vs_code --output json   # PAUSED
   ```

   Si no queda `PAUSED`, **no continuar**: reanudarlo de nuevo y abortar la
   prueba, dejando el cutover como está.

7. En la rama (`git switch spec/007-elt-as-code`), en `databricks.yml`, target
   `prod`: `pause_status: UNPAUSED`.
8. Commit, merge a `main`, push y deploy (mismo patrón que en 2–4, con
   `chore(bundle): T-20 restaura el cutover` y
   `Merge spec/007-elt-as-code: T-20 restaura el cutover`).
9. Verificar el estado final:

   ```powershell
   databricks jobs get 916863262593632 --profile vs_code --output json   # UNPAUSED
   databricks jobs get 704156530681034 --profile vs_code --output json   # PAUSED
   ```

10. Comprobar que el job del bundle sigue con la cron correcta, 7 tareas,
    `timeout_seconds: 1800`, `max_concurrent_runs: 1` y **sin runs nuevos** (si
    la prueba se hace el mismo día del último run, el único sigue siendo
    `471695262858572`; si se hace más tarde, el último será el del día
    anterior y lo que importa es que no haya más de uno por día).

## Registro de tiempos (rellenar y volcar a `cutover.md` §10)

| Paso | Hora inicio | Hora fin | Tiempo |
|---|---|---|---|
| Fase 1: commit del `PAUSED` | | | |
| Merge + push | | | |
| `validate` + `deploy` | | | |
| Bundle verificado `PAUSED` + manual reanudado | | | |
| Fase 2: manual pausado en la UI | | | |
| Fase 2: commit del `UNPAUSED` | | | |
| Merge + push | | | |
| `validate` + `deploy` | | | |
| Estado final verificado (bundle `UNPAUSED` + manual `PAUSED`) | | | |

Criterios de `cutover.md` §8.1: pausa efectiva **< 5 min**; reconciliación
completa (git + deploy) **< 10 min**. Si algo difiere, se corrige `cutover.md`.

## Al terminar

- `cutover.md` §10: filas de T-20 y de la restauración con los tiempos reales.
- `evidence.md`: fila de T-20 (procedimiento ejecutado, sin sorpresas, tiempos).
- `tasks.md`: T-20 → `[x]`.
- Estado final obligatorio: bundle `UNPAUSED` programado a las 23:00; manual
  `PAUSED`; sin runs activos ni nuevos.
