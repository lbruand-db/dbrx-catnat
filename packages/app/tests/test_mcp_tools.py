"""End-to-end MCP tool tests via the in-memory client transport.

Stubs the warehouse SDK at `tools.get_app_sql` and at `allowlist`
indirectly (the allowlist call shares the same `Sql` stub). Verifies the
tools round-trip parameters correctly and shape the response.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp import _sql_client as _sql_client_mod
from catnat_app.backend.mcp import mcp_server
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from mcp.shared.memory import create_connected_server_and_client_session


def _make_stub(side_effect_per_call: list[tuple[list[str], list[list[object]]]]) -> Sql:
    """Build a Sql stub whose responses cycle through (columns, rows) tuples.

    Each MCP tool call typically issues two warehouse statements: a layer
    lookup against `layer_index`, then the actual data query. The stub
    cycles per call so we can script those two responses.
    """
    stub = MagicMock(spec=Sql)
    responses = []
    for cols, rows in side_effect_per_call:
        r = MagicMock()
        r.status.state = StatementState.SUCCEEDED
        r.status.error = None
        r.result.data_array = rows
        r.manifest.schema.columns = [MagicMock(name=c) for c in cols]
        # MagicMock auto-creates the `.name` attribute as a Mock, override it.
        for col_mock, name in zip(r.manifest.schema.columns, cols, strict=True):
            col_mock.name = name
        responses.append(r)
    stub.execute_statement = MagicMock(side_effect=responses)
    return stub


def _override(monkeypatch: pytest.MonkeyPatch, sql: Sql) -> None:
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, "test_catalog"))


def _allowed_layer_row(
    *,
    layer_id: str = "hazard_ppri_communes",
    table_fq: str = "test_catalog.catnat_silver.hazard_ppri_communes",
    peril: str = "flood",
    grain: str = "polygon",
    h3_column: object = None,
    geom_column: object = "geometry",
    is_displayable: object = True,
) -> list[list[object]]:
    return [
        [
            layer_id,
            table_fq,
            peril,
            "silver",
            grain,
            h3_column,
            geom_column,
            is_displayable,
        ]
    ]


# --- query_layer --------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_query_layer_returns_shaped_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _make_stub(
        [
            # 1. Allowlist lookup
            (
                [],
                _allowed_layer_row(),
            ),
            # 2. The actual query: 2 rows, 3 cols
            (
                ["geometry_geojson", "layer_id", "code_dep"],
                [
                    [
                        '{"type":"Polygon","coordinates":[[[4.85,45.75]]]}',
                        "hazard_ppri_communes",
                        "069",
                    ],
                    [
                        '{"type":"Polygon","coordinates":[[[4.86,45.76]]]}',
                        "hazard_ppri_communes",
                        "069",
                    ],
                ],
            ),
        ]
    )
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(
            "query_layer",
            arguments={"layer_id": "hazard_ppri_communes", "limit": 50},
        )
    assert not result.isError, f"tool error: {result.content}"
    payload = result.structuredContent
    assert payload is not None
    assert payload["layer_id"] == "hazard_ppri_communes"
    assert payload["row_count"] == 2
    assert payload["truncated"] is False
    assert payload["columns"] == ["geometry_geojson", "layer_id", "code_dep"]
    assert payload["rows"][0]["code_dep"] == "069"


@pytest.mark.anyio("asyncio")
async def test_query_layer_passes_bbox_as_wkt_param(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _make_stub(
        [
            ([], _allowed_layer_row()),
            (["geometry_geojson"], [['{"type":"Polygon"}']]),
        ]
    )
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        await session.call_tool(
            "query_layer",
            arguments={
                "layer_id": "hazard_ppri_communes",
                "bbox": [4.7, 45.6, 5.0, 45.9],
            },
        )

    # Second call is the data query; it must carry a bbox_wkt parameter.
    data_call = sql.execute_statement.call_args_list[1]
    params = data_call.kwargs["parameters"]
    bbox_param = next((p for p in params if p.name == "bbox_wkt"), None)
    assert bbox_param is not None
    assert isinstance(bbox_param, StatementParameterListItem)
    assert bbox_param.value.startswith("POLYGON((4.7 45.6")


@pytest.mark.anyio("asyncio")
async def test_query_layer_rejects_unknown_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Allowlist lookup returns no rows — `LayerNotAllowed` becomes a ValueError
    # which FastMCP surfaces as a tool error (isError=True).
    sql = _make_stub([([], [])])
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool("query_layer", arguments={"layer_id": "no_such_layer"})
    assert result.isError
    text_block = next(c for c in result.content if c.type == "text")
    assert "unknown layer" in text_block.text


# --- intersect_layer ----------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_intersect_layer_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _make_stub(
        [
            ([], _allowed_layer_row()),
            (
                ["geometry_geojson", "layer_id"],
                [['{"type":"Polygon"}', "hazard_ppri_communes"]],
            ),
        ]
    )
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(
            "intersect_layer",
            arguments={
                "layer_id": "hazard_ppri_communes",
                "geom_wkt": "POINT(4.85 45.75)",
            },
        )
    assert not result.isError
    payload = result.structuredContent
    assert payload is not None
    assert payload["row_count"] == 1


# --- nearest ------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_nearest_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _make_stub(
        [
            ([], _allowed_layer_row()),
            (
                ["geometry_geojson", "layer_id", "distance_deg"],
                [
                    ['{"type":"Polygon"}', "a", 0.001],
                    ['{"type":"Polygon"}', "b", 0.003],
                ],
            ),
        ]
    )
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(
            "nearest",
            arguments={
                "layer_id": "hazard_ppri_communes",
                "point_wkt": "POINT(4.85 45.75)",
                "k": 2,
            },
        )
    assert not result.isError
    payload = result.structuredContent
    assert payload is not None
    assert payload["row_count"] == 2
    assert payload["rows"][0]["distance_deg"] < payload["rows"][1]["distance_deg"]


# --- buffer -------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_buffer_returns_wkt(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = _make_stub(
        [
            (
                ["buffered_wkt"],
                [["POLYGON((4.84 45.74, 4.86 45.74, 4.86 45.76, 4.84 45.76, 4.84 45.74))"]],
            ),
        ]
    )
    _override(monkeypatch, sql)
    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool(
            "buffer",
            arguments={"geom_wkt": "POINT(4.85 45.75)", "meters": 1000},
        )
    assert not result.isError
    payload = result.structuredContent
    assert payload is not None
    assert payload["meters"] == 1000
    assert payload["buffered_wkt"].startswith("POLYGON")
