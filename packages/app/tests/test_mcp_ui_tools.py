"""Tests for the UI-mutating MCP tools (`backend/mcp/ui_tools.py`).

Stubs the Sql handle directly — these tests cover the payload shape,
the allowlist hookups, and the polygon-only guard on `add_layer`.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp.allowlist import LayerNotAllowed
from catnat_app.backend.mcp.ui_tools import (
    add_layer_impl,
    remove_layer_impl,
    style_layer_impl,
    zoom_to_impl,
)
from databricks.sdk.service.sql import StatementParameterListItem, StatementState


def _stub_sql_chain(*results: tuple[list[str], list[list[object]]]) -> Sql:
    """Build a stub that cycles through `(column_names, rows)` pairs across calls."""
    stub = MagicMock(spec=Sql)
    responses = []
    for cols, rows in results:
        r = MagicMock()
        r.status.state = StatementState.SUCCEEDED
        r.status.error = None
        r.result.data_array = rows
        r.manifest.schema.columns = [MagicMock(name=c) for c in cols]
        for col_mock, name in zip(r.manifest.schema.columns, cols, strict=True):
            col_mock.name = name
        responses.append(r)
    stub.execute_statement = MagicMock(side_effect=responses)
    return stub


def _allowed_polygon_layer_row() -> list[list[object]]:
    return [
        [
            "hazard_ppri_communes",
            "cat.catnat_silver.hazard_ppri_communes",
            "flood",
            "silver",
            "polygon",
            None,
            "geometry",
            True,
        ]
    ]


def _allowed_h3_layer_row() -> list[list[object]]:
    return [
        [
            "hazard_rga_h3",
            "cat.catnat_gold.hazard_rga_h3",
            "drought",
            "gold",
            "h3_r9_cell",
            "h3",
            None,
            True,
        ]
    ]


# --- add_layer --------------------------------------------------------


def test_add_layer_returns_featurecollection_with_per_peril_default_style() -> None:
    sql = _stub_sql_chain(
        # Allowlist lookup
        ([], _allowed_polygon_layer_row()),
        # add_layer query: geom_geojson, layer_id, code_dep
        (
            ["geom_geojson", "layer_id", "code_dep"],
            [
                [
                    '{"type":"Polygon","coordinates":[[[4.85,45.75],[4.86,45.75],[4.86,45.76],[4.85,45.75]]]}',
                    "hazard_ppri_communes",
                    "069",
                ],
                [
                    '{"type":"Polygon","coordinates":[[[4.87,45.77],[4.88,45.77],[4.88,45.78],[4.87,45.77]]]}',
                    "hazard_ppri_communes",
                    "069",
                ],
            ],
        ),
    )
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes")
    assert result["op"] == "add_layer"
    assert result["layer_id"] == "hazard_ppri_communes"
    assert result["peril"] == "flood"
    assert result["row_count"] == 2
    assert result["status"] == "ok"
    # FeatureCollection shape
    fc = result["geojson"]
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    assert fc["features"][0]["type"] == "Feature"
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    assert fc["features"][0]["properties"]["code_dep"] == "069"
    # Flood layer → blue-ish default style
    assert result["style"]["color"] == "#1f77b4"


def test_add_layer_uses_override_style_when_supplied() -> None:
    sql = _stub_sql_chain(
        ([], _allowed_polygon_layer_row()),
        (["geom_geojson"], []),
    )
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes", style={"color": "#ff0000"})
    assert result["style"] == {"color": "#ff0000"}


def test_add_layer_refuses_h3_grain_layer() -> None:
    sql = _stub_sql_chain(([], _allowed_h3_layer_row()))
    with pytest.raises(LayerNotAllowed, match="polygon-grain"):
        add_layer_impl(sql, "cat", "hazard_rga_h3")


def test_add_layer_skips_rows_with_null_or_invalid_geometry() -> None:
    sql = _stub_sql_chain(
        ([], _allowed_polygon_layer_row()),
        (
            ["geom_geojson"],
            [
                ['{"type":"Polygon","coordinates":[[[0,0],[1,1],[0,0]]]}'],
                [None],
                ["this is not json"],
            ],
        ),
    )
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes")
    assert result["row_count"] == 1
    assert len(result["geojson"]["features"]) == 1


def test_add_layer_applies_where_filter() -> None:
    sql = _stub_sql_chain(
        ([], _allowed_polygon_layer_row()),
        (["geom_geojson", "code_dep"], [['{"type":"Polygon","coordinates":[]}', "069"]]),
    )
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes", where={"code_dep": "069"})
    assert result["where"] == {"code_dep": "069"}
    assert result["row_count"] == 1

    # The data query (call #2) must carry `code_dep` as a bound parameter
    # and NOT inline it into the SQL string.
    data_call = sql.execute_statement.call_args_list[1]
    statement = data_call.kwargs["statement"]
    params = data_call.kwargs["parameters"]
    assert "`code_dep` = :w_code_dep" in statement
    assert "069" not in statement  # value flows only via the bind param
    code_dep_param = next(p for p in params if p.name == "w_code_dep")
    assert code_dep_param.value == "069"
    assert code_dep_param.type == "STRING"


def test_add_layer_applies_bbox_filter() -> None:
    sql = _stub_sql_chain(
        ([], _allowed_polygon_layer_row()),
        (["geom_geojson"], [['{"type":"Polygon","coordinates":[]}']]),
    )
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes", bbox=(4.5, 45.4, 5.2, 46.1))
    assert result["bbox"] == [4.5, 45.4, 5.2, 46.1]

    # ST_Intersects with bind-parameter WKT must appear in the SQL.
    data_call = sql.execute_statement.call_args_list[1]
    statement = data_call.kwargs["statement"]
    params = data_call.kwargs["parameters"]
    assert "ST_Intersects(geometry, ST_GeomFromText(:bbox_wkt, 4326))" in statement
    bbox_param = next(p for p in params if p.name == "bbox_wkt")
    assert bbox_param.value.startswith("POLYGON((4.5 45.4")


def test_add_layer_passes_table_fq_as_parameter_marker() -> None:
    sql = _stub_sql_chain(
        ([], _allowed_polygon_layer_row()),
        (["geom_geojson"], []),
    )
    add_layer_impl(sql, "cat", "hazard_ppri_communes")
    # 2nd call is the add_layer query.
    data_call = sql.execute_statement.call_args_list[1]
    params = data_call.kwargs["parameters"]
    fq = next(p for p in params if p.name == "table_fq")
    assert isinstance(fq, StatementParameterListItem)
    assert fq.value == "cat.catnat_silver.hazard_ppri_communes"


# --- remove_layer -----------------------------------------------------


def test_remove_layer_returns_op_payload_no_sql() -> None:
    out = remove_layer_impl("hazard_ppri_communes")
    assert out == {
        "op": "remove_layer",
        "layer_id": "hazard_ppri_communes",
        "status": "ok",
    }


# --- zoom_to ----------------------------------------------------------


def test_zoom_to_returns_geom_geojson() -> None:
    sql = _stub_sql_chain(
        ([], [['{"type":"Point","coordinates":[4.85,45.75]}']]),
    )
    out = zoom_to_impl(sql, geom_wkt="POINT(4.85 45.75)")
    assert out["op"] == "zoom_to"
    assert out["geom_geojson"]["type"] == "Point"
    assert out["geom_geojson"]["coordinates"] == [4.85, 45.75]


def test_zoom_to_rejects_empty_result() -> None:
    sql = _stub_sql_chain(([], [[None]]))
    with pytest.raises(ValueError, match="could not parse"):
        zoom_to_impl(sql, geom_wkt="not-wkt")


# --- style_layer ------------------------------------------------------


def test_style_layer_collects_only_supplied_fields() -> None:
    out = style_layer_impl("hazard_ppri_communes", color="#ff0000", fill_opacity=0.5)
    assert out["op"] == "style_layer"
    assert out["layer_id"] == "hazard_ppri_communes"
    assert out["style"] == {"color": "#ff0000", "fillOpacity": 0.5}


def test_style_layer_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        style_layer_impl("any")


# Quiet a flake-prone interaction between `MagicMock(spec=...)` and
# json.dumps when properties include strings — the test setup is correct
# but we surface a hint if it changes.
_ = json
