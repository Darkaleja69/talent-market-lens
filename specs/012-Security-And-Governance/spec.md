# Especificación funcional: seguridad, gobernanza y calidad de datos

> **Borrador (2026-11-05).** Rama `spec/012-security-governance`, creada desde
> `main` (`74f3c11`) en el worktree `talent-market-lens-012`. Es la última spec
> del track de certificación: practica el **dominio 7 (seguridad y gobernanza)** y
> absorbe las prácticas de **cuarentena Delta y CHECK constraints** pendientes del
> backlog del roadmap. Presupuesto asignado por la 009: **0 runs de dev + 1 sesión
> SQL, ≤ 1,3 €**.

## Contexto y objetivo

El proyecto ya usa Unity Catalog, dos catálogos (`job_offers_dev` y
`job_offers`), tablas Delta por capas y una SAS de solo lectura para el informe.
El job tiene hoy `run_as` de la persona, no de un service principal; el acceso al
lake usa una Storage Credential y una External Location cuya identidad (Managed
Identity o service principal) se comprobará. El modelo de acceso no se ha auditado
y reducido de forma explícita: hace falta saber qué principal puede hacer qué sobre
cada catálogo, esquema y tabla, qué permisos se heredan y cómo volver atrás sin
dejar el job o el informe sin acceso.

La calidad también está repartida entre varias defensas. Los scrapers y el landing
ya separan ficheros o filas inválidas; el bronce Delta ya tiene tablas
`<fuente>_quarantine` para determinados errores de ingesta. Falta cerrar el
contrato: cubrir los valores inválidos del backlog (como un rating desplazado a
`company_name`), definir qué ocurre con cada fila en cuarentena y añadir
restricciones Delta en silver/gold sin convertir un dato malo conocido en un fallo
semanal inesperado.

**Objetivo (resultado único):** que el pipeline de ofertas quede **gobernado con
privilegios mínimos —o con el exceso real medido y aprobado— en las identidades de
automatización y almacenamiento, y con un contrato de calidad ejecutable**, ambos
demostrados con evidencia y con el run semanal y el dashboard intactos; el exceso de
la cuenta personal se medirá y aprobará del mismo modo. El aprendizaje de row
filters y column masks se hará sobre una tabla de laboratorio: son controles de
demostración, no una restricción nueva sobre los datos que publica el proyecto.

La cuarentena y los constraints forman parte del mismo resultado: las reglas
claras separan o tratan los errores esperables antes de la escritura, y Delta
impide que una violación no prevista termine persistida como dato válido.

## Estado de partida

### Existe y se conserva

- Catálogos `job_offers_dev` y `job_offers`; esquemas por capas, incluidos
  `bronze`, `silver`, `gold` y `ops`.
- Job semanal gestionado como Asset Bundle, con 9 tareas tras la 011 y `run_as`
  de la persona. La identidad usada por la Storage Credential/External Location
  está fuera del repositorio y se inventariará sin exponer secretos.
- Bronce incremental con `MERGE`, idempotencia y tablas Delta de cuarentena por
  fuente. La 011 publica también sus filas ingeridas y en cuarentena en
  `ops.run_metrics`.
- Cuarentena previa de ficheros y de filas en el pipeline local de scrapers.
- Informe Power BI alimentado por `gold/pbi_export` en ADLS mediante una SAS de
  solo lectura; no necesita permisos de lectura sobre las tablas Unity Catalog.
- Runtime del job compatible con las funciones Delta utilizadas; la disponibilidad
  concreta de row filters/column masks se comprobará en Free Edition antes de la
  demo.
- Suites locales de notebooks y pipeline, y Delta local ya viable en este equipo
  para pruebas de `MERGE` y restricciones cuando el conector lo permita.

### Pendiente de reconocer sin darlo por supuesto

- Owners, grants directos, grants heredados y privilegios efectivos de cada
  principal en dev y prod; en particular, si la identidad de ejecución y la de
  almacenamiento —dedicada o compartida, service principal o Managed Identity—
  tienen más privilegios de los que necesitan.
- Privilegios necesarios para cada operación real del job: crear tablas o
  esquemas, leer, escribir, ejecutar `MERGE`, evolucionar esquema y mantener
  cuarentena/métricas. También se decidirá y documentará si se conserva el
  `run_as` actual o se propone una migración separada; esta spec no lo cambia por
  defecto.
- Cobertura exacta de los motivos de cuarentena actuales y volumen de filas
  históricas pendientes de tratamiento.
- Reglas de calidad ya aplicadas en las transformaciones de silver/gold y
  violaciones históricas que impedirían añadir un `CHECK CONSTRAINT`.
- Existencia de grupos/principales utilizables para la demo de laboratorio. No se
  crearán identidades ni grupos nuevos sin una necesidad acreditada en T-01.

Toda afirmación que no pueda comprobarse mediante código, CLI o API quedará
marcada `[NO VERIFICADO]`; no se abrirá el SQL warehouse para este reconocimiento.

## Requisitos funcionales

### RF-1 — Inventario de acceso y política por capa

**Criterio (EARS):** Cuando se modifique la gobernanza, deberá existir una matriz
versionada con owners, principales, privilegios directos y heredados, operación que
los justifica y privilegio mínimo necesario para cada objeto de dev y prod; los
cambios y su rollback deberán quedar definidos **antes** de revocar nada, y las
consultas de metadatos se harán por API/CLI sin despertar el SQL warehouse.

### RF-2 — Privilegio mínimo de las identidades de automatización

**Criterio (EARS):** Cuando la identidad de automatización o la de almacenamiento
—dedicada o compartida, según confirme T-01— acceda a recursos del pipeline, deberá
disponer únicamente de los privilegios necesarios para sus operaciones; no podrá
conservar `ALL PRIVILEGES`, `MANAGE`, ownership ni grants o acceso de storage más
amplios de lo justificado por la matriz. Si la Storage Credential usa service
principal, se le aplicará el estándar de least privilege; si usa Managed Identity, el
mismo inventario a esa identidad. Si su Azure RBAC no puede comprobarse o
reducirse con las credenciales disponibles, se registrará el alcance real como
`[NO VERIFICADO]` o desviación explícita aprobada, sin afirmar least privilege y sin
bloquear el cierre por no crear accesos o credenciales nuevas. Cuando `run_as` sea la misma
persona que administra el workspace, se conservará su vía de administración: el
exceso efectivo derivado de su rol se inventariará y se registrará como limitación o
desviación explícita aprobada, sin afirmar que sus privilegios efectivos quedaron
mínimos. El `run_as` actual no se migrará ni se creará un service principal sin una
decisión explícita y separada; toda reducción aplicable tendrá rollback documentado y
comprobará que el job y el dashboard siguen funcionando.

### RF-3 — Herencia, reproducibilidad y auditoría

**Criterio (EARS):** Cuando se conceda un privilegio, se aplicará en el nivel más
específico que cubra la operación —tabla u objeto concreto— y solo se usará
catálogo o esquema cuando todos los hijos presentes y futuros necesiten ese
privilegio, dejando explícitas las excepciones; cada grant o revocación deberá ser
reproducible mediante comandos versionados, verificable mediante metadatos y
reversible. Si hay tablas de auditoría accesibles,
la sesión SQL única incluirá una consulta que relacione los cambios con su actor;
si no, quedará `[NO VERIFICADO]` y se conservará la evidencia de API/CLI.

### RF-4 — Demo controlada de row filter y column mask

**Criterio (EARS):** Cuando dos principales o grupos de laboratorio con distinto
perfil consulten la misma tabla de ofertas sintética, el row filter deberá mostrar
solo las filas autorizadas y la column mask deberá ocultar el campo elegido al
perfil restringido, mientras el perfil autorizado conserva el valor original; la
demo deberá incluir grants previos, herencia, consulta positiva y negativa,
retirada de los objetos y evidencia, **sin aplicar filtros ni máscaras a las
tablas de producción ni alimentar el dashboard desde ellas**. La vía primaria será
Free Edition; si no soporta filtros/máscaras o no permite dos perfiles, el
laboratorio del curso será un fallback válido con la limitación y su evidencia,
nunca un workspace pagado.

### RF-5 — Contrato de calidad y cuarentena Delta

**Criterio (EARS):** Cuando una fila de ingesta tenga una estructura o un valor
inválido cubierto por el contrato, deberá quedar en
`bronze.<fuente>_quarantine` con fuente, motivo estable, fecha de ingesta y los
datos necesarios para auditarla; las filas válidas del mismo lote deberán seguir
su curso, la repetición del lote no duplicará la cuarentena y los totales
`válidas + filas únicas enviadas a cuarentena` reconciliarán con la entrada,
también en lotes repetidos. Cada fila tendrá un motivo
principal según una precedencia estable, conservando `_source_file` en su
referencia de deduplicación. Como mínimo se mantendrán `missing_key` y
`missing_scraped_at`, y se cubrirá el desplazamiento de un rating a `company_name`
cuando sea inequívoco según la regla acordada, como segunda defensa tras la
coherencia local ya existente.

### RF-6 — Tratamiento posterior de la cuarentena

**Criterio (EARS):** Cuando se revisen filas cuarentenadas, cada motivo deberá
tener una acción operativa definida —corregir y reprocesar, o descartar con
evidencia—, con reconciliación de conteos e idempotencia; ninguna fila podrá
eliminarse definitivamente sin dejar motivo y referencia al lote de origen. El
inventario histórico por fuente y motivo —conteos y rango temporal, sin extraer
ni copiar payloads sensibles— será la base del tratamiento; el resto quedará
inventariado. El tratamiento histórico se limitará a los motivos incorporados por
esta spec y no reescribirá el crudo del landing.

### RF-7 — CHECK constraints en silver/gold

**Criterio (EARS):** Cuando se escriba una fila que incumpla una regla de calidad
acordada, Delta deberá rechazarla mediante constraints con nombres estables en las
tablas silver/gold seleccionadas. Como mínimo se evaluarán `job_id` obligatorio
—no nulo y, si su tipo es `STRING`, también `trim(job_id) <> ''`—, el orden de los
salarios cuando ambos existan y fechas dentro de límites justificados por los
datos. La regla salarial deberá respetar el diseño actual: un rango original
inválido se marca y neutraliza en los importes anualizados, por lo que la
constraint candidata será sobre esos importes normalizados, no una prohibición
automática de conservar el dato original. Los nombres no se asumen iguales entre
capas: silver usa `salary_min_annual`/`salary_max_annual` y `gold.fact_offers`
usa `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR`; antes de generar DDL deberá
existir un mapa explícito tabla-columna por capa. Antes de añadir cada constraint
deberán medirse las violaciones existentes y definirse su tratamiento; si una
regla no puede aplicarse sin descartar ofertas legítimas, quedará fuera con la
medición y el motivo, sin maquillar el criterio.

### RF-8 — Calidad previa a la restricción y fallo visible

**Criterio (EARS):** Cuando una transformación produzca una violación prevista,
la fila deberá recibir la acción explícita decidida en RF-5/RF-6 antes de llegar
a la escritura restringida —cuarentena, corrección reproducible o marca/
neutralización ya definida—; cuando aparezca una violación no prevista, el
constraint deberá hacer fallar la escritura en lugar de descartar silenciosamente
la fila. Las reglas de negocio y su clasificación vivirán en módulos comprobables,
y los notebooks solo coordinarán su aplicación.

### RF-9 — Despliegue y comprobación real sin regresiones

**Criterio (EARS):** Cuando se desplieguen los cambios, el primer run programado
posterior deberá cerrar las 9 tareas, demostrar ausencia de regresión con conteos
coherentes y las métricas del run, dejar las cuarentenas y constraints
verificadas, publicar el dashboard con la `data_date` esperada y conservar el SQL
warehouse `STOPPED` antes y después; también deberá demostrarse que el modelo de
permisos no impide ninguna operación necesaria. La idempotencia se prueba en
local (T-06) y se hereda de las specs 008/010/011: un run programado no la
demuestra por sí solo. Repetir el run real solo será necesario si la persona
autoriza el coste adicional. La spec no se cerrará solo con tests o con un deploy.

### RF-10 — Presupuesto y entornos

**Criterio (EARS):** Cuando se cierre la spec, no deberá haberse consumido ningún
run de dev y solo podrá haberse utilizado **una sesión SQL agrupada de menos de
10 minutos**, con coste total de desarrollo ≤ **1,3 €**; los grants y metadatos se
consultarán por API/CLI, la demo se hará en Free Edition —o en el laboratorio del
curso si Free Edition no soporta filtros/máscaras o dos perfiles, con la limitación
registrada— y las pruebas de reglas se harán en local. Cualquier desviación exigirá
aprobación previa explícita y la actualización de spec, plan y tareas antes de
gastar. Todo gasto se anotará en `docs/costs.md` con motivo, identificador y coste.

### RF-11 — Práctica del temario y notas

**Criterio (EARS):** Cuando se cierre la spec, deberá existir
`docs/databricks-de/notes/07-security-governance.md`, escrita desde la evidencia,
con Unity Catalog, ACLs, ownership, herencia, least privilege, row filters,
column masks, CHECK constraints y errores/dudas reales; el roadmap deberá
reflejar qué prácticas del backlog quedaron cerradas y cuáles quedaron fuera,
con el bronce deduplicado cerrado por 008/010 y el dominio 7 descrito con
`run_as` persona e identidad de storage real.

## Requisitos no funcionales

- **Stack simple:** Databricks CLI/API, SQL, PySpark y Delta existentes; ninguna
  dependencia nueva. Free Edition se usa solo como laboratorio.
- **Lógica e interfaz:** reglas puras y generación/validación de políticas en
  módulos `*.py` con tests; notebooks y comandos operativos solo coordinan.
- **Datos y trazabilidad:** el crudo de ADLS no se modifica; toda corrección vuelve
  a pasar por el camino de ingesta y conserva su referencia de origen.
- **Sin cambio de semántica válida:** las ofertas que cumplen el contrato conservan
  sus claves, columnas, deduplicación y resultado analítico.
- **Seguridad documental:** ningún PAT, SAS, secreto, token ni salida con
  credenciales se guarda en el repositorio o en evidencias.
- **Idioma:** identificadores y comentarios de código en inglés; documentación y
  mensajes a la persona en español.
- **Verificación independiente:** cada tarea funcional requiere tests aplicables y
  revisión independiente del diff antes de su commit atómico.

## Fuera de alcance

- Aplicar row filters o column masks a las tablas reales del proyecto; la demo es
  suficiente para el dominio y evita cambiar lo que ve el job o Power BI.
- Adoptar ABAC, aprovisionar SCIM/IdP, rediseñar identidades, red, cifrado o
  secretos, y migrar el metastore: no son necesarios para el objetivo medido.
- Cambiar la deduplicación de negocio de oro por localización y ventana temporal.
  Sigue siendo una práctica pendiente del backlog, pero requiere una spec con ese
  único resultado porque cambia qué ofertas se consideran distintas.
- Rehacer el bronce deduplicado: ya se cerró en la 008 y se validó en la 010.
- Modificar scrapers o su cuarentena local salvo que una prueba demuestre un fallo
  en el contrato que esta spec necesita; en ese caso se propondrá una corrección
  separada y no se arreglará durante el diagnóstico.
- Cambiar cadencia, compute, layout, streaming, exports o dashboard salvo la
  evidencia mínima necesaria para comprobar esta spec.
- Reprocesar todo el histórico o borrar cuarentenas antiguas sin una decisión
  explícita por motivo y evidencia de reconciliación.

## Criterios de finalización

- Matriz de acceso aplicada y revisada; identidad de almacenamiento clasificada como
  dedicada o compartida desde T-01; reducciones aplicadas en las identidades de
  automatización y almacenamiento cuando sea posible, o exceso/alcance real medido y
  registrado como limitación/desviación aprobada —incluido `[NO VERIFICADO]` si el
  Azure RBAC no puede comprobarse—, con rollback y sin pérdida de acceso
  administrativo. No se bloquea el cierre por no crear accesos o credenciales nuevas.
  Si no hay service principal, quedará acreditado el mecanismo real en lugar de crear
  uno solo para cumplir el temario.
- Demo de row filter y column mask ejecutada en Free Edition —o en el laboratorio
  del curso como fallback con la limitación registrada— con perfiles
  autorizado/restringido y objetos retirados al terminar.
- Contrato de calidad implementado con tests; filas válidas e inválidas y
  repetición del lote reconciliadas; idempotencia probada en local y heredada;
  tratamiento de cuarentena documentado.
- Constraints elegidas presentes en las tablas acordadas, con sus comprobaciones
  negativas y con cualquier regla descartada justificada por mediciones.
- Suites afectadas en verde, revisión independiente por tarea y commits atómicos.
- Primer run programado posterior en verde, sin regresión y con conteos
  coherentes, dashboard publicado, warehouse `STOPPED` y coste registrado.
- Nota `07-security-governance.md`, roadmap y documentación operativa al día;
  merge final `--no-ff` solo si hay commits posteriores al checkpoint de T-09, y tag
  `spec-012` solo después de la comprobación real; no se etiqueta antes de T-10.
