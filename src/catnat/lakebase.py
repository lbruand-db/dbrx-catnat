"""Lakebase Postgres connection helpers.

Used by:
- `catnat mirror` CLI — runs `mirror_silver_to_lakebase` from a developer
  laptop (auth = the user's Databricks CLI profile).
- The catnat-app FastAPI route `/api/tiles/<layer>/{z}/{x}/{y}.pbf` —
  same code path, auth = the app's service principal token.

The Databricks SDK is the single credential resolver: every connection
attempt re-asks `WorkspaceClient.postgres.generate_database_credential`
for a fresh password. The credentials are short-lived (~1h) so we don't
cache them across `asyncpg.connect` calls; for a pool, we set a
`setup` hook to bind a new password per connection at acquisition
time (TODO once the pool path is wired).

See SPEC §10.7 for the architectural decision.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from databricks.sdk import WorkspaceClient

# Lakebase resource identity for the catnat demo. Provisioned via the
# setup script (`src/catnat/notebooks/_setup/30_lakebase_provision.py`).
LAKEBASE_PROJECT = "catnat-tiles"
LAKEBASE_BRANCH = "production"
LAKEBASE_ENDPOINT = "primary"
LAKEBASE_DATABASE = "databricks_postgres"
LAKEBASE_SCHEMA = "geo"


def endpoint_resource() -> str:
    return f"projects/{LAKEBASE_PROJECT}/branches/{LAKEBASE_BRANCH}/endpoints/{LAKEBASE_ENDPOINT}"


@dataclass(frozen=True)
class LakebaseConfig:
    host: str
    user: str
    database: str = LAKEBASE_DATABASE
    port: int = 5432


def resolve_config(ws: WorkspaceClient) -> LakebaseConfig:
    """Look up the endpoint host + auth identity from the SDK.

    The user is whoever the WorkspaceClient is authenticated as — a
    real human via a CLI profile, or the app SP in the runtime. Both
    paths land in Postgres as the corresponding role (the human's
    email for users, the SP's client_id UUID for service principals,
    per the LAKEBASE_OAUTH_V1 mapping).
    """
    endpoint = ws.postgres.get_endpoint(name=endpoint_resource())
    host = endpoint.status.hosts.host  # type: ignore[union-attr]

    # For human users, current_user.me() returns the SCIM user with
    # `user_name` set to the email. For SPs, it returns the SP with
    # `application_id` (the UUID) — and the Postgres role for an SP
    # is its application_id, not the SCIM display name.
    me = ws.current_user.me()
    if me.user_name:
        user = me.user_name
    else:
        # Service principals authenticate to Postgres as their
        # application_id (the LAKEBASE_OAUTH_V1 mapping).
        app_id = getattr(me, "application_id", None) or me.id or ""
        user = str(app_id)

    return LakebaseConfig(host=host, user=user)


async def connect(ws: WorkspaceClient) -> asyncpg.Connection:
    """Open a single asyncpg connection with a fresh credential."""
    cfg = resolve_config(ws)
    cred = ws.postgres.generate_database_credential(endpoint=endpoint_resource())
    return await asyncpg.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cred.token,  # type: ignore[union-attr]
        database=cfg.database,
        ssl="require",
    )


__all__ = [
    "LAKEBASE_BRANCH",
    "LAKEBASE_DATABASE",
    "LAKEBASE_ENDPOINT",
    "LAKEBASE_PROJECT",
    "LAKEBASE_SCHEMA",
    "LakebaseConfig",
    "connect",
    "endpoint_resource",
    "resolve_config",
]
