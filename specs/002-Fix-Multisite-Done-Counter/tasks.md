# Tareas: contador DONE por portal Multi-site

## 1. Corrección

- [x] **T-01 — Validar temporalmente el contador DONE por portal** (~20 min)
  - **RF:** RF-1.
  - **Hecho cuando:** el contador `DONE` de un portal solo se atribuye al run
    analizado si el fichero se modificó dentro de la ventana del run y no hay un
    marcador de inicio de ejecución posterior al `DONE`; en caso contrario queda
    `None`.

- [x] **T-02 — Probar evidencia DONE de otra ejecución** (~15 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Hecho cuando:** tests con `mtime`/última línea controlados distinguen el
    contador del run actual, el de una noche anterior y la ausencia de marcador.

- [x] **T-03 — Ejecutar la suite del diagnóstico** (~10 min)
  - **RF:** RF-1.
  - **Depende de:** T-01, T-02.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa completo.

## 2. Casos reales de finalización

- [x] **T-04 — Admitir líneas benignas posteriores al DONE** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Hecho cuando:** el contador se atribuye aunque al `DONE` le sigan líneas
    benignas (Glassdoor: `Total ofertas almacenadas`); un `INTERRUMPIDO` (NVB)
    no aporta contador; y un marcador de inicio de ejecución posterior al
    `DONE` lo invalida.

- [x] **T-05 — Probar los casos reales de finalización** (~15 min)
  - **RF:** RF-1.
  - **Depende de:** T-04.
  - **Hecho cuando:** tests con fixtures cubren el `DONE` de Glassdoor con
    línea posterior, el `INTERRUMPIDO` de NVB y el inicio de una nueva
    ejecución tras el `DONE`.

- [x] **T-06 — Ejecutar la suite del diagnóstico** (~10 min)
  - **RF:** RF-1.
  - **Depende de:** T-04, T-05.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa completo.
