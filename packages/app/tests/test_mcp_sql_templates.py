"""Unit tests for the parameterised SQL builders in `mcp.sql_templates`.

These never hit a warehouse — they assert on the shape of the rendered
SQL and on the bound parameter list. Anything that touches user-supplied
strings (column names, geometry WKT, where values) MUST go through a
parameter marker, never an f-string.
"""

from __future__ import annotations

import pytest
from catnat_app.backend.mcp.allowlist import AllowedLayer
from catnat_app.backend.mcp.sql_templates import (
    QUERY_LAYER_MAX_ROWS,
    build_buffer,
    build_intersect_layer,
    build_nearest,
    build_query_layer,
)
from databricks.sdk.service.sql import StatementParameterListItem


def _geom_layer() -> AllowedLayer:
    return AllowedLayer(
        layer_id="hazard_ppri_communes",
        table_fq="cat.catnat_silver.hazard_ppri_communes",
        schema="catnat_silver",
        peril="flood",
        medallion="silver",
        grain="polygon",
        geom_column="geometry",
        h3_column=None,
    )


def _h3_layer() -> AllowedLayer:
    return AllowedLayer(
        layer_id="hazard_rga_h3",
        table_fq="cat.catnat_gold.hazard_rga_h3",
        schema="catnat_gold",
        peril="drought",
        medallion="gold",
        grain="h3_r9_cell",
        geom_column=None,
        h3_column="h3",
    )


def _params_by_name(
    params: list[StatementParameterListItem],
) -> dict[str, StatementParameterListItem]:
    return {p.name: p for p in params}


# --- build_query_layer --------------------------------------------------


def test_query_layer_projects_geometry_to_bbox_not_full_geojson() -> None:
    """query_layer is analytical — geometry projects to a bbox (4 floats)
    so a 500-row response can't blow the LLM's 1 M-token budget. The
    full ST_AsGeoJSON shape is reserved for intersect_layer / nearest
    (single-feature lookups, not surveys)."""
    b = build_query_layer(_geom_layer())
    assert "ST_XMin(geometry) AS geometry_xmin" in b.statement
    assert "ST_YMin(geometry) AS geometry_ymin" in b.statement
    assert "ST_XMax(geometry) AS geometry_xmax" in b.statement
    assert "ST_YMax(geometry) AS geometry_ymax" in b.statement
    # Crucially: no ST_AsGeoJSON in the query_layer projection.
    assert "ST_AsGeoJSON" not in b.statement
    assert "* EXCEPT (geometry)" in b.statement
    assert "FROM IDENTIFIER(:table_fq)" in b.statement


def test_intersect_layer_still_projects_full_geojson() -> None:
    """intersect_layer is a single-feature lookup — the agent asked for
    geometry, give it the geometry. Capped at 500 rows by limit."""
    b = build_intersect_layer(_geom_layer(), geom_wkt="POINT(4.85 45.75)")
    assert "ST_AsGeoJSON(geometry) AS geometry_geojson" in b.statement


def test_query_layer_projects_h3_to_hex() -> None:
    b = build_query_layer(_h3_layer())
    assert "h3_h3tostring(h3) AS h3_hex" in b.statement
    assert "* EXCEPT (h3)" in b.statement


def test_query_layer_bbox_uses_st_intersects_with_param_marker() -> None:
    b = build_query_layer(_geom_layer(), bbox=(4.7, 45.6, 5.0, 45.9), limit=50)
    assert "ST_Intersects(geometry, ST_GeomFromText(:bbox_wkt, 4326))" in b.statement
    params = _params_by_name(b.parameters)
    assert "bbox_wkt" in params
    # WKT closes the ring back to the first point.
    assert params["bbox_wkt"].value.startswith("POLYGON((")
    assert params["bbox_wkt"].value.count(",") == 4
    # Geometry literal never f-stringed into the statement.
    assert "POLYGON" not in b.statement


def test_query_layer_bbox_rejected_on_h3_layer() -> None:
    with pytest.raises(ValueError, match="bbox filter not supported"):
        build_query_layer(_h3_layer(), bbox=(0.0, 0.0, 1.0, 1.0))


def test_query_layer_where_uses_param_markers_per_predicate() -> None:
    b = build_query_layer(
        _geom_layer(),
        where={"code_dep": "069", "is_red_zone": True, "policy_count": 5},
    )
    assert "`code_dep` = :w_code_dep" in b.statement
    assert "`is_red_zone` = :w_is_red_zone" in b.statement
    assert "`policy_count` = :w_policy_count" in b.statement
    assert b.statement.count(" AND ") == 2  # 3 predicates → 2 joiners
    params = _params_by_name(b.parameters)
    assert params["w_code_dep"].value == "069"
    assert params["w_code_dep"].type == "STRING"
    assert params["w_is_red_zone"].value == "true"
    assert params["w_is_red_zone"].type == "BOOLEAN"
    assert params["w_policy_count"].value == "5"
    assert params["w_policy_count"].type == "INT"


def test_query_layer_where_rejects_unsafe_column_names() -> None:
    with pytest.raises(ValueError, match="unsafe identifier"):
        build_query_layer(_geom_layer(), where={"col; DROP TABLE x": 1})


def test_query_layer_limit_clamps_to_max() -> None:
    b = build_query_layer(_geom_layer(), limit=10_000)
    assert f"LIMIT {QUERY_LAYER_MAX_ROWS}" in b.statement


def test_query_layer_limit_clamps_to_one() -> None:
    b = build_query_layer(_geom_layer(), limit=-5)
    assert "LIMIT 1" in b.statement


# --- build_intersect_layer ----------------------------------------------


def test_intersect_layer_requires_geom_column() -> None:
    with pytest.raises(ValueError, match="requires a geom_column"):
        build_intersect_layer(_h3_layer(), geom_wkt="POINT(0 0)")


def test_intersect_layer_emits_st_intersects_with_param() -> None:
    b = build_intersect_layer(_geom_layer(), geom_wkt="POINT(4.85 45.75)")
    assert "ST_Intersects(geometry, ST_GeomFromText(:geom_wkt, 4326))" in b.statement
    params = _params_by_name(b.parameters)
    assert params["geom_wkt"].value == "POINT(4.85 45.75)"


# --- build_nearest ------------------------------------------------------


def test_nearest_requires_geom_column() -> None:
    with pytest.raises(ValueError, match="requires a geom_column"):
        build_nearest(_h3_layer(), point_wkt="POINT(0 0)")


def test_nearest_orders_by_st_distance_and_limits_k() -> None:
    b = build_nearest(_geom_layer(), point_wkt="POINT(4.85 45.75)", k=7)
    assert "ST_Distance(geometry, ST_GeomFromText(:point_wkt, 4326)) AS distance_deg" in b.statement
    assert "ORDER BY distance_deg ASC" in b.statement
    assert "LIMIT 7" in b.statement


def test_nearest_clamps_k_to_100() -> None:
    b = build_nearest(_geom_layer(), point_wkt="POINT(0 0)", k=999)
    assert "LIMIT 100" in b.statement


# --- build_buffer -------------------------------------------------------


def test_buffer_emits_st_buffer_with_degree_param() -> None:
    b = build_buffer(geom_wkt="POINT(4.85 45.75)", meters=1000)
    assert "ST_Buffer(ST_GeomFromText(:geom_wkt, 4326), :degrees)" in b.statement
    params = _params_by_name(b.parameters)
    # 1000m / 85_000 m-per-degree ≈ 0.01176 deg
    assert 0.011 < float(params["degrees"].value) < 0.013
    assert params["degrees"].type == "DOUBLE"
