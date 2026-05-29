"""Gold PPRI commune H3 polyfill → DuckDB.

Bigger polygons (~0.05° on a side) so polyfill at r=9 yields multiple cells.
Asserts that `status` and `cod_commune` survive the polyfill and that both
PPR-status flavours produce non-empty cell sets.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;
CREATE SCHEMA IF NOT EXISTS catnat_gold;

CREATE OR REPLACE TABLE catnat_bronze.hazard_ppri_communes AS
SELECT
  gml_id, cod_nat_pprn, lib_pprn, list_lib_risque_long, lib_bassin_risques,
  cod_commune, lib_commune, status,
  dat_prescription_raw, dat_approbation_raw, dat_modification_raw,
  dat_appli_ant_raw, dat_annexion_plu_raw,
  ST_GeomFromText(wkt) AS geometry,
  TIMESTAMP '2026-05-29 12:00:00' AS _ingested_at,
  '/test/ppri.geojsonl' AS _source_file
FROM (VALUES
  ('gml.A', '01DDT19970011', 'PPRi Tenay', 'Inondation', 'Albarine', '01416', 'Tenay',  'approuv',
   '01-10-1996', '10-01-1997', '', '', '',
   'POLYGON((5.49 45.89, 5.54 45.89, 5.54 45.94, 5.49 45.94, 5.49 45.89))'),
  ('gml.B', '75DDT20100022', 'PPRi Paris', 'Inondation', 'Seine',    '75112', 'Paris',  'approuv',
   '15-03-2005', '02-07-2010', '20-11-2018', '', '',
   'POLYGON((2.40 48.83, 2.45 48.83, 2.45 48.87, 2.40 48.87, 2.40 48.83))'),
  ('gml.C', '69DDT20180033', 'PPRi Lyon',  'Inondation', 'Saône',    '69123', 'Lyon',   'prescrit',
   '12-06-2018', '', '', '', '',
   'POLYGON((4.83 45.73, 4.88 45.73, 4.88 45.78, 4.83 45.78, 4.83 45.73))')
) AS t(gml_id, cod_nat_pprn, lib_pprn, list_lib_risque_long, lib_bassin_risques,
       cod_commune, lib_commune, status,
       dat_prescription_raw, dat_approbation_raw, dat_modification_raw,
       dat_appli_ant_raw, dat_annexion_plu_raw, wkt);
"""


def test_gold_ppri_h3_polyfill_carries_status_and_commune(
    runner: DuckRunner, notebooks_dir: Path
) -> None:
    runner.execute(_SEED_BRONZE)
    runner.run_notebook(
        notebooks_dir / "silver" / "20_ppri_communes.sql",
        params={"catalog": "memory"},
    )
    runner.run_notebook(
        notebooks_dir / "gold" / "20_ppri_communes_h3.sql",
        params={"catalog": "memory", "resolution": "9"},
    )

    # 1. Mart non-empty; every source commune produced at least one cell.
    n_cells, n_communes = runner.query("""
        SELECT COUNT(*), COUNT(DISTINCT cod_commune)
        FROM catnat_gold.hazard_ppri_communes_h3
    """)[0]
    assert n_cells > 0
    assert n_communes == 3

    # 2. Both statuses present in the mart.
    by_status = runner.query("""
        SELECT status, COUNT(*) AS cells, COUNT(DISTINCT cod_commune) AS communes
        FROM catnat_gold.hazard_ppri_communes_h3
        GROUP BY status ORDER BY status
    """)
    assert [s for s, _, _ in by_status] == ["approuv", "prescrit"]
    for _, cells, communes in by_status:
        assert cells > 0
        assert communes > 0

    # 3. Per-commune cell counts are sane for ~0.05° polygons at r=9.
    per_commune = runner.query("""
        SELECT cod_commune, COUNT(*) AS cells
        FROM catnat_gold.hazard_ppri_communes_h3
        GROUP BY cod_commune
        ORDER BY cod_commune
    """)
    for _, cells in per_commune:
        assert 10 < cells < 50_000, f"unexpected cell count: {cells}"

    # 4. Approbation date is carried through (when present).
    paris = runner.query("""
        SELECT DISTINCT dat_approbation
        FROM catnat_gold.hazard_ppri_communes_h3
        WHERE cod_commune = '75112'
    """)
    assert str(paris[0][0]) == "2010-07-02"
