-- Communes where all three perils (RGA, PPRI, TRI) co-occur on at least one
-- H3 r=9 cell. The set-intersect at the H3 level is the cheap way to ask
-- "where is my worst-of-the-worst exposure". One row per commune; the
-- `triple_hazard_cells` column tells you how concentrated the overlap is.

WITH
  rga AS (
    SELECT DISTINCT h3
    FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_rga_h3')
  ),
  ppri AS (
    SELECT DISTINCT h3
    FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_ppri_communes_h3')
  ),
  tri AS (
    SELECT DISTINCT h3
    FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
  ),
  triple AS (
    SELECT h3 FROM rga
    INTERSECT
    SELECT h3 FROM ppri
    INTERSECT
    SELECT h3 FROM tri
  )
SELECT
  c.code_insee,
  c.nom_officiel,
  c.code_dep,
  c.population,
  COUNT(*) AS triple_hazard_cells
FROM IDENTIFIER(:catalog || '.catnat_gold.admin_communes_h3') c
JOIN triple t ON c.h3 = t.h3
GROUP BY c.code_insee, c.nom_officiel, c.code_dep, c.population
ORDER BY triple_hazard_cells DESC
LIMIT 20;
