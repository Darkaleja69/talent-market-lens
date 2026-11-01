# Diseño: layout físico (T-05) y compute (T-06)

> **Alcance de este documento.** Las dos últimas decisiones de la spec 010 que no
> son código: **T-05** (layout físico: `OPTIMIZE`, `liquid clustering`,
> particionado o **no cambiar**) y **T-06** (compute y concurrencia). **Las dos
> partes están escritas**: layout (§1–§8) y compute (§9). **Este documento no
> cambia la spec**, la cumple: RF-7 (layout) y RF-6 (compute) exigen la decisión
> documentada «aunque la decisión sea no cambiar nada».
>
> **Fecha de la medición:** 2026-10-30, entre las 07:06 y las 07:12 (hora de
> `lastModified` de las 18 tablas).
>
> **Fuente:** `DESCRIBE DETAIL` de las **18 tablas** del catálogo
> **`job_offers_dev`** ejecutado en una **celda de cuaderno** de un clúster
> **all-purpose de lectura**, más el recuento de ficheros y bytes de los dos
> exports. Salida **literal** en
> `docs/media/performance/2026-10-30-describe-detail-dev.txt`. **Sin runs**
> nuevos y **sin SQL warehouse** (0 sesiones de SQL Editor). La **fuente de §9
> (compute)** es distinta: la **API de runs** del run `879269828668729`
> (2026-10-30), no el `DESCRIBE DETAIL`; tampoco lanza un run ni abre SQL.
>
> **Coste:** **céntimos**. El clúster de lectura estuvo abierto del orden de
> **2–3 min** (estimación de la sesión; **duración exacta no registrada**). La
> referencia de `docs/costs.md` para un `D4ds_v4` en *jobs* (~0,75 €/h ≈
> 1,25 céntimos/min) daría **~3–4 céntimos** para 2–3 min; que la tarifa de
> **all-purpose** sea superior a la de *jobs* es un **supuesto** que **no se ha
> medido** (`[NO VERIFICADO]`), así que la cifra de jobs es una **cota inferior**
> y en `docs/costs.md` queda como **estimación en céntimos**, no como importe
> cerrado. La parte de compute (§9) **no añade coste**: se apoya en el run
> `879269828668729`, ya pagado en T-03.
>
> **Método:** `DESCRIBE DETAIL` por tabla (columnas `numFiles`,
> `sizeInBytes`, `avgFileBytes`, `partitionColumns`, `clusteringColumns` y el
> `delta.enableDeletionVectors` de la propiedad de tabla) y recuento de
> ficheros/bytes de `gold/pbi_export_dev` y `gold/web_export_dev`. Las **filas**
> son el `numRecords` que Delta guarda como estadística al escribir, **no un
> `COUNT(*)` independiente**; para las cuatro `offers_*` coincide exactamente con
> el `target_rows` que dejó el run `879269828668729` de T-03
> (3.112 / 2.527 / 12.543 / 10.072), lo que la valida en esas cuatro.

## 1. Fuente, método y límites

### 1.1 De dónde salen los números

El fichero de evidencia tiene dos bloques: (1) una fila por tabla con las
columnas de `DESCRIBE DETAIL`; (2) el recuento de ficheros y bytes de los
exports de oro. **Todo lo que aparece en §2 y §5 sale de ahí**; no hay cifras
estimadas ni extrapoladas. Lo que no está en el fichero va marcado
`[NO VERIFICADO]`.

Unidades: **MB = 10⁶ bytes** y **kB = 10³ bytes** (decimal), para que
`avgFileBytes` del fichero coincida con lo que se cita (7.201.037 B = 7,20 MB,
428.800 B = 0,43 MB).

### 1.2 Límites del método

- `avgFileBytes` es `sizeInBytes / numFiles`: **una media aritmética**, no una
  distribución. El fichero trae `minFileBytes` y `maxFileBytes` como **`None`**
  en las 18 tablas, así que **no se sabe** si dentro de una tabla conviven
  ficheros muy grandes con muy pequeños. En las tablas de 1–2 ficheros la media
  es casi el dato; en `infojobs` (5) y en las dos dimensiones (4) la media puede
  esconder dispersión.
- `numFiles` cuenta los ficheros vivos del **snapshot actual** (los que no están
  lógicamente borrados). No dice cuántas versiones ni cuántos ficheros muertos
  quedan pendientes de `VACUUM`.
- `DESCRIBE DETAIL` **no informa del número de deletion vectors** por tabla; solo
  se conoce la propiedad de tabla (`deletionVectors = true` en las 18).
- No se ha ejecutado ningún `COUNT(*)`, `OPTIMIZE`, `VACUUM` ni `ANALYZE`, y no
  se ha abierto el SQL warehouse.

## 2. Tabla por tabla (18 tablas)

Las 18 tablas del catálogo, agrupadas por capa. `sizeInBytes` es el literal
medido; la media por fichero es la del fichero de evidencia. La última columna es
la lectura en una frase.

| Tabla | Filas | `numFiles` | `sizeInBytes` | Media/fichero | Lectura |
|---|---|---|---|---|---|
| `bronze.indeed` | 3.366 | 1 | 7.201.037 (7,20 MB) | 7,20 MB | 1 fichero, sin fragmentación |
| `bronze.infojobs` | 2.706 | **5** | 2.143.999 (2,14 MB) | **0,43 MB** | **5 ficheros; la media por fichero más baja de bronce** |
| `bronze.linkedin` | 15.220 | 6 | 33.092.854 (33,09 MB) | 5,52 MB | 6 ficheros; la tabla más grande |
| `bronze.multi_site` | 11.222 | 6 | 13.421.544 (13,42 MB) | 2,24 MB | 6 ficheros; 13,42 MB en total |
| `silver.offers_indeed` | 3.112 | 2 | 3.017.241 (3,02 MB) | 1,51 MB | 2 ficheros; 3,02 MB en total |
| `silver.offers_infojobs` | 2.527 | 2 | 1.989.647 (1,99 MB) | 0,99 MB | 2 ficheros; media por debajo de 1 MB pero la tabla es de 2 MB |
| `silver.offers_linkedin` | 12.543 | 2 | 15.912.334 (15,91 MB) | 7,96 MB | 2 ficheros; 15,91 MB en total |
| `silver.offers_multi_site` | 10.072 | 2 | 1.517.405 (1,52 MB) | 0,76 MB | 2 ficheros; media por debajo de 1 MB pero la tabla es de 1,5 MB |
| `silver.companies_indeed` | 1.497 | 1 | 17.827 (17,8 kB) | 17,8 kB | 1 fichero; tabla pequeña |
| `silver.companies_infojobs` | 1.019 | 1 | 12.337 (12,3 kB) | 12,3 kB | 1 fichero; tabla pequeña |
| `silver.companies_linkedin` | 4.131 | 1 | 236.739 (236,7 kB) | 236,7 kB | 1 fichero; tabla pequeña |
| `silver.companies_multi_site` | 4.498 | 1 | 43.537 (43,5 kB) | 43,5 kB | 1 fichero; tabla pequeña |
| `gold.fact_offers` | 26.302 | 1 | 19.150.630 (19,15 MB) | 19,15 MB | **1 fichero: la tabla grande de oro no está fragmentada** |
| `gold.fact_offer_skills` | 44.928 | 1 | 275.982 (276,0 kB) | 276,0 kB | 1 fichero; la tabla con más filas del catálogo |
| `gold.dim_skill_list` | 95 | **4** | 6.200 (6,2 kB) | **1,55 kB** | **4 ficheros diminutos; 6,2 kB en total** |
| `gold.dim_calendar` | 2.919 | 1 | 21.110 (21,1 kB) | 21,1 kB | 1 fichero; tabla pequeña |
| `gold.companies_complete_catalog` | 10.027 | 1 | 299.770 (299,8 kB) | 299,8 kB | 1 fichero; tabla pequeña |
| `gold.dim_currency` | 19 | **4** | 9.326 (9,3 kB) | **2,33 kB** | **4 ficheros diminutos; 9,3 kB en total** |
| **Total (18 tablas)** | **156.203** | **42** | **98.369.519 (98,37 MB)** | — | **42 ficheros; el bronce es el 56,8 % de los bytes** |

Reparto por capa, del mismo fichero: **bronce 32.514 filas / 55.859.434 B (56,8 %
de los bytes)**, **silver 39.399 / 22.747.067 B (23,1 %)**, **oro 84.290 /
19.763.018 B (20,1 %)**. `bronze.linkedin` sola es el **33,6 %** de los bytes del
catálogo.

Dos hechos que atraviesan la tabla entera:

- **`partitionColumns` y `clusteringColumns` son `[]` en las 18 tablas.** Ninguna
  está particionada ni clusterizada: el layout es «tabla plana» (uno o varios
  ficheros, sin ejes de poda).
- **`delta.enableDeletionVectors` está activa en las 18.** Es una propiedad de
  tabla, no un contador: dice que los `MERGE`/`UPDATE`/`DELETE` *pueden* usar
  deletion vectors, no cuántos hay vigentes.

Rango de filas del catálogo: **19** (`dim_currency`) a **44.928**
(`fact_offer_skills`). Ninguna tabla llega a 45k filas y la mayor en bytes es
`bronze.linkedin` con 33,09 MB.

## 3. Lectura

### 3.1 ¿Hay ficheros pequeños?

**Sí, pero ninguno es un problema de escala: en bronce, el peor caso son 5
ficheros y 2,14 MB; en oro, 4 ficheros de 6–9 kB.**

- **`bronze.infojobs`: 5 ficheros × 0,43 MB = 2,14 MB.** Tiene la **media por
  fichero más baja de bronce** (0,43 MB); el máximo de ficheros son **6**
  (`linkedin`, 6 × 5,52 MB, y `multi_site`, 6 × 2,24 MB). Leerlo son **5
  aperturas en vez de 1** para 2,14 MB: un sobrecoste que no se ha medido
  (`[NO VERIFICADO]`) pero que es del orden del ruido frente a los **383 s de
  arranque del clúster y 134 s de bronce** que midió T-03 en el run completo.
- **`gold.dim_skill_list` y `gold.dim_currency`: 4 ficheros cada una, de 1,55 y
  2,33 kB de media (6,2 y 9,3 kB en total).** Cuatro ficheros en 6–9 kB es
  «muchos ficheros» en número y **muy poco** en bytes: son dimensiones de 6–9 kB
  que se leen enteras en los `JOIN` de oro. **`[NO VERIFICADO]`** por qué son 4: la
  hipótesis razonable es una escritura sin `coalesce` desde un frame con 4
  particiones no vacías, pero `DESCRIBE DETAIL` no da el linaje de ficheros y no
  se ha leído el plan de escritura de oro.
- **En el resto, el criterio es el tamaño total, no el del fichero.** El
  objetivo de Delta es de **cientos de MB** (**1 GB por defecto en
  `delta.targetFileSize`**; **128 MB con `optimizeWrite`**), una **referencia de
  la documentación**, no una medición de este repositorio; y es irrelevante con
  tablas de **6 kB–33 MB**: `bronze.linkedin` son 33,09 MB en 6 ficheros de
  5,52 MB, `bronze.multi_site` 13,42 MB en 6 de 2,24 MB, `bronze.indeed`
  7,20 MB en 1, `fact_offers` 19,15 MB en 1, `companies_*` 12–237 kB en 1 y las
  cuatro `offers_*` 1,52–15,91 MB en 2. No se afirma que esos ficheros sean
  «sanos»: se afirma que **la tabla entera es pequeña**, que es lo que decide el
  coste de leerla.

Esto **cierra el flanco de H6**: T-01 ya la había descartado como causa principal
(las escrituras de A2 son de 1–4 tareas) y el layout medido lo confirma — no hay
una acumulación de ficheros pequeños que explique ningún tiempo. El caso con más
ficheros por byte son las dos dimensiones de oro (`dim_skill_list` y
`dim_currency`, 4 ficheros en 6,2 y 9,3 kB), y aun así son kilobytes.

### 3.2 ¿Crece el número de ficheros con los `MERGE`?

**No: el recuento es estacionario dentro del run y la aritmética de ficheros
cuadra.**

T-04 midió las ocho pasadas reales del run `879269828668729` (cuatro silvers ×
dos pases) con `operationMetrics`:

- **Pase 1:** `numTargetFilesAdded = 1`, `numTargetFilesRemoved = 0`,
  `numDeletionVectorsAdded = 1` → el fichero base sigue y se añade uno con las
  55–458 filas afectadas: **1 → 2 ficheros**.
- **Pase 2:** `numTargetFilesAdded = 1`, `numTargetFilesRemoved = 1`,
  `numDeletionVectorsAdded = 0`, y `numTargetBytesRemoved` **igual byte a byte**
  a `numTargetBytesAdded` del pase 1 (453.383 / 66.087 / 656.076 / 41.133 B) →
  se retira el fichero del pase 1 y se añade el del pase 2: **2 → 2 ficheros**.

Y esto es lo que dice el `DESCRIBE DETAIL` de hoy: **las cuatro `offers_*` tienen
2 ficheros**. La foto es posterior al run (los `lastModified` son de 07:10–07:12
y los conteos coinciden con su `target_rows`), así que el estado que se mide es
**consistente con** base + fichero del último pase; **la ruta de los ficheros no
se mide** (`[NO VERIFICADO]`). Que el «base» fuera **1 fichero** tras la
reconstrucción de A2 se deduce de la aritmética (`numFiles = 2` con `added = 1` y
`removed = 0`), no de una medición anterior al run: **`[NO VERIFICADO]`** el
`numFiles` de partida.

**Límite de la evidencia, y en qué dirección puede fallar.** Se han medido **dos
pases de un mismo run**, no la cadencia de semanas. Dentro de ese run el recuento
es estacionario (2 y 2). Lo que **no** está medido (`[NO VERIFICADO]`) es si el
**pase 1 de un run nuevo** vuelve a dejar `filesRemoved = 0`: si lo hiciera, el
recuento subiría **1 fichero por run** (2 → 3 → 4…), no por pase. Aun en ese peor
caso, con cadencia semanal el umbral de revisión de §6 (50 ficheros) tardaría
**~48 runs** en alcanzarse, y el `DESCRIBE DETAIL` tras el run de T-08 lo
resolverá **sin un run adicional**.

### 3.3 ¿Qué aporta Delta aquí?

Con `partitionColumns = []` y `clusteringColumns = []` en las 18 tablas, Delta
**no aporta poda por partición ni por clustering**: no hay ningún eje por el que
descartar ficheros antes de leerlos. Lo que aporta, y sí se usa, es otra cosa:

- **Transacciones y versiones:** el `MERGE` atómico, `DESCRIBE HISTORY`,
  time travel y `RESTORE` (el plan de rollback de silver de T-04/T-09).
- **`MERGE` con `WHEN MATCHED`:** el mecanismo del incremental de la 008; sin
  Delta habría que reescribir la tabla entera.
- **Deletion vectors:** permiten que un `MERGE` de 55–458 filas escriba **1
  fichero** sin reescribir el base (`numTargetRowsCopied = 0`, medido en T-04).
  Es la razón de que el layout se mantenga en 2 ficheros.
- **Estadísticas por fichero:** Delta guarda `min`/`max` de las primeras columnas
  por fichero, lo que permite *data skipping* **dentro** de una tabla. Con 1–6
  ficheros el *skipping* podría descartar **algún** fichero, pero **el ahorro no
  se ha medido** (`[NO VERIFICADO]`): con 1 fichero no hay nada que descartar y
  con 6 el máximo ahorro sería leer uno de seis.

En resumen: **Delta aporta aquí corrección y escrituras baratas, no velocidad de
lectura.** La velocidad de lectura la da el tamaño total: **98,37 MB** de catálogo,
con una tabla máxima de 33,09 MB (y 19,15 MB la mayor de oro).

## 4. Decisión y motivos

**Decisión (2026-10-30): no cambiar el layout de ninguna de las 18 tablas.**
Ni `OPTIMIZE`, ni `liquid clustering`, ni particionado. Ninguna tabla se toca,
ninguna propiedad se cambia y no se gasta ninguna escritura física.

El motivo de fondo es que **el problema que estas tres técnicas resuelven no
existe a esta escala**: no hay fragmentación problemática (42 ficheros en 18
tablas; el máximo son 6, en `linkedin` y `multi_site`, y la media más baja de
bronce es `infojobs` con 5 × 0,43 MB), no hay volumen (98 MB, 156k filas) y no
hay consultas con filtro selectivo que podar.

### 4.1 `OPTIMIZE`: no

`OPTIMIZE` compacta ficheros pequeños reescribiéndolos. Qué compactaría aquí y
qué costaría:

| Grupo | Estado hoy | Lo que haría `OPTIMIZE` | Coste | ¿Compensa? |
|---|---|---|---|---|
| `gold.fact_offers` | **1 fichero**, 26.302 filas, 19,15 MB | nada | — | **No hay nada que compactar** |
| `offers_*` (4 tablas) | **2 ficheros** de 0,76–7,96 MB | 2 → 1 fichero | **reescribe 1,5–15,9 MB por tabla** | No: gana 1 fichero de 0,76–7,96 MB |
| `companies_*` (4 tablas) | **1 fichero** de 12–237 kB | nada | — | No hay nada que compactar |
| `gold.dim_skill_list` / `dim_currency` | **4 ficheros**, 6,2 / 9,3 kB en total | 4 → 1 fichero | reescribe **6,2 / 9,3 kB** | No: la ganancia **no es medible** |
| `bronze.infojobs` | **5 ficheros**, 2,14 MB en total | 5 → 1 fichero | reescribe **2,14 MB** | No: el ahorro máximo son 4 aperturas de 0,43 MB |
| `bronze.linkedin` / `multi_site` | 6 ficheros de 2,24–5,52 MB | 6 → 1 | reescribe **13,4–33,1 MB** | No: la tabla entera son 13,4–33,1 MB |

Los dos argumentos que cierran la decisión:

1. **La tabla grande ya está en 1 fichero.** `fact_offers` (19,15 MB, 26.302
   filas) es la mayor de oro y su Parquet es el mayor de los dos exports
   (**33,34 MB** en PBI y **3,27 MB** en web), y **no tiene nada que compactar**.
   Las que sí tienen 2 ficheros son 1,5–15,9 MB: compactarlas reescribe **más
   bytes de los que ahorra** en la siguiente lectura.
2. **Donde la compactación sería «barata» en bytes, no hay ganancia que medir.**
   En `dim_skill_list` y `dim_currency` se reescribirían **6,2 y 9,3 kB**: el
   efecto de pasar de 4 a 1 fichero no es medible con los instrumentos que
   tenemos (los tiempos de oro se miden **por tarea**, 56 s, no por tabla;
   §8).

Y un coste que existe aunque los bytes sean pocos: **`OPTIMIZE` reescribe
los ficheros que compacta** —donde solo hay **1 fichero no reescribe nada**; solo
actúa en las tablas con **≥2 ficheros**— y corre dentro de la ventana del job
(RF-3 tiene un 31 % de margen, no margen infinito). Con **deletion vectors
activos en las 18 tablas**, se supone que la reescritura **materializa los
borrados** pendientes, pero **el efecto no se ha medido** (`[NO VERIFICADO]`). Es
una escritura física: exigiría **aprobación previa** de la persona y un run para
medir su efecto. No se ha ejecutado.

### 4.2 `liquid clustering`: no

`liquid clustering` (clustering líquido) organiza los ficheros por el valor de
una o varias columnas para que las consultas que **filtran por ellas** puedan
descartar ficheros. Aquí no hay esa consulta:

- **Oro y exports leen la tabla completa.** No hay ningún `WHERE` por columna de
  negocio en las lecturas que dominan el run: **oro se reconstruye entero** (la
  008 dejó fuera de alcance su incremental) y los dos exports vuelcan cada tabla
  completa a un Parquet de un fichero (§5).
- **El incremental filtra por `_ingest_date` en silver**, pero con **2 ficheros**
  por tabla el *data skipping* podría descartar **como mucho uno**; **el ahorro
  no se ha medido** (`[NO VERIFICADO]`).
  Además, particionar por fecha de lote crearía **una partición por lote** y
  clusterizar por ella agruparía por rangos de fecha; el eje de poda sería una
  fecha que **solo usa el incremental**, y el número y tamaño de los ficheros
  resultantes **no se han medido** (`[NO VERIFICADO]`). El paso `window` de T-03
  cuesta **5,7–6,3 s**, no es el cuello.
- **El coste de mantenimiento es real y continuo:** cada `MERGE` tendría que
  repartir por la clave de clustering y reescribir los ficheros necesarios para
  mantener el agrupamiento. Y con **1 fichero** no hay nada que descartar; con
  **2** podría descartar **como mucho uno**, y el ahorro **no se ha medido**
  (`[NO VERIFICADO]`). Para que el clustering sirviera habría que **partir la
  tabla en varios ficheros**, que es justo lo que `OPTIMIZE` evita. Se pagaría
  mantenimiento para un beneficio que exige empeorar el layout.
- Adoptarlo también tiene coste: exigiría `ALTER TABLE ... CLUSTER BY` y una
  reescritura del layout existente para que las filas quedaran agrupadas.

**De 19 a 44.928 filas por tabla no justifican ese mantenimiento.** La decisión
es «no» por ausencia de consulta que lo aproveche, no por falta de soporte de la
versión de Delta (que no se ha comprobado: `[NO VERIFICADO]`).

### 4.3 Particionado: no

Particionar crea un directorio por valor de la columna. Aquí no hace falta, y no
por el tamaño de los ficheros resultantes —que **no se ha medido**
(`[NO VERIFICADO]`)— sino porque el volumen y las consultas no lo piden:

- **Ninguna tabla supera 44.928 filas** (máximo `fact_offer_skills`) ni 33,09 MB
  (máximo `bronze.linkedin`), y **el catálogo entero son 98,37 MB**: cualquier
  partición sería menor que eso y quedaría **muy por debajo de la guía de ~1 GB
  por partición**. No se gana nada que el escaneo completo no dé ya.
- **No hay un filtro dominante por fecha en las lecturas de oro y exports** (leen
  completo: §4.2). El único filtro por fecha del sistema es la ventana de
  `_ingest_date` del incremental, y particionar por ella crearía **una partición
  por lote de ingesta**: `bronze.linkedin` tiene 15.220 filas repartidas en 6
  ficheros y `infojobs` 2.706 en 5; cuántos lotes hay **no se ha medido**
  (`[NO VERIFICADO]`).
- Si alguna vez hiciera falta podar por fecha, el orden correcto sería
  **clustering** (que reparte por valor sin crear directorios) y **antes** habría
  que medir una consulta que lo aproveche. Hoy no existe.

### 4.4 Deletion vectors: se quedan

Los DVs están **activos en las 18 tablas** y son parte de por qué el `MERGE` es
barato: el pase 1 marca las 55–458 filas actualizadas en el fichero base y
escribe un fichero nuevo, sin reescribir el base (`numTargetRowsCopied = 0`,
`filesRemoved = 0`).

- **El pase 1 deja 1 DV por tabla y el pase 2 reescribe el fichero del pase 1**
  (T-04): con esa ventana, **no se acumulan más allá de un pase**. Es una
  **inferencia** coherente con las métricas, no una lectura del contador.
- **`DESCRIBE DETAIL` no informa del número de DVs**, así que **no se afirma
  cuántos hay vigentes ahora** (`[NO VERIFICADO]`). Solo se sabe que la
  funcionalidad está activa.
- **No se justifica `OPTIMIZE` solo por purgarlos**: purgar DVs es un efecto
  secundario de compactar, y aquí la compactación no se paga por sí misma
  (§4.1). Leer una tabla con 1–2 DVs cuesta aplicar un *bitmap*; **el sobrecoste
  no se ha medido** (`[NO VERIFICADO]`), y el catálogo entero son 98,37 MB. Si en
  el futuro un `DESCRIBE HISTORY` mostrara decenas de DVs por tabla, el
  disparador sería ese número, no una corazonada.

## 5. Efecto en oro y exports

**Ninguno, y está medido.** Los exports no son copias de los ficheros Delta: se
escriben como Parquet nuevo, **un fichero por tabla** más `meta.json`. Por tanto
el layout de las tablas Delta **no se propaga** a lo que lee Power BI o la web, y
no cambiar el layout no cambia nada aguas abajo.

| Export | Ficheros | Bytes | Mayor fichero | Lectura |
|---|---|---|---|---|
| `gold/pbi_export_dev` | 7 | 34.241.722 | `fact_offers.parquet` **33.342.583** (97,4 %) | 1 Parquet por tabla + `meta.json` (341 B) |
| `gold/web_export_dev` | 5 | 3.689.553 | `fact_offers.parquet` **3.273.038** (88,7 %) | 1 Parquet por tabla + `meta.json` (480 B) |

- **Un fichero por tabla en los dos exports:** no hay ficheros que compactar en la
  salida, un Parquet por tabla. `pbi_export_dev` publica 6 tablas (7 ficheros con
  `meta.json`) y `web_export_dev` 4 tablas (5 ficheros con `meta.json`).
- **El tamaño del Parquet no es el de la tabla Delta.** `fact_offers` ocupa
  **19,15 MB** en Delta, **33,34 MB** en el export de PBI (**1,74×**) y
  **3,27 MB** en el de la web (**0,17×**). Que el export se genere **desde la
  tabla lógica** y no copiando ficheros Delta lo demuestra el tamaño: una copia
  mediría lo mismo que la tabla. La explicación de cada ratio (distinto conjunto
  de columnas y distinta compresión) **no se ha medido** (`[NO VERIFICADO]`), y
  tampoco **si un `OPTIMIZE` cambiaría el Parquet publicado** —podría alterar el
  orden de filas y la compresión—: `[NO VERIFICADO]`.
- **Tiempos de oro y exports:** el run de T-03 midió **56 s** de tarea de oro y
  **92 s** de exports, pero **no hay `timings_s` por tabla de oro** ni por
  fichero de export (`[NO VERIFICADO]`), así que no se puede afirmar cuánto de
  esos 56 s es lectura de `fact_offers`. Es la razón por la que no se ha medido
  la ganancia de compactar: no hay instrumento.

## 6. Qué haría revisar la decisión

La decisión se revisa si aparece **cualquiera** de estos disparadores, medidos con
el mismo `DESCRIBE DETAIL` (**sin un run adicional**) o con los
`timings_s`/`Query Profile` que ya publica el job:

1. **Más de 50 ficheros en una tabla**, o **más de 10 en cualquiera de las
   cuatro `offers_*`**. Hoy el máximo es 6 y 2 respectivamente.
2. **Media por fichero < 1 MB en una tabla que se lee completa y que supere
   10 MB en total.** Hoy hay medias por debajo de 1 MB (`infojobs` 0,43 MB,
   `offers_multi_site` 0,76 MB, `offers_infojobs` 0,99 MB, y las dos dimensiones
   de oro, 1,55 y 2,33 kB), pero **ninguna de esas tablas llega a 10 MB**, que es
   la condición que convierte el número de ficheros en coste.
3. **Una consulta con filtro selectivo dominante** —que descarte ≥90 % de las
   filas por una columna— en oro, en un export o en el camino del incremental, y
   que aparezca en el `Query Profile`/`timings_s` como coste relevante. Ahí
   `liquid clustering` (antes que particionar) pasaría a tener argumento.
4. **Crecimiento de volumen ≥10×** (más de ~450.000 filas o ~330 MB en una tabla,
   frente a las 44.928 filas y 33,09 MB de hoy): a partir de ahí el tamaño de
   fichero vuelve a importar y `OPTIMIZE` tendría algo que compactar de verdad.
5. **Deletion vectors acumulados**: si una medición muestra decenas de DVs
   vigentes en una tabla (hoy no se puede ver con `DESCRIBE DETAIL`), purgarlos
   pasaría a ser un motivo propio.

Mientras ninguno se cumpla, **no se toca el layout**: repetir la medición no
exige un run adicional; cambiar el layout sí (y exige aprobación previa).

## 7. Coste aceptado

Lo que se acepta por no cambiar nada, con su tamaño:

- **5 ficheros en `bronze.infojobs` en vez de 1** (2,14 MB en total; 0,43 MB de
  media). Coste no medido, acotado por el tamaño de la tabla.
- **2 ficheros en cada `offers_*` en vez de 1** (1,5–15,9 MB por tabla). Es el
  estado natural del `MERGE` con DVs y **no crece con los pases de un mismo
  run** (§3.2).
- **4 ficheros en `dim_skill_list` y `dim_currency`** (6,2 y 9,3 kB). Cuatro
  aperturas de ficheros de 1,55–2,33 kB.
- **Sin poda por partición ni clustering**: todas las lecturas parten de la tabla
  completa (el *data skipping* por estadísticas podría descartar algún fichero,
  pero **el ahorro no se ha medido**). Sobre **98,37 MB**, y con la mayor tabla de
  **1 fichero** (`fact_offers`, 19,15 MB) —la mayor por bytes es
  `bronze.linkedin`, 33,09 MB en 6—, el escaneo completo está acotado por esos
  98,37 MB.
- **DVs activos** en las 18 tablas, con el sobrecoste de aplicar el *bitmap* al
  leer; **no medido** y acotado por el tamaño de la tabla (el único DV que añade
  un pase, §4.4).
- **Céntimos** de clúster de lectura (§ cabecera): el precio de saberlo, no de
  cambiarlo.

## 8. Qué no se ha medido

- **Tiempo de las consultas de oro y de los exports por tabla.** El run de T-03
  da **56 s** de tarea de oro y **92 s** de exports, pero no hay `timings_s` por
  tabla de oro ni por fichero publicado. Sin ese dato no se puede cuantificar la
  ganancia de compactar; solo acotarla por bytes. `[NO VERIFICADO]`
- **Número de deletion vectors vigentes por tabla.** `DESCRIBE DETAIL` no lo
  informa y no se ha consultado `DESCRIBE HISTORY` para contarlos.
  `[NO VERIFICADO]`
- **El efecto real de un `OPTIMIZE`.** **No se ha ejecutado**: es una escritura
  física y exige aprobación previa de la persona (AGENTS.md) además de un run
  para medirla. Todo lo que se dice de su coste son bytes a reescribir, no
  segundos. `[NO VERIFICADO]`
- **La distribución de tamaños dentro de una tabla.** `minFileBytes` y
  `maxFileBytes` vienen **`None`** en las 18 filas; solo hay media aritmética.
- **La causa de los 4 ficheros de `dim_skill_list` y `dim_currency`.** Hipótesis
  (escritura sin `coalesce` desde un frame con 4 particiones no vacías), no
  comprobada. `[NO VERIFICADO]`
- **La causa de los 5–6 ficheros del bronce.** Coherente con una escritura por
  lote de ingesta, pero el linaje de ficheros no se ha mirado. `[NO VERIFICADO]`
- **Que el patrón `added=1, removed=1` se mantenga en muchos runs seguidos.**
  Medido en **dos pases** (T-04); la cadencia larga es extrapolación hasta el
  run de T-08. `[NO VERIFICADO]`
- **La tarifa de all-purpose** para convertir los 2–3 min del clúster de lectura
  en euros exactos. `[NO VERIFICADO]`
- **Si el runtime soporta `liquid clustering`.** No se ha comprobado la versión
  de Delta/DBR del clúster; la decisión no depende de ello (se descarta por
  ausencia de consulta), pero conviene saberlo si algún día se revisa.
  `[NO VERIFICADO]`

## 9. Compute y concurrencia (T-06)

> **Qué se decide aquí y con qué.** Si el compute de producción se queda como
> está (RF-6) y cómo se reparte el trabajo: tipo de nodo, single node o
> multi-nodo, Photon, serverless, concurrencia, particiones de *shuffle* y
> `timeout`. **La decisión es «no cambiar»**, y se sostiene en el run
> `879269828668729` del 2026-10-30 (8/8 en verde, **927,1 s**), en los `timings_s`
> y las `merge_metrics` que ese run dejó en su resumen (T-02/T-03b) y en la tabla
> de opciones de `plan.md` §7.2. **No se ha lanzado ningún run para esta tarea y
> no se ha abierto el SQL warehouse**: los números se leen del run ya pagado en
> T-03, por la API de runs. **Coste de T-06: 0 €.**
>
> **Aviso de lectura.** Todo lo que es **estimación** va marcado como tal; lo que
> no se ha medido en este repositorio va con `[NO VERIFICADO]`. La decisión se
> toma con **un** run medido, no con una comparación de nodos: no hay ninguna
> medición de `D8ds_v4`, multi-nodo ni serverless en este repo (§9.9).

### 9.1 Decisión (2026-10-30)

Se mantiene la configuración actual, sin tocar ningún `.yml`:

| Clave | Valor que se queda | Por qué |
|---|---|---|
| `node_type_id` | `Standard_D4ds_v4` **single node** (4 vCPU / 16 GiB) | El run cabe: 8/8 en 927 s y los cuatro silvers ya corren en paralelo sobre 4 vCPU (§9.2) |
| `is_single_node` | `true` | Hay trabajo repartible (398 s, §9.4), pero el ahorro **estimado** (≤ ~200 s, ~20 %) **no compensa el 2× de coste** y no hay medición que lo respalde |
| `kind` | `CLASSIC_PREVIEW` | Es el clúster de jobs clásico del proyecto; no se cambia |
| `spark_version` | `17.3.x-scala2.13` | La del código validado en T-03/T-03b; cambiarla abriría otra variable |
| `runtime_engine` | `STANDARD` (**Photon fuera**) | Photon no se ha medido en este repo; activarlo sería otro cambio sin evidencia |
| `performance_target` | `PERFORMANCE_OPTIMIZED` | Se mantiene |
| `max_concurrent_runs` | `1` | El job es semanal; dos runs a la vez compartirían catálogo y exports |
| Concurrencia interna | **cuatro silvers en paralelo** tras el bronce | Su ventana es 298,5 s; en secuencia la aritmética se iría a ~1.760 s (§9.4) |
| `spark.sql.shuffle.partitions` | `"8"` en el job cluster (commit `61da8c5`) | Medido: 200 → 8 sacó el incremental del `TIMEDOUT` (T-03) |
| AQE | **activo con el valor por defecto** (sin `spark_conf`) | El run lo confirma en su `config` (§9.5) |
| `timeout_seconds` (job) | **`1800`** | No se sube: lo prohíbe la spec (`plan.md` §6.7 y §10) |
| `timeout_seconds` (tareas) | `0` | Sin tope por tarea; el tope del job acota el run (§9.6) |

**No cambia ningún fichero `.yml`.** La única clave de compute que se fijó en su
día —`spark.sql.shuffle.partitions: "8"`— ya estaba en
`resources/elt.job.yml` desde T-03 (commit `61da8c5`, aprobado el 2026-10-28).
Por eso **T-06 no necesita `bundle validate` ni aprobación previa pendiente**: no
hay diff de `.yml` que validar.

`plan.md` §7.2 preveía tomar la decisión **con la medición de T-07/T-08**. La
persona decide el 2026-10-30 **mantener la configuración** con la medida
disponible (el run de T-03) y **revalidarla** en T-07/T-08 con los mismos
criterios (§9.8): si alguno falla, la decisión se reabre con la medida del run
que falle.

### 9.2 Camino crítico medido (run `879269828668729`, 2026-10-30)

Run único de dev, ventana 2026-10-24…2026-10-28, `repeat_window=true`, **8/8
tareas en verde**, **927,1 s** (15 m 27 s). Tiempos **relativos al inicio del
run**, leídos de la API de runs (`start_time`, `end_time`, `setup_duration` y
`execution_duration` de cada tarea). **Ventana** es de inicio a fin de la tarea;
**ejecución** es `execution_duration`: la ventana incluye el `setup_duration` de
la tarea (2–3 s), así que **no se mezclan**.

| Tramo | Ventana (s) | Ejecución (s) | Qué es |
|---|---|---|---|
| Bronce | 0,0–517,5 (**517,5 s**) | **134 s** | **383 s de `setup_duration` (arranque) + 134 s de ejecución** |
| Cuatro silvers (en paralelo) | 517,8–816,3 (**298,5 s**) | máx. **295 s** | linkedin 295, indeed 290, multi_site 288, infojobs 255 |
| Oro | 817,0–875,7 (**58,7 s**) | **56 s** | Reconstrucción completa de oro |
| Dos exports (en paralelo) | 876,0–925,8 (**49,8 s**) | máx. **47 s** | report 47 s (ventana 49,8 s); web 45 s (ventana 48,7 s) |
| **Suma de ventanas** | **924,5 s** | — | + **1,3 s** de huecos entre tareas = **925,8 s** de la primera a la última |

**La aritmética, para que se pueda reproducir:**

- **Ventanas:** 517,5 + 298,5 + 58,7 + 49,8 = **924,5 s**.
- **Huecos entre tareas:** bronce→silvers 517,8 − 517,5 = **0,3 s**;
  silvers→oro 817,0 − 816,3 = **0,6 s**; oro→exports 876,0 − 875,7 = **0,4 s**
  ⇒ **0,3 + 0,6 + 0,4 = 1,3 s** (exactos: 0,348 + 0,611 + 0,379 = 1,338 s).
- 924,5 + 1,3 = **925,8 s** desde el inicio de la primera tarea (0,0 s) hasta el
  fin de la última.
- **927,096 − 925,8 = 1,3 s** de arranque/cierre del run ⇒ **927,1 s**
  (`run_duration` = 927.096 ms). No hay 12 s de huecos: los huecos son **1,3 s**.

Tres lecturas de la tabla:

- **RF-3 se cumple con 31,3 % de margen sobre el gate de 1350 s y 48,5 % sobre
  los 1800 s de producción.** 927,1 s ≤ 1350 s (el gate de `plan.md` §3, que ya
  descuenta 450 s de los 1800 s): quedan **422,9 s** hasta el gate y **872,9 s**
  hasta los 1800 s del job.
- **El cuello no es el incremental.** Bronce son **517,5 s, el 56 % del run**, y
  de ellos **383 s son arranque del clúster**: más que el tramo entero de las
  cuatro silvers juntas (298,5 s de ventana). El incremental ya no decide el
  tiempo del run.
- **Las cuatro silvers caben en paralelo y en 4 vCPU.** Su ventana es **298,5 s**
  y la ejecución más larga **295 s** (295, 290, 288 y 255 s); no hay señal de que
  compitan por CPU (§9.9: no se ha revisado el *spill* de este run). Los **92 s**
  de export de T-03 son la **suma** de las dos tareas; en el camino crítico solo
  cuentan **49,8 s de ventana** (47 s de ejecución) porque corren a la vez.

### 9.3 Tabla de opciones: veredicto y motivo

Salida de `plan.md` §7.2, ordenada de la decisión a los descartes:

| Opción | Perfil | Coste relativo/run | Veredicto y motivo |
|---|---|---|---|
| `D4ds_v4` single node (actual) | 4 vCPU / 16 GiB | **0,23 € estimados por duración** | **Se queda**: 927 s, 8/8, RF-3 con **31,3 % de margen** sobre el gate de 1350 s y **48,5 % sobre los 1800 s de producción** |
| `D2ds_v6` single node | 2 vCPU / 8 GiB | ~0,13 € | **Descartado**: en la 009 **no completó** (`DRIVER_NOT_RESPONDING` a los 18,2 min, run `954013261343017`); que con la mitad de núcleos el tramo de silvers (295 s) y los `MERGE` serían más lentos es una **inferencia**, no una medida de CPU |
| `D8ds_v4` single node | 8 vCPU / 32 GiB | ~0,48 € | **Descartado hoy**: el 41 % del run (383 s) es arranque de clúster y se **supone** que no mejora con más núcleos (`[NO VERIFICADO]`); el tramo paralelizable (silvers 295 s + oro 56 s + exports 47 s = 398 s) como mucho baja a la mitad ⇒ ahorro ≤ ~200 s (~20 %) por **2× coste**; sin medición que lo respalde y con el presupuesto de dev agotado (3/3) |
| Multi-nodo (driver + workers) | 1–2 workers | según nodos | **Descartado hoy**: mismo argumento que `D8ds_v4` (más núcleos para un 398 s paralelizable) con más superficie operativa; el cuello es el arranque y el bronce |
| Serverless (jobs) | gestionado | DBU superior, sin *spike* | **Descartado hoy**: coste por run sin medir (el *spike* sigue pendiente en la 009) y ninguna medición de este repo; no se puede justificar con evidencia |

### 9.4 Por qué no se sube el nodo (ni se añaden workers): el techo está en el arranque

El argumento es aritmético y está **acotado por arriba**; es una **estimación, no
una medición** de un run con `D8ds_v4` o con workers.

- **Lo que se puede acelerar con más núcleos son 398 s de 927** (43 %): silvers
  295 s + oro 56 s + exports 47 s. Los otros **517,5 s** (56 %) son bronce, y
  **383 s de ellos son arranque**, que se **supone** independiente del tamaño del
  nodo (`[NO VERIFICADO]`, §9.9).
- **El mejor caso posible sería bajar esos 398 s a la mitad**, suponiendo
  paralelización perfecta (no lo es: hay pasos secuenciales, `MERGE` y un solo
  driver). Ahorro **≤ ~200 s (~20 %)**: el run quedaría en ~730 s.
- **A 2× la tarifa, ese run más corto seguiría costando más**: ~0,36–0,38 €
  (0,48 € × 730/927, **estimación**) frente a los **0,23 €** estimados del run
  actual. Se pagaría el doble durante los 383 s de arranque para no ganar nada en
  ellos.
- **Multi-nodo no cambia el cálculo**: añade núcleos a la misma fracción
  paralelizable (398 s) y más superficie operativa (workers que arrancan, se
  pierden o se infrautilizan) para un driver que ya no es el cuello.
- **La concurrencia se queda en paralelo, y también es aritmética**: serializar
  las cuatro silvers sustituiría 295 s por la **suma** de sus duraciones
  (255 + 288 + 290 + 295 = **1.128 s**), y el run se iría a **~1.760 s** —por
  encima del gate de 1.350 s y a 40 s del `timeout`—. **Estimación** de suma
  simple, sin medir el run secuencial.

Mientras no haya una medición que muestre lo contrario (T-07/T-08, §9.8), **no
se paga el doble por un ahorro estimado que ni siquiera baja el coste**.

### 9.5 Configuración definitiva: particiones y AQE

La configuración de Spark vive en el **job cluster** de `resources/elt.job.yml`,
que **comparten las ocho tareas** (todas declaran `job_cluster_key: tm-lens-job`),
así que se fija una vez y aplica a bronce, silvers, oro y exports:

| Clave | Valor | Dónde |
|---|---|---|
| `spark.sql.shuffle.partitions` | `"8"` | `job_clusters[].new_cluster.spark_conf` (commit `61da8c5`) |
| `spark.sql.adaptive.enabled` | **sin declarar** ⇒ valor por defecto del runtime (**activo**) | — |

**Prueba de la config efectiva: el propio run.** El resumen de
`879269828668729` publica el campo `config`:

```
{"shuffle_partitions":"8","adaptive_enabled":"true","overrides":{"shuffle_partitions":"8"}}
```

- `shuffle_partitions = "8"`: el valor llega al clúster (no se quedó en el
  `.yml`).
- `adaptive_enabled = "true"`: AQE está activo **sin override**, es decir, por
  defecto del runtime.
- `overrides` solo lista `shuffle_partitions`: es la única propiedad que el job
  sobrescribe. Esto **cierra el `[NO VERIFICADO]` de T-01** sobre la config
  efectiva.

**Por qué 8 y no 16.** Con 8 se han medido los tres runs que sostienen la
decisión (808 s, 974 s y 927 s) y es lo que sacó el incremental del `TIMEDOUT`
(200 → 8, T-03). **`16` no se ha medido en este repo** (`[NO VERIFICADO]`):
cambiarlo ahora sería abrir otra variable sin necesidad, y el run ya cumple RF-3.

### 9.6 `timeout_seconds`: se queda en 1800 s

- **A nivel de job, `timeout_seconds: 1800` se queda.** No se sube: la spec lo
  prohíbe como solución (`plan.md` §6.7 y la tabla de riesgos de §10: «subir el
  timeout y dar el problema por resuelto» → **prohibido**). El gate no es
  «termina algún día» sino **≤1350 s** (RF-3), y hoy el run hace 927 s: subir el
  tope no compraría nada y ocultaría la señal si empeora.
- **Las tareas llevan `timeout_seconds: 0`** (sin tope por tarea). Se acepta
  porque el tope del job **acota el run entero**: un run que se pase de 1800 s
  muere como `TIMEDOUT` (le pasó a B, B2 y B3), que es justo la señal que se
  quiere. **No se propone cambio.**

### 9.7 Coste por run

- **0,17–0,23 € por run** según duración (**808–974 s**): 808 s → **0,17 €**,
  974 s → **0,20 €** y 927 s → **0,23 €**. La referencia de **~0,24 €/run** de
  `plan.md` §7.2 corresponde a **15–19 min** y aquí se usa solo como **orden de
  magnitud**. Las cifras son **estimaciones por duración** con el método de
  `docs/costs.md` (tarifa de jobs clásicos); no se ha leído una factura por run
  (§9.9).
- **El arranque son 383 s, el 41 % del reloj.** Se **supone** que no depende del
  tamaño del nodo (`[NO VERIFICADO]`: un solo run medido, y con `D4ds_v4`): si se
  cumpliera, se pagaría igual en un `D4ds_v4` que en un `D8ds_v4`, solo que a
  doble tarifa. Es la razón económica del «no cambiar».

### 9.8 Cuándo se revisa

La decisión **se revisa en T-07/T-08** (runs A y B en dev) **con los mismos
criterios**; si alguno falla, **se reabre**:

1. **El run completo pasa de 1350 s** (gate de RF-3). Hoy: **927 s**.
2. **Un pase de silver pasa de 226 s** (invariante de RF-2a). Hoy:
   **89,4–138,2 s** (caliente–frío).
3. **La Spark UI muestra *spill* a disco, evicción de memoria o GC dominante**
   en un run. Hoy **no se ha revisado** el *spill* de este run
   (`[NO VERIFICADO]`).

Si ninguno se cumple, la configuración pasa a producción **tal cual** en T-09.
La revisión en T-07/T-08 **exige presupuesto aprobado**: a 2026-10-30 los
**3 runs** están consumidos (T-03 A/B/T-03b). Comparar nodos exigiría **uno de
los runs del presupuesto** (el de reserva, ya consumido por T-03b) con
aprobación explícita.

### 9.9 Qué no se ha medido

- **Tiempos de `D8ds_v4` en este repo.** No se ha corrido. El ahorro de ≤200 s
  de §9.4 es una **estimación aritmética** sobre el reparto medido del run, no
  una medición. `[NO VERIFICADO]`
- **Tiempos de multi-nodo.** Tampoco se ha corrido; se descarta por el mismo
  argumento estimado. `[NO VERIFICADO]`
- **Serverless (jobs): tiempo y DBU.** Sin run y sin *spike*; el «DBU superior,
  sin *spike*» de `plan.md` §7.2 **no es una medición de este repo**. `[NO VERIFICADO]`
- **Estabilidad de los 383 s de arranque.** Se midieron **una vez** (un run); los
  runs históricos muestran arranques de **2–9 min** (`plan.md` §3), así que la
  cifra **no es una constante** y puede comerse margen sin que el código cambie.
  `[NO VERIFICADO]`
- **Que los 383 s de arranque no dependan del tamaño del nodo.** Es un
  **supuesto**: solo hay un run medido y con `D4ds_v4`; no se ha comparado el
  `setup_duration` entre tipos de nodo. `[NO VERIFICADO]`
- **El *spill*, la memoria pico y el GC del run `879269828668729`.** No se ha
  revisado su Spark UI; es el tercer umbral de revisión y **no está comprobado
  todavía**. `[NO VERIFICADO]`
- **Si Photon ayudaría o no.** `runtime_engine: STANDARD` es la configuración
  actual y validada, no el resultado de una comparación. `[NO VERIFICADO]`
- **El «no completó» de `D2ds_v6`** es evidencia **heredada de la 009**; no se
  ha repetido aquí. `[NO VERIFICADO]`
- **La factura por run.** Las cifras de §9.7 son estimaciones por duración; no se
  ha leído Azure Cost Analysis ni la Account Console run a run. `[NO VERIFICADO]`
