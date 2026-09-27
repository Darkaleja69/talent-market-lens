# Plan técnico: contador DONE por portal Multi-site

## 1. Objetivo y límites

Corregir el defecto R5 en `read_multi_site_runs` de
`scrapers-pipeline/verification/run_evidence.py`: el contador `DONE` de un portal
solo se atribuye a la ejecución analizada cuando la evidencia pertenece a esa
ejecución. No se modifica el `outcome` del portal, ni otros contadores, ni la
API pública del módulo.

## 2. Cambio propuesto

En `read_multi_site_runs`, sustituir la selección actual
(`done_lines[-1]` + `_time_within_run` sobre la línea) por un helper puro:

```
def _done_counter_for_run(log_text: str, log_path: Path,
                          summary: PipelineSummary) -> int | None
```

Reglas:
1. Buscar la **última** coincidencia de `_DONE_RE` en `log_text`. Si no existe,
   `None` (un `INTERRUMPIDO` no es un `DONE`).
2. Rechazar si aparece un **marcador de inicio de ejecución del portal**
   posterior a esa última coincidencia (nueva constante `_RUN_START_RE`, p. ej.
   `===\s+[^=]*Scraper`, que cubre los seis portales). En ese caso el `DONE`
   pertenece a una ejecución anterior.
3. Comprobar que el fichero `log_path` fue modificado dentro de la ventana del
   run: `run_start <= mtime <= run_end`, con `mtime` convertido a hora local
   (`datetime.fromtimestamp`). Si la ventana no puede determinarse, no se
   atribuye contador (evidencia no concluyente).
4. Solo entonces devolver el número capturado por `_DONE_RE`.

Las líneas benignas posteriores al `DONE` (p. ej. `Total ofertas almacenadas`
de Glassdoor, o cualquier salida del merge/traducción) se ignoran y **no**
invalidan el contador.

Se mantiene la condición `outcome == OUTCOME_OK`: un portal no correcto no
aporta contador.

## 3. Tests

En `scrapers-pipeline/tests/test_run_evidence.py` (fixtures con `tmp_path`):

- `run.log` cuya última línea `DONE` es de una noche anterior y con `mtime`
  fuera de la ventana ⇒ `offers_current_run is None`.
- `run.log` cuya última línea `DONE` es del run actual y `mtime` dentro de la
  ventana (ajustada con `os.utime`) ⇒ extrae el contador.
- `run.log` del run actual donde al `DONE` le siguen líneas benignas (p. ej.
  `Total ofertas almacenadas: 570`) ⇒ extrae el contador (caso Glassdoor).
- `run.log` cuyo último marcador es `INTERRUMPIDO` en vez de `DONE` ⇒ `None`
  (caso NVB).
- `run.log` donde un nuevo marcador de inicio (`=== ... Scraper ===`) aparece
  después del último `DONE` ⇒ `None` (el `DONE` es de una ejecución anterior).
- Se conservan los tests de formatos reales `DONE:` (irishjobs/jobs_ch/glassdoor
  con "ofertas nuevas"; devitjobs/nvb sin ella), ajustando el `mtime` de los
  fixtures para que caigan en la ventana.

Todos los tests son offline, sin red ni credenciales, y usan fixtures
sintéticas.

## 4. Cumplimiento de la constitución

- **Stack simple:** solo biblioteca estándar (`os`, `datetime`, `pathlib`).
- **Lógica e interfaz:** la decisión es una función pura comprobable.
- **Tests:** se añaden/ajustan tests unitarios offline.
- **Idioma:** identificadores y comentarios en inglés.
- **Sin escritura de datos** ni cambios en `specs/001-*`.

## 5. Trazabilidad

| RF | Parte del plan |
|---|---|
| RF-1 | `_done_counter_for_run` y sus tests |
