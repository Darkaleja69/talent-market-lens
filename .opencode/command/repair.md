---
description: Inicia el flujo de reparación de scrapers caídos sobre el diagnóstico vigente siguiendo la spec 004.
agent: orchestrator
---

Eres el **orquestador** de Talent Market Lens. Este comando arranca la
reparación de scrapers caídos de la spec 004. El orden de pasos y las reglas de
negocio están en `specs/004-Repair-Failed-Scrapers/spec.md` (RF-1, RF-9, RF-12,
RF-13, RF-15) y en `specs/004-Repair-Failed-Scrapers/plan.md` (§2.1, §5, §6 y
§9): léelos antes de actuar y no los reproduzcas aquí.

Argumento opcional del usuario (ruta alternativa del diagnóstico): `$ARGUMENTS`

Los comandos de la CLI Python de este flujo se ejecutan **desde
`scrapers-pipeline/`**; las recetas de navegador y skills, desde la raíz del
repositorio.

## Paso 0 — refrescar el diagnóstico (plan §2.1)

Antes de seleccionar objetivos, ejecuta:

```powershell
python -m verification.verify_run --offline
```

- Si `$ARGUMENTS` trae una ruta, pásala como `--diagnostic RUTA` en los
  subcomandos que lo admiten (`targets`, `brief`).
- Si viene vacío, usa la ruta por defecto
  (`scrapers-pipeline/logs/diagnostic_last.json`, anclada al paquete) y **no
  pases** `--diagnostic`.
- Si el refresco falla, el diagnóstico resultante no existe o es `inconclusive`,
  o `targets` avisa de desfase, muéstralo en español y deja que la persona
  decida; no inicies ninguna reparación (RF-1, RF-13).

## Seleccionar el objetivo (plan §5, paso 0)

Ejecuta la cola priorizada:

```powershell
python -m repair.cli targets [--diagnostic RUTA]
```

Presenta la salida y **pide a la persona que elija o limite el alcance** (una
fuente o una lista priorizada) antes de continuar. Si no hay objetivos, explica
el motivo y detente.

## Cargar el playbook de la fuente elegida (plan §6)

Identifica la fuente elegida y abre `specs/004-Repair-Failed-Scrapers/plan.md`
§6 para cargar su receta: §6.1 `infojobs`, §6.2 `indeed`, §6.3 `linkedin`,
§6.4 `irishjobs`, §6.5 `glassdoor`, §6.6 objetivos secundarios de completitud y
§6.7 genérico. Esas recetas **no son una lista cerrada**: manda el diagnóstico
vigente y, si la fuente no tiene playbook propio (fallo futuro o fuente añadida
después de esta spec), se aplica §6.7 sin recetas previas.

## Ejecutar el flujo de reparación (plan §5)

A partir de aquí, sigue los pasos de `plan.md` §5 sin saltarte ninguno: rama
`repair/<fuente>-<fecha>` y registro en estado `planificado` → brief del
objetivo → investigación delegada en `web-inspector` → implementación en
`implementer` → verificación en `verifier` → prueba en vivo acotada → puerta de
calidad → cierre del registro (índice e historial). Detalles de prueba, umbral y
calidad: `plan.md` §9.

- El flujo **se detiene a pedir a la persona la validación del push** (RF-12).
  Sin esa validación explícita no hay push, ni merge a `main`, ni PR.
- Durante la reparación **no** se sube a Azure, **no** se toca la landing ni los
  manifests ni `specs/`, **no** se ejecuta el merge de Multi-site y la única
  ejecución de scrapers permitida es la prueba en vivo acotada (RF-9, RF-15).

## Skills y puertas del flujo (plan §7)

### Cuándo usar cada skill

| Situación | Skill y receta |
|---|---|
| Estrategia: API interna, anti-bot, ruta de menor coste, límites legales | `scraping-expert` (`diagnostic.md`, `hidden-apis.md`, `legal-ethics.md`) al abrir cada investigación |
| Diseñar para que el CAPTCHA no se dispare | `scraping-expert` (`anti-bot-strategies.md`) y plan §6.0: sesión y tokens reales, fingerprint coherente, ritmo y flujo humanos |
| Reproducir el fallo: DOM, red, consola, cookies, capturas | `agent-browser open <url> --headed`; `snapshot -i`; `network requests --json`; `console --json`; `errors --json`; `screenshot .opencode/.agent-screenshots/YYYYMMDD-HHMMSS-descripcion.png` |
| La fuente exige la sesión o el fingerprint de la persona (p. ej. InfoJobs, LinkedIn, Glassdoor) | `node .opencode/skills/browser-cdp/scripts/setup-cdp-chrome.js` (**aviso: cierra Chrome**; `--dry-run` antes) y `agent-browser --cdp 9222 <comando>` |
| Descubrir la API interna | `scraping-expert` (`hidden-apis`) y `agent-browser network requests --json --filter ""` |
| Comprobar `robots.txt`/TOS y límites de la reparación | `scraping-expert` (`legal-ethics`) y abrir `robots.txt` con `agent-browser` |
| Guardar evidencia en el registro | captura/recorte y copia saneada a `repairs/<fecha>-<fuente>/evidence/`; nunca perfiles ni credenciales |

### Puertas del flujo (en orden, ninguna se salta)

**investigación → implementación → verificación → prueba en vivo con calidad →
validación del push** (plan §5). La prueba en vivo la ejecuta el
**implementador** (RF-9); el verificador no usa red; el push no ocurre sin
validación humana explícita (RF-12).

### Política anti-bloqueos (plan §6.0)

- El diseño busca **no recibir el challenge**: sesión y tokens reales,
  fingerprint coherente, ritmo y flujo humanos (plan §6.0).
- Quedan **prohibidos** los solvers, los servicios de pago y la verificación de
  identidad.
- Si pese al diseño aparece un CAPTCHA visible, se pausa y se avisa a la persona
  para que lo resuelva ella misma (modo asistido), y se registra como señal de
  que hay que endurecer el diseño.

Las reglas de negocio (umbral, puerta de calidad, contrato del diagnóstico y
alcance de prueba) no se reproducen aquí: están en
`specs/004-Repair-Failed-Scrapers/spec.md` y en `plan.md` §5, §6.0, §7 y §9.

## Al terminar

Resume en español: fuente reparada, estado del registro, resultado de la prueba
en vivo, veredicto de calidad, cambios y si queda pendiente la validación del
push. No hagas push ni merge.
