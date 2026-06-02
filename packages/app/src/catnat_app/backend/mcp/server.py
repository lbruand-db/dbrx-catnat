"""FastMCP server instance + tool registrations.

The server is module-level so the same instance is shared across the SSE
endpoint, the test client, and any future in-process agent loop.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import tools as _tools
from . import ui_tools as _ui_tools

mcp_server: FastMCP = FastMCP(
    name="catnat",
    instructions=(
        "Geospatial Unity Catalog tools for French CatNat (drought/flood/storm) "
        "risk analysis on Databricks. Layer names refer to entries in "
        "`catnat_silver.layer_index` (use `list_layers` to enumerate them). "
        "All spatial geometries are EPSG:4326 WKT unless noted otherwise."
    ),
)

# Tool registration — implementations live in `tools.py` (data) and
# `ui_tools.py` (map-mutating) so we can unit-test them without spinning
# up the MCP transport.
_tools.register(mcp_server)
_ui_tools.register(mcp_server)
