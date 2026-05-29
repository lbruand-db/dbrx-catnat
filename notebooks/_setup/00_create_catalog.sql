-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Setup — Catalog, schemas, and raw-staging volume
-- MAGIC
-- MAGIC Idempotent. Run once per workspace. Re-run is safe (uses `IF NOT EXISTS`).
-- MAGIC
-- MAGIC **Note on naming:** the spec calls for a top-level `catnat` catalog, but the
-- MAGIC FEVM workspace metastore only grants schema-level privileges. We nest under
-- MAGIC the workspace-default catalog and prefix all schemas with `catnat_`. When we
-- MAGIC migrate to a workspace where we have `CREATE CATALOG` on the metastore,
-- MAGIC change the `catalog` widget below to `catnat` and re-run.

-- COMMAND ----------

-- MAGIC %md ## Parameters

-- COMMAND ----------


-- COMMAND ----------

-- MAGIC %md ## Schemas

-- COMMAND ----------

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:catalog || '.catnat_bronze')
  COMMENT 'CatNat bronze — raw ingests from Géorisques, BRGM, C3S, IGN, synthetic portfolio';

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:catalog || '.catnat_silver')
  COMMENT 'CatNat silver — typed, geometry-validated, CRS-normalized to EPSG:4326';

CREATE SCHEMA IF NOT EXISTS IDENTIFIER(:catalog || '.catnat_gold')
  COMMENT 'CatNat gold — H3-indexed marts (r=9 for points, r=7 for national aggregates)';

-- COMMAND ----------

-- MAGIC %md ## Raw-staging volume
-- MAGIC
-- MAGIC Used to land downloaded archives (PPRI shapefiles, BRGM polygons, C3S NetCDFs)
-- MAGIC before they're parsed into Delta. One volume keeps everything in one place
-- MAGIC and lets us reuse the cache across notebook runs.

-- COMMAND ----------

CREATE VOLUME IF NOT EXISTS IDENTIFIER(:catalog || '.catnat_bronze.raw')
  COMMENT 'Staging area for raw downloads (Géorisques, BRGM, C3S, INSEE)';

-- COMMAND ----------

-- MAGIC %md ## Verification
-- MAGIC
-- MAGIC `SHOW SCHEMAS IN IDENTIFIER(:catalog)` is currently rejected by the SQL
-- MAGIC parser (parameter marker not allowed in this position), so we verify via
-- MAGIC `information_schema` instead.

-- COMMAND ----------

SELECT schema_name
FROM IDENTIFIER(:catalog || '.information_schema.schemata')
WHERE schema_name LIKE 'catnat_%'
ORDER BY schema_name;

-- COMMAND ----------

SELECT 'volume_path' AS key, '/Volumes/' || :catalog || '/catnat_bronze/raw' AS value;
