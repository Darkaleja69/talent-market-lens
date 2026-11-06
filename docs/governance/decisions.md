# Decisiones de gobernanza (spec 012)

> Registro de decisiones explícitas que afectan al modelo de acceso y a la
> evidencia de la spec 012. Cada decisión tiene fecha, alcance, motivo, quién
> aprueba y qué queda abierto. No sustituye a `spec.md`/`plan.md`: los desarrolla.

| ID | Fecha | Tema | Estado |
|---|---|---|---|
| D-01 | 2026-11-06 | Exceso de la cuenta personal, `run_as` y alcance de T-02/T-09 | **Aprobada** |

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
| External Location sobre la raíz del contenedor | Desviación medida; **pendiente de aprobación explícita** | Antes de T-09 |
| Retirada de las 4 Storage Credentials duplicadas | Preflight preparado; **decisión en T-09** | T-09 |
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
