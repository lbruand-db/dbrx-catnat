-- Databricks notebook source
-- MAGIC %md
-- MAGIC # Silver — synthetic insurer portfolio
-- MAGIC
-- MAGIC Generates `catnat_silver.portfolio_policies` — ~`:n_policies` rows
-- MAGIC weighted by `admin_communes.population` and randomly placed on the
-- MAGIC commune's H3 r=9 polyfill. No upstream fetch — pure SQL.
-- MAGIC
-- MAGIC Each policy carries:
-- MAGIC - `cleabs` / `code_insee` / `code_dep` — links back to IGN admin layers
-- MAGIC - `h3` — joins to every hazard gold table
-- MAGIC - `insured_value_eur` — log-normal, ~250 k EUR median
-- MAGIC - `coverage_{flood,rga,storm}` — booleans (~85 %/95 %/60 % opt-in)
-- MAGIC - `policy_start_date` — random in the last 5 years
-- MAGIC
-- MAGIC Idempotent: `CREATE OR REPLACE TABLE`. Re-running with a fixed
-- MAGIC `:n_policies` produces a different sample each time (Spark `rand()`),
-- MAGIC which is fine for a demo and avoids the policy_id collision issues
-- MAGIC seeding would introduce.

-- COMMAND ----------

CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  COMMENT 'Silver: synthetic insurer portfolio. ~n_policies rows weighted by commune population. Not real data — for demo only.'
  TBLPROPERTIES (
    'catnat.layer'     = 'portfolio_policies',
    'catnat.medallion' = 'silver',
    'catnat.synthetic' = 'true'
  )
AS
WITH commune_pop AS (
  SELECT
    cleabs, code_insee, nom_officiel AS nom_commune, code_dep, population
  FROM IDENTIFIER(:catalog || '.catnat_silver.admin_communes')
  WHERE population IS NOT NULL AND population > 0
),
totals AS (
  SELECT SUM(population) AS total_pop FROM commune_pop
),
-- Per-commune policy quota, proportional to population.
quotas AS (
  SELECT
    c.cleabs, c.code_insee, c.nom_commune, c.code_dep, c.population,
    GREATEST(
      0,
      CAST(ROUND(CAST(:n_policies AS DOUBLE) * c.population / t.total_pop) AS INT)
    ) AS quota
  FROM commune_pop c CROSS JOIN totals t
),
-- One row per policy slot.
expanded AS (
  SELECT
    q.cleabs, q.code_insee, q.nom_commune, q.code_dep,
    posexplode(sequence(1, q.quota)) AS (slot_idx, _)
  FROM quotas q
  WHERE q.quota > 0
),
-- Each commune's H3 cells collected once, indexed for random picks.
commune_cells AS (
  SELECT cleabs, collect_list(h3) AS cells, COUNT(*) AS n_cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  GROUP BY cleabs
)
SELECT
  ROW_NUMBER() OVER (ORDER BY e.cleabs, e.slot_idx)            AS policy_id,
  e.cleabs,
  e.code_insee,
  e.nom_commune,
  e.code_dep,
  cc.cells[CAST(rand() * cc.n_cells AS INT)]                   AS h3,
  CAST(9 AS INT)                                               AS resolution,
  -- Log-normal insured value: exp(mu + sigma * randn()), clipped sanely.
  LEAST(5000000, GREATEST(50000,
    CAST(EXP(12.4 + randn() * 0.55) AS BIGINT)
  ))                                                           AS insured_value_eur,
  rand() < 0.85                                                AS coverage_flood,
  rand() < 0.95                                                AS coverage_rga,
  rand() < 0.60                                                AS coverage_storm,
  DATE_SUB(CURRENT_DATE(), CAST(rand() * 1825 AS INT))         AS policy_start_date,
  CAST('active' AS STRING)                                     AS policy_status,
  CURRENT_TIMESTAMP()                                          AS _generated_at
FROM expanded e
JOIN commune_cells cc ON e.cleabs = cc.cleabs
WHERE cc.n_cells > 0;

-- COMMAND ----------

ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN policy_id         COMMENT 'Identifiant de police / Policy primary key (synthetic, sequential)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN cleabs            COMMENT 'Identifiant IGN de la commune / IGN commune key (join to admin_communes)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN code_insee        COMMENT 'Code INSEE / INSEE commune code';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN h3                COMMENT 'Cellule H3 r=9 / H3 cell of policy location (joins to every hazard gold)';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN insured_value_eur COMMENT 'Capital assuré (EUR) / Insured value, log-normal ~250k median';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN coverage_flood    COMMENT 'Garantie inondation / Flood coverage flag';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN coverage_rga      COMMENT 'Garantie sécheresse-RGA / Drought/clay-shrinkage coverage flag';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN coverage_storm    COMMENT 'Garantie tempête / Storm coverage flag';
ALTER TABLE IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
  ALTER COLUMN policy_start_date COMMENT 'Date de souscription / Policy start date';

-- COMMAND ----------

SELECT
  code_dep,
  COUNT(*)                              AS n_policies,
  SUM(insured_value_eur)                AS total_insured_eur,
  ROUND(AVG(insured_value_eur))         AS avg_insured_eur,
  SUM(CASE WHEN coverage_flood THEN 1 ELSE 0 END) AS n_flood,
  SUM(CASE WHEN coverage_rga   THEN 1 ELSE 0 END) AS n_rga,
  SUM(CASE WHEN coverage_storm THEN 1 ELSE 0 END) AS n_storm
FROM IDENTIFIER(:catalog || '.catnat_silver.portfolio_policies')
GROUP BY code_dep
ORDER BY code_dep;
