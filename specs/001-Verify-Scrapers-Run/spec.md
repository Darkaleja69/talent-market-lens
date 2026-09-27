# Especificación funcional: diagnóstico de ejecuciones diarias

## Contexto y objetivo

Talent Market Lens recopila ofertas mediante varios scrapers y las publica en la landing de Azure para su consumo posterior. Esta funcionalidad permite revisar la ejecución diaria más reciente, verificar los datos obtenidos y su publicación, y detectar oportunidades para mejorar la información recogida.

Ante un fallo, el diagnóstico contrasta la fuente con su web real y recomienda cambios para revisión humana. También muestra qué proporción de ofertas contiene cada dato relevante, por fuente, y su evolución respecto de ejecuciones anteriores. El objetivo último es recoger toda la información disponible en las búsquedas estipuladas por regiones.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería de datos de nivel junior.

## Historias de usuario

- Como responsable del proyecto, quiero consultar la ejecución diaria más reciente para saber qué ocurrió en cada fuente.
- Como mantenedor, quiero ver la completitud de los datos por fuente y campo para encontrar campos que convenga mejorar.
- Como mantenedor, quiero ver la tendencia de la completitud frente a ejecuciones comparables para confirmar que cada ajuste mejora la recogida.
- Como mantenedor, quiero ver evidencias y una causa probable ante un fallo para decidir qué cambio aplicar.
- Como responsable de los datos, quiero comprobar que lo publicado está preparado para la carga analítica posterior.

## Requisitos funcionales

### RF-1 — Analizar la ejecución más reciente completada

**Criterio de aceptación (EARS):** Cuando la persona solicite el diagnóstico después de una ejecución diaria, el sistema deberá analizar la última ejecución que alcanzó un estado final, aunque alguna o todas las fuentes no hayan producido datos, y presentar su resultado por fuente.

### RF-2 — Cubrir las fuentes y las búsquedas previstas

**Criterio de aceptación (EARS):** Cuando se analice una ejecución, el sistema deberá informar por separado sobre Indeed, LinkedIn e InfoJobs, y sobre cada portal independiente de Multi-site: IrishJobs, StepStone NL, DevITjobs, NVB, Jobs.ch y Glassdoor. También deberá informar sobre las búsquedas estipuladas para cada región.

Dentro de Multi-site, cada portal se evalúa como una fuente independiente: unos pueden quedar correctos y otros fallidos en la misma ejecución.

### RF-3 — Marcar una fuente como fallida

**Criterio de aceptación (EARS):** Cuando una fuente haya terminado, el sistema deberá marcarla como fallida si produjo cero ofertas, si su completitud en un dato obligatorio es inferior al 90 %, si un fichero no se puede leer o incumple el esquema/columnas obligatorias del contrato de datos del proyecto, o si no hay evidencia suficiente para confirmar su validez.

La falta de evidencia (sin datos, sin registros o evidencia contradictoria) se considera fallo directo.
Los valores inválidos de campos individuales se contabilizan conforme a RF-4 y RF-5; su efecto en el estado de la fuente se determina mediante los umbrales de completitud de RF-3 y RF-10.

### RF-4 — Mostrar completitud de los datos

**Criterio de aceptación (EARS):** Cuando haya ofertas obtenidas, el sistema deberá mostrar para cada fuente y cada dato relevante el número de ofertas únicas con un valor válido, el total de ofertas únicas y su porcentaje de completitud.

Los datos a medir incluirán, como mínimo, descripción, salario, skills y modalidad de trabajo (presencial, híbrida o remota), además de título, empresa, ubicación y fecha de publicación.

La descripción, el nombre de la empresa y el título son datos obligatorios: su completitud debería ser del 100 %. El resto de datos podrá tener una completitud menor.

### RF-5 — Validar los valores de cada dato

**Criterio de aceptación (EARS):** Cuando se evalúe un dato de una oferta, el sistema deberá contarlo como válido solo si cumple las reglas del contrato de datos del proyecto para ese dato; en caso contrario, deberá contarlo como inválido y no como válido.

### RF-6 — Comparar completitud entre lo obtenido y lo publicado

**Criterio de aceptación (EARS):** Cuando los datos publicados en la landing estén disponibles para el análisis, el sistema deberá mostrar su completitud por fuente y dato, y señalar cualquier diferencia respecto de los datos obtenidos por el scraper.

### RF-7 — Mostrar la tendencia de la completitud

**Criterio de aceptación (EARS):** Cuando existan ejecuciones anteriores comparables conservadas en Azure, el sistema deberá mostrar la variación de la completitud por fuente y dato a lo largo de las últimas cinco ejecuciones y señalar las mejoras y los retrocesos. Si hay menos de cinco, deberá usar las disponibles e indicar cuántas ha usado.

Dos ejecuciones son comparables cuando cubren las mismas fuentes y las mismas búsquedas por región; si la configuración cambió, se indica y no se compara esa parte.

### RF-8 — Verificar la publicación en la landing

**Criterio de aceptación (EARS):** Cuando haya datos preparados para su publicación, el sistema deberá comprobar que los aceptados están disponibles y son legibles en la landing; si hay rechazos, pérdidas o diferencias, deberá informarlos sin declarar la publicación como correcta. Los datos que aún no aparecen en el momento del diagnóstico deberán reportarse como pendientes de publicar, no como error.

### RF-9 — Investigar fallos con las webs reales

**Criterio de aceptación (EARS):** Cuando el diagnóstico detecte una fuente fallida, el sistema deberá contrastar con la web real afectada cómo se presentan los datos y comparar esa evidencia con los resultados de la ejecución. Si no puede verificar la web, deberá indicarlo sin presentar una causa como confirmada.

### RF-10 — Investigar la completitud insuficiente con las webs reales

**Criterio de aceptación (EARS):** Cuando la completitud de un dato obligatorio no alcance el 100 % o la de cualquier otro dato no supere el 60 %, el sistema deberá contrastar con la web real si el dato ha cambiado de componente o de ubicación y comparar esa evidencia con los resultados de la ejecución. Si no puede verificar la web, deberá indicarlo sin presentar una causa como confirmada.

### RF-11 — Entregar recomendaciones accionables

**Criterio de aceptación (EARS):** Cuando exista una fuente fallida o una completitud insuficiente, el informe deberá incluir para la fuente afectada el resultado, la evidencia disponible, la causa probable, el cambio recomendado en el código y cómo comprobar manualmente la corrección. Deberá distinguir los hechos verificados de las hipótesis.

### RF-12 — Mantener la decisión en manos de una persona

**Criterio de aceptación (EARS):** Mientras se realiza el diagnóstico, el sistema no deberá reintentar la ejecución, modificar código ni aplicar correcciones; la persona decidirá si realiza los cambios recomendados. La única intervención automática sobre una ejecución será detener un scraper bloqueado conforme a RF-15.

### RF-13 — Informar cuando no se pueda analizar la ejecución

**Criterio de aceptación (EARS):** Cuando no exista una ejecución analizable o falten por completo los datos y registros necesarios, el sistema deberá marcar el diagnóstico como inconcluso e indicar qué no pudo comprobar.

### RF-14 — Clasificar el resultado global

**Criterio de aceptación (EARS):** Cuando se analice una ejecución, el sistema deberá clasificar cada fuente únicamente como correcta o fallida y, según el conjunto, clasificar el resultado global como:

- **correcto** si todas las fuentes son correctas;
- **parcial** si hay fuentes correctas y fallidas;
- **fallido** si todas las fuentes son fallidas;
- **inconcluso** si no se pudo analizar la ejecución.

### RF-15 — Detectar y detener scrapers bloqueados

**Criterio de aceptación (EARS):** Mientras un scraper esté en ejecución, el supervisor deberá comprobar si avanza el contador de ofertas capturadas. Si el contador avanza, deberá mantener el scraper activo y no marcarlo como bloqueado. Si el contador no avanza durante el periodo de inactividad establecido para ese scraper, deberá detener ese proceso, registrar la causa como bloqueo por falta de progreso y marcar esa fuente como fallida. En Multi-site, esta comprobación y detención se realizará por portal, sin detener los demás.

## Requisitos no funcionales

- El informe deberá ser claro y accionable para una persona que mantiene el proyecto con conocimientos de ingeniería de datos de nivel junior.
- Los mensajes dirigidos a la persona deberán estar en español.
- El diagnóstico no alterará datos, configuración ni estado de publicación. El supervisor solo podrá detener un proceso que cumpla la condición de bloqueo de RF-15.
- Los porcentajes deberán acompañarse de sus recuentos para que su interpretación sea verificable.
- Con la misma evidencia de una ejecución, el diagnóstico deberá informar los mismos hechos y distinguirlos de cualquier hipótesis.
- La funcionalidad deberá cubrirse con tests unitarios y de integración sobre datos de ejemplo, sin depender de las webs reales ni de credenciales.

## Casos límite

- Una fuente termina sin error técnico, pero produce cero ofertas: esa fuente cuenta como fallida.
- Falta evidencia para confirmar la validez de una fuente: cuenta como fallida.
- Un dato obligatorio no llega al 100 % pero se mantiene en el 90 % o más: se reporta como incidencia, sin marcar la fuente como fallida.
- Un dato obligatorio baja del 90 %: la fuente cuenta como fallida.
- Un dato no obligatorio no supera el 60 %: se investiga con la web real, sin marcar la fuente como fallida por ese motivo.
- Una oferta no publica un dato (por ejemplo, salario): se cuenta como dato ausente, sin atribuir por ello el fallo al scraper.
- Un dato contiene un valor que no cumple el contrato de datos: se cuenta como inválido y reduce la completitud de ese dato.
- Dentro de Multi-site, unos portales pueden quedar correctos y otros fallidos en la misma ejecución.
- Hay varias ejecuciones el mismo día: se diagnostica la última que terminó y dejó datos.
- La web real no está accesible o no se puede verificar: la fuente conserva su estado (correcta o fallida) y la investigación queda sin concluir, indicándolo explícitamente.
- Los datos preparados y los disponibles en la landing difieren: se detalla la discrepancia y no se confirma la publicación como correcta.
- Los datos debían subirse pero aún no aparecen al diagnosticar: se reportan como pendientes de publicar.
- No existe una ejecución anterior comparable: no se muestra tendencia y se indica que no hay referencia.
- No se encuentra una ejecución analizable, o faltan por completo sus registros o datos: el diagnóstico queda inconcluso.
- Un scraper sigue activo y aumenta su contador de ofertas: se mantiene en ejecución y no se declara bloqueado.
- Un scraper sigue activo, pero su contador no aumenta durante el periodo de inactividad establecido: se detiene ese scraper y se registra como fallido, sin detener los demás portales de Multi-site.

## Fuera de alcance

- Ejecutar o programar los scrapers.
- Reintentar ejecuciones o modificar código automáticamente.
- Ejecutar transformaciones analíticas posteriores a la landing.
- Validar o modificar el dashboard de Power BI.
- Enviar alertas o monitorizar ejecuciones continuamente.
- Crear un almacén propio de histórico del diagnóstico.

## Criterios de finalización

- Los resultados se presentan por separado para cada fuente independiente (incluidos los portales de Multi-site) y para las búsquedas por región.
- Se muestran porcentajes, recuentos y tendencia de completitud para cada fuente y dato relevante, sobre ofertas únicas.
- Se informa la diferencia de completitud entre lo obtenido y lo publicado cuando ambas medidas estén disponibles.
- Los escenarios de cero ofertas, falta de evidencia, dato obligatorio por debajo del 90 %, dato no obligatorio por debajo del 60 %, publicación pendiente y ausencia de ejecución analizable se reportan correctamente.
- Ante una fuente fallida o completitud insuficiente, el informe aporta evidencia, una causa probable y recomendaciones de cambio y verificación.
- La consulta a webs reales solo ocurre para fuentes fallidas o para datos por debajo de los umbrales, y no altera el estado de la fuente.
- El resultado global se clasifica como correcto, parcial, fallido o inconcluso según corresponda.
- La funcionalidad cuenta con tests unitarios y de integración sobre datos de ejemplo.
- Un scraper con progreso no es detenido; uno sin progreso durante su periodo establecido es detenido y registrado como fallido.
- El diagnóstico no inicia ejecuciones ni modifica datos, configuración o código.

## Decisiones aclaradas

- Sin evidencia suficiente → la fuente es fallida (no existe estado "inconclusa" por fuente).
- Dato obligatorio por debajo del 100 % → activa la investigación; entre el 90 % y el 99 % es incidencia, y por debajo del 90 % la fuente es fallida.
- Dato no obligatorio por debajo del 60 % → se investiga, sin marcar la fuente como fallida.
- El "cero ofertas" se evalúa por fuente independiente; los portales de Multi-site cuentan por separado.
- Se diagnostica la última ejecución completada; el histórico para la tendencia procede de lo ya conservado en Azure.
- La tendencia usa ejecuciones comparables (mismas fuentes y búsquedas), hasta cinco.
- La validez de cada dato se define en el contrato de datos del proyecto.
- La completitud se calcula sobre ofertas únicas, en las etapas obtenida y publicada.
- Los datos aún no publicados se reportan como pendientes, no como error.
- La web no verificable no cambia el estado de la fuente; solo deja la investigación sin concluir.
- Verificación mediante tests unitarios y de integración sobre datos de ejemplo.
- Multi-site incluye seis fuentes independientes: IrishJobs, StepStone NL, DevITjobs, NVB, Jobs.ch y Glassdoor.
- Un proceso activo se considera en progreso mientras el contador de ofertas capturadas avance; si no avanza durante el periodo establecido para ese scraper, se detiene y se marca fallido.
