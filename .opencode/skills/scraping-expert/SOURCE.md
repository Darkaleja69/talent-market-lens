# Origen de la skill

- **Origen:** https://github.com/MrBridgeHQ/scraping-expert-claude
  (carpeta `skills/scraping-expert/`).
- **Commit instalado:** `3a92fed`.
- **Fecha de instalación:** 2026-09-30.
- **Licencia:** MIT (© 2026 Mr Bridge) — ver `LICENSE`.
- **Instalación:** `SKILL.md` y `references/` verbatim, con una única
  adaptación: la `description` del frontmatter se acortó porque superaba el
  límite de 1024 caracteres que exige opencode (el cuerpo y las referencias no
  se tocaron).

## Adaptaciones del proyecto

- La regla 1 del autor ("no hacer scraping desde la máquina local") se
  interpreta en Talent Market Lens así: los scrapers corren en local por diseño
  del proyecto; las peticiones en vivo SOLO se hacen dentro del ciclo acotado
  de reparación de la spec 004 y después de comprobar `robots.txt`/TOS.
- El proyecto no usa Apify ni APIs gestionadas de pago. Las referencias de esa
  parte (`apify-patterns.md`, `managed-apis.md`, `error-handling-ppe.md`) se
  usan como doctrina general (taxonomía de errores, patrón graceful-exit), no
  como recomendación de compra.
- Las referencias no se versionan aquí: viven en `references/` y se cargan solo
  cuando el caso lo requiere.
