# LinkedIn Jobs Scraper â€” Data (EspaÃ±a)

Scraper de ofertas de empleo de LinkedIn Jobs para roles de datos
(Data Analyst, Data Scientist, Data Engineer, Data Architect, BI) en
Madrid, Barcelona y Bilbao. Devuelve un fichero tabular (`jobs.csv` +
`jobs.parquet`) listo para ingest en un Lakehouse de Databricks.

## Enfoque

- **NÃºcleo local**: Python + Playwright (vÃ­a `patchright`, fork parcheado
  anti-detecciÃ³n) + sesiÃ³n autenticada con tu propia cuenta de LinkedIn.
- **Respaldo comercial**: si el nÃºcleo local detecta un bloqueo (CAPTCHA /
  checkpoint), conmuta automÃ¡ticamente al actor *LinkedIn Jobs Scraper*
  de Apify para los (role, ciudad) restantes (requiere `APIFY_TOKEN`).
- **Anti-bloqueo respetuoso**: delays humanos aleatorios, scroll lento
  incremental, sin concurrencia, perfil persistente, volumen bajo
  (~75 ofertas). **No evade CAPTCHAs**: si aparece, se detiene y conmuta
  a Apify (o te avisa).

## âš ï¸ Aviso legal

El scraping automatizado puede contravenir los TÃ©rminos de Servicio de
LinkedIn (secciÃ³n 8.2). Usas tu propia cuenta bajo tu responsabilidad.
El caso *hiQ v. LinkedIn* (EE.UU.) ampara scrapear datos pÃºblicos, pero
el login automatizado implica aceptar los ToS. Este scraper **no** elude
CAPTCHAs ni controles de acceso: si aparecen, se detiene o conmuta a un
proveedor comercial (Apify) que gestiona el cumplimiento por su cuenta.

## InstalaciÃ³n

```powershell
cd <ruta-proyecto>\linkedin_jobs_scraper

# (Opcional) entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Dependencias
pip install -r requirements.txt

# Navegador Chromium (binario parcheado)
patchright install chromium
```

## ConfiguraciÃ³n

1. Copia `.env.example` a `.env` y rellena tus credenciales:

```
LINKEDIN_EMAIL=tu_email@ejemplo.com
LINKEDIN_PASS=tu_password
APIFY_TOKEN=           # opcional, para el respaldo
HEADLESS=false         # false = ventana visible (mÃ¡s humano)
```

2. Ajusta `config.yaml` si quieres cambiar roles, ciudades, delays o
   volumen. Los `geoId` de Madrid/Barcelona/Bilbao ya estÃ¡n puestos.

## Uso

### EjecuciÃ³n completa (15 combinaciones, ~75 ofertas, con detalle)

```powershell
python -m src.main
```

Esto:
1. Lanza Chromium con tu perfil persistente.
2. Si no hay sesiÃ³n guardada, hace login (pedirÃ¡ 2FA por consola si lo
   tienes activado â€” solo la primera vez).
3. Para cada (role Ã— ciudad): navega, scroll lento, extrae tarjetas,
   visita cada oferta para descripciÃ³n/skills/salario, pausas humanas.
4. Si hay bloqueo, conmuta esa combinaciÃ³n a Apify.
5. Escribe `data/output/jobs.csv` + `data/output/jobs.parquet`.

### Variantes

```powershell
# Solo un rol y una ciudad (prueba rÃ¡pida)
python -m src.main --role "Data Analyst" --city Madrid

# Solo SERP, sin entrar en cada oferta (rÃ¡pido, sin descripciÃ³n)
python -m src.main --no-detail

# Limitar a 10 ofertas por bÃºsqueda
python -m src.main --max-jobs-per-search 10

# Sin respaldo Apify (si solo quieres el nÃºcleo local)
python -m src.main --no-apify

# Modo verbose (debug)
python -m src.main -v
```

## Salida

### `data/output/jobs.csv`
InspecciÃ³n rÃ¡pida. `skills` como string separado por `|`.

### `data/output/jobs.parquet`
Esquema tipado (con `skills` como `list<string>` nativo) para Databricks.

### Esquema (32 columnas)

| Campo | Tipo | Origen |
|---|---|---|
| `job_id` | string | SERP (`urn:li:jobPosting:XXX`) â€” clave de dedup |
| `job_url` | string | SERP (sin params tracking) |
| `title` | string | SERP |
| `company_name` | string | SERP |
| `location_raw` / `location_city` / `location_region` / `location_country` | string | SERP |
| `posted_datetime` | string (ISO) | SERP (`<time datetime>`) |
| `posted_relative` | string | SERP ("hace 23 horas") |
| `is_new` | bool | SERP (badge `--new`) |
| `company_url` / `company_industry` / `company_size` | string | Detalle |
| `work_mode` | string | Detalle (Remoto/HÃ­brido/Presencial) |
| `employment_type` | string | Detalle |
| `experience_level` | string | Detalle |
| `salary_raw` | string | Detalle |
| `salary_min` / `salary_max` | float | Detalle (parseado) |
| `salary_currency` / `salary_period` | string | Detalle |
| `num_applicants` | int | Detalle (requiere login) |
| `description_full` | string | Detalle |
| `role_summary` | string | Detalle (intro del puesto, previo a la 1Âª secciÃ³n) |
| `company_description` | string | Detalle (Sobre la empresa / Company Description) |
| `responsibilities` | string | Detalle (Funciones / Responsibilities / Â¿QuÃ© harÃ¡s?) |
| `requirements` | string | Detalle (Requisitos / Requirements / Â¿QuÃ© buscamos?) |
| `benefits` | string | Detalle (Beneficios / Benefits / Lo que ofrecemos) |
| `skills` | list[string] | Detalle |
| `search_role` / `search_city` | string | config |
| `source` | string | `"local"` \| `"apify"` |
| `scraped_at` | string (ISO UTC) | sistema |

### Ingest en Databricks Lakehouse

```sql
-- Opcion A: COPY INTO (batch)
CREATE TABLE IF NOT EXISTS bronze.linkedin_jobs (
  job_id STRING, job_url STRING, title STRING, company_name STRING,
  location_raw STRING, location_city STRING, location_region STRING,
  location_country STRING, posted_datetime STRING, posted_relative STRING,
  is_new BOOLEAN, company_url STRING, company_industry STRING,
  company_size STRING, work_mode STRING, employment_type STRING,
  experience_level STRING, salary_raw STRING, salary_min DOUBLE,
  salary_max DOUBLE, salary_currency STRING, salary_period STRING,
  num_applicants BIGINT, description_full STRING,
  role_summary STRING, company_description STRING,
  responsibilities STRING, requirements STRING, benefits STRING,
  skills ARRAY<STRING>,
  search_role STRING, search_city STRING, source STRING, scraped_at TIMESTAMP
) USING DELTA;

COPY INTO bronze.linkedin_jobs
FROM 'abfss://container@storage.dfs.core.windows.net/landing/jobs.parquet'
FILEFORMAT = PARQUET;

-- Opcion B: Autoloader (streaming incremental)
CREATE STREAMING TABLE bronze.linkedin_jobs_auto;
LOAD DATA INTO bronze.linkedin_jobs_auto
USING AUTOLOADER
LOCATION 'abfss://container@storage.dfs.core.windows.net/landing/'
FORMAT 'PARQUET';
```

## Estructura del proyecto

```
linkedin_jobs_scraper/
â”œâ”€â”€ .env / .env.example      # credenciales (NO commitear .env)
â”œâ”€â”€ .gitignore
â”œâ”€â”€ config.yaml              # roles, ciudades, geoIds, delays
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ README.md
â”œâ”€â”€ auth/
â”‚   â”œâ”€â”€ storage_state.json   # cookies de sesiÃ³n (auto, no commitear)
â”‚   â””â”€â”€ profile/             # perfil Chromium persistente (no commitear)
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ browser.py           # patchright + stealth + perfil persistente
â”‚   â”œâ”€â”€ login.py             # login + 2FA manual + guardado storage_state
â”‚   â”œâ”€â”€ human.py             # delays random + scroll lento + jitter
â”‚   â”œâ”€â”€ search.py            # URLs + navegaciÃ³n SERP + scroll
â”‚   â”œâ”€â”€ parse_serp.py        # tarjetas (selectores verificados HTML real)
â”‚   â”œâ”€â”€ parse_detail.py      # descripciÃ³n, skills, salario, empresa, segmentaciÃ³n
â”‚   â”œâ”€â”€ parse_sections.py     # segmentaciÃ³n heurÃ­stica bilingÃ¼e (ES/EN)
â”‚   â”œâ”€â”€ apify_fallback.py    # respaldo comercial
â”‚   â”œâ”€â”€ models.py            # JobOffer (dataclass) + esquema
â”‚   â”œâ”€â”€ store.py             # CSV + Parquet + checkpoint + dedup
â”‚   â””â”€â”€ main.py              # orquestador con conmutaciÃ³n
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ raw_html/            # snapshots HTML (depuraciÃ³n)
â”‚   â”œâ”€â”€ checkpoints/         # estado por (role,ciudad)
â”‚   â”œâ”€â”€ output/jobs.csv
â”‚   â””â”€â”€ output/jobs.parquet
â””â”€â”€ tests/
    â”œâ”€â”€ test_parse_serp.py
    â””â”€â”€ test_parse_sections.py
```

## Tests

```powershell
pytest tests/ -v
```

Los tests unitarios (`parse_location`, `clean_url`, `extract_job_id`,
`JobOffer.to_flat_dict`) no necesitan red ni navegador. Los tests de
segmentaciÃ³n (`split_description_sections`) cubren descripciones
bilingÃ¼es (ES/EN), HTML, texto plano y casos borde. El test con HTML
real usa el snapshot guardado en `data/raw_html/serp_madrid.html` (se
auto-copia desde el snapshot de la fase de investigaciÃ³n si estÃ¡
disponible).

## Estrategia anti-bloqueo (respetuosa, no invasiva)

| Medida | ImplementaciÃ³n |
|---|---|
| Stealth binario | `patchright` (parchea Chromium, no JS) |
| Perfil persistente | `launch_persistent_context` reutiliza cookies/huella |
| UA + locale + tz realistas | Chrome 131 desktop, `es-ES`, `Europe/Madrid` |
| `navigator.webdriver` oculto | `add_init_script` + flag patched |
| Delays humanos | `random.uniform` 1.5â€“20s segÃºn contexto |
| Scroll lento | 200â€“400px por paso, nunca al final instantÃ¡neo |
| Sin concurrencia | 1 pÃ¡gina, 1 oferta a la vez |
| Volumen bajo | ~75 ofertas/sesiÃ³n; configurable |
| Respeto a bloqueos | `BlockedException` â†’ para y conmuta a Apify |
| `robots.txt` | revisar `https://www.linkedin.com/robots.txt` antes |

## Lo que NO hace

- No evade CAPTCHAs ni los resuelve automÃ¡ticamente.
- No rota proxies propios.
- No scrapea perfiles personales ni datos privados.
- No commitea credenciales (`.env`, `auth/` en `.gitignore`).

## Troubleshooting

- **`LoginFailedError: Falta configurar LINKEDIN_EMAIL`**: copia
  `.env.example` a `.env` y rellena.
- **Aparece un 2FA**: el scraper se pausa y pide el cÃ³digo por consola.
  IntrodÃºcelo (solo la primera vez; luego se guarda `storage_state.json`).
- **`BlockedException`**: revisa `data/run.log`. Si tienes `APIFY_TOKEN`,
  esa combinaciÃ³n se reintentarÃ¡ vÃ­a Apify automÃ¡ticamente.
- **Selectores rotos**: LinkedIn cambia clases cada pocas semanas. Mira
  `data/raw_html/` y ajusta los selectores en `parse_serp.py` /
  `parse_detail.py`. Los tests te avisarÃ¡n si el HTML real cambia.
- **Cabeceras de secciÃ³n no detectadas (segmentaciÃ³n)**: LinkedIn no
  expone funciones/requisitos/beneficios como campos estructurados. El
  scraper los detecta heurÃ­sticamente mirando `<strong>`, `<h3>` y
  patrones de texto en la descripciÃ³n. Si una oferta tiene cabeceras
  atÃ­picas, su contenido irÃ¡ a `role_summary`. Para aÃ±adir nuevos
  patrones, edita `SECTION_HEADERS` en `src/parse_sections.py`.
- **`patchright` no instala Chromium**: prueba `python -m patchright install chromium`.
