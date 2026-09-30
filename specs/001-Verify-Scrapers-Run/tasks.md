# Tareas: diagnóstico de ejecuciones diarias

Las tareas están ordenadas por dependencia. Cada una está dimensionada para una
sesión de aproximadamente 15–25 minutos y debe completarse junto con su
comprobación indicada antes de continuar.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación
  (tests en verde), con mensaje convencional y referencia a la tarea
  (p. ej. `feat(verification): T-14 regla de progreso`).
- El commit local es un checkpoint; no se hace push sin revisión previa.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`) para mantener cada revisión manejable.
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec antes de
  continuar.
- Ninguna tarea se marca como hecha sin haber ejecutado su comprobación.

## 1. Catálogo y contrato de datos

- [x] **T-01 — Registrar las nueve fuentes independientes** (~20 min)
  - **RF:** RF-2.
  - **Hecho cuando:** el catálogo distingue Indeed, LinkedIn, InfoJobs y los seis portales Multi-site con los identificadores usados por el run.

- [x] **T-02 — Mapear campos e identificadores de Indeed, LinkedIn e InfoJobs** (~25 min)
  - **RF:** RF-2, RF-4, RF-5.
  - **Depende de:** T-01.
  - **Hecho cuando:** para cada una de las tres fuentes existe un mapa probado de título, empresa, descripción, salario, skills, modalidad, ubicación, fecha e identificador único.

- [x] **T-03 — Mapear los seis portales Multi-site** (~20 min)
  - **RF:** RF-2, RF-4, RF-5.
  - **Depende de:** T-01.
  - **Hecho cuando:** cada valor de `site` se asocia al portal correcto y sus campos canónicos se pueden medir independientemente.

- [x] **T-04 — Documentar reglas de validez por dato en el contrato** (~25 min)
  - **RF:** RF-3, RF-5.
  - **Depende de:** T-02, T-03.
  - **Hecho cuando:** el contrato describe valores válidos/ausentes para cada campo medido, incluidos modalidad, salario, skills y campos obligatorios, sin exigir longitudes mínimas de texto.

- [x] **T-05 — Probar mapas y reglas del contrato** (~20 min)
  - **RF:** RF-2, RF-5.
  - **Depende de:** T-04.
  - **Hecho cuando:** tests cubren los nueve identificadores de fuente, todos los alias definidos y ejemplos válidos e inválidos por campo.

## 2. Evidencia e identificación de ejecuciones

- [x] **T-06 — Leer el resumen del pipeline general** (~20 min)
  - **RF:** RF-1, RF-3, RF-14.
  - **Hecho cuando:** un fixture de log produce fecha, inicio, fin, estados, subidas, rechazos y errores sin inferir éxito por la mera existencia del log.

- [x] **T-07 — Extraer evidencia de ejecuciones de Indeed** (~20 min)
  - **RF:** RF-1, RF-2, RF-3.
  - **Depende de:** T-06.
  - **Hecho cuando:** un fixture permite distinguir ofertas parseadas en el run actual, error, salida vacía y snapshot antiguo.

- [x] **T-08 — Extraer evidencia de ejecuciones de LinkedIn** (~20 min)
  - **RF:** RF-1, RF-2, RF-3.
  - **Depende de:** T-06.
  - **Hecho cuando:** un fixture permite leer intento, resultado y contador actual, sin contar ofertas acumuladas de ejecuciones anteriores como nuevas del run.

- [x] **T-09 — Extraer evidencia de ejecuciones de InfoJobs** (~20 min)
  - **RF:** RF-1, RF-2, RF-3.
  - **Depende de:** T-06.
  - **Hecho cuando:** un fixture permite distinguir el resultado `RESULT`, cero ofertas, CAPTCHA/error y salida fechada del run actual.

- [x] **T-10 — Extraer estado independiente de cada portal Multi-site** (~25 min)
  - **RF:** RF-1, RF-2, RF-3, RF-14.
  - **Depende de:** T-06.
  - **Hecho cuando:** fixtures de `last_run.json` y logs producen seis estados separados, sin que el éxito del merge oculte un portal fallido.

- [x] **T-11 — Seleccionar la última ejecución con estado final** (~20 min)
  - **RF:** RF-1, RF-13.
  - **Depende de:** T-06–T-10.
  - **Hecho cuando:** se selecciona el último run finalizado aunque no haya generado ofertas; solo se devuelve inconcluso cuando no se puede identificar o leer un run. Si la ejecución más reciente aún no ha terminado, el diagnóstico queda inconcluso indicando que no puede verificar el proceso todavía.

- [x] **T-12 — Probar descubrimiento y casos sin evidencia** (~20 min)
  - **RF:** RF-1, RF-3, RF-13.
  - **Depende de:** T-11.
  - **Hecho cuando:** tests distinguen run finalizado sin ofertas (fuentes fallidas), log truncado, ejecuciones duplicadas del día y ausencia de run analizable.

## 3. Progreso y detención de procesos bloqueados

- [x] **T-13 — Definir el evento de progreso y umbral por fuente** (~20 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-01.
  - **Hecho cuando:** queda especificado un contador acumulativo de ofertas parseadas, incluye duplicados ya conocidos y un periodo configurable; el valor inicial es 40 minutos.

- [x] **T-14 — Implementar la regla pura de progreso/inactividad** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-13.
  - **Hecho cuando:** tests con reloj simulado confirman que un contador creciente mantiene el proceso y un contador quieto solo lo declara bloqueado al alcanzar el umbral.

- [x] **T-15 — Emitir progreso estructurado desde Indeed** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-13.
  - **Hecho cuando:** un test confirma que el contador avanza al parsear ofertas y no depende únicamente de actividad genérica del log.

- [x] **T-16 — Emitir progreso estructurado desde LinkedIn** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-13.
  - **Hecho cuando:** un test confirma progreso de tarjetas/ofertas procesadas incluso si ya existían en el snapshot acumulado.

- [x] **T-17 — Emitir progreso estructurado desde InfoJobs** (~20 min)
  - **RF:** RF-15.
  - **Depende de:** T-13.
  - **Hecho cuando:** un test confirma que el contador se actualiza al procesar resultados de búsqueda y conserva el total del run.

- [x] **T-18 — Emitir progreso por portal Multi-site** (~25 min)
  - **RF:** RF-2, RF-15.
  - **Depende de:** T-03, T-13.
  - **Hecho cuando:** los seis portales mantienen contadores independientes y sus fixtures demuestran que el avance de uno no se atribuye a los demás.

- [x] **T-19 — Supervisar el progreso de Indeed, LinkedIn e InfoJobs** (~25 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-14–T-17.
  - **Hecho cuando:** al superar el periodo de inactividad se detiene solo el árbol de procesos de la fuente bloqueada y queda registrado el motivo.

- [x] **T-20 — Supervisar cada proceso hijo de Multi-site** (~25 min)
  - **RF:** RF-2, RF-12, RF-15.
  - **Depende de:** T-14, T-18.
  - **Hecho cuando:** un test de integración detiene solo el portal sin progreso y confirma que los otros cinco siguen ejecutándose.

- [x] **T-21 — Evitar reintentos tras detención por watchdog en Indeed** (~20 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-19.
  - **Hecho cuando:** un test demuestra que una detención por falta de progreso termina ese run sin relanzarlo, mientras un error distinto conserva su política existente.

- [x] **T-22 — Evitar reintentos tras detención por watchdog en LinkedIn** (~20 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-19.
  - **Hecho cuando:** un test demuestra que la causa de bloqueo termina el run sin consumir otro intento.

- [x] **T-23 — Evitar reintentos tras detención por watchdog en InfoJobs** (~20 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-19.
  - **Hecho cuando:** un test demuestra que la causa de bloqueo termina el run sin consumir otro intento.

- [x] **T-24 — Probar límites e aislamiento del watchdog** (~25 min)
  - **RF:** RF-12, RF-15.
  - **Depende de:** T-19–T-23.
  - **Hecho cuando:** tests cubren contador creciente, límite justo antes/en/después del timeout y detención de un único portal Multi-site.

## 4. Contrato, completitud y estado de fuentes

- [x] **T-25 — Validar legibilidad y estructura de los Parquet obtenidos** (~20 min)
  - **RF:** RF-3, RF-5.
  - **Depende de:** T-04.
  - **Hecho cuando:** fixtures cubren Parquet legible, corrupto y sin columnas obligatorias; los dos últimos se clasifican como incumplimiento estructural.

- [x] **T-26 — Deduplicar por clave de fuente** (~20 min)
  - **RF:** RF-4, RF-5.
  - **Depende de:** T-02, T-03.
  - **Hecho cuando:** tests demuestran que `job_key`, `job_id` e `id_oferta` producen el total de ofertas únicas esperado por fuente.

- [x] **T-27 — Calcular validez y completitud por campo** (~25 min)
  - **RF:** RF-4, RF-5.
  - **Depende de:** T-05, T-25, T-26.
  - **Hecho cuando:** un fixture produce numerador, denominador, porcentaje y conteos de válidos/ausentes/inválidos para todos los campos mínimos.

- [x] **T-28 — Aplicar umbrales y estados por fuente** (~20 min)
  - **RF:** RF-3, RF-10, RF-14.
  - **Depende de:** T-27.
  - **Hecho cuando:** tests verifican obligatorio 89.9/90/99/100 %, opcional 60/60.1 %, cero ofertas y falta de evidencia según los estados especificados.

## 5. Publicación y tendencias

- [x] **T-29 — Crear adaptador de lectura remota para Azure** (~25 min)
  - **RF:** RF-6, RF-8, RF-13.
  - **Depende de:** T-06.
  - **Hecho cuando:** el adaptador permite listar y descargar a temporales mediante el AzCopy existente, y elimina temporales al cerrar la operación.

- [x] **T-30 — Verificar manifests y objetos publicados** (~25 min)
  - **RF:** RF-6, RF-8.
  - **Depende de:** T-29.
  - **Hecho cuando:** tests con Azure simulado detectan objeto ausente, legibilidad, discrepancia de filas/checksum y manifest rechazado.

- [x] **T-31 — Distinguir snapshot, delta y publicación pendiente** (~25 min)
  - **RF:** RF-3, RF-6, RF-8.
  - **Depende de:** T-27, T-30.
  - **Hecho cuando:** un snapshot no vacío con delta `OnlyNewOffers` vacío no se clasifica como cero ofertas capturadas y los objetos aún no visibles se informan como pendientes.

- [x] **T-32 — Añadir huella de fuentes y búsquedas al manifest existente** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-01, T-06.
  - **Hecho cuando:** tests comprueban que la huella cambia al cambiar fuentes/búsquedas y no contiene credenciales.

- [x] **T-33 — Calcular tendencia de hasta cinco ejecuciones comparables** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-29, T-32.
  - **Hecho cuando:** fixtures de Azure producen tendencia por fuente/campo usando solo huellas coincidentes y señalan cuántas ejecuciones se compararon.

- [x] **T-34 — Cubrir histórico insuficiente y cambios de configuración** (~20 min)
  - **RF:** RF-7, RF-13.
  - **Depende de:** T-33.
  - **Hecho cuando:** tests con cero a cuatro ejecuciones y manifests sin huella no se presentan como series comparables falsas.

## 6. Investigación, informe y CLI

- [x] **T-35 — Preparar el contexto para investigar una anomalía** (~20 min)
  - **RF:** RF-9, RF-10, RF-11.
  - **Depende de:** T-07–T-10, T-27, T-28.
  - **Hecho cuando:** para una fuente/umbral afectado se genera fuente, búsqueda, región, URL de ejemplo, campo, métrica y evidencia local.

- [x] **T-36 — Representar una investigación web no confirmada** (~20 min)
  - **RF:** RF-9, RF-10, RF-11.
  - **Depende de:** T-35.
  - **Hecho cuando:** tests muestran que una web no accesible queda como investigación inconclusa, sin cambiar el estado de ingestión ni afirmar una causa.

- [x] **T-37 — Clasificar estados globales** (~20 min)
  - **RF:** RF-13, RF-14.
  - **Depende de:** T-11, T-28, T-30.
  - **Hecho cuando:** tests verifican correcto, parcial, fallido e inconcluso, incluidos todos los fallidos y run no identificable.

- [x] **T-38 — Presentar completitud y estado por fuente en español** (~25 min)
  - **RF:** RF-2, RF-4, RF-6, RF-7, RF-14, RF-15.
  - **Depende de:** T-27, T-30, T-33, T-37.
  - **Hecho cuando:** fixtures generan un informe por fuente/portal con recuentos, porcentajes, tendencia, estado y evidencia de progreso o detención.

- [x] **T-39 — Presentar investigación y recomendaciones sin mezclar hechos e hipótesis** (~20 min)
  - **RF:** RF-9, RF-10, RF-11.
  - **Depende de:** T-35, T-36, T-38.
  - **Hecho cuando:** un informe de muestra separa observaciones, causa probable, recomendación y comprobación manual.

- [x] **T-40 — Conectar la CLI al flujo de diagnóstico** (~25 min)
  - **RF:** RF-1, RF-11–RF-14.
  - **Depende de:** T-11, T-30, T-33, T-38, T-39.
  - **Hecho cuando:** una prueba de integración analiza el último run finalizado, emite el informe y no ejecuta scrapers ni modifica datos/configuración/publicación.

## 7. Integración y verificación final

- [x] **T-41 — Probar integración de evidencias y calidad por fuente** (~25 min)
  - **RF:** RF-1–RF-5, RF-13, RF-14.
  - **Depende de:** T-12, T-25–T-28, T-37.
  - **Hecho cuando:** un fixture de las nueve fuentes verifica selección del run, parseo, campos, duplicados, umbrales y estados.

- [x] **T-42 — Probar integración de Azure, delta y tendencias** (~25 min)
  - **RF:** RF-6–RF-8, RF-13.
  - **Depende de:** T-30–T-34.
  - **Hecho cuando:** una landing simulada prueba objetos válidos, pendientes, rechazados, delta vacío y cinco ejecuciones comparables sin acceso real a Azure.

- [x] **T-43 — Probar integración del bloqueo de un portal** (~25 min)
  - **RF:** RF-3, RF-12, RF-14, RF-15.
  - **Depende de:** T-24, T-37.
  - **Hecho cuando:** un proceso simulado con contador creciente no se detiene y uno estancado se detiene sin relanzarse ni detener los demás portales.

- [x] **T-44 — Documentar el comando de tests del diagnóstico en AGENTS.md** (~15 min)
  - **RF:** RF-1–RF-15 (verificación del alcance).
  - **Depende de:** T-40.
  - **Hecho cuando:** AGENTS.md incluye el comando reproducible de la suite nueva y su directorio de ejecución.

- [x] **T-45 — Ejecutar la suite del diagnóstico** (~20 min)
  - **RF:** RF-1–RF-15.
  - **Depende de:** T-41–T-44.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` termina correctamente.

- [x] **T-46 — Ejecutar regresión de Indeed y LinkedIn** (~25 min)
  - **RF:** RF-1–RF-5, RF-12, RF-15.
  - **Depende de:** T-15, T-16, T-21, T-22, T-45.
  - **Hecho cuando:** pasan las suites de Indeed y LinkedIn tras los cambios de progreso/watchdog.

- [x] **T-47 — Ejecutar regresión de InfoJobs** (~20 min)
  - **RF:** RF-1–RF-5, RF-12, RF-15.
  - **Depende de:** T-17, T-23, T-45.
  - **Hecho cuando:** pasan pytest, Ruff y mypy de InfoJobs.

- [x] **T-48 — Ejecutar regresión de Multi-site** (~25 min)
  - **RF:** RF-2–RF-5, RF-12, RF-15.
  - **Depende de:** T-18, T-20, T-45.
  - **Hecho cuando:** pasa la suite de Multi-site y los tests cubren los seis portales independientes.

- [x] **T-49 — Validar una investigación real cuando se active un umbral** (~25 min; condicional)
  - **RF:** RF-9–RF-11.
  - **Depende de:** T-39, T-45.
  - **Hecho cuando:** ante un caso real disponible, se contrasta una oferta/campo con su web y el informe separa evidencia, hipótesis y resultado; si no es verificable, queda declarado como tal.

## 8. Comprobación remota real (reapertura)

La ejecución del diagnóstico contra la landing real del run 2026-09-29 reveló
que la comprobación remota no funciona: AzCopy 10.32.4 lista nombres cortos (no
claves del contenedor) y los manifests escritos por PowerShell 5.1 llevan BOM
UTF-8. Los tests no lo detectaron porque sus dobles devuelven claves completas
y JSON sin BOM. Se corrige dentro de esta misma spec.

- [x] **T-50 — Usar claves completas al listar objetos remotos** (~25 min)
  - **RF:** RF-6, RF-7, RF-8.
  - **Hecho cuando:** `AzCopyReader.list_objects` prefija los nombres cortos que
    devuelve AzCopy 10.32.4 con el prefijo pedido; `list_manifest_keys`,
    `verify_run._latest_manifest` y `trends.select_history` descargan con la
    clave relativa al contenedor; tests reproducen la salida real y los que
    asumían nombres cortos quedan actualizados.

- [x] **T-51 — Leer manifests tolerando el BOM UTF-8** (~20 min)
  - **RF:** RF-7, RF-8.
  - **Depende de:** T-50.
  - **Hecho cuando:** `landing.load_manifest` parsea manifests con y sin BOM
    (`utf-8-sig`) y sigue devolviendo `None` con JSON inválido.

- [x] **T-52 — Escribir los manifests sin BOM en los wrappers PS** (~20 min)
  - **RF:** RF-8.
  - **Hecho cuando:** `run_scrapers_and_upload.ps1` y `recover_and_upload.ps1`
    escriben los manifests con UTF-8 sin BOM (PS 5.1) y el lector sigue
    tolerando los manifests históricos con BOM.

- [x] **T-53 — Comprobar el diagnóstico contra la landing real** (~25 min)
  - **RF:** RF-6, RF-7, RF-8, RF-13.
  - **Depende de:** T-55, T-57, T-58, T-59, T-60, T-61.
  - **Hecho cuando:** `python -m verification.verify_run` sobre el último run
    finalizado carga el manifest publicado por el run, clasifica la publicación
    de cada fuente (sin degradarla a "no comprobada" ni a "pendiente" por
    errores de lectura), calcula la tendencia con las ejecuciones comparables
    disponibles y deja el fichero JSON de RF-16; el resultado queda registrado
    como evidencia.
  - **Evidencia (2026-09-30, run 2026-09-30):** publicación `correcta` en
    Indeed (delta 200), LinkedIn (delta 186) e IrishJobs (delta 5); InfoJobs
    `sin datos que publicar`; tendencia con 2 ejecuciones comparables (Indeed);
    fichero en `scrapers-pipeline/logs/diagnostic_last.json` (UTF-8 sin BOM).

- [x] **T-54 — Ejecutar la suite del diagnóstico** (~15 min)
  - **RF:** RF-1–RF-15.
  - **Depende de:** T-53.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa.
  - **Evidencia (2026-09-30):** 634 passed.

- [x] **T-55 — Acotar el manifest analizado al run** (~25 min)
  - **RF:** RF-1, RF-6, RF-8.
  - **Depende de:** T-50.
  - **Hecho cuando:** solo se usa como ancla de publicación y tendencia un
    manifest cuyo stamp cae dentro de la ventana del run analizado; un manifest
    de otra ejecución no se presenta como si fuera del run (caso InfoJobs
    2026-09-11 en el run 2026-09-30). Si el run no dejó manifest y la fuente no
    preparó datos, la publicación se informa como "sin datos que publicar" (no
    como pendiente) y no hay ancla de tendencia; tests cubren ambos casos.

- [x] **T-56 — Alinear la clave publicada con el manifest** (~20 min)
  - **RF:** RF-6, RF-8.
  - **Hecho cuando:** la subida de `run_scrapers_and_upload.ps1` no añade la
    carpeta de staging (`azcopy copy ... --as-subdir=false`), la clave real
    queda `dia=YYYY-MM-DD/<fichero>` como declara `remote`, y una comprobación
    local con AzCopy demuestra que no se crea subcarpeta.
    `recover_and_upload.ps1` sube ficheros sueltos y no cambia.
  - **Evidencia (2026-09-30):** AzCopy 10.32.4 real resuelve clave plana
    `dia=.../<fichero>` con `--as-subdir=false` (y anidada con el modo por
    defecto); confirmación end-to-end pendiente en la próxima subida real del
    pipeline.

- [x] **T-57 — Resolver la clave publicada real** (~25 min)
  - **RF:** RF-6, RF-7, RF-8.
  - **Hecho cuando:** `landing.verify_manifest` y
    `trends.load_published_completeness` localizan el objeto por clave exacta y,
    si no existe, por nombre de fichero único bajo el prefijo publicado
    (manifests históricos con carpeta de staging intermedia); si hay ambigüedad
    no se adivina; tests cubren clave exacta, anidada única, ambigua y ausente.

- [x] **T-58 — Informar la causa real si la tendencia no se puede calcular** (~20 min)
  - **RF:** RF-7, RF-13.
  - **Hecho cuando:** `_build_trends` no silencia `RemoteError`: el informe
    indica qué fuente no pudo medirse y por qué, en vez de afirmar que no hay
    ejecuciones comparables; tests cubren el fallo de lectura y la ausencia
    real de histórico.

## 9. Resultado legible por máquina (RF-16)

- [x] **T-59 — Serializar el informe a un diccionario legible por máquina** (~25 min)
  - **RF:** RF-16.
  - **Hecho cuando:** `report.py` expone una función pura que convierte
    `DiagnosticReport` en un diccionario con `schema_version`, `generated_at`,
    `run`, `global_status`, `sources` (id, tipo, estado, resultado, ofertas,
    completitud por campo con válidos/total/porcentaje/obligatorio, publicación
    y motivos, evidencias), `trend` e investigaciones; claves e identificadores
    en inglés, sin credenciales; tests de estructura y de estados
    (correcto/parcial/fallido/inconcluso y `not_applicable`).

- [x] **T-60 — Escribir el fichero de resultado desde la CLI** (~25 min)
  - **RF:** RF-16.
  - **Depende de:** T-59.
  - **Hecho cuando:** `python -m verification.verify_run` escribe por defecto
    `scrapers-pipeline/logs/diagnostic_last.json` (UTF-8 sin BOM, escritura
    atómica, se sobrescribe) también cuando el diagnóstico es inconcluso, con
    `--output` para elegir ruta; el informe en pantalla no cambia y se anuncia
    la ruta; tests con `tmp_path` cubren escritura, sobrescritura, inconcluso y
    `--output`.

- [x] **T-61 — Medir solo las ejecuciones comparables de la tendencia** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-57, T-58.
  - **Hecho cuando:** la tendencia no descarga los objetos publicados de todo
    el histórico: primero selecciona las ejecuciones por huella y solo mide la
    actual y hasta cinco comparables; la ejecución real del diagnóstico sobre
    la landing termina en un tiempo razonable; tests demuestran que los
    manifiestos no seleccionados no disparan descargas de objetos publicados.
    (Defecto real: la comprobación del run 2026-09-30 se quedó colgada más de
    15 minutos midiendo 18–25 manifests por scraper.)
