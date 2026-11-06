# Laboratorio de row filter y column mask (spec 012, T-03)

> **Fecha:** 2026-11-06. **Tarea:** T-03 de `specs/012-Security-And-Governance`.
> **Estado:** **preparado, pendiente de ejecución por la persona.** No se ha
> ejecutado ninguna sentencia: en este equipo no hay credenciales de Free
> Edition y el workspace pagado no se usa.
> **Coste:** 0 €. **Sin datos reales, sin objetos en `job_offers` ni
> `job_offers_dev`, sin tocar el dashboard ni la SAS.**
>
> Este documento es la guía de ejecución del SQL versionado
> `docs/governance/lab-row-filter-column-mask.sql`. La guía **no sustituye a la
> evidencia**: los resultados se anotan en la tabla de §7 con la salida real y su
> query ID.

## 1. Qué demuestra el laboratorio y qué no

**Demuestra** (RF-4), sobre una tabla sintética de 8 ofertas y dos perfiles:

1. que los grants base (`USE SCHEMA`, `SELECT`, `EXECUTE`) son previos a la
   política y que `USE` **no da acceso a filas**;
2. la **herencia real esquema → tabla**: `GRANT SELECT ON SCHEMA` hace ver las 8
   filas sin grant de tabla, `REVOKE` lo retira y `GRANT SELECT ON TABLE`
   devuelve el acceso;
3. que sin política el perfil restringido ve la tabla completa (8 filas, valor
   original de `contact_email`);
4. que con la política el perfil autorizado conserva las 8 filas y el email
   original, y el restringido ve solo 4 filas (Barcelona) y `***` en
   `contact_email`; `company_name` **no** se enmascara y se ve igual en ambos;
5. que un principal **sin `SELECT` sigue sin acceso** aunque el row filter
   exista: la política no concede nada;
6. que la retirada (`DROP ROW FILTER`, `DROP MASK`, revocaciones y `DROP` de
   tabla/funciones/esquema) deja el entorno limpio y comprobable, con **0 grants
   explícitos** antes de borrar los objetos.

**No demuestra**: least privilege del pipeline, seguridad del workspace pagado,
rendimiento, ABAC, ni que las tablas reales puedan llevar políticas. Aplicar
filtros o máscaras a producción está fuera de alcance (RF-4 y §4 del `spec.md`).

## 2. Catálogo y capacidad de Free Edition

### 2.1 El catálogo se parametriza

El SQL **no fija un catálogo**. Usa un único placeholder, `<catalog>`, 17 veces
en el fichero; 11 de ellas son la sentencia `USE CATALOG <catalog>;` (una al
inicio de cada bloque). Antes de ejecutar:

1. ejecutar **una vez** `SHOW CATALOGS;` en el editor y anotar el nombre;
2. sustituir `<catalog>` en **todo** el fichero con buscar/reemplazar;
3. en **cada sesión de perfil**, ejecutar `USE CATALOG <catalog>;` antes de las
   consultas.

Qué catálogo usar: el que devuelva `SHOW CATALOGS` y permita crear un esquema.
Free Edition suele exponer `workspace`; el laboratorio del curso puede exponer
`main` u otro. **No se concede `USE CATALOG`**: en el catálogo del workspace es
implícito para todos los usuarios, así que la prueba de herencia se hace con
`GRANT SELECT ON SCHEMA` (bloque 2 del SQL).

### 2.2 Lo confirmado y lo que decide el preflight

Consultado el **2026-11-06** en la documentación oficial (fuentes en §10).

| # | Afirmación | Estado | Consecuencia |
|---|---|---|---|
| C1 | Row filters y column masks son controles de Unity Catalog: UDF SQL `BOOLEAN` aplicada con `ALTER TABLE … SET ROW FILTER … ON (cols)` y UDF sobre una columna con `ALTER TABLE … ALTER COLUMN … SET MASK` | **Confirmado** | La sintaxis del SQL es la documentada |
| C2 | Para **asignar** la política se necesita `EXECUTE` en la función, `USE SCHEMA` y `USE CATALOG`; sobre tabla existente, ser owner o tener `MANAGE` + `SELECT` | **Confirmado** | Bloques 2 y 3 conceden esos privilegios antes del bloque 4 |
| C3 | Para **consultar** una tabla con política: SQL warehouse, shared access (DBR 12.2+) o dedicated 15.4+ con serverless | **Confirmado** | Free Edition es serverless; el SQL warehouse sirve |
| C4 | Limitaciones: sin `MERGE` si la política tiene anidamiento/agregaciones/ventanas/límites/no determinismo; sin time travel; sin clones; no se aplica a vistas; `REPLACE TABLE` conserva las políticas | **Confirmado** | Notas para T-11 (bloque 7 del SQL y §9) |
| C5 | `CREATE GROUP` crea un **grupo workspace-local**, y los grupos workspace-local **no se pueden usar en Unity Catalog** | **Confirmado** | No hay ningún comando SQL que cree un grupo válido para `GRANT` |
| C6 | Los **grupos de cuenta** solo se crean en la consola de cuenta o por SCIM. `Settings > Identity and access > Groups` crea grupos workspace-local | **Confirmado** | La ruta A no se puede montar desde la UI del workspace ni desde SQL |
| C7 | Free Edition: solo serverless; **1 workspace y 1 metastore**; **sin account console ni APIs de cuenta**; sin SSO/SCIM | **Confirmado** | Sin consola de cuenta, **la ruta A no es viable en Free Edition** |
| C8 | `is_account_group_member()` solo evalúa grupos de **cuenta**; `is_member()` es la función heredada para grupos del workspace | **Confirmado** (la primera; la segunda se usa solo como diagnóstico) | Las funciones de la ruta A usan la primera; `is_member` se consulta en 5.1 |
| C9 | Free Edition soporta row filters/column masks | **No confirmado**: no aparece en la lista de funciones no soportadas, pero tampoco hay una declaración explícita | Lo prueba el preflight 0C.2 |
| C10 | Free Edition permite invitar un segundo usuario | **No confirmado** | Lo prueba la ruta B; si no se puede, plan B |
| C11 | El propietario de la tabla está exento del filtro/máscara | **No confirmado** | Se **observa** en el caso 5.4: 8 filas + `sample_email` original = **no concluyente** (owner exento); 4 filas + `***` = la política también aplica al owner (anotar) |

**Lectura honesta:** en Free Edition la ruta A queda descartada por C5+C6 (no hay
consola de cuenta y SQL/UI solo crean grupos workspace-local). Queda la **ruta B**
si se puede invitar a un segundo usuario; si no, el **plan B** (laboratorio del
curso), que RF-4 admite como fallback válido. No se traslada la demo al
workspace pagado.

## 3. Preflight de 1 minuto

Se ejecuta en dos partes porque la prueba decisiva necesita el esquema del bloque
0B. Tiempo total de sentencias: ~1 minuto; el montaje previo, ~5.

| Paso | Bloque | Qué se ejecuta | Qué se aprende | Si falla |
|---|---|---|---|---|
| P1 | 0A.1–0A.2 | `current_user()`, `current_version().dbsql_version`, `SHOW CATALOGS` | Identidad, versión de SQL y catálogo de trabajo | Sin catálogo editable: **plan B**. El permiso para `GRANT SELECT ON SCHEMA` se comprueba en el paso 10 |
| P2 | 0A.3–0A.4 | `SHOW GROUPS LIKE '*gov_lab*'`, `SHOW GROUPS WITH USER <tu login>` | Grupos existentes y tipo (cuenta vs workspace) | Si `SHOW GROUPS` no está disponible, continuar: la prueba decisiva es P4 |
| P3 | 0A.5 | `CREATE GROUP gov_lab_probe` | Si el workspace permite crear grupos | Error: no hay grupo nuevo; usar ruta B o plan B |
| P4 | 0C.1 | `GRANT SELECT … TO gov_lab_probe` sobre la tabla del laboratorio | **Decisivo**: si UC acepta el grupo, la ruta A es viable en este workspace | Error «workspace-local» o de grant: **ruta A descartada**; ejecutar 0C.3 (limpieza de la sonda) y pasar a B/plan B |
| P5 | 0C.2 | Filtro y máscara sobre `probe_offers` (2 filas) y consulta | **Decisivo**: si el mecanismo funciona en este workspace | Error o 0 filas: **plan B**. **Sonda 0C.2: 2 filas y email original = no concluyente** (owner exento) → ruta B o plan B, nunca éxito |

**Árbol de decisión**

```text
P5 devuelve error o 0 filas .................. plan B (laboratorio del curso)
P5 devuelve 2 filas en la sonda (owner exento) ... no concluyente: ruta B o plan B
P5 pasa y P4 pasa ............................ ruta A (dos grupos de cuenta)
P5 pasa y P4 falla, con 2 usuarios ........... ruta B (dos usuarios existentes)
P5 pasa, P4 falla y 1 solo usuario ........... plan B + limitación registrada
```

## 4. Rutas y qué prueba cada una

| Ruta | Perfiles | Qué prueba | Viabilidad / limitación |
|---|---|---|---|
| **A** | Grupos de cuenta `gov_lab_authorized` / `gov_lab_restricted` | El caso completo de RF-4 con dos sesiones reales | Solo si el administrador de la cuenta los crea en la consola de cuenta o por SCIM. **No viable en Free Edition** (sin consola) ni desde `Settings`/SQL (crean grupos workspace-local que UC rechaza) |
| **B** | Dos usuarios existentes y la tabla privada `access_profiles` | Filtro y máscara por `current_user()` con dos sesiones reales; el perfil sin acceso se prueba revocando `SELECT` | Requiere un **segundo usuario invitado** al workspace. No usa grupos; la política contiene subconsultas (incompatible con `MERGE`, §9) |
| **Plan B** | Los grupos/usuarios que dé el laboratorio del curso | Lo mismo que A, en un workspace que sí lo permite | Se registra la limitación de Free Edition con su evidencia y la demo se hace en el laboratorio |

**Criterio de parada (obligatorio):** si P5 falla o no es concluyente, o si no
existen dos principales distintos, **no se fuerza la demo en Free Edition** y no
se prueba en el workspace pagado. Se documenta la limitación con la salida del
preflight y se pasa al plan B. Si tampoco el plan B está disponible, la tarea
queda `[NO VERIFICADO]`; no se rellena la evidencia con resultados supuestos.

## 5. Ejecución paso a paso

Convención: **A** = administrador/creador de la tabla; **AU** = perfil
autorizado; **RE** = perfil restringido. Cada perfil usa una sesión de navegador
distinta (ventana privada o perfil separado) y ejecuta `USE CATALOG <catalog>;`
al empezar. Guardar cada salida como captura o texto saneado (sin correos reales
si se van a versionar) y anotar el **query ID** del historial del SQL editor.

**Ruta B (checklist):** además de sustituir los correos:

1. **Bloque 2:** comentar las líneas de la ruta A y descomentar las de la ruta B.
2. **Bloque 3:** omitir **3A**; en **3B**, descomentar los cuatro
   `GRANT EXECUTE` (sin ellos el acceso base y el caso opcional 7 quedan
   incompletos).
3. **Bloque 4:** comentar las líneas de la ruta A y descomentar las de la ruta B.
4. **Bloque 6:** comentar las revocaciones de la ruta A y descomentar las de la
   ruta B.

Si se deja la ruta A activa, los `GRANT`/`REVOKE` fallan porque
`gov_lab_authorized` y `gov_lab_restricted` no existen.

| # | Quién | Acción | Resultado esperado | Evidencia |
|---:|---|---|---|---|
| 1 | A | Ejecutar una vez `SHOW CATALOGS;` y sustituir `<catalog>` en todo el SQL (buscar/reemplazar) | Placeholder sustituido; 0 apariciones de `<catalog>` | `SHOW CATALOGS` |
| 2 | A | Ejecutar **0A** (identidad, versión, grupos, `CREATE GROUP gov_lab_probe`) | `current_user()` = tu login; `dbsql_version`; 0 grupos `gov_lab*`; el `CREATE GROUP` funciona | Salida de 0A.1–0A.5 |
| 3 | A | Ejecutar **0B** | Esquema creado; **borra restos de `access_profiles` de una pasada anterior** (seguridad de re-ejecución); 8 filas; 2 ubicaciones; 2 empresas; salarios válidos; emails `@example.com` | Conteo y filas base |
| 4 | A | Ejecutar **0C.1** | Si el `GRANT SELECT` al grupo sonda pasa: ruta A. Si falla: ruta B o plan B | Mensaje de error/salida del grant |
| 5 | A | Ejecutar **0C.2** | `rows_visible_expected_1 = 1`; `masked_value_expected_3_stars = ***` | Dos resultados del probe |
| 6 | A | Ejecutar **0C.3** (limpieza de la sonda), **siempre**; si el `REVOKE` falla porque el grant nunca se aplicó, ejecutar igualmente `DROP GROUP gov_lab_probe` y el resto | `SHOW GROUPS LIKE 'gov_lab_probe'` = 0; sin tabla/funciones de sonda | Salida de 0C.3 |
| 7 | A | Crear perfiles: **1A** (grupos de cuenta en consola/SCIM; no viable en Free Edition) o **1B** (dos usuarios invitados) | Dos principales distintos y verificables | `SHOW GROUPS` o usuarios del workspace |
| 8 | A | Ejecutar **2** solo con las dos sentencias `GRANT USE SCHEMA` (sin ningún `GRANT SELECT`) | Ambos perfiles tienen `USE`, pero aún **no leen** | `SHOW GRANTS` |
| 9 | AU y RE | Intentar `SELECT COUNT(*) FROM governance_lab.offers` | **Error de permisos** en ambos (**caso 1a**) | Mensaje de cada perfil |
| 10 | A | `GRANT SELECT ON SCHEMA governance_lab TO <RE>` y consultar como RE | **RE ve 8 filas** sin grant de tabla (**caso 1b: herencia**) | Salida de RE + `SHOW GRANTS ON SCHEMA` |
| 11 | A | `REVOKE SELECT ON SCHEMA … FROM <RE>` y consultar como RE | **RE vuelve a recibir error de permisos** (**caso 1c**) | Mensaje de RE |
| 12 | A | `GRANT SELECT ON TABLE governance_lab.offers` a RE y AU; consultar como cada uno | **Ambos ven 8 filas** (**caso 1d**); es también la base del caso 2 | Salida de AU/RE |
| 13 | A | Crear funciones (**3A** ruta A; **3B** ruta B) y sus `EXECUTE`, sin aplicar todavía la política; en ruta B, descomentar los cuatro `GRANT EXECUTE` de 3B | Sin error; funciones visibles y 0 políticas aplicadas | `SHOW FUNCTIONS IN governance_lab` |
| 14 | AU y RE | Consultar **antes** de aplicar la política (5.2/5.3 sin bloque 4) | **Ambos ven 8 filas y el email original** (**caso 2**) | Salida de cada perfil |
| 15 | A | Ejecutar **4** (ruta elegida) y las consultas de metadatos | `information_schema.row_filters`/`column_masks` muestran la tabla, `location` y `contact_email` | Dos filas de metadatos |
| 16 | AU | Ejecutar **5.1** y **5.2** | `account_group_authorized = true` (ruta A) o `can_see_all`; **8 filas**, email original, `company_name` original | Salida de AU (**caso 3**) |
| 17 | RE | Ejecutar **5.1** y **5.3** | **4 filas**, solo `Barcelona`, `contact_email = ***`, `company_name` original | Salida de RE (**caso 4**) |
| 18 | A | Ejecutar **5.4** (vista del owner) y anotar lo observado en la misma captura: `visible_rows`, `distinct_emails` y `sample_email` | 8 filas + `sample_email` original = **no concluyente** (owner exento, no es un pase); 4 filas + `sample_email = '***'` = la política también aplica al owner (anotar) | Salida de A (**caso 5.4**) |
| 19 | A → RE → A | Caso negativo: `REVOKE SELECT ON TABLE` a RE, RE consulta, `GRANT SELECT ON TABLE` de nuevo | **Error de permisos** aunque la política exista; tras el re-grant vuelve a ver 4 filas (**caso 5**) | Mensajes y re-grant |
| 20 | A | Ejecutar **6.1–6.3**: quitar políticas, revocar y comprobar **0 grants** antes de borrar | `SHOW GRANTS ON TABLE` y `ON SCHEMA` = **0 filas** | Dos salidas de `SHOW GRANTS` |
| 21 | A | Ejecutar **6.4–6.5**: borrar objetos y verificar | 0 filas en `row_filters`/`column_masks`; esquema y tabla inexistentes | Salidas de limpieza (**caso 6**) |
| 22 | A | Borrar los grupos de cuenta en la consola/SCIM (si se crearon) y comprobar `SHOW GROUPS LIKE 'gov_lab_*'` | 0 grupos `gov_lab*` | Salida final |

**Sobre `EXECUTE` (pregunta guía del temario):** la documentación exige
`EXECUTE` para **asignar** la función y no lo enumera como requisito para
consultar la tabla: la política se evalúa con los derechos del propietario
(*definer's rights*) y el contexto de usuario (`is_account_group_member`) se
resuelve como invocador. Se concede en el bloque 3 por completitud; si se quiere
comprobar, revocar `EXECUTE` a RE y repetir 5.3 (caso opcional 7). No es un caso
mínimo y no bloquea el cierre.

## 6. Resultados esperados por caso mínimo

| Caso | Perfil | Consulta (bloque) | Resultado esperado |
|---|---|---|---|
| 1a. `USE` sin `SELECT` | AU y RE | `SELECT COUNT(*)` con `USE SCHEMA` y sin grants (paso 9) | Error de permisos |
| 1b. Herencia esquema → tabla | RE | Tras `GRANT SELECT ON SCHEMA` (paso 10) | **8 filas sin grant de tabla** |
| 1c. Retirada de la herencia | RE | Tras `REVOKE SELECT ON SCHEMA` (paso 11) | Error de permisos |
| 1d. Grant de tabla | AU y RE | Tras `GRANT SELECT ON TABLE` (paso 12) | 8 filas en ambos |
| 2. Acceso base sin política | AU y RE | 5.2/5.3 antes del bloque 4 (paso 14) | 8 filas; `contact_email` original en ambos |
| 3. Autorizado con política | AU | 5.1 + 5.2 | 8 filas; email original; `company_name` original |
| 4. Restringido con política | RE | 5.1 + 5.3 | 4 filas; solo `Barcelona`; `contact_email = ***`; `company_name` original |
| 5. Principal sin `SELECT` | RE tras el revoke | 5.5 | Error de permisos; tras re-grant, 4 filas y `***` |
| 5.4. Vista del owner | A (owner) | 5.4 (incluye `contact_email`) | **No asumido**: 8 filas + `sample_email` original = **no concluyente** (owner exento); 4 filas + `***` = la política también aplica al owner (anotar) |
| 6. Retirada | A | Bloque 6 | **0 grants** en tabla y esquema antes de borrar; 0 metadatos/esquema después |
| 7 (opcional) | RE sin `EXECUTE` | 5.3 | Anotar si la consulta sigue funcionando; no es caso mínimo |

**Comprobaciones de forma:** el conteo del perfil restringido debe ser
exactamente 4 y el del autorizado 8; `SELECT DISTINCT location` en RE debe
devolver solo `Barcelona`; `contact_email` en RE debe ser `***` y no contener
`@`; `company_name` debe conservar `Acme Analytics`/`Beta Cloud` en ambos. Si
alguna difiere, **el caso no pasa**: se registra la diferencia y no se marca como
demo completada.

## 7. Tabla de evidencia (rellenar durante la ejecución)

El **query ID** se copia del historial del SQL editor (Query history) de la
sesión que ejecutó la consulta; no se inventa ni se deduce. Para `SHOW GRANTS` y
DDL también hay un ID de ejecución en el historial.

| # | Caso | Perfil | Consulta / bloque | Query ID | Resultado esperado | Resultado observado | Salida o captura | Fecha |
|---:|---|---|---|---|---|---|---|---|
| 1 | Catálogo elegido | A | `SHOW CATALOGS` | | Nombre del catálogo; `<catalog>` sustituido | | | |
| 2 | Preflight identidad/versión | A | 0A.1–0A.2 | | `current_user()` y `dbsql_version` visibles | | | |
| 3 | Preflight grupos | A | 0A.3–0A.5 | | 0 grupos `gov_lab*`; `CREATE GROUP` funciona | | | |
| 4 | Preflight grupo UC | A | 0C.1 | | **Ruta A** si el `GRANT` pasa | | | |
| 5 | Preflight políticas | A | 0C.2 | | 1 fila y `***`; 2 filas = no concluyente (sonda) | | | |
| 6 | Limpieza de la sonda | A | 0C.3 | | 0 grupos y 0 objetos de sonda | | | |
| 7 | 1a. `USE` sin `SELECT` | AU y RE | Paso 9 | | Error de permisos | | | |
| 8 | 1b. Herencia esquema → tabla | RE | Paso 10 | | 8 filas sin grant de tabla | | | |
| 9 | 1c. Herencia retirada | RE | Paso 11 | | Error de permisos | | | |
| 10 | 1d. Grant de tabla | AU y RE | Paso 12 | | 8 filas en ambos | | | |
| 11 | 2. Base sin política | AU y RE | Paso 14 | | 8 filas, emails originales | | | |
| 12 | 3. Autorizado | AU | 5.1 + 5.2 | | 8 filas, email y empresa originales | | | |
| 13 | 4. Restringido | RE | 5.1 + 5.3 | | 4 filas, Barcelona, `***`, empresa original | | | |
| 14 | 5. Sin `SELECT` | RE | 5.5 | | Error; tras re-grant, 4 filas | | | |
| 15 | 5.4. Vista del owner | A | 5.4 | | 8 filas + `sample_email` original = no concluyente (owner exento); 4 filas + `***` = política aplicada al owner (anotar) | | | |
| 16 | 6. Cero grants | A | 6.3 | | `SHOW GRANTS` tabla y esquema = 0 filas | | | |
| 17 | 6. Limpieza final | A | 6.4–6.5 | | 0 políticas, 0 objetos, 0 esquema | | | |
| 18 | Grupos borrados | A | Consola/SCIM + `SHOW GROUPS` | | 0 grupos `gov_lab*` | | | |
| 19 | Opcional: sin `EXECUTE` | RE | 5.3 | | Anotar comportamiento | | | |

Saneado antes de versionar: correos y nombres reales → `<autorizado>` /
`<restringido>`; nada de tokens, SAS ni cabeceras de autenticación. Las capturas
deben mostrar la consulta y el resultado, no la barra de cuenta.

## 8. Plan B — laboratorio del curso

Se usa cuando el preflight demuestra que Free Edition no soporta los filtros o
las máscaras (P5), P5 no es concluyente, la ruta A no es viable y no hay un
segundo usuario invitado para la ruta B. Es un fallback **válido** según RF-4 y
RF-10.

1. Ejecutar el mismo SQL en el workspace del laboratorio, sustituyendo
   `<catalog>` por el catálogo que devuelva su `SHOW CATALOGS` (puede ser `main`
   u otro). No se crea nada en el workspace pagado.
2. Usar los usuarios/grupos que proporcione el curso. Si hay tres principales,
   añadir un tercer perfil `gov_lab_noaccess` **sin** `SELECT` y usarlo para el
   caso 5, en lugar de revocar y re-conceder.
3. Repetir los pasos 1–22 y rellenar la misma tabla de evidencia, con query ID.
4. Anotar en el encabezado de la evidencia: edición del workspace, versión de
   SQL, si los perfiles son grupos de cuenta o usuarios y qué no se pudo
   reproducir.
5. Registrar la limitación de Free Edition en `evidence.md` con la salida real
   del preflight (no una suposición) y reflejarla en las notas de T-11.

## 9. Notas para T-11 (incompatibilidades si se aplicara al proyecto)

Documentadas en `filters-and-masks` (2026-10-09); el SQL las repite en el
bloque 7:

- **No determinismo y `MERGE`**: `is_account_group_member`, `current_user` y
  `session_user` no son deterministas. Las políticas con funciones no
  deterministas no son compatibles con `MERGE`; la ruta A (grupos) y la ruta B
  (subconsultas) quedan en ese supuesto. El comportamiento exacto de la ruta A
  **no se ha probado** → `[NO VERIFICADO]` para T-11.
- **Referencias a tablas con política**: una política no puede referenciar una
  tabla que tenga un row filter o una máscara activos. La tabla de mapeo de la
  ruta B (`access_profiles`) debe quedar **sin política**.
- **Time travel**: no soportado con filtros/máscaras a nivel de tabla.
- **Clones** profundos y superficiales: no soportados.
- **Delta Lake APIs y acceso por ruta** a los ficheros: no soportados.
- **Vistas**: no se les puede aplicar filtro/máscara.
- **Particiones**: `DELETE`/`UPDATE`/`MERGE` sobre tabla particionada con
  política en la columna de partición exige DBR 17.2+.
- **Compute dedicado**: DBR 15.4 LTS+ con serverless; lecturas en 15.4–16.2 y
  escrituras solo en 16.3+ con patrones soportados (`MERGE INTO`).
- **`REPLACE TABLE`** conserva filtros y máscaras existentes.
- La política **no concede acceso**: sigue haciendo falta `GRANT`.
- Una tabla admite **un** row filter; una columna, **una** máscara.
- **Separación de funciones:** el propietario de la tabla puede retirar su propia
  política (`DROP ROW FILTER`/`DROP MASK`). Si se quiere que un owner no pueda
  eludirla, la vía es ABAC, no el filtro a nivel de tabla.

## 10. Fuentes consultadas (2026-11-06)

| Tema | URL | Última actualización |
|---|---|---|
| Row filters y column masks (requisitos, limitaciones) | https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/ | 2026-10-09 |
| Aplicación manual, ejemplos y *definer's rights* | https://docs.databricks.com/aws/en/data-governance/unity-catalog/filters-and-masks/manually-apply | 2026-10-08 |
| Cláusula `ROW FILTER` (privilegios y sintaxis) | https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-syntax-ddl-row-filter | 2026-09-11 |
| `CREATE GROUP` (grupo workspace-local) | https://docs.databricks.com/aws/en/sql/language-manual/security-create-group | 2026-09-11 |
| Grupos workspace-local (no utilizables en Unity Catalog) | https://docs.databricks.com/aws/en/admin/users-groups/workspace-local-groups | 2026-09-11 |
| Limitaciones de Free Edition (sin account console) | https://docs.databricks.com/aws/en/getting-started/free-edition-limitations | 2026-10-18 |
| Alta en Free Edition | https://docs.databricks.com/aws/en/getting-started/free-edition | 2026-09-11 |
| `is_account_group_member` (solo grupos de cuenta) | https://docs.databricks.com/aws/en/sql/language-manual/functions/is_account_group_member | 2026-09-11 |
| ABAC frente a filtros a nivel de tabla (la política no concede acceso) | https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/abac-vs-rls-cm | 2026-09-24 |
| `INFORMATION_SCHEMA.ROW_FILTERS` (auditoría) | https://docs.databricks.com/aws/en/sql/language-manual/information-schema/row_filters | 2026-09-11 |

## 11. Verificación independiente del SQL (sin ejecutarlo)

Comprobaciones que puede hacer el `verifier` con búsquedas de texto sobre
`lab-row-filter-column-mask.sql`:

1. **Catálogo parametrizado:** **0** apariciones de `main` (ni en SQL ni en
   comentarios); **17** apariciones del placeholder `<catalog>` y **11**
   sentencias `USE CATALOG <catalog>;` (una al inicio de cada bloque). La guía
   explica sustituirlo una sola vez con buscar/reemplazar.
2. **Sin datos reales ni del proyecto:** 0 coincidencias de `job_offers`,
   `job_offers_dev`, `infojobs`, `linkedin`, `indeed`, `PBI_EXPORT_SAS`,
   `WEB_EXPORT_SAS`. Los únicos correos son de `example.com`; los `site` son
   `lab-board-a`/`lab-board-b`.
3. **Herencia real:** existe `GRANT SELECT ON SCHEMA governance_lab` y su
   `REVOKE` en el bloque 2, además del `GRANT SELECT ON TABLE` posterior.
4. **Cobertura de retirada:** en la ruta A, cada `GRANT` tiene su `REVOKE`
   (10/10, contando la sonda y la herencia). La ruta B añade cuatro
   `GRANT EXECUTE` que están **comentados por defecto** y se activan al seguir
   la checklist de §5; sus `REVOKE` están comentados en el bloque 6. 6 funciones
   creadas y 6 `DROP FUNCTION IF EXISTS`; `SET/DROP ROW FILTER` 2/2;
   `SET/DROP MASK` 2/2.
5. **Cierre con 0 grants:** el bloque 6 ejecuta `SHOW GRANTS ON TABLE` y
   `SHOW GRANTS ON SCHEMA` **antes** de borrar los objetos, y exige 0 filas.
6. **Sonda con limpieza separada:** el bloque 0C.3 revoca, borra el grupo
   `gov_lab_probe` y elimina tabla/funciones de la sonda aunque 0C.1/0C.2 fallen.
7. **Aislamiento y re-ejecución:** todas las tablas y funciones están bajo
   `governance_lab`; ningún `INSERT` escribe fuera de ese esquema. El bloque 0B
   borra `access_profiles` antes del test de herencia, para que una segunda
   pasada no exponga la tabla de mapeo al perfil restringido con
   `GRANT SELECT ON SCHEMA`.
8. **Sintaxis:** paréntesis equilibrados excluyendo comentarios (0); sin
   `DROP GROUP IF EXISTS` (no existe); sin `GRANT`/`REVOKE` con varios
   principales (la sintaxis admite uno solo).
9. **Que no hay resultados inventados:** este documento y `evidence.md` no
   afirman ningún resultado de ejecución; solo expectativas y el estado
   «preparado, pendiente de ejecución».
