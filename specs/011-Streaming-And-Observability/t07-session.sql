-- =====================================================================
-- T-07 (spec 011) — sesión SQL única: system.* y Query Profile
--
-- Guion de ejecución de la persona. Es una VARIANTE FUNCIONAL CON
-- LITERALES de las consultas versionadas en docs/RUNBOOK.md §9.8.2 /
-- docs/es/RUNBOOK.md §9.6.2: usa `job_id IN (...)` para cubrir los dos
-- jobs del proyecto en una sola consulta (el RUNBOOK parametriza con
-- :job_id) y reordena alguna columna (job_id/run_id antes de
-- trigger_type). No es copia literal del manual. El RUNBOOK usa ya el
-- mismo literal de workspace (7405619889654446) en las tres consultas.
-- Si una consulta se corrige aquí, se corrige también en el RUNBOOK
-- (misma tarea, T-07).
--
-- Workspace: 7405619889654446 (adb-7405619889654446.6.azuredatabricks.net)
--   VERSIÓN EJECUTADA (2026-11-03): este es el guion que se ejecutó tal cual.
--   Las consultas de este fichero filtran por el id LITERAL porque el editor
--   de la persona no admite current_workspace_id() ([UNRESOLVED_ROUTINE],
--   07:46:39). Tras la sesión, el RUNBOOK pasó también al literal en las tres
--   consultas (docs/RUNBOOK.md §9.8.2 y docs/es/RUNBOOK.md §9.6.2), con nota
--   de que el literal es específico de este workspace.
--   Si `workspace_id` no fuese numérico: CAST(workspace_id AS STRING) = '7405619889654446'.
--
-- Warehouse: Serverless Starter Warehouse (2X-Small)
--            id 53d8e3e91af6749c · auto_stop_mins 10
-- Coste: una sola ventana de 10 minutos facturables ≈ 0,43 €.
--        Todo lo de este fichero va EN LA MISMA VENTANA.
--
-- Ids de referencia:
--   dev  = 320947022571947
--   prod = 916863262593632
--   último run de producción = 317888374535056
--     (ONE_TIME, SUCCESS, 2026-10-31 18:15:53 → 18:28:17 = 744 s)
--     No hay run programado del 2026-11-01: el job estaba PAUSED y se
--     repuso el 2026-11-02; el siguiente es el domingo 2026-11-08.
--
-- Antes y después (PowerShell, 0 €: son metadatos y no despiertan nada):
--   databricks warehouses list --profile vs_code
--   databricks warehouses stop 53d8e3e91af6749c --profile vs_code
--   databricks warehouses list --profile vs_code
--
-- Entorno: SQL editor (NO un notebook sobre clúster all-purpose: eso es
-- otro compute y otro coste).
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1) Runs de dev y prod de los últimos 30 días, con duración real.
--    Criterio: devuelve filas de los dos jobs del proyecto.
--    Contraste: el run del 31-oct debe dar run_seconds ≈ 744.
--    Nota: las columnas *_duration_seconds de esta tabla son 0 en jobs
--    multi-tarea; por eso la duración se suma sobre las rebanadas.
-- ---------------------------------------------------------------------
SELECT job_id,
       run_id,
       trigger_type,
       max_by(result_state, period_end_time)     AS result_state,
       max_by(termination_code, period_end_time) AS termination_code,
       min(period_start_time) AS run_start,
       max(period_end_time)   AS run_end,
       sum(unix_timestamp(period_end_time) - unix_timestamp(period_start_time)) AS run_seconds
FROM system.lakeflow.job_run_timeline
WHERE workspace_id = 7405619889654446
  AND job_id IN (320947022571947, 916863262593632)
  AND period_start_time >= current_timestamp() - INTERVAL 30 DAYS
GROUP BY run_id, job_id, trigger_type
ORDER BY run_start DESC;


-- ---------------------------------------------------------------------
-- 1b) Retención y alcance de la tabla (cierra el [NO VERIFICADO] de
--     "retención" de design-observability.md §1.2).
--     La LATENCIA no se puede medir sin un run fresco: queda para
--     T-08/T-13 y se documenta como tal.
-- ---------------------------------------------------------------------
SELECT count(*)               AS rows_all,
       count(DISTINCT job_id) AS jobs,
       count(DISTINCT run_id) AS runs,
       min(period_start_time) AS oldest_period,
       max(period_end_time)   AS newest_period
FROM system.lakeflow.job_run_timeline
WHERE workspace_id = 7405619889654446;


-- ---------------------------------------------------------------------
-- 2) Tareas del último run de producción.
--    Debe salir con 8 tareas: ese run es anterior al deploy de la
--    novena (Publish_Run_Summary). Confirma el uso de job_run_id (en
--    esta tabla run_id es el run de la TAREA) y que las duraciones por
--    tarea NO son 0 (corrección de T-06).
-- ---------------------------------------------------------------------
SELECT task_key,
       result_state,
       setup_duration_seconds,
       execution_duration_seconds,
       cleanup_duration_seconds,
       period_start_time,
       period_end_time
FROM system.lakeflow.job_task_run_timeline
WHERE workspace_id = 7405619889654446
  AND job_run_id = 317888374535056
ORDER BY period_start_time;


-- ---------------------------------------------------------------------
-- 3) DBUs por run y SKU.
--    Lo que se valida: que usage_metadata.job_run_id existe y trae el
--    run. Ojo: job_id aquí es texto; job_run_id también.
-- ---------------------------------------------------------------------
SELECT usage_metadata.job_id     AS job_id,
       usage_metadata.job_run_id AS job_run_id,
       sku_name,
       sum(usage_quantity)       AS dbus
FROM system.billing.usage
WHERE workspace_id = 7405619889654446
  AND usage_date >= current_date() - INTERVAL 30 DAYS
  AND usage_metadata.job_id IN ('320947022571947', '916863262593632')
GROUP BY usage_metadata.job_id, usage_metadata.job_run_id, sku_name
ORDER BY dbus DESC;


-- ---------------------------------------------------------------------
-- 4) Columnas reales de list_prices (metadatos, misma sesión) para el
--    join DBU → € que T-06 dejó como [NO VERIFICADO]. Ajusta la 5 si
--    los nombres difieren.
-- ---------------------------------------------------------------------
DESCRIBE TABLE system.billing.list_prices;


-- ---------------------------------------------------------------------
-- 5) Coste del último run de producción en la moneda de la cuenta.
--    price_end_time IS NULL = precio vigente.
-- ---------------------------------------------------------------------
WITH u AS (
  SELECT usage_metadata.job_run_id AS job_run_id,
         sku_name,
         sum(usage_quantity)       AS dbus
  FROM system.billing.usage
  WHERE workspace_id = 7405619889654446
    AND usage_date >= current_date() - INTERVAL 30 DAYS
    AND usage_metadata.job_run_id = '317888374535056'
  GROUP BY usage_metadata.job_run_id, sku_name
), p AS (
  SELECT sku_name,
         max_by(pricing.default, price_start_time) AS price_per_dbu,
         max_by(currency_code, price_start_time)   AS currency_code
  FROM system.billing.list_prices
  WHERE price_end_time IS NULL
  GROUP BY sku_name
)
SELECT u.job_run_id, u.sku_name, u.dbus, p.price_per_dbu, p.currency_code,
       u.dbus * p.price_per_dbu AS amount
FROM u
LEFT JOIN p USING (sku_name)
ORDER BY amount DESC;


-- =====================================================================
-- Qué hay que traer de vuelta (para cerrar T-07)
--
--   1. Hora de inicio y de fin de la sesión (reloj de pared).
--   2. Consulta 1: filas de dev y prod con run_seconds.
--   3. Consulta 1b: oldest_period / newest_period.
--   4. Consulta 2: task_key + duraciones (¿alguna a 0?).
--   5. Consulta 3: SKUs y DBUs del run 317888374535056.
--   6. Consultas 4 y 5: columnas de list_prices e importe del run.
--   7. Captura del Query Profile de la consulta 1
--      (evidence/t07-query-profile.png en esta carpeta).
--   8. Desviaciones: errores de tipos, columnas inexistentes, vacíos.
--
-- Saneado: sin correos, sin SAS, sin rutas locales. Los ids de job y
-- de run ya están en el repositorio y no hace falta ocultarlos.
--
-- Criterio de cierre: las tres consultas versionadas devuelven filas de
-- los jobs del proyecto, el coste por run del último run de producción
-- está registrado, el Query Profile está capturado y el warehouse queda
-- STOPPED antes y después.
-- =====================================================================
