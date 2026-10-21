# Sonda con navegador de perfil propio — irishjobs (2026-10-17, iteración T-34)

- **Hipótesis en prueba:** el 403 de Akamai de la prueba anterior está ligado al perfil/estado del navegador del scraper, no a la IP.
- **Método:** `agent-browser` (su propio perfil, reutilizando la sesión de la investigación de esta mañana), headful, **1 carga** del SERP. Navegador: Chrome 154 (perfil propio en directorio temporal de `agent-browser`), user-agent nativo Chromium 154. Sesión cerrada al terminar (nada que preservar).
- **Hora de la carga:** 2026-10-17 09:45:50 UTC (11:45:50 local, +02:00), según la cabecera `date` de la respuesta.

## Comprobaciones de la carga

| Comprobación | Resultado |
|---|---|
| URL | `https://www.irishjobs.ie/jobs/data-analyst?age=7` |
| **Status HTTP del documento** | **200** (`text/html; charset=utf-8`) |
| Título de la página | `Data Analyst job vacancies - Updated Daily` |
| Tarjetas `[data-testid="job-item"]` | **25** |
| Contador `[data-at="search-jobs-count"]` | presente (texto `69`) |
| Contenedor `[data-at="unified-resultlist"]` | no encontrado con ese selector en esta carga |
| Señales anti-bot en cabeceras | `ak_p` en `server-timing` y `x-akamai-transformed: 9 - 0 pmb=mRUM,2` (Akamai presente, **sin** mitigar la respuesta) |
| CAPTCHA o interstitial visible | ninguno |

## Lectura

1. Un navegador con perfil propio (sin el estado del perfil del scraper) recibe **200 con el SERP completo y 25 tarjetas parseables con los selectores actuales** del scraper. El contenido no está roto y la IP responde.
2. El 403 `Access Denied` de la prueba anterior (`BLOCKED ... challenge:access_denied status=403`) apunta al **perfil/estado del navegador del scraper** (cookies `_abck`/`bm` marcadas), no a la IP ni al sitio entero.
3. Procede el paso 2 de esta iteración: apartar (sin borrar) el perfil marcado y reintentar la prueba acotada con perfil renovado.

**Saneado:** sin cookies, sin tokens y sin rutas de usuario; solo URL pública, cabeceras no sensibles y conteos del DOM.
