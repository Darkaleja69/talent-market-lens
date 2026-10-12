# Evidencia — spec 007: ELT como código (Asset Bundles)

Registro cronológico de las comprobaciones de cada tarea. Las capturas y
salidas saneadas se guardan junto a este fichero o en las rutas indicadas.
**Nunca** credenciales, tokens ni SAS.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-12 | T-01 | `docs/databricks-de/roadmap.md` y `docs/databricks-de/notes/README.md` versionados en `spec/007-elt-as-code` | hecho |
| 2026-10-12 | T-02 | `databricks auth profiles`: perfil `vs_code` con `Valid: Yes`; `databricks api get /api/2.1/unity-catalog/catalogs --profile vs_code` devuelve los catálogos del metastore | hecho; UC activo ⇒ edición Premium/Enterprise (inferida) |
| 2026-10-12 | T-03 | `jobs get` del job `704156530681034`: 7 tareas (bronze → 4 silver → gold → export web), job cluster `tm-lens-job` (Standard_D4ds_v4), `max_concurrent_runs: 1`, sin timeout ni `git_source`; programación `50 0 23 * * ?` (`Europe/Madrid`); `job-actual.json` saneado en la spec | hecho; corrige el supuesto de File Arrival |
