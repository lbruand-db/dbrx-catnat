-- For each commune, what % of its H3 r=9 cells fall in a "fort" RGA
-- (clay-shrinkage) zone? Surfaces the communes where almost every household
-- foundation is at risk — useful for actuary-frame Act 3 conversations.

WITH rga_fort AS (
  SELECT DISTINCT h3
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  WHERE susceptibility_label = 'fort'
),
commune_cells AS (
  SELECT cleabs, COUNT(*) AS total_cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3')
  GROUP BY cleabs
),
exposed_cells AS (
  SELECT c.cleabs, COUNT(*) AS rga_fort_cells
  FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3') c
  JOIN rga_fort r ON c.h3 = r.h3
  GROUP BY c.cleabs
)
SELECT
  c.code_insee,
  c.nom_officiel,
  c.code_dep,
  c.population,
  e.rga_fort_cells,
  t.total_cells,
  ROUND(100.0 * e.rga_fort_cells / t.total_cells, 1) AS pct_rga_fort
FROM IDENTIFIER(:catalog || '.catnat_silver.admin_communes') c
JOIN exposed_cells e ON c.cleabs = e.cleabs
JOIN commune_cells t ON c.cleabs = t.cleabs
ORDER BY pct_rga_fort DESC, c.population DESC
LIMIT 15;
