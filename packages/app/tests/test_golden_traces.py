"""Golden-trace regression tests.

Each `tests/golden/*.json` file is a recorded agent turn — every OpenAI
chunk and every MCP tool call captured by `scripts/probe_agent.py
--record`. This test parametrically replays each trace through the
*current* `run_agent` and asserts the observed event sequence still
matches the recording.

Why this exists: the agent loop has subtle behaviour around streaming
tool-call accumulation, UI-payload splitting (`_split_ui_payload`),
empty-arguments normalisation, and end-of-iteration detection. Unit
tests in `test_agent_loop.py` cover each piece in isolation; this is
the end-to-end pin that catches regressions where the pieces fit
together wrong.

What the test does *not* catch: prompt drift. We replay scripted chunks,
so changes to the system prompt, the schema cheat sheet, or
`_format_context` don't affect the result. Run the live demo for that
kind of regression.

To re-record a trace after an intentional loop change:

    cd packages/app
    uv run python scripts/probe_agent.py \\
        --record tests/golden/<name>.json \\
        "<prompt>"
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from catnat_app.backend.agent.loop import run_agent

GOLDEN_DIR = Path(__file__).parent / "golden"


# --- Reviving recorded OpenAI chunks --------------------------------------


def _ns(d: dict[str, Any] | None) -> Any:
    """Convert a JSON-dump'd OpenAI chunk into a namespace tree.

    The loop reads `chunk.choices[0].delta.content` and
    `chunk.choices[0].delta.tool_calls[*].{index,id,function.name,function.arguments}`
    — only those attributes need to exist. Nested dicts become
    `SimpleNamespace`; lists are recursed; scalars pass through.
    """
    if d is None:
        return None
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in d.items()})
    if isinstance(d, list):
        return [_ns(v) for v in d]
    return d


class _ScriptedStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks
        self._i = 0

    def __aiter__(self) -> _ScriptedStream:
        return self

    async def __anext__(self) -> Any:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class _ScriptedCompletions:
    def __init__(self, iterations: list[list[Any]]) -> None:
        self._iterations = iterations
        self._call = 0

    async def create(self, **_: Any) -> _ScriptedStream:
        if self._call >= len(self._iterations):
            raise AssertionError(
                f"replay: agent asked for iteration #{self._call} but only "
                f"{len(self._iterations)} recorded"
            )
        chunks = self._iterations[self._call]
        self._call += 1
        return _ScriptedStream(chunks)


@dataclass
class _ScriptedClient:
    iterations: list[list[Any]]
    chat: SimpleNamespace = field(init=False)

    def __post_init__(self) -> None:
        self.chat = SimpleNamespace(completions=_ScriptedCompletions(self.iterations))


# --- Reviving recorded MCP tool calls -------------------------------------


@dataclass
class _ToolContent:
    """Minimal shape compatible with `_result_payload` in loop.py."""

    type: str
    text: str | None = None


@dataclass
class _ToolResult:
    isError: bool  # noqa: N815 — mirrors MCP CallToolResult
    structuredContent: Any  # noqa: N815
    content: list[_ToolContent]


@dataclass
class _MCPTool:
    name: str
    description: str
    inputSchema: dict[str, Any]  # noqa: N815


@dataclass
class _ToolsListResponse:
    tools: list[_MCPTool]


class _ReplayMCPSession:
    """Returns canned tool results in the recorded order.

    Asserts that the agent calls the same tool with the same arguments
    as during recording — that's the structural contract the trace
    pins. If the agent diverges (different tool, different args), the
    test fails with a clear diff.
    """

    def __init__(
        self,
        tools_schema: list[dict[str, Any]],
        calls: list[dict[str, Any]],
    ) -> None:
        self._tools_response = _ToolsListResponse(
            tools=[
                _MCPTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {"type": "object", "properties": {}}),
                )
                for t in tools_schema
            ]
        )
        self._calls = list(calls)  # popped left-to-right
        self.observed: list[dict[str, Any]] = []

    async def list_tools(self) -> _ToolsListResponse:
        return self._tools_response

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> _ToolResult:
        if not self._calls:
            raise AssertionError(
                f"replay: agent called unexpected tool {name!r}({arguments!r}); "
                f"no more recorded calls"
            )
        expected = self._calls.pop(0)
        self.observed.append({"name": name, "arguments": arguments or {}})
        # Pin tool name strictly. Arguments must match — minor key-order
        # differences are fine because we compare as dicts.
        assert name == expected["name"], (
            f"replay: tool call mismatch — got {name!r}, expected {expected['name']!r}"
        )
        assert (arguments or {}) == (expected.get("arguments") or {}), (
            f"replay: arguments mismatch for {name!r} — "
            f"got {arguments!r}, expected {expected.get('arguments')!r}"
        )

        return _ToolResult(
            isError=bool(expected.get("is_error", False)),
            structuredContent=expected.get("structured_content"),
            content=[
                _ToolContent(type="text", text=t)
                for t in (expected.get("content_text") or [])
                if t is not None
            ],
        )


# --- Test ------------------------------------------------------------------


def _discover_traces() -> list[Path]:
    if not GOLDEN_DIR.is_dir():
        return []
    return sorted(GOLDEN_DIR.glob("*.json"))


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    "trace_path",
    _discover_traces() or [pytest.param(None, marks=pytest.mark.skip(reason="no golden traces"))],
    ids=lambda p: p.stem if p else "none",
)
async def test_golden_trace_replays_deterministically(trace_path: Path) -> None:
    """Replay a recorded trace and check observed events match."""
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    iterations = [[_ns(c) for c in it] for it in trace["openai_iterations"]]
    client = _ScriptedClient(iterations=iterations)
    mcp = _ReplayMCPSession(
        tools_schema=trace["tools_schema"],
        calls=trace["mcp_calls"],
    )

    @contextlib.asynccontextmanager
    async def _factory() -> Any:
        yield mcp

    observed: list[dict[str, Any]] = []
    async for ev in run_agent(
        messages=trace["input"]["messages"],
        context=trace["input"].get("context"),
        client_factory=lambda: client,  # type: ignore[arg-type]
        mcp_session_factory=_factory,
    ):
        observed.append({"name": ev.name, "data": ev.data})

    expected = trace["events"]
    # Structural pin: same event sequence, same payloads. The recording
    # is the ground truth; if the loop changed in a way that's
    # intentional, re-record by re-running `probe_agent.py --record`.
    assert len(observed) == len(expected), (
        f"event count mismatch: observed {len(observed)}, expected {len(expected)}. "
        f"Observed names: {[e['name'] for e in observed]}; "
        f"expected names: {[e['name'] for e in expected]}"
    )
    for i, (got, want) in enumerate(zip(observed, expected, strict=True)):
        assert got["name"] == want["name"], (
            f"event #{i}: name mismatch — observed {got['name']!r}, expected {want['name']!r}"
        )
        assert got["data"] == want["data"], (
            f"event #{i} ({got['name']}): data mismatch.\n"
            f"  observed: {got['data']!r}\n"
            f"  expected: {want['data']!r}"
        )

    # Every recorded tool call should have been consumed.
    assert mcp._calls == [], (
        f"replay finished but {len(mcp._calls)} tool calls were never invoked: "
        f"{[c['name'] for c in mcp._calls]}"
    )
