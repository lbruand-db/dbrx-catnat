"""MCP (Model Context Protocol) server for the catnat agent.

Exposes Unity-Catalog-backed tools the agent can call (`list_layers`,
`query_layer`, spatial helpers). Mounted on the FastAPI app at `/mcp` —
the transport is HTTP/SSE per SPEC §10.3.
"""

from .server import mcp_server

__all__ = ["mcp_server"]
