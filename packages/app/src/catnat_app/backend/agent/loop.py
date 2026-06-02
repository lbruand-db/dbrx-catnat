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
from collections.abc import AsyncIterator, Callable
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session
from openai import AsyncOpenAI

from ..mcp import mcp_server
from .client import get_client, get_model
from .events import AgentEvent
from .prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


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


async def run_agent(
    messages: list[dict[str, Any]],
    *,
    client_factory: Callable[[], AsyncOpenAI] = get_client,
    model: str | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run one agent turn against the catnat MCP server.

    `messages` is the prior conversation in OpenAI chat shape (no system
    prompt — we prepend ours). Yields events the route streams as SSE.
    """
    client = client_factory()
    used_model = model or get_model()

    async with create_connected_server_and_client_session(mcp_server) as mcp_session:
        tools_response = await mcp_session.list_tools()
        openai_tools = [_mcp_to_openai_tool(t) for t in tools_response.tools]

        history: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
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
                    yield AgentEvent("delta", {"text": delta.content})
                if delta.tool_calls:
                    _accumulate_tool_calls(tool_calls_acc, list(delta.tool_calls))

            if not tool_calls_acc:
                yield AgentEvent("done", {"final_text": assistant_text})
                return

            tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
            history.append(
                {
                    "role": "assistant",
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

                yield AgentEvent("tool_call", {"id": tc_id, "name": tool_name, "arguments": args})

                try:
                    tool_result = await mcp_session.call_tool(tool_name, arguments=args)
                    payload, is_error = _result_payload(tool_result)
                except Exception as e:
                    payload = f"tool call raised: {e}"
                    is_error = True

                yield AgentEvent(
                    "tool_result",
                    {
                        "id": tc_id,
                        "name": tool_name,
                        "result": payload,
                        "is_error": is_error,
                    },
                )

                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(payload, ensure_ascii=False, default=str),
                    }
                )

        yield AgentEvent(
            "error",
            {"message": f"agent exceeded {MAX_ITERATIONS} iterations without resolution"},
        )


__all__ = ["MAX_ITERATIONS", "run_agent"]
