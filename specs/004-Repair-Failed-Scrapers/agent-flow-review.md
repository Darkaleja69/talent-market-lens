# Revisión del flujo de agentes — T-22 (objetivo simulado, sin red)

- **Spec:** `specs/004-Repair-Failed-Scrapers` (RF-3, RF-9, RF-12, RF-14).
- **Tarea:** T-22, `tasks.md:224-232` (grupo 5: agentes, skills y comando de entrada).
- **Rama:** `spec/004-repair-failed-scrapers`; revisión anclada a
  `344ec82` (`feat(agents): T-21 guia de skills del orquestador`, 2026-10-05).
- **Fecha de la revisión:** 2026-10-05.
- **Modo:** offline. No se usó red, ni navegadores, ni skills en vivo, ni se
  invocaron subagentes; solo se ejecutó el núcleo determinista (`repair.cli`)
  contra una copia temporal del fixture saneado.
- **Naturaleza del documento:** revisión documentada de una **ejecución
  simulada** del flujo. Comprueba que el brief que alimenta al especialista y
  los contratos versionados encadenan las puertas; no es el log de una
  investigación real (véase §8, límites).

## 1. Objetivo y alcance

El objetivo de T-22 es comprobar el flujo de agentes con un **objetivo
simulado y sin red** y dejar constancia en este archivo. La simulación usa el
diagnóstico saneado del run 2026-10-03 como si fuera el diagnóstico vigente:
el núcleo determinista produce la cola de objetivos y el brief de InfoJobs, y
sobre esas salidas reales se contrastan los contratos de los agentes
(`web-inspector`, `implementer`, `verifier`), el comando `/repair` y las
puertas del plan.

Lo que esta revisión **sí** comprueba:

1. el especialista recibe el brief y el playbook (el brief los incluye y el
   contrato del subagente los exige);
2. el especialista devuelve la auditoría de campos y la meta de calidad
   (contrato de salida de `web-inspector`);
3. el implementador recibe el informe con causa, cambio, alcance y metas
   (plan §5, paso 3, y comando `/repair`);
4. el verificador no usa red (prohibición explícita en `verifier.md`);
5. ninguna puerta se salta (orden investigación → implementación →
   verificación → prueba en vivo con calidad → validación del push).

Lo que esta revisión **no** comprueba se marca en §8 (`[NO VERIFICADO]`).

## 2. Preparación de la prueba

Operaciones realizadas, todas offline:

1. Copia del fixture saneado fuera del repositorio:
   - origen: `scrapers-pipeline/tests/fixtures/diagnostic_2026-10-03.sanitized.json`
     (saneado con `<HOME>`, conservado así);
   - destino: `<TEMP>\t22\diagnostic.json` (ruta absoluta temporal fuera del
     repo). En este informe, `<TEMP>` es la máscara de `%TEMP%\opencode`, es
     decir, `<HOME>\AppData\Local\Temp\opencode`; en los bloques JSON las
     barras invertidas van escapadas (`\\`), como en el fichero original.
2. Directorio de trabajo de la CLI: `scrapers-pipeline/` (como exige el
   `README.md` del módulo, `repair/README.md:9-11`).
3. Comandos ejecutados (resumen de salida en §3 y §4):

   ```powershell
   # workdir: scrapers-pipeline/
   python -m repair.cli targets --diagnostic "<TEMP>\t22\diagnostic.json"
   python -m repair.cli brief --source infojobs --diagnostic "<TEMP>\t22\diagnostic.json" --output "<TEMP>\t22\brief-infojobs.json"
   ```

Resultados: ambos comandos terminaron con código de salida `0`. No hubo
fallos que documentar. No se ejecutó la prueba en vivo acotada (RF-9) ni
ningún scraper.

## 3. Selección simulada (`targets`)

Salida real de `targets` (recorte; la cola completa tiene 19 objetivos):

```text
Objetivos de reparación — run de origen: 2026-10-03
Total: 19 objetivos (5 primarios, 14 secundarios)

1. indeed — primario (primary) | kind: direct
   Estado: failed | outcome: ok (ejecución correcta)
   Motivo: campo obligatorio 'description' por debajo del umbral
   ...
2. linkedin — primario (primary) | kind: direct
   ...
3. infojobs — primario (primary) | kind: direct
   Estado: failed | outcome: blocked (bloqueo por CAPTCHA/anti-bot)
   Motivo: sin evidencia suficiente para confirmar la fuente
   Playbook: §6.1 InfoJobs — bloqueo por CAPTCHA
4. irishjobs — primario (primary) | kind: multi_site
   ...
5. glassdoor — primario (primary) | kind: multi_site
   ...
6-19. secundarios (indeed, linkedin, stepstone_nl, nvb, jobs_ch)
```

La cola respeta la priorización de RF-1: primero las 5 fuentes `failed`
(`indeed`, `linkedin`, `infojobs`, `irishjobs`, `glassdoor`) y después las 14
investigaciones de completitud. Coincide con el run de referencia del plan
(`specs/004-Repair-Failed-Scrapers/plan.md:55-70`).

**Objetivo elegido para la simulación:** `infojobs` como primario. Es el caso
de cierre de la spec (RF-3 y criterios de finalización) y su playbook §6.1
permite comprobar el handoff completo. El fixture solo produce un candidato
`infojobs` (su investigación es `source_failed`, sin campo), así que
`brief --source infojobs` no necesitó `--field` (contrato en
`repair/README.md:70-74`).

## 4. Brief del objetivo (`brief --source infojobs`)

Comando (exit `0`):

```powershell
python -m repair.cli brief --source infojobs --diagnostic "<TEMP>\t22\diagnostic.json" --output "<TEMP>\t22\brief-infojobs.json"
```

Campos reales del JSON generado `brief-infojobs.json` (identificadores en
inglés, como exige el contrato de `brief.py`):

- **Objetivo y run:** `schema_version: 1`, `source: "infojobs"`,
  `kind: "direct"`, `role: "primary"`, `field: null`, `trigger: null`,
  `run_date: "2026-10-03"`, `offers_current_run: 0`,
  `offers_snapshot: null`, `completeness_scope: "run"`.
- **Motivo (fallo observado):**

  ```json
  "reason": {
    "outcome": "blocked",
    "failures": ["sin evidencia suficiente para confirmar la fuente"],
    "summary": "blocked: captcha/anti-bot (1 diagnostic failure reason(s))"
  }
  ```

- **Evidencia del diagnóstico:**

  ```json
  "evidence": [
    "estado del pipeline: no_data (subidos=0, rechazados=0)",
    "resultado de la ejecución: bloqueado (blocked)",
    "error registrado: captcha block detected",
    "detalle: blocked=true",
    "intento 1/3"
  ]
  ```

- **Rutas locales de evidencia (`evidence_paths_base: "repository_root"`):**

  ```json
  "evidence_paths": [
    "infojobs_jobs_scraper/data/run_nightly.log",
    "infojobs_jobs_scraper/data/nightly_stdout_attempt1.log",
    "infojobs_jobs_scraper/data/logs/run_20261003_*.log",
    "<TEMP>\\t22\\diagnostic.json",
    "<HOME>\\Documents\\projects\\scrapers-pipeline\\logs\\upload-2026-10-03.log"
  ]
  ```

  La ruta del usuario en el fixture se conserva saneada (`<HOME>`); la ruta
  temporal del diagnóstico se sanea en este informe como `<TEMP>`. El brief no
  contiene credenciales ni rutas de perfiles de navegador (criterio de T-05 y
  no funcional de la spec).

- **Playbook aplicable (lo que el especialista debe aplicar):**

  ```json
  "playbook": {
    "section": "6.1",
    "title": "InfoJobs - CAPTCHA block",
    "recipe": "Blocked by a visible CAPTCHA on open (Distil) with 0 offers. Check which detector marker fires, whether the real user session gets the SERP, and whether an allowed internal JSON endpoint exists. Design to avoid the challenge: real session and challenge-token reuse, coherent fingerprint, human headers and pace; log the block reason and support an assisted pause. Red lines: no CAPTCHA solvers, no identity checks, no paid services.",
    "test_scope": {
      "description": "1 keyword x 1 city x 1 page",
      "parameters": { "keywords": 1, "cities": 1, "pages": 1 },
      "constraints": ["no Azure upload", "no landing update", "no merge"],
      "command": null
    }
  }
  ```

  La receta §6.1 del plan (`plan.md:277-296`) queda referenciada en el brief.

- **Perfil y metas de calidad (`quality[]`):** `title`, `company` y
  `description` con `required: true` y `target_pct: 100.0` (obligatorios);
  `id`, `salary`, `work_mode`, `location`, `posted_date` con
  `target_pct: 100.0`; `skills` con `target_pct: null` porque el portal no lo
  publica (no es fallo ni meta incumplible, RF-16). `current_pct` es `null` en
  todos porque el run no capturó muestra (`completeness: []`).
- **Alcance de prueba:** `1 keyword x 1 city x 1 page`, sin subida a Azure,
  sin landing y sin merge (`test_scope`), coherente con RF-9 y con
  `plan.md:295-296`.

Con esto, el brief entregado al especialista cubre lo que exige el contrato de
entrada de `web-inspector` (fuente, tipo, rol, run de origen, fallo, evidencia
y rutas, playbook, perfil/metas de calidad y alcance de prueba):
`.opencode/agent/web-inspector.md:21-33`.

## 5. Handoff al especialista (`web-inspector`)

**El brief le entrega el playbook.** El JSON anterior incluye
`playbook.section: "6.1"` con `title`, `recipe` y `test_scope`, y el contrato
del subagente pide exactamente eso como entrada: "el **brief en inglés** del
objetivo ... y las rutas de logs y parsers relevantes" con "playbook aplicable
(§6.1–§6.7 del plan) con sus hipótesis y el alcance de prueba propuesto"
(`.opencode/agent/web-inspector.md:23-33`, cita de las líneas 31-32). El contrato de
entrada del plan coincide: `plan.md:419-421`.

**El contrato de salida exige auditoría de campos y meta de calidad.** El
informe que debe devolver el especialista contiene, en este orden
(`.opencode/agent/web-inspector.md:91-125`):

1. **Hechos observados, separados de las hipótesis** — líneas 93-94
   ("separando siempre los hechos de las hipótesis"), 96-98.
2. **Evidencias** (capturas, recortes de red/respuestas y URLs) — líneas
   99-101.
3. **Causa probable y su fundamento** — líneas 102-103.
4. **Auditoría de cobertura de campos** (obligatoria), tabla
   `campo | ¿disponible en web/API? | ¿lo extrae el scraper hoy? | brecha |
   extracción recomendada | base legal` — líneas 104-108.
5. **Meta de calidad propuesta**, por campo y con lo que la web/API permite,
   nunca por debajo de la cobertura actual — líneas 113-117.
6. **Cambio recomendado** (módulo/ficheros y qué hacer) — líneas 118-120.
7. **Comprobación manual propuesta** — líneas 121-122.
8. **Riesgos y límites** (bloqueo, `robots.txt`/TOS, credenciales, presupuesto
   y lo no comprobado) — líneas 123-125.

Este contrato replica la salida de §8 del plan (`plan.md:423-434`). Además el
subagente no edita código (`edit: deny` en `.opencode/agent/web-inspector.md:7`
y líneas 16-19), comprueba `robots.txt`/TOS antes de proponer cambios
(líneas 39-50) y no resuelve CAPTCHAs, registrando el challenge como evidencia
(líneas 127-142). En esta prueba simulada no se invocó al subagente: lo
comprobado es que su entrada (brief + playbook) y su salida obligatoria
(auditoría de campos + meta de calidad) están encadenadas por contrato.

## 6. Handoff al implementador

El plan §5 encadena el informe del especialista con la implementación:

> "3. Con el informe, el orquestador da instrucciones al implementador:
> causa, cambio recomendado, alcance, metas de calidad y restricciones. El
> implementador repara, añade tests y actualiza el registro con la causa y los
> cambios." — `specs/004-Repair-Failed-Scrapers/plan.md:223-226`.

El comando `/repair` repite ese orden sin saltarse pasos:

> "rama `repair/<fuente>-<fecha>` y registro en estado `planificado` → brief
> del objetivo → investigación delegada en `web-inspector` → implementación en
> `implementer` → verificación en `verifier` → prueba en vivo acotada → puerta
> de calidad → cierre del registro (índice e historial)."
> — `.opencode/command/repair.md:58-63`.

El paso 2 del plan fija el contenido del informe del especialista que después
pasa al implementador (auditoría de campos y meta de calidad,
`plan.md:221-222`), y el
propio `implementer.md` declara que no decide el alcance: "No decides el
alcance: lo recibes" (`.opencode/agent/implementer.md:11-13`). Con el brief de
§4 y el contrato de §5, el implementador recibe causa ("captcha block
detected", `reason.outcome: blocked`), cambio recomendado (sección 6 del
informe), alcance (`test_scope`) y metas de calidad (`quality[]`).

## 7. Verificación sin red y puertas del flujo

**El verificador no usa red.** Su contrato lo prohíbe explícitamente:

> "No ejecutes pruebas que necesiten red, navegadores o credenciales reales.
> Si un criterio solo puede comprobarse así, márcalo como **NO VERIFICABLE** e
> indícalo." — `.opencode/agent/verifier.md:29-30`.

El verificador tampoco edita (`edit: deny`, `.opencode/agent/verifier.md:7`)
y su trabajo es revisar el diff de la tarea contra la spec y ejecutar las
suites del componente afectado (`.opencode/agent/verifier.md:32-49`, con la
ejecución de la suite en la línea 39). Encaja con la decisión 6 del plan: la
prueba en vivo la ejecuta el implementador y el verificador no recibe red
(`plan.md:546-547`).

**Puertas del flujo, en orden, con la cita donde cada una queda definida.**
La lista canónica está en `/repair` y en el orquestador:

> "**investigación → implementación → verificación → prueba en vivo con
> calidad → validación del push** (plan §5)." —
> `.opencode/command/repair.md:85-90` (y `.opencode/agent/orchestrator.md:77-82`).

| # | Puerta | Dónde se define | Cuándo se abre |
|---|---|---|---|
| 1 | Investigación | `plan.md:221-222` (paso 2: `web-inspector` con brief y skills; el informe incluye auditoría de campos y meta de calidad); RF-3 en `spec.md:70-78`; contrato en `.opencode/agent/web-inspector.md:91-125` | Cuando existe el brief del objetivo |
| 2 | Implementación | `plan.md:223-226` (paso 3: causa, cambio, alcance, metas y restricciones al implementador); RF-7 | Cuando el informe del especialista está disponible |
| 3 | Verificación | `plan.md:227-229` (paso 4: suites del scraper y del diagnóstico, diff contra la spec 004, el playbook y el registro; con bloqueantes vuelve al paso 3); RF-8; sin red en `.opencode/agent/verifier.md:29-30` | Cuando el fix está implementado |
| 4 | Prueba en vivo con calidad | `plan.md:230-235` (pasos 5-6: prueba acotada, `quality.py`, umbral y sin regresión; si falla, itera o escala); RF-9 en `spec.md:127-134`; RF-10 y RF-16; detalle en `plan.md:442-484`; la ejecuta el implementador (`plan.md:546-547`) | Cuando los tests pasan |
| 5 | Validación del push | `plan.md:238-239` (paso 8: "No hay push ni merge sin esa validación"); RF-12 en `spec.md:153-159`; también `.opencode/command/repair.md:65-66` | Cuando la reparación está probada (tests, umbral y calidad) |

Ninguna puerta se salta: el flujo de `plan.md:217-239` es secuencial; el paso
4 devuelve al 3 si hay bloqueantes y el paso 6 itera dentro de la misma rama o
escala a la persona, pero nunca omite la verificación ni la validación humana.
El aislamiento entre fuentes de RF-14 (`spec.md:169-174`) se respalda con el
hecho de que cada objetivo se simula por separado (un brief por fuente y
campo, `repair/README.md:70-74`). En la simulación, las puertas 1-3 se
comprobaron por contrato/artefacto; las puertas 4-5 no se ejecutan sin red ni
rama de fix y quedan marcadas en §8.

## 8. Resultado de T-22 y límites

Confirmación punto por punto del "Hecho cuando" (`tasks.md:224-232`), que pide
"una revisión documentada de una ejecución de prueba (objetivo simulado, sin
red) que confirma que":

| Punto del "Hecho cuando" | Evidencia en esta revisión | Estado |
|---|---|---|
| El especialista recibe el brief y el playbook | Brief real de `infojobs` con `playbook.section: "6.1"` y `test_scope` (§4); contrato de entrada `web-inspector.md:21-33` | Confirmado por contrato y artefacto (sin subagente vivo) |
| Devuelve la auditoría de campos y la meta de calidad | Secciones obligatorias 4 y 5 del contrato de salida, `web-inspector.md:104-108` y `113-117`; §8 del plan, `plan.md:428-431` | Confirmado por contrato |
| El implementador recibe el informe | `plan.md:223-226` (causa, cambio, alcance, metas y restricciones); secuencia de `.opencode/command/repair.md:58-63`; `implementer.md:11-13` | Confirmado |
| El verificador no usa red | Prohibición explícita en `verifier.md:29-30`; decisión 6 del plan, `plan.md:546-547` | Confirmado |
| Ninguna puerta se salta | Tabla de puertas de §7 con su definición (`repair.md:85-90`, `orchestrator.md:77-82`, pasos del `plan.md:217-239`) | Confirmado |

El informe cumple el "Hecho cuando" de T-22. Este documento es la revisión
documentada y queda en `specs/004-Repair-Failed-Scrapers/agent-flow-review.md`.

**Límites — lo no comprobado en esta prueba (marcado como `[NO VERIFICADO]`):**

- `[NO VERIFICADO]` **Ejecución real del subagente `web-inspector`.** No se
  invocó ningún subagente (prohibido por las reglas de la tarea); no hay
  informe real de investigación ni auditoría de campos emitida por el modelo,
  solo el contrato que la exige. La prueba confirma el encadenamiento
  brief → contrato, no la calidad de una investigación real.
- `[NO VERIFICADO]` **Navegador, red y skills en vivo.** No se abrieron
  URLs, no se usó `agent-browser`/`browser-cdp` ni se cargó `scraping-expert`
  contra una fuente real; las recetas de `.opencode/command/repair.md:75-83` y
  `web-inspector.md:54-85` no se ejecutaron.
- `[NO VERIFICADO]` **Puerta de prueba en vivo (RF-9) y puerta de calidad
  (RF-16).** No se ejecutó ningún scraper (la prueba en vivo es de red y queda
  fuera de esta simulación); `threshold` y `quality` no se probaron aquí más
  allá de su definición en `plan.md:459-484`.
- `[NO VERIFICADO]` **Validación humana del push (RF-12).** No hay rama de fix
  ni push en esta prueba; el contrato se cita, no se ejerce.
- **Sí verificado:** el núcleo determinista offline ejecutado (`targets` y
  `brief`, exit `0`), la estructura y campos del brief de `infojobs`, la
  inclusión del playbook §6.1, las metas de calidad y el alcance de prueba, y
  la coherencia de los contratos citados con esos artefactos. `git status
  --short` solo muestra este archivo nuevo.
