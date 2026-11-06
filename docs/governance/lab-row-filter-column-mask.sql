-- =============================================================================
-- Talent Market Lens - spec 012, T-03
-- Row filter and column mask lab on a synthetic offers table.
--
-- Scope
--   * Unity Catalog only. Run in Databricks Free Edition (primary route) or in
--     the course lab (fallback). Never in the paid project workspace.
--   * 0 EUR. No real data, no production objects, no dashboard, no real emails:
--     every address ends in @example.com and every site is a fictitious board.
--   * Every object lives under `governance_lab` and is removed in block 6.
--
-- BEFORE YOU RUN
--   * This script was NOT executed when it was written (no Free Edition
--     credentials on this machine). It is a lab script, not evidence.
--   * Replace the single placeholder `<catalog>` with the catalog returned by
--     `SHOW CATALOGS` BEFORE running anything. Use find/replace "all": it
--     appears in every block. Free Edition often exposes `workspace`; the
--     course lab may expose another name.
--   * Each block starts with `USE CATALOG <catalog>;` because each profile uses
--     its own session. Run the blocks in order, statement by statement, and
--     fill the evidence table of `lab-row-filter-column-mask.md` with what you
--     actually see (including the query ID from the SQL editor history).
--   * Route B also needs two real addresses, only in your local copy (never
--     commit them). Route A needs account groups, which SQL cannot create.
--
-- Blocks
--   0A  Capability preflight: identity, catalog, groups         ~20 s
--   0B  Lab schema, table and synthetic rows                    ~10 s
--   0C  Decisive preflight: group grant and policies            ~30 s
--   1   Profiles: A) account groups  B) two existing users
--   2   Base grants and grant inheritance (schema -> table)
--   3   Policy functions and their EXECUTE grants
--   4   Apply the row filter (location) and the mask (contact_email)
--   5   Verification queries, run once per profile
--   6   Cleanup, zero-grant check and post-cleanup verification
--   7   Incompatibility notes (input for T-11)
--
-- Reference: docs.databricks.com/aws/en/data-governance/unity-catalog/
--            filters-and-masks/ (last updated 2026-10-09)
-- =============================================================================


-- =============================================================================
-- BLOCK 0A - CAPABILITY PREFLIGHT
-- Answers: who am I, which runtime, which catalog, which groups exist, and can
-- this workspace create a group at all?
--
-- If you do not know the catalog yet, run `SHOW CATALOGS;` once, replace
-- <catalog> in the whole file, and then run this block from the top.
-- =============================================================================

USE CATALOG <catalog>;

-- 0A.1 Identity and runtime. Save this output: it is evidence row 1.
SELECT current_user()                        AS principal,
       current_catalog()                     AS current_catalog,
       current_version().dbsql_version       AS dbsql_version,
       current_version().dbr_version         AS dbr_version;

-- 0A.2 Catalog evidence. The chosen catalog must be one where you can create a
-- schema and grant USE SCHEMA.
SHOW CATALOGS;

-- 0A.3 Lab groups already present. On a first run this returns 0 rows.
SHOW GROUPS LIKE '*gov_lab*';

-- 0A.4 Which groups does the current user belong to, and are they account
-- groups (usable by Unity Catalog) or workspace-local ones?
-- Replace the address with your own login (paste it; do not commit it).
SHOW GROUPS WITH USER `paste-your-login@example.com`;

-- 0A.5 Controlled CREATE GROUP probe.
-- Expected result in a standard workspace: the statement succeeds, but it
-- creates a WORKSPACE-LOCAL group. Block 0C.1 is the decisive test: Unity
-- Catalog can only be granted privileges on ACCOUNT groups.
-- Re-run only: if a previous run left the probe group, uncomment and run the
-- DROP GROUP line first (it has no IF EXISTS), then CREATE GROUP again.
-- DROP GROUP gov_lab_probe;
CREATE GROUP gov_lab_probe;
SHOW GROUPS LIKE 'gov_lab_probe';


-- =============================================================================
-- BLOCK 0B - LAB SCHEMA, TABLE AND SYNTHETIC ROWS
-- =============================================================================

USE CATALOG <catalog>;

CREATE SCHEMA IF NOT EXISTS governance_lab
  COMMENT 'Synthetic lab for row filters and column masks (spec 012, T-03). No real data.';

-- Re-run safety: a previous pass may have left the route B mapping table. Drop
-- it BEFORE the schema-level SELECT test (case 1b), so an old mapping table is
-- never exposed to the restricted profile through `GRANT SELECT ON SCHEMA`.
DROP TABLE IF EXISTS governance_lab.access_profiles;

-- Restarting the lab from scratch: this drops the table and its policies.
DROP TABLE IF EXISTS governance_lab.offers;

CREATE TABLE governance_lab.offers (
  job_id        STRING NOT NULL,
  site          STRING,
  title         STRING,
  company_name  STRING,
  location      STRING,
  salary_min    INT,
  salary_max    INT,
  contact_email STRING
)
COMMENT 'Synthetic job offers: 8 rows, 2 locations, 2 companies, valid salary ranges.';

INSERT INTO governance_lab.offers
  (job_id, site, title, company_name, location, salary_min, salary_max, contact_email)
VALUES
  ('lab-001', 'lab-board-a', 'Data Engineer',             'Acme Analytics', 'Madrid',    38000, 52000, 'contact.lab001@example.com'),
  ('lab-002', 'lab-board-a', 'Analytics Engineer',        'Beta Cloud',     'Barcelona', 42000, 55000, 'contact.lab002@example.com'),
  ('lab-003', 'lab-board-b', 'BI Developer',              'Acme Analytics', 'Barcelona', 34000, 46000, 'contact.lab003@example.com'),
  ('lab-004', 'lab-board-b', 'Data Analyst',              'Beta Cloud',     'Madrid',    28000, 36000, 'contact.lab004@example.com'),
  ('lab-005', 'lab-board-a', 'Platform Engineer',         'Acme Analytics', 'Madrid',    50000, 68000, 'contact.lab005@example.com'),
  ('lab-006', 'lab-board-b', 'Data Scientist',            'Beta Cloud',     'Barcelona', 47000, 62000, 'contact.lab006@example.com'),
  ('lab-007', 'lab-board-a', 'Machine Learning Engineer', 'Acme Analytics', 'Barcelona', 55000, 72000, 'contact.lab007@example.com'),
  ('lab-008', 'lab-board-b', 'Junior Data Analyst',       'Beta Cloud',     'Madrid',    24000, 30000, 'contact.lab008@example.com');

-- Baseline without any policy: 8 rows, original company names and emails.
SELECT COUNT(*) AS visible_rows FROM governance_lab.offers;
SELECT * FROM governance_lab.offers ORDER BY job_id;


-- =============================================================================
-- BLOCK 0C - DECISIVE PREFLIGHT
-- =============================================================================

USE CATALOG <catalog>;

-- 0C.1 Can Unity Catalog grant a data privilege to the group created in 0A.5?
--   SUCCESS -> groups created in this workspace are UC-usable: route A is open.
--   ERROR containing "workspace-local" (or any grant error) -> route A is NOT
--   possible here: run block 0C.3 to drop the probe group and use route B (two
--   existing users) or the course lab (plan B).
-- SELECT on the lab table is the decisive privilege: a group that cannot read
-- the table is useless for this demo even if it can see the schema.
GRANT SELECT ON TABLE governance_lab.offers TO gov_lab_probe;
SHOW GRANTS gov_lab_probe ON TABLE governance_lab.offers;

-- 0C.2 Do row filters and column masks work in this workspace?
-- These functions do not depend on the caller: the probe only proves that the
-- mechanism is available on this compute (not that two profiles exist).
DROP TABLE IF EXISTS governance_lab.probe_offers;
CREATE TABLE governance_lab.probe_offers (location STRING, contact_email STRING);
INSERT INTO governance_lab.probe_offers
VALUES ('Madrid', 'probe1@example.com'), ('Barcelona', 'probe2@example.com');

CREATE OR REPLACE FUNCTION governance_lab.probe_filter(location STRING)
RETURNS BOOLEAN
RETURN location = 'Madrid';

CREATE OR REPLACE FUNCTION governance_lab.probe_mask(contact_email STRING)
RETURNS STRING
RETURN '***';

ALTER TABLE governance_lab.probe_offers
  SET ROW FILTER governance_lab.probe_filter ON (location);
ALTER TABLE governance_lab.probe_offers
  ALTER COLUMN contact_email SET MASK governance_lab.probe_mask;

-- Expected: rows_visible_expected_1 = 1 and masked_value_expected_3_stars = ***
-- If you get 2 rows and the original email, the table owner may be exempt from
-- table-level policies in this workspace. That result is NOT conclusive: record
-- it and go to route B or plan B (guide case 5.4 and section 3).
SELECT COUNT(*) AS rows_visible_expected_1 FROM governance_lab.probe_offers;
SELECT DISTINCT contact_email AS masked_value_expected_3_stars
FROM governance_lab.probe_offers;

-- 0C.3 Probe cleanup. Run it ALWAYS, also when 0C.1 or 0C.2 failed. The REVOKE
-- may fail if the grant never happened: that error is expected; the DROP GROUP
-- must always run so no probe group is left behind.
REVOKE SELECT ON TABLE governance_lab.offers FROM gov_lab_probe;
DROP GROUP gov_lab_probe;
ALTER TABLE governance_lab.probe_offers DROP ROW FILTER;
ALTER TABLE governance_lab.probe_offers ALTER COLUMN contact_email DROP MASK;
DROP TABLE IF EXISTS governance_lab.probe_offers;
DROP FUNCTION IF EXISTS governance_lab.probe_filter;
DROP FUNCTION IF EXISTS governance_lab.probe_mask;
SHOW GROUPS LIKE 'gov_lab_probe';


-- =============================================================================
-- BLOCK 1A - PROFILE ROUTE A: TWO ACCOUNT GROUPS
--
-- Account groups can only be created in the ACCOUNT CONSOLE or with account
-- SCIM provisioning. "Settings > Identity and access > Groups" creates
-- WORKSPACE-LOCAL groups, and Unity Catalog rejects them (block 0C.1).
-- Free Edition has no account console (documented limitation), so route A is
-- NOT viable there: use route B with a second invited user, or plan B.
--
-- The account administrator creates both groups and adds the members:
--   gov_lab_authorized -> sees all 8 rows and the original contact_email
--   gov_lab_restricted -> sees only Barcelona and the masked email
--
-- A principal in both groups is treated as authorized (the filter checks the
-- authorized group first). This precedence is intentional.
-- =============================================================================

USE CATALOG <catalog>;

SHOW GROUPS LIKE 'gov_lab_*';


-- =============================================================================
-- BLOCK 1B - PROFILE ROUTE B: TWO EXISTING USERS
--
-- Use this route when there are two users in the workspace and no usable
-- account groups (the normal case in Free Edition when a second user has been
-- invited). Replace the two addresses below in YOUR LOCAL COPY only; never
-- commit real email addresses.
--
-- The mapping table is created in block 3B, after the inheritance test, so the
-- schema-level grant used in block 2 never exposes it.
-- =============================================================================

USE CATALOG <catalog>;

-- Route B principals (only in your local copy):
--   authorized.user@example.com
--   restricted.user@example.com


-- =============================================================================
-- BLOCK 2 - BASE GRANTS AND GRANT INHERITANCE
--
-- A row filter or a column mask never grants access: it only restricts a
-- principal that already has the base privileges. `USE CATALOG` is NOT granted
-- or revoked here: in the workspace catalog it is implicit for all users. The
-- inheritance test uses SELECT on the schema versus SELECT on the table.
--
-- Run the SELECT checks below as the named profile, in its own session, after
-- running `USE CATALOG <catalog>;` in that session.
-- =============================================================================

USE CATALOG <catalog>;

-- Route A (account groups).
GRANT USE SCHEMA ON SCHEMA governance_lab TO `gov_lab_authorized`;
GRANT USE SCHEMA ON SCHEMA governance_lab TO `gov_lab_restricted`;

-- CASE 1a - USE is not SELECT: with USE SCHEMA but no SELECT, both profiles
-- must get a permission error. Run as each profile:
--   SELECT COUNT(*) FROM governance_lab.offers;   -- expected PERMISSION_DENIED

-- CASE 1b - inheritance schema -> table. Grant SELECT on the schema to the
-- restricted profile only. It must see all 8 rows without a table-level grant.
GRANT SELECT ON SCHEMA governance_lab TO `gov_lab_restricted`;
-- As the restricted profile:
--   SELECT COUNT(*) FROM governance_lab.offers;   -- expected 8

-- CASE 1c - remove the inherited grant. The restricted profile must lose access
-- again, even though nothing changed on the table.
REVOKE SELECT ON SCHEMA governance_lab FROM `gov_lab_restricted`;
-- As the restricted profile:
--   SELECT COUNT(*) FROM governance_lab.offers;   -- expected PERMISSION_DENIED

-- CASE 1d - explicit table grant (the level the guide recommends). Both
-- profiles see all 8 rows again and this is the baseline for case 2.
GRANT SELECT ON TABLE governance_lab.offers TO `gov_lab_restricted`;
GRANT SELECT ON TABLE governance_lab.offers TO `gov_lab_authorized`;
-- As each profile:
--   SELECT COUNT(*) FROM governance_lab.offers;   -- expected 8

-- Route B equivalent (uncomment and replace the addresses in your local copy;
-- keep route A commented out if you use this one):
-- GRANT USE SCHEMA ON SCHEMA governance_lab TO `authorized.user@example.com`;
-- GRANT USE SCHEMA ON SCHEMA governance_lab TO `restricted.user@example.com`;
--   CASE 1a: both -> PERMISSION_DENIED
-- GRANT SELECT ON SCHEMA governance_lab TO `restricted.user@example.com`;
--   CASE 1b: restricted -> 8 rows
-- REVOKE SELECT ON SCHEMA governance_lab FROM `restricted.user@example.com`;
--   CASE 1c: restricted -> PERMISSION_DENIED
-- GRANT SELECT ON TABLE governance_lab.offers TO `restricted.user@example.com`;
-- GRANT SELECT ON TABLE governance_lab.offers TO `authorized.user@example.com`;
--   CASE 1d: both -> 8 rows

SHOW GRANTS ON SCHEMA governance_lab;
SHOW GRANTS ON TABLE governance_lab.offers;


-- =============================================================================
-- BLOCK 3A - POLICY FUNCTIONS, ROUTE A (GROUP MEMBERSHIP)
--
-- Run this block only for route A; skip it for route B (its functions are not
-- attached and block 6 drops them with IF EXISTS).
-- Pure SQL UDFs, no external UDFs, no table reads. The row filter restricts by
-- `location` and the mask hides `contact_email`. The only session-dependent
-- input is group membership, which is the point of the policy (and makes the
-- function non-deterministic: see block 7).
-- `is_account_group_member` reads ACCOUNT groups, not workspace-local groups.
-- =============================================================================

USE CATALOG <catalog>;

CREATE OR REPLACE FUNCTION governance_lab.gov_filter_location_group(location STRING)
RETURNS BOOLEAN
RETURN is_account_group_member('gov_lab_authorized')
       OR location = 'Barcelona';

CREATE OR REPLACE FUNCTION governance_lab.gov_mask_email_group(contact_email STRING)
RETURNS STRING
RETURN CASE
         WHEN is_account_group_member('gov_lab_authorized') THEN contact_email
         ELSE '***'
       END;

-- EXECUTE is part of the base privileges of the lab. The documentation requires
-- it to ASSIGN a policy and does not list it as a requirement to query (the
-- policy runs with the owner's rights). The optional case 7 of the guide checks
-- whether a reader can still query without EXECUTE.
GRANT EXECUTE ON FUNCTION governance_lab.gov_filter_location_group TO `gov_lab_authorized`;
GRANT EXECUTE ON FUNCTION governance_lab.gov_filter_location_group TO `gov_lab_restricted`;
GRANT EXECUTE ON FUNCTION governance_lab.gov_mask_email_group TO `gov_lab_authorized`;
GRANT EXECUTE ON FUNCTION governance_lab.gov_mask_email_group TO `gov_lab_restricted`;


-- =============================================================================
-- BLOCK 3B - POLICY FUNCTIONS, ROUTE B (PRIVATE MAPPING TABLE)
--
-- Run this block only for route B; skip it for route A (the mapping table and
-- the two functions are dropped in block 6 with IF EXISTS).
-- Same behaviour keyed by current_user(). The subqueries make the policy
-- incompatible with MERGE on this table (see block 7); that is acceptable for a
-- lab and is documented for T-11.
-- =============================================================================

USE CATALOG <catalog>;

DROP TABLE IF EXISTS governance_lab.access_profiles;

CREATE TABLE governance_lab.access_profiles (
  principal        STRING NOT NULL,
  can_see_all      BOOLEAN NOT NULL,
  allowed_location STRING
)
COMMENT 'Private access mapping read by the lab policies. Synthetic addresses only.';

INSERT INTO governance_lab.access_profiles VALUES
  ('authorized.user@example.com', TRUE,  NULL),
  ('restricted.user@example.com', FALSE, 'Barcelona');

CREATE OR REPLACE FUNCTION governance_lab.gov_filter_location_user(location STRING)
RETURNS BOOLEAN
RETURN COALESCE(
         (SELECT p.can_see_all
            FROM governance_lab.access_profiles p
           WHERE p.principal = current_user()),
         FALSE)
       OR location = (SELECT p.allowed_location
                        FROM governance_lab.access_profiles p
                       WHERE p.principal = current_user());

CREATE OR REPLACE FUNCTION governance_lab.gov_mask_email_user(contact_email STRING)
RETURNS STRING
RETURN CASE
         WHEN COALESCE(
                (SELECT p.can_see_all
                   FROM governance_lab.access_profiles p
                  WHERE p.principal = current_user()),
                FALSE)
           THEN contact_email
         ELSE '***'
       END;

-- ROUTE B EXECUTE grants. They are commented with the rest of route B: when you
-- choose route B, uncomment these four lines (block 3B is in the route B
-- checklist of the guide). Without them the base access and the optional case 7
-- are incomplete. If you use route A, leave them commented and skip block 3B.
-- GRANT EXECUTE ON FUNCTION governance_lab.gov_filter_location_user TO `authorized.user@example.com`;
-- GRANT EXECUTE ON FUNCTION governance_lab.gov_filter_location_user TO `restricted.user@example.com`;
-- GRANT EXECUTE ON FUNCTION governance_lab.gov_mask_email_user TO `authorized.user@example.com`;
-- GRANT EXECUTE ON FUNCTION governance_lab.gov_mask_email_user TO `restricted.user@example.com`;


-- =============================================================================
-- BLOCK 4 - APPLY THE POLICIES
--
-- Route A (default). Route B is commented right below: apply ONE route only.
-- There is one row filter per table and one mask per column; re-running the
-- statements replaces them.
-- =============================================================================

USE CATALOG <catalog>;

ALTER TABLE governance_lab.offers
  SET ROW FILTER governance_lab.gov_filter_location_group ON (location);
ALTER TABLE governance_lab.offers
  ALTER COLUMN contact_email SET MASK governance_lab.gov_mask_email_group;

-- Route B:
-- ALTER TABLE governance_lab.offers
--   SET ROW FILTER governance_lab.gov_filter_location_user ON (location);
-- ALTER TABLE governance_lab.offers
--   ALTER COLUMN contact_email SET MASK governance_lab.gov_mask_email_user;

-- Metadata proof: the policy is attached to the table (and to which function).
SELECT * FROM information_schema.row_filters
 WHERE schema_name = 'governance_lab';
SELECT * FROM information_schema.column_masks
 WHERE schema_name = 'governance_lab';


-- =============================================================================
-- BLOCK 5 - VERIFICATION QUERIES (RUN EACH BLOCK AS ITS PROFILE)
--
-- Open a separate browser session per profile (or a different SQL editor tab
-- signed in as that user), run `USE CATALOG <catalog>;` in each session, and
-- save the output plus the query ID as evidence.
-- =============================================================================

USE CATALOG <catalog>;

-- 5.1 Run as any profile: which branch of the policy applies to me?
-- In route B the group does not exist: both functions should return false.
SELECT current_user()                             AS principal,
       is_account_group_member('gov_lab_authorized') AS account_group_authorized,
       is_member('gov_lab_authorized')            AS workspace_group_authorized;

-- 5.2 Authorized profile: expected 8 rows, original company names and emails.
SELECT COUNT(*) AS visible_rows FROM governance_lab.offers;
SELECT job_id, site, title, company_name, location, salary_min, salary_max, contact_email
FROM governance_lab.offers
ORDER BY job_id;

-- 5.3 Restricted profile: expected 4 rows (Barcelona only), company names
-- original and contact_email = '***'.
SELECT COUNT(*) AS visible_rows FROM governance_lab.offers;
SELECT DISTINCT location FROM governance_lab.offers ORDER BY location;
SELECT job_id, site, title, company_name, location, salary_min, salary_max, contact_email
FROM governance_lab.offers
ORDER BY job_id;

-- 5.4 Owner view (admin, table creator): record exactly what you see, including
-- `sample_email` in this same capture (original vs '***'). The documentation
-- does not state an implicit exemption for the table owner, so this is an
-- observation, not an assumption. Two rows plus the original email is NOT a
-- pass: it means the preflight was not conclusive (route B/plan B).
SELECT current_user()                 AS principal,
       COUNT(*)                       AS visible_rows,
       COUNT(DISTINCT contact_email)  AS distinct_emails,
       MIN(contact_email)             AS sample_email
FROM governance_lab.offers;

-- 5.5 Negative control without SELECT (admin revokes, restricted re-runs,
-- admin re-grants). Expected: PERMISSION_DENIED / insufficient privileges,
-- even though the row filter exists.
--   ADMIN:      REVOKE SELECT ON TABLE governance_lab.offers FROM `gov_lab_restricted`;
--   RESTRICTED: SELECT COUNT(*) FROM governance_lab.offers;
--   ADMIN:      GRANT SELECT ON TABLE governance_lab.offers TO `gov_lab_restricted`;
--
-- Route B: replace the group by `restricted.user@example.com` in both lines.


-- =============================================================================
-- BLOCK 6 - CLEANUP
--
-- Apply the revokes of the route you actually used and comment the other one:
-- revoking from a principal that does not exist fails. If a policy was already
-- removed, `DROP ROW FILTER` and `DROP MASK` fail with "no row filter / no mask
-- defined": that error is expected and harmless.
-- =============================================================================

USE CATALOG <catalog>;

-- 6.1 Remove the policies.
ALTER TABLE governance_lab.offers DROP ROW FILTER;
ALTER TABLE governance_lab.offers ALTER COLUMN contact_email DROP MASK;

-- 6.2 Route A revokes (one principal per statement; GRANT/REVOKE take a single
-- principal). USE CATALOG is not revoked because it is implicit and never
-- granted by this script.
REVOKE SELECT ON TABLE governance_lab.offers FROM `gov_lab_authorized`;
REVOKE SELECT ON TABLE governance_lab.offers FROM `gov_lab_restricted`;
REVOKE EXECUTE ON FUNCTION governance_lab.gov_filter_location_group FROM `gov_lab_authorized`;
REVOKE EXECUTE ON FUNCTION governance_lab.gov_filter_location_group FROM `gov_lab_restricted`;
REVOKE EXECUTE ON FUNCTION governance_lab.gov_mask_email_group FROM `gov_lab_authorized`;
REVOKE EXECUTE ON FUNCTION governance_lab.gov_mask_email_group FROM `gov_lab_restricted`;
REVOKE USE SCHEMA ON SCHEMA governance_lab FROM `gov_lab_authorized`;
REVOKE USE SCHEMA ON SCHEMA governance_lab FROM `gov_lab_restricted`;

-- Route B revokes (only if route B was used; replace addresses in your copy).
-- REVOKE SELECT ON TABLE governance_lab.offers FROM `authorized.user@example.com`;
-- REVOKE SELECT ON TABLE governance_lab.offers FROM `restricted.user@example.com`;
-- REVOKE EXECUTE ON FUNCTION governance_lab.gov_filter_location_user FROM `authorized.user@example.com`;
-- REVOKE EXECUTE ON FUNCTION governance_lab.gov_filter_location_user FROM `restricted.user@example.com`;
-- REVOKE EXECUTE ON FUNCTION governance_lab.gov_mask_email_user FROM `authorized.user@example.com`;
-- REVOKE EXECUTE ON FUNCTION governance_lab.gov_mask_email_user FROM `restricted.user@example.com`;
-- REVOKE USE SCHEMA ON SCHEMA governance_lab FROM `authorized.user@example.com`;
-- REVOKE USE SCHEMA ON SCHEMA governance_lab FROM `restricted.user@example.com`;

-- 6.3 CLOSURE CRITERION: zero explicit grants before dropping objects.
-- Both queries must return 0 rows. If any row appears, revoke it and repeat.
SHOW GRANTS ON TABLE governance_lab.offers;
SHOW GRANTS ON SCHEMA governance_lab;

-- 6.4 Drop the objects (only after 6.3 is clean).
DROP FUNCTION IF EXISTS governance_lab.gov_filter_location_group;
DROP FUNCTION IF EXISTS governance_lab.gov_mask_email_group;
DROP FUNCTION IF EXISTS governance_lab.gov_filter_location_user;
DROP FUNCTION IF EXISTS governance_lab.gov_mask_email_user;
DROP TABLE IF EXISTS governance_lab.access_profiles;
DROP TABLE IF EXISTS governance_lab.offers;
DROP SCHEMA IF EXISTS governance_lab CASCADE;

-- 6.5 Post-cleanup verification: 0 rows in the three queries.
SHOW SCHEMAS IN <catalog> LIKE 'governance_lab';
SELECT * FROM information_schema.row_filters
 WHERE schema_name = 'governance_lab';
SELECT * FROM information_schema.column_masks
 WHERE schema_name = 'governance_lab';

-- Account groups (route A) are deleted in the account console/SCIM. DROP GROUP
-- only deletes workspace-local groups; if block 0C.1 proved this workspace
-- accepts the created group, drop it here (it has no IF EXISTS: an error means
-- the group is not there).
-- DROP GROUP gov_lab_authorized;
-- DROP GROUP gov_lab_restricted;


-- =============================================================================
-- BLOCK 7 - INCOMPATIBILITY NOTES (INPUT FOR T-11)
--
-- Documented limitations of table-level row filters and column masks:
--   * `is_account_group_member`, `current_user` and `session_user` are not
--     deterministic. Policies that contain non-deterministic functions are not
--     compatible with MERGE. Route A uses group membership and route B uses
--     subqueries, so neither is verified as MERGE-compatible here.
--   * A policy cannot reference a table that has an active row filter or column
--     mask. The route B mapping table must stay policy-free.
--   * Time travel is not supported on tables with table-level filters/masks.
--   * Deep and shallow clones are not supported.
--   * Delta Lake APIs and path-based access to the table files are not
--     supported.
--   * You cannot attach filters/masks to a view.
--   * DELETE/UPDATE/MERGE on a partitioned table whose partition column has a
--     policy requires DBR 17.2+.
--   * Dedicated access compute requires DBR 15.4 LTS+ and serverless compute;
--     reads on 15.4-16.2, writes (INSERT/UPDATE/DELETE) only on 16.3+ and
--     through supported patterns such as MERGE INTO.
--   * REPLACE TABLE retains existing row filters and column masks.
--   * A filter/mask does not grant access: GRANT is still required.
--   * Only one row filter per table and one mask per column.
--   * The table owner can remove its own policy: table-level filters do not
--     separate duties. Use ABAC when the owner must not be able to bypass it.
-- =============================================================================
