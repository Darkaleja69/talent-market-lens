# Sonda `channel="chrome"` — irishjobs (2026-10-17, iteración T-34)

- **Hipótesis en prueba:** el 403 de Akamai lo provoca el binario/fingerprint del
  Chromium empaquetado de patchright (148), no la IP ni el contenido; el Chrome
  real instalado en la máquina (155) debería recibir 200.
- **Método:** script temporal fuera del repositorio (no versionado), patchright
  síncrono de la misma instalación que usa el scraper, headful, presupuesto de
  **6 cargas de documento**; se consumieron **3**. Una pasada por variante, sin
  reintentos. Ningún CAPTCHA visible en ninguna carga (no hubo que pausar ni
  resolver nada). Sin servicios de pago.
- **Perfiles scratch nuevos** (dentro de `<PROFILE_DIR>/`):
  `irishjobs_probe` (variante A) e `irishjobs_probe_b` (reservado para B, no se
  llegó a usar). No se tocaron `irishjobs` ni `irishjobs_flagged_20261017`.
- **Hora local (+02:00):** variante A a las 18:31:44; variante A2 (portada
  18:31:51, SERP 18:32:02). UTC de las respuestas: 16:31:37 / 16:31:45 /
  16:31:54.

## Opciones de lanzamiento

Variante A (réplica exacta de `launch_context` de `src/core/browser.py` más el
canal):

- `user_data_dir=<PROFILE_DIR>/irishjobs_probe`,
  `headless=False`, `viewport={"width":1536,"height":864}`, `locale="en-GB"`,
  `timezone_id="Europe/Dublin"`, `timeout=300000`,
  `ignore_default_args=["--enable-automation"]`, args:
  `--disable-blink-features=AutomationControlled`,
  `--disable-features=IsolateOrigins,site-per-process`, `--no-sandbox`,
  `--disable-dev-shm-usage`; cabecera extra `Accept-Language: en-US,en;q=0.9`;
  **+ `channel="chrome"`**.
- UA observado: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
  (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36` (Chrome real 155).

Variante B (solo si A bloqueaba): `channel="chrome"` sin los cuatro args y sin
cabecera extra, perfil scratch `irishjobs_probe_b`. **No se ejecutó**: A ya
devolvió 200 con tarjetas.

## Resultados

| Variante | Carga | Status | `job-item` | `resultlist` | `result_count` | Título | Marcadores de bloqueo/captcha | Captura |
|---|---|---|---|---|---|---|---|---|
| A | SERP directo | **200** | **25** | presente | presente (`69`) | `Data Analyst job vacancies - Updated Daily` | ninguno | `.opencode/.agent-screenshots/20261017-183145-A-direct-cards.png` |
| A2 | portada (warmup) | 200 | 0 (portada) | no aplica | no aplica | `Jobs in Ireland - Best jobs Ireland has to offer \| IrishJobs.ie` | ninguno | no |
| A2 | SERP tras warmup | **200** | **25** | presente | presente (`69`) | `Data Analyst job vacancies - Updated Daily` | ninguno | `.opencode/.agent-screenshots/20261017-183202-A2-after-warmup-cards.png` |

URL de ambas cargas de SERP: `https://www.irishjobs.ie/jobs/data-analyst?age=7`
(portada: `https://www.irishjobs.ie/`). Espera breve de 3 s tras
`domcontentloaded`; en A2, el warmup reprodujo el flujo del scraper: portada,
espera, descarte del banner de consentimiento (`serp.dismiss_cookie_banner`) y
después el SERP (que en la prueba en vivo es exactamente el flujo que ejecuta el
scraper).

Cabeceras no sensibles de las cargas de SERP (resumen): `content-type:
text/html; charset=utf-8`; `server-timing` con `cdn-cache` (REVALIDATE en A,
HIT en A2), `edge; dur`, `origin; dur` y `ak_p` **presente** (un valor de
telemetría de Akamai; no se copia); `x-akamai-transformed: 9 - 0 pmb=mRUM,2`;
`date` según lo indicado arriba. Akamai sigue delante del sitio, **sin mitigar**
la respuesta.

## Lectura

1. **El canal explica el 200.** Con el mismo equipo e IP, la misma URL y un
   perfil scratch nuevo, el Chrome real 155 (`channel="chrome"`) recibe el SERP
   completo (25 tarjetas, 69 resultados) tanto en carga directa como tras el
   flujo de warmup; el Chromium empaquetado de patchright (148) recibió 403
   `challenge:access_denied` en la prueba en vivo del mismo día (11:38 y 11:47,
   también con perfil renovado). La única diferencia entre ambos escenarios es
   el binario/fingerprint del navegador: la IP, el contenido y los selectores
   quedan descartados como causa.
2. **El contenido y los selectores del scraper son válidos** en la respuesta
   obtenida: `[data-testid="job-item"]` = 25, `[data-at="unified-resultlist"]`
   presente y `[data-at="search-jobs-count"]` = `69`.
3. **Conclusión:** procede parametrizar el canal de navegador como opción
   opt-in solo para irishjobs (`browser_channel: "chrome"`), manteniendo el
   binario empaquetado para el resto de portales. No hizo falta retirar flags ni
   la cabecera extra: la variante B no se ejecutó.

**Saneado:** sin cookies, sin tokens, sin valores de telemetría y sin rutas de
usuario (se usa `<REPO>`); solo URL pública, cabeceras no sensibles, conteos del
DOM y recortes sin datos personales. La captura copiada al registro
(`probe-chrome-channel-A2-serp-cards-20261017.png`) es el SERP público de la
variante A2, sin datos personales.
