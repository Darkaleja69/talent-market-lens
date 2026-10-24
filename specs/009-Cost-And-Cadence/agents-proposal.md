# Propuesta de cambios en `AGENTS.md` (spec 009)

**Estado: no aplicada.** Requiere el OK de la persona. Sale del cierre de la
spec 009 (coste y cadencia).

## Comandos

Añadir a la sección **Comandos**:

- El job de producción se ejecuta **los domingos a las 23:00**
  (`Europe/Madrid`); el dashboard público se publica **los lunes a las 01:00
  UTC** (`.github/workflows/dashboard.yml`), con comprobación de frescura: si
  `meta.json.data_date` tiene más de 8 días, falla sin desplegar.
- Informe Power BI: lee `gold/pbi_export` (6 Parquet + `meta.json`) en modo
  Import con una **SAS de solo lectura** en las credenciales del dataset; el SQL
  warehouse debe estar `STOPPED`. El export lo escribe la tarea
  `Build_Report_Export` en la misma ejecución del job.
- Coste: `docs/costs.md` es el registro. Medir con Azure Cost Analysis y la
  Account Console (gratis); un despertar del warehouse 2X-Small cuesta ~0,43 €
  (10 min facturables).

## Reglas

Añadir a la sección **Reglas**:

- **No despertar el SQL warehouse sin avisar del coste.** Cada despertar son
  10 min facturables (~0,43 € en 2X-Small). Para consultas ad hoc, agruparlas en
  una sola ventana o usar un notebook en el clúster del job.
- La SAS del informe (`PBI_EXPORT_SAS`) vive en **Power BI Desktop**; nunca en
  el repositorio, logs ni capturas.
- El informe no se valida con SQL: se valida con un refresh completo y el
  warehouse `STOPPED` antes y después (historial SQL sin consultas en la
  ventana).
- El ajuste de compute (serverless, `D2ds_v6`/`D4ds_v6`) queda para la spec 010:
  `D2ds_v6` no completa el run con las cuatro silvers en paralelo.

## Ramas y commits

Sin cambios.

## Al terminar cualquier tarea

Añadir:

- Si el cambio toca el job o el informe, comprobar que el warehouse sigue
  `STOPPED` y anotar el coste del run en `docs/costs.md`.
