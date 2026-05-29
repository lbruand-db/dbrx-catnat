"""Gold RGA H3 polyfill → DuckDB.

Runs the full silver → gold transition over a synthetic bronze, verifies that
`h3_polyfillash3(ST_AsBinary(geom), r)` (Databricks) is rewritten to
`h3_polygon_wkt_to_cells(ST_AsText(geom), r)` (DuckDB) and produces a
non-empty mart.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

# Slightly larger polygons (~0.05° on a side) so h3_polyfillash3 at r=9
# returns multiple cells per polygon.
_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;
CREATE SCHEMA IF NOT EXISTS catnat_gold;

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
  (10, '09', 1, 'rga_10', 'gml.10', 'POLYGON((0.80 42.80, 0.85 42.80, 0.85 42.85, 0.80 42.85, 0.80 42.80))'),
  (20, '09', 2, 'rga_20', 'gml.20', 'POLYGON((0.85 42.80, 0.90 42.80, 0.90 42.85, 0.85 42.85, 0.85 42.80))'),
  (30, '09', 3, 'rga_30', 'gml.30', 'POLYGON((0.90 42.80, 0.95 42.80, 0.95 42.85, 0.90 42.85, 0.90 42.80))')
) AS t(gid, insee_dep, susceptibility_code, id_zone_rga, gml_id, wkt);
"""


def test_gold_rga_h3_polyfill_produces_cells(runner: DuckRunner, notebooks_dir: Path) -> None:
    runner.execute(_SEED_BRONZE)
    runner.run_notebook(
        notebooks_dir / "silver" / "10_rga_susceptibility.sql",
        params={"catalog": "memory"},
    )
    runner.run_notebook(
        notebooks_dir / "gold" / "10_rga_h3.sql",
        params={"catalog": "memory", "resolution": "9"},
    )

    # 1. Mart is non-empty and every source zone produced at least one cell.
    n_cells, n_zones = runner.query("""
        SELECT COUNT(*), COUNT(DISTINCT gid)
        FROM catnat_gold.hazard_rga_h3
    """)[0]
    assert n_cells > 0
    assert n_zones == 3

    # 2. Cells per zone is sane for a ~0.05°×0.05° polygon at r=9 (expect dozens).
    per_zone = runner.query("""
        SELECT gid, COUNT(*) AS cells
        FROM catnat_gold.hazard_rga_h3
        GROUP BY gid
        ORDER BY gid
    """)
    for _, cells in per_zone:
        assert 10 < cells < 5000, f"unexpected cell count: {cells}"

    # 3. The label + code projection survives the polyfill.
    labels = runner.query("""
        SELECT DISTINCT susceptibility_code, susceptibility_label
        FROM catnat_gold.hazard_rga_h3
        ORDER BY susceptibility_code
    """)
    assert labels == [(1, "faible"), (2, "moyen"), (3, "fort")]
