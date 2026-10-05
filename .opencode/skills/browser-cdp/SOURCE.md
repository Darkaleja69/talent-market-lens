# Origen de la skill

- **Origen:** https://github.com/LoveIiei/oh-story-opencode
  (carpeta `.opencode/skills/browser-cdp/`), commit `55799a7`.
- **Licencia:** MIT (© 2025 oh-story-opencode) — ver `LICENSE`.
- **Fecha de instalación:** 2026-09-30.
- `scripts/setup-cdp-chrome.js` se copió verbatim del repositorio de origen.
- `scripts/cdp-utils.js` se copió verbatim de
  `.opencode/skills/story-long-scan/scripts/` del mismo repositorio.

## Adaptaciones

- `SKILL.md` traducido al español y recortado: se eliminaron las referencias a
  las skills de escritura de historias (el contexto original del repositorio).
- Sustituido el placeholder `{SKILL_DIR}` por la ruta relativa real dentro del
  repositorio (`.opencode/skills/browser-cdp/...`).
- Añadida la sección de combinación con `agent-browser` (`--cdp 9222`) y el
  aviso de que el script cierra todas las ventanas de Chrome al arrancar.

## Requisitos

- Node.js y Google Chrome instalados (verificados en Windows el 2026-09-30).
