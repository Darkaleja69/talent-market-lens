# Especificación funcional: reparación de scrapers caídos

## Contexto y objetivo

El diagnóstico diario (spec 001) escribe en
`scrapers-pipeline/logs/diagnostic_last.json` el resultado de la última
ejecución: estado global, estado por fuente independiente (Indeed, LinkedIn,
InfoJobs y los seis portales de Multi-site), ofertas, completitud, publicación,
motivos, evidencias e investigaciones pendientes.

Esta funcionalidad convierte ese diagnóstico en reparaciones: detecta las
fuentes fallidas, investiga la web/API real para averiguar por qué no llegan
datos, repara el scraper correspondiente (se permite el rediseño si mejora la
calidad) y demuestra que el arreglo funciona con tests y una prueba en vivo
acotada. Cada reparación se lleva a cabo en su propia rama de fix y se registra
para dejar constancia de qué se rompió y cómo se arregló.

El objetivo último es que ninguna fuente se quede caída: que el proceso
encuentre la causa y la solución con evidencia, sin servicios de pago y
respetando primero `robots.txt` y los términos de uso, dejando en manos de la
persona la validación del push y cualquier decisión que exceda los medios
locales.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería
  de datos de nivel junior.

## Historias de usuario

- Como mantenedor, quiero que tras el diagnóstico se me propongan las
  reparaciones de las fuentes fallidas para no tener que investigarlas a mano.
- Como mantenedor, quiero que la investigación contraste la web/API real y me
  diga por qué no están llegando datos.
- Como mantenedor, quiero que el scraper quede reparado y probado en vivo para
  confiar en que la fuente vuelve a ingerir.
- Como mantenedor, quiero constancia del plan y del resultado de cada
  reparación para saber qué falló y qué se cambió.
- Como responsable del proyecto, quiero validar el push de cada reparación una
  vez que esté probada, y decidir yo si se escala más allá de los medios
  locales.

## Requisitos funcionales

### RF-1 — Seleccionar los objetivos reparables del diagnóstico

**Criterio de aceptación (EARS):** Cuando exista
`scrapers-pipeline/logs/diagnostic_last.json` con un resultado global distinto
de "inconcluso", el sistema deberá identificar las fuentes independientes
marcadas como fallidas y construir para cada una un objetivo de reparación con
su evidencia (resultado, ofertas, completitud, publicación, motivos y la
investigación asociada si existe). También podrá incluir como objetivos
secundarios las investigaciones de completitud de fuentes no fallidas
(obligatorio por debajo del 100 % u opcional en 60 % o menos). Si el
diagnóstico no existe o es inconcluso, el sistema deberá indicarlo y no iniciar
ninguna reparación.

Los objetivos se presentarán priorizados: primero las fuentes fallidas y
después los secundarios.

### RF-2 — Abrir una rama y un registro por reparación

**Criterio de aceptación (EARS):** Cuando se inicie la reparación de una
fuente, el sistema deberá crear una rama de fix propia (`repair/<fuente>-<fecha>`)
y un registro en `repairs/<fecha>-<fuente>/` con el plan de actuación (fuente,
fallo observado, evidencia del diagnóstico, plan de investigación y estado
"planificado") antes de modificar el scraper.

### RF-3 — Investigar la web/API real con un especialista

**Criterio de aceptación (EARS):** Cuando exista un objetivo de reparación, el
subagente especialista deberá investigar la fuente real con las skills
instaladas (`scraping-expert`, `browser-cdp`, `agent-browser`) y devolver un
informe que distinga hechos observados de hipótesis, con evidencias
(capturas, respuestas, peticiones), la causa probable y el cambio recomendado.
La investigación deberá comprobar `robots.txt` y los términos de uso antes de
proponer un cambio, y no modificar código.

### RF-4 — Priorizar la evitación de CAPTCHA y rate limit

**Criterio de aceptación (EARS):** Cuando el fallo sea un bloqueo (CAPTCHA,
403/429 o límite de peticiones), la reparación deberá buscar primero la vía que
lo evite (API JSON interna, sesión y cookies reales, cabeceras y ritmo
adecuados, endpoints alternativos) sin implementar resolución automática de
CAPTCHAs ni superar verificaciones de identidad.

### RF-5 — No usar servicios de pago y consultar cualquier escalado

**Criterio de aceptación (EARS):** Mientras existan alternativas locales no
deshabilitadas, el proceso no deberá habilitar servicios de pago ni contratar
proxies. Si tras agotar las técnicas locales el bloqueo persiste, deberá
detenerse y presentar a la persona las opciones de escalado (por ejemplo,
rotación de proxies o IPs) con su explicación, sin aplicarlas por su cuenta.

### RF-6 — Respetar `robots.txt` y los términos de uso primero

**Criterio de aceptación (EARS):** La investigación y el fix deberán intentar
la obtención de datos dentro de lo permitido por `robots.txt` y los términos de
uso; si la única vía conocida para lograr la calidad requerida implicara
incumplirlos, el proceso deberá detenerse y consultar a la persona antes de
continuar.

### RF-7 — Reparar el scraper

**Criterio de aceptación (EARS):** Cuando la causa esté identificada, el
implementador deberá modificar el scraper afectado (código o configuración del
scraper) —incluido un rediseño completo si mejora la calidad— y añadir o
actualizar los tests correspondientes. Las dependencias nuevas se minimizarán y
solo se añadirán si son claramente necesarias, quedando justificadas en el
registro de la reparación.

### RF-8 — Verificar tests y diff antes de dar por reparado

**Criterio de aceptación (EARS):** Cuando el fix esté implementado, el
verificador deberá ejecutar las suites del scraper afectado y del diagnóstico y
revisar el diff de la reparación contra esta spec y el registro; no se dará la
reparación por implementada si hay hallazgos bloqueantes.

### RF-9 — Probar en vivo de forma acotada

**Criterio de aceptación (EARS):** Cuando los tests pasen, el implementador
deberá ejecutar el scraper reparado en vivo de forma acotada (una búsqueda o
configuración representativa, sin subir datos a Azure ni modificar la landing,
sin ejecutar el merge de Multi-site) y registrar el recuento de ofertas
obtenido, el alcance y la evidencia en el registro de la reparación. En
Multi-site, la prueba ejecutará únicamente el portal afectado.

### RF-10 — Exigir un umbral incremental de ofertas

**Criterio de aceptación (EARS):** Cuando se evalúe una prueba en vivo, el
sistema deberá considerarla suficiente solo si el recuento de ofertas alcanza
el umbral de esa fuente: 1 oferta en su primera reparación y, en reparaciones
posteriores, más ofertas que el mayor recuento verificado en reparaciones
anteriores de la misma fuente, de modo que el listón nunca baje. El historial
de recuentos verificados se conservará con la reparación.

### RF-11 — Dejar constancia de cada reparación

**Criterio de aceptación (EARS):** Cuando una reparación termine (probada,
descartada o escalada a la persona), el sistema deberá completar su registro
con el resultado, los cambios realizados, las pruebas (tests y prueba en vivo
con su alcance y recuento) y el estado final, y actualizar el índice de
reparaciones (`repairs/README.md`) y el historial de umbrales.

### RF-12 — Validación humana del push

**Criterio de aceptación (EARS):** Cuando una reparación esté probada (tests en
verde y prueba en vivo con el umbral alcanzado), el sistema deberá pedir a la
persona que valide el push de la rama del fix. Mientras no haya esa validación,
no deberá hacer push, merge a `main` ni abrir pull requests.

### RF-13 — Contrato de entrada y preparación del ciclo

**Criterio de aceptación (EARS):** Cuando la persona ejecute el proceso, el
sistema deberá aceptar la ruta del diagnóstico (por defecto
`scrapers-pipeline/logs/diagnostic_last.json`) y documentar su contrato de
entrada y salida, de forma que el futuro ciclo "run nocturno → verificación →
reparación" pueda invocarlo sin cambios de interfaz.

### RF-14 — Aislar las reparaciones entre fuentes

**Criterio de aceptación (EARS):** Cuando haya varias fuentes fallidas, cada
una se reparará en su propia rama y registro, sin mezclar cambios entre
fuentes; un fix fallido o pendiente de decisión no deberá bloquear las
reparaciones de las demás.

### RF-15 — No alterar la landing, Azure ni las specs durante la reparación

**Criterio de aceptación (EARS):** Mientras se repara, el sistema no deberá
subir datos a Azure, modificar la landing, los manifests ni los ficheros de
`specs/`; la única ejecución de scrapers permitida es la prueba en vivo acotada
de RF-9.

## Requisitos no funcionales

- Los mensajes y registros dirigidos a la persona estarán en español; los
  identificadores, campos de máquina y nombres de fichero, en inglés.
- El núcleo determinista (selección de objetivos, umbral incremental y
  registros) se cubrirá con tests unitarios y de integración offline, sin red
  ni credenciales.
- La prueba en vivo la ejecuta el implementador; el verificador no necesita
  red para su trabajo.
- No se implementarán soluciones de CAPTCHA ni se contratarán servicios de
  pago; las nuevas dependencias de los scrapers se justificarán en el registro.
- Los registros y evidencias no contendrán credenciales ni datos personales
  innecesarios.
- La selección de objetivos, el umbral y el registro serán reproducibles: la
  misma entrada produce la misma decisión.
- Las skills y agentes del proceso viven en `.opencode/` y quedan fuera de git
  por decisión del proyecto; el proceso documentará su instalación.

## Casos límite

- No existe el diagnóstico o es inconcluso: no se inicia ninguna reparación y
  se indica por qué.
- Fuente fallida por bloqueo (CAPTCHA/rate limit): se busca la vía que lo evite
  sin resolverlo.
- Fuente fallida por error técnico (por ejemplo, exit code distinto de cero o
  un portal de Multi-site con error): se investiga el error real de la fuente.
- Fuente fallida por quedarse sin ofertas con la ejecución correcta: se
  contrasta la web real (búsquedas, resultados y paginación).
- Fuente fallida por completitud de un campo obligatorio (por ejemplo,
  `description`): la investigación se centra en ese campo y en su contrato.
- Fuente fallida solo por la publicación en la landing y no por la captura: se
  excluye de la reparación del scraper y se reporta a la persona.
- La prueba en vivo no alcanza el umbral: la reparación no está probada y se
  itera dentro de la misma rama; si se agotan las opciones locales, se escala.
- La prueba en vivo obtiene 0 ofertas: no prueba nada; se trata como intento
  fallido y se documenta.
- La fuente ya era correcta: no se toca.
- Un nuevo run diario llega durante una reparación: el objetivo original se
  mantiene y queda registrado el run de origen.
- El fix requeriría incumplir `robots.txt`/TOS o usar servicios de pago:
  el proceso se detiene y consulta a la persona.
- Varios portales de Multi-site fallidos en la misma ejecución: cada portal es
  un objetivo independiente, con su rama y su prueba en vivo aislada.
- El merge de Multi-site no se ejecuta durante la prueba en vivo; solo el
  portal afectado.
- El historial de umbrales no existe todavía: el umbral de esa fuente es 1.
- Una reparación queda descartada o sin decisión: se registra y no bloquea a
  las demás fuentes.

## Fuera de alcance

- Disparar el proceso automáticamente al terminar el run nocturno (v1 manual;
  el ciclo queda preparado, no automatizado).
- Bloquear decisiones que excedan los medios locales (proxies, servicios de
  pago): solo se proponen a la persona.
- Resolver CAPTCHAs o verificaciones de identidad.
- Cambiar los umbrales, contratos o comportamiento del diagnóstico de la
  spec 001.
- Subir, publicar o transformar datos (landing, Azure, Databricks).
- Validar o modificar el dashboard de Power BI.
- Refactors ajenos a la fuente reparada.

## Criterios de finalización

- El núcleo determinista (objetivos, brief, umbral, registros y CLI) existe en
  `scrapers-pipeline/repair/` con tests offline en verde.
- El subagente `web-inspector` y la elección de skills quedan documentados para
  el orquestador, y las tres skills están instaladas y en uso.
- Cada reparación deja un registro versionado con plan, cambios, pruebas y
  resultado; el índice y el historial de umbrales se actualizan.
- La selección de objetivos funciona sobre el diagnóstico real del 2026-09-30:
  detecta `infojobs`, `stepstone_nl`, `nvb`, `jobs_ch`, `glassdoor`, `indeed` y
  `linkedin` con su evidencia, y las investigaciones secundarias.
- **Comprobación real:** el proceso repara y prueba en vivo al menos la fuente
  `infojobs` (bloqueo por CAPTCHA al abrir) con el umbral alcanzado, tests en
  verde y registro completo, y la persona valida el push; las demás fuentes
  fallidas quedan documentadas como reparaciones pendientes.
- No se hace push ni merge sin validación de la persona.
- El comando de tests del módulo de reparaciones queda reflejado en
  `AGENTS.md` con el visto bueno de la persona.

## Decisiones aclaradas

- Proceso ejecutado por agentes opencode: especialista web → implementador →
  verificador; la prueba en vivo la hace el implementador y el verificador se
  centra en tests y diff.
- Una spec (la 004) construye el proceso; cada fix se abre en su propia rama
  `repair/<fuente>-<fecha>` con su pequeño plan almacenado.
- El push de una rama de fix se pide a la persona solo cuando el fix esté
  probado (tests en verde y prueba en vivo con umbral alcanzado).
- Se permite el rediseño del scraper; las dependencias se minimizan pero no se
  bloquea un arreglo claramente necesario.
- Sin servicios de pago; primero técnicas locales y respeto a
  `robots.txt`/TOS; el escalado (por ejemplo, proxies) se consulta.
- Prioridad a evitar CAPTCHA y rate limit sin entrar en ellos.
- La prueba en vivo es acotada y ligada a la ejecución del proceso; no se
  repite en exceso y nunca sube datos.
- Umbral incremental: la siguiente reparación de una fuente exige más ofertas
  que el mayor recuento verificado anterior (mínimo 1 la primera vez).
- Los registros de reparación se conservan en el repositorio (rama del fix) y
  el historial de umbrales es legible por máquina.
- Las skills y agentes quedan fuera de git (`.opencode/` ignorado), por
  decisión de la persona.
- Primer caso real: InfoJobs, que abre directamente en CAPTCHA.
- La primera versión se ejecuta a mano; el contrato queda preparado para el
  ciclo nocturno.
