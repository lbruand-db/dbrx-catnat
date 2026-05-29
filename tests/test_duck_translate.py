"""Pure translation unit tests for `catnat.duck.translate`.

These lock in the shape of the Databricks→DuckDB rewrites so any future fix
to the regex layer that breaks a known good translation fails loudly.
"""

from __future__ import annotations

from catnat.duck import is_skippable, translate


def test_identifier_unwrap() -> None:
    sql = "SELECT 1 FROM IDENTIFIER(:catalog || '.catnat_silver.foo')"
    out = translate(sql, {"catalog": "any_catalog"})
    assert "catnat_silver.foo" in out
    assert "IDENTIFIER" not in out
    assert ":catalog" not in out


def test_param_substitution_quotes_strings_not_numbers() -> None:
    sql = "SELECT CAST(:resolution AS INT), :input_path"
    out = translate(sql, {"resolution": "9", "input_path": "/tmp/x.geojsonl"})
    assert "CAST(9 AS INT)" in out
    assert "'/tmp/x.geojsonl'" in out


def test_h3_longlatash3_argument_swap() -> None:
    sql = "SELECT h3_longlatash3(2.35, 48.85, 9)"
    out = translate(sql, {}).upper()
    # Lat first in DuckDB; sqlglot uppercases anonymous function names on emit.
    assert "H3_LATLNG_TO_CELL(48.85, 2.35, 9)" in out


def test_h3_polyfillash3_to_wkt() -> None:
    sql = "SELECT h3_polyfillash3(ST_AsBinary(geometry), 9)"
    out = translate(sql, {}).upper()
    assert "H3_POLYGON_WKT_TO_CELLS(ST_ASTEXT(GEOMETRY), 9)" in out


def test_try_to_date_to_strptime() -> None:
    sql = "SELECT TRY_TO_DATE(d, 'dd-MM-yyyy')"
    out = translate(sql, {})
    # sqlglot serialises Cast in `CAST(... AS DATE)` form (not the ::DATE
    # shorthand) and keeps the format string verbatim.
    assert "TRY_STRPTIME(d, '%d-%m-%Y')" in out
    assert "AS DATE" in out.upper()
    assert "CAST" in out.upper()


def test_lateral_view_explode_with_nested_parens() -> None:
    sql = "SELECT cell FROM t LATERAL VIEW explode(h3_polyfillash3(ST_AsBinary(g), 9)) AS cell"
    out = translate(sql, {})
    # sqlglot translates LATERAL VIEW → CROSS JOIN UNNEST with an auto-aliased
    # table; we just check the shape and that nested-paren content survived.
    assert "LATERAL VIEW" not in out
    assert "UNNEST" in out
    assert "(cell)" in out  # the inner column name carries through


def test_tblproperties_stripped() -> None:
    sql = "CREATE OR REPLACE TABLE foo TBLPROPERTIES ('a' = 'b', 'c' = 'd') AS SELECT 1"
    out = translate(sql, {})
    assert "TBLPROPERTIES" not in out


def test_optimize_is_skippable() -> None:
    assert is_skippable("-- comment\nOPTIMIZE foo ZORDER BY (h3)")
    assert is_skippable("ALTER TABLE foo ALTER COLUMN x COMMENT 'y'")
    assert not is_skippable("SELECT 1")
