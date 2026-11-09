# Seguridad y gobernanza (dominio 7)

> Nota escrita el **2026-11-08** desde la evidencia de la spec 012 y cerrada el
> **2026-11-09** con el resultado del run programado (T-10).

## Qué he practicado

- **Inventario real de Unity Catalog** con CLI/API, sin abrir el SQL warehouse:
  owners, grants directos y efectivos de dos catálogos, 8 esquemas de capa y 39
  tablas; ACL del job; Storage Credential y External Location.
- **Ensayo de privilegio mínimo en dev**: conceder `SELECT` en una tabla,
  verificar con `grants get` y `get-effective`, revocar y comprobar que vuelve a
  `{}`. También probé la herencia concediendo en el **esquema** `ops` y viendo
  cómo la tabla hija lo recibía con `inherited_from_*`.
- **Row filter y column mask** en Databricks Free Edition, sobre una tabla
  sintética de ofertas: perfil autorizado con todas las filas y el contacto
  original; perfil restringido con solo Barcelona y `***`; sin `SELECT`, la
  política no concede acceso; limpieza de políticas y objetos.
- **Contrato de calidad** en `Data_Quality.py`: reglas con `rule_id` estable,
  mapa tabla-columna por capa, predicados compartidos entre PySpark y el DDL, y
  acciones (cuarentena, marca o constraint).
- **Cuarentena Delta** con un motivo nuevo (`company_name_rating`): una fila
  cuyo `company_name` es exactamente `4,0` o `4.0` va a
  `bronze.<fuente>_quarantine`; un nombre que solo contiene dígitos
  (`24/7 Retail`) sigue siendo válido. Precedencia estable:
  `missing_key` > `missing_scraped_at` > `company_name_rating`.
- **CHECK constraints** en silver y gold: `job_id` obligatorio y orden de los
  salarios anualizados, con `IS NULL OR` explícito. Se midió el histórico en
  una única sesión SQL y se aplicaron 20 constraints (10 dev + 10 prod).

## Comprobación real (T-10, 2026-11-09)

El run programado del **2026-11-08 23:00** (`Europe/Madrid`) fue el primero
posterior al deploy de T-09. Cerró **9/9 tareas en SUCCESS** en **14 min 40 s**
(`run_id 762969079133747`). El bronce procesó **11.710 filas** y **0** fueron a
cuarentena; no se creó ninguna tabla `<fuente>_quarantine`. El cierre
`Publish_Run_Summary` escribió la fila de `ops.run_metrics` (`record.status ok`)
y sus cuatro checks quedaron en verde: fuentes 4/4, resumen del bronce,
`data_date 2026-11-07` con 1 día de antigüedad y 0 ficheros pendientes.

Las **10 constraints de producción** siguen presentes y ninguna escritura las
violó. Los grants siguen en `{}`, el job conserva sus 9 tareas, el cron y el
`run_as` persona, y el warehouse quedó **`STOPPED`** antes y después. El
workflow del dashboard (`42581592569`) publicó el export del run con
`data_date 2026-11-07`, dentro del límite de 8 días.

Lo que **no** demuestra este run: la idempotencia (se probó en local en T-06)
ni que la cuenta personal tenga privilegios mínimos (su exceso sigue aprobado
como limitación en D-01).

## Conceptos clave (en mis palabras)

- **El owner no es un grant.** `grants get` devuelve `{}` para el owner porque
  el ownership no se enumera como privilegio; eso **no** significa que no tenga
  acceso. El owner tiene todas las capacidades del objeto de forma implícita y
  puede gestionar sus hijos. `MANAGE` sí es un grant explícito, se puede dar a
  varios principales y hereda a los hijos cuando se concede en un contenedor.
- **`grants get` no es `get-effective`.** El primero muestra lo asignado
  explícitamente; el segundo, lo asignado **más lo heredado**, con
  `inherited_from_name`/`inherited_from_type`. Para inventariar hacen falta los
  dos: solo `get` oculta la herencia; solo `get-effective` oculta de dónde
  viene.
- **`USE CATALOG`/`USE SCHEMA` no dan datos.** Son requisitos de contexto:
  sin ellos la lectura falla, pero no sustituyen a `SELECT`. `MODIFY` exige
  además `SELECT`.
- **Una tabla nueva hereda los grants del contenedor** y su creador pasa a ser
  su owner. Por eso el proyecto concede en tabla salvo los `USE`; un `SELECT`
  en un esquema publicaría también las tablas futuras.
- **Los grants de metastore no heredan** a catálogos/esquemas/tablas (la
  excepción documentada es `READ METADATA`). Los `CREATE_*` del grupo de
  administradores son capacidad administrativa, no acceso a datos.
- **Una CHECK de Delta tiene que ser verdadera para cada fila.** Si la
  expresión evalúa a `NULL`, la fila se rechaza. Por eso las reglas con nulos
  válidos se escriben `IS NULL OR ...`; no se puede confiar en la lógica de
  tres valores.
- **El privilegio mínimo no siempre es alcanzable con el modelo actual.** Si el
  job corre como la persona que es owner de todo y miembro de `admins`, su
  privilegio efectivo es máximo. Se puede medir y aprobar como limitación, pero
  no afirmar que es mínimo. Migrar `run_as` a una identidad dedicada es otra
  spec.
- **La SAS de Power BI está fuera de Unity Catalog.** El informe lee
  `gold/pbi_export` por ADLS con una SAS de solo lectura; ningún grant de UC
  arregla ni rompe esa vía. Un 403 de ADLS no se soluciona con `SELECT`.
- **La cuarentena no es un constraint.** La cuarentena separa lo que el
  contrato ya sabe que es inválido antes de escribir; la constraint es la
  última defensa y hace fallar la escritura ante una violación no prevista.

## Comandos y trucos

```powershell
# Identidad y metadatos (gratis, sin despertar el warehouse)
databricks current-user me --profile vs_code -o json
databricks catalogs get job_offers --profile vs_code -o json
databricks schemas list job_offers --profile vs_code -o json
databricks tables list job_offers silver --profile vs_code --omit-columns -o json
databricks tables get job_offers.gold.fact_offers --profile vs_code -o json

# Grants: directo vs efectivo (los dos hacen falta)
databricks grants get catalog job_offers --profile vs_code -o json
databricks grants get table job_offers.ops.run_metrics --profile vs_code -o json
databricks grants get-effective table job_offers.ops.run_metrics `
  --principal "account users" --profile vs_code -o json

# Cambio y reversión (el inverso es el mismo update con "remove")
'{"changes":[{"principal":"account users","add":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-grant.json
databricks grants update table job_offers_dev.ops.run_metrics `
  --profile vs_code --json "@$env:TEMP\uc-grant.json"
'{"changes":[{"principal":"account users","remove":["SELECT"]}]}' |
  Set-Content -Encoding ascii $env:TEMP\uc-revoke.json
databricks grants update table job_offers_dev.ops.run_metrics `
  --profile vs_code --json "@$env:TEMP\uc-revoke.json"

# Job y ACL: separar lo directo de lo heredado de /jobs/
databricks jobs get 916863262593632 --profile vs_code -o json
databricks jobs get-permissions 916863262593632 --profile vs_code -o json

# Warehouse: debe seguir STOPPED antes y después
databricks warehouses list --profile vs_code -o json

# Constraints por metadatos, sin SQL
databricks tables get job_offers.gold.fact_offers --profile vs_code -o json
```

Trucos que me llevo:

- En PowerShell, para `--json` es más seguro escribir el cuerpo en un fichero
  temporal y pasar `@$env:TEMP\...` que pelear con las comillas.
- `tables get` devuelve las constraints en `properties` con el prefijo
  `delta.constraints.`; es la verificación gratuita después de aplicar DDL.
- El DDL de constraints se genera desde el módulo (`Data_Quality.py`) y los
  tests comparan los ficheros SQL con el módulo: no se edita el SQL a mano.
- Para probar Delta en local: `$env:TML_LOCAL_DELTA=1; python -m pytest
  databricks_notebooks/tests/test_constraints_delta.py -q`, en aislamiento,
  porque necesita su propia sesión Spark.
- En Free Edition, `CREATE GROUP` crea grupos locales que Unity Catalog no
  puede usar; la demo viable fue con **dos usuarios reales** (ruta B).

## Errores que he cometido y qué aprendí

- **Confundir `{}` con «sin acceso».** Al principio parecía que la persona no
  tenía privilegios porque `get-effective` devolvía `{}`. No es eso: el owner
  no se enumera. Aprendí a mirar owner, `get` y `get-effective` juntos.
- **Creer que un `SELECT` en el esquema bastaba para leer.** El ensayo de
  herencia concedió solo `SELECT` y no `USE`; `get-effective` lo mostraba, pero
  la lectura habría fallado sin `USE SCHEMA`. La herencia se demuestra, el
  acceso completo no.
- **Olvidar que un CHECK con `NULL` rechaza.** La forma ingenua
  `salary_min_annual <= salary_max_annual` habría rechazado las filas con
  salario nulo (18.614 en gold de prod). Se corrigió con `IS NULL OR`.
- **Pensar que el run programado demuestra idempotencia.** No: la idempotencia
  se prueba en local con el mismo lote repetido; el run real valida que no hay
  regresión, que los permisos funcionan y que los conteos cuadran.
- **Intentar reducir el privilegio de la cuenta personal revocando grants.**
  No hay grants que revocar: el acceso viene del ownership. Reducirlo exige
  migrar `run_as`, que es una decisión aparte.
- **Tratar la cuarentena como un cajón sin tratamiento.** Cada motivo necesita
  una acción: corregir y reprocesar (el crudo no se toca) o descartar con
  motivo, fecha, conteo y referencia al lote. Sin eso, la cuarentena se
  convierte en basura que nadie revisa.
- **Ambigüedad del `company_name`.** Un `4,0` es un rating desplazado, pero un
  `24/7 Retail` es un nombre legítimo. La regla exige que el valor sea **solo**
  el rating; si hay duda, no se cuarentena.

## Dudas pendientes

- **RBAC de Azure de la Managed Identity**: no se ha podido leer con las
  credenciales de la spec; queda `[NO VERIFICADO]`. No se afirma que sea mínimo.
- **`MERGE WITH SCHEMA EVOLUTION` y `overwriteSchema` con un principal no
  owner**: hoy funciona por ownership; falta probarlo con una identidad
  dedicada.
- **External Location en la raíz del contenedor**: aprobada como desviación
  medida; reducirla a prefijos exige storage nuevo (EL por prefijo y, para
  Azure RBAC, contenedores separados o ABAC).
- **Credenciales de storage duplicadas**: cuatro credenciales del mismo access
  connector no las referencia ninguna EL de este workspace, pero no se puede
  excluir otro recurso fuera de él; quedan documentadas, no borradas.
- **Retención y eventos de `system.access.audit`**: la tabla está disponible
  (2.711 filas en el último día), pero los filtros de la ventana no devolvieron
  eventos de los grants temporales. No se inventa la causa.
- **Fecha de publicación como constraint**: `IsValidPostingDate` es un flag
  (2026–2089) y gold tiene fechas desde 2018; convertir el flag en rechazo es
  una decisión de producto, no un `ADD CONSTRAINT` de esta spec.

## Evidencia

- `docs/governance/access-matrix.md`: inventario, matriz por objeto, herencia,
  exceso medido y `[NO VERIFICADO]`.
- `docs/governance/grants-plan.md`: ensayo de dev, preflight de prod, comandos
  de una futura identidad y rollback.
- `docs/governance/decisions.md`: D-01 (exceso de la cuenta personal), D-02
  (publicación de T-09, EL en la raíz y credenciales duplicadas) y D-03 (cierre
  de la spec tras el run real).
- `docs/governance/quality-inventory.md`: reglas existentes y mapa por capa.
- `docs/governance/constraints-ddl.sql` y `t08-profile-queries.sql`: DDL e
  inventario de la sesión única.
- `docs/governance/evidence.md`: una fila por comprobación, con fecha, comando,
  resultado, identificador y coste.
- `docs/RUNBOOK.md` §11 y §12: operativa de gobernanza y de calidad.
