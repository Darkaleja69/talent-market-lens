# InfoJobs Job Scraper

Scraper educado para ofertas de empleo de InfoJobs con Playwright (navegador real), respetando `robots.txt` y sin evadir CAPTCHAs.

## Requisitos

- Python 3.10+
- Google Chrome instalado
- [Playwright](https://playwright.dev) con Chromium

## Instalación

```bash
pip install -r requirements.txt
playwright install chromium
```

## Ejecución

```bash
python -m scraper.main

# Opciones:
python -m scraper.main --ciudades madrid,barcelona --keywords data --paginas 2
python -m scraper.main --login             # pausa para login manual
```

## Salidas

- `data/offers_YYYYMMDD_HHMMSS.xlsx` — Excel tabular
- `data/offers_YYYYMMDD_HHMMSS.parquet` — Parquet
- `data/delta_table/` — Delta Lake (para Databricks)

## Verificación

```bash
ruff check scraper tests
mypy scraper
pytest tests/
```
