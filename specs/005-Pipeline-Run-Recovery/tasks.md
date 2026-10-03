# Tareas: recuperación de runs abortados del pipeline

Las tareas están ordenadas por dependencia. Cada una está dimensionada para una
sesión de aproximadamente 15–25 minutos y debe completarse junto con su
comprobación indicada antes de continuar.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación
  (tests en verde), con mensaje convencional y referencia a la tarea
  (p. ej. `feat(recovery): T-05 detectar runs truncados`).
- El commit local es un checkpoint; no se hace push sin revisión previa.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`) para mantener cada revisión manejable.
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec antes de
  continuar.
- Ninguna tarea se marca como hecha sin haber ejecutado su comprobación.

## 1. Evidencia del incidente y estado del run

- [x] **T-01 — Preservar e inventariar los restos del run 2026-10-01** (~20 min)
  - **RF:** RF-2, RF-7.
  - **Hecho cuando:** existe una copia íntegra de los artefactos del run
    truncado (Indeed `indeed_jobs_20261001_0001.parquet`, Multi-site
    `jobs_unified.parquet`, `last_run.json` y `irishjobs/output/jobs.parquet`)
    en `%TEMP%\opencode\pending-recovery-2026-10-01` con sus tamaños y hashes
    registrados; la copia es la referencia de la comprobación real.

- [x] **T-02 — Investigar la evidencia disponible del aborto** (~25 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-01.
  - **Hecho cuando:** `specs/005-Pipeline-Run-Recovery/evidence-2026-10-01.md`
    recoge los hechos comprobados (log truncado, `LastTaskResult` 0x8007042B,
    ausencia de WER, registro del Programador deshabilitado, *roam* de Wi-Fi a
    las 01:59:45, puertos efímeros agotados a las 02:10), las hipótesis
    (condición de red de la tarea, `StopOnIdleEnd`) y su estado
    confirmado/descartado; separa hechos de hipótesis.

- [x] **T-03 — Definir el estado del run en disco** (~25 min)
  - **RF:** RF-1.
  - **Hecho cuando:** existe el esquema documentado de
    `logs/run_state/<fecha>.json` (claves en inglés: `run_date`, `started_at`,
    `finished_at`, `sources` con `status`, `uploaded`, `rejected`,
    `last_activity_at`; estado `pending`/`closed`) y una función de escritura
    atómica disponible para los `.ps1`.

- [x] **T-04 — Persistir el estado desde el pipeline en hitos** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-03.
  - **Hecho cuando:** `run_scrapers_and_upload.ps1` escribe el estado al
    inicio, tras cada resultado de fuente y al cierre, sin alterar el flujo
    normal; un test verifica que un log truncado puede asociarse a su estado
    `pending`.

## 2. Decisión (módulo Python)

- [x] **T-05 — Detectar runs truncados reutilizando `run_evidence`** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-03.
  - **Hecho cuando:** `verification/recovery.py` expone
    `discover_truncated_runs` (run con `Inicio` sin `Fin` y/o estado `pending`,
    excluyendo cerrados) y los tests cubren: truncado con y sin estado, run
    cerrado normal, sin logs y varios runs el mismo día.

- [x] **T-06 — Construir el plan de recuperación por fuente** (~25 min)
  - **RF:** RF-2, RF-3.
  - **Depende de:** T-05.
  - **Hecho cuando:** `build_plan` devuelve, por fuente, los pendientes con su
    día real (Indeed/InfoJobs por nombre fechado más reciente; LinkedIn por
    delta contra `uploaded_keys`, omitiendo si no hay nuevas; Multi-site por
    `jobs_unified*` fechado o reconstrucción con `merge.py`) e incluye
    `required_cols`, `coherence`, `fingerprint_source` y `key_column`; tests
    cubren cada fuente, ausencia de restos y Multi-site sobrescrito.

- [x] **T-07 — Garantizar idempotencia del plan** (~25 min)
  - **RF:** RF-3.
  - **Depende de:** T-06.
  - **Hecho cuando:** el plan omite lo ya publicado (objeto remoto coincidente
    vía `verification.landing` o claves ya en `uploaded_keys`) y su ejecución
    repetida no cambia subidas; tests con lector falso y con claves repetidas.

- [x] **T-08 — Decidir `_READY` y generar el cierre analizable** (~25 min)
  - **RF:** RF-5, RF-6.
  - **Depende de:** T-06.
  - **Hecho cuando:** `decide_ready` aplica `any_valid`/`all` a los datos
    publicados reales con motivo cuando no procede, y `closing_block` produce
    el formato que `run_evidence` reconoce; un test demuestra que el run con
    cierre pasa a seleccionable por el diagnóstico.

## 3. Supervisor, ejecutor y ciclo

- [x] **T-09 — Supervisar el pipeline con transcripción y evidencia** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-04.
  - **Hecho cuando:** `run_pipeline_supervised.ps1` lanza el pipeline como
    hijo con `-RedirectStandardOutput/Error` a `logs/transcript-<fecha>.log`,
    captura el código de salida y, ante un cierre sin `Fin pipeline`, escribe
    `run_state/<fecha>.abort.json` con evidencia; una prueba local con un hijo
    que sale sin cerrar demuestra la detección sin tocar Azure.

- [x] **T-10 — Esperar de forma acotada y reconciliar al arrancar** (~25 min)
  - **RF:** RF-2, RF-4.
  - **Depende de:** T-09.
  - **Hecho cuando:** el supervisor espera (config por defecto 6 h) a que
    desaparezcan los locks de los wrappers de la fecha y, si no lo consigue,
    deja el run `pending`; el arranque del pipeline reconcilia pendientes
    anteriores antes de lanzar los scrapers; tests de decisión cubren ambos
    caminos.

- [x] **T-11 — Evolucionar el ejecutor de recuperación** (~25 min)
  - **RF:** RF-2, RF-3.
  - **Depende de:** T-06, T-07.
  - **Hecho cuando:** `recover_and_upload.ps1` acepta `-Date`/`-PlanJson`,
    valida con `--coherence`/`--fingerprint-source`, filtra `OnlyNewOffers`,
    sube con `--as-subdir=false`, escribe manifest con `remote` sin BOM,
    actualiza `uploaded_keys` solo tras éxito, registra omitidos/rechazados y
    devuelve código distinto de cero si algo falla; sigue funcionando sin
    `-Date` para históricos.

- [x] **T-12 — Cerrar el log y escribir `_READY` fiel** (~25 min)
  - **RF:** RF-5, RF-6.
  - **Depende de:** T-08, T-11.
  - **Hecho cuando:** el ejecutor añade a `logs/upload-<run_date>.log` el
    bloque de cierre con el resumen por fuente y solo escribe
    `_READY/dia=<run_date>` si la política se cumple con datos publicados
    (registrando el motivo si no); no reescribe `_READY` ya existente.

- [x] **T-13 — Respetar los pendientes en la limpieza** (~20 min)
  - **RF:** RF-2.
  - **Depende de:** T-05.
  - **Hecho cuando:** `cleanup_old_data.ps1` excluye los ficheros de runs
    `pending` y aplica una retención configurable (por defecto 7 días) al
    resto, registrando lo que caduca; un test de decisión cubre ambos casos.

- [x] **T-14 — Llevar el ciclo a la tarea programada** (~15 min)
  - **RF:** RF-4, RF-7.
  - **Depende de:** T-09, T-10.
  - **Hecho cuando:** la acción de `Scrapers_Daily_Upload` ejecuta
    `run_pipeline_supervised.ps1` manteniendo horario, condiciones y límites, y
    la persona lo valida; una ejecución de comprobación registra la
    transcripción.
  - **Evidencia (2026-10-03):** acción cambiada a `run_pipeline_supervised.ps1`
    (solo cambió la acción respecto del XML original; backup en
    `%TEMP%\opencode\task-backup`); ejecución de comprobación 12:32:55–14:34:25
    con `====  Fin pipeline` y transcripción
    `logs/transcript-2026-10-03.log`; estado `closed` sin `abort.json`;
    verificación independiente con suite completa en verde (752 passed); la
    persona valida el cambio.

- [x] **T-15 — Probar la integración offline del ciclo** (~25 min)
  - **RF:** RF-1–RF-6.
  - **Depende de:** T-10–T-13.
  - **Hecho cuando:** un fixture temporal con logs truncados, estado y restos
    de las cuatro fuentes produce el plan, publica en una landing simulada sin
    duplicar, cierra el run y decide `_READY`; sin Azure ni credenciales.

## 4. Verificación y cierre

- [x] **T-16 — Ejecutar la suite y la regresión del diagnóstico** (~25 min)
  - **RF:** RF-1–RF-9.
  - **Depende de:** T-15.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa (sin
    regresiones en `run_evidence`, `verify_run` y watchdog) y las suites de los
    scrapers no cambian.
  - **Evidencia (2026-10-03):** 747 passed; flake de `test_verify_run.py` no
    reproducido en 10/10 pasadas; scrapers 51/31/104/92.

- [x] **T-16A — Sellar el manifest de recuperación con la fecha del run** (~25 min)
  - **RF:** RF-3, RF-5, RF-6.
  - **Depende de:** T-11, T-12, T-16.
  - **Hecho cuando:** los manifests que sube la recuperación planificada se
    sellan con la fecha del run y un instante dentro de su ventana (fecha del
    run + `max(hora de inicio del log, hora de ejecución)`), de modo que el
    diagnóstico los ancle como publicación del run recuperado aunque la
    recuperación sea días después; un test de recuperación tardía demuestra que
    `run_diagnostic` clasifica la publicación como correcta (antes: pendiente)
    y la suite del pipeline queda en verde.

- [x] **T-17 — Comprobación real con el run 2026-10-01** (~25 min)
  - **RF:** RF-3, RF-5, RF-6.
  - **Depende de:** T-15, T-16, T-16A.
  - **Hecho cuando:** con los restos preservados (T-01) y validación de la
    persona, la recuperación publica Indeed y Multi-site con manifest, deja el
    run analizable y `python -m verification.verify_run` lo diagnostica con la
    publicación correcta y tendencia; el resultado queda registrado como
    evidencia.
  - Si los restos ya no sirvieran: aborto controlado equivalente con una
    fuente acotada, con la persona validando la subida.
  - **Evidencia (2026-10-03):** ver `recovery-2026-10-01.md`; recuperación
    exit 0, Indeed (67 filas) y Multi-site (6 nuevas) con manifest, cierre del
    log y `_READY` `RECOVERED`; el diagnóstico analiza el run y clasifica la
    publicación como correcta (tendencia no comparable por huellas).

- [x] **T-18 — Tratar la causa raíz** (~25 min)
  - **RF:** RF-8.
  - **Depende de:** T-02, T-14.
  - **Hecho cuando:** con la evidencia disponible y la instrumentación activa,
    se corrige la causa si es identificable (p. ej. condición de red de la
    tarea) o se documenta en `evidence-2026-10-01.md` la mitigación y lo no
    averiguado.
  - **Evidencia (2026-10-03):** `RunOnlyIfNetworkAvailable=false` y
    `StopOnIdleEnd=false` aplicados a `Scrapers_Daily_Upload` (solo esos dos
    ajustes; backup del XML en `%TEMP%\opencode\task-backup`); la tarea no
    requirió elevación; el registro operativo del Programador quedó habilitado
    con consola elevada y verificado (`IsEnabled=True`); la sección 8 de
    `evidence-2026-10-01.md` documenta la
    mitigación y lo no averiguado (H1 sin confirmar, H2 neutralizada); suite
    761 passed; verificación independiente PASS.

- [x] **T-19 — Documentar el proceso en AGENTS.md** (~15 min)
  - **RF:** RF-1–RF-9 (verificación del alcance).
  - **Depende de:** T-14, T-17.
  - **Hecho cuando:** AGENTS.md incluye el comando de recuperación (`-DryRun` y
    real), el estado del run y el comando de tests, con el visto bueno de la
    persona.
  - **Evidencia (2026-10-03):** AGENTS.md documenta el ciclo supervisado, la
    recuperación manual (`-DryRun`/real/`-PlanJson`), el estado del run y el
    comando de tests; verificación independiente PASS; la persona revisó el
    diff, aportó su propio ajuste y dio el visto bueno.

## 5. Correcciones de la comprobación real (2026-10-03)

La ejecución de comprobación de T-14 destapó dos defectos de esta misma spec:
la reconciliación al arrancar recuperó 18 fechas históricas fuera de alcance y
re-subió tres veces el run del 2026-07-23 porque el plan no consultó la
landing. Se corrigen dentro de esta misma spec (AGENTS.md).

- [x] **T-20 — Idempotencia real en el plan de recuperación** (~25 min)
  - **RF:** RF-3.
  - **Depende de:** T-07, T-11.
  - **Hallazgo:** el plan que usa la recuperación automática no inyecta el
    lector remoto de `verification.landing`, así que `published_key` queda nulo
    y lo ya publicado no se omite (2026-07-23: plan idéntico y mismas subidas
    tres veces).
  - **Hecho cuando:** el plan de recuperación que consumen la reconciliación y
    el ejecutor consulta la landing (`verification.landing`) y omite lo ya
    publicado; un test de doble recuperación demuestra que la segunda no vuelve
    a subir ni duplica manifests; la suite del pipeline sigue en verde.
  - **Evidencia (2026-10-03):** CLI `plan --check-landing` construye el lector
    real (`landing.AzCopyReader`; SAS solo desde el entorno, nunca en argv ni
    logs) y omite lo publicado con `published_key`; `recover_and_upload.ps1` lo
    usa en reconciliación y supervisor; sin SAS/red falla cerrado y el run
    queda `pending`; test e2e de doble recuperación sin subidas nuevas ni
    manifests duplicados; suite 757 passed; verificación independiente PASS.

- [x] **T-21 — Acotar la reconciliación a los runs recuperables** (~25 min)
  - **RF:** RF-2, RF-4.
  - **Depende de:** T-05, T-10.
  - **Hallazgo:** el arranque supervisado del 2026-10-03 recuperó 2026-07-22…
    2026-09-25 (18 fechas, 27 subidas, `_READY` históricos) pese a que la spec
    deja los históricos fuera de alcance; `pending --before 2026-10-03` sigue
    devolviendo 3 runs ya recuperados/`superseded`.
  - **Hecho cuando:** la reconciliación no recupera runs anteriores al
    2026-10-01 ni runs ya cerrados/`superseded`, lo registra con motivo, y un
    test cubre el límite inferior y el filtro; el comando `pending` deja de
    listar lo ya recuperado.
  - **Evidencia (2026-10-03):** constante única `AUTO_RECOVERY_MIN_DATE`
    (2026-10-01) y exclusión de `superseded` en `_pending_selection`; reconcile
    registra los excluidos con motivo y no los invoca; `pending --before
    2026-10-03` pasa de 3 runs a 0 con 3 `skipped`; la limpieza (T-13) puede
    caducar lo ya recuperado; suite 761 passed; verificación independiente PASS.
