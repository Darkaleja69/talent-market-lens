# Especificación funcional: bronce deduplicado e ingesta incremental (Delta)

> **Borrador inicial (2026-10-17).** Define el **bloque A (bronce upsert)** para
> poder arrancar directamente; el **bloque B (silvers/gold incrementales)** se
> detallará al cerrar el bloque A. Nace del diagnóstico de la spec 007
> (`specs/007-ELT-As-Code/evidence.md`) y de las decisiones tomadas con la
> persona.

## Contexto y objetivo

La ingesta actual es append-only: LinkedIn y multi_site suben al landing
snapshots acumulativos y un checkpoint nuevo de Auto Loader los relee todos.
Primer run dev (diagnóstico 2026-10-17): bronce `linkedin` con 9.569.896 filas
para 13.376 ofertas (~715 copias; 247 ficheros, 14,4 GB) frente a 16.371 filas
en prod para las mismas ofertas; la silver dev superó los 92,9 min (prod:
5,9 min). El run completo de prod tarda ~20 min en un nodo de 4 cores.

Este trabajo convierte el bronce en **estado actual deduplicado** (upsert
idempotente con `MERGE` por clave, gana la captura más reciente) y practica los
dominios 2 (Delta) y 4 (arquitectura incremental) del track. Incluye la
migración de lo ya acumulado y la validación en dev; el cambio a producción
usa el mecanismo seguro estrenado en la 007 (bundle, CI y ventana).

## Herencia de la 009: coste y cadencia (2026-10-24)

La spec 009 (coste y cadencia) se fusionó a `main` el 2026-10-24 y cambia el
contexto en el que se ejecuta esta spec. **No se reabre su alcance**: se hereda
y se respeta.

- **Cadencia semanal.** El job de producción corre **los domingos a las 23:00**
  (`Europe/Madrid`); el dashboard público se publica los lunes a las 01:00 UTC y
  falla sin desplegar si `meta.json.data_date` tiene más de 8 días. Esta spec
  **no** toca la programación.
- **Job de 8 tareas.** La 009 añade `Build_Report_Export` (Parquet de
  `gold/pbi_export` para Power BI en modo Import, sin SQL warehouse). La ingesta
  de esta spec está aguas arriba: el run dev debe seguir cerrándolas en verde y
  escribiendo `gold/web_export_dev` y `gold/pbi_export_dev` con su `meta.json`.
- **Compute fijado.** `Standard_D4ds_v4` single node; `D2ds_v6` no completó el
  run (009, T-12) y el ajuste de compute queda para la 010, que lo probará con
  el código definitivo de esta spec.
- **Presupuesto de runs.** La 009 reserva para esta spec **3 runs de dev y 1
  sesión SQL** (≤ 2,0 €; tabla por spec de `specs/009-Cost-And-Cadence`
  `plan.md` §17). Decisión del 2026-10-24: **se renuncia a la sesión SQL** —3
  runs y 0 sesiones— y el techo de 2,0 € se mantiene, con verificación
  local-first: los tests locales son gratis y la evidencia de historial se lee de
  las métricas y salidas del propio run, no del SQL Editor (cada despertar del
  warehouse son 10 min facturables, ~0,43 €). Cada run se anota con su motivo y
  su coste en `docs/costs.md`.
- **Gasto anterior a la política.** Esta spec ya gastó 2 runs de dev el
  2026-10-21 (uno fallido y el verde de T-03), ~0,48 €. Quedan registrados como
  hundidos y no consumen el presupuesto nuevo.
- **Comprobación real de producción.** Con cadencia semanal, el cierre de T-06
  depende del **run programado del domingo**, no de «una noche real»: el cutover
  y el one-off de producción se ejecutan en la ventana previa al domingo.

## Decisiones (2026-10-24)

- **Presupuesto:** 3 runs de dev y **0 sesiones SQL** (la 009 reservaba 1 sesión;
  se renuncia el 24-oct y el techo de 2,0 € se mantiene). Los 2 runs de T-03 son
  anteriores a la política y quedan hundidos.
- **One-off versionada:** la migración del bronce es un job `dedupe_oneoff` con
  notebook propio y parámetros, no SQL ni consultas ad hoc. Es idempotente y se
  reutiliza en dev y en prod (T-06).
- **Evidencia por API:** el notebook del one-off devuelve un resumen JSON
  (`dbutils.notebook.exit`) con conteos y versión de rollback, para que la
  evidencia se lea con `jobs get-run-output` sin despertar el warehouse.
- **Cierre en producción:** T-06 se verifica con el **run programado del
  domingo**; no se lanza un run manual del job de ELT. El procedimiento está en
  `cutover.md`.
- **Limitación aceptada:** la evolución de esquema con una columna nueva y la
  cuarentena de punta a punta no se ejercitan en dev, porque el landing es
  compartido con producción y un fichero de prueba lo ingeriría el job real. Se
  aceptan como limitación documentada (cubiertas por los tests del MERGE y de la
  cuarentena, y vigiladas en el run semanal con aviso por email).
- **Comparaciones de tiempo:** el sink (*append* o *upsert*) y el tamaño del lote
  cambian el tiempo de bronce; el arranque del clúster (2 m 42 s–6 m 52 s) no
  mide el código. T-05 exige bronce **exec ≤ 2 m 29 s** (T-03, único run con el
  sink nuevo) y total **< 16 m 19 s** (run de 8 tareas de la 009), y anota el
  lote.

## Decisiones (2026-10-17)

- **Sin historial de cambios** (no CDF): bronce = última captura por oferta.
- **Sin expiración técnica** en bronce; el «último mes» es un filtro de
  negocio, en gold/BI.
- **Claves de dedup:** `job_id` (linkedin, indeed, infojobs); `site + job_id`
  (multi_site). *Nota: los nombres reales de columna en bronce son `job_key`
  (indeed), `job_id` (linkedin), `id_oferta` (infojobs) y `site + job_id`
  (multi_site); ver Decisiones 2026-10-21.*
- **Gana el `scraped_at` más reciente**; reprocesar o recibir ficheros
  desordenados no cambia el resultado (idempotencia).
- El crudo canónico sigue en el landing (constitución 5); bronce es derivado.

## Decisiones (2026-10-21)

- **Claves (D1):** se mantiene la clave natural de cada fuente (`job_key` en
  indeed, `job_id` en linkedin, `id_oferta` en infojobs, `site + job_id` en
  multi_site; las silvers las renombran a `job_id`). El dedup de negocio «misma
  oferta republicada con clave nueva» **no** vive aquí: ya existe en oro
  (`gold_build.ipynb`, `_keep_best` por `title_norm` + `company_norm` +
  `salary_filled`). Bronce conserva una fila por anuncio. Limitación conocida
  de ese dedup (no incluye localización ni fecha): se trata en una spec aparte,
  no en esta.
- **Condición (D2):** la entrante gana si su instante de captura es
  estrictamente mayor; empate → desempata `_ingest_date`; empate exacto → no se
  actualiza. Una fila guardada sin temporal válida se repara con la primera
  captura buena. Las filas entrantes con clave o temporal nulas, en blanco o
  imparseables (columna según el mapa por fuente) no entran en el MERGE: van a
  cuarentena.
- **Comparación (D3):** la condición del MERGE castea la temporal de cada
  fuente a `timestamp` con `try_to_timestamp` (nunca a fecha, y nunca
  `to_timestamp`: con ANSI un valor malformado abortaría el lote): dos capturas
  del mismo día con horas distintas deben ordenarse. La fecha es solo de
  presentación: las silvers derivan `posted_date` con `to_date` y oro la
  consume ya como `date`.
- **Cuarentena (D4):** tabla `bronze.<fuente>_quarantine`, escrita en el mismo
  `foreachBatch` que el MERGE, deduplicada por huella de fila
  (`sha2(to_json(struct(columnas de la fuente))))` + motivo + fichero — no por
  clave, porque las filas con clave nula no tienen clave con la que
  deduplicar. Su tratamiento posterior (corregir o descartar) se profundizará
  en specs futuras; aquí solo se captura el dato con su motivo.
- **Checkpoint (D5):** plan A — se conserva `<checkpoints_root>/<fuente>/ckpt`
  (sin relectura del landing) y el histórico se normaliza con una migración
  one-off (RF-2). `ckpt_v2` (relectura completa) queda como plan B si el
  cambio de sink falla en dev.

## Requisitos funcionales (borrador)

### RF-1 — Bronce como estado actual
**Criterio (EARS):** Cuando la ingesta procese ficheros nuevos, cada clave
(`job_key`, `job_id`, `id_oferta`, o `site + job_id` según la fuente) deberá
quedar con una única fila —la del instante de captura más reciente (la columna
temporal de cada fuente: `scraped_at` o `fecha_scraped`)— y reprocesar los
mismos ficheros no deberá cambiar el resultado.

### RF-2 — Migración del bronce acumulado
**Criterio (EARS):** Cuando se migre el bronce existente, cada tabla deberá
quedar en el universo único de ofertas, con conteos verificables por fuente;
dev primero y prod con ventana y rollback.

### RF-3 — Contratos intactos
**Criterio (EARS):** Cuando el nuevo ingest escriba, la evolución de esquema
(`mergeSchema`, `_rescued_data`) deberá seguir funcionando y las silvers
existentes no deberán cambiar de contrato (sus salidas se mantienen); el run dev
deberá además cerrar las 8 tareas del job en verde, con `gold/web_export_dev` y
`gold/pbi_export_dev` escritos y sus `meta.json` válidos.

### RF-4 — Validación en dev
**Criterio (EARS):** Cuando se ejecute el ciclo dev con el ingest nuevo, deberá
terminar en verde y más rápido que el actual, con idempotencia demostrada
(segunda ejecución sin cambios) y conteos comparados con prod para la misma
ventana, sin salirse del presupuesto heredado de la 009: **3 runs de dev** y
**ninguna sesión de SQL** (la evidencia de versiones y conteos se toma de las
métricas y salidas del run).

### RF-5 — Cambio de producción seguro
**Criterio (EARS):** Cuando el ingest nuevo llegue a producción, deberá hacerse
por el flujo de la 007 (CI en verde y ventana acordada **antes de un domingo**),
con rollback documentado y el **run programado del domingo** verificado; el
one-off de producción se ejecuta en esa ventana y no se lanza un run manual
extra.

### RF-6 — Cuarentena de filas no fusionables
**Criterio (EARS):** Cuando un lote traiga filas con la clave de su fuente
(`job_key`, `job_id`, `id_oferta`, o `site` + `job_id`) o su columna temporal
(`scraped_at` o `fecha_scraped`) nulas, en blanco o imparseables, el ingest
deberá escribirlas en `bronze.<fuente>_quarantine` con su motivo
(`missing_key` / `missing_scraped_at`), la fecha de cuarentena y el fichero de
origen, sin incluirlas en el MERGE y sin abortar el resto del lote. Reprocesar
el mismo fichero no deberá duplicar la cuarentena (huella de fila + motivo +
fichero).

## Requisitos no funcionales (borrador)

- **Stack simple:** Auto Loader y Delta existentes; sin dependencias nuevas.
- **Lógica e interfaz:** la lógica de dedup/upsert vive en un módulo
  comprobable con tests; el notebook coordina.
- **Persistencia:** el landing no se toca; el checkpoint sigue siendo la única
  fuente de «qué ficheros ya se leyeron».
- **Coste:** la verificación es local-first; el presupuesto heredado de la 009
  (3 runs de dev, 0 sesiones SQL) es un techo, no una estimación, y cada run se
  anota con su coste en `docs/costs.md`.
- **Idioma:** identificadores en inglés; documentos y mensajes, en español.

## Fuera de alcance

- Historial de cambios (CDF) y expiración técnica de ofertas.
- Cambios de scrapers o landing (specs 004/005).
- Cadencia, compute, export del informe y presupuesto de coste (spec 009); esta
  spec solo los respeta.
- Bloque B (silvers/gold incrementales): se especifica al cerrar el bloque A.

## Criterios de finalización (bloque A)

- Bronce dev migrado; ciclo dev en verde con tiempos y conteos registrados,
  dentro del presupuesto de 3 runs de dev y sin sesiones SQL.
- Idempotencia demostrada (re-ejecución sin cambios de filas).
- Los exports de la 009 (`gold/web_export_dev`, `gold/pbi_export_dev`) siguen
  escribiéndose con `meta.json` válido en cada run dev.
- Suites existentes en verde.
- Prod cambiado en la ventana previa a un domingo, con el rollback de
  despliegue probado en seco en la 007 y el `RESTORE` de datos documentado, y el
  run programado del domingo verificado sin despertar el SQL warehouse.
- Coste de cada run de la spec anotado en `docs/costs.md`.
