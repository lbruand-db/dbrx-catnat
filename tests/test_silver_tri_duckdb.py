"""Silver TRI flood-hazard notebook → DuckDB.

Covers scenario × intensity × flood-type label mapping, boolean cast of
`est_ref`, ISO date casting via `TRY_CAST(... AS DATE)`, and the H3 r=7
centroid column.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

# 6 valid polygons spanning the scenario × intensity grid + 1 empty geom that
# silver must drop. Coordinates are chosen so centroids fall in different H3
# r=7 cells across France.
_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;

CREATE OR REPLACE TABLE catnat_bronze.hazard_tri_flood AS
SELECT
  gml_id, id::BIGINT AS id, id_s_inond, typ_inond, typ_inond2, scenario,
  datentree_raw, datsortie_raw, cours_deau, est_ref, id_tri,
  scenario_code, intensity_code,
  ST_GeomFromText(wkt) AS geometry,
  TIMESTAMP '2026-05-29 12:00:00' AS _ingested_at,
  '/test/tri.geojsonl' AS _source_file
FROM (VALUES
  ('gml.A', 1, 'SIN_1', '01', NULL, '01Freq', '2013-12-06', '2014-08-18', 'Albarine',   't', 'FRE_TRI_LYON', '01', '01FOR',
   'POLYGON((4.83 45.73, 4.86 45.73, 4.86 45.76, 4.83 45.76, 4.83 45.73))'),
  ('gml.B', 2, 'SIN_2', '01', NULL, '02Moy',  '2013-12-06', '2014-08-18', 'Saône',      't', 'FRE_TRI_LYON', '01', '02MOY',
   'POLYGON((4.86 45.73, 4.89 45.73, 4.89 45.76, 4.86 45.76, 4.86 45.73))'),
  ('gml.C', 3, 'SIN_3', '01', NULL, '03Ext',  '2013-12-06', '2014-08-18', 'Saône',      'f', 'FRE_TRI_LYON', '01', '04FAI',
   'POLYGON((4.89 45.73, 4.92 45.73, 4.92 45.76, 4.89 45.76, 4.89 45.73))'),
  ('gml.D', 4, 'SIN_4', '02', NULL, '02Moy',  '2013-12-06', NULL,         NULL,         't', 'FRE_TRI_BASTIA', '02', '02MOY',
   'POLYGON((9.43 42.69, 9.46 42.69, 9.46 42.72, 9.43 42.72, 9.43 42.69))'),
  ('gml.E', 5, 'SIN_5', '03', NULL, '03Ext',  '2013-12-06', '2014-08-18', 'Seine',      't', 'FRE_TRI_PARIS', '03', '03MCC',
   'POLYGON((2.34 48.84, 2.37 48.84, 2.37 48.87, 2.34 48.87, 2.34 48.84))'),
  ('gml.F', 6, 'SIN_6', '03', NULL, '03Ext',  '2013-12-06', '2014-08-18', 'Seine',      't', 'FRE_TRI_PARIS', '03', '04FAI',
   'POLYGON((2.37 48.84, 2.40 48.84, 2.40 48.87, 2.37 48.87, 2.37 48.84))'),
  ('gml.G', 7, 'SIN_7', '02', NULL, '02Moy',  '2013-12-06', '2014-08-18', 'Vilaine',    'f', 'FRE_TRI_RENNES', '02', '01FOR',
   'POLYGON EMPTY')
) AS t(gml_id, id, id_s_inond, typ_inond, typ_inond2, scenario,
       datentree_raw, datsortie_raw, cours_deau, est_ref, id_tri,
       scenario_code, intensity_code, wkt);
"""


def test_silver_tri_labels_dates_and_filters(runner: DuckRunner, notebooks_dir: Path) -> None:
    runner.execute(_SEED_BRONZE)

    runner.run_notebook(
        notebooks_dir / "silver" / "30_tri_flood.sql",
        params={"catalog": "memory"},
    )

    # 1. Empty geometry dropped; 6 remain.
    n = runner.query("SELECT COUNT(*) FROM catnat_silver.hazard_tri_flood")
    assert n[0][0] == 6

    # 2. Scenario labels map correctly.
    scenarios = runner.query("""
        SELECT scenario_code, scenario_label, COUNT(*)
        FROM catnat_silver.hazard_tri_flood
        GROUP BY scenario_code, scenario_label
        ORDER BY scenario_code
    """)
    assert scenarios == [
        ("01", "frequent", 3),
        ("02", "moyen", 1),
        ("03", "extreme", 2),
    ]

    # 3. Intensity labels map correctly.
    intensities = runner.query("""
        SELECT intensity_code, intensity_label
        FROM catnat_silver.hazard_tri_flood
        ORDER BY intensity_code
    """)
    assert ("01FOR", "fort") in intensities
    assert ("02MOY", "moyen") in intensities
    assert ("03MCC", "mcc") in intensities
    assert ("04FAI", "faible") in intensities

    # 4. Flood-type labels resolve.
    types = runner.query("""
        SELECT DISTINCT typ_inond, typ_inond_label
        FROM catnat_silver.hazard_tri_flood
        ORDER BY typ_inond
    """)
    assert types == [
        ("01", "debordement_cours_deau"),
        ("02", "submersion_marine"),
        ("03", "ruissellement"),
    ]

    # 5. `est_ref` becomes a boolean.
    refs = runner.query("""
        SELECT gml_id, is_reference
        FROM catnat_silver.hazard_tri_flood
        WHERE gml_id IN ('gml.A', 'gml.C')
        ORDER BY gml_id
    """)
    assert refs == [("gml.A", True), ("gml.C", False)]

    # 6. ISO dates cast to DATE; NULLs survive.
    dates = runner.query("""
        SELECT gml_id, datentree, datsortie
        FROM catnat_silver.hazard_tri_flood
        WHERE gml_id IN ('gml.A', 'gml.D')
        ORDER BY gml_id
    """)
    assert str(dates[0][1]) == "2013-12-06"
    assert str(dates[0][2]) == "2014-08-18"
    assert dates[1][2] is None  # gml.D had NULL datsortie

    # 7. Centroid H3 r=7 populated for every survivor.
    h3_count = runner.query("""
        SELECT COUNT(*) FROM catnat_silver.hazard_tri_flood
        WHERE centroid_h3_r7 IS NOT NULL
    """)
    assert h3_count[0][0] == 6
