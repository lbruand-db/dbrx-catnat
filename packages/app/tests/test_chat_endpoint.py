"""Smoke test for the `/api/chat` SSE route.

Mocks the agent loop so we don't depend on FMAPI; verifies the route
wraps the agent stream into a `text/event-stream` response with the
right SSE record format.
"""

from __future__ import annotations

from typing import Any

import pytest
from catnat_app.backend import router as router_mod
from catnat_app.backend.agent.events import AgentEvent
from catnat_app.backend.app import app
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


async def _fake_run_agent(messages: list[dict[str, Any]], **_: Any):
    """Three-event stream regardless of input."""
    yield AgentEvent("delta", {"text": "Bonjour"})
    yield AgentEvent("delta", {"text": " Lucas"})
    yield AgentEvent("done", {"final_text": "Bonjour Lucas"})


def test_chat_endpoint_streams_sse_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router_mod, "run_agent", _fake_run_agent)

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    body = resp.text
    # Each SSE record is `event: <name>\ndata: <json>\n\n`.
    assert "event: delta" in body
    assert "event: done" in body
    assert '"final_text": "Bonjour Lucas"' in body
    # Two delta records expected.
    assert body.count("event: delta") == 2


def test_chat_endpoint_rejects_missing_messages(client: TestClient) -> None:
    resp = client.post("/api/chat", json={})
    assert resp.status_code == 422
