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
