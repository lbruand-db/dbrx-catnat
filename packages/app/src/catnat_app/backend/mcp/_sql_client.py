"""Lazy singleton SQL handle for MCP tools.

MCP tools run outside the FastAPI request lifecycle, so they can't use the
`AppSqlDependency` DI hook. Instead we hold a module-level `Sql` wrapped
around an app-SP `WorkspaceClient` — the same identity the `/api/layers`
route uses, just acquired imperatively.
"""

from __future__ import annotations

import os
from functools import lru_cache

from databricks.sdk import WorkspaceClient

from ..core.sql import Sql, SqlConfig


@lru_cache(maxsize=1)
def _client() -> WorkspaceClient:
    return WorkspaceClient()


@lru_cache(maxsize=1)
def _config() -> SqlConfig:
    return SqlConfig()  # ty: ignore[missing-argument]


def get_app_sql() -> tuple[Sql, str]:
    """Return a (Sql, catalog) pair for use inside an MCP tool body."""
    catalog = os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")
    return Sql(config=_config(), api=_client().statement_execution), catalog


__all__ = ["get_app_sql"]
