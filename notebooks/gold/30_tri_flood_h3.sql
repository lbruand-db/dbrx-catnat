-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold — TRI flood-hazard H3 r=9 mart
-- MAGIC
-- MAGIC Decomposes silver TRI polygons to H3 cells at resolution 9 (~150 m edge).
-- MAGIC One row per `(gml_id, h3)` so the mart preserves the full
-- MAGIC (scenario × intensity × TRI × type) grain for point-in-polygon joins.
-- MAGIC
-- MAGIC `h3_polyfillash3` takes WKB or WKT (not native GEOMETRY), hence the
-- MAGIC `ST_AsBinary(geometry)` wrapper.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';
CREATE WIDGET TEXT resolution DEFAULT '9';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  COMMENT 'Gold: TRI flood-hazard footprints decomposed to H3 cells (default r=9). One row per (gml_id, h3). Source: catnat_silver.hazard_tri_flood.'
  TBLPROPERTIES (
    'catnat.layer'         = 'hazard_tri_flood',
    'catnat.peril'         = 'flood',
    'catnat.medallion'     = 'gold',
    'catnat.h3_resolution' = '9'
  )
AS
SELECT
  cell                       AS h3,
  CAST(:resolution AS INT)   AS resolution,
  gml_id,
  id_tri,
  scenario_code,
  scenario_label,
  intensity_code,
  intensity_label,
  typ_inond,
  typ_inond_label,
  cours_deau
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
LATERAL VIEW explode(h3_polyfillash3(ST_AsBinary(geometry), CAST(:resolution AS INT))) AS cell;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN h3              COMMENT 'Cellule H3 / H3 cell id';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN resolution      COMMENT 'Résolution H3 / H3 resolution';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN scenario_code   COMMENT 'Probabilité (01/02/03) / Return-period code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN intensity_code  COMMENT 'Intensité (01FOR/02MOY/03MCC/04FAI) / Intensity code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN id_tri          COMMENT 'Identifiant TRI / TRI identifier';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ALTER COLUMN typ_inond       COMMENT 'Type inondation / Flood type code';

-- COMMAND ----------

OPTIMIZE IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3') ZORDER BY (h3);

-- COMMAND ----------

WITH per_footprint AS (
  SELECT gml_id, scenario_code, intensity_code, COUNT(*) AS cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  GROUP BY gml_id, scenario_code, intensity_code
)
SELECT
  (SELECT COUNT(*)           FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')) AS n_cells,
  (SELECT COUNT(DISTINCT h3) FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')) AS n_distinct_h3,
  scenario_code,
  intensity_code,
  COUNT(*)                AS n_footprints,
  AVG(cells)              AS avg_cells_per_footprint
FROM per_footprint
GROUP BY scenario_code, intensity_code
ORDER BY scenario_code, intensity_code;
