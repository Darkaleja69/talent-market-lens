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
  (multi_site).
- **Gana el `scraped_at` más reciente**; reprocesar o recibir ficheros
  desordenados no cambia el resultado (idempotencia).
- El crudo canónico sigue en el landing (constitución 5); bronce es derivado.

## Requisitos funcionales (borrador)

### RF-1 — Bronce como estado actual
**Criterio (EARS):** Cuando la ingesta procese ficheros nuevos, cada clave
(`job_id`, o `site + job_id` en multi_site) deberá quedar con una única fila
—la de `scraped_at` más reciente— y reprocesar los mismos ficheros no deberá
cambiar el resultado.

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
