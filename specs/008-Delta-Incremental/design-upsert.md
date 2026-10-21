# Diseño del upsert de bronce (T-01)

Decisiones cerradas con la persona el 2026-10-21, revisadas tras la verificación
independiente del 2026-10-21 (hallazgos B1–B4: nombres de columna por fuente,
tipo de la temporal, ANSI y sintaxis de la evolución de esquema). Es la entrada
de T-02 (módulo y tests), T-03 (ingesta en dev), T-04 (verificación dev) y T-06
(prod).

## D0 — Mapa de columnas por fuente (obligatorio antes de escribir código)

El bronce **no** tiene los mismos nombres ni tipos en las cuatro fuentes: las
silvers renombran al leer. El ingest nuevo trabaja sobre el bronce, así que el
mapa es parte del diseño.

| Fuente | Columna clave en bronce | Columna temporal en bronce | Tipo de la temporal |
|---|---|---|---|
| indeed | `job_key` | `scraped_at` | `timestamp` (UTC, `datetime64[ns, UTC]`) |
| linkedin | `job_id` | `scraped_at` | `string` ISO-8601, offset `+00:00` |
| infojobs | `id_oferta` | `fecha_scraped` | `timestamp` (local, sin zona) |
| multi_site | `site` + `job_id` | `scraped_at` | `string` ISO-8601 |

- Fuente del mapa: `databricks_notebooks/silver_*.ipynb` (`withColumnsRenamed`),
  `indeed_jobs_scraper/scraper/schema.py` (`scraped_at: datetime64[ns, UTC]`),
  `infojobs_jobs_scraper/*/models.py` (`id_oferta`, `fecha_scraped: datetime`) y
  la auditoría de la 007 para LinkedIn/multi_site (`scraped_at` string ISO con
  offset único `+00:00`).
- Cuando la spec dice «clave `job_id`», significa **el identificador de la
  fuente**: `job_key`, `job_id` o `id_oferta` según la tabla.
- Los tipos reales se confirman con `DESCRIBE TABLE` en dev en T-03 antes de
  apoyarse en ellos; el diseño no depende de ello porque normaliza con
  `try_to_timestamp` (no-op si ya es timestamp).
- `_ingest_date` (`date`) y `_source_file` (`string`) las añade el ingest y son
  iguales en las cuatro tablas.

## D1 — Claves

| Fuente | Tabla bronce | Clave del MERGE |
|---|---|---|
| indeed | `bronze.indeed` | `t.job_key = s.job_key` |
| linkedin | `bronze.linkedin` | `t.job_id = s.job_id` |
| infojobs | `bronze.infojobs` | `t.id_oferta = s.id_oferta` |
| multi_site | `bronze.multi_site` | `t.site = s.site AND t.job_id = s.job_id` |

- Se usa la columna tal cual llega, sin normalizar: es la misma que consumen
  las silvers tras renombrar.
- En `multi_site`, `site` se toma antes del rename a `source_scraper`
  (auditoría 007: 6 valores, sin nulos). `site` nulo o en blanco → cuarentena.
- El dedup de negocio («misma oferta republicada con clave nueva») **no** vive
  aquí: está en oro (`gold_build.ipynb`, `_keep_best` por
  `title_norm` + `company_norm` + `salary_filled`). Bronce conserva una fila
  por anuncio. Evidencia medida (silver dev, 2026-10-21; top 25, hay más): el
  duplicado de negocio es **cross-portal** y con la **misma localización** en
  todos los casos (2–3 fuentes), con separaciones entre capturas de 1 día a ~9
  meses; se anota como candidata a spec propia (localización + ventana
  temporal), fuera de la 008. Detalle en `evidence.md`.

## D2 — Condición de actualización

La fila entrante gana si su instante de captura es **estrictamente mayor** que
el guardado. Empate de instante → desempata `_ingest_date`. Empate exacto → no
se actualiza (idempotencia). Una fila guardada sin fecha válida (legacy) se
repara con la primera captura buena.

```sql
MERGE WITH SCHEMA EVOLUTION INTO <catalog>.bronze.<fuente> AS t
USING <batch> AS s
ON  <condición de clave según D1>
WHEN MATCHED AND (
       try_to_timestamp(t.<temporal>) IS NULL
    OR try_to_timestamp(s.<temporal>) > try_to_timestamp(t.<temporal>)
    OR (try_to_timestamp(s.<temporal>) = try_to_timestamp(t.<temporal>)
        AND s._ingest_date > t._ingest_date)
     ) THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
```

- La cláusula es `MERGE WITH SCHEMA EVOLUTION INTO ...` (entre `MERGE` y `INTO`),
  disponible desde DBR 15.2/15.4; el job usa 17.3. No es `MERGE INTO ... WITH
  SCHEMA EVOLUTION`.

- **Nulos y formatos raros:** se usa `try_to_timestamp`, no `to_timestamp`. Con
  ANSI activo (DBR 17.3) `to_timestamp('')` o un valor malformado lanzaría
  `CAST_INVALID_INPUT` y abortaría el lote; `try_to_timestamp` devuelve NULL.
- El batch nunca trae la temporal nula ni imparseable: esas filas van a
  cuarentena (D4), así que la comparación no puede anularse.
- `try_to_timestamp(t.<temporal>) IS NULL` cubre tanto la fila guardada nula
  como la guardada con `''` o texto inválido (la rama `IS NULL` de la columna
  cruda no las cubría).

## D3 — Comparación por timestamp

- Se compara el **instante**, no la fecha: dos capturas del mismo día con horas
  distintas deben ordenarse. La fecha es solo presentación: las silvers derivan
  `posted_date` con `to_date` y oro la consume ya como `date`.
- Para LinkedIn y multi_site el bronce guarda string ISO-8601 (`+00:00`);
  para Indeed e InfoJobs ya es `timestamp`. `try_to_timestamp` unifica ambos
  casos.
- Nota: `fecha_scraped` de InfoJobs es hora local sin zona; no se compara con
  otras fuentes (cada tabla se mergea contra sí misma), así que no hay mezcla
  de husos.
- Guardia de offset (solo LinkedIn/multi_site, T-03). Cubre también la variante
  `Z`, que `regexp_extract` devolvería como cadena vacía:

```sql
SELECT DISTINCT regexp_extract(scraped_at, '([+-][0-9]{2}:[0-9]{2}|Z)$', 1)
FROM <catalog>.bronze.<fuente>;
```

## D4 — Cuarentena

Tabla por fuente, append-only y con evolución de esquema:

```
<catalog>.bronze.<fuente>_quarantine
  <todas las columnas de la fuente, tal cual llegan>
  _ingest_date        date     -- control del ingest (ya viene en el lote)
  _source_file        string
  _row_hash           string   -- sha2(to_json(struct(columnas de la fuente)), 256)
  _quarantine_reason  string   -- 'missing_key' | 'missing_scraped_at'
  _quarantine_at      timestamp -- current_timestamp() del lote
```

- **Qué va a cuarentena:**
  - clave nula o en blanco (`trim(cast(col as string)) == ''`), incluido `site`
    en multi_site → `missing_key`;
  - temporal nula, en blanco o imparseable (`try_to_timestamp(col) IS NULL`) →
    `missing_scraped_at`.
  - Si concurren ambos, gana `missing_key` (precedencia definida aquí; T-02 la
    prueba).
- **Orden en el lote:** primero se separan las inválidas (no participan en el
  dedup ni en el MERGE), después se deduplica el resto. Así una fila inválida
  no puede desplazar a una válida de la misma clave.
- **Escritura:** en el mismo `foreachBatch`, con la misma lectura del lote (sin
  releer el fichero).
- **Identidad:** `_row_hash` se calcula **solo sobre columnas de la fuente**
  (excluye `_ingest_date`, `_source_file` y las `_quarantine_*`), con
  `to_json(struct(...))`: distingue `NULL` de `''` y no sufre las colisiones de
  `concat_ws` (`("a", NULL)` y `(NULL, "a")`, o separadores dentro de valores).
  Matiz: un `timestamp` se serializa en la zona horaria de la sesión, así que la
  huella es estable **con la zona fija** (los tests fijan UTC; cada tabla de
  cuarentena vive en un solo entorno, dev o prod). Se deduplica por `_row_hash`
  + `_quarantine_reason` + `_source_file`, primero **dentro del lote** y luego
  con `anti join` filtrado por los `_source_file` del lote contra lo ya
  guardado (acota el coste al crecer la tabla): reprocesar el mismo fichero no
  duplica la cuarentena. Un fichero re-subido con **otra ruta** y el mismo
  contenido sí se volvería a cuarentenar (la ruta forma parte de la identidad);
  se acepta y se documenta.
- **Zona horaria:** el notebook fija `spark.sql.session.timeZone=UTC` antes de
  calcular la huella (los tests también), para que `to_json` no cambie de
  representación entre local y clúster.
- Las filas en cuarentena no entran en el MERGE y no abortan el lote.
- Su tratamiento posterior (corregir o descartar con evidencia) queda para
  specs futuras; aquí solo se captura el dato y el motivo.

## D5 — Checkpoint y evolución de esquema

- **Plan A (decidido):** se conserva `<checkpoints_root>/<fuente>/ckpt`. El
  ingest nuevo solo lee ficheros nuevos (no relee los 14,4 GB / 247 ficheros de
  dev) y el histórico acumulado se normaliza con una one-off (T-04 en dev si
  hace falta; T-06 en prod, **antes** del primer lote nuevo de la ventana).
- **Plan B (solo si T-03 falla):** `ckpt_v2` desde cero + `drop` de la tabla y
  reconstrucción desde landing (relectura completa).
- Con `foreachBatch`, el `.option("mergeSchema","true")` del `writeStream` no
  actúa sobre el sink. La evolución de esquema se hace explícita en el MERGE:
  `MERGE WITH SCHEMA EVOLUTION INTO ...` (DBR 15.2+; el job usa 17.3). Se
  prueba en T-03 con una columna nueva y se comprueba que `_rescued_data` sigue
  apareciendo como columna normal (RF-3).

## Casos resueltos

1. **Clave nueva** → `WHEN NOT MATCHED` → `INSERT`.
2. **Captura nueva de una clave existente** (instante mayor) → `UPDATE`; la
   fila queda con los valores y con `_ingest_date`/`_source_file` nuevos.
3. **Fichero viejo re-subido** (instante menor) → `WHEN MATCHED` no se cumple:
   no se escribe nada; la tabla no cambia.
4. **Dos ficheros del mismo microbatch con la misma clave** → el dedup del lote
   deja una fila (la de instante mayor, `_ingest_date` como desempate); el
   resultado no depende del orden de lectura. **Empate exacto de instante:** se
   conserva una fila arbitraria; si dos capturas del mismo segundo difieren en
   contenido, el resultado no es determinista (riesgo aceptado y documentado;
   si apareciera en la práctica, se añadiría un desempate estable por huella de
   contenido).
5. **Fila con clave nula o `''`** (o `site` nulo/blanco en multi_site) →
   `missing_key`; va a cuarentena y el resto del lote se mergea con normalidad.
6. **Fila con clave válida y temporal nula, `''` o imparseable** →
   `missing_scraped_at`; va a cuarentena y no se inserta en bronce.
7. **Fila guardada con temporal nula/blanca/imparseable y captura nueva válida**
   → `UPDATE` (rama `try_to_timestamp(t.<temporal>) IS NULL`).
8. **Empate exacto de instante y de `_ingest_date`** → no se actualiza;
   reejecutar el mismo lote el mismo día deja la tabla igual. Un replay en
   **otro día** con el mismo instante de captura actualiza `_ingest_date` y
   `_source_file` (los datos de la oferta no cambian): RF-1 garantiza una fila
   por clave, no bytes idénticos.
9. **Reproceso del mismo fichero con filas inválidas** → `anti join` por
   `_row_hash` + motivo + fichero; la cuarentena no crece.

## Consecuencias en T-02..T-06

- **T-02:** módulo nuevo (p. ej. `Upsert_Job_Offers.py`) con el mapa de D0
  (tabla → columna clave y columna temporal), el split válido/cuarentena con
  `_row_hash`, y el dedup del lote: se añade una columna temporal normalizada
  (`_scraped_ts = try_to_timestamp(<temporal>)`) y se reutiliza
  `keep_latest_by` con `order_by=("_scraped_ts", "_ingest_date")`, sin
  modificar `Deduplicate_Job_Offers.py` (lo usan las silvers). **`_scraped_ts`
  es auxiliar: se elimina antes del MERGE y antes de escribir la cuarentena, y
  no entra en `_row_hash`** (el test de contrato de columnas lo comprueba).
  Tests en `databricks_notebooks/tests/test_upsert_job_offers.py`, con
  `spark.sql.session.timeZone = UTC` en el fixture para que la huella sea
  estable. Se cubren los cuatro esquemas de bronce (timestamp y string).
- **T-03:** `bronze_ingest` pasa de `.toTable` a `.foreachBatch` + MERGE y
  escribe la cuarentena; `DESCRIBE TABLE` de las cuatro tablas; prueba con una
  columna nueva y con una fila inválida; se mide el run dev.
- **T-04:** verificación del bronce dev (ya normalizado por la 007) y one-off
  solo si el ingest nuevo ha vuelto a duplicar. **La one-off de prod no va
  aquí: va en T-06**, antes del primer lote nuevo de la ventana.
- **T-05/T-06:** comparación con la línea base (dev 17 m 43 s / bronze 7 m 36 s)
  y cutover con el mecanismo y rollback de la 007.

## Evidencia de auditoría

Consultas a ejecutar en dev (y repetir en prod antes de T-06). Los resultados se
pegan en `evidence.md`; hasta entonces, pendientes.

```sql
-- 1) Claves nulas o en blanco (una tabla por fuente; cambia columna y tabla)
SELECT count(*) FROM <catalog>.bronze.indeed
WHERE job_key IS NULL OR trim(cast(job_key AS string)) = '';
-- linkedin: job_id · infojobs: id_oferta · multi_site: site y job_id
-- (repetir la condición para site)

-- 2) Temporal nula, en blanco o imparseable (una por fuente)
SELECT count(*) FROM <catalog>.bronze.linkedin
WHERE try_to_timestamp(scraped_at) IS NULL;
-- indeed: scraped_at (timestamp) · infojobs: fecha_scraped · multi_site: scraped_at

-- 3) Capturas del mismo día por clave (string ISO: linkedin y multi_site)
SELECT count(*) AS claves
FROM (
  SELECT job_id, to_date(try_to_timestamp(scraped_at)) AS d
  FROM <catalog>.bronze.linkedin
  GROUP BY job_id, d
  HAVING count(DISTINCT scraped_at) > 1
);
-- multi_site: agrupar por site, job_id, d

-- 4) Offsets distintos (guardia de T-03)
SELECT DISTINCT regexp_extract(scraped_at, '([+-][0-9]{2}:[0-9]{2}|Z)$', 1)
FROM <catalog>.bronze.linkedin;   -- repetir en multi_site
```

| Consulta | Resultado |
|---|---|
| 1, LinkedIn y multi_site (prod y dev) | 0 (007, T-10a) |
| 1, indeed (`job_key`) e infojobs (`id_oferta`) | pendiente de pegar |
| 2, las cuatro tablas | pendiente de pegar |
| 3, LinkedIn y multi_site | pendiente de pegar |
| 4, offsets (guardia) | pendiente; se ejecuta en T-03 |
| Grupos de negocio con >1 clave (silver, dev) | **medido**: 25 grupos (top 25, hay más), todos cross-site y con la misma localización; separación 1 día – ~9 meses; detalle en `evidence.md` |

