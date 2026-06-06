"""End-to-end probe for `/api/chat`.

Drives the FastAPI app in-process (no uvicorn, no Apps gateway, no
browser) so we can see exactly what the agent loop is doing on a real
warehouse + real FMAPI call. Useful for both manual debugging and as a
template for an integration test.

Two modes:

- Default (no `--record`): POST to `/api/chat` via `httpx.ASGITransport`
  so the FastAPI route is fully exercised end-to-end.
- `--record PATH`: bypass HTTP, call `run_agent()` directly, wrap the
  OpenAI client and MCP session so every chunk + every tool call is
  serialised into a JSON trace file. Used to capture golden traces
  replayed by `tests/test_golden_traces.py`.

Usage:

    cd packages/app
    uv run python scripts/probe_agent.py \\
        --profile fevm-stable-po64og \\
        --warehouse 1c97ee257092c2b3 \\
        --catalog serverless_stable_po64og_catalog \\
        "Affiche les communes du Rhône sur la carte."

    # Capture a golden trace:
    uv run python scripts/probe_agent.py \\
        --record tests/golden/act1_show_ppri.json \\
        "Affiche les communes du Rhône sur la carte."

It prints every SSE event as it arrives, with elapsed time, plus a
summary at the end. Tool results are truncated to keep the output
readable.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def _truncate(value: Any, max_chars: int = 200) -> str:
    s = json.dumps(value, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"… ({len(s) - max_chars} more chars)"


# --- Pretty-printing of streamed events (shared by HTTP + direct probe) ---

_EventStats = dict[str, int]


def _new_stats() -> _EventStats:
    return {"delta": 0, "tool_call": 0, "tool_result": 0, "map_op": 0, "done": 0, "error": 0}


def _print_event(
    event: str,
    payload: dict[str, Any],
    stats: _EventStats,
    tool_names: list[str],
    t0: float,
    state: dict[str, str],
) -> None:
    stats[event] = stats.get(event, 0) + 1
    elapsed = time.monotonic() - t0
    if event == "delta":
        if stats["delta"] == 1:
            print(f"\n[+{elapsed:5.2f}s] delta (first)", flush=True)
        sys.stdout.write(payload.get("text", ""))
        sys.stdout.flush()
    elif event == "tool_call":
        name = payload.get("name", "?")
        tool_names.append(name)
        print(
            f"\n[+{elapsed:5.2f}s] tool_call #{stats['tool_call']:>2} "
            f"{name}({_truncate(payload.get('arguments', {}), 120)})"
        )
    elif event == "tool_result":
        marker = "ERROR" if bool(payload.get("is_error")) else "ok"
        print(
            f"[+{elapsed:5.2f}s] tool_result      [{marker}] "
            f"{payload.get('name', '?')} → {_truncate(payload.get('result'), 240)}"
        )
    elif event == "map_op":
        op = payload.get("op", "?")
        details = {k: v for k, v in payload.items() if k not in {"geojson", "geom_geojson"}}
        if "geojson" in payload:
            details["geojson_features"] = len(payload["geojson"].get("features", []))
        if "geom_geojson" in payload:
            details["geom_geojson_type"] = payload["geom_geojson"].get("type")
        print(f"[+{elapsed:5.2f}s] map_op           {op}: {_truncate(details, 200)}")
    elif event == "done":
        state["final_text"] = payload.get("final_text", "")
        print(f"\n[+{elapsed:5.2f}s] done — final_text len={len(state['final_text'])}")
    elif event == "error":
        state["error_message"] = payload.get("message", "?")
        print(f"\n[+{elapsed:5.2f}s] ERROR: {state['error_message']}")


def _summarise(stats: _EventStats, tool_names: list[str], state: dict[str, str]) -> int:
    print()
    print("=" * 60)
    print(f"Stats: {stats}")
    if tool_names:
        print(f"Tools: {tool_names}")
    if state.get("final_text"):
        print(f"Final text ({len(state['final_text'])} chars):")
        print(state["final_text"])
    if state.get("error_message"):
        print(f"Error: {state['error_message']}")
        return 1
    return 0


# --- HTTP probe (default mode) --------------------------------------------


async def probe_http(message: str) -> int:
    # Lazy import so env vars are set first.
    from catnat_app.backend.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as client:
        t0 = time.monotonic()
        print(f"[+0.00s] POST /api/chat — {message!r}")

        async with client.stream(
            "POST",
            "/api/chat",
            json={"messages": [{"role": "user", "content": message}]},
            timeout=300.0,
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                print(f"[!] HTTP {resp.status_code}: {body[:500]}")
                return 1

            buffer = ""
            stats = _new_stats()
            tool_names: list[str] = []
            state: dict[str, str] = {}

            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    event = ""
                    data = ""
                    for line in frame.split("\n"):
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                    if not event:
                        continue
                    try:
                        payload = json.loads(data) if data else {}
                    except json.JSONDecodeError:
                        payload = {"_raw": data}
                    _print_event(event, payload, stats, tool_names, t0, state)

            return _summarise(stats, tool_names, state)


# --- Recording mode (direct call into run_agent) --------------------------


def _json_safe(value: Any) -> Any:
    """Best-effort conversion to JSON-safe values for trace serialisation."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # Fall back to repr — better to lose precision than crash the recorder.
    return str(value)


class _RecordingStream:
    """Wraps an OpenAI streaming response; records each chunk as a dict."""

    def __init__(self, real: Any, iterations: list[list[dict[str, Any]]]) -> None:
        self._real = real
        self._this_iter: list[dict[str, Any]] = []
        iterations.append(self._this_iter)

    def __aiter__(self) -> _RecordingStream:
        self._aiter = self._real.__aiter__()
        return self

    async def __anext__(self) -> Any:
        chunk = await self._aiter.__anext__()
        # OpenAI chunks are pydantic models — model_dump gives a stable dict.
        try:
            self._this_iter.append(chunk.model_dump())
        except Exception:
            # Defensive: never break recording on serialisation hiccup.
            self._this_iter.append({"_unserialisable": repr(chunk)})
        return chunk


class _RecordingCompletions:
    def __init__(self, real: Any, iterations: list[list[dict[str, Any]]]) -> None:
        self._real = real
        self._iterations = iterations

    async def create(self, **kwargs: Any) -> _RecordingStream:
        stream = await self._real.create(**kwargs)
        return _RecordingStream(stream, self._iterations)


class _RecordingChat:
    def __init__(self, real: Any, iterations: list[list[dict[str, Any]]]) -> None:
        self.completions = _RecordingCompletions(real.completions, iterations)


class _RecordingOpenAIClient:
    """Wraps `AsyncOpenAI` and records every streamed chunk."""

    def __init__(self, real: Any, iterations: list[list[dict[str, Any]]]) -> None:
        self._real = real
        self.chat = _RecordingChat(real.chat, iterations)


class _RecordingMCPSession:
    """Wraps an MCP `ClientSession`; records tools schema + each call."""

    def __init__(
        self,
        real: Any,
        tools_record: list[dict[str, Any]],
        calls_record: list[dict[str, Any]],
    ) -> None:
        self._real = real
        self._tools_record = tools_record
        self._calls_record = calls_record

    async def list_tools(self) -> Any:
        result = await self._real.list_tools()
        if not self._tools_record:
            for t in result.tools:
                self._tools_record.append(
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": _json_safe(t.inputSchema)
                        or {"type": "object", "properties": {}},
                    }
                )
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._real.call_tool(name, arguments=arguments)
        self._calls_record.append(
            {
                "name": name,
                "arguments": _json_safe(arguments) or {},
                "is_error": bool(getattr(result, "isError", False)),
                "structured_content": _json_safe(getattr(result, "structuredContent", None)),
                "content_text": [
                    getattr(c, "text", None)
                    for c in (getattr(result, "content", None) or [])
                    if getattr(c, "type", None) == "text"
                ],
            }
        )
        return result


async def probe_record(message: str, out_path: Path, *, context: dict[str, Any] | None) -> int:
    """Call `run_agent` directly, wrap I/O, write a golden-trace JSON."""
    from catnat_app.backend.agent.client import get_client
    from catnat_app.backend.agent.loop import _default_mcp_session_factory, run_agent

    iterations: list[list[dict[str, Any]]] = []
    tools_record: list[dict[str, Any]] = []
    calls_record: list[dict[str, Any]] = []

    real_client = get_client()
    recording_client = _RecordingOpenAIClient(real_client, iterations)

    @contextlib.asynccontextmanager
    async def _recording_factory() -> Any:
        async with _default_mcp_session_factory() as real:
            yield _RecordingMCPSession(real, tools_record, calls_record)

    events_record: list[dict[str, Any]] = []
    t0 = time.monotonic()
    stats = _new_stats()
    tool_names: list[str] = []
    state: dict[str, str] = {}

    print(f"[+0.00s] run_agent (recording) — {message!r}")

    async for event in run_agent(
        messages=[{"role": "user", "content": message}],
        context=context,
        client_factory=lambda: recording_client,  # type: ignore[arg-type]
        mcp_session_factory=_recording_factory,
    ):
        events_record.append({"name": event.name, "data": _json_safe(event.data)})
        _print_event(event.name, event.data or {}, stats, tool_names, t0, state)

    trace = {
        "name": out_path.stem,
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "input": {
            "messages": [{"role": "user", "content": message}],
            "context": _json_safe(context),
        },
        "tools_schema": tools_record,
        "openai_iterations": iterations,
        "mcp_calls": calls_record,
        "events": events_record,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"\nWrote trace → {out_path} "
        f"({len(events_record)} events, {len(calls_record)} tool calls, "
        f"{sum(len(it) for it in iterations)} chunks)"
    )

    return _summarise(stats, tool_names, state)


# --- CLI ------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="User message to send to the agent")
    parser.add_argument(
        "--profile",
        default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "fevm-stable-po64og"),
        help="Databricks CLI profile to authenticate against (default: env or fevm-stable-po64og)",
    )
    parser.add_argument(
        "--warehouse",
        default=os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "1c97ee257092c2b3"),
        help="Warehouse id (default: env or the dev workspace's)",
    )
    parser.add_argument(
        "--catalog",
        default=os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog"),
        help="Catalog name (default: env or the dev workspace's)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("CATNAT_AGENT_MODEL", "databricks-claude-sonnet-4-6"),
    )
    parser.add_argument(
        "--record",
        type=Path,
        help="Capture a golden trace to this JSON file (bypasses /api/chat, "
        "calls run_agent directly with recording wrappers).",
    )
    parser.add_argument(
        "--context",
        type=str,
        help="Optional JSON for the reverse-channel context block "
        "(viewport / active_layers / selection). Only honoured in --record mode.",
    )
    args = parser.parse_args()

    os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    os.environ["DATABRICKS_SQL_WAREHOUSE_ID"] = args.warehouse
    os.environ["CATNAT_CATALOG"] = args.catalog
    os.environ["CATNAT_AGENT_MODEL"] = args.model

    if args.record is not None:
        context = json.loads(args.context) if args.context else None
        return asyncio.run(probe_record(args.message, args.record, context=context))
    return asyncio.run(probe_http(args.message))


if __name__ == "__main__":
    sys.exit(main())
