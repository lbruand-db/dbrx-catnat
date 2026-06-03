"""OpenAI-SDK client pointed at the Databricks Foundation Model API.

Databricks exposes Claude over an OpenAI-compatible `/serving-endpoints/<endpoint>/invocations`
surface. The OpenAI Python SDK works against it directly when we set
`base_url=<workspace>/serving-endpoints` and authenticate with the app's
service-principal token.

App-SP tokens expire after ~1 hour. We can't bake a token into the
`AsyncOpenAI` instance — that's what we did originally and it produced
`PermissionDeniedError: Invalid Token` after the first hour. Instead we
hand the OpenAI SDK a custom `httpx.AsyncClient` whose `auth` flow asks
the Databricks SDK for fresh credentials on every request. The SDK's
`Config.authenticate()` is the right call site: it caches the current
token and silently refreshes when it's near expiry.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from functools import lru_cache

import httpx
from databricks.sdk import WorkspaceClient
from openai import AsyncOpenAI

DEFAULT_MODEL = "databricks-claude-sonnet-4-6"


def get_model() -> str:
    """Resolve the chat-completion model endpoint name from the environment."""
    return os.environ.get("CATNAT_AGENT_MODEL", DEFAULT_MODEL)


class _DatabricksTokenAuth(httpx.Auth):
    """httpx auth flow that re-asks the Databricks SDK for credentials
    on every outgoing request.

    The SDK's `Config.authenticate()` is the canonical credential
    resolver — it handles PAT, OAuth M2M, OAuth U2M, ambient app-SP and
    refreshes expired tokens transparently.
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(self, ws: WorkspaceClient) -> None:
        self._ws = ws

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        for name, value in self._ws.config.authenticate().items():
            request.headers[name] = value
        yield request


@lru_cache(maxsize=1)
def _async_client() -> AsyncOpenAI:
    """Lazy-build the AsyncOpenAI client.

    The bearer token is resolved per-request via `_DatabricksTokenAuth`
    so we survive the ~1h app-SP token lifetime without restarting the
    process.
    """
    ws = WorkspaceClient()
    base_url = f"{ws.config.host.rstrip('/')}/serving-endpoints"
    http_client = httpx.AsyncClient(auth=_DatabricksTokenAuth(ws))
    return AsyncOpenAI(
        base_url=base_url,
        # OpenAI SDK requires *some* api_key value; ours is overridden
        # per-request by the auth flow above. Any non-empty string works.
        api_key="databricks-sdk-managed",
        http_client=http_client,
    )


def get_client() -> AsyncOpenAI:
    return _async_client()


__all__ = ["DEFAULT_MODEL", "_DatabricksTokenAuth", "get_client", "get_model"]
