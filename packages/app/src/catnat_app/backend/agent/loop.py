"""Agent loop: FMAPI ⇄ MCP, streaming SSE events.

The contract:

- Input is a list of OpenAI-shaped messages (already including any prior
  user/assistant turns, but **not** the system prompt — we prepend that
  ourselves).
- Output is an `AsyncIterator[AgentEvent]` which the FastAPI route
  formats as SSE.
- The loop is capped at `MAX_ITERATIONS` to prevent runaway tool chains;
  on hitting the cap we emit an `error` event and stop.

Tool dispatch goes through the in-memory MCP transport, so all the
allowlist + parameter-marker guards in `mcp.tools` apply transparently.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session
from openai import AsyncOpenAI

from ..mcp import mcp_server
from .client import get_client, get_model
from .events import AgentEvent
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _trace_emit(event: AgentEvent, t0: float) -> None:
    """Log emit timestamps when `CATNAT_AGENT_EMIT_TRACE=1`.

    Used by `scripts/probe_agent.py` (and ad-hoc debugging) to tell
    apart loop slowness from transport buffering.
    """
    if os.environ.get("CATNAT_AGENT_EMIT_TRACE") == "1":
        elapsed = time.monotonic() - t0
        logger.info("emit %s at +%.2fs", event.name, elapsed)


# Hard cap on tool-call iterations per turn — anything more is the agent
# spinning on a malformed tool, not making progress.
MAX_ITERATIONS = 8


def _mcp_to_openai_tool(mcp_tool: Any) -> dict[str, Any]:
    """Translate one MCP `Tool` to the OpenAI function-tool schema."""
    return {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description or "",
            "parameters": mcp_tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def _accumulate_tool_calls(acc: dict[int, dict[str, Any]], deltas: list[Any]) -> None:
    """In-place merge of streamed tool-call deltas into the accumulator.

    OpenAI streams partial tool calls — the `id` and `function.name`
    arrive in the first chunk, then `function.arguments` trickles in as
    text. We accumulate per `index`.
    """
    for d in deltas:
        idx = d.index
        slot = acc.setdefault(
            idx,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if d.id:
            slot["id"] = d.id
        if d.function is not None:
            if d.function.name:
                slot["function"]["name"] += d.function.name
            if d.function.arguments:
                slot["function"]["arguments"] += d.function.arguments


# Fields stripped from a UI-tool result before it reaches the LLM —
# `geojson` and `geom_geojson` can be megabytes of feature data and add
# no value to the model's next decision.
_LLM_STRIP_KEYS = frozenset({"geojson", "geom_geojson"})


def _split_ui_payload(payload: Any, is_error: bool) -> tuple[dict[str, Any] | None, Any]:
    """Split a tool payload into (map_op for FE, slim summary for LLM).

    FastMCP wraps a tool's dict/list return under `structuredContent =
    {"result": <value>}`. A UI tool is one whose inner result is a dict
    carrying an `op` key — we unwrap it for the FE map_op event and
    return the same inner dict (with heavy fields stripped) so the LLM
    sees a clean success summary instead of the wrapper noise.
    """
    if is_error:
        return None, payload
    inner = payload
    if isinstance(payload, dict) and set(payload.keys()) == {"result"}:
        inner = payload["result"]
    if not isinstance(inner, dict) or "op" not in inner:
        return None, payload
    map_op = inner
    llm = {k: v for k, v in inner.items() if k not in _LLM_STRIP_KEYS}
    return map_op, llm


def _result_payload(tool_result: Any) -> tuple[Any, bool]:
    """Extract a JSON-serialisable payload + an is-error flag from an MCP CallToolResult."""
    is_error = bool(getattr(tool_result, "isError", False))
    if getattr(tool_result, "structuredContent", None) is not None:
        return tool_result.structuredContent, is_error
    text_chunks = [
        getattr(c, "text", str(c))
        for c in getattr(tool_result, "content", []) or []
        if getattr(c, "type", None) == "text"
    ]
    if len(text_chunks) == 1:
        return text_chunks[0], is_error
    if text_chunks:
        return text_chunks, is_error
    return None, is_error


def _format_context(context: dict[str, Any] | None) -> str:
    """Render the FE-supplied map state as a short system-prompt suffix.

    The reverse-channel block (UI.md §3.2.1) — viewport, active layers,
    eventually selection / drawings. Keep it terse: the LLM doesn't
    need pretty JSON, just enough to know what the user is looking at.
    """
    if not context:
        return ""
    lines = ["Current map state (what the user sees right now):"]
    vp = context.get("viewport")
    if vp:
        bbox = vp.get("bbox")
        zoom = vp.get("zoom")
        center = vp.get("center")
        parts = []
        if bbox and len(bbox) == 4:
            parts.append(f"bbox=[{bbox[0]:.3f}, {bbox[1]:.3f}, {bbox[2]:.3f}, {bbox[3]:.3f}]")
        if zoom is not None:
            parts.append(f"zoom={zoom:g}")
        if center and len(center) == 2:
            parts.append(f"center=({center[0]:.3f}, {center[1]:.3f})")
        if parts:
            lines.append(f"- Viewport: {', '.join(parts)}")
    active = context.get("active_layers") or []
    if active:
        labels = []
        for layer in active:
            label = layer.get("layer_id", "?")
            count = layer.get("row_count")
            labels.append(f"{label}" + (f" ({count} features)" if count is not None else ""))
        lines.append(f"- Active agent-added layers: {', '.join(labels)}")
    else:
        lines.append("- Active agent-added layers: none")
    selection = context.get("selection")
    if selection:
        layer_id = selection.get("layer_id", "?")
        props = selection.get("properties") or {}
        latlng = selection.get("latlng")
        # Show only the most useful attribute fields so the prompt
        # stays short. Anything not in this set gets dropped — the
        # agent can ask follow-ups with intersect_layer / nearest if
        # it needs more.
        key_fields = ("code_insee", "nom_officiel", "code_dep", "dept", "peril_kind")
        shown = {k: props[k] for k in key_fields if k in props}
        # Fall back to first 4 properties for layers whose attribute
        # set we don't pre-recognise.
        if not shown and props:
            shown = dict(list(props.items())[:4])
        attr_str = ", ".join(f"{k}={v!r}" for k, v in shown.items()) if shown else "(no key attrs)"
        latlng_str = f" @ ({latlng[0]:.4f}, {latlng[1]:.4f})" if latlng and len(latlng) == 2 else ""
        lines.append(f"- Selection: layer={layer_id} {attr_str}{latlng_str}")
        lines.append(
            "  ↑ The user clicked this feature. Treat 'this', 'ce truc', "
            "'cette zone' etc. as referring to it."
        )
    return "\n".join(lines)


def _default_mcp_session_factory() -> AbstractAsyncContextManager[Any]:
    """Default MCP context: in-memory transport over the real catnat server."""
    return create_connected_server_and_client_session(mcp_server)


async def run_agent(
    messages: list[dict[str, Any]],
    *,
    context: dict[str, Any] | None = None,
    client_factory: Callable[[], AsyncOpenAI] = get_client,
    model: str | None = None,
    mcp_session_factory: Callable[
        [], AbstractAsyncContextManager[Any]
    ] = _default_mcp_session_factory,
) -> AsyncIterator[AgentEvent]:
    """Run one agent turn against the catnat MCP server.

    `messages` is the prior conversation in OpenAI chat shape (no system
    prompt — we prepend ours). `context` is the FE's snapshot of the
    current map state (viewport, active layers, etc.) — folded into the
    system prompt so the agent reads it without a tool call. Yields
    events the route streams as SSE.

    `mcp_session_factory` is the seam used by golden-trace replay tests
    and the `scripts/probe_agent.py --record` recorder to swap MCP
    without touching the loop.
    """
    client = client_factory()
    used_model = model or get_model()
    t0 = time.monotonic()

    async with mcp_session_factory() as mcp_session:
        tools_response = await mcp_session.list_tools()
        openai_tools = [_mcp_to_openai_tool(t) for t in tools_response.tools]

        system_prompt = SYSTEM_PROMPT
        context_block = _format_context(context)
        if context_block:
            system_prompt = f"{SYSTEM_PROMPT}\n\n{context_block}"

        history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *messages,
        ]

        for iteration in range(MAX_ITERATIONS):
            logger.debug("agent iteration %d", iteration)

            stream = await client.chat.completions.create(
                model=used_model,
                messages=history,  # type: ignore[arg-type]
                tools=openai_tools,  # type: ignore[arg-type]
                stream=True,
            )

            assistant_text = ""
            tool_calls_acc: dict[int, dict[str, Any]] = {}

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    assistant_text += delta.content
                    ev = AgentEvent("delta", {"text": delta.content})
                    _trace_emit(ev, t0)
                    yield ev
                if delta.tool_calls:
                    _accumulate_tool_calls(tool_calls_acc, list(delta.tool_calls))

            if not tool_calls_acc:
                ev = AgentEvent("done", {"final_text": assistant_text})
                _trace_emit(ev, t0)
                yield ev
                return

            tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            # Anthropic's FMAPI validator rejects tool_calls whose
            # `arguments` field is the empty string — Claude emits `""`
            # when the tool takes no args. Replace with `"{}"` so the
            # next iteration's request validates.
            for tc in tool_calls:
                if not tc["function"]["arguments"].strip():
                    tc["function"]["arguments"] = "{}"
            history.append(
                {
                    "role": "assistant",
                    # FMAPI accepts a null `content` when tool_calls is
                    # present; an empty string also passes but is
                    # semantically odd. Stick with the empty string
                    # (also what OpenAI's spec recommends) — the bug
                    # was strictly the arguments field.
                    "content": assistant_text,
                    "tool_calls": tool_calls,
                }
            )

            for tc in tool_calls:
                tc_id = tc["id"]
                tool_name = tc["function"]["name"]
                args_json = tc["function"]["arguments"]
                try:
                    args = json.loads(args_json) if args_json else {}
                except json.JSONDecodeError:
                    args = {}

                ev_tc = AgentEvent("tool_call", {"id": tc_id, "name": tool_name, "arguments": args})
                _trace_emit(ev_tc, t0)
                yield ev_tc

                try:
                    tool_result = await mcp_session.call_tool(tool_name, arguments=args)
                    payload, is_error = _result_payload(tool_result)
                except Exception as e:
                    payload = f"tool call raised: {e}"
                    is_error = True

                # UI-mutating tools tag their result with an `op` field —
                # the full payload goes to the FE via `map_op` while the
                # LLM gets a slim summary so it doesn't drown in geojson.
                map_op_payload, llm_payload = _split_ui_payload(payload, is_error)
                if map_op_payload is not None:
                    ev_map = AgentEvent("map_op", map_op_payload)
                    _trace_emit(ev_map, t0)
                    yield ev_map

                ev_tr = AgentEvent(
                    "tool_result",
                    {
                        "id": tc_id,
                        "name": tool_name,
                        "result": llm_payload,
                        "is_error": is_error,
                    },
                )
                _trace_emit(ev_tr, t0)
                yield ev_tr

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(llm_payload, ensure_ascii=False, default=str),
                    }
                )

        yield AgentEvent(
            "error",
            {"message": f"agent exceeded {MAX_ITERATIONS} iterations without resolution"},
        )


__all__ = ["MAX_ITERATIONS", "run_agent"]
