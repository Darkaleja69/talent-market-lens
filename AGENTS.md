# AGENTS.md — Talent Market Lens

## Proyecto
Talent Market Lens es un proyecto que busca generar una plataforma analítica de ofertas de trabajo. Se ejecutan diariamente diferentes scrapers para obtener las ofertas de trabajo y se cargan esas ofertas en Databricks. Se realiza la ELT (databricks_notebooks/) y posteriormente en Power BI se genera un dashboard para analizar los datos (Job_Offers_Dashboard.pbip).

## Comandos
- Ejecutar el ciclo diario (igual que la tarea programada): `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scrapers-pipeline\run_pipeline_supervised.ps1"`. El supervisor lanza `run_scrapers_and_upload.ps1` con transcripción y, si el run no cierra con `Fin pipeline`, deja evidencia, espera de forma acotada a los wrappers de la fecha y ejecuta la recuperación; si la espera expira o la recuperación no puede ejecutarse o cerrar, el run queda `pending` para el arranque siguiente.
- Recuperación manual de un run truncado (requiere `LANDING_SAS_TOKEN` y AzCopy; no relanza scrapers):
    - En seco, sin subir ni cerrar nada: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scrapers-pipeline\recover_and_upload.ps1" -Date <YYYY-MM-DD> -DryRun`
    - Real: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scrapers-pipeline\recover_and_upload.ps1" -Date <YYYY-MM-DD>`
    - `-PlanJson <ruta>` recupera desde un plan ya generado (`python -m verification.recovery plan`, desde `scrapers-pipeline`); sin `-Date` ni `-PlanJson` se recorre el histórico completo (uso manual). La automatización (reconciliación al arrancar y supervisor) ya invoca el ejecutor con `-Date`.
- Estado del run y evidencia: `scrapers-pipeline/logs/run_state/<YYYY-MM-DD>.json` con `status` `pending`/`closed`. Ante un cierre anómalo el supervisor escribe `logs/run_state/<fecha>.abort.json` y la transcripción `logs/transcript-<fecha>.log`; el cierre normal queda en `logs/upload-<fecha>.log` (`Fin pipeline`).
- Reparación de scrapers caídos (spec 004): comando `/repair` para el flujo completo, o la CLI desde `scrapers-pipeline/`:
    - Refresco del diagnóstico (paso 0): `python -m verification.verify_run --offline`.
    - CLI de reparaciones: `python -m repair.cli targets|brief|threshold|quality|record`.
- Bundle (Databricks Asset Bundles): `databricks bundle validate -t dev|prod --profile <perfil>`, `databricks bundle deploy -t dev|prod --profile <perfil>` y `databricks bundle summary -t <target> --profile <perfil>`. El deploy **no ejecuta** el job: solo sube ficheros y actualiza su definición.
- Tests: No hay tests generales. Cada scraper tiene su propio test, y los notebooks de Databricks tienen el suyo:
    - Tests scrapers: `python -m pytest tests -q`
    - Test databricks: `python -m pytest databricks_notebooks/tests -q`
    - Pipeline, diagnóstico y reparaciones (desde la raíz del repositorio): `python -m pytest scrapers-pipeline/tests -q`

## Estilo y convenciones
- Solo biblioteca estándar (pytest únicamente para tests).
- Los datos deben estar en inglés; mensajes de usuario en español.

## Reglas
- Lee docs/constitution.md y la spec activa antes de tocar código.
- No modifiques archivos dentro de specs/ salvo petición explícita.
- Si modificas algo importante del proyecto, que no concuerda con la spec, debes modificar la spec correspondiente.
- El job de producción lo gestiona el bundle (`edit_mode: UI_LOCKED`): no se edita en la UI. Para pausar o reanudar, se cambia `pause_status` en el target `prod` de `databricks.yml` y se despliega.
- CI del bundle: validación en PR/push (`.github/workflows/databricks-bundle-validate.yml`) y deploy de dev manual por dispatch con aprobación (`.github/workflows/databricks-bundle-deploy-dev.yml`); producción siempre manual y validada.
- Rollback de producción: procedimiento, IDs y registro en `specs/007-ELT-As-Code/cutover.md`; el job manual pausado (`704156530681034`) se conserva como vía de vuelta.

### Trabajo de las specs (bloque local de este worktree; no debe llegar a `main`)

- **La persona decide** (alcance, diseño, alternativas) y **revisa** lo escrito;
  el asistente prepara las tareas, **implementa el código** (notebooks, módulos,
  tests), lo explica y lo commitea tras la verificación independiente.
- La persona ejecuta el **bundle** (deploy/run) y las consultas contra el
  workspace para las comprobaciones reales; el asistente no llama al bundle ni
  al workspace salvo petición explícita.
- **Alcance de la verificación:** el `verifier` revisa solo el diff de la tarea
  y ejecuta solo los tests de ese cambio. La suite completa es un gate de la
  spec (tarea de validación), no de cada tarea.
- Estas instrucciones son propias del trabajo de especificación (estas specs y
  sus worktrees), no del proyecto real: al fusionar esta rama a `main`
  —checkpoint o cierre—, restaurar `AGENTS.md` de `main` antes de cerrar el
  merge (`git checkout main -- AGENTS.md`) para que este bloque no se fusione.

## Commits
- Un commit atómico por tarea completada, cuando su comprobación pasa (tests en verde).
- Mensaje convencional (`feat`/`fix`/`test`/`docs`/`chore`) con la intención y la tarea (p. ej. `feat(verification): T-14 regla de progreso`).
- Si un commit necesita "y" para describirse, divídelo con `git add -p`.
- El commit local es un checkpoint; se sube con frecuencia a la rama de la spec (respaldo y visibilidad). Lo que se revisa antes de publicar es el merge a `main`, no cada push.
- Al cerrar cada grupo de tareas (~5), detente y revisa `git log -p` del grupo contra la spec activa.

## Ramas y flujo de trabajo
- Una rama por spec: `spec/<NNN>-<nombre-corto>` (p. ej. `spec/001-verify-scrapers-run`). Se crea desde `main` al iniciar la spec.
- El trabajo de cada spec se hace en un **worktree** en una carpeta hermana del clon principal (p. ej. `../talent-market-lens-006`), nunca dentro del árbol de otro worktree: así no ensucia `git status`, no choca con `specs/` ni peligra con limpiezas accidentales.
- Antes de crear una spec, comprueba en `specs/` que su número no esté ya en uso: los números de spec no se reutilizan ni se crean duplicados (si existen 001 y 002, la siguiente es 003).
- Todo el trabajo de una spec (código, tests y actualización de `specs/`) ocurre en su rama.
- `main` solo recibe merges de ramas de spec o de reparación (`repair/`); no se commitea directamente en `main`.
- Se hace `git push` de la rama a `origin` con frecuencia para respaldar y dejar historial visible.
- Fusionar a `main` con las tareas cerradas y los tests en verde (`git switch main`, `git pull`, `git merge --no-ff spec/<...>`) es un **checkpoint**, no el cierre: la rama **no se borra y no se etiqueta** todavía.
- Una spec se **cierra** (etiquetar `git tag -a spec-NNN -m "spec NNN completada"` y borrar la rama) solo cuando su **comprobación real** funciona, no solo los tests. Si la comprobación real falla, se corrige dentro de la misma spec y su rama, y se repite.
- El worktree de la spec se elimina (`git worktree remove <ruta>`) al **cerrar** la spec (tag y borrado de rama); el merge a `main` no lo elimina, solo lleva consigo los documentos de `specs/`.
- Los defectos de implementación o de tests detectados al verificar una spec se corrigen en esa misma spec; no se abre una spec nueva para parchear el objetivo incumplido de otra.
- Cada reparación de scraper vive en su rama `repair/<fuente>-<YYYYMMDD>` (p. ej. `repair/infojobs-20261005`), creada desde `main` (mientras el proceso de la 004 no esté fusionado, desde la rama de esa spec, porque el proceso vive en ella). Su registro versionado queda en `repairs/<YYYYMMDD>-<fuente>/` (índice en `repairs/README.md`, historial en `repairs/history.json`). El push de la rama requiere la validación explícita de la persona (RF-12).


## Al terminar cualquier tarea
- Verifica mediante los tests establecidos que no hay errores.
- Al cerrar una spec, ejecuta además su comprobación real (en la 001, la CLI contra la landing real) y registra el resultado; la spec no se cierra hasta que esa comprobación funcione.
