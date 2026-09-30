# AGENTS.md — Talent Market Lens

## Proyecto
Talent Market Lens es un proyecto que busca generar una plataforma analítica de ofertas de trabajo. Se ejecutan diariamente diferentes scrapers para obtener las ofertas de trabajo y se cargan esas ofertas en Databricks. Se realiza la ELT (databricks_notebooks/) y posteriormente en Power BI se genera un dashboard para analizar los datos (Job_Offers_Dashboard.pbip).

## Comandos
- Ejecutar: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scrapers-pipeline\run_scrapers_and_upload.ps1`
- Tests: No hay tests generales. Cada scraper tiene su propio test, y los notebooks de Databricks tienen el suyo:
    - Tests scrapers: `python -m pytest tests -q`
    - Test databricks: `python -m pytest databricks_notebooks/tests -q`
    - Diagnóstico (desde la raíz del repositorio): `python -m pytest scrapers-pipeline/tests -q`

## Estilo y convenciones
- Solo biblioteca estándar (pytest únicamente para tests).
- Los datos deben estar en inglés; mensajes de usuario en español.

## Reglas
- Lee docs/constitution.md y la spec activa antes de tocar código.
- No modifiques archivos dentro de specs/ salvo petición explícita.
- Si modificas algo importante del proyecto, que no concuerda con la spec, debes modificar la spec correspondiente.

## Commits
- Un commit atómico por tarea completada, cuando su comprobación pasa (tests en verde).
- Mensaje convencional (`feat`/`fix`/`test`/`docs`/`chore`) con la intención y la tarea (p. ej. `feat(verification): T-14 regla de progreso`).
- Si un commit necesita "y" para describirse, divídelo con `git add -p`.
- El commit local es un checkpoint; se sube con frecuencia a la rama de la spec (respaldo y visibilidad). Lo que se revisa antes de publicar es el merge a `main`, no cada push.
- Al cerrar cada grupo de tareas (~5), detente y revisa `git log -p` del grupo contra la spec activa.

## Ramas y flujo de trabajo
- Una rama por spec: `spec/<NNN>-<nombre-corto>` (p. ej. `spec/001-verify-scrapers-run`). Se crea desde `main` al iniciar la spec.
- Antes de crear una spec, comprueba en `specs/` que su número no esté ya en uso: los números de spec no se reutilizan ni se crean duplicados (si existen 001 y 002, la siguiente es 003).
- Todo el trabajo de una spec (código, tests y actualización de `specs/`) ocurre en su rama.
- `main` solo recibe merges de ramas de spec; no se commitea directamente en `main`.
- Se hace `git push` de la rama a `origin` con frecuencia para respaldar y dejar historial visible.
- Fusionar a `main` con las tareas cerradas y los tests en verde (`git switch main`, `git pull`, `git merge --no-ff spec/<...>`) es un **checkpoint**, no el cierre: la rama **no se borra y no se etiqueta** todavía.
- Una spec se **cierra** (etiquetar `git tag -a spec-NNN -m "spec NNN completada"` y borrar la rama) solo cuando su **comprobación real** funciona, no solo los tests. Si la comprobación real falla, se corrige dentro de la misma spec y su rama, y se repite.
- Los defectos de implementación o de tests detectados al verificar una spec se corrigen en esa misma spec; no se abre una spec nueva para parchear el objetivo incumplido de otra.
- Recomendado aunque trabajes en solitario: abrir un Pull Request de la rama contra `main` y revisarlo contra la spec antes de fusionar, para practicar el flujo de equipo.

## Al terminar cualquier tarea
- Verifica mediante los tests establecidos que no hay errores.
- Al cerrar una spec, ejecuta además su comprobación real (en la 001, la CLI contra la landing real) y registra el resultado; la spec no se cierra hasta que esa comprobación funcione.