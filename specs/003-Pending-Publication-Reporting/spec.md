# Especificación funcional: informar la publicación pendiente en el diagnóstico

## Contexto y objetivo

El diagnóstico de la ejecución diaria (spec 001) comprueba que los datos
aceptados estén publicados y sean legibles en la landing. Cuando un objeto
esperado todavía no es visible, la comprobación del manifest lo marca como
pendiente, pero el flujo de la CLI intenta además medir el delta publicado y, al
no poder descargar el objeto ausente, clasifica la publicación como
"no comprobada" (`not_checked`).

Esto incumple RF-8 de la spec 001: los datos que aún no aparecen deben
informarse como "pendiente de publicar", no como un fallo ni como una
comprobación no realizada. Esta especificación corrige esa clasificación.

## Usuarios

- La persona que mantiene y opera el proyecto, con conocimientos de ingeniería
  de datos de nivel junior.

## Historias de usuario

- Como responsable del proyecto, quiero que los datos que aún no aparecen en la
  landing se informen como pendientes de publicar, no como no comprobados.

## Requisitos funcionales

### RF-1 — Informar la publicación pendiente

**Criterio de aceptación (EARS):** Cuando el diagnóstico compruebe la
publicación de una fuente y el objeto esperado aún no sea visible en la landing,
el sistema deberá clasificar la publicación como "pendiente de publicar"
(`PUBLICATION_PENDING`), mostrarlo así en el informe en español y no marcar la
fuente como fallida por ese motivo.

Un fallo de conectividad o de credenciales al consultar la landing no es un
objeto pendiente: se mantiene como publicación "no comprobada"
(`PUBLICATION_NOT_CHECKED`).

## Requisitos no funcionales

- Los mensajes dirigidos a la persona deberán estar en español.
- La corrección se cubrirá con tests de integración offline, sin red, Azure ni
  credenciales.
- El diagnóstico sigue sin alterar datos, configuración ni publicación.
- Se reutiliza el stack existente; no se añaden dependencias.

## Casos límite

- Un manifest referencia un objeto remoto que aún no está listado: pendiente de
  publicar.
- La landing no responde por conectividad o credenciales: no comprobada, no
  pendiente.
- El objeto está visible y es correcto: la clasificación existente no cambia.

## Fuera de alcance

- El matiz de un delta publicado vacío (`no_new_offers` frente a `not_checked`),
  documentado como observación y no incluido en esta corrección.
- Cualquier otro cambio del diagnóstico de la spec 001.

## Criterios de finalización

- Un test de integración demuestra que, con un objeto remoto no visible,
  `run_diagnostic` produce `PUBLICATION_PENDING` para esa fuente y no la marca
  como fallida.
- El informe en español indica "pendiente de publicar".
- La suite del diagnóstico (`python -m pytest scrapers-pipeline/tests -q`) queda
  en verde.
