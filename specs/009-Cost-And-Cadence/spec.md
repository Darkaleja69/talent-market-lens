# Especificación funcional: coste y cadencia del pipeline

## Contexto y objetivo

El ciclo diario de Talent Market Lens cuesta hoy unos 2 € al día y ha llegado a
5 € en días con consultas al SQL warehouse. Medido sobre el histórico real del
job (API de Jobs, 22-oct-2026), el gasto no está donde parecía: el run de
producción dura 19,2 min y cuesta ~0,24 €, mientras que **cada despertar del
warehouse cuesta ~1,29 €** (Small serverless, 12 DBU/h, mínimo facturable de 10
minutos; desde el 23-oct es 2X-Small, ~0,43 €). Los picos de 2 € y 5 € **no vienen del job ni de los runs de dev
colgados**: la facturación muestra que son sesiones de SQL aisladas (cada hueco
de más de 10 min reinicia los 10 min facturables). El timeout de 1800 s de la
007 sigue siendo una red de seguridad, pero no era la causa del gasto.

Este trabajo baja el coste mensual de Azure a **≤ 3 €/mes** sin perder la
comprobación real del pipeline, con tres palancas: ejecutar la ELT **una vez por
semana** (el scraping local sigue diario y es gratis), **sacar el informe Power
BI del SQL warehouse** (lee Parquet de ADLS en modo Import) y **abaratar el
compute del job** (`Standard_D2ds_v6`; serverless se evalúa en la 010). Se mantiene
Databricks como plataforma de la ELT y el warehouse se conserva parado para
consultas ad hoc.

No se cambia de cuenta ni de proveedor: migrar de suscripción no reduce la
tarifa recurrente (mismos SKU y región) y solo aporta créditos temporales.

## Usuarios

- La persona que mantiene el proyecto (nivel junior), que paga la factura y
  necesita entender y reproducir cada decisión de coste.
- El visitante anónimo del portfolio, que seguirá viendo el dashboard público
  con datos semanales y su fecha visible.
- El reviewer técnico, que debe encontrar un proyecto con criterio de coste
  documentado y medido, no solo funcional.

## Historias de usuario

- Como responsable del proyecto, quiero que la factura baje de ~65 €/mes a
  ≤ 3 €/mes sin romper el pipeline, para que el proyecto siga siendo sostenible.
- Como mantenedor, quiero que el informe Power BI se refresque sin encender el
  warehouse, para que consultar los datos no dependa de un recurso caro.
- Como mantenedor, quiero medir el coste con datos de facturación y no con
  estimaciones, para saber qué cambio funcionó.
- Como mantenedor, quiero que un run colgado no pueda facturar horas, para que
  un descuido no cueste más que un mes entero.
- Como visitante, quiero saber de cuándo son los datos, para no interpretar como
  actuales unos datos de la semana pasada.
- Como reviewer, quiero ver el antes y el después medidos, para valorar el
  criterio de ingeniería y no solo el resultado.

## Requisitos funcionales

### RF-1 — Cadencia semanal del job y del dashboard

**Criterio de aceptación (EARS):** Cuando se despliegue el target `prod`, el job
`Git Based ELT` deberá ejecutarse **una vez por semana** (domingo a las 23:00,
`Europe/Madrid`) y el workflow `Dashboard (GitHub Pages)` deberá publicar
después de esa ejecución (lunes a las 01:00 UTC), sin solaparse con ella; el
scraping y la subida a la landing seguirán siendo diarios y no se verán
afectados. La programación deberá vivir en `databricks.yml` (no en la UI) y
`databricks bundle validate` deberá pasar en dev y prod.

### RF-2 — El informe no consulta el SQL warehouse

**Criterio de aceptación (EARS):** Cuando se refresque el modelo semántico, las
seis tablas que hoy leen de Databricks (`Fact_Offers`, `Fact_OfferSkills`,
`Dim_SkillList`, `Dim_Calendar`, `Dim_Companies`, `Dim_Currency`) deberán leer
Parquet de ADLS en modo Import, y el SQL warehouse deberá permanecer en estado
`STOPPED` durante todo el refresh: ninguna consulta del informe deberá
despertarlo.

### RF-3 — Export del informe separado del export público

**Criterio de aceptación (EARS):** Cuando el job termine la capa Gold, deberá
escribir en `gold/pbi_export` (prefijo propio por target) un export
**idempotente** con las seis tablas completas —incluidas las columnas internas
de `fact_offers` que el informe usa y la web no publica—, **siempre en modo
`full`**, con nombres de fichero fijos y un `meta.json` con fecha de datos,
fecha de generación, modo, conteo por tabla y audiencia. El export público
`gold/web_export` deberá mantener intacto su contrato y seguir sin publicar
columnas internas (`description_clean`, `skills`, `salary_quality`,
`skills_source`, `experience_level_source`, `posted_date_raw`,
`posted_date_source`, `salary_currency`, `salary_period`, `work_mode`,
`is_salary_available`).

### RF-4 — Credenciales del refresh fuera del repositorio

**Criterio de aceptación (EARS):** Cuando se configure el origen de datos del
informe, la credencial de lectura (SAS de solo lectura acotada a
`gold/pbi_export`, o identidad de servicio) deberá guardarse en las credenciales
del dataset y **nunca** en el `.pbip`, en el repositorio, en los logs ni en las
evidencias; la caducidad y el procedimiento de rotación deberán estar
documentados en el RUNBOOK.

### RF-5 — Compute del job más barato sin perder el run

**Criterio de aceptación (EARS):** Cuando se ajuste el compute del job, deberá
adoptarse `Standard_D2ds_v6` single node (2 vCPU/8 GB: mitad de DBU y VM que el
`Standard_D4ds_v4` actual; el workspace no ofrece `D2ds_v4`) y el run semanal
deberá completarse en verde en
**≤ 30 min**; el `timeout_seconds: 1800` se mantiene en dev y prod. La
comparación con serverless Standard se traslada a la spec 010 (rendimiento),
porque debe medirse con el código definitivo de la ingesta de la 008 y no debe
bloquear el ahorro de esta spec.

### RF-6 — Medición reproducible y presupuesto con alerta

**Criterio de aceptación (EARS):** Cuando se mida el coste, deberá usarse una
fuente gratuita —Account Console → Usage para DBU por SKU y Azure Cost Analysis
para coste por recurso— o, si hace falta una medición reproducible, la consulta
versionada contra `system.billing.usage` (que cuesta ~1,29 € por despertar el
warehouse); `docs/costs.md` deberá registrar la línea base, el método y la
medición posterior con el mismo criterio; la suscripción deberá tener un
presupuesto mensual con alerta al 50 % y al 100 %.

### RF-7 — No regresión del ecosistema

**Criterio de aceptación (EARS):** Cuando se cierre la spec, no deberán haberse
modificado `scrapers-pipeline/**`, la landing, la web pública (más allá de su
programación) ni las specs 004–008; el contrato de las tablas `gold` (nombres y
semántica) deberá quedar intacto; la web pública deberá seguir publicando el
export con su `meta.json` y las suites existentes deberán quedar en verde.

### RF-8 — Documentación operativa

**Criterio de aceptación (EARS):** Cuando concluya el trabajo, el RUNBOOK
deberá documentar la cadencia semanal, el export del informe, la configuración
de credenciales y el procedimiento para medir el coste; se propondrá a la
persona el cambio de `AGENTS.md` (comandos y cadencia) y no se aplicará sin su
confirmación.

## Requisitos no funcionales

- **Stack simple (constitución 1):** no se añade runtime ni dependencia nueva;
  el conector Parquet y `AzureStorage.DataLake` son nativos de Power BI, y el
  export reutiliza el patrón ya probado de la 006.
- **Spec y código (constitución 2):** esta spec es la activa de la 009; toda
  desviación se refleja aquí junto con el código.
- **Lógica e interfaz (constitución 3):** el contrato del export vive en un
  módulo Python comprobable con tests; el notebook solo coordina.
- **Tests (constitución 4):** tests nuevos del export del informe y suites
  existentes en verde (`databricks_notebooks`, `scrapers-pipeline`).
- **Persistencia (constitución 5):** los datos crudos siguen en ADLS antes de
  la ELT; el export del informe es una proyección derivada, no la copia
  canónica.
- **Idioma (constitución 6):** identificadores y ficheros en inglés; documentos
  y mensajes para la persona, en español.
- **Seguridad:** sin secretos versionados; la SAS del informe es de solo
  lectura y de alcance mínimo; el export público no gana columnas internas.
- **Coste:** el objetivo es un techo verificable (≤ 3 €/mes), no una estimación.

- **Coste de desarrollo:** el techo de 3 €/mes es de **producción**; el
  desarrollo sigue la política local-first y el presupuesto de runs de
  `plan.md` §17 (para esta spec: ≤ 2 runs de dev y ≤ 1 sesión SQL).

## Casos límite

- El export del informe supera los 25 MiB: **no** cambia a `aggregated` (esa
  regla es solo del export público); se mide el tamaño y se documenta.
- El refresh en Power BI Service no puede autenticarse contra ADLS: el refresh
  falla de forma visible y el rollback (volver al conector de Databricks) queda
  documentado con su coste conocido.
- Un run semanal falla: el dashboard publica los datos de la semana anterior y
  `meta.json` lo hace visible; nunca se publica un export a medias.
- La spec 008 (delta incremental) cambia la duración del job: la medición se
  repite; el techo de coste no cambia.
- La comparación con serverless se pospone a la 010: en la 009 se aplica
  `D2ds_v6` (mitad de DBU y VM) sin riesgo y se documenta el motivo.
- La SAS caduca: el refresh falla de forma visible; la rotación está
  documentada y probada.
- Cruce con la 008 en `AGENTS.md` y en
  `.github/workflows/databricks-bundle-deploy-dev.yml`: se resuelve a mano al
  fusionar; si el cruce es mayor, se detiene y se consulta a la persona.
- Una ejecución manual coincide con el domingo programado: `max_concurrent_runs:
  1` impide el solape.
- El warehouse se queda encendido por una consulta ad hoc: no afecta al
  presupuesto del pipeline, pero se documenta que cada despertar cuesta ~1,29 €.

## Fuera de alcance

- Migrar el pipeline a Databricks Free Edition: no sirve como producción
  (serverless-only, sin storage externo, uso no comercial, sin SLA). Se usa como
  **laboratorio** de práctica del temario y de la UI, no como migración.
- DBCU prepago o reservas: no reducen la tarifa recurrente y añaden compromiso.
- Salir de Databricks (ELT en GitHub Actions con DuckDB/PySpark): queda
  documentado como opción futura en `plan.md` §13, no se implementa.
- Cambiar la lógica, el modelo de datos o la incrementalidad de la ELT (spec
  008).
- La comparación con serverless y el ajuste fino de compute (spec 010).
- Cambiar la web pública más allá de su programación y de la comprobación de
  frescura.
- Licencias de Power BI (Pro/PPU) y coste del puesto de trabajo local.

## Criterios de finalización

- Job de producción con programación semanal desplegada desde el bundle y
  workflow del dashboard alineado.
- Las seis tablas del informe leen Parquet de ADLS; un refresh completo termina
  en verde con el warehouse `STOPPED`.
- Export `gold/pbi_export` escrito por el job, idempotente y con `meta.json`;
  export público sin cambios de contrato.
- Compute elegido con medición antes/después y run semanal en verde en ≤ 30 min.
- `docs/costs.md` con línea base y medición posterior; presupuesto y alertas
  activos.
- Suites en verde: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q`.
- **Comprobación real:** un run completo de producción lanzado a mano tras el
  deploy (verde, export actualizado, informe refrescado sin despertar el
  warehouse y web publicada con la fecha correcta) **más** el primer run
  programado del domingo, con el coste medido del periodo.
- No se hace push ni merge sin validación de la persona (flujo de `AGENTS.md`).

## Decisiones aclaradas

- 2026-10-23: se mantiene la ELT en Databricks; se prioriza esta spec sobre la
  008, que queda aparcada en su worktree y con su rama respaldada.
- 2026-10-23: scraping diario (local, sin coste de Azure) y ELT semanal.
- 2026-10-23: el informe sale del warehouse; el warehouse se conserva parado
  para consultas ad hoc (cada despertar, ~1,29 €).
- 2026-10-23: el export del informe es **independiente** del público, para no
  debilitar la regla de «solo campos públicos» de la 006.
- 2026-10-23: no se migra de cuenta de Azure.
- 2026-10-23: la hoja de ruta se renumera para dejar el coste en la 009
  (rendimiento → 010, streaming → 011, gobernanza → 012) y adopta la política
  local-first con presupuesto de runs por spec (`plan.md` §17).
- 2026-10-23: tres entornos con usos separados — local (tests y modelos
  pequeños), Databricks Free Edition (laboratorio de certificación, UI y
  MLflow) y el workspace pagado (pipeline real, ≤ 3 €/mes). La spec de ML/MLOps
  queda para después del examen, con presupuesto propio.
