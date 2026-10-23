# Tareas: coste y cadencia del pipeline

Tareas ordenadas por dependencia y dimensionadas para sesiones de ~20–40
minutos. **Objetivo → pasos y pistas → hecho cuando → evidencia.** Ninguna tarea
se marca como hecha sin ejecutar su comprobación.

La persona revisa el plan y cada grupo; el asistente implementa y la verificación
es independiente (`verifier`), que revisa el diff de la tarea contra esta spec y
el plan antes de cada commit.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación, con
  mensaje convencional y referencia a la tarea
  (p. ej. `feat(report): T-05 export del informe con contrato completo`).
- Antes del commit, el verificador revisa el diff de la tarea contra esta spec
  y el plan; no se commitea con hallazgos `BLOQUEANTE`.
- El commit local es un checkpoint; no se hace push sin revisión previa de la
  persona.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`).
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec.
- Comandos de comprobación: `databricks bundle validate -t dev|prod` y las
  suites (`python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio).
- **Producción siempre manual y validada:** el deploy a prod de T-14 requiere el
  OK explícito de la persona.

## 1. Medición y línea base

- [ ] **T-01 — Línea base de coste** (~30 min)
  - **RF:** RF-6.
  - **Objetivo:** saber cuánto se gasta y en qué SKU, con datos de facturación y
    no con estimaciones.
  - **Pasos:** obtener la línea base de dos fuentes **gratuitas**: Account
    Console → Usage (DBU por SKU y día) y Azure Cost Analysis agrupado por
    recurso (VM, almacenamiento y workspace). Dejar la consulta versionada en
    `docs/costs/billing_by_sku.sql` como método reproducible y ejecutarla solo
    si el console no da el desglose necesario (~1,29 € con el warehouse Small
    actual). Escribir `docs/costs.md` con método, supuestos de tarifa y la tabla
    de línea base.
  - **Hecho cuando:** `docs/costs.md` tiene la línea base con fecha, y la suma
    de SKU + VM + storage explica el gasto diario observado, incluido el
    ~0,80 € de los días «solo job» que hoy no cuadra con la tarifa de lista.
  - **Evidencia:** captura de Usage y de Cost Analysis (sin credenciales) en
    `evidence.md`.

- [ ] **T-02 — Inventario del contrato del informe** (~25 min)
  - **RF:** RF-3, RF-7.
  - **Objetivo:** fijar exactamente qué columnas y tablas necesita el modelo
    antes de tocar nada.
  - **Pasos:** completar en `evidence.md` la tabla de las seis particiones M:
    tabla Gold de origen, columnas usadas (`sourceColumn` del TMDL) y columnas
    que el export público **no** incluye. Confirmar la lista de 31 columnas de
    `fact_offers`, las 7 de `companies_complete_catalog` y las 5 de
    `dim_currency` con la **API de Unity Catalog** (gratis):
    `databricks api get /api/2.1/unity-catalog/tables/job_offers.gold.<tabla>`.
    No usar `DESCRIBE TABLE`: es SQL y cuesta ~1,29 € por despertar el warehouse.
  - **Hecho cuando:** la tabla del inventario está completa y no queda ninguna
    columna del modelo sin origen identificado.
  - **Evidencia:** tabla en `evidence.md` + salida de la API.

- [ ] **T-02b — Hoja de ruta renumerada y política de verificación** (~25 min)
  - **RF:** RF-6, RF-8.
  - **Depende de:** aprobación de la persona (cambia la planificación del track).
  - **Objetivo:** que la 009 no choque con los números ya planificados y que el
    resto del track nazca con presupuesto de runs.
  - **Pasos:** en `docs/databricks-de/roadmap.md`, renumerar las specs futuras
    (rendimiento 009→010, streaming 010→011, gobernanza 011→012), añadir la fila
    «coste y cadencia → 009» al mapa de dominios, reajustar el calendario para
    que siga cabiendo antes del examen y añadir la sección de verificación
    local-first con el presupuesto por spec de `plan.md` §17; dejar anotados los
    tres entornos (local, Free Edition como laboratorio de certificación y UI,
    workspace pagado como producción) y la futura spec de ML/MLOps tras el
    examen; anotar en la bitácora (spec 009 creada, warehouse Small, modelo de
    coste).
  - **Hecho cuando:** el roadmap no tiene números duplicados, el calendario
    cierra antes del 21–27 de diciembre y la política y los entornos quedan
    escritos.
  - **Evidencia:** diff del roadmap + fila en `evidence.md`.

## 2. Cadencia semanal

- [ ] **T-03 — Cron semanal del job** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Objetivo:** que el job de producción se ejecute una vez por semana desde
    el bundle.
  - **Pasos:** en `databricks.yml` (target `prod`), cambiar
    `quartz_cron_expression` a `50 0 23 ? * MON` manteniendo
    `timezone_id: Europe/Madrid`; dejar `pause_status` como esté hoy; ejecutar
    `databricks bundle validate -t dev` y `-t prod` y `databricks bundle summary
    -t prod` para ver el cron resultante. **No desplegar a prod todavía.**
  - **Hecho cuando:** validate en verde en ambos targets y `summary` muestra el
    cron semanal.
  - **Evidencia:** salida de `summary` en `evidence.md`.

- [ ] **T-04 — Cron del dashboard y frescura** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-03.
  - **Objetivo:** que la web se publique después del job semanal y no repita el
    mismo export siete veces.
  - **Pasos:** en `.github/workflows/dashboard.yml`, cambiar el `cron` a
    `0 1 * * TUE`; añadir al paso de validación una comprobación de frescura
    (si `meta.json.data_date` tiene más de 8 días, fallar con mensaje claro en
    español y no desplegar). Probar con `workflow_dispatch`.
  - **Hecho cuando:** el workflow en verde publica con datos frescos y falla de
    forma visible con un `meta.json` viejo (probado con un valor simulado en
    local).
  - **Evidencia:** URL del run + prueba de la comprobación de frescura.

## 3. Export del informe

- [ ] **T-05 — Módulo `Prepare_PBI_Export.py` con tests** (~40 min)
  - **RF:** RF-3.
  - **Depende de:** T-02.
  - **Objetivo:** un contrato comprobable para el export del informe, separado
    del público.
  - **Pasos:** crear el módulo con la lista de columnas por tabla (las 31 de
    `fact_offers` y las de las cinco dimensiones), `build_meta` con
    `mode: full`, `audience: pbi` y conteos, y escritura idempotente con
    `coalesce(1)` reutilizando el patrón de `Prepare_Web_Export`. Las 31 de
    `fact_offers` son 29 de Gold más `GeoCountry`/`GeoRegion`, que se derivan
    con `REGION_MAP` y la normalización de país de `Prepare_Web_Export`; `skills`
    se escribe como texto (`concat_ws(", ", coalesce(skills, array()))`, que da
    cadena vacía si el array es nulo) porque en Gold es `ARRAY<string>`. Tests
    en `databricks_notebooks/tests/test_pbi_export.py`: contrato de columnas,
    equivalencia de `GeoCountry`/`GeoRegion` con el export público, `meta.json`,
    idempotencia y que el export público no cambia.
  - **Hecho cuando:** `python -m pytest databricks_notebooks/tests -q` en verde
    y el módulo no tiene dependencias de terceros (puede importar módulos del
    propio repo, como `Prepare_Web_Export`).
  - **Evidencia:** salida de la suite + diff revisado.

- [ ] **T-06 — Tarea del job y variable por target** (~30 min)
  - **RF:** RF-3.
  - **Depende de:** T-05.
  - **Objetivo:** que el job escriba el export del informe al terminar Gold.
  - **Pasos:** crear `databricks_notebooks/report_export_build.ipynb` (widgets
    `catalog`, `storage_account`, `container`, `pbi_export_prefix`); añadir la
    tarea `Build_Report_Export` en `resources/elt.job.yml` con
    `depends_on: Gold_Complete_Catalog` y sin bloquear `Build_for_Web-Dashboard`;
    añadir la variable `pbi_export_prefix` con `gold/pbi_export` (prod) y
    `gold/pbi_export_dev` (dev); `bundle validate` en ambos targets.
  - **Hecho cuando:** validate en verde y `summary` muestra la tarea y la
    variable en cada target.
  - **Evidencia:** `summary` en `evidence.md`.

- [ ] **T-07 — Run dev del export del informe** (~25 min)
  - **RF:** RF-3, RF-7.
  - **Depende de:** T-06.
  - **Objetivo:** comprobar el export contra datos reales en dev.
  - **Pasos:** desplegar dev y lanzar el job dev completo (~0,25 €); verificar
    los siete ficheros en `gold/pbi_export_dev`, el `meta.json` y que los
    conteos coinciden con las tablas Gold dev; comprobar que
    `gold/web_export_dev` sigue igual.
  - **Hecho cuando:** run verde, siete ficheros presentes, mismo número de filas
    que
    Gold y export público intacto.
  - **Evidencia:** run id, duración, tabla de conteos y `meta.json` en
    `evidence.md`.

## 4. Informe sin warehouse

- [ ] **T-08 — Repuntar las tres particiones simples** (~30 min)
  - **RF:** RF-2, RF-4.
  - **Depende de:** T-07.
  - **Objetivo:** empezar por las tablas sin lógica, para validar credenciales y
    conector con riesgo mínimo.
  - **Pasos:** en `Fact_OfferSkills.tmdl`, `Dim_SkillList.tmdl` y
    `Dim_Currency.tmdl`, sustituir `Databricks.Catalogs` por
    `AzureStorage.DataLake` + `Parquet.Document` (patrón de `plan.md` §6), con
    la URL sin SAS; configurar la credencial en el origen de datos; refrescar
    esas tres tablas.
  - **Hecho cuando:** las tres refrescan en Power BI Desktop sin error y los
    conteos coinciden con Gold.
  - **Evidencia:** captura del refresh + conteos.

- [ ] **T-09 — Repuntar `Fact_Offers`, `Dim_Calendar` y `Dim_Companies`** (~35 min)
  - **RF:** RF-2, RF-4.
  - **Depende de:** T-08.
  - **Objetivo:** completar el cambio sin tocar la lógica de negocio.
  - **Pasos:** en `Fact_Offers.tmdl`, leer `fact_offers.parquet` y **eliminar**
    la lógica M de `GeoCountry`/`GeoRegion` y el `NestedJoin` contra
    `Dim_RegionMap` (las calcula el export del informe, no Gold; se verifica que
    las medidas de país y región coinciden antes y después); la tabla calculada
    `Dim_RegionMap` se conserva sin uso, documentada, para no tocar más el
    modelo; en
    `Dim_Calendar.tmdl` conservar el filtro de `Year`; en `Dim_Companies.tmdl`
    leer `dim_companies.parquet`; refresh completo del modelo.
  - **Hecho cuando:** refresh completo en verde y las medidas principales
    (ofertas, skills, salario medio, países y regiones) coinciden con el valor
    previo al cambio.
  - **Evidencia:** captura comparativa antes/después de las medidas.

- [ ] **T-10 — Verificación sin warehouse** (~25 min)
  - **RF:** RF-2, RF-4, RF-7.
  - **Depende de:** T-09.
  - **Objetivo:** demostrar que el informe ya no despierta el warehouse.
  - **Pasos:** confirmar el warehouse en `STOPPED`; lanzar el refresh completo
    (Desktop o Service); confirmar que sigue `STOPPED` al terminar y que no hay
    DBU de SKU SQL en la ventana; comprobar que el export público y la web no
    cambian. Documentar en el RUNBOOK la credencial, su caducidad y la rotación.
  - **Hecho cuando:** refresh en verde con el warehouse `STOPPED` antes y
    después, sin DBU SQL en la ventana.
  - **Evidencia:** estado del warehouse (captura) + consulta de `system.billing`
    de la ventana.

## 5. Compute y presupuesto

- [ ] **T-11 — Spike de serverless en modo Standard** (~40 min)
  - **RF:** RF-5.
  - **Depende de:** T-07.
  - **Objetivo:** saber si serverless soporta el patrón y cuánto ahorra, antes
    de comprometerse.
  - **Pasos:** en dev, definir el compute serverless con
    `performance_target: STANDARD`; lanzar el job dev; comprobar
    `cloudFiles`, los imports de módulos del repo y `%load_ext`; medir duración
    y DBU del run (`system.billing.usage`, SKU de serverless). Si falla, anotar
    el error exacto y pasar al plan B.
  - **Hecho cuando:** hay una decisión documentada con evidencia (funciona o
    no) y una comparación de coste con el run clásico.
  - **Evidencia:** run id, duración, DBU y error (si lo hay) en `evidence.md`.

- [ ] **T-12 — Aplicar el compute elegido** (~30 min)
  - **RF:** RF-5.
  - **Depende de:** T-11.
  - **Objetivo:** dejar el job con la opción más barata que cumpla el run.
  - **Pasos:** aplicar serverless Standard o, en su defecto, `D2ds_v4` y limpiar
    `kind: CLASSIC_PREVIEW`; `bundle validate`; run dev de comprobación; medir
    duración y coste.
  - **Hecho cuando:** run dev en verde en ≤ 30 min y coste por run menor que la
    línea base.
  - **Evidencia:** diff + medición antes/después.

- [ ] **T-13 — Presupuesto y alertas** (~20 min)
  - **RF:** RF-6, RF-8.
  - **Depende de:** T-01.
  - **Objetivo:** que un gasto anómalo avise solo.
  - **Pasos:** crear presupuesto mensual de 15 € en la suscripción con alertas
    al 50 % y 100 % al correo de la persona; etiquetar los recursos del proyecto
    (`project=talent-market-lens`); anotar en `docs/costs.md` cómo se consulta
    el presupuesto y qué hacer si salta.
  - **Hecho cuando:** el presupuesto existe con las dos alertas y la etiqueta
    aplicada a storage y workspace.
  - **Evidencia:** captura del presupuesto (sin datos sensibles).

## 6. Deploy a producción y cierre

- [ ] **T-14 — Deploy a producción y cutover de cadencia** (~30 min)
  - **RF:** RF-1, RF-5.
  - **Depende de:** T-04, T-10, T-12, T-13.
  - **Objetivo:** llevar todo a producción sin sorpresas.
  - **Pasos:** con el OK explícito de la persona, `databricks bundle validate -t
    prod` y `databricks bundle deploy -t prod`; comprobar en `jobs get` el cron
    semanal, la tarea nueva, el compute y el `timeout_seconds: 1800`; lanzar un
    run manual de producción y verificar export del informe, export público y
    web; dejar escrito el rollback (cron diario y particiones M al conector de
    Databricks) en `evidence.md`.
  - **Hecho cuando:** deploy en verde, run manual de producción completo en
    verde y los dos exports actualizados.
  - **Evidencia:** `jobs get` saneado, run id, duración, `meta.json` de ambos
    exports y URL de la web.

- [ ] **T-15 — Evidencia, documentación y comprobación real** (~30 min)
  - **RF:** RF-6, RF-7, RF-8.
  - **Depende de:** T-14.
  - **Objetivo:** cerrar con todo verificable y con el coste medido.
  - **Pasos:** consolidar `evidence.md`; suites en verde
    (`databricks_notebooks` y `scrapers-pipeline`); actualizar `docs/RUNBOOK.md`
    (cadencia, export del informe, credenciales, medición de coste) y la
    propuesta de `AGENTS.md` (sin aplicar sin OK); repetir la consulta de
    facturación y anotar el coste del periodo en `docs/costs.md`; confirmar el
    primer run programado del lunes; solicitar a la persona la revisión final
    (push/merge/tag solo con su OK).
  - **Hecho cuando:** suites verdes, evidencia completa, coste del periodo
    ≤ 3 €/mes y primer run semanal programado en verde.
  - **Evidencia:** salidas de suites, consulta de facturación y fila de cierre
    en `evidence.md`.
