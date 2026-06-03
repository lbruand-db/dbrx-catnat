"""Agent-loop tests.

The OpenAI client is fully mocked — we never hit FMAPI in CI. The MCP
side runs end-to-end via the same in-memory transport tests use, with
the warehouse stubbed at `mcp.tools.get_app_sql`.

Each test scripts a sequence of "iterations". Each iteration is a list
of streamed chunks the mock client emits. The loop consumes them, calls
any tool deltas through MCP, then asks the mock for the next iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from catnat_app.backend.agent.loop import run_agent
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp import _sql_client as _sql_client_mod
from databricks.sdk.service.sql import StatementState

# --- Mock chunk shapes that mimic the openai streaming surface --------


@dataclass
class _ToolCallFnDelta:
    name: str | None = None
    arguments: str | None = None


@dataclass
class _ToolCallDelta:
    index: int = 0
    id: str | None = None
    function: _ToolCallFnDelta | None = None


@dataclass
class _Delta:
    content: str | None = None
    tool_calls: list[_ToolCallDelta] | None = None


@dataclass
class _Choice:
    delta: _Delta


@dataclass
class _Chunk:
    choices: list[_Choice] = field(default_factory=list)


def _chunk_text(text: str) -> _Chunk:
    return _Chunk(choices=[_Choice(delta=_Delta(content=text))])


def _chunk_tool_call(
    *, index: int = 0, id: str | None = None, name: str | None = None, arguments: str | None = None
) -> _Chunk:
    return _Chunk(
        choices=[
            _Choice(
                delta=_Delta(
                    tool_calls=[
                        _ToolCallDelta(
                            index=index,
                            id=id,
                            function=_ToolCallFnDelta(name=name, arguments=arguments),
                        )
                    ]
                )
            )
        ]
    )


class _ScriptedStream:
    """Async iterator over a fixed list of chunks."""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self._i = 0

    def __aiter__(self) -> _ScriptedStream:
        return self

    async def __anext__(self) -> _Chunk:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class _ScriptedClient:
    """Mocks `AsyncOpenAI`. Each `create()` call returns the next scripted stream."""

    def __init__(self, iterations: list[list[_Chunk]]) -> None:
        self._iterations = iterations
        self._call = 0
        # Capture the last call args so tests can inspect them.
        self.calls: list[dict[str, Any]] = []
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = self._create

    async def _create(self, **kwargs: Any) -> _ScriptedStream:
        self.calls.append(kwargs)
        if self._call >= len(self._iterations):
            raise AssertionError(
                f"agent asked for iteration #{self._call} but only {len(self._iterations)} scripted"
            )
        chunks = self._iterations[self._call]
        self._call += 1
        return _ScriptedStream(chunks)


# --- Helper: stub the MCP-side SQL handle -----------------------------


def _stub_sql(rows: list[list[object]]) -> Sql:
    stub = MagicMock(spec=Sql)
    response = MagicMock()
    response.status.state = StatementState.SUCCEEDED
    response.status.error = None
    response.result.data_array = rows
    response.manifest.schema.columns = []
    stub.execute_statement = MagicMock(return_value=response)
    return stub


# --- Tests ------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_context_block_is_folded_into_system_prompt() -> None:
    """The reverse-channel `context` (UI.md §3.2.1) must reach the model
    via the system prompt, not via a tool call."""
    client = _ScriptedClient(iterations=[[_chunk_text("ok")]])
    async for _ in run_agent(
        messages=[{"role": "user", "content": "hi"}],
        context={
            "viewport": {"bbox": [4.5, 45.4, 5.2, 46.1], "zoom": 11, "center": [4.85, 45.75]},
            "active_layers": [{"layer_id": "hazard_ppri_communes", "row_count": 237}],
        },
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        pass
    # The first FMAPI call carries the augmented system message.
    sys_msg = client.calls[0]["messages"][0]
    assert sys_msg["role"] == "system"
    content = sys_msg["content"]
    assert "Current map state" in content
    assert "bbox=[4.500" in content
    assert "hazard_ppri_communes (237 features)" in content


@pytest.mark.anyio("asyncio")
async def test_text_only_turn_emits_delta_and_done(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tool calls: agent streams text, ends with `done`."""
    client = _ScriptedClient(iterations=[[_chunk_text("Bonjour "), _chunk_text("Lucas.")]])
    events: list[Any] = []
    async for ev in run_agent(
        messages=[{"role": "user", "content": "hi"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        events.append((ev.name, ev.data))
    assert [e[0] for e in events] == ["delta", "delta", "done"]
    assert events[0][1]["text"] == "Bonjour "
    assert events[1][1]["text"] == "Lucas."
    assert events[2][1]["final_text"] == "Bonjour Lucas."


@pytest.mark.anyio("asyncio")
async def test_single_tool_call_dispatches_to_mcp_then_finalises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Iteration 1 = tool call to `list_layers`; iteration 2 = final text."""
    sql = _stub_sql(
        [
            [
                "hazard_rga_h3",
                "cat.catnat_gold.hazard_rga_h3",
                "drought",
                "gold",
                "h3_r9_cell",
                "h3",
                None,
                "Etalab",
                True,
                "RGA",
            ]
        ]
    )
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, "cat"))

    client = _ScriptedClient(
        iterations=[
            [
                _chunk_text("Let me check."),
                _chunk_tool_call(index=0, id="call_1", name="list_layers", arguments=""),
                _chunk_tool_call(index=0, arguments="{}"),
            ],
            [_chunk_text("Found 1 layer: RGA drought.")],
        ]
    )
    events: list[Any] = []
    async for ev in run_agent(
        messages=[{"role": "user", "content": "quelles couches sont disponibles?"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        events.append((ev.name, ev.data))

    names = [e[0] for e in events]
    assert names == ["delta", "tool_call", "tool_result", "delta", "done"]

    # Tool call payload
    tc_event = next(e[1] for e in events if e[0] == "tool_call")
    assert tc_event["name"] == "list_layers"
    assert tc_event["id"] == "call_1"
    assert tc_event["arguments"] == {}

    # Tool result payload — structuredContent surfaces as the result dict
    tr_event = next(e[1] for e in events if e[0] == "tool_result")
    assert tr_event["is_error"] is False
    # FastMCP wraps a list-return tool's payload under `{result: [...]}`.
    assert isinstance(tr_event["result"], dict)
    assert len(tr_event["result"]["result"]) == 1
    assert tr_event["result"]["result"][0]["peril"] == "drought"


@pytest.mark.anyio("asyncio")
async def test_tool_error_is_surfaced_to_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`query_layer` against an unknown layer → tool_result.is_error=True;
    the agent then sees the error and can recover (we check it gets a chance to)."""

    # No rows from the allowlist lookup → MCP returns isError=True.
    sql = _stub_sql([])
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, "cat"))

    client = _ScriptedClient(
        iterations=[
            [
                _chunk_tool_call(index=0, id="call_x", name="query_layer", arguments=""),
                _chunk_tool_call(index=0, arguments='{"layer_id":"does_not_exist"}'),
            ],
            [_chunk_text("That layer does not exist; try `list_layers` first.")],
        ]
    )
    events = []
    async for ev in run_agent(
        messages=[{"role": "user", "content": "show me does_not_exist"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        events.append((ev.name, ev.data))

    tr = next(e for e in events if e[0] == "tool_result")[1]
    assert tr["is_error"] is True


@pytest.mark.anyio("asyncio")
async def test_empty_tool_arguments_normalised_to_object_for_next_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: Claude streams `arguments=""` for zero-arg tools and the
    Anthropic FMAPI validator then rejects the next request with
    `INVALID_PARAMETER_VALUE: ... not a valid JSON string`. The loop must
    normalise `""` to `"{}"` before resending the history."""
    sql = _stub_sql(
        [
            [
                "hazard_rga_h3",
                "cat.catnat_gold.hazard_rga_h3",
                "drought",
                "gold",
                "h3",
                "h3",
                None,
                "Etalab",
                True,
                "RGA",
            ]
        ]
    )
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, "cat"))

    # Only ONE arguments chunk, and it's empty — no follow-up "{}".
    client = _ScriptedClient(
        iterations=[
            [_chunk_tool_call(index=0, id="call_1", name="list_layers", arguments="")],
            [_chunk_text("ok")],
        ]
    )
    async for _ in run_agent(
        messages=[{"role": "user", "content": "layers?"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        pass

    # Inspect what we sent on iteration #2 (the resubmit with the tool result).
    second = client.calls[1]
    assistant_msg = next(m for m in second["messages"] if m["role"] == "assistant")
    assert assistant_msg["tool_calls"][0]["function"]["arguments"] == "{}", (
        "empty arguments must be normalised to '{}' so FMAPI accepts the resubmit"
    )


@pytest.mark.anyio("asyncio")
async def test_ui_tool_result_emits_map_op_and_strips_geojson_for_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a tool result has an `op` field, the loop emits both a
    `map_op` event (full payload) and a `tool_result` (geojson stripped
    so the LLM doesn't drown in features)."""
    # Allowlist row for a polygon layer, then the add_layer query result.
    polygon_row = [
        [
            "hazard_ppri_communes",
            "cat.catnat_silver.hazard_ppri_communes",
            "flood",
            "silver",
            "polygon",
            None,
            "geometry",
            True,
        ]
    ]
    sql = MagicMock(spec=Sql)
    responses = []
    # 1) allowlist lookup
    r1 = MagicMock()
    r1.status.state = StatementState.SUCCEEDED
    r1.status.error = None
    r1.result.data_array = polygon_row
    r1.manifest.schema.columns = []
    responses.append(r1)
    # 2) add_layer SELECT
    r2 = MagicMock()
    r2.status.state = StatementState.SUCCEEDED
    r2.status.error = None
    r2.result.data_array = [['{"type":"Polygon","coordinates":[[[0,0],[1,1],[1,0],[0,0]]]}', "x"]]
    r2.manifest.schema.columns = [MagicMock(), MagicMock()]
    r2.manifest.schema.columns[0].name = "geom_geojson"
    r2.manifest.schema.columns[1].name = "code_dep"
    responses.append(r2)
    sql.execute_statement = MagicMock(side_effect=responses)
    monkeypatch.setattr(_sql_client_mod, "get_app_sql", lambda: (sql, "cat"))

    client = _ScriptedClient(
        iterations=[
            [
                _chunk_tool_call(index=0, id="call_1", name="add_layer", arguments=""),
                _chunk_tool_call(index=0, arguments='{"layer_id":"hazard_ppri_communes"}'),
            ],
            [_chunk_text("Done.")],
        ]
    )
    events: list[Any] = []
    async for ev in run_agent(
        messages=[{"role": "user", "content": "show me PPRI"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        events.append((ev.name, ev.data))

    names = [e[0] for e in events]
    # map_op must precede the tool_result so the FE sees the UI mutation
    # before the LLM-visible summary.
    assert "map_op" in names
    assert names.index("map_op") < names.index("tool_result")

    map_op = next(e for e in events if e[0] == "map_op")[1]
    assert map_op["op"] == "add_layer"
    assert map_op["layer_id"] == "hazard_ppri_communes"
    # Full geojson present in map_op
    assert "geojson" in map_op
    assert map_op["geojson"]["type"] == "FeatureCollection"

    tool_result = next(e for e in events if e[0] == "tool_result")[1]
    # geojson stripped from LLM-bound payload
    assert "geojson" not in tool_result["result"]
    assert tool_result["result"]["op"] == "add_layer"
    assert tool_result["result"]["row_count"] == 1

    # And the same stripped payload is what we resubmit in history.
    second = client.calls[1]
    tool_msg = next(m for m in second["messages"] if m["role"] == "tool")
    assert "geojson" not in tool_msg["content"]


@pytest.mark.anyio("asyncio")
async def test_max_iterations_emits_error() -> None:
    """If FMAPI keeps emitting tool calls forever, we abort cleanly."""
    # Script enough iterations that each one is a tool call. We script
    # exactly MAX_ITERATIONS so the loop hits the cap.
    from catnat_app.backend.agent.loop import MAX_ITERATIONS

    # Use a recoverable tool (buffer) — but the mock client never produces
    # a final text turn, so we just spin.
    iteration = [
        _chunk_tool_call(index=0, id="call_x", name="buffer", arguments=""),
        _chunk_tool_call(index=0, arguments='{"geom_wkt":"POINT(0 0)","meters":10}'),
    ]
    client = _ScriptedClient(iterations=[iteration for _ in range(MAX_ITERATIONS)])

    # buffer doesn't go through the allowlist; we still need a real Sql stub
    # because buffer_impl executes a statement.
    sql = _stub_sql([["POLYGON((0 0,1 1,0 0))"]])
    _sql_client_mod.get_app_sql = lambda: (sql, "cat")

    events = []
    async for ev in run_agent(
        messages=[{"role": "user", "content": "buffer forever"}],
        client_factory=lambda: client,  # type: ignore[arg-type]
    ):
        events.append((ev.name, ev.data))

    assert events[-1][0] == "error"
    assert "exceeded" in events[-1][1]["message"]
