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
from databricks.sdk.service.sql import StatementState


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


def test_add_layer_returns_tile_url_with_per_peril_default_style() -> None:
    sql = _stub_sql_chain(([], _allowed_polygon_layer_row()))
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes")
    assert result["op"] == "add_layer"
    assert result["layer_id"] == "hazard_ppri_communes"
    assert result["peril"] == "flood"
    assert result["status"] == "ok"
    # Tile URL is the slippy-map template the FE feeds to L.vectorGrid.protobuf.
    assert result["tile_url"] == "/api/tiles/hazard_ppri_communes/{z}/{x}/{y}.pbf"
    # No eager geojson in the response — we ship a URL, not features.
    assert "geojson" not in result
    # Flood layer → blue-ish default style
    assert result["style"]["color"] == "#1f77b4"


def test_add_layer_uses_override_style_when_supplied() -> None:
    sql = _stub_sql_chain(([], _allowed_polygon_layer_row()))
    result = add_layer_impl(sql, "cat", "hazard_ppri_communes", style={"color": "#ff0000"})
    assert result["style"] == {"color": "#ff0000"}


def test_add_layer_refuses_h3_grain_layer() -> None:
    sql = _stub_sql_chain(([], _allowed_h3_layer_row()))
    with pytest.raises(LayerNotAllowed, match="polygon-grain"):
        add_layer_impl(sql, "cat", "hazard_rga_h3")


def test_add_layer_only_consults_the_allowlist() -> None:
    """The new path doesn't fetch features — exactly one warehouse call
    (the allowlist lookup) is expected."""
    sql = _stub_sql_chain(([], _allowed_polygon_layer_row()))
    add_layer_impl(sql, "cat", "hazard_ppri_communes")
    assert sql.execute_statement.call_count == 1


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
    sql = _stub_sql_chain(([], [['{"type":"Point","coordinates":[4.85,45.75]}']]))
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


# Re-export json so the test file's `import json` doesn't get flagged
# as unused by ruff in case the existing geojson-string assertions are
# removed entirely.
_ = json
