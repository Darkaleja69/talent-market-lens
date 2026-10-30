# Diseño: layout físico (T-05) y compute (T-06)

> **Alcance de este documento.** Las dos últimas decisiones de la spec 010 que no
> son código: **T-05** (layout físico: `OPTIMIZE`, `liquid clustering`,
> particionado o **no cambiar**) y **T-06** (compute y concurrencia, que
> añadirá aquí su sección). **Hoy solo existe la parte de layout**; la de compute
> se escribirá al cerrar T-06. **Este documento no cambia la spec**, la cumple:
> RF-7 exige la decisión documentada «aunque la decisión sea no cambiar nada».
>
> **Fecha de la medición:** 2026-10-30, entre las 07:06 y las 07:12 (hora de
> `lastModified` de las 18 tablas).
>
> **Fuente:** `DESCRIBE DETAIL` de las **18 tablas** del catálogo
> **`job_offers_dev`** ejecutado en una **celda de cuaderno** de un clúster
> **all-purpose de lectura**, más el recuento de ficheros y bytes de los dos
> exports. Salida **literal** en
> `docs/media/performance/2026-10-30-describe-detail-dev.txt`. **Sin runs, sin
> SQL warehouse** (0 sesiones de SQL Editor).
>
> **Coste:** **céntimos**. El clúster de lectura estuvo abierto del orden de
> **2–3 min** (estimación de la sesión; **duración exacta no registrada**). La
> referencia de `docs/costs.md` para un `D4ds_v4` en *jobs* (~0,75 €/h ≈
> 1,25 céntimos/min) daría **~3–4 céntimos** para 2–3 min; que la tarifa de
> **all-purpose** sea superior a la de *jobs* es un **supuesto** que **no se ha
> medido** (`[NO VERIFICADO]`), así que la cifra de jobs es una **cota inferior**
> y en `docs/costs.md` queda como **estimación en céntimos**, no como importe
> cerrado.
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
