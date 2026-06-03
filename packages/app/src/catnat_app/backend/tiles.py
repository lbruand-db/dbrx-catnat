"""Vector tile endpoint — `/api/tiles/<layer>/{z}/{x}/{y}.pbf`.

Serves Mapbox Vector Tiles (MVT/protobuf) from the Lakebase PostGIS
mirror of `catnat_silver.*`. Pattern mirrors the
[lakebase-vector-tile](https://github.com/danny-db/lakebase-vector-tile)
PoC (SPEC §10.7):

  SELECT ST_AsMVT(q, <layer>, 4096, 'geom') FROM (
    SELECT ST_AsMVTGeom(ST_Transform(geom, 3857), ST_TileEnvelope(z,x,y),
                        4096, 256, true) AS geom, <attrs>
    FROM geo.<layer>
    WHERE ST_Intersects(ST_Transform(geom, 3857), ST_TileEnvelope(z,x,y))
  ) q

Allowlist: the requested `<layer>` must (a) be a key in
`catnat_silver.layer_index` with `is_displayable=true`, and (b) have a
corresponding `geo.<layer>` table in Lakebase. Anything else → 404.

For v1 we don't pool — `asyncpg.connect` per request. Credentials are
short-lived (~1h) so refreshing per request is the simplest correct
path. A real pool with a per-connection token refresh is a P6 polish
item.

Cache: an in-process LRU keyed by `(layer, z, x, y)` with a 5-minute
TTL. Adequate for demo traffic; nothing fancy.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import asyncpg
from databricks.sdk import WorkspaceClient
from fastapi import APIRouter, HTTPException, Response

from .app_sql import AppSqlDependency
from .mcp.allowlist import LayerNotAllowed, get_allowed_layer

logger = logging.getLogger(__name__)


# Constants matching `catnat.lakebase` so the app doesn't accidentally
# drift from the mirror-side configuration.
LAKEBASE_PROJECT = "catnat-tiles"
LAKEBASE_BRANCH = "production"
LAKEBASE_ENDPOINT_NAME = "primary"
LAKEBASE_DATABASE = "databricks_postgres"
LAKEBASE_SCHEMA = "geo"


def _endpoint_resource() -> str:
    return (
        f"projects/{LAKEBASE_PROJECT}/branches/{LAKEBASE_BRANCH}/endpoints/{LAKEBASE_ENDPOINT_NAME}"
    )


# --- In-process tile cache --------------------------------------------


_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ENTRIES = 2000
_tile_cache: dict[tuple[str, int, int, int], tuple[bytes, float]] = {}


def _cache_get(key: tuple[str, int, int, int]) -> bytes | None:
    entry = _tile_cache.get(key)
    if entry is None:
        return None
    body, ts = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _tile_cache.pop(key, None)
        return None
    return body


def _cache_put(key: tuple[str, int, int, int], body: bytes) -> None:
    if len(_tile_cache) >= _CACHE_MAX_ENTRIES:
        # Drop the oldest half to amortise the eviction cost.
        sorted_keys = sorted(_tile_cache.keys(), key=lambda k: _tile_cache[k][1])
        for k in sorted_keys[: len(sorted_keys) // 2]:
            _tile_cache.pop(k, None)
    _tile_cache[key] = (body, time.monotonic())


# --- Lakebase connection ---------------------------------------------


def _safe_layer_ident(layer_id: str) -> str:
    """Validate `layer_id` for safe embedding in Postgres SQL."""
    if not layer_id or not all(c.isalnum() or c == "_" for c in layer_id):
        raise HTTPException(status_code=400, detail=f"unsafe layer_id: {layer_id!r}")
    return layer_id


async def _open_lakebase_conn() -> asyncpg.Connection:
    """Open a fresh asyncpg connection with an SDK-resolved credential.

    The app SP runs this. The Postgres role is the SP's `application_id`
    (LAKEBASE_OAUTH_V1 mapping); the token is a short-lived JWT.
    """
    ws = WorkspaceClient()
    me = ws.current_user.me()
    user = me.user_name or str(getattr(me, "application_id", None) or me.id or "")
    endpoint = ws.postgres.get_endpoint(name=_endpoint_resource())
    host = endpoint.status.hosts.host  # type: ignore[union-attr]
    cred = ws.postgres.generate_database_credential(endpoint=_endpoint_resource())
    return await asyncpg.connect(
        host=host,
        port=5432,
        user=user,
        password=cred.token,  # type: ignore[union-attr]
        database=LAKEBASE_DATABASE,
        ssl="require",
    )


# --- Route -----------------------------------------------------------


router = APIRouter(prefix="/api")


@router.get("/tiles/metadata", operation_id="tilesMetadata")
async def tiles_metadata(sql: AppSqlDependency) -> dict[str, Any]:
    """Return the set of layers served via the tile endpoint.

    Practical use: the FE could call this once and decide per-layer
    whether to wire `L.vectorGrid.protobuf` or fall back to GeoJSON.
    For v1 we only expose polygon layers since that's what the mirror
    actually pushes (§4.5).
    """
    # `_get_app_sql` from app_sql.py is the wrapper around the SQL
    # warehouse; we use it to query `layer_index`.
    import os

    from databricks.sdk.service.sql import StatementParameterListItem, StatementState

    catalog = os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")
    response = sql.execute_statement(
        statement=(
            "SELECT layer_id, peril FROM IDENTIFIER(:catalog || "
            "'.catnat_silver.layer_index') "
            "WHERE is_displayable = true AND geom_column IS NOT NULL "
            "ORDER BY layer_id"
        ),
        wait_timeout="30s",
        parameters=[StatementParameterListItem(name="catalog", value=catalog, type="STRING")],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error.message if (response.status and response.status.error) else "?"
        raise HTTPException(status_code=500, detail=f"layer_index lookup failed: {err}")
    layers = [
        {"layer_id": r[0], "peril": r[1], "tile_url": f"/api/tiles/{r[0]}/{{z}}/{{x}}/{{y}}.pbf"}
        for r in (response.result.data_array or [])
        if response.result
    ]
    return {"layers": layers}


@router.get("/tiles/{layer}/{z}/{x}/{y}.pbf", operation_id="tile")
async def get_tile(layer: str, z: int, x: int, y: int, sql: AppSqlDependency) -> Response:
    """Serve a single MVT tile from the Lakebase mirror.

    Allowlist + cache + ST_AsMVT. Returns `application/x-protobuf`.
    """
    safe = _safe_layer_ident(layer)

    # Validate against the same allowlist the MCP tools use — never
    # serve a tile for a layer not in `layer_index` or outside the
    # `catnat_silver`/`catnat_gold` schemas.
    import os

    catalog = os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")
    try:
        get_allowed_layer(sql, catalog, safe)
    except LayerNotAllowed as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    if z < 0 or z > 22:
        raise HTTPException(status_code=400, detail="zoom out of range")
    max_tile = 2**z
    if x < 0 or x >= max_tile or y < 0 or y >= max_tile:
        raise HTTPException(status_code=400, detail="tile coordinates out of range")

    cache_key = (safe, z, x, y)
    cached = _cache_get(cache_key)
    if cached is not None:
        return Response(
            content=cached,
            media_type="application/x-protobuf",
            headers={"Cache-Control": "public, max-age=300", "X-Cache": "hit"},
        )

    # ST_AsMVT against the Lakebase mirror. The geometry column is
    # always named `geom` (the mirror's convention from
    # `catnat.mirror._mirror_one_layer`).
    sql_text = f"""
        SELECT ST_AsMVT(q, $1, 4096, 'geom') FROM (
            SELECT
                ST_AsMVTGeom(
                    ST_Transform(geom, 3857),
                    ST_TileEnvelope($2::integer, $3::integer, $4::integer),
                    4096, 256, true
                ) AS geom,
                t.*
            FROM "{LAKEBASE_SCHEMA}"."{safe}" t
            WHERE ST_Intersects(
                ST_Transform(geom, 3857),
                ST_TileEnvelope($2::integer, $3::integer, $4::integer)
            )
        ) q
    """

    conn = await _open_lakebase_conn()
    try:
        body = await conn.fetchval(sql_text, safe, z, x, y)
    except asyncpg.UndefinedTableError as e:
        # The layer is in the allowlist but not (yet) mirrored.
        raise HTTPException(
            status_code=404,
            detail=f"layer '{safe}' is allowlisted but not mirrored to Lakebase yet",
        ) from e
    finally:
        await conn.close()

    if body is None:
        body = b""
    _cache_put(cache_key, bytes(body))
    return Response(
        content=bytes(body),
        media_type="application/x-protobuf",
        headers={"Cache-Control": "public, max-age=300", "X-Cache": "miss"},
    )


__all__ = ["router"]
