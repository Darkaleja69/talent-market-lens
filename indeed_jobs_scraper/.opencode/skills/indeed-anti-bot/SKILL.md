---
name: indeed-anti-bot
description: Recetario de tecnicas anti-CAPTCHA especificas para Indeed (Cloudflare JSD, DataDome, PerimeterX, Arkose). Usa cuando el scraper reciba un challenge, cuando el usuario quiera escalar volumen o anadir paises, o cuando haya que endurecer el modo stealth. Cubre patchright headed, scrolling humano, delays, sesion persistente, deteccion de challenges y fallback sin coste.
---

# Anti-bot para Indeed — recetario

Indeed combina varios sistemas anti-bot. Este skill recoge que aplicar y en que orden, segun el stack del proyecto (Python + patchright, sin proxy, sin CAPTCHA solver).

## Sistemas anti-bot de Indeed (orden de aparicion)
1. **Cloudflare JSD pasivo** (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`): lo mas comun. Inyecta un iframe 1x1 y fingerprinting JS. No es CAPTCHA interactivo; falla si detecta automatizacion.
2. **Blobs anti-bot en enlaces** (`bb`, `xkcb`): tokens por impresion que expiran. No bloquean el scraping de la SERP, solo invalidan los enlaces `/rc/clk`. Solucion: usar `/viewjob?jk=`.
3. **Challenge interactivo** (DataDome / reCAPTCHA / hCaptcha / Arkose / PerimeterX `px-captcha`): aparece con volumen/frecuencia altos o IPs marcadas. Aqui es donde el scraper se detiene y avisa.

## Estrategia del proyecto (sin proxy, sin coste)
Por prioridad, de mas a menos efectivo:

### 1. Navegador real headed + stealth (lo mas importante)
- **patchright** (no playwright + playwright-stealth, que esta estancado). Parchea `navigator.webdriver`, runtime, CDP detection de forma integrada.
- **Headed, no headless**: headless es bandera roja para Cloudflare.
- `channel="chrome"`: usa Chrome real instalado si esta disponible (mas realista que Chromium bundled).
- Args: `--disable-blink-features=AutomationControlled`, `--no-sandbox`, `--start-maximized`.
- `add_init_script`: refuerza `navigator.webdriver = undefined`, `navigator.languages`, `navigator.plugins`, `window.chrome.runtime`, `permissions.query`.

### 2. Identidad coherente
- Viewport 1920x1080, locale del pais (`es-ES`), timezone del pais (`Europe/Madrid`).
- User-Agent real de Chrome estable (lista corta, uno por sesion, no rotar por peticion).
- Headers `Sec-Ch-Ua`, `Sec-Fetch-*`, `Accept-Language` coherentes con el UA y locale.

### 3. Sesion persistente (cookies)
- `storage_state` guardado en `output/session_state.json` entre ejecuciones.
- Indeed baja la friccion si ve una sesion conocida (cookies de preferencias, locale, `ctk`).
- Reusar sesion es mejor que limpiarla en cada run.

### 4. Delays aleatorios amplios
| Situacion | Rango (segundos) |
|---|---|
| Antes de navegar a una URL | 5 - 12 |
| Entre pagina 1 y 2 de la misma ciudad | 8 - 15 |
| Entre ciudades | 60 - 90 |
| Entre paises | 120 - 180 |
| Backoff tras CAPTCHA | 60 (fijo, 1 reintento) |

Nunca delays fijos ni demasiado cortos. El jitter aleatorio es clave.

### 5. Scrolling gradual humano (lo que pidio el usuario: "muy poco a poco")
Implementado en `scraper/anti_bot.py:human_scroll`:
- Pasos de 200-400px (aleatorio) cada 0.8-1.5s.
- Cada 5-9 pasos, pausa larga de 2-4s (como si leyera una oferta).
- 15% de los pasos son un poco mas grandes; 8% retroceden un poco (muy humano).
- Para al llegar al fondo (2 iteraciones sin avance) o tras 30 viewports.
- Asienta cargas diferidas 1.5s tras terminar.

### 6. Rate limit autoimpuesto
- Sin proxy: max **6 SERPs por ejecucion** (3 ciudades x 2 paginas).
- No ejecutar el scraper en bucle rapido. Entre ejecuciones, esperar al menos varios minutos.

### 7. Deteccion temprana de challenge (politica: avisar sin coste)
`scraper/anti_bot.py:detect_captcha` comprueba tras `goto` y tras `scroll`:
- URL: `/captcha`, `chkjsproc`, `/cdn-cgi/challenge-platform/`, `challenges.cloudflare.com`, `px-captcha`, `dd-captcha`, `funcaptcha`, `arkoselabs`.
- `<title>`: "just a moment", "verificación", "captcha", "are you a human".
- iframes: `iframe[src*="challenges.cloudflare.com"]`, `iframe#px-captcha`, `iframe[src*="datadome.co"]`, `iframe[src*="arkoselabs"]`, `iframe[src*="funcaptcha"]`, `#px-captcha`, `.cf-turnstile`.

Si salta: screenshot + log + aborta limpio. **No** intentar resolverlo automaticamente (politica del usuario: sin coste).

### 8. Backoff con nueva sesion
1 reintento: cerrar navegador, esperar 60s, lanzar nueva sesion (nuevo UA, posiblemente nueva IP si hay proxy). Si vuelve a fallar, avisar al usuario.

## Si el usuario quiere escalar (fuera del prototipo)
Por orden de efectividad:

1. **Proxies residenciales** (Bright Data, Smartproxy, Oxylabs, IPRoyal). Casi imprescindible para >6 SERPs/dia o para paises fuera del de la IP local. Formato: `http://user:pass@host:port`. En patchright: `context_kwargs["proxy"] = {"server": ..., "username": ..., "password": ...}`.
2. **Rotar IP por ciudad** (no por peticion): 1 IP por ciudad, mantenerla toda la ejecucion.
3. **CAPTCHA solver de pago** (2Captcha, Anti-Captcha, CapMonster). Solo cuando 1+2 no basten. Necesita API key en `.env`. Cubre reCAPTCHA v2/v3, hCaptcha, Funcaptcha. ~1.45$/1000.
4. **Bajar frecuencia**: mejor 1 ejecucion diaria de 6 SERPs que 4 ejecuciones de 6.

## Que NO hacer
- Headless puro (dispara Cloudflare).
- `requests`/`httpx` directos (Indeed es SPA React, no devuelve job cards sin JS).
- Rotar UA por peticion (sospechoso).
- Delays fijos o <3s.
- Mas de 6 SERPs sin proxy.
- Clickar el enlace de paginacion (mejor navegar directo a `&start=10`).
- Usar `/rc/clk` para obtener la URL de la oferta (los blobs expiran; usar `/viewjob?jk=`).

## Diagnostico cuando salta CAPTCHA
1. Revisar `output/screenshot_*.png` (full_page).
2. Revisar `output/log_*.log` buscando `CAPTCHA` o `challenge`.
3. Si es Cloudflare "Just a moment" intermitente: suele pasar esperando mas (subir `DELAY_PRE_NAV` a 15-20s) y reusando sesion.
4. Si es DataDome/PerimeterX persistente: la IP esta marcada. Necesitas proxy residencial o esperar 24h.
5. Si es Arkose/Funcaptcha: solo se va con CAPTCHA solver de pago o cambio de IP.

## Como actualizar este skill
Si aparece un nuevo tipo de challenge o una tecnica deja de funcionar:
1. Documentar el challenge (URL, title, iframe, screenshot).
2. Actualizar `CAPTCHA_URL_MARKERS` / `CAPTCHA_TITLE_MARKERS` en `scraper/config.py`.
3. Actualizar este skill con la solucion y la fecha.
