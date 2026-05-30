"""Silver IGN admin_communes view → DuckDB.

Seeds a synthetic `ign_bdtopo.commune_dedup` table shaped like dbtopo-bricks
output, runs the silver view definition, and asserts the projected columns,
geometry-validity filter, and centroid H3 r=7 column.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

# Synthetic dbtopo-bricks output: 3 valid Rhône (69) communes + 1 empty geom
# that silver should drop.
_SEED = """
CREATE SCHEMA IF NOT EXISTS ign_bdtopo;
CREATE SCHEMA IF NOT EXISTS catnat_silver;

CREATE OR REPLACE TABLE ign_bdtopo.commune_dedup AS
SELECT
  cleabs, code_insee, code_insee_du_departement, code_insee_de_la_region,
  nom_officiel, nom_usuel, population::INT AS population, dept,
  ST_GeomFromText(wkt) AS geometry
FROM (VALUES
  ('COMMUNE_00001', '69123', '069', '84', 'Lyon',           'Lyon',           513275, '069',
   'POLYGON((4.80 45.71, 4.90 45.71, 4.90 45.79, 4.80 45.79, 4.80 45.71))'),
  ('COMMUNE_00002', '69266', '069', '84', 'Villeurbanne',   'Villeurbanne',   154848, '069',
   'POLYGON((4.88 45.76, 4.92 45.76, 4.92 45.78, 4.88 45.78, 4.88 45.76))'),
  ('COMMUNE_00003', '69244', '069', '84', 'Saint-Priest',   'Saint-Priest',    45995, '069',
   'POLYGON((4.93 45.69, 4.97 45.69, 4.97 45.73, 4.93 45.73, 4.93 45.69))'),
  ('COMMUNE_00099', '69ZZZ', '069', '84', 'Empty commune',  'Empty',               0, '069',
   'POLYGON EMPTY')
) AS t(cleabs, code_insee, code_insee_du_departement, code_insee_de_la_region,
       nom_officiel, nom_usuel, population, dept, wkt);
"""


def test_silver_ign_projects_columns_and_filters_empty(
    runner: DuckRunner, notebooks_dir: Path
) -> None:
    runner.execute(_SEED)
    runner.run_notebook(
        notebooks_dir / "silver" / "40_ign_communes.sql",
        params={"catalog": "memory", "ign_schema": "ign_bdtopo"},
    )

    # 1. Empty geometry dropped; 3 remain.
    n = runner.query("SELECT COUNT(*) FROM catnat_silver.admin_communes")
    assert n[0][0] == 3

    # 2. Column renames applied (code_insee_du_departement → code_dep,
    #    code_insee_de_la_region → code_reg).
    columns = {
        row[0]
        for row in runner.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'catnat_silver' AND table_name = 'admin_communes'"
        )
    }
    assert {"code_dep", "code_reg", "centroid_h3_r7"} <= columns
    assert "code_insee_du_departement" not in columns

    # 3. Lyon's commune has the expected attributes.
    lyon = runner.query("""
        SELECT code_insee, nom_officiel, population, code_dep
        FROM catnat_silver.admin_communes
        WHERE cleabs = 'COMMUNE_00001'
    """)
    assert lyon == [("69123", "Lyon", 513275, "069")]

    # 4. Every surviving row has a non-null centroid H3 r=7.
    h3 = runner.query(
        "SELECT COUNT(*) FROM catnat_silver.admin_communes WHERE centroid_h3_r7 IS NULL"
    )
    assert h3[0][0] == 0
