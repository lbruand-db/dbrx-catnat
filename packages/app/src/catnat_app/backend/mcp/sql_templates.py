"""SQL-fragment builders used by MCP tools.

Kept here (not inlined in `tools.py`) so we can unit-test the SQL shape
without spinning up FastMCP. Every builder returns a `(statement,
parameters)` pair using parameter markers — geometry/WHERE values flow
exclusively as bind parameters, never as f-string interpolations.

The MCP spec caps a tool's inline return at ~1MB (SPEC §5.4). For v1 we
enforce that with a hard 500-row LIMIT and column projections that drop
binary geometry blobs (`ST_AsGeoJSON` for polygons, `h3_h3tostring` for
H3). Spilling overflow results to session-scoped UC tables is tracked
as a P6 polish item.
"""

from __future__ import annotations

from dataclasses import dataclass

from databricks.sdk.service.sql import StatementParameterListItem

from .allowlist import AllowedLayer

# Hard cap on rows any single query_layer call may surface to the agent.
QUERY_LAYER_MAX_ROWS = 500


@dataclass(frozen=True)
class BuiltStatement:
    """A (parameterised) SQL statement ready for `execute_statement`."""

    statement: str
    parameters: list[StatementParameterListItem]


def _safe_identifier(name: str) -> str:
    """Validate an identifier for embedding in SQL (column names, etc.).

    Databricks supports backtick-quoted identifiers but we never want a
    user-supplied string to drive structural SQL. Allow only word
    characters (`[A-Za-z0-9_]+`) and refuse otherwise.
    """
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _bbox_polygon_wkt(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    """Render a bbox as a closed WKT POLYGON."""
    return (
        f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, "
        f"{max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
    )


def _projection(layer: AllowedLayer) -> str:
    """Build the SELECT projection clause for the layer.

    Strips heavy binary columns:
    - `geom_column` → `ST_AsGeoJSON(col) AS <col>_geojson`
    - `h3_column`  → `h3_h3tostring(col) AS <col>_hex`

    Keeps every other column with `* EXCEPT (heavy_col)`.
    """
    if layer.geom_column:
        col = _safe_identifier(layer.geom_column)
        return f"ST_AsGeoJSON({col}) AS {col}_geojson, * EXCEPT ({col})"
    if layer.h3_column:
        col = _safe_identifier(layer.h3_column)
        return f"h3_h3tostring({col}) AS {col}_hex, * EXCEPT ({col})"
    return "*"


def build_query_layer(
    layer: AllowedLayer,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    where: dict[str, str | int | float | bool] | None = None,
    limit: int = 100,
) -> BuiltStatement:
    """Build the SQL for `query_layer(layer_id, bbox, where, limit)`.

    - `bbox` is `(min_lon, min_lat, max_lon, max_lat)`. Only supported on
      layers carrying a `geom_column`; H3-grain layers reject with
      `ValueError` (use `intersect_layer` for those once bbox→H3
      polyfill is wired).
    - `where` is `{column: value}` AND-joined equality predicates. Column
      names go through `_safe_identifier`; values bind as parameters
      typed by Python type.
    - `limit` is clamped to `[1, QUERY_LAYER_MAX_ROWS]`.
    """
    conditions: list[str] = []
    params: list[StatementParameterListItem] = [
        StatementParameterListItem(name="table_fq", value=layer.table_fq, type="STRING"),
    ]

    if bbox is not None:
        if not layer.geom_column:
            raise ValueError(
                f"bbox filter not supported for layer '{layer.layer_id}' "
                "(no geom_column). Try a polygon-grain layer or omit bbox."
            )
        poly_wkt = _bbox_polygon_wkt(*bbox)
        col = _safe_identifier(layer.geom_column)
        conditions.append(f"ST_Intersects({col}, ST_GeomFromText(:bbox_wkt, 4326))")
        params.append(StatementParameterListItem(name="bbox_wkt", value=poly_wkt, type="STRING"))

    if where:
        for col_name, val in where.items():
            col = _safe_identifier(col_name)
            pname = f"w_{col}"
            conditions.append(f"`{col}` = :{pname}")
            if isinstance(val, bool):
                ptype, pval = "BOOLEAN", "true" if val else "false"
            elif isinstance(val, int):
                ptype, pval = "INT", str(val)
            elif isinstance(val, float):
                ptype, pval = "DOUBLE", str(val)
            else:
                ptype, pval = "STRING", str(val)
            params.append(StatementParameterListItem(name=pname, value=pval, type=ptype))

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    capped = max(1, min(limit, QUERY_LAYER_MAX_ROWS))
    statement = (
        f"SELECT {_projection(layer)} FROM IDENTIFIER(:table_fq) {where_sql} LIMIT {capped}"
    ).strip()
    return BuiltStatement(statement=statement, parameters=params)


def build_intersect_layer(
    layer: AllowedLayer,
    *,
    geom_wkt: str,
    limit: int = 100,
) -> BuiltStatement:
    """Build SQL for `intersect_layer(layer_id, geom_wkt)`.

    Returns rows from the layer that spatially intersect the supplied
    geometry. Requires the layer to expose a `geom_column` — H3 layers
    are out of scope for v1 (would need polyfill of the input geom).
    """
    if not layer.geom_column:
        raise ValueError(
            f"intersect_layer requires a geom_column; layer '{layer.layer_id}' has none"
        )
    col = _safe_identifier(layer.geom_column)
    capped = max(1, min(limit, QUERY_LAYER_MAX_ROWS))
    statement = (
        f"SELECT {_projection(layer)} "
        f"FROM IDENTIFIER(:table_fq) "
        f"WHERE ST_Intersects({col}, ST_GeomFromText(:geom_wkt, 4326)) "
        f"LIMIT {capped}"
    )
    params = [
        StatementParameterListItem(name="table_fq", value=layer.table_fq, type="STRING"),
        StatementParameterListItem(name="geom_wkt", value=geom_wkt, type="STRING"),
    ]
    return BuiltStatement(statement=statement, parameters=params)


def build_nearest(
    layer: AllowedLayer,
    *,
    point_wkt: str,
    k: int = 5,
) -> BuiltStatement:
    """Build SQL for `nearest(point_wkt, layer_id, k)`.

    k-NN by `ST_Distance` against the layer's geom_column. Like
    `intersect_layer`, requires a geom_column.
    """
    if not layer.geom_column:
        raise ValueError(f"nearest requires a geom_column; layer '{layer.layer_id}' has none")
    col = _safe_identifier(layer.geom_column)
    capped = max(1, min(k, 100))
    statement = (
        f"SELECT {_projection(layer)}, "
        f"ST_Distance({col}, ST_GeomFromText(:point_wkt, 4326)) AS distance_deg "
        f"FROM IDENTIFIER(:table_fq) "
        f"ORDER BY distance_deg ASC "
        f"LIMIT {capped}"
    )
    params = [
        StatementParameterListItem(name="table_fq", value=layer.table_fq, type="STRING"),
        StatementParameterListItem(name="point_wkt", value=point_wkt, type="STRING"),
    ]
    return BuiltStatement(statement=statement, parameters=params)


def build_buffer(*, geom_wkt: str, meters: float) -> BuiltStatement:
    """Build SQL for `buffer(geom_wkt, meters)`.

    Wraps `ST_Buffer` on a literal geometry. The result is returned as
    WKT so the agent can pass it to subsequent tool calls (intersect_layer,
    nearest, etc.) without round-tripping through binary encodings.

    Note: `ST_Buffer` on EPSG:4326 with a meter distance requires a
    metric SRID conversion under the hood; for the demo we approximate
    via the geometry-degree distance equivalent at French latitudes
    (1° ≈ 111_111 m at the equator; at lat 46° ≈ 77_000 m). This is
    accurate enough for the visual buffers the demo uses.
    """
    # Approximate degree-equivalent of `meters` at French latitudes.
    # SPEC §4.3 — H3 r=9 cells are ~150m wide; we want buffers in the
    # same ballpark to feel right. Reasonable approximation across the
    # demo's geographic footprint.
    degrees = meters / 85_000.0
    statement = (
        "SELECT ST_AsText(ST_Buffer(ST_GeomFromText(:geom_wkt, 4326), :degrees)) AS buffered_wkt"
    )
    params = [
        StatementParameterListItem(name="geom_wkt", value=geom_wkt, type="STRING"),
        StatementParameterListItem(name="degrees", value=str(degrees), type="DOUBLE"),
    ]
    return BuiltStatement(statement=statement, parameters=params)


__all__ = [
    "BuiltStatement",
    "QUERY_LAYER_MAX_ROWS",
    "build_buffer",
    "build_intersect_layer",
    "build_nearest",
    "build_query_layer",
]
