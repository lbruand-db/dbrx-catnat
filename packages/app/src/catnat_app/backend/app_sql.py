"""SQL dependency backed by the app's service-principal `WorkspaceClient`.

Phase 2.5 originally wired `/api/layers` and `/api/kepler/portfolio` through
apx's OBO `Sql` dependency (`Dependencies.Sql`), but the Apps gateway minted
OBO tokens never carry the `sql` scope on this workspace even with
`user_api_scopes: ["sql"]` declared on the app resource. The platform-level
fix is unresolved; for a demo over public hazard data and a synthetic
portfolio, attributing queries to the app SP is acceptable.

The app SP already has `CAN_USE` on the warehouse via the
`resources.sql_warehouse` block in `app.yml`, so this path Just Works.
"""

from __future__ import annotations

from typing import Annotated, TypeAlias

from fastapi import Depends, Request

from .core._defaults import ClientDependency
from .core.sql import Sql, SqlConfig


def _get_app_sql(request: Request, ws: ClientDependency) -> Sql:
    config: SqlConfig | None = getattr(request.app.state, "sql_config", None)
    if config is None:
        config = SqlConfig()  # ty: ignore[missing-argument]
    return Sql(config=config, api=ws.statement_execution)


AppSqlDependency: TypeAlias = Annotated[Sql, Depends(_get_app_sql)]
