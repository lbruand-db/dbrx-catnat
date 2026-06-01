import os

from databricks.sdk.service.iam import User as UserOut
from databricks.sdk.service.sql import StatementState

from .core import Dependencies, create_router
from .models import Layer, LayerListOut, VersionOut

router = create_router()


def _catalog() -> str:
    """Read `CATNAT_CATALOG` lazily so tests can override per-call."""
    return os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")


@router.get("/version", response_model=VersionOut, operation_id="version")
async def version():
    return VersionOut.from_metadata()


@router.get("/current-user", response_model=UserOut, operation_id="currentUser")
def me(user_ws: Dependencies.UserClient):
    return user_ws.current_user.me()


_LAYERS_SQL = """
SELECT
    layer_id, table_fq, peril, medallion, grain,
    h3_column, geom_column, license, is_displayable, description
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
ORDER BY peril, medallion, layer_id
"""


@router.get("/layers", response_model=LayerListOut, operation_id="listLayers")
def list_layers(sql: Dependencies.Sql) -> LayerListOut:
    """Return every catnat layer the demo can surface.

    Reads `catnat_silver.layer_index` via the user's SQL warehouse OBO token,
    so RLS / GRANTs apply as for any other query the user runs themselves.
    """
    response = sql.execute_statement(
        statement=_LAYERS_SQL,
        wait_timeout="30s",
        parameters=[{"name": "catalog", "value": _catalog(), "type": "STRING"}],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"layer_index query failed: {msg}")
    rows = response.result.data_array if response.result else []
    layers = [
        Layer(
            layer_id=r[0],
            table_fq=r[1],
            peril=r[2],
            medallion=r[3],
            grain=r[4],
            h3_column=r[5],
            geom_column=r[6],
            license=r[7],
            is_displayable=r[8] == "true" if isinstance(r[8], str) else bool(r[8]),
            description=r[9],
        )
        for r in (rows or [])
    ]
    return LayerListOut(layers=layers)
