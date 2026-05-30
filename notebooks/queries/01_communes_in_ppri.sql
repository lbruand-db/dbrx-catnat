-- Top-10 Rhône communes by PPRI flood-zone overlap (cell count is a
-- reasonable proxy for area at H3 r=9). Demonstrates the canonical join
-- pattern: every gold table carries `h3` at the same resolution, so a
-- cross-layer join is a one-liner.

WITH ppri_approuv_cells AS (
  SELECT DISTINCT h3
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  WHERE status = 'approuv'
)
SELECT
  c.code_insee,
  c.nom_officiel,
  c.population,
  COUNT(*) AS h3_cells_in_ppri
FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3') c
JOIN ppri_approuv_cells p ON c.h3 = p.h3
WHERE c.code_dep = '069'
GROUP BY c.code_insee, c.nom_officiel, c.population
ORDER BY h3_cells_in_ppri DESC
LIMIT 10;
