---
name: indeed-structure
description: Referencia viva de la estructura del HTML/JSON de las paginas de resultados de Indeed (SERP). Usa cuando el scraper devuelva 0 ofertas, cuando Indeed cambie selectores, o cuando haya que anadir un nuevo campo a JobOffer. Cubre job cards, data-testids, window._initialData (dos arrays: GraphQL con descripcion completa y plano con metadatos), URLs /viewjob?jk=, paginacion start=10 y tokens anti-bot bb/xkcb.
---

# Estructura de Indeed (SERP) — referencia viva

Confirmado investigando `https://es.indeed.com/jobs?q=data&l=Madrid` (Indeed España, layout two-pane, SPA React con module federation `mosaic-*`).

## Job cards (16 por pagina)
Jerarquia de cada tarjeta:
```
li.css-1ac2h1w
  div.cardOutline.tapItem.dd-privacy-allow.result[id="job_<JK>"][class*=" job_<JK>"]
    div[data-testid="slider_container"]
      div[data-testid="slider_item"]
        div[data-testid="fade-in-wrapper"]
          div.job_seen_beacon
            table.mainContentTable > td.resultContent  (campos)
            div.ctaContainer                              (boton guardar)
```

- **Job key (JK):** hex de 16. En `id="job_<JK>"` y en `data-jk` del `<a>` del titulo.
- **Card activa/abierta:** anade clase `vjs-highlight`.

## data-testid relevantes (recuento sobre 16 cards)
| data-testid | Uso |
|---|---|
| `slider_container` | contenedor superior |
| `slider_item` / `slider_sub_item` | capas interior / inferior |
| `fade-in-wrapper` | wrapper de animacion |
| `company-name` | nombre de empresa |
| `text-location` | ubicacion |
| `belowJobSnippet` | descripcion corta (bullets) |
| `more_links` | enlaces relacionados (sueldos, faq) |
| `attribute_snippet_testid` | atributo generico (jornada, beneficios) |
| `attribute_snippet_testid salary-snippet-container` | salario (solo ~3/16) |
| `brandingLogo` | logo de empresa |
| `timing-attribute` | contenedor empresa+ubicacion (NO fecha) |
| `pagination-page-current/next/2/3/4/5` | paginacion |

## Selectores CSS por campo (fallback cuando el JSON falla)
| Campo | Selector |
|---|---|
| Job key | `div[id^="job_"]` (atributo id) o `a[id^="job_"]` (atributo `data-jk`) |
| Titulo | `h2.jobTitle span[id^="jobTitle-"]` (atributo `title`) o `h2.jobTitle a.jcs-JobTitle` |
| Empresa | `span[data-testid="company-name"]` |
| Ubicacion | `div[data-testid="text-location"]` |
| Salario | `li[data-testid*="salary-snippet-container"] span` |
| Snippet | `div[data-testid="belowJobSnippet"] ul li` |
| Jornada/beneficios | `li[data-testid="attribute_snippet_testid"] span` |
| Logo | `div[data-testid="brandingLogo"] img.brandingLogo-image` (atributo src) |
| Guardar | `button.bookmark[aria-label="Save job Toggle"]` |

> Nota: el titulo puede ser `h2.jobTitle` o `h3.jobTitle` segun release. Probar ambos.

## JSON embebido (FUENTE PRINCIPAL, mas fiable que HTML)
### IMPORTANTE (confirmado en produccion 2026-06): Indeed BORRA window._initialData y window.mosaic tras hidratar (anti-scraping). No usar page.evaluate para leerlos. En su lugar, leer page.content() y extraer el <script id="mosaic-data"> con regex (los scripts siguen en el DOM aunque las variables globales se borren).

Tag: `<script id="mosaic-data" type="text/javascript">` (Sigue en el DOM tras la carga).

**Ruta exacta del array de ofertas (confirmada):**
```
window.mosaic.providerData["mosaic-provider-jobcards"]
  -> metaData
  -> mosaicProviderJobCardsModel
  -> results[]   (15-16 items, array plano)
```

**Extraccion (implementada en scraper/parser.py):**
1. `html = page.content()`
2. regex `<script id="mosaic-data"[^>]*>(.*?)</script>` (DOTALL)
3. dentro, buscar `window.mosaic.providerData["mosaic-provider-jobcards"] = ` y **balancear llaves** (manejando strings con escapes para no romper con `}` literales en descripciones)
4. `json.loads` del substring
5. navegar `obj["metaData"]["mosaicProviderJobCardsModel"]["results"]`

### Campos del array plano (item keys, confirmado en produccion)
| Campo | Notas |
|---|---|
| `jobkey` (**minuscula**) | job key de 16 hex. Ojo: NO es `jobKey` camelCase. Fallback: `mouseDownHandlerOption.jobKey` o regex `jk=([a-f0-9]+)` en `link`. |
| `displayTitle` | titulo (alternativo: `title`) |
| `company` | nombre empresa |
| `companyIdEncrypted` | = fccid |
| `companyRating` | float o None |
| `companyReviewCount` | int o None |
| `companyOverviewLink` | /cmp/... |
| `formattedLocation` | texto completo ubicacion |
| `jobLocationCity` / `jobLocationState` | ciudad / provincia |
| `extractedSalary` | {min, max, type} (solo ~3/15) |
| `salarySnippet` | {text, currency, source} — `text` es el salario legible ("23.000 € al año") |
| `formattedRelativeTime` | "hace 30+ días" |
| `createDate` / `pubDate` | epoch ms |
| `sponsored` | bool |
| `snippet` | HTML con bullets (limpiar tags) |
| `remoteWorkModel` | {text: "Trabajo híbrido", type: "REMOTE_HYBRID"} -> is_remote |
| `viewJobLink` | /viewjob?jk=... (URL canonica, usar esta) |
| `link` | /rc/clk?jk=...&bb=...&xkcb=... (EVITAR, tokens expiran) |
| `translatedAttributes` | [{text: "Jornada completa"}] -> job_type (a menudo vacio) |
| `thirdPartyApplyUrl` | URL externa de aplicacion (a veces) |

### Array GraphQL (JobDataResult, con descripcion completa) — YA NO ACCESIBLE facilmente
`hostQueryExecutionResult.data.jobData.results[]` con `job.description.html` y `job.url` existe en el HTML, PERO dentro de un IIFE webpack anónimo de ~414KB (script sin id), dificil de extraer con regex. El array plano NO incluye descripcion completa. Para obtenerla habria que entrar a /viewjob?jk=... de cada oferta (fuera del prototipo "solo listado").

### Estrategia de parseo (implementada en scraper/parser.py)
1. `page.content()` -> extraer `<script id="mosaic-data">` -> `providerData["mosaic-provider-jobcards"]["metaData"]["mosaicProviderJobCardsModel"]["results"]` (array plano).
2. Mapear cada item a JobOffer (campos arriba).
3. Fallback HTML solo si el JSON no esta (selectores corregidos: `div.cardOutline` + `[data-jk]`, ya NO `div[id^="job_"]`).

## Paginacion
- URL: `&start=0` (pag 1), `&start=10` (pag 2), `&start=20` (pag 3)... incrementos de 10.
- Enlace siguiente: `a[data-testid="pagination-page-next"][aria-label="Página siguiente"]`.
- Para scraping: navegar directo a la URL con `start=` (no clickar el enlace).

### IMPORTANTE (confirmado en produccion 2026-06): Indeed BLOQUEA pagina 2+ con login
- `start=10` (pagina 2) redirige a `https://secure.indeed.com/auth` (login): "Para ver más de una página de empleos, crea una cuenta o inicia sesión".
- Title de la redireccion: "Iniciar sesión | Cuentas Indeed".
- El scraper lo detecta (`runner._is_login_redirect`) y omite la pagina con aviso.
- **Solucion implementada**: `python main.py --login` (login asistido: abre el navegador, el usuario se loguea a mano resolviendo el CAPTCHA humano, el scraper guarda la sesion y pagina sin limite).

### Parametro limit=50 NO sirve (confirmado en produccion 2026-06)
- `&limit=25` y `&limit=50` disparan Cloudflare ("Security Check - Indeed.com", "Additional Verification Required", Ray ID). No usar sin proxy/login.

## URLs de oferta
- **Usar siempre** `/viewjob?jk=<JK>` (canonica, estable).
- **Evitar** `/rc/clk?jk=...&bb=<BLOB>&xkcb=<TOKEN>&fccid=...&vjs=3` (orgánicas): los blobs `bb` y `xkcb` expiran y son anti-bot por impresion.

## Tokens anti-bot en enlaces (no usar, solo informativo)
| Token | Donde |
|---|---|
| `xkcb` | param en /rc/clk (= `blobKey` en JSON plano) |
| `bb` | param en /rc/clk (blob cifrado, cambia por impresion) |
| `fccid` | param en /rc/clk (= `companyIdEncrypted`) |
| `tk` / `logTk` / `ctk` | tokens de sesion en `window.mosaic.initialData` |
| `data-mobtk` | token movil en cada `<a>` de tarjeta |
| `indeedcsrftoken` | en `mosaic-provider-reportcontent` |

## Otros scripts embebidos (menos utiles para scraping)
- `id="mosaic-data"`: `window.mosaic.initialData` (metadata SERP: country, ctk, logTk, language).
- `id="mosaic-init-data"`: `window.mosaic.providers` (mapa de modulos React).
- `id="_indeed_gnav_config"` (application/json): config global nav.
- `autoOpenTwoPaneViewjobResponse.body`: detalle de la oferta auto-abierta en el panel derecho.

## Anti-bot detectado
- **Cloudflare JSD pasivo**: `/cdn-cgi/challenge-platform/scripts/jsd/main.js`, `window.__CF$cv$params`, iframe 1x1 oculto.
- **No detectado** en captura limpia: DataDome, reCAPTCHA, hCaptcha, Arkose, PerimeterX. Aparecen con volumen/frecuencia altos.
- Mitigacion implicita: enlaces orgánicos via `/rc/clk` con blobs que expiran.

## robots.txt (aviso legal)
Indeed bloquea `/*&start=` y `/viewjob?` para bots genéricos (solo Google/Bing/etc. permitidos). El scraping tecnico viola sus TOS; el scraper de este proyecto se disena respetuoso (bajo volumen, delays largos) para uso personal. Para uso profesional: API oficial de Indeed (publisher program, hoy cerrado a nuevos) o partners.

## Como actualizar este skill
Si el scraper devuelve 0 ofertas y la pagina carga:
1. `webfetch` a `https://es.indeed.com/jobs?q=data&l=Madrid` (format html).
2. Buscar nuevos `data-testid` y la estructura del JSON embebido.
3. Actualizar este archivo y `scraper/parser.py`.

## Output (esquema tipado para Databricks/pandas, 2026-06)

El scraper genera 7 archivos por ejecucion en `output/`:

| Archivo | Uso |
|---|---|
| `indeed_jobs_<ts>.parquet` | Principal: Parquet snappy, 28 cols, esquema tipado (Int64, Float64, boolean, string, datetime64[ns,UTC]). Leer con `pd.read_parquet()` o `spark.read.parquet()`. |
| `indeed_jobs_<ts>.delta/` | Delta Lake (`_delta_log/`). Nativo Databricks ACID. Leer con `spark.read.format("delta")`. |
| `indeed_jobs_<ts>.jsonl` | JSON Lines (1 objeto/linea). Cargar con `pd.read_json(lines=True)` o `spark.read.json()`. |
| `indeed_jobs_<ts>.xlsx` | Excel: **1 hoja 'jobs'**, sin `description_html`, freeze panes A2, bold header, formato fecha. Inspeccion humana. |
| `indeed_jobs_<ts>.csv` | CSV UTF-8 BOM. Respaldo universal. |
| `scrape_metadata_<ts>.json` | Metadata del run: run_id (UUID), duration, conteos por ciudad/pagina, term/country/cities. |
| `session_state.json` | Cookies de sesion. Reutilizar entre ejecuciones. |

El esquema tipado se define en `scraper/schema.py` (`JOB_OFFER_DTYPES`) y se aplica via `scraper/exporters.py` antes de cada export. Garantiza estabilidad entre runs.

### Como funciona la export
1. `runner.py._export()` recibe la lista de `JobOffer` post-dedup.
2. Convierte cada oferta a `to_typed_dict()` (con datetime nativos).
3. Pasa por `schema.parse_typed_df()` (astype + reordenar columnas + parsear fechas).
4. Llama a `exporters.export_all(df, meta, output_dir, ts)` que escribe los 7 archivos. Resiliente: si un formato falla (ej. Delta sin deltalake), loguea warning y continua con los demas.
5. `exporters.build_metadata()` construye el JSON de metadatos con run_id, conteos, duracion.
