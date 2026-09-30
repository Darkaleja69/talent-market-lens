# Tareas: reparación de scrapers caídos

Las tareas están ordenadas por dependencia. Cada una está dimensionada para una
sesión de aproximadamente 15–25 minutos y debe completarse junto con su
comprobación indicada antes de continuar.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación
  (tests en verde), con mensaje convencional y referencia a la tarea
  (p. ej. `feat(repair): T-07 regla de umbral incremental`).
- El commit local es un checkpoint; no se hace push sin revisión previa.
- Si un diff de tarea supera ~400 líneas, se divide en commits apilados
  (`git add -p`) para mantener cada revisión manejable.
- Al terminar cada grupo (sección de este documento), se detiene la
  implementación y se revisa `git log -p` del grupo contra la spec antes de
  continuar.
- Ninguna tarea se marca como hecha sin haber ejecutado su comprobación.
- Las tareas del grupo 7 se ejecutan en la rama del fix
  (`repair/infojobs-<fecha>`), creada desde esta rama; sus commits viven allí y
  no en la rama de la spec.

## 1. Leer el diagnóstico y construir objetivos

- [ ] **T-01 — Leer y validar el diagnóstico de reparaciones** (~20 min)
  - **RF:** RF-1.
  - **Hecho cuando:** `repair/targets.py` lee `diagnostic_last.json`, exige
    `schema_version: 1` y se detiene con un mensaje claro si falta, es
    ilegible o la versión no es la esperada; tests con fichero ausente,
    JSON inválido y versión desconocida.

- [ ] **T-02 — Extraer las fuentes fallidas con su evidencia** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Hecho cuando:** un fixture del diagnóstico real del 2026-09-30 produce
    objetivos para `indeed`, `linkedin`, `infojobs`, `stepstone_nl`, `nvb`,
    `jobs_ch` y `glassdoor`, cada uno con estado, resultado, ofertas, motivos y
    evidencias; un diagnóstico correcto no produce objetivos y uno inconcluso
    se rechaza indicándolo.

- [ ] **T-03 — Incluir los objetivos secundarios de completitud** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-02.
  - **Hecho cuando:** las investigaciones `required_field_below_target` y
    `optional_field_at_or_below_threshold` se convierten en objetivos
    secundarios con su campo, y quedan priorizados después de las fuentes
    fallidas.

- [ ] **T-04 — Construir el brief por objetivo** (~25 min)
  - **RF:** RF-1, RF-3.
  - **Depende de:** T-02, T-03.
  - **Hecho cuando:** `repair/brief.py` produce un JSON en inglés con fuente,
    tipo, run de origen, motivo, evidencia, campos afectados y el alcance de
    prueba propuesto, sin credenciales; tests de estructura y de contenido.

- [ ] **T-05 — Presentar los objetivos en español** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-04.
  - **Hecho cuando:** la salida para la persona lista los objetivos en español,
    priorizados y con un resumen legible (fuente, estado, motivo), y los
    identificadores de máquina se mantienen en inglés.

- [ ] **T-06 — Cubrir el diagnóstico real con tests** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-02–T-05.
  - **Hecho cuando:** la suite incluye un fixture saneado del diagnóstico del
    2026-09-30 y verifica selección, priorización, brief y mensajes sin red.

## 2. Umbral incremental e historial

- [ ] **T-07 — Implementar la regla pura del umbral** (~20 min)
  - **RF:** RF-10.
  - **Hecho cuando:** `repair/threshold.py` calcula
    `umbral = max(1, mayor recuento verificado + 1)` por fuente; tests con
    historial vacío (1), con 2 (3) y con un recuento previo mayor.

- [ ] **T-08 — Leer y actualizar `repairs/history.json`** (~25 min)
  - **RF:** RF-10.
  - **Depende de:** T-07.
  - **Hecho cuando:** el historial por fuente se lee y se actualiza sin
    decrecer nunca, con fuentes independientes y tolerancia a fichero ausente
    o corrupto; tests de escritura atómica y de formato en inglés.

- [ ] **T-09 — Probar los límites del umbral** (~20 min)
  - **RF:** RF-10.
  - **Depende de:** T-07, T-08.
  - **Hecho cuando:** tests cubren primera reparación, subida del listón
    (2 → 3), no decrecimiento, aislamiento entre fuentes e historial
    manipulado.

## 3. Registros y constancia

- [ ] **T-10 — Definir la plantilla y el índice de reparaciones** (~20 min)
  - **RF:** RF-2, RF-11.
  - **Hecho cuando:** existe la plantilla del registro (`plan.md`) con fuente,
    run, fallo observado, evidencia, plan de investigación, cambios, pruebas,
    resultado y estado, más `repairs/README.md` como índice y un ejemplo
    saneado.

- [ ] **T-11 — Crear el registro al abrir una reparación** (~25 min)
  - **RF:** RF-2.
  - **Depende de:** T-10.
  - **Hecho cuando:** `repair/records.py` crea `repairs/<fecha>-<fuente>/` en
    estado `planificado` a partir del brief, sin sobrescribir un registro
    existente; tests de creación y de colisión.

- [ ] **T-12 — Completar el registro y actualizar índice y historial** (~25 min)
  - **RF:** RF-11.
  - **Depende de:** T-08, T-11.
  - **Hecho cuando:** una reparación terminada (probada, descartada o
    escalada) actualiza el registro, el índice y el historial con su resultado
    y su recuento verificado; tests de los tres estados.

- [ ] **T-13 — Comprobar que los registros no filtran secretos** (~15 min)
  - **RF:** RF-11.
  - **Depende de:** T-12.
  - **Hecho cuando:** un test recorre los registros y evidencias y rechaza
    credenciales, tokens o rutas de perfiles de navegador conocidas.

## 4. CLI y contrato de entrada

- [ ] **T-14 — Construir la CLI delgada de reparaciones** (~25 min)
  - **RF:** RF-13.
  - **Depende de:** T-04, T-08, T-12.
  - **Hecho cuando:** `python -m repair.cli targets|brief|threshold|record`
    funciona desde `scrapers-pipeline/`, acepta `--diagnostic` con la ruta por
    defecto y no contiene reglas de negocio propias.

- [ ] **T-15 — Probar la CLI** (~20 min)
  - **RF:** RF-13.
  - **Depende de:** T-14.
  - **Hecho cuando:** tests cubren subcomandos, ruta por defecto, ruta
    explícita, fichero ausente y códigos de salida, con mensajes en español.

- [ ] **T-16 — Documentar el uso del módulo de reparaciones** (~15 min)
  - **RF:** RF-13.
  - **Depende de:** T-14.
  - **Hecho cuando:** `scrapers-pipeline/repair/README.md` explica los
    subcomandos, el contrato del diagnóstico y el ciclo previsto (run nocturno
    → verificación → reparación) sin duplicar la spec.

## 5. Agentes, skills y comando de entrada

- [ ] **T-17 — Crear el subagente `web-inspector`** (~25 min)
  - **RF:** RF-3, RF-4, RF-6.
  - **Hecho cuando:** `.opencode/agent/web-inspector.md` define el subagente en
    solo lectura con el contrato de informe de la spec (hechos, evidencias,
    causa, cambio recomendado, comprobación manual, riesgos), la regla de
    `robots.txt`/TOS primero, la prohibición de resolver CAPTCHAs y el uso de
    las tres skills.

- [ ] **T-18 — Añadir el comando de entrada `/repair`** (~20 min)
  - **RF:** RF-13.
  - **Depende de:** T-14.
  - **Hecho cuando:** `.opencode/command/repair.md` lanza el flujo sobre el
    diagnóstico por defecto, admite una ruta alternativa y remite a la spec
    004 para el orden de pasos.

- [ ] **T-19 — Documentar la elección de skills para el orquestador** (~20 min)
  - **RF:** RF-3–RF-9.
  - **Depende de:** T-17.
  - **Hecho cuando:** el comando `/repair` y el prompt del orquestador
    incorporan la tabla "cuándo usar cada skill" y las puertas del flujo
    (investigación → implementación → verificación → prueba en vivo →
    validación del push), sin duplicar reglas de negocio.

- [ ] **T-20 — Comprobar el flujo de agentes en una fuente simulada** (~20 min)
  - **RF:** RF-3, RF-14.
  - **Depende de:** T-17–T-19.
  - **Hecho cuando:** una revisión documentada de una ejecución de prueba
    (objetivo simulado, sin red) confirma que el especialista recibe el brief,
    el implementador recibe el informe, el verificador no usa red y ninguna
    puerta se salta.

## 6. Integración offline y regresión

- [ ] **T-21 — Probar la integración del núcleo determinista** (~25 min)
  - **RF:** RF-1–RF-11.
  - **Depende de:** T-06, T-09, T-13, T-15.
  - **Hecho cuando:** un test recorre sobre el fixture real
    `targets → brief → registro → umbral` sin red, con siete fuentes fallidas,
    objetivos secundarios y reparaciones aisladas por fuente.

- [ ] **T-22 — Regresión de la suite del pipeline** (~15 min)
  - **RF:** RF-8.
  - **Depende de:** T-21.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa
    completa tras los cambios del núcleo.

- [ ] **T-23 — Probar los límites del flujo determinista** (~20 min)
  - **RF:** RF-1, RF-10, RF-14.
  - **Depende de:** T-21.
  - **Hecho cuando:** tests cubren diagnóstico inexistente/inconcluso,
    reparación por portal Multi-site, escalado (sin historial o con él) y
    aislamiento entre reparaciones simultáneas.

## 7. Primer caso real (rama `repair/infojobs-<fecha>`)

- [ ] **T-24 — Abrir la rama y el registro de InfoJobs** (~15 min)
  - **RF:** RF-2.
  - **Depende de:** T-11, T-14.
  - **Hecho cuando:** existe la rama `repair/infojobs-<fecha>` creada desde
    esta rama y su registro `planificado` generado desde el brief real.

- [ ] **T-25 — Investigar el bloqueo real de InfoJobs** (~25 min)
  - **RF:** RF-3–RF-6.
  - **Depende de:** T-24.
  - **Hecho cuando:** el informe del especialista describe el CAPTCHA al abrir,
    las rutas alternativas exploradas (API interna, sesión/cookies, cabeceras,
    ritmo), lo verificado en `robots.txt`/TOS y la causa probable, con
    evidencias; sin resolver CAPTCHAs ni usar servicios de pago.

- [ ] **T-26 — Reparar el scraper de InfoJobs** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-25.
  - **Hecho cuando:** el scraper obtiene ofertas por la vía elegida (rediseño
    permitido), sus tests pasan y el registro refleja causa, cambios y
    dependencias justificadas.

- [ ] **T-27 — Verificar el fix de InfoJobs** (~25 min)
  - **RF:** RF-8.
  - **Depende de:** T-26.
  - **Hecho cuando:** el verificador ejecuta la suite de InfoJobs (pytest,
    Ruff y mypy) y la del diagnóstico, revisa el diff contra esta spec y el
    registro, y no quedan hallazgos bloqueantes.

- [ ] **T-28 — Probar InfoJobs en vivo de forma acotada** (~25 min)
  - **RF:** RF-9, RF-10, RF-15.
  - **Depende de:** T-27.
  - **Hecho cuando:** el implementador ejecuta una búsqueda acotada sin subir
    datos, obtiene al menos el umbral (1 oferta en la primera reparación) y
    guarda alcance, recuento y evidencia en el registro.

- [ ] **T-29 — Cerrar el registro y validar el push** (~20 min)
  - **RF:** RF-11, RF-12.
  - **Depende de:** T-28.
  - **Hecho cuando:** el registro queda completo y en estado `probado`, el
    índice y el historial se actualizan, y la persona valida el push de la
    rama antes de subirla.

## 8. Cierre de la spec

- [ ] **T-30 — Documentar el comando de tests en AGENTS.md** (~15 min)
  - **RF:** RF-13.
  - **Depende de:** T-22.
  - **Hecho cuando:** `AGENTS.md` refleja (con el visto bueno previo de la
    persona) el comando del módulo de reparaciones, la convención de ramas
    `repair/` y la ubicación de los registros.

- [ ] **T-31 — Listar las reparaciones pendientes del run real** (~20 min)
  - **RF:** RF-1, RF-11.
  - **Depende de:** T-29.
  - **Hecho cuando:** quedan documentadas, con su evidencia, las fuentes
    fallidas del run 2026-09-30 no reparadas aún (`indeed`, `linkedin`,
    `stepstone_nl`, `nvb`, `jobs_ch`, `glassdoor` y los objetivos secundarios),
    listas para abrir sus ramas cuando la persona quiera.

- [ ] **T-32 — Ejecutar la suite completa y registrar la comprobación real** (~20 min)
  - **RF:** RF-1–RF-15.
  - **Depende de:** T-24–T-31.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa y el
    resultado de la reparación real de InfoJobs (rama, recuento, validación de
    la persona) queda registrado como evidencia de cierre.
