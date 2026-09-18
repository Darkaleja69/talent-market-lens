# Roadmap

**Talent Market Lens v1 is complete and running end to end** — multi-source
ingestion, a Medallion pipeline on Databricks and a Power BI report on top of a
clean star schema.

The platform will keep evolving in small, documented iterations.

## Focus areas

- **Operational maturity** — incremental loading, idempotent upserts, scheduling
  and monitoring of the daily pipeline.
- **Data quality** — run-level metrics, schema contracts and richer
  freshness/coverage signals.
- **Broader coverage** — additional job portals and cross-portal
  de-duplication.
- **Analytical depth** — new business questions on top of the existing model.
- **Applied ML (early exploration)** — explainable, reproducible ranking models.

## Guiding principles

- Reproducible and config-driven, with no secrets in the repository.
- Quality and uncertainty are made explicit — never silently imputed.
- Every iteration is documented and testable.
