-- Population sitting on H3 cells that overlap any PPRI flood polygon
-- (approuv + prescrit), grouped by département. Per-commune populations are
-- pro-rated by the fraction of the commune's cells that overlap the hazard,
-- giving a defensible "population at risk" number from polygon overlap.

WITH any_ppri AS (
  SELECT DISTINCT h3
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
),
commune_totals AS (
  SELECT cleabs, COUNT(*) AS total_cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  GROUP BY cleabs
),
commune_exposed AS (
  SELECT c.cleabs, COUNT(*) AS exposed_cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3') c
  JOIN any_ppri p ON c.h3 = p.h3
  GROUP BY c.cleabs
)
SELECT
  s.code_dep,
  COUNT(*)                                       AS n_communes_exposed,
  SUM(s.population)                              AS total_population_in_exposed_communes,
  SUM(s.population * e.exposed_cells / t.total_cells)::BIGINT
                                                 AS pop_under_flood_prorated
FROM IDENTIFIER(:catalog || '.catnat_silver.admin_communes') s
JOIN commune_exposed e ON s.cleabs = e.cleabs
JOIN commune_totals  t ON s.cleabs = t.cleabs
GROUP BY s.code_dep
ORDER BY pop_under_flood_prorated DESC;
