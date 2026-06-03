from starlette.routing import Mount

from .core import create_app
from .mcp import mcp_server
from .router import router
from .tiles import router as tiles_router

app = create_app(routers=[router, tiles_router])

# Mount the MCP SSE server at /mcp. apx's `create_app` mounts a static
# catch-all at `/`; route order matters in Starlette, so we insert the
# /mcp mount at the front of the router so requests to /mcp/sse and
# /mcp/messages/... reach the MCP transport instead of being absorbed
# by the static handler.
app.router.routes.insert(0, Mount("/mcp", app=mcp_server.sse_app()))
