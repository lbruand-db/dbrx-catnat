"""Gold IGN admin_communes H3 polyfill → DuckDB."""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

_SEED = """
CREATE SCHEMA IF NOT EXISTS ign_bdtopo;
CREATE SCHEMA IF NOT EXISTS catnat_silver;
CREATE SCHEMA IF NOT EXISTS catnat_gold;

CREATE OR REPLACE TABLE ign_bdtopo.ign_bdtopo_commune_dedup AS
SELECT
  cleabs, code_insee, code_insee_du_departement, code_insee_de_la_region,
  code_postal, nom_officiel, population::INT AS population,
  superficie_cadastrale::INT AS superficie_cadastrale, code_siren, dept,
  ST_GeomFromText(wkt) AS geometry
FROM (VALUES
  ('COMMUNE_X', '69123', '069', '84', '69001', 'Lyon',         513275, 4787, '216900514', '069',
   'POLYGON((4.80 45.71, 4.90 45.71, 4.90 45.79, 4.80 45.79, 4.80 45.71))'),
  ('COMMUNE_Y', '69266', '069', '84', '69100', 'Villeurbanne', 154848, 1448, '216902668', '069',
   'POLYGON((4.88 45.76, 4.92 45.76, 4.92 45.78, 4.88 45.78, 4.88 45.76))')
) AS t(cleabs, code_insee, code_insee_du_departement, code_insee_de_la_region,
       code_postal, nom_officiel, population, superficie_cadastrale, code_siren, dept, wkt);
"""


def test_gold_ign_polyfill_and_metadata(runner: DuckRunner, notebooks_dir: Path) -> None:
    runner.execute(_SEED)
    runner.run_notebook(
        notebooks_dir / "silver" / "40_ign_communes.sql",
        params={
            "catalog": "memory",
            "ign_schema": "ign_bdtopo",
            "ign_table_prefix": "ign_bdtopo_",
        },
    )
    runner.run_notebook(
        notebooks_dir / "gold" / "40_ign_communes_h3.sql",
        params={"catalog": "memory", "resolution": "9"},
    )

    # 1. Polyfill produced cells for every silver commune.
    n_cells, n_communes = runner.query("""
        SELECT COUNT(*), COUNT(DISTINCT cleabs)
        FROM catnat_gold.admin_communes_h3
    """)[0]
    assert n_cells > 0
    assert n_communes == 2

    # 2. Per-commune cell counts sane for ~0.05° polygons at r=9.
    per_commune = runner.query("""
        SELECT cleabs, COUNT(*) FROM catnat_gold.admin_communes_h3
        GROUP BY cleabs ORDER BY cleabs
    """)
    for _, cells in per_commune:
        assert 10 < cells < 100_000, f"unexpected cell count: {cells}"

    # 3. Lyon's population + insee carry through the polyfill.
    lyon = runner.query("""
        SELECT DISTINCT code_insee, nom_officiel, population, code_dep
        FROM catnat_gold.admin_communes_h3
        WHERE cleabs = 'COMMUNE_X'
    """)
    assert lyon == [("69123", "Lyon", 513275, "069")]
