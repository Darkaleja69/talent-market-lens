# Especificación funcional: recuperación de runs abortados del pipeline

## Contexto y objetivo

El pipeline nocturno (`scrapers-pipeline/run_scrapers_and_upload.ps1`) lanza los
scrapers en paralelo, valida sus ficheros y los publica en la landing de Azure.
El 2026-10-01 el proceso principal murió a las 01:59:29 (`LastTaskResult`
0x8007042B) justo después de publicar LinkedIn: Indeed y Multi-site siguieron
ejecutándose como procesos huérfanos hasta las 02:22 y 04:50, pero nadie validó
ni subió sus datos, no hubo cierre del run ni `_READY`, Databricks no ingirió y
el diagnóstico de la 001 no puede analizar la ejecución. No quedó evidencia
directa de la causa (la tarea no guarda transcripción y el registro operativo
del Programador de tareas está deshabilitado; hay un *roam* de Wi-Fi a las
01:59:45 como pista, no como hecho).

Esta funcionalidad garantiza que un aborto del proceso principal no deje el run
sin publicar ni sin registrar: los datos ya generados se publican o quedan
recuperables con evidencia, el run se cierra de forma que el diagnóstico pueda
analizarlo y el aborto deja evidencia suficiente para corregir su causa.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería
  de datos de nivel junior.

## Historias de usuario

- Como responsable del proyecto, quiero que un aborto del pipeline no pierda
  los datos que los scrapers ya generaron.
- Como mantenedor, quiero saber que un run quedó truncado y qué quedó
  pendiente, sin tener que revisar los logs a mano.
- Como mantenedor, quiero recuperar y publicar lo pendiente sin duplicar lo ya
  publicado.
- Como mantenedor, quiero evidencia del aborto para corregir su causa.
- Como responsable de los datos, quiero que `_READY` (y la ingesta de
  Databricks) refleje solo lo realmente publicado.

## Requisitos funcionales

### RF-1 — Detectar y registrar un run truncado

**Criterio de aceptación (EARS):** Cuando el pipeline termine sin su cierre
normal, el sistema deberá persistir el estado del run (fecha, inicio, último
avance por fuente, subidas confirmadas y hora de la última actividad) y
marcarlo como truncado, aunque el proceso principal haya muerto.

### RF-2 — Conservar los datos generados

**Criterio de aceptación (EARS):** Cuando un run quede truncado, el sistema
deberá preservar los ficheros válidos generados por sus scrapers (incluidos los
que terminen después del aborto) hasta que se recuperen o caduquen según una
retención configurable, de modo que la limpieza periódica no los elimine antes.

### RF-3 — Publicar lo pendiente sin duplicar

**Criterio de aceptación (EARS):** Cuando exista un run truncado con datos
pendientes, el sistema deberá validarlos con el mismo contrato, coherencia y
huella que el pipeline, publicarlos con su manifest y registrar por fuente lo
publicado, lo omitido y lo rechazado; no deberá volver a publicar ofertas ya
confirmadas.

### RF-4 — Reconciliar arranques posteriores

**Criterio de aceptación (EARS):** Cuando el pipeline arranque, deberá
comprobar si algún run anterior quedó truncado sin recuperar y publicar sus
pendientes disponibles antes de dar por cerrado ese run anterior, sin
interferir con el run que empieza.

### RF-5 — Cerrar el run de forma analizable

**Criterio de aceptación (EARS):** Cuando un run truncado se recupere o se
declare irrecuperable, deberá quedar cerrado en su log general con el mismo
formato de fin que usa el pipeline y un resumen por fuente, de forma que el
diagnóstico de la 001 lo analice como un run finalizado.

### RF-6 — `_READY` fiel a lo publicado

**Criterio de aceptación (EARS):** El sistema solo deberá escribir
`_READY/dia=<fecha del run>` cuando la política configurada se cumpla con datos
realmente publicados (del run o de su recuperación); si no se escribe, deberá
quedar registrado el motivo.

### RF-7 — Evidencia del aborto

**Criterio de aceptación (EARS):** Cuando el proceso principal termine de forma
anómala, el sistema deberá dejar evidencia suficiente para diagnosticar la
causa (código de salida, transcripción de la consola, eventos del sistema
disponibles y último estado del run) sin depender de que la persona estuviera
delante.

### RF-8 — Causa raíz

**Criterio de aceptación (EARS):** Cuando exista evidencia de un aborto, el
sistema deberá identificar su causa raíz; si es corregible, se corregirá en el
pipeline, y si no puede determinarse, se dejará constancia de la mitigación
aplicada y de lo no averiguado.

### RF-9 — Sin cambios en el run normal

**Criterio de aceptación (EARS):** Mientras el run termine con normalidad, el
sistema deberá mantener el comportamiento actual (claves publicadas, manifests,
`_READY`, logs y códigos de salida) sin subidas ni procesos adicionales
perceptibles.

## Requisitos no funcionales

- Biblioteca estándar de Python y PowerShell 5.1; se reutilizan
  `ensure_compatible.py`, `filter_new_offers.py`, `coherence.py` y los helpers
  de `verification/`; no se añaden dependencias.
- Las decisiones (run truncado, pendientes por fuente, cierre y `_READY`)
  vivirán en un módulo Python comprobable; los `.ps1` solo coordinan procesos e
  IO (constitución 3).
- Tests unitarios y de integración offline, sin Azure ni credenciales.
- Mensajes a la persona en español; identificadores y ficheros de máquina en
  inglés.
- Sin credenciales ni SAS en logs, estados ni manifests.
- Idempotencia: recuperar dos veces no duplica objetos ni claves; la limpieza
  respeta la retención.

## Casos límite

- El proceso principal muere tras publicar algunas fuentes (p. ej. LinkedIn):
  solo se recuperan las pendientes.
- Los scrapers siguen vivos tras el aborto: la recuperación no publica hasta
  que sus procesos han terminado (o se cierra por espera acotada), y nunca
  mata procesos por su cuenta.
- El snapshot de Multi-site ya fue sobrescrito por un run posterior: se
  recupera lo que exista de forma fechada; si no queda nada recuperable, se
  registra y no se inventan datos.
- No hay SAS, red o AzCopy al recuperar: los pendientes quedan preservados y
  documentados; no se borran.
- El aborto ocurrió antes de que ningún scraper terminara: el run se cierra sin
  datos, con su motivo.
- La recuperación ya se ejecutó: no repite subidas ni registra claves dos veces.
- El run original ya escribió `_READY`: no se reescribe con otra semántica ni
  se duplica.
- La limpieza de datos antiguos coincide con pendientes: los pendientes no se
  borran.
- La causa raíz no es identificable con la evidencia disponible: se documenta y
  la mitigación evita la pérdida.

## Fuera de alcance

- Reparar los scrapers caídos (spec 004).
- Cambiar el diagnóstico de la 001 o sus umbrales; solo se escribe el cierre
  del log que el pipeline produce.
- Relanzar scrapers o repetir el run.
- Garantizar una causa raíz que la evidencia no permita determinar.
- Mover el pipeline a otro planificador o a la nube.
- Recuperar datos de runs anteriores al 2026-10-01: los históricos siguen
  cubiertos por la ejecución manual de recuperación.

## Criterios de finalización

- Un run normal no cambia su comportamiento y los tests del proyecto siguen en
  verde.
- Ante un aborto (controlado o real), el run queda marcado como truncado, sus
  pendientes se publican con manifest sin duplicados, el log se cierra de forma
  analizable y `_READY` respeta la política.
- El diagnóstico de la 001 analiza el run recuperado y clasifica su publicación.
- **Comprobación real:** con los restos preservados del run 2026-10-01 (Indeed
  y Multi-site, guardados en `%TEMP%\opencode\pending-recovery-2026-10-01`), el
  cierre/recuperación publica los pendientes con manifest, deja el run
  analizable y `python -m verification.verify_run` lo diagnostica con la
  publicación correcta; la persona valida la subida a Azure. Si los restos ya
  no sirvieran, se hará un aborto controlado equivalente con validación de la
  persona.

## Decisiones aclaradas

- La recuperación se integra en el ciclo nocturno (supervisor + reconciliación
  al arrancar) y también puede ejecutarse a mano en modo revisión (`-DryRun`).
- Se prefiere preservar y publicar los datos del run truncado antes que
  descartarlos; descartar requiere decisión de la persona.
- El cierre del run recuperado se escribe en el log del propio run para que el
  diagnóstico lo trate como finalizado.
- `_READY` se decide con datos publicados reales y la política existente
  (`any_valid`/`all`).
- La instrumentación (transcripción y estado) se añade al ciclo del pipeline,
  no al diagnóstico.
- No se matan procesos huérfanos por política: se espera de forma acotada a que
  terminen y se recuperan sus datos; el arranque siguiente reconcilia lo que
  quede pendiente.
- El supervisor acota su espera para no bloquear el run de la noche siguiente.
