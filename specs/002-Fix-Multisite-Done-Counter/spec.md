# Especificación funcional: contador DONE por portal Multi-site

## Contexto y objetivo

En la verificación del Grupo 2 de `001-Verify-Scrapers-Run` se detectó un defecto
residual (R5): el contador `DONE` de un portal Multi-site puede heredarse de una
ejecución anterior. Los `run.log` de los portales se abren en modo append y el
contador se tomaba de la última línea `DONE` del fichero validando solo la hora,
de modo que un portal correcto del run actual sin línea `DONE` propia podía
atribuirse el contador de la noche anterior.

Al revisar los logs reales de finalización se comprobó además que **el `DONE` no
siempre es la última línea del `run.log`**:

- **Glassdoor** (`src/glassdoor/__main__.py`) registra `Total ofertas almacenadas:
  N` **después** de que `run()` emita `=== GLASSDOOR DONE: ... ===`.
- **NVB** emite `=== NVB INTERRUMPIDO: ... ===` en lugar de `DONE` cuando el
  portal se interrumpe (`src/nvb/scraper.py`); solo un `DONE` acredita una
  finalización correcta.
- El **merge/traducción** de Multi-site (`merge.py`, con traducción NL→EN) se
  ejecuta al final del proceso, después de los scrapers, y no forma parte de la
  finalización de un portal.

El objetivo es que el contador `DONE` de un portal se atribuya a la ejecución
analizada **solo** cuando la evidencia pertenece realmente a esa ejecución,
admitiendo líneas benignas posteriores al `DONE`. Esta spec corrige únicamente
ese defecto; no cambia el alcance de la verificación.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería
  de datos de nivel junior.

## Requisitos funcionales

### RF-1 — Atribuir el contador DONE solo a su ejecución

**Criterio de aceptación (EARS):** Cuando se lea el contador `DONE` de un portal
Multi-site para la ejecución analizada, el sistema deberá usar la **última línea
`DONE`** del `run.log` del portal siempre que (a) no exista un **marcador de
inicio de una nueva ejecución** del portal posterior a esa línea (es decir, el
`DONE` pertenece a la última ejecución del portal y no a una anterior), y (b) el
fichero `run.log` haya sido modificado dentro de la ventana de la ejecución
analizada. Las líneas benignas posteriores al `DONE` (por ejemplo, el recuento
`Total ofertas almacenadas` de Glassdoor) **no** deben impedir la atribución.

Si no existe `DONE`, si hay un marcador de inicio de ejecución posterior al
último `DONE`, o si el fichero no fue modificado en la ventana, el contador del
run deberá quedar sin valor (`None`), sin reutilizar la evidencia de otra
ejecución. Un `INTERRUMPIDO` no es un `DONE` y no aporta contador.

## Requisitos no funcionales

- La corrección se cubre con tests unitarios offline sobre fixtures sintéticas,
  sin depender de datos reales, de red ni de credenciales.
- Sin dependencias nuevas (biblioteca estándar; pytest para tests).
- El resto del comportamiento de `run_evidence.py` (T-06 a T-12 de
  `001-Verify-Scrapers-Run`) no debe alterarse.

## Casos límite

- `run.log` de una noche anterior cuya última línea `DONE` cae en la franja
  horaria del run actual y no va seguida de otro inicio: se rechaza si el
  fichero no fue modificado en la ventana; si una nueva ejecución arrancó
  después de ese `DONE`, también se rechaza por pertenecer a una ejecución
  anterior.
- `run.log` del run actual cuyo fichero no se modificó en la ventana: no se
  atribuye el contador.
- `run.log` sin ninguna línea `DONE`: el contador queda `None`.
- Glassdoor: `=== GLASSDOOR DONE: N ... ===` seguido de `Total ofertas
  almacenadas: N` (y de cualquier otra línea benigna posterior): **sí** se
  atribuye el contador.
- NVB: `=== NVB INTERRUMPIDO: N nuevas, N total ===` no es un `DONE`; el
  contador queda `None` (el portal además no es correcto).
- Un `run.log` cuya última ejecución arrancó pero no terminó (hay un marcador de
  inicio posterior al último `DONE`): el contador queda `None`.
- El merge/traducción final de Multi-site no se interpreta como finalización de
  un portal ni aporta contador.
- Portal no correcto (`error`/`empty`): no se atribuye contador, como hasta ahora.

## Fuera de alcance

- Cambiar la clasificación de estado del portal (`outcome`), que ya procede de
  `last_run.json` validado por `run_at`.
- Otros contadores de fuentes directas (Indeed, LinkedIn, InfoJobs).
- Cualquier cambio de umbrales, watchdog o publicación.

## Criterios de finalización

- Un `run.log` con última línea `DONE` de otra ejecución no aporta contador al
  run actual.
- Un `run.log` del run actual con su línea `DONE` final sí aporta el contador.
- La suite `python -m pytest scrapers-pipeline/tests -q` pasa completa.

## Decisiones aclaradas

- La pertenencia al run se comprueba con la **última línea `DONE`** del
  `run.log`, exigiendo que no exista un marcador de inicio de ejecución del
  portal posterior a ella y que la fecha de modificación del fichero esté dentro
  de la ventana `[inicio, fin]` del run. Las líneas benignas posteriores al
  `DONE` se ignoran.
- Sin evidencia concluyente, se prefiere dejar el contador sin valor antes que
  reutilizar el de otra ejecución (mismo criterio que RF-3: la falta de
  evidencia no produce un falso positivo).
