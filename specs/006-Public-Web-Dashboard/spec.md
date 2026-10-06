# Especificación funcional: dashboard web público del portfolio

## Contexto y objetivo

El informe Power BI del proyecto (`Job_Offers_Dashboard.pbip`) demuestra la capa
BI, pero no puede ser el canal de consulta pública: exige licencia y no permite
que cualquier visitante explore los datos reales sin cuenta. Hoy el portfolio
solo puede enseñar capturas del informe. El portfolio necesita una vía pública,
interactiva, sin licencia y sin coste para consultar las ofertas, los salarios,
las skills y la geografía que ya produce la capa Gold.

Esta funcionalidad publica un dashboard web estático en GitHub Pages: una tarea
final del job de Databricks escribe un export Parquet de Gold más un
`meta.json` de frescura en ADLS; un workflow de GitHub Actions lo descarga a
diario con una SAS de solo lectura y despliega el sitio; el navegador del
visitante ejecuta SQL sobre los Parquet con DuckDB-WASM y pinta las vistas con
ECharts. Los datos no se versionan, no hay backend ni login, y Power BI queda
como evidencia de la capa BI (`.pbip`, capturas y GIF), no como canal de
consulta. El embedding temporal de Power BI queda como extra opcional fuera de
esta spec.

El trabajo está diseñado para ejecutarse **en paralelo a la spec 004** (rama y
worktree propios creados desde `main`, sin solapar archivos con la 004/005) y
sin tocar el pipeline, la landing ni las specs ajenas.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería
  de datos de nivel junior.
- Cualquier visitante anónimo del portfolio (reclutador, reviewer técnico), sin
  cuenta, sin licencia y sin instalar nada.

## Historias de usuario

- Como responsable del portfolio, quiero un enlace público donde cualquiera vea
  el dashboard con datos reales, sin cuentas ni licencias.
- Como visitante, quiero explorar ofertas, salarios, skills y tendencias con
  filtros y búsqueda, y abrir la oferta original.
- Como visitante, quiero la interfaz en español o en inglés, según mi idioma, y
  que mi elección se recuerde.
- Como visitante, quiero saber de cuándo son los datos y de dónde salen, con
  sus limitaciones, para confiar en lo que veo.
- Como mantenedor, quiero que el sitio se actualice solo a diario sin
  commitear datos ni exponer credenciales.
- Como mantenedor, quiero probar la web en local antes de publicarla.
- Como mantenedor, quiero que este trabajo no interfiera con la spec 004 ni con
  el pipeline nocturno.
- Como responsable de los datos, quiero publicar solo campos públicos de
  ofertas públicas y ninguna credencial.

## Requisitos funcionales

### RF-1 — Export de la capa Gold para la web

**Criterio de aceptación (EARS):** Cuando se ejecute el job de Databricks con la
tarea de export al final, el sistema deberá escribir en la ruta configurada de
ADLS (`gold/web_export/` por defecto) un export Parquet con nombres de fichero
fijos —`fact_offers`, `fact_offer_skills`, `dim_skill_list`, `dim_calendar`— y
un `meta.json`, con solo los campos públicos y necesarios para las vistas.
La tarea deberá ser idempotente (repetirla no duplica ni corrompe el export),
añadirse al final del job sin alterar las tareas existentes y no depender de
Power BI para calcular la geografía.

### RF-2 — Geografía portada a funciones puras

**Criterio de aceptación (EARS):** Cuando el export calcule `GeoCountry` y
`GeoRegion`, deberá usar funciones puras equivalentes a las del modelo Power BI
(la normalización de país de la partición M de `Fact_Offers` y el mapeo de
`Dim_RegionMap` como constante versionada en el repositorio) y cubiertas por
tests, de modo que el mapa y los filtros de país funcionen sin Power BI.

### RF-3 — `meta.json` de frescura y trazabilidad

**Criterio de aceptación (EARS):** Siempre que se escriba un export, deberá
acompañarse de `meta.json` con la fecha de los datos, la fecha de generación,
la versión del export, el modo (`full` o `aggregated`), el recuento por tabla y
por fuente; la web deberá mostrar la fecha de los datos de forma visible.

### RF-4 — Control de tamaño con agregados

**Criterio de aceptación (EARS):** Si el export supera el umbral de tamaño
acordado (~25 MB, configurable), el sistema deberá generar además agregados para
las vistas analíticas y limitar el explorador a un alcance documentado, de modo
que el sitio siga cargando de forma razonable; el modo usado quedará registrado
en `meta.json` y explicado en la metodología.

### RF-5 — Actualización diaria y publicación automática

**Criterio de aceptación (EARS):** Cuando se ejecute el workflow de GitHub
Actions (programado a diario o lanzado a mano), deberá descargar el export
desde ADLS con `curl` y una SAS de solo lectura guardada como secreto
(`WEB_EXPORT_SAS`), ensamblar el sitio y desplegarlo en GitHub Pages. No deberá
versionar datos ni usar credenciales de escritura; si la descarga o el
ensamblado fallan, deberá fallar de forma visible sin publicar un sitio
incompleto.

### RF-6 — Sitio estático sin backend ni build

**Criterio de aceptación (EARS):** La web deberá ser HTML, CSS y módulos ES
servidos como sitio estático, con DuckDB-WASM y ECharts cargados por CDN con
versión fijada, sin backend, sin login y sin proceso de build; deberá ejecutar
SQL en el navegador sobre los Parquet del export.

### RF-7 — Vistas del dashboard

**Criterio de aceptación (EARS):** La web deberá ofrecer, al menos, estas
vistas navegables y enlazables:
- **Market Pulse:** KPIs de total de ofertas, empresas, países y ciudades
  contratando, ofertas de los últimos 7 y 30 días, reparto remoto/híbrido/
  presencial, tasa de publicación de salario, tendencia mensual, top roles,
  top empresas y reparto por experiencia.
- **Roles & Skills:** demanda y cuota por skill, salario mediano por skill,
  burbuja demanda vs. salario, donut por categoría y tabla de benchmark.
- **Salary Insights:** salario medio, mediano, P25 y P75, medias por categoría
  de rol y modalidad, top skills pagadas y mapa por país.
- **Opportunity Explorer:** tabla consultable de ofertas individuales.
- **Metodología:** texto de fuentes, proceso, frescura y limitaciones.

### RF-8 — Medidas portadas con semántica equivalente

**Criterio de aceptación (EARS):** Las medidas clave del modelo (al menos:
Total Job Offers; Companies/Countries/Cities Hiring; Offers Last 7/30 Days;
Remote/Hybrid/On-site/Remote-Friendly Share; Salary Disclosure Rate; Skill
Demand; Skill Share; Skill Median Salary EUR; Avg/Median/P25/P75 Mid Salary
EUR) deberán conservar la semántica del modelo: filtros de calidad salarial
(`> 0`, `≤ 1 000 000`, `min ≤ max`), ventanas de 7/30 días relativas a la
última fecha de publicación, cuotas sobre el total del contexto y demanda de
skill por oferta distinta. Sus cifras deberán ser cotejables con el informe
Power BI para la misma fecha de datos, y las diferencias conocidas se
documentarán en la metodología.

### RF-9 — Filtros globales

**Criterio de aceptación (EARS):** El sitio deberá aplicar los filtros globales
—país, experiencia, rol, modalidad, empresa, skill, fuente y rango salarial— de
forma coherente a todas las vistas, mostrando los filtros activos y
permitiendo limpiarlos.

### RF-10 — Explorador de ofertas

**Criterio de aceptación (EARS):** El explorador deberá permitir buscar y
filtrar ofertas y mostrar, por oferta, campos públicos (título, empresa,
ubicación, modalidad, categoría de rol, salario mediano anual, fecha y fuente)
con un enlace real a la oferta original; deberá limitar el volumen mostrado
(paginación o tope) y no incluir descripciones ni columnas internas.

### RF-11 — Instantánea y desarrollo local

**Criterio de aceptación (EARS):** Mientras se desarrolla o si no hay CI, el
sistema deberá permitir preparar una instantánea local equivalente
(`scrapers-pipeline/export_web_snapshot.ps1`) y servir el sitio en local con el
mismo contrato de archivos (mismos nombres y rutas relativas que en Pages), sin
depender de Databricks ni de Actions.

### RF-12 — Aislamiento del trabajo paralelo a la 004

**Criterio de aceptación (EARS):** Mientras la spec 004 esté en curso, este
trabajo deberá vivir en su rama y worktree propios (`spec/006-public-web-dashboard`
creada desde `main`), no deberá tocar `scrapers-pipeline/repair/**`,
`.opencode/**` ni specs ajenas, y no deberá subir datos a Azure ni modificar la
landing ni el pipeline nocturno. El orden de merge con la 004 será indiferente
mientras no haya solape de archivos.

### RF-13 — Metodología, atribución y límites

**Criterio de aceptación (EARS):** La web deberá incluir la metodología y el
aviso correspondiente: fuentes de los datos, traza del pipeline, normalización
salarial, procedencia de fechas, frescura, limitaciones conocidas y nota legal
(ofertas públicas de portales de empleo; sin datos personales ni credenciales),
en español y sin prometer más precisión de la que los datos tienen.

### RF-14 — Degradación visible

**Criterio de aceptación (EARS):** Si el export no está disponible, está vacío
o la descarga falla, la web o el workflow deberán fallar de forma visible
(aviso y fecha del último dato disponible), y nunca mostrar cifras inventadas,
un sitio en blanco ni datos a medias.

### RF-15 — Documentar la publicación y preservar Power BI como evidencia

**Criterio de aceptación (EARS):** Al terminar, los README (raíz y español)
deberán documentar la URL pública, la actualización diaria y dónde vive el
export; el proyecto Power BI con sus capturas y GIF se mantendrá como evidencia
  de la capa BI, con la aclaración de que el canal público es el sitio.

### RF-16 — Interfaz bilingüe (español e inglés)

**Criterio de aceptación (EARS):** La web deberá ofrecer su interfaz en español
e inglés mediante un selector visible, recordar la preferencia del visitante y
aplicarla a todas las vistas, gráficos, mensajes y a la metodología; los textos
de ambos idiomas deberán mantener el mismo contenido y las URLs seguirán siendo
compartibles.

## Requisitos no funcionales

- **Stack simple (constitución 1):** sin dependencias nuevas del núcleo; el
  export reutiliza PySpark del proyecto; DuckDB-WASM y ECharts se cargan por
  CDN con versión fijada y licencia compatible (MIT / Apache-2.0); el CI usa
  `curl`.
- **Lógica e interfaz (constitución 3):** proyección, geografía, metadatos y
  decisión de tamaño viven en un módulo comprobable; el notebook, el workflow y
  la web solo coordinan entradas y salidas.
- **Tests (constitución 4):** funciones puras con pytest y tests de integración
  con Spark; antes de cerrar, las suites existentes
  (`python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q`) quedan en verde.
- **Persistencia (constitución 5):** los datos crudos siguen en Azure; el
  export es derivado y no se versiona; la web es de solo lectura; el
  repositorio no crece con datos.
- **Idioma (constitución 6):** identificadores y artefactos de máquina en
  inglés; la interfaz del sitio, en español e inglés con selector; los registros
  y mensajes para la persona, en español; los datos se muestran tal cual, en
  inglés.
- **Privacidad y seguridad:** solo campos públicos de ofertas públicas; sin
  descripciones ni datos personales; SAS de solo lectura, con ámbito mínimo y
  rotación documentada, nunca en el repositorio, en logs ni en artefactos.
- **Rendimiento:** primera carga razonable en conexión normal; consultas
  interactivas (< ~1 s) con el export de tamaño típico; los Parquet se cargan
  una vez por visita.
- **Accesibilidad y móvil:** diseño responsive, navegable con teclado,
  contraste suficiente y sin depender de hover.
- **Reproducibilidad:** la misma fecha de datos produce el mismo sitio; las
  versiones del CDN quedan fijadas y `meta.json` identifica la versión del
  export.

## Casos límite

- El export supera ~25 MB: se generan agregados y el explorador se limita a un
  alcance documentado; el modo queda en `meta.json` y en la metodología.
- La SAS caduca o no tiene permiso: el workflow falla con mensaje claro y el
  sitio anterior queda intacto (no se publica a medias).
- El run nocturno no generó export nuevo o el job falló: la web conserva el
  último dato y muestra su fecha; el workflow lo señala.
- No hay red o el CDN está bloqueado: la web muestra un aviso, no se queda en
  blanco.
- El navegador no soporta WASM: mensaje de compatibilidad.
- Ofertas sin salario: quedan fuera de las métricas salariales como en el
  modelo; la tasa de publicación lo refleja.
- `location_country` vacío o `(Not specified)`: `GeoCountry` queda como
  `(Not specified)`; región sin mapeo: `(Other)`, igual que el modelo.
- Ofertas duplicadas: Gold ya deduplica; el export no deduce por su cuenta.
- URL de oferta rota o caducada: se muestra igual (la oferta era real en su
  fecha); no se verifica en vivo.
- Workflow lanzado fuera de la rama por defecto: el cron solo corre en la rama
  por defecto; durante el desarrollo se usa `workflow_dispatch`.
- Dos ejecuciones del workflow a la vez: se serializan para no pisarse.
- La tarea de export no tiene permiso de escritura en ADLS: fallo explícito en
  el job, sin publicar nada.
- Pico de tráfico en Pages: el sitio es estático y cacheable; los límites del
  plan gratuito son suficientes para un portfolio.
- La fecha de datos es más antigua que el último run: se muestra la fecha real
  y la metodología explica la frescura.
- Durante la 004, el job o el pipeline cambian: la 006 no los toca; cualquier
  cruce se detiene y se consulta a la persona.

## Fuera de alcance

- Power BI Embedded, Fabric u otras licencias de pago; el embed de Power BI es
  un extra opcional que no forma parte de esta spec.
- Backend, API, login, RLS o cualquier cómputo en servidor.
- Replicar las 58 medidas o las 5 páginas de Power BI al detalle.
- Publicar datos crudos, descripciones o columnas internas de calidad.
- Automatizar Databricks desde el repositorio: la tarea del export y la
  configuración de Pages y del secreto son pasos manuales únicos, documentados.
- App con build (Vite/TypeScript): solo si el sitio crece.
- ML, recomendador, alertas o analítica de uso.
- Idiomas distintos del español y el inglés, PWA y otros extras.
- Modificar la spec 004/005, `scrapers-pipeline/repair/**`, `.opencode/**`, la
  landing, los manifests o el pipeline nocturno.

## Criterios de finalización

- El módulo del export (proyección, geografía, metadatos y tamaño) existe con
  funciones puras probadas, y el notebook y la tarea final del job funcionan de
  forma idempotente con una ejecución real y su tamaño medido.
- El workflow está en verde (dispatch y cron) con Pages activo y la URL pública
  sirviendo las cuatro vistas en ambos idiomas, también en móvil.
- Paridad cotejada: 4–5 KPIs del sitio coinciden con el informe Power BI (o sus
  capturas) para la misma fecha de datos, con las diferencias documentadas.
- Filtros, búsqueda y enlaces reales funcionan; `meta.json` avanza tras un
  ciclo completo; la web avisa si el dato falta o es antiguo.
- Las suites existentes quedan en verde:
  `python -m pytest databricks_notebooks/tests -q` y
  `python -m pytest scrapers-pipeline/tests -q`.
- README y documentación describen la URL, el ciclo diario y el export; Power BI
  queda documentado como evidencia.
- **Comprobación real:** abrir la URL pública en incógnito (escritorio y
  móvil), comprobar las cuatro vistas, filtros, búsqueda y enlaces, cotejar
  4–5 KPIs contra el informe o sus capturas de la misma fecha, y ejecutar un
  ciclo completo del workflow (dispatch tras un export nuevo y después el
  cron), registrando el resultado como evidencia.
- No se hace push ni merge sin validación de la persona (flujo de `AGENTS.md`).

## Decisiones aclaradas

- App sin build, todo por CDN, coherente con "stack simple"; Vite/TS solo como
  mejora futura si el sitio crece.
- Los datos no se commitean: se inyectan en el deploy desde ADLS con una SAS de
  solo lectura; el repositorio no crece.
- Privacidad: se muestran título, empresa y URL reales (ofertas públicas); no
  se publican descripciones ni columnas internas; la metodología lo explica.
- Power BI queda como evidencia (`.pbip` + capturas + GIF); el embed es un
  extra fuera de la spec.
- Actualización diaria vía GitHub Actions; el cron solo corre en la rama por
  defecto y durante el desarrollo se usa `workflow_dispatch`.
- Geografía portada a funciones puras con `Dim_RegionMap` versionada como
  constante; si el modelo cambia, la constante se actualiza con la spec.
- Solo las ~18 medidas clave; el resto del modelo no se replica.
- UI bilingüe español/inglés con selector (datos tal cual, en inglés); idiomas
  adicionales quedan fuera de alcance.
- Trabajo en worktree/rama `spec/006-public-web-dashboard` desde `main`
  (que ya incluye la 005); paralelo a la 004 y sin solape de archivos.
- La tarea de Databricks, el secreto `WEB_EXPORT_SAS` y la configuración de
  Pages son pasos manuales únicos, documentados en el plan.
