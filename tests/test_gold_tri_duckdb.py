"""Gold TRI flood-hazard H3 polyfill → DuckDB.

Verifies the polyfill produces a non-empty mart per scenario × intensity cell,
and that the TRI metadata (`id_tri`, `cours_deau`, `typ_inond`) survives the
projection.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;
CREATE SCHEMA IF NOT EXISTS catnat_gold;

CREATE OR REPLACE TABLE catnat_bronze.hazard_tri_flood AS
SELECT
  gml_id, id::BIGINT AS id, id_s_inond, typ_inond, typ_inond2, scenario,
  datentree_raw, datsortie_raw, cours_deau, est_ref, id_tri,
  scenario_code, intensity_code,
  ST_GeomFromText(wkt) AS geometry,
  TIMESTAMP '2026-05-29 12:00:00' AS _ingested_at,
  '/test/tri.geojsonl' AS _source_file
FROM (VALUES
  ('gml.X', 10, 'SIN_10', '01', NULL, '01Freq', '2013-12-06', '2014-08-18', 'Saône',  't', 'FRE_TRI_LYON', '01', '01FOR',
   'POLYGON((4.83 45.73, 4.88 45.73, 4.88 45.78, 4.83 45.78, 4.83 45.73))'),
  ('gml.Y', 20, 'SIN_20', '01', NULL, '02Moy',  '2013-12-06', '2014-08-18', 'Saône',  't', 'FRE_TRI_LYON', '01', '02MOY',
   'POLYGON((4.88 45.73, 4.93 45.73, 4.93 45.78, 4.88 45.78, 4.88 45.73))'),
  ('gml.Z', 30, 'SIN_30', '03', NULL, '03Ext',  '2013-12-06', NULL,         'Seine',  't', 'FRE_TRI_PARIS', '03', '04FAI',
   'POLYGON((2.34 48.84, 2.39 48.84, 2.39 48.89, 2.34 48.89, 2.34 48.84))')
) AS t(gml_id, id, id_s_inond, typ_inond, typ_inond2, scenario,
       datentree_raw, datsortie_raw, cours_deau, est_ref, id_tri,
       scenario_code, intensity_code, wkt);
"""


def test_gold_tri_h3_polyfill_preserves_grid(runner: DuckRunner, notebooks_dir: Path) -> None:
    runner.execute(_SEED_BRONZE)
    runner.run_notebook(
        notebooks_dir / "silver" / "30_tri_flood.sql",
        params={"catalog": "memory"},
    )
    runner.run_notebook(
        notebooks_dir / "gold" / "30_tri_flood_h3.sql",
        params={"catalog": "memory", "resolution": "9"},
    )

    # 1. Mart non-empty; every source polygon got a cell.
    n_cells, n_footprints = runner.query("""
        SELECT COUNT(*), COUNT(DISTINCT gml_id)
        FROM catnat_gold.hazard_tri_flood_h3
    """)[0]
    assert n_cells > 0
    assert n_footprints == 3

    # 2. Per (scenario, intensity) cell, count is sane for ~0.05° polygons at r=9.
    by_cell = runner.query("""
        SELECT scenario_code, intensity_code, COUNT(*) AS cells
        FROM catnat_gold.hazard_tri_flood_h3
        GROUP BY scenario_code, intensity_code
        ORDER BY scenario_code, intensity_code
    """)
    assert len(by_cell) == 3
    for _, _, cells in by_cell:
        assert 10 < cells < 100_000

    # 3. Labels and metadata survive the polyfill.
    labels = runner.query("""
        SELECT DISTINCT scenario_label, intensity_label
        FROM catnat_gold.hazard_tri_flood_h3
        ORDER BY scenario_label, intensity_label
    """)
    assert ("frequent", "fort") in labels
    assert ("frequent", "moyen") in labels
    assert ("extreme", "faible") in labels

    # 4. TRI id + water body carry through.
    seine = runner.query("""
        SELECT DISTINCT id_tri, cours_deau
        FROM catnat_gold.hazard_tri_flood_h3
        WHERE scenario_code = '03'
    """)
    assert seine == [("FRE_TRI_PARIS", "Seine")]
