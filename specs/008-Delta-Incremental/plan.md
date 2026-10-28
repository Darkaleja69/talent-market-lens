# Plan técnico: bronce deduplicado e ingesta incremental

> Detalle del **bloque A** (T-01–T-06) y del **bloque B** (T-07–T-12, §10).
> Actualizado el **2026-10-24**: trae a esta rama el merge de `main` con el
> cierre de la 009 (cadencia semanal, job de 8 tareas con `Build_Report_Export`,
> compute `D4ds_v4` y presupuesto de runs) y planifica el bloque B mientras
> T-06 espera el run programado del domingo.

## 1. Objetivo y límites

Bronce = estado actual por oferta (upsert idempotente). El landing no cambia en
ninguno de los dos bloques. El bloque B (§10) convierte las cuatro silvers de
ofertas en incrementales por grupo semántico, con watermark derivado de
`_ingest_date`; las dimensiones `companies_*` y toda la capa oro siguen en
reconstrucción completa.

## 2. Estado de partida (tras la 007)

- `databricks_notebooks/bronze_ingest.ipynb`: **un** notebook con cuatro
  secciones (indeed, linkedin, infojobs, multi_site). Cada una: Auto Loader
  (`cloudFiles`, parquet, sin notificaciones) + `checkpointLocation`
  `<checkpoints_root>/<fuente>/ckpt` + `schemaLocation .../schema` +
  `mergeSchema=true` + `trigger(availableNow=True)` + `.toTable(...)` (append).
  Añade `_ingest_date` (`current_date()` del lote, no la fecha del fichero) y
  `_source_file` (desde `_metadata.file_path`).
- Bundle de la 007 como vía de despliegue y validación: `databricks.yml` con
  targets `dev` (catalog `job_offers_dev`, checkpoints `_checkpoints_dev`,
  `export_prefix gold/web_export_dev`, job `Git Based ELT Dev`, sin
  programación) y `prod` (job `Git Based ELT` `916863262593632`, cron
  `50 0 23 * * ?`, `timeout_seconds: 1800`). El deploy **no ejecuta** el job.
- Auditoría del bronce (007, T-10a, solo lectura): `job_id` con **0 nulos y 0
  vacíos** en LinkedIn y multi_site (prod y dev; indeed e infojobs **no** se
  auditaron entonces); `scraped_at` es string ISO-8601 con offset único
  `+00:00` en esas dos fuentes; en `multi_site` la columna `site` llega
  poblada (6 valores) y la clave compuesta se forma **antes** del rename a
  `source_scraper`.
- Las cuatro fuentes **no comparten nombres ni tipos** en bronce (mapa en
  `design-upsert.md` §D0): indeed `job_key` + `scraped_at` timestamp UTC;
  infojobs `id_oferta` + `fecha_scraped` timestamp local; linkedin `job_id` +
  `scraped_at` string ISO; multi_site `site` + `job_id` + `scraped_at` string
  ISO.
- `databricks_notebooks/Deduplicate_Job_Offers.py` (007, T-10a) ya tiene
  `keep_latest_by(df, keys, order_by=("scraped_at", "_ingest_date"))` con
  ventana `row_number()` y 9 tests: dedup del **lote** reutilizable por el
  upsert; las filas con clave nula o en blanco pasan sin reducir.
- Bronce dev **normalizado el 2026-10-24** (T-04, job `dedupe_oneoff`, run
  `328818148400464`): indeed 3.039, infojobs 2.617, linkedin 14.289 y multi_site
  11.067, una fila por clave y **0 filas sin clave** en las cuatro. Prod sigue
  acumulado (a 22-oct: indeed 9.552 filas/2.959 claves, infojobs 45.044/2.587,
  linkedin 17.080/14.084, multi_site 12.159/11.086) y ha ingerido después con el
  sink *append*: la one-off de T-06 tendrá más histórico.
- Líneas base de tiempos de T-05. Ojo al **sink** y al **tamaño del lote**
  (días de ficheros acumulados), que es lo que hace comparables los runs:

| Run | Fecha | Tareas | Sink de bronce | Lote | Total | Bronce exec | Bronce setup |
|---|---|---|---|---|---|---|---|
| `540330014336242` | 18-oct | 7 | append | diario | 17 m 43 s | 1 m 14 s | 6 m 21 s |
| `339441526455403` (T-03) | 21-oct | 7 | **upsert** | ~3 días | 13 m 39 s | **2 m 29 s** | 2 m 42 s |
| `18912486133844` (009) | 23-oct | **8** | append | ~2 días | 16 m 19 s | 1 m 11 s | 6 m 22 s |
| `274846748795055` (T-05 A) | 24-oct | 8 | **upsert** | ~1 día | **13 m 16 s** | 1 m 46 s | 6 m 22 s |
| `923443117247499` (009, prod) | 23-oct | 8 | append | diario | 18 m 56 s | 1 m 10 s | 6 m 52 s |

  El **arranque del clúster** (`setup`) varía entre 2 m 42 s y 6 m 52 s según el
  run y **no mide el código**: la comparación del ingest usa el tiempo de
  **ejecución** de bronce. T-05 exige bronce **exec ≤ 2 m 29 s** (T-03, único run
  *upsert* previo) y **total < 16 m 19 s** (run de 8 tareas). El *upsert* tarda
  más en el bronce que el *append* (1 m 46 s frente a 1 m 10–14 s, porque
  deduplica el lote y hace MERGE) y aun así el run completo es más corto, porque
  las silvers procesan menos filas (2,8–3,6 min frente a 4,1–8,9 min en T-03).
  Los 17 m 43 s y 18 m 56 s quedan como referencia de «lo actual». El lote de
  T-05 depende de cuándo se lanzó el último ingest en dev, así que se anota su
  tamaño (`numInputRows` y días desde el run anterior) junto al tiempo.

### Estado tras la 009 (2026-10-24)

- **Job de 8 tareas:** Bronze → 4 Silver (paralelas) → Gold →
  `Build_for_Web-Dashboard` y `Build_Report_Export`. El export del informe
  depende solo de Gold y escribe `gold/pbi_export(_dev)` (6 Parquet + `meta.json`,
  modo `full`, sin columnas internas filtradas); el público conserva su contrato.
  La ingesta de esta spec está aguas arriba: cualquier cambio de contrato en
  bronce/silver/gold rompe los dos exports.
- **Cadencia:** prod `50 0 23 ? * SUN` (`Europe/Madrid`, `UNPAUSED`); dev sin
  programación. El dashboard publica los lunes a las 01:00 UTC y **no despliega**
  si `meta.json.data_date` tiene más de 8 días: un fallo semanal deja la web con
  datos viejos pero visibles, y no hay margen para «reintentar mañana».
- **Compute:** `Standard_D4ds_v4` single node, `CLASSIC_PREVIEW`, runtime
  `17.3.x-scala2.13`, `timeout_seconds: 1800`. `D2ds_v6` no completó el run
  (`954013261343017`); el ajuste de compute es de la 010.
- **Coste de referencia:** run dev completo ~0,24 €; run corto de un notebook
  ~0,05–0,13 €; cada despertar del SQL warehouse 2X-Small ~0,43 € (10 min
  facturables). Presupuesto de esta spec: **3 runs de dev y 0 sesiones SQL**
  (≤ 2,0 €).
- **Línea base de tiempos:** el ingest nuevo de T-03 (13 m 39 s, bronze
  5 m 11 s, sink *upsert*) es de **7 tareas**; el run de la 009 (`18912486133844`)
  es de **8 tareas** y 16 m 19 s, pero con el sink *append*. T-05 compara el
  bronce con 5 m 11 s y el total con 16 m 19 s. Detalle y tabla en §2.
- **Tamaño del lote:** en prod, el primer run semanal leerá ~7 días de ficheros
  nuevos (antes eran diarios) y la one-off de T-06 tendrá más histórico que
  deduplicar. En dev el lote depende de cuándo se lanzó el último ingest: T-05
  anota `numInputRows` y los días transcurridos, y no extrapola de T-03.

## 3. Arquitectura objetivo

```text
landing (cruda, snapshots acumulativos)
  → Auto Loader (checkpoint; ver §4 «checkpoint»)
  → foreachBatch: dedup del lote por clave + MERGE a bronze (upsert)
  → silvers (sin cambios en este bloque)

bronce (estado actual: 1 fila por clave)
  + una única ejecución one-off que deja prod en ese estado (RF-2)
```

Claves por fuente (una tabla por fuente, sin cambios de nombre). La columna del
bronce **no** se llama igual en las cuatro: las silvers renombran al leer.

| Fuente | Tabla bronce | Columna clave | Columna temporal |
|---|---|---|---|
| indeed | `bronze.indeed` | `job_key` | `scraped_at` (timestamp) |
| linkedin | `bronze.linkedin` | `job_id` | `scraped_at` (string ISO) |
| infojobs | `bronze.infojobs` | `id_oferta` | `fecha_scraped` (timestamp) |
| multi_site | `bronze.multi_site` | `site` + `job_id` | `scraped_at` (string ISO) |

## 4. Diseño del upsert (cerrado en T-01)

- Clave y temporal por fuente: la tabla de §3 y `design-upsert.md` §D0. Son las
  columnas de bronce que las silvers renombran a `job_id`/`scraped_at`; se usan
  tal cual, sin normalizar.
- Condición de actualización: la fila entrante gana si su instante de captura
  es **estrictamente mayor**; empate → `_ingest_date`; empate exacto → no se
  actualiza. La comparación usa `try_to_timestamp` (no `to_timestamp`): con ANSI
  activo, un blanco o un valor malformado abortaría el lote.
- Dedup del lote: primero se separan las inválidas (cuarentena) y después se
  deduplica lo válido por instante, con la misma semántica de
  `keep_latest_by` (ventana `row_number()`); así una fila inválida no desplaza
  a una válida de la misma clave.
- Claves o temporales nulas, en blanco o imparseables: **Decidido (D4):** van a
  `bronze.<fuente>_quarantine` con motivo (`missing_key` /
  `missing_scraped_at`), `_quarantine_at`, `_source_file` y `_row_hash`, escritas
  en el mismo `foreachBatch` y **deduplicadas por huella de fila
  (`sha2` de `to_json(struct(columnas de la fuente))`) + motivo + fichero**
  (por clave no vale: las filas sin clave no tienen clave). Se descartan del
  MERGE.
- Columnas de control: `_ingest_date` y `_source_file` se actualizan al ganar
  la fila nueva (reflejan la última captura que escribió la fila).
- Evolución de esquema: `mergeSchema` del stream intacto, pero con
  `foreachBatch` el `.option("mergeSchema","true")` del `writeStream` no actúa
  sobre el sink: se usa `MERGE WITH SCHEMA EVOLUTION INTO ...` (DBR 15.2+) y se
  comprueba que `_rescued_data` sigue apareciendo como columna normal (RF-3).
- **Checkpoint (decidido, D5):** plan A, conservar
  `<checkpoints_root>/<fuente>/ckpt`. No se deduplica lo ya escrito, así que la
  one-off de migración (§5) es obligatoria; a cambio no se relee el landing
  (14,4 GB / 247 ficheros en dev). Plan B si el cambio de sink falla en T-03:
  `ckpt_v2` (relectura completa) + `drop` y reconstrucción.

## 5. Migración del bronce acumulado (RF-2)

- **dev:** normalizado el 2026-10-24 con la one-off (run `328818148400464`,
  4/4 tablas a una fila por clave). Si un lote nuevo vuelve a duplicar, se
  relanza el mismo job `dedupe_oneoff`.
- **prod:** una única ejecución de migración sobre la tabla existente
  (dedup por clave y temporal de cada fuente, con la misma condición del
  upsert), en ventana y **antes del primer lote del ingest nuevo**, con conteos
  antes/después por fuente. La alternativa `drop` + reingesta desde landing
  queda descartada con el plan A (implica relectura). Se ejecuta en T-06.
- Rollback: redeploy del commit anterior del bundle (mecanismo de
  `specs/007-ELT-As-Code/cutover.md`) y, si la tabla queda inconsistente,
  `RESTORE`/time travel a la versión previa del `_delta_log`.

## 6. Comprobaciones

- Tests del módulo (pytest + Spark local): gana el más reciente, empate,
  desorden, clave nula/en blanco, idempotencia y contrato de columnas
  (mismo enfoque que los 9 tests de `test_dedup_job_offers.py`), más el
  enrutado a cuarentena (motivo y no duplicación al reprocesar).
- **Local-first:** todo lo que no necesite Databricks (SQL generado, dedup,
  idempotencia del MERGE, contrato de columnas) se prueba en local con pyspark;
  los runs se reservan a lo que exige el workspace (Auto Loader real, tablas de
  Unity Catalog y el job cluster).
- Ciclo dev en verde y **más rápido** que la línea base del §2, con
  idempotencia demostrada (segunda ejecución sin cambios de filas) y conteos
  comparados con prod para la misma ventana. Fuente de cada dato, sin SQL:
  conteos de **bronce** de la salida del notebook one-off (T-04/T-06), conteos de
  **gold** del `meta.json` de los dos exports, y la inmovilidad del run B por
  `numInputRows = 0` de cada fuente. Nada de SQL Editor: la spec no usa sesiones
  SQL.
- Presupuesto: **3 runs de dev** (T-04 one-off, T-05 ciclo y T-05 repetición) y
  ninguna sesión SQL. Cada run se anota en `docs/costs.md` con motivo, id y coste
  estimado; un run fallido consume presupuesto igualmente.
- Exports de la 009 intactos en cada run dev: `gold/web_export_dev` y
  `gold/pbi_export_dev` escritos, `meta.json` válido, y las 8 tareas en verde.
- Suites existentes en verde: `databricks_notebooks` y `scrapers-pipeline`.
- Prod: ventana **previa a un domingo**, rollback disponible, one-off de prod
  antes del primer lote del ingest nuevo, **run programado del domingo**
  verificado (8/8 tareas, exports actualizados y dashboard publicado) y bitácora
  del roadmap actualizada; notas `02-delta.md` y
  `04-incremental-architecture.md` escritas antes de cerrar.
- Evidencia por tarea en `specs/008-Delta-Incremental/evidence.md` (mismo
  formato que la 007: fecha, tarea, comprobación, resultado).

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| MERGE con esquema evolucionando | Probar `MERGE WITH SCHEMA EVOLUTION` con una columna nueva en dev (T-03); el `mergeSchema` del `writeStream` no actúa con `foreachBatch` |
| Llegada desordenada o ficheros viejos re-subidos | Condición por instante de captura + tests de desorden (T-02) |
| Checkpoint reutilizado no reprocesa el histórico | One-off de migración (§5) separada del ingest |
| MERGE por lote sobre tablas grandes en dev | Medir en T-03/T-05 con la línea base del §2 |
| Migración de prod en caliente | Ventana, conteos antes/después y `RESTORE` documentado |
| Merge en prod: la tabla destino debe existir (el MERGE ya no la crea como `toTable`) | Fail fast en el handler (`ValueError` claro) y runbook: las 4 tablas bronce deben existir antes del run |
| CI: el deploy de dev solo admite `main`/`spec/007-elt-as-code` | Decidir en T-03: ampliar el guard a `spec/008-delta-incremental` o desplegar por CLI local (`--profile vs_code`). **Resuelto tras el merge de la 009:** el workflow admite `spec/008-delta-incremental` |
| Presupuesto de runs (3 dev, 0 SQL) | Un fallo de diseño en un run se paga: se corrige en local con tests antes de volver a lanzar; el run fallido se anota en `docs/costs.md` y se descuenta |
| Cadencia semanal: un run fallido deja la web con datos de la semana anterior | El dashboard no despliega si `meta.json.data_date` supera 8 días; el cutover se hace antes de un domingo y el run se vigila el lunes |
| Lotes semanales más grandes (7 días por run) | Medir en T-05 el tamaño del lote, el tiempo del MERGE y el efecto en la one-off; no extrapolar de los runs diarios de T-03 |
| La tarea `Build_Report_Export` alarga el run y depende de Gold | Es aditiva (solo depende de Gold); verificar en cada run dev que los dos exports se escriben y que su contrato no cambia |

## 8. Secuencia

1. **Bloque A:** T-01–T-06 (este documento), con el reparto de runs del §9.
2. **Bloque B:** T-07–T-12 (§10), planificado el 2026-10-24 mientras T-06 espera
   el run del domingo. El orden es diseño → módulo y tests → notebooks en dev →
   dos runs de dev → producción → cierre. **Descopeado a la 010 el 2026-10-28**
   tras los `TIMEDOUT` de T-10: el diseño queda aquí y la validación y el
   despliegue pasan a la spec de rendimiento.

## 9. Presupuesto de runs (heredado de la 009)

La política de la 009 (`plan.md` §17) fija para esta spec **3 runs de dev y 1
sesión SQL** (≤ 2,0 €). Decisión del 2026-10-24: **se renuncia a la sesión SQL**
y se usan los 3 runs de dev; la evidencia de historial y conteos sale del propio
run.

| Tarea | Ejecución | Runs de dev | Coste aprox. |
|---|---|---|---|
| T-03 (hecho, 2026-10-21) | 2 runs dev: uno fallido (`__batch_indeed`) y el verde `339441526455403` | 2 (**previos a la política**, hundidos) | ~0,48 € |
| T-04 (hecha, 24-oct) | job `dedupe_oneoff` en dev (clúster propio, 8 m 58 s) | 1 | ~0,11 € |
| T-05 | ciclo dev completo con las 8 tareas | 1 | ~0,24 € |
| T-05 | repetición inmediata sin ficheros nuevos (idempotencia) | 1 | ~0,24 € |
| T-06 | one-off de prod + deploy en la ventana previa al domingo | 0 (prod) | ~0,05–0,13 € |
| T-06 | verificación real | **run programado del domingo** | ya en el techo de la 009 |
| T-10 (bloque B) | run dev con las silvers incrementales | 1 | ~0,24 € |
| T-10 (bloque B) | repetición + backfill forzado (idempotencia) | 1 | ~0,24 € |
| **Total dev tras la política** | | **3 (+2 del bloque B)** | **≈ 1,1 €** |
| **Sesiones SQL** | | | **0** |

Reglas de gasto:

- Si un run de dev falla por un defecto de código, el siguiente intento se
  descuenta del total: se corrige en local con los tests antes de relanzar.
- **No hay sesión SQL reservada.** Si hiciera falta una (por ejemplo,
  `DESCRIBE HISTORY` de una tabla concreta), se pide aprobación explícita a la
  persona, se agrupa en una sola ventana de 10 min (~0,43 €) y se anota como
  extra fuera del presupuesto en `docs/costs.md`.
- Si se agotan los 3 runs del bloque A sin cerrarlo, se pide aprobación a la
  persona y el run extra se descuenta del presupuesto de la spec siguiente (010),
  como prevé la política de la 009 §17.
- **Bloque B (aprobado el 2026-10-24):** presupuesto propio de **2 runs de dev y
  0 sesiones SQL** (~0,48 €); no consume los del bloque A.
- Prod no se prueba con runs manuales extra: la comprobación real es el run
  programado del domingo.

## 10. Bloque B — silvers incrementales

> **Descopeado a la spec 010 el 2026-10-28.** El diseño de esta sección y el
> código de T-07–T-09 se conservan como referencia y como base del trabajo de
> rendimiento; la validación A/B, la decisión de compute y el despliegue a
> producción se siguen en `specs/010-Performance-And-Layout`. Esta spec cierra
> con el bloque A.

### 10.1 Estado de partida (código actual)

Las cuatro silvers **solo comparten el esqueleto**, no el proceso: lectura
completa de bronce → cadena de transformación **propia de cada fuente** →
`enrich` → normalización de empresa → dedup por
`_key = lower(trim(title))|lower(trim(company_name))|lower(trim(location_city))`
con `row_number()` ordenado por `posted_date` desc, `_ingest_date` desc →
`drop` de internas → `overwrite`. El bloque B **no unifica esas cadenas**: el
módulo nuevo solo calcula la ventana, el conjunto afectado y el MERGE; cada
notebook conserva su transform tal cual.

| Fuente | Particularidades que hay que preservar |
|---|---|
| indeed | Renombra `job_key`, `company`, `city`, `state`, `country`, `salary_text`, `salary_type`, `workplace_type`, `search_term`, `ingest_date`, `description_text`; fija `salary_currency`=EUR, `skills`/`experience_level` vacíos y `source_scraper`=Indeed; `companies_indeed` se deriva del bronce **antes** de enrich y normaliza `company_rating` por `review_count`; **es la única que hace `normalize_location_codes`**; su `drop` incluye además `salary_min`/`salary_max` y reemplaza `salary_period`/`work_mode` por las columnas `_norm` de enrich |
| linkedin | `keep_latest_by(["job_id"])` **antes** de enrich; solo renombra `description_full`; deriva `posted_date` de `posted_datetime`/`posted_relative` usando `scraped_at` (intervalos de minutos a años); `companies_linkedin` se deriva del frame preparado (antes del cast) y limpia `company_size` por rangos, con `company_industry`/`company_description`; escribe ofertas y empresas en celdas separadas |
| infojobs | Renombra columnas en español (`id_oferta`→`job_id`, `titulo`, `empresa`, `ciudad`, `provincia`, `pais`, `fecha_publicacion`, `modalidad`, `moneda`, `salario_*`, `periodo`, `descripcion_snippet`, `jornada`, `fecha_scraped`→`scraped_at`, `fuente`→`source_scraper`, `keyword_buscada`); `posted_date` ya viene fecha; `companies_infojobs` se deriva de `df_completed` (solo nombre); **sin** normalización de códigos de localización |
| multi_site | `keep_latest_by(["site","job_id"])` antes de enrich; renombra `description_snippet`, `site`→`source_scraper`, `salary_disclosed`→`is_salary_available`; deriva `posted_date` de `posted_relative` en inglés y español; `companies_multi_site` desde `df_completed` (solo nombre); **sin** normalización de códigos de localización |

Consecuencias de diseño:

- El **transform se queda en cada notebook**; el módulo solo recibe/entrega
  DataFrames. Cualquier intento de factorizar las cadenas en un único proceso
  cambiaría resultados (fechas, monedas, empresas, columnas que se descartan).
- Las tablas `companies_*` siguen en **reconstrucción completa**: `indeed` y
  `linkedin` las derivan del frame preparado (antes de enrich), como hoy;
  `infojobs` y `multi_site` pasan a leer `silver.offers_<fuente>.company_name`
  (los mismos nombres ganadores, ya canónicos) y **no reaplican ninguna
  normalización**, solo `dropDuplicates`. El orden es ofertas primero y
  empresas después.
- `gold_build`, `Prepare_Gold.py` y los exports no cambian.
- Bronce tras el bloque A: 3.039 + 2.617 + 14.289 + 11.096 ≈ 31k filas.

### 10.2 Diseño

**Unidad de incremental: grupo semántico.** No por `job_id`: el dedup de silver
es por `_key`, así que un MERGE por clave de negocio cambiaría el resultado
(movería el ganador de un grupo). Se recalcula el grupo entero.

**Conjunto afectado (clave exacta sobre bronce):**

```text
city_bronze  = de.location_city_normalized_column(location_city)   # expresión columnar
key_bronze   = key_expr(title, normalize_company(company_name), city_bronze)
changed      = prepared if from_date is None else
               prepared.where(_ingest_date >= from_date)       # ventana abierta si tabla vacía
# to_date, si viene, se aplica como tope superior (ver changed_since del diseño)
keys_new     = changed.select(key_bronze.alias("k"))
keys_old     = silver.join(changed_ids, <clave de negocio>)    # job_id (+ source_scraper)
                     .select(key_expr.alias("k"))
affected     = keys_new.union(keys_old).distinct()
candidates   = prepared.where(key_bronze IN affected)          # igualdad exacta
recompute    = <cadena propia del notebook>(candidates)        # intacta, sin unificar
MERGE INTO silver s USING recompute n ON <key iguales>
  WHEN MATCHED AND n._ingest_date IS NULL THEN DELETE          # marcadores
  WHEN MATCHED THEN UPDATE SET *
  WHEN NOT MATCHED AND n._ingest_date IS NOT NULL THEN INSERT *
```

La unión por `job_id` es un **semi-join**, no un `IN` con lista recogida al
driver: la ventana puede traer miles de claves.

**Por qué la clave exacta es posible (decisión 2026-10-25).** `enrich` **no toca**
`title` ni `company_name`, y normaliza `location_city` con la misma regla que
`Enrich_Job_Offers_Dataframes.location_city_normalized_column`, la misma
expresión columnar que escribe `enrich`. Para usarla sobre el bronce sin
repetirla se añade al módulo `location_city_normalized_column(col)` (construida
con los mismos mapas y reglas, sin UDF: un UDF que importa el módulo puede fallar
en los *workers*), y `enrich` pasa a usarla para que haya **una sola
definición**. Por tanto la clave se puede calcular sobre el bronce con la misma
normalización que usa la silver: título tal cual, empresa con el normalizador de
la fuente (callable del notebook) y ciudad con esa función. Los candidatos se
seleccionan por **igualdad exacta de clave**, no por un superconjunto:
desaparecen el atajo por pares (título, empresa), la guarda de nulos y la guarda
de `|`, y RF-7 no necesita excepciones. El coste es una expresión columnar sobre
~31k filas; lo caro (`enrich` con regex sobre descripciones) se sigue ejecutando
solo sobre los candidatos. El supuesto nuevo es la equivalencia de esa función
con la columna de `enrich`: se prueba en local (P16b) y con datos reales en el
run B. `enrich` debe llamarse con `add_location_fill=True` y
`normalize_location_city=True` (sus defaults); con cualquiera de las dos en
`False`, la ciudad de la silver y la clave de bronce divergen.

No se persiste `_key`: se recalcula desde las columnas. La cadena de cada fuente
(rename, fechas, enrich, empresas, normalización de localización y `drop`) **no
se toca**: el notebook solo cambia de dónde lee el DataFrame de ofertas y cómo
lo escribe. El `keep_latest_by` temprano de linkedin (`job_id`) y multi_site
(`site` + `job_id`) se ejecuta en `prepare_bronze` sobre el bronce completo (hoy
es no-op) y los candidatos contienen todas las filas del grupo, así que da el
mismo resultado que sobre la tabla completa.

`keys_old` cubre la **migración de grupo** (una oferta que cambia de ciudad o
de título): el grupo antiguo se recalcula y expulsa la fila huérfana. Lo que el
incremental **no** puede detectar es la **desaparición** de filas de bronce
(`RESTORE`, rewrite o borrado, incluido un cambio de `job_id` o de `site`):
esos casos exigen `force_full` y así se documenta en el runbook.

**Watermark.** `watermark = max(_ingest_date)` de `silver.offers_<fuente>`;
ventana `>= watermark - lookback_days` (7 por defecto). Sin `_ingest_date` en
la tabla (primera ejecución con el código nuevo) → `force_full`: se lee el
bronce completo y se escribe con `overwrite` (la migración es el propio primer
run). **Tabla vacía pero con la columna** → `from_date` nulo, ventana abierta y
MERGE de todos los grupos (no es `full`). Ventana vacía → no se escribe nada
(sin commit). `force_full` llega como texto desde el widget y se parsea con
`as_bool` (`"false"`, `"0"`, `""` → `False`).

**Columnas de control.** Silver conserva `_ingest_date` (lo necesita el
watermark); `_source_file` se queda en bronce. Oro la ignora por selección
explícita (`FACT_OFFERS_COLUMNS`) y los exports proyectan listas explícitas
(`WEB_FACT_COLUMNS`, `PBI_FACT_OFFERS_COLUMNS`); se comprueba en T-09/T-10 que
ni las tablas oro ni los Parquet cambian. El contrato de silver se mantiene por
fuente: **indeed 31 columnas, linkedin/infojobs/multi_site 33** (las tres
conservan `salary_min`/`salary_max`) **+ `_ingest_date`**; no hay lista global.

**Escritura.** MERGE sobre el grupo semántico con origen y destino deduplicados
(una fila por grupo). El `overwrite` con `overwriteSchema` sigue usándose solo
en la reconstrucción completa; el MERGE usa `WITH SCHEMA EVOLUTION` y el origen
lleva las columnas del destino **más las nuevas** (proyectar solo a
`target.columns` impediría la evolución).

**Dimensiones y oro.** `companies_*` y toda la capa oro siguen en
reconstrucción completa: el dedup de negocio y `salary_filled` son ventanas
globales y su tamaño es pequeño.

**Reproceso por fecha y fuente.** La fuente la fija el notebook (`source` sale
de su nombre); los widgets son `from_date`, `to_date`, `lookback_days` y
`force_full`; el notebook filtra la ventana y el MERGE es idempotente. El
runbook documenta el backfill y su coste de lectura (el bronce completo se lee
para el join sobre grupos afectados, pero solo se transforma lo afectado).

### 10.3 Comprobaciones

- **Equivalencia (gate principal, local):** con un fixture de bronce que cubra
  los casos (clave nueva, captura más reciente, captura más antigua, migración
  de grupo, empate, grupo sin cambios, nulos en las componentes), comparar
  `transform(bronce completo)` con la aplicación incremental en dos pasos: los
  DataFrames deben ser iguales (mismo conjunto de filas y columnas). Incluye
  **P16b**: `location_city_normalized_column` contra la columna de `enrich` en
  un fixture con acentos, países, ciudades y nulos (la función pura
  `location_city_normalized` queda como referencia), y la clave calculada sobre
  bronce contra la de la silver.
- **Idempotencia:** segunda aplicación de la misma ventana deja el mismo
  contenido (puede haber commit, no cambio de filas); los conteos de grupos
  afectados, borrados o fusionados pueden bajar porque la primera pasada ya
  limpió los grupos migrados. `force_full` → misma tabla.
- **Run dev (2 runs):** ciclo de 8 tareas en verde. **Run A** es la migración
  (la tabla no tiene `_ingest_date` → modo completo con `overwrite`). **Run B es
  un backfill incremental** con `from_date`/`to_date` sobre bronce existente
  (sin ficheros nuevos), que **sí ejecuta el MERGE, los marcadores y el
  conjunto afectado**, y repite la misma ventana una segunda vez dentro del
  notebook para demostrar idempotencia. Sus exports deben ser **idénticos byte
  a byte** a los del run A (equivalencia en datos reales). Se miden silver y
  total frente a 2,8–3,6 min y 13 m 16 s, anotando el tamaño del lote (desde el
  run de T-05 hay ficheros nuevos en el landing, así que los exports no son
  comparables con los de entonces).

- Sin SQL: la evidencia sale de la salida de los notebooks (resumen JSON con
  conteos y ventana), de las métricas del run y de los `meta.json`.
- Suites: `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q`.

### 10.4 Migración y producción

- No hay one-off: el primer run con el código nuevo reconstruye la silver
  completa (misma duración que hoy) y deja `_ingest_date` poblado.
- Deploy a prod en ventana previa a un domingo (el siguiente al cierre de T-06),
  con rollback de bundle y `RESTORE` de las silvers disponible; verificación con
  el run programado y el dashboard del lunes, sin SQL warehouse.

### 10.5 Riesgos

| Riesgo | Mitigación |
|---|---|
| **Dar por iguales las cuatro silvers** | Cada cadena de transformación se queda en su notebook; el módulo solo cubre ventana, conjunto afectado y MERGE. Inventario de diferencias en §10.1 y diff celda a celda aprobado en T-09 |
| **`location_city_normalized_column` no coincide con la columna de `enrich`** | P16b (columnar vs `enrich`, con la pura como referencia) y el run B exige exports idénticos a A. `enrich` debe llamarse con `add_location_fill=True` y `normalize_location_city=True` |
| Divergencia entre incremental y rebuild completo | Test de equivalencia como gate (T-08) y comparación de exports en dev (T-10) |
| Migración de grupo (cambia título/ciudad) deja filas huérfanas | `keys_old` por `job_id` cambiado; caso cubierto en tests |
| Fichero tardío con `_ingest_date` anterior al watermark | `lookback_days` = una cadencia (7) y backfill documentado |
| MERGE con columnas nuevas | `MERGE ... WITH SCHEMA EVOLUTION` y test de contrato |
| `_ingest_date` se filtra a oro o a los exports | Oro selecciona columnas explícitas; los exports proyectan listas explícitas que no la incluyen; comprobación en T-09/T-10 |
| `companies_*` alimentadas por error con el subconjunto afectado (linkedin las deriva del frame preparado; infojobs y multi_site leen la silver recién escrita) | Se mantiene la lectura completa para `indeed`/`linkedin`; `infojobs`/`multi_site` leen la silver sin reaplicar normalización y con `dropDuplicates`; el diff de T-09 se revisa celda a celda |
| Dos runs de dev insuficientes | Un fallo de código se corrige en local con tests antes de relanzar; si se agotan, se pide aprobación |
