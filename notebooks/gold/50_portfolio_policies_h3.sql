-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Gold — portfolio H3 r=9 rollup
-- MAGIC
-- MAGIC Pre-aggregates `portfolio_policies` by H3 cell so the demo's national
-- MAGIC choropleth (Act 1) joins to any hazard layer in a single equi-join
-- MAGIC and renders without per-row aggregation at query time.
-- MAGIC
-- MAGIC One row per `(h3, code_dep)`. Columns:
-- MAGIC - `n_policies` — count
-- MAGIC - `sum_insured_value_eur` — total exposure in that cell
-- MAGIC - `n_flood` / `n_rga` / `n_storm` — per-peril coverage counts
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`, `ZORDER BY h3`.

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  COMMENT 'Gold: portfolio aggregated by H3 r=9. Source: catnat_silver.portfolio_policies.'
  TBLPROPERTIES (
    'catnat.layer'         = 'portfolio_policies',
    'catnat.medallion'     = 'gold',
    'catnat.h3_resolution' = '9',
    'catnat.synthetic'     = 'true'
  )
AS
SELECT
  h3,
  code_dep,
  COUNT(*)                                                            AS n_policies,
  SUM(insured_value_eur)                                              AS sum_insured_value_eur,
  CAST(ROUND(AVG(insured_value_eur)) AS BIGINT)                       AS avg_insured_value_eur,
  SUM(CASE WHEN coverage_flood THEN 1 ELSE 0 END)                     AS n_flood,
  SUM(CASE WHEN coverage_rga   THEN 1 ELSE 0 END)                     AS n_rga,
  SUM(CASE WHEN coverage_storm THEN 1 ELSE 0 END)                     AS n_storm,
  SUM(CASE WHEN coverage_flood THEN insured_value_eur ELSE 0 END)     AS sum_insured_flood_eur,
  SUM(CASE WHEN coverage_rga   THEN insured_value_eur ELSE 0 END)     AS sum_insured_rga_eur,
  SUM(CASE WHEN coverage_storm THEN insured_value_eur ELSE 0 END)     AS sum_insured_storm_eur
FROM IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
GROUP BY h3, code_dep;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  ALTER COLUMN h3                    COMMENT 'Cellule H3 r=9 / H3 cell id';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  ALTER COLUMN code_dep              COMMENT 'Code département INSEE / Department code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  ALTER COLUMN n_policies            COMMENT 'Nombre de polices dans la cellule / Policy count';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  ALTER COLUMN sum_insured_value_eur COMMENT 'Capital assuré total (EUR) / Total insured value';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
  ALTER COLUMN sum_insured_flood_eur COMMENT 'Capital assuré garanti inondation (EUR) / Sum insured with flood coverage';

-- COMMAND ----------

OPTIMIZE IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3') ZORDER BY (h3);

-- COMMAND ----------

SELECT
  code_dep,
  COUNT(*)                       AS n_cells,
  SUM(n_policies)                AS n_policies,
  SUM(sum_insured_value_eur)     AS total_insured_eur,
  ROUND(AVG(n_policies), 1)      AS avg_policies_per_cell
FROM IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
GROUP BY code_dep
ORDER BY code_dep;
