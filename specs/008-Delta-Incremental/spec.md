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
existentes no deberán cambiar de contrato (sus salidas se mantienen).

### RF-4 — Validación en dev
**Criterio (EARS):** Cuando se ejecute el ciclo dev con el ingest nuevo, deberá
terminar en verde y más rápido que el actual, con idempotencia demostrada
(segunda ejecución sin cambios) y conteos comparados con prod para la misma
ventana.

### RF-5 — Cambio de producción seguro
**Criterio (EARS):** Cuando el ingest nuevo llegue a producción, deberá
hacerse por el flujo de la 007 (CI en verde y ventana acordada), con rollback
documentado y una noche real verificada.

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
- **Idioma:** identificadores en inglés; documentos y mensajes, en español.

## Fuera de alcance

- Historial de cambios (CDF) y expiración técnica de ofertas.
- Cambios de scrapers o landing (specs 004/005).
- Bloque B (silvers/gold incrementales): se especifica al cerrar el bloque A.

## Criterios de finalización (bloque A)

- Bronce dev migrado; ciclo dev en verde con tiempos y conteos registrados.
- Idempotencia demostrada (re-ejecución sin cambios de filas).
- Suites existentes en verde.
- Prod cambiado en ventana, con rollback probado y noche real verificada.
