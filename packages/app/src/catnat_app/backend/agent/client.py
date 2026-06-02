"""OpenAI-SDK client pointed at the Databricks Foundation Model API.

Databricks exposes Claude over an OpenAI-compatible `/serving-endpoints/<endpoint>/invocations`
surface. The OpenAI Python SDK works against it directly when we set
`base_url=<workspace>/serving-endpoints` and authenticate with the app's
service-principal token.
"""

from __future__ import annotations

import os
from functools import lru_cache

from databricks.sdk import WorkspaceClient
from openai import AsyncOpenAI

DEFAULT_MODEL = "databricks-claude-sonnet-4-6"


def get_model() -> str:
    """Resolve the chat-completion model endpoint name from the environment."""
    return os.environ.get("CATNAT_AGENT_MODEL", DEFAULT_MODEL)


@lru_cache(maxsize=1)
def _async_client() -> AsyncOpenAI:
    """Lazy-build the AsyncOpenAI client.

    Uses the app SP's auth via `WorkspaceClient().config` — the same
    identity that grants `USE CATALOG` on the catnat schemas. The host
    and token are pulled from the DAB-supplied env.
    """
    ws = WorkspaceClient()
    base_url = f"{ws.config.host.rstrip('/')}/serving-endpoints"
    return AsyncOpenAI(
        base_url=base_url,
        # `databricks-sdk` resolves the right credential (PAT, OAuth M2M,
        # ambient app SP) — we mint a fresh header token per process.
        api_key=ws.config.authenticate()["Authorization"].split(" ", 1)[1],
    )


def get_client() -> AsyncOpenAI:
    return _async_client()


__all__ = ["DEFAULT_MODEL", "get_client", "get_model"]
