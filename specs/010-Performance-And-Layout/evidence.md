# Evidencia — rendimiento del incremental, layout físico y compute

> Registro de comprobaciones de la spec 010, con el formato de la 007/008:
> fecha, tarea, comprobación y resultado. Solo hechos verificables: lo que no
> se ha medido queda `[NO VERIFICADO]`.
>
> **Herencia:** el estado de partida (runs A, B, A2, B2 y B3 del bloque B, con
> ids, tiempos y conteos) está en `specs/008-Delta-Incremental/evidence.md` y en
> `docs/costs.md`. No se copia aquí; se referencia.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-28 | — | Creación de la spec a partir del bloqueo de T-10 (008) | Borrador de `spec.md`, `plan.md` y `tasks.md`; rama `spec/010-performance-and-layout` desde `spec/008-delta-incremental` (`af22467`), pendiente de rebase sobre `main` al cerrar o descopear la 008. Sin runs, sin código y sin cambios en `specs/008-*` |
| 2026-10-28 | — | Decisiones de la persona | **Bloque B descopeado de la 008**: la validación A/B y la producción se cierran aquí (T-08/T-09). **Presupuesto aprobado:** 3 runs + 0 SQL dentro del techo de 1,8 € de la 009 (sustituye a 2 runs + 1 SQL). **Fix de `keyed` revertido** en la 008 (se re-evalúa en T-03). **T-06 confirmada en verde** (run del domingo 25-oct y dashboard del lunes); falta anotar id y tiempos en la 008 |
| 2026-10-28 | — | Verificación independiente del borrador y del bloqueo de T-10 | **PASA CON MENORES.** Dos bloqueantes corregidos en la 008/`costs.md`: (1) «no es volumen, es coste fijo» era una conclusión sin medir y contradecía que `_ingest_date` es la fecha del lote (la ventana recalcula casi todo el bronce; el reparto lo mide T-01); (2) el coste se estimaba a 0,24 €/run ignorando que los `TIMEDOUT` duran ~30 min → ~0,38 € por duración y la 008 queda en ~2,07 € frente a su techo de 2,0 €. Menores corregidos en la 010: presupuesto (3 runs + 0 SQL, aprobado), bronce vs silver en §2.1, inventario de `_run_once` (nombres, `left_semi`/`left_anti`, 6+1 frames), medición por **acción** (`setJobGroup` + listener) y no por línea perezosa, H5/H6 ya no se declaran gratis, T-03 en dos commits y T-04 con decisión preliminar + confirmación en T-08. `[NO VERIFICADO]`: ids, tiempos, conteos y SHA-256 (exigen workspace); T-06 confirmada pero sin id ni tiempos anotados |
