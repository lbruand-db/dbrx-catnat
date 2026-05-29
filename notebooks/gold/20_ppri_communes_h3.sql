-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold — PPRI commune footprints H3 r=9 mart
-- MAGIC
-- MAGIC Decomposes silver PPRI commune polygons into H3 cells at resolution 9
-- MAGIC (~150 m edge) for sub-second equi-joins against `portfolio_policies`.
-- MAGIC One row per `(cod_commune, status, h3)`.
-- MAGIC
-- MAGIC `h3_polyfillash3` takes WKB or WKT (not native GEOMETRY), hence the
-- MAGIC `ST_AsBinary(geometry)` wrapper.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';
CREATE WIDGET TEXT resolution DEFAULT '9';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  COMMENT 'Gold: PPRI commune footprints decomposed to H3 cells (default r=9). One row per (cod_commune, status, h3). Source: catnat_silver.hazard_ppri_communes.'
  TBLPROPERTIES (
    'catnat.layer'         = 'hazard_ppri_communes',
    'catnat.peril'         = 'flood',
    'catnat.medallion'     = 'gold',
    'catnat.h3_resolution' = '9'
  )
AS
SELECT
  cell                       AS h3,
  CAST(:resolution AS INT)   AS resolution,
  cod_nat_pprn,
  cod_commune,
  lib_commune,
  status,
  dat_approbation
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
LATERAL VIEW explode(h3_polyfillash3(ST_AsBinary(geometry), CAST(:resolution AS INT))) AS cell;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ALTER COLUMN h3              COMMENT 'Cellule H3 / H3 cell id (BIGINT, encoded)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ALTER COLUMN resolution      COMMENT 'Résolution H3 / H3 resolution (default 9)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ALTER COLUMN cod_nat_pprn    COMMENT 'Code national PPRN / National PPRN code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ALTER COLUMN cod_commune     COMMENT 'Code INSEE / INSEE commune code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ALTER COLUMN status          COMMENT 'Statut PPR / PPR status (approuv | prescrit)';

-- COMMAND ----------

OPTIMIZE IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3') ZORDER BY (h3);

-- COMMAND ----------

WITH per_commune AS (
  SELECT cod_commune, status, COUNT(*) AS cells_in_commune
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  GROUP BY cod_commune, status
)
SELECT
  (SELECT COUNT(*)           FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')) AS n_cells,
  (SELECT COUNT(DISTINCT h3) FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')) AS n_distinct_h3,
  status,
  COUNT(*)                                                                                       AS n_communes,
  AVG(cells_in_commune)                                                                          AS avg_cells_per_commune
FROM per_commune
GROUP BY status
ORDER BY status;
