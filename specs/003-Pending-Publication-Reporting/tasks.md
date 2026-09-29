# Tareas: informar la publicación pendiente en el diagnóstico

Las tareas están ordenadas por dependencia. Cada una se dimensiona para una
sesión corta y se cierra con un commit atómico cuando pasa su comprobación.

## Convención de commits

- Un commit atómico por tarea al pasar su comprobación, con mensaje convencional
  y referencia a la tarea (p. ej. `fix(publication): T-01 ...`).
- No se hace push sin revisión previa.

## 1. Corrección y verificación

- [x] **T-01 — No degradar la publicación pendiente a "no comprobada"** (~25 min)
  - **RF:** RF-1.
  - **Hecho cuando:** un test de integración demuestra que
    `run_diagnostic(reader=...)` con un objeto remoto no visible produce
    `PUBLICATION_PENDING`, la fuente no es fallida y el informe en español indica
    "pendiente de publicar"; un fallo de conectividad sigue siendo `not_checked`.
    Se actualiza el test que hoy documenta el defecto.

- [x] **T-02 — Ejecutar la suite del diagnóstico** (~15 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` termina en
    verde.
