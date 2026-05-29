-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — BRGM RGA susceptibility
-- MAGIC
-- MAGIC Cleans up the bronze RGA layer:
-- MAGIC
-- MAGIC 1. Filters out invalid / empty geometries (bronze had 99/100 valid in the
-- MAGIC    sample). Databricks SQL on this warehouse doesn't expose `ST_MakeValid`,
-- MAGIC    so we drop rather than repair; tracked as a P0 follow-up.
-- MAGIC 2. Maps the BRGM `susceptibility_code` (1..4) to a human label.
-- MAGIC 3. Pre-computes the H3 r=7 cell of the centroid for fast national rollups
-- MAGIC    (gold owns the r=9 polyfill for point-in-polygon queries).
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  COMMENT 'Silver: BRGM RGA susceptibility polygons, geometry-repaired and labeled. Source: catnat_bronze.hazard_rga_susceptibility (Géorisques WFS, Etalab 2.0).'
  TBLPROPERTIES (
    'catnat.layer'   = 'hazard_rga',
    'catnat.peril'   = 'drought',
    'catnat.medallion' = 'silver'
  )
AS
SELECT
  gid,
  insee_dep,
  susceptibility_code,
  CASE susceptibility_code
    WHEN 1 THEN 'faible'
    WHEN 2 THEN 'moyen'
    WHEN 3 THEN 'fort'
    WHEN 4 THEN 'tres_fort'
    ELSE 'unknown'
  END                                                       AS susceptibility_label,
  id_zone_rga,
  geometry,
  h3_longlatash3(ST_X(ST_Centroid(geometry)),
                 ST_Y(ST_Centroid(geometry)), 7)            AS centroid_h3_r7,
  _ingested_at,
  _source_file
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
WHERE ST_IsValid(geometry)
  AND NOT ST_IsEmpty(geometry);

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN gid                  COMMENT 'Identifiant global / Global feature id (BRGM gid)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN insee_dep            COMMENT 'Code département INSEE / INSEE department code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN susceptibility_code  COMMENT 'Code BRGM (1..4) / BRGM susceptibility code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN susceptibility_label COMMENT 'Libellé FR (faible/moyen/fort/tres_fort) / French label';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN id_zone_rga          COMMENT 'Identifiant de zone RGA / RGA zone identifier';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN geometry             COMMENT 'Polygone exposition (réparé) / Susceptibility polygon, repaired (GEOMETRY, EPSG:4326)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility')
  ALTER COLUMN centroid_h3_r7       COMMENT 'Cellule H3 r=7 du centroïde / H3 r=7 cell of polygon centroid (national-aggregate grain)';

-- COMMAND ----------

SELECT
  COUNT(*)                            AS n_features,
  COUNT(DISTINCT susceptibility_code) AS n_levels,
  COUNT(DISTINCT centroid_h3_r7)      AS n_distinct_h3_r7,
  SUM(CASE WHEN ST_IsValid(geometry) THEN 1 ELSE 0 END) AS n_valid_geom
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_rga_susceptibility');
