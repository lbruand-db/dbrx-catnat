"""Silver RGA notebook → DuckDB.

Seeds a synthetic bronze table (4 valid polygons across all susceptibility
levels + 1 empty geometry), runs the silver notebook against in-memory DuckDB,
and asserts: invalid geometries are filtered, susceptibility codes map to the
right labels, and the centroid H3 r=7 cell is populated.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

# Synthetic bronze: 4 valid polygons + 1 empty geom that should be dropped.
# Polygons sit in Ariège (~lon=0.9, lat=42.8) so the centroid → H3 r=7 lookup
# returns a real cell.
_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;

CREATE OR REPLACE TABLE catnat_bronze.hazard_rga_susceptibility AS
SELECT
  gid::BIGINT          AS gid,
  insee_dep            AS insee_dep,
  susceptibility_code  AS susceptibility_code,
  id_zone_rga          AS id_zone_rga,
  gml_id               AS gml_id,
  ST_GeomFromText(wkt) AS geometry,
  TIMESTAMP '2026-05-29 12:00:00' AS _ingested_at,
  '/test/rga.geojsonl' AS _source_file
FROM (VALUES
  (1, '09', 1, 'rga_1', 'gml.1', 'POLYGON((0.80 42.80, 0.81 42.80, 0.81 42.81, 0.80 42.81, 0.80 42.80))'),
  (2, '09', 2, 'rga_2', 'gml.2', 'POLYGON((0.82 42.80, 0.83 42.80, 0.83 42.81, 0.82 42.81, 0.82 42.80))'),
  (3, '09', 3, 'rga_3', 'gml.3', 'POLYGON((0.84 42.80, 0.85 42.80, 0.85 42.81, 0.84 42.81, 0.84 42.80))'),
  (4, '09', 4, 'rga_4', 'gml.4', 'POLYGON((0.86 42.80, 0.87 42.80, 0.87 42.81, 0.86 42.81, 0.86 42.80))'),
  (5, '09', 2, 'rga_5', 'gml.5', 'POLYGON EMPTY')
) AS t(gid, insee_dep, susceptibility_code, id_zone_rga, gml_id, wkt);
"""


def test_silver_rga_filters_invalid_and_maps_labels(
    runner: DuckRunner, notebooks_dir: Path
) -> None:
    runner.execute(_SEED_BRONZE)

    notebook = notebooks_dir / "silver" / "10_rga_susceptibility.sql"
    runner.run_notebook(notebook, params={"catalog": "memory"})

    # 1. Empty geometry was dropped; 4 valid rows remain.
    rows = runner.query("SELECT COUNT(*) FROM catnat_silver.hazard_rga_susceptibility")
    assert rows[0][0] == 4

    # 2. Susceptibility codes 1..4 map to the right labels.
    label_pairs = runner.query("""
        SELECT susceptibility_code, susceptibility_label
        FROM catnat_silver.hazard_rga_susceptibility
        ORDER BY susceptibility_code
    """)
    assert label_pairs == [
        (1, "faible"),
        (2, "moyen"),
        (3, "fort"),
        (4, "tres_fort"),
    ]

    # 3. Every row got an H3 r=7 cell (non-null).
    h3_count = runner.query("""
        SELECT COUNT(*) FROM catnat_silver.hazard_rga_susceptibility
        WHERE centroid_h3_r7 IS NOT NULL
    """)
    assert h3_count[0][0] == 4

    # 4. All 4 polygons sit in the same neighborhood; their r=7 cells should
    #    coincide (or be neighbors). Sanity-check there's at most a handful.
    distinct = runner.query("""
        SELECT COUNT(DISTINCT centroid_h3_r7)
        FROM catnat_silver.hazard_rga_susceptibility
    """)
    assert distinct[0][0] <= 4
