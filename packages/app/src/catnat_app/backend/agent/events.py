"""SSE event shapes emitted by the agent loop.

The FE consumes these via `EventSource`. Each event is one named SSE
record (`event: <name>\\ndata: <json>\\n\\n`).

- `delta`        — `{text: str}`                  partial assistant text
- `tool_call`    — `{id, name, arguments}`        agent decided to call a tool
- `tool_result`  — `{id, name, result, is_error}` tool returned (or raised);
                                                  `result` is the LLM-visible
                                                  payload (geojson stripped)
- `map_op`       — `{op, ...}`                    UI-mutating side effect for
                                                  the Leaflet pane (add_layer,
                                                  remove_layer, zoom_to,
                                                  style_layer). Carries the
                                                  geojson the LLM doesn't see.
- `done`         — `{final_text: str}`            turn complete
- `error`        — `{message: str}`               agent loop / FMAPI failure
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

EventName = Literal["delta", "tool_call", "tool_result", "map_op", "done", "error"]


@dataclass(frozen=True)
class AgentEvent:
    name: EventName
    data: dict[str, Any]


def sse(event: AgentEvent) -> str:
    """Format an `AgentEvent` as a single SSE record."""
    return f"event: {event.name}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


__all__ = ["AgentEvent", "EventName", "sse"]
