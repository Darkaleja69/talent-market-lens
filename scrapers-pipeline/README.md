# Pipeline diario: Scrapers -> ADLS Gen2 -> Databricks (medallion)

Este pipeline:
1. Corre todos los scrapers desde un unico `.ps1` en tu PC (Task Scheduler a las **00:00**).
2. Sube los archivos CSV/Parquet resultantes a **Azure Data Lake Storage Gen2** con `azcopy`.
3. En **Databricks**, un Job diario consume esos archivos con **Autoloader** y los escribe en tablas **Bronze** (una por scraper).
4. Un segundo paso hace `MERGE` a la tabla **Silver.ofertas** unificada.
5. Todo bajo **Unity Catalog**: schema `scraper_pipeline.bronze` y `scraper_pipeline.silver`.

```
 PC local                       Azure ADLS Gen2                  Databricks Unity Catalog
 ---------                      ---------------                  -----------------------
 run_scrapers_and_upload.ps1    landing/                         scraper_pipeline.bronze.scraper_*
   ejecuta scrapers   -->         scraper_a/dia=YYYY-MM-DD/  --Autoloader-->  bronze.scraper_a  \
   azcopy upload                  scraper_b/dia=YYYY-MM-DD/  --Autoloader-->  bronze.scraper_b   } MERGE -> silver.ofertas
                                 ...
```

---

## PLAN DE EVOLUCION DEL PROYECTO

El objetivo es evolucionar desde un conjunto de scrapers hacia una plataforma de
inteligencia del mercado laboral y recomendacion personalizada de empleo.

```text
Scrapers
  -> ADLS landing
  -> Databricks Bronze
  -> dbt staging/intermediate/marts
  -> Gold y metricas de mercado
  -> Power BI

CV
  -> Streamlit/FastAPI
  -> perfil estructurado
  -> filtros y busqueda semantica
  -> ranking
  -> recomendaciones
```

### Decisiones de arquitectura

- Databricks sera la plataforma principal.
- ADLS Gen2 almacenara los ficheros originales.
- Autoloader se encargara de Bronze.
- dbt gestionara Silver y Gold.
- Power BI servira para el analisis de mercado.
- Streamlit sera la primera interfaz del recomendador.
- FastAPI podra anadirse despues como backend profesional.
- Airflow se incorporara cuando sustituya realmente a Task Scheduler.
- No se mantendran varios orquestadores ejecutando las mismas tareas.

### Fase 0: estabilizar el pipeline

Antes de anadir funcionalidades de IA:

- Unificar `scraper_pipeline` y `job_offers`.
- Unificar `job_id`, `offer_id` y `job_id_unified`.
- Unificar `source`, `source_scraper` y `scraper_source`.
- Corregir las referencias a `silver_merge.py`.
- Actualizar las referencias a `<storage-account>` y `<storage-account>`.
- Resolver los procesos que terminan con `exit=-1`.
- Registrar cada scraper como `success`, `partial` o `failed`.
- Permitir procesar las fuentes correctas aunque una falle.
- Crear la tabla `pipeline_runs`.
- Guardar filas, duracion, tamano, errores y frescura de cada ejecucion.
- Anadir alertas cuando una fuente deje de producir datos.

Resultado esperado: un fallo parcial no debe impedir procesar todo el dia.

### Fase 1: modelo de datos profesional

#### `dim_job`

Una version actual de cada oferta. Campos prioritarios:

```text
job_id_unified, source_job_id, source_site, canonical_url, apply_url,
company_name_normalized, company_id, location_country_iso, latitude, longitude,
role_category, seniority_normalized, work_mode_normalized,
salary_min_annual, salary_max_annual, salary_eur_min, salary_eur_max,
salary_confidence, required_skills, preferred_skills, languages_required,
education_required, visa_sponsorship, travel_required, benefits_array,
first_seen_at, last_seen_at, last_changed_at, is_active, closed_at,
content_hash, data_quality_score, deduplication_method
```

#### `fact_job_observation`

Una fila por oferta y ejecucion para conservar el historico:

```text
job_id_unified, run_id, observed_at, was_present, salary_min, salary_max,
description_hash, num_applicants, is_active
```

Permitira analizar ofertas nuevas y desaparecidas, cambios salariales, demanda
por tecnologia y tiempo de permanencia de una oferta.

#### `dim_company`

```text
company_id, company_name, company_name_normalized, industry, company_size,
company_url, rating, review_count
```

#### `dim_skill`

```text
skill_id, skill_name, skill_category, canonical_name, aliases
```

No se debe guardar todo en una unica tabla. La tabla de ofertas debe representar
la oferta actual y las tablas de observaciones, empresas y skills deben mantener
los detalles historicos y reutilizables.

### Fase 2: incorporar dbt

dbt se utilizara para las transformaciones SQL, tests, documentacion y linaje.
Autoloader seguira siendo responsable de la ingesta y PySpark del procesamiento
pesado, NLP y embeddings.

```text
models/
  staging/
    stg_indeed.sql
    stg_linkedin.sql
    stg_infojobs.sql
  intermediate/
    int_jobs_normalized.sql
    int_jobs_deduplicated.sql
    int_job_observations.sql
  marts/
    dim_job.sql
    dim_company.sql
    dim_skill.sql
    fact_job_observation.sql
    mart_market_trends.sql
    mart_salary_analysis.sql
```

Anadir:

- Tests `unique` y `not_null`.
- Valores aceptados para modalidad y seniority.
- Tests de salarios negativos.
- Tests de duplicados.
- Tests de relaciones entre dimensiones y hechos.
- Modelos incrementales.
- Snapshots para cambios historicos.
- Descripciones de tablas y columnas.
- Linaje generado por dbt.

### Fase 3: producto de analisis laboral

El dashboard de Power BI debe mostrar:

- Ofertas activas por pais y ciudad.
- Salario medio por rol y seniority.
- Skills mas demandadas.
- Evolucion temporal de la demanda.
- Empresas que mas contratan.
- Portales con mas ofertas.
- Reparto remoto, hibrido y presencial.
- Tiempo medio de permanencia de las ofertas.
- Comparativa entre Data Analyst, Data Engineer y Data Scientist.
- Ofertas nuevas de la ultima semana.

### Fase 4: recomendador de CV

La primera version se implementara fuera de Power BI:

- Streamlit para la interfaz.
- PyMuPDF para PDF.
- `python-docx` para DOCX.
- `sentence-transformers` para embeddings.
- FAISS, pgvector o Databricks Vector Search para recuperar ofertas.
- Databricks SQL Connector para consultar los datos.

Flujo funcional:

1. El usuario sube un CV.
2. Se extrae el texto.
3. Se genera un perfil estructurado.
4. Se extraen skills, experiencia, roles, idiomas y preferencias.
5. Se aplican filtros obligatorios.
6. Se recuperan ofertas relevantes.
7. Se calcula una puntuacion hibrida.
8. Se devuelve el top 5 con explicaciones.
9. Se guarda el feedback del usuario.

La puntuacion inicial combinara coincidencia de skills, similitud semantica,
rol, seniority, localizacion, modalidad, salario y antiguedad. Los pesos deben
estar configurados y versionados, no ocultos en el codigo.

Cada recomendacion debe mostrar:

- Puntuacion total.
- Skills coincidentes.
- Skills ausentes.
- Motivo de la recomendacion.
- Evidencia de la descripcion.
- Nivel de confianza.
- Salario y modalidad.
- Enlace para aplicar.

No se utilizara inicialmente un LLM como juez unico del ranking. El ranking debe
ser reproducible. El LLM podra utilizarse mas adelante para extraer informacion y
redactar explicaciones.

### Fase 5: tablas del recomendador

#### `candidate_profile`

```text
profile_id, skills, years_experience, preferred_roles,
preferred_locations, preferred_work_modes, minimum_salary, profile_version
```

#### `recommendation_run`

```text
run_id, profile_id, created_at, embedding_model, ranking_version
```

#### `recommendation_result`

```text
run_id, job_id_unified, rank, total_score, skill_score, semantic_score,
location_score, salary_score, matched_skills, missing_skills, explanation
```

#### `user_feedback`

```text
profile_id, job_id_unified, action, created_at
```

El feedback permitira evolucionar hacia un modelo de learning-to-rank.

### Fase 6: IA avanzada

Despues del MVP se podran anadir:

- Azure AI Document Intelligence para CVs escaneados.
- Azure OpenAI para extraccion y explicaciones.
- Databricks Vector Search.
- MLflow para registrar versiones del recomendador.
- Model Serving para exponer el modelo.
- Evaluacion automatica de la relevancia.
- Comparacion entre reglas, embeddings y rerankers.

El CV es un dato sensible. Por defecto no se debe guardar el PDF original ni
datos personales que no sean necesarios. Se debe documentar el proveedor externo,
cifrar el almacenamiento, aplicar retencion limitada y permitir borrar el perfil.

### Fase 7: Airflow

Airflow tendra sentido cuando orqueste el flujo completo:

```text
scrapers
  -> validacion
  -> landing
  -> Bronze
  -> dbt build
  -> embeddings
  -> indice vectorial
  -> recomendaciones
  -> alertas
```

El DAG debe incluir tareas independientes por scraper, reintentos, backfills,
sensores de llegada de archivos, ejecucion de Databricks, ejecucion de dbt,
actualizacion del indice y notificaciones.

Hasta entonces, la orquestacion sera:

```text
Task Scheduler -> Databricks File Arrival -> dbt
```

Para aprender Airflow se puede usar Docker Compose. Antes de convertirlo en el
orquestador principal conviene mover los scrapers que dependen del navegador
fuera del PC personal, por ejemplo a una VM, contenedor o servicio cloud.

### Fase 8: calidad y portfolio

Anadir:

- Docker Compose.
- Tests unitarios de parsers.
- Tests de integracion con HTML de ejemplo.
- dbt tests.
- Linting y formateo.
- GitHub Actions.
- Terraform o Bicep para Azure.
- Dataset anonimizado de demostracion.
- Monitorizacion del pipeline.
- Documentacion de costes.
- Diagrama de arquitectura.
- Registro de decisiones tecnicas.
- README reproducible.
- Capturas del dashboard.
- Video corto de la aplicacion.

### Orden definitivo de implementacion

1. Estabilizar scrapers y ejecuciones.
2. Corregir el modelo y los nombres.
3. Crear el historico de observaciones.
4. Incorporar dbt.
5. Crear el dashboard de mercado laboral.
6. Construir el recomendador determinista.
7. Anadir embeddings y busqueda semantica.
8. Guardar feedback de usuario.
9. Introducir MLflow.
10. Sustituir Task Scheduler por Airflow cuando el sistema este preparado.

### Definicion de terminado

El proyecto estara listo para portfolio cuando pueda demostrar:

- Pipeline diario reproducible y monitorizado.
- Ingesta incremental y transformaciones versionadas.
- Tests de calidad y linaje de datos.
- Historico de ofertas y analisis de mercado.
- Dashboard Power BI funcional.
- Aplicacion que recibe un CV y devuelve cinco ofertas explicadas.
- Ranking reproducible y versionado.
- Feedback almacenado para mejorar el modelo.
- Seguridad y privacidad documentadas.
- Despliegue reproducible y demo publica con datos anonimizados.

---

## INDICE

- [FASE 1 â€” Azure: crear Storage Account y contenedor landing](#fase-1)
- [FASE 2 â€” Azure: crear Service Principal y SAS token](#fase-2)
- [FASE 2-bis â€” Alternativa robusta: generar SAS con PowerShell (recomendado)](#fase-2bis)
- [FASE 3 â€” Local: instalar azcopy y configurar el env var](#fase-3)
- [FASE 4 â€” Local: ajustar config.ps1 y probar el script](#fase-4)
- [FASE 5 â€” Local: agendar en Task Scheduler a las 00:00](#fase-5)
- [FASE 6 â€” Databricks: crear Catalog y Schemas en Unity Catalog](#fase-6)
- [FASE 7 â€” Databricks: crear External Location para ADLS](#fase-7)
- [FASE 8 â€” Databricks: notebook Bronze (Autoloader por scraper)](#fase-8)
- [FASE 9 â€” Databricks: notebook Silver (MERGE)](#fase-9)
- [FASE 10 â€” Databricks: crear Workflow diario](#fase-10)
- [Plan de evolucion del proyecto](#plan-de-evolucion-del-proyecto)
- [Verificacion y troubleshooting](#verificacion)

---

<a id="fase-1"></a>
## FASE 1 â€” Azure: crear Storage Account y contenedor `landing`

1. Azure Portal (<https://portal.azure.com>) -> **Create a resource** -> **Storage account**.
2. **Basics**:
   - Subscription / Resource Group: a tu eleccion.
   - Storage account name: `mystorageaccount` (minusculas, globalmente unico). Apunta el nombre â€” va en `config.ps1`.
   - Region: la misma region que tu Databricks Workspace (importante para rendimiento y coste de salida).
   - Primary service **Azure Data Lake Storage Gen 2** -> activar **Hierarchical namespace**.
   - Redundancy: LRS o GRS segun preferencia (LRS suele bastar).
3. **Advanced**: Debian dejar **HNS enabled**, **Blob public access = Disabled**, **Secure transfer required = Enabled**.
4. Review + Create. Espera a que se despliegue (~30s).
5. Entra en el recurso recien creado -> **Containers** (en menu "Data storage") -> **+ Container**:
   - Name: `landing`
   - Anonymous access level: **Private (no anonymous access)**.
6. Ya tienes la URL base: `https://mystorageaccount.blob.core.windows.net/landing`.

> Tip: tambien puedes acceder por `abfss://landing@mystorageaccount.dfs.core.windows.net` desde Databricks. Usamos `blob.core` para azcopy (mas simple).

---

<a id="fase-2"></a>
## FASE 2 â€” Azure: crear SAS token

Usaremos un **SAS token de cuenta** con permisos limitados. Alternativamente podrias crear un Service Principal (mas seguro para entornos compartidos), pero el SAS es mas simple para una primera version local.

1. En el Storage Account -> **Security + networking** -> **Access keys** -> **Generate SAS**.
2. O bien, en menu izquierdo -> **Security + networking** -> **Shared access signature**:
   - Allowed services: **Blob** (deja los demas desactivados si solo necesitas blob).
   - Allowed resource types: Container, Object.
   - Allowed permissions: **Write**, **Add**, **Create**, **List**. (No Read ni Delete desde el PC â€” menos riesgo si se filtra el token.)
   - Expiry date: 1 ano (renovarsaldo a fin de ano). Apunta la fecha en tu calendario.
   - Allowed IP addresses: opcional â€” tu IP publica domestica (cuidado si tienes IP dinamica).
3. Click **Generate SAS and connection string**.
4. Copia **solo** el valor que empieza con `?sv=...` (es el "SAS token", incluyendo el `?` inicial). No copies toda la URL. Si tu portal te devuelve solo `sv=...` sin `?`, anadele el `?` al principio tu mismo.
5. GuÃ¡rdalo como env var del usuario (ver FASE 3), incluyendo el `?`: `setx LANDING_SAS_TOKEN "?sv=..."`. **NO** lo pegues en `config.ps1` ni lo subas a git.

Renovacion anual: repite estos pasos y actualiza la env var `LANDING_SAS_TOKEN`.

---

<a id="fase-2bis"></a>
## FASE 2-bis â€” Alternativa robusta: generar el SAS con PowerShell (recomendado)

Si al copiar el SAS desde el portal te aparece `403 Signature did not match` (muy comun: el clipboard corta caracteres del `sig`, o te entrega un token URL-encoded con longitudes anormalas), usa este metodo. Genera el SAS directamente contra la storage key via Azure PowerShell â€” sin pasar por copiar-pegar del portal.

### Requisitos previos
- Tu usuario de Azure (el mismo con el que entras al Portal) tiene acceso al Storage Account.

### Pasos
1. Abre PowerShell (no necesitas admin, pero si execution policy es restrictivo executa con `-ExecutionPolicy Bypass`).
2. Ejecuta:
   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<ruta-proyecto>\scrapers-pipeline\generate_sas.ps1"
   ```
3. Si no tienes el modulo Az.Storage, lo instala automaticamente (puede pedir confirmacion: responde `Y`).
4. Aparecera un **codigo de login**. Abre <https://microsoft.com/devicelogin> en el navegador, introduce el codigo y autoriza con tu cuenta de Azure.
5. Si tienes varias suscripciones, te listara y te pedira el ID (pega el de la suscripcion donde esta el Storage Account; lo ves en Azure Portal > Subscriptions).
6. El script:
   - Busca el storage account (debe llamarse como $StorageAccount en config.ps1).
   - Recupera su primary key.
   - Genera un SAS del contenedor `landing` con permisos `racwl` (Read/Add/Create/Write/List) y caducidad a 1 ano.
   - Lo guarda como env var `LANDING_SAS_TOKEN` via `setx`.
   - Hace un `azcopy list` automatico para verificar.
7. Debes ver algo como:
   ```
   SAS generado correctamente:
       Longitud: 287 chars
       Caducidad: 2027-07-22
   Env var LANDING_SAS_TOKEN guardada.
   ```
   Si en el `azcopy list` final no aparece un error 401/403, ya puedes pasar a la FASE 4.

### Renovacion
- Repite el comando una vez al ano (ajusta `AddYears(1)` en `generate_sas.ps1` si quieres mas o menos tiempo).
- Apunta la fecha de caducidad en tu calendario: si el SAS expira, el pipeline fallara con `AuthenticationFailed`.

### Ventaja sobre FASE 2 (manual portal)
- No hay clipboard involucrado, no se corrompe el `sig`.
- El SAS se genera contra la storage key real â€” la firma siempre coincide.
- Reproducible y documentado.

---

<a id="fase-3"></a>
## FASE 3 â€” Local: instalar azcopy y configurar la env var

### 3.1 Instalar azcopy
1. Descarga azcopy para Windows 64-bit: <https://aka.ms/azcopy-windows64> (es un .zip).
2. Extraelo en, p.ej., `C:\Tools\azcopy\azcopy.exe`.
3. Anadelo al PATH: en PowerShell (como admin):
   ```powershell
   $p = "C:\Tools\azcopy"
   $old = [Environment]::GetEnvironmentVariable("Path","Machine")
   [Environment]::SetEnvironmentVariable("Path","$old;$p","Machine")
   ```
   Reabre PowerShell (cerrar y abrir nuevo window â€” el PATH no se refresca en sesiones ya abiertas) y verifica: `azcopy --version` -> deberia imprimir la version.

### 3.2 Guardar el SAS token como variable de entorno
```powershell
setx LANDING_SAS_TOKEN "?sv=2022-11-02&ss=..."
```
> Importante: tras `setx`, **cierra y abre** PowerShell para que la env var este disponible. `setx` no afecta a la sesion actual.

### 3.3 Verificar
```powershell
echo $env:LANDING_SAS_TOKEN   # deberia mostrar el token
azcopy list "https://mystorageaccount.blob.core.windows.net/landing$env:LANDING_SAS_TOKEN"
#  (nota: el comando de version es `azcopy --version`, NO `azcopy version`)
```
Si lista (incluso vacio) -> acceso OK.

---

<a id="fase-4"></a>
## FASE 4 â€” Local: ajustar `config.ps1` y probar

Edita `config.ps1`:

- `$StorageAccount` -> el nombre real de tu Storage Account.
- `$Scrapers` -> un bloque por scraper funcional. Para cada uno:
  - `Name`   : nombre logico (snake_case, sin espacios).
  - `Format` : `"parquet"` o `"csv"`.
  - `Wrapper`: path absoluto al wrapper `.ps1` del scraper (cada wrapper gestiona
    sus propios reintentos / lock / timeout). Si un scraper no tenia wrapper
    (como InfoJobs), se ha creado uno `run_infojobs_nightly.ps1`.
  - `OutDir` : carpeta donde el scraper escribe los archivos del dia.
  - `Ext`    : extension generada (`"parquet"` o `"csv"`).
  - `RenameToTimestamp` : `$true` si el wrapper sobreescribe un archivo fijo
    (p.ej. `jobs.parquet`); el pipeline hace una copia con sufijo de timestamp
    para que Autoloader la detecte como archivo nuevo.

### Pre-flight Chrome CDP (Indeed)
El pipeline comprueba antes de arrancar si responde un Chrome con
`--remote-debugging-port=9222` (requisito del wrapper de Indeed). Si no,
lo levanta automaticamente con el perfil `indeed_jobs_scraper\output\chrome_cdp_profile`.
Recuerda loguearte en indeed.com en esa ventana la primera vez (la sesion
se guarda en el perfil).

### Ejecucion paralela
Los 4 scrapers se lanzan en procesos independientes. Si uno tarda horas
(LinkedIn) no bloquea a los demas. Cada uno que termina OK sube su output
inmediatamente a ADLS. Solo se escribe el trigger file `_READY/dia=<hoy>.txt`
cuando TODOS acaban sin fallos.

Prueba a mano antes de agendar:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<ruta-proyecto>\scrapers-pipeline\run_scrapers_and_upload.ps1"
```
Revisa `<logs>\upload-<fecha>.log`. Si todo OK, en Azure Portal veras los archivos en `landing/scraper_X/dia=YYYY-MM-DD/`.

---

<a id="fase-5"></a>
## FASE 5 â€” Local: agendar en Task Scheduler a las **00:00**

1. Abre **Task Scheduler** (taskschd.msc) -> **Create Task** (no Basic).
2. PestaÃ±a **General**:
   - Name: `Scrapers_Daily_Upload`
   - Marca **Run whether user is logged on or not** (asi corre dusk/dawn).
   - **Run with highest privileges**: si scrapers necesitan permisos admin.
3. PestaÃ±a **Triggers** -> **New**:
   - Begin the task: **On a schedule**
   - Settings -> **Daily**, Start: `00:00:00 AM`.
   - Marca **Enabled**.
4. PestaÃ±a **Actions** -> **New**:
   - Action: **Start a program**
   - Program/script: `powershell.exe`
   - Add arguments: `-NoProfile -ExecutionPolicy Bypass -File "<ruta-proyecto>\scrapers-pipeline\run_scrapers_and_upload.ps1"`
5. PestaÃ±a **Conditions**: desmarca "Start the task only if computer is on AC power" (laptops).
6. PestaÃ±a **Settings**: marca **Run task as soon as possible after a scheduled start is missed** (asi recupera si el PC estuvo apagado a las 00:00).
7. OK. Pedira tus credenciales de Windows.

Verificacion manual: en Task Scheduler, boton derecho -> **Run**. Revisa el log al rato.

---

<a id="fase-6"></a>
## FASE 6 â€” Databricks: Catalog y Schemas en Unity Catalog

Requisito: workspace con Unity Catalog habilitado (deberia si creaste la cuenta con esa opcion; verificalo en **Admin Console -> Unity Catalog**).

> **IMPORTANTE â€” ORDEN DE EJECUCION**: haz la **FASE 7 primero** (crear External
> Location + Service Principal) y luego vuelve aqui. Sin la External Location,
> `CREATE CATALOG ... MANAGED LOCATION` falla con error de autorizacion. El orden
> correcto es: FASE 7 (acceso al storage) -> FASE 6 (crear catalog que usa ese
> storage).

En un **SQL Editor** (Databricks workspace) ejecuta (o usa el archivo `databricks/01_create_catalog.sql` con los valores ya rellenos para tu storage):
```sql
-- Catalog (espacio aislado para todo el proyecto)
-- IMPORTANTE: NO pongas ';' antes del MANAGED LOCATION (es parte de la misma sentencia)
CREATE CATALOG IF NOT EXISTS scraper_pipeline
    COMMENT 'Pipeline de ofertas desde scrapers'
    MANAGED LOCATION 'abfss://landing@<storage-account>.dfs.core.windows.net/managed/';

-- Schemas (equivalente a bases de datos)
CREATE SCHEMA IF NOT EXISTS scraper_pipeline.bronze
    COMMENT 'Capa bronze: una tabla Delta por scraper, append raw del dia';

CREATE SCHEMA IF NOT EXISTS scraper_pipeline.silver
    COMMENT 'Capa silver: ofertas unificadas y deduplicadas';

CREATE SCHEMA IF NOT EXISTS scraper_pipeline.gold
    COMMENT 'Capa gold: agregados y KPIs derivados de silver';
```
- `MANAGED LOCATION` es donde Databricks guarda las tablas Delta QUE EL ADMINISTRA. Crea antes una carpeta `managed/` en el contenedor `landing` (Azure Portal -> Storage account -> Containers -> landing -> + Add directory). Es seguro (solo Databricks escribe ahi).
- Esa carpeta `managed/` es DISTINTA del `landing/` donde van los parquets crudos que sube tu PC via azcopy. Mantenerlas separadas evita que Autoloader escanee archivos internos de las tablas Delta.

Atajo: si `CREATE CATALOG ... MANAGED LOCATION` no te deja por permisos, pide a tu admin que te asigne rol `Catalog Creator` o lo cree el.

---

<a id="fase-7"></a>
## FASE 7 â€” Databricks: External Location para ADLS

Para que Databricks lea los archivos crudos del bucket `landing/` necesita una **External Location** registrada en Unity Catalog.

> **ESTA FASE SE HACE ANTES QUE LA FASE 6.** Sin la External Location, `CREATE
> CATALOG ... MANAGED LOCATION` falla con error de autorizacion.

Hay 2 metodos. El **metodo A (Access Connector)** es el recomendado por
Databricks actualmente y el que muestra la UI por defecto. El **metodo B
(Service Principal)** es el clasico.

### 7.1 â€” Metodo A: Access Connector for Azure Databricks (recomendado)

Una "Access Connector" es un recurso de Azure que da a Databricks una managed
identity para acceder al storage. No hay secrets que rotar, mas simple.

**Paso 1: Crear el Access Connector en Azure**
1. Azure Portal -> **Create a resource** -> busca **Access Connector for Azure
   Databricks** -> **Create**.
2. Rellena:
   - Subscription: la misma que tu storage y workspace.
   - Resource group: el mismo que tu Databricks (`rg-dataengineering-sandbox-weurope`)
     o `projects-workspace`, el que prefieras.
   - Name: `access-connector-joboffers`.
   - Region: **West Europe** (misma que todo).
   - Managed identity: deja **System assigned** (default).
3. Review + Create -> Create. Tarda ~30s.

**Paso 2: Dar permiso al Access Connector sobre el storage**
1. Ve al Access Connector -> pestaÃ±a **System assigned** -> copia el **Object ID**
   de la managed identity (o usa el nombre del connector en el role assignment).
2. Storage account `<storage-account>` -> **Access Control (IAM)** -> **Add role assignment**:
   - Role: **Storage Blob Data Contributor**.
   - Assign access to: **Managed identity**.
   - Members: **+ Select members** -> busca `access-connector-joboffers` -> selecciona.
3. Review + assign. Espera 1-2 min a propagar.

**Paso 3: Registrar External Location en Databricks**
1. Databricks workspace -> **Catalog** (sidebar) -> **External locations** ->
   **Create external location**.
2. Rellena:
   - **Name**: `landing_loc`
   - **URL**: `abfss://landing@<storage-account>.dfs.core.windows.net/`
   - **Storage credential**: click **Create new**:
     - **Access connector ID**: pega el **Resource ID** del Access Connector
       (lo sacas de Azure Portal -> Access Connector -> **Properties** o
       **JSON view**; es una URL larga `/subscriptions/.../providers/Microsoft.Databricks/accessConnectors/...`)
     - **User-assigned managed identity ID**: **DEJAR VACIO** (usas system-assigned).
3. Click **Test connection** -> debe decir **SUCCESS**.
4. **Create**.

A partir de aqui podras usar `landing_loc` en notebooks como si fuera una ruta
local, con autenticacion gestionada por Unity Catalog.

### 7.2 â€” Metodo B: Service Principal (alternativa clasica)

Si la UI no te deja usar Access Connector o prefieres el metodo clasico:

1. Azure Portal -> **Microsoft Entra ID** -> **App registrations** -> **New registration**.
   - Name: `databricks-reader`.
   - Supported account types: Single tenant.
2. **Certificates & secrets** -> **New client secret** -> 1 year. **Copia el valor** (no el ID).
3. **Overview** -> copia **Application (client) ID** y **Directory (tenant) ID**.
4. Storage Account `<storage-account>` -> **Access Control (IAM)** -> **Add role assignment**:
   - Role: **Storage Blob Data Contributor**.
   - Assign access to: **User, group, or service principal**.
   - Members: busca `databricks-reader`.
   - Si no aparece al buscar: usa el metodo A (Access Connector) o PowerShell
     con `New-AzRoleAssignment -ObjectId <service-principal-object-id>`.
5. Databricks -> **Catalog** -> **External locations** -> **Create external location**:
   - Name: `landing_loc`.
   - URL: `abfss://landing@<storage-account>.dfs.core.windows.net/`.
   - Storage credential: **Create new** -> Type **Service Principal** -> rellena
     Client ID, Client Secret, Tenant ID.
6. **Test connection** -> SUCCESS -> **Create**.

---

<a id="fase-8"></a>
## FASE 8 â€” Databricks: notebook Bronze (Autoloader por scraper)

Crea un notebook llamado `bronze_ingest` en tu workspace de Databricks. Por cada
scraper, una celda PySpark que lanza un Auto Loader que lee los parquets del
landing y los escribe a una tabla Delta en `job_offers.bronze.<scraper>`.

> Los archivos `.py` listos para importar estan en `databricks/bronze_ingest.py`.

### Rutas usadas
- **Source**: `abfss://landing@<storage-account>.dfs.core.windows.net/<scraper>/`
  (carpeta del landing donde tu PC sube los parquets via azcopy)
- **Checkpoints**: `abfss://landing@<storage-account>.dfs.core.windows.net/_checkpoints/<scraper>/`
  (estado del stream para no reprocesar lo ya cargado)
- **Destino**: tabla `job_offers.bronze.<scraper>` (Delta, en `managed/`)

### Celda para LinkedIn (Parquet)
```python
(spark.readStream.format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation",
            "abfss://landing@<storage-account>.dfs.core.windows.net/_checkpoints/linkedin/schema")
    .option("cloudFiles.useNotifications", "false")    # polling (sin file events)
    .load("abfss://landing@<storage-account>.dfs.core.windows.net/linkedin/")
    .withColumn("_ingest_date", current_date())
    .withColumn("_source_file", col("_metadata.file_path"))   # NO usar input_file_name() en UC
    .writeStream
    .format("delta")
    .option("checkpointLocation",
            "abfss://landing@<storage-account>.dfs.core.windows.net/_checkpoints/linkedin/ckpt")
    .option("mergeSchema", "true")     # por si el scraper anade columnas nuevas
    .trigger(availableNow=True)        # batch-like: consume lo pendiente y para
    .toTable("job_offers.bronze.linkedin")
)
```

### Celdas para los otros 3 scrapers
Identico patron, cambiando `linkedin` por `indeed`, `infojobs`, `multi_site`.
Si algun scraper generase CSV en vez de Parquet, cambia:
```python
.option("cloudFiles.format", "csv")
.option("header", "true")
.option("inferSchema", "true")
#.option("sep", ";")      # descomentar si tu separador no es coma
```

> `trigger(availableNow=True)` es clave: hace que el Job de Databricks (FASE 10)
> procese solo los archivos nuevos desde el ultimo run y termine, en lugar de
> dejar el stream indefinidamente abierto. Esto encaja con un schedule diario.

Checkpoints en `_checkpoints/` (en el bucket, no DBFS) -> reanuda el estado si
el cluster muere, no re-procesa archivos ya cargados.

### Verificar
Tras ejecutar el notebook, en **Catalog** -> `job_offers` -> `bronze` veras 4
tablas: `indeed`, `linkedin`, `infojobs`, `multi_site`. En una celda nueva:
```sql
SELECT 'linkedin' AS s, COUNT(*) AS n FROM job_offers.bronze.linkedin
UNION ALL SELECT 'indeed',  COUNT(*) FROM job_offers.bronze.indeed
UNION ALL SELECT 'infojobs', COUNT(*) FROM job_offers.bronze.infojobs
UNION ALL SELECT 'multi_site', COUNT(*) FROM job_offers.bronze.multi_site;
```

---

<a id="fase-9"></a>
## FASE 9 â€” Databricks: notebook Silver (MERGE)

Crea notebook `silver_merge` que corre **despues** de `bronze_ingest`.

> El archivo listo para importar esta en `databricks/silver_merge.py`.

```sql
-- Solo procesamos los cambios del dia (watermark por _ingest_date)
CREATE OR REFRESH TEMP VIEW new_ofertas AS
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (
                 PARTITION BY offer_id
                 ORDER BY _ingest_date DESC, scraper_source DESC
             ) AS rn
    FROM (
        SELECT * FROM job_offers.bronze.indeed
        UNION ALL
        SELECT * FROM job_offers.bronze.linkedin
        UNION ALL
        SELECT * FROM job_offers.bronze.infojobs
        UNION ALL
        SELECT * FROM job_offers.bronze.multi_site
    )
    WHERE _ingest_date = current_date()
)
WHERE rn = 1;

MERGE INTO job_offers.silver.ofertas AS t
USING new_ofertas AS s
   ON t.offer_id = s.offer_id
WHEN MATCHED  AND t._ingest_date < s._ingest_date THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
```

> **IMPORTANTE**: las columnas de cada scraper deben ser las mismas para que el
> `UNION ALL` funcione. Si los scrapers tienen schemas distintos (p.ej. Indeed
> tiene `salary_min` pero InfoJobs no), ejecuta primero esto para alinearlas:
> ```sql
> -- Ver schemas de cada scraper
> DESCRIBE job_offers.bronze.indeed;
> DESCRIBE job_offers.bronze.linkedin;
> DESCRIBE job_offers.bronze.infojobs;
> DESCRIBE job_offers.bronze.multi_site;
> ```
> Y si difieren, crea una vista por scraper que seleccione solo las columnas
> comunes con `CAST` al mismo tipo. El archivo `databricks/silver_merge.py`
> incluye un helper para esto.

Notas:
- `ROW_NUMBER() ... PARTITION BY offer_id`: si el mismo `offer_id` viene de 2 scrapers el mismo dia, gana el de `_ingest_date` mas reciente (y en empate, alfabetico de `scraper_source`). Ajusta si quieres preferir un scraper concreto.
- Filtrar `_ingest_date = current_date()` evita re-procesar el historico cada noche.
- Si el mismo `offer_id` cambia (p.ej. precio), entra por la rama `WHEN MATCHED ... UPDATE` y el historial viejo queda consultable via **time travel** de Delta.

---

<a id="fase-10"></a>
## FASE 10 â€” Databricks: Workflow diario

1. Sidebar **Workflows** -> **Create Job** -> name `daily_scrapers_pipeline`.
2. **Task 1**:
   - Type: **Notebook**
   - Source: workspace -> selecciona `bronze_ingest`.
   - Cluster: uno **serverless** o un **job cluster** pequeÃ±o (`Spark 14.x`, 1 worker es suficiente).
3. **Task 2** (aÃ±adir `+ Add` -> **Depends on** Task 1):
   - Type **Notebook**, selecciona `silver_merge`, mismo cluster.
4. **Schedule**: en lugar de hora fija, usa **File Arrival** (ver seccion "Trigger por File Arrival" abajo) â€” asi el job no se adelanta a la subida aunque los scrapers tarden horas.
5. PestaÃ±a **Alerts** -> anade tu email para `Job failure`.
6. Save -> **Run now** para probar.

### Orden temporal del dia (variable segun duracion de scrapers)

```
00:00   Task Scheduler lanza run_scrapers_and_upload.ps1 (PC local).
00:00+  Scrapers van corriendo (algunos tardan horas) y se suben a medida
        que terminan -> landing/scraper_X/dia=<hoy>/
??      Cuando TODOS los scrapers terminan OK, el script sube un trigger
        file: landing/_READY/dia=<hoy>.txt
+min    Databricks detecta el trigger (polling cada 1 min) y arranca:
          Task 1 Bronze (Autoloader consume lo nuevo del dia)
          Task 2 Silver (MERGE)
~+10m   silver.ofertas actualizada; lista para consultas/dashboard.
```

### Trigger por File Arrival (recommended)

En vez de schedule diario fijo, usa Databricks **File Arrival trigger**:

1. En el Job -> pestaÃ±a **Schedule** -> selecciona **File arrival**.
2. Source: Unity Catalog volume o external location.
3. Path to file: `_READY/dia=YYYY-MM-DD.txt` (o un patron glob, p.ej. `_READY/dia=*.txt`).
4. Min time since last modification: `1 min` (margen para que azcopy finalize flush).
5. Tras que el archivo aparezca y permanezca estable 1 min, el job arranca.

Si el trigger file por fecha con variable `dia=` no te da (Databricks normalmente matchea contra un path fijo), usa un path estable:
- Nombre fijo: `_READY/daily_ok.txt`. El script PowerShell sobrescribe este archivo cada noche con la fecha del dia dentro (como contenido). Databricks file-arrival dispara siempre el mismo path. Ojo: con overwrite, Databricks tiene que detectar la modificacion â€” para mas robustez, usa **nombre unico**: `_READY/<dia>.txt` y un volumen `landing` con glob. Confirma el soporte en tu version de Databricks, o usa la alternativa del siguiente parrafo.

### Alternativa: schedule fijo tardio (mas simple)

Si no quieres meterte con File Arrival, pon un schedule fijo generoso suficientemente tarde como para que TODOS los scrapers hayan terminado siempre (p.ej. 08:00 si el peor scraper tarda 7h). Menos elegante pero funciona:
1. Job -> Schedule -> **Daily** a las 08:00 (ajustar).
2. El script en cualquier caso sigue escribiendo el trigger file (utiles para trazabilidad/monitoreo), pero el job no depende de el.

De las opciones, recomendamos File Arrival porque:
- No hay que adivinar cuanto dura el scraper mas lento.
- Si un dia los scrapers acaban a las 02:00, Databricks arranca a las 02:01 (rapido).
- Si acaban a las 06:00, Databricks arranca a las 06:01 (espera).

---

<a id="verificacion"></a>
## Verificacion y troubleshooting

### Test de fin a end
```sql
SELECT COUNT(*) FROM scraper_pipeline.bronze.scraper_a;       -- archivos subidos hoy
SELECT COUNT(*), MAX(_ingest_date) FROM scraper_pipeline.silver.ofertas;
DESCRIBE HISTORY scraper_pipeline.silver.ofertas;             -- time travel
```

### Sintomas comunes
| Sintoma | Causa probable | Fix |
|---|---|---|
| `azcopy` 403 Authentication Failed | SAS expirado o env var no set | regenerar SAS, `setx`, reabrir terminal |
| Autoloader no procesa nada nuevo | checkpoint apunta a ruta antigua | borrar `_checkpoints/scraper_X/ckpt` o revisar `cloudFiles.useNotifications` |
| `bronze.scraper_X` sin columnas nuevas | `mergeSchema` false | poner `.option("mergeSchema","true")` (ya en template) |
| Job Databricks no ven archivos | Service Principal sin rol `Storage Blob Data Contributor` | revisar IAM del storage |
| Filas duplicadas en silver | `offer_id` no unico por scraper | revisa que los scrapers generen IDs estables |
| Silver no se actualiza | `WHERE _ingest_date = current_date()` filtra todo si Autoloader corrio antes de medianoche | quitar el WHERE o usar `>= date_sub(current_date(), 1)` durante pruebas |

### Renovacion anual
- SAS token del PC (FASE 2) y client secret del Service Principal Databricks (FASE 7): ambos caducan en 1 ano. Apunta la fecha.
- En Databricks: **Catalog** -> **Storage credentials** -> rotate secret.
