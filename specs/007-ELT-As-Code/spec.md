# Especificación funcional: ELT como código (Asset Bundles) y entorno dev

## Contexto y objetivo

La ELT de Talent Market Lens se ejecuta en un job de Databricks (`Git Based
ELT`) configurado **a mano** en el workspace: las tareas de notebook se sirven
desde una Git folder apuntada a una rama del repositorio y el job se dispara por
File Arrival sobre `_READY/dia=YYYY-MM-DD.txt`. No existe definición versionada
del job, ni entornos separados, ni parámetros auditables, ni validación
automática: cada cambio de la ELT (specs 008–011: MERGE incremental, layout
físico, observabilidad, gobernanza) obliga a editar el job a mano y arriesga el
ciclo diario.

Este trabajo convierte el job en **código** con Databricks Asset Bundles:
definición versionada, un entorno de desarrollo aislado (catálogo propio), 
parametrización por target y comprobación en CI, con un cutover de producción
coordinado y reversible. Es el bloque de CI/CD del temario de la certificación
Data Engineer Professional y deja el terreno listo para el resto del track,
cuya hoja de ruta (`docs/databricks-de/roadmap.md`) se versiona aquí.

El trabajo no toca los scrapers, el pipeline local, la landing ni los
dashboards (Power BI y web); solo la definición del job ELT y su entorno.

## Usuarios

- La persona que mantiene el proyecto (nivel junior), que aprende el dominio
  CI/CD aplicándolo aquí.
- El propio proyecto, que necesita entornos y despliegues reproducibles para
  seguir evolucionando sin riesgo.

## Historias de usuario

- Como mantenedor, quiero el job versionado en el repositorio, para revisar
  cambios y volver atrás sin tocar el workspace a mano.
- Como mantenedor, quiero un entorno dev con catálogo propio, para probar la
  ELT sin riesgo para los datos de producción.
- Como mantenedor, quiero desplegar a dev o prod con los mismos recursos y
  distintos parámetros, para no mantener dos jobs a mano.
- Como mantenedor, quiero que la CI valide el bundle en cada cambio, para
  detectar errores antes de desplegar.
- Como estudiante del certificado, quiero el track documentado (hoja de ruta y
  notas), para repasar y dejar evidencia del aprendizaje.
- Como responsable del ciclo diario, quiero un cutover reversible y
  atestiguado con un run real, para no poner en riesgo la ELT nocturna.

## Requisitos funcionales

### RF-1 — Job ELT versionado como Asset Bundle

**Criterio de aceptación (EARS):** Cuando se ejecute `databricks bundle
validate` en la raíz del repositorio, deberá pasar sin errores, y el bundle
deberá definir el job ELT completo —Bronze, los cuatro Silver, Gold y el export
web, con sus dependencias— tomando como base el job `Git Based ELT` capturado
con `databricks bundle generate`, sin secretos embebidos y sin mover los
notebooks de sitio.

### RF-2 — Entornos dev y prod aislados

**Criterio de aceptación (EARS):** Cuando se despliegue el target `dev`, el job
deberá leer la landing en solo lectura y escribir únicamente en el catálogo
`job_offers_dev`, checkpoints `_checkpoints_dev` y export `gold/web_export_dev`,
sin escribir ni borrar nada en los recursos de producción; el target `prod`
deberá reproducir el comportamiento actual (catálogo `job_offers`, checkpoints
`_checkpoints`, export `gold/web_export` y disparo por File Arrival). El job dev
deberá estar pausado por defecto y no competir nunca con el de producción.

### RF-3 — Parámetros por target y gestión de secretos

**Criterio de aceptación (EARS):** Cuando se despliegue cualquier target, el
catálogo, la cuenta de almacenamiento, el contenedor, las raíces de checkpoints
y el prefijo del export deberán provenir de variables del bundle definidas por
target; ningún secreto (token, SAS) deberá quedar en el repositorio, en los logs
ni en la definición desplegada. La autenticación usará perfiles locales de la
CLI para dev y secretos de CI o un service principal documentado para
producción.

### RF-4 — Run dev end-to-end con evidencia

**Criterio de aceptación (EARS):** Cuando se lance el job dev con el `_READY`
más reciente, todas las tareas deberán completarse en verde y las tablas dev
deberán quedar consistentes con las de producción para la misma fecha (conteos
por tabla y contrato de columnas), registrando run id, duración y conteos como
evidencia en la spec.

### RF-5 — Validación en CI

**Criterio de aceptación (EARS):** Cuando se abra o actualice una pull request
que toque el bundle, los recursos o los notebooks de la ELT, el repositorio
deberá ejecutar `databricks bundle validate` y fallar de forma visible si la
definición es inválida; el workflow solo usará credenciales de dev y no
desplegará a producción.

### RF-6 — Cutover reversible a producción

**Criterio de aceptación (EARS):** Cuando se ejecute el cutover, el disparo por
File Arrival sobre `_READY/dia=YYYY-MM-DD.txt` deberá seguir lanzando la ELT
una única vez al día —nunca dos jobs duplicados a la vez—, el job manual
anterior quedará deshabilitado y conservado como rollback, y una noche real
completará con evidencia; el rollback (deshabilitar el job del bundle y
rehabilitar el manual) deberá estar documentado y probado en seco.

### RF-7 — Documentación operativa y actualización de AGENTS

**Criterio de aceptación (EARS):** Cuando concluya el trabajo, RUNBOOK y README
deberán documentar el bundle, los targets, las variables, los comandos de
despliegue y el procedimiento de cutover y rollback; se propondrá a la persona
el cambio de `AGENTS.md` con los comandos nuevos y no se aplicará sin su
confirmación.

### RF-8 — Track de certificación versionado

**Criterio de aceptación (EARS):** Cuando se cierre la spec, deberán existir
`docs/databricks-de/roadmap.md` —con calendario, mapa de los siete dominios a
las specs y bitácora— y `docs/databricks-de/notes/` preparada para las notas
por dominio; cada spec posterior del track la actualizará al cerrar. La nota
`05-cicd.md` deberá estar escrita con lo aprendido en esta spec.

### RF-9 — Aislamiento y no regresión del ecosistema

**Criterio de aceptación (EARS):** Cuando se ejecute este trabajo, no deberá
modificar `scrapers-pipeline/**`, la landing, `Job_Offers_Dashboard.*`, la web
ni las specs 004/005/006; el contrato de las tablas `gold` (nombres y semántica
de columnas) deberá quedar intacto y las suites existentes deberán quedar en
verde.

## Requisitos no funcionales

- **Stack simple (constitución 1):** la CLI y los Asset Bundles son la vía
  estándar de Databricks para versionar jobs; sustituyen configuración manual,
  no añaden runtime nuevo; la CI usa GitHub Actions (ya prevista en la 006);
  sin dependencias Python nuevas.
- **Spec y código (constitución 2):** esta spec es la activa de la 007; toda
  desviación se refleja aquí junto con el código.
- **Lógica e interfaz (constitución 3):** la definición del job es declarativa;
  la lógica de las tareas no cambia (vive en los notebooks y módulos actuales).
- **Tests (constitución 4):** suites existentes en verde más `bundle validate`
  y las comprobaciones reales (run dev, noche tras cutover, rollback en seco).
- **Persistencia (constitución 5):** ningún dato nuevo en el repositorio; dev
  reutiliza el mismo almacenamiento con prefijos propios y solo lectura sobre
  la landing.
- **Idioma (constitución 6):** identificadores, recursos y definiciones en
  inglés; documentos y mensajes para la persona, en español.
- **Seguridad:** sin secretos versionados; tokens de CI con alcance de dev;
  permisos revisados antes del cutover; ninguna credencial en evidencias.
- **Coste:** el job dev se lanza a demanda y en pausa por defecto; los datos
  del proyecto son pequeños y el coste de una ejecución completa es acotado.

## Casos límite

- `bundle generate` no reproduce algún detalle del job manual (trigger de File
  Arrival, política de cluster): se reconstruye a mano comparando con el JSON
  exportado y se documenta la diferencia.
- El esquema del bundle no soporta el trigger de File Arrival: se mantiene el
  trigger configurado fuera del bundle y se documenta como excepción
  controlada.
- Doble disparo durante el cutover: el checklist deshabilita el job manual
  antes de habilitar el del bundle, y la primera noche se verifica un único run.
- El nombre del job cambia respecto al manual: se registran los ids nuevos y se
  revisan alertas o referencias externas.
- El PAT de CI caduca: el workflow falla de forma visible; la rotación queda
  documentada.
- Primer run dev de Bronze re-procesa todo el histórico de la landing
  (checkpoint nuevo): se acepta, se mide y se documenta.
- Un `_READY` ya procesado vuelve a disparar en dev: dev no escribe en
  producción, sin impacto; se documenta.
- La persona no tiene permisos para crear jobs o tokens (workspace gestionado):
  lo confirma el recon (T-02/T-03) y, si falta algo, se escala antes de seguir.
- Diferencias de conteo dev vs prod por datos nuevos entre runs: la
  comparación usa la misma fecha de `_READY` como referencia.
- Rollback con un run ya en marcha: se espera a que termine; nunca dos
  ejecuciones simultáneas.
- Cambios de la 006 durante el trabajo: no se solapa ningún fichero; si aparece
  un cruce inesperado, se detiene y se consulta a la persona.

## Fuera de alcance

- Cambios de lógica, física o incrementalidad de la ELT (specs 008–011).
- CI/CD de los scrapers, la landing o el dashboard web (spec 006).
- Terraform, multi-workspace y automatización del cutover (sigue siendo un paso
  humano validado).
- Deploy a producción desde CI (solo se valida y, opcionalmente, se despliega a
  dev con `workflow_dispatch`).

## Criterios de finalización

- `databricks bundle validate` en verde en local y en CI, con un pull request
  que primero falla y después pasa.
- Job dev desplegado y un run end-to-end con evidencia (run id, duración,
  conteos por tabla, checkpoints dev).
- Cutover a producción con una noche real: disparo único por File Arrival, run
  en verde, tablas y export avanzan; job manual deshabilitado como rollback.
- Rollback probado en seco y documentado en `cutover.md`.
- RUNBOOK y README actualizados; propuesta de `AGENTS.md` presentada a la
  persona.
- Hoja de ruta y nota `05-cicd.md` versionadas.
- Suites existentes en verde: `python -m pytest databricks_notebooks/tests -q`
  y `python -m pytest scrapers-pipeline/tests -q`.
- **Comprobación real:** un ciclo nocturno completo tras el cutover (la subida
  de `_READY` del pipeline local dispara el job del bundle, que termina en
  verde y actualiza tablas y export) registrado como evidencia.
- No se hace push ni merge sin validación de la persona (flujo de `AGENTS.md`).

## Decisiones aclaradas

- Mismo workspace, dos catálogos (`job_offers_dev` y `job_offers`) y raíces de
  checkpoints y export separadas por target.
- Base del bundle: `bundle generate` del job real, revisado a mano.
- Dev en pausa salvo ejecución manual; prod con File Arrival.
- CI solo valida; el deploy de dev es manual (u opcional por dispatch); el de
  producción es siempre manual y validado.
- Los notebooks no se mueven ni se renombran: el bundle los referencia donde
  están.
- Autenticación: perfil local de la CLI para dev; service principal preferido
  para producción, con el usuario actual como respaldo documentado.
- La tarea de export web de la 006 queda absorbida en la definición del bundle
  tras su cierre.
- El roadmap y las notas del track viven en `docs/databricks-de/` dentro de
  esta spec; las siguientes specs los actualizan.
