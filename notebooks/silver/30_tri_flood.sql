-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — TRI flood-hazard footprints
-- MAGIC
-- MAGIC Cleans up bronze TRI:
-- MAGIC
-- MAGIC 1. Filters invalid / empty geometries (no `ST_MakeValid` on this WH).
-- MAGIC 2. Casts `datentree` / `datsortie` from ISO `yyyy-MM-dd` strings to `DATE`.
-- MAGIC 3. Adds human labels for scenario, intensity, and flood type.
-- MAGIC 4. Pre-computes a centroid H3 r=7 cell for national aggregates.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------


-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  COMMENT 'Silver: TRI flood-hazard footprints, dates parsed and labels resolved. Source: catnat_bronze.hazard_tri_flood.'
  TBLPROPERTIES (
    'catnat.layer'     = 'hazard_tri_flood',
    'catnat.peril'     = 'flood',
    'catnat.medallion' = 'silver'
  )
AS
SELECT
  gml_id,
  id,
  id_s_inond,
  typ_inond,
  CASE typ_inond
    WHEN '01' THEN 'debordement_cours_deau'
    WHEN '02' THEN 'submersion_marine'
    WHEN '03' THEN 'ruissellement'
    ELSE 'other'
  END                              AS typ_inond_label,
  scenario_code,
  CASE scenario_code
    WHEN '01' THEN 'frequent'
    WHEN '02' THEN 'moyen'
    WHEN '03' THEN 'extreme'
    ELSE 'unknown'
  END                              AS scenario_label,
  intensity_code,
  CASE intensity_code
    WHEN '01FOR' THEN 'fort'
    WHEN '02MOY' THEN 'moyen'
    WHEN '03MCC' THEN 'mcc'
    WHEN '04FAI' THEN 'faible'
    ELSE 'unknown'
  END                              AS intensity_label,
  cours_deau,
  est_ref = 't'                    AS is_reference,
  id_tri,
  TRY_CAST(datentree_raw AS DATE)  AS datentree,
  TRY_CAST(datsortie_raw AS DATE)  AS datsortie,
  geometry,
  h3_longlatash3(ST_X(ST_Centroid(geometry)),
                 ST_Y(ST_Centroid(geometry)), 7) AS centroid_h3_r7,
  _ingested_at,
  _source_file
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
WHERE ST_IsValid(geometry)
  AND NOT ST_IsEmpty(geometry);

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN scenario_code   COMMENT 'Probabilité code (01/02/03) / Return-period scenario code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN scenario_label  COMMENT 'Libellé FR (frequent/moyen/extreme) / French scenario label';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN intensity_code  COMMENT 'Intensité code (01FOR/02MOY/03MCC/04FAI) / Intensity code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN intensity_label COMMENT 'Libellé FR (fort/moyen/mcc/faible) / French intensity label';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN typ_inond_label COMMENT 'Libellé FR du type / French flood-type label';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN is_reference    COMMENT 'Empreinte de référence (boolean) / Reference footprint flag';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN datentree       COMMENT 'Date d''entrée en vigueur / Validity start';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN datsortie       COMMENT 'Date de sortie / Validity end';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
  ALTER COLUMN centroid_h3_r7  COMMENT 'Cellule H3 r=7 du centroïde / H3 r=7 cell of polygon centroid';

-- COMMAND ----------

SELECT
  scenario_code, scenario_label,
  intensity_code, intensity_label,
  COUNT(*)                AS n_features,
  COUNT(DISTINCT id_tri)  AS n_tris
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_tri_flood')
GROUP BY scenario_code, scenario_label, intensity_code, intensity_label
ORDER BY scenario_code, intensity_code;
