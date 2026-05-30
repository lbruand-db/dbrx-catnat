-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — IGN BD TOPO admin_communes (via dbtopo-bricks)
-- MAGIC
-- MAGIC Wraps the dbtopo-bricks output table `<catalog>.<ign_schema>.commune_dedup`
-- MAGIC in a thin silver view that:
-- MAGIC
-- MAGIC 1. Projects the columns we actually use downstream (everything else stays
-- MAGIC    one hop away in the source table — UC lineage links the view back).
-- MAGIC 2. Pre-computes the commune-centroid H3 r=7 cell, matching the convention
-- MAGIC    used by every other silver layer in this repo.
-- MAGIC 3. Keeps native `GEOMETRY(4326)` — no copy of the geometry data.
-- MAGIC
-- MAGIC **Prerequisite:** the dbtopo-bricks bundle must have been deployed and
-- MAGIC run against the same catalog. See SPEC §4.4.
-- MAGIC
-- MAGIC **Source CRS:** dbtopo-bricks server-side `ST_Transform`s from Lambert-93
-- MAGIC (EPSG:2154) to WGS84 (EPSG:4326), so this view is already in 4326.
-- MAGIC
-- MAGIC **Licence:** IGN BD TOPO v3.5 — Licence Ouverte 2.0.

-- COMMAND ----------

CREATE OR REPLACE VIEW IDENTIFIER(:catalog || '.catnat_silver.admin_communes')
  COMMENT 'Silver: IGN BD TOPO communes, sourced from dbtopo-bricks.commune_dedup. Licence Ouverte 2.0 (IGN).'
AS
SELECT
  cleabs,
  code_insee,
  code_insee_du_departement                                AS code_dep,
  code_insee_de_la_region                                  AS code_reg,
  nom_officiel,
  nom_usuel,
  population,
  dept,
  geometry,
  h3_longlatash3(ST_X(ST_Centroid(geometry)),
                 ST_Y(ST_Centroid(geometry)), 7)           AS centroid_h3_r7
FROM IDENTIFIER(:catalog || '.' || :ign_schema || '.commune_dedup')
WHERE ST_IsValid(geometry)
  AND NOT ST_IsEmpty(geometry);

-- COMMAND ----------

SELECT
  code_dep,
  COUNT(*)                            AS n_communes,
  COUNT(DISTINCT centroid_h3_r7)      AS n_distinct_h3_r7,
  SUM(population)                     AS total_population
FROM IDENTIFIER(:catalog || '.catnat_silver.admin_communes')
GROUP BY code_dep
ORDER BY code_dep;
