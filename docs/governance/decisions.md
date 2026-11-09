# Decisiones de gobernanza (spec 012)

> Registro de decisiones explícitas que afectan al modelo de acceso y a la
> evidencia de la spec 012. Cada decisión tiene fecha, alcance, motivo, quién
> aprueba y qué queda abierto. No sustituye a `spec.md`/`plan.md`: los desarrolla.

| ID | Fecha | Tema | Estado |
|---|---|---|---|
| D-01 | 2026-11-06 | Exceso de la cuenta personal, `run_as` y alcance de T-02/T-09 | **Aprobada** |
| D-02 | 2026-11-08 | Publicación de T-09: deploy de prod, EL en la raíz y credenciales duplicadas | **Aprobada** |
| D-03 | 2026-11-09 | Cierre de la spec 012 tras la comprobación real de T-10 | **Aprobada** |

## D-01 — El exceso de la cuenta personal se aprueba como limitación; `run_as` no se migra en esta spec

**Fecha:** 2026-11-06.
**Tareas afectadas:** T-02 (aplicación/ensayo), T-09 (preflight de prod), T-10
(comprobación real).
**Requisitos afectados:** RF-1, RF-2, RF-3 y RF-10.

### Contexto

`access-matrix.md` (T-01) midió que:

- la cuenta personal es **owner de los 2 catálogos, los 8 esquemas de capa y las
  39 tablas**, y miembro del grupo `admins`;
- el `run_as` del job de prod (`916863262593632`) y del de dev
  (`320947022571947`) es esa misma cuenta;
- no hay ningún grant directo en los objetos del proyecto (`{}` en catálogo,
  esquemas y tablas), ni service principal de automatización;
- la Storage Credential es una **Azure Managed Identity** dedicada al proyecto,
  con la External Location `landing-loc` sobre la **raíz** del contenedor y
  compartida entre dev y prod;
- hay **4 Storage Credentials duplicadas** de la misma Managed Identity que
  ninguna EL referencia.

Reducir el privilegio efectivo de la cuenta exigiría migrar `run_as` a otra
identidad (y crear esa identidad). RF-2 lo prohíbe sin una decisión separada y
la persona ha decidido no hacerlo en esta spec.

### Decisión

1. **Se aprueba como limitación/desviación documentada** el exceso de la cuenta
   personal: owner de todo y miembro de `admins`. Su privilegio efectivo es
   máximo por ownership y rol, no mínimo. **No se afirmará least privilege**
   sobre ella en ningún documento de la spec.
2. **No se migra `run_as`** en esta spec: sigue siendo la persona. **No se crea**
   ningún service principal, identidad ni grupo nuevo. La migración a una
   identidad de automatización dedicada queda anotada como **candidata a spec
   futura**, con su propio presupuesto.
3. **Único cambio de grants autorizado y ejecutado en T-02:** conceder
   temporalmente `SELECT` a `account users` sobre la tabla **dev**
   `job_offers_dev.ops.run_metrics`, verificar con `grants get` y
   `get-effective`, **revocar inmediatamente** y comprobar que vuelve a `{}`.
   Resultado: aplicado y revertido el 2026-11-06
   (`evidence/t02-grant-*.txt`, `t02-rollback-after.txt`,
   `t02-final-state.txt`). No queda grant residual.
4. **Producción no se toca en T-02:** ningún grant, revocación, cambio de
   storage, job o bundle. Los comandos de prod quedan **preparados con
   preflight/rollback** en `grants-plan.md`, sin ejecutar.
5. **Las 4 Storage Credentials duplicadas no se retiran en T-02.** Se dejan para
   **T-09**, que ejecutará el preflight de `grants-plan.md` §5 y decidirá con
   aprobación explícita. La credencial en uso
   (`landing-loc_azuremanagedidentity_1791406770615`) no se toca.
6. **La External Location en la raíz del contenedor** sigue siendo una
   **desviación medida pendiente de aprobación explícita antes de T-09**. D-01 no
   la aprueba: solo deja constancia de que reducirla exige cambios de storage
   (EL por prefijo y, para Azure RBAC, contenedores separados o condiciones ABAC)
   fuera del alcance de T-02.
7. **El RBAC de Azure de la Managed Identity sigue `[NO VERIFICADO]`**: no se ha
   leído con las credenciales de esta tarea y no se afirma que sea mínimo.

### Motivo

- El objetivo de RF-2 es el mínimo **de las identidades de automatización y
  almacenamiento**, no degradar la vía de administración de la persona. Revocar
  ownership o `admins` dejaría el job y el informe sin su camino de
  administración y no reduciría el riesgo real sin migrar la identidad.
- Crear un service principal «de adorno» está expresamente prohibido por la spec
  y añadiría una identidad nueva sin operación que la justifique.
- El estado medido no tiene grants que revocar; el trabajo útil de T-02 es
  demostrar que un grant se aplica en el nivel mínimo, se verifica y se revierte,
  y dejar los comandos de prod listos y reversibles.

### Quién aprueba

La persona propietaria del proyecto y administradora del workspace, el
**2026-11-06**, en la revisión previa de T-02. La aprobación cubre los puntos 1–7
en los términos escritos aquí.

### Qué queda abierto

| Tema | Estado | Cuándo |
|---|---|---|
| Migración de `run_as` a una identidad de automatización dedicada | Candidata a **spec futura**; requiere crear identidad, cambiar el job y probar permisos con un run | Fuera de la 012 |
| External Location sobre la raíz del contenedor | Desviación medida; **aprobada por D-02** (2026-11-08) | Cerrado antes de T-09 |
| Retirada de las 4 Storage Credentials duplicadas | Preflight preparado; **D-02 decide dejarlas documentadas** | T-09 (D-02) |
| RBAC de Azure de la Managed Identity | `[NO VERIFICADO]` | Se documenta como limitación; no se inventa |
| Identidad duplicada en `admins` (H11) | No se decide aquí ni se toca; sigue como está | Sin cambio en la 012 |
| Grants de una futura identidad de automatización | Comandos e inversos preparados en `grants-plan.md` §4, no aplicados | Spec futura |

### Consecuencias

- T-02 cierra **sin afirmar** que las identidades quedaron mínimas: devuelve el
  estado `{}` y el ensayo de rollback como evidencia.
- T-09 **no aplicará grants en prod** con el modelo actual: su paso de grants es
  una **verificación de no regresión** (todo sigue `{}`, owners intactos, job de
  9 tareas). Si el preflight falla, T-09 no se cierra y no se aplica nada.
- T-10 valida que el exceso aprobado no impide el run ni el dashboard; no se usa
  para afirmar least privilege.

## D-02 — Publicación de T-09: deploy de prod, EL en la raíz y credenciales duplicadas

**Fecha:** 2026-11-08.
**Tareas afectadas:** T-09 (publicación), T-10 (comprobación real).
**Requisitos afectados:** RF-1, RF-2, RF-3, RF-9 y RF-10.

### Contexto

El preflight de T-09 (2026-11-08) confirmó el estado medido en T-01/T-02: prod
sin grants (`{}`), owners en la cuenta personal, job de 9 tareas con el cron
`50 0 23 ? * SUN` (`Europe/Madrid`, `UNPAUSED`) y `run_as` persona, 20
constraints presentes y warehouse `STOPPED`. El deploy de prod solo sube
`databricks_notebooks/Data_Quality.py` y `Upsert_Job_Offers.py`; no cambia
YAML, job, cron, compute ni tareas.

Quedaban abiertos tres puntos de D-01: la aprobación explícita de la External
Location en la raíz, la retirada de las 4 Storage Credentials duplicadas y el
paso de grants de T-09.

### Decisión

1. **Se aprueba el merge checkpoint `--no-ff` de la rama a `main` y el deploy de
   producción desde `main`**, con `databricks bundle validate -t prod` repetido
   desde `main` antes del deploy. El deploy no ejecuta el job.
2. **No se aplica ningún grant ni revocación en prod**: el preflight devuelve
   `{}` en catálogo, esquemas, tablas, External Location y Storage Credential.
   El paso de grants de T-09 es una verificación de no regresión.
3. **Se aprueba la External Location `landing-loc` sobre la raíz del contenedor
   como desviación medida.** Reducirla a prefijos exige EL por prefijo y, para
   Azure RBAC, contenedores separados o condiciones ABAC; queda fuera de la 012
   y no se afirma least privilege sobre el almacenamiento.
4. **Las 4 Storage Credentials duplicadas se dejan y se documentan** como
   pendiente de higiene. El preflight demuestra que ninguna External Location de
   este workspace las referencia, pero no puede excluir otro recurso fuera del
   workspace; no se borra nada en producción.

### Motivo

- El objetivo de T-09 es publicar los cambios aprobados sin consumir compute de
  desarrollo y conservar la vía de vuelta atrás.
- No hay grants que cambiar con el modelo actual (D-01), y crear o retirar
  credenciales sin poder verificar todo su uso añadiría riesgo sin mejorar el
  resultado de la spec.
- La aprobación de la EL en la raíz cierra el punto que D-01 dejó pendiente y
  permite registrar la desviación sin maquillarla.

### Quién aprueba

La persona propietaria del proyecto y administradora del workspace, el
**2026-11-08**, antes del merge y del deploy de producción.

### Qué queda abierto

| Tema | Estado | Cuándo |
|---|---|---|
| Retirada de las 4 Storage Credentials duplicadas | Pendiente de higiene, `[NO VERIFICADO]` fuera del workspace | Spec futura |
| Reducción de la External Location a prefijos | Desviación aprobada; no se cambia en la 012 | Fuera de la 012 |
| RBAC de Azure de la Managed Identity | `[NO VERIFICADO]` | Se documenta como limitación |
| Migración de `run_as` a una identidad dedicada | Candidata a spec futura | Fuera de la 012 |

### Consecuencias

- T-09 se cierra con el deploy de prod y la evidencia del preflight, sin runs de
  dev y sin SQL.
- T-10 queda a la espera del run programado del **2026-11-08 a las 23:00**
  (`Europe/Madrid`); el tag `spec-012` y el cierre no se hacen antes de que ese
  run esté en verde.

## D-03 — Cierre de la spec 012 tras la comprobación real de T-10

**Fecha:** 2026-11-09.
**Tareas afectadas:** T-10 (comprobación real) y T-11 (cierre).
**Requisitos afectados:** RF-9, RF-10 y RF-11.

### Contexto

El run programado posterior al deploy de T-09 (`762969079133747`) cerró **9/9**
tareas en **SUCCESS** en 14 min 40 s. El bronce procesó 11.710 filas y **0**
fueron a cuarentena; `Publish_Run_Summary` escribió la fila de
`ops.run_metrics` con `record.status ok` y los cuatro checks en verde. Las 10
constraints de producción y los grants `{}` siguen presentes, el job conserva
sus 9 tareas, cron y `run_as` persona, y el warehouse quedó `STOPPED` antes y
después. El workflow del dashboard `42581592569` publicó el export con
`data_date 2026-11-07` (2 días, límite 8).

### Decisión

1. **Se aprueba el cierre de la spec 012**: T-10 pasa a completada con la
   evidencia del run real y T-11 con la documentación actualizada.
2. **Se aprueba el merge final `--no-ff` a `main`**, el tag anotado
   `spec-012`, el borrado de la rama `spec/012-security-governance` y la
   retirada del worktree `talent-market-lens-012`, según `AGENTS.md`.
3. **No se repite el run** ni se abre una segunda sesión SQL: el resultado es
   suficiente y el coste adicional no está autorizado.
4. Se mantienen como limitaciones/desviaciones aprobadas el exceso de la
   cuenta personal (D-01), la EL en la raíz y las credenciales duplicadas
   (D-02), y el RBAC de Azure `[NO VERIFICADO]`.

### Quién aprueba

La persona propietaria del proyecto y administradora del workspace, el
**2026-11-09**, al pedir el cierre de la spec tras revisar el run y el
dashboard.

### Consecuencias

- La spec 012 se cierra con **0 runs de dev**, **1 sesión SQL** (~0,43 €) y un
  run ordinario de producción (~0,18 € estimados); no se imputa al techo de dev.
- T-11 queda cerrada y el track de certificación queda completo.
