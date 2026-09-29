# Plan técnico: informar la publicación pendiente en el diagnóstico

## 1. Objetivo y límites

Corregir la clasificación de publicación del diagnóstico cuando el objeto
esperado aún no es visible, sin cambiar el resto de estados ni introducir
dependencias. Todo el trabajo ocurre en `scrapers-pipeline/verification/` y
`scrapers-pipeline/tests/`.

## 2. Causa y cambio propuesto

`verify_run._publication_for` ejecuta `landing.verify_manifest` y, acto seguido,
`trends.load_published_completeness`. Si el manifest referencia un objeto no
visible, `verify_manifest` devuelve `STATE_PENDING`, pero
`load_published_completeness` intenta descargarlo, lanza `RemoteError` y el
`except` lo convierte en `not_checked`, descartando el estado pendiente ya
calculado.

Cambio: resolver primero el estado del manifest y, si es `STATE_PENDING`,
clasificar la publicación como pendiente sin intentar medir el delta publicado.
El `RemoteError` por conectividad/credenciales (fallo de listado o descarga de un
objeto que sí está listado) sigue clasificándose como `not_checked`.

## 3. Módulos afectados

| Módulo | Responsabilidad | RF |
|---|---|---|
| `verification/verify_run.py` (`_publication_for`) | Resolver el manifest antes de medir el delta; no medir si el objeto está pendiente. | RF-1 |
| `verification/publication.py` | Sin cambios: `classify_publication` ya mapea `STATE_PENDING` a `PUBLICATION_PENDING`. | RF-1 |
| `tests/test_integration_landing_trends.py` | Sustituir el comentario del defecto por un test que exija `PUBLICATION_PENDING` por `run_diagnostic`. | RF-1 |

## 4. Estrategia de tests

- Test de integración offline: manifest con un fichero que referencia un objeto
  remoto no listado → `run_diagnostic(reader=...)` produce
  `PUBLICATION_PENDING`, la fuente no es fallida y el informe dice "pendiente de
  publicar".
- Test de no-regresión: un fallo de listado (`RemoteError`) sigue produciendo
  `not_checked` sin marcar la fuente como fallida.
- Ejecutar `python -m pytest scrapers-pipeline/tests -q` desde la raíz.

## 5. Cumplimiento de la constitución

- **Stack simple:** solo biblioteca estándar y lo ya existente; sin dependencias
  nuevas.
- **Spec y código:** el cambio implementa únicamente RF-1 de esta spec.
- **Lógica e interfaz:** la decisión vive en la coordinación de `_publication_for`;
  `publication.classify_publication` (regla pura) no cambia.
- **Tests:** test de integración offline añadido/actualizado.
- **Persistencia:** no se escribe nada; solo lectura remota simulada.
- **Idioma:** identificadores en inglés; informe y mensajes en español.
