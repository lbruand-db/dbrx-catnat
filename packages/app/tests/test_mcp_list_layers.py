"""End-to-end test of the MCP `list_layers` tool.

Uses `mcp.shared.memory.create_connected_server_and_client_session` to
exercise the FastMCP server through an in-memory transport, then checks
that:

- the tool is discoverable via `list_tools`,
- a call returns a list of layer dicts shaped like the underlying SQL,
- the SQL is parameterised (no string interpolation of the catalog).

The actual warehouse call is short-circuited by monkey-patching
`get_app_sql` to return a `Sql` stub that emits a canned response.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp import _sql_client as _sql_client_mod
from catnat_app.backend.mcp import mcp_server
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from mcp.shared.memory import create_connected_server_and_client_session


def _make_sql_stub(rows: list[list[object]]) -> Sql:
    stub = MagicMock(spec=Sql)
    response = MagicMock()
    response.status.state = StatementState.SUCCEEDED
    response.status.error = None
    response.result.data_array = rows
    stub.execute_statement = MagicMock(return_value=response)
    return stub


def _override_sql(monkeypatch: pytest.MonkeyPatch, sql: Sql, catalog: str) -> None:
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, catalog))


@pytest.mark.anyio("asyncio")
async def test_list_layers_tool_is_registered() -> None:
    async with create_connected_server_and_client_session(mcp_server) as session:
        tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    assert "list_layers" in names


@pytest.mark.anyio("asyncio")
async def test_list_layers_returns_displayable_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_rows = [
        [
            "hazard_rga_h3",
            "cat.catnat_gold.hazard_rga_h3",
            "drought",
            "gold",
            "h3_r9_cell",
            "h3",
            None,
            "Etalab 2.0",
            True,
            "RGA H3 r=9 cells",
        ],
        [
            "hazard_ppri_communes",
            "cat.catnat_silver.hazard_ppri_communes",
            "flood",
            "silver",
            "polygon",
            None,
            "geometry",
            "Etalab 2.0",
            True,
            "PPRI commune footprints (silver, polygon).",
        ],
    ]
    stub = _make_sql_stub(stub_rows)
    _override_sql(monkeypatch, stub, "test_catalog")

    async with create_connected_server_and_client_session(mcp_server) as session:
        result = await session.call_tool("list_layers", arguments={})

    assert not result.isError, f"tool call failed: {result.content}"
    # FastMCP wraps list-return tools into `structuredContent={"result": [...]}`.
    assert result.structuredContent is not None
    payload = result.structuredContent["result"]
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["layer_id"] == "hazard_rga_h3"
    assert payload[0]["peril"] == "drought"
    assert payload[1]["geom_column"] == "geometry"


@pytest.mark.anyio("asyncio")
async def test_list_layers_uses_parameter_marker_for_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalog name must travel as a `StatementParameterListItem`, not be
    string-interpolated into the SQL (raw dict crashes the SDK; concat is an
    injection surface)."""
    stub = _make_sql_stub([])
    _override_sql(monkeypatch, stub, "test_catalog")

    async with create_connected_server_and_client_session(mcp_server) as session:
        await session.call_tool("list_layers", arguments={})

    params = stub.execute_statement.call_args.kwargs["parameters"]
    assert params, "expected at least one parameter"
    for p in params:
        assert isinstance(p, StatementParameterListItem)
    names = {p.name for p in params}
    assert "catalog" in names
    catalog_param = next(p for p in params if p.name == "catalog")
    assert catalog_param.value == "test_catalog"
