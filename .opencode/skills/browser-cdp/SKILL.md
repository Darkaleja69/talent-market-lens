---
name: browser-cdp
description: Arranca y controla Chrome por el protocolo CDP reutilizando el perfil real del usuario (sesión y cookies ya iniciadas): navegar, extraer contenido, inspeccionar cookies/almacenamiento y obtener capturas. Úsala cuando la investigación de un scraper necesite la sesión autenticada existente (LinkedIn, Glassdoor, etc.) o el navegador del usuario; aviso importante: el script cierra todas las ventanas de Chrome al arrancar. Se combina con agent-browser (agent-browser --cdp 9222) para automatizar ese Chrome.
license: MIT (contenido derivado de LoveIiei/oh-story-opencode)
compatibility: opencode
metadata:
  origin: https://github.com/LoveIiei/oh-story-opencode
  commit: 55799a7
---

# browser-cdp

Controla Chrome a través del protocolo CDP (Chrome DevTools Protocol) para
reutilizar la sesión ya iniciada del usuario (cookies / localStorage / session)
y hacer navegación, extracción de contenido, inspección y capturas.

Requisitos: Node.js y Google Chrome instalados (ambos verificados en la máquina
del proyecto, Windows).

## Arranque

Desde la raíz del repositorio:

```bash
node ".opencode/skills/browser-cdp/scripts/setup-cdp-chrome.js"
```

Modos opcionales del script:

```bash
node ".opencode/skills/browser-cdp/scripts/setup-cdp-chrome.js" 9222        # puerto CDP (por defecto 9222)
node ".opencode/skills/browser-cdp/scripts/setup-cdp-chrome.js" --dry-run   # solo imprime lo que haría
```

## Qué hace el script (paso a paso)

1. Localiza `chrome.exe` (en Windows, `%LOCALAPPDATA%`, `%PROGRAMFILES%`).
2. Comprueba si el puerto CDP ya está escuchando: si es así, reutiliza el Chrome
   existente y termina.
3. Copia el perfil real de Chrome (`%LOCALAPPDATA%\Google\Chrome\User Data\Default`)
   a `%USERPROFILE%\chrome-debug-profile\Default`, o refresca solo `Cookies` y
   `Login Data` si el perfil de depuración ya existe.
4. **Cierra todas las ventanas de Chrome** (`taskkill /F /IM chrome.exe`).
5. Arranca Chrome en modo depuración:
   `chrome.exe --remote-debugging-port=9222 --user-data-dir=%USERPROFILE%\chrome-debug-profile`.
6. Verifica la conexión contra `http://127.0.0.1:9222/json/version`.

Avisos:

- El paso 4 cierra tu Chrome actual: guarda lo que tengas abierto antes.
- El perfil de depuración contiene tus cookies y sesiones: vive fuera del
  repositorio y no debe versionarse ni copiarse a otro sitio.
- Al reutilizar el perfil real, las páginas autenticadas (LinkedIn, Glassdoor,
  etc.) se abren ya logueadas, con el fingerprint del navegador del usuario.

## Uso una vez arrancado

Con Chrome escuchando en 9222, se automatiza con la CLI `agent-browser`
(skill `agent-browser`), que se conecta al Chrome existente:

```bash
agent-browser --cdp 9222 open "https://ejemplo.com" --headed
agent-browser --cdp 9222 snapshot -i
agent-browser --cdp 9222 get title
agent-browser --cdp 9222 eval "document.body.innerText.slice(0, 2000)"
agent-browser --cdp 9222 screenshot .opencode/.agent-screenshots/YYYYMMDD-HHMMSS-descripcion.png
```

El helper `scripts/cdp-utils.js` expone funciones comunes para scripts Node
(`ab`, `evalJSON`, `sleep`, `scrollLoad`, `safeStr`); ejemplo:

```js
const { ab, evalJSON, scrollLoad } = require(".opencode/skills/browser-cdp/scripts/cdp-utils");
ab(9222, "open", "https://ejemplo.com");
const title = evalJSON(9222, "JSON.stringify(document.title)");
```

Para inspeccionar cookies o storage directamente por CDP también sirven los
endpoints HTTP de depuración (`http://127.0.0.1:9222/json`) o `agent-browser --cdp 9222
cookies get --json`.

## Cuándo usar esta skill en Talent Market Lens

- Cuando la fuente exige sesión: LinkedIn, Glassdoor u otra página que la
  persona ya tiene abierta y logueada en su Chrome real.
- Cuando hace falta ver la página "como la ve el usuario" (idioma, región,
  sesión, cookies de consentimiento) antes de tocar el scraper.
- No usarla para volumen ni para acciones de cuenta; la navegación es de
  investigación y debe ser acotada.
- Si el puerto 9222 ya está en uso por otro Chrome de depuración, la skill
  reutiliza esa instancia en lugar de arrancar otra.
