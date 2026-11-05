# Plan técnico: seguridad, gobernanza y calidad de datos

> Borrador del **2026-11-05**, rama `spec/012-security-governance` (desde `main`,
> `74f3c11`), worktree `talent-market-lens-012`.
>
> Regla de oro: **primero inventario y rollback; después grants, cuarentena y
> constraints**. Ninguna revocación ni restricción se improvisa sobre producción.

## 1. Objetivo y límites

Dejar un modelo de acceso mínimo para las identidades de automatización y
almacenamiento —dedicadas o compartidas, según T-01— y medido/aprobado para la cuenta
personal, reproducible y reversible, y un contrato de calidad que separe los errores
esperables sin perder filas válidas y bloquee las violaciones no previstas. Row
filters y column masks se practican en una tabla sintética de Free Edition —o del
laboratorio del curso como fallback—, no sobre el pipeline real.

No se cambian claves, deduplicación, semántica de ofertas válidas, exports,
cadencia, compute ni layout. El presupuesto de la 009 es taxativo en el arranque:
**0 runs de dev, 1 sesión SQL < 10 min y ≤ 1,3 €**. El run semanal de producción
es la comprobación real y se contabiliza en el presupuesto ordinario de producción.

## 2. Estado y reconocimiento previo

La 008/010 dejaron bronce con upsert idempotente y cuarentena Delta por fuente; la
011 añadió `ops.run_metrics`, resumen del bronce, una novena tarea de cierre y
health rules. Power BI consume Parquet de ADLS con SAS, no Unity Catalog. Estos
mecanismos se reutilizan.

T-01 hará un reconocimiento **solo local + API/CLI**:

1. Objetos y owners de `job_offers_dev` y `job_offers`: catálogo, esquemas, tablas,
   funciones y volumes si existen.
2. `run_as`/identidad efectiva del job —hoy la persona—, grants directos y
   privilegios heredados por catálogo/esquema; tipo e identidad de la Storage
   Credential (Managed Identity o service principal), si es dedicada o compartida,
   y permisos de la External Location, sin leer secretos.
3. Operaciones que el código ejecuta realmente en cada capa: `SELECT`, `MODIFY`,
   creación/evolución de tablas, escritura de cuarentena y `ops`.
4. Grants de otros principales, sin tocar roles de administración ni ownership en
   este reconocimiento.
5. Esquema y motivos actuales de las cuatro cuarentenas, reglas ya presentes en
   bronce/silver/gold y tests disponibles.
6. Capacidades de la cuenta Free Edition y principales/grupos de laboratorio
   existentes, sin crear identidades ni grupos.

Las salidas se saneian y se guardan como evidencia. Lo que la API no pueda
responder queda `[NO VERIFICADO]`; no se sustituye por una sesión SQL adicional.

## 3. Modelo de acceso objetivo

La matriz final se deduce del inventario; no se copian permisos genéricos a ciegas.
La estructura mínima será:

| Principal | Ámbito | Capacidad que debe conservar | Restricción buscada |
|---|---|---|---|
| Identidad `run_as` del job (hoy la persona) | catálogo del entorno y esquemas usados | `USE CATALOG`, `USE SCHEMA` y solo los `SELECT`/`MODIFY`/creación necesarios por capa | los grants directos se ajustan a la operación; el exceso efectivo derivado del rol admin/ownership se inventaría y se aprueba como limitación; migrar `run_as` requiere decisión aparte |
| Identidad de Storage Credential | credencial y External Location del lake | solo el acceso de storage requerido por las rutas del proyecto | sin permisos de gestión o rutas ajenas; T-01 confirma si es dedicada o compartida y si es Managed Identity o service principal; si su Azure RBAC no es comprobable, se registra el alcance real como `[NO VERIFICADO]`/desviación |
| Persona/administración | objetos que ya administra | administrar y aplicar rollback | no perder el último camino de administración |
| Lectores reales, si existen | objetos concretos que consuman | lectura mínima | sin escritura ni gestión |
| Perfiles de laboratorio | catálogo/esquema de demo en Free Edition | lectura sobre la tabla sintética y ejecución de funciones de política | ningún acceso al workspace pagado |

Power BI queda fuera de la matriz de lectores UC porque su acceso real es la SAS de
ADLS. La comprobación de que esa vía sigue funcionando forma parte del cierre.

### 3.1. Decisiones de grants

- Conceder en el nivel más específico que cubra la operación (tabla u objeto); usar
  catálogo o esquema solo cuando todos los hijos presentes y futuros deban recibir
  ese privilegio. En caso contrario, grant en el objeto concreto.
- `USE CATALOG`/`USE SCHEMA` habilitan el uso del contenedor, no lectura de datos;
  se conceden junto a los privilegios mínimos correspondientes.
- Ownership y `MANAGE` no se reparten para simplificar: solo se conservan donde
  sean necesarios para administrar.
- Evitar `ALL PRIVILEGES` para identidades de automatización. Si el inventario
  demuestra que una operación exige un privilegio adicional, se añade a la matriz
  con evidencia; no se devuelve un grant amplio como atajo. Si la identidad de
  almacenamiento no es un service principal, no se crea uno solo para practicar.
- Si `run_as` es la misma persona administradora, no se revoca su vía de
  administración: se mide el exceso efectivo que aporta su rol y se registra como
  limitación/desviación aprobada. No se afirma que sus privilegios efectivos sean
  mínimos y no se crea un service principal ni se migra `run_as` por defecto.
- El least privilege se exige a las identidades de automatización y almacenamiento;
  T-01 confirma si son dedicadas o compartidas. En las dedicadas se reduce a lo
  necesario; en las compartidas se documenta el alcance y el exceso no reducible como
  limitación aprobada, sin afirmar que quedaron mínimas. No se aplica a la cuenta
  personal que administra el workspace.
- Preferir grupos si ya existen. No se introduce un IdP/SCIM ni una arquitectura de
  grupos nueva en el workspace pagado para esta demo.

### 3.2. Artefactos y rollback

La matriz, los comandos CLI/API y sus inversos quedan versionados en la carpeta de
la spec. Antes de cada cambio se guarda el estado anterior saneado. El orden es:

1. validar comandos y permisos del operador;
2. aplicar primero grants mínimos que falten;
3. comprobar privilegios efectivos y la identidad `run_as`;
4. revocar únicamente los sobrantes enumerados en la matriz;
5. repetir la lectura de metadatos y guardar diff;
6. si falla una operación, restaurar el estado anterior con los comandos inversos.

T-02 aplica o ensaya estos cambios solo en dev y deja preparados los comandos de
prod, pero **no revoca prod**. La aplicación en producción se hace en T-09, con
preflight por operación, cerca de la ventana semanal y con el rollback listo. El
rollback de dev se ensaya en T-04. Para producción, el último recurso sigue siendo
el job manual pausado `704156530681034` documentado en
`specs/007-ELT-As-Code/cutover.md`.

No se usa SQL para grants, owners ni ACL de Unity Catalog: se consultan por CLI/API,
que es gratuita. La sesión SQL única se reserva para perfiles de calidad, constraints
y auditoría. Ninguna salida incluye PAT, SAS, secretos ni cabeceras de autenticación.

## 4. Laboratorio de row filters y column masks

### 4.1. Datos y objetos

En Free Edition —o en el laboratorio del curso si aquella no soporta la demo— se
crea un esquema aislado con una tabla Delta pequeña de ofertas sintéticas. Como
mínimo contendrá `job_id`, `site`, `location`, `salary_min`,
`salary_max` y un campo de contacto ficticio que pueda enmascararse. No se copian
correos, nombres ni otras filas reales del landing.

Se versionan los SQL de creación, dos perfiles de acceso, consultas y limpieza. El
filtro y la máscara serán funciones SQL deterministas basadas en el principal o
grupo actual; la función exacta se elegirá tras comprobar la versión disponible en
Free Edition.

### 4.2. Matriz de prueba

| Caso | Perfil autorizado | Perfil restringido |
|---|---|---|
| Grants previos | uso de contenedores + `SELECT` + `EXECUTE` necesarios | mismo acceso base; la política decide la restricción |
| Row filter | ve todas las filas de la demo | ve solo el subconjunto permitido |
| Column mask | ve el contacto original ficticio | ve el valor enmascarado |
| Ausencia de grant base | sin acceso aunque la política exista | sin acceso |
| Limpieza | se retiran filtro/máscara, funciones, tabla y grants del laboratorio | ídem |

La política no sustituye al `GRANT`: primero se concede acceso base y después se
restringe. Se capturan consultas positivas y negativas y los errores esperados.
Free Edition es la vía primaria. Si no soporta la función o no permite dos perfiles,
se documenta la limitación con evidencia y se usa el laboratorio del curso como
fallback válido; no se traslada la demo al workspace pagado ni se abre otra sesión
SQL.

## 5. Contrato de calidad

### 5.1. Registro único de reglas

Se crea un módulo en `databricks_notebooks/` (nombre propuesto: `Data_Quality.py`)
con funciones puras y un registro de reglas en Python (constantes/dataclasses
versionadas en el módulo, **no una tabla Delta**) para las reglas nuevas y el DDL.
Se reutilizan los predicados ya probados de `Upsert_Job_Offers.py`,
`Enrich_Job_Offers_Dataframes.py` y `Prepare_Gold.py`; no se reescriben para
unificarlos si eso amplía el riesgo. Cada regla define:

| Campo | Contenido |
|---|---|
| `rule_id` | identificador estable, en inglés |
| capa y fuentes | dónde se evalúa |
| expresión | predicado puro y, cuando proceda, expresión SQL Delta |
| acción | cuarentena, corrección reproducible, marca/neutralización o fallo |
| evidencia | columnas de origen, motivo, fecha y hash/referencia |
| tests | válida, inválida, nula, límite y repetición |

Las expresiones nuevas no se duplican: separación y DDL consumen la misma
definición. Cuando una regla ya existe, el registro referencia su función actual
en vez de copiarla. Si Delta no admite una expresión concreta, la regla conserva
su validación previa y se documenta por qué no hay constraint.

### 5.2. Reglas iniciales a contrastar

- Conservar `missing_key` y `missing_scraped_at` y cualquier motivo ya existente.
- `job_id` (o la clave normalizada correspondiente) no nulo; si su tipo es `STRING`,
  además `trim(job_id) <> ''`. La columna concreta sale del mapa tabla-columna por
  capa, no de un nombre supuesto.
- Salarios: la ELT ya marca `salary_quality` y pone a `NULL` los importes
  anualizados cuando el rango original es inválido. Se añaden los tests que faltan
  para rangos invertidos/no positivos y se evalúa el orden cuando ambos importes son
  no nulos, con nombres por capa: `salary_min_annual`/`salary_max_annual` en silver y
  `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR` en `gold.fact_offers`. Antes de
  cualquier DDL se fija un mapa explícito tabla-columna por capa; no se asume que
  los nombres coincidan. No se rechaza la oferta solo por conservar el rango
  original marcado como inválido.
- Fechas: silver ya rellena `posted_date` ausente y gold ya tiene
  `IsValidPostingDate` (años 2026–2089). Se contrasta ese comportamiento con los
  datos antes de proponer una constraint; no se fija otro rango arbitrario ni se
  convierte el flag en rechazo sin evidencia.
- `company_name` formado exclusivamente por un rating decimal (caso `4,0`): solo se
  cuarentena cuando el patrón y el contexto de columnas hagan el desplazamiento
  inequívoco. Un nombre parecido no basta.

Cada regla tendrá una decisión explícita; las descartadas quedan con medición y
motivo. No se normaliza ni repara un campo inventando su valor.

### 5.3. Cuarentena e idempotencia

Se reutilizan `bronze.<fuente>_quarantine` y su esquema siempre que pueda expresar
el motivo y el origen; cualquier ampliación será aditiva y compatible. Se conserva
la clave actual `(_row_hash, _quarantine_reason, _source_file)` y una precedencia
estable que asigna **un motivo principal por fila**, de modo que:

- repetir el mismo lote no multiplica filas;
- la precedencia entre motivos concurrentes queda probada;
- `válidas + filas únicas enviadas a cuarentena` reconcilia con la entrada, también
  cuando el mismo lote se repite;
- una fila inválida nunca entra en el `MERGE` del bronce válido;
- una ejecución con todas las filas inválidas no fabrica un bronce vacío aparente.

La cuarentena local de scrapers sigue siendo una defensa anterior y distinta: esta
spec no mezcla sus conteos con los de Delta.

### 5.4. Tratamiento posterior

El RUNBOOK incluirá, por `rule_id`, consulta de inventario, decisión y pasos para:

1. **corregir y reprocesar:** conservar el crudo, generar una entrada corregida con
   referencia al origen y dejar que el upsert idempotente haga su trabajo;
2. **descartar:** registrar motivo, fecha, conteo y referencia al lote antes de
   cualquier borrado;
3. **reconciliar:** demostrar que no se duplican ofertas válidas ni se pierde una
   fila sin explicación.

No se ejecuta una limpieza histórica masiva. El perfil de cuarentenas históricas por
fuente y motivo (conteos y rango temporal, sin copiar payloads sensibles) se obtiene
en la sesión SQL única y es la base de RF-6; se trata como máximo una muestra de los
motivos nuevos para demostrar el procedimiento y el resto queda inventariado.

## 6. Constraints Delta

Las constraints son la última defensa, no el mecanismo de cuarentena. Se generan
nombres estables `chk_<tabla>_<regla>` y DDL idempotente a nivel operativo: primero
se inspeccionan las constraints existentes y solo se añaden las ausentes.

Antes de generar DDL se fija un mapa explícito tabla-columna por capa: silver usa
`salary_min_annual`/`salary_max_annual` y `gold.fact_offers` usa
`SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR`; `job_id` y las fechas se mapean igual.
No se asume que una regla comparta nombre entre capas ni se copia el DDL de una a
otra sin ese mapa.

Secuencia por regla:

1. perfilar dev y prod en la sesión SQL única;
2. guardar conteos de violaciones y decidir tratamiento;
3. probar en local la expresión, los nulos y el caso negativo;
4. comprobar en local que la escritura actual y el código pendiente evitan las
   violaciones previstas;
5. añadir la constraint si el histórico ya la cumple o se ha tratado con evidencia;
6. comprobar su presencia por metadatos y el rechazo de una escritura inválida en
   una tabla temporal/local, nunca ensuciando la tabla real.

`ALTER TABLE ... ADD CONSTRAINT` valida las filas existentes. Por tanto, está
prohibido ejecutar el DDL si el perfil muestra violaciones sin tratar. Si una
constraint no puede añadirse dentro de la única ventana SQL sin riesgo, se pospone
y se marca `[NO VERIFICADO]`; no se abre otra sesión para forzar el cierre.

## 7. Sesión SQL única

Se preparan todas las sentencias antes de despertar el warehouse 2X-Small. La
sesión agrupa, en este orden:

1. perfil de violaciones y nulos de las reglas candidatas en dev/prod;
2. perfil de cuarentenas históricas por fuente y motivo —conteos y rango temporal,
   sin extraer ni copiar payloads sensibles—, necesario para RF-6;
3. inventario de constraints existentes y consulta de auditoría si está disponible;
4. DDL previamente aprobado para constraints sin violaciones pendientes;
5. comprobaciones inmediatas necesarias que no puedan hacerse por API;
6. captura de resultados y parada/confirmación `STOPPED`.

Antes de despertar el warehouse se avisa del coste (~0,43 €, 10 minutos facturables)
y se pide confirmación explícita; sin esa confirmación no se abre. Duración objetivo
< 10 minutos; coste esperado ~0,43 € y techo de spec 1,3 €. Grants, owners y ACL se
consultan por CLI/API; tablas y constraints, por metadatos gratuitos cuando sea
posible. Si el tiempo no alcanza, se priorizan los perfiles de cuarentenas y
violaciones y se pospone el resto; no se abre otra sesión. No se usa esta sesión para
grants, owners ni ACL de Unity Catalog, ni para la demo de filtros/máscaras o
consultas ad hoc no relacionadas.

## 8. Validación sin runs de dev

| Nivel | Comprobación | Coste |
|---|---|---|
| Local | reglas puras, cuarentena, reconciliación, idempotencia, DDL y casos nulos/límite | 0 € |
| Local Delta | constraints y `MERGE` sobre tablas temporales cuando el runtime lo soporte | 0 € |
| Bundle | `databricks bundle validate -t dev` y `-t prod`; el deploy no ejecuta | 0 € |
| API/CLI | grants efectivos, owners, constraints y estado del job/warehouse | 0 € |
| Free Edition | herencia, row filter, column mask y consultas por perfil | 0 € |
| Producción | primer run programado posterior, ausencia de regresión, conteos coherentes, `ops.run_metrics`, dashboard y warehouse | presupuesto ordinario |

Un deploy no es evidencia de ejecución y un run programado no demuestra idempotencia:
esta se prueba en local (T-06) y se hereda de las specs 008/010/011. El run real
demuestra ausencia de regresión y conteos coherentes; repetirlo solo se plantea si
la persona autoriza el coste adicional. Si una incertidumbre exige un run de dev, se
detiene la tarea y se pide autorización para modificar el presupuesto; no se consume
silenciosamente.

## 9. Presupuesto

| Bloque | Runs dev | Sesiones SQL | Otros | Coste dev |
|---|---:|---:|---|---:|
| T-01 inventario | 0 | 0 | API/CLI | 0 € |
| T-02–T-04 acceso y laboratorio | 0 | 0 | API/CLI + Free Edition | 0 € |
| T-05–T-07 calidad y tests | 0 | 0 | local | 0 € |
| T-08 sesión agrupada | 0 | **1** | warehouse < 10 min | ~0,43 € |
| T-09 publicación | 0 | 0 | validate/merge/deploy/API | 0 € |
| T-10 run programado | 0 | 0 | producción ordinaria | 0 € de dev |
| T-11 cierre | 0 | 0 | local/API | 0 € |
| **Total** | **0** | **1** | — | **≤ 1,3 €** |

Cada gasto se anota en `docs/costs.md`. Un intento fallido también cuenta. Cualquier
run o sesión extra requiere aprobación previa y actualización de los tres documentos
de la spec.

## 10. Riesgos

| Riesgo | Mitigación |
|---|---|
| Revocar un grant necesario y detener el job | diff previo, grants mínimos antes de revocar, rollback inverso y run programado como gate |
| Aplicar revocaciones de prod demasiado pronto | T-02 solo aplica/ensaya en dev y prepara comandos; T-09 hace preflight por operación y aplica cerca de la ventana semanal |
| El deploy se pasa de la ventana del domingo 23:00 | completar antes; si no, el siguiente run será una semana después y se replanifica, sin run de dev |
| `run_as` es la persona administradora y su exceso no es reducible | medirlo y registrarlo como limitación/desviación aprobada; no afirmar least privilege ni revocar la administración |
| Un run programado no demuestra idempotencia | probarla en local (T-06) y heredarla de 008/010/011; el run real solo valida ausencia de regresión |
| Dejar a la persona sin administración | conservar un camino de owner/admin verificado antes y después |
| Un constraint bloquea históricos legítimos | perfil previo, nulos/límites explícitos y no añadir con violaciones sin tratar |
| `company_name` numérico sea un nombre legítimo | regla inequívoca con contexto, fixtures límite y descarte documentado si hay ambigüedad |
| Cuarentena duplicada al reprocesar | dedup por referencia de fila + motivo y prueba de repetición |
| Reparar filas cambia el crudo o duplica ofertas | crudo inmutable, reproceso idempotente y reconciliación |
| Free Edition no soporta filtros/máscaras o no hay dos perfiles | comprobar en T-01; usar laboratorio del curso y marcar límites, nunca workspace pagado |
| La única sesión SQL se queda corta | sentencias preparadas y priorizadas; lo no esencial queda pendiente, sin segunda sesión |
| Filtros/máscaras rompen `MERGE` o exports | se mantienen exclusivamente en laboratorio |
| Sin run de dev aparece un fallo en producción | maximizar Delta local, validar bundle y rollback; autorización expresa si hace falta cambiar presupuesto |

## 11. Secuencia y puntos de parada

1. **Reconocimiento (T-01):** matriz inicial, reglas existentes y viabilidad del
   laboratorio. Revisión independiente.
2. **Gobernanza (T-02–T-04):** cambios de dev, evidencia de herencia/auditoría y
   demo Free Edition. **Parar** para revisar `git log -p` del grupo.
3. **Calidad (T-05–T-08):** contrato, cuarentena, constraints y única sesión SQL.
   **Parar** para revisar `git log -p` del grupo antes de publicar.
4. **Publicación y comprobación real (T-09–T-10):** T-09 valida desde la rama; el
   merge checkpoint `--no-ff` a `main` se hace solo con petición explícita tras la
   revisión del grupo; el deploy de prod sale de `main` y los grants/revocaciones
   aprobados se aplican con preflight cerca de la ventana semanal. T-10 es el run
   programado real. No hay deploy de prod antes del merge.
5. **Cierre (T-11):** documentación final, merge final `--no-ff` solo si hay commits
   posteriores al checkpoint de T-09, y tag `spec-012` y cierre solo después de T-10.

Todo deploy o cambio de producción debe completarse antes del domingo 23:00
(`Europe/Madrid`). Si se pasa esa hora, el siguiente run programado será una semana
después y se replanifica, sin consumir un run de dev.

La persona aprueba previamente cualquier cambio de notebooks, módulos, YAML,
grants/revocaciones, constraints o recursos de laboratorio, con fichero/objeto,
diff o comando y motivo. Cada tarea se commitea solo tras tests y revisión
independiente del diff; no se hace push sin petición explícita. Cada tarea lleva su
estimación en `tasks.md`; si el diff funcional (previsto o real) supera ~400 líneas,
se divide antes de continuar.
