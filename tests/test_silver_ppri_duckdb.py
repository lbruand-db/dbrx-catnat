"""Silver PPRI notebook → DuckDB.

Verifies the `dd-MM-yyyy` date parsing via `TRY_TO_DATE`, the geometry-validity
filter, and the centroid H3 r=7 column on a synthetic bronze with both
approuv + prescrit rows.
"""

from __future__ import annotations

from pathlib import Path

from catnat.duck import DuckRunner

_SEED_BRONZE = """
CREATE SCHEMA IF NOT EXISTS catnat_bronze;
CREATE SCHEMA IF NOT EXISTS catnat_silver;

CREATE OR REPLACE TABLE catnat_bronze.hazard_ppri_communes AS
SELECT
  gml_id,
  cod_nat_pprn,
  lib_pprn,
  list_lib_risque_long,
  lib_bassin_risques,
  cod_commune,
  lib_commune,
  status,
  dat_prescription_raw,
  dat_approbation_raw,
  dat_modification_raw,
  dat_appli_ant_raw,
  dat_annexion_plu_raw,
  ST_GeomFromText(wkt) AS geometry,
  TIMESTAMP '2026-05-29 12:00:00' AS _ingested_at,
  '/test/ppri.geojsonl' AS _source_file
FROM (VALUES
  ('gml.1', '01DDT19970011', 'PPRi Tenay',     'Inondation', 'Albarine',  '01416', 'Tenay',     'approuv',  '01-10-1996', '10-01-1997', '',           '', '',
   'POLYGON((5.49 45.89, 5.54 45.89, 5.54 45.95, 5.49 45.95, 5.49 45.89))'),
  ('gml.2', '75DDT20100022', 'PPRi Paris-Est', 'Inondation', 'Seine',     '75112', 'Paris 12e', 'approuv',  '15-03-2005', '02-07-2010', '20-11-2018', '', '',
   'POLYGON((2.40 48.83, 2.45 48.83, 2.45 48.85, 2.40 48.85, 2.40 48.83))'),
  ('gml.3', '69DDT20180033', 'PPRi Saône',     'Inondation', 'Saône',     '69123', 'Lyon',      'prescrit', '12-06-2018', '',           '',           '', '',
   'POLYGON((4.83 45.74, 4.86 45.74, 4.86 45.78, 4.83 45.78, 4.83 45.74))'),
  ('gml.4', '13DDT19990044', 'PPRi Camargue',  'Inondation', 'Rhône-aval','13001', 'Aix-Arles', 'prescrit', '08-04-1999', '',           '',           '', '',
   'POLYGON EMPTY')
) AS t(gml_id, cod_nat_pprn, lib_pprn, list_lib_risque_long, lib_bassin_risques,
       cod_commune, lib_commune, status,
       dat_prescription_raw, dat_approbation_raw, dat_modification_raw,
       dat_appli_ant_raw, dat_annexion_plu_raw, wkt);
"""


def test_silver_ppri_parses_dates_and_filters_geoms(
    runner: DuckRunner, notebooks_dir: Path
) -> None:
    runner.execute(_SEED_BRONZE)

    notebook = notebooks_dir / "silver" / "20_ppri_communes.sql"
    runner.run_notebook(notebook, params={"catalog": "memory"})

    # 1. The empty-geometry row was dropped; 3 remain.
    n = runner.query("SELECT COUNT(*) FROM catnat_silver.hazard_ppri_communes")
    assert n[0][0] == 3

    # 2. Date parsing: the approved Tenay PPR has prescription 01-10-1996.
    tenay = runner.query("""
        SELECT dat_prescription, dat_approbation
        FROM catnat_silver.hazard_ppri_communes
        WHERE cod_nat_pprn = '01DDT19970011'
    """)
    assert str(tenay[0][0]) == "1996-10-01"
    assert str(tenay[0][1]) == "1997-01-10"

    # 3. Both statuses survive.
    statuses = runner.query("""
        SELECT status, COUNT(*) FROM catnat_silver.hazard_ppri_communes
        GROUP BY status ORDER BY status
    """)
    assert statuses == [("approuv", 2), ("prescrit", 1)]

    # 4. Empty / unparseable date strings become NULL via TRY_TO_DATE.
    null_dates = runner.query("""
        SELECT COUNT(*) FROM catnat_silver.hazard_ppri_communes
        WHERE dat_approbation IS NULL
    """)
    assert null_dates[0][0] == 1  # the prescrit Lyon row

    # 5. Centroid H3 r=7 populated for every surviving row.
    h3_count = runner.query("""
        SELECT COUNT(*) FROM catnat_silver.hazard_ppri_communes
        WHERE centroid_h3_r7 IS NOT NULL
    """)
    assert h3_count[0][0] == 3
