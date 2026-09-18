# Roadmap

**Talent Market Lens v1 está completo y funcionando de punta a punta**: ingesta
multi-fuente, un pipeline Medallion en Databricks y un informe Power BI sobre un
esquema en estrella limpio.

La plataforma seguirá evolucionando en iteraciones pequeñas y documentadas.

## Áreas de foco

- **Madurez operativa** — carga incremental, upserts idempotentes, planificación
  y monitorización del pipeline diario.
- **Calidad de datos** — métricas por ejecución, contratos de esquema y mejores
  señales de frescura y cobertura.
- **Mayor cobertura** — nuevos portales y deduplicación entre portales.
- **Profundidad analítica** — nuevas preguntas de negocio sobre el modelo actual.
- **ML aplicado (exploración inicial)** — modelos de ranking explicables y
  reproducibles.

## Principios

- Reproducible y guiado por configuración, sin secretos en el repositorio.
- La calidad y la incertidumbre se hacen explícitas, nunca se imputan a ciegas.
- Cada iteración está documentada y es testeable.
