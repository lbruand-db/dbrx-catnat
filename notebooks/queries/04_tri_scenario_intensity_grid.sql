-- TRI hazard inventory: number of TRIs and H3 r=9 cells per
-- (scenario × intensity) cell of the EU Floods Directive grid. Useful as
-- the opening slide of the "what data do we even have" demo step.
--
-- Sample mode pulls 30 features per layer; for `--full` runs this gives
-- the national footprint of every cell.

SELECT
  scenario_code,
  scenario_label,
  intensity_code,
  intensity_label,
  COUNT(DISTINCT id_tri) AS n_tris,
  COUNT(*)               AS n_h3_cells
FROM IDENTIFIER(:catalog || '.catnat_gold.hazard_tri_flood_h3')
GROUP BY scenario_code, scenario_label, intensity_code, intensity_label
ORDER BY scenario_code, intensity_code;
