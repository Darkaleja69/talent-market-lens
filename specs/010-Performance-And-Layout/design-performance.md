# Diseño: diagnóstico de rendimiento del incremental (T-01)

> **Alcance de este documento.** T-01 de la spec 010: atribuir el coste del
> incremental de las cuatro silvers **sin lanzar runs y sin tocar código**, a
> partir de la Spark UI de B2/B3, del inventario de acciones de `_run_once`
> (`plan.md` §2.2) y de la aritmética de los números medidos. **0 €, 0 runs,
> 0 sesiones SQL.**
>
> **Revisión 3 (2026-10-28).** Corrige las duraciones de silver: **22 m 04 s
> (B3) y ~27 min (B) son la duración de la tarea de silver, no el corte del
> run**; restarles el bronce lo contaría dos veces (8 m 06 s + 22 m 04 s ≈ 30 m
> 10 s, a 20 s del total). Los múltiplos frente al rebuild son **5,9× y 7,2× ≈
> 6–7×**. Se retira la prueba de imposibilidad de §3.2 (con 22 m 04 s, 3,55
> consultas simultáneas **sí** cabe en 4) y B2 queda como «sin duración
> registrada». Las capturas se archivan en `docs/media/performance/`.
>
> **Revisión 2 (2026-10-28), tras la verificación independiente.** Corrige de
> raíz la descomposición «×4 por concurrencia» de la revisión 1: **A2 también
> ejecutaba las cuatro silvers en paralelo** (`resources/elt.job.yml`: las cuatro
> dependen de `Bronze_Ingestion` y comparten `tm-lens-job`), así que los núcleos
> son los mismos en A2 y en B2/B3 y no pueden ser el diferencial. Corrige además
> los conteos de stages con *shuffle write*, los rangos de KiB/tarea, el instante
> de cancelación y varias lecturas que la revisión 1 daba por probadas.
>
> **Advertencia de método.** Las capturas disponibles son dos pantallas por run
> (resumen de consultas y *Failed Stages*). No hay *Storage*, *Tasks*,
> *Executors*, *event log* ni la Spark UI del run A. Lo que esas pantallas no
> enseñan **no se afirma**; todo lo no medido va marcado `[NO VERIFICADO]`.
>
> **Este documento no cambia la spec.** Las discrepancias con `plan.md` o
> `tasks.md` se señalan en §2.6 sin editarlos.

## 1. Fuentes y método

### 1.1 Fuentes usadas

| Fuente | Qué aporta | Límite |
|---|---|---|
| `docs/media/performance/2026-10-27-b3-sql.png` | B3 (`1015448868873733`): resumen SQL/DataFrame (49 consultas, media 1,6 min, 3 fallidas) y las 3 consultas fallidas | Recorte: solo las 3 filas de la tabla |
| `docs/media/performance/2026-10-27-b3-stages.png` | B3: *Failed Stages* (4 stages, 200 tareas cada uno) | Solo el resumen por stage, sin *details* ni *Tasks* |
| `docs/media/performance/2026-10-27-b2-stages.png` | B2 (`1056455747512203`): *Failed Stages* (4 stages) | Ídem |
| `docs/media/performance/2026-10-27-b2-sql.png` | B2: resumen SQL/DataFrame (55 consultas, media 1,4 min, 4 fallidas) y las 4 fallidas | Ídem |
| `specs/008-Delta-Incremental/evidence.md` | Duración de los runs, bronce, ausencia de escrituras, medición local de ~45 s por pase y aviso de codegen | Sin resumen JSON de las silvers (los runs se cancelaron) |
| `specs/008-Delta-Incremental/design-incremental.md` §5 | Contrato del incremental: clave, ventana, conjunto afectado, marcadores, `MERGE` | Diseño, no medición |
| `databricks_notebooks/Silver_Incremental.py` | Inventario real de acciones de `_run_once` | Lectura de código |
| `databricks_notebooks/Silver_Indeed.ipynb` + `Enrich_Job_Offers_Dataframes.py` | Composición de `build_offers` (lo que se paga en cada pase) | Lectura de código |
| `resources/elt.job.yml`, `plan.md` §2.1 | Compute: `D4ds_v4` single node, 4 vCPU / 16 GiB, `STANDARD`, `17.3.x-scala2.13`, `timeout_seconds: 1800`, **cuatro silvers en paralelo** | Configuración del bundle, no de la captura |
| `docs/costs.md` | Duración y coste de los runs del bloque B | Coste estimado por duración |

### 1.2 Método

1. Leer las cuatro capturas y **transcribir** los números (no fiarse del
   resumen previo).
2. Convertir las duraciones de stages en **coste por tarea**, con el número de
   tareas y los bytes de cada stage.
3. Cerrar la aritmética del run: tiempo total de consulta frente a la duración
   del run y a la de las silvers; comprobar qué reparto es **posible** y cuál no.
4. Contrastar cada hipótesis H1–H6 de `plan.md` §4 con esos números.
5. Ordenar los cambios por efecto esperado × coste × riesgo.

### 1.3 Qué no se ha podido ver

- La pestaña **Storage** (fracción cacheada, *spill* a disco) y la de
  **Executors** (GC, memoria pico).
- La tabla de **tareas de un stage** (mínimo/mediana/máximo por tarea, *shuffle
  read/write*, *spill*, ejecución frente a planificación).
- El **event log** y el plan físico (`explain`) de las consultas de B2/B3.
- **La Spark UI del run A (rebuild)**: sin ella no se sabe cuántas tareas por
  stage ni cuántos *exchanges* tiene el modo `full`, que es la mitad que falta
  de la comparación (§3.3, §6).
- Qué tabla o frame lee cada uno de los 8 stages fallidos.
- El **resumen JSON** de las silvers: `changed_rows`, `affected_groups`,
  `recomputed_groups`, `deleted_groups` y `merge_rows` de B2/B3 no existen.
- La duración del bronce en B2.
- La configuración efectiva del clúster (`spark.sql.shuffle.partitions`,
  `spark.sql.files.*`, AQE): no está en el bundle y no se ha consultado.

## 2. Hechos medidos

### 2.1 Los dos runs

| Hecho | B2 `1056455747512203` | B3 `1015448868873733` | Fuente |
|---|---|---|---|
| Resultado | `TIMEDOUT` | `TIMEDOUT` | evidence 008 |
| Duración del run | 30 m 37 s | 30 m 30 s | evidence 008 |
| Bronce | en verde, duración no registrada | **8 m 06 s** | evidence 008 |
| **Duración de la tarea de silver** (no run-relative) | **sin duración registrada** | **22 m 04 s** | evidence 008 + `plan.md` §2.1 |
| Escrituras | ninguna (versiones Delta intactas) | ninguna | evidence 008 |
| Consultas SQL/DataFrame (clúster) | **55** | **49** | captura |
| Duración media por consulta | **1,4 min** | **1,6 min** | captura |
| Consultas fallidas | **4** (7,3 %) | **3** (6,1 %) | captura |
| Consultas en ejecución al mirar | 0 | 0 | captura |

Dos aclaraciones que cambian la lectura respecto a las revisiones anteriores:

- **`22 m 04 s` es la duración de la tarea de silver, no un tiempo run-relative.**
  El bronce terminó a los 8 m 06 s y 8 m 06 s + 22 m 04 s ≈ 30 m 10 s, a 20 s del
  total del run (30 m 30 s): el reparto cuadra, y **restarle el bronce al corte
  contaría el bronce dos veces**. Es el mismo criterio que usa `plan.md` §2.1.
- **B2 no registró la duración de las silvers** (ni la del bronce). El otro punto
  medido es **B** (`186230462263213`): 30 m 46 s de run, bronce 3 m 29 s ⇒
  **~27 min** de silver (3 m 29 s + 27 min ≈ 30 m 29 s, a 17 s del total).

### 2.2 Las consultas y stages fallidas: una cancelación, no cuatro fallos

En los dos runs, las consultas «fallidas» **son las que estaban en vuelo en el
instante de la cancelación**: el error es `[SPARK_JOB_CANCELLED]` /
`[AQE_THREAD_INTERRUPTED]` y todas terminan en una **ventana de 3 s**.

| Run | Consulta | Enviada (local) | Duración | Fin calculado | Error |
|---|---|---|---|---|---|
| B2 | 44 | 12:52:22 | 2,5 min | 12:54:52 | `AQE_THREAD_INTERRUPTED` |
| B2 | 48 | 12:53:38 | 1,2 min | 12:54:50 | `SPARK_JOB_CANCELLED` (job 130) |
| B2 | 51 | 12:54:38 | 11 s | 12:54:49 | `AQE_THREAD_INTERRUPTED` |
| B2 | 53 | 12:54:50 | 2 s | 12:54:52 | `SPARK_JOB_CANCELLED` (job 134) |
| B3 | 42 | 00:29:22 | 2,4 min | 00:31:46 | `AQE_THREAD_INTERRUPTED` |
| B3 | 44 | 00:29:54 | 1,9 min | 00:31:48 | `SPARK_JOB_CANCELLED` (job 128) |
| B3 | 47 | 00:31:23 | 26 s | 00:31:49 | `AQE_THREAD_INTERRUPTED` |

- **Ventana de cancelación: 3 s.** B2 termina entre 12:54:49 y 12:54:52; B3,
  entre 00:31:46 y 00:31:49.
- **La conversión UTC/local es una inferencia**, no un dato leído: las horas de
  las stages vienen en UTC y las de las consultas en hora local (Europe/Madrid,
  UTC+1 en esa fecha), y el desfase de 1 h explica los pares
  (`23:31:23` + 26 s = `00:31:49`). `[NO VERIFICADO]` la zona horaria mostrada
  por cada pestaña.
- **Anomalía 607 ↔ 42 `[NO VERIFICADO]`.** La stage 607 arranca a las 23:29:09,
  **~13 s antes** de la consulta 42 (23:29:22 en UTC): una stage no puede
  empezar antes que su consulta. O la correlación por duración no es 1:1, o hay
  desfase de reloj. No se resuelve con las capturas.
- **El recuento cuadra:** el resumen dice 3 consultas fallidas y la tabla muestra
  3. Los 4 stages de B3 **no** son 4 consultas.

Consecuencia: **la cancelación pilló a las cuatro silvers a mitad de una
acción**. En B2 el emparejamiento consulta↔stage es 1:1 (4 y 4). En B3 hay 3
consultas para 4 stages: al menos una consulta tenía dos stages en vuelo a la
vez (dos ramas independientes del mismo *job*), aunque la anomalía de la
consulta 42 impide afirmar cuál. Ninguna había terminado el primer pase (y no
hay escrituras, lo que lo confirma). Las duraciones de stage son **cotas
inferiores**: la cancelación las truncó.

### 2.3 Los ocho stages fallidos (4 por run)

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

**Tres hechos que hay que subrayar:**

1. **Los ocho stages fallidos tienen exactamente 200 tareas.** *(Los stages del
   run A no están medidos: no hay Spark UI del rebuild.)*
2. **Cinco stages escriben *shuffle*** (lado map: leen de almacenamiento y
   escriben *shuffle*): B2/714 (283,0 B), B2/709 (5,5 KiB), B3/607 (10,2 KiB),
   B3/619 (10,8 KiB) y B3/648 (4,8 KiB). **Los tres de `materialize source` no
   tienen ni `Shuffle Read` ni `Shuffle Write`**: son escaneos (de la fuente o
   del destino del `MERGE`). En los ocho, `Shuffle Read` está vacío.
3. **El `Input` es de 20,7 KiB a 2,9 MiB** para 200 tareas: entre 133 B y 28,4
   KiB por tarea.

### 2.4 Coste derivado por tarea

`t/tarea (1 ranura) = duración ÷ tareas hechas`; `t/tarea (4 ranuras) =
duración × 4 ÷ tareas hechas`. Son las dos esquinas del intervalo: una consulta
con 4 vCPU puede usar hasta 4 ranuras **si no compite**, y con las cuatro silvers
a la vez el reparto efectivo está entre 1 y 4. **No está medido** en cuál de las
dos esquinas se trabajó; la columna de 1 ranura solo da la cota más favorable al
diagnóstico.

| Run | Stage | Tareas | Duración | KiB/tarea | t/tarea (1 ranura) | t/tarea (4 ranuras) | KiB/s |
|---|---|---|---|---|---|---|---|
| B2 | 643 | 194 | 144 s | 0,20 | **0,74 s** | 2,97 s | 0,27 |
| B2 | 682 | 90 | 66 s | 28,4 | **0,73 s** | 2,93 s | 38,8 |
| B2 | 709 | 98 | 10 s | 0,35 | 0,10 s | 0,41 s | 3,4 |
| B2 | 714 | 5 | 2 s | 17,8 | 0,40 s | 1,60 s | 44,4 |
| B3 | 607 | 180 | 156 s | 16,5 | **0,87 s** | 3,47 s | 19,0 |
| B3 | 619 | 199 | 144 s | 3,3 | **0,72 s** | 2,89 s | 4,5 |
| B3 | 630 | 163 | 114 s | 0,13 | **0,70 s** | 2,80 s | 0,18 |
| B3 | 648 | 87 | 26 s | 5,1 | **0,30 s** | 1,20 s | 17,1 |

Dos regularidades, que son el núcleo del diagnóstico:

- **Las cinco stages de ≥66 s cuestan 0,70–0,87 s por tarea**, con tan solo
  **0,13–28,4 KiB por tarea** (más de 200× de diferencia de bytes, 1,2× de
  diferencia de tiempo por tarea). Las cuatro más largas van de 0,13 a 16,5
  KiB/tarea.
- **La duración de la stage sigue al número de tareas, no a los bytes**: la
  stage de 90 tareas con 2,5 MiB tarda 66 s y la de 194 tareas con 38,5 KiB
  tarda 144 s; el cociente de duraciones (0,46) es igual al cociente de tareas
  (0,46) con **66× más bytes** en la corta. El rendimiento es de 0,2–44 KiB/s,
  muy por debajo de lo que da el almacenamiento incluso con ficheros pequeños.
  **El tiempo se va en arrancar y ejecutar tareas, no en mover datos.**

Lo que **no** se puede derivar de aquí: cuántas tareas hay que pagar en cada
acción (solo se ven las ocho stages que estaban en vuelo), ni cuántas por stage
en el rebuild. El coste por tarea sí está medido; el reparto de tareas por pase,
no (§3.2).

### 2.5 El instante de la cancelación dice dónde estaba cada silver

- **B2:** dos stages son `MERGE operation - materialize source` y llevaban 1,1 y
  2,4 min en vuelo ⇒ al menos **dos silvers habían llegado al `MERGE`** (paso 14
  de `_run_once`). Son cotas inferiores: la cancelación las truncó.
- **B3:** solo una stage es del `MERGE`; las otras tres son acciones previas.
- En B2 una consulta llevaba **2 s** (acababa de empezar) mientras otra llevaba
  2,5 min: los cuatro silvers avanzan a distinta velocidad. Que las dos stages de
  `MERGE` sean de las más avanzadas sugiere que las tablas pequeñas (indeed
  3.096 e infojobs 2.527 filas) son las que más lejos llegan, pero **no se puede
  saber a qué silver pertenece cada stage** `[NO VERIFICADO]`. **Ninguna de las
  cuatro terminó un pase** en su ventana de silver (22 m 04 s en B3, ~27 min en
  B); si hay un término de tamaño, no se puede medir con estas capturas
  `[NO VERIFICADO]`.

### 2.6 Discrepancias detectadas y su estado

| Afirmación previa | Lo que dicen las capturas o el estado actual |
|---|---|
| «Entradas de 20–650 KiB y 2,5–38,5 KiB en los stages de MERGE» | Los `Input` van de **20,7 KiB a 2,9 MiB**. Los 2,5 **MiB** de B2/682 y los 2,9 **MiB** de B3/607 se habían leído como KiB: factor 1.000. |
| «Dos de los cuatro stages son `MERGE operation`» | Cierto en **B2** (643 y 682); en **B3 solo uno** (630). |
| «Todas las consultas fallidas terminan en el mismo segundo» | Terminan en una **ventana de 3 s**: B2 12:54:49–52 y B3 00:31:46–49. |
| «12–14 consultas por silver» y «19,5 min de consulta por silver» (**revisión 1 de este documento**) | Lo que no se sostiene es el **reparto**: 12,3 (49/4) es solo la **cota superior** de consultas por silver, y el tiempo de consulta no se puede repartir porque la UI agrega el **clúster entero** (incluye bronce), no por consulta ni por notebook (§3.2). |
| «3,7–6×» con «~14 min» (B3) y «~23 min» (B) (**revisiones 1–2 de este documento**) | **Mal**: 22 m 04 s **es** la duración de la tarea de silver, no el corte; y B son ~27 min. Múltiplos: 22 m 04 s / 3 m 46 s ≈ **5,9×** y ~27 min / 3 m 46 s ≈ **7,2×** ⇒ **≈6–7×**. |
| «×4 por concurrencia» y «A2 con 4 núcleos» (**revisión 1 de este documento**) | **Falso**: A2 también corre las cuatro silvers en paralelo sobre `tm-lens-job` (`elt.job.yml`). Los núcleos son iguales en A2 y en B2/B3 y no explican el diferencial (§3.1, §3.3). |
| `plan.md` §4: H2, H3 y H4 **duplicadas** | **Corregido en el plan**: cada hipótesis aparece una vez. |
| `plan.md` §2.2 / `tasks.md`: «10 acciones» | **Corregido: 11 acciones** por pase (§3.4). El objetivo de T-02 sigue siendo ≤6. |
| `tasks.md` §4: «techo de 1,0 €» | **Corregido: 1,8 €** (el reparto aprobado es 3 runs + 0 SQL, ~0,72 €). |
| `plan.md` §4, H5: «se mide con el run B» | **Corregido: con el run B de T-08**. El B de la 008 se canceló y no dejó resumen. |
| Columna *Silvers* de `plan.md` §2.1 | **El plan explicita que es la duración de la tarea de silver, no un tiempo run-relative**: B3 22 m 04 s (8 m 06 s de bronce + 22 m 04 s ≈ 30 m 10 s, a 20 s del total) y B ~27 min (3 m 29 s + 27 min ≈ 30 m 29 s). **B2 no la registró.** |

## 3. Atribución del coste

### 3.1 El volumen no es la diferencia; los núcleos tampoco

La comparación limpia es **A2 contra B y B3**: las tres **apuntan** a las mismas
tablas y el mismo bronce, con **las cuatro silvers en paralelo**. (B y B3 no
llegaron a escribir: la comparación es de coste, no de resultado.)

| Run | Modo | Filas de bronce | Acciones por silver | Duración de la tarea de silver |
|---|---|---|---|---|
| A2 `207866361330883` | `full`: `transform` + escritura + `count()` final | ~31k | **2** | **2 m 47 s – 3 m 46 s** |
| B `186230462263213` | incremental, ventana de 7 días | ~31k (la ventana cubre casi todo `[NO VERIFICADO]`) | **11** (código, §3.4) | **~27 min** (30 m 46 s − 3 m 29 s de bronce), sin terminar |
| B2 `1056455747512203` | ídem | ~31k | **11** | **sin duración registrada** |
| B3 `1015448868873733` | ídem | ~31k | **11** | **22 m 04 s**, sin terminar |

Múltiplos, cada uno con su métrica —no intercambiables:

- **Por duración de la tarea de silver**: B3 22 m 04 s frente a 3 m 46 s ⇒
  **5,9×**; B ~27 min frente a 3 m 46 s ⇒ **7,2×** ⇒ **≈6–7×**. Es el diferencial
  que se usa en este documento. B2 no entra: no registró su duración.
- **Por tiempo de consulta total** (77–78 min): **no evaluable**. El reparto por
  silver no es derivable (§3.2) y el «~5×» que salía de suponer 19–20 min por
  silver es el mismo error de la revisión 1; se deja solo como referencia de que
  el orden de magnitud no baja de ×5.

**Qué mide el listón `2 m 47 s–3 m 46 s`.** **Se toma como duración de tarea del
run** —la hipótesis conservadora, porque incluye más trabajo— y así se compara:
la duración de tarea incluye **todo el notebook**, `inc.run` **más** el bloque
`companies_*`, que se escribe después. Por tanto el listón es **holgado** si se
compara con un `T_inc` que salga de `timings_s` (solo `inc.run`), y la
comparación honesta debe incluir `companies_*` en los dos lados o excluirlo de
los dos. `[NO VERIFICADO]` el origen exacto de la cifra de A2 (la evidencia de la
008 no copió su `elapsed_s`); queda anotado para T-07, donde el resumen lo dirá.

**El diferencial es de trabajo, no de núcleos.** Como A2 y B2/B3 comparten el
mismo clúster y el mismo grafo de cuatro silvers en paralelo, el reparto de CPU
por consulta es el mismo en los dos casos. Lo que cambia es **cuántas tareas se
pagan por acción y cuántas acciones hay**: 11 acciones por pase frente a las 2
del rebuild, y stages de 200 tareas en el incremental (los ocho medidos) frente a
un perfil de stages del rebuild **que no está medido** (§6).

### 3.2 La aritmética de las consultas: qué se puede y qué no

| Magnitud | B2 | B3 |
|---|---|---|
| Consultas (clúster) | 55 | 49 |
| Media por consulta | 1,4 min | 1,6 min |
| **Tiempo total de consulta** | **77,0 min** | **78,4 min** |
| Duración del run | 30,6 min | 30,5 min |
| **Consultas simultáneas de media sobre el run** | **2,5** | **2,6** |
| Duración de la tarea de silver | sin registrar | 22 m 04 s |
| Ídem si **todas** las consultas fueran de las silvers | no evaluable (sin ventana de silver) | **3,55** (cabe en 4) |
| Consultas por silver (cota superior: 55/4 y 49/4) | 13,8 | 12,3 |

Lectura:

- Sobre el run completo hay **2,5–2,6 consultas en vuelo de 4 posibles**: el
  nodo está ocupado con entre dos y tres consultas simultáneas de media.
- En B3, si **todas** las 49 consultas fueran de las silvers y solo de su ventana
  de 22 m 04 s, saldrían **3,55 consultas simultáneas**: **cabe** en las 4, pero
  deja poco margen y exige suponer que el bronce no ejecutó ninguna consulta, que
  es improbable (la ingesta es *streaming* con `foreachBatch`: cada micro-lote es
  una ejecución).
- **El reparto por silver no es derivable**, y no porque sea imposible: la UI
  agrega el **clúster entero** (bronce + cuatro silvers + consultas internas) y
  da una media por consulta de todas ellas. **12,3 (49/4) es solo la cota
  superior** de consultas por silver; el tiempo de consulta **no** se puede
  dividir entre cuatro (ese fue el error de la revisión 1, que llegaba a 19,5 min
  por silver).
- Lo que **sí** está medido por silver es su **duración de tarea** (22 m 04 s en
  B3, ~27 min en B) y el hecho de que **no terminó**. El reparto fino por acción
  es lo que instrumenta T-02.

**La media de 1,4–1,6 min es una mezcla, no un coste unitario.** En la misma
ventana conviven consultas de 2 s, 26 s y ≥2,4 min. Sin `timings_s` por acción
no se sabe qué acción es cada duración.

### 3.3 Coste por tarea y por acción: lo medido y el modelo

**Lo medido, directamente:** los ocho stages fallidos tienen 200 tareas y las
cinco stages de ≥66 s cuestan **0,70–0,87 s por tarea** con 0,13–28,4 KiB por
tarea (§2.4), con duraciones de **66 a 156 s**. Eso es independiente de cualquier
modelo y sostiene la prioridad P1/P2 (§5): mientras una stage tenga 200 tareas y
cada tarea cueste ~0,8 s, cada acción pesada cuesta ~2,5 min.

**El modelo, y solo el modelo:**

```
T_accion ≈ n_tareas × t_tarea / ranuras
t_tarea ≈ 0,7–0,9 s   (medido en las cinco stages de ≥66 s)
n_tareas = 200        (medido en los ocho stages fallidos)
ranuras = las que tenga la consulta; NO está medido
```

Esto es una **cuadrícula de interpretación, no una descomposición medida**:
`ranuras` es justo lo que no se puede leer de estas capturas (haría falta la
tabla de tareas con ejecución frente a planificación y el reparto por
*executor*). Y **no puede ser el diferencial con A2**, porque A2 corre con la
misma concurrencia de cuatro silvers.

**El diferencial, en la parte medida, es `tareas por acción × número de
acciones`.** Del lado del incremental las dos piezas están medidas: **200 tareas
por stage** y **11 acciones por pase**. Del lado de A2 no está medida ninguna de
las dos: solo su duración total (2 m 47 s–3 m 46 s).

**La consecuencia, que es una incógnita y no un resultado.** Si el rebuild de A2
tardó 2 m 47 s–3 m 46 s con el mismo reparto de CPU (4 vCPU para las cuatro
silvers, igual que en B2/B3) y con el mismo coste por tarea, **no pudo tener 3–4
stages de 200 tareas** (a ~150 s cada una serían 7–10 min). Tiene que haber al
menos una de estas tres cosas, y **ninguna está medida**:

1. **muchas menos tareas por stage** en el rebuild (p. ej. *coalesce* de AQE en
   el lado reduce, o menos ficheros en las tablas de entonces);
2. **menos *exchanges*** en el plan de `full` (`[NO VERIFICADO]`: la revisión 1
   suponía «~4 *exchanges*» sin contarlos);
3. **un coste por tarea mucho menor** en esas stages (p. ej. sin el *codegen*
   caído a interpretado, o tareas que cortan por `limit`).

Medirlo es barato: con la **Spark UI del run A de T-07** se ven las tareas por
stage del rebuild, y con `explain("formatted")` en local se cuentan sus
*exchanges* (T-02).

**Por qué B3 no podía mejorar a B2.** El parche de B3 compartía `keyed` entre
los **dos pases** de `repeat_window`. Como B3 no escribió nada y una de las
silvers seguía en el `MERGE` del **primer** pase al cancelarse, **ninguna silver
llegó a empezar el segundo**: el parche no llegó a ejecutarse. No fue
«insuficiente»: fue **inmedible**. (Y explica que B2 y B3 den prácticamente el
mismo resultado.)

### 3.4 De dónde salen las 11 acciones

Del código de `_run_once` (`Silver_Incremental.py`). En **un** pase, sin
`repeat_window`. **El orden de la tabla es lógico, no cronológico**: las acciones
son perezosas y su orden real lo fija el plan (p. ej. `require_no_null_ingest_date`
fuerza a materializar `recompute` antes de que llegue el `changed.count()`).

| # | Punto del código | Acción | Tareas que paga (según el modelo) |
|---|---|---|---|
| 1 | `window_bounds` → `max(_ingest_date)` sobre silver | 1 consulta | escaneo de silver |
| 2 | primer uso de `changed` → `changed.isEmpty()` | 1 consulta (`limit`) | puede cortar en la primera partición |
| 3 | `affected.count()` (semi-join contra silver + `union` + `distinct`) | 1 consulta | 2–3 *exchanges* |
| 4 | `require_no_null_ingest_date` (`limit` sobre `recompute`) | 1 consulta | primera partición de `recompute` |
| 5 | `require_unique_groups` (`groupBy(_key).count()`) | 1 consulta | `recompute` + *exchange* |
| 6 | `changed.count()` | 1 consulta | caché |
| 7 | `recompute.count()` | 1 consulta | caché (o cadena, si no cupo) |
| 8 | `markers.count()` (escaneo de silver + 2 joins) | 1 consulta | 2 *exchanges* |
| 9 | `merge_src.count()` | 1 consulta | caché/union |
| 10 | **`MERGE`** | 1 consulta | `materialize source`/`target` + escritura |
| 11 | `target.count()` tras el MERGE | 1 consulta | escaneo de silver |

Son **11 acciones**. `keyed`, `recompute_keys` y `merge_src` se materializan
**dentro** de las acciones que los usan, no como acciones aparte (la acción 3
materializa además `keyed` y `changed`, y la 8 materializa `recompute_keys`).

**Reconciliación con las 12–14 consultas de la UI.** La UI cuenta ejecuciones
SQL del **clúster entero** (incluye bronce) y, por silver, la cota superior es
12,3–13,8. Con 11 acciones de código, las 1–3 de diferencia son, con toda
probabilidad, **consultas internas**: ejecuciones propias del `MERGE` de Delta,
`createOrReplaceTempView`/análisis y llamadas de catálogo (`tableExists`).
`[NO VERIFICADO]` cuáles y cuántas.

Tres observaciones de código que sostienen el reparto:

- **`target` se lee cinco veces por pase y no se cachea**: `window_bounds`
  (`max`), `old_groups` (semi-join), `orphan_markers` (semi-join + anti-join),
  el `MERGE` y el `count()` final. Con `repeat_window=true`, **diez**. De esas
  cinco, **dos son evitables** sin cambiar el resultado (`old_groups` y
  `orphan_markers` pueden leer un `target` cacheado; el `MERGE` y el `count()`
  posterior no, porque el `count()` debe ver el estado de después del `MERGE`).
- **`keep()` cachea siete frames** (`keyed`, `changed`, `affected`, `recompute`,
  `recompute_keys`, `markers`, `merge_src`). `cache()` es perezoso **por
  partición**: la primera acción que toca un frame cacheado con `limit(1)`
  rellena **una** de las 200 particiones; el resto se paga en la siguiente
  acción. La caché ayuda, pero no desde la primera consulta.
- **La cadena de negocio se paga dos veces por pase**: `normalize_company`
  dentro de `keyed` (bronce) y otra vez `build_offers` →
  `apply_company_canonical` sobre los candidatos. Cada una es un `distinct()` +
  *join* con una cadena de **128 ramas `when`/`rlike`** (contadas en el fichero;
  el comentario del módulo dice «~110»), más `de.enrich` con las cascadas de
  skills/experiencia/rol/salario y varias `create_map` (`_LOCATION_CITY_CANONIC`
  110 entradas, `_LOCATION_REGION_CANONIC` 104, `EXPERIENCE_LEVEL_MAP` 52).

### 3.5 Las 200 tareas: ¿particiones de *shuffle* o ficheros pequeños?

Es la pregunta que decide entre H6 y la configuración de *shuffle*, y **las
capturas no la cierran**. Lo que se afirma y lo que se deduce:

**Lo medido**
- Los ocho stages fallidos, todos con exactamente 200 tareas.
- `Shuffle Read` vacío en los ocho; `Input` de 133 B a 28,4 KiB por tarea.
- **Cinco** stages escriben *shuffle* (lado map): leen de almacenamiento.
  **Tres** (`materialize source`) no leen ni escriben *shuffle*: escanean.

**Las dos explicaciones, y por qué no son excluyentes**
1. **`spark.sql.shuffle.partitions = 200`** (valor por defecto; **no está
   configurado** en `resources/elt.job.yml`, `[NO VERIFICADO]` el valor efectivo
   del clúster). Explicaría que cualquier `Exchange` tenga 200 particiones y,
   con ellas, **200 tareas de reducción**; y cualquier escritura, **200 ficheros**.
2. **Ficheros pequeños por tabla**: cada escritura de 200 particiones deja ~200
   ficheros de pocas decenas de KiB; el siguiente escaneo vuelve a tener ~200
   tareas. Es un bucle que se realimenta: **la causa 1 produce la causa 2**.

El indicio que **inclina la balanza hacia los ficheros** (sin demostrarlo) es el
tamaño por tarea: 133 B–28,4 KiB es el tamaño de un **fichero** de Delta
pequeño, no el de un bloque de *shuffle* (que o sería mucho mayor, o sería
vacío). Además, las dos entradas más grandes —2,5 MiB (B2/682) y 2,9 MiB
(B3/607)— cuadran con el **bronce completo** de una fuente (~1 KB/fila ×
2,5–3k filas), lo que sugiere que los escaneos de 200 ficheros son de **bronce**
(escrito por micro-lotes de *streaming*, que dejan muchos ficheros pequeños) y
no de silver `[NO VERIFICADO]`. `[NO VERIFICADO]` también que un escaneo de
Delta en este runtime haga una tarea por fichero en vez de empaquetar ficheros
pequeños; si empaquetara, las 200 tareas obligarían a descartar esta lectura y
quedarse con la del *shuffle*.

**Lo que falta para cerrarlo:** `DESCRIBE DETAIL` de las cuatro silvers y las
cuatro bronces (`numFiles`, `sizeInBytes`). Si `numFiles ≈ 200` por tabla, H6
está **presente**, no confirmada como causa: para confirmarla hay que **comparar
las tareas por stage con `numFiles`** y **medir después de `OPTIMIZE`** (si al
bajar los ficheros bajan las tareas y el tiempo, la causa queda demostrada).
Cuesta 0 €.

Consecuencia práctica: **no hace falta resolver la disyuntiva para priorizar**.
Las dos causas se atacan con el mismo par de cambios (§5, P1 y P2) y se miden
con el mismo par de números (tareas por stage y `numFiles`). Si P0 confirma que
los ficheros están en bronce, `OPTIMIZE` empieza por bronce.

### 3.6 Lo que este diagnóstico **no** explica

- **Por qué una tarea cuesta 0,7–0,9 s.** El desglose entre arranque de tarea,
  apertura de fichero en ABFS, inicialización de las expresiones por partición,
  evaluación interpretada (H1) y contención (H4) exige la tabla de tareas y la
  pestaña de *executors*, que no están.
- **Cuánto cuesta cada acción de cada silver.** Es lo que instrumenta T-02 con
  `setJobGroup` + `SparkListener`; hoy solo hay cotas.
- **El perfil de stages del rebuild (A2).** Sin la Spark UI del run A no se sabe
  si su ventaja viene de menos tareas por stage, de menos *exchanges* o de un
  coste por tarea menor (§3.3). Es la mitad que falta de la comparación.
- **Si el `MERGE` reescribe la tabla entera.** `operationMetrics` no existe (no
  hubo commit). A 12k filas el volumen no puede ser el problema, pero no está
  medido.

## 4. Hipótesis H1–H6

| # | Hipótesis | Estado | Evidencia | Qué falta |
|---|---|---|---|---|
| H1 | Codegen: la proyección supera 64 KB y cae a interpretado | **PARCIAL** | El aviso «Code grows beyond 64 KB» se observó en la medición local de B3 (evidence 008). La cadena lo hace verosímil: 128 ramas `when`/`rlike` × 2 (bronce y recálculo), cascadas de skills/experiencia/rol/salario y varias `create_map` (110 y 104 entradas); son expresiones que se inicializan por partición (compilación de los 128 regex y de los mapas, `[NO VERIFICADO]` en este runtime). Pero **la captura no muestra ninguna stage de codegen** y el aviso no cuantifica el coste. | `explain("codegen")` en local (tamaño por stage y si aparece el corte de 64 KB) y un A/B local partiendo la proyección. **Techo conocido:** la medición local da ~45 s por pase; si ese es el coste de la evaluación interpretada, H1 no puede explicar por sí sola 22 m 04 s (B3) ni ~27 min (B). |
| H2 | Pasadas y caché: `recompute` se materializa 5–6 veces y la caché no aguanta 4 silvers en 16 GiB | **PARCIAL** | **Confirmado (mecanismo):** 11 acciones por pase y `target` leído **5 veces por pase sin caché** (§3.4). **No confirmado:** que la causa sea la evicción. Hay acciones cortas (2 s en B2, 26 s en B3) junto a acciones de ≥2,4 min, lo que es **compatible con un `limit`** sobre la primera partición, pero **no prueba que haya caché**; `cache()` rellena por particiones, así que un `limit` deja 199 de 200 sin cargar. | Pestaña Storage (fracción cacheada, *spill*) en el instante de la cancelación y `timings_s` por acción (T-02). Sin eso no se separa «no cabe» de «cada acción paga tareas». |
| H3 | `MERGE`: el `ON` es una expresión a los dos lados, sin `_key`, sin poda por estadísticas y reescribe el destino entero | **PARCIAL** | Medido: el `MERGE` **se alcanzó** y sus stages `materialize source` costaron **1,1–2,4 min con 200 tareas y 20,7 KiB–2,5 MiB de entrada** (B2/643, B2/682, B3/630); son cotas inferiores por la cancelación. **Al menos dos** silvers de B2 murieron dentro del `MERGE`. **No medido:** filas copiadas, si reescribe toda la tabla, ni el reparto entre `materialize source`, `target` y escritura. | `operationMetrics` (`numTargetRowsCopied`, insertadas/actualizadas/borradas) en el run B de T-08 y `explain` del plan del `MERGE` en local (T-04). A 12k filas el volumen del destino no puede ser el problema: lo será el número de tareas. |
| H4 | Concurrencia y compute: cuatro jobs en 4 vCPU comparten CPU y memoria | **PARCIAL (con corrección)** | Medido: **2,5–2,6 consultas simultáneas de 4 posibles** sobre el run y **200 tareas por stage** (en el corte había hasta 800 tareas en vuelo: 4 stages × 200). **Corrección al enunciado del plan:** no es que «cada job corra con 1–2 tareas» —hay 200 tareas por stage— sino que compiten por 4 ranuras. **Pero la concurrencia no puede explicar el diferencial**: A2 tenía la misma concurrencia y fue **≈6–7×** más rápido. | GC, memoria pico y **ranuras reales** por consulta (tabla de tareas y pestaña Executors). Sin eso no se sabe cuánto del coste por tarea es contención y cuánto es trabajo. |
| H5 | Ventana: el watermark hace que el incremental recalcule casi todo | **CONFIRMADA (documental)** | `_ingest_date` es la **fecha del lote**, no la de captura: con `lookback_days=7` la ventana abarca todo lote con fecha ≤ `to_date` y, en dev, **se supone** que cubre casi todo el bronce (31k filas; el 27-oct entraron 190) `[NO VERIFICADO]` (no hay `changed_rows`). B2/B3 son el **peor caso**, y por eso el listón es A2 (mismo volumen). | El `changed_rows` exacto de B2/B3 no existe (sin resumen). Se puede reproducir en local con el watermark y el bronce, que es gratis. Se medirá en el **run B de T-08**. |
| H6 | Layout: los `MERGE`/`overwrite` dejan ficheros pequeños | **PRESENTE, NO CONFIRMADA como causa** | Los ocho stages fallidos tienen 200 tareas para 0,13–28,4 KiB por tarea; cinco son lado map leyendo ficheros; el rendimiento es de 0,2–44 KiB/s. Un Delta escrito con 200 particiones deja ~200 ficheros de decenas de KiB (12.504 filas / 200 ≈ 63 filas por fichero), y el bronce de *streaming* deja muchos ficheros por micro-lote. Pero **no hay `DESCRIBE DETAIL`** y las 200 podrían venir solo de `shuffle.partitions`. | `DESCRIBE DETAIL` de las 8 tablas (`numFiles`, `sizeInBytes`, tamaño medio). Con `numFiles ≈ 200` H6 queda **presente**; para **confirmar la causa**, comparar tareas por stage con `numFiles` y medir tras `OPTIMIZE`. |

**Resumen del estado.** Ninguna hipótesis se declara causa única. Lo que las
capturas **sí demuestran** es que el coste se paga **por tarea y por acción**
(200 tareas por stage en los ocho stages medidos, 0,70–0,87 s por tarea en cinco
de ellos, 11 acciones por pase) y que el **volumen no explica la diferencia** con
A2. H2 (número de acciones) está medida como **mecanismo**; H4 (concurrencia)
está medida como **condición** pero **descartada como diferencial**, porque A2 la
comparte; H1, H3 y H6 están presentes pero no cuantificadas.

## 5. Cambios priorizados

Ordenados por efecto esperado ÷ (coste × riesgo). Los tres primeros son
configuración y medida; ninguno cambia el resultado del incremental (el gate es
el de `plan.md` §3 y RF-4).

| P | Cambio | Tarea | Efecto esperado | Coste | Riesgo | Cómo se mide antes/después |
|---|---|---|---|---|---|---|
| **P0** | `DESCRIBE DETAIL` de bronce y silver, y **Spark UI del run A** (tareas por stage del rebuild) | T-05 / T-07 (cierra H6 y §3.3) | Ninguno por sí solo: **decide** entre ficheros y *shuffle*, y da el perfil de stages del rebuild | 0 € (la UI de A2 si sigue retenida; si no, la del run A de T-07) | Nulo (solo lectura) | `numFiles`, `sizeInBytes`, tamaño medio por tabla; tareas por stage del rebuild |
| **P1** | **`spark.sql.shuffle.partitions` 200 → 8–16** en el clúster del job, y **`delta.autoOptimize.optimizeWrite = true`** (o `optimizeWrite` al escribir) | **T-06** la config del clúster (toca `elt.job.yml`) y T-05 la propiedad de tabla: **aprobación previa** | 200 → ≤16 tareas por *exchange* y por escritura: una stage de ~2,5 min baja a **~15 s** (−90 %); rompe el bucle «200 particiones → 200 ficheros → 200 tareas» | 0 € (config) | Bajo: 16 particiones sobran para 3k–12k filas (16 tareas × 4 silvers = 64 tareas, 16 tandas sobre 4 vCPU); bajar a 2–4 sí penalizaría el `MERGE`. `optimizeWrite` añade un *shuffle* pequeño en la escritura y **cambia el layout** (aprobación) | Tareas por stage y duración de stage en el siguiente run; `numFiles` después de la primera escritura |
| **P2** | **`OPTIMIZE`** (compactación) de las 4 bronces y las 4 silvers, **solo si P0 da `numFiles` alto** (y **bronce primero** si es donde están los ficheros) | T-05 | Escaneos de 200 → 1–4 tareas: una acción de escaneo de ~2,5 min baja a **~5 s** | 0 € de runs; reescritura de 31k + 31k filas (segundos) | Bajo, pero **reescribe datos físicos**: aprobación previa y `RESTORE` disponible | `DESCRIBE DETAIL` antes/después + tareas y duración del primer escaneo posterior |
| **P3** | **Menos acciones por pase** (11 → ≤6): cachear `target` una vez (5 lecturas → 3 escaneos reales, o 2 si `target_rows` sale de Delta), sacar los conteos de Delta (`DESCRIBE HISTORY`/`operationMetrics`) en vez de `count()`, proyectar la clave antes de los marcadores | T-02/T-03 | 11 → ~6 consultas y **−2 escaneos del destino** por pase: **−5 a −6,5 min por silver** con el coste actual; con P1/P2 ya aplicado, −20 a −40 s | 0 € | Medio: el resumen tiene 16 campos y no puede perder ninguno (test de contrato); la caché de `target` consume memoria (12k × 36 columnas ≈ decenas de MB por silver) | Nº de consultas por pase (captura SQL/DataFrame) y `timings_s` por acción (T-02) |
| **P4** | **Caché con criterio y codegen**: `persist(MEMORY_AND_DISK)`/`localCheckpoint(eager=True)` en `recompute`, y partir la proyección de `build_offers` en una frontera materializada | T-03 | Solo si T-02 demuestra que `recompute` se paga >1 vez o que `explain("codegen")` muestra el corte de 64 KB; techo estimado **~45 s por pase evitado** (medición local) | 0 € | Medio: `localCheckpoint` rompe el linaje (bien) pero materializa a disco; partir la cadena **no puede cambiar ninguna expresión de negocio** | `explain("codegen")` (tamaño) + banco local antes/después + `timings_s` del paso `recompute` |
| **P5** | **`_key` materializada en silver** para que el `ON` del `MERGE` sea igualdad de columna | T-04 | Solo si, tras P1–P3, el `MERGE` sigue pesando mucho. Hoy su stage medida es **1,1–2,4 min (cota inferior)**, sobre una tarea de silver de 22 m 04 s (B3) y ~27 min (B) | 0 € de run | **Alto**: columna interna nueva, contrato con la persona, proyección explícita en oro/exports, `RESTORE` | `operationMetrics` del `MERGE` (filas copiadas) y duración de su stage, antes/después |

**Descartados ahora, con motivo**

- **Subir `timeout_seconds`**: prohibido por la spec (§6.6 del plan). No cambia
  el coste.
- **Secuenciar las cuatro silvers como solución**: no reduce el trabajo total
  (las mismas tareas) y **no ataca la causa**: el grano del coste es la tarea,
  no la cola. Se puede probar como experimento barato en T-03 si P1–P3 no bastan.
- **Subir a `D8ds_v4` antes de arreglar las tareas**: si el coste por tarea es
  CPU, duplicar núcleos reduce el reloj ~2× y **duplica el coste por run**: se
  paga lo mismo por un trabajo que se puede reducir 12× con configuración. Queda
  como decisión de T-06 **con la medición de P1–P3 delante**.
- **Photon / serverless**: cambio de compute, no de código; pertenece a T-06 y
  no arregla que una stage tenga 200 tareas.

**Efecto acumulado esperado (para el gate, no como promesa):** con P1+P2+P3, un
pase pasa de 11 acciones de ~1,5 min a ~6 acciones de 5–20 s más un `MERGE`
de ~30–60 s ⇒ `T_inc ≈ 2–3,5 min`, dentro del listón de 3 m 46 s. El suelo real
es **el coste de calcular la cadena una vez** (~45 s locales): por eso P4 y P5
solo entran si el banco y el run B los justifican.

## 6. Datos que faltan para cerrar el diagnóstico

Lista corta, gratis y en orden de valor:

1. **`DESCRIBE DETAIL` de las 8 tablas** (bronce y silver de las cuatro fuentes):
   `numFiles`, `sizeInBytes`, tamaño medio y `numFiles` de la versión de A2.
   Cierra H6 y fija el objetivo de P2.
2. **Spark UI (o *event log*) del run A de T-07**: tareas por stage del rebuild y
   su duración. Es la mitad que falta de §3.3: sin ella no se sabe si A2 gana por
   menos tareas, menos *exchanges* o menor coste por tarea.
3. **Una stage completa en detalle** (la más larga de B3, id 607, si la UI o el
   *event log* siguen retenidos): mínimo/mediana/máximo por tarea, *shuffle
   read/write*, *spill*, **ranuras** y tiempo de planificación frente a
   ejecución. Es lo que reparte los 0,8 s por tarea.
4. **Pestaña Storage en el instante de la cancelación** (fracción cacheada de los
   7 frames, *spill* a disco). Decide si H2 es «no cabe» o «coste por tarea».
5. **`explain("formatted")` local del plan de `_run_once`** (y del modo `full`):
   número de *exchanges* de cada uno, escaneos de `target`, `BroadcastExchange`
   y confirmación de que el `distinct()` de empresa se resuelve con *broadcast*.
6. **`explain("codegen")` local** (banco de T-02): tamaño del código por stage y
   si aparece el corte de 64 KB en `build_offers` o en `enrich`.
7. **Parámetros de la medición local de «~45 s por pase»**: filas, núcleos,
   caché y si incluía la escritura.
8. **Valores efectivos** de `spark.sql.shuffle.partitions`,
   `spark.sql.files.maxPartitionBytes`, `openCostInBytes` y AQE en el clúster del
   job (leíbles en el propio run y anotables en el resumen de T-02).

## 7. Criterio de aceptación del cambio

Referencia: `plan.md` §3 y RF-2/RF-3 de la spec.

| Gate | Umbral | Cómo se mide |
|---|---|---|
| **RF-2a** (ventana ancha = casi todo el bronce) | `T_inc ≤ 3 m 46 s` por silver | `timings_s` del resumen instrumentado (T-02), no reloj externo |
| **RF-2b** (ventana ≤1 % del bronce) | `T_inc ≤ 75 s` por silver | Ídem, con el escenario pequeño del banco |
| **RF-3** (run completo en dev) | ≤ **22 m 30 s**, 8/8 en verde | Duración del run por la API, con el lote anotado |
| **Invariante de diseño** | `T_inc` con ventana ancha **< `T_full`** (2 m 47 s–3 m 46 s de A2) | Mismo banco, mismo lote |
| **RF-4/RF-5** | Exports A/B idénticos byte a byte, `MERGE` con escrituras, repetición sin cambio de contenido | SHA-256 de los 12 Parquet + `operationMetrics` |
| Gate intermedio de este diagnóstico | ≤**16 tareas** por stage, ≤**6 acciones** por pase y `numFiles` bajo tras P2 | Spark UI + `DESCRIBE DETAIL` + captura SQL/DataFrame |

**Simetría del listón (§3.1).** `T_full` se toma como la duración de **tarea** de
A2 (`[NO VERIFICADO]` el origen exacto de la cifra: la evidencia no copió su
`elapsed_s`), y esa duración incluye el bloque `companies_*`; `T_inc` saldrá de
`timings_s`, que solo cubre `inc.run`. Antes de T-07 hay que fijar una de las
dos: o se compara duración de tarea contra duración de tarea (con `companies_*`
dentro), o se compara `elapsed_s`/`timings_s` contra `elapsed_s`. Queda anotado
como decisión de T-07.

El criterio no se cumple «en local» ni «por cálculo»: se cumple cuando el run A
y el run B de T-07/T-08 lo miden con el resumen instrumentado. Este documento
solo fija **qué** hay que mover y **cómo** se sabrá si se movió.
