-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Bronze — BRGM RGA susceptibility
-- MAGIC
-- MAGIC Loads clay-shrinkage exposure polygons (Géorisques layer `ms:ALEARG_REALISE`)
-- MAGIC from a line-delimited GeoJSON staged in `catnat_bronze.raw/rga/` into a
-- MAGIC native-`GEOMETRY(4326)` Delta table.
-- MAGIC
-- MAGIC **Upstream:** the fetcher `scripts/fetch_rga.sh` pulls the layer via WFS,
-- MAGIC converts to `GeoJSONSeq`, and uploads to the volume.
-- MAGIC
-- MAGIC **Source CRS:** WFS is queried with `srsName=EPSG:4326`, so no
-- MAGIC reprojection is needed in this notebook. The optional `geom_l93_g{1,5,10,25}`
-- MAGIC pre-simplified Lambert-93 WKB columns are dropped here — silver/gold can
-- MAGIC re-simplify with `ST_SimplifyPreserveTopology` if needed.
-- MAGIC
-- MAGIC **Susceptibility codification** (BRGM convention, mapped in silver):
-- MAGIC `1 = faible, 2 = moyen, 3 = fort, 4 = très fort`.
-- MAGIC
-- MAGIC **License:** Etalab 2.0 / Licence Ouverte (Géorisques / BRGM).

-- COMMAND ----------

-- MAGIC %md ## Parameters

-- COMMAND ----------

CREATE WIDGET TEXT catalog DEFAULT 'serverless_stable_po64og_catalog';
CREATE WIDGET TEXT input_path DEFAULT '/Volumes/serverless_stable_po64og_catalog/catnat_bronze/raw/rga/rga_sample.geojsonl';

-- COMMAND ----------

-- MAGIC %md ## Load
-- MAGIC
-- MAGIC Read the file as text (one line = one feature), pull the geometry out as a
-- MAGIC JSON sub-object, and parse with `ST_GeomFromGeoJSON`. We use this pattern
-- MAGIC (instead of letting Spark infer the JSON schema) because Spark's JSON
-- MAGIC inference coerces the nested coordinate arrays to strings, which would
-- MAGIC otherwise require reconstruction.

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  COMMENT 'Bronze: BRGM RGA (retrait-gonflement des argiles) susceptibility polygons. Source: Géorisques WFS layer ms:ALEARG_REALISE. Licence: Etalab 2.0. Generated using BRGM data on Géorisques (https://www.georisques.gouv.fr).'
  TBLPROPERTIES (
    'catnat.layer' = 'hazard_rga',
    'catnat.peril' = 'drought',
    'catnat.source' = 'georisques-wfs',
    'catnat.licence' = 'etalab-2.0'
  )
AS
SELECT
  CAST(get_json_object(value, '$.properties.gid')         AS BIGINT) AS gid,
  CAST(get_json_object(value, '$.properties.insee_dep')   AS STRING) AS insee_dep,
  CAST(get_json_object(value, '$.properties.id')          AS INT)    AS susceptibility_code,
  CAST(get_json_object(value, '$.properties.id_zone_rga') AS STRING) AS id_zone_rga,
  CAST(get_json_object(value, '$.properties.gml_id')      AS STRING) AS gml_id,
  ST_GeomFromGeoJSON(get_json_object(value, '$.geometry'))           AS geometry,
  current_timestamp()                                                AS _ingested_at,
  :input_path                                                        AS _source_file
FROM read_files(:input_path, format => 'text');

-- COMMAND ----------

-- MAGIC %md ## Add column comments
-- MAGIC
-- MAGIC Bilingual (FR/EN) to match the dbtopo-bricks convention for IGN layers.

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN gid                 COMMENT 'Identifiant global / Global feature id (BRGM gid)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN insee_dep           COMMENT 'Code département INSEE (2–3 chars) / INSEE department code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN susceptibility_code COMMENT 'Code BRGM (1=faible, 2=moyen, 3=fort, 4=très fort) / BRGM susceptibility code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN id_zone_rga         COMMENT 'Identifiant de zone RGA / RGA zone identifier';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN gml_id              COMMENT 'Identifiant GML source / Source GML id (provenance)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN geometry            COMMENT 'Polygone exposition / Susceptibility polygon (GEOMETRY, EPSG:4326)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN _ingested_at        COMMENT 'Horodatage d''ingestion / Ingestion timestamp';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility')
  ALTER COLUMN _source_file        COMMENT 'Chemin du fichier source / Source file path (provenance)';

-- COMMAND ----------

-- MAGIC %md ## Verification

-- COMMAND ----------

SELECT
  COUNT(*)                                            AS n_features,
  COUNT(DISTINCT insee_dep)                           AS n_departments,
  COUNT(DISTINCT susceptibility_code)                 AS n_levels,
  SUM(CASE WHEN ST_IsValid(geometry) THEN 1 ELSE 0 END) AS n_valid_geom
FROM IDENTIFIER(:catalog || '.catnat_bronze.hazard_rga_susceptibility');
