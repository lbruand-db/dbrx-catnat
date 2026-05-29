-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold — RGA H3 r=9 mart
-- MAGIC
-- MAGIC Decomposes silver RGA polygons into H3 cells at resolution 9 (~150 m edge),
-- MAGIC the grain for point-in-polygon joins against `portfolio_policies` (which
-- MAGIC will carry their own r=9 cell). One row per `(gid, h3)`.
-- MAGIC
-- MAGIC ```text
-- MAGIC policy (lon, lat) → h3_longlatash3(lon, lat, 9) → equi-join gold.hazard_rga_h3
-- MAGIC ```
-- MAGIC
-- MAGIC The H3 polyfill function takes WKB or WKT (not native GEOMETRY), hence the
-- MAGIC `ST_AsBinary(geometry)` wrapper.
-- MAGIC
-- MAGIC Optimisations:
-- MAGIC - `ZORDER BY h3` so equi-join lookups are clustered.
-- MAGIC - Cached size metric in `n_cells` for downstream cost-estimation.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';
CREATE WIDGET TEXT resolution DEFAULT '9';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  COMMENT 'Gold: BRGM RGA susceptibility decomposed to H3 cells (default r=9, ~150 m edge). One row per (gid, h3). Source: catnat_silver.hazard_rga_susceptibility.'
  TBLPROPERTIES (
    'catnat.layer'   = 'hazard_rga',
    'catnat.peril'   = 'drought',
    'catnat.medallion' = 'gold',
    'catnat.h3_resolution' = '9'
  )
AS
SELECT
  cell                        AS h3,
  CAST(:resolution AS INT)    AS resolution,
  gid,
  insee_dep,
  susceptibility_code,
  susceptibility_label,
  id_zone_rga
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
LATERAL VIEW explode(h3_polyfillash3(ST_AsBinary(geometry), CAST(:resolution AS INT))) AS cell;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN h3                   COMMENT 'Cellule H3 / H3 cell id (BIGINT, encoded)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN resolution           COMMENT 'Résolution H3 / H3 resolution (default 9)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN gid                  COMMENT 'Identifiant zone (silver) / Zone id back-reference to silver';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN insee_dep            COMMENT 'Code département INSEE / INSEE department code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN susceptibility_code  COMMENT 'Code BRGM (1..4) / BRGM susceptibility code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN susceptibility_label COMMENT 'Libellé FR / French label';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ALTER COLUMN id_zone_rga          COMMENT 'Identifiant zone RGA / RGA zone identifier';

-- COMMAND ----------

-- ZORDER on h3 for fast equi-join lookups; safe to re-run.
OPTIMIZE IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3') ZORDER BY (h3);

-- COMMAND ----------

WITH per_zone AS (
  SELECT gid, COUNT(*) AS cells_in_zone
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  GROUP BY gid
)
SELECT
  (SELECT COUNT(*)          FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')) AS n_cells,
  (SELECT COUNT(DISTINCT h3) FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')) AS n_distinct_h3,
  COUNT(*)                                                                              AS n_source_zones,
  AVG(cells_in_zone)                                                                    AS avg_cells_per_zone
FROM per_zone;
