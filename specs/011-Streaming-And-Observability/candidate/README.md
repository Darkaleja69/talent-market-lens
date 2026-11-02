# Kit de la demo — comparar los dos mecanismos en Free Edition (T-09)

Este kit permite ejecutar en una cuenta **Databricks Free Edition** (0 €) los dos
mecanismos de ingesta sobre **los mismos CSV**, sin tocar el workspace pagado y sin
crear ningún pipeline `serverless` de pago:

1. **Declarativo (candidato B):** streaming tables con Auto Loader, expectativas,
   cuarentena y `AUTO CDC` SCD1 (`pipeline_declarative.py`).
2. **Actual (línea base A):** Structured Streaming + `foreachBatch` + `MERGE`,
   reutilizando `databricks_notebooks/Upsert_Job_Offers.py`
   (`mechanism_current.py`).

Free Edition es serverless por definición, pero es gratuita: lo que se evita es el
DBU. La demo valida **semántica, contrato y observabilidad**, **no** tiempo ni coste
de producción. La decisión de T-10 se toma con esta evidencia y con la estimación de
coste marcada `[NO VERIFICADO]`.

## Contenido

| Fichero | Qué es | ¿Se versiona? |
|---|---|---|
| `generate_dataset.py` | Genera CSV, `schema.json` y `manifest.json` desde el Parquet real del landing. | Sí |
| `schema.json` | Tipo Spark de cada columna de cada fuente (derivado de Arrow). | Sí |
| `manifest.json` | Conteos esperados, `sha256`, muestreo y truncado. **Sin contenido de filas.** | Sí |
| `data/<fuente>.csv` | Extracto de 288 filas (280 válidas + 8 inválidas inyectadas). | **No** (`.gitignore`); se regenera |
| `pipeline_declarative.py` | Pipeline declarativo para Free Edition. | Sí |
| `mechanism_current.py` | Mecanismo actual adaptado al mismo CSV. | Sí |
| `tests/test_generate_dataset.py` | Tests locales del generador (sin Spark). | Sí |
| `tests/test_mechanism_current.py` | Tests locales de los parámetros, widgets y resumen del stream del mecanismo actual (sin Spark ni Databricks). | Sí |
| `evidence/` | Capturas saneadas de T-09b y su guía de saneado. | Sí |

## Conteos esperados (manifiesto del 2026-11-02)

Origen: el Parquet **más reciente** de cada prefijo del landing (descarga con
`LANDING_SAS_TOKEN` y AzCopy; 0 €, solo egress). El fichero y su fecha están en el
manifiesto; aquí no se reproduce ninguna ruta local.

| Fuente | Fichero origen (`dia=`) | Filas CSV | Válidas | `missing_key` | `missing_scraped_at` | Columnas | Bytes CSV |
|---|---|---:|---:|---:|---:|---:|---:|
| `indeed` | `2026-11-01` | 103 | 101 | 1 | 1 | 33 | 239.210 |
| `infojobs` | `2026-11-01` | 124 | 122 | 1 | 1 | 24 | 123.825 |
| `linkedin` | `2026-11-01` | 49 | 47 | 1 | 1 | 34 | 81.229 |
| `multi_site` | `2026-10-31` | 12 | 10 | 1 | 1 | 42 | 27.379 |
| **Total** | — | **288** | **280** | **4** | **4** | — | **471.643** (≈ 461 KiB) |

Los CSV no superan **1 MB** en total. Las claves son únicas en el extracto
(`distinct_valid_keys` = válidas), así que el destino SCD1 debe quedar con **101 /
122 / 47 / 10** filas. Las 8 filas inválidas son **inyectadas** (no vienen del
landing) y están declaradas como sintéticas en `manifest.json`.

### Datos y privacidad

Los CSV son **datos públicos del landing** y **no se versionan**: `candidate/data/`
está en `.gitignore`; solo se versionan el generador, `schema.json`, el manifiesto,
el código y el README. El extracto puede incluir **datos de contacto públicos** (en
este lote aparecen un correo y un nombre), así que la carpeta no se comparte tal
cual.

### NULL y cadena vacía en CSV

En CSV, `NULL` y la cadena vacía se serializan igual (`""`): una columna con vacíos
pasa a `NULL` al leer con el esquema explícito. En este extracto afecta a
`infojobs.provincia`; no afecta a la comparación entre los dos mecanismos porque
ambos leen el mismo CSV con el mismo esquema.

### Contrato que se conserva

| Fuente | Clave(s) | Columna de captura |
|---|---|---|
| `indeed` | `job_key` | `scraped_at` (timestamp) |
| `infojobs` | `id_oferta` | `fecha_scraped` (timestamp) |
| `linkedin` | `job_id` | `scraped_at` (string ISO-8601) |
| `multi_site` | `site` + `job_id` | `scraped_at` (string ISO-8601) |

Reglas equivalentes a `Upsert_Job_Offers.split_quarantine`:

- `missing_key` si alguna clave es `NULL` o vacía/espacios; **gana** sobre el otro
  motivo.
- `missing_scraped_at` si la captura es `NULL`, vacía o no parseable
  (`try_to_timestamp`).
- `_row_hash = sha2(to_json(struct(<columnas de fuente>)), 256)`.
- Cuarentena con `_quarantine_reason`, `_quarantine_at`, `_row_hash` y
  `_source_file`; las inválidas nunca llegan al destino.
- `_ingest_date = current_date()` y `_source_file = _metadata.file_path`.
- La captura más reciente gana; `_ingest_date` solo desempata.

### Truncado y formato del extracto

- Descripciones (`description*`, `descripcion*`) a **600** caracteres; resto de
  textos a **300**. Se listan las columnas realmente truncadas en el manifiesto.
- Saltos de línea sustituidos por espacios (el lector CSV usa `multiLine=false`).
- Timestamps como texto naive UTC con microsegundos:
  `yyyy-MM-dd HH:mm:ss.SSSSSS`; hay que leerlos con
  `timestampFormat=yyyy-MM-dd HH:mm:ss.SSSSSS`.
- `linkedin.skills` (array en Parquet) se serializa como **string JSON**; el resto
  de columnas se conserva.
- **`escape='"'` es obligatorio** al leer los CSV: las descripciones llevan comillas
  dobles escapadas por duplicación y el `escape` por defecto de Spark (barra
  invertida) desalinea las filas. Los dos mecanismos del kit ya lo configuran.

## Regenerar el extracto (opcional, local, 0 €)

Requisitos: Python 3 con `pyarrow` (y `pytest` para los tests). No hace falta
Databricks.

```powershell
# 1) Listar y descargar el Parquet mas reciente de cada fuente.
#    LANDING_SAS_TOKEN vive en el entorno; NUNCA se imprime ni se versiona.
$sas = $env:LANDING_SAS_TOKEN
azcopy list "https://joboffershns.blob.core.windows.net/landing/indeed$sas" `
  --machine-readable --properties LastModifiedTime --output-type json
azcopy copy "https://joboffershns.blob.core.windows.net/landing/<ruta-del-listado>$sas" `
  "$env:TEMP\tml-011-kit\input\indeed\dia=AAAA-MM-DD\<fichero>.parquet"

# 2) Generar (repite para infojobs, linkedin y multi_site).
python specs/011-Streaming-And-Observability/candidate/generate_dataset.py `
  --input-dir "$env:TEMP\tml-011-kit\input" `
  --output-dir specs/011-Streaming-And-Observability/candidate `
  --rows 200 --seed 20261102

# 3) Tests locales del generador y de los parámetros del mecanismo actual.
python -m pytest specs/011-Streaming-And-Observability/candidate/tests -q
```

`--rows` es el máximo **por fuente** antes de inyectar las dos inválidas. El
muestreo es determinista con `--seed`; si el fichero tiene menos filas, se usan
todas (`sampled: false`). Si los CSV superan 1 MB, baja `--rows` o el truncado y
regenera.

## Paso a paso en Free Edition (T-09b)

### 0. Antes de empezar

- Cuenta **Free Edition** propia. **No** copies el PAT ni la SAS del workspace
  pagado.
- Sube solo los CSV, `schema.json`, el pipeline y los módulos. No hay secretos en el
  kit.
- Los nombres de catálogo/esquema/volumen de este README son un ejemplo
  (`workspace`, `tml_demo`, `landing`, `bronze_lab`, `bronze_current`); si usas
  otros, cambia también la configuración del pipeline y el notebook.
- **CLI opcional:** si configuras la CLI contra la cuenta Free Edition, usa el
  perfil **`free`** (`databricks auth login https://<workspace-free> --profile free`)
  y añade `--profile free` a los comandos. La vía recomendada para crear el
  pipeline y el notebook es la UI de Free Edition; la CLI no es obligatoria.

### 1. Crear esquemas y volumen

En un notebook de Free Edition (o en el editor SQL, si está disponible):

```sql
CREATE SCHEMA IF NOT EXISTS workspace.tml_demo;
CREATE VOLUME IF NOT EXISTS workspace.tml_demo.landing;
CREATE SCHEMA IF NOT EXISTS workspace.bronze_lab;
CREATE SCHEMA IF NOT EXISTS workspace.bronze_current;
```

### 2. Subir los datos al Volume

En **Catalog** → `workspace` → `tml_demo` → `landing`:

1. Sube `schema.json` a la raíz del volumen.
2. Crea las carpetas `indeed`, `infojobs`, `linkedin` y `multi_site`.
3. Sube `data/indeed.csv` a `landing/indeed/indeed.csv`, y así con las cuatro.

La ruta final debe ser:

```
/Volumes/workspace/tml_demo/landing/schema.json
/Volumes/workspace/tml_demo/landing/indeed/indeed.csv
/Volumes/workspace/tml_demo/landing/infojobs/infojobs.csv
/Volumes/workspace/tml_demo/landing/linkedin/linkedin.csv
/Volumes/workspace/tml_demo/landing/multi_site/multi_site.csv
```

### 3. Subir el código a Workspace

Crea una carpeta `/Workspace/Users/<usuario>/tml-011-kit/` y sube como **ficheros
Python** (no como notebooks):

- `pipeline_declarative.py`
- `mechanism_current.py`
- `Upsert_Job_Offers.py` (copiado de `databricks_notebooks/`)
- `Deduplicate_Job_Offers.py` (dependencia del anterior)

`Upsert_Job_Offers.py` importa `Deduplicate_Job_Offers`; sin el segundo, el mecanismo
actual falla al importar.

> **Si el `import` falla en Free Edition (diagnóstico rápido):** comprueba que
> `mechanism_current.py` es un **fichero** Python y no un notebook, que está en una
> **carpeta de proyecto** (no en la raíz del home) y que has **reiniciado el kernel**
> después de crearlo. Como último recurso, carga el módulo por ruta explícita:
>
> ```python
> import importlib.util
> import sys
>
> spec = importlib.util.spec_from_file_location(
>     "mechanism_current",
>     "/Workspace/Users/<usuario>/tml-011-kit/mechanism_current.py",
> )
> mechanism_current = importlib.util.module_from_spec(spec)
> sys.modules["mechanism_current"] = mechanism_current
> spec.loader.exec_module(mechanism_current)
> ```
>
> El cargador por ruta no sustituye al `sys.path`: `mechanism_current` importa
> `Upsert_Job_Offers`, así que la carpeta debe seguir en `sys.path`.

### 4. Pipeline declarativo

En **Jobs & Pipelines** → **Create** → **ETL pipeline**:

- Nombre: `tml-011-declarative`.
- **Serverless**: activado (Free Edition).
- Código: selecciona `/Workspace/Users/<usuario>/tml-011-kit/pipeline_declarative.py`.
- Catálogo/esquema de destino: `workspace` / `bronze_lab`.
- **Configuration** (clave → valor):
  - `landing_root` → `/Volumes/workspace/tml_demo/landing`
  - `target_schema` → `workspace.bronze_lab`
  - `schema_path` (opcional) → `/Volumes/workspace/tml_demo/landing/schema.json`
- Sin schedule, sin modo continuo.

Pulsa **Start** (no **Full refresh**). El pipeline crea, por fuente, las tablas
`<fuente>_raw`, `<fuente>_valid`, `<fuente>_quarantine` y `<fuente>` (SCD1).

**Segunda ejecución:** vuelve a pulsar **Start** sin subir ficheros nuevos. Debe
procesar **0 filas** y no cambiar los conteos. Si haces *Full refresh*, se
reprocesa todo y no sirve como evidencia de exactly-once.

### 5. Mecanismo actual

`mechanism_current.py` es una **librería**: se importa desde el notebook y se llama
a `run()`. **No** lo ejecutes como notebook ni con `%run`: desde que no hay
`__main__` no arranca nada, pero ejecutarlo como notebook no define el módulo ni
resuelve sus imports. Sus parámetros (`landing_root`, `target_schema` y los
opcionales `schema_path` y `checkpoint_root`) se pasan como **argumentos** de `run()`
o como **widgets** del notebook; `schema_path` y `checkpoint_root` usan por defecto
`<landing_root>/schema.json` y `<landing_root>/_checkpoints/current`. En el pipeline
esos mismos nombres van en la `configuration` (Lakeflow la expone como `spark.conf`);
en el notebook **no** se usa `spark.conf` **para pasar parámetros** (la zona horaria
se sigue fijando dentro de `run()`).

Crea un notebook `current_mechanism_demo` con tres celdas:

```python
# Celda 1: modulo en el path
import sys
sys.path.append("/Workspace/Users/<usuario>/tml-011-kit")
```

```python
# Celda 2: parametros como widgets (o pasalos directamente a run())
dbutils.widgets.text("landing_root", "/Volumes/workspace/tml_demo/landing")
dbutils.widgets.text("target_schema", "workspace.bronze_current")
dbutils.widgets.text("checkpoint_root", "/Volumes/workspace/tml_demo/landing/_checkpoints/current")
```

```python
# Celda 3: ejecutar y medir la duracion de la ejecucion
import time
import mechanism_current

started = time.perf_counter()
mechanism_current.run(
    spark,
    landing_root=dbutils.widgets.get("landing_root"),
    target_schema=dbutils.widgets.get("target_schema"),
    checkpoint_root=dbutils.widgets.get("checkpoint_root"),
)
print(f"Duracion de la ejecucion: {time.perf_counter() - started:.1f} s")
```

Sin widgets, pasa los valores directamente a `run()`:

```python
mechanism_current.run(
    spark,
    landing_root="/Volumes/workspace/tml_demo/landing",
    target_schema="workspace.bronze_current",
)
```

`run()` crea el esquema y las tablas destino si no existen, arranca las cuatro
consultas con `trigger(availableNow=True)` y espera a todas. Devuelve (y deja en la
salida del notebook) un resumen por fuente, en el orden `indeed`, `infojobs`,
`linkedin`, `multi_site`:

| Campo | Origen | Significado |
|---|---|---|
| `source` | — | Fuente del stream. |
| `query_id` | `query.id` | Id de la consulta; el checkpoint lo persiste, así que se repite entre ejecuciones. |
| `status` | `query.status["message"]` | Estado final de la consulta (`Stopped` al terminar). |
| `num_input_rows` | `lastProgress["numInputRows"]` | Filas leídas en el último lote; **0** si no hubo ningún lote. |
| `batch_id` | `lastProgress["batchId"]` | Último lote del motor; **`None`** si no hubo ninguno. |
| `batches` | `len(query.recentProgress)` | Lotes que el motor conserva para esa consulta. |

Cada stream se imprime como
`Stream <fuente> finalizada: <status> (num_input_rows=…, batch_id=…, batches=…)`.
El resumen solo lee el progreso del motor: **no lanza acciones nuevas** de Spark.

**Segunda ejecución:** vuelve a ejecutar la celda 3 sin subir ficheros nuevos. Los
checkpoints del Volume hacen que no se ingiera nada: el resumen debe mostrar
`num_input_rows: 0` y `batch_id: None` en las cuatro fuentes **si no hubo ningún
microbatch**; si Spark ejecuta un microbatch vacío, saldrá `num_input_rows: 0` con su
`batch_id`. En ambos casos los conteos del destino no cambian, que es la evidencia de
exactly-once. Para repetir desde cero, borra el checkpoint:

```python
dbutils.fs.rm("/Volumes/workspace/tml_demo/landing/_checkpoints/current", True)
```

### 6. Evidencia que hay que traer (`evidence/`)

Guarda capturas o JSON **saneados** (sin rutas locales, correos ni SAS) con estos
nombres:

| Fichero sugerido | Qué debe mostrar |
|---|---|
| `declarative-run1-graph.png` | Grafo/linaje del pipeline con las cuatro fuentes. |
| `declarative-run1-expectations.png` | Métricas de `has_valid_key` y `has_valid_scraped_at`: 1 fallo de cada una por fuente. |
| `declarative-run1-eventlog.png` | Event log o detalle de la ejecución con filas procesadas y duración. |
| `declarative-run2-zero.png` | Segunda ejecución: 0 filas nuevas / sin cambios. |
| `current-run1-eventlog.png` | Event log o Spark UI del mecanismo actual; progreso del stream. |
| `current-run2-zero.png` | Segunda ejecución: `numInputRows = 0` por fuente. |
| `counts.sql.txt` | Salida de los conteos de abajo (destino y cuarentena). |
| `timings.md` | Duración de cada ejecución (pipeline y notebook). |

Conteos para pegar en `counts.sql.txt` (ejecuta en un notebook; **no** hace falta
SQL warehouse):

```sql
SELECT 'indeed' AS source, COUNT(*) AS rows FROM workspace.bronze_lab.indeed
UNION ALL SELECT 'infojobs', COUNT(*) FROM workspace.bronze_lab.infojobs
UNION ALL SELECT 'linkedin', COUNT(*) FROM workspace.bronze_lab.linkedin
UNION ALL SELECT 'multi_site', COUNT(*) FROM workspace.bronze_lab.multi_site;

SELECT 'indeed' AS source, _quarantine_reason, COUNT(*) AS rows
FROM workspace.bronze_lab.indeed_quarantine GROUP BY _quarantine_reason
UNION ALL
SELECT 'infojobs', _quarantine_reason, COUNT(*) FROM workspace.bronze_lab.infojobs_quarantine GROUP BY _quarantine_reason
UNION ALL
SELECT 'linkedin', _quarantine_reason, COUNT(*) FROM workspace.bronze_lab.linkedin_quarantine GROUP BY _quarantine_reason
UNION ALL
SELECT 'multi_site', _quarantine_reason, COUNT(*) FROM workspace.bronze_lab.multi_site_quarantine GROUP BY _quarantine_reason;
```

Esperado: destino **101 / 122 / 47 / 10**; cuarentena **2 por fuente** (1
`missing_key`, 1 `missing_scraped_at`). Repite los mismos conteos sobre
`workspace.bronze_current` para el mecanismo actual.

## Límites y `[NO VERIFICADO]`

- El extracto no cubre el 100 % del esquema ni el volumen real; la paridad total se
  valida en el cutover (T-12). Los textos están truncados y las listas serializadas.
- Las tablas `<fuente>` del pipeline declarativo son **gestionadas por el pipeline**
  (requisito de `AUTO CDC`): son tablas nuevas del esquema de laboratorio, no las
  `bronze.<fuente>` de producción.
- Free Edition no representa el tiempo ni el coste de producción. No se usan
  `system.*`, SQL warehouse, health rules ni alertas.
- **`[NO VERIFICADO]` hasta que se ejecute T-09b:**
  - Disponibilidad de `AUTO CDC` y de `@dp.expect` en Free Edition.
  - Aceptación de `sequence_by` como `struct(_scraped_ts, _ingest_date)` y de
    `except_column_list` en `dp.create_auto_cdc_flow`.
  - Que el pipeline serverless pueda leer `schema.json` del Volume con `open()`.
  - Límites de la UI de Free Edition (event log, métricas de expectativas, Spark UI)
    y cuota de la sesión.
  - Que `CREATE SCHEMA`, `CREATE VOLUME` y los permisos del catálogo `workspace`
    funcionen como se describen.

### Qué hacer si algo falla

| Síntoma | Acción |
|---|---|
| `dp.expect` no existe | Comenta los dos decoradores `@dp.expect` de `_raw`. El split válidas/cuarentena sigue igual; las métricas de expectativas se pierden y se anota. |
| `AUTO CDC` no está disponible | Aplica la contingencia de `plan.md` §8.3: `append_flow` a una tabla y dedup por clave en una tabla posterior. Documenta que la comparación pierde el SCD1 nativo. |
| `sequence_by` con `struct` falla | Cambia a `sequence_by=F.col("_scraped_ts")`; se pierde el desempate por `_ingest_date` (anótalo). |
| `except_column_list` falla | Quita el argumento; el destino incluirá `_scraped_ts` (columna extra) y se anota la desviación. |
| `MERGE WITH SCHEMA EVOLUTION` no está disponible | **Verificado en Free Edition el 2026-11-02 con las dos ejecuciones; los conteos quedan pendientes.** Si apareciera, el mecanismo actual fallará al primer lote: captura el error como evidencia y ejecuta la demo declarativa. No se parchea el módulo de producción en el kit. |
| `open()` del `schema.json` del Volume falla | Sube `schema.json` junto al código y configura `schema_path` a `/Workspace/Users/<usuario>/tml-011-kit/schema.json`. |
| Los CSV se desalinean | Comprueba que el lector usa `escape='"'` y `header=true`; vuelve a subir el CSV completo. |
| Auto Loader pide `cloudFiles.schemaLocation` pese al esquema explícito | Añade `.option("cloudFiles.schemaLocation", "<landing_root>/_schemas/<fuente>")` en los dos mecanismos; la carpeta se crea sola. |
| La cuota de Free Edition corta la sesión | Reanuda al día siguiente; no hay coste, pero anota la incidencia en `evidence/`. |

## Limpieza (0 €)

```sql
DROP SCHEMA IF EXISTS workspace.bronze_lab CASCADE;
DROP SCHEMA IF EXISTS workspace.bronze_current CASCADE;
DROP SCHEMA IF EXISTS workspace.tml_demo CASCADE;
```

Borra el pipeline desde la UI y, si quieres, la carpeta de Workspace:

```powershell
databricks workspace delete /Workspace/Users/<usuario>/tml-011-kit --recursive --profile free
```

No borres nada del workspace pagado: este kit no lo toca.
