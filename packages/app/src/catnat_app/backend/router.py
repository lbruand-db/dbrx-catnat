import os

from databricks.sdk.service.iam import User as UserOut
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from .core import Dependencies, create_router
from .models import KeplerDatasetOut, Layer, LayerListOut, VersionOut

# Hard cap on rows the Kepler endpoint returns — keeps the HTTP payload
# bounded and the browser-side Kepler instance responsive. Dept 069 has ~60k
# H3 r=9 cells in admin_communes_h3; 8k is enough for a visually rich heatmap
# without paying for the long tail of cells with zero policies.
_KEPLER_PORTFOLIO_LIMIT = 8000

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


_PORTFOLIO_KEPLER_SQL = """
SELECT
    h3_h3tostring(h3)                       AS h3,
    code_dep,
    n_policies,
    sum_insured_value_eur,
    n_flood,
    n_rga,
    n_storm
FROM IDENTIFIER(:catalog || '.catnat_gold.portfolio_policies_h3')
ORDER BY sum_insured_value_eur DESC
LIMIT :row_limit
"""


@router.get(
    "/kepler/portfolio",
    response_model=KeplerDatasetOut,
    operation_id="keplerPortfolio",
)
def kepler_portfolio(sql: Dependencies.Sql) -> KeplerDatasetOut:
    """Return the portfolio H3 rollup as a Kepler-loadable dataset.

    H3 cell ids are stringified via `h3_h3tostring` so Kepler's H3 layer
    accepts them directly (its `processH3Layer` expects 15-char hex strings,
    not BIGINT). The top-N-by-insured-value sort keeps the worst cells in
    frame even when we cap the row count.
    """
    response = sql.execute_statement(
        statement=_PORTFOLIO_KEPLER_SQL,
        wait_timeout="30s",
        parameters=[
            StatementParameterListItem(name="catalog", value=_catalog(), type="STRING"),
            StatementParameterListItem(
                name="row_limit", value=str(_KEPLER_PORTFOLIO_LIMIT), type="INT"
            ),
        ],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"portfolio kepler query failed: {msg}")
    raw_rows = response.result.data_array if response.result else []
    fields = ["h3", "code_dep", "n_policies", "sum_insured_value_eur", "n_flood", "n_rga", "n_storm"]
    rows: list[dict[str, str | int | float | None]] = []
    for r in raw_rows or []:
        rows.append(
            {
                "h3": r[0],
                "code_dep": r[1],
                "n_policies": int(r[2]) if r[2] is not None else None,
                "sum_insured_value_eur": int(r[3]) if r[3] is not None else None,
                "n_flood": int(r[4]) if r[4] is not None else None,
                "n_rga": int(r[5]) if r[5] is not None else None,
                "n_storm": int(r[6]) if r[6] is not None else None,
            }
        )
    return KeplerDatasetOut(
        id="portfolio_h3", label="Portfolio exposure (H3 r=9)", fields=fields, rows=rows
    )


@router.get("/layers", response_model=LayerListOut, operation_id="listLayers")
def list_layers(sql: Dependencies.Sql) -> LayerListOut:
    """Return every catnat layer the demo can surface.

    Reads `catnat_silver.layer_index` via the user's SQL warehouse OBO token,
    so RLS / GRANTs apply as for any other query the user runs themselves.
    """
    response = sql.execute_statement(
        statement=_LAYERS_SQL,
        wait_timeout="30s",
        parameters=[
            StatementParameterListItem(name="catalog", value=_catalog(), type="STRING")
        ],
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
