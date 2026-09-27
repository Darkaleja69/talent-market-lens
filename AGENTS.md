# AGENTS.md — Talent Market Lens

## Proyecto
Talent Market Lens es un proyecto que busca generar una plataforma analítica de ofertas de trabajo. Se ejecutan diariamente diferentes scrapers para obtener las ofertas de trabajo y se cargan esas ofertas en Databricks. Se realiza la ELT (databricks_notebooks/) y posteriormente en Power BI se genera un dashboard para analizar los datos (Job_Offers_Dashboard.pbip).

## Comandos
- Ejecutar: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scrapers-pipeline\run_scrapers_and_upload.ps1`
- Tests: No hay tests generales. Cada scraper tiene su propio test, y los notebooks de Databricks tienen el suyo:
    - Tests scrapers: `python -m pytest tests -q´
    - Test databricks: `python -m pytest databricks_notebooks/tests -q´

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
- El commit local es un checkpoint; no hagas push sin revisar antes los diffs.
- Al cerrar cada grupo de tareas (~5), detente y revisa `git log -p` del grupo contra la spec activa.

## Al terminar cualquier tarea
- Verifica mediante los tests establecidos que no hay errores. 