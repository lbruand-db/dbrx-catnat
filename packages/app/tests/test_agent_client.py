"""Regression tests for `backend/agent/client.py`.

The motivating bug: the original implementation baked the bearer into
`AsyncOpenAI` at construction time. The app SP token expired after an
hour and FMAPI started returning 403 `Invalid Token`. The fix is the
`_DatabricksTokenAuth` httpx auth flow — these tests pin that it asks
the SDK for credentials on EVERY request rather than once at startup.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
from catnat_app.backend.agent.client import _DatabricksTokenAuth


def _build_request() -> httpx.Request:
    return httpx.Request("POST", "https://workspace/serving-endpoints/x/invocations")


def test_auth_flow_attaches_authorization_header_from_sdk() -> None:
    ws = MagicMock()
    ws.config.authenticate.return_value = {"Authorization": "Bearer t1"}

    auth = _DatabricksTokenAuth(ws)
    request = _build_request()

    flow = auth.auth_flow(request)
    sent = next(flow)
    assert sent.headers["Authorization"] == "Bearer t1"


def test_auth_flow_re_asks_sdk_on_every_request() -> None:
    """The whole point of the auth callback: credentials must be fresh
    per request, so a token-refresh inside the SDK takes effect on the
    next outgoing call without restarting the process."""
    ws = MagicMock()
    ws.config.authenticate.side_effect = [
        {"Authorization": "Bearer first"},
        {"Authorization": "Bearer refreshed"},
    ]
    auth = _DatabricksTokenAuth(ws)

    first = next(auth.auth_flow(_build_request()))
    second = next(auth.auth_flow(_build_request()))

    assert first.headers["Authorization"] == "Bearer first"
    assert second.headers["Authorization"] == "Bearer refreshed"
    assert ws.config.authenticate.call_count == 2


def test_auth_flow_forwards_extra_headers_returned_by_sdk() -> None:
    """`Config.authenticate()` may return additional headers (e.g.
    Cloudflare workspace identifiers). All of them should attach."""
    ws = MagicMock()
    ws.config.authenticate.return_value = {
        "Authorization": "Bearer t",
        "X-Databricks-Workspace-Id": "12345",
    }
    auth = _DatabricksTokenAuth(ws)
    request = next(auth.auth_flow(_build_request()))
    assert request.headers["Authorization"] == "Bearer t"
    assert request.headers["X-Databricks-Workspace-Id"] == "12345"
