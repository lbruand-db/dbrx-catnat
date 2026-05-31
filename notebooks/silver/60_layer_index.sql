-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — layer index (registry)
-- MAGIC
-- MAGIC Single source of truth for every catnat layer the demo can surface.
-- MAGIC The MCP server's `list_layers` tool (Phase 3) reads from this table —
-- MAGIC adding a new layer means appending one row here, no MCP code changes.
-- MAGIC
-- MAGIC Columns:
-- MAGIC - `layer_id` — stable demo-side identifier (e.g. `hazard_rga_h3`)
-- MAGIC - `table_fq` — fully-qualified UC name to query (catalog.schema.table)
-- MAGIC - `peril` — `flood` / `drought` / `storm` / `reference` / `portfolio`
-- MAGIC - `medallion` — `silver` / `gold`
-- MAGIC - `grain` — what each row represents (`polygon`, `h3_r9_cell`, …)
-- MAGIC - `h3_column` — H3 column name (NULL if not H3-indexed)
-- MAGIC - `geom_column` — native GEOMETRY column name (NULL for gold marts)
-- MAGIC - `license` — redistribution licence
-- MAGIC - `is_displayable` — UI should show this in layer picker
-- MAGIC - `description` — one-line description for the agent
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`. The `table_fq` column resolves
-- MAGIC the catalog at build time so downstream consumers can `SELECT FROM
-- MAGIC IDENTIFIER(table_fq)` without re-parameterising.

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  COMMENT 'Registry of every catnat layer that downstream consumers (MCP, app, demo) can surface. Phase 1.'
  TBLPROPERTIES (
    'catnat.layer'     = 'layer_index',
    'catnat.medallion' = 'silver'
  )
AS
SELECT
  layer_id,
  :catalog || '.catnat_' || medallion || '.' || layer_id AS table_fq,
  peril, medallion, grain,
  h3_column, geom_column, license, is_displayable, description
FROM (VALUES
  ('hazard_rga_susceptibility',  'drought',   'silver', 'polygon',
   NULL,  'geometry', 'Etalab 2.0',          false,
   'BRGM clay-shrinkage susceptibility polygons (silver), 1-4 levels.'),
  ('hazard_rga_h3',              'drought',   'gold',   'h3_r9_cell',
   'h3',  NULL,       'Etalab 2.0',          true,
   'BRGM clay-shrinkage susceptibility decomposed to H3 r=9 cells.'),

  ('hazard_ppri_communes',       'flood',     'silver', 'polygon',
   NULL,  'geometry', 'Etalab 2.0',          false,
   'PPR Inondation commune-level footprints (approuv + prescrit).'),
  ('hazard_ppri_communes_h3',    'flood',     'gold',   'h3_r9_cell',
   'h3',  NULL,       'Etalab 2.0',          true,
   'PPRI commune footprints decomposed to H3 r=9 cells.'),

  ('hazard_tri_flood',           'flood',     'silver', 'polygon',
   NULL,  'geometry', 'Etalab 2.0',          false,
   'TRI hazard maps (scenario x intensity grid), polygon footprints.'),
  ('hazard_tri_flood_h3',        'flood',     'gold',   'h3_r9_cell',
   'h3',  NULL,       'Etalab 2.0',          true,
   'TRI hazard footprints decomposed to H3 r=9 cells.'),

  ('admin_communes',             'reference', 'silver', 'polygon',
   NULL,  'geometry', 'Licence Ouverte 2.0', true,
   'IGN BD TOPO commune polygons with INSEE code, population, geometry.'),
  ('admin_communes_h3',          'reference', 'gold',   'h3_r9_cell',
   'h3',  NULL,       'Licence Ouverte 2.0', true,
   'IGN communes decomposed to H3 r=9 cells.'),

  ('portfolio_policies',         'portfolio', 'silver', 'policy',
   'h3',  NULL,       'Synthetic',           false,
   'Synthetic insurance policies, population-weighted, H3 r=9 located.'),
  ('portfolio_policies_h3',      'portfolio', 'gold',   'h3_r9_cell',
   'h3',  NULL,       'Synthetic',           true,
   'Per-cell portfolio rollup: counts and insured values per H3 cell.'),

  ('events',                     'reference', 'silver', 'event',
   NULL,  NULL,       'Etalab 2.0',          true,
   'Hand-seeded recent CatNat events (Ciaran, Domingos, Eunice, etc.).')
) AS t(layer_id, peril, medallion, grain,
       h3_column, geom_column, license, is_displayable, description);

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN layer_id       COMMENT 'Identifiant stable / Stable layer key';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN table_fq       COMMENT 'Nom UC qualifié / Fully-qualified UC table name';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN peril          COMMENT 'Péril / Peril (flood, drought, storm, reference, portfolio)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN medallion      COMMENT 'Niveau médaillon / Medallion stage (silver | gold)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN grain          COMMENT 'Granularité / Grain';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN h3_column      COMMENT 'Nom colonne H3 / H3 column name (NULL if not H3-indexed)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.layer_index')
  ALTER COLUMN is_displayable COMMENT 'Visible dans le sélecteur UI / Show in the UI layer picker';

-- COMMAND ----------

SELECT peril, medallion, COUNT(*) AS n_layers
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
GROUP BY peril, medallion
ORDER BY peril, medallion;
