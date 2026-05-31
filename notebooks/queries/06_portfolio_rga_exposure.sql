-- Portfolio EUR exposure to "fort" RGA susceptibility, by département.
-- The H3 equi-join is what makes this cheap — both `portfolio_policies_h3`
-- and `hazard_rga_h3` are ZORDER'd on `h3` so Photon does a hash join
-- without touching geometries.

WITH rga_fort AS (
  SELECT DISTINCT h3
  FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  WHERE susceptibility_label = 'fort'
)
SELECT
  p.code_dep,
  COUNT(DISTINCT p.h3)                                    AS n_exposed_cells,
  SUM(p.n_policies)                                       AS n_policies_in_rga_fort,
  SUM(p.sum_insured_value_eur)                            AS sum_insured_in_rga_fort_eur,
  SUM(p.sum_insured_rga_eur)                              AS sum_insured_with_rga_coverage_eur
FROM IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3') p
JOIN rga_fort r ON p.h3 = r.h3
GROUP BY p.code_dep
ORDER BY sum_insured_in_rga_fort_eur DESC;
