-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold — IGN admin_communes H3 r=9 mart
-- MAGIC
-- MAGIC Decomposes silver commune polygons into H3 cells at resolution 9 so a
-- MAGIC point-in-commune lookup is a single equi-join against `h3` — same shape
-- MAGIC as the other gold marts (RGA, PPRI, TRI).
-- MAGIC
-- MAGIC One row per `(cleabs, h3)`.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  COMMENT 'Gold: IGN BD TOPO communes decomposed to H3 cells (default r=9). One row per (cleabs, h3). Source: catnat_silver.admin_communes.'
  TBLPROPERTIES (
    'catnat.layer'         = 'admin_communes',
    'catnat.medallion'     = 'gold',
    'catnat.h3_resolution' = '9'
  )
AS
SELECT
  cell                       AS h3,
  CAST(:resolution AS INT)   AS resolution,
  cleabs,
  code_insee,
  code_dep,
  nom_officiel,
  population
FROM IDENTIFIER(:catalog || '.catnat_silver.admin_communes')
LATERAL VIEW explode(h3_polyfillash3(ST_AsBinary(geometry), CAST(:resolution AS INT))) AS cell;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  ALTER COLUMN h3           COMMENT 'Cellule H3 / H3 cell id';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  ALTER COLUMN resolution   COMMENT 'Résolution H3 / H3 resolution';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  ALTER COLUMN cleabs       COMMENT 'Identifiant IGN unique / IGN unique key';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  ALTER COLUMN code_insee   COMMENT 'Code INSEE de la commune / INSEE commune code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  ALTER COLUMN code_dep     COMMENT 'Code INSEE du département / Department code';

-- COMMAND ----------

OPTIMIZE IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3') ZORDER BY (h3);

-- COMMAND ----------

WITH per_commune AS (
  SELECT cleabs, code_dep, COUNT(*) AS cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  GROUP BY cleabs, code_dep
)
SELECT
  (SELECT COUNT(*)           FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')) AS n_cells,
  (SELECT COUNT(DISTINCT h3) FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')) AS n_distinct_h3,
  code_dep,
  COUNT(*)                AS n_communes,
  AVG(cells)              AS avg_cells_per_commune
FROM per_commune
GROUP BY code_dep
ORDER BY code_dep;
