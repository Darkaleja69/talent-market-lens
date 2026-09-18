# Pipeline en Fabric: Scrapers -> OneLake/ADLS -> Lakehouses (medallion)

Esta guia reemplaza a las FASES 6-10 del README principal. Es para alguien que
viene de Fabric (no de Databricks) y quiere hacer el pipeline de ingesta +
unificacion de ofertas con la nomenclatura y los recursos nativos de Fabric.

Asumimos que ya tienes:
- Azure Storage Account `<storage-account>` (ADLS Gen2) en `westeurope`, contenedor `landing`.
- 64 archivos .parquet subidos en `landing/<scraper>/dia=2026-07-23/`.
- Fabric habilitado en tu tenant (Trial o capacity).

---

## INDICE

- [Conceptos de Fabric que necesitas](#conceptos)
- [FASE 6 â€” Crear Workspace y 2 Lakehouses](#fase-6)
- [FASE 7 â€” Crear Shortcut al ADLS landing](#fase-7)
- [FASE 8 â€” Notebook Bronze con Auto Loader](#fase-8)
- [FASE 9 â€” Notebook Silver con MERGE](#fase-9)
- [FASE 10 â€” Data Pipeline diario](#fase-10)
- [Verificacion y troubleshooting](#verificacion)

---

<a id="conceptos"></a>
## Conceptos de Fabric que necesitas

Fabric es una plataforma SaaS con un monton de "item types" (Lakehouse, Notebook,
Pipeline, Dataset, etc.). Estos son los que vas a usar:

### Workspace
Contenedor organizativo que agrupa items + permisos + billing. Equivale a un
"Resource Group" de Azure pero a nivel de Fabric. Todo lo que crees (lakehouses,
notebooks, pipelines) vive dentro de un workspace. Los roles (Admin, Member,
Contributor, Viewer) controlan quien puede ver/editar.

### Lakehouse
Almacenamiento de datos sobre Delta Lake. Es lo mas cercano a una "base de
datos" en Fabric. Contiene:
- **Tables**: tablas Delta transaccionales (ACID, time travel, schema evolution).
- **Files**: sistema de archivos plano donde puedes meter shortcuts a almacen
  externo (ADLS, S3, Google Cloud Storage, otros lakehouses de OneLake).

Un Lakehouse tiene un schema por defecto llamado `dbo`. Cuando escribes
`SELECT * FROM bronze_lh.dbo.indeed`, `bronze_lh` es el Lakehouse y `dbo` es
el schema interno.

### Shortcut
Un puntero a datos que viven FUERA del Lakehouse (en ADLS, en otro Lakehouse
de OneLake, en S3...). Aparece como una carpeta dentro de `Files/` pero los
datos no se copian: se accede en sitio. Es lo que usaremos para que `bronze_lh`
vea el contenedor `landing` de tu storage account sin mover los 64 parquets.

### Notebook
Lo mismo que en Databricks: celdas de codigo Python/PySpark/SQL que se ejecutan
en un Spark session. En Fabric, el cluster se llama "Spark compute" y se
arranca automaticamente al ejecutar (no tienes que configurar nada).

### Auto Loader (cloudFiles)
Un "streaming source" de Spark que vigila una carpeta y, cuando aparecen
archivos nuevos, los lee y los escribe a una tabla Delta. Lleva un checkpoint
para no reprocesar lo ya cargado. Funciona igual en Fabric y Databricks porque
ambos usan el mismo runtime de Spark.

### Data Pipeline
Orquestador visual. Le dices "ejecuta este notebook, y cuando termine, ejecuta
este otro". Puedes anadir dependencias, schedules, reintentos. Equivale a los
"Jobs/Workflows" de Databricks o a Azure Data Factory.

### MERGE INTO
Sentencia SQL de Delta Lake que hace "upsert": si la fila ya existe (por
offer_id), la actualiza; si no, la inserta. Lo usaremos en Silver para mantener
solo la version mas reciente de cada oferta.

---

<a id="fase-6"></a>
## FASE 6 â€” Crear Workspace y 2 Lakehouses

### 6.1 Crear el Workspace
1. Ve a <https://app.fabric.microsoft.com>.
2. Click en **Workspaces** (icono de cuadrados en el menu izquierdo) -> **New workspace**.
3. Rellena:
   - **Name**: `joboffers`
   - **Description**: `Pipeline de ofertas de empleo desde scrapers`
   - **License mode**: Trial (o Fabric capacity si tu org lo tiene).
   - **Default storage format**: deja el default (OneLake).
4. Apply. Espera unos segundos.

> Por que un workspace nuevo: asi aislas este proyecto de cualquier otro
> experimento. Los permisos se gestionan a nivel workspace.

### 6.2 Crear los 2 Lakehouses
Dentro del workspace `joboffers`:

1. Click **New** (boton arriba derecha) -> **Show all** -> **Lakehouse**.
2. **Name**: `bronze_lh` -> Create.
3. Repite: **New** -> **Lakehouse** -> **Name**: `silver_lh` -> Create.

Ahora tienes 2 Lakehouses vacios. Cada uno tiene 2 secciones:
- **Tables**: vacio por ahora.
- **Files**: vacio por ahora (aqui pondremos el shortcut al landing en FASE 7).

> Por que 2 Lakehouses y no 1: arquitectura medallion. Bronze = datos crudos
> tal cual llegan del scraper (1 tabla por scraper). Silver = datos limpios y
> unificados (1 tabla `ofertas` con todos los scrapers juntos). Separarlos en
> Lakehouses distintos permite dar permisos distintos (p.ej. un analista puede
> ver silver pero no bronze) y mantener orden.

---

<a id="fase-7"></a>
## FASE 7 â€” Crear Shortcut al ADLS `landing`

Necesitamos que `bronze_lh` pueda leer los parquets que ya subiste a
`abfss://landing@<storage-account>.dfs.core.windows.net/`. Para eso, un shortcut.

### 7.1 Crear una Connection a tu storage account
Fabric necesita credenciales para acceder al ADLS. Hay 2 opciones:

**Opcion A: Service Principal (recomendado, mas seguro)**
1. En Azure Portal -> **Microsoft Entra ID** -> **App registrations** -> **New registration**.
   - Name: `fabric-joboffers-reader`
   - Single tenant.
2. **Certificates & secrets** -> **New client secret** -> 1 ano -> COPIA el valor.
3. **Overview** -> copia **Application (client) ID** y **Directory (tenant) ID**.
4. Storage Account `<storage-account>` -> **Access Control (IAM)** -> **Add role assignment**:
   - Role: **Storage Blob Data Reader** (solo lectura; Fabric no necesita escribir en landing).
   - Assign to: `fabric-joboffers-reader`.

**Opcion B: Account Key (mas simple, menos seguro)**
1. Storage Account `<storage-account>` -> **Security + networking** -> **Access keys**.
2. Copia `key1` (la clave primaria).

### 7.2 Crear el Shortcut en bronze_lh
1. Ve al workspace `joboffers` -> click en **bronze_lh**.
2. En el panel del Lakehouse, click **New shortcut** (arriba, en la vista de Files).
3. Selecciona **External sources** -> **Azure Data Lake Storage Gen2**.
4. Si te pide crear una **Connection**:
   - **Connection name**: `joboffers_landing_conn`
   - **Authentication kind**: Service principal (opcion A) o Account key (opcion B).
   - Rellena tenant/client ID/secret (opcion A) o pega la storage key (opcion B).
5. Configura el shortcut:
   - **URL**: `https://<storage-account>.dfs.core.windows.net`
   - **Subpath**: `/landing`
   - **Shortcut name**: `landing`
6. Create.

Aparecera una carpeta llamada `landing` dentro de **Files** en `bronze_lh`.
Si la expands, veras `indeed/`, `infojobs/`, `linkedin/`, `multi_site/`.

### 7.3 Verificar
1. En el workspace -> **New** -> **Notebook** -> nombre `verify_landing`.
2. En la celda, pega:
```python
files = mssparkutils.fs.ls("Files/landing/indeed/dia=2026-07-23/")
for f in files:
    print(f.name, f.size)
```
3. Run. Deberias ver 4 archivos `indeed_jobs_20260723_*.parquet`.

Si ves los archivos: OK, shortcut funciona. Si error 403: revisa que el Service
Principal tiene rol `Storage Blob Data Reader` (puede tardar 1-2 min en propagar).

---

<a id="fase-8"></a>
## FASE 8 â€” Notebook Bronze con Auto Loader

Crea un notebook llamado `bronze_ingest` dentro del workspace `joboffers`.
Asegurate de que esta asociado al Lakehouse `bronze_lh` (arriba, en el menu
"Lakehouse" del notebook, selecciona `bronze_lh`).

Pega una celda por scraper. Cada celda lanza un Auto Loader que lee del
shortcut y escribe a una tabla Delta en `bronze_lh`.

### Celda 1: Indeed (Parquet)
```python
src = "Files/landing/indeed/"
ckpt = "Files/_checkpoints/indeed/"

(spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", ckpt + "schema")
    .option("cloudFiles.useNotifications", "false")  # polling; Fabric no requiere Event Grid
    .load(src)
    .withColumn("_ingest_date", current_date())
    .withColumn("_source_file", input_file_name())
    .writeStream
    .format("delta")
    .option("checkpointLocation", ckpt + "ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)   # modo batch: procesa lo pendiente y para
    .toTable("indeed")
)
```

### Celda 2: LinkedIn (Parquet)
```python
src = "Files/landing/linkedin/"
ckpt = "Files/_checkpoints/linkedin/"

(spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", ckpt + "schema")
    .option("cloudFiles.useNotifications", "false")
    .load(src)
    .withColumn("_ingest_date", current_date())
    .withColumn("_source_file", input_file_name())
    .writeStream
    .format("delta")
    .option("checkpointLocation", ckpt + "ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("linkedin")
)
```

### Celda 3: InfoJobs (Parquet)
```python
src = "Files/landing/infojobs/"
ckpt = "Files/_checkpoints/infojobs/"

(spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", ckpt + "schema")
    .option("cloudFiles.useNotifications", "false")
    .load(src)
    .withColumn("_ingest_date", current_date())
    .withColumn("_source_file", input_file_name())
    .writeStream
    .format("delta")
    .option("checkpointLocation", ckpt + "ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("infojobs")
)
```

### Celda 4: Multi-site (Parquet)
```python
src = "Files/landing/multi_site/"
ckpt = "Files/_checkpoints/multi_site/"

(spark.readStream
    .format("cloudFiles")
    .option("cloudFiles.format", "parquet")
    .option("cloudFiles.schemaLocation", ckpt + "schema")
    .option("cloudFiles.useNotifications", "false")
    .load(src)
    .withColumn("_ingest_date", current_date())
    .withColumn("_source_file", input_file_name())
    .writeStream
    .format("delta")
    .option("checkpointLocation", ckpt + "ckpt")
    .option("mergeSchema", "true")
    .trigger(availableNow=True)
    .toTable("multi_site")
)
```

### Que hace `trigger(availableNow=True)`
Hace que el Auto Loader se comporte como batch: procesa solo los archivos que
aparecieron desde la ultima ejecucion (gracias al checkpoint) y despues PARA.
Sin esto, el stream se quedaria abierto indefinidamente esperando mas archivos,
y el notebook nunca terminaria. Con `availableNow=True`, cada noche el notebook
procesa lo nuevo del dia y acaba en minutos. Perfecto para un schedule diario.

### Verificar
Tras ejecutar el notebook, en `bronze_lh` -> **Tables** veras 4 tablas:
`indeed`, `linkedin`, `infojobs`, `multi_site`. En una celda nueva:
```python
display(spark.sql("SELECT COUNT(*) FROM indeed"))
```

---

<a id="fase-9"></a>
## FASE 9 â€” Notebook Silver con MERGE

### 9.1 Conectar silver_lh con bronze_lh
Para que el notebook de Silver pueda leer las tablas de `bronze_lh`, crea un
shortcut **de Lakehouse a Lakehouse** dentro del mismo workspace:

1. Ve a **silver_lh** -> **New shortcut**.
2. Selecciona **Microsoft OneLake**.
3. Te listara los Lakehouses del workspace -> selecciona **bronze_lh**.
4. Shortcut name: `bronze_lh` (mismo nombre; convencion).
5. Create.

Ahora desde notebooks en contexto `silver_lh` puedes hacer:
```sql
SELECT * FROM bronze_lh.dbo.indeed;
```

### 9.2 Crear la tabla silver vacia (una sola vez)
Crea un notebook `silver_init` y ejecuta UNA vez:
```sql
CREATE TABLE IF NOT EXISTS ofertas (
    offer_id STRING,
    title STRING,
    price STRING,
    currency STRING,
    url STRING,
    country STRING,
    city STRING,
    posted_at STRING,
    scraper_source STRING,
    _ingest_date DATE
) USING DELTA;
```
> Ajusta las columnas a las que realmente tengan tus scrapers. Si no las
> sabes, en una celda previa ejecuta `DESCRIBE bronze_lh.dbo.indeed` y copia.

### 9.3 Notebook silver_merge
Crea notebook `silver_merge` asociado a `silver_lh`:

```sql
-- Vista temporal con las ofertas nuevas de hoy, deduplicadas por offer_id
CREATE OR REPLACE TEMP VIEW new_ofertas AS
SELECT * FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY offer_id
        ORDER BY _ingest_date DESC, scraper_source DESC
    ) AS rn
    FROM (
        SELECT * FROM bronze_lh.dbo.indeed
        UNION ALL
        SELECT * FROM bronze_lh.dbo.linkedin
        UNION ALL
        SELECT * FROM bronze_lh.dbo.infojobs
        UNION ALL
        SELECT * FROM bronze_lh.dbo.multi_site
    )
    WHERE _ingest_date = current_date()
)
WHERE rn = 1;

-- Upsert: actualiza existentes, inserta nuevos
MERGE INTO ofertas AS t
USING new_ofertas AS s
   ON t.offer_id = s.offer_id
WHEN MATCHED AND t._ingest_date < s._ingest_date THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;
```

Notas:
- `ROW_NUMBER() ... PARTITION BY offer_id`: si el mismo `offer_id` viene de 2
  scrapers el mismo dia, gana el de `_ingest_date` mas reciente (y en empate,
  el alfabetico de scraper_source).
- `WHERE _ingest_date = current_date()`: solo procesamos lo de hoy. Evita
  re-escanear el historico cada noche.
- `WHEN MATCHED ... UPDATE`: si la oferta ya existe y la nueva es mas reciente
  (p.ej. cambio el precio), se actualiza. La version vieja queda consultable
  via time travel de Delta (`DESCRIBE HISTORY ofertas`).

---

<a id="fase-10"></a>
## FASE 10 â€” Data Pipeline diario

### 10.1 Crear el pipeline
1. Workspace `joboffers` -> **New** -> **Data pipeline** -> nombre `daily_scrapers`.
2. Veras un lienzo vacio con actividades a la derecha.

### 10.2 Anadir actividad Bronze
1. En el panel **Activities** (derecha), arrastra **Notebook** al lienzo.
2. Click en la actividad -> panel de configuracion abajo:
   - **Notebook**: selecciona `bronze_ingest`.
   - **Base notebook**: deja el default.
   - Rename la actividad a `Bronze_ingest`.

### 10.3 Anadir actividad Silver (depende de Bronze)
1. Arrastra otra **Notebook** al lienzo.
2. Configuracion:
   - **Notebook**: `silver_merge`.
   - Rename a `Silver_merge`.
3. Para crear la dependencia: click en `Bronze_ingest`, en el borde derecho
   aparecera un cuadrado verde -> arrastra el cable hasta `Silver_merge`.
   Ahora Silver solo correra si Bronze termina OK.

### 10.4 Programar (Schedule)
1. PestaÃ±a **Home** (arriba) -> **Schedule**.
2. Activa **Run** (toggle).
3. Configura:
   - **Frequency**: Daily.
   - **Time**: `03:00 AM` (tu pipeline local sube a las 00:00 y los scrapers
     tardan horas; 03:00 es un compromise. Si algun dia los scrapers no han
     terminado a las 03:00, Bronze simplemente no procesara nada nuevo, no
     falla - el checkpoint lo gestiona).
   - **Time zone**: tu ciudad.
4. Apply.

> Sobre File Arrival: Fabric soporta triggers basados en eventos del ADLS via
> Event Grid + Logic Apps, pero es mucho mas complejo de configurar que en
> Databricks. Para empezar, el schedule fijo a las 03:00 es suficiente. Si
> mas adelante quieres reaccionar en cuanto los scrapers terminen (sin
> esperar a las 03:00), se puede anadir un trigger de eventos.

### 10.5 Alertas
1. En el workspace -> pestaÃ±a **Monitor** (izquierda) -> veras el pipeline.
2. Si un run falla, aparece en rojo. Para recibir email: **Settings** (workspace)
   -> **Notifications** -> activa email on failure.
3. Para alertas mas finas (p.ej. "si Silver falla 3 dias seguidos"): usa
   **Data Activator** (otro item de Fabric que reacciona a eventos).

### 10.6 Probar
1. En el pipeline -> boton **Run** (arriba).
2. Ve a **Monitor** -> veras el run en progreso.
3. Cuando termine (deberian ser ~5-10 min), verifica en `silver_lh` -> Tables:
   `SELECT COUNT(*), MAX(_ingest_date) FROM ofertas;`

---

<a id="verificacion"></a>
## Verificacion y troubleshooting

### Test de fin a fin
```sql
-- Cuantas filas tiene cada tabla bronze
SELECT 'indeed' AS scraper, COUNT(*) AS n FROM bronze_lh.dbo.indeed
UNION ALL SELECT 'linkedin', COUNT(*) FROM bronze_lh.dbo.linkedin
UNION ALL SELECT 'infojobs', COUNT(*) FROM bronze_lh.dbo.infojobs
UNION ALL SELECT 'multi_site', COUNT(*) FROM bronze_lh.dbo.multi_site;

-- Silver
SELECT COUNT(*), MAX(_ingest_date) FROM ofertas;

-- Time travel: ver el estado de ofertas antes del ultimo MERGE
DESCRIBE HISTORY ofertas;
```

### Sintomas comunes
| Sintoma | Causa | Fix |
|---|---|---|
| Notebook no ve `Files/landing/` | No asociaste `bronze_lh` al notebook | Arriba del notebook, en "Lakehouse", anade bronze_lh |
| Auto Loader no procesa nada nuevo | Checkpoint apunta a ruta antigua | Borra `Files/_checkpoints/<scraper>/ckpt` y vuelve a ejecutar |
| `bronze_lh.dbo.X` no accesible desde silver | Falta shortcut bronze->silver | FASE 9.1: crea el shortcut OneLake en silver_lh |
| Error 403 al leer el shortcut | Service Principal sin rol o key caducada | Revisa IAM del storage; rol `Storage Blob Data Reader` |
| Filas duplicadas en silver | `offer_id` no unico por scraper | Revisa que los scrapers generen IDs estables |
| MERGE falla: "schema mismatch" | Columnas distintas entre scrapers | Usa `mergeSchema=true` en Auto Loader y alinea schemas manualmente si hace falta |

### Flujo diario recordatorio
```
00:00  Task Scheduler (tu PC) lanza run_scrapers_and_upload.ps1
??     Scrapers corren + azcopy sube a landing/<scraper>/dia=<hoy>/
03:00  Fabric Data Pipeline (schedule):
         Bronze_ingest notebook (4 Auto Loaders consumen lo nuevo)
         Silver_merge notebook (MERGE a silver_lh.dbo.ofertas)
~03:15  silver_lh.dbo.ofertas actualizada; lista para Power BI / SQL endpoints
```