"""MCP tools that mutate the Leaflet UI.

Unlike the data-only tools in `tools.py`, these tools' "side effect" is
a UI change: render a layer, fly the camera, restyle. The MCP transport
itself can't push to the browser — the agent loop intercepts a tool
result that carries an `op: "<name>"` field and emits a parallel
`map_op` SSE event with the full payload. The LLM-bound `tool_result`
strips the heavy `geojson` field so the model doesn't drown in
features.

For v1 we support polygon-grain layers only on `add_layer`. H3-grain
layers would need either client-side h3-js polyfill or a server-side
`h3_boundaryasgeojson` projection; both are deferred.
"""

from __future__ import annotations

import json
from typing import Any

from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from mcp.server.fastmcp import FastMCP

from ..core.sql import Sql
from . import _sql_client
from .allowlist import LayerNotAllowed, get_allowed_layer
from .sql_templates import _safe_identifier, build_filter_clauses

# Default per-peril styles — sensible-looking choropleth colours the
# agent can override via `style_layer`.
DEFAULT_STYLES: dict[str, dict[str, Any]] = {
    "flood": {"color": "#1f77b4", "fillColor": "#1f77b4", "fillOpacity": 0.35, "weight": 1},
    "drought": {"color": "#8c564b", "fillColor": "#8c564b", "fillOpacity": 0.35, "weight": 1},
    "storm": {"color": "#9467bd", "fillColor": "#9467bd", "fillOpacity": 0.35, "weight": 1},
    "reference": {"color": "#7f7f7f", "fillColor": "#7f7f7f", "fillOpacity": 0.15, "weight": 1},
}


def _default_style(peril: str) -> dict[str, Any]:
    """Pick a default Leaflet path style based on the layer's peril."""
    return DEFAULT_STYLES.get(peril, DEFAULT_STYLES["reference"]).copy()


# ---- add_layer --------------------------------------------------------


# Cap on rows shipped to the FE per layer. Larger than `query_layer`'s
# 500 because the FE renders these as map polygons (cheap) and the LLM
# never sees them.
ADD_LAYER_MAX_ROWS = 2000


def add_layer_impl(
    sql: Sql,
    catalog: str,
    layer_id: str,
    *,
    style: dict[str, Any] | None = None,
    limit: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    where: dict[str, str | int | float | bool] | None = None,
) -> dict[str, Any]:
    """Render a polygon-grain layer on the operational map.

    Pulls up to `ADD_LAYER_MAX_ROWS` features via `ST_AsGeoJSON` and
    bundles them into a GeoJSON FeatureCollection the FE will hand to
    Leaflet's `L.geoJSON`. The `bbox` / `where` filters mirror
    `query_layer` and exist so the agent can scope a layer in a single
    tool call (instead of `query_layer` → eyeball → `add_layer` → zoom).
    """
    layer = get_allowed_layer(sql, catalog, layer_id)
    if not layer.geom_column:
        raise LayerNotAllowed(
            f"add_layer for v1 supports polygon-grain layers only; "
            f"'{layer_id}' has no geom_column (grain={layer.grain})"
        )

    col = _safe_identifier(layer.geom_column)
    capped = max(1, min(limit or ADD_LAYER_MAX_ROWS, ADD_LAYER_MAX_ROWS))

    # Shared filter plumbing with `query_layer`; we always add a
    # `geom IS NOT NULL` guard on top because Leaflet chokes on null
    # features.
    conditions, filter_params = build_filter_clauses(layer, bbox=bbox, where=where)
    conditions.append(f"{col} IS NOT NULL")
    where_sql = "WHERE " + " AND ".join(conditions)

    statement = (
        f"SELECT ST_AsGeoJSON({col}) AS geom_geojson, * EXCEPT ({col}) "
        f"FROM IDENTIFIER(:table_fq) "
        f"{where_sql} "
        f"LIMIT {capped}"
    )
    params = [
        StatementParameterListItem(name="table_fq", value=layer.table_fq, type="STRING"),
        *filter_params,
    ]
    response = sql.execute_statement(
        statement=statement,
        wait_timeout="30s",
        parameters=params,
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"add_layer query failed: {msg}")

    column_names: list[str] = []
    if response.manifest and response.manifest.schema and response.manifest.schema.columns:
        column_names = [c.name for c in response.manifest.schema.columns]
    rows = list(response.result.data_array or []) if response.result else []

    features: list[dict[str, Any]] = []
    for r in rows:
        # `geom_geojson` is column 0; everything else is properties.
        geom_str = r[0]
        if not geom_str:
            continue
        try:
            geometry = json.loads(geom_str)
        except json.JSONDecodeError:
            continue
        props = {column_names[i]: r[i] for i in range(1, len(r))} if column_names else {}
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    return {
        "op": "add_layer",
        "layer_id": layer.layer_id,
        "peril": layer.peril,
        "geojson": {"type": "FeatureCollection", "features": features},
        "style": style or _default_style(layer.peril),
        "row_count": len(features),
        "bbox": list(bbox) if bbox is not None else None,
        "where": where,
        "status": "ok",
    }


# ---- remove_layer -----------------------------------------------------


def remove_layer_impl(layer_id: str) -> dict[str, Any]:
    """No-op server-side — the FE matches by `layer_id` to drop the layer."""
    return {"op": "remove_layer", "layer_id": layer_id, "status": "ok"}


# ---- zoom_to ----------------------------------------------------------


def zoom_to_impl(sql: Sql, *, geom_wkt: str) -> dict[str, Any]:
    """Convert a WKT geometry to a Leaflet-friendly bounds via ST_AsGeoJSON.

    The FE uses Leaflet's GeoJSON layer to compute the bbox + flyTo.
    """
    statement = "SELECT ST_AsGeoJSON(ST_GeomFromText(:wkt, 4326)) AS g"
    response = sql.execute_statement(
        statement=statement,
        wait_timeout="30s",
        parameters=[
            StatementParameterListItem(name="wkt", value=geom_wkt, type="STRING"),
        ],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"zoom_to query failed: {msg}")
    rows = list(response.result.data_array or []) if response.result else []
    if not rows or not rows[0][0]:
        raise ValueError(f"zoom_to: could not parse WKT {geom_wkt!r}")
    return {
        "op": "zoom_to",
        "geom_geojson": json.loads(rows[0][0]),
        "status": "ok",
    }


# ---- style_layer ------------------------------------------------------


def style_layer_impl(
    layer_id: str,
    *,
    color: str | None = None,
    fill_color: str | None = None,
    fill_opacity: float | None = None,
    weight: float | None = None,
) -> dict[str, Any]:
    """Restyle an already-added layer. No SQL — the FE re-applies the style."""
    style: dict[str, Any] = {}
    if color is not None:
        style["color"] = color
    if fill_color is not None:
        style["fillColor"] = fill_color
    if fill_opacity is not None:
        style["fillOpacity"] = fill_opacity
    if weight is not None:
        style["weight"] = weight
    if not style:
        raise ValueError(
            "style_layer: at least one of color/fill_color/fill_opacity/weight required"
        )
    return {
        "op": "style_layer",
        "layer_id": layer_id,
        "style": style,
        "status": "ok",
    }


# ---- registration -----------------------------------------------------


def register(server: FastMCP) -> None:
    @server.tool(
        name="add_layer",
        description=(
            "Render a polygon-grain layer on the operational Leaflet map. "
            "Pass `layer_id` from `list_layers`. `bbox` is "
            "[min_lon,min_lat,max_lon,max_lat]; `where` is an AND-joined "
            "dict of column = value predicates. Filter at call time "
            "rather than calling `query_layer` first to explore. Optional "
            "`style` overrides the per-peril default colour. Up to 2000 "
            "features. H3-grain layers are not supported in this phase — "
            "use `list_layers` to check `geom_column` first."
        ),
    )
    def _add_layer(
        layer_id: str,
        style: dict[str, Any] | None = None,
        limit: int | None = None,
        bbox: list[float] | None = None,
        where: dict[str, str | int | float | bool] | None = None,
    ) -> dict[str, Any]:
        sql, catalog = _sql_client.get_app_sql()
        bbox_tuple: tuple[float, float, float, float] | None = None
        if bbox is not None:
            if len(bbox) != 4:
                raise ValueError("bbox must be [min_lon, min_lat, max_lon, max_lat]")
            bbox_tuple = (bbox[0], bbox[1], bbox[2], bbox[3])
        try:
            return add_layer_impl(
                sql,
                catalog,
                layer_id,
                style=style,
                limit=limit,
                bbox=bbox_tuple,
                where=where,
            )
        except LayerNotAllowed as e:
            raise ValueError(str(e)) from e

    @server.tool(
        name="remove_layer",
        description="Remove a previously-added layer from the map by `layer_id`.",
    )
    def _remove_layer(layer_id: str) -> dict[str, Any]:
        return remove_layer_impl(layer_id)

    @server.tool(
        name="zoom_to",
        description=(
            "Fly the operational map camera to fit a WKT geometry "
            "(EPSG:4326). Use after `nearest` / `intersect_layer` to centre "
            "the result on screen."
        ),
    )
    def _zoom_to(geom_wkt: str) -> dict[str, Any]:
        sql, _ = _sql_client.get_app_sql()
        return zoom_to_impl(sql, geom_wkt=geom_wkt)

    @server.tool(
        name="style_layer",
        description=(
            "Restyle a previously-added layer. Accepts standard Leaflet "
            "path style fields: `color`, `fill_color`, `fill_opacity`, "
            "`weight`."
        ),
    )
    def _style_layer(
        layer_id: str,
        color: str | None = None,
        fill_color: str | None = None,
        fill_opacity: float | None = None,
        weight: float | None = None,
    ) -> dict[str, Any]:
        return style_layer_impl(
            layer_id,
            color=color,
            fill_color=fill_color,
            fill_opacity=fill_opacity,
            weight=weight,
        )


__all__ = [
    "ADD_LAYER_MAX_ROWS",
    "add_layer_impl",
    "register",
    "remove_layer_impl",
    "style_layer_impl",
    "zoom_to_impl",
]
