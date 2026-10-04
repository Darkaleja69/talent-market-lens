<!--
Plantilla de un registro de reparación (spec 004, RF-2 y RF-11; tarea T-12).

La usa `repair/records.py` (T-13): copia este fichero a
`repairs/<YYYYMMDD>-<fuente>/plan.md` y sustituye los marcadores:

  {{source}}    id de la fuente en inglés (p. ej. infojobs)
  {{run_date}}  fecha del run de origen, YYYY-MM-DD
  {{branch}}    rama del fix, repair/<fuente>-<YYYYMMDD>
  {{status}}    estado del registro: planificado al crearlo; probado,
                descartado o escalado al terminar

Actualización sin perder lo escrito (T-14). Cada sección está delimitada por
dos comentarios HTML estables, invisibles al renderizar:

  - apertura: un comentario HTML cuyo contenido es  record:section:<id>
  - cierre:   un comentario HTML cuyo contenido es  /record:section:<id>

`complete_record` localiza ambos comentarios de la sección que actualiza y
sustituye solo el contenido que queda entre ellos; el resto del fichero se
conserva tal cual. No cambies ni reordenes los marcadores: la actualización es
determinista y depende de ellos. Los ids de sección, en inglés, son: source,
failure, evidence, investigation, changes, tests, live_test, quality, result.

Idioma y saneado: los ids de fuente, los códigos de máquina y los nombres de
fichero van en inglés; el texto para la persona, en español. No incluyas
credenciales, tokens ni rutas de perfiles de navegador (RF-11): las rutas
locales de usuario se escriben como <HOME>.
-->

# Reparación {{source}} — run {{run_date}}

<!-- record:section:source -->
## Fuente y run

- **Fuente:** {{source}}
- **Run de origen:** {{run_date}}
- **Rama del fix:** {{branch}}
- **Registro creado:** (fecha de apertura)
<!-- /record:section:source -->

<!-- record:section:failure -->
## Fallo observado

_(Pendiente: estado y resultado del diagnóstico, ofertas del run y del
snapshot, motivos y brecha de completitud.)_
<!-- /record:section:failure -->

<!-- record:section:evidence -->
## Evidencia

_(Pendiente: extractos saneados del diagnóstico y recortes de log; rutas
locales referenciadas, sin credenciales ni perfiles de navegador.)_

- Diagnóstico: ...
- Rutas locales: ...
- Extractos: ...
<!-- /record:section:evidence -->

<!-- record:section:investigation -->
## Plan de investigación

_(Pendiente: playbook §6 aplicable, hechos observados frente a hipótesis,
comprobación de robots.txt y términos de uso, causa probable y cambio
recomendado.)_
<!-- /record:section:investigation -->

<!-- record:section:changes -->
## Cambios realizados

_(Pendiente: módulos y ficheros modificados, diseño elegido y dependencias
nuevas justificadas, si las hay.)_
<!-- /record:section:changes -->

<!-- record:section:tests -->
## Pruebas: tests

_(Pendiente: suites ejecutadas del scraper afectado y del diagnóstico, y su
resultado.)_
<!-- /record:section:tests -->

<!-- record:section:live_test -->
## Pruebas: prueba en vivo

_(Pendiente: alcance acotado y repetible, comando, recuento de ofertas, umbral
exigido, restricciones respetadas —sin Azure, landing ni merge— y evidencia.)_
<!-- /record:section:live_test -->

<!-- record:section:quality -->
## Calidad

_(Pendiente: tabla antes/después por campo y meta, y veredicto de `quality.py`;
se rellena con `quality_before.json` y `quality_after.json`.)_

| Campo | Obligatorio | Antes | Después | Delta | Meta | Estado |
|---|---|---|---|---|---|---|
| _(pendiente)_ | | | | | | |
<!-- /record:section:quality -->

<!-- record:section:result -->
## Resultado y estado

- **Resultado:** (pendiente)
- **Estado:** {{status}} (al crear, `planificado`; al terminar, `probado`,
  `descartado` o `escalado`)
- **Validación del push:** pendiente de la persona (RF-12)
<!-- /record:section:result -->
