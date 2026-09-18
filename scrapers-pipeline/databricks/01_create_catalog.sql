-- =============================================================================
--  01_create_catalog.sql
--  FASE 6 del pipeline: crear el Catalog 'scraper_pipeline' y los 3 schemas
--  (bronze, silver, gold) en Unity Catalog.
--
--  Ejecutar en: Databricks Workspace -> SQL Editor (o un Notebook en celda SQL).
--
--  Storage account: <storage-account>
--  Contenedor:      landing
--  Region:          westeurope
-- =============================================================================

-- -----------------------------------------------------------------------------
-- PRE-REQUISITO: crear la carpeta 'managed/' en el contenedor 'landing' de tu
-- storage account. Puedes hacerlo desde Azure Portal:
--   Storage account '<storage-account>' -> Containers -> 'landing' -> '+ Add directory'
--   Nombre: managed
-- O con Azure Storage Explorer, o con azcopy subiendo un fichero vacio a
--   landing/managed/.keep
--
-- Por que una carpeta 'managed' separada de 'landing/':
--   - 'landing/' contiene los parquets crudos que sube tu PC via azcopy.
--   - 'managed/' es donde Databricks guarda las tablas Delta que EL administra
--     (bronze.*, silver.*). Solo Databricks deberia escribir ahi.
--   - Mantenerlos separados evita que Autoloader escanee accidentalmente
--     archivos Delta internos de sus tablas.
-- -----------------------------------------------------------------------------

-- 1) Crear el CATALOG (namespace top-level del proyecto)
--    Sintaxis: CREATE CATALOG <nombre> MANAGED LOCATION '<url abfss>'
--    NOTA: NO pongas ';' antes del MANAGED LOCATION (es parte de la misma sentencia).
CREATE CATALOG IF NOT EXISTS scraper_pipeline
    COMMENT 'Pipeline de ofertas de empleo desde scrapers (indeed, linkedin, infojobs, multi_site)'
    MANAGED LOCATION 'abfss://landing@<storage-account>.dfs.core.windows.net/managed/';

-- Verificar
DESCRIBE CATALOG scraper_pipeline;

-- 2) Crear los SCHEMAS (equivalente a "bases de datos" dentro del catalog)
--    Cada tabla Delta creada sin LOCATION explicita ira a
--    abfss://landing@<storage-account>.dfs.core.windows.net/managed/<schema>/<tabla>/

-- Capa BRONZE: datos crudos tal cual llegan del scraper (1 tabla por scraper)
CREATE SCHEMA IF NOT EXISTS scraper_pipeline.bronze
    COMMENT 'Capa bronze: una tabla Delta por scraper, append raw del dia';

-- Capa SILVER: ofertas unificadas y deduplicadas por offer_id
CREATE SCHEMA IF NOT EXISTS scraper_pipeline.silver
    COMMENT 'Capa silver: ofertas unificadas y deduplicadas (MERGE por offer_id)';

-- Capa GOLD: agregados/KPIs para dashboards (la creas cuando la necesites)
CREATE SCHEMA IF NOT EXISTS scraper_pipeline.gold
    COMMENT 'Capa gold: agregados y KPIs derivados de silver (dashboards, Power BI)';

-- Verificar los 3 schemas
SHOW SCHEMAS IN scraper_pipeline;

-- =============================================================================
--  RESULTADO ESPERADO tras ejecutar todo:
--
--  catalogName         catalogType  provider  comment                                storageRoot
--  ------------------  -----------  --------  -------------------------------------  ---
--  scraper_pipeline    MANAGED      ...       Pipeline de ofertas de empleo desde... abfss://landing@<storage-account>.dfs.core.windows.net/managed/
--
--  Y SHOW SCHEMAS deberia listar:
--    bronze
--    gold
--    information_schema   (creado por defecto)
--    silver
-- =============================================================================

-- -----------------------------------------------------------------------------
-- TROUBLESHOOTING
--
--  Error: "User does not have permission CREATE CATALOG"
--    -> Tu usuario no tiene rol 'Catalog Creator' en Unity Catalog.
--       Solucion: pide a un admin de la cuenta que te lo asigne desde
--       Admin Console -> Unity Catalog -> Privileges -> Add 'Catalog Creator'.
--       Alternativa: que un admin ejecute este script por ti.
--
--  Error: "MANAGED LOCATION ... does not exist or is not a valid external location"
--    -> La carpeta 'managed/' no existe en el contenedor 'landing', o no hay
--       una External Location registrada apuntando ahi.
--       Solucion rapida: crea la carpeta desde Azure Portal y vuelve a ejecutar.
--       Si aun falla, en vez de MANAGED LOCATION, crea el catalog sin eso
--       (Databricks usara el storage por defecto del workspace):
--           CREATE CATALOG scraper_pipeline COMMENT '...';
--       Las tablas iran al storage gestionado del workspace (DBFS interno),
--       que tambien funciona pero menos flexible.
--
--  Error: "Path 'abfss://landing@...' is not authorized"
--    -> Databricks no tiene acceso al storage account. Necesitas la FASE 7
--       primero: registrar una External Location con un Service Principal.
--       Workaround: crea el catalog SIN MANAGED LOCATION (ver arriba).
-- -----------------------------------------------------------------------------