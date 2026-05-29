-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — PPRI commune-level footprints
-- MAGIC
-- MAGIC Cleans up the bronze PPRI layer:
-- MAGIC
-- MAGIC 1. Filters out invalid / empty geometries (`ST_MakeValid` is not exposed
-- MAGIC    on this warehouse; we drop instead of repair).
-- MAGIC 2. Parses the WFS `dd-MM-yyyy` date strings to native `DATE` via
-- MAGIC    `TRY_TO_DATE` (returns NULL on malformed/empty input rather than
-- MAGIC    failing the whole table).
-- MAGIC 3. Pre-computes the commune-centroid H3 cell at r=7 for national rollups.
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`.

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  COMMENT 'Silver: PPR Inondation commune-level footprints, dates parsed and geometry-validated. Source: catnat_bronze.hazard_ppri_communes.'
  TBLPROPERTIES (
    'catnat.layer'     = 'hazard_ppri_communes',
    'catnat.peril'     = 'flood',
    'catnat.medallion' = 'silver'
  )
AS
SELECT
  gml_id,
  cod_nat_pprn,
  lib_pprn,
  list_lib_risque_long,
  lib_bassin_risques,
  cod_commune,
  lib_commune,
  status,
  TRY_TO_DATE(dat_prescription_raw, 'dd-MM-yyyy') AS dat_prescription,
  TRY_TO_DATE(dat_approbation_raw,  'dd-MM-yyyy') AS dat_approbation,
  TRY_TO_DATE(dat_modification_raw, 'dd-MM-yyyy') AS dat_modification,
  TRY_TO_DATE(dat_appli_ant_raw,    'dd-MM-yyyy') AS dat_appli_ant,
  TRY_TO_DATE(dat_annexion_plu_raw, 'dd-MM-yyyy') AS dat_annexion_plu,
  geometry,
  h3_longlatash3(ST_X(ST_Centroid(geometry)),
                 ST_Y(ST_Centroid(geometry)), 7) AS centroid_h3_r7,
  _ingested_at,
  _source_file
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_ppri_communes')
WHERE ST_IsValid(geometry)
  AND NOT ST_IsEmpty(geometry);

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN cod_nat_pprn       COMMENT 'Code national PPRN / National PPRN code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN cod_commune        COMMENT 'Code INSEE de la commune / INSEE commune code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN status             COMMENT 'Statut PPR / PPR status (approuv | prescrit)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN dat_prescription   COMMENT 'Date de prescription / Prescription date';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN dat_approbation    COMMENT 'Date d''approbation / Approval date';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN geometry           COMMENT 'Empreinte commune (validée) / Commune footprint, validated (GEOMETRY, EPSG:4326)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
  ALTER COLUMN centroid_h3_r7     COMMENT 'Cellule H3 r=7 du centroïde / H3 r=7 cell of commune centroid';

-- COMMAND ----------

SELECT
  status,
  COUNT(*)                            AS n_features,
  COUNT(DISTINCT cod_commune)         AS n_communes,
  COUNT(DISTINCT cod_nat_pprn)        AS n_pprns,
  MIN(dat_approbation)                AS earliest_approval,
  MAX(dat_approbation)                AS latest_approval
FROM IDENTIFIER(:catalog || '.catnat_silver.hazard_ppri_communes')
GROUP BY status
ORDER BY status;
