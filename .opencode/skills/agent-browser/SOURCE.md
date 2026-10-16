# Origen de la skill

- **Contenido adaptado de:** https://github.com/cabi/opencode-agent-browser
  (`src/skills/agent-browser.ts`), commit `8587bce`.
- **Licencia:** MIT (© 2026) — ver `LICENSE`.
- **Fecha de instalación:** 2026-09-30.
- Se descartó el stub de `grojeda/opencode-config` por no tener licencia.

## Adaptaciones

- Texto pasado a español y reorganizado; se añadió la sección "Cuándo usar esta
  skill en Talent Market Lens".
- Capturas redirigidas a `.opencode/.agent-screenshots/` para no ensuciar el
  repositorio (`.opencode/` ya está ignorado por git).
- Diagnóstico de problemas adaptado a Windows (sin `pkill`).
- Añadida la integración `agent-browser --cdp 9222` con la skill `browser-cdp`.

## Requisitos

- `npm i -g agent-browser` y `agent-browser install` (descarga del navegador).
  Instalados en la máquina del proyecto el 2026-09-30.
