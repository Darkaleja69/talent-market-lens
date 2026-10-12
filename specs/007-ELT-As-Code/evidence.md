# Evidencia — spec 007: ELT como código (Asset Bundles)

Registro cronológico de las comprobaciones de cada tarea. Las capturas y
salidas saneadas se guardan junto a este fichero o en las rutas indicadas.
**Nunca** credenciales, tokens ni SAS.

| Fecha | Tarea | Comprobación | Resultado |
|---|---|---|---|
| 2026-10-12 | T-01 | `docs/databricks-de/roadmap.md` y `docs/databricks-de/notes/README.md` versionados en `spec/007-elt-as-code` | hecho |
| 2026-10-12 | T-02 | `databricks auth profiles`: perfil `vs_code` con `Valid: Yes`; `databricks api get /api/2.1/unity-catalog/catalogs --profile vs_code` devuelve los catálogos del metastore | hecho; UC activo ⇒ edición Premium/Enterprise (inferida) |
