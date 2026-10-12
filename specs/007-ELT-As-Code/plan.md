# Plan técnico: ELT como código (Asset Bundles) y entorno dev

## 1. Objetivo y límites

Convertir la definición del job `Git Based ELT` —hoy configurada a mano en el
workspace— en un Asset Bundle versionado, con un target dev aislado, un target
prod que reproduce el comportamiento actual, validación en CI y un cutover
reversible. No cambia la lógica de los notebooks, ni el pipeline local, ni los
dashboards.

## 2. Estado de partida (se confirma en T-02–T-04)

- Job `Git Based ELT` (id `704156530681034`): 7 tareas de notebook (bronze →
  cuatro silver → gold → export web) configuradas a mano; notebooks servidos
  desde una Git folder apuntada a una rama; job cluster `tm-lens-job`
  (Standard_D4ds_v4); `max_concurrent_runs: 1`, sin timeout; sin `git_source`
  (los notebooks se referencian por ruta); programación diaria `50 0 23 * * ?`
  (`Europe/Madrid`), sin trigger de eventos.
- Unity Catalog con catálogo `job_offers` (bronze/silver/gold) y
  almacenamiento externo; Premium/Enterprise inferida por Unity Catalog (T-02).
- Sin IaC, sin entornos, sin CI. Los notebooks ya reciben `catalog`,
  `storage_account`, `container` y `checkpoints_root` por widgets, lo que
  facilita la parametrización.
- `origin` es GitHub (`Darkaleja69/talent-market-lens`); la tarea de export web
  de la 006 (`Build_for_Web-Dashboard`) ya está en el job y se capturará en el
  bundle.
- Huecos por confirmar y registrar en `evidence.md`: permisos para crear jobs y
  tokens, y ref real de la Git folder y su modo de actualización (T-04).

## 3. Arquitectura objetivo

```text
GitHub repo (origin)
├── databricks.yml                  ← raíz del bundle
├── resources/elt_job.yml           ← definición del job (parametrizada)
├── databricks_notebooks/**         ← código ELT (sin cambios, se referencia)
├── .github/workflows/…             ← CI: bundle validate
└── docs/databricks-de/**           ← track de certificación

dev:  bundle deploy -t dev  → /Workspace/Users/…/.bundle/talent-market-lens/dev/files
      job «TML ELT (dev)» (pausado) → job_offers_dev · _checkpoints_dev · web_export_dev

prod: bundle deploy -t prod → …/.bundle/talent-market-lens/prod/files
      job «Git Based ELT» → job_offers · _checkpoints · gold/web_export · programación 23:50
```

## 4. Stack y herramientas

- Databricks CLI con Asset Bundles (versión fijada y anotada en el RUNBOOK).
- GitHub Actions para CI (instala la CLI en el runner con versión fijada).
- Sin dependencias Python nuevas; nada de runtime adicional.

## 5. Estructura de archivos

- `databricks.yml` — nombre del bundle, variables, targets y `sync` acotado
  (subir solo `databricks_notebooks/**` y `resources/**`; no el repo entero).
- `resources/elt_job.yml` — job con tareas, dependencias, compute, programación
  y `max_concurrent_runs: 1`.
- `.github/workflows/databricks-bundle-validate.yml` — validación en CI.
- `.gitignore` — añadir `.databricks/` (estado local del bundle).
- `docs/databricks-de/roadmap.md` y `docs/databricks-de/notes/…`.
- `specs/007-ELT-As-Code/{spec,plan,tasks,evidence,cutover}.md`.
- Los notebooks no se mueven.

## 6. Diseño del bundle

Variables y valores por target:

| Variable | dev | prod |
|---|---|---|
| `catalog` | `job_offers_dev` | `job_offers` |
| `storage_account` | (la de producción) | (la de producción) |
| `container` | (el de producción) | (el de producción) |
| `checkpoints_root` | `…/_checkpoints_dev` | `…/_checkpoints` |
| `export_prefix` | `gold/web_export_dev` | `gold/web_export` |
| `job_name` | `TML ELT (dev)` | `Git Based ELT` |

- `workspace` por target: perfil local (`--profile`) para dev; en prod, el
  mismo host con el perfil documentado.
- Sin `run_as` explícito al principio (se mantiene el owner actual); se revisa
  en T-15 si conviene un service principal.

## 7. Diseño del job

- Tareas que replican el fan-out actual: bronze → cuatro silver → gold →
  export web, con los mismos `depends_on`.
- `notebook_path: ${workspace.file_path}/databricks_notebooks/<notebook>`.
- `base_parameters` alimentados desde las variables (los notebooks ya leen
  widgets).
- Compute: replicar el job cluster actual `tm-lens-job` (Standard_D4ds_v4) en
  `job_clusters`; si dev puede usar uno más pequeño y barato, se documenta.
- Programación: `schedule` del bundle con la misma expresión que el job manual
  (`50 0 23 * * ?`, `Europe/Madrid`); dev sin programación.
- `max_concurrent_runs: 1` (como hoy) y timeout razonable; notificaciones
  opcionales.
- Dev: sin programación y `pause_status: PAUSED`; prod: programación activa
  solo tras el cutover.

## 8. Entorno dev

- Catálogo `job_offers_dev` con schemas `bronze`, `silver` y `gold`; si el
  metastore no tiene almacenamiento por defecto, se ubica en la misma storage
  con prefijo propio (se decide en T-08).
- Checkpoints en `_checkpoints_dev`: el primer run re-lee todo el histórico de
  la landing; coste pequeño, medido y documentado.
- Export dev en `gold/web_export_dev` para no pisar el export que consume la
  web pública.
- La landing es de solo lectura para dev: no se escribe ni se borra nada.

## 9. Validación continua (CI)

- Workflow en `pull_request` (y push a la rama de la spec) que ejecuta
  `databricks bundle validate -t dev` con perfil CI
  (`DATABRICKS_HOST` + `DATABRICKS_TOKEN` de dev como secretos del repositorio).
- Sin deploy en CI inicialmente; deploy dev manual y, opcionalmente, por
  `workflow_dispatch` con environment protegido (T-14, extra).
- `concurrency` para no solapar ejecuciones.

## 10. Cutover y rollback

Precondiciones: validate y CI en verde, run dev con conteos cotejados, plan de
rollback revisado y ventana acordada (tarde, antes del ciclo nocturno).

Pasos del cutover:

1. `bundle deploy -t prod` con el job en pausa (no dispara nada).
2. Deshabilitar el job manual → habilitar el job del bundle (registrar ids y
   hora).
3. Noche real: verificar una única ejecución programada (23:50) y run en
   verde; tablas y export avanzan.
4. Evidencia en `evidence.md` y `cutover.md`.

Rollback: deshabilitar el job del bundle → rehabilitar el manual. Se prueba en
seco dentro de la ventana y se documenta con tiempos.

## 11. Estrategia de comprobaciones

- `databricks bundle validate` local y en CI (incluye un PR que falla y luego
  pasa).
- Run dev end-to-end con conteos por tabla frente a producción para los mismos
  datos de origen.
- Cutover con noche real y disparo único.
- Rollback en seco.
- Suites existentes en verde:
  `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q`.

## 12. Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| Una noche sin ejecución tras el cutover | Rollback documentado y probado; cutover con margen y verificación a la mañana siguiente; revisar la programación y `logs/upload-<fecha>.log` del pipeline local |
| Dev y prod compartiendo algún recurso por un variable mal puesta | Diff del JSON de ambos jobs antes del cutover; checklist en `cutover.md` |
| Sin permisos para crear jobs, tokens o el SP | Recon temprano (T-02/T-03); escalar antes de continuar |
| La programación del bundle difiere (cron o zona horaria) | Cotejo en el dry-run (T-17) y checklist del cutover |
| Sync del bundle sube demasiado (repo entero) | Acotar `sync` a `databricks_notebooks/**` y `resources/**`; validar tamaño en T-09 |
| Ediciones manuales del job tras el cutover | Regla: el job solo se cambia por bundle; CI valida en cada PR |
| PAT de CI caducado | Rotación documentada; el fallo es visible en el workflow |
| Doble ejecución durante la ventana | `max_concurrent_runs: 1` + checklist (manual apagado antes de encender bundle) |
| Coste del entorno dev | Job pausado por defecto; un run por iteración; compute pequeño o serverless |

## 13. Decisiones y alternativas descartadas

- **Terraform / API de Jobs a mano:** más superficie y peor encaje con el
  temario; el bundle es la vía estándar.
- **Dos workspaces:** coste y administración innecesarios; dos catálogos y
  prefijos distintos bastan.
- **Mover los notebooks a `src/`:** riesgo gratuito para la web y los
  dashboards; el bundle los referencia in situ.
- **Un solo target con variables manuales:** pierde la separación dev/prod y
  la lección de entornos y parámetros.
- **Mantener la Git folder como fuente del job:** no versiona la configuración
  del job ni separa entornos; el cutover la reemplaza (la Git folder sigue
  siendo la fuente del código hasta entonces).

## 14. Secuencia de implementación

- Semana 1: T-01–T-04 (preparación, sin tocar la ELT).
- Tras el cierre de la 006 (y `git merge main` en la rama): T-05–T-11 (bundle
  base y entorno dev).
- Semanas 2–3: T-12–T-14 (CI), T-15–T-20 (producción y cutover), T-21–T-22
  (documentación y cierre).

## 15. Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T-03, T-05, T-06, T-07 |
| RF-2 | T-08, T-09, T-10, T-15 |
| RF-3 | T-07, T-12 |
| RF-4 | T-10 |
| RF-5 | T-12, T-13 |
| RF-6 | T-15, T-16, T-17, T-18, T-19, T-20 |
| RF-7 | T-21 |
| RF-8 | T-01, T-22 |
| RF-9 | T-04, T-17, T-22 |

## 16. Cumplimiento de la constitución

- **Stack simple:** la CLI y los bundles son la herramienta estándar para
  versionar jobs; no añaden runtime al proyecto.
- **Spec y código:** todo el trabajo corresponde a esta spec y se refleja aquí.
- **Lógica e interfaz:** no se toca la lógica de negocio; solo definición y
  despliegue.
- **Tests:** suites existentes en verde más las comprobaciones de despliegue.
- **Persistencia:** nada nuevo en el repositorio; dev usa prefijos propios.
- **Idioma:** recursos y definiciones en inglés; documentos en español.
