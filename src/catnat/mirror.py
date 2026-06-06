"""Mirror displayable silver geometries from UC Delta → Lakebase PostGIS.

Idempotent. Re-runs `DROP TABLE … CASCADE` + `CREATE TABLE` per layer
so an interrupted mirror always leaves a clean state. Suitable for a
daily DAB job (`catnat-job mirror`) or a manual `uv run catnat mirror`
on a laptop.

For v1 we mirror polygon-grain displayable layers only (the same set
`add_layer` can render). H3-grain layers are out of scope here — the
tile endpoint can't `ST_AsMVT` them without first materialising the
H3 cell as a polygon, and that's a separate decision.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import asyncpg
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from . import lakebase
from .config import CONFIG

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayerToMirror:
    layer_id: str
    table_fq: str
    geom_column: str
    peril: str


_LAYER_INDEX_SQL = """
SELECT layer_id, table_fq, peril, geom_column
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
WHERE is_displayable = true AND geom_column IS NOT NULL
ORDER BY layer_id
"""


def _enumerate_layers(ws: WorkspaceClient) -> list[LayerToMirror]:
    """Pull the set of polygon layers we ship to Lakebase."""
    response = ws.statement_execution.execute_statement(
        warehouse_id=CONFIG.warehouse_id,
        statement=_LAYER_INDEX_SQL,
        parameters=[
            StatementParameterListItem(name="catalog", value=CONFIG.catalog, type="STRING")
        ],
        wait_timeout="30s",
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error.message if (response.status and response.status.error) else "?"
        raise RuntimeError(f"layer_index enumeration failed: {err}")
    out: list[LayerToMirror] = []
    for r in (response.result.data_array or []) if response.result else []:
        out.append(LayerToMirror(layer_id=r[0], table_fq=r[1], peril=r[2], geom_column=r[3]))
    return out


# Bbox-scope a layer at read time when its full size would overflow
# the Statement Execution API's 26 MB inline-result cap.
# `hazard_rga_susceptibility` is national (~50K polygons, ~1 GB
# of WKT) and is the only one over the limit today. The bbox covers
# Rhône (dept 069 — SPEC §4.4 v1 scope) plus the four neighbouring
# depts whose communes overlap dbtopo-bricks (01 Ain, 38 Isère,
# 42 Loire, 71 Saône-et-Loire).
#
# Bumping this set unconditionally is wrong: a too-tight bbox dropped
# TRI's Mediterranean footprints (lat ~43) and 75% of PPRI's Ain rows
# (lat ~46) when applied across the board. Layers that already fit
# stay unbounded.
#
# The proper long-term fix is `disposition=EXTERNAL_LINKS` (paginated
# chunks instead of inline), tracked as a P6 polish item.
LAYERS_NEEDING_BBOX_SCOPE: frozenset[str] = frozenset({"hazard_rga_susceptibility"})
MIRROR_BBOX_WKT = "POLYGON((3.9 45.2, 5.5 45.2, 5.5 46.6, 3.9 46.6, 3.9 45.2))"


def _read_layer_rows(
    ws: WorkspaceClient, layer: LayerToMirror
) -> tuple[list[str], list[list[object]]]:
    """SELECT every row of the layer, with geometry projected to WKT.

    For v1 we load each layer fully into memory before pushing. The
    Statement Execution API splits results across chunks (~256 rows
    each for polygon layers); we have to chase `next_chunk_index` to
    get the full layer. Skipping that step is how 242 of 496 communes
    silently went missing on the first mirror run.
    """
    params: list[StatementParameterListItem] = [
        StatementParameterListItem(name="table_fq", value=layer.table_fq, type="STRING"),
    ]
    where_clause = ""
    if layer.layer_id in LAYERS_NEEDING_BBOX_SCOPE:
        where_clause = f"WHERE ST_Intersects({layer.geom_column}, ST_GeomFromText(:bbox_wkt, 4326))"
        params.append(
            StatementParameterListItem(name="bbox_wkt", value=MIRROR_BBOX_WKT, type="STRING")
        )
    sql = (
        f"SELECT ST_AsText({layer.geom_column}) AS geom_wkt, "
        f"* EXCEPT ({layer.geom_column}) FROM IDENTIFIER(:table_fq) {where_clause}"
    ).strip()
    response = ws.statement_execution.execute_statement(
        warehouse_id=CONFIG.warehouse_id,
        statement=sql,
        parameters=params,
        wait_timeout="50s",
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error.message if (response.status and response.status.error) else "?"
        raise RuntimeError(f"read {layer.layer_id} failed: {err}")

    columns: list[str] = []
    if response.manifest and response.manifest.schema and response.manifest.schema.columns:
        columns = [c.name for c in response.manifest.schema.columns]

    rows: list[list[object]] = list(response.result.data_array or []) if response.result else []
    next_idx = response.result.next_chunk_index if response.result else None
    statement_id = response.statement_id
    while next_idx is not None and statement_id is not None:
        chunk = ws.statement_execution.get_statement_result_chunk_n(
            statement_id=statement_id, chunk_index=next_idx
        )
        rows.extend(chunk.data_array or [])
        next_idx = chunk.next_chunk_index

    # Fail loud on partial reads. The chunk-pagination bug that lost
    # 242/496 communes (commit d9b942a) was internally consistent —
    # the FE rendered what it got, the agent said "237 features", no
    # error anywhere. Asserting the row count against the manifest
    # turns a silent geographic-data hole into a hard mirror failure.
    expected = response.manifest.total_row_count if response.manifest else None
    if expected is not None and len(rows) != expected:
        raise RuntimeError(
            f"partial read of {layer.layer_id}: got {len(rows)} rows, "
            f"manifest says {expected}. Likely a chunk-pagination drop — "
            f"do NOT push to Lakebase or the demo map will silently lose "
            f"features."
        )
    return columns, rows


def _quote_pg_ident(name: str) -> str:
    """Quote an identifier for Postgres. Refuses anything outside [A-Za-z0-9_]."""
    safe = "".join(c for c in name if c.isalnum() or c == "_")
    if safe != name or not safe:
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{safe}"'


async def _mirror_one_layer(
    conn: asyncpg.Connection,
    layer: LayerToMirror,
    columns: list[str],
    rows: list[list[object]],
) -> int:
    """Create `geo.<layer_id>` with the layer's attributes + geometry.

    Geometry is stored as PostGIS `GEOMETRY(Geometry, 4326)`. A GIST
    index is created so `ST_AsMVTGeom` / `ST_Intersects` scans are
    fast. Returns the number of rows inserted (non-null geometry only).
    """
    table = _quote_pg_ident(layer.layer_id)
    schema = _quote_pg_ident(lakebase.LAKEBASE_SCHEMA)

    # Column 0 is `geom_wkt`; columns[1:] are the attribute columns.
    if not columns or columns[0] != "geom_wkt":
        raise RuntimeError(f"unexpected manifest for {layer.layer_id}: cols={columns!r}")
    attr_cols = columns[1:]
    quoted_attrs = [_quote_pg_ident(c) for c in attr_cols]

    # Attribute types: everything's TEXT in v1 — we don't need
    # tightly-typed columns to render tiles, and PostGIS doesn't care.
    # Tile rendering uses these as MVT feature properties (strings).
    col_decls = ", ".join(f"{c} TEXT" for c in quoted_attrs)
    col_decls = "geom GEOMETRY(Geometry, 4326)" + (", " + col_decls if col_decls else "")

    drop_sql = f"DROP TABLE IF EXISTS {schema}.{table} CASCADE"
    create_sql = f"CREATE TABLE {schema}.{table} ({col_decls})"
    index_sql = (
        f"CREATE INDEX {_quote_pg_ident(layer.layer_id + '_geom_idx')} "
        f"ON {schema}.{table} USING GIST (geom)"
    )

    await conn.execute(drop_sql)
    await conn.execute(create_sql)

    # Build an INSERT for each row. asyncpg `executemany` handles
    # parameter binding efficiently; geometry comes in as WKT and is
    # promoted via ST_GeomFromText.
    insert_sql = (
        f"INSERT INTO {schema}.{table} (geom, "
        + ", ".join(quoted_attrs)
        + ") VALUES (ST_GeomFromText($1, 4326)"
        + "".join(f", ${i + 2}" for i in range(len(attr_cols)))
        + ")"
    )

    inserted = 0
    batch: list[tuple] = []
    BATCH = 500
    for r in rows:
        geom_wkt = r[0]
        if geom_wkt is None or not isinstance(geom_wkt, str):
            continue
        attrs = tuple(None if r[i] is None else str(r[i]) for i in range(1, len(r)))
        batch.append((geom_wkt, *attrs))
        if len(batch) >= BATCH:
            await conn.executemany(insert_sql, batch)
            inserted += len(batch)
            batch = []
    if batch:
        await conn.executemany(insert_sql, batch)
        inserted += len(batch)

    await conn.execute(index_sql)
    return inserted


async def _run_async(layers_filter: set[str] | None = None) -> dict[str, int]:
    """Async core. Returns {layer_id: rows_inserted}."""
    ws = WorkspaceClient(profile=CONFIG.profile)
    layers = _enumerate_layers(ws)
    if layers_filter is not None:
        layers = [layer for layer in layers if layer.layer_id in layers_filter]

    logger.info("Mirroring %d layer(s) → Lakebase %s", len(layers), lakebase.LAKEBASE_PROJECT)
    summary: dict[str, int] = {}
    conn = await lakebase.connect(ws)
    try:
        for layer in layers:
            logger.info("- %s", layer.layer_id)
            columns, rows = _read_layer_rows(ws, layer)
            n = await _mirror_one_layer(conn, layer, columns, rows)
            summary[layer.layer_id] = n
            logger.info("  rows=%d", n)
    finally:
        await conn.close()
    return summary


def run(layers: list[str] | None = None) -> dict[str, int]:
    """Sync entrypoint used by the CLI and the DAB job."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(_run_async(layers_filter=set(layers) if layers else None))


__all__ = ["run"]
