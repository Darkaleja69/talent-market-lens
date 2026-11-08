# Tareas: seguridad, gobernanza y calidad de datos

> Borrador del **2026-11-05**. Rama `spec/012-security-governance` (desde `main`,
> `74f3c11`), worktree `talent-market-lens-012`. La persona decide y ejecuta las
> prácticas de laboratorio; el asistente orienta, prepara el trabajo y revisa. La
> implementación de código se delega y cada tarea exige comprobación y revisión
> independiente del diff antes de su commit.
>
> **Aprobación previa:** antes de tocar notebooks, módulos, YAML, grants,
> ownership, constraints o recursos de Databricks se presenta fichero/objeto,
> diff o comando exacto, motivo y rollback, y se espera el OK. Los tests y la
> documentación local quedan fuera de esta regla.
>
> **Presupuesto (009 §17):** **0 runs de dev + 1 sesión SQL < 10 min**, con coste
> de desarrollo ≤ **1,3 €** (esperado ~0,43 € con el warehouse 2X-Small). Grants y
> metadatos por API/CLI, demo en Free Edition —o laboratorio del curso como
> fallback— y reglas en local. El run programado de producción se imputa al
> presupuesto ordinario de producción. Cualquier gasto adicional exige aprobación
> previa y actualizar `spec.md`, `plan.md` y `tasks.md`.
>
> **Consumido al arrancar:** 0 runs de dev, 0 sesiones SQL, 0 €.

## Convención de commits y evidencia

- Un commit atómico por tarea al pasar su comprobación
  (p. ej. `feat(governance): T-02 privilegios mínimos por capa`).
- Suite principal: `python -m pytest databricks_notebooks/tests -q`. Si se toca el
  pipeline local, también `python -m pytest scrapers-pipeline/tests -q`.
- Evidencia: una fila por comprobación en `evidence.md` (fecha, tarea, comando o
  prueba, resultado, identificador y coste), sin tokens, SAS ni datos sensibles.
- **Parar tras T-04 y tras T-08** para que la persona revise `git log -p` del
  grupo. No continuar con el grupo siguiente sin petición explícita.
- **Estimación y tamaño:** cada tarea indica una estimación razonable. Si el diff
  funcional (previsto o real) supera ~400 líneas, la tarea se divide antes de
  continuar; tests y documentación no cuentan como lógica funcional.
- No hacer push, merge ni tag sin la petición correspondiente. El tag `spec-012`
  solo llega después de la comprobación real de T-10.

## 1. Reconocimiento y gobernanza (dominio 7)

- [x] **T-01 — Inventario gratuito de identidades, privilegios y calidad actual**
  - **Objetivo:** sustituir supuestos por una línea base comprobada de Unity
    Catalog, almacenamiento y reglas existentes, sin abrir el warehouse.
  - **Comprobaciones:**
    - Owners y grants directos/heredados de `job_offers_dev`, `job_offers`, sus
      esquemas (`bronze`, `silver`, `gold`, `ops`) y tablas, mediante API/CLI.
    - `run_as` y ACL del job; tipo e identidad de la Storage Credential y permisos
      de la External Location. Confirmar si hay service principal o Managed
      Identity y si la identidad de almacenamiento es dedicada o compartida, sin
      consultar ni copiar secretos.
    - Mapa de operaciones del código por capa y privilegio que requieren; acceso de
      Power BI por SAS como vía independiente de Unity Catalog.
    - Motivos y dedup actuales de `Upsert_Job_Offers.py`, reglas salariales de
      `Enrich_Job_Offers_Dataframes.py`, `IsValidPostingDate` de gold y ausencia de
      constraints versionadas. Existencia/contenido de cuarentenas vivas solo si
      puede obtenerse por metadatos gratuitos; el resto se perfila en T-08.
    - Soporte de row filters/column masks y principales/grupos disponibles en Free
      Edition. Solo lectura: comprobar capacidad sin crear identidades ni grupos.
  - **Preguntas guía:** ¿qué privilegio hereda realmente cada identidad? ¿qué
    operación justifica `MODIFY` o creación? ¿dónde aparece el service principal
    del roadmap? ¿qué reglas ya marcan datos y no deben convertirse en rechazos?
  - **Pistas/documentación:** `databricks.yml`, `resources/elt.job.yml`,
    `docs/RUNBOOK.md`, `Upsert_Job_Offers.py` y
    [privileges reference](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference).
  - **Hecho cuando:** existe `access-matrix.md` con estado actual, operación
    justificante, privilegio objetivo, fuente y rollback por principal/objeto, incluida
    la clasificación dedicada/compartida de la identidad de almacenamiento;
    `quality-inventory.md` recoge las reglas existentes y los desconocidos quedan
    `[NO VERIFICADO]`. No se ha cambiado ningún grant ni recurso.
  - **Evidencia:** salidas saneadas de CLI/API, rutas de código y fila en
    `evidence.md`. **0 €, 0 runs, 0 SQL.**
  - **Estimación:** 3–4 h; diff documental ~200–300 líneas.

- [x] **T-02 — Aplicar privilegios mínimos en dev y preparar prod**
  - **Objetivo:** ensayar en dev el modelo mínimo justificado por T-01 y dejar
    preparados los comandos de producción, sin migrar `run_as` ni crear un service
    principal de adorno. Las identidades de automatización/almacenamiento —dedicadas
    o compartidas, según T-01— se ajustan donde sea posible; si su Azure RBAC no
    puede comprobarse o reducirse, se registra el alcance real como
    `[NO VERIFICADO]`/desviación aprobada, sin afirmar least privilege y sin bloquear
    el cierre por no crear accesos o credenciales nuevas. Si `run_as` es la persona
    administradora, su exceso efectivo se mide y se propone como limitación aprobada.
  - **Entregable:** comandos CLI/API versionados y su inverso; cambios aplicados o
    ensayados en dev; grants mínimos que falten antes de revocar sobrantes; diff de
    privilegios efectivos de dev y comandos de prod preparados con preflight; alcance
    real de Azure RBAC/storage documentado o `[NO VERIFICADO]`; decisión explícita
    sobre cualquier privilegio que deba conservarse.
  - **Restricciones:** **no conceder ni revocar en prod en esta tarea**; conservar un
    camino de administración verificado; no tocar la SAS de Power BI salvo para
    comprobar su alcance documentado; no almacenar tokens; no ejecutar el job para
    probar permisos.
  - **Preguntas guía:** ¿el grant debe estar en catálogo, esquema o tabla? ¿lo
    heredarán también objetos futuros que no deberían? ¿qué privilegio permite
    `MERGE WITH SCHEMA EVOLUTION` y cuál permite crear la cuarentena?
  - **Pistas/documentación:**
    [Manage privileges](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/manage-privileges/)
    y [best practices](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/best-practices).
  - **Hecho cuando:** los cambios de dev están aplicados o ensayados, la matriz y
    el estado comprobable de dev coinciden, el rollback está ensayado al menos sobre un
    grant de dev no crítico, los comandos de prod están preparados con
    preflight/rollback y la persona conserva administración. Si `run_as` es la persona
    administradora, el exceso efectivo derivado de su rol queda inventariado y
    registrado como limitación/desviación aprobada; no se afirma que sus privilegios
    efectivos sean
    mínimos. Cualquier reducción de Azure RBAC/storage que no pueda comprobarse se
    registra como `[NO VERIFICADO]`/desviación aprobada, no se inventa y no se afirma
    least privilege.
  - **Evidencia:** before/after de API de dev, comandos de prod preparados, prueba de
    rollback en dev y fila en `evidence.md`. **0 €.**
  - **Estimación:** 2–3 h; diff ~150–250 líneas entre comandos y documentación.

- [x] **T-03 — Demo de row filter y column mask en Free Edition o laboratorio del curso**
  - **Objetivo:** demostrar control de filas y columnas sobre una misma tabla
    sintética con un perfil autorizado y otro restringido.
  - **Entregable:** SQL versionado de creación, grants base, funciones de política,
    consultas positivas/negativas y limpieza; ejecución guiada por la persona en
    Free Edition como vía primaria. Sin datos reales ni objetos en el workspace
    pagado.
  - **Casos mínimos:** herencia de grants; acceso base sin política; perfil
    autorizado con todas las filas y valor original; perfil restringido con
    subconjunto y máscara; principal sin `SELECT` que sigue sin acceso; retirada de
    políticas y objetos.
  - **Preguntas guía:** ¿por qué una máscara no concede acceso? ¿qué permisos exige
    ejecutar la función? ¿qué ocurre si se quita el filtro? ¿qué incompatibilidad
    tendría con `MERGE`, time travel o clones si se aplicara al proyecto?
  - **Pistas/documentación:**
    [Row filters and column masks](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/filters-and-masks/)
    y [aplicación manual](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/filters-and-masks/manually-apply).
  - **Hecho cuando:** la matriz de casos tiene resultados y capturas/salidas
    saneadas, los objetos se han limpiado y las limitaciones están escritas. Si
    Free Edition no soporta filtros/máscaras o no permite dos perfiles, se usa el
    laboratorio del curso como fallback válido, con la limitación y la evidencia
    registradas, sin trasladarlo al workspace pagado.
  - **Evidencia:** SQL, identificadores de ejecución, resultados por perfil,
    limpieza y fila en `evidence.md`. **0 €.**
  - **Estimación:** 3–4 h; diff ~250–350 líneas entre SQL y documentación.

- [x] **T-04 — Herencia, auditoría y RUNBOOK de gobernanza**
  - **Objetivo:** dejar el modelo operable por una persona junior y demostrar que
    una concesión puede rastrearse y revertirse.
  - **Entregable:** sección del RUNBOOK con matriz de privilegios, herencia,
    ownership frente a `MANAGE`, comandos de inspección/cambio/rollback, acceso de
    Power BI y procedimiento ante un run fallido por permisos.
  - **Comprobación:** consulta preparada de `system.access.audit` (o la tabla
    disponible) para T-08; si no hay acceso, evidencia de API/CLI como alternativa.
    Un ejercicio de ida y vuelta en dev demuestra herencia y reversión sin runs; el
    rollback de dev queda ensayado y documentado.
  - **Preguntas guía:** ¿por qué ownership no es lo mismo que `MANAGE`? ¿qué grant
    hereda una tabla nueva? ¿cómo se distingue un fallo UC de uno de SAS/ADLS?
  - **Hecho cuando:** otra persona puede inventariar, cambiar y revertir un grant
    siguiendo el documento; la consulta de auditoría queda lista para la única
    sesión SQL y no hay secretos en la evidencia.
  - **Evidencia:** diff del RUNBOOK, salida del ejercicio y fila en `evidence.md`.
    **0 €. Punto de parada y revisión del grupo T-01–T-04.**
  - **Estimación:** 2–3 h; diff documental ~200–300 líneas.

## 2. Calidad de datos y constraints (backlog)

- [x] **T-05 — Contrato de calidad comprobable**
  - **Objetivo:** definir reglas, precedencia y acciones sin duplicar la lógica ya
    existente ni cambiar la semántica de ofertas válidas.
  - **Entregable:** módulo propuesto `databricks_notebooks/Data_Quality.py` con
    identificadores estables, registro de reglas en Python (constantes/dataclasses,
    **no una tabla Delta**), predicados nuevos y generación de DDL; tests en
    `databricks_notebooks/tests/test_data_quality.py` y los huecos salariales en la
    suite de integración correspondiente. Antes de generar DDL, mapa explícito
    tabla-columna por capa.
  - **Reglas mínimas:** conservar `missing_key`/`missing_scraped_at`; rating
    decimal inequívoco en `company_name`; `job_id` obligatorio (no nulo y, si es
    `STRING`, `trim(job_id) <> ''`); orden salarial con nulos válidos y nombres por
    capa (`salary_min_annual`/`salary_max_annual` en silver,
    `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR` en `gold.fact_offers`); fechas según
    el comportamiento real de `posted_date`/`IsValidPostingDate`.
  - **Acciones:** cuarentena para errores de ingesta, marca/neutralización para el
    contrato salarial/fechas ya existente y fallo de constraint ante una violación
    no prevista. No se rechaza una oferta solo por conservar un salario original
    inválido ya neutralizado.
  - **Preguntas guía:** ¿qué hace inequívoco un rating en empresa? ¿cómo se evalúa
    un CHECK con `NULL`? ¿qué reglas no deben aplicarse al histórico? ¿qué expresión
    puede compartir el filtro y Delta sin UDF?
  - **Pistas/documentación:**
    [Constraints on Azure Databricks](https://learn.microsoft.com/en-us/azure/databricks/tables/constraints).
  - **Hecho cuando:** cada regla tiene acción y tests de válida/inválida/nula/límite;
    los casos salariales invertido y no positivo quedan cubiertos; el mapa
    tabla-columna por capa está escrito y no se asume que los nombres coincidan;
    `bundle validate` sigue en verde si se toca configuración; no se ha aplicado DDL
    real.
  - **Evidencia:** pytest, registro de reglas y mapa por capa, fila en `evidence.md`.
    **0 €.**
  - **Estimación:** 4–6 h; diff ~300–400 líneas entre módulo y tests.

- [x] **T-06 — Ampliar y cerrar el ciclo de cuarentena Delta**
  - **Objetivo:** llevar el caso `company_name` a la cuarentena Delta como segunda
    defensa y definir cómo se corrige/reprocesa o descarta con evidencia.
  - **Entregable:** integración mínima en `Upsert_Job_Offers.py`/bronce, reutilizando
    el esquema y la dedup `(_row_hash, _quarantine_reason, _source_file)`; tests en
    `test_upsert_job_offers.py`; procedimiento por motivo en el RUNBOOK.
  - **Casos mínimos:** rating `4,0`, nombre legítimo parecido, motivos concurrentes
    con precedencia estable, lote mixto, repetición del mismo fichero, todas las
    filas inválidas y reconciliación
    `entrada = válidas + filas únicas enviadas a cuarentena`, también con lotes
    repetidos. La idempotencia se demuestra aquí, en local; un run programado no la
    sustituye.
  - **Tratamiento:** una demostración local de corrección/reproceso y otra de
    descarte con referencia al origen; el crudo no cambia y el upsert no duplica.
    No se borra ni reprocesa el histórico real en esta tarea.
  - **Preguntas guía:** ¿por qué `_source_file` forma parte de la identidad? ¿qué
    ocurre si concurren dos motivos? ¿cómo se distingue una corrección de una oferta
    nueva? ¿qué evidencia permite borrar una cuarentena?
  - **Hecho cuando:** los tests demuestran separación, idempotencia, precedencia y
    reconciliación; el resumen de bronce/`ops.run_metrics` sigue contando filas
    nuevas sin acciones sobre todo el lote; el RUNBOOK cubre ambos tratamientos.
  - **Evidencia:** pytest, conteos de fixtures, ejemplo de resumen y fila en
    `evidence.md`. **0 €.**
  - **Estimación:** 4–6 h; diff ~300–400 líneas entre código, tests y RUNBOOK.

- [x] **T-07 — DDL y pruebas locales de constraints en silver/gold**
  - **Objetivo:** dejar listas solo las constraints compatibles con los datos y el
    diseño actual, con su comprobación negativa y rollback.
  - **Entregable:** DDL versionado desde el contrato de T-05 para las tablas
    seleccionadas, comandos `DROP CONSTRAINT` inversos y tests Delta locales sobre
    tablas temporales; consultas de perfil listas para T-08. El DDL parte del mapa
    explícito tabla-columna por capa, sin asumir nombres iguales.
  - **Candidatas:** `job_id` no nulo y, si es `STRING`, `trim(job_id) <> ''`; orden
    de salarios anualizados cuando ambos existen, con `salary_min_annual`/
    `salary_max_annual` en silver y `SalaryMinAnnual_EUR`/`SalaryMaxAnnual_EUR` en
    `gold.fact_offers`; fecha solo si el perfil y el flag actual justifican una regla
    enforceable. Ninguna constraint sobre el rango salarial original que contradiga
    `salary_quality`.
  - **Restricción:** `ADD CONSTRAINT` valida el histórico; si no puede demostrarse
    localmente la expresión o los nulos, no pasa a T-08. Las reglas pospuestas se
    marcan `[NO VERIFICADO]` con motivo.
  - **Preguntas guía:** ¿qué hace Delta al añadir una constraint con violaciones?
    ¿cómo se prueba un rechazo sin ensuciar la tabla real? ¿cómo se evita añadir dos
    veces el mismo constraint?
  - **Hecho cuando:** el caso válido escribe, el inválido falla, el nulo sigue la
    semántica acordada, repetir el despliegue lógico no duplica constraints y el
    rollback las retira en la tabla temporal; el mapa por capa cubre cada constraint.
    Las consultas de T-08 caben en una ventana < 10 min.
  - **Evidencia:** tests Delta, DDL/inverso, mapa por capa y fila en `evidence.md`.
    **0 €.**
  - **Estimación:** 3–4 h; diff ~250–350 líneas entre DDL y tests.

- [x] **T-08 — Sesión SQL única: perfil, cuarentena, constraints y auditoría**
  - **Objetivo:** medir el histórico, perfilar las cuarentenas, aplicar únicamente el
    DDL seguro y consultar la auditoría de gobernanza en una sola ventana.
  - **Preparación:** warehouse `STOPPED` antes; sentencias y orden revisados;
    aprobación explícita de cada `ADD CONSTRAINT`; código de T-05–T-07 en verde;
    aviso del coste (~0,43 €, 10 minutos facturables) y confirmación explícita antes
    de despertar el warehouse.
  - **Ejecución:** perfil de dev/prod, perfil de cuarentenas históricas por fuente y
    motivo (conteos y rango temporal, sin copiar payloads sensibles), constraints
    existentes, consulta de auditoría preparada en T-04, DDL aprobado sin violaciones
    pendientes y comprobaciones inmediatas; parada y confirmación `STOPPED` después.
  - **Decisión:** una regla con violaciones no se fuerza. Se registra conteo y
    tratamiento pendiente; el resto de la cuarentena queda inventariado para RF-6.
    No se abre otra sesión para terminarla. Las constraints aplicadas se verifican
    después por API/metadatos gratuitos cuando sea posible.
  - **Hecho cuando:** hay una sola sesión < 10 min, resultados completos o límites
    explícitos, inventario de cuarentenas con conteos y rango temporal, warehouse
    `STOPPED` antes/después, coste anotado y matriz de reglas actualizada
    (aplicada/pospuesta con evidencia).
  - **Evidencia:** identificadores de consultas, conteos, constraints resultantes,
    auditoría o `[NO VERIFICADO]`, estado del warehouse, `docs/costs.md` y fila en
    `evidence.md`. **1 sesión; ~0,43 € esperados. Punto de parada y revisión del
    grupo T-05–T-08.**
  - **Estimación:** 1–2 h; diff ~50–150 líneas de sentencias y evidencia.

## 3. Despliegue, comprobación real y cierre

- [x] **T-09 — Validar, merge checkpoint y desplegar prod desde `main`**
  - **Objetivo:** publicar los cambios aprobados desde `main`, conservando un camino
    de vuelta atrás y sin consumir compute de desarrollo.
  - **Comprobaciones:** suites afectadas, `databricks bundle validate -t dev` y
    `-t prod` desde la rama, diff de notebooks/módulos/job, grants efectivos y
    constraints por metadatos, warehouse `STOPPED`. El deploy no cuenta como
    comprobación funcional.
  - **Publicación:** tras la revisión del grupo y **solo con petición explícita**, se
    hace el merge checkpoint `--no-ff` de la rama a `main`; no se crea tag, no se
    borra la rama y no se retira el worktree. Tras el merge se repite
    `databricks bundle validate -t prod` desde `main` antes del deploy. El deploy de
    producción sale de `main` (nunca de la rama) y no cambia cron, `run_as`, compute
    ni tareas. El deploy de dev, si se usa, se hace antes desde la rama y sin
    ejecutar el job.
  - **Grants de prod:** preflight por operación y aplicación de los grants/
    revocaciones preparados en T-02 y aprobados para producción, cerca de la ventana
    semanal y con los comandos inversos listos. Si el preflight falla, no se aplica
    nada, T-09 no se cierra y se replanifica.
  - **Calendarización:** deploy y cambios de prod deben completarse antes del domingo
    23:00 `Europe/Madrid`. Si se pasa esa hora, el siguiente run programado será una
    semana después: se documenta y se replanifica, sin run de dev.
  - **Rollback:** restaurar archivos/job desde el `main` anterior; grants con los
    inversos de T-02; constraints con T-07; cuarentena nueva sin borrado de datos. El
    último recurso de producción es el job manual pausado `704156530681034` descrito
    en `specs/007-ELT-As-Code/cutover.md`. Se documenta qué pasos requieren detener o
    pausar el job si estuviera corriendo.
  - **Hecho cuando:** validaciones en verde, `validate -t prod` repetido desde `main`,
    merge checkpoint hecho solo con petición explícita, prod desplegado desde `main`
    sin runs de dev, grants de prod aplicados con preflight, y la evidencia distingue
    lo desplegado de lo aún no ejecutado; el job sigue con 9 tareas y programación
    intacta; no se ha creado tag ni borrado rama/worktree. Si el preflight de grants
    falla, T-09 no se cierra. Cualquier desviación requiere aprobación explícita y
    actualización previa de `spec.md`, `plan.md` y `tasks.md`.
  - **Evidencia:** validaciones (incluida la de `main`), resúmenes de deploy, commit
    de `main`, preflight y cambios de grants, metadatos y fila en `evidence.md`.
    **0 € de dev.**
  - **Estimación:** 2–3 h; diff ~100–200 líneas entre comandos y evidencia.

- [ ] **T-10 — Comprobación real: run programado, permisos y dashboard**
  - **Objetivo:** demostrar que la gobernanza y la calidad funcionan juntas en el
    ciclo semanal real, sin regresión.
  - **Alcance de la prueba:** la idempotencia se prueba en T-06 en local y se hereda
    de las specs 008/010/011; un run programado no la demuestra. Este run valida
    ausencia de regresión, permisos y conteos coherentes. Repetirlo solo si la
    persona autoriza el coste adicional; no es requisito por defecto.
  - **Hecho cuando:** el primer run programado posterior al deploy cierra las 9
    tareas; la identidad `run_as` puede leer/escribir todo lo necesario;
    `ops.run_metrics` tiene una fila con checks en verde y conteos coherentes de
    bronce/cuarentena; no aparece una violación inesperada de constraint; las
    constraints y grants siguen presentes; el workflow del dashboard publica con
    `data_date` correcta; y el warehouse queda `STOPPED` antes y después.
  - **Si hay filas nuevas en cuarentena:** reconciliar el lote y registrar motivo y
    tratamiento. **Si falla por permisos/calidad:** aplicar rollback o corregir en
    esta misma spec y repetir la comprobación solo con autorización de coste; no se
    cierra con el fallo explicado.
  - **Evidencia:** id del run, 9/9 tareas, fila de métricas, conteos, id del
    workflow, metadatos de grants/constraints, warehouse, coste en `docs/costs.md`
    y fila en `evidence.md`. **0 runs de dev; coste ordinario de producción.**
  - **Estimación:** 1–2 h; diff ~50–100 líneas de evidencia.

- [ ] **T-11 — Cierre del track de certificación**
  - **Objetivo:** dejar el aprendizaje y el estado operativo cerrados, con el
    backlog correctamente reflejado.
  - **Entregables:** nota `docs/databricks-de/notes/07-security-governance.md` e
    índice; roadmap con spec 012, bronce deduplicado marcado como cerrado por
    008/010, dominio 7 corregido para reflejar `run_as` persona e identidad de
    storage real, y backlog de cuarentena/constraints y dedup de oro pendiente
    actualizado; RUNBOOK y `docs/costs.md` completos.
  - **Comprobaciones:** `python -m pytest databricks_notebooks/tests -q` y cualquier
    otra suite afectada; revisión de los tres grupos; presupuesto 0 runs de dev y
    ≤ 1 sesión SQL salvo desviación aprobada/documentada; T-10 en verde.
  - **Hecho cuando:** todos los RF tienen evidencia o desviación explícita, no hay
    hallazgos bloqueantes, la comprobación real funciona y la persona aprueba el
    cierre. Después, si hay commits posteriores al checkpoint de T-09, se hace el
    merge final `--no-ff` a `main`; el tag anotado `spec-012` y el cierre (borrado de
    la rama y retirada del worktree según `AGENTS.md`) solo se hacen después de
    T-10. No se etiqueta ni se cierra antes del run real.
  - **Evidencia:** suites, nota, roadmap, costes, aprobación y registro del cierre.
    **0 € adicionales.**
  - **Estimación:** 4–6 h; diff documental ~250–400 líneas.
  - **Estado (2026-11-08):** nota `docs/databricks-de/notes/07-security-governance.md`,
    índice y roadmap preparados y commiteados (`b47217c`). T-11 **no se cierra**
    hasta que T-10 esté en verde; el tag `spec-012`, el merge final y la retirada
    del worktree quedan pendientes de esa comprobación.
