# Plan técnico: diagnóstico de ejecuciones diarias

## 1. Objetivo y límites

Implementar una verificación que, tras una ejecución diaria, reúna la evidencia
local del run y la publicación en Azure, mida la calidad de las ofertas y
presente un informe en español. Durante la ejecución, un supervisor observará el
progreso de cada scraper y detendrá únicamente los procesos bloqueados según
RF-15.

La verificación no vuelve a ejecutar scrapers, no reintenta procesos detenidos,
no cambia datos ni escribe en la landing. Las lecturas remotas se descargarán a
un directorio temporal y se eliminarán al terminar. La única acción sobre un
proceso es la detención autorizada por RF-15.

La unidad de estado es cada fuente independiente: Indeed, LinkedIn, InfoJobs,
IrishJobs, StepStone NL, DevITjobs, NVB, Jobs.ch y Glassdoor. Multi-site es el
grupo que ejecuta los seis últimos portales; sus resultados se evalúan por
separado.

## 2. Estructura de módulos propuesta

La lógica de negocio residirá en módulos Python comprobables. La CLI y los
wrappers PowerShell coordinarán argumentos, procesos y presentación, sin
contener reglas de calidad.

| Módulo | Responsabilidad | RF cubiertos |
|---|---|---|
| `sources.py` | Catálogo de las nueve fuentes, alias de campos por origen, clave de deduplicación, dimensiones de búsqueda y ubicación de sus evidencias. | RF-2, RF-4, RF-5 |
| `run_evidence.py` | Seleccionar el último run completado; leer el log general, logs de wrappers, resultados por sub-scraper y manifests; distinguir registros ausentes de una ejecución analizable. | RF-1, RF-2, RF-3, RF-13 |
| `progress.py` | Interpretar contadores acumulativos de ofertas capturadas, medir inactividad por fuente y decidir mantener o detener el proceso. Funciones puras para que los umbrales sean testeables. | RF-12, RF-15 |
| `field_contract.py` | Relacionar campos canónicos con los nombres usados por cada fuente y aplicar el contrato del proyecto y los validadores de coherencia existentes. | RF-3, RF-5 |
| `completeness.py` | Deduplicar ofertas por la clave propia de cada fuente y calcular numeradores, denominadores y porcentajes en las etapas obtenida y publicada. | RF-3, RF-4, RF-5, RF-6 |
| `landing.py` | Consultar manifests y objetos remotos en modo lectura; descargar temporalmente los objetos necesarios y comprobar existencia, lectura, filas y checksum. | RF-6, RF-8 |
| `trends.py` | Encontrar las últimas cinco ejecuciones comparables conservadas en Azure y calcular la evolución de completitud publicada. | RF-7 |
| `investigation.py` | Preparar la revisión de la web real con fuente, búsqueda, región, ofertas de ejemplo, campo incompleto y evidencia del run. Se invoca únicamente por fallo o umbral de completitud. | RF-9, RF-10, RF-11 |
| `status.py` | Aplicar umbrales por fuente y campo y obtener el estado de fuente y resultado global. | RF-3, RF-10, RF-14, RF-15 |
| `report.py` | Construir el informe en español, distinguiendo hechos, hipótesis, proceso detenido, incidencias, pendiente de publicación y datos no verificables. | RF-11, RF-13, RF-14, RF-15 |
| `verify_run.py` | CLI delgada que coordina descubrimiento, mediciones, verificaciones y reporte; no ejecuta scrapers. | RF-1–RF-15 |

Los módulos Python se situarán en `scrapers-pipeline/verification/` y sus tests
en `scrapers-pipeline/tests/`. El supervisor actual seguirá siendo responsable
de iniciar procesos y manejar sus identificadores; delegará las decisiones de
progreso en `progress.py`. El supervisor Multi-site aplicará el mismo criterio a
cada proceso hijo de forma independiente.

## 3. Evidencia y verificación por fuente

### 3.1 Evidencia común del run

1. Seleccionar el último run general que haya llegado a su registro final,
   incluso si ninguna fuente produjo datos. Un run finalizado sin datos es
   analizable y sus fuentes se clasifican según RF-3/RF-14; solo la ausencia de
   evidencia suficiente para identificar o leer un run produce un resultado
   inconcluso (RF-1, RF-3, RF-13).
2. Interpretar `scrapers-pipeline/logs/upload-<fecha>.log` para extraer inicio,
   fin, estado, errores, ficheros subidos/rechazados y resultado global del
   pipeline (RF-1, RF-3, RF-14).
3. Leer los manifests existentes bajo `landing/_manifests/`. Sus filas, hashes,
   estado por fichero y rutas remotas acreditan qué se validó y publicó (RF-6,
   RF-8).
4. Leer `_READY` solo como señal de que el pipeline notificó disponibilidad
   posterior; no sustituye la comprobación de los manifests y ficheros (RF-8).
5. Nunca inferir que un snapshot local pertenece al run actual solo porque
   exista. Confirmar su timestamp frente al inicio del run y contrastarlo con
   los logs/checkpoints de esa fuente (RF-1, RF-3).

### 3.2 Inventario y evidencias específicas

| Fuente independiente | Evidencia de ejecución y progreso | Datos obtenidos y clave | Comprobación publicada |
|---|---|---|---|
| Indeed | Log general, `output/nightly.log`, logs de intento, checkpoints y eventos de parseo por búsqueda. | Snapshot/artefactos del run; deduplicación por `job_key`. Alias como `company`, `description_text`, `salary_text` y `workplace_type`. | Manifest `indeed`, ficheros de `landing/indeed/dia=...`; comparar contra el conjunto de salida seleccionado para ese run. |
| LinkedIn | Log general, `data/run_nightly.log`, stdout/stderr por intento, marcador final, checkpoints y contadores de tarjetas/IDs procesados. | Snapshot de `jobs.parquet`; deduplicación por `job_id`. Campos como `company_name`, `description_full`, `skills` y `work_mode`. | Manifest `linkedin` y ficheros del día. El upload es incremental: solo se comparará el delta publicado con su manifest, nunca se interpretará un delta vacío como cero ofertas capturadas si el snapshot contiene ofertas. |
| InfoJobs | Log general, `data/run_nightly.log`, stdout/stderr por intento y resultado final `RESULT`. | Ficheros fechados de ofertas del run; deduplicación por `id_oferta`. Se normalizan los alias españoles (`titulo`, `empresa`, `modalidad`, etc.) según el contrato. | Manifest `infojobs` y ficheros de `landing/infojobs/dia=...`. |
| IrishJobs | `multi_site/data/irishjobs/run.log`, resultado independiente en `data/merged/last_run.json` y avance de ofertas del proceso. | `data/irishjobs/output/jobs.parquet`; deduplicación por `job_id`, segmento `site=irishjobs`. | Filas del portal dentro del delta `landing/multi_site/dia=...`, verificadas con el manifest de `multi_site`. |
| StepStone NL | Log y resultado independiente del sub-scraper `stepstone_nl`; contador de ofertas procesadas. | Salida propia del portal; deduplicación por `job_id`, segmento `site=stepstone_nl`. | Filas `stepstone_nl` del delta de Multi-site y manifest remoto. |
| DevITjobs | Log y resultado independiente del sub-scraper `devitjobs`; contador de ofertas procesadas. | Salida propia del portal; deduplicación por `job_id`, segmento `site=devitjobs`. | Filas `devitjobs` del delta de Multi-site y manifest remoto. |
| NVB | Log y resultado independiente del sub-scraper `nvb`; contador de ofertas procesadas. | Salida propia del portal; deduplicación por `job_id`, segmento `site=nvb`. | Filas `nvb` del delta de Multi-site y manifest remoto. |
| Jobs.ch | Log y resultado independiente del sub-scraper `jobs_ch`; contador de ofertas procesadas. | Salida propia del portal; deduplicación por `job_id`, segmento `site=jobs_ch`. | Filas `jobs_ch` del delta de Multi-site y manifest remoto. |
| Glassdoor | Log propio `data/glassdoor/run.log`, contadores de tarjetas y ofertas por búsqueda, y resultado independiente en `last_run.json`. | Salida propia del portal; deduplicación por `job_id`, segmento `site=glassdoor`. | Filas `glassdoor` del delta de Multi-site y manifest remoto. La actividad se determina por el contador de ofertas/tarjetas procesadas, no por que el proceso siga vivo ni por escrituras arbitrarias al log. |

`run_all.ps1` registra los seis sub-scrapers en `last_run.json`; la salida
unificada incluye el campo `site`. Se cruzarán ambas evidencias para que un
fallo de Glassdoor no se oculte tras el éxito de otro portal (RF-2, RF-14).

### 3.3 Supervisión del progreso y bloqueo

1. Cada unidad de scraping emite un contador acumulativo y machine-readable de
   ofertas/tarjetas parseadas durante el run. Se cuenta progreso capturado, no
   solo ofertas nuevas respecto del histórico: los duplicados ya conocidos
   también demuestran que la página sigue siendo procesada (RF-15).
2. El supervisor observa el contador por proceso. Si aumenta, mantiene el
   proceso activo. La mera vida del proceso, CPU o actualización genérica del
   log no basta como prueba de avance (RF-15).
3. Se usa un periodo de inactividad inicial de 40 minutos, configurable por
   fuente (override) para sus pausas y cadencias normales. Los límites absolutos
   existentes se mantienen como protección distinta del límite por falta de
   progreso (RF-15).
4. Si el contador no avanza durante el periodo de esa fuente, el supervisor
   detiene el árbol de procesos de esa unidad, registra el motivo en el log y
   el resultado individual, y no la relanza automáticamente. En Multi-site se
   detiene solo el sub-scraper afectado y los demás continúan (RF-3, RF-12,
   RF-14, RF-15).
5. Los wrappers existentes de Indeed, LinkedIn e InfoJobs tienen reintentos. La
   salida por watchdog debe distinguirse de un error técnico reintentable para
   que esta política de detención no cause un relanzamiento automático (RF-12,
   RF-15).

## 4. Medición, landing y tendencias

### 4.1 Datos obtenidos y válidos

- `sources.py` mantiene el mapa de cada campo canónico a sus columnas de origen
  y clave de deduplicación: `job_key`, `job_id` o `id_oferta` según corresponda.
- `field_contract.py` toma el contrato de datos del proyecto como autoridad y
  reutiliza las comprobaciones estructurales existentes en `coherence.py`.
  Antes de implementar las métricas, las reglas de validez de los campos
  medidos se dejarán documentadas en ese contrato (RF-3, RF-5).
- Un Parquet ilegible o sin esquema/columnas obligatorias es un fallo estructural
  de la fuente. Los valores incoherentes por oferta reducen la completitud y se
  evalúan con los umbrales de campo; no se confunden con un fallo de lectura o
  esquema (RF-3–RF-5, RF-10).
- Para cada fuente y búsqueda/región, se contarán ofertas únicas, válidas,
  ausentes e inválidas para título, empresa, descripción, salario, skills,
  modalidad, ubicación y fecha (RF-2, RF-4, RF-5).
- Un campo obligatorio bajo 100 % activa investigación; entre 90 % y menos de
  100 % es incidencia y bajo 90 % falla la fuente. Un campo no obligatorio con
  60 % o menos activa investigación, pero no falla por esa causa (RF-3, RF-4,
  RF-10, RF-14).
- La ausencia real de salario en una oferta se computa como campo ausente, no
  como valor inválido; la validez no se decide mediante longitud mínima de
  texto (RF-5).

### 4.2 Comprobación de la landing

- Usar el manifest de cada publicación para localizar los objetos remotos y
  conocer filas, hashes y estado previo a la subida (RF-6, RF-8).
- Usar el AzCopy ya configurado en el pipeline para listar y descargar objetos
  solo a temporales; calcular de nuevo filas/hash y eliminar esos temporales al
  terminar. No invocar `ensure_compatible.py` durante el diagnóstico porque
  puede reescribir o poner ficheros en cuarentena (RF-8, C#5).
- En LinkedIn y Multi-site, distinguir el snapshot local del delta creado por
  `OnlyNewOffers`. Un delta remoto vacío no demuestra por sí solo que el
  scraper obtuvo cero ofertas; se contrasta con el snapshot y los logs. El
  informe muestra porcentajes de ambas etapas y explica que las poblaciones
  pueden ser distintas (RF-3, RF-6, RF-8).
- Cuando un objeto esperado todavía no sea visible, informar `pendiente de
  publicar`; solo informar pérdida/rechazo cuando manifiesto y evidencia del
  pipeline lo confirmen. Si la lectura remota falla por credenciales o
  conectividad, el resultado de publicación queda no comprobado (RF-8,
  RF-13).

### 4.3 Tendencias

- La tendencia se calcula con ficheros de las particiones diarias ya
  conservadas en Azure; no se crea una base histórica local (RF-7, C#5).
- La serie histórica es la completitud de lo publicado en cada run. Para
  LinkedIn y Multi-site, representa el delta de ofertas nuevas de ese día, no
  el snapshot acumulado del scraper (RF-6, RF-7).
- Comparabilidad se prueba con una huella de las fuentes y búsquedas/regiones
  efectivas guardada dentro del manifest ya existente. La huella excluye
  credenciales. Los manifests antiguos sin huella no se declaran comparables
  por inferencia y no aportan a la serie (RF-7).
- Se seleccionan hasta cinco runs comparables, incluida la ejecución analizada;
  se informa cuántos se pudieron usar. Si la huella cambia, esa fuente/búsqueda
  no se mezcla con la serie anterior (RF-7).

## 5. Investigación de webs reales

`investigation.py` solo se activa para una fuente fallida, un campo obligatorio
por debajo del 100 % o un campo no obligatorio en 60 % o menos. Prepara un
contexto acotado: portal y región, término de búsqueda, URL de ejemplo, oferta
representativa, campo afectado, evidencia de la ejecución y parser/regla
relacionada.

La persona/orquestador contrasta el caso puntual en la web real usando el medio
de acceso que ya emplea ese scraper. No se lanza una segunda ejecución completa.
La investigación separa observado, hipótesis y cambio recomendado; si la página
no es accesible o no puede verificarse, queda como no confirmada y no modifica
el estado de ingestión de la fuente (RF-9, RF-10, RF-11, RF-12).

## 6. Decisiones técnicas y alternativas descartadas

1. **Python para reglas + PowerShell para ciclo de vida de procesos.** Python ya
   se usa en validación y dispone de pytest; PowerShell ya posee los handles de
   los procesos del pipeline. La lógica de límites/estados será pura en Python;
   los wrappers solo consultan la decisión, detienen el proceso indicado y
   registran el resultado (C#1, C#3, RF-15). **Descartada:** reescribir todo el
   orquestador nocturno en otro lenguaje, por el riesgo y alcance innecesarios.
2. **Reutilizar el contrato y `coherence.py`.** Evita duplicar validación de
   URLs, IDs, fechas y valores estructuralmente incoherentes. Las reglas nuevas
   de completitud se documentan en el contrato y se cubren con tests (C#1,
   C#2, RF-5). **Descartada:** una segunda tabla independiente de validación.
3. **PyArrow para leer Parquet.** Es una excepción justificada a la convención de
   biblioteca estándar: ya se utiliza en `ensure_compatible.py` y Parquet no se
   puede interpretar con la biblioteca estándar; además preserva los tipos al
   medir los datos tabulares (C#1, RF-4, RF-8). No se añade otra dependencia.
   **Descartada:** usar Spark para esta lectura puntual por el coste de arranque;
   exportar/cargar CSV perdería tipos y duplicaría lecturas.
4. **AzCopy existente para lectura de Azure.** Usa la misma configuración y
   herramienta del pipeline; las operaciones del diagnóstico son listar y
   descargar, nunca subir/borrar (C#1, C#5, RF-8). **Descartada:** incorporar
   un SDK Azure nuevo solo para esta consulta.
5. **Progreso por contador de ofertas parseadas.** Distingue Glassdoor bloqueado
   de un proceso vivo que aún obtiene ofertas; no depende de CPU ni de cualquier
   escritura de log (RF-15). **Descartadas:** matar por tiempo total aunque haya
   progreso o considerar actividad cualquier línea de log. El periodo inactivo
   inicial de 40 minutos es configurable por fuente mediante override.
6. **Manifests existentes para trazabilidad e histórico.** Se incorpora a cada
   manifest un fingerprint de fuentes/búsquedas efectivas; no se crea una
   persistencia de diagnóstico paralela (C#5, RF-7, RF-8).
   **Descartadas:** inferir que la configuración histórica no cambió o usar un
   histórico local como copia canónica.
7. **Web-check acotado y asistido por persona.** La inspección se limita a los
   portales/casos que incumplen umbrales y reutiliza el medio de acceso del
   scraper correspondiente. **Descartada:** un crawler universal adicional,
   que duplicaría scrapers, dependencias y riesgo de bloqueos (C#1, RF-9,
   RF-10, RF-12).
8. **Informe de consola en español.** Salida sencilla con estados, números,
   evidencia y recomendación; sin persistir otro histórico local (C#5, C#6,
   RF-11, RF-13, RF-14). **Descartados:** crear otro dashboard o sistema de
   alertas, ambos fuera del alcance.

## 7. Secuencia de ejecución de implementación

1. Crear el catálogo de fuentes, alias canónicos, claves y búsquedas conforme a
   RF-2, incluidos los seis portales independientes de Multi-site (RF-2, RF-5).
2. Añadir eventos acumulativos de progreso por fuente/sub-scraper y el estado
   explícito de proceso bloqueado. Integrar el umbral de inactividad en los
   supervisores actuales; probar detención aislada y ausencia de retry (RF-12,
   RF-15).
3. Implementar lectura de logs/manifests, selección de run completado y
   clasificación de falta de evidencia (RF-1, RF-3, RF-13, RF-14).
4. Documentar validez por campo en el contrato y construir validación,
   deduplicación y completitud por fuente/búsqueda (RF-2–RF-5).
5. Leer la landing, comprobar manifest y contenido y distinguir snapshot de
   delta `OnlyNewOffers` y publicación pendiente (RF-6, RF-8).
6. Añadir el fingerprint de búsquedas a los manifests y calcular tendencia con
   las últimas cinco ejecuciones comparables (RF-7).
7. Añadir el contexto de investigación web y el informe en español; integrar
   estados e hipótesis con hechos observados (RF-9–RF-14).
8. Ejecutar tests unitarios, integración local, suites de los componentes
   modificados y comprobar manualmente los flujos de web-check solo cuando se
   active un caso real (RF-1–RF-15, NFR).

## 8. Estrategia de tests

### Unitarios offline

- `run_evidence`: varios logs por día, último run terminado con y sin ofertas,
  log truncado, run incompleto y evidencia contradictoria (RF-1, RF-3, RF-13).
- `sources`/`field_contract`: los nueve identificadores de fuente, alias de
  columnas, Parquet ilegible, columnas obligatorias ausentes, identificadores
  vacíos/duplicados y valores válidos/inválidos según el contrato (RF-2, RF-3,
  RF-5).
- `progress`: contador que avanza/no avanza, umbral justo antes/en/después del
  timeout, contador de duplicados que sigue avanzando, y aislamiento del
  sub-scraper detenido en Multi-site (RF-12, RF-15).
- `completeness`: ofertas únicas con duplicados, campos ausentes/invalidos,
  porcentaje, obligatorio 89.9/90/99/100 % y no obligatorio 60/60.1 % (RF-3,
  RF-4, RF-10, RF-14).
- `landing`: objeto presente/ausente/pendiente, manifest rechazado, mismatch de
  hash/filas, delta vacío con snapshot no vacío y fallo de lectura remoto usando
  un cliente falso (RF-6, RF-8, RF-13).
- `trends`: cero a cinco runs, cinco seleccionados, fingerprint igual/distinto
  y cambios de fuentes/búsquedas (RF-7).
- `status`/`report`: todos correctos, todos fallidos (incluido run finalizado
  sin ofertas), mezcla parcial, ejecución no analizable, datos pendientes e
  investigación no confirmada; mensajes en español y recuentos presentes
  (RF-11, RF-13, RF-14).

### Integración sin red ni credenciales

Un fixture temporal representará una ejecución completa con logs, los seis
resultados Multi-site, artefactos Parquet por fuente, manifests y una landing
local simulada. El flujo integrado deberá demostrar:

- correlación entre log general, salida por portal y manifest;
- completitud obtenida/publicada y subset `OnlyNewOffers` sin falso cero;
- tendencia reproducible sobre cinco particiones comparables;
- fuente fallida, proceso detenido, fuentes correctas restantes y estado global
  parcial;
- diagnóstico sin escritura de datos, configuración o publicación.

Las funciones puras y adaptadores remotos se separan para que los tests usen el
fixture local. No se ejecutan webs reales, navegador, AzCopy contra Azure ni
credenciales durante pytest.

### Validación de cambios en wrappers y scrapers

Cuando se instrumenten wrappers o scrapers, ejecutar además sus suites propias:

- Indeed: desde `indeed_jobs_scraper/`, `python -m pytest tests -q`.
- LinkedIn: desde `linkedin_jobs_scraper/`, `python -m pytest tests -q`.
- InfoJobs: desde `infojobs_jobs_scraper/`, `python -m pytest tests -q`,
  `ruff check scraper tests` y `mypy scraper`.
- Multi-site: desde `multi_site_job_scraper/`, `python -m pytest tests -q`.
- Diagnóstico: desde la raíz, `python -m pytest scrapers-pipeline/tests -q`.

## 9. Trazabilidad RF

| RF | Partes del plan que lo cubren |
|---|---|
| RF-1 | `run_evidence.py`; selección de run completado y fixtures con varios runs. |
| RF-2 | `sources.py`; tabla de nueve fuentes y verificación por región/búsqueda. |
| RF-3 | `status.py`, `completeness.py`; cero ofertas, contrato, umbral obligatorio y falta de evidencia. |
| RF-4 | `completeness.py`; oferta única, recuentos y porcentajes por fuente/campo. |
| RF-5 | `field_contract.py`; contrato y reutilización de `coherence.py`. |
| RF-6 | `completeness.py` + `landing.py`; comparación obtenida/publicada por población. |
| RF-7 | `trends.py`; Azure, fingerprint y últimas cinco ejecuciones comparables. |
| RF-8 | `landing.py`; manifests, descarga de lectura y estados pendientes/rechazados/perdidos. |
| RF-9 | `investigation.py`; contexto/evidencia de página real solo para fuente fallida. |
| RF-10 | `investigation.py`; disparadores 100 % y 60 % e investigación real. |
| RF-11 | `report.py`; hechos, hipótesis, causa probable y recomendación verificable. |
| RF-12 | supervisor; sin retry ni arreglos automáticos, salvo detención limitada por RF-15. |
| RF-13 | `run_evidence.py` + `report.py`; ejecución ausente/no analizable o lectura remota no disponible. |
| RF-14 | `status.py`; estados por fuente y global. |
| RF-15 | `progress.py` + supervisores PowerShell; contador de ofertas, timeout de inactividad y detención por portal. |

## 10. Cumplimiento de la constitución

- **Stack simple:** biblioteca estándar para CLI, logs, JSON, fechas y supervisión;
  PyArrow/validadores/AzCopy ya existentes para Parquet y Azure; ninguna nueva
  dependencia sin una justificación y actualización de la spec (C#1).
- **Spec y código:** este plan implementa únicamente RF-1–RF-15; cualquier
  cambio de comportamiento fuera de ellos requerirá actualizar la spec activa
  (C#2).
- **Lógica e interfaz:** mediciones, estados, progreso y umbrales son funciones
  testeables; CLI y PowerShell solo coordinan IO/procesos (C#3).
- **Tests:** suites unitarias e integración offline con pytest y suites de cada
  scraper modificado antes de cerrar (C#4).
- **Persistencia:** datos crudos e histórico siguen en Azure; el diagnóstico
  solo lee remoto y usa temporales locales efímeros; no crea copia histórica
  local ni escribe resultados a Azure (C#5).
- **Idioma:** nombres de módulos, identificadores y comentarios en inglés;
  informe y mensajes a la persona en español (C#6).
