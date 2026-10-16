# Fase 2 — Sesión real de la persona en glassdoor.ie (T-35, 2026-10-15)

- **Fuente:** glassdoor (mercado Irlanda, `glassdoor.ie`). **Rama:** `repair/glassdoor-20261012`.
- **Fecha/hora:** 2026-10-15, 22:44–23:05 (Europe/Madrid).
- **Modo:** `browser-cdp` (Chrome real de la persona, puerto CDP 9222, perfil de depuración; la persona cerró Chrome e inició sesión a mano) + `agent-browser 0.38.1`. Sesión iniciada (`isLoggedIn: true`); sin solvers, sin acciones de cuenta, sin servicios de pago.
- **Alcance:** pestaña abierta → 1 búsqueda «data analyst» en Irlanda + 1 recarga; 1 sonda acotada al endpoint de autocompletado. Ninguna ejecución de scraper.
- **Saneado:** sin cookies, tokens, correo ni identificadores de sesión; la ciudad del perfil personal queda redactada; rutas de usuario como `<HOME>`.

## Hechos observados

1. **`.ie` sirve con normalidad con la sesión real, sin redirección a `.com` y sin challenge.**
   - SERP cargado: título «192 Data analyst jobs in Ireland | Glassdoor».
   - URL: `https://www.glassdoor.ie/Job/ireland-data-analyst-jobs-SRCH_IL.0,7_KO8,20.htm?sc.keyword=Data%2520Analyst`
   - Contexto embebido del sitio: `cfBotScore: 92`, `knownBot: false`, `cfThreatScore: 0` (la sesión real pasa Cloudflare).
2. **Ubicación de Irlanda usada por el propio sitio: `(locationId=70, tipo N/COUNTRY)`.**
   - `"tldLocation":{"locationId":70,"locationType":"N"}` en el estado embebido del SERP.
   - Petición del propio sitio al cargar el SERP: `POST /job-search-next/bff/getJobAlertStatusQuery` → `200`, cuerpo `{"keyword":"data analyst","locationId":70,"locationType":"COUNTRY"}`.
   - ⇒ Para `config_gd.py`: `location_fallback = {"id": 70, "type": "COUNTRY"}`.
3. **La ruta de autocompletado que usa `resolve_location` está retirada:** `GET /findPopularLocationAjax.htm?maxLocationsToReturn=10&term=Ireland` → **HTTP 404** en la sesión real (con el perfil del scraper, el run 2026-10-12 recibió 403 de Cloudflare en la misma ruta). El fix no puede depender de ese endpoint.
4. **Endpoint de datos observado:** `POST /graph` (`200`; 2 llamadas pequeñas, 128–129 ms) y `job-search-next/bff/*` (perfil/alertas). El SERP de `.ie` llega pre-renderizado en el HTML (32 `listingId` embebidos; 192 ofertas totales). El payload histórico del scraper (`JobSearchResultsQuery` a `/graph`) no se ejercitó en esta fase: lo valida la prueba en vivo.
5. **Salario en EUR (H7 confirmada):** los datos embebidos de ofertas de `.ie` traen `"payCurrency":"EUR"` (p. ej. `payPeriodAdjustedPay {p10: 35000, p50: 41833}` anual; hay ofertas sin banda: `payPeriodAdjustedPay: null`). Cobertura parcial: no todas las ofertas publican salario.
6. **Tiempos (sesión caliente):** navegación completa del SERP ≈ **3,73 s**; XHR `/graph` ≈ **130 ms**. El tiempo por descripción no se midió en esta fase (cada descripción es una llamada del scraper; lo mide la prueba en vivo).
7. **CSRF/home:** no comprobado en esta fase; las anomalías del perfil persistente del scraper (redirección a `.com`, token de 1 carácter) son de su perfil propio y quedan para la observación de la prueba en vivo.

## Conclusión para el fix

Fijar `location_fallback = {"id": 70, "type": "COUNTRY"}` en `multi_site_job_scraper/src/glassdoor/config_gd.py` (mercado IE): si `resolve_location` no responde (404/403), el run ya no aborta y busca en Irlanda con los valores que usa el propio sitio. `location_name` se mantiene vacío (= `country_name` «Ireland»): el autocompletado se intenta primero y el fallback solo cubre su retirada.

## Evidencias

| Fichero | Contenido |
|---|---|
| `.opencode/.agent-screenshots/20261015-224844-gd-ie-serp-session.png` | SERP de `.ie` con la sesión real («192 Data analyst jobs in Ireland») |
| `phase2-ie-location-network-summary.txt` | Valores de ubicación de Irlanda y estado del autocompletado |
| `phase2-ie-serp-network-summary.txt` | Método/estado de las peticiones relevantes del SERP |
| `phase2-ie-timings.txt` | Tiempos medidos |

HAR completa (sin versionar; contiene cookies): `<HOME>\AppData\Local\Temp\opencode\gd-serp-20261015.har` — no copiar al repositorio.
