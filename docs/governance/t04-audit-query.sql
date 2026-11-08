-- =============================================================================
-- Talent Market Lens - spec 012, T-04 (prepared for T-08)
-- Governance audit: who changed a Unity Catalog grant or an object owner.
--
-- STATUS: PREPARED, NOT EXECUTED.
--   This file is not evidence. It is run once inside the single SQL session of
--   T-08, grouped with the quality profiles, so it adds no warehouse wake-up
--   and no cost. Never open a second session to run it.
--
-- SOURCE: system.access.audit (Public Preview).
--   Schema, service and action names checked against the official reference on
--   2026-11-06:
--     https://learn.microsoft.com/en-us/azure/databricks/admin/system-tables/audit-logs
--     https://learn.microsoft.com/en-us/azure/databricks/admin/account-settings/audit-logs
--   Unity Catalog events: service_name = 'unityCatalog'. The same page states
--   that this service is NOT delivered through Azure diagnostic settings: if
--   the system table is unavailable there is no historical substitute, only
--   the timestamped CLI/API captures (see block 4).
--
-- WHAT IT ANSWERS
--   1. Which principal (actor) granted/revoked a privilege or changed the
--      owner of a project object, when, and with what request/response.
--   2. Which actor changed the ACL of the project jobs (service_name = 'jobs').
--   3. Whether the table is available at all (block 0), so the session can
--      stop early instead of burning the window on retries.
--
-- PARAMETERS (Databricks SQL editor: bind them before running; in a scripted
-- notebook replace each :name with a literal):
--   :start_date    first event_date of the window, inclusive  e.g. DATE'2026-10-07'
--   :end_date      last  event_date of the window, inclusive  e.g. DATE'2026-11-06'
--   :workspace_id  this workspace id as a string            e.g. '7405619889654446'
--   :principal     optional principal, e.g. 'account users' or '<sp-elt>'
--
-- CAVEATS AND [NO VERIFICADO] (cannot be confirmed without executing):
--   * request_params is map<string,string> and the key names depend on the
--     action: `securable_full_name` for updatePermissions, `full_name_arg` for
--     updateSchema/updateTables, `name_arg` for updateCatalog. The query
--     coalesces them; a row may still expose no name. [NO VERIFICADO] until run.
--   * Unity Catalog events are account-level: workspace_id is '0' and the
--     originating workspace is expected in request_params['workspace_id'].
--     That every event carries it is [NO VERIFICADO].
--   * Ownership: `changeSecurableOwner` belongs to the legacy Hive metastore
--     service (`sqlPermissions`), NOT to Unity Catalog. For Unity Catalog the
--     transfer is expected inside an `update*` action with an `owner` key
--     (documented for tables, volumes, credentials and external locations; NOT
--     listed for catalogs/schemas). The exact action/key for catalog and schema
--     ownership is [NO VERIFICADO]; block 1 keeps the whole `update*` family and
--     the `owner IS NOT NULL` condition so the evidence shows what is really
--     emitted.
--   * `response` is rendered with to_json() on purpose: this file does not
--     depend on the struct field names (statusCode/errorMessage in the docs vs
--     a snake_case variant). [NO VERIFICADO] until seen in the output.
--   * Retention, row counts and permissions on system.access.audit in this
--     workspace are [NO VERIFICADO] (T-04 did not execute SQL).
-- =============================================================================


-- =============================================================================
-- BLOCK 0 - AVAILABILITY PREFLIGHT (cheap; run first)
-- Expected: one row. If this fails with TABLE_OR_VIEW_NOT_FOUND or
-- PERMISSION_DENIED, record [NO VERIFICADO], skip blocks 1-3 and use block 4.
-- =============================================================================
SELECT count(*) AS audit_rows_last_day
FROM system.access.audit
WHERE event_date >= current_date() - 1;


-- =============================================================================
-- BLOCK 1 - UNITY CATALOG GRANTS AND OWNERSHIP ON PROJECT OBJECTS
-- One row per change, newest first. `changes_json` is the raw PATCH body
-- (principal + add/remove) and `new_owner` the owner key, when present.
-- The optional principal filter is commented: enable it to answer "what did
-- this principal receive or lose?" without a second query.
-- =============================================================================
WITH governance_changes AS (
  SELECT
    event_time,
    event_date,
    service_name,
    action_name,
    user_identity.email                        AS actor_email,
    user_identity.subject_name                 AS actor_subject_name,
    identity_metadata.run_as                   AS authorization_identity,
    request_params['workspace_id']             AS originating_workspace_id,
    request_params['metastore_id']             AS metastore_id,
    request_params['securable_type']           AS securable_type,
    coalesce(
      request_params['securable_full_name'],
      request_params['full_name_arg'],
      request_params['name_arg'],
      request_params['name']
    )                                          AS securable_name,
    request_params['changes']                  AS changes_json,
    request_params['owner']                    AS new_owner,
    to_json(response)                          AS response_json
  FROM system.access.audit
  WHERE event_date BETWEEN :start_date AND :end_date
    AND service_name = 'unityCatalog'
    AND action_name IN (
      'updatePermissions',
      'updateCatalog', 'updateSchema', 'updateTables',
      'updateExternalLocation', 'updateStorageCredential', 'updateCredential',
      'updateVolume', 'updateFunction', 'updateRegisteredModel'
    )
    AND (
      request_params['workspace_id'] = :workspace_id
      OR workspace_id = :workspace_id
    )
    AND (
      request_params['changes'] IS NOT NULL
      OR request_params['owner'] IS NOT NULL
    )
)
SELECT
  event_time,
  action_name,
  securable_type,
  securable_name,
  actor_email,
  authorization_identity,
  changes_json,
  new_owner,
  response_json,
  originating_workspace_id,
  metastore_id
FROM governance_changes
WHERE (
      securable_name LIKE 'job_offers%'       -- catalogs, schemas and tables
   OR securable_name LIKE 'landing-loc%'      -- external location / credentials
   OR lower(securable_type) = 'metastore'     -- metastore-level grants (casing
                                              -- [NO VERIFICADO]; lower() is safe)
)
--   AND changes_json LIKE '%' || :principal || '%'   -- optional principal filter
ORDER BY event_time DESC;


-- =============================================================================
-- BLOCK 2 - GRANTS RECEIVED OR LOST BY ONE PRINCIPAL (parameter :principal)
-- Uses request_params['changes'] as the filter, which is the JSON body of the
-- permissions PATCH. If the JSON is shaped differently, read block 1 raw and
-- filter by eye; do not open another session.
-- =============================================================================
SELECT
  event_time,
  action_name,
  request_params['securable_type'] AS securable_type,
  coalesce(
    request_params['securable_full_name'],
    request_params['full_name_arg'],
    request_params['name_arg']
  )                                AS securable_name,
  user_identity.email              AS actor_email,
  identity_metadata.run_as         AS authorization_identity,
  request_params['changes']        AS changes_json,
  to_json(response)                AS response_json
FROM system.access.audit
WHERE event_date BETWEEN :start_date AND :end_date
  AND service_name = 'unityCatalog'
  AND action_name = 'updatePermissions'
  AND request_params['workspace_id'] = :workspace_id
  AND request_params['changes'] LIKE '%' || :principal || '%'
ORDER BY event_time DESC;


-- =============================================================================
-- BLOCK 3 - JOB ACL CHANGES (service_name = 'jobs')
-- changeJobAcl / resetJobAcl document request_params.resourceId and
-- request_params.grants. The exact value of resourceId for a job is
-- [NO VERIFICADO]; the LIKE on the raw map is the fallback that always works.
-- =============================================================================
SELECT
  event_time,
  action_name,
  user_identity.email                       AS actor_email,
  identity_metadata.run_as                  AS authorization_identity,
  request_params['resourceId']              AS resource_id,
  request_params['targetUserId']            AS target_principal_id,
  request_params['aclPermissionSet']        AS acl_permission_set,
  request_params['grants']                  AS grants_json,
  to_json(response)                         AS response_json
FROM system.access.audit
WHERE event_date BETWEEN :start_date AND :end_date
  AND service_name = 'jobs'
  AND action_name IN ('changeJobAcl', 'resetJobAcl')
  AND (
    to_json(request_params) LIKE '%916863262593632%'   -- prod job
    OR to_json(request_params) LIKE '%320947022571947%' -- dev job
    OR to_json(request_params) LIKE '%704156530681034%' -- paused rollback job
  )
ORDER BY event_time DESC;


-- =============================================================================
-- BLOCK 4 - ALTERNATIVE IF system.access.audit IS NOT AVAILABLE
-- Do NOT open another session and do NOT invent rows. Record [NO VERIFICADO]
-- and keep the timestamped CLI/API evidence, which proves the current state
-- (not the history):
--
--   databricks grants get table job_offers_dev.ops.run_metrics --profile vs_code -o json
--   databricks grants get schema job_offers_dev.ops --profile vs_code -o json
--   databricks grants get catalog job_offers_dev --profile vs_code -o json
--   databricks grants get-effective table job_offers_dev.ops.run_metrics \
--     --principal "account users" --profile vs_code -o json
--   databricks jobs get-permissions 916863262593632 --profile vs_code -o json
--
-- Current-state complement inside the same SQL session (not historical).
-- SHOW GRANTS lists inherited, denied and granted privileges (SQL reference),
-- so it is closer to `get-effective` than to `grants get`:
--   SHOW GRANTS ON TABLE job_offers_dev.ops.run_metrics;
--   SHOW GRANTS ON SCHEMA job_offers_dev.ops;
--   SHOW GRANTS ON CATALOG job_offers_dev;
--   SHOW GRANTS `account users` ON TABLE job_offers_dev.ops.run_metrics;
--
-- Fallback rules for T-08:
--   * Any failure in block 0 => mark "auditoría: [NO VERIFICADO]" in
--     evidence.md and stop this file. No second SQL session.
--   * A failure in block 1-3 after block 0 succeeded => save the error text
--     and the query id, mark only that block [NO VERIFICADO].
--   * Never paste tokens, SAS or full request bodies with credentials into the
--     evidence: keep actor, action, object, principal and add/remove only.
-- =============================================================================
