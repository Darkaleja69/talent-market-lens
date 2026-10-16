- **Causa raíz atendida (doble):** (1) **Origen:** los runs anteriores al
  18-09-2026 tuvieron una fase de detalle parcialmente fallida que persistió
  filas solo-SERP; los intentos que terminaban **vacíos no dejaban rastro**
  (las 2 filas del 10-10 se intentaron y quedaron sin descripción sin ningún
  error ni aviso). (2) **Fijación:** el flujo solo reintenta el detalle de una
  oferta **si vuelve a aparecer en el SERP del combo** dentro de su ventana
  (7 días ES / 30 días IE-NL) y sus ~50 tarjetas; al caer fuera, las ~950 filas
  quedaron congeladas para siempre. Evidencia: `evidence/investigation-report.md`
  (§1, §3) y `evidence/run_2026-10-11_extract.log`.
- **Justificación de no-backfill:** la vía de recuperación del histórico
  (1 GET por `job_id` a `jobPosting/<id>` para 954 filas) está **deshabilitada
  por `robots.txt` y los TOS** de LinkedIn (`User-agent: *` → `Disallow: /`;
  User Agreement §8.2 y Crawling Terms prohíben el scraping sin permiso
  expreso). Evidencia: `evidence/robots_linkedin_extract_2026-10-11.txt`,
  `evidence/robots_linkedin_2026-10-11.txt` y
  `evidence/tos_linkedin_extract_2026-10-11.txt`. Cifras del snapshot (9686):
  **954 filas sin descripción, 950 con `scraped_at` ≤ 17-09-2026** (813 del
  17-09) según `evidence/parquet_analysis_2026-10-11.txt`. No se ejecutó
  ninguna petición nueva a rutas de ofertas; la decisión de solicitar permiso
  (`whitelist-crawl@linkedin.com`) o usar una vía licenciada queda para la
  persona (informe §8).
- **Garantía a partir de ahora:** ninguna descripción vacía pasa desapercibida
  (WARNING + contador `detail_desc_empty` + resumen del run) y las pendientes
  se reintentan **mientras su ventana siga abierta** (máx. 3 intentos,
  presupuesto 25/run por defecto), sin rutas/endpoints nuevos y con volumen
  acotado (≤ 25 GET/run al endpoint de detalle ya usado).
- **Módulos y funciones:**
  - `src/main.py`: `_scrape_one_local` registra cada detalle sin descripción
    (`log.warning`, `store.warning_inc("detail_desc_empty")`,
    `store.pending_detail_add`) y devuelve su contador; `_print_summary` imprime
    «Detalles sin descripcion (telemetria)». Nuevas `_pending_window_days`,
    `_parse_iso_utc`,     `_select_pending_for_retry` (pura: descripción vacía +
    ventana por ciudad + `attempts < 3` + más antigua primero + presupuesto) y
    `_retry_pending_details` (solo guest, tras el barrido de combos; limpia al
    inicio los pendientes ya recuperados por otra vía; éxito →
    `pending_detail_remove` + PROGRESS; fallo → `pending_detail_increment` +
    WARNING; bloqueo → checkpoint y aborto de la fase, sin incrementar;
    checkpoint final).
  - `src/store.py`: `detail_pending` en `state.json`
    (`{job_id: {first_seen, attempts}}`), carga retrocompatible y helpers
    `detail_pending`, `pending_detail_get/add/increment/remove`.
  - `src/parse_detail.py`: guarda de dígitos en
    `_extract_salary_from_description` (sin dígito no hay importe: fuera
    `", libra"`, `". €"`, `"., Euro"`) e inferencia multilingüe
    (`\bhybrid(e|es)?\b`; `\bthuiswerk(en)?\b` → remoto, con límites de palabra
    para no marcar compuestos como «thuiswerkvergoeding»).
  - `src/search.py`: ante HTTP 429 se honra `Retry-After` (segundos, tope
    60 s) y se reintenta de forma acotada (máx. 2) con backoff + jitter;
    agotado, devuelve `""` como antes. El ritmo no sube (≤ 30 req/min).
  - `tools/recompute_derived.py` (nuevo): recomputo local sin red de los
    derivados del snapshot (`salary_raw` vacío o sin dígito; `work_mode`
    vacío), con copia previa de `jobs.csv`/`jobs.parquet` a `backup-<ts>-...`,
    informe antes/después y recuento de filas invariante.
- **Dependencias nuevas:** ninguna (biblioteca estándar y stack existente;
  pytest solo para tests).
- **Nivel §6.0:** se mantiene el **nivel 1** (endpoint guest por HTTP y
  parseo) con ritmo humano (delays, ≤ 30 req/min); **sin escalar** a sesión
  navegada ni credenciales (la señal del run es 429 de ritmo, no un challenge).
  Sin solvers, proxies de pago ni rutas/endpoints nuevos; la extracción JSON-LD
  queda reservada a una segunda ronda con fixture guest real.
- **Alcance de campos:** `salary` y `work_mode` se mejoran hasta lo que el
  portal exponga, **sin prometer el 100 %** (no siempre publica salario ni
  modalidad); `skills` es derivado de la descripción y no se exige como campo.
- **Recomputo local (una vez, sin red):** 9686 filas antes/después (sin
  cambios); `salary_raw` válido 1670 → 2339 (+669 completados), inválidos
  133 → 0 (limpiados); `work_mode` 4129 → 4752 (+623 recuperados). Copias
  previas en `data/output/backup-20261011_204449-jobs.*` (fuera de git).
  Evidencia: `evidence/derived-recompute-20261011.txt`.
- **Calidad (reconciliación de `quality_before.json`):** siguiendo el
  precedente de Indeed, `salary`, `work_mode` y `skills` quedan **sin meta**
  (`target_pct: null`) porque el portal no los publica de forma fiable y su
  ausencia no es fallo; se comparará la cobertura obtenida sin regresión
  (tolerancia 5 pp). `description` mantiene meta 100 % (obligatoria y foco) y
  `id`, `title`, `company`, `location` y `posted_date` siguen a 100 %.
  `context.json` conserva el brief original (metas teóricas al 100 %); el
  perfil efectivo de la puerta es el `quality_before.json` reconciliado.
- **Nota de proceso:** el registro se actualizó por edición directa de esta
  sección porque `python -m repair.cli record --record ... --changes ...`
  exige también `--status` y `--result` (estados finales) y la prueba en vivo
  aún no se ha ejecutado; el texto íntegro de cambios y tests queda en
  `evidence/record-changes-20261011.md` y `evidence/record-tests-20261011.md`
  para la completación final tras la prueba en vivo.
