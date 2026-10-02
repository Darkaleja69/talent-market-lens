# Plan técnico: recuperación de runs abortados del pipeline

## 1. Objetivo y límites

Garantizar que un aborto del proceso principal de
`run_scrapers_and_upload.ps1` no pierda los datos ya generados por los
scrapers: el run se cierra, lo pendiente se valida y publica (o se preserva con
evidencia), `_READY` refleja lo realmente publicado y el aborto deja evidencia
para corregir su causa.

La recuperación no repite scraping, no relanza wrappers, no mata procesos
huérfanos y no modifica el diagnóstico de la 001. Reutiliza la validación,
filtrado y publicación existentes. Los restos del incidente 2026-10-01 están
preservados en `%TEMP%\opencode\pending-recovery-2026-10-01` para la
comprobación real.

## 2. Hallazgos del incidente que condicionan el diseño

- El proceso principal muere y el log general queda sin `Fin pipeline`; los
  wrappers siguen vivos como huérfanos y sus datos no se publican (el subidor
  es el padre).
- El estado del run (`$RunStart`, `$runningJobs`, `$PublishResults`) solo vive
  en memoria; no hay transcripción ni `trap`/`finally` global.
- `Invoke-ScraperUpload` selecciona por `LastWriteTime >= $RunStart`; relanzar
  el pipeline no recoge los ficheros del run anterior.
- `recover_and_upload.ps1` ya recorre los restos de los 4 scrapers, pero es
  manual, no usa `--coherence`/`--fingerprint-source`, ignora los errores de
  subida y siempre escribe `_READY/dia=$Today` (fecha de ejecución, no del
  dato), sin respetar `$ReadyPolicy`.
- Multi-site no conserva copia fechada de `jobs_unified.parquet` con
  `OnlyNewOffers`; si corre el run siguiente, el snapshot se sobrescribe.
- La tarea `Scrapers_Daily_Upload` es `IgnoreNew` con límite de 36 h: el cierre
  del run anterior debe terminar antes del arranque siguiente.
- Pista de causa (hipótesis, no hecho): *roam* de Wi-Fi a las 01:59:45 y tarea
  con `RunOnlyIfNetworkAvailable=true`; el registro operativo del Programador
  de tareas está deshabilitado y no hay WER del proceso.

## 3. Arquitectura

Tres piezas nuevas o evolucionadas, sin tocar el flujo feliz del pipeline:

1. **Supervisor de ciclo** (`scrapers-pipeline/run_pipeline_supervised.ps1`):
   nueva acción de la tarea programada. Lanza `run_scrapers_and_upload.ps1`
   como único hijo, guarda transcripción y código de salida, detecta el cierre
   anómalo y ejecuta el cierre/recuperación con espera acotada.
2. **Módulo de decisión** (`scrapers-pipeline/verification/recovery.py`):
   funciones puras y CLI que, con los logs, el estado del run y los artefactos
   locales, deciden si un run está truncado, qué pendientes tiene cada fuente,
   cómo publicarlos, si procede `_READY` y el bloque de cierre. Se prueba con
   pytest y reutiliza `verification.run_evidence`.
3. **Ejecutor de recuperación** (`scrapers-pipeline/recover_and_upload.ps1`
   evolucionado): consume el plan del módulo y publica lo pendiente con la
   misma validación, filtrado, manifests y estado de subidas que el pipeline.
   Sigue sirviendo para la recuperación manual de históricos con `-DryRun`.

**Descartado:** Job Objects o P/Invoke para matar árboles (dependencia y riesgo
innecesarios; los huérfanos hacen trabajo útil y ahora se recuperan), y
duplicar la subida dentro de cada wrapper (carreras sobre `uploaded_keys` y
dobles subidas).

## 4. Estado del run y evidencia

- `run_scrapers_and_upload.ps1` añade `Write-RunState` y persiste
  `logs/run_state/<fecha>.json` en hitos: inicio, resultado/subida por fuente y
  fin. El esquema (claves en inglés) incluye `run_date`, `started_at`,
  `finished_at`, `sources{}` con `status`, `uploaded`, `rejected` y
  `last_activity_at`. La escritura es atómica y no altera el flujo.
- El supervisor, al ver un hijo con salida anómala (exit sin `Fin pipeline`),
  escribe `logs/run_state/<fecha>.abort.json` con: código de salida, hora,
  últimas líneas del log general, transcripción (`logs/transcript-<fecha>.log`)
  y PIDs/locks de wrappers vivos. Consulta eventos de sistema disponibles
  (Kernel-Power, red, PowerShell) y registra lo que encuentre.
- La transcripción se captura con `Start-Process -RedirectStandardOutput/Error`;
  no depende de que el proceso principal coopere.

## 5. Módulo Python de decisión (`verification/recovery.py`)

Funciones puras (sin red) y CLI `python -m verification.recovery`:

- `discover_truncated_runs(logs_dir, state_dir)`: run con `Inicio` sin `Fin`
  (reutiliza `run_evidence`) y/o estado `pending`; excluye runs ya cerrados.
- `build_plan(run_date, projects_root, config)`: por fuente, pendientes
  recuperables con su día real: Indeed/InfoJobs por nombre fechado más
  reciente, LinkedIn por delta contra `uploaded_keys` (si no hay nuevas, se
  omite), Multi-site por `jobs_unified*` fechado o reconstrucción con
  `merge.py` sobre salidas por portal; incluye `required_cols`, `coherence`,
  `fingerprint_source` y `key_column` de `config.ps1`. Registra lo no
  recuperable con motivo.
- `decide_ready(policy, published)`: `any_valid`/`all` con datos publicados
  reales y motivo si no se escribe.
- `closing_block(result)`: líneas con el formato exacto del pipeline
  (`====  Fin pipeline. Fallos: N  Duracion: ...s ====` y resumen por fuente).
- `pending_status()`: consulta para la limpieza y para saber si un run sigue
  abierto (locks vivos).

Tests unitarios con fixtures (logs truncados, estados, restos por fuente,
duplicados, sin SAS, snapshots ya subidos) y de integración offline con
temporales.

## 6. Ejecutor de recuperación

`recover_and_upload.ps1` se amplía (compatible con su uso actual):

- Parámetros `-Date <YYYY-MM-DD>` (run a cerrar), `-PlanJson` (salida del
  módulo) y `-DryRun`; sin `-Date` mantiene el barrido histórico.
- Para cada ítem del plan: staging temporal, validación completa con
  `ensure_compatible.py --manifest --quarantine-dir --required-cols
  --coherence --fingerprint-source`, filtrado `filter_new_offers.py` para
  `OnlyNewOffers` (delta contra `uploaded_keys`), subida con
  `--as-subdir=false`, manifest con `remote` sin BOM y actualización de
  `uploaded_keys` solo tras éxito.
- Antes de subir, comprueba la landing con `verification.landing`
  (clave exacta/objeto coincidente) para no repetir publicaciones ya
  confirmadas; los errores de subida cuentan y cambian el código de salida.
- Escribe el cierre del run (bloque de `closing_block`) en
  `logs/upload-<run_date>.log` y decide `_READY/dia=<run_date>` con
  `decide_ready`, registrando el motivo si no se escribe.
- Nunca borra pendientes no recuperados; los deja marcados en el estado y
  devuelve código distinto de cero para que se reintente.

## 7. Supervisor y ciclo nocturno

1. La tarea programada pasa a ejecutar `run_pipeline_supervised.ps1` (mismo
   horario y límite; validación de la persona al cambiarla).
2. El supervisor arranca el pipeline con transcripción y espera su fin.
3. Si el log tiene `Fin pipeline`, termina propagando el código de salida.
4. Si no, espera de forma acotada (config por defecto 6 h, siempre antes del
   arranque siguiente) a que desaparezcan los locks de los wrappers de esa
   fecha; después ejecuta el cierre/recuperación y termina con código 0/1.
5. Si agota la espera, publica lo disponible, deja el run como `pending` (sin
   `Fin`) y el arranque siguiente reconcilia y cierra.
6. Al arrancar cada run normal, antes de lanzar los scrapers, reconcilia
   `pending` de fechas anteriores y cierra lo que ya no tenga locks vivos.

## 8. Cierre analizable y `_READY`

- El cierre replica el formato que `run_evidence` reconoce (`====\s*Fin
  pipeline`) y añade el resumen por fuente; los tests comprueban que el run
  pasa a seleccionable por el diagnóstico y que las evidencias por fuente
  siguen saliendo de los logs/manifests reales.
- `_READY` se escribe una sola vez por fecha de run, solo si la política se
  cumple con datos publicados; el contenido indica si fue normal o recuperado.
- Si el run original ya escribió `_READY`, la recuperación no lo cambia.

## 9. Retención y limpieza

- Mientras un run esté `pending`, sus ficheros y los de su fecha quedan
  excluidos de `cleanup_old_data.ps1`, que consulta el estado con
  `verification.recovery pending`.
- Retención configurable (por defecto 7 días) para pendientes no recuperables;
  al caducar se borran registrando el motivo. Los restos preservados del
  incidente se mantienen hasta la comprobación real.

## 10. Causa raíz

- Con la evidencia recogida se investigan las hipótesis (roam de Wi-Fi y
  `RunOnlyIfNetworkAvailable`, `StopOnIdleEnd`, agotamiento de puertos
  efímeros visto a las 02:10, fallo de PowerShell).
- Si la causa es corregible en la tarea o en el pipeline, se corrige; si no se
  puede determinar, se documenta el resultado de la investigación y la
  mitigación. Se propone habilitar el registro operativo del Programador de
  tareas (requiere permiso) para el siguiente aborto.

## 11. Estrategia de tests

- Unitarios `test_recovery.py`: detección de truncados (con y sin estado),
  plan por fuente, delta de LinkedIn sin nuevas, Multi-site sin snapshot,
  idempotencia (objetos ya publicados), política `_READY`, bloque de cierre.
- Integración offline: workspace temporal con logs/estado/restos del incidente
  simulado; se comprueba el plan, el cierre y los códigos de salida sin Azure.
- Regresión: `python -m pytest scrapers-pipeline/tests -q` y verificación de
  que el diagnóstico sigue verde (`test_run_evidence`, `test_verify_run`).
- La comprobación real usa los restos preservados del 2026-10-01 y el
  diagnóstico real.

## 12. Secuencia de implementación

1. Preservar y registrar la evidencia del run 2026-10-01 (hecho en parte).
2. Estado del run en el pipeline y transcripción del supervisor.
3. Módulo `verification.recovery` con tests.
4. Ejecutor de recuperación evolucionado con validación completa y cierre.
5. Supervisor y reconciliación al arrancar; retención.
6. Cambio de la tarea programada (con la persona).
7. Drill real con los restos preservados y diagnóstico.
8. Causa raíz: corregir o documentar.

## 13. Trazabilidad RF

| RF | Partes del plan que lo cubren |
|---|---|
| RF-1 | `Write-RunState` y `discover_truncated_runs` (secciones 4 y 5). |
| RF-2 | Retención y exclusión de limpieza (secciones 5 y 9). |
| RF-3 | Plan por fuente, validación completa y idempotencia (secciones 5 y 6). |
| RF-4 | Reconciliación al arrancar (sección 7). |
| RF-5 | `closing_block` y formato de `run_evidence` (sección 8). |
| RF-6 | `decide_ready` y escritura única (secciones 5 y 8). |
| RF-7 | Supervisor, transcripción y `abort.json` (secciones 4 y 7). |
| RF-8 | Investigación y corrección de causa (sección 10). |
| RF-9 | Flujo normal intacto y regresión (secciones 3, 6 y 11). |

## 14. Cumplimiento de la constitución

- **Stack simple:** sin dependencias nuevas; se reutilizan validadores,
  `verification` y AzCopy existentes (C#1).
- **Spec y código:** todo cambio corresponde a RF-1–RF-9 (C#2).
- **Lógica e interfaz:** la decisión vive en `verification/recovery.py`; los
  `.ps1` coordinan procesos e IO (C#3).
- **Tests:** módulo nuevo con tests offline y regresión del diagnóstico (C#4).
- **Persistencia:** los datos crudos y el histórico siguen en Azure; el estado
  del run es operativo y local, no una copia canónica (C#5).
- **Idioma:** identificadores y ficheros en inglés; mensajes en español (C#6).
