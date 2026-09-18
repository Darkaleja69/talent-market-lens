# Indeed Jobs Scraper

Scraper de ofertas de empleo de Indeed optimizado para evitar CAPTCHAs. DiseÃ±ado para EspaÃ±a (Madrid, Barcelona, Bilbao) y preparado para Irlanda, Suiza y PaÃ­ses Bajos.

## Stack
- **Python 3.11** + **patchright** (Playwright con stealth integrado, mejor que `playwright-stealth` que estÃ¡ estancado).
- **pandas + pyarrow + openpyxl** para export tipado a Parquet, Delta Lake, JSONL, XLSX, CSV.
- **fake-useragent** para User-Agents reales.

**Formato principal para Databricks/pandas: Parquet** (tipado, esquema embebido, nativo Spark).
TambiÃ©n se genera **Delta Lake** (`_delta_log/`, nativo Databricks ACID).

## Por quÃ© este stack
Indeed es una SPA React (module federation `mosaic-*`); `requests`/`httpx` no sirven porque las job cards se renderizan con JS. patchright lanza un Chrome real **headed** (no headless, que dispara Cloudflare), parchea `navigator.webdriver` y fingerprints, y permite scrolling fino y delays humanos.

## CÃ³mo evita CAPTCHAs (sin proxy, sin coste)
1. **Headed + stealth**: Chrome real, `navigator.webdriver` borrado, UA/headers coherentes.
2. **SesiÃ³n persistente**: guarda cookies en `output/session_state.json` y las reutiliza para parecer usuario recurrente.
3. **Scrolling muy poco a poco**: pasos de 200-400px cada 0.8-1.5s con jitter y pausas largas cada 5-9 pasos.
4. **Delays aleatorios amplios**: 5-12s pre-navegaciÃ³n, 8-15s entre pÃ¡ginas, 60-90s entre ciudades.
5. **Rate limit autoimpuesto**: mÃ¡x 6 SERPs por ejecuciÃ³n (3 ciudades Ã— 2 pÃ¡ginas).
6. **DetecciÃ³n + aviso**: si salta un challenge (Cloudflare, DataDome, PerimeterX, Arkose), screenshot + log + aborta limpio. No resuelve automÃ¡ticamente (polÃ­tica: sin coste).
7. **URLs canÃ³nicas**: usa `/viewjob?jk=<JK>` en vez de `/rc/clk` (cuyos tokens `bb`/`xkcb` expiran).

## InstalaciÃ³n
```powershell
cd <ruta-proyecto>\indeed_jobs_scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
patchright install chromium
```

> `patchright install chromium` descarga el navegador. Si tienes Chrome estable instalado, patchright lo usarÃ¡ automÃ¡ticamente (`channel="chrome"`).

## Uso
### Prototipo (EspaÃ±a, Madrid/Barcelona/Bilbao, 2 pÃ¡ginas, "data")
```powershell
python main.py
```

### Variante
```powershell
python main.py --country ES --cities Madrid,Barcelona --term "data engineer" --pages 2
python main.py --country IE --cities Dublin --term "data" --pages 2
python main.py --country CH --cities Zurich,Geneva --term "data" --pages 2
python main.py --country NL --cities Amsterdam --term "data" --pages 2
```

Flags:
- `--country` (ES, IE, CH, NL). Default: ES.
- `--cities` (lista separada por comas). Default: las del paÃ­s.
- `--term` (tÃ©rmino de bÃºsqueda). Default: `"data"`.
- `--pages` (pÃ¡ginas por ciudad). Default: 2.
- `--fromage` (antigÃ¼edad mÃ¡xima en dÃ­as). `--terms` (varios tÃ©rminos separados por comas).
- `--enrich-rate` (fracciÃ³n 0.0-1.0 a enriquecer) y `--enrich-max` (tope de clics por SERP).
- `--output` (directorio de salida). Default: `./output`.

## Modo auto-servicio (recomendado): tÃº inicias sesiÃ³n, el scraper solo lee

DiseÃ±ado para mÃ­nimo volumen, mÃ¡xima fiabilidad y cero coste. El scraper NO
lanza Chrome ni toca fingerprints: se conecta a un Chrome real que TÃš has
abierto y en el que TÃš has iniciado sesiÃ³n.

1. Abre Chrome con CDP y perfil dedicado:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start_chrome_cdp.ps1
   ```

2. En la ventana que se abra: entra en `https://es.indeed.com/`, inicia sesiÃ³n
   y resuelve el CAPTCHA a mano. Navega un par de minutos y **deja Chrome abierto**.

3. Ejecuta la pasada de calidad. Por defecto el wrapper recorre **PaÃ­ses Bajos
   (Amsterdam, Rotterdam)** e **Irlanda (Dublin, Cork)** con 4 tÃ©rminos
   (`data, data engineer, data analyst, data scientist`), 2 pÃ¡ginas, ~90% de
   ofertas con detalle completo y perfil lento/seguro:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\run_nightly_indeed.ps1
   ```

   o directamente (un solo paÃ­s):

   ```powershell
   python main.py --country NL --cdp 9222 --terms "data,data engineer,data analyst,data scientist" --cities Amsterdam,Rotterdam --pages 2 --fromage 7 --enrich-rate 0.9 --enrich-max 15
   ```

   `--enrich-max` limita los clics de enriquecimiento por SERP (mÃ¡s bajo = mÃ¡s
   seguro). Las ofertas ya vistas se rellenan sin clics desde la cachÃ© persistente
   `output/descriptions_cache.json`, asÃ­ que la cobertura sube entre ejecuciones.
   El wrapper incluye un **watchdog**: si el scraper no muestra actividad en
   20 min, mata el proceso y reintenta (evita runs colgadas).

4. Los resultados salen en `output/indeed_jobs_<ts>.*` (parquet, jsonl, csv, xlsx...).

Reglas de uso para que Indeed no te bloquee:

- **Sin VPN ni proxy** en la conexiÃ³n (Indeed los detecta).
- **Sin extensiones** en el perfil de Chrome (el script las desactiva).
- MÃ¡ximo 4 SERPs y ~30 ofertas por noche.
- `fromage=7` para quedarte solo con ofertas recientes.
- Si aparece un challenge (exit code 3), **no reintentes esa noche**: espera 24h.
- El enriquecimiento (clics en cada oferta + `/viewjob`) es la parte mÃ¡s delicada;
  por eso la pasada nocturna es pequeÃ±a y con pausas (`--pause-every 2`).

## Output por ejecuciÃ³n (esquema tipado, sin acumular)

Cada ejecuciÃ³n genera en `output/`, con timestamp `YYYYMMDD_HHMM`:

| Archivo | Formato | Columnas | Para quÃ© |
|---|---|---|---|
| `indeed_jobs_<ts>.parquet` | Parquet snappy | 28 cols, esquema tipado | **Principal para Databricks y pandas** |
| `indeed_jobs_<ts>.delta/` | Delta Lake | 28 cols, con `_delta_log/` | Databricks nativo (ACID, time travel) |
| `indeed_jobs_<ts>.jsonl` | JSON Lines | 28 cols, 1 objeto/lÃ­nea | `spark.read.json`, `pd.read_json(lines=True)` |
| `indeed_jobs_<ts>.json` | JSON anidado | 28 cols, paÃ­sâ†’ciudad | Lectura humana / backup |
| `indeed_jobs_<ts>.xlsx` | Excel 1 hoja `jobs` | 27 cols (sin `description_html`) | InspecciÃ³n humana (freeze header A2, bold) |
| `indeed_jobs_<ts>.csv` | CSV UTF-8 BOM | 28 cols | Respaldo universal |
| `scrape_metadata_<ts>.json` | JSON | run_id, ts, conteos, etc. | AuditorÃ­a / tabla metadatos en Databricks |
| `session_state.json` | JSON | Cookies de sesiÃ³n | Reutilizar sesiÃ³n entre ejecuciones |
| `log_<ts>.log` | Texto | Log completo | DiagnÃ³stico |

### Esquema tipado (dtypes pandas/Arrow)

```
string[python]:  job_key, title, company, viewjob_url, apply_url, location,
                 city, state, country, salary_text, salary_type, job_type,
                 snippet, description_html, posted_relative,
                 company_overview_link, company_id_encrypted,
                 search_term, city_query
Int64:           company_review_count, salary_min, salary_max, page
Float64:         company_rating
boolean:         is_remote, is_sponsored
datetime64[ns,UTC]: posted_date, scraped_at
```

Los dtypes se aplican explÃ­citamente desde `scraper/schema.py` con `df.astype(...)` + `pd.to_datetime(utc=True)`, garantizando un esquema **estable entre ejecuciones** (sin que pandas infiera `float64` donde deberÃ­a ser `str`).

El XLSX excluye `description_html` (puede tener varios KB y hace el Excel pesado/ilegible) pero se mantiene en Parquet, Delta, JSONL y CSV.

### Ejemplos de carga downstream

```python
# pandas â€” tipos conservados automÃ¡ticamente
import pandas as pd
df = pd.read_parquet("output/indeed_jobs_20260621_1442.parquet")
# posted_date: datetime64[ns, UTC]
# is_remote: boolean
# salary_min: Int64 (nullable)

# Databricks / Spark desde Parquet
df = spark.read.parquet("dbfs:/mnt/indeed/indeed_jobs_20260621_1442.parquet")
df.printSchema()  # esquema inferido desde el parquet

# Databricks / Spark desde Delta (nativo, recomendado)
df = spark.read.format("delta").load("dbfs:/mnt/indeed/indeed_jobs_20260621_1442.delta")

# JSONL alternativo
df = spark.read.json("dbfs:/mnt/indeed/indeed_jobs_20260621_1442.jsonl")

# Metadatos del run
meta = spark.read.json("dbfs:/mnt/indeed/scrape_metadata_20260621_1442.json")
```

## Si salta un CAPTCHA
El scraper para (exit code 3), guarda lo conseguido y te avisa por stderr. El
wrapper nocturno **no reintenta** en ese caso. Opciones:
1. **Esperar 24h** (la IP estÃ¡ marcada; reintentar solo la empeora).
2. **Cambiar de red** (mÃ³vil/hotspot) y reintentar al dÃ­a siguiente.
3. **Revisar el diagnÃ³stico**: `output/diag_*.png` + `output/diag_*.html` + `output/log_*.log`.
4. Para volumen alto: aÃ±adir proxies residenciales o una API gestionada (ver skill `indeed-anti-bot`).

## Subagentes y skills de opencode
El proyecto incluye configuraciÃ³n de opencode en `.opencode/`:
- **Agente `indeed-scraper`**: subagente experto para ejecutar, depurar y mantener el scraper.
- **Skill `indeed-structure`**: referencia viva de selectores, `data-testid` y `window._initialData`.
- **Skill `indeed-anti-bot`**: recetario anti-CAPTCHA especÃ­fico de Indeed.

Para que carguen, abre opencode **desde la carpeta del proyecto** y reinicia si ya estaba abierto.

## Aviso legal
El `robots.txt` de Indeed bloquea `&start=` y `/viewjob?` para bots genÃ©ricos. TÃ©cnicamente el scraping viola sus TOS. Este scraper se diseÃ±Ã³ respetuoso (bajo volumen, delays largos, sin sobrecargar) para uso personal. Para uso profesional, usa la API oficial de Indeed (publisher program, hoy cerrado a nuevos) o partners autorizados. La responsabilidad del uso es tuya.

## Estructura
```
indeed_jobs_scraper/
â”œâ”€â”€ .opencode/
â”‚   â”œâ”€â”€ agents/indeed-scraper.md
â”‚   â”œâ”€â”€ skills/indeed-structure/SKILL.md
â”‚   â”œâ”€â”€ skills/indeed-anti-bot/SKILL.md
â”‚   â””â”€â”€ opencode.json
â”œâ”€â”€ scraper/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ config.py      paÃ­ses, ciudades, dominios, constantes anti-bot
â”‚   â”œâ”€â”€ models.py      dataclass JobOffer
â”‚   â”œâ”€â”€ anti_bot.py    delays, human_scroll, detect_captcha
â”‚   â”œâ”€â”€ browser.py     patchright headed + stealth + sesiÃ³n persistente
â”‚   â”œâ”€â”€ parser.py      extrae window._initialData + fallback HTML
â”‚   â””â”€â”€ runner.py      orquesta paÃ­sâ†’ciudadâ†’N pÃ¡ginas, dedup, export
â”œâ”€â”€ output/            JSON, CSV, XLSX, sesiÃ³n, logs, screenshots
â”œâ”€â”€ main.py            CLI
â”œâ”€â”€ requirements.txt
â””â”€â”€ README.md
```
