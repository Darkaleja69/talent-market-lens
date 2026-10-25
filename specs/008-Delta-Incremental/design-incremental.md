# Diseño del incremental de silver (T-07, bloque B)

Entrada de **T-08** (módulo `Silver_Incremental.py` y tests), **T-09** (notebooks
de silver), **T-10** (dos runs de dev) y **T-11** (producción y runbook).
Decisiones cerradas con la persona el **2026-10-24**; complementa a
`design-upsert.md` (bloque A) sin reabrirlo.

**Revisión 4 (2026-10-25): clave exacta sobre bronce.** Sustituye el mecanismo
de selección de la revisión 3 (superconjunto por pares) y cierra los tres
bloqueantes de la última verificación. `spec.md` (bloque B y RF-7..RF-11),
`plan.md` §10 y `tasks.md` (T-07..T-10) ya recogen esta decisión y son la fuente
de verdad; este documento la desarrolla y concreta.

**Revisión 5 (2026-10-25, tras el NO PASA de la verificación de T-07).** Cierra
dos bloqueantes nuevos y cinco importantes:

1. **La repetición se compara por contenido, no por conteos.** Repetir la misma
   ventana puede reducir `affected_groups`, `deleted_groups` y `merge_rows`
   porque la primera pasada ya limpió los grupos migrados; el contenido final,
   `target_rows` y `recomputed_groups` (en el fixture de P15/P21) sí deben
   coincidir. Si el grupo antiguo conservara filas, `recomputed_groups` podría
   bajar y el contenido manda (§5.5, P15/P21, caso 19, §10 T-10).
2. **Ventana abierta en `changed_since`.** Si `from_date is None` no se filtra
   por `_ingest_date`; `to_date` es opcional. Una tabla vacía con la columna se
   prueba con `run` completo y MERGE de referencia (P1b).
3. **Nombres sin mezclar.** La clave usa `location_city_normalized_column`; la
   función pura `location_city_normalized` queda solo como referencia de P16b.
4. **Precondición de `enrich`.** Solo normaliza la ciudad con
   `add_location_fill=True` y `normalize_location_city=True`; `build_offers` los
   pasa explícitos y P16b demuestra que `add_location_fill=False` no vale para
   la clave (3/28 diferencias medidas).
5. **P16b sin número fijo.** El fixture se construye explícitamente y el test
   cuenta los valores; la verificación contó 281/281 entre la pura y la
   columnar.
6. **Imports del módulo.** `Enrich_Job_Offers_Dataframes.py` necesita
   `from pyspark.sql import Column` y `from pyspark.sql import functions as F`
   a nivel de módulo (§10 T-08).
7. **Redacción.** El resumen distingue `changed_rows` (filas de bronce) de
   `affected_groups` (claves distintas); una tabla vacía con `_ingest_date`
   **no** es `full` (§5.5, §6, §9).

## Qué cambia en la revisión 4

1. **La clave se calcula sobre el bronce con la misma normalización que usará la
   silver.** `enrich` no toca `title` ni `company_name`, y normaliza
   `location_city` con la misma expresión columnar que expone el módulo
   `Enrich_Job_Offers_Dataframes.location_city_normalized_column` (la que
   `enrich` pasa a reutilizar). Por tanto:

   ```text
   key_bronze = key_from_columns(title,
                                 normalize_company(company_name),
                                 location_city_normalized_column(location_city))
   ```

   La empresa se normaliza con el callable de cada notebook
   (`apply_company_canonical(apply_company_normalization(df))`) y la ciudad con
   **una expresión columnar** sobre `prepared` (~31k filas), sin UDF ni
   serialización del módulo en los *workers*. No se ejecuta `enrich` completo
   para seleccionar candidatos.

2. **`affected` son claves exactas** (`keys_new` ∪ `keys_old`) y `candidates`
   son las filas de `prepared` cuya clave **es exactamente igual** a una de
   `affected`. No hay superconjunto por pares (título, empresa), ni guarda de
   nulos, ni guarda de `|`, ni excepciones a RF-7.

3. **`mode` solo puede ser `full` por tres motivos:** `force_full`, tabla
   inexistente o tabla sin `_ingest_date`. Una tabla **vacía** con la columna no
   fuerza `full`: la ventana queda abierta (`from_date = null`) y el MERGE
   inserta todo (mismo contenido que una reconstrucción, sin `overwrite`). Se
   retira el «watermark nulo» como motivo de `full`.

4. **`repeat_window` es un widget real** (celda 0), forma parte del contrato de
   `run` y produce el sobre `{"repeat_window": true, "runs": [s1, s2]}`. T-09 lo
   cablea y T-10 lo lee de `jobs get-run-output`.

5. **P15b y P16 incluyen `apply_company_normalization` (`initcap`) antes de
   `apply_company_canonical`**, que es la composición real de las cuatro
   silvers. Se añade **P16b** (equivalencia ciudad columnar ↔ columna de `enrich`
   y clave de bronce ↔ clave de silver) y se corrige el fixture G5: con clave
   exacta incluye nulos en las componentes y exige `incremental == full`.

### Los tres bloqueantes de la última verificación

| # | Bloqueante | Cómo se cierra en esta revisión |
|---|---|---|
| 1 | El documento describía degradaciones a `full` por claves inseguras, contra RF-7/RF-8/RF-11 | El mecanismo de clave exacta elimina la guarda y el superconjunto; `mode` queda con exactamente tres motivos (`force_full`, `table_missing`, `missing_ingest_date`) y el incremental siempre corre en el resto (§3 D-B4, §4, §5, §6) |
| 2 | `repeat_window` no estaba en el contrato ni en el resumen | Widget en la celda 0 (§2.2), parámetro de `run` y sobre `{"repeat_window": true, "runs": [...]}` (§4, §5.5), test P21 y lectura en T-10 (§5.5, §10) |
| 3 | P15b/P16 omitían `apply_company_normalization` y faltaba P16b | Composición real `initcap` → canónico en P15b/P16 (§8); P16b de ciudad y clave (§8); fixture G5 con nulos y `incremental == full` (§8) |

### Historial

- **Revisión 3 (2026-10-24):** contrato por fuente (indeed 31; linkedin,
  infojobs y multi_site 33), pseudocódigo ejecutable, run A/B, `as_bool`, sin
  widget `source`, vista temporal con nombre único y procedencia de
  `companies_infojobs`/`companies_multi_site`.
- **Revisión 2 (2026-10-24):** cast al contrato dentro de `build_offers`,
  definiciones a nivel del notebook, `companies_*` sin renormalizar y celdas de
  diagnóstico eliminadas.
- **Revisión 1 (2026-10-24):** primera propuesta (grupo semántico, watermark y
  MERGE).

Alcance: las **cuatro silvers de ofertas** (`offers_indeed`, `offers_linkedin`,
`offers_infojobs`, `offers_multi_site`) pasan a escritura incremental. Las
dimensiones `companies_*` y **toda la capa oro siguen en reconstrucción
completa** (motivo en la spec: las ventanas de dedup de negocio y
`salary_filled` de oro son globales y su tamaño es pequeño).

Presupuesto del bloque: **2 runs de dev y 0 sesiones SQL**. Todo lo que se pueda
decidir y probar en local se prueba en local antes de gastar un run.

---

## 1. Estado de partida del código

### 1.1 Forma común de las cuatro silvers

Hoy cada notebook hace, en este orden y **siempre sobre la tabla completa**:

1. `df = spark.read.table(bronze.<fuente>)` (lectura completa; LinkedIn y
   multi_site añaden `dedup.keep_latest_by`, que desde el bloque A es un no-op
   porque bronce ya tiene una fila por clave).
2. **Rename** por fuente a los nombres canónicos (tabla en §2.1).
3. Columnas derivadas y de relleno (`lit`, `posted_date` desde
   `posted_relative` en LinkedIn/multi_site) y **cast al contrato**
   (`df_structure_Schema`, 22 campos). Ese `select` es también el que
   **excluye `_source_file`**.
4. `de.enrich(...)`: skills, `experience_level`, `employment_type`,
   `role_category`, salario anual, `_norm` de periodo y `work_mode`,
   `posted_date_raw`/`posted_date_source`, `scraped_date` (que usa
   `_ingest_date` como último recurso) y **normalización de `location_city`**
   (expresión columnar por fila).
5. Normalización de empresa: `initcap` + colapso de espacios
   (`apply_company_normalization`, definida en cada notebook) y nombre canónico
   de las grandes compañías (`apply_company_canonical`, copia en el notebook o
   función del módulo `Enrich`; 128 patrones). En indeed/LinkedIn/multi_site se
   evalúa sobre los nombres distintos y se une por join, en InfoJobs es una
   expresión columna a columna que convierte `NULL` en blanco.
6. **Dedup por grupo semántico**:

   ```python
   _key = lower(trim(regexp_replace(
       concat_ws("|", title, company_name, location_city), r"\s+", " ")))
   w = Window.partitionBy("_key").orderBy(desc("posted_date"), desc("_ingest_date"))
   df = (df.withColumn("_rn", row_number().over(w))
           .filter(F.col("_rn") == 1)
           .drop("_key", "_rn"))
   ```

7. `drop` de columnas internas y de trabajo y rename de los `*_norm`.
8. `write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(...)`.

### 1.2 Lo que hay que tener presente

- **`_ingest_date` se tira hoy** en las cuatro (está en la lista de `drop`). El
  incremental la necesita: hay que **dejar de dropearla** y nada más (la columna
  ya viene casteada a `date`; la escribe `bronze_ingest` con `current_date()`,
  comprobado en el notebook).
- **`_source_file` nunca llega a silver** (bronce la trae del ingest; el
  `select` del contrato la deja fuera). Con la corrección 1 de la revisión 2
  viaja en `prepared` y sigue quedándose fuera en `build_offers`.
- **`enrich` no toca `title` ni `company_name`**: solo los lee. **Sí toca
  `location_city`**: la sustituye por `city_canon` (ciudad canónica en inglés;
  `NULL` → `''`; desconocida → el valor original recortado).
- **La normalización de `location_city` tiene una sola definición.**
  `Enrich_Job_Offers_Dataframes.location_city_normalized_column(col)` construye
  la expresión columnar que usa `enrich`: `trim(coalesce(col, ""))` →
  `translate(lower(...))` → `_LOCATION_CITY_CLEAN_RULES` (regex acumulativos) →
  `coalesce(map[_LOCATION_CITY_CANONIC][norm], map[clean], city_v)`. `enrich`
  la reutiliza para la columna `location_city` y la clave de bronce usa la
  **misma** función; no hay UDF, ni *pickle* del módulo, ni import en los
  *workers*. La función pura `location_city_normalized` se conserva como
  referencia documental y para la comprobación de P16b; P16b comprueba que la
  columnar (y la pura) coinciden con la columna de `enrich`. El refactor de
  `enrich` es un cambio de `Enrich_Job_Offers_Dataframes.py` y requiere
  **aprobación previa del diff** (T-08).
- **La composición de empresa que produce la silver es
  `apply_company_canonical(apply_company_normalization(x))`**: primero
  `initcap` + colapso de espacios, después canónico. `initcap` es imprescindible
  para el **nombre guardado** y para la dimensión `companies_*`: «ACME S.L.» se
  guarda como «Acme S.l.». La **`_key` no cambia** por `initcap`, porque la
  expresión la pasa a minúsculas: sin `initcap` la clave de bronce y la de silver
  seguirían coincidiendo (medido: 6 de 30 nombres cambian, la clave no; §8.1).
- **La clave se puede calcular sobre bronce** porque `title` y `company_name`
  llegan intactos a la silver y `location_city` se normaliza con la misma
  expresión columnar. A esa clave se le llama **clave exacta**. La misma
  expresión aplicada a bronce **crudo**, sin normalizar empresa ni ciudad, no
  sirve: «GOOGLE IRELAND LTD» en bronce es «Google» en silver, y «madrid» es
  «Madrid».
- **`concat_ws` omite los `NULL`.** Ya no es un riesgo: la clave se calcula con
  la **misma función sobre los mismos valores normalizados** a los dos lados.
  Dos tripletas distintas que producen la misma clave son, por definición, el
  mismo grupo en la dedup de silver; seleccionar por igualdad de clave recupera
  **todas** las filas de ese grupo, no un subconjunto. Lo que la revisión 3
  trataba como colisión insegura aquí es una colisión **legítima** (y
  preexistente) del grupo semántico.
- **`apply_company_canonical` es determinista y la composición con `initcap` es
  un punto fijo sobre el valor guardado** (§2.3, medido).
- **`companies_*` no se toca**, salvo la procedencia de InfoJobs y multi_site
  (§2.3) y el orden de escritura (después de las ofertas).
- La dedup usa `row_number()` sin desempate estable: con empate exacto en
  `posted_date` **y** `_ingest_date` dentro de un grupo, la fila ganadora no es
  determinista. Ya ocurre hoy (riesgo preexistente, aceptado en §9).

### 1.3 Contrato real de la silver: **uno por fuente** (medido, no supuesto)

> **No hay un contrato global de silver.** El contrato de cada tabla es el
> **contrato actual de su silver más `_ingest_date`** (que hoy se dropea y pasa a
> conservarse): **indeed 31 columnas**; **linkedin, infojobs y multi_site 33**.
> Las tres últimas conservan `salary_min` y `salary_max`; indeed los elimina en
> su `drop`. Los totales **ya incluyen `_ingest_date`**: el contrato de 22 la
> trae y lo que cambia es que deja de estar en la lista del `drop`.

| Fuente | Columnas | `salary_min` / `salary_max` | Cuenta |
|---|---|---|---|
| indeed | **31** | **no** (el `drop` los quita) | 22 del contrato + 14 de `enrich` − 5 (`salary_min`, `salary_max`, `scraped_at`, `salary_period`, `work_mode`) |
| linkedin | **33** | sí | 22 + 14 − 3 (`scraped_at`, `salary_period`, `work_mode`) |
| infojobs | **33** | sí | ídem |
| multi_site | **33** | sí | ídem |

Las cuatro comparten el mismo contrato de 22 campos (`df_structure_Schema`; en
LinkedIn se llama `offers_df_structure_Schema`) y el mismo `enrich`, que añade
14 columnas: `skills_source`, `experience_level_source`, `employment_type`,
`role_category`, `salary_source`, `salary_min_annual`, `salary_max_annual`,
`salary_quality`, `salary_period_norm`, `work_mode_norm`, `work_mode_source`,
`posted_date_raw`, `scraped_date`, `posted_date_source`.

Lista de columnas, en el orden del destino (`[...]` = **solo** linkedin,
infojobs y multi_site):

```text
job_id, job_url, title, company_name, location_city, location_region,
location_country, posted_date, is_salary_available, salary_currency,
[salary_min, salary_max,] salary_raw, experience_level, description_clean,
skills, source_scraper, search_role, _ingest_date, skills_source,
experience_level_source, employment_type, role_category, salary_source,
salary_min_annual, salary_max_annual, salary_quality, salary_period,
work_mode, work_mode_source, posted_date_raw, scraped_date, posted_date_source
```

Medido ejecutando `de.enrich` sobre un frame con el contrato de 22 campos y
aplicando el `drop` + `rename` de cada notebook **sin dropear `_ingest_date`**
(local, Spark 4.2.0; tabla de §8.1). Consecuencias:

- El origen del `MERGE` debe tener **exactamente las columnas del destino de esa
  fuente** (`target.columns`) más las nuevas que añada el `transform` (§3
  D-B6). El módulo lo valida antes de escribir; **no hay lista global**.
- El test de contrato (**P13**) se ejecuta **una vez por fuente** y se
  parametriza por `target.columns`; como red de seguridad fija los tamaños
  medidos en un mapa por fuente (`{"indeed": 31, "linkedin": 33, "infojobs": 33,
  "multi_site": 33}`), no en una lista de columnas compartida.
- **T-09 no debe «cuadrar» los contratos**: está prohibido añadir `salary_min`
  o `salary_max` al `drop` de linkedin/infojobs/multi_site. Son columnas de
  negocio que hoy se guardan; el incremental no las toca.
- `posted_date_raw`, `scraped_date`, `posted_date_source`, `salary_quality`,
  `*_source`, `salary_*_annual`, `employment_type` y `role_category` **no son
  columnas internas**: los consume `Prepare_Gold.FACT_OFFERS_COLUMNS`. No se
  pueden confundir con `_key`/`_rn` al validar el contrato.

### 1.4 La función columnar de ciudad (una sola definición)

`Enrich_Job_Offers_Dataframes.py` gana esta función, que construye la **misma
expresión** que `enrich` usa hoy para `location_city`:

```python
def location_city_normalized_column(col: Column) -> Column:
    """Column expression for location_city; same rule as enrich(), no UDF."""
    city_v = F.trim(F.coalesce(col, F.lit("")))
    city_norm = F.translate(F.lower(city_v),
                            _LOCATION_ACCENT_SRC, _LOCATION_ACCENT_DST)
    city_clean = city_norm
    for pattern, repl in _LOCATION_CITY_CLEAN_RULES:
        city_clean = F.regexp_replace(city_clean, pattern, repl)
    canonic_city = F.create_map([
        F.lit(item)
        for kv in _LOCATION_CITY_CANONIC.items()
        for item in (kv[0], kv[1])
    ])
    return F.coalesce(canonic_city[city_norm], canonic_city[city_clean], city_v)
```

Requiere a nivel de módulo `from pyspark.sql import Column` y
`from pyspark.sql import functions as F`; el módulo hoy no los importa en la
cabecera y T-08 los añade.

- **`enrich` la reutiliza**: sustituye su `city_canon` inline por
  `city_canon = location_city_normalized_column(F.col(location_city_col))` y
  sigue usando `city_v`/`city_norm`/`city_clean` para región y país. Así queda
  **una sola definición** de la regla de ciudad.
- **Precondición de `enrich`:** solo normaliza la ciudad si
  `add_location_fill=True` **y** `normalize_location_city=True` (sus valores por
  defecto). `build_offers` los pasa **explícitos**; con `add_location_fill=False`
  la ciudad de la silver no se normaliza y la clave de bronce diverge (3/28
  diferencias medidas, P16b).
- **La clave de bronce usa la misma función**; no hay UDF, ni *pickle* del
  módulo, ni import en los *workers*. La expresión es nativa (`translate`,
  `regexp_replace`, `create_map`), sin intérprete Python por fila.
- El mapa `_LOCATION_CITY_CANONIC` se construye una vez en el plan (como hoy en
  `enrich`), no una vez por fila desde Python; `F.coalesce` conserva el
  comportamiento: conocida → canónica, desconocida → `city_v` (recortada),
  `NULL`/vacío → `''`.
- La función pura `location_city_normalized` se conserva como referencia
  documental y para la comprobación de P16b; la que se usa en la clave y en
  `enrich` es la columnar.
- El cambio de `Enrich_Job_Offers_Dataframes.py` requiere **aprobación previa
  del diff** (T-08) y la suite `databricks_notebooks` en verde, incluidos
  `test_enrich_pure.py` y los tests de integración.

---

## 2. Mapa por fuente y celdas que cambian

### 2.1 Columnas implicadas

`prepared` = el DataFrame de bronce **después del rename y de las derivaciones
baratas** (rename, flags `lit`, `posted_date` de LinkedIn/multi_site y
`keep_latest_by` temprano) y **antes del cast al contrato**. Es el frame que
maneja el módulo y el que leen `companies_indeed`/`companies_linkedin`.

| Fuente | Tabla bronce | Clave en bronce | En `prepared` | Temporal en bronce | Columnas de la clave | Cómo se calcula la clave sobre bronce |
|---|---|---|---|---|---|---|
| indeed | `bronze.indeed` | `job_key` | `job_id` | `scraped_at` (`timestamp`) | `title`, `company_name`, `location_city` | `title` tal cual; `company_name` = `initcap` + canónico; `location_city` = `de.location_city_normalized_column` (expresión columnar) |
| linkedin | `bronze.linkedin` | `job_id` | `job_id` | `scraped_at` (string ISO) | ídem | ídem |
| infojobs | `bronze.infojobs` | `id_oferta` | `job_id` | `fecha_scraped` (`timestamp` local) | ídem | ídem (este notebook no tiene la celda de códigos ISO, que no toca la ciudad) |
| multi_site | `bronze.multi_site` | `site` + `job_id` | `source_scraper` + `job_id` | `scraped_at` (string ISO) | ídem | ídem |

**Columnas extra que viajan en `prepared`** (no están en el contrato de 22 y por
eso el cast ya no puede ir antes de `companies_*`):

| Notebook | Extra | Quién la consume |
|---|---|---|
| indeed | `company_rating` (`double`) | `companies_indeed` (celda de rating, `company_review_count > 0`) |
| indeed | `company_review_count` (`int/long`) | `companies_indeed` (su dedup ordena por esta columna) |
| indeed | `_source_file` | nadie (el cast la descarta) |
| linkedin | `company_size` | `companies_linkedin` (limpieza por rangos) |
| linkedin | `company_industry`, `company_description` | `companies_linkedin` (se emiten tal cual) |
| linkedin | `_source_file` | nadie |
| infojobs | `employment_type` (`jornada`), `_source_file` | nadie (el cast las descarta; `employment_type` la reconstruye `enrich`) |
| multi_site | `_source_file` | nadie |

Comprobación columna a columna de los bloques `companies_*` contra `prepared`:

| Bloque | Columnas que usa | Origen en `prepared` | ¿Se pierde con la corrección 1? |
|---|---|---|---|
| `companies_indeed` | `company_name` | rename `company` → `company_name` | no |
| | `company_rating`, `company_review_count` | columnas propias de bronce | **no** (antes sí: las descartaba el cast) |
| | `company_rating_normalized`, `_key`, `_rn` | las crea el propio bloque | — |
| `companies_linkedin` | `company_name` | nombre nativo de bronce | no |
| | `company_size`, `company_industry`, `company_description` | columnas propias de bronce | **no** (antes sí) |
| | `company_size_clean` | la crea el propio bloque | — |
| `companies_infojobs` | `company_name` | lee la silver recién escrita (§2.3) | no |
| `companies_multi_site` | `company_name` | lee la silver recién escrita (§2.3) | no |

Notas:

- La **columna temporal no interviene** en el incremental de silver (bronce ya
  está deduplicado por captura). Solo sobrevive en `keep_latest_by`, que es
  no-op.
- En `prepared`, la clave de negocio de las cuatro es `job_id`; multi_site
  añade `source_scraper`. Sirve para el puente «víctima → grupo» de §5.2.
- `_ingest_date` (`date`) es la fecha del **lote de ingesta** (`current_date()`
  en `bronze_ingest`), no la del fichero ni la de la captura.
- El rename de indeed incluye `ingest_date` → `_ingest_date`; esa columna no
  existe en bronce (la pone `bronze_ingest` como `_ingest_date`), así que el
  rename es un no-op y no hay duplicado.

### 2.2 Celdas que cambian por notebook

Numeración = índice 0-based **final**. Se consolida el código en dos funciones
(`prepare_bronze`, `build_offers`) y las definiciones van a una **celda de
definiciones** (corrección 2 de la revisión 2), de modo que la lectura pueda ser
`df = prepare_bronze(spark.read.table(...))` y `df` siga siendo el frame que
leen `companies_indeed`/`companies_linkedin` (cero cambios en esas celdas).

Regla del diff de T-09: **se mueve código, no se reescribe**. La lista de
cambios de texto permitidos es **cerrada** (§2.5): todo lo demás debe ser
idéntico carácter a carácter.

Recuento (distingue «celda que sobrevive», «celda que desaparece como celda
independiente» y «celda nueva o reconstruida»):

| Notebook | Hoy | Sobreviven (solo índice o cambio cerrado) | Desaparecen como celda | Nuevas o reconstruidas | Final |
|---|---|---|---|---|---|
| silver_indeed | 26 | 11 | 15 | 4 | 15 |
| silver_linkedin | 24 | 10 | 14 | 4 | 14 |
| silver_infojobs | 19 | 4 | 15 | 5 | 9 |
| silver_multisite | 18 | 5 | 13 | 5 | 10 |

#### silver_indeed — 26 celdas hoy → 15 finales

| Final | Contenido | Origen | Cambio |
|---|---|---|---|
| 0 | `import os`, `import json`, `import Enrich_Job_Offers_Dataframes as de`, `import Silver_Incremental as inc`; widgets `catalog`/`bronze_schema`/`silver_schema`, `lookback_days` (`"7"`), `from_date` (`""`), `to_date` (`""`), `force_full` (`"false"`), `repeat_window` (`"false"`); `_cfg`, `bronze_table`, `silver_table` | 0 | + `json`, + `inc`, + 5 widgets |
| 1 | md «Seguimos con las ofertas de trabajo» | 6 | solo índice |
| 2 | md enrich | 9 | solo índice |
| 3 | md «Normalize Company_Name…» | 11 | solo índice |
| 4 | md dedup por título/empresa/ciudad | 13 | solo índice |
| 5 | **definiciones**: `import pyspark.sql.types as T`; `df_structure_Schema`; `normalize_company_name`; `apply_company_normalization`; `COMPANY_ALIAS_PATTERNS`; `company_name_canonical`; `apply_company_canonical`; `_REGION_CODE_NAMES`, `_COUNTRY_CODE_NAMES`, `normalize_location_codes`; `prepare_bronze`; `build_offers`; `normalize_company` | 3 + 4 + 12 (definiciones) + 17 (definiciones) y nuevas | se mueven tal cual; de las **dos** `add_semantic_key` de indeed **se elimina solo la de ofertas** (vieja celda 14); la de empresas (vieja celda 23, final 11) se conserva |
| 6 | `df = prepare_bronze(spark.read.table(bronze_table("indeed")))` | 1 + 5 + 7 (cuerpos) | rename y flags pasan a `prepare_bronze` |
| 7 | `summary = inc.run(spark, silver_table("offers_indeed"), df, source="indeed", transform=build_offers, normalize_company=normalize_company, normalize_city_column=de.location_city_normalized_column, lookback_days=…, from_date=…, to_date=…, force_full=…, repeat_window=… )` | nueva (sustituye 19) | escritura por MERGE; la repetición de la ventana la resuelve `run` con `repeat_window` |
| 8 | md `Dim_Companies` | 20 | solo índice |
| 9 | `companies_df = df.select(["company_name","company_rating","company_review_count"])`, normalización y canónico | 21 | **sin cambio** (`df` = `prepared`; las definiciones están en 5) |
| 10 | `company_rating_normalized` | 22 | sin cambio |
| 11 | dedup de empresas por `company_review_count` (**con su `add_semantic_key`**) | 23 | sin cambio |
| 12 | `drop`/rename de `company_rating` | 24 | sin cambio |
| 13 | write `companies_indeed` | 25 | sin cambio (queda **después** de 7) |
| 14 | `dbutils.notebook.exit(json.dumps(summary, ...))` | nueva | último: `NotebookExit` corta lo que venga detrás |

Recuento: **sobreviven 11** (0, 6, 9, 11, 13, 20, 21, 22, 23, 24, 25);
**desaparecen 15**: movidas a la 5 → 3, 4, 12, 17; movidas a la 6 → 1, 5, 7;
disueltas en `build_offers` → 8, 10, 14, 15, 16, 18; eliminada por diagnóstico →
2 (`display(df)`); sustituida por la llamada a `run` → 19. Se **crean o
reconstruyen 4** (5, 6, 7, 14). 11 + 4 = 15.

En la celda 15 vieja, la lista del `drop` pasa de
`['salary_min','salary_max','scraped_at','_ingest_date','salary_period','work_mode']`
a `['salary_min','salary_max','scraped_at','salary_period','work_mode']`
(único cambio de texto de negocio, además de la línea del `_key`).

`build_offers` de indeed, en orden, sin cambiar una expresión: cast al contrato
→ `de.enrich(add_location_fill=True, normalize_location_city=True)` →
`apply_company_normalization` → `apply_company_canonical` → `_key` con
`inc.key_expression()` + `row_number` → `drop` → rename `*_norm` →
`normalize_location_codes` → `return`. Los cuatro notebooks usan las mismas
banderas explícitas en `de.enrich`.

#### silver_linkedin — 24 celdas hoy → 14 finales

| Final | Contenido | Origen | Cambio |
|---|---|---|---|
| 0 | config + `json`, `inc`, `dedup`; + 5 widgets | 0 | + `json`, + `inc`, + widgets |
| 1 | md `## Job Offers` | 3 | índice |
| 2 | md enrich | 10 | índice |
| 3 | md «Normalize Company_Name…» | 12 | índice |
| 4 | md dedup | 14 | índice |
| 5 | **definiciones**: `offers_df_structure_Schema`; `normalize_company_name`; `apply_company_normalization`; patrones + `company_name_canonical` + `apply_company_canonical`; `posted_date_from_relative` (cuerpo de la celda 6, con `return`); `prepare_bronze`; `build_offers`; `normalize_company` | 4, 13, 6 (definición), nuevas | `add_semantic_key` (única, de ofertas) se elimina |
| 6 | `df = prepare_bronze(spark.read.table(bronze_table("linkedin")))` | 1 + 5 + 6 (llamada) + 7 (cuerpos) | `keep_latest_by`, rename, `posted_date` y flags dentro de `prepare_bronze` |
| 7 | `summary = inc.run(...)` con `normalize_city_column=de.location_city_normalized_column` y `repeat_window` | nueva (sustituye 18) | |
| 8 | md `Dim_Companies` | 19 | índice |
| 9 | `companies_df = df.select(["company_name","company_size","company_industry","company_description"])` + normalización + canónico | 20 | **sin cambio** (`df` = `prepared`; conserva las 3 columnas de empresa) |
| 10 | limpieza de `company_size` por rangos | 21 | sin cambio |
| 11 | `dropDuplicates(["company_name"])` + rename de `company_size` | 22 | sin cambio |
| 12 | write `companies_linkedin` | 23 | después de 7 |
| 13 | `dbutils.notebook.exit(...)` | nueva | |

Recuento: **sobreviven 10** (0, 3, 10, 12, 14, 19, 20, 21, 22, 23);
**desaparecen 14**: a la 5 → 4, 13, 6 (definición); a la 6 → 1, 5, 7; disueltas
en `build_offers` → 8, 11, 15, 16, 17; eliminadas por diagnóstico → 2
(`display(df)`), 9 (`df_final.describe()`); sustituida por `run` → 18. Se
**crean 4** (5, 6, 7, 13). 10 + 4 = 14.

Mismo cambio de una línea en el `drop`
(`['scraped_at','salary_period','work_mode']`).

#### silver_infojobs — 19 celdas hoy → 9 finales

| Final | Contenido | Origen | Cambio |
|---|---|---|---|
| 0 | config + `json`, `inc`; + 5 widgets | 0 | + imports y widgets |
| 1 | md enrich | 9 | índice |
| 2 | md «Normalize Company_Name…» | 11 | índice |
| 3 | md dedup | 13 | índice |
| 4 | **definiciones**: `df_structure_Schema`; `normalize_company_name`; `apply_company_normalization`; patrones + `company_name_canonical` + `apply_company_canonical` (variante columnar); `prepare_bronze`; `build_offers`; `normalize_company` | 2, 3, 12 (definiciones), nuevas | `add_semantic_key` (única) se elimina |
| 5 | `df = prepare_bronze(spark.read.table(bronze_table("infojobs")))` | 1 + 4 + 6 (cuerpos) | rename y flags dentro |
| 6 | `summary = inc.run(...)` | nueva (sustituye 18, primera mitad) | |
| 7 | `companies_df = spark.read.table(silver_table("offers_infojobs")).select("company_name").dropDuplicates()` + write `companies_infojobs` | 15 + 18 (segunda mitad) | **sin normalización** (§2.3); después de 6 |
| 8 | `dbutils.notebook.exit(...)` | nueva | |

Recuento: **sobreviven 4** (0, 9, 11, 13); **desaparecen 15**: a la 4 → 2, 3, 12;
a la 5 → 1, 4, 6; disueltas en `build_offers` → 7, 10, 14, 16, 17; eliminadas por
diagnóstico → 5 (`display(df)`), 8 (`display(df_final)`); la 15 se reconstruye
en la 7; la 18 se parte entre la 6 y la 7. Se **crean 5** (4, 5, 6, 7, 8).
4 + 5 = 9.

En la celda 16 vieja, `_ingest_date` sale del `drop`
(`['scraped_at','salary_period','work_mode']`).

#### silver_multisite — 18 celdas hoy → 10 finales

| Final | Contenido | Origen | Cambio |
|---|---|---|---|
| 0 | config + `json`, `inc`, `dedup`; + 5 widgets | 0 | + imports y widgets |
| 1 | md enrich | 7 | índice |
| 2 | md «Normalize Company_Name…» | 9 | índice |
| 3 | md dedup | 11 | índice |
| 4 | md `# Dim_Companies` | 13 | índice |
| 5 | **definiciones**: `df_structure_Schema`; `normalize_company_name`; `apply_company_normalization`; patrones + `company_name_canonical` + `apply_company_canonical`; `posted_date_from_relative` (cuerpo de la celda 5, con `return`); `prepare_bronze`; `build_offers`; `normalize_company` | 2, 3, 10 (definiciones), 5 (definición), nuevas | `add_semantic_key` (única) se elimina |
| 6 | `df = prepare_bronze(spark.read.table(bronze_table("multi_site")))` | 1 + 4 + 5 (llamada) + 6 (cuerpos) | `keep_latest_by(["site","job_id"])` **antes** del rename `site`→`source_scraper` |
| 7 | `summary = inc.run(...)` | nueva (sustituye 17, primera mitad) | |
| 8 | `companies_df = spark.read.table(silver_table("offers_multi_site")).select("company_name").dropDuplicates()` + write `companies_multi_site` | 14 + 17 (segunda mitad) | **sin normalización** (§2.3); después de 7 |
| 9 | `dbutils.notebook.exit(...)` | nueva | |

Recuento: **sobreviven 5** (0, 7, 9, 11, 13); **desaparecen 13**: a la 5 → 2, 3,
5, 10; a la 6 → 1, 4; disueltas en `build_offers` → 6, 8, 12, 15, 16; la 14 se
reconstruye en la 8; la 17 se parte entre la 7 y la 8. Se **crean 5** (5, 6, 7,
8, 9). 5 + 5 = 10.

Mismo cambio de una línea en el `drop`
(`['scraped_at','salary_period','work_mode']`).

La reestructuración no cambia ni una línea de la lógica de negocio: es
indentación, un `return` y el traslado de las definiciones. `prepare_bronze` es
barato (rename, `lit`, dedup temprana, `posted_date`) y se ejecuta **una vez
sobre el bronce completo**; `build_offers` es lo caro (`enrich` con regex sobre
descripciones) y se ejecuta **solo sobre el conjunto afectado** (o sobre todo, en
reconstrucción).

### 2.3 Procedencia de `companies_infojobs` y `companies_multi_site`

Hoy esos dos bloques leen `df_completed` (post-dedup), toman `company_name` y
**vuelven a aplicar** `apply_company_normalization` + `apply_company_canonical`
antes de `dropDuplicates()`. Ese frame ya no existirá fuera del camino de
ofertas. La sustitución es:

```python
companies_df = (spark.read.table(silver_table("offers_infojobs"))
                .select("company_name").dropDuplicates())
```

**sin** `apply_company_normalization` ni `apply_company_canonical`: el valor
guardado ya es canónico y reaplicarlos es un no-op (se demuestra en T-08, P16).

Argumento de equivalencia (medido en local, §8.1):

1. El valor guardado es `stored = canonical(initcap(x))` (o `NULL` en
   multi_site, que el canónico con join conserva).
2. `initcap` es idempotente sobre su propia salida: `initcap("McKinsey &
   Company") = "Mckinsey & Company"`, `initcap("IKEA") = "Ikea"`,
   `initcap("T-Mobile") = "T-mobile"`, y volver a aplicarlo no cambia nada.
3. **Todo literal canónico casa con su propio patrón** (`(?i)`): comprobado con
   los 128 patrones; por tanto `canonical(initcap("McKinsey")) =
   canonical("Mckinsey") = "McKinsey"` y `canonical(initcap("IKEA")) = "IKEA"`.
4. Si el valor no es canónico, ya es la salida de `initcap` y ningún patrón
   casó en la primera pasada; volver a evaluar da lo mismo (determinista).
5. Conclusión: `canonical(initcap(stored)) = stored` para todo valor guardado →
   **el conjunto de nombres no cambia** y la dimensión es idéntica. La copia del
   notebook y la del módulo `Enrich_Job_Offers_Dataframes` son **idénticas** (128
   patrones, comparadas en local).
6. El conjunto de filas también es idéntico: la silver guarda una fila por grupo
   (la ganadora) y `select("company_name").dropDuplicates()` devuelve los
   nombres distintos, que es exactamente lo que devuelve hoy
   `df_completed.select("company_name").dropDuplicates()`.
7. Si el run no escribe (ventana vacía), la lectura devuelve la tabla anterior,
   con el mismo contenido que hoy. Si es la primera ejecución, el modo completo
   ya dejó la tabla escrita antes de este bloque.

**Consecuencia operativa (nueva).** Al dejar de renormalizar, la dimensión
hereda lo que haya en silver: **si se cambia la lista de 128 patrones canónicos
(se añade o se retira un patrón), hay que relanzar con `force_full`** las
silvers afectadas para que los nombres ya guardados se recalculen; el
incremental no reescribe grupos no afectados y los nombres antiguos
sobrevivirían. Queda en el runbook y en §9.

`companies_indeed` y `companies_linkedin` **no cambian**: siguen leyendo
`prepared` (antes de `enrich`/dedup), que es el frame que conserva sus columnas
extra.

### 2.4 Orden de ejecución exacto por notebook

Los mismos pasos en las cuatro, con el `source` y las celdas de empresa propios:

1. **Imports y widgets** (celda 0): `os`, `json`, `Enrich_Job_Offers_Dataframes`,
   `Silver_Incremental` (`Deduplicate_Job_Offers` en LinkedIn/multi_site);
   `dbutils.widgets.text` de `catalog`, `bronze_schema`, `silver_schema`,
   `lookback_days`, `from_date`, `to_date`, `force_full` y `repeat_window`. Es
   la única celda con efectos al importar (registro de widgets) y la que
   resuelve `_cfg`.
2. **Definiciones** (celda de definiciones): esquema, normalización de empresa,
   `posted_date_from_relative` (solo LinkedIn/multi_site), `prepare_bronze`,
   `build_offers`, `normalize_company`. No ejecuta nada: son `def` y literales.
3. **Lectura y preparación**: `df = prepare_bronze(spark.read.table(...))`.
   Única lectura de bronce del notebook; el frame queda **sin materializar**
   (lazy). Devuelve el frame **antes** del cast/select (lo necesitan
   `companies_indeed` y `companies_linkedin`).
4. **`inc.run(...)`**: ventana, conjunto afectado, recálculo y **un solo
   commit** (MERGE o `overwrite`). Devuelve el resumen. El notebook le pasa
   `repeat_window` tal cual; si el widget es verdadero (solo T-10), el módulo
   ejecuta dos veces la implementación interna (`_run_once`) con los mismos
   argumentos y devuelve el sobre `{"repeat_window": true, "runs": [s1, s2]}`
   (§5.5). La celda no tiene condicionales.
5. **Escritura de `companies_*`**: después de las ofertas. `indeed`/`linkedin`
   leen `df`; `infojobs`/`multi_site` leen `silver.offers_<fuente>` recién
   escrita (o la anterior si el run no escribió).
6. **`dbutils.notebook.exit(json.dumps(...))`**: última celda, siempre.

Por qué queda fuera de las funciones:

- Los **widgets** son efectos de sesión y se registran al principio; una función
  no debe depender de `dbutils`.
- La **lectura** es el único punto de entrada de datos: así el módulo recibe un
  DataFrame y no conoce catálogo, esquemas ni widgets (testeable en local).
- La **llamada a `run`** concentra el commit; el notebook no debe tener otra
  escritura de ofertas.
- Las **definiciones** (corrección 2) tienen que ser visibles a la vez para
  `inc.run` (como callables) y para los bloques `companies_*`. Anidadas dentro de
  `build_offers` no existirían fuera.
- El **exit** interrumpe el notebook con una excepción: si no es la última celda,
  se come las escrituras de `companies_*`.

`source` **no es un widget**: lo pasa el notebook como constante
(`"indeed"`, `"linkedin"`, `"infojobs"`, `"multi_site"`) porque cada notebook
ejecuta una sola fuente (RF-9). `repeat_window` es el **widget de repetición**
que menciona `tasks.md` (T-10): por defecto `"false"`, así que la producción no
cambia; solo lo activa el run B.

### 2.5 Qué se puede y qué no se puede mover

- Se mueven: cuerpos de celda a funciones, definiciones a la celda de
  definiciones, markdown de sitio.
- **No** se mueve ni se cambia: el mapa de rename, los `lit`/flags, la
  derivación de `posted_date`, el `select` del contrato, `de.enrich`,
  `apply_company_*`, la ventana de dedup (`orderBy` incluido), la lista del
  `drop` (salvo `_ingest_date`), los renames `*_norm`, `normalize_location_codes`
  (solo indeed) y los bloques `companies_*` **salvo** la procedencia de
  infojobs/multi_site (§2.3).

El único cambio de `Enrich_Job_Offers_Dataframes.py` es la extracción de
`location_city_normalized_column` y su reutilización por `enrich` (T-08, con
aprobación previa del diff); la llamada del notebook a `de.enrich` no cambia.

**Lista cerrada de cambios de texto permitidos en T-09** (nada más):

1. `def`, `return` e indentación al trasladar cuerpos a funciones.
2. La línea de `_key` de ofertas pasa a `inc.key_expression()` (y **solo** esa;
   la `add_semantic_key` de empresas de indeed se queda).
3. Quitar `_ingest_date` de la lista del `drop` de ofertas.
4. Quitar de la celda de definiciones las **llamadas sueltas** de normalización
   (`df_completed=apply_company_normalization(...)`,
   `df_completed=apply_company_canonical(...)`): pasan a `build_offers` y a
   `normalize_company`.
5. Renombrar los parámetros de las funciones (`df`, `df_completed`, `df_final`
   → `df`) y ajustar las referencias; `df_final` puede quedar como alias local
   del cast dentro de `build_offers`.
6. Envolver el cuerpo de `posted_date_from_relative` (LinkedIn celda 6,
   multi_site celda 5) en una función con `return df`; las expresiones internas
   no se tocan.
7. La procedencia de `companies_infojobs`/`companies_multi_site` (§2.3).
8. La celda 0 (imports, widgets, `repeat_window`) y la celda de escritura de
   ofertas (llamada a `inc.run` con `normalize_city_column` y
   `repeat_window`), más la nueva celda de `exit`.
9. El orden de las celdas.
10. La llamada a `de.enrich` fija `add_location_fill=True` y
    `normalize_location_city=True` (sus defaults ya son esos valores; se
    explicitan para que la precondición de la clave quede en el código).

---

## 3. Decisiones cerradas

### D-B1 — Alcance: solo las cuatro silvers de ofertas

**Decisión.** `offers_indeed`, `offers_linkedin`, `offers_infojobs` y
`offers_multi_site` escriben por MERGE. `companies_*` y toda la capa oro
(`gold_build`, `Prepare_Gold.write_gold`, los dos exports) siguen en
`overwrite` completo.

**Justificación.** El dedup de negocio de oro (`title_norm` + `company_norm` +
`salary_filled`) y el reparto de salario son ventanas globales: un cambio en una
oferta puede cambiar la ganadora de otro grupo, así que un incremental por
partes cambiaría el resultado. A 26k filas, reconstruirlo cuesta segundos.

**Alternativa descartada.** Incrementalizar también oro o las dimensiones
(`companies_*` tiene cientos de nombres): más superficie, más riesgo de
divergencia y ningún ahorro medible.

### D-B2 — Unidad de incremental: el grupo semántico `_key`

**Decisión.** Se recalcula el **grupo semántico completo**. La clave es la misma
expresión que ya usa la dedup:

```text
_key = lower(trim(regexp_replace(
    concat_ws('|', title, company_name, location_city), '\s+', ' ')))
```

La expresión vive **una sola vez** en `Silver_Incremental.key_from_columns()`
(Column) y `key_sql(alias)` (texto SQL, para el `ON` del MERGE); `key_expression()`
es el envoltorio que la aplica a las tres columnas estándar. El notebook la
consume desde ahí y **desaparece `add_semantic_key` de ofertas de los cuatro
notebooks** (el de empresas de indeed no se toca: es otro `_key` sobre
`company_name` sola).

**Clave exacta sobre bronce.** Para seleccionar candidatos, la misma expresión
se evalúa sobre `prepared` **con la misma normalización que aplicará la silver**:

```text
key_bronze(r) = key_from_columns(
    r.title,
    normalize_company(r.company_name),
    location_city_normalized_column(r.location_city))
```

- `normalize_company` es el callable del notebook:
  `apply_company_canonical(apply_company_normalization(df))` (cada notebook
  aporta su variante de canónico).
- `location_city_normalized_column` es la **función columnar** del módulo
  `Enrich_Job_Offers_Dataframes` (la misma que usa `enrich`): `trim`/`coalesce`,
  `translate(lower(...))`, las reglas de limpieza y el mapa canónico. No hay
  UDF ni *pickle* del módulo en los *workers*.
- No se calcula la clave sobre bronce crudo: no coincidiría con la guardada.

**Justificación.** El resultado de la silver es «la fila ganadora de cada
grupo». Un MERGE por `job_id` cambiaría el resultado: si entra una captura de
una oferta que no es la ganadora del grupo, el ganador puede seguir siendo otra
fila, y el MERGE por `job_id` la habría sobrescrito o duplicado. La clave exacta
permite seleccionar **todas** las filas del grupo, no un superconjunto ni un
subconjunto.

**Colisiones de clave.** `concat_ws` omite los `NULL` y una componente puede
contener el separador `|`; por eso dos tripletas distintas pueden producir la
misma `_key`. Eso **no rompe nada**: la dedup de silver las considera el mismo
grupo, y la selección por igualdad de clave recupera todas sus filas. La
revisión 3 intentaba evitar estas colisiones porque seleccionaba por un par
(título, empresa) que no identificaba el grupo; con la clave exacta, la colisión
es exactamente la definición del grupo.

**Alternativa descartada.** MERGE por `job_id` (más barato de razonar, pero
cambia el resultado y rompe RF-7) y selección por superconjunto de pares
(obligaba a una guarda de nulos y a renunciar al incremental; se retira en §3).

### D-B3 — Conjunto afectado: `keys_new` ∪ `keys_old` (con migración de grupo)

**Decisión.** Dos fuentes de claves, unidas y deduplicadas:

- `keys_new`: claves exactas de bronce de las filas con `_ingest_date` en la
  ventana.
- `keys_old`: claves **de la silver** de las filas cuyo `job_id` está entre los
  `job_id` cambiados (más `source_scraper` en multi_site). Se obtienen con un
  **semi-join** (`left_semi`) contra las claves de negocio de la ventana, no con
  una lista recogida al driver: la ventana puede traer miles de claves.

`candidates` = filas de `prepared` (ya con empresa normalizada y clave exacta)
cuya clave está en `affected`, por **igualdad de clave** (semi-join exacto).

`keys_old` es la **migración de grupo**: si una oferta que era la ganadora de su
grupo cambia de ciudad o de título, su grupo antiguo se recalcula y la fila
huérfana se expulsa (§5.4). No hace falta para las filas perdedoras: una fila
que no está en silver no puede ser la ganadora de su grupo, así que su salida de
un grupo no cambia el ganador de ese grupo.

**Justificación.** Cubre los dos sentidos del cambio sin estado extra: grupos
que ganan filas y grupos que las pierden.

**Alternativa descartada.** Calcular el conjunto afectado solo por `keys_new`
(deja huérfanos al migrar de grupo) o reconstruir siempre (es el status quo).

**Resuelto el hallazgo H1 de la revisión 3.** Antes la clave se calculaba sobre
valores transformados y no se podía reproducir sobre bronce; por eso se
seleccionaba con un par (título, empresa). Ahora la normalización de empresa y
ciudad es la misma a los dos lados, así que la clave de bronce **es** la clave
guardada. No hay proyección más gruesa ni recorte posterior: `candidates` ya es
el conjunto exacto de filas de los grupos afectados.

### D-B4 — Watermark, ventana y `force_full`

**Decisión.**

```text
watermark = (select max(_ingest_date) from silver.offers_<fuente>)
from_date = from_date (widget) or (watermark - lookback_days)   # lookback_days = 7
to_date   = to_date (widget) or null                            # sin tope superior
ventana   = [_ingest_date >= from_date]  # solo si from_date no es null
            [and _ingest_date <= to_date]  # solo si to_date no es null
```

- `lookback_days` por defecto **7** (una cadencia semanal) para tolerar
  reprocesos y huecos. `int | str`; `""` → 7; negativo o no entero →
  `ValueError`.
- **`mode = "full"` solo por tres motivos**:
  1. `force_full=true`;
  2. la tabla destino **no existe** (`table_missing`);
  3. la tabla existe pero **no tiene `_ingest_date`** (`missing_ingest_date`):
     es la primera ejecución con el código nuevo y la migración.
- **Tabla vacía con `_ingest_date`:** `max(_ingest_date)` es nulo y la ventana
  queda **abierta** (`from_date = null`): `changed_since` **no filtra** por
  `_ingest_date` (§4), el incremental lee todo el bronce y el MERGE inserta todos
  los grupos. **No es un `full`** (no hace falta `overwrite`) y el contenido
  final es el mismo. Se retira el «watermark nulo» como motivo de `full` para
  que `mode` tenga exactamente los tres motivos de RF-7/RF-8.
- `from_date` sin `to_date` = ventana abierta hacia delante. `to_date` sin
  `from_date` y sin `force_full` → `ValueError`; `from_date > to_date` →
  `ValueError`. Formatos: ISO `YYYY-MM-DD` (`datetime.date.fromisoformat`);
  cualquier otra cosa → `ValueError` con mensaje en español.
- **Los widgets entregan texto.** `force_full` **no** puede usarse como
  booleano de Python (`bool("false")` es `True` y el incremental no correría
  nunca): el módulo lo convierte con `as_bool` (§4), que acepta `bool`
  (`True`/`False` tal cual) y texto: `""`, `"false"`, `"0"` → `False`;
  `"true"`, `"1"` → `True`; cualquier otro valor → `ValueError`. `None` se
  trata como `""` (→ `False`). `run` **delega** la conversión de `force_full` en
  `window_bounds`; no la repite.
- `from_date`/`to_date` se aceptan como `str | date | None`; `""` → `None`.

**Justificación.** El watermark se deriva del propio dato (no hay tabla de
control ni CDF, decisión de la spec) y el lookback absorbe la ventana perdida.

**Alternativa descartada.** Tabla de control de watermarks (estado extra que
puede quedar desincronizado) y CDF (historial de cambios, fuera de alcance).

**Matiz medido.** `_ingest_date` es la fecha del **lote**, así que un fichero
tardío entra con la fecha del run en que se ingiere y **cae dentro de la
ventana**. El riesgo real de «fila con fecha anterior al watermark» solo
aparece al reescribir bronce con fechas antiguas (una one-off, un `RESTORE`, un
backfill manual); eso se cubre con `from_date`/`to_date` o `force_full` y queda
documentado en el runbook (§6, §9).

**Matiz del watermark.** Se calcula sobre las filas **guardadas** (la ganadora
de cada grupo). Si en un run todas las capturas nuevas pierden la dedup de su
grupo, `max(_ingest_date)` no avanza y el run siguiente repite la misma
ventana: es inocuo (mismo recálculo, mismo contenido), no se pierde nada.

**Filas que desaparecen del bronce: no se detectan.** El incremental solo ve
filas **presentes** en la ventana; si una fila se borra, se reescribe o cambia
su clave en bronce (`RESTORE`, rewrite, cambio de `job_id`, cambio de `site` en
multi_site), ningún grupo se recalcula por ella y silver conserva el ganador
antiguo. Ese caso **exige `force_full`** (§6, §9 y runbook).

### D-B5 — Columnas de control y contrato con oro/exports

**Decisión.** Silver conserva **solo `_ingest_date`** (`date`). No se guarda
`_source_file` (hoy no llega) ni `_key` (se calcula al vuelo).
El resto de columnas no cambia: el contrato de cada silver es el actual **más
`_ingest_date`** (que hoy se dropea), es decir **31** en indeed y **33** en el
resto (§1.3). **No hay contrato global**: cada tabla tiene el suyo.

**Justificación.** `_ingest_date` es lo que permite el watermark y la selección
de la ventana. Oro no la usa (selecciona columnas explícitas) y los exports
también; guardar `_key` añadiría una segunda columna de control que la decisión
de la persona no contempla.

**Alternativa descartada.** Materializar `_key` en silver para abaratar el `ON`
del MERGE y el semi-join del conjunto afectado. Se descarta por contrato; el
coste evitado es irrelevante a 26k filas y el `ON` con expresión no impide podar
ficheros de forma significativa a este tamaño.

**Verificación del contrato** (§7, P13).

### D-B6 — Escritura por MERGE

**Decisión.** Un solo `MERGE ... WITH SCHEMA EVOLUTION` por fuente y por run,
con **una fila por grupo en el origen** y el `ON` por la expresión de `_key`
calculada a los dos lados. El SQL completo, en este orden exacto:

```sql
MERGE WITH SCHEMA EVOLUTION INTO <catalog>.silver.offers_<fuente> AS t
USING <view> AS s
ON  lower(trim(regexp_replace(concat_ws('|', t.title, t.company_name, t.location_city), '\\s+', ' ')))
  = lower(trim(regexp_replace(concat_ws('|', s.title, s.company_name, s.location_city), '\\s+', ' ')))
WHEN MATCHED AND s._ingest_date IS NULL THEN DELETE
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED AND s._ingest_date IS NOT NULL THEN INSERT *
```

Comprobado contra la documentación de Delta
(`docs.databricks.com/.../delta/merge` y `.../tables/update-schema`, consultadas
el 2026-10-24) y contra el runtime real del job (`17.3.x-scala2.13`,
`resources/elt.job.yml`):

- **Orden de ramas.** «Si hay varias cláusulas `whenMatched`, se evalúan en el
  orden en que se especifican; todas menos la última deben llevar condición».
  Por eso la de `DELETE` va **primero y con condición** (`s._ingest_date IS
  NULL`) y la de `UPDATE` va última y sin condición. Además, «puede haber como
  máximo un `update` y un `delete` por `whenMatched`», que es el caso.
- **`UPDATE SET *` / `INSERT *`.** Sin evolución de esquema exigen que el
  origen tenga las mismas columnas que el destino (error de análisis). **Con
  `WITH SCHEMA EVOLUTION` el contrato es distinto**: las columnas que están en el
  origen y no en el destino **se añaden** al final de la tabla, y las que están
  en el destino y no en el origen se dejan intactas en `UPDATE SET *` y a `NULL`
  en `INSERT *`. Por tanto el módulo **proyecta el origen a `target.columns`
  (en el orden del destino) y arrastra además cualquier columna nueva del
  transform**, al final y en el orden del transform. Si el transform deja de
  producir una columna del destino → `ValueError` antes de escribir.
- **`MERGE WITH SCHEMA EVOLUTION`** está documentado desde **15.4 LTS** para
  `MERGE`; el job corre en 17.3, así que sobra margen y coincide con lo que ya
  hace el bronce (bloque A).
- **Origen = vista temporal con nombre único.** El `USING` admite tabla, vista o
  subconsulta; `MERGE` sobre una **vista** solo está restringido cuando la vista
  es el **destino**. El nombre se genera **por fuente y ejecución**
  (`__silver_batch_<source>_<8 hex>`) y se elimina en un `finally`
  (`dropTempView`), dentro de `merge_into_silver`. Con el nombre fijo anterior,
  dos ejecuciones solapadas en el mismo contexto podían pisarse. **El
  aislamiento de vistas temporales entre tareas del mismo clúster no está
  verificado**: `[NO VERIFICADO]` hasta T-10 (la evidencia será que las cuatro
  tareas hacen MERGE sin error y con sus conteos). El nombre único evita
  depender de ese aislamiento.
- **Unicidad del origen.** «Solo una fila del origen puede casar con una fila
  del destino»; desde DBR 16.0 se evalúan también las condiciones de
  `WHEN MATCHED` para detectar duplicados. Nuestro origen es único por grupo por
  construcción (`affected` con `distinct`, recálculo deduplicado y marcadores
  solo para claves ausentes del recálculo); el módulo lo **valida** antes del
  MERGE (P12, P13).
- **Marcador de grupo vacío.** No hay columna extra: el discriminador es
  `_ingest_date IS NULL`, que en un origen real nunca ocurre (el módulo lo
  comprueba y aborta). La rama `DELETE` solo se ejecuta contra claves que existen
  en el destino (los marcadores salen de `target`), así que no puede borrar de
  más ni insertar nada (el `INSERT` exige `IS NOT NULL`).
- **Limitación real: el `ON` con expresión no poda ficheros.** Delta no puede
  usar data skipping sobre `_key` (no es columna física del destino), así que
  cada MERGE escanea la tabla destino completa (26k filas, pocos ficheros) y
  reescribe solo los ficheros tocados. Aceptado a este tamaño; la alternativa
  (materializar `_key`) reabriría D-B5 y es spec 010.
- **Limitación real: columna nueva.** La evolución la añade al final y **solo
  rellena las filas reescritas**; las filas de grupos no afectados quedan a
  `NULL`, lo que **no** es equivalente a una reconstrucción completa. Por eso
  añadir un campo al contrato es una tarea explícita que se ejecuta con
  `force_full` (documentado en el runbook y en el resumen como `new_columns`).
  El test P19 fija el comportamiento. Lo mismo vale para un cambio en los 128
  patrones canónicos (§2.3).
- **El `overwrite` con `overwriteSchema` sigue usándose en la reconstrucción
  completa** (modo `full`): es lo que crea `_ingest_date` en la migración y lo
  que deja el esquema actualizado de golpe. El MERGE nunca lo usa.
- **Sin cambios en la ventana no se escribe**: si la ventana no trae filas, o
  no hay grupos afectados, el notebook termina sin commit.
- **Reproceso**: widgets `from_date`, `to_date`, `lookback_days`, `force_full`
  y `repeat_window` (el `source` es el propio notebook). Es idempotente porque
  el grupo se recalcula siempre desde bronce completo (§5.3), no desde la
  ventana.

**Alternativa descartada.** Reconstrucción completa en cada run (status quo) y
`DELETE` de los grupos afectados seguido de `INSERT` en dos transacciones
(dos commits y un estado intermedio inconsistente si falla el segundo).

### Retirado en la revisión 4

Se eliminan del diseño, y no deben reaparecer en T-08/T-09:

- La **selección por superconjunto de pares** (título, empresa) y las columnas
  de trabajo que la acompañaban.
- La **guarda de claves inseguras** y sus motivos y cualquier caída a `full`
  por datos.
- La **excepción a RF-7**: no hay ningún caso de datos que degrade el run a
  reconstrucción. El incremental corre siempre salvo `force_full`, tabla
  inexistente o tabla sin `_ingest_date`.
- El motivo de **watermark nulo**: la tabla vacía usa ventana abierta (§3
  D-B4).

Lo que **sí** se conserva de la revisión 3: `_ingest_date` como única columna
de control, MERGE con evolución de esquema, vista temporal de nombre único,
contrato por fuente (31/33), cadenas de transformación sin unificar, run A/B y
la procedencia de `companies_*`.

---

## 4. Contrato del módulo (entrada de T-08)

`databricks_notebooks/Silver_Incremental.py`, sin dependencias nuevas, con la
misma forma que `Upsert_Job_Offers.py` (funciones puras + SQL generado como
texto). El módulo **no conoce** la lógica de negocio: recibe el transform, el
normalizador de empresa y la expresión columnar de ciudad como callables.

```python
@dataclass(frozen=True)
class SourceSpec:
    silver_keys: tuple[str, ...]      # ("job_id",) | ("source_scraper", "job_id")

SOURCE_SPECS = {
    "indeed":     SourceSpec(("job_id",)),
    "linkedin":   SourceSpec(("job_id",)),
    "infojobs":   SourceSpec(("job_id",)),
    "multi_site": SourceSpec(("source_scraper", "job_id")),
}

@dataclass(frozen=True)
class Window:
    mode: str                 # "full" | "incremental"
    reason: str | None        # force_full | table_missing | missing_ingest_date |
                              # None (incremental)
    watermark: date | None
    from_date: date | None
    to_date: date | None
    lookback_days: int

KEY_COLUMNS = ("title", "company_name", "location_city")
KEY = "_key"
DEFAULT_LOOKBACK_DAYS = 7

def key_from_columns(title: Column, company: Column, city: Column) -> Column
def key_expression(alias: str | None = None) -> Column
def key_sql(alias: str) -> str                  # misma expresión, en texto SQL
def as_date(value, *, name: str) -> date | None # widgets: "" -> None, ISO o ValueError
def as_bool(value) -> bool                      # widgets: ver tabla de abajo
def window_bounds(spark, target_table, *, lookback_days, from_date, to_date,
                  force_full) -> Window         # parsea force_full con as_bool
def changed_since(prepared, window) -> DataFrame  # abierta si from_date is None
def select_candidates(keyed, affected) -> DataFrame
def recompute_groups(candidates, *, transform) -> DataFrame
def require_no_null_ingest_date(recompute) -> None
def require_unique_groups(frame, *, name: str) -> None  # invariante de §5.4
def orphan_markers(target, affected, recompute_keys) -> DataFrame
def merge_source(recompute, markers, target_columns) -> DataFrame
def merge_sql(target_table, view_name) -> str
def merge_into_silver(spark, target_table, merge_src, *, view_name) -> None
def run(spark, target_table, prepared, *, source, transform, normalize_company,
        normalize_city_column, lookback_days=DEFAULT_LOOKBACK_DAYS,
        from_date=None, to_date=None, force_full=False,
        repeat_window=False) -> dict
```

`as_bool` (regla cerrada; la prueban P1/P3/P10/P21):

| Entrada | Resultado |
|---|---|
| `True` / `False` (bool) | tal cual |
| `""`, `"false"`, `"0"` (cualquier caja y espacios) | `False` |
| `"true"`, `"1"` | `True` |
| `None` | `False` (equivale a `""`) |
| cualquier otra cosa (`"yes"`, `"no"`, `"2"`, …) | `ValueError` con mensaje en español |

**Ventana abierta.** `changed_since` no filtra por `_ingest_date` cuando
`window.from_date is None`; solo aplica el tope superior si `to_date` no es
nulo. Es lo que permite que una tabla **vacía con `_ingest_date`** (watermark
nulo) lea todo el bronce y lo inserte por MERGE:

```python
def changed_since(prepared, window):
    if "_ingest_date" not in prepared.columns:
        raise ValueError("Falta _ingest_date en el bronce preparado.")
    if dict(prepared.dtypes)["_ingest_date"] != "date":
        raise ValueError("_ingest_date debe ser de tipo date.")
    cond = F.lit(True)
    if window.from_date is not None:
        cond = cond & (F.col("_ingest_date") >= F.lit(window.from_date))
    if window.to_date is not None:
        cond = cond & (F.col("_ingest_date") <= F.lit(window.to_date))
    return prepared.where(cond)
```

`window_bounds` produce `from_date=None` cuando la tabla existe, tiene
`_ingest_date` y `max(_ingest_date)` es nulo (tabla vacía), salvo que el widget
`from_date` traiga una fecha. En ese caso el resumen lleva `from_date: null` y
es válido.

`run` es el **único punto de escritura**. `repeat_window` se parsea con
`as_bool` **dentro de `run`**; si es verdadero, `run` llama dos veces a la
implementación interna (`_run_once`) con los mismos argumentos y devuelve el
sobre de §5.5; si es falso, devuelve el resumen plano de 16 campos. El notebook
no repite la llamada ni construye el sobre.

Contrato del lado del notebook (no del módulo):

- `prepare_bronze(raw) -> DataFrame`: **sin** cast al contrato. Devuelve el frame
  renombrado y derivado, con las columnas extra de §2.1.
- `build_offers(prepared) -> DataFrame`: cast al contrato + enrich + empresa +
  dedup + `drop` + rename `*_norm` (+ códigos de localización en indeed).
  **No** contiene definiciones: solo llamadas (corrección 2). No toca `title` ni
  `company_name` más allá de la normalización de empresa, y llama a `de.enrich`
  con `add_location_fill=True` y `normalize_location_city=True` **explícitos**
  (sin ellos la ciudad no se normaliza y la clave de bronce no coincide). Aplica
  a `location_city` exactamente la misma regla que
  `de.location_city_normalized_column`.
- `normalize_company(df) -> DataFrame`: composición de nivel superior de las dos
  funciones del notebook,
  `apply_company_canonical(apply_company_normalization(df))` (cada notebook
  aporta su variante de canónico). El módulo la aplica a `prepared`, así que
  **recibe el frame preparado completo** y devuelve el mismo frame con
  `company_name` canónico.
- `normalize_city_column(col: Column) -> Column`:
  `de.location_city_normalized_column`. El módulo la aplica como expresión
  columnar sobre `prepared`; recibe y devuelve una `Column`, no un valor, y no
  crea ningún UDF.
- `run` nunca llama a `prepare_bronze`: recibe `prepared` ya construido, de modo
  que la lectura de bronce ocurre una sola vez.
- `source` es una constante del notebook (`"indeed"`, …), no un widget (RF-9).

Reglas de `run` (todas con test):

- `prepared` se lee **una vez** por el notebook; el módulo no lee bronce.
- `changed_since` exige `_ingest_date` de tipo `date` en `prepared` (la escribe
  `bronze_ingest`); si falta o no es fecha → `ValueError` antes de seguir.
- `transform` se llama sobre los **candidatos** (`candidates`), nunca sobre
  `prepared` completo salvo en modo `full`.
- El módulo **valida antes de escribir**: origen único por grupo, `_ingest_date`
  no nulo en el recálculo, columnas del origen ⊇ columnas del destino
  (`target.columns`) más las nuevas y ninguna columna interna (`_key`, `_rn`).
- Materializa `keyed`, `changed`, `affected`, `recompute`, `recompute_keys`,
  `markers` y `merge_src` (`cache`/`unpersist`) porque se inspeccionan
  (conteos) y luego se escriben: sin cache, `normalize_company`/key/enrich
  correrían dos o tres veces. Los conteos del resumen se calculan **antes** del
  MERGE, con el destino todavía sin actualizar; `target_rows` se lee después.
- `merge_source` **no recibe ni emite `_key`**: la clave se calcula aparte para
  validar unicidad y para los marcadores, y se descarta antes del MERGE.

---

## 5. Conjunto afectado y MERGE

### 5.1 Expresión exacta de la clave (una sola definición)

```python
def key_from_columns(title, company, city):
    return F.lower(F.trim(F.regexp_replace(
        F.concat_ws("|", title, company, city), r"\s+", " ")))


def key_expression(alias=None):
    def c(name):
        return F.col(f"{alias}.{name}") if alias else F.col(name)
    return key_from_columns(c("title"), c("company_name"), c("location_city"))
```

Detalles que hay que respetar tal cual (los hereda la dedup actual):
`concat_ws` **omite los NULL**; el `regexp_replace` va sobre la cadena ya
concatenada; `trim` solo recorta los extremos de la cadena completa; `lower`
va al final. La misma función se aplica a bronce (con empresa y ciudad ya
normalizadas) y a silver; por eso una colisión de clave agrupa las mismas filas
a los dos lados.

`key_from_columns(title, company, city)` es la forma de `key_expression` que
recibe las tres columnas; es la que usa el bronce (`title`, empresa ya
normalizada y ciudad de `location_city_normalized_column`). `key_expression()`
es el atajo para las tres columnas estándar (silver y recálculo).

`key_sql(alias)` reproduce lo mismo en texto SQL para el `ON`, con el patrón
escapado:

```python
def key_sql(alias):
    return (
        "lower(trim(regexp_replace(concat_ws('|', "
        f"{alias}.title, {alias}.company_name, {alias}.location_city), "
        r"'\\s+', ' ')))"
    )
```

- **Escapado del patrón (medido en local, Spark 4.2).** En SQL,
  `select '\s+'` devuelve `s+` (Spark se come la barra) y `regexp_replace('a   b',
  '\s+', ' ')` deja el texto intacto; con `'\\s+'` el patrón llega correcto
  (`a   b` → `a b`). Por eso el texto SQL lleva **doble barra** y la `Column`
  lleva `r"\s+"` de Python. Comparado valor a valor: con `'\\s+'`,
  `key_expression()` y `key_sql()` dan exactamente el mismo resultado en filas
  con espacios repetidos, tabuladores, `NULL` y cadena vacía (P11).

### 5.2 Pseudocódigo de `run` (orden exacto, ya ejecutable)

```python
import time
from uuid import uuid4

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F


def run(spark, target_table, prepared, *, source, transform, normalize_company,
        normalize_city_column, lookback_days=DEFAULT_LOOKBACK_DAYS,
        from_date=None, to_date=None, force_full=False,
        repeat_window=False) -> dict:
    """One incremental run, or two plus the repeat envelope."""
    repeat = as_bool(repeat_window)      # parse BEFORE the first commit
    args = dict(
        spark=spark, target_table=target_table, prepared=prepared, source=source,
        transform=transform, normalize_company=normalize_company,
        normalize_city_column=normalize_city_column,
        lookback_days=lookback_days,
        from_date=from_date, to_date=to_date, force_full=force_full,
    )
    first = _run_once(**args)
    if not repeat:
        return first
    second = _run_once(**args)           # same explicit window, same content
    return {"repeat_window": True, "runs": [first, second]}


def _run_once(spark, target_table, prepared, *, source, transform,
              normalize_company, normalize_city_column, lookback_days,
              from_date, to_date, force_full) -> dict:
    t0 = time.monotonic()
    bounds = window_bounds(
        spark, target_table, lookback_days=lookback_days,
        from_date=from_date, to_date=to_date, force_full=force_full,
    )

    # --- 1. Full mode: only force_full / table_missing / missing_ingest_date
    if bounds.mode == "full":
        offers = transform(prepared).cache()
        try:
            rows = offers.count()
            offers.write.mode("overwrite").option("overwriteSchema", "true") \
                  .saveAsTable(target_table)
        finally:
            offers.unpersist()
        return _summary(source, bounds, changed_rows=0, affected_groups=0,
                        recomputed_groups=rows, deleted_groups=0,
                        merge_rows=0, new_columns=[], written=True,
                        target_rows=spark.read.table(target_table).count(),
                        elapsed_s=time.monotonic() - t0)

    target = spark.read.table(target_table)   # window_bounds checked it exists
    keys = SOURCE_SPECS[source].silver_keys

    cached = []

    def keep(df):
        df = df.cache()
        cached.append(df)
        return df

    try:
        # --- 2. Exact key over bronze -------------------------------------
        keyed = keep(
            normalize_company(prepared)                    # initcap + canonical
            .withColumn(KEY, key_from_columns(
                F.col("title"),
                F.col("company_name"),
                normalize_city_column(F.col("location_city"))))
        )

        changed = keep(changed_since(keyed, bounds))       # _ingest_date in window
        if changed.isEmpty():
            return _summary(source, bounds, changed_rows=0, affected_groups=0,
                            recomputed_groups=0, deleted_groups=0,
                            merge_rows=0, new_columns=[], written=False,
                            target_rows=target.count(),
                            elapsed_s=time.monotonic() - t0)

        # --- 3. Affected set: exact keys -----------------------------------
        keys_new = changed.select(F.col(KEY)).distinct()
        changed_ids = changed.select(*keys)

        old_groups = target.join(changed_ids, list(keys), "left_semi")
        keys_old = old_groups.select(key_expression().alias(KEY))

        affected = keep(keys_new.unionByName(keys_old).distinct())

        # --- 4. Candidates: exact key equality -----------------------------
        candidates = select_candidates(keyed, affected)

        # --- 5. Recompute the whole group with the notebook chain ----------
        recompute = keep(recompute_groups(candidates, transform=transform))
        require_no_null_ingest_date(recompute)      # ValueError before writing

        # The recompute key is derived HERE: build_offers already dropped
        # _key, so a later select(KEY) would be UNRESOLVED_COLUMN.
        recompute_keys = keep(
            recompute.select(key_expression().alias(KEY)).distinct()
        )

        # --- 6. Markers: affected groups with no rows left in bronze --------
        markers = keep(orphan_markers(target, affected, recompute_keys))

        # --- 7. MERGE source: target.columns + new columns ------------------
        merge_src = keep(merge_source(recompute, markers, target.columns))

        # --- 8. A single commit --------------------------------------------
        view_name = f"__silver_batch_{source}_{uuid4().hex[:8]}"
        merge_into_silver(spark, target_table, merge_src, view_name=view_name)

        return _summary(source, bounds, changed_rows=changed.count(),
                        affected_groups=affected.count(),
                        recomputed_groups=recompute.count(),
                        deleted_groups=markers.count(),
                        merge_rows=merge_src.count(),
                        new_columns=[c for c in recompute.columns
                                     if c not in target.columns],
                        written=True,
                        target_rows=spark.read.table(target_table).count(),
                        elapsed_s=time.monotonic() - t0)
    finally:
        for df in cached:
            df.unpersist()


def select_candidates(keyed, affected):
    """Rows of prepared whose exact key is in the affected set."""
    return keyed.join(affected, KEY, "left_semi").drop(KEY)


def recompute_groups(candidates, *, transform):
    """Recompute the affected groups with the notebook's own chain."""
    return transform(candidates)


def orphan_markers(target, affected, recompute_keys):
    """One marker per affected group that has no rows left in bronze."""
    return (target
            .withColumn(KEY, key_expression())
            .join(affected, KEY, "left_semi")
            .join(recompute_keys, KEY, "left_anti")
            .drop(KEY)
            .withColumn("_ingest_date", F.lit(None).cast("date")))
```

`merge_into_silver` es quien crea la vista temporal y la borra **siempre**:

```python
def merge_into_silver(spark, target_table, merge_src, *, view_name):
    if not spark.catalog.tableExists(target_table):
        raise ValueError(
            "La tabla destino no existe: ejecuta con force_full o revisa la migración."
        )
    merge_src.createOrReplaceTempView(view_name)
    try:
        spark.sql(merge_sql(target_table, view_name))
    finally:
        spark.catalog.dropTempView(view_name)
```

Notas de ejecución (eliminan las ambigüedades de las revisiones anteriores):

- **La clave del recálculo se calcula antes de los marcadores y de
  `merge_source`.** `build_offers` devuelve el frame ya deduplicado y sin
  `_key`; por eso `recompute_keys` se deriva en un frame aparte. Cualquier
  `select(KEY)` sobre `recompute` después de eso sería `UNRESOLVED_COLUMN`.
- **`candidates` se construye por semi-join exacto** sobre `keyed` (que lleva
  `_key`), no por un `IN` con lista recogida al driver y no por pares. Si una
  clave colisiona con dos tripletas distintas, el semi-join devuelve **todas**
  sus filas.
- **`merge_source` proyecta a `target.columns` (orden del destino) + columnas
  nuevas** del transform; así `_key` no llega al MERGE. El frame que recibe no
  debe traer columnas internas (`_key`, `_rn` ni otras de trabajo): si las trae,
  `ValueError`.
- **Los marcadores se construyen con `_key` y luego se descarta**; el
  discriminador es `_ingest_date = NULL`. Sus columnas nuevas (si el transform
  trae alguna) se rellenan con `NULL` del tipo del recálculo dentro de
  `merge_source`.
- `affected`, `recompute`, `recompute_keys`, `markers` y `merge_src` se cachean
  porque se usan varias veces; son pequeños (grupos y filas afectadas, no la
  tabla). `keyed` y `changed` se cachean para no repetir la normalización de
  empresa/ciudad ni la expresión columnar.
- `run` libera los `cache` en un `finally`; `merge_into_silver` borra la vista
  temporal en su propio `finally`, también si el MERGE falla.
- El semi-join de `old_groups` es por nombre de columna (`list(keys)`), así que
  no hay columnas duplicadas aunque `target` y `changed_ids` compartan nombres.

### 5.3 Por qué la clave exacta es correcta

La afirmación que sostiene el mecanismo es:

```text
para toda fila r de bronce, con s = build_offers({r}):
key_bronze(r) == key_from_columns(s.title, s.company_name, s.location_city)
```

Prueba por partes:

1. **`title`**: `enrich` solo la lee y la dedup no la modifica. `key_bronze` y la
   silver usan el mismo valor.
2. **`company_name`**: `enrich` no la toca. `run` aplica `normalize_company` a
   `prepared` antes de calcular la clave; `build_offers` aplica la **misma**
   composición (cada notebook, su variante) antes de la dedup. La composición
   es un punto fijo sobre su salida (`canonical(initcap(stored)) = stored`,
   P16), así que aplicarla dos veces no cambia el valor.
3. **`location_city`**: `enrich` escribe `city_canon` con
   `location_city_normalized_column`, y la clave de bronce usa **esa misma
   función** sobre la ciudad cruda. Al haber una sola definición, la
   equivalencia es por construcción; P16b la fija como regresión contra la
   columna de `enrich` (y contra la función pura, que se conserva como
   referencia) para acentos, países, ciudades conocidas/desconocidas, distritos,
   `NULL`, vacío y espacios. Para `NULL`, la expresión devuelve `''`; para una
   desconocida, el valor original recortado.
4. Por 1–3, la terna normalizada es la misma a los dos lados; aplicar
   `key_from_columns` (determinista) da la misma clave.

Con eso:

- **`candidates` contiene todas las filas de cada grupo afectado.** La
  selección es por igualdad de `key_bronze`, que por el lema es la `_key` del
  grupo; no hay subconjunto.
- **`transform(candidates)` recalculado y recortado a `affected` es idéntico a
  `transform(prepared)` recortado a `affected`.** El recálculo ve todas las
  filas del grupo (la dedup elige la misma ganadora) y no ve filas de otros
  grupos (el semi-join es exacto). Una colisión de clave no rompe esto: las
  tripletas que colisionan **son** el mismo grupo de la dedup, y están todas.
- **Los grupos no afectados no cambian.** Ninguna fila suya está en la ventana
  (`keys_new`) y ninguna fila suya de silver tiene un `job_id` cambiado
  (`keys_old`). El único cambio posible fuera de la ventana es una reescritura
  de bronce con fecha antigua, que es la limitación conocida del watermark
  (§3 D-B4) y se cubre con backfill/`force_full`.
- **Los grupos afectados que se quedan sin filas se borran** con los marcadores
  (anti-join con `recompute_keys`): es la migración de grupo y el borrado de la
  ganadora.

Coste: una pasada estrecha sobre ~31k filas para normalizar empresa (join sobre
nombres distintos), ciudad (expresión columnar) y clave, más un semi-join;
`enrich` (lo caro) solo corre sobre los candidatos. No se lee ni se transforma
el bronce completo con regex de descripciones.

### 5.4 MERGE, tabla destino y primera ejecución

- El MERGE **exige que la tabla destino exista** (igual que el bloque A): si no
  existe, `merge_into_silver` lanza `ValueError` con el mensaje de que hay que
  ejecutar con `force_full` (o que la migración no se ha hecho). En la práctica
  no se llega ahí: `window_bounds` detecta tabla inexistente y va a modo
  completo.
- **Primera ejecución** (dev y prod): la tabla existe pero no tiene
  `_ingest_date` → modo completo → `overwrite` con el esquema nuevo (la columna
  se crea con `overwriteSchema`). Esa es la migración; no hay one-off.
- **Tabla vacía con `_ingest_date`**: no es `full`; la ventana queda abierta y
  el MERGE inserta todos los grupos (mismo contenido que un rebuild, un solo
  commit). Es un caso degenerado, no el camino normal.
- **Marcadores**: una fila por grupo afectado sin filas en bronce; el MERGE la
  usa solo para borrar. Nunca se inserta, y sus columnas extra (si el transform
  trae alguna nueva) van a `NULL` con el tipo del recálculo.
- **Invariantes que el módulo comprueba** antes de escribir (fallo rápido, con
  mensaje en español): origen único por grupo (sin
  `DELTA_MULTIPLE_SOURCE_ROW_MATCHES`), `_ingest_date` no nulo en el origen real,
  columnas del origen ⊇ columnas del destino y ausencia de columnas internas.
- **Empate exacto** en `posted_date` y `_ingest_date`: el ganador es arbitrario
  (igual que hoy). El fixture de pruebas evita el empate exacto.
- **Filas que desaparecen del bronce** (borrado, `RESTORE`, rewrite, cambio de
  `job_id` o de `site`): el incremental **no las detecta**; hay que ejecutar con
  `force_full` (§6, §9).

### 5.5 Resumen JSON (`dbutils.notebook.exit`) y `repeat_window`

`run` devuelve un `dict` serializable; el notebook lo escribe **en una línea**:

```python
dbutils.notebook.exit(json.dumps(summary, ensure_ascii=True,
                                 separators=(",", ":"), default=str))
```

**Contrato de `run` y del sobre.** El notebook llama **una sola vez**:

```python
summary = inc.run(
    spark, silver_table("offers_<fuente>"), df,
    source="<fuente>", transform=build_offers,
    normalize_company=normalize_company,
    normalize_city_column=de.location_city_normalized_column,
    lookback_days=lookback_days, from_date=from_date, to_date=to_date,
    force_full=force_full, repeat_window=repeat_window,
)
```

- `repeat_window` falso (producción y run A): `run` devuelve el objeto de **16
  campos** de la tabla.
- `repeat_window` verdadero (solo run B de T-10): `run` ejecuta dos veces la
  implementación con **los mismos argumentos** y devuelve
  `{"repeat_window": true, "runs": [summary1, summary2]}`; cada elemento tiene
  los 16 campos. El segundo run usa la misma ventana explícita
  (`from_date`/`to_date`); sin ventana explícita, el watermark podría haber
  cambiado y la segunda ventana se recalcularía — por eso `repeat_window` solo
  se usa en el run B con rango explícito. Combinado con `force_full` haría dos
  reconstrucciones completas (no se usa; el run B es incremental).

**Qué es idempotente en la repetición.** La repetición vuelve a ejecutar el
MERGE con el mismo bronce y deja el **mismo contenido**, pero **no
necesariamente los mismos conteos**. Si la primera pasada recalculó un grupo
que migró y generó un marcador, la segunda ya no ve la fila vieja en `target`:
`keys_old` deja de incluirla y el marcador desaparece. Ejemplo medido:
`affected_groups=2, recomputed_groups=1, deleted_groups=1` →
`affected_groups=1, recomputed_groups=1, deleted_groups=0`.

- **Deben coincidir:** `mode` (`incremental`), `mode_reason` (`null`),
  `written` (`true`), `changed_rows` (misma ventana y mismo bronce),
  `recomputed_groups` (en el fixture de repetición P15/P21, que vacía el grupo
  migrado: 1→1), `new_columns`, `target_rows` y el **contenido** de la tabla
  (mismo conjunto de filas y columnas; en dev, los exports).
- **Pueden bajar y no son criterio de idempotencia:** `affected_groups`,
  `deleted_groups` y `merge_rows`.
- **Pueden cambiar sin afectar a la idempotencia:** `watermark` (la primera
  pasada puede haber insertado una fecha mayor) y `elapsed_s`.
- **Matiz de `recomputed_groups`:** si el grupo antiguo **conserva filas** tras
  la migración, la segunda pasada ya no lo recalcula (`keys_old` solo ve el
  `job_id` en su grupo nuevo) y `recomputed_groups` bajaría; el contenido y
  `target_rows` no cambian. El fixture de repetición vacía el grupo migrado, y
  el criterio duro es siempre contenido + `target_rows`.

Campos exactos (16, siempre presentes; `null` cuando no aplica):

| Campo | Tipo | Qué es |
|---|---|---|
| `source` | str | `indeed` / `linkedin` / `infojobs` / `multi_site` |
| `mode` | str | `full` o `incremental` |
| `mode_reason` | str o null | `force_full`, `table_missing`, `missing_ingest_date`; `null` en incremental |
| `watermark` | `"YYYY-MM-DD"` o null | `max(_ingest_date)` leído antes del run; null si la tabla no existe o está vacía |
| `from_date` | `"YYYY-MM-DD"` o null | inicio efectivo de la ventana; **null en modo `full`** y en **ventana abierta** (tabla vacía con `_ingest_date`); es un valor válido |
| `to_date` | `"YYYY-MM-DD"` o null | tope superior, si se pidió; null en modo `full` o sin tope |
| `lookback_days` | int | 7 por defecto |
| `changed_rows` | int | **filas** de `prepared` en la ventana (no claves); en ventana abierta, todas las del bronce; 0 en `full` |
| `affected_groups` | int | **claves distintas** (grupos) de `keys_new` ∪ `keys_old`, no filas; 0 en `full` |
| `recomputed_groups` | int | grupos recalculados (filas del recálculo, una por grupo); en `full`, filas escritas |
| `deleted_groups` | int | marcadores (grupos que se vacían); 0 en `full` |
| `merge_rows` | int | filas del origen del MERGE (recálculo + marcadores); 0 en `full` |
| `new_columns` | list[str] | columnas que el transform añade al contrato (evolución); `[]` en `full` |
| `written` | bool | `false` si no hubo commit (ventana vacía) |
| `target_rows` | int | filas de la silver después del run (una por grupo) |
| `elapsed_s` | float | segundos de `run`, 1 decimal |

**Cómo lo lee T-10 sin SQL.** `databricks jobs get-run-output <task-run-id>`
devuelve `{"notebook_output": {"result": "<json>"}}`; el `result` es el JSON de
una línea. Para el run B:

1. Parsear el `result`; debe tener `repeat_window: true` y `runs` con dos
   elementos.
2. Comprobar en **los dos** resúmenes: `mode == "incremental"`,
   `mode_reason == null`, `written == true`, `changed_rows > 0`, y que
   **coinciden** `recomputed_groups` (si la ventana de B vacía un grupo
   migrado), `new_columns`, `target_rows` y el contenido de la tabla (en dev,
   los exports). `affected_groups`, `deleted_groups` y `merge_rows` **pueden
   bajar** en la segunda pasada y no son criterio; `watermark` y `elapsed_s`
   pueden diferir. Si el grupo antiguo conservara filas, `recomputed_groups`
   también podría bajar y el contenido manda. Eso es la idempotencia por
   contenido de la ventana repetida.
3. Comprobar en el run A (sin sobre): `mode == "full"`,
   `mode_reason == "missing_ingest_date"` (o `table_missing` si es la primera
   vez), y que `recomputed_groups` coincide con las filas de la silver.

El formato exacto de `get-run-output` y el escapado del `result` están
`[NO VERIFICADO]` hasta T-10; si la CLI devuelve el JSON con escapes, se
deserializa la cadena `result` antes de leer los campos. No hace falta consultar
la tabla (y por tanto no se despierta el warehouse).

Detalles de serialización: las fechas salen como `"YYYY-MM-DD"` (no `date`), los
`None` como `null`, y `ensure_ascii=True` evita que la consola/CLI escape nada.
El resumen no lleva listas largas: nunca se imprimen claves ni ids de fila.

### 5.6 Riesgos de ejecución del MERGE

| Riesgo | Qué pasaría | Estado |
|---|---|---|
| **`WHEN MATCHED` múltiple en el origen** | `DELTA_MULTIPLE_SOURCE_ROW_MATCHES` y run abortado | Imposible por construcción y validado antes del MERGE (P12) |
| **Columnas nuevas con `WITH SCHEMA EVOLUTION`** | se añaden al final y solo se rellenan en las filas reescritas; el resto queda `NULL` | Aceptado y documentado; la migración de un campo del contrato se hace con `force_full` (D-B6) |
| **Cambio en los 128 patrones canónicos** | los nombres ya guardados no se recalculan (companies_* lee silver) | `force_full` de las silvers afectadas (D-B6, §2.3) |
| **`_ingest_date` nulo en el origen real** | la rama `DELETE` borraría la fila del grupo | `ValueError` antes de escribir (P14) |
| **Tabla destino inexistente** | error de análisis del MERGE | `window_bounds` va a `full`; si aun así se llega, `ValueError` con instrucción |
| **Tabla destino sin `_ingest_date`** | no hay watermark | modo `full` con `reason="missing_ingest_date"` (P1) |
| **Tabla destino vacía** | `max(_ingest_date)` nulo | incremental con ventana abierta; el MERGE inserta todo (P1) |
| **Ventana vacía** | nada que escribir | sin commit; `written=false` (P8) |
| **Origen con menos columnas que el destino** | `UPDATE SET *` falla o deja columnas sin valor | `merge_source` valida y lanza `ValueError` con la lista |
| **Origen con columnas internas** | `_key`/`_rn` viajarían al MERGE | `merge_source` valida y lanza `ValueError` |
| **Origen con columnas de más** | con evolución se añaden al destino | permitido y reportado en `new_columns` (P19) |
| **`ON` con expresión** | sin poda de ficheros: escaneo del destino (26k filas) | aceptado (§9); layout/`OPTIMIZE` en la 010 |
| **Vista temporal** | el MERGE no ve el DataFrame | `createOrReplaceTempView` con nombre único por fuente y ejecución; se borra en `finally`. Aislamiento entre tareas `[NO VERIFICADO]` hasta T-10; el nombre único evita depender de él |
| **`cache` sin `unpersist`** | memoria retenida hasta el final del notebook | `run` libera al terminar (`try/finally`) |
| **Reintento del task** | el MERGE es idempotente; repetir deja el mismo contenido | aceptado (P9) |
| **Fallo entre ofertas y `companies_*`** | ofertas nuevas y dimensiones de la ejecución anterior | mismo comportamiento que hoy (las dos escrituras ya estaban en celdas consecutivas); se corrige en el siguiente run |
| **Deriva entre la expresión de la clave y la de `enrich`** | dos definiciones de ciudad producirían claves distintas | Imposible por construcción: `enrich` reutiliza `location_city_normalized_column` (una sola definición); P16b lo fija y el diff de T-08 se aprueba antes |

---

## 6. Casos límite

| # | Caso | Resultado esperado |
|---|---|---|
| 1 | **Clave nueva** (grupo que no existe en silver) | El grupo entra por `keys_new`, se recalcula y `WHEN NOT MATCHED` lo inserta. Una fila más en la silver. |
| 2 | **Captura más reciente** de una oferta del grupo (mismo `job_id`, gana la dedup) | El grupo se recalcula desde bronce; `WHEN MATCHED` actualiza la fila del grupo en su sitio (misma `_key`), sin duplicar. |
| 3 | **Captura más antigua** (la fila de bronce no gana la dedup del grupo) | El grupo se recalcula igual; el ganador sigue siendo el mismo y se reescribe el mismo contenido. Ninguna fila huérfana, ningún cambio de valores. |
| 4 | **Empate** (`posted_date` igual en dos filas del grupo) | Desempata `_ingest_date` desc. Empate exacto en ambas: el ganador es arbitrario (igual que hoy, riesgo preexistente, §9); el fixture de pruebas evita el empate exacto. |
| 5 | **Migración de grupo** (la oferta ganadora cambia de ciudad o título) | `keys_new` trae el grupo nuevo y `keys_old` el antiguo. El antiguo se recalcula: si le quedan filas, actualiza su ganador; si no, el marcador lo **borra** (sin huérfano). |
| 6 | **Ventana vacía** (sin filas de bronce en la ventana) | No se escribe nada: ni MERGE ni commit. Resumen con `changed_rows = 0`, `written = false`. |
| 7 | **Primera ejecución** (tabla inexistente o sin `_ingest_date`) o `force_full` | Reconstrucción completa por `overwrite`; la tabla queda con `_ingest_date` poblado y su número de filas = `transform(bronce completo)`. |
| 8 | **Tabla vacía con `_ingest_date`** | **No es `full`**: `window_bounds` devuelve `incremental` con `from_date = null` (ventana abierta), `changed_since` no filtra por `_ingest_date` y el MERGE inserta todos los grupos. `changed_rows` = filas del bronce, `affected_groups` = claves distintas, `target_rows` = grupos; `from_date: null` en el resumen es válido. |
| 9 | **Fichero tardío anterior al watermark** | Un fichero tardío normal entra con la fecha del lote y cae en la ventana. Solo se pierde un cambio de bronce reescrito con fecha anterior al watermark; se recupera con `from_date`/`to_date` o `force_full`. |
| 10 | **`job_id` repetido dentro de la ventana** | Imposible en bronce (una fila por clave tras el bloque A). Aunque lo estuviera, `changed_ids` se usa en `left_semi` (no duplica filas del destino) y `affected` se deduplica con `distinct()`. |
| 11 | **Columna nueva en bronce** | `prepare_bronze` la deja pasar; el `select` del contrato la descarta mientras no esté en `df_structure_Schema`. Al añadirla al contrato, el MERGE la crea en silver con `WITH SCHEMA EVOLUTION` (y las filas no reescritas quedan a `NULL`: §3 D-B6). Oro no la ve hasta que esté en `FACT_OFFERS_COLUMNS`. |
| 12 | **`_ingest_date` nulo en el origen real** (bronce corrupto) | El módulo aborta con `ValueError` antes del MERGE en lugar de borrar filas por confundirlas con marcadores. |
| 13 | **Repetición del mismo backfill** | Mismos grupos, mismos valores, mismo contenido final (puede haber un commit nuevo con datos idénticos, §9). |
| 14 | **Nulos en las componentes de la clave** (`title`, `company_name` o `location_city`) | No hay caso especial: la clave se calcula con la misma función a los dos lados. `location_city` nula → `''` por `location_city_normalized_column` y por `enrich`; empresa nula → `NULL` (o `''` en InfoJobs) en ambos lados. El grupo se recalcula completo. |
| 15 | **Dos tripletas distintas con la misma `_key`** (colisión por `concat_ws` o por una barra vertical en una componente) | Son el mismo grupo de la dedup; el semi-join exacto devuelve **todas** sus filas y el recálculo elige la misma ganadora que el rebuild. No hay degradación a `full`. |
| 16 | **Ciudad desconocida** («Ciudad Inexistente», «  barcelona  ») | `enrich` y `location_city_normalized_column` devuelven el valor original recortado (o el canónico si se reconoce); la clave coincide. P16b lo cubre. |
| 17 | **Filas que desaparecen del bronce** (borrado, `RESTORE`, rewrite, cambio de `job_id`, cambio de `site` en multi_site) | El incremental **no las detecta**: el grupo no está en `keys_new` ni en `keys_old` y silver conserva el ganador antiguo. Hay que ejecutar `force_full` (documentado en §9 y en el runbook). |
| 18 | **Cambio en los 128 patrones canónicos** | `companies_*` lee silver sin renormalizar; los nombres ya guardados no cambian. Ejecutar `force_full` de las silvers afectadas (§2.3). |
| 19 | **Repetición de la ventana dentro del notebook** (`repeat_window`, solo T-10) | Dos commits con el mismo **contenido**. Deben coincidir `target_rows`, `recomputed_groups` (en el caso de migración del fixture, que vacía el grupo) y el conjunto de filas; `affected_groups`, `deleted_groups` y `merge_rows` **pueden bajar** si la primera pasada ya limpió un grupo migrado, y no son criterio. |
| 20 | **`companies_*` con el run sin escritura** | Lee la silver anterior y reescribe la dimensión igual; ninguna diferencia con hoy. |

---

## 7. Contrato con oro y exports (RF-10)

| Consumidor | Cómo lee | ¿Ve `_ingest_date`? |
|---|---|---|
| `gold_build.ipynb` (unión de las 4 silvers) | `unionByName(allowMissingColumns=True)` (indeed aporta 31 columnas, el resto 33: `salary_min`/`salary_max` quedan nulas en las filas de indeed) y después `_keep_best` por `job_id` y por (`title_norm`, `company_norm`, `salary_filled`) | La columna viaja en el DataFrame unido, pero **no se usa** en ninguna ventana |
| `Prepare_Gold.build_fact_offers` | `df.select(*keep)` con `keep = [c for c in FACT_OFFERS_COLUMNS if c in df.columns]` (proyección explícita y tolerante) | **No**: no está en la lista |
| `fact_offer_skills`, `dim_*` | columnas explícitas (`job_id`, `skills`, `posted_date`, …) | **No** |
| `gold.companies_complete_catalog` | `companies_*` (sin cambios) | **No** |
| `Prepare_Web_Export.project_*` | `df.select(*WEB_*_COLUMNS)` (contrato) | **No** |
| `Prepare_PBI_Export.build_pbi_export` | `df.select(*PBI_*_COLUMNS)` (contrato) | **No** |

Cómo se comprueba sin SQL (0 sesiones):

1. **Local (T-08):** P13, **una vez por fuente y parametrizado por
   `target.columns`**: el origen del MERGE tiene exactamente las columnas del
   destino (más las nuevas) y ninguna interna (`_key`, `_rn`). Como red de
   seguridad, el tamaño del contrato por fuente (`indeed 31`; `linkedin`,
   `infojobs`, `multi_site` 33, **incluyendo `_ingest_date`**) se afirma en un
   mapa por fuente, no en una lista global de columnas.
2. **Local (T-08):** test que reutiliza `Prepare_Gold.build_fact_offers` sobre la
   salida incremental (el patrón ya existe en `test_integration_spark.py`) y
   verifica que `FACT_OFFERS_COLUMNS` sigue saliendo sin `_ingest_date`; y el
   mismo razonamiento para las listas de contrato de los dos exports
   (proyección explícita; se comprueba que ninguna columna `_*` está en la
   lista).
3. **Dev (T-09/T-10):** las 8 tareas en verde y los dos exports con `meta.json`
   válido. **Run A = migración completa** (tabla sin `_ingest_date` → `full` con
   `overwrite`). **Run B = backfill incremental** (`from_date`/`to_date` sobre
   el bronce existente, sin ficheros nuevos, `mode="incremental"` en las cuatro
   tareas), que ejecuta de verdad el conjunto afectado, el recálculo y los
   marcadores, y **repite la misma ventana dentro del notebook** para demostrar
   idempotencia. Sus Parquet de `web_export_dev` y `pbi_export_dev` deben ser
   **idénticos byte a byte** a los del run A: eso es la equivalencia
   incremental ↔ reconstrucción sobre datos reales. El `meta.json` no es byte a
   byte (lleva `generated_at`): se comparan sus conteos y `data_date`.
   Referencia del run de T-05 (bloque A): `pbi_export_dev` 33.790.833 B,
   `fact_offers` 26.057.

Si en el run B alguna tarea devuelve un `mode_reason` distinto de `null` (o el
`mode` no es `incremental`), el run **no** demuestra equivalencia incremental:
se registra el motivo y se corrige antes de T-11. No hay motivos de datos que
degraden el run: solo una tabla inexistente, sin `_ingest_date` o `force_full`.

---

## 8. Plan de pruebas de T-08

`databricks_notebooks/tests/test_silver_incremental.py`, con Spark local (mismo
fixture `spark` que `test_upsert_job_offers.py`, zona UTC). **No hay
`delta-spark` en local** (comprobado: `import delta` → `ModuleNotFoundError`),
así que:

- La lógica (ventana, conjunto afectado, candidatos, marcadores, contrato,
  resumen y sobre) se prueba con DataFrames reales.
- La semántica del MERGE se prueba con una **implementación de referencia** en
  el propio test (upsert por `_key` de las filas con `_ingest_date` no nulo +
  borrado de las claves de los marcadores), que es exactamente lo que dice el
  SQL del §3 D-B6. El fixture publica la silver como **vista temporal**
  (`createOrReplaceTempView`: `spark.catalog.tableExists` y `spark.read.table`
  la ven igual que una tabla) y el test **monkeypatchea `merge_into_silver`**
  para aplicar esa implementación de referencia y volver a publicar el
  resultado; así `run` se ejercita entero (ventana, conjunto afectado,
  recálculo, marcadores y resumen) sin Delta y sin `saveAsTable` (que en el
  Windows local exige `winutils`). El modo `full` se prueba por
  `window_bounds`/`P1`, no por su escritura.
- El SQL generado se prueba por estructura (texto), como en el bloque A.
- **Limitación declarada:** el MERGE real (Delta), `MERGE WITH SCHEMA EVOLUTION`
  y el `USING` sobre vista temporal se ejercitan en el run dev (T-10) y en
  producción (T-11), no en local.

Los tests reciben un `transform` **de prueba** que respeta el contrato real: no
toca `title` ni `company_name` (salvo el `normalize_company` de juguete), aplica
a `location_city` la misma regla que el `normalize_city_column` de prueba
(`F.trim(F.coalesce(col, F.lit("")))`), calcula `_key` con `key_expression()`,
deduplica con la ventana real (`posted_date` desc, `_ingest_date` desc) y
conserva `_ingest_date`; el `normalize_company` de prueba es **idempotente** y
colapsa dos variantes («ACME S.L.» → «Acme») para ejercitar la normalización
compartida. El `normalize_city_column` de prueba es
`lambda col: F.trim(F.coalesce(col, F.lit("")))`.

**P15b y P16 incluyen la composición real.** La cadena de referencia de P15b se
construye, en este orden, con:

1. el mapa de rename y el esquema **copiados** del notebook;
2. `de.enrich(add_location_fill=True, normalize_location_city=True)` (que ya
   usa `location_city_normalized_column` para la ciudad);
3. `apply_company_normalization` **copiada del notebook** (`initcap` + colapso
   de espacios);
4. `de.apply_company_canonical` (join; la usan indeed/LinkedIn/multi_site) o la
   variante columnar copiada del notebook de InfoJobs;
5. `inc.key_expression()`;
6. la ventana de dedup y el `drop`/rename del notebook.

Sin el paso 3 la cadena **no** es la real: para empresas que no casan con
ningún patrón, el valor guardado sería distinto y la clave de bronce no
coincidiría. P16 lo demuestra con el punto fijo y con el contraejemplo sin
`initcap`. La cadena real de los notebooks no es importable: se valida en T-10
con el **run B incremental** sobre datos reales, y el diff de T-09 es el control
de deriva de la copia (rename y esquema).

| ID | Test | Qué demuestra | RF |
|---|---|---|---|
| P1 | `test_full_mode_only_for_three_reasons` | `full` por tabla inexistente, sin `_ingest_date` y `force_full`; tabla **vacía con la columna** → `incremental` con ventana abierta; `as_bool` sobre una tabla con `_ingest_date`: `force_full="true"` → `full`/`force_full`; `"false"` y `""` → `incremental` | RF-8, RF-11 |
| P1b | `test_empty_target_open_window_end_to_end` | **Caso de P1 con `run` completo**: `target` vacío con `_ingest_date` y bronce no vacío, con la implementación de referencia del MERGE; comprueba `mode="incremental"`, `mode_reason=None`, `written=True`, `changed_rows>0` (filas), `from_date is None` y `target_rows` = grupos distintos del bronce | RF-8 |
| P2 | `test_window_bounds_uses_max_ingest_date_minus_lookback` | Ventana = `max(_ingest_date) − lookback_days`, sin tope superior; `lookback_days="7"` y `7` dan lo mismo | RF-8 |
| P3 | `test_invalid_window_parameters` | `to_date` sin `from_date`, `from_date > to_date`, ISO inválido, `lookback_days` negativo → `ValueError`; `as_bool("yes")`/`"2"` → `ValueError` | RF-9 |
| P4 | `test_new_group_is_inserted` | Clave nueva → `INSERT` de una fila | RF-7 |
| P5 | `test_newer_capture_wins_and_older_does_not` | Gana la captura ganadora del grupo; la más antigua no pisa | RF-7 |
| P6 | `test_group_migration_expels_the_orphan` | Migración de grupo sin fila huérfana (marcador borra) | RF-7 |
| P7 | `test_only_affected_groups_are_written` | Grupos sin cambios no entran en el origen del MERGE | RF-7 |
| P8 | `test_empty_window_does_not_write` | Sin filas en la ventana no hay escritura (`written=false`) | RF-8 |
| P9 | `test_repeated_window_is_idempotent` | Repetir la ventana deja el mismo contenido | RF-8, RF-9 |
| P10 | `test_backfill_range_and_force_full` | `from_date`/`to_date` recalculan lo pedido; `force_full="false"` → incremental, `"true"` → completo, `""` → no fuerza | RF-9 |
| P11 | `test_key_expression_matches_sql_text` | `key_from_columns`/`key_expression` y `key_sql` dan el mismo valor con espacios repetidos, tabuladores, `NULL` y vacío; incluye el caso `'\s+'` mal escapado como contraejemplo | RF-7 |
| P12 | `test_merge_sql_shape` | `MERGE WITH SCHEMA EVOLUTION`, `ON` por expresión, **orden** DELETE → UPDATE → INSERT, condiciones de las ramas, `UPDATE SET *`/`INSERT *`, nombre de vista recibido | RF-7 |
| P13 | `test_merge_source_contract_by_source` | **Parametrizado por fuente y por `target.columns`**: origen = columnas del destino (más nuevas), `_ingest_date` presente, sin `_key`/`_rn`; falta una columna → `ValueError`; columna interna → `ValueError`. Tamaños medidos: indeed 31, resto 33 | RF-10 |
| P14 | `test_recompute_rejects_null_ingest_date` | `ValueError` ante `_ingest_date` nulo | RF-8 |
| P15 | **`test_equivalence_incremental_equals_full_rebuild`** | **Gate principal (fixture sintético)**: aplicar el incremental sobre el fixture y comparar con `transform(bronce completo)`; incluye G5 con nulos en las componentes y la colisión de clave; `mode == "incremental"` y `mode_reason is None`. La repetición de la ventana se compara por **contenido**, `target_rows` y `recomputed_groups` (en este fixture el grupo migrado se vacía); `affected_groups`, `deleted_groups` y `merge_rows` pueden bajar y no son criterio | RF-7, RF-8 |
| P15b | `test_equivalence_with_reference_chain_indeed_and_linkedin` | Igual que P15 con la **cadena de referencia** (`de.enrich` con `add_location_fill=True` y `normalize_location_city=True` + `apply_company_normalization` copiada + `de.apply_company_canonical` + `inc.key_expression()`) y el rename/esquema copiados del notebook. **No es la cadena real** (se valida en T-10) | RF-7 |
| P16 | `test_company_normalization_is_fixed_point_and_name_set` | Con las **funciones reales** —`de.apply_company_canonical` (join) y la variante **columnar** copiada del notebook de infojobs—: los 128 literales canónicos casan consigo mismos; `canonical(initcap(stored)) = stored` sobre un fixture con `NULL`, `''`, `&`, apóstrofes, espacios repetidos y Unicode; y el contraejemplo **sin `initcap`** cambia el **nombre guardado** en las empresas que no casan (la `_key` no cambia:
la expresión la pasa a minúsculas). Justifica §2.3 y la composición de P15b | RF-7 |
| P16b | `test_city_normalized_column_matches_enrich_and_key_matches_silver` | `de.location_city_normalized_column` contra la columna `location_city` que produce `de.enrich(add_location_fill=True, normalize_location_city=True)` (y la función pura `de.location_city_normalized`, que se conserva como referencia), en un fixture **construido explícitamente** con todas las claves y valores de los mapas de ciudad/país, más acentos, distritos numerados («Dublin 1», «Dublin 10»), «Greater X Area», «X y alrededores», «X area», `NULL`, vacío y espacios; el test **cuenta** los valores (la verificación contó **281/281** entre la pura y la columnar). Incluye un caso con `add_location_fill=False` que demuestra que **no** es una combinación válida para la clave (**3/28 diferencias medidas**) y la **clave calculada sobre bronce** (`initcap` + canónico + `location_city_normalized_column`) contra la clave de la silver (`enrich` + canónico + `key_expression()`) para las mismas filas, incluidas las de componentes nulas | RF-7 |
| P17 | `test_candidates_are_exact_key_equality` | Solo entran las filas cuya clave exacta está en `affected`: una fila con el mismo título y empresa pero **otra ciudad** no se selecciona; una clave compartida por dos tripletas distintas selecciona **ambas**; `transform(candidates)` recortado a `affected` = `transform(prepared)` recortado | RF-7 |
| P18 | `test_null_components_are_grouped_by_exact_key` | Fixture con `NULL` en cada componente: la clave de bronce coincide con la de silver, el grupo se recalcula completo y `incremental == full`; no hay `mode_reason` | RF-7 |
| P19 | `test_merge_source_allows_new_columns` | Columna nueva del transform viaja al origen, el marcador la lleva a `NULL` con su tipo y se reporta en `new_columns` | RF-7 |
| P20 | `test_summary_fields_and_json` | El resumen tiene exactamente los 16 campos, fechas como `YYYY-MM-DD`, `from_date`/`watermark` a `null` en modo `full` (y `from_date` nulo válido en ventana abierta), serializable en una línea; `mode_reason` solo toma los tres valores de `full` o `null` | RF-8 |
| P21 | `test_repeat_window_envelope` | `repeat_window="true"` con `from_date`/`to_date` explícitos → `{"repeat_window": true, "runs": [s1, s2]}` con dos resúmenes de 16 campos; deben coincidir `mode`, `mode_reason`, `written`, `changed_rows`, `recomputed_groups` (en el caso de migración del fixture), `new_columns`, `target_rows` y el **contenido** final; `affected_groups`, `deleted_groups` y `merge_rows` pueden bajar y **no** se comparan; si el grupo antiguo conservara filas, `recomputed_groups` podría bajar y el contenido manda. `repeat_window="false"`/`""` → resumen plano (sin sobre); `"yes"` → `ValueError` | RF-8, RF-9 |

Fixture del gate P15 (una fuente, temporal `timestamp`):

- **Bronce inicial**: grupo G1 con dos ofertas (`A` fecha 2026-09-01 y `B`
  2026-09-10, gana `B`), G2 con `C` (2026-10-05), G3 con `D` (2026-10-01), G4
  con el mismo título y empresa que G1 pero **otra ciudad** (clave distinta), y
  **G5 con nulos en las componentes**.
- **G5 corregido**: incluye (i) el antiguo par de nulos desplazados
  (`A(title='Data Analyst', company=NULL, city='Madrid')` y
  `B(title='Data Analyst', company='Madrid', city=NULL)`), que con la
  normalización real **ya no colisiona** (la ciudad nula pasa a `''` y la
  empresa nula se omite: `data analyst|madrid` frente a `data analyst|madrid|`);
  (ii) una fila con `title` nulo; (iii) una fila con `company_name` nulo y
  ciudad no nula; (iv) una fila con `location_city` nula sola; (v) una fila con
  `company_name=''`; (vi) una fila con las tres componentes nulas; y (vii) una
  **colisión real** por el separador: `title='Data|Analyst', company='ACME',
  city='Madrid'` frente a `title='Data', company='Analyst',
  city='ACME|Madrid'` (misma `_key`: `data|analyst|acme|madrid`, tripletas
  distintas). Con clave exacta **no hay ambigüedad**: el incremental debe dar
  exactamente el mismo resultado que el rebuild para todos los casos, sin `full`
  y sin `mode_reason`.
- **Silver inicial**: `transform(bronce inicial)` (estado antes del lote).
- **Lote**: oferta nueva `E` (grupo nuevo), captura nueva de `A` que la hace
  ganadora de G1, `C` cambia de ciudad (migración de G2 a G2′), captura vieja
  de `D`, una captura nueva de una fila de G5 con la misma clave que otra
  tripleta, y una fila de G4 sin cambios.
- **Comprobación**: `run` incremental → comparar con `transform(bronce final)`
  por conjunto de filas (sin orden) y por columnas. Después, repetir la misma
  ventana (`repeat_window` con `from_date`/`to_date` explícitos) y comprobar que
  el **contenido** no cambia y que coinciden `target_rows` y
  `recomputed_groups` (en este fixture el grupo migrado se vacía);
  `affected_groups`, `deleted_groups` y `merge_rows` pueden bajar en la segunda
  pasada (el grupo migrado ya se limpió) y **no** se comparan. Después,
  `force_full` → mismo resultado.

### 8.1 Comprobaciones hechas en local antes de escribir este diseño

Ejecutadas con Spark **4.2.0** (misma línea que el runtime 17.3), sin
`delta-spark`:

| Comprobación | Resultado |
|---|---|
| Contrato real por fuente (`de.enrich` + `drop` + `rename`, **conservando `_ingest_date`**) | **indeed 31**; **linkedin, infojobs y multi_site 33** (los tres conservan `salary_min`/`salary_max`; los totales incluyen `_ingest_date`) |
| Columnas que añade `enrich` sobre el contrato de 22 | **14** (listadas en §1.3) |
| Copia de `COMPANY_ALIAS_PATTERNS` en el notebook vs módulo `Enrich` | **idénticas** (128 patrones) |
| Todo literal canónico casa con su propio patrón | **sí** (0 fallos) ⇒ `canonical(initcap(stored)) = stored` |
| `initcap` idempotente (`McKinsey & Company`, `IKEA`, `T-Mobile`, `NULL`, espacios) | **sí** en todos |
| Composición `canonical(initcap(x))` sobre 30 nombres (incluye `NULL`, vacío, `&`, apóstrofes, Unicode) | punto fijo **30/30** |
| **Contraejemplo sin `initcap`**: `canonical(x)` vs `canonical(initcap(x))` | **6 de 30** difieren (`ACME S.L.`, `acme s.l.`, `Foo  Bar`, `AT&T`, `Strasse GmbH`, …) ⇒ `initcap` es obligatorio antes del canónico |
| `location_city_normalized_column` vs la columna de `enrich` con el fixture **construido explícitamente** (todas las claves y valores de `_LOCATION_CITY_CANONIC` y `_LOCATION_CITY_TO_COUNTRY`, más acentos, distritos numerados, «Greater X Area», «X y alrededores», «X area», ciudades desconocidas, `NULL`, vacío, espacios y mayúsculas) | La columnar es **la misma expresión que `enrich`** (una sola definición); la verificación contó **281/281** entre la pura y la columnar (0 diferencias) y P16b cuenta los valores reales. `NULL`/vacío → `''`; desconocida → original recortado |
| `enrich` con `add_location_fill=False` | **3/28 diferencias** frente a la ciudad normalizada: **no** es una combinación válida para la clave; `build_offers` pasa `add_location_fill=True` y `normalize_location_city=True` |
| Idempotencia de `location_city_normalized` (pura) sobre 14 valores | **sí** en todos |
| **Clave de bronce vs clave de silver** (`initcap` + canónico + `location_city_normalized_column` vs `enrich` + canónico + `key_expression()`) sobre un fixture con variantes de empresa, acentos, ciudades conocidas/desconocidas y componentes nulas | **16/16** claves iguales |
| `key_sql` con `'\s+'` (una barra) vs `key_expression()` | **distinto** (`data   scientist` → `data   scienti t`) |
| `key_sql` con `'\\s+'` (dos barras) vs `key_expression()` | **idéntico** en espacios, tabuladores, `NULL` y vacío |
| `concat_ws` con `NULL` intermedios / todo `NULL` | omite el `NULL` / devuelve `''`; con clave exacta la colisión es el mismo grupo (§5.3) |
| `delta-spark` local | **no instalado** |

Lo que **no** se puede validar en local y queda asignado al run dev (T-10):
el MERGE real de Delta, `MERGE WITH SCHEMA EVOLUTION` (columnas nuevas y `NULL`
en filas no reescritas), el `USING` sobre vista temporal y el aislamiento de
vistas entre tareas (`[NO VERIFICADO]`), el comportamiento con los datos reales
(nulos, colisiones, tipos, frecuencia), los tiempos (`[NO VERIFICADO]`), las 8
tareas en paralelo, el formato de `jobs get-run-output` (`[NO VERIFICADO]`) y la
vía de parámetros del run B (`[NO VERIFICADO]`).

---

## 9. Riesgos y limitaciones aceptadas

| Riesgo | Mitigación / estado |
|---|---|
| **Divergencia incremental ↔ reconstrucción** | Gate P15/P15b/P16b en local (fixture, cadena de referencia y clave exacta), contrato y transform compartidos, y en dev el **run B incremental** sobre datos reales: sus exports deben salir idénticos byte a byte a los del run A |
| **`location_city_normalized_column` no coincide con la columna de `enrich`** | P16b en local (fixture completo de mapas y adversarios; 281/281 en la verificación) y el run B: si diverge, los exports no serán idénticos y el run se detiene; al ser una sola definición, la deriva exigiría un cambio incorrecto del refactor |
| **Refactor de `enrich` para reutilizar la expresión** | Cambio de `Enrich_Job_Offers_Dataframes.py` con **aprobación previa del diff** (T-08) y la suite `databricks_notebooks` en verde (`test_enrich_pure.py` e integración); al ser una sola definición no hay deriva entre la clave y la columna |
| **`enrich` sin las banderas de ciudad** | `add_location_fill=False` deja la ciudad sin normalizar: 3/28 diferencias medidas y la clave de bronce no coincide. `build_offers` pasa `add_location_fill=True` y `normalize_location_city=True` explícitos |
| **Coste de la expresión columnar sobre ~31k filas** | Es una expresión nativa (sin Python worker), más barata que un UDF; se mide en T-10. No se ejecuta `enrich` completo sobre el bronce, que es lo caro |
| **Coste de leer el bronce completo** para normalizar empresa/ciudad y calcular la clave | Una pasada estrecha de ~31k filas + join de nombres distintos + expresión columnar + semi-join. Es el precio de la igualdad exacta; lo caro (`enrich`) corre solo sobre los candidatos |
| **Deriva entre la normalización de empresa de bronce y la de `build_offers`** | El módulo aplica el **mismo callable** `normalize_company`; el diff de T-09 controla que el notebook no cambie la composición; P16 prueba el punto fijo y P15b la cadena de referencia |
| **Colisión de clave entre tripletas distintas** | No es un fallo: son el mismo grupo de la dedup; la selección exacta recupera todas las filas. Caso cubierto en G5/P17/P18 |
| **Ventana con lookback y cambio con fecha antigua** | `lookback_days = 7` (una cadencia) y backfill documentado en el runbook; `force_full` como último recurso |
| **Filas que desaparecen del bronce** (`RESTORE`, rewrite, borrado, cambio de `job_id` o de `site`) | El incremental no las detecta; `force_full` documentado en §6 y en el runbook. Se vigila en el run semanal |
| **Cambio en los 128 patrones canónicos** | Las dimensiones leen silver sin renormalizar; hay que ejecutar `force_full` de las silvers afectadas (§2.3) |
| **Commits con datos idénticos al repetir la ventana** | El contenido no cambia; el MERGE reescribe los ficheros de los grupos afectados. Se acepta: el coste es un commit, no un cambio de datos. Compactación/`OPTIMIZE`, en la 010 |
| **Repetición con conteos distintos** | `affected_groups`, `deleted_groups` y `merge_rows` pueden bajar porque la primera pasada ya limpió el grupo migrado; se compara **contenido**, `target_rows` y `recomputed_groups` (§5.5, P15/P21, T-10) |
| **Tabla vacía con `_ingest_date`** | **No es `full`**: `window_bounds` da `incremental` con `from_date=null`, `changed_since` lee todo el bronce y el MERGE inserta los grupos; `from_date: null` en el resumen es válido. P1b ejecuta el `run` completo |
| **`ON` con expresión (sin poda de ficheros)** | Escaneo completo del destino (26k filas) por MERGE. Aceptado a este tamaño; si crece, se materializa `_key` (reabriría D-B5) |
| **Empate exacto en `posted_date` y `_ingest_date`** | No determinista, **preexistente** en la reconstrucción; no se corrige aquí (fuera de alcance: spec propia de dedup de negocio) |
| **MERGE real no ejecutable en local** (sin `delta-spark`) | Semántica cubierta por la implementación de referencia del test y por el SQL; el motor real se valida en el run dev (T-10) y en producción (T-11) |
| **Vista temporal y aislamiento de sesión** | Nombre único por fuente y ejecución + `dropTempView` en `finally`; el aislamiento entre tareas está `[NO VERIFICADO]` hasta T-10. El diseño no depende de él |
| **Duplicados por grupo en el destino** | Invariante garantizada por la reconstrucción inicial y por la unicidad del origen (el módulo lo comprueba). Un MERGE no colapsaría duplicados preexistentes |
| **`df_structure_Schema` descarta columnas nuevas de bronce** | Comportamiento actual; no se cambia. Una columna nueva del scraper se añade al contrato en una tarea explícita, con `force_full` para que no queden `NULL` en las filas no reescritas |
| **Origen con una columna que el transform ya no produce** | `ValueError` claro en `merge_source` antes de escribir (protege `UPDATE SET *`) |
| **Origen con columnas internas (`_key`, `_rn`)** | `ValueError` en `merge_source`; `build_offers` ya las dropea |
| **Escapado del patrón `\s` en el texto SQL del `ON`** | `'\\s+'` en SQL (Spark reduce `'\s+'` a `s+`); lo cubre el test P11 comparando `key_expression()` con `key_sql()` |
| **`repeat_window` sin ventana explícita** | El segundo run recalcularía la ventana desde el watermark y podría quedar vacío; solo se usa en el run B con `from_date`/`to_date` explícitos (§5.5) |
| **`--params` de la CLI no disponible** | T-10 usa el job corto `silver_backfill` con los mismos widgets (alternativa documentada en §10) |
| **`companies_*` desfasadas si falla la celda de ofertas** | El fallo deja el run en rojo (aviso por email) y la siguiente ejecución las reconstruye; mismo comportamiento que hoy |

---

## 10. Encaje en las tareas siguientes

- **T-08** — `Silver_Incremental.py` + `test_silver_incremental.py` (§4 y §8).
  Sin dependencias nuevas. No ejecuta Databricks. Incluye `key_from_columns`,
  `key_expression`, `key_sql`, `window_bounds`, `changed_since` (ventana abierta
  si `from_date is None`), `select_candidates`, `recompute_groups`,
  `orphan_markers`, `merge_source`, `merge_into_silver`, `run` con
  `normalize_city_column`, `repeat_window` y `as_bool`; **no** incluye las
  funciones retiradas en §3. Añade en `Enrich_Job_Offers_Dataframes.py` la
  función columnar `location_city_normalized_column(col)` y hace que `enrich` la
  reutilice para la columna `location_city` (una sola definición, sin UDF);
  **ese cambio del módulo requiere aprobación previa del diff**. La función
  necesita a nivel de módulo `from pyspark.sql import Column` y
  `from pyspark.sql import functions as F` (hoy el módulo no los importa arriba).
  `build_offers` llama a `de.enrich` con `add_location_fill=True` y
  `normalize_location_city=True` explícitos; cualquier otra combinación no vale
  para la clave. La proyección del origen es `target.columns` ∪ nuevas.
  P15b/P16 incluyen `initcap`; P16b cubre la ciudad **columnar** contra `enrich`
  (fixture explícito y contado, sin fijar un número; incluye el caso
  `add_location_fill=False` con 3/28 diferencias) y la clave de bronce contra la
  de silver. P1b ejecuta el `run` completo sobre `target` vacío con
  `_ingest_date`; P15/P21 comparan la repetición por contenido, `target_rows` y
  `recomputed_groups`, no por `affected_groups`/`deleted_groups`/`merge_rows`.
  El test de contrato (P13) se parametriza por fuente y por `target.columns`; no
  hay lista global de 31. La suite `python -m pytest databricks_notebooks/tests
  -q` debe quedar verde, incluidos `test_enrich_pure.py` y los tests de
  integración.
- **T-09** — notebooks de silver según §2.2 y §2.4 (requiere el **OK previo** de
  AGENTS.md: fichero, celdas, diff y motivo), `bundle deploy -t dev`. El diff
  debe enseñar que solo se mueve código y que las celdas de negocio no cambian
  (lista cerrada de cambios de texto en §2.5). Incluye:
  - la procedencia de `companies_infojobs`/`companies_multi_site` (§2.3);
  - el orden de escritura (ofertas antes de `companies_*`);
  - la retirada de los `display`/`describe`;
  - los widgets `lookback_days`, `from_date`, `to_date`, `force_full` y
    `repeat_window`; **sin** widget `source`;
  - la llamada a `inc.run` con
    `normalize_city_column=de.location_city_normalized_column` y
    `repeat_window=repeat_window`;
  - la llamada a `de.enrich` dentro de `build_offers` con
    `add_location_fill=True` y `normalize_location_city=True` explícitos;
  - **parámetros de job** en `resources/elt.job.yml` (decisión de T-09/T-10):
    a nivel de job,

    ```yaml
    parameters:
      - name: backfill_from
        default: ""
      - name: backfill_to
        default: ""
      - name: repeat_window
        default: ""
    ```

    y en las cuatro tareas silver, dentro de `notebook_task.base_parameters`
    (el nombre del parámetro de notebook es el del widget; el nombre del
    parámetro de job es el de la CLI):

    ```yaml
    from_date: "{{job.parameters.backfill_from}}"
    to_date: "{{job.parameters.backfill_to}}"
    repeat_window: "{{job.parameters.repeat_window}}"
    ```

    Vacíos por defecto: no cambian el comportamiento ni en dev ni en prod. Los
    parámetros de job requieren la misma aprobación previa que los notebooks.
  - `docs/RUNBOOK.md` + `docs/es/RUNBOOK.md` con el procedimiento de backfill
    (parámetros, ventana, coste y rollback).
- **T-10** — dos runs dev: **A = migración completa** (tabla sin
  `_ingest_date` → `full` con `overwrite`, 8/8 tareas, exports de referencia) y
  **B = backfill incremental** con `from_date`/`to_date` sobre el bronce
  existente (sin ficheros nuevos), que ejecuta el MERGE, los marcadores y el
  conjunto afectado, y **repite la misma ventana dentro del notebook**
  (`repeat_window`) para demostrar idempotencia. Vía del run B (decisión de
  T-09/T-10):
  1. `bundle run elt -t dev --profile vs_code --params backfill_from=…,backfill_to=…,repeat_window=true`
     si la CLI instalada admite `--params` (`[NO VERIFICADO]`);
  2. si no, job corto `silver_backfill` (como `dedupe_oneoff`) con los mismos
     widgets y parámetros.

  Evidencia: ids, `mode` y `mode_reason` de las cuatro tareas, el sobre de
  `repeat_window` comparado por **contenido** (`target_rows`, `recomputed_groups`
  si la ventana de B vacía un grupo migrado, y exports; `affected_groups`,
  `deleted_groups` y `merge_rows` pueden bajar en la segunda pasada y no son
  criterio; si el grupo antiguo conservara filas, `recomputed_groups` podría
  bajar y el contenido manda), tiempos (silver ≤ 2,8–3,6 min; total ≤ 13 m 16 s),
  `jobs get-run-output` y exports **byte a byte** iguales a A. La ventana de B se
  elige no vacía y, si es posible, un subconjunto propio del bronce (p. ej. el
  último `_ingest_date` presente) para que el MERGE no reescriba todo.
- **T-11** — producción: el primer run programado reconstruye (tabla sin
  `_ingest_date`), el siguiente es incremental. Rollback de bundle y `RESTORE`
  de las silvers (mecanismo de la 007). Runbook: `force_full`, `lookback_days`,
  `from_date`/`to_date`, `repeat_window` no se usa en producción, las **filas
  que desaparecen del bronce** y el **cambio de patrones canónicos** (ambos
  exigen `force_full`).

---

## 11. Dudas abiertas y `[NO VERIFICADO]`

Ninguna duda bloquea el diseño: alcance, unidad, conjunto afectado, watermark,
columnas de control, escritura y clave exacta están cerradas (§3).

Quedan como comprobaciones de ejecución, no como decisiones de diseño:

| Tema | Estado | Dónde se resuelve |
|---|---|---|
| MERGE real de Delta | `[NO VERIFICADO]` (sin `delta-spark` local) | Run A/B de T-10; producción T-11 |
| `MERGE WITH SCHEMA EVOLUTION` (columnas nuevas y `NULL` en filas no reescritas) | `[NO VERIFICADO]` | T-10/T-11; P19 fija la semántica esperada |
| `USING` sobre vista temporal y aislamiento de vistas entre tareas | `[NO VERIFICADO]` | T-10 (cuatro tareas concurrentes) |
| Formato exacto de `jobs get-run-output` y escapado del `result` | `[NO VERIFICADO]` | T-10; se parsea la cadena si hace falta |
| Tiempos y coste real de la expresión columnar y del semi-join | `[NO VERIFICADO]` | T-10; se anota el lote y los tiempos |
| `--params` de la CLI del bundle | `[NO VERIFICADO]` | T-10; si no existe, job corto `silver_backfill` |
| Precedencia de los parámetros de job sobre los widgets | `[NO VERIFICADO]` en este entorno | T-09/T-10; los defaults vacíos hacen que un fallo de precedencia no cambie el comportamiento |
| Cómo se lanza un backfill en producción | Decisión operativa de T-11 | Runbook |

No queda ningún `[NO VERIFICADO]` de **corrección** de la ciudad: la función es
una expresión columnar nativa (sin UDF ni import en *workers*), P16b la compara
con la columna de `enrich` en local sobre un fixture construido explícitamente
(281/281 en la verificación) y la comprobación en datos reales es el gate de
exports idénticos del run B (T-10). El único punto pendiente es el
**coste/tiempo** de la expresión columnar, ya listado como riesgo de rendimiento
(T-10).
