# Tareas: reparación de scrapers caídos

Las tareas están ordenadas por dependencia. Cada una está dimensionada para una
sesión de aproximadamente 15–25 minutos y debe completarse junto con su
comprobación indicada antes de continuar. Los grupos 8 y 9 cierran ciclos
completos de reparación (varias delegaciones y sesiones) y se marcan al cerrar
su registro, no por fase.

## Convención de commits

- Cada tarea se cierra con **un commit atómico** al pasar su comprobación
  (tests en verde), con mensaje convencional y referencia a la tarea
  (p. ej. `feat(repair): T-08 regla de umbral incremental`).
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
- Las tareas de los grupos 8 y 9 se ejecutan **después del cierre de la 004**,
  cada una en su rama `repair/<fuente>-<fecha>` creada desde `main`, con su
  propio registro; no reabren la spec.
- El comando de comprobación de cada tarea del núcleo es
  `python -m pytest scrapers-pipeline/tests -q` desde la raíz del repositorio.

## 1. Diagnóstico, objetivos y brief

- [ ] **T-01 — Leer, validar y comprobar la vigencia del diagnóstico** (~25 min)
  - **RF:** RF-1, RF-13.
  - **Hecho cuando:** `repair/targets.py` lee el diagnóstico (ruta
    parametrizable, por defecto `scrapers-pipeline/logs/diagnostic_last.json`),
    exige `schema_version: 1`, se detiene con un mensaje claro si falta, es
    ilegible o la versión no es la esperada, y avisa (sin fallar) si el
    diagnóstico es más antiguo que el último `upload-*.log`; tests con fichero
    ausente, JSON inválido, versión desconocida y aviso de desfase.

- [ ] **T-02 — Extraer las fuentes fallidas con su evidencia** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-01.
  - **Hecho cuando:** un fixture saneado del diagnóstico real del 2026-10-03
    produce objetivos para `indeed`, `linkedin`, `infojobs`, `irishjobs` y
    `glassdoor`, cada uno con estado, resultado, ofertas, motivos, evidencias y
    las rutas locales de evidencia del playbook; una fuente desconocida incluida
    en el fixture también produce objetivo genérico; un diagnóstico correcto no
    produce objetivos y uno inconcluso se rechaza indicándolo.

- [ ] **T-03 — Incluir los objetivos secundarios de completitud** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-02.
  - **Hecho cuando:** las investigaciones `required_field_below_target` y
    `optional_field_at_or_below_threshold` se convierten en objetivos
    secundarios con su campo y su porcentaje (`stepstone_nl`, `nvb`, `jobs_ch`,
    `indeed`, `linkedin` en el fixture real), y quedan priorizados después de
    las fuentes fallidas.

- [ ] **T-04 — Calcular el perfil de calidad de cada objetivo** (~25 min)
  - **RF:** RF-1, RF-7.
  - **Depende de:** T-02, T-03.
  - **Hecho cuando:** cada objetivo incluye, por campo, si es obligatorio, su
    porcentaje actual y la meta propuesta (100 % en obligatorios; mejora sobre
    el valor actual en los opcionales que la web/API expone), sin inventar
    valores fuera del fixture y con tests de los casos 0 %, umbral 60 %, campo
    ausente por el portal (sin meta, no es fallo) y campo degradado.

- [ ] **T-05 — Construir el brief por objetivo** (~25 min)
  - **RF:** RF-1, RF-3.
  - **Depende de:** T-02–T-04.
  - **Hecho cuando:** `repair/brief.py` produce un JSON en inglés con fuente,
    tipo, run de origen, motivo, evidencia (incluidas las rutas locales),
    playbook del plan (§6) aplicable, perfil y metas de calidad, y el alcance de
    prueba propuesto, sin credenciales ni rutas de perfiles de navegador; tests
    de estructura y de contenido.

- [ ] **T-06 — Presentar los objetivos en español** (~20 min)
  - **RF:** RF-1.
  - **Depende de:** T-05.
  - **Hecho cuando:** la salida para la persona lista los objetivos en español,
    priorizados y con un resumen legible (fuente, estado, motivo, brecha de
    calidad y playbook), y los identificadores de máquina se mantienen en
    inglés.

- [ ] **T-07 — Cubrir el diagnóstico real con tests** (~25 min)
  - **RF:** RF-1.
  - **Depende de:** T-02–T-06.
  - **Hecho cuando:** la suite incluye un fixture saneado del diagnóstico del
    2026-10-03 (`<HOME>` en lugar de rutas de usuario) y verifica selección,
    priorización, perfil de calidad, brief y mensajes sin red; incluye una
    fuente desconocida y comprueba el flujo genérico.

## 2. Umbral incremental y puerta de calidad

- [ ] **T-08 — Implementar la regla pura del umbral** (~20 min)
  - **RF:** RF-10.
  - **Hecho cuando:** `repair/threshold.py` calcula
    `umbral = max(1, mayor recuento verificado + 1)` por fuente; tests con
    historial vacío (1), con 2 (3) y con un recuento previo mayor.

- [ ] **T-09 — Leer y actualizar `repairs/history.json`** (~25 min)
  - **RF:** RF-10.
  - **Depende de:** T-08.
  - **Hecho cuando:** el historial por fuente se lee y se actualiza sin
    decrecer nunca, con fuentes independientes y tolerancia a fichero ausente
    o corrupto; tests de escritura atómica y de formato en inglés.

- [ ] **T-10 — Probar los límites del umbral** (~20 min)
  - **RF:** RF-10.
  - **Depende de:** T-08, T-09.
  - **Hecho cuando:** tests cubren primera reparación, subida del listón
    (2 → 3), no decrecimiento, aislamiento entre fuentes e historial
    manipulado.

- [ ] **T-11 — Implementar la puerta de calidad** (~25 min)
  - **RF:** RF-7, RF-8, RF-11, RF-16.
  - **Depende de:** T-04.
  - **Hecho cuando:** `repair/quality.py` mide un parquet con
    `verification.completeness` (reutilizado, sin métricas nuevas), lo compara
    con el perfil del brief y emite un veredicto con la tabla antes/después: los
    obligatorios deben estar al 100 % en la muestra, ningún campo ya cubierto
    puede caer más de 5 puntos porcentuales (`QUALITY_REGRESSION_TOLERANCE_PP`)
    y los campos objetivo alcanzan su meta solo cuando la web/API los expone;
    los opcionales ausentes por el portal no bloquean ni cuentan como regresión
    (solo si se degradan) y la `description` completa es prioritaria; tests
    offline con parquet sintético de mejora, regresión, opcional ausente que no
    bloquea, opcional degradado que sí, obligatorio incompleto, muestra vacía e
    ilegible.

## 3. Registros y constancia

- [ ] **T-12 — Definir la plantilla y el índice de reparaciones** (~25 min)
  - **RF:** RF-2, RF-11.
  - **Hecho cuando:** existe la plantilla del registro (`plan.md`) con fuente,
    run, fallo observado, evidencia, plan de investigación, cambios, pruebas
    (tests y prueba en vivo con alcance y recuento), sección de calidad
    (antes/después y meta), resultado y estado; más `repairs/README.md` como
    índice (fecha, fuente, estado, rama, resultado, calidad) y un ejemplo
    saneado.

- [ ] **T-13 — Crear el registro al abrir una reparación** (~25 min)
  - **RF:** RF-2.
  - **Depende de:** T-12.
  - **Hecho cuando:** `repair/records.py` crea `repairs/<fecha>-<fuente>/` en
    estado `planificado` a partir del brief (incluido `context.json` y el
    directorio `evidence/`), sin sobrescribir un registro existente; tests de
    creación y de colisión.

- [ ] **T-14 — Completar el registro y actualizar índice e historial** (~25 min)
  - **RF:** RF-11.
  - **Depende de:** T-09, T-11, T-13.
  - **Hecho cuando:** una reparación terminada (probada, descartada o escalada)
    actualiza el registro, el índice y el historial con su resultado, su
    recuento verificado y su delta de calidad (`quality_after.json`); tests de
    los tres estados.

- [ ] **T-15 — Comprobar que los registros no filtran secretos** (~15 min)
  - **RF:** RF-11.
  - **Depende de:** T-14.
  - **Hecho cuando:** un test recorre los registros y evidencias y rechaza
    credenciales, tokens o rutas de perfiles de navegador conocidas; las
    capturas y extractos se guardan saneados.

## 4. CLI y contrato de entrada

- [ ] **T-16 — Construir la CLI delgada de reparaciones** (~25 min)
  - **RF:** RF-13.
  - **Depende de:** T-05, T-09, T-11, T-14.
  - **Hecho cuando:** `python -m repair.cli targets|brief|threshold|quality|record`
    funciona desde `scrapers-pipeline/`, acepta `--diagnostic` con la ruta por
    defecto y no contiene reglas de negocio propias.

- [ ] **T-17 — Probar la CLI** (~20 min)
  - **RF:** RF-13.
  - **Depende de:** T-16.
  - **Hecho cuando:** tests cubren subcomandos, ruta por defecto, ruta
    explícita, fichero ausente y códigos de salida, con mensajes en español.

- [ ] **T-18 — Documentar el uso y el paso 0 de refresco** (~20 min)
  - **RF:** RF-13.
  - **Depende de:** T-16.
  - **Hecho cuando:** `scrapers-pipeline/repair/README.md` explica los
    subcomandos, el contrato del diagnóstico, el **refresco previo**
    (`python -m verification.verify_run --offline` desde `scrapers-pipeline/`)
    y el ciclo previsto (run nocturno → verificación → reparación), sin
    duplicar la spec.

## 5. Agentes, skills y comando de entrada

- [ ] **T-19 — Crear el subagente `web-inspector` (contrato v2)** (~25 min)
  - **RF:** RF-3, RF-4, RF-6.
  - **Hecho cuando:** `.opencode/agent/web-inspector.md` define el subagente en
    modo lectura de código (solo escribe evidencias bajo
    `repairs/<fecha>-<fuente>/evidence/` y `.opencode/.agent-screenshots/`) con
    el contrato de informe de §8 del plan: hechos vs. hipótesis, evidencias,
    causa, **auditoría de cobertura de campos**, **meta de calidad propuesta**,
    cambio recomendado, comprobación manual y riesgos; regla de `robots.txt`/TOS
    primero, prohibición de resolver CAPTCHAs y uso de las tres skills. El
    informe explica por qué se disparó el bloqueo (si lo hubo) y qué técnicas
    preventivas de §6.0 aplican (sesión/tokens reales, fingerprint, ritmo y
    flujo humanos).

- [ ] **T-20 — Añadir el comando de entrada `/repair`** (~20 min)
  - **RF:** RF-13.
  - **Depende de:** T-16.
  - **Hecho cuando:** `.opencode/command/repair.md` lanza el flujo sobre el
    diagnóstico por defecto (con el paso 0 de refresco), admite una ruta
    alternativa, selecciona el playbook de §6 del plan para la fuente y remite a
    la spec 004 para el orden de pasos.

- [ ] **T-21 — Documentar la elección de skills para el orquestador** (~20 min)
  - **RF:** RF-3–RF-9.
  - **Depende de:** T-19.
  - **Hecho cuando:** el comando `/repair` y el prompt del orquestador
    incorporan la tabla "cuándo usar cada skill" con recetas concretas
    (`agent-browser --cdp 9222`, `network requests --json`, capturas en
    `.opencode/.agent-screenshots/`, aviso de que `browser-cdp` cierra Chrome) y
    las puertas del flujo (investigación → implementación → verificación →
    prueba en vivo con calidad → validación del push) y la política anti-bloqueos
    §6.0 (diseñar para no disparar el CAPTCHA, sin solvers), sin duplicar reglas
    de negocio.

- [ ] **T-22 — Comprobar el flujo de agentes en una fuente simulada** (~20 min)
  - **RF:** RF-3, RF-14.
  - **Depende de:** T-19–T-21.
  - **Hecho cuando:** una revisión documentada de una ejecución de prueba
    (objetivo simulado, sin red) confirma que el especialista recibe el brief y
    el playbook, devuelve la auditoría de campos y la meta de calidad, el
    implementador recibe el informe, el verificador no usa red y ninguna puerta
    se salta.

## 6. Integración offline y regresión

- [ ] **T-23 — Probar la integración del núcleo determinista** (~25 min)
  - **RF:** RF-1–RF-11, RF-16.
  - **Depende de:** T-07, T-10, T-11, T-15, T-17.
  - **Hecho cuando:** un test recorre sobre el fixture real
    `targets → brief → registro → umbral → calidad` sin red, con las cinco
    fuentes fallidas, los objetivos secundarios y las reparaciones aisladas por
    fuente.

- [ ] **T-24 — Probar los límites del flujo determinista** (~20 min)
  - **RF:** RF-1, RF-9, RF-10, RF-14.
  - **Depende de:** T-23.
  - **Hecho cuando:** tests cubren diagnóstico inexistente/inconcluso/desfasado,
    watchdog `exit=75` tratado como fallo de progreso con su evidencia,
    reparación por portal Multi-site, escalado (sin historial o con él),
    aislamiento entre reparaciones simultáneas y fuente desconocida añadida al
    diagnóstico.

- [ ] **T-25 — Regresión de la suite del pipeline** (~15 min)
  - **RF:** RF-8.
  - **Depende de:** T-23.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa
    completa tras los cambios del núcleo.

## 7. Primer caso real (rama `repair/infojobs-<fecha>`)

- [ ] **T-26 — Abrir la rama y el registro de InfoJobs** (~15 min)
  - **RF:** RF-2.
  - **Depende de:** T-13, T-16.
  - **Hecho cuando:** existe la rama `repair/infojobs-<fecha>` creada desde
    esta rama, con el diagnóstico refrescado y su registro `planificado`
    generado desde el brief real (playbook §6.1).

- [ ] **T-27 — Investigar el bloqueo real de InfoJobs** (~25 min)
  - **RF:** RF-3–RF-6.
  - **Depende de:** T-26.
  - **Hecho cuando:** el informe del especialista describe el CAPTCHA al abrir,
    las rutas alternativas exploradas (API interna, sesión/cookies reales con
    `browser-cdp`, cabeceras, ritmo, endpoints alternativos), lo verificado en
    `robots.txt`/TOS, la auditoría de cobertura de campos, la causa probable y
    la meta de calidad propuesta, con evidencias; sin resolver CAPTCHAs ni usar
    servicios de pago. El informe explica por qué se dispara y la estrategia
    preventiva §6.0 que debe implementarse (sesión/tokens reales, fingerprint,
    flujo y ritmo humanos).

- [ ] **T-28 — Reparar el scraper de InfoJobs** (~25 min)
  - **RF:** RF-7.
  - **Depende de:** T-27.
  - **Hecho cuando:** el scraper obtiene ofertas por la vía elegida (rediseño
    permitido), sus tests pasan (pytest, Ruff y mypy) y el registro refleja
    causa, cambios, dependencias justificadas y meta de calidad. El rediseño
    aplica la estrategia preventiva de §6.0 (sin solvers) y documenta la técnica
    contra el challenge.

- [ ] **T-29 — Verificar el fix de InfoJobs** (~25 min)
  - **RF:** RF-8.
  - **Depende de:** T-28.
  - **Hecho cuando:** el verificador ejecuta la suite de InfoJobs (pytest,
    Ruff y mypy) y la del diagnóstico, revisa el diff contra esta spec, el
    playbook §6.1 y el registro, y no quedan hallazgos bloqueantes.

- [ ] **T-30 — Probar InfoJobs en vivo de forma acotada** (~25 min)
  - **RF:** RF-9, RF-10, RF-15, RF-16.
  - **Depende de:** T-29.
  - **Hecho cuando:** el implementador ejecuta una búsqueda acotada (1 keyword
    × 1 ciudad × 1 página, sin subir datos, sin landing y sin merge), obtiene al
    menos el umbral (1 oferta en la primera reparación), pasa la puerta de
    calidad (obligatorios al 100 %, sin regresión y meta alcanzada; los
    opcionales que el portal no publica no bloquean) y guarda
    alcance, recuento, `quality_after.json` y evidencia en el registro. La
    prueba busca no recibir CAPTCHA visible; si aparece, se registra como fallo
    de diseño (la persona puede resolverlo en modo asistido para continuar) y se
    itera. Si no se alcanza el umbral, se itera en la misma rama; si se agotan
    las opciones locales, se prepara el escalado con opciones para la persona.

- [ ] **T-31 — Cerrar el registro y validar el push** (~20 min)
  - **RF:** RF-11, RF-12.
  - **Depende de:** T-30.
  - **Hecho cuando:** el registro queda completo y en estado `probado` (o
    `escalado` con las opciones presentadas), el índice y el historial se
    actualizan, y la persona valida el push de la rama antes de subirla.

## 8. Reparaciones posteriores del run 2026-10-03

Estas tareas se ejecutan **tras el cierre de la 004**, con el proceso ya
fusionado en `main`. Cada una es un ciclo completo gobernado por el flujo de §5
del plan: rama `repair/<fuente>-<fecha>` desde `main` → registro → `web-inspector`
con el playbook → implementador → verificador → prueba en vivo (umbral +
calidad) → registro completo → validación de la persona. Todas aplican la
política anti-bloqueos §6.0 (diseñar para no recibir el challenge, sin solvers).
La lista de fuentes se recalcula con `targets` sobre el último diagnóstico; si
el run cambia antes de ejecutarlas, los objetivos y playbooks se ajustan. Una
reparación descartada o escalada se registra y no bloquea a las demás.

- [ ] **T-32 — Reparar Indeed (playbook §6.2)** (ciclo completo)
  - **RF:** RF-1–RF-12.
  - **Hecho cuando:** el registro de `repair/indeed-<fecha>` queda probado (o
    escalado/descartado con justificación y opciones); la descripción alcanza el
    100 % del alcance de prueba con el fallback `/viewjob` alcanzable o vía
    equivalente; el login de la página 2 y el conflicto de `--limit` quedan
    resueltos o documentados; la prueba en vivo supera el umbral y la puerta de
    calidad, incluidos los campos secundarios que la persona seleccione
    (`salary` y `work_mode` si el sitio los expone; `skills` no se exige como
    campo: se derivan de la descripción en Databricks); tests en verde.

- [ ] **T-33 — Reparar LinkedIn (playbook §6.3)** (ciclo completo)
  - **RF:** RF-1–RF-12.
  - **Hecho cuando:** el registro de `repair/linkedin-<fecha>` queda probado;
    la descripción agregada alcanza el 100 % (backfill del histórico o
    justificación registrada de por qué no procede); `salary` y `work_mode` se
    mejoran hasta lo que la web/API exponga; sin regresión; prueba en vivo con
    umbral y calidad; tests en verde.

- [ ] **T-34 — Reparar IrishJobs (playbook §6.4)** (ciclo completo)
  - **RF:** RF-1–RF-12.
  - **Hecho cuando:** el registro de `repair/irishjobs-<fecha>` queda probado;
    la causa raíz (bloqueo o cambio de HTML) está evidenciada; el scraper emite
    progreso por página (0 tarjetas también cuenta) y ya no muere por watchdog;
    la prueba en vivo obtiene al menos 1 oferta y supera la puerta de calidad;
    tests en verde.

- [ ] **T-35 — Reparar Glassdoor (playbook §6.5)** (ciclo completo)
  - **RF:** RF-1–RF-12.
  - **Hecho cuando:** el registro de `repair/glassdoor-<fecha>` queda probado;
    el scraper emite latido de progreso durante las descripciones y respeta un
    presupuesto declarado; la prueba en vivo termina sin `exit=75` con ofertas
    por encima del umbral y la descripción dentro del presupuesto; tests en
    verde.

## 9. Mejoras de completitud de fuentes no fallidas (opcionales)

Investigaciones secundarias del diagnóstico (RF-1). Se ejecutan solo si la
persona las selecciona, después del cierre de la 004 y con el mismo ciclo y las
mismas puertas que el grupo 8.

- [ ] **T-36 — Mejorar `stepstone_nl` (playbook §6.6)** (ciclo completo)
  - **RF:** RF-1, RF-7–RF-12.
  - **Hecho cuando:** el registro de `repair/stepstone_nl-<fecha>` documenta la
    auditoría de campos, extrae `salary`/`work_mode` (y `skills` si el portal
    los expone) del detalle, sube el porcentaje sobre una muestra
    representativa con evidencia, sin regresión y con tests en verde. Si el
    portal no publica un campo, no es fallo; la prioridad es la `description`
    completa.

- [ ] **T-37 — Mejorar `nvb` (playbook §6.6)** (ciclo completo)
  - **RF:** RF-1, RF-7–RF-12.
  - **Hecho cuando:** el registro de `repair/nvb-<fecha>` corrige el mapeo y la
    validación de `work_mode` contra la API (0 % con 55 inválidos), la prueba en
    vivo supera el umbral y la puerta de calidad, y los tests pasan.

- [ ] **T-38 — Mejorar `jobs_ch` (playbook §6.6)** (ciclo completo)
  - **RF:** RF-1, RF-7–RF-12.
  - **Hecho cuando:** el registro de `repair/jobs_ch-<fecha>` amplía la
    extracción del detalle (`salary`, `work_mode` y `skills` solo si el portal
    los publica; la `description` completa es la prioridad), mejora los
    porcentajes sobre una muestra representativa sin regresión, y los tests
    pasan.

## 10. Cierre de la spec

Este grupo se ejecuta **antes** que los grupos 8 y 9: cierra la 004; las
reparaciones posteriores usan el proceso ya fusionado en `main`.

- [ ] **T-39 — Documentar el comando de tests y las convenciones en AGENTS.md** (~15 min)
  - **RF:** RF-13.
  - **Depende de:** T-25.
  - **Hecho cuando:** `AGENTS.md` refleja (con el visto bueno previo de la
    persona) el comando del módulo de reparaciones, la convención de ramas
    `repair/` y la ubicación de los registros (`repairs/`).

- [ ] **T-40 — Ejecutar la suite completa y registrar la comprobación real** (~20 min)
  - **RF:** RF-1–RF-16.
  - **Depende de:** T-26–T-31, T-39.
  - **Hecho cuando:** `python -m pytest scrapers-pipeline/tests -q` pasa y el
    resultado de la reparación real de InfoJobs (rama, recuento, calidad,
    validación de la persona) queda registrado como evidencia de cierre, junto
    al listado de fuentes no reparadas aún con su playbook y su evidencia.
