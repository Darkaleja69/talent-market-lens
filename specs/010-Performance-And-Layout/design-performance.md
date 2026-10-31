# Diseño: diagnóstico de rendimiento del incremental (T-01)

> **Alcance de este documento.** T-01 de la spec 010: atribuir el coste del
> incremental de las cuatro silvers **sin lanzar runs y sin tocar código**, a
> partir de la Spark UI de A2, B, B2 y B3, del inventario de acciones de
> `_run_once` (`plan.md` §2.2) y de la aritmética de los números medidos.
> **0 €, 0 runs, 0 sesiones SQL.**
>
> **Revisión 4 (2026-10-28), con la Spark UI de A2 y las stages completadas y los
> ejecutores de B3.** Cambia el diagnóstico: **el número de acciones no es el
> diferencial** (A2 hace **57** consultas y B3 49), sino el **coste por consulta**
> (15 s en A2 frente a 84–96 s en el incremental) causado por las **tareas por
> acción**: 1–4 tareas por stage en A2, **200** en el incremental, y **10.790
> tareas** en B3 para ~31k filas. H6 (ficheros pequeños) pasa a **descartada como
> causa principal** y la palanca es `spark.sql.shuffle.partitions`. Se retiran o
> degradan los cambios que la revisión anterior presentaba como palanca (menos
> acciones, `OPTIMIZE`).
>
> **Revisiones anteriores.** La 3 corrigió las duraciones de silver (22 m 04 s en
> B3 y ~27 min en B, duración de tarea, no run-relative) y los múltiplos (5,9× y
> 7,2×). La 2 corrigió la descomposición «×4 por concurrencia» (A2 también corre
> las cuatro silvers en paralelo). Ambas correcciones siguen vigentes.
>
> **Advertencia de método.** Lo que las pantallas no enseñan **no se afirma**;
> todo lo no medido va marcado `[NO VERIFICADO]`.
>
> **Este documento no cambia la spec.** Las discrepancias con `plan.md` o
> `tasks.md` se señalan en §2.8 sin editarlos.

## 1. Fuentes y método

### 1.1 Fuentes usadas

| Fuente | Qué aporta | Límite |
|---|---|---|
| `docs/media/performance/2026-10-27-a2-sql.png` | **A2** (`207866361330883`): 57 consultas, media **15 s**, 0 fallidas; últimas consultas de 0,3–1 s (export) | Recorte: 8 filas de la tabla |
| `docs/media/performance/2026-10-27-a2-stages.png` | A2 stages: **266 completadas, 220 skipped, 2 fallidas**; últimas stages (11:20): 1–3 tareas, 0,05–0,6 s, `Input` 22,5–293,4 KiB, `Output` 2,7–471,4 KiB | Solo la última página |
| `docs/media/performance/2026-10-27-a2-overwrites.png` | A2 stages de `inc.run` y `companies_df.write.mode("overwrite")` (11:17:28–11:18:15): **1–4 tareas**, **0,1–3 s**, `Output` 43,1 KiB–2,1 MiB; una stage 1/1 de 3 s con **13,4 MiB de entrada y 35,8 MiB de shuffle write**; una 4/4 de 0,7 s leyendo la silver | Solo un tramo de 47 s |
| `docs/media/performance/2026-10-27-b3-sql.png` | B3 (`1015448868873733`): 49 consultas, media **1,6 min**, 3 fallidas | Recorte: 3 filas |
| `docs/media/performance/2026-10-27-b3-stages.png` | B3 *Failed Stages* (4 stages, **200 tareas** cada uno) | Sin *details* ni *Tasks* |
| `docs/media/performance/2026-10-27-b3-completed-stages.png` | B3 stages completadas: **137**, **508 skipped**, 4 fallidas; stages 47–56 (23:10:14–23:10:24): **1–4 tareas**, **0,2–6 s**, `Input` 891 KiB–30,5 MiB, `Shuffle Write` **46,0 B–47,7 MiB** | Solo la página 1 |
| `docs/media/performance/2026-10-27-b3-executors.png` | B3 executors: **1 ejecutor (driver), 4 cores**, **10.790 tareas completadas** (10.795 totales), *task time* 18,3 h (GC 8 s), `Input` 731,6 MiB, `Shuffle Read`/`Write` 79,7 MiB, **0 B de storage y de disco** | Agregado de todo el run |
| `docs/media/performance/2026-10-27-b2-sql.png` | B2 (`1056455747512203`): 55 consultas, media **1,4 min**, 4 fallidas | Recorte |
| `docs/media/performance/2026-10-27-b2-stages.png` | B2 *Failed Stages* (4 stages, 200 tareas) | Ídem |
| `specs/008-Delta-Incremental/evidence.md` | Duración de los runs, bronce, ausencia de escrituras, medición local de ~45 s por pase y aviso de codegen | Sin resumen JSON de las silvers |
| `databricks_notebooks/Silver_Incremental.py`, `Silver_Indeed.ipynb`, `Enrich_Job_Offers_Dataframes.py` | Inventario de acciones y composición de `build_offers` | Lectura de código |
| `resources/elt.job.yml`, `plan.md` §2.1 | `D4ds_v4` single node (4 vCPU / 16 GiB), `STANDARD`, `17.3.x-scala2.13`, `timeout_seconds: 1800`, cuatro silvers en paralelo | Configuración, no medición |
| `docs/costs.md` | Duración y coste de los runs | Coste estimado por duración |

### 1.2 Método

1. Transcribir las cifras de cada captura (no fiarse del resumen previo).
2. Comparar **A2 (rebuild) contra B/B2/B3 (incremental)** en las tres magnitudes
   que la UI permite: consultas, duración por consulta y tareas.
3. Cerrar la aritmética: tareas × coste por tarea frente a la capacidad de 4
   núcleos y a la duración del run.
4. Contrastar H1–H6 de `plan.md` §4 y reordenar los cambios.

### 1.3 Qué no se ha podido ver

- La tabla de **tareas de un stage** (mínimo/mediana/máximo, cola de
  planificación, *spill*): el desglose fino de por qué una tarea cuesta ~0,7 s.
- La **fracción cacheada durante** el run (la vista de executors es del final, con
  las cachés ya liberadas).
- El **event log** y el plan físico (`explain`) de las consultas.
- Los **valores efectivos** de `spark.sql.shuffle.partitions`, AQE y
  `spark.sql.files.*`: no están en el bundle. **Es el dato que falta más
  importante** (§6).
- El **total de tareas de A2**: la vista no lo publica y las capturas solo
  muestran dos tramos de stages (§3.3).
- Qué tabla o frame lee cada uno de los 8 stages fallidos.

## 2. Hechos medidos

### 2.1 Los runs

| Hecho | A2 (rebuild) | B2 | B3 |
|---|---|---|---|
| Id | `207866361330883` | `1056455747512203` | `1015448868873733` |
| Resultado | **SUCCESS** 8/8 | `TIMEDOUT` | `TIMEDOUT` |
| Duración del run | **14 m 39 s** | 30 m 37 s | 30 m 30 s |
| Bronce | 9 m 07 s | no registrado | **8 m 06 s** |
| **Duración de la tarea de silver** (no run-relative) | **2 m 47 s – 3 m 46 s** | sin duración registrada | **22 m 04 s** |
| Escrituras | sí | ninguna | ninguna |
| Consultas SQL/DataFrame (clúster) | **57** | **55** | **49** |
| Media por consulta | **15 s** | **1,4 min** (84 s) | **1,6 min** (96 s) |
| Consultas fallidas | **0** (0,0 %) | 4 (7,3 %) | 3 (6,1 %) |

El run **B** (`186230462263213`, 30 m 46 s, bronce 3 m 29 s) da el segundo punto
de silver del incremental: **~27 min** de tarea. B2 no registró la suya.

Nota de simetría: la tarea de silver incluye `inc.run` **y** el bloque
`companies_df.write.mode("overwrite")` —la captura de A2 los muestra
intercalados—, así que el listón `2 m 47 s–3 m 46 s` incluye ese trabajo extra y
es **holgado** frente a un `T_inc` que salga de `timings_s` (§3.1).

### 2.2 A2: el rebuild hace muchas consultas y todas baratas

| Hecho | Valor |
|---|---|
| Consultas | **57** en **14 m 39 s** ⇒ 855 s de consulta acumulada |
| Media | **15 s** (las últimas, de export, 0,3–1 s) |
| Stages | **266 completadas**, 220 *skipped*, **2 fallidas** (reintentadas: el run terminó en verde) |
| Tareas por stage | **1–4** (3/3, 1/1, 1/1, 1/1, 3/3 en las últimas; 4/4 al leer la silver; 1/1 y 2/2 en los `overwrite`) |
| Duración por stage | **0,05–3 s** |
| Bytes | `Input` 17,2 KiB–**13,4 MiB**; `Output` de las escrituras **43,1 KiB–2,1 MiB**; `Shuffle Write` hasta **35,8 MiB** en una stage de 3 s |

Dos consecuencias directas:

- **Los `overwrite` de A2 escriben con 1–4 tareas** ⇒ las tablas quedan con
  **pocos ficheros** (1–4 por escritura). La hipótesis de «muchos ficheros
  pequeños» pierde su base (§3.4).
- Una stage de **13,4 MiB de entrada** con **1 tarea** en 3 s mueve ~4,5 MiB/s en
  una sola tarea: el trabajo real de datos no es el cuello en este clúster.

### 2.3 B3: las primeras stages de silver también van con 1–4 tareas

Stages completadas 47–56 (23:10:14–23:10:24), las primeras del `inc.run`:

| Stage | Duración | Tareas | Input | Shuffle Write |
|---|---|---|---|---|
| 47 | 0,2 s | 1/1 | — | 67,0 B |
| 48 | 0,7 s | 4/4 | — | — |
| 49 | 0,7 s | 4/4 | — | — |
| 50 | 0,7 s | 4/4 | — | — |
| 51 | 0,8 s | 4/4 | — | — |
| 52 | 5 s | 2/2 | **30,5 MiB** | **47,7 MiB** |
| 53 | 3 s | 2/2 | 2,1 MiB | 646,8 KiB |
| 54 | 2 s | 2/2 | 12,9 MiB | 28,4 MiB |
| 55 | 1 s | 2/2 | 891,3 KiB | 646,8 KiB |
| 56 | 6 s | 3/3 | 2.034,4 KiB | 46,0 B |

Balance del run (vista de executors): **137 stages completadas, 508 skipped, 4
fallidas**; **1 ejecutor (el driver), 4 cores**; **10.790 tareas completadas**
(10.795 totales: las 5 que mataron las stages fallidas), `Input` **731,6 MiB**,
`Shuffle Read` y `Write` **79,7 MiB** cada uno, **0 B de storage y de disco**
(cachés liberadas al terminar).

Las primerísimas stages del silver **no** tienen 200 tareas: tienen 1–4. Las 200
aparecen después (§3.4). Y el `Input` total (731,6 MiB) es ~23× el bronce (~31
MB): el run **relee** mucho, pero lo hace en tareas que cuestan ~0,7 s cada una.

### 2.4 Los ocho stages fallidos (los 200-task)

| Run | Stage | Descripción | Enviada (UTC) | Duración | Tareas hechas/total | Input | Shuffle Read | Shuffle Write |
|---|---|---|---|---|---|---|---|---|
| B2 | 643 | `MERGE operation - materialize source` | 11:52:25 | 2,4 min | 194/200 (1 killed) | 38,5 KiB | — | — |
| B2 | 682 | `MERGE operation - materialize source` | 11:53:42 | 1,1 min | 90/200 (2 killed) | 2,5 MiB | — | — |
| B2 | 709 | `inc.run(...)` | 11:54:39 | 10 s | 98/200 (1 killed) | 33,9 KiB | — | 5,5 KiB |
| B2 | 714 | `inc.run(...)` | 11:54:51 | 2 s | 5/200 (4 killed) | 88,8 KiB | — | 283,0 B |
| B3 | 607 | `inc.run(...)` | 23:29:09 | 2,6 min | 180/200 (1 killed) | 2,9 MiB | — | 10,2 KiB |
| B3 | 619 | `inc.run(...)` | 23:29:23 | 2,4 min | 199/200 (1 killed) | 650,6 KiB | — | 10,8 KiB |
| B3 | 630 | `MERGE operation - materialize source` | 23:29:57 | 1,9 min | 163/200 (2 killed) | 20,7 KiB | — | — |
| B3 | 648 | `inc.run(...)` | 23:31:23 | 26 s | 87/200 (1 killed) | 444,0 KiB | — | 4,8 KiB |

Los cuatro son los stages **en vuelo en el instante de la cancelación**, con
duraciones **truncadas**: son cotas inferiores. Cinco escriben *shuffle* (lado
map); los tres de `materialize source` no leen ni escriben *shuffle*.

### 2.5 Coste por tarea: lo que sí está medido

| Run | Stage | Tareas | Duración | KiB/tarea | t/tarea (1 ranura) | t/tarea (4 ranuras) |
|---|---|---|---|---|---|---|
| B2 | 643 | 194 | 144 s | 0,20 | **0,74 s** | 2,97 s |
| B2 | 682 | 90 | 66 s | 28,4 | **0,73 s** | 2,93 s |
| B2 | 709 | 98 | 10 s | 0,35 | 0,10 s | 0,41 s |
| B2 | 714 | 5 | 2 s | 17,8 | 0,40 s | 1,60 s |
| B3 | 607 | 180 | 156 s | 16,5 | **0,87 s** | 3,47 s |
| B3 | 619 | 199 | 144 s | 3,3 | **0,72 s** | 2,89 s |
| B3 | 630 | 163 | 114 s | 0,13 | **0,70 s** | 2,80 s |
| B3 | 648 | 87 | 26 s | 5,1 | 0,30 s | 1,20 s |

- **Las cinco stages de ≥66 s cuestan 0,70–0,87 s por tarea** con 0,13–28,4 KiB
  por tarea: el tiempo por tarea **no depende de los bytes**.
- La duración de la stage sigue al **número de tareas**: 90 tareas / 2,5 MiB /
  66 s frente a 194 tareas / 38,5 KiB / 144 s (cociente de duraciones 0,46 =
  cociente de tareas 0,46, con 66× más bytes en la corta).

### 2.6 Aritmética del conjunto: 10.790 tareas para ~31k filas

`10.790 tareas × 0,7 s ≈ 7.553 s` de ejecución de tareas. La capacidad del nodo
es **4 cores × 1.830 s = 7.320 s**. El run entero (30 m 30 s) es, por tanto,
**ejecución de tareas**: no hay hueco para esperas largas ni para trabajo que no
sea tarea.

Aviso: la UI muestra «*Task time* 18,3 h (GC 8 s)», que **no es compatible** con 4
cores × 30,5 min (7.320 core-s) `[NO VERIFICADO]`; el número coherente sale de
las 10.790 tareas y de la duración del run (~0,7 s/tarea), que coincide con lo
medido en §2.5. La cifra de 18,3 h no se usa en ninguna conclusión.

### 2.7 El instante de la cancelación y la cronología

Las consultas «fallidas» son las que estaban **en vuelo** al cancelar: terminan
en una **ventana de 3 s** (B2 12:54:49–52; B3 00:31:46–49).

| Run | Consulta | Enviada (local) | Duración | Fin calculado | Error |
|---|---|---|---|---|---|
| B2 | 44 | 12:52:22 | 2,5 min | 12:54:52 | `AQE_THREAD_INTERRUPTED` |
| B2 | 48 | 12:53:38 | 1,2 min | 12:54:50 | `SPARK_JOB_CANCELLED` (job 130) |
| B2 | 51 | 12:54:38 | 11 s | 12:54:49 | `AQE_THREAD_INTERRUPTED` |
| B2 | 53 | 12:54:50 | 2 s | 12:54:52 | `SPARK_JOB_CANCELLED` (job 134) |
| B3 | 42 | 00:29:22 | 2,4 min | 00:31:46 | `AQE_THREAD_INTERRUPTED` |
| B3 | 44 | 00:29:54 | 1,9 min | 00:31:48 | `SPARK_JOB_CANCELLED` (job 128) |
| B3 | 47 | 00:31:23 | 26 s | 00:31:49 | `AQE_THREAD_INTERRUPTED` |

- **Cronología de B3:** primeras stages de silver a las **23:10:14**; cancelación
  a las **23:31:47** ⇒ **~21,5 min** de silver, coherente con los 22 m 04 s de la
  tarea y con el bronce terminado a las 8 m 06 s.
- **La conversión UTC/local es una inferencia** (`[NO VERIFICADO]` la zona de
  cada pestaña): el desfase de 1 h explica los pares.
- **Anomalía 607 ↔ 42 `[NO VERIFICADO]`:** la stage 607 arranca a las 23:29:09,
  ~13 s antes de la consulta 42 (23:29:22).
- En B2 el emparejamiento consulta↔stage es 1:1 (4 y 4). En B3 hay 3 consultas
  para 4 stages: al menos una tenía dos stages en vuelo (dos ramas del mismo
  *job*), aunque la anomalía de la 42 impide afirmar cuál.
- Las **5 tareas matadas** de los cuatro stages de **B3** (1+2+1+1) coinciden con
  las 5 tareas de diferencia entre «totales» y «completadas» de la vista de
  executors (10.795 − 10.790): el recuento es consistente. (En B2 fueron 8.)

### 2.8 Discrepancias detectadas y su estado

| Afirmación previa | Lo que dicen las capturas o el estado actual |
|---|---|
| «Entradas de 20–650 KiB y 2,5–38,5 KiB en los stages de MERGE» | Los `Input` van de **20,7 KiB a 2,9 MiB**. Los 2,5 **MiB** y 2,9 **MiB** se habían leído como KiB: factor 1.000. |
| «Dos de los cuatro stages son `MERGE operation`» | Cierto en **B2** (643 y 682); en **B3 solo uno** (630). |
| «Todas terminan en el mismo segundo» | Ventana de **3 s**: B2 12:54:49–52 y B3 00:31:46–49. |
| «12–14 consultas por silver» y «19,5 min de consulta por silver» (**revisión 1**) | Lo no sostenible es el **reparto**: 12,3 (49/4) es solo la **cota superior** y el tiempo de consulta no se divide entre cuatro (§3.2). |
| «3,7–6×» con «~14 min» (B3) y «~23 min» (B) (**revisiones 1–2**) | **Mal**: 22 m 04 s **es** la duración de la tarea de silver, y B son ~27 min ⇒ **5,9× y 7,2× ≈ 6–7×**. |
| «×4 por concurrencia» y «A2 con 4 núcleos» (**revisión 1**) | **Falso**: A2 también corre las cuatro silvers en paralelo sobre `tm-lens-job`. |
| «El número de acciones es la palanca» y «menos `count()` es P3» (**revisiones 1–3**) | **Falso con los datos de A2**: A2 hace **57** consultas (más que B3, 49) y tarda **5,4–5,5× menos** en total; el diferencial está en el **coste por consulta** (§3.1). |
| «H6: ficheros pequeños» como causa probable (**revisiones 1–3**) | **Descartada como causa principal**: A2 escribe con 1–4 tareas y las primeras stages de B3 leen con 1–4 tareas (§3.4). |
| `plan.md` §4: H2, H3 y H4 **duplicadas** | **Corregido en el plan**. |
| `plan.md` §2.2 / `tasks.md`: «10 acciones» | **Corregido: 11 acciones** por pase (§3.5). |
| `tasks.md` §4: «techo de 1,0 €» | **Corregido: 1,8 €** (3 runs + 0 SQL, ~0,72 €). |
| `plan.md` §4, H5: «se mide con el run B» | **Corregido: con el run B de T-08**. |
| Columna *Silvers* de `plan.md` §2.1 | **Es la duración de la tarea de silver, no un tiempo run-relative**: B3 22 m 04 s (8 m 06 s + 22 m 04 s ≈ 30 m 10 s) y B ~27 min. B2 no la registró. |

### 2.7 Configuración efectiva (pestaña Environment)

La pestaña **Environment** de la Spark UI de B3 (captura de la persona,
2026-10-28) **solo muestra las propiedades que se han fijado explícitamente**;
las que usan el valor por defecto no aparecen. Allí **no están**
`spark.sql.shuffle.partitions` ni `spark.sql.adaptive.*`: nadie las ha
sobreescrito, así que el job corre con los **valores por defecto del runtime**
(200 particiones de shuffle; AQE activo). Es coherente con las **200 tareas por
stage** que muestran las capturas de `Stages` (§2.3) y descarta que el 200 venga
de una configuración puesta a mano en el job.

`[NO VERIFICADO]`: el valor exacto que ve el job (`spark.conf.get`) no se ha
impreso; se confirma con el número de tareas del primer run con el cambio
(200 → 8) o con una línea de log en el notebook.

## 3. Atribución del coste

### 3.1 No es el volumen, no son los núcleos y **no es el número de acciones**

| Run | Modo | Consultas | Media/consulta | Tiempo total de consulta | Tarea de silver | Tareas (clúster) |
|---|---|---|---|---|---|---|
| A2 | `full` | **57** | **15 s** | **855 s (14,25 min)** | **2 m 47 s – 3 m 46 s** | no publicado (~300–1.000, §3.3) |
| B2 | incremental | 55 | **84 s** | **4.620 s (77,0 min)** | sin registrar | no publicado |
| B3 | incremental | 49 | **96 s** | **4.704 s (78,4 min)** | **22 m 04 s** | **10.790** |

- **El volumen es el mismo** (~31k filas de bronce, ventana de 7 días que cubre
  casi todo) y los núcleos también (mismo clúster, 4 silvers en paralelo): **no
  explican la diferencia**.
- **El número de acciones tampoco**: A2 hace **más** consultas (57) que B3 (49) y
  aun así acumula **5,4–5,5× menos** tiempo de consulta.
- **El diferencial es el coste por consulta: 15 s frente a 84–96 s (5,6–6,4×)**.
  Y como cada consulta es un conjunto de tareas, el diferencial real es
  **cuántas tareas paga cada acción** (§3.3).

Caveat de la media: la de A2 (15 s) incluye consultas de 0,3–1 s (export) y la
del incremental (84–96 s) solo consultas de silvers hasta el corte; la
comparación es de orden de magnitud, no exacta `[NO VERIFICADO]` el reparto por
tipo de consulta.

Listón: `T_full = 2 m 47 s–3 m 46 s` es la **duración de tarea** de A2 (se toma
así, hipótesis conservadora) e incluye el bloque `companies_*`; `T_inc` saldrá de
`timings_s`, que solo cubre `inc.run`. Antes de T-07 hay que comparar lo mismo en
los dos lados. `[NO VERIFICADO]` el origen exacto de la cifra de A2.

### 3.2 La aritmética de las consultas

| Magnitud | A2 | B2 | B3 |
|---|---|---|---|
| Consultas | 57 | 55 | 49 |
| Media | 15 s | 84 s | 96 s |
| **Tiempo total de consulta** | **14,25 min** | **77,0 min** | **78,4 min** |
| Duración del run | 14,65 min | 30,6 min | 30,5 min |
| Consultas simultáneas de media sobre el run | **0,97** | 2,5 | 2,6 |
| Consultas por silver (cota superior, ÷4) | 14,3 | 13,8 | 12,3 |

- En **A2 la suma de duraciones casi iguala la del run** (0,97 concurrentes). O
  las consultas no se solaparon, o la media está dominada por pocas consultas
  largas y la división no es fiable: `[NO VERIFICADO]`. No se usa para nada más.
- En el incremental hay **2,5–2,6 consultas en vuelo de 4 posibles**.
- **El reparto por silver no es derivable**: la UI agrega el **clúster entero**
  (bronce + cuatro silvers + consultas internas). 12,3–14,3 es solo la cota
  superior de consultas por silver.
- Lo que **sí** está medido por silver es su **duración de tarea** (22 m 04 s en
  B3, ~27 min en B) y el hecho de que **no terminó**.

### 3.3 El diferencial: tareas por acción

| Magnitud | A2 (rebuild) | B3 (incremental) |
|---|---|---|
| Tareas por stage | **1–4** (medido en 15 stages) | **200** en las 8 stages fallidas; 1–4 en las primeras |
| Tareas del clúster | no publicado; **266 stages** completadas × 1–4 ⇒ **~300–1.000** | **10.790** |
| Tareas por silver | ~75–250 (estimación) | **~2.700** (10.790 ÷ 4) |
| Coste por tarea | ~0,1–3 s (stages de 1–4 tareas) | **0,70–0,87 s** (medido) |
| Coste por consulta | **15 s** | **84–96 s** |

- **Orden de magnitud ×11–36** en tareas por silver (B3 **~2.700** frente a
  **~75–250** de A2, estimado; o **10.790** totales frente a ~300–1.000 del
  clúster, `[NO VERIFICADO]`).
- El coste por tarea es **parecido** en los dos modos (0,1–3 s en A2, 0,7–0,9 s
  en el incremental). Lo que cambia es **cuántas tareas se pagan**.
- Con **10.790 tareas × 0,7 s ≈ 7.553 s** y una capacidad de **7.320 s** (4
  cores × 30,5 min), el run entero es ejecución de tareas: **el recurso está
  saturado y se gasta en arrancar y ejecutar tareas, no en mover datos**
  (731,6 MiB leídos, 79,7 MiB de *shuffle*: nada que justifique 30 min).

### 3.4 De dónde salen las 200 tareas: **particiones de *shuffle***

Por eliminación, con los datos nuevos:

1. **No son ficheros.** Los `overwrite` de A2 escriben con **1–4 tareas** ⇒ 1–4
   ficheros por escritura; A2 lee la silver con **4 tareas**; y las primeras
   stages de B3 leen el **bronce** con **1–4 tareas** (una, 30,5 MiB en 2
   tareas). Si las tablas tuvieran ~200 ficheros, esas lecturas tendrían ~200
   tareas.
2. **Son particiones de *shuffle*.** El único sitio del plan que produce
   exactamente 200 de algo es `spark.sql.shuffle.partitions` (valor por defecto,
   **no configurado** en `resources/elt.job.yml`). Las stages de 200 tareas son
   *exchanges* (o materializaciones de Delta repartidas por la clave del `MERGE`,
   que usan el mismo número) y varias muestran un `Shuffle Write` diminuto
   (0,3–10,8 KiB): **no mueven volumen**, mueven tareas.
3. **AQE no está coalesciendo** (o está desactivado): con 3k–12k filas y AQE
   activo, un *exchange* de 200 particiones debería reducirse a unas pocas.
   `[NO VERIFICADO]`: hay que leer la configuración efectiva en el próximo run.

**H6 queda descartada como causa principal.** `numFiles` sigue sin medirse
`[NO VERIFICADO]`, pero **ya no es necesario para decidir**: con las escrituras de
1–4 tareas y las lecturas de 1–4 tareas, el layout no explica las 200.

### 3.5 Las 11 acciones: inventario (ya no palanca)

Del código de `_run_once`, **11 acciones por pase** (el orden es **lógico, no
cronológico**: las acciones son perezosas y `require_no_null_ingest_date`
materializa `recompute` antes de los conteos):

| # | Punto del código | Acción |
|---|---|---|
| 1 | `window_bounds` → `max(_ingest_date)` | 1 consulta |
| 2 | `changed.isEmpty()` (`limit`) | 1 |
| 3 | `affected.count()` | 1 |
| 4 | `require_no_null_ingest_date` (`limit`) | 1 |
| 5 | `require_unique_groups` | 1 |
| 6 | `changed.count()` | 1 |
| 7 | `recompute.count()` | 1 |
| 8 | `markers.count()` | 1 |
| 9 | `merge_src.count()` | 1 |
| 10 | **`MERGE`** | 1 |
| 11 | `target.count()` | 1 |

`keyed`, `recompute_keys` y `merge_src` se materializan dentro de las acciones que
los usan. Las 12,3–14,3 consultas por silver de la UI cuadran con 11 acciones más
1–3 consultas internas del `MERGE`/catálogo `[NO VERIFICADO]`.

**Pero el número de acciones no es la palanca**: A2 hace 57 consultas y es 5,4×
más rápido en total. Quitar `count()` sigue siendo correcto por limpieza y por
resumen, pero su efecto está acotado por el coste por consulta (§5, P3 «bajo
valor»).

### 3.6 Lo que este diagnóstico **no** explica

- **Por qué una tarea cuesta 0,7–0,9 s** con 0,13–28,4 KiB: el desglose entre
  arranque, serialización del plan, inicialización de expresiones y evaluación.
  Es lo que mide P2 con `explain("codegen")` y la tabla de tareas.
- **Cuánto del tiempo de una consulta es cada acción** de cada silver: lo
  instrumenta T-02.
- **Si el `MERGE` reescribe la tabla entera**: faltan `operationMetrics`.
- **El reparto por tipo de consulta** (bronce / silver / export) en cada run.

## 4. Hipótesis H1–H6

| # | Hipótesis | Estado | Evidencia | Qué falta |
|---|---|---|---|---|
| H1 | Codegen: la proyección supera 64 KB y cae a interpretado | **PARCIAL: no es el diferencial** | El aviso se observó en local (evidence 008) y la cadena lo hace verosímil (128 ramas `when`/`rlike` × 2, cascadas de skills/experiencia/rol/salario, `create_map` de 110 y 104 entradas). Pero **A2 ejecuta la misma cadena y su coste por tarea es del mismo orden** (0,1–3 s) con 5,4× menos tiempo total: el codegen no separa los dos modos. | `explain("codegen")` local y un A/B partiendo la proyección (P2). Si baja el coste por tarea, ayuda **proporcionalmente**; no explica las 200 tareas. |
| H2 | Pasadas y caché: `recompute` se materializa 5–6 veces y la caché no aguanta 4 silvers en 16 GiB | **PARCIAL: la caché se usó; la evicción no se confirma** | **508 stages skipped** en B3 y **220** en A2 ⇒ la caché y la reutilización de *shuffle* funcionaron. Al final **0 B de storage y de disco**: las cachés se liberaron (`unpersist`). El `Input` total (731,6 MiB ≈ 23× el bronce) muestra relecturas, pero el coste dominante es el número de tareas (10.790), no un recálculo masivo de la cadena. | La fracción cacheada **durante** el run (no al final) y `timings_s` por acción (T-02). |
| H3 | `MERGE`: el `ON` es una expresión a los dos lados, sin `_key`, sin poda por estadísticas y reescribe el destino entero | **PARCIAL** | Sus stages `materialize source` cuestan **1,1–2,4 min (cota inferior) con 200 tareas** y 20,7 KiB–2,5 MiB; **al menos dos** silvers de B2 murieron dentro del `MERGE`. A2 no tiene `MERGE`, así que es una parte del diferencial, no toda. | `operationMetrics` en el run B de T-08 y `explain` del plan del `MERGE` (T-04). |
| H4 | Concurrencia y compute: cuatro jobs en 4 vCPU comparten CPU y memoria | **CONFIRMADA (parcial)** | **1 ejecutor (driver), 4 cores**; **10.790 tareas × ~0,7 s ≈ 7.553 s** frente a **7.320 s** de capacidad en 30,5 min: el run es **ejecución de tareas** y el recurso está saturado. Las stages de 200 tareas son *launch-bound* (200 × 0,7 s ≈ 140 s con una ranura). **No explica el diferencial con A2**, que comparte el mismo reparto. | Ranuras reales por consulta y el desglose ejecución/planificación por tarea. **No** hace falta más compute para el diagnóstico: la palanca es menos tareas. |
| H5 | Ventana: el watermark hace que el incremental recalcule casi todo | **CONFIRMADA (documental)** | `_ingest_date` es la **fecha del lote**: la ventana de 7 días abarca todo lote con fecha ≤ `to_date` y, en dev, **se supone** que cubre casi todo el bronce (31k filas; el 27-oct entraron 190) `[NO VERIFICADO]` (no hay `changed_rows`). | `changed_rows` (no existe en B2/B3). Se medirá en el **run B de T-08**. |
| H6 | Layout: los `MERGE`/`overwrite` dejan ficheros pequeños | **DESCARTADA como causa principal** | A2 escribe con **1–4 tareas** (`Output` 43,1 KiB–2,1 MiB) y las primeras stages de B3 leen con **1–4 tareas** (hasta 30,5 MiB en 2): no hay ~200 ficheros. Las 200 son **particiones de *shuffle*** (§3.4). | `numFiles` por tabla sigue `[NO VERIFICADO]`, pero **ya no decide**: solo confirmaría que el layout no es el cuello. |

**Resumen del estado.** El mecanismo medido es: **11 acciones por pase, 200
tareas por *exchange*** (10.790 en el run), **0,70–0,87 s por tarea** y **4
cores**, con el **coste por consulta 5,6–6,4× mayor** que en el rebuild. H4
(1 ejecutor, 4 cores, saturación) y H6 (descartada) están resueltas en lo que
importa; H2 sigue parcial; H1 y H3 son condiciones presentes pero **no el
diferencial**.

## 5. Cambios priorizados

| P | Cambio | Tarea | Efecto esperado | Coste | Riesgo | Cómo se mide antes/después |
|---|---|---|---|---|---|---|
| **P1** | **Particiones de *shuffle*: `spark.sql.shuffle.partitions` 200 → 8–16** (y **comprobar la config efectiva y AQE** en el propio run) | **T-03** (experimento, con la lectura de config de T-02) y **T-06** (config definitiva en `elt.job.yml`, **aprobación previa**) | Tareas por silver de **~2.700 a ~150–250** (−90 %): *exchange* de 200 × 0,7 s ≈ 140 s → 8–16 × 0,7 s ≈ **6–11 s**. Silver de **22 min a ~2–3 min**, con **suelo ~2,7 min** (11 consultas × 15 s, el mismo orden que el rebuild) | 0 € (config) | Bajo para el dato; con 4 cores, 8–16 particiones siguen dando 2–4 tandas por consulta. Bajar a 2–4 penalizaría el `MERGE` | Tareas por stage y por run (UI) y `timings_s` (T-02): objetivo ≤ **16–32 tareas por stage** y ≤ **~300 por silver** |
| **P2** | **Coste por tarea y codegen**: `explain("codegen")`, tamaño del código, y si el aviso de 64 KB aparece en `build_offers`/`enrich`; partir la proyección con una frontera materializada | T-03 (y banco de T-02) | Multiplica **solo el coste por tarea** (0,7 s). Si baja a la mitad, el efecto es 2× sobre el tiempo de tareas; **no** reduce las 200 tareas | 0 € | Medio: partir la cadena **no puede cambiar ninguna expresión de negocio** | `explain("codegen")` (tamaño) + banco local antes/después + `timings_s` del paso `recompute` |
| **P3** | **Menos acciones por pase** (11 → ≤6): cachear `target` (5 lecturas → 3 escaneos), conteos desde Delta | T-02/T-03 | **Bajo valor** con la evidencia nueva: A2 hace **más** consultas (57) y es 5,4× más rápido; quitar 5 consultas ahorra ~5 × 15 s = **~75 s** con el coste por consulta de A2, no minutos | 0 € | Medio: el resumen tiene 16 campos (test de contrato) | Nº de consultas por pase y `timings_s` |
| **P4** | **`MERGE`**: `operationMetrics` y decisión sobre `_key` materializada | T-04 (decisión), confirmación en T-08 | Solo si, tras P1, el `MERGE` sigue pesando: hoy son 200 tareas y ≥1,1–2,4 min (cota inferior) de los ~22 | 0 € | **Alto** (contrato): columna interna, proyección explícita, `RESTORE` | `numTargetRowsCopied` y duración de su stage |
| **P5** | **Compute**: `D8ds_v4`, multi-nodo o serverless | T-06 | Duplicar núcleos reduce el reloj ~2× si el coste por tarea es CPU, y **duplica el coste por run**: se paga lo mismo por un trabajo que P1 reduce 10× | 2–4× por run | Medio | Tiempo por configuración y coste; **solo después de P1** |

**Descartados (con la evidencia nueva)**

- **`OPTIMIZE` / compactación**: solo si `DESCRIBE DETAIL` da `numFiles` alto; hoy
  **improbable** (A2 escribe 1–4 ficheros). Pasa de palanca a comprobación.
- **Subir `timeout_seconds`**: prohibido por la spec y no cambia el coste.
- **Secuenciar las silvers**: no reduce el trabajo; el grano del coste es la tarea.
- **Menos acciones como palanca principal**: ver P3.
- **Photon / serverless como solución al cuello**: cambia el coste por tarea, no
  las 200 tareas; pertenece a P5.

**Expectativa, sin prometer:** con P1, el incremental quedaría **en el orden del
rebuild** (2 m 47 s–3 m 46 s), que es **justo el gate RF-2a**: si el suelo de
~2,7 min se cumple, entra por poco; si no, habrá que sumar P2 (coste por tarea).
Por eso el orden es P1 → medir → P2.

**Nota de secuencia:** P1 se **experimenta en T-03** (config del `SparkSession` o
del notebook) y la **config definitiva se fija en T-06** (`.yml` del clúster),
porque el efecto de las particiones es el que decide el resto. Así T-03/T-05 no
se miden sin la palanca principal y el banco local se calibra con la config real.

## 6. Datos que faltan para cerrar el diagnóstico

1. ~~**Configuración efectiva** de `spark.sql.shuffle.partitions` y AQE en el
   clúster.~~ **Resuelto por ausencia** (§2.7): no hay override en
   `Environment`, así que valen los defaults (200 y AQE activo). Queda por
   confirmar el valor que imprime `spark.conf.get`, gratis, en el primer run con
   el cambio.

2. **Detalle de una stage de 200 tareas** (tabla de tareas: mínimo/mediana/máximo,
   cola de planificación, *spill*): el desglose de los 0,7 s por tarea (P2).
3. **`explain("formatted")` y `explain("codegen")` locales** del plan de
   `_run_once` y del modo `full`: *exchanges* de cada uno, tamaño del código y si
   aparece el corte de 64 KB.
4. **Total de tareas de A2**: la vista de stages no lo publica; con las 266
   completadas y 1–4 tareas se acota a ~300–1.000 `[NO VERIFICADO]`.
5. **`operationMetrics` del `MERGE`** (en el run B de T-08) y `numFiles` por tabla
   (ya **no decide** H6, pero cierra el layout).
6. **Parámetros de la medición local de «~45 s por pase»**: filas, núcleos y si
   incluía escritura.

## 7. Criterio de aceptación del cambio

Referencia: `plan.md` §3 y RF-2/RF-3 de la spec.

| Gate | Umbral | Cómo se mide |
|---|---|---|
| **RF-2a** (ventana ancha = casi todo el bronce) | `T_inc ≤ 3 m 46 s` por silver | `timings_s` del resumen instrumentado (T-02) |
| **RF-2b** (ventana ≤1 % del bronce) | `T_inc ≤ 75 s` por silver | Ídem, escenario pequeño del banco |
| **RF-3** (run completo en dev) | ≤ **22 m 30 s**, 8/8 en verde | Duración del run por la API, con el lote anotado |
| **Invariante de diseño** | `T_inc` con ventana ancha **< `T_full`** (2 m 47 s–3 m 46 s) | Mismo banco, mismo lote; **misma simetría** (duración de tarea con `companies_*` o `timings_s` sin él, en los dos lados) |
| **RF-4/RF-5** | Exports A/B **idénticos en contenido** (`sha256` de `files`, escritura ordenada; **T-09**, producción), `MERGE` con escrituras y repetición sin cambio (**T-08**) | Comparación de `files` contra `previous_files` en `meta.json` (10 Parquet: 6 PBI + 4 web) + `operationMetrics` |
| **Gate intermedio de P1** | ≤ **16–32 tareas** por stage y ≤ **~300 tareas** por silver; `spark.sql.shuffle.partitions` efectivo documentado | Spark UI (stages) + resumen del run |

El criterio no se cumple «en local» ni «por cálculo»: se cumple con los runs de
T-07/T-08 (tiempo, `MERGE` e idempotencia) y, para RF-4, con la comparación A/B
de `meta.json` en **T-09** (producción, escritura ordenada). Este documento fija
**qué** hay que mover y **cómo** se sabrá si se movió.

## 8. Resultado del primer cambio (medido el 2026-10-28)

Se fijó `spark.sql.shuffle.partitions = 8` en el job cluster (una línea) y se
lanzaron los dos runs, ya con el cambio desplegado:

| Run | Modo | Total | Silvers (módulo) | Por pase |
|---|---|---|---|---|
| A `485578578869927` | `force_full` | **808 s** (13 m 28 s) | 117-169 s | — |
| B `269508392317481` | incremental + `repeat_window` | **974 s** (16 m 14 s) | 249-318 s / 173-249 s | **4 m 09 s-5 m 18 s** y **2 m 53 s-4 m 09 s** |

- `MERGE` con **55-458** filas por fuente; `deleted_groups = 0`; conteos
  finales **idénticos a A** (3.112 / 2.527 / 12.543 / 10.072) y los dos pases de
  la repetición dejan el mismo contenido.
- **Lo que se ha ganado:** el incremental **ya termina y cabe** (16 m 14 s con
  dos pases; en producción, sin `repeat_window`, será un solo pase). Antes:
  `TIMEDOUT` a los 30 min con las silvers en >22 min.
- **Lo que no:** el pase incremental (4-5 min) sigue por encima del rebuild
  (2-3 min): **RF-2a no se cumple**. El cuello ya no son las 200 tareas por
  stage (el cambio se nota en 4x) sino el **coste fijo por consulta**: 11
  acciones a ~20-30 s cada una, más el `MERGE`. Con la ventana de 24-28 solo
  cambian 55-485 filas, así que el tiempo es **overhead**, no datos.
- **Siguiente palanca:** quitar acciones (P3) y/o abaratar el coste por tarea
  (P2). Para decidir cuánto se gana con cada una, el resumen necesita los
  tiempos por acción: **T-02 vuelve a la mesa**.
- `[NO VERIFICADO]`: las tareas por stage del run B (no se capturó su Spark
  UI); los exports A/B no se capturaron (B sobrescribió los de A y no se guardó
  su SHA-256; la equivalencia está por conteos e idempotencia).

## 9. Decisión del MERGE (T-04)

**Decisión (2026-10-30): no materializar `_key` en silver y no crear tabla de
stage.** El `ON` sigue siendo la expresión calculada a los dos lados y el
`MERGE` se queda como está. La decisión se toma con las `operationMetrics` del
run `879269828668729` de T-03 (**8 pasadas reales de `MERGE`**, cuatro silvers ×
dos pases de `repeat_window`), no con el plan: el `explain` local no se ha
podido obtener (§9.4). **0 €**: sin run ni sesión SQL; solo lectura gratuita de
la API de runs (el resumen lo publica el propio notebook).

### 9.1 La medición que la sostiene

La columna «Pase (s)» es la duración del **pase** completo tal como llega en la
medición del run; **no es `timings_s`**, que es el diccionario de tiempos por
paso. El paso `merge` sale de ese diccionario y dura **10,5–28,9 s**
(**19,1–28,9 s** en el pase en frío y **10,5–21,4 s** en el segundo pase). Las
columnas de escritura salen de `merge_metrics`, leídas con `DESCRIBE HISTORY`
**en el clúster del job** (metadatos de Spark, no el SQL warehouse).

| Tabla | Pase | `changed` | `affected` | `target_rows` | Pase (s) | `filesAdded` | `filesRemoved` | `bytesAdded` | `bytesRemoved` | `DVadded` | `copied` | `updated` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `offers_indeed` | 1 | 229 | 226 | 3.112 | 138,2 | 1 | 0 | 453.383 | 0 | 1 | 0 | 226 |
| `offers_indeed` | 2 | 229 | 226 | 3.112 | 110,5 | 1 | 1 | 453.383 | 453.383 | 0 | 0 | 226 |
| `offers_infojobs` | 1 | 55 | 55 | 2.527 | 123,8 | 1 | 0 | 66.087 | 0 | 1 | 0 | 55 |
| `offers_infojobs` | 2 | 55 | 55 | 2.527 | 89,4 | 1 | 1 | 66.087 | 66.087 | 0 | 0 | 55 |
| `offers_linkedin` | 1 | 485 | 458 | 12.543 | 136,6 | 1 | 0 | 656.076 | 0 | 1 | 0 | 458 |
| `offers_linkedin` | 2 | 485 | 458 | 12.543 | 112,3 | 1 | 1 | 656.076 | 656.076 | 0 | 0 | 458 |
| `offers_multi_site` | 1 | 122 | 121 | 10.072 | 136,7 | 1 | 0 | 41.133 | 0 | 1 | 0 | 121 |
| `offers_multi_site` | 2 | 122 | 121 | 10.072 | 112,2 | 1 | 1 | 41.133 | 41.133 | 0 | 0 | 121 |

| Tabla | Pase | `executionTimeMs` | `scanTimeMs` | `materializeSourceTimeMs` | `rewriteTimeMs` |
|---|---|---|---|---|---|
| `offers_indeed` | 1 | 12.233 | 2.841 | 1.891 | 7.442 |
| `offers_indeed` | 2 | 7.215 | 2.182 | 731 | 4.245 |
| `offers_infojobs` | 1 | 23.280 | 6.814 | 3.443 | 12.968 |
| `offers_infojobs` | 2 | 12.941 | 5.179 | 1.087 | 6.584 |
| `offers_linkedin` | 1 | 13.512 | 4.934 | 999 | 7.434 |
| `offers_linkedin` | 2 | 5.857 | 2.092 | 819 | 2.905 |
| `offers_multi_site` | 1 | 15.298 | 5.656 | 1.091 | 8.491 |
| `offers_multi_site` | 2 | 8.628 | 3.307 | 1.021 | 4.238 |

En las **ocho** pasadas:

- **`numTargetRowsCopied = 0`.** Delta no ha copiado ni una fila del destino: no
  hay reescritura de filas no afectadas.
- **`numTargetFilesAdded = 1`.** Un solo fichero nuevo por pase, con
  **41,1 KB–656,1 KB**; el resto de ficheros de la tabla no se reescribe
  (`bytesRemoved=0` en el primer pase y un único fichero retirado en el
  segundo).
- **`numTargetBytesRemoved` del pase 2 = `numTargetBytesAdded` del pase 1**
  (las cuatro tablas, con bytes idénticos: 453.383 / 66.087 / 656.076 / 41.133). Es
  **consistente con** retirar el fichero añadido en el pase 1 (la ruta del
  fichero no se mide): reescribe el fichero de las filas cambiadas, no la
  tabla.
- **`numTargetRowsInserted = 0`, `numTargetRowsDeleted = 0`,
  `numTargetRowsMatchedDeleted = 0`,
  `numTargetRowsNotMatchedBySourceUpdated = 0`,
  `numTargetChangeFilesAdded = 0`.** Es una actualización pura de grupos
  existentes: `updated` = `affected` (55–458) y `target_rows` no cambia.
- **`target_rows` idénticos a los del run A** (3.112 / 2.527 / 12.543 / 10.072)
  y `deleted_groups = 0`: idempotente **en conteos**; el contenido de los
  exports se cierra en **T-09** (decisión del 2026-10-31, RF-4 por contenido).
- **`scanTimeMs` 2,1–6,8 s** y **`materializeSourceTimeMs` 0,7–3,4 s**: las dos
  partes del `MERGE` que tocan la expresión de la clave cuestan, juntas,
  **≤ 10,3 s por pase** (máximo real: infojobs pase 1, 6.814 + 3.443 =
  10.257 ms), dentro de un `executionTimeMs` de 5,9–23,3 s.

### 9.2 Lectura de H3

`plan.md` §4 describía H3 así: «el `ON` es una expresión calculada a los dos
lados, sin `_key` almacenada; no hay poda por estadísticas y **el destino se
reescribe entero**».

- **«El destino se reescribe entero» queda descartada.** `numTargetRowsCopied=0`
  en las ocho pasadas, `filesAdded=1` y `bytesRemoved=0` en el primer pase. La
  hipótesis se formuló antes de tener métricas; ya no se sostiene.
- **Lo que queda de H3 es el coste del `ON` con expresión**, no un problema de
  escritura: el paso `merge` (**10,5–28,9 s** por pase; **19,1–28,9 s** en el
  pase en frío y **10,5–21,4 s** en el segundo) **está entre los tres pasos más
  caros de cada pase: primero en `offers_infojobs` (los dos pases), segundo en
  `offers_indeed` y en el segundo pase de `offers_multi_site`, y tercero en
  `offers_linkedin` (los dos pases) y en el primer pase de `offers_multi_site`**.
  En los segundos puestos queda por delante `recompute_check`
  (**41,09–50,44 s**); en los terceros, también `changed_count` (22,124 s en
  `offers_multi_site` pase 1; 25,065 s y 11,310 s en `offers_linkedin` pases 1 y
  2). El plan no se ha podido leer (§9.4), así que **no se afirma** si esa
  expresión impide poda por fichero ni si hay un `Exchange` sobre ella; lo
  medido es su factura, no su causa.
- **El pase 1 deja 1 deletion vector** por tabla (`numDeletionVectorsAdded=1`):
  las 55–458 filas actualizadas se marcan como borradas en el fichero base y se
  escriben en un fichero nuevo, sin reescribir el base (`filesRemoved=0`,
  `bytesRemoved=0`). El pase 2 **no añade ningún DV** y es **consistente con**
  retirar el fichero añadido en el pase 1 (la ruta no se mide). **Matiz
  medido:** que el pase 2 «materialice» el DV del pase 1 no se deduce de las
  métricas —si lo materializara, retiraría también el fichero base, y
  `numTargetBytesRemoved` coincide con el tamaño del fichero del pase 1—.
  **Handoff a T-05 (layout).** Hecho medido: `delta.enableDeletionVectors` está
  **activa en las tablas del catálogo dev medidas** (18 filas de `DESCRIBE
  DETAIL`: las 14 de bronze, silver de ofertas y gold, más las 4 `companies_*`),
  salida literal en
  `docs/media/performance/2026-10-30-describe-detail-dev.txt` (clúster de
  lectura del 2026-10-30). `DESCRIBE DETAIL` **no** informa del número de DVs.
  **Inferencia** (no medida): con esta ventana, el pase 1 añade 1 DV y el pase 2
  reescribe el fichero del pase 1, así que los DVs **no se acumulan más allá de
  un pase**. T-05 decidirá si eso justifica `OPTIMIZE`; su alcance no cambia.

### 9.3 Coste aceptado y alternativas descartadas

Coste que se acepta: **10,5–28,9 s por pase** en el paso `merge` (**19,1–28,9 s**
en el pase en frío y **10,5–21,4 s** en el segundo), que está entre los tres
pasos más caros de cada pase: primero en `offers_infojobs` (los dos pases),
segundo en `offers_indeed` y en el segundo pase de `offers_multi_site`, y
tercero en `offers_linkedin` (los dos pases) y en el primer pase de
`offers_multi_site`. Con `executionTimeMs` 5,9–23,3 s, el pase completo
(89,4–138,2 s) cabe de sobra en el gate RF-2a (226 s). No se toca el contrato de
silver ni se gasta un run.

| Alternativa | Por qué se descarta |
|---|---|
| **Columna `_key` materializada** en silver (la excepción controlada de la spec) | **No arregla un problema que no existe**: el destino ya no se reescribe entero. Toca contrato (`_key` interna en las cuatro silvers, proyección explícita en oro y exports), exige migración, tests y `RESTORE` disponible. Su ganancia máxima sería el paso `merge` completo (**10,5–28,9 s** por pase) y, dentro de él, el reparto del `ON` no se conoce: el plan físico no se ha podido leer (`[NO VERIFICADO]`, §9.4); `scanTimeMs` y `materializeSourceTimeMs` son cotas de escaneo y materialización, **no** la atribución del `ON`. |
| **Tabla de stage** por run (materializar `merge_src`) | Añade **una escritura Delta por pase** y su limpieza (versiones, ficheros, DV) para ahorrar, como máximo, el paso `merge` completo (10,5–28,9 s), cuyo reparto interno no se conoce. Además duplica el estado que hay que mantener y complica la idempotencia. El origen ya es una vista temporal: pasarlo a tabla suma I/O sin ganancia medida. |
| **Partir la clave** (dedup por columnas sueltas o por hash precalculado de negocio) | Cambia el **modelo de dedup** (identidad del grupo semántico): choca con «sin cambio de semántica» y con la equivalencia A/B de RF-4. No hay evidencia de que el `ON` sea el cuello. |

Queda **abierto, con evidencia a favor de no hacerlo**: si en el futuro el
`merge` crece con el volumen, la palanca barata sería volver a medir el plan y,
solo entonces, decidir entre `_key` o stage. Hoy no se justifica.

### 9.4 Qué **no** se ha verificado

- **El plan físico del `MERGE` en local (`explain`) no se ha podido obtener**,
  así que quedan `[NO VERIFICADO]`: si hay `Exchange`/shuffle sobre la
  expresión del `ON`, si el destino tiene poda por fichero o partición y si se
  escanea completo. El intento (script temporal fuera del repositorio,
  `%TEMP%\opencode\t04_merge_explain\`) arrancó Spark 4.2.0 con Delta cargado
  desde JARs locales (`local[2]`, 8 particiones, AQE activo, la misma expresión
  de `key_sql`; la traza completa de la primera variante no se conservó), pero
  **este host Windows no tiene `winutils.exe` ni `hadoop.dll`**:
  - `[NO VERIFICADO]` (anotado durante la sesión, no conservado en `out.txt`):
    `CREATE TABLE ... USING DELTA` falló en
    `RawLocalFileSystem.setPermission` → `Shell.getWinUtilsPath` (`HADOOP_HOME
    and hadoop.home.dir are unset`).
  - Una tabla Delta vacía creada a mano falla al planificar en
    `RawLocalFileSystem.listStatus` → `FileUtil.canRead` →
    `NativeIO$Windows.access0`: `UnsatisfiedLinkError` (falta `hadoop.dll`).

  **No se ha instalado nada**, como manda la tarea: el `explain` queda
  `[NO VERIFICADO]` con este error, y la decisión se sostiene en las
  `operationMetrics` del run real, que es la evidencia de mayor rango. Los
  tests del proyecto ya asumen esta limitación (saltan las escrituras en
  Windows sin `HADOOP_HOME`).
- **El reparto del `ON` dentro del `merge`**: `scanTimeMs` y
  `materializeSourceTimeMs` son cotas, no la atribución de la expresión.
- **La materialización del DV del pase 1 en el pase 2**: ver el matiz de §9.2.
- **La extrapolación a producción**: la spec anota **190 filas** ingeridas en
  indeed el 27-oct, menos que este run de dos pases; que el coste por pase sea
  igual o menor es una expectativa, no una medida (`[NO VERIFICADO]` hasta
  T-09).
