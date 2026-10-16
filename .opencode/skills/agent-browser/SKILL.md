---
name: agent-browser
description: Automatiza un navegador real desde la CLI agent-browser para investigar y reparar scrapers: abrir URLs, snapshots de elementos, clics, formularios, esperas, cookies y almacenamiento, consola, red, eval de JavaScript y capturas. Úsala cuando haya que VER o INTERACTUAR con la web/API actual (reproducir un fallo, comprobar selectores, observar respuestas o bloqueos) durante la investigación de un scraper. Para decidir QUÉ cambiar usa la skill scraping-expert; si necesitas la sesión ya iniciada del usuario, combínala con browser-cdp (agent-browser --cdp 9222).
license: MIT (contenido adaptado de cabi/opencode-agent-browser)
compatibility: opencode
metadata:
  origin: https://github.com/cabi/opencode-agent-browser
  commit: 8587bce
---

# Automatización de navegador con agent-browser

CLI de automatización de navegador para agentes. Chrome/Chromium vía CDP con
snapshots del árbol de accesibilidad y referencias compactas de elementos `@eN`.

Requisitos (ya instalados en la máquina del proyecto):

```bash
npm i -g agent-browser
agent-browser install        # descarga el navegador que usa la CLI
```

## CÓMO FUNCIONA

agent-browser mantiene su propia sesión de navegador con cookies.

- Se hace login una vez → cookies guardadas → autenticación persistente.
- **AVISO: `agent-browser close` BORRA TODAS LAS COOKIES** (habría que volver a
  hacer login). Para conservar la sesión, navega con `open <url>` y no cierres.
- Contra un Chrome ya arrancado por la skill `browser-cdp` (perfil real del
  usuario, puerto 9222) se usa `agent-browser --cdp 9222 <comando>` y no se crea
  una sesión propia.

## FLUJO DE TRABAJO

### 1. Abrir

```bash
agent-browser session list                    # sesiones existentes
agent-browser open <url> --headed             # abrir URL (navegador visible)
agent-browser set viewport 1920 1080          # tamaño de ventana
```

### 2. Analizar la página

```bash
agent-browser snapshot -i                     # elementos interactivos con @refs
agent-browser snapshot -i -c                  # salida compacta
```

### 3. Interactuar (usar los @refs del snapshot)

```bash
agent-browser click @e1                       # clic
agent-browser fill @e2 "text"                 # rellenar (limpia antes)
agent-browser type @e2 "text"                 # escribir sin limpiar
agent-browser press Enter                     # pulsar tecla
agent-browser select @e1 "value"              # desplegable
agent-browser scroll down 500                 # scroll
agent-browser hover @e1                       # hover
agent-browser check @e1                       # marcar checkbox
```

### 4. Obtener información

```bash
agent-browser get text @e1                    # texto del elemento
agent-browser get html @e1                    # HTML del elemento
agent-browser get attr data-id @e1            # atributo
agent-browser get title                       # título de la página
agent-browser get url                         # URL actual
```

### 5. Navegar

```bash
agent-browser open <url>                      # ir a URL (conserva cookies)
agent-browser back | forward | reload
```

### 6. Esperas

```bash
agent-browser wait @e1                        # esperar elemento
agent-browser wait 2000                       # esperar milisegundos
agent-browser wait --text "Success"           # esperar texto
```

### 7. Capturas (evidencia)

```bash
agent-browser screenshot .opencode/.agent-screenshots/YYYYMMDD-HHMMSS-descripcion.png
agent-browser screenshot .opencode/.agent-screenshots/YYYYMMDD-HHMMSS-descripcion.png --full
```

## DEV TOOLS (USAR SIEMPRE --json)

### Consola y errores

```bash
agent-browser console --json                  # todos los logs de consola
agent-browser errors --json                   # solo errores de página
```

### Cookies y almacenamiento

```bash
agent-browser cookies get --json              # todas las cookies
agent-browser storage local --json            # localStorage
agent-browser storage session --json          # sessionStorage
```

### Red (para descubrir APIs internas)

```bash
agent-browser network requests --filter "" --json   # peticiones capturadas
agent-browser network requests --clear
```

### Ejecutar JavaScript

```bash
agent-browser eval "window.location.href"
agent-browser eval "document.title"
agent-browser eval "localStorage.getItem('key')"
```

### Depuración

```bash
agent-browser highlight @e1                   # resaltar elemento
agent-browser trace start                     # grabar traza
agent-browser trace stop ./trace.zip          # guardar traza
```

## REGLAS

1. `--headed` siempre al abrir: la persona debe ver el navegador.
2. `--json` siempre en console, errors, cookies, storage y network.
3. `snapshot` antes de interactuar y de nuevo tras cada navegación: los `@refs`
   cambian con la página.
4. Las capturas se guardan en `.opencode/.agent-screenshots/` (ignorado por
   git). Nombres `YYYYMMDD-HHMMSS-descripcion.png`.
5. No cerrar la sesión si hay login: `close` borra las cookies.

## CUÁNDO USAR ESTA SKILL EN TALENT MARKET LENS

- Durante la reparación de un scraper, para ver y reproducir en vivo lo que le
  pasa a la fuente: bloqueo (CAPTCHA/403/429), cambio de estructura, JSON
  interno que ya no responde, etc.
- Para reunir evidencia verificable: captura, peticiones de red y respuestas.
- Antes de decidir la estrategia o el cambio de código, cargar
  `scraping-expert`: esta CLI no decide qué herramientas usar.
- No usarla para recoger ofertas en volumen (eso es trabajo del scraper) ni para
  acciones de cuenta. Hacer primero la comprobación de `robots.txt`/TOS que fija
  el proyecto; las peticiones al objetivo deben ser acotadas y pocas.
- En esta máquina (Windows) el proyecto ejecuta los scrapers en local por
  diseño; probar en vivo SOLO dentro del ciclo de reparación acotado.

## DIAGNÓSTICO DE PROBLEMAS

Si el navegador no responde:

```bash
agent-browser session list
# Si la sesión está rota: cerrarla implica perder cookies.
```

En Windows, finaliza el proceso del daemon desde el Administrador de tareas si
quedó colgado. Como último recurso, `agent-browser close` (borra cookies) y
volver a abrir.
