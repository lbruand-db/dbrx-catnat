-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Bronze — PPRI commune-level footprints
-- MAGIC
-- MAGIC Loads Géorisques `PPRN_COMMUNE_RISQINOND_*` polygons (commune-level
-- MAGIC indicator of "this commune is in a PPR Inondation"). Two status flavors
-- MAGIC are unioned with a `status` column:
-- MAGIC
-- MAGIC - `approuv`  — PPR approved (legally in force).
-- MAGIC - `prescrit` — PPR prescribed (procedure started, not yet approved).
-- MAGIC
-- MAGIC Inputs are line-delimited GeoJSON files staged by `catnat.fetch.ppri` in
-- MAGIC the bronze raw volume (cache-first). The detailed in-PPRI zoning (zone
-- MAGIC rouge / zone bleue) lives in per-DDT shapefiles outside this WFS and is
-- MAGIC post-v1 — see SPEC §4.1.
-- MAGIC
-- MAGIC **Source CRS:** WFS is queried with `srsName=EPSG:4326`, so no reprojection.
-- MAGIC
-- MAGIC **Licence:** Etalab 2.0 / Licence Ouverte (Géorisques).

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';
CREATE WIDGET TEXT input_approuv DEFAULT '/Volumes/serverless_stable_po64og_catalog/catnat_bronze/raw/ppri/ppri_approuv_sample.geojsonl';
CREATE WIDGET TEXT input_prescrit DEFAULT '/Volumes/serverless_stable_po64og_catalog/catnat_bronze/raw/ppri/ppri_prescrit_sample.geojsonl';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  COMMENT 'Bronze: PPR Inondation commune-level footprints (approuv + prescrit). Source: Géorisques WFS layers ms:PPRN_COMMUNE_RISQINOND_APPROUV and _PRESCRIT. Licence: Etalab 2.0.'
  TBLPROPERTIES (
    'catnat.layer'   = 'hazard_ppri_communes',
    'catnat.peril'   = 'flood',
    'catnat.source'  = 'georisques-wfs',
    'catnat.licence' = 'etalab-2.0'
  )
AS
WITH approuv AS (
  SELECT value AS raw, 'approuv' AS status, :input_approuv AS _source_file
  FROM read_files(:input_approuv, format => 'text')
),
prescrit AS (
  SELECT value AS raw, 'prescrit' AS status, :input_prescrit AS _source_file
  FROM read_files(:input_prescrit, format => 'text')
),
unioned AS (
  SELECT * FROM approuv
  UNION ALL
  SELECT * FROM prescrit
)
SELECT
  CAST(get_json_object(raw, '$.properties.gml_id')              AS STRING) AS gml_id,
  CAST(get_json_object(raw, '$.properties.cod_nat_pprn')        AS STRING) AS cod_nat_pprn,
  CAST(get_json_object(raw, '$.properties.lib_pprn')            AS STRING) AS lib_pprn,
  CAST(get_json_object(raw, '$.properties.list_lib_risque_long')AS STRING) AS list_lib_risque_long,
  CAST(get_json_object(raw, '$.properties.lib_bassin_risques')  AS STRING) AS lib_bassin_risques,
  CAST(get_json_object(raw, '$.properties.cod_commune')         AS STRING) AS cod_commune,
  CAST(get_json_object(raw, '$.properties.lib_commune')         AS STRING) AS lib_commune,
  status,
  CAST(get_json_object(raw, '$.properties.dat_prescription')    AS STRING) AS dat_prescription_raw,
  CAST(get_json_object(raw, '$.properties.dat_approbation')     AS STRING) AS dat_approbation_raw,
  CAST(get_json_object(raw, '$.properties.dat_modification')    AS STRING) AS dat_modification_raw,
  CAST(get_json_object(raw, '$.properties.dat_appli_ant')       AS STRING) AS dat_appli_ant_raw,
  CAST(get_json_object(raw, '$.properties.dat_annexion_plu')    AS STRING) AS dat_annexion_plu_raw,
  ST_GeomFromGeoJSON(get_json_object(raw, '$.geometry'))                  AS geometry,
  current_timestamp()                                                      AS _ingested_at,
  _source_file
FROM unioned;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN gml_id               COMMENT 'Identifiant GML source / Source GML id (provenance)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN cod_nat_pprn         COMMENT 'Code national PPRN / National PPRN code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN lib_pprn             COMMENT 'Libellé PPRN / PPR name';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN list_lib_risque_long COMMENT 'Liste des risques / Risk labels (free-text)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN lib_bassin_risques   COMMENT 'Bassin de risques / Risk basin';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN cod_commune          COMMENT 'Code INSEE de la commune / INSEE commune code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN lib_commune          COMMENT 'Nom de la commune / Commune name';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN status               COMMENT 'Statut PPR / PPR status (approuv | prescrit)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
  ALTER COLUMN geometry             COMMENT 'Empreinte commune / Commune footprint (GEOMETRY, EPSG:4326)';

-- COMMAND ----------

SELECT
  status,
  COUNT(*)                                              AS n_features,
  COUNT(DISTINCT cod_commune)                           AS n_communes,
  COUNT(DISTINCT cod_nat_pprn)                          AS n_pprns,
  SUM(CASE WHEN ST_IsValid(geometry) THEN 1 ELSE 0 END) AS n_valid_geom
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
GROUP BY status
ORDER BY status;
