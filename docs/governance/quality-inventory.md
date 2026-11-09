# Inventario de calidad — reglas existentes, cuarentena y huecos (spec 012, T-01)

> **Fecha:** 2026-11-06. **Tarea:** T-01. **Método:** lectura de código y
> metadatos de Unity Catalog (`tables get`/`tables list`) por CLI. **Sin SQL, sin
> leer datos, sin conteos históricos** (el perfil va en T-08), **0 €**.
>
> Este documento **no decide** las reglas de T-05: inventaría lo que ya existe,
> lo que falta y lo que no debe convertirse en rechazo.

## 1. Resumen

| # | Hallazgo | Implicación |
|---|---|---|
| Q1 | El bronce ya separa filas inválidas con **dos motivos**: `missing_key` y `missing_scraped_at`, con precedencia `missing_key` > `missing_scraped_at`. | Se conservan; T-05/T-06 no los redefinen. |
| Q2 | La cuarentena es **idempotente por `(_row_hash, _quarantine_reason, _source_file)`** y el chequeo se limita a los ficheros del lote. | Repetir un lote no duplica; hay tests que lo prueban. |
| Q3 | **No existe ninguna tabla `<fuente>_quarantine`** en dev ni en prod (8 comprobaciones). | El esquema no es verificable por metadatos; se deriva del código (§3.3) y no hay histórico Delta que inventariar (RF-6). |
| Q4 | El salario ya está cubierto por **marca + neutralización** (`salary_quality`, importes anuales a `NULL`), no por rechazo. | El constraint candidato debe ser sobre `salary_min_annual`/`salary_max_annual` (silver) y `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR` (gold), nunca sobre el rango original. |
| Q5 | `company_name` con rating tiene **dos defensas**: el scraper (`coherence.py`, rechaza la fila) y **gold** (filtra `^\d+[,.]\d{1,2}$` en `companies_complete_catalog`). No hay cuarentena Delta. | La regla Delta de T-06 es una **segunda defensa** para bronce; el descarte silencioso de gold ya existe y no se rompe. |
| Q6 | **No hay constraints versionadas ni aplicadas**: 0 DDL en el repo, 0 propiedades `delta.constraints.*` en las 39 tablas, 0 columnas `nullable=false`. | El mapa tabla-columna (§7.2) hay que fijarlo antes de generar DDL (T-05/T-07). |
| Q7 | `IsValidPostingDate` es un **flag** (2026–2089), no un filtro. En `gold.fact_offers` la estadística Delta da `posted_date` mínimo **2018-10-13**. | Un `CHECK (posted_date >= 2026-01-01)` **fallaría sobre el histórico**. No puede ser rechazo sin medir y decidir tratamiento (T-08). |
| Q8 | `job_id` es `STRING` y la estadística de `gold.fact_offers` da `nullCount = 0`; en bronce las claves son también `STRING` y nullable. | «No nulo + `trim <> ''`» es candidato viable; el conteo exacto se confirma en T-08. |
| Q9 | La cuarentena local de scrapers (ficheros y filas CSV) es una defensa **anterior y distinta**. | No se mezclan sus conteos con los de Delta (RF-5). |

## 2. Mapa de reglas existentes por capa

| Capa | Regla | Dónde vive | Acción actual | Evidencia |
|---|---|---|---|---|
| Bronce | Clave obligatoria: cualquier clave `NULL` o vacía/espacios (tras `trim` y cast a string) | `Upsert_Job_Offers._missing_key_expr` (l. 107-121) | Cuarentena `missing_key`; la fila no entra al MERGE | `databricks_notebooks/Upsert_Job_Offers.py` |
| Bronce | Fecha de captura obligatoria y parseable: `try_to_timestamp(scraped_at) IS NULL` | `Upsert_Job_Offers.split_quarantine` (l. 142-179) | Cuarentena `missing_scraped_at` | ídem |
| Bronce | Precedencia de motivos | ídem (l. 173-176) | `missing_key` gana si concurren | `tests/test_upsert_job_offers.py::test_missing_key_takes_precedence_over_missing_temporal` |
| Bronce | Dedup intra-lote: una fila por clave, la captura más reciente (`_scraped_ts`, desempate `_ingest_date`) | `Upsert_Job_Offers.dedupe_batch` (l. 182-203) + `Deduplicate_Job_Offers.keep_latest_by` | Se queda la última captura | tests de `keep_latest_by` |
| Bronce | Upsert idempotente | `Upsert_Job_Offers.merge_sql` (l. 206-236) | `MERGE WITH SCHEMA EVOLUTION`; gana la captura estrictamente más reciente, desempate `_ingest_date` | tests de `merge_sql` |
| Bronce | La tabla destino debe existir | `bronze_ingest.ipynb` (celda de configuración, l. 115-118) | Falla antes del primer microbatch con mensaje claro | notebook |
| Bronce | Cuarentena append-only, con evolución de esquema, aislada del MERGE | `bronze_ingest.ipynb` (l. 141-178) | Escribe solo filas nuevas | notebook |
| Silver | Clave semántica de oferta: `title` + `company_name` + `location_city` (normalizados) | `Silver_Incremental.key_expression/key_sql` (l. 146-184) | Agrupa y recalcula por grupo; `MERGE` por `_key` | `tests/test_silver_incremental.py` |
| Silver | Guardas de corrección antes del MERGE | `Silver_Incremental.require_no_null_ingest_date`, `require_unique_groups` (l. 380-405) | Fallan la tarea si el recálculo no es publicable | tests |
| Silver | Normalización/enriquecimiento y salario anual | `Enrich_Job_Offers_Dataframes.enrich` | Completa huecos (`only_if_missing=True`) y calcula `salary_quality` | `tests/test_enrich_pure.py`, `test_integration_spark.py` |
| Silver | Relleno de `posted_date` | `enrich(add_posted_date_fill=True)` | Usa `scraped_at` cuando falta la fecha publicada y deja `posted_date_source` | tests de enrich |
| Gold | Dedup por `job_id` y después por `title_norm + company_norm + salary_filled` | `gold_build.ipynb` (celdas 3 y 4) + `Prepare_Gold.keep_best` (l. 320-341) | Una fila por grupo, prefiriendo la más completa | `tests/test_integration_spark.py` |
| Gold | Compañías: descarta `company_name` que sea exactamente un rating y nombres vacíos | `gold_build.ipynb` (celda de compañías) | **Descarte silencioso** en `companies_complete_catalog` | notebook |
| Gold | Flag de fecha válida 2026–2089 | `Prepare_Gold.add_posted_date_flags` (l. 237-247) | Marca `IsValidPostingDate`; **no filtra** | tests |
| Gold | Conversión a EUR y cubo de work mode | `Prepare_Gold.add_salary_eur`, `add_work_mode_bucket` | Divisa desconocida ⇒ EUR `NULL`; modo vacío ⇒ `(Not specified)` | tests |
| Ops | Métricas del run: 4 checks y una fila por `run_id` | `Run_Metrics.build_checks` (l. 440), `merge_sql` (l. 546-559) y `record` (l. 603) | `MERGE` por `run_id`; nunca propaga el error de escritura | `tests/test_run_metrics.py` |
| Local (scrapers) | Coherencia de fila: `job_id` no vacío, URL/rating fuera de columnas de texto, fechas parseables | `scrapers-pipeline/coherence.py` (`RATING_RE` l. 23, `HARD_RULES` l. 86-115) | Fila a CSV de cuarentena local o fichero rechazado | `docs/RUNBOOK.md` §10 |

## 3. Cuarentena Delta

### 3.1 Motivos y precedencia

| Motivo | Condición | Acción | Notas |
|---|---|---|---|
| `missing_key` | Alguna clave de la fuente es `NULL` o `""`/espacios tras `trim(cast(... AS STRING))` | Se escribe en `bronze.<fuente>_quarantine` **y no entra al MERGE** | `indeed`: `job_key`; `linkedin`: `job_id`; `infojobs`: `id_oferta`; `multi_site`: `site` + `job_id` |
| `missing_scraped_at` | `try_to_timestamp(<scraped_at>) IS NULL` (nulo, vacío o no parseable) | Ídem | `scraped_at` salvo InfoJobs (`fecha_scraped`) |

**Precedencia estable:** si concurren, `missing_key` gana (una fila = un motivo
principal). `trim` de Spark solo elimina espacios ASCII; tabuladores y otros
espacios Unicode **no** se consideran vacíos (documentado en el código).

### 3.2 Identidad y anti-duplicado

- Identidad de fila de cuarentena: **`(_row_hash, _quarantine_reason, _source_file)`**
  (`QUARANTINE_IDENTITY_COLUMNS`, l. 48-52).
- `_row_hash` = `sha2(to_json(struct(<columnas de fuente>)), 256)`; excluye
  `_ingest_date`, `_source_file`, `_row_hash`, `_scraped_ts` y toda columna
  `_quarantine_*`. La sesión se fija a UTC para que el hash sea estable (D4).
- Antes de escribir: `dropDuplicates` por la terna y `left anti` contra la tabla
  existente, **leyendo solo los ficheros del lote** (`_source_file IN (...)`).
  Repetir el lote no crece la tabla; una fila repetida en el mismo lote se escribe
  una vez. Tests: `test_reprocessing_a_batch_does_not_grow_quarantine`,
  `test_duplicate_quarantine_rows_in_one_batch_are_written_once`.

### 3.3 Esquema de la tabla de cuarentena (derivado del código, no de metadatos)

`saveAsTable(..., mode="append", mergeSchema=true)` sobre un DataFrame que es el
payload de origen más:

| Columna añadida | Tipo (según código) | Contenido |
|---|---|---|
| `_quarantine_reason` | `string` | `missing_key` / `missing_scraped_at` |
| `_quarantine_at` | `timestamp` | `current_timestamp()` del lote |
| `_row_hash` | `string` (sha-256 hex) | Huella de la fila |
| `_source_file` | `string` | Ruta del fichero de landing (puede ser `NULL` si el batch no la trae) |

Como **ninguna tabla existe** (Q3), el esquema real, los tipos y los conteos son
`[NO VERIFICADO]`; se confirman en T-08/T-06 al crearse. Cualquier ampliación
(motivo nuevo) será **aditiva y compatible** con esta terna.

### 3.4 Consecuencia de Q3

- No hay histórico Delta de cuarentena que perfilar por motivo/fuente: el
  inventario de RF-6 sobre Delta parte de cero. La cuarentena **local** de
  scrapers sí puede tener histórico, pero es otra capa y no se mezcla.
- El primer lote con filas inválidas creará la tabla con `mergeSchema`; el
  `CREATE TABLE` de la primera escritura exige privilegio en el esquema (ver
  `access-matrix.md` §5.6).

## 4. Deduplicación vigente

| Nivel | Clave | Desempate | Mecanismo |
|---|---|---|---|
| Bronce, intra-lote | Clave de fuente (`job_key` / `job_id` / `id_oferta` / `site+job_id`) | `_scraped_ts` DESC, `_ingest_date` DESC | `row_number()` (`Deduplicate_Job_Offers.keep_latest_by`) |
| Bronce, tabla | Misma clave | Captura estrictamente más reciente; `_ingest_date` en empate | `MERGE` (la fila entrante gana solo si es más nueva) |
| Silver | `_key` = `title` + `company_name` + `location_city` normalizados | Recálculo del grupo completo | `MERGE WITH SCHEMA EVOLUTION` por `_key` |
| Gold, paso 1 | `job_id` | Descripción no vacía > salario no nulo > `posted_date` DESC > `source_scraper` > `job_id` | `keep_best` |
| Gold, paso 2 | `title_norm + company_norm + salary_filled` | ídem | `keep_best` |
| Compañías gold | `company_name` normalizado | industria/tamaño/rating no nulos, `source` ASC | `row_number()` |

**Deuda conocida (fuera de alcance):** la dedup de oro por título/empresa/
salario **no incluye localización ni fecha**; cambiarla exige spec propia
(`roadmap.md`). T-01 no la toca.

## 5. Reglas salariales (estado actual)

Implementadas en `Enrich_Job_Offers_Dataframes.enrich` (l. 1582-1650):

| Señal | Expresión | Efecto |
|---|---|---|
| `non_pos` | `min <= 0` o `max <= 0` | `salary_quality = invalid`; importes anuales a `NULL` |
| `inv_range` | ambos no nulos y `min > max` | `salary_quality = invalid`; importes anuales a `NULL` |
| `unknown_period` | hay salario y el periodo no se reconoce (factor `NULL`) | `salary_quality = unknown_period`; anual `NULL` (no se inventa) |
| `outlier_review` | mes > 30.000; hora > 400; semana > 5.000; día > 1.000; año > 1.000.000 | `salary_quality = outlier_review`; anual `NULL` |
| `ok` | hay salario y ninguna señal anterior | Se conserva el anual calculado |
| `missing` | no hay salario | Anual `NULL` |

- Los factores anuales son por token (`hour` > `day` > `week` > `month` > `year`);
  el rango **original** se conserva y solo se neutralizan los importes anuales.
- `salary_validation_reason` existe pero es **opt-in** y por defecto no se genera.
- **Cobertura de tests:** hay casos `ok`, `unknown_period` y periodo `hour`
  (`test_integration_spark.py` l. 149-200). **No hay test de rango invertido
  (`min > max`) ni de importe no positivo**: hueco explícito para T-05.
- **Candidata de constraint:** `salary_min_annual <= salary_max_annual` **solo
  cuando ambos son no nulos** (en silver) y su equivalente en gold. Un `CHECK`
  con `NULL` se evalúa como «no viola», que es justo la semántica que el diseño
  necesita. No se propone constraint sobre `salary_min`/`salary_max` originales.

## 6. Fechas

- Silver rellena `posted_date` desde `scraped_at` cuando falta y deja
  `posted_date_source` (`posted`/`scraped`). No se ha medido cuántas filas usan
  cada origen.
- Gold marca `IsValidPostingDate = posted_date no nulo AND year >= 2026 AND year < 2090`.
  Es un **flag**, no un filtro: no elimina filas.
- **Medición disponible sin SQL** (estadística Delta de `job_offers.gold.fact_offers`,
  puede estar desactualizada): `posted_date` `min = 2018-10-13`, `max = 2026-11-02`,
  `nullCount = 0`; `IsValidPostingDate` con 2 valores distintos y 0 nulos.
  ⇒ **existen filas con fecha anterior a 2026 en gold**.
- Consecuencia: una constraint `posted_date >= '2026-01-01'` **no puede añadirse
  sin tratar antes esas filas**; el flag actual no las rechaza. El conteo exacto y
  la decisión (mantener el flag, constraint solo si el histórico cumple, o
  tratamiento explícito) van a T-08. Rango alternativo (p. ej. `>= 2000`) exigiría
  justificación por datos, no se inventa aquí.

## 7. Constraints Delta

### 7.1 Estado

- **Repo:** ninguna sentencia de constraint en `databricks_notebooks/`,
  `resources/` ni `databricks.yml`: 0 `ADD CONSTRAINT`, 0 `CHECK (`, 0
  `CONSTRAINT <nombre>`. Las 4 coincidencias del grep no son DDL: dos predicados
  SQL `IS NOT NULL` (`Upsert_Job_Offers.py:247`, `Silver_Incremental.py:536`), un
  `if not nulls.isEmpty()` de Python (`Silver_Incremental.py:389`, validación en
  el driver) y un docstring (`Run_Metrics.py:426`)
  (`evidence/t01-repo-constraints-grep.txt`).
- **Metadatos:** 0 tablas con propiedades `delta.constraints.*`; 0 columnas con
  `nullable = false` en las 39 tablas. `delta.feature.invariants = supported` es
  una capacidad del formato, **no** una constraint existente.
- No hay `NOT NULL` en el catálogo: todas las columnas son `nullable=True`.

### 7.2 Mapa tabla-columna por capa (fijado antes de cualquier DDL)

| Regla | Bronce | Silver | Gold |
|---|---|---|---|
| Clave obligatoria | `job_key` (indeed), `job_id` (linkedin), `id_oferta` (infojobs), `site`+`job_id` (multi_site) — todas `STRING` | `job_id` `STRING` | `job_id` `STRING` |
| Orden salarial | — | `salary_min_annual` / `salary_max_annual` `DOUBLE` | `SalaryMinAnnual_EUR` / `SalaryMaxAnnual_EUR` `DOUBLE` |
| Fecha | `posted_date`/`fecha` según fuente (bronce no se propone como objetivo de constraint) | `posted_date` `DATE` | `posted_date` `DATE` |
| Marca de calidad | — | `salary_quality` `STRING` | `salary_quality` `STRING` |
| Rating en empresa | `company_name` `STRING` | `company_name` `STRING` | `company_name` `STRING` (y `companies_complete_catalog`) |

Tablas candidatas por capa: `silver.offers_<fuente>` (4) y `gold.fact_offers`;
las dimensiones no llevan `job_id` ni salario y quedan fuera salvo que T-05 lo
justifique. **No se asume que los nombres coincidan entre capas** (ya no
coinciden: `salary_min_annual` vs `SalaryMinAnnual_EUR`).

### 7.3 Reglas que ya marcan/neutralizan y NO deben ser rechazos

1. **Rango salarial original inválido** (`min <= 0`, `min > max`): la oferta se
   conserva con `salary_quality = invalid` y anuales `NULL`. Un `CHECK` sobre los
   importes originales la rechazaría: **prohibido**.
2. **Periodo desconocido** y **outliers**: se conservan marcados y neutralizados.
3. **`posted_date` anterior a 2026**: hoy se conserva con
   `IsValidPostingDate = false`; convertir el flag en rechazo exige medición y
   decisión (Q7).
4. **`company_name` con rating en `companies_complete_catalog`**: hoy se
   descarta silenciosamente de la dimensión de compañías; la regla nueva de
   bronce debe **cuarentenar**, no romper el resto de la oferta ni cambiar la
   semántica de `fact_offers`.

## 8. Candidatas para T-05 (sin decidir)

| `rule_id` propuesto | Capa | Acción propuesta | Qué falta medir/decidir |
|---|---|---|---|
| `missing_key` | Bronce | Cuarentena (ya existe) | Nada; se conserva |
| `missing_scraped_at` | Bronce | Cuarentena (ya existe) | Nada; se conserva |
| `company_name_rating` | Bronce | Cuarentena (segunda defensa) | Umbral de inequívoco: patrón `^\d+[,.]\d{1,2}$` **y** contexto (rating en columna de rating o nombre ausente). Un nombre legítimo «4.0» o «24/7 Retail» no debe caer |
| `job_id_required` | Silver + Gold | Fallo de constraint | Conteo de nulos/vacíos por tabla (T-08); tipo `STRING` confirmado ⇒ `trim(job_id) <> ''` |
| `salary_range_order` | Silver + Gold | Fallo de constraint | Conteo de `min > max` con ambos no nulos; debe ser 0 o tratarse antes |
| `posted_date_bounds` | Gold | Fallo de constraint | Conteo de fechas fuera de rango; la estadística ya sugiere que **no** es candidata directa |
| `salary_quality_domain` | Silver + Gold | Fallo de constraint | Valores distintos de `salary_quality` (T-08); alternativa de bajo riesgo si el dominio está cerrado |

T-05 debe dar a cada regla: identificador estable, capa/fuentes, expresión pura,
acción, evidencia y tests (válida, inválida, nula, límite y repetición). Las
reglas descartadas quedan con medición y motivo.

## 9. Desconocidos `[NO VERIFICADO]`

| Tema | Motivo |
|---|---|
| Esquema y tipos reales de `<fuente>_quarantine` | Las 8 tablas no existen. |
| Conteo de filas con `company_name` = rating en bronce/silver/gold | Requiere SQL (T-08). |
| Conteo de `job_id` nulo/vacío en silver/gold | Requiere SQL; la estadística de gold da `nullCount = 0`, pero no cubre `''` ni las tablas silver. |
| Conteo de `min > max` y de importes no positivos | Requiere SQL (T-08); no hay tests del caso invertido (hueco de T-05). |
| Filas con `posted_date` fuera de 2026–2089 y su reparto por fuente | Requiere SQL; la estadística solo da mínimo/máximo globales. |
| Valores distintos de `salary_quality` y su distribución | Requiere SQL. |
| Histórico de cuarentena local de scrapers por fuente/motivo | Está fuera del alcance de T-01 (y es otra capa); no se ha leído. |
| Reglas exactas que Free Edition permite demostrar (row filter/mask) | Sin credenciales; se confirma al inicio de T-03. |

## 10. Qué se conserva y qué no se toca

- Se conservan **sin cambios** `missing_key`, `missing_scraped_at`, la terna de
  identidad de cuarentena, la dedup de bronce/silver/gold y el flag de fecha.
- No se toca el crudo de ADLS, ni se reprocesa histórico, ni se borran
  cuarentenas (no hay ninguna en Delta).
- No se aplica ningún DDL en T-01; el mapa de §7.2 es la entrada de T-05/T-07.

## 11. Estado final tras T-08 y T-10 (2026-11-09)

> Esta sección actualiza el inventario; las tablas de §8 y §9 conservan el
> estado del 2026-11-06 y no se reescriben.

- **Reglas de bronce activas:** `missing_key`, `missing_scraped_at` y
  `company_name_rating` (T-06). El run programado `762969079133747` procesó
  11.710 filas y **0 fueron a cuarentena**; no se creó ninguna tabla
  `<fuente>_quarantine`.
- **Constraints aplicadas:** `job_id_required` y `salary_range_order` en las 4
  silver y `gold.fact_offers` (20 en total, 10 dev + 10 prod), verificadas por
  API y respetadas por el run real.
- **Regla pospuesta:** `posted_date_contract` sigue `[NO VERIFICADO]` como
  constraint; se midieron 138 filas anteriores a 2026 en cada catálogo y
  `IsValidPostingDate` es un flag, no un filtro.
- **Desconocidos cerrados:** esquema/tipos de cuarentena (no hay tablas),
  conteos de `job_id` y de rango salarial (0 violaciones en T-08), y valores de
  `salary_quality` (perfilados en la sesión única).
- **Desconocido que no se cierra:** el reparto exacto de eventos de auditoría y
  el RBAC de Azure de la Managed Identity siguen `[NO VERIFICADO]`; no se
  inventan.
