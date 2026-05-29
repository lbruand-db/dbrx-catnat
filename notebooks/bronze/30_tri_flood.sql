-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Bronze — Géorisques TRI flood-hazard footprints
-- MAGIC
-- MAGIC Loads the EU Floods Directive `ALEA_SYNT_<scenario>_<intensity>_FXX`
-- MAGIC layers (eleven metropolitan layers across the scenario × intensity grid)
-- MAGIC from a single line-delimited GeoJSON staged by `catnat.fetch.tri`.
-- MAGIC
-- MAGIC The fetcher tags each feature with `_scenario_code` and
-- MAGIC `_intensity_code` so bronze can carry both as first-class columns
-- MAGIC without parsing the source layer name.
-- MAGIC
-- MAGIC **Scenario codes** (column `scenario_code`):
-- MAGIC `01` = Fréquent  · `02` = Moyen  · `03` = Extrême
-- MAGIC
-- MAGIC **Intensity codes** (column `intensity_code`):
-- MAGIC `01FOR` = Fort  · `02MOY` = Moyen  · `03MCC` = MCC (moyen-courant)  · `04FAI` = Faible
-- MAGIC
-- MAGIC **Source CRS:** queried with `srsName=EPSG:4326`, no reprojection here.
-- MAGIC
-- MAGIC **Licence:** Etalab 2.0 / Licence Ouverte (Géorisques).

-- COMMAND ----------

-- input_path is a glob across the eleven per-layer files written by
-- `catnat fetch tri`. Pattern: tri/tri_<scenario>_<intensity>_<suffix>.geojsonl

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  COMMENT 'Bronze: TRI flood-hazard footprints (EU Floods Directive, scenario × intensity grid). Source: Géorisques WFS layers ms:ALEA_SYNT_<scenario>_<intensity>_FXX. Licence: Etalab 2.0.'
  TBLPROPERTIES (
    'catnat.layer'   = 'hazard_tri_flood',
    'catnat.peril'   = 'flood',
    'catnat.source'  = 'georisques-wfs',
    'catnat.licence' = 'etalab-2.0'
  )
AS
SELECT
  CAST(get_json_object(value, '$.properties.gml_id')          AS STRING) AS gml_id,
  CAST(get_json_object(value, '$.properties.id')              AS BIGINT) AS id,
  CAST(get_json_object(value, '$.properties.id_s_inond')      AS STRING) AS id_s_inond,
  CAST(get_json_object(value, '$.properties.typ_inond')       AS STRING) AS typ_inond,
  CAST(get_json_object(value, '$.properties.typ_inond2')      AS STRING) AS typ_inond2,
  CAST(get_json_object(value, '$.properties.scenario')        AS STRING) AS scenario,
  CAST(get_json_object(value, '$.properties.datentree')       AS STRING) AS datentree_raw,
  CAST(get_json_object(value, '$.properties.datsortie')       AS STRING) AS datsortie_raw,
  CAST(get_json_object(value, '$.properties.cours_deau')      AS STRING) AS cours_deau,
  CAST(get_json_object(value, '$.properties.est_ref')         AS STRING) AS est_ref,
  CAST(get_json_object(value, '$.properties.id_tri')          AS STRING) AS id_tri,
  CAST(get_json_object(value, '$.properties._scenario_code')  AS STRING) AS scenario_code,
  CAST(get_json_object(value, '$.properties._intensity_code') AS STRING) AS intensity_code,
  ST_GeomFromGeoJSON(get_json_object(value, '$.geometry'))               AS geometry,
  current_timestamp()                                                    AS _ingested_at,
  :input_path                                                            AS _source_file
FROM read_files(:input_path, format => 'text');

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN id_tri         COMMENT 'Identifiant TRI / TRI identifier (e.g. FRE_TRI_BASTIA)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN scenario_code  COMMENT 'Probabilité (01=fréquent, 02=moyen, 03=extrême) / Return-period scenario code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN intensity_code COMMENT 'Intensité (01FOR/02MOY/03MCC/04FAI) / Hazard intensity code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN typ_inond      COMMENT 'Type inondation (01=débordement, 02=submersion, 03=ruissellement) / Flood type code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN cours_deau     COMMENT 'Cours d''eau associé / Associated water body';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN est_ref        COMMENT 'Empreinte de référence (t/f) / Reference footprint flag';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
  ALTER COLUMN geometry       COMMENT 'Empreinte aléa / Hazard footprint (GEOMETRY, EPSG:4326)';

-- COMMAND ----------

SELECT
  scenario_code,
  intensity_code,
  COUNT(*)                                              AS n_features,
  COUNT(DISTINCT id_tri)                                AS n_tris,
  COUNT(DISTINCT typ_inond)                             AS n_types,
  SUM(CASE WHEN ST_IsValid(geometry) THEN 1 ELSE 0 END) AS n_valid_geom
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_tri_flood')
GROUP BY scenario_code, intensity_code
ORDER BY scenario_code, intensity_code;
