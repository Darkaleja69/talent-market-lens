# Plan técnico: reparación de scrapers caídos

## 1. Objetivo y límites

Construir un proceso que, a partir del diagnóstico diario (spec 001), convierta
cada fuente fallida en una reparación probada: investigar la web/API real,
identificar la causa, reparar el scraper, pasar los tests y demostrar en vivo
que vuelven a obtenerse ofertas por encima del umbral incremental. Cada
reparación vive en su rama `repair/<fuente>-<fecha>` y deja un registro.

El proceso no sube datos a Azure, no modifica la landing, no cambia el
diagnóstico ni sus umbrales y no resuelve CAPTCHAs. La única ejecución de
scrapers permitida es la prueba en vivo acotada de RF-9. El push de una rama de
fix solo ocurre tras la validación de la persona (RF-12).

## 2. Piezas del proceso

### 2.1 Núcleo determinista (Python)

La lógica que decide **qué** se repara y **cuándo** una prueba es suficiente es
determinista y testeable; los agentes hacen el trabajo de investigación y
cambio de código. El paquete vivirá en `scrapers-pipeline/repair/`, hermano de
`verification/`, con tests en `scrapers-pipeline/tests/`.

| Módulo | Responsabilidad | RF |
|---|---|---|
| `repair/targets.py` | Leer el diagnóstico, validar `schema_version` y extraer las fuentes fallidas y los objetivos secundarios con su evidencia. | RF-1 |
| `repair/brief.py` | Construir el contexto de un objetivo (JSON en inglés) para el especialista y el implementador. | RF-1, RF-3 |
| `repair/threshold.py` | Regla pura del umbral incremental y lectura/actualización de `repairs/history.json`. | RF-10 |
| `repair/records.py` | Crear y completar `repairs/<fecha>-<fuente>/plan.md`, actualizar el índice y el historial. | RF-2, RF-11 |
| `repair/cli.py` | CLI delgada (`python -m repair.cli targets|brief|threshold|record`) que acepta la ruta del diagnóstico. | RF-13 |

La CLI se ejecuta desde `scrapers-pipeline/`, como `verification.verify_run`. La
cola de grupos `targets` se muestra en español para la persona; `brief` y el
contenido de `history.json` se serializan en inglés.

### 2.2 Agentes

| Pieza | Papel |
|---|---|
| Orquestador (ya existe) | Ejecuta el flujo de la sección 3, elige la skill adecuada (sección 6), abre la rama y el registro, y pide la validación del push. |
| `web-inspector` (nuevo subagente) | Investiga la fuente real en modo lectura y devuelve el informe de la sección 7. | 
| Implementador (ya existe) | Repara el scraper, añade tests y ejecuta la prueba en vivo acotada. |
| Verificador (ya existe) | Revisa el diff y ejecuta las suites; no usa red. |

`web-inspector` se define en `.opencode/agent/web-inspector.md` con
`mode: subagent`, `edit: deny` y `bash: allow`. No modifica código ni lanza
scrapers completos.

### 2.3 Skills (instaladas en `.opencode/skills/`, fuera de git por decisión del proyecto)

| Skill | Uso |
|---|---|
| `scraping-expert` | Doctrina de diagnóstico: API JSON interna, anti-bot, escalera de herramientas, selectores, taxonomía de errores, `robots.txt`/TOS. Se carga al abrir cada investigación. |
| `browser-cdp` | Arranca Chrome con el perfil real del usuario (sesión iniciada) en el puerto 9222. Aviso: cierra las ventanas de Chrome. |
| `agent-browser` | Automatiza el navegador: snapshots, clics, red, consola, cookies, evaluaciones y capturas. Contra el Chrome de `browser-cdp` se usa `agent-browser --cdp 9222`. |

La CLI `agent-browser` (global) y su Chrome quedan instalados en la máquina;
la skill `browser-cdp` solo necesita Node y Chrome. Ni el proceso ni los
scrapers añaden dependencias de pago.

### 2.4 Registros y ramas

```
repairs/
  README.md              # índice: fecha, fuente, estado, rama, resultado
  history.json           # historial de recuentos verificados por fuente (inglés)
  20260930-infojobs/
    plan.md              # plan + resultado (español)
    context.json         # brief generado (inglés)
    evidence/            # capturas, recortes de log, respuestas
```

- Rama por fix: `repair/<fuente>-<YYYYMMDD>`, creada desde `main`; durante el
  primer caso real (cierre de la 004) se crea desde la rama de la spec 004,
  porque el proceso vive en ella. Las reparaciones posteriores se ramifican
  desde `main`.
- El registro se crea en el momento de abrir la rama (`planificado`) y se
  completa al terminar. Se versiona en la rama del fix y llega a `main` con su
  merge.

## 3. Flujo operativo por reparación

1. La persona lanza el proceso (comando `/repair` o petición al orquestador).
   La CLI `targets` lee el diagnóstico y presenta la lista priorizada de
   objetivos; la persona elige o limita el alcance.
2. El orquestador crea la rama `repair/<fuente>-<fecha>` y el registro con
   `records.py` (estado `planificado`), y genera el brief con `brief.py`.
3. El orquestador delega la investigación en `web-inspector` (sección 7) con el
   brief y las skills.
4. Con el informe, el orquestador da instrucciones al implementador: causa,
   cambio recomendado, alcance y restricciones. El implementador repara, añade
   tests y actualiza el registro con la causa y los cambios. Si necesita una
   dependencia nueva, la justifica en el registro.
5. El orquestador delega la verificación en `verifier`: suites del scraper
   afectado y del diagnóstico, revisión del diff contra la spec 004 y el
   registro. Con bloqueantes, vuelve al paso 4.
6. El implementador ejecuta la prueba en vivo acotada (sección 4) y anota
   alcance, recuento y evidencia.
7. `threshold.py` comprueba el umbral: si no se alcanza, se itera desde el
   paso 4 dentro de la misma rama; si se alcanza, se completa el registro, se
   actualiza el índice y el historial, y se presenta el resumen en español.
8. Con el fix probado, el orquestador pide a la persona la validación del push
   (RF-12). No hay push ni merge sin esa validación.

## 4. Prueba en vivo y umbral incremental

**Alcance de la prueba.** La configuración más pequeña representativa de la
fuente que produzca un recuento comparable entre reparaciones (por ejemplo, una
búsqueda/región). El alcance elegido se documenta en el registro y se repite
igual en las comparaciones. En Multi-site se ejecuta solo el portal afectado.

**Límites.** Sin subida a Azure, sin landing, sin merge; pocos intentos por
reparación (los de la propia iteración del fix); se ejecuta cuando la persona
lance el proceso. No se repite por rutina: la ejecución del proceso es el
disparador (RF-9).

**Evidencia.** Recuento de ofertas parseadas (los contadores de progreso que ya
existen), log recortado, fecha/hora y alcance, guardados en `evidence/`.

**Umbral (RF-10).** `umbral(fuente) = max(1, best_verified_offers(fuente) + 1)`,
donde `best_verified_offers` es el mayor recuento obtenido en pruebas en vivo
**dadas por buenas** en reparaciones anteriores de esa fuente, conservado en
`repairs/history.json`. El historial nunca decrece: si una reparación se cierra
con 2 ofertas verificadas, la siguiente exigirá al menos 3. Sin historial, el
umbral es 1.

## 5. Contrato con el diagnóstico de la 001

- Entrada: `scrapers-pipeline/logs/diagnostic_last.json` (`schema_version: 1`),
  con `run`, `global_status`, `sources[]` (`id`, `kind`, `status`, `outcome`,
  `offers_current_run`, `completeness`, `publication`, `failures`, `evidence`),
  `trend` e `investigations[]`.
- El diagnóstico se consume en modo lectura; la 004 no lo modifica. Si el
  `schema_version` no es el esperado, el proceso se detiene con un mensaje
  claro en vez de interpretar mal los datos.
- Los objetivos secundarios salen de `investigations[]`
  (`required_field_below_target` y `optional_field_at_or_below_threshold`).
- `global_status` distinto de `partial`/`failed` (por ejemplo, `inconclusive`)
  no produce objetivos (RF-1).
- El run de origen queda fijado en el registro; un diagnóstico nuevo durante la
  reparación no cambia el objetivo.

## 6. Cuándo usar cada skill (guía para el orquestador)

| Situación | Skill |
|---|---|
| Decidir la estrategia: ¿hay API JSON interna?, ¿qué anti-bot?, ¿qué ruta de menor coste?, límites legales | `scraping-expert` (siempre al abrir la investigación) |
| Reproducir el fallo y ver la página actual: DOM, red, consola, cookies, capturas | `agent-browser` |
| La fuente exige la sesión iniciada del usuario o su fingerprint (LinkedIn, Glassdoor) | `browser-cdp` + `agent-browser --cdp 9222` |
| Descubrir la API interna de la web | `scraping-expert` (`hidden-apis`) + `agent-browser network requests` + skill embebida `derive-client` |
| Comprobar `robots.txt`/TOS y límites de la reparación | `scraping-expert` (`legal-ethics`) |

Regla de orden: primero `scraping-expert` decide **qué** se va a cambiar;
después las skills de navegador permiten **ver y evidenciar**; el implementador
cambia; el verificador comprueba; la prueba en vivo la hace el implementador.

## 7. Contrato del subagente `web-inspector`

**Entrada:** breve del objetivo (fuente, run de origen, fallo y evidencia,
alcance de prueba propuesto) más las rutas de logs/parsers relevantes.

**Salida (informe en español):**

- Hechos observados, separados de hipótesis.
- Evidencias: rutas de capturas, recortes de red/respuestas y URLs.
- Causa probable y su fundamento.
- Cambio recomendado (módulo/ficheros y qué hacer).
- Comprobación manual propuesta.
- Riesgos y límites: bloqueo, `robots.txt`/TOS, credenciales usadas.

**Restricciones:** no edita código, no ejecuta scrapers completos, no resuelve
CAPTCHAs, no usa servicios de pago y no deja credenciales en el informe.

## 8. Estrategia de tests

### Unitarios offline (`scrapers-pipeline/tests/test_repair_*.py`)

- `targets`: diagnóstico real saneado (copia del 2026-09-30), fallido simple,
  parcial, correcto, inconcluso, sin fichero, `schema_version` desconocido,
  `blocked`/`error`/`empty`, portales Multi-site y objetivos secundarios.
- `brief`: estructura, claves en inglés, sin credenciales, priorización.
- `threshold`: sin historial → 1; historial con 2 → 3; nunca decrece; fuentes
  independientes; historial corrupto o ausente.
- `records`: creación de `plan.md` desde plantilla, actualización del índice y
  del historial, estados (`planificado`, `probado`, `descartado`, `escalado`).
- `cli`: subcomandos, ruta `--diagnostic`, códigos de salida y mensajes.

### Integración offline

Un fixture con el diagnóstico del 2026-09-30 (saneado) recorre
`targets → brief → registro → umbral` sin red y comprueba que las siete fuentes
fallidas quedan detectadas con su evidencia y que las reparaciones quedan
aisladas por fuente.

### Regresión

- `python -m pytest scrapers-pipeline/tests -q` después de cada tarea.
- Suites del scraper modificado: Indeed, LinkedIn, InfoJobs y Multi-site según
  el caso (`python -m pytest tests -q` desde la carpeta del scraper; InfoJobs
  además Ruff y mypy).

### Comprobación real

El primer caso (InfoJobs) se repara en su rama, con prueba en vivo y validación
de la persona. El resultado se registra en `repairs/` y cierra la spec (junto a
la suite en verde). La prueba en vivo no se ejecuta dentro de pytest.

## 9. Decisiones técnicas y alternativas descartadas

1. **Núcleo determinista mínimo + agentes para investigación y código.** La
   decisión de qué repara y cuándo una prueba basta es lógica de negocio
   testeable; interpretar una web y reescribir un scraper no cabe en un script
   sin modelo. **Descartado:** CLI puramente determinista (no puede
   investigar) y flujo 100 % libre del modelo (decisiones no reproducibles).
2. **Los registros viven en el repositorio, con historial legible por
   máquina.** Da constancia versionada junto al fix y alimenta el umbral.
   **Descartado:** un almacén externo en Azure para esta historia (la
   constitución reserva Azure para datos, y aquí se trata de proceso).
3. **Umbral incremental simple sobre pruebas de reparación.** Same-método,
   mismo alcance; sin estadística. **Descartado:** umbral fijo (no acumula
   evidencia) y umbral por tendencia del diagnóstico (acopla la decisión a
   series que aún no son comparables).
4. **La prueba en vivo la ejecuta el implementador.** Decisión de la persona;
   el verificador queda sin red, como está definido. **Descartado:** dar red al
   verificador o automatizar la prueba en pytest.
5. **Primer fix real desde la rama de la spec 004.** El proceso vive ahí y la
   comprobación real debe usarlo tal cual; después, ramas de fix desde `main`.
   **Descartado:** fusionar la 004 sin ninguna reparación real.
6. **Sin servicios de pago ni resolución de CAPTCHAs.** Decisión de la persona;
   si el bloqueo no se evita con medios locales, se consulta. **Descartado:**
   proxies de pago, solvers y APIs gestionadas.
7. **Multi-site por portal.** Cada portal es un objetivo independiente ya en el
   diagnóstico. **Descartado:** tratar Multi-site como una sola fuente.
8. **Skills y agentes fuera de git.** Decisión de la persona; el proceso
   documenta su instalación para que sea reproducible.
9. **`robots.txt`/TOS primero; escalado consultivo.** Se comprueba antes de
   proponer cambios y se pregunta si no hay vía compatible. **Descartado:**
   decidir por la persona.

## 10. Secuencia de implementación

1. Núcleo: `targets.py` y `brief.py` sobre el diagnóstico real, con tests
   (RF-1, RF-3).
2. Regla incremental e historial: `threshold.py`, con tests (RF-10).
3. Registros: `records.py`, plantilla y índice (RF-2, RF-11).
4. CLI delgada y contrato de entrada (RF-13).
5. `web-inspector` + guía de skills y comando `/repair` (RF-3, RF-4, RF-6).
6. Flujo de reparación documentado para el orquestador (gates, ramas,
   validación del push) (RF-7, RF-8, RF-9, RF-12, RF-14, RF-15).
7. Integración offline con el diagnóstico real y regresión completa.
8. Primer caso real: rama `repair/infojobs-<fecha>`, investigación,
   implementación, verificación, prueba en vivo y validación (cierre real).
9. Documentación del comando de tests en `AGENTS.md` (con visto bueno previo) y
   listado de reparaciones pendientes.

## 11. Trazabilidad RF

| RF | Partes del plan |
|---|---|
| RF-1 | `repair/targets.py`, `brief.py`; fixtures del diagnóstico real. |
| RF-2 | `records.py`; ramas `repair/`; flujo paso 2. |
| RF-3 | `web-inspector`; secciones 6 y 7; brief. |
| RF-4 | Guía de skills; doctrina `scraping-expert` (hidden APIs, anti-bot). |
| RF-5 | Sección 7 y 9 (restricciones); escalado en el flujo. |
| RF-6 | `web-inspector` y regla legal; flujo paso 3. |
| RF-7 | Implementador; flujo pasos 4 y 6. |
| RF-8 | `verifier`; flujo paso 5; suites de regresión. |
| RF-9 | Flujo paso 6; sección 4. |
| RF-10 | `repair/threshold.py`; `history.json`. |
| RF-11 | `records.py`; índice y plantilla; flujo paso 7. |
| RF-12 | Flujo paso 8; registro del push. |
| RF-13 | `repair/cli.py`; contrato de la sección 5; comando `/repair`. |
| RF-14 | Ramas y registros por fuente; fixtures por portal. |
| RF-15 | Límites de la sección 1; prueba acotada de la sección 4. |

## 12. Cumplimiento de la constitución

- **Stack simple:** núcleo en biblioteca estándar (pytest para tests); sin
  dependencias nuevas en los scrapers salvo justificación en el registro. Las
  herramientas de investigación (CLI `agent-browser`, Chrome, skills) son
  tooling local, no runtime, y quedan fuera de git.
- **Spec y código:** este plan implementa RF-1–RF-15; lo que exceda el alcance
  se propone como spec nueva o actualización de la 004.
- **Lógica e interfaz:** selección, umbral y registros son módulos probados; la
  CLI y los agentes solo coordinan.
- **Tests:** unitarios e integración offline del núcleo; suites de cada scraper
  modificado; bits de red solo en la prueba en vivo acotada, fuera de pytest.
- **Persistencia:** no se escribe en Azure ni en la landing; las evidencias
  locales son operativas y los registros van al repositorio.
- **Idioma:** identificadores y artefactos de máquina en inglés; mensajes,
  informes y registros para la persona, en español.
