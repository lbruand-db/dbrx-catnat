import os
from collections.abc import AsyncIterator
from typing import Any

from databricks.sdk.service.iam import User as UserOut
from databricks.sdk.service.sql import StatementParameterListItem, StatementState
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .agent import run_agent, sse
from .app_sql import AppSqlDependency
from .core import Dependencies, create_router
from .models import Layer, LayerListOut, VersionOut

router = create_router()


class ChatMessage(BaseModel):
    """One turn in the chat history (OpenAI shape, minus system).

    Server-side we prepend the system prompt — the FE never has to know
    what it is, and prompt edits don't require an FE redeploy.
    """

    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ChatViewport(BaseModel):
    """Snapshot of the Leaflet pane's current view at send time."""

    bbox: list[float] | None = None  # [min_lon, min_lat, max_lon, max_lat]
    zoom: float | None = None
    center: list[float] | None = None  # [lon, lat]


class ChatActiveLayer(BaseModel):
    """One agent-added layer the user is currently looking at."""

    layer_id: str
    row_count: int | None = None


class ChatContext(BaseModel):
    """Reverse-channel state attached to every /api/chat POST so the
    agent reads the user's map view without needing a tool call.

    Per UI.md §3.2.1. The FE projects its `MapState` into this shape
    (heavy fields like full geojson left behind); the agent loop folds
    it into the system prompt before the first FMAPI call of the turn.
    """

    viewport: ChatViewport | None = None
    active_layers: list[ChatActiveLayer] = []


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: ChatContext | None = None


def _catalog() -> str:
    """Read `CATNAT_CATALOG` lazily so tests can override per-call."""
    return os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")


@router.get("/version", response_model=VersionOut, operation_id="version")
async def version():
    return VersionOut.from_metadata()


@router.get("/current-user", response_model=UserOut, operation_id="currentUser")
def me(user_ws: Dependencies.UserClient):
    return user_ws.current_user.me()


_LAYERS_SQL = """
SELECT
    layer_id, table_fq, peril, medallion, grain,
    h3_column, geom_column, license, is_displayable, description
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
ORDER BY peril, medallion, layer_id
"""


@router.get("/layers", response_model=LayerListOut, operation_id="listLayers")
def list_layers(sql: AppSqlDependency) -> LayerListOut:
    """Return every catnat layer the demo can surface.

    Reads `catnat_silver.layer_index` via the app SP's SQL warehouse client.
    OBO was intended but the gateway never mints the `sql` scope on this
    workspace; see `app_sql.py` for the rationale.
    """
    response = sql.execute_statement(
        statement=_LAYERS_SQL,
        wait_timeout="30s",
        parameters=[StatementParameterListItem(name="catalog", value=_catalog(), type="STRING")],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"layer_index query failed: {msg}")
    rows = response.result.data_array if response.result else []
    layers = [
        Layer(
            layer_id=r[0],
            table_fq=r[1],
            peril=r[2],
            medallion=r[3],
            grain=r[4],
            h3_column=r[5],
            geom_column=r[6],
            license=r[7],
            is_displayable=r[8] == "true" if isinstance(r[8], str) else bool(r[8]),
            description=r[9],
        )
        for r in (rows or [])
    ]
    return LayerListOut(layers=layers)


@router.post("/chat", operation_id="chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream agent SSE events for one chat turn.

    The body is the prior conversation in OpenAI shape (system prompt is
    prepended server-side). Response is `text/event-stream` with events
    `delta`, `tool_call`, `tool_result`, `done`, `error`, `map_op`
    (see `backend/agent/events.py` for shapes).

    Headers explicitly disable upstream buffering — the Databricks Apps
    gateway (and most nginx-style proxies in the chain) will hold a
    streaming response until completion unless asked otherwise.
    """
    payload_messages = [m.model_dump(exclude_none=True) for m in request.messages]
    context_payload = request.context.model_dump(exclude_none=True) if request.context else None

    async def event_source() -> AsyncIterator[str]:
        async for event in run_agent(payload_messages, context=context_payload):
            yield sse(event)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx-style proxies (the Databricks Apps gateway is one)
            # honour this to skip response buffering.
            "X-Accel-Buffering": "no",
        },
    )
