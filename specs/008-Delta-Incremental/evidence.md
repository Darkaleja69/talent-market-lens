# Evidencia — spec 008: bronce deduplicado e ingesta incremental

Registro cronológico de las comprobaciones de cada tarea. **Nunca**
credenciales, tokens ni SAS.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-21 | T-01 | `design-upsert.md` con el mapa de columnas por fuente (§D0), condición de merge, cuarentena y checkpoint; tres rondas de verificación independiente | 1.ª ronda: 3 bloqueantes (columnas `job_key`/`id_oferta`/`fecha_scraped`, tipos de la temporal y `to_timestamp` con ANSI). 2.ª: B1–B3 cerrados y B4 (sintaxis de `MERGE WITH SCHEMA EVOLUTION`). 3.ª: **PASA**, con `_scraped_ts` auxiliar anotado para T-02. Corregido en `spec.md`/`plan.md`/`tasks.md`; queda pegar la auditoría |
| 2026-10-21 | T-01 | Auditoría de negocio (silver, dev): grupos con misma localización y >1 clave | `[NO VERIFICADO]` cualitativamente por la persona: bastantes grupos, también cross-site, separación 1 día – >1 mes; falta pegar la salida |
| 2026-10-21 | T-01 | Claves nulas/blancas en indeed e infojobs; temporales nulas/blancas/imparseables; capturas del mismo día por clave; offsets (guardia de T-03) | pendiente de pegar; consultas en `design-upsert.md` §Evidencia |
