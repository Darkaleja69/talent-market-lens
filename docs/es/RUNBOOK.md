# Runbook — ejecución del pipeline desde cero

Guía para reproducir el pipeline completo (scrapers → ADLS → Bronze → Silver →
Gold → Power BI) en una cuenta nueva, sin credenciales en el repositorio.

> Este runbook asume las **versiones vigentes** de `databricks_notebooks/`.
> La configuración de almacenamiento, catálogo, esquemas y checkpoints está
> **externalizada** (widgets de Databricks / variables de entorno): no hay
> cuentas, rutas ni tokens hard-coded en el código.

---

## 1. Requisitos

| Componente | Versión / nota |
|---|---|
| Python (local) | 3.11+ (para tests y scrapers) |
| PySpark | 3.5 (lo aporta el Databricks Runtime; en local vía `requirements.txt`) |
| Databricks | Workspace con **Unity Catalog** habilitado |
| Azure | Suscripción con **ADLS Gen2** (Hierarchical Namespace activado) |
| Power BI | Power BI Desktop (formato PBIP) |
| AzCopy | Para subir los Parquet al landing (opcional según orquestación) |

Instala dependencias locales:

```powershell
pip install -r requirements.txt
```

---

## 2. Secretos y variables de entorno

**Regla: 0 secretos en el repositorio.** Ningún token, SAS, clave de cuenta ni
cadena de conexión se versiona.

| Secreto / parámetro | Dónde vive | Cómo se pasa |
|---|---|---|
| `LANDING_SAS_TOKEN` | Variable de entorno del usuario (local) | `setx LANDING_SAS_TOKEN "<SAS del contenedor landing>"` antes de subir con AzCopy. |
| `storage_account` | Widget de Databricks | Parámetro de la tarea / widget en el notebook Bronze. |
| `container` | Widget de Databricks | Por defecto `landing`. |
| `catalog`, `bronze_schema`, `silver_schema`, `gold_schema` | Widget de Databricks | Por defecto `job_offers`, `bronze`, `silver`, `gold`. |
| `checkpoints_root` | Widget de Databricks | Opcional; por defecto `<STORAGE_BASE>/_checkpoints`. |
| Acceso de Databricks al Data Lake | **Unity Catalog Storage Credential + External Location** (Managed Identity o Service Principal) | Configuración del workspace, nunca en el repo. |

`.gitignore` excluye `.env`, logs, `quarantine/`, backups, caches y datos
Parquet para que nada de esto acabe en Git.

---

## 3. Preparar Unity Catalog y el landing

1. **Storage**: crea el contenedor `landing` en ADLS Gen2.
2. **Credencial de almacenamiento** (Storage Credential) y **External Location**
   en Unity Catalog apuntando al contenedor. Dales permisos a los usuarios/grupos
   que ejecutarán los notebooks.
3. **Catálogo y esquemas**:

   ```sql
   CREATE CATALOG IF NOT EXISTS job_offers;
   CREATE SCHEMA IF NOT EXISTS job_offers.bronze;
   CREATE SCHEMA IF NOT EXISTS job_offers.silver;
   CREATE SCHEMA IF NOT EXISTS job_offers.gold;
   ```

   > Si usas otros nombres, no edites el código: pásalos por los widgets
   > `catalog`, `bronze_schema`, `silver_schema`, `gold_schema`.

4. **Landing por fuente**: cada scraper deja sus Parquet en
   `landing/<fuente>/` (`indeed/`, `linkedin/`, `infojobs/`, `multi_site/`).

---

## 4. Subir los módulos Python a Databricks

Los notebooks Silver y Oro importan módulos del repositorio:

- `Enrich_Job_Offers_Dataframes.py` → `import Enrich_Job_Offers_Dataframes as de`
- `Prepare_Gold.py` → `import Prepare_Gold as dg`

Súbelos al workspace (Git folder, Repos o Workspace files) y asegúrate de que
están en el `sys.path` del notebook (el notebook Oro ya usa
`%load_ext autoreload`). **Nota:** si en tu workspace los módulos tienen otro
nombre, ajusta el import en la celda de configuración de cada notebook.

---

## 5. Orden de ejecución

Todos los notebooks leen su configuración de widgets y, si no se pasan,
resuelven por variable de entorno o usan el default. En Databricks Workflows,
define los parámetros a nivel de tarea.

### 5.1 Bronze — `bronze_ingest.ipynb`

Por cada fuente, Auto Loader con `trigger(availableNow=True)`:

```
cloudFiles (parquet) → Delta job_offers.bronze.<fuente>
```

Detalles: `schemaLocation` y `checkpointLocation` bajo
`<checkpoints_root>/<fuente>/{schema,ckpt}`, `mergeSchema=true`,
`_ingest_date` y `_source_file` añadidos.

**Parámetros:** `storage_account` (obligatorio), `container`, `catalog`,
`bronze_schema`, `checkpoints_root`.

### 5.2 Silver — un notebook por fuente

`silver_indeed.ipynb`, `silver_infojobs.ipynb`,
`silver_linkedin.ipynb`, `silver_multisite.ipynb`.

Cada uno: lee `bronze.<fuente>`, renombra columnas, aplica
`Enrich_Job_Offers_Dataframes.enrich()`, normaliza empresa/location, deduplica y escribe
`silver.offers_<fuente>` y `silver.companies_<fuente>`.

**Parámetros:** `catalog`, `bronze_schema`, `silver_schema`.

### 5.3 Gold — `gold_build.ipynb`

Une las cuatro Silver (`unionByName(allowMissingColumns=True)`), deduplica y
construye las tablas Gold con `Prepare_Gold.build_gold()` +
`write_gold()`. Escribe además `companies_complete_catalog`.

**Parámetros:** `catalog`, `silver_schema`, `gold_schema`.

### 5.4 Encadenado recomendado

```
Bronze (4 fuentes) → Silver Indeed / InfoJobs / LinkedIn / MultiSite (paralelo)
                   → Oro → Power BI
```

Recomendaciones de orquestación: tareas independientes por fuente, reintentos,
fuente, reintentos, sensor de llegada de ficheros y registro `success/partial/failed`.

La orquestación real de producción está versionada como código (Databricks Asset
Bundle): ver §9.

---

## 6. Tests

```powershell
python -m pytest databricks_notebooks/tests -q
```

Dos suites:

- `test_enrich_pure.py` — funciones puras de normalización (roles, modalidad
  multilingüe, periodos salariales, parseo numérico y catálogo de skills).
- `test_integration_spark.py` — integración con Spark:
  `enrich()`, `build_fact_offers()`, conversión de divisas,
  `build_fact_offer_skills()`, tabla vacía, reejecución idempotente,
  `dim_calendar()`, `build_gold()` y `write_gold()`.

Notas:

- Requiere Java 17/21 y `pyspark` (ver `requirements.txt`).
- La suite completa está pensada para ejecutarse en Databricks o en CI Linux.

---

## 7. Power BI

Abre `Job_Offers_Dashboard.pbip` (formato PBIP: informe PBIR + modelo TMDL) y
conecta el modelo a las tablas `job_offers.gold.*`. El modelo usa
`Fact_Offers`, `Fact_OfferSkills`, `Dim_Calendar`, `Dim_Companies`,
`Dim_SkillList`, `Dim_Currency` y las tablas geográficas del mapa.

---

## 8. Checklist rápida desde cero

1. `pip install -r requirements.txt`
2. Crear catálogo + schemas + External Location en Unity Catalog.
3. Subir `Enrich_Job_Offers_Dataframes.py` y `Prepare_Gold.py` al workspace.
4. Lanzar Bronze con `storage_account` (y resto de widgets) → 4 tablas Bronze.
5. Lanzar los 4 Silver → `silver.offers_*` + `silver.companies_*`.
6. Lanzar Oro → `gold.fact_offers`, `fact_offer_skills`, dims y `companies_complete_catalog`.
7. Abrir el PBIP y validar el informe.
8. `pytest` en verde antes de publicar.
9. Desplegar el job ELT programado como Asset Bundle (§9) y dejar que corra a diario.

---

## 9. La ELT como código: Databricks Asset Bundle, CI/CD y cutover

El job ELT de producción —**un job con 7 tareas**: Bronze → 4 Silver (en
paralelo) → Oro → export web— está **versionado como código** en el repositorio y
se despliega con la CLI de Databricks. `databricks.yml` contiene el bundle, sus
targets y la programación (la sobreescribe el target `prod`);
`resources/elt.job.yml`, la definición del job (tareas, `depends_on`, compute,
timeout y notificaciones). Los notebooks se referencian desde los ficheros
sincronizados del bundle, no desde una Git folder.

### 9.1 Targets y variables

Misma definición para ambos entornos; solo cambian las variables:

| Variable | `dev` | `prod` |
|---|---|---|
| `catalog` | `job_offers_dev` | `job_offers` |
| `checkpoints_root` | `<storage>/_checkpoints_dev` | `<storage>/_checkpoints` |
| `export_prefix` | `gold/web_export_dev` | `gold/web_export` |
| `job_name` | `Git Based ELT Dev` (el bundle antepone `[dev <usuario>]` al desplegar) | `Git Based ELT` |
| Programación | ninguna (solo runs manuales) | `50 0 23 * * ?`, `Europe/Madrid`, `UNPAUSED` |

El `sync` está acotado a `databricks_notebooks`, así que un deploy sube solo lo
que ejecuta el job.

### 9.2 Deploy

```powershell
databricks bundle validate -t dev  --profile <perfil>
databricks bundle deploy   -t dev  --profile <perfil>
databricks bundle summary  -t dev  --profile <perfil>   # comprobar paths y variables

databricks bundle validate -t prod --profile <perfil>
databricks bundle deploy   -t prod --profile <perfil>
```

`bundle deploy` **no ejecuta** el job: solo sube ficheros y actualiza la
definición. Los despliegues de producción son siempre manuales y validados; el
target de producción exige `run_as` y `permissions` (production mode).

### 9.3 Integración continua

- `.github/workflows/databricks-bundle-validate.yml`: ejecuta
  `databricks bundle validate -t dev` en `pull_request` / `push` que tocan
  `databricks.yml`, `resources/**`, `databricks_notebooks/**` o el propio
  workflow. Solo valida; nunca despliega. Secretos de repo: `DATABRICKS_HOST`,
  `DATABRICKS_TOKEN` (alcance dev).
- `.github/workflows/databricks-bundle-deploy-dev.yml`: deploy manual
  (`workflow_dispatch`) **solo a dev**, detrás del entorno `dev` de GitHub
  (*required reviewers*). El job dev no tiene programación, así que no correrá
  solo tras un deploy.

### 9.4 Programación, monitorización y rollback

La programación vive en el target `prod` (`pause_status` es un override, así que
pausar desde la UI no basta: el siguiente `bundle deploy -t prod` restauraría el
valor de la definición). Para cambiar el estado, se edita `databricks.yml`, se
commitea, se mergea y se despliega.

- Comprobación diaria: `databricks jobs list-runs --job-id <id del job prod> --limit 5`
  (esperado: exactamente un run al día, a las 23:00, `SUCCESS`).
- Fallos: el job notifica por correo (`on_failure` en `resources/elt.job.yml`).
- **Rollback** (volver al job manual anterior, conservado en pausa como
  respaldo): poner `pause_status: PAUSED` en el target `prod`, commit, merge,
  deploy (`bundle deploy -t prod`), verificar que el job del bundle queda
  `PAUSED` y reanudar la programación del job manual. Esperado: pausa efectiva
  < 5 min; reconciliación completa (git + deploy) < 10 min.
- El procedimiento completo de cutover/rollback, los IDs y el registro de
  ejecución de la spec 007 viven en `specs/007-ELT-As-Code/cutover.md`. Los ids
  de los jobs en uso están ahí; el job manual pausado no se borra mientras sea
  la vía de rollback.

> **Deuda conocida:** `resources/elt.job.yml` versiona el correo de notificación
> de fallo. Es previo al bundle y el repositorio es público; queda como deuda
> aceptada (decisión del 2026-10-19). Pasarlo a secreto está pendiente.
