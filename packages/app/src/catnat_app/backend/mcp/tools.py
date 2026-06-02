"""MCP tool implementations.

Each tool is implemented as a plain function that takes a `Sql` instance
(so unit tests can inject a stub) plus its semantic arguments, then
registered with the FastMCP server in `register(...)`. The MCP-facing
wrappers translate FastMCP's call surface to the plain function.
"""

from __future__ import annotations

from typing import Any

from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from mcp.server.fastmcp import FastMCP

from ..core.sql import Sql
from . import _sql_client
from . import sql_templates as t
from .allowlist import LayerNotAllowed, get_allowed_layer

# --- Shared helpers -----------------------------------------------------


def _run(sql: Sql, statement: str, parameters: list[StatementParameterListItem]) -> list[list[Any]]:
    """Execute a parameterised statement and return the row array.

    Raises `RuntimeError` on warehouse-side failures so the FastMCP server
    surfaces a tool error (rather than a successful empty payload).
    """
    response = sql.execute_statement(statement=statement, wait_timeout="30s", parameters=parameters)
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"sql failed: {msg}")
    return list(response.result.data_array or []) if response.result else []


def _column_names(response_result_manifest_schema_columns: Any) -> list[str]:
    """Extract column names from a Statement Execution API manifest."""
    if not response_result_manifest_schema_columns:
        return []
    return [c.name for c in response_result_manifest_schema_columns]


def _run_with_columns(
    sql: Sql, statement: str, parameters: list[StatementParameterListItem]
) -> tuple[list[str], list[list[Any]]]:
    """Like `_run`, but also returns the column names from the response manifest."""
    response = sql.execute_statement(statement=statement, wait_timeout="30s", parameters=parameters)
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"sql failed: {msg}")
    cols: list[str] = []
    if response.manifest and response.manifest.schema and response.manifest.schema.columns:
        cols = [c.name for c in response.manifest.schema.columns]
    rows: list[list[Any]] = list(response.result.data_array or []) if response.result else []
    return cols, rows


def _rows_as_dicts(columns: list[str], rows: list[list[Any]]) -> list[dict[str, Any]]:
    return [dict(zip(columns, r, strict=False)) for r in rows]


# --- Tool impls ---------------------------------------------------------


_LIST_LAYERS_SQL = """
SELECT
    layer_id, table_fq, peril, medallion, grain,
    h3_column, geom_column, license, is_displayable, description
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
WHERE is_displayable = true
ORDER BY peril, medallion, layer_id
"""


def list_layers_impl(sql: Sql, catalog: str) -> list[dict[str, Any]]:
    """Every displayable row of `catnat_silver.layer_index`."""
    rows = _run(
        sql,
        _LIST_LAYERS_SQL,
        [StatementParameterListItem(name="catalog", value=catalog, type="STRING")],
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "layer_id": r[0],
                "table_fq": r[1],
                "peril": r[2],
                "medallion": r[3],
                "grain": r[4],
                "h3_column": r[5],
                "geom_column": r[6],
                "license": r[7],
                "is_displayable": (r[8] == "true" if isinstance(r[8], str) else bool(r[8])),
                "description": r[9],
            }
        )
    return out


def query_layer_impl(
    sql: Sql,
    catalog: str,
    layer_id: str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    where: dict[str, str | int | float | bool] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Constrained SELECT against an allowlisted layer."""
    layer = get_allowed_layer(sql, catalog, layer_id)
    built = t.build_query_layer(layer, bbox=bbox, where=where, limit=limit)
    columns, rows = _run_with_columns(sql, built.statement, built.parameters)
    return {
        "layer_id": layer.layer_id,
        "table_fq": layer.table_fq,
        "columns": columns,
        "rows": _rows_as_dicts(columns, rows),
        "row_count": len(rows),
        "truncated": len(rows) >= t.QUERY_LAYER_MAX_ROWS,
    }


def intersect_layer_impl(
    sql: Sql,
    catalog: str,
    layer_id: str,
    *,
    geom_wkt: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Features from `layer_id` that intersect `geom_wkt` (EPSG:4326)."""
    layer = get_allowed_layer(sql, catalog, layer_id)
    built = t.build_intersect_layer(layer, geom_wkt=geom_wkt, limit=limit)
    columns, rows = _run_with_columns(sql, built.statement, built.parameters)
    return {
        "layer_id": layer.layer_id,
        "columns": columns,
        "rows": _rows_as_dicts(columns, rows),
        "row_count": len(rows),
    }


def nearest_impl(
    sql: Sql,
    catalog: str,
    layer_id: str,
    *,
    point_wkt: str,
    k: int = 5,
) -> dict[str, Any]:
    """k features in `layer_id` nearest to `point_wkt`, ordered by distance."""
    layer = get_allowed_layer(sql, catalog, layer_id)
    built = t.build_nearest(layer, point_wkt=point_wkt, k=k)
    columns, rows = _run_with_columns(sql, built.statement, built.parameters)
    return {
        "layer_id": layer.layer_id,
        "columns": columns,
        "rows": _rows_as_dicts(columns, rows),
        "row_count": len(rows),
    }


def buffer_impl(sql: Sql, *, geom_wkt: str, meters: float) -> dict[str, Any]:
    """ST_Buffer wrapper. Returns the buffered geometry as WKT."""
    built = t.build_buffer(geom_wkt=geom_wkt, meters=meters)
    rows = _run(sql, built.statement, built.parameters)
    if not rows:
        raise RuntimeError("buffer returned no rows")
    return {"buffered_wkt": rows[0][0], "meters": meters}


# --- Registration -------------------------------------------------------


def register(server: FastMCP) -> None:
    """Bind tool implementations to the FastMCP server."""

    @server.tool(
        name="list_layers",
        description=(
            "Enumerate every displayable layer in Unity Catalog. Returns a "
            "list of `{layer_id, table_fq, peril, medallion, grain, "
            "h3_column, geom_column, license, description}`. Use the "
            "`layer_id` for follow-up tool calls."
        ),
    )
    def _list_layers() -> list[dict[str, Any]]:
        sql, catalog = _sql_client.get_app_sql()
        return list_layers_impl(sql, catalog)

    @server.tool(
        name="query_layer",
        description=(
            "SELECT a constrained slice of an allowlisted layer. "
            "`layer_id` must come from `list_layers`. `bbox` is "
            "[min_lon,min_lat,max_lon,max_lat] (geometry layers only). "
            "`where` is an AND-joined dict of column = value predicates. "
            "Returns up to 500 rows with binary geometries projected to "
            "GeoJSON / H3 cells projected to hex strings."
        ),
    )
    def _query_layer(
        layer_id: str,
        bbox: list[float] | None = None,
        where: dict[str, str | int | float | bool] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        sql, catalog = _sql_client.get_app_sql()
        bbox_tuple: tuple[float, float, float, float] | None = None
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat]")
            bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
        try:
            return query_layer_impl(
                sql, catalog, layer_id, bbox=bbox_tuple, where=where, limit=limit
            )
        except LayerNotAllowed as e:
            raise ValueError(str(e)) from e

    @server.tool(
        name="intersect_layer",
        description=(
            "Return features from `layer_id` whose geometry intersects "
            "the supplied WKT geometry (EPSG:4326). Requires the layer to "
            "have a `geom_column` (use `list_layers` to check). Up to 500 "
            "rows; results dropped beyond that — narrow the geometry."
        ),
    )
    def _intersect_layer(layer_id: str, geom_wkt: str, limit: int = 100) -> dict[str, Any]:
        sql, catalog = _sql_client.get_app_sql()
        try:
            return intersect_layer_impl(sql, catalog, layer_id, geom_wkt=geom_wkt, limit=limit)
        except LayerNotAllowed as e:
            raise ValueError(str(e)) from e

    @server.tool(
        name="nearest",
        description=(
            "Return the `k` features from `layer_id` nearest to "
            "`point_wkt` (EPSG:4326), ordered by ST_Distance. Requires a "
            "geom_column on the layer."
        ),
    )
    def _nearest(layer_id: str, point_wkt: str, k: int = 5) -> dict[str, Any]:
        sql, catalog = _sql_client.get_app_sql()
        try:
            return nearest_impl(sql, catalog, layer_id, point_wkt=point_wkt, k=k)
        except LayerNotAllowed as e:
            raise ValueError(str(e)) from e

    @server.tool(
        name="buffer",
        description=(
            "Return `ST_Buffer(geom_wkt, meters)` as WKT. Convenience "
            "wrapper to expand a point/line/polygon by a metric distance "
            "(approximated as a degree offset at French latitudes)."
        ),
    )
    def _buffer(geom_wkt: str, meters: float) -> dict[str, Any]:
        sql, _ = _sql_client.get_app_sql()
        return buffer_impl(sql, geom_wkt=geom_wkt, meters=meters)


__all__ = [
    "buffer_impl",
    "intersect_layer_impl",
    "list_layers_impl",
    "nearest_impl",
    "query_layer_impl",
    "register",
]
