# Informe de investigación — Indeed (repairs/20261018-indeed)

- **Fecha:** 2026-10-18 (17:00–17:35 local aprox.).
- **Objetivo:** reparar `description` (87.9 % → 100 % del alcance) y auditar `salary`, `work_mode` y `skills`.
- **Run de origen:** IE 2026-10-18 02:19–04:09 (Dublin+Cork × 4 términos × 2 páginas): 116 únicas, `description_filled 102/116` (87.9 %), `enrich 51/52`, salary 36.2 %, work_mode 16.4 %, skills 0 %.
- **Herramientas:** lectura offline de parquet/logs; `agent-browser 0.38.1` (sesión propia; 1 SERP IE + 2 clics, sin CAPTCHA); fetch de `robots.txt` (IE/NL) y TOS. **No** se usó el Chrome del usuario (el puerto 9222 estaba caído; no se cerró ni arrancó ningún Chrome). No se ejecutó el scraper ni la prueba en vivo.
- **Evidencias:** `repairs/20261018-indeed/evidence/` (detalle en §9). Capturas en `.opencode/.agent-screenshots/`.

---

## 1. Hechos observados

### 1.1 Aritmética de las 14 descripciones perdidas (respuesta a la pregunta 1)

El parquet del run tiene 116 filas y **102** con `description_text` no vacío (misma métrica que la puerta: alias `description_text`; no hay filas con HTML > 50 y texto vacío). Faltan 14, y su causa queda cuadrada al detalle con el log:

- **`--enrich` solo intenta "candidatas"**: ofertas sin descripción tras aplicar el `desc-cache` (650 entradas al arrancar) + la descripción embebida del auto-open (1/SERP). En este run hubo **65 candidatas** repartidas en 16 SERPs (suma de "candidatas sin descripcion" de los logs).
- **El sampler `rate=0.9` saltó 13**: en `parser.py` → `sample_size = max(1, int(len(candidates) * 0.9))`; con `int()` truncando y el mínimo forzado, casi todo SERP pequeño pierde 1 candidata (`2→1`, `3→2`, `4→3`, `7→6`, `9→8`, `13→11`…). Suma de saltadas: **13**.
- **1 clic falló**: `03:05:01 WARNING enriquecimiento fallido jk=3a8c91f14177f18a: Locator.click: Timeout 5000ms exceeded` (Solutions Architect, DAA; data engineer/Cork p2). Los 51 restantes sí obtuvieron descripción.
- **`--enrich-max=15` NO limitó** (la SERP con más candidatas tenía 13).
- **Cuadre exacto:** 116 − 102 = **14 = 13 saltadas por muestreo + 1 fallo de clic**. El `enrich_attempted=52` del metadata es solo la muestra intentada (65 candidatas − 13 saltadas = 52); `enrich_success_rate 51/52` (98 %) es engañoso porque no cuenta las saltadas.
- **Agravante (recuperación perdida):** 4 de las 14 (`1a1c22fd…`, `901dd88d…`, `1d6a7c41…`, `03c78841…`) sí tienen entrada en el `descriptions_cache.json` con `cached_at` **dentro del run** (00:51:11Z, 00:36:13Z, 01:36:23Z, 01:59:43Z): una copia duplicada en un SERP posterior fue enriquecida, pero `_dedup` conserva el **primer** objeto por `job_key` sin fusionar campos, así que el parquet exportó el objeto pobre. (El cache sí las guardó para el run siguiente.)
- Mapeo 1:1 de las 14 a su SERP de primera aparición, con causa y detalle: `evidence/indeed-20261018-enrich-arithmetic.txt` y `evidence/indeed-20261018-missing-descriptions.csv`.

Composición por SERP (candidatas/intentadas/saltadas/fallo): `1/1/0/0`, `13/11/2/0`, `2/1/1/0`, `1/1/0/0`, `9/8/1/0`, `7/6/1/0`, `4/3/1/0`, `7/6/1/1`, `4/3/1/0`, `7/6/1/0`, `3/2/1/0`, `2/1/1/0`, `2/1/1/0`, `1/1/0/0`, `2/1/1/0`, `0/0/0/0` → **65/52/13/1**.

**Respuesta directa:** sí, la política `--enrich-rate 0.9` es la causa principal (13 de 14); `--enrich-max` no actuó; 13 ofertas no llegaron a intentarse nunca; el fallo 51/52 y las no intentadas explican exactamente el 102/116. No hubo ninguna otra causa (ni panel roto ni descripciones ausentes).

### 1.2 La web real hoy (respuesta a la pregunta 3, parte de hechos)

SERP IE `q=data&l=Dublin&radius=25&fromage=7`, sin challenge, 15 resultados + 16 tarjetas DOM:

- **Descripción disponible**: (a) auto-open embebido `body.jobInfoWrapperModel.jobInfoModel.sanitizedJobDescription` (4.336 chars, ruta nueva del parser, la antigua `hostQueryExecutionResult` no existe); (b) panel tras clic: `[data-testid=viewjob-job-content]` / `vj-job-title` / `structured-job-summary` presentes, los selectores viejos (`#jobDescriptionText`) a 0. El panel ya mostraba el job nuevo a los ~3 s del clic (cadena de esperas actual: 4 s + 8 s; margen de sobra). El contenido llega por un `POST apis.indeed.com/graphql` (`operationName: Viewjob`) que **dispara la propia página**; no se observó ningún GET directo a `/viewjob`.
- **salary**: las tarjetas publican `salarySnippet.text` + `extractedSalary` en 6/15 (40 %); donde no existe, el panel muestra literalmente **"Pay information not provided"** → no hay salario oculto que extraer. El scraper ya lee esos dos campos del JSON (cobertura = exactamente lo publicado).
- **work_mode**: `remoteWorkModel{type,text}` presente en 3/15 (`type="REMOTE_HYBRID"`, `text="Hybrid work"`); las ofertas onsite no traen marca. El panel, para las clicadas, muestra `"Hybrid work in …"`/`"Remote in …"` en el resumen.
- **skills**: **no existe campo ni clave** en las tarjetas (`mosaic-provider-jobcards`), en el auto-open, en la query `Viewjob` del panel ni en el DOM del panel. Solo hay "skills" en un fragmento de analíticas de candidatos (sesión iniciada, no aplica). Conclusión: no es extraíble como campo; se deriva de la descripción en Databricks (como fija la spec).
- **Dos bugs de extracción confirmados por código + datos**:
  1. `parser.py` `_map_item_to_offer`: `if "REMOTE" in rtype` **antes** de `"HYBRID"` → `REMOTE_HYBRID` se mapea a `"remote"` (debería ser `hybrid`).
  2. `translatedAttributes` viene **vacío en 15/15**; `job_type` quedó 0/116. `jobTypes[]` sí trae datos (`["Contract"]`, `["Permanent"]`, `["Full-time","Permanent"]`).
- **Panel = fuente adicional**: el resumen trae salario, tipo ("Fixed term"/"Full-time") y modalidad; el bloque de beneficios ("Employee discount, …") que hoy se pierde casi entero por la lista de palabras clave.
- El perfil fresco de agent-browser tiene banner OneTrust que cubría las tarjetas (un primer clic falló por "element covered"); aceptado y continuó. En el perfil persistente del nightly no bloqueó (51/52 clics OK), pero conviene cubrirlo con reintento.

### 1.3 Legal (respuesta a la pregunta 4)

- `robots.txt` IE/NL (2026-10-18): SERP y `&start=0..90` permitidas; `Disallow: /viewjob`, `/m/viewjob`, `/graphql` para `*`. Extractos en `evidence/indeed-20261018-robots-tos-relevant.txt`.
- TOS (actualizados 2026-09-29): prohíben el acceso automatizado "sin permiso expreso", pero **conceden permiso condicional "solely as outlined in our robots.txt file"** → la frontera válida es robots.txt.
- Se mantiene la decisión vigente: descripción por la **vía de UI** (clic en tarjeta y lectura del panel); el interruptor `allow_viewjob_fallback` sigue en `False`; ninguna llamada directa a `/viewjob` ni `/graphql`. **No fue necesario parar nada.**
- Hallazgo nuevo (preexistente, decisión de la persona): con `radius=25`, la página 1 no coincide con `Allow: /*&start=0&` y, según RFC 9309 (matching con query), cae bajo `Disallow: /*radius=`; la página 2 sí queda cubierta por `Allow: /*&start=10&` (match más específico). Opción mínima: añadir `&start=0` en página 1 (o revisar `radius`).

## 2. Hipótesis y estado

| # | Hipótesis | Estado | Fundamento |
|---|---|---|---|
| H1 | El muestreo `rate=0.9` deja candidatas sin intentar (13) | **Confirmada** | Suma 13 saltadas = 13 de las 14 perdidas, mapeo 1:1 por SERP |
| H2 | 1 clic falló por timeout y no se reintenta | **Confirmada** | WARNING con `jk=3a8c91f14177f18a`; 51/52 |
| H3 | `_dedup` pierde descripciones de duplicados enriquecidos después | **Confirmada** | 4 claves con `cached_at` intra-run posterior a su primera aparición; el parquet conserva el primer objeto |
| H4 | El panel/selectores están rotos (como antes del T-32) | **Refutada** | Panel en vivo con `vj-*`, actualización ~3 s; 51/52 clics OK en el run |
| H5 | Hay descripciones genuinamente ausentes en el portal | **Refutada** | Todas las ofertas observadas tienen descripción en panel/auto-open; "Pay information not provided" aplica al salario, no a la descripción |
| H6 | `enrich_success_rate` oculta el problema (mide solo la muestra) | **Confirmada (diseño)** | 51/52 = 98 % frente a `description_filled` 87.9 % |
| H7 | `REMOTE_HYBRID` mal mapeado a `remote` | **Confirmada** | Código (orden de `in`) + `REMOTE_HYBRID` en vivo |
| H8 | skills no es un campo publicado; solo derivable de la descripción | **Confirmada** | Item keys, body auto-open, query `Viewjob`, DOM del panel |

## 3. Causa raíz (respuesta a la pregunta 2)

**Combinación, con este peso:**

1. **(a) Límite de muestreo — causa principal (13/14).** `--enrich-rate 0.9` + `int()` trunca: con candidatas pequeñas por SERP (2–13 tras cache+auto-open) siempre se saltan 1–2 por SERP; suma 13.
2. **(b) Fallo de extracción puntual (1/14).** Un `Locator.click` de 5 s expiró (posible overlay/estabilidad de la tarjeta) y la oferta se quedó sin descripción; no hay reintento.
3. **(c) Descripciones ausentes en el portal: NO.** La descripción completa existe para todas las observadas (panel + auto-open).
4. **(Agravante d) `_dedup` sin fusión de campos.** El parquet conserva el primer objeto por `job_key`; las 4 ofertas que sí se enriquecieron en un duplicado posterior no llegan al export (sí al cache).

El panel, los selectores y el auto-open funcionan; el `enrich_success_rate` del metadata no es la métrica del problema.

## 4. Auditoría de campos

| campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha | extracción recomendada | base legal |
|---|---|---|---|---|---|
| `description` | Sí: (a) auto-open embebido `sanitizedJobDescription` (1/SERP); (b) panel tras clic (`viewjob-job-content`); (c) cache | Parcial: 102/116 | **14** (13 saltadas + 1 clic) + 4 no fusionadas | Intentar **todas** las candidatas (`rate=1.0`, tope `max_enrich` como único límite), reintentar 1 vez el clic, fusionar duplicados en `_dedup` | SERP permitida; panel = UI de la página (decisión oct-2026); `/viewjob` directo **no** |
| `salary` | Solo cuando el empleador lo publica: `salarySnippet.text`/`extractedSalary` (6/15 hoy; 36 % IE / 67 % NL en los runs); si no, "Pay information not provided" | Sí, para todo lo publicado (42/116) | **No hay brecha técnica**: el 100 % no depende del scraper | Mantener mapeo actual; medir la tasa publicada; no prometer 100 % | SERP permitida |
| `work_mode` | Parcial: `remoteWorkModel.type/text` para remotas/híbridas (3/15 hoy); panel (`structured-job-summary`: "Hybrid work in …"/"Remote in …") para las clicadas; onsite no se publica | Parcial (19/116) con **bug** `REMOTE_HYBRID→remote` | cobertura real (portal) + corrección de mapeo | Comprobar `HYBRID` antes que `REMOTE`; mapear `REMOTE_HYBRID→hybrid`, `REMOTE_WORK_FROM_HOME/REMOTE→remote`; usar `rwm.text` y el texto del panel; `_looks_remote` solo como último recurso | SERP + panel (UI) |
| `skills` | **No**: no existe campo ni clave en tarjetas, auto-open, query del panel ni DOM | No (no hay columna fuente) | — | No extraer; derivar de `description` en Databricks (spec) | — |

Extra (fuera de foco, barato): `jobTypes[]` de las tarjetas → `job_type`/`contract_type` (hoy 0/116 por `translatedAttributes` vacío); palabras clave del panel ("Fixed term", bloque de beneficios) en `_map_conditions_to_offer`.

## 5. Meta de calidad propuesta (pregunta 5)

Alcance de prueba: **1 país × 1 término × 1 página** (una SERP; ≤ 15 resultados ⇒ ≤ 15 candidatas ⇒ el tope `max_enrich=15` no puede morder).

| campo | meta propuesta | razón |
|---|---|---|
| `description` | **100 % en el alcance** (prioritaria) | Con `rate=1.0` + reintento de clic + fusión de duplicados, todas las candidatas se intentan y la descripción existe para todas las observadas |
| `title`, `company`, `id`, `location`, `posted_date` | 100 % (sin cambio) | ya cumplen |
| `salary` | **sin meta** (`target_pct: null`) + regla de no-regresión | el portal solo publica ~36–67 % según país/momento; el scraper ya captura lo publicado |
| `work_mode` | **sin meta** (`target_pct: null`) + no-regresión; se espera mejora de cobertura/exactitud con el fix | solo remotas/híbridas se publican; onsite no existe como dato |
| `skills` | sin meta (ya en el brief) | no publicado por el portal |

**Aviso de registro (para el orquestador):** el `quality_before.json` de este registro tiene `salary` y `work_mode` con `target_pct: 100.0`; tal cual, la puerta añadiría `REASON_TARGET` y **fallaría aun sin regresión** (el 100 % no depende del scraper). Hay que reconciliarlo como se hizo en 20261010 (target `null` para ambos) antes de la prueba en vivo. La única meta dura de esta reparación es `description` (required) al 100 % en el alcance.

## 6. Cambio recomendado (pregunta 6)

1. **`indeed_jobs_scraper/run_nightly_indeed.ps1`**: `$EnrichRate = 1.0` (comentario: todas las candidatas hasta el tope; el resto del cache). Mantener `$EnrichMax = 15` y las pausas. *(Un flag cambia la causa principal sin tocar lógica.)*
2. **`scraper/parser.py`**
   - `enrich_offers_via_right_panel`: dejar de saltar candidatas con `rate=1.0` (todas); si `max_enrich` llega a morder, registrar `WARNING` con cuántas quedan pendientes (hoy solo se registra el recorte, no las saltadas por rate). Opcional: cola pendiente intra-run.
   - **Reintento de clic**: helper que haga hasta 2 intentos (scroll + espera 1–2 s + `click(timeout=10s)`); registrar `jk` solo tras agotar. Cubre el caso `3a8c91f14177f18a` y overlays tipo OneTrust (opcional: cerrar el banner si está visible antes del bucle).
   - `_map_item_to_offer` (work_mode): comprobar `HYBRID` antes que `REMOTE`; `REMOTE_HYBRID→hybrid`, `REMOTE_WORK_FROM_HOME/REMOTE→remote`; usar `remoteWorkModel.text`; el heurístico de título/location como último recurso (y sin pisar un `remoteWorkModel` explícito).
   - Opcional barato: `jobTypes[0]` → `job_type`; añadir "fixed term"/bloque de beneficios a `_map_conditions_to_offer`.
3. **`scraper/runner.py`**: en `_dedup`, cuando un `job_key` se repite, **fusionar campos** del duplicado si el objeto conservado no los tiene (`description_html/text`, `snippet`, `benefits`, `contract_type`, `schedule`, `workplace_type`). Arregla los 4 casos de cache intra-run. *(Alternativa equivalente: `apply_cache_to_offers(all_offers, self.desc_cache)` tras deduplicar.)*
4. **`main.py`**: actualizar el texto de `--enrich-rate` (1.0 = todas las candidatas hasta el tope; valores <1 solo para prorratear riesgo de forma explícita).
5. **`scraper/config.py`** (opcional / decisión de la persona): alinear página 1 con robots (`&start=0` en la URL) o revisar `radius` (ver §1.3).
6. **Tests**: `tests/test_enrich.py` (rate 1.0 no salta candidatas; reintento de clic; fallo definitivo se registra), `tests/test_parser.py` (mapeo `REMOTE_HYBRID→hybrid`/`REMOTE_WORK_FROM_HOME→remote`, `jobTypes`), y test nuevo del merge de duplicados (runner/dedup). Sin dependencias nuevas.

## 7. Comprobación manual propuesta (prueba en vivo acotada)

1. Arrancar Chrome CDP con la sesión real (`start_chrome_cdp.ps1`) y comprobar `python -m scraper.cdp_probe --port 9222` → `CDP_HANDSHAKE_OK`.
2. Desde `indeed_jobs_scraper/`:
   `python main.py --country IE --cities Dublin --term data --pages 1 --fromage 7 --cdp 9222 --enrich --enrich-rate 1.0 --enrich-max 15` (sin `--limit`; sin Azure/landing/merge).
3. Log esperado: `descripciones from _initialData ... 1/N`; `enriquecidas X/X` con X = candidatas y sin recorte por rate; ningún `enriquecimiento fallido`; sin CAPTCHA/challenge. Latido PROGRESS presente.
4. Offline (parquet): `description_text` no vacío = **total de ofertas** del run (meta dura). `salary`/`work_mode`: sin regresión frente a 36.2 %/16.4 % (con targets reconciliados).
5. Repetición opcional (estabilidad): un segundo run 1×1×1; los mismos `job_key` deben cubrirse ahora por cache (0 clics).

## 8. Riesgos y límites

- **No se pudo usar el Chrome 9222** (caído a las 17:13; no se cerró/arrancó Chrome): la comprobación en la sesión real queda para la fase de prueba en vivo. La observación se hizo con el navegador propio de agent-browser (perfil limpio, banner OneTrust; aceptado).
- **Causa exacta del único timeout de clic no reproducida** (posible inestabilidad/overlay); la mitigación propuesta (reintento + cierre de banner) es prudente pero no garantiza el 100 % ante un elemento realmente no clicable.
- **Atribución de las 13 saltadas** es aritmética (1:1 con los recuentos por SERP), no re-ejecutable: `random.sample` no usa semilla y no se guardó la muestra por oferta.
- **NL de la misma noche**: 103 únicas, 79 descritas (76.7 %) y 14 fallos de panel en el enrich (5/5 sin descripción, 4/4 con 2 fallos…). Es otro patrón (panel) fuera de este alcance; tras el cambio conviene revisar que no se agrave (el fix de reintento ayuda).
- **`radius=` y RFC 9309**: matiz legal preexistente; se reporta para decisión de la persona (no bloquea esta reparación; no se tocó ninguna ruta prohibida).
- **`skills`**: no extraíble; cualquier intento de "rellenarlo" inventaría datos. Se mantiene la vía Databricks (derivar de descripción).
- **Página 2 / login / `--limit`**: fuera de alcance (el playbook mantiene la decisión vigente).
- Ejemplos de salario del parquet contienen `€`/`–` correctos (U+20AC/U+2013); el "mojibake" que se observa en consola es solo codepage de visualización, no corrupción de datos.

## 9. Evidencias

| fichero | contenido |
|---|---|
| `repairs/20261018-indeed/evidence/investigation-report.md` | este informe |
| `repairs/20261018-indeed/evidence/indeed-20261018-enrich-arithmetic.txt` | tabla por SERP, aritmética 65/52/13/1, mapeo de las 14, `cached_at` de las 4 recuperadas por duplicado |
| `repairs/20261018-indeed/evidence/indeed-20261018-log-excerpts.txt` | extracto recortado del log del run (cache, parse, autoOpen, muestreo, resultado por SERP, fallo de clic) |
| `repairs/20261018-indeed/evidence/indeed-20261018-missing-descriptions.csv` | las 14 filas (jk, SERP de primera aparición, título, empresa, causa) |
| `repairs/20261018-indeed/evidence/indeed-20261018-live-observations.md` | hechos en vivo: SERP, marcadores DOM, auto-open, salary/remote/jobTypes, dos clics con tiempos, red (`Viewjob` GraphQL), ausencia de skills |
| `repairs/20261018-indeed/evidence/indeed-20261018-robots-tos-relevant.txt` | extractos de robots.txt (IE/NL), matiz RFC 9309 (`radius=`), cláusula de TOS del permiso condicional |
| `.opencode/.agent-screenshots/20261018-171548-indeed-ie-serp-autoopen-panel.png` | SERP IE con panel auto-abierto (descripción + salario del job auto-open) |
| `.opencode/.agent-screenshots/20261018-172331-indeed-ie-panel-pay-not-provided.png` | panel tras clic en oferta sin salario: "Pay information not provided" |
