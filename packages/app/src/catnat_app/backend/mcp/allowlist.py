"""Layer allowlist for MCP tools (SPEC §5.4 "Design rules").

Every tool that touches a UC table goes through `get_allowed_layer` first.
The function:

1. Looks up the layer by id in `catnat_silver.layer_index`.
2. Refuses if `is_displayable = false` — the layer registry is the single
   source of truth for what the agent may see.
3. Refuses if the layer's `table_fq` points outside the allowed schemas
   (`catnat_silver` or `catnat_gold`). Belt-and-braces against a future
   bad row in `layer_index`.

Tests pin the failure modes by mocking the `Sql` handle — no warehouse
round-trip needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from ..core.sql import Sql

ALLOWED_SCHEMAS: frozenset[str] = frozenset({"catnat_silver", "catnat_gold"})


class LayerNotAllowed(Exception):
    """Raised when an MCP tool tries to use a layer that isn't on the allowlist."""


@dataclass(frozen=True)
class AllowedLayer:
    """The fields a spatial tool needs to build a query against a layer."""

    layer_id: str
    table_fq: str
    schema: str
    peril: str
    medallion: str
    grain: str
    geom_column: str | None
    h3_column: str | None


_LOOKUP_SQL = """
SELECT
    layer_id, table_fq, peril, medallion, grain,
    h3_column, geom_column, is_displayable
FROM IDENTIFIER(:catalog || '.catnat_silver.layer_index')
WHERE layer_id = :layer_id
"""


def get_allowed_layer(sql: Sql, catalog: str, layer_id: str) -> AllowedLayer:
    """Return the metadata for `layer_id` or raise `LayerNotAllowed`."""
    response = sql.execute_statement(
        statement=_LOOKUP_SQL,
        wait_timeout="30s",
        parameters=[
            StatementParameterListItem(name="catalog", value=catalog, type="STRING"),
            StatementParameterListItem(name="layer_id", value=layer_id, type="STRING"),
        ],
    )
    if not response.status or response.status.state != StatementState.SUCCEEDED:
        err = response.status.error if response.status else None
        msg = err.message if err else "unknown"
        raise RuntimeError(f"layer_index lookup failed: {msg}")
    rows = response.result.data_array if response.result else []
    if not rows:
        raise LayerNotAllowed(f"unknown layer: {layer_id}")

    r = rows[0]
    is_displayable = r[7] == "true" if isinstance(r[7], str) else bool(r[7])
    if not is_displayable:
        raise LayerNotAllowed(f"layer is not displayable: {layer_id}")

    table_fq = r[1]
    schema = _schema_of(table_fq)
    if schema not in ALLOWED_SCHEMAS:
        raise LayerNotAllowed(
            f"layer {layer_id} points at schema '{schema}' which is not in "
            f"the allowlist {sorted(ALLOWED_SCHEMAS)}"
        )

    return AllowedLayer(
        layer_id=r[0],
        table_fq=table_fq,
        schema=schema,
        peril=r[2],
        medallion=r[3],
        grain=r[4],
        h3_column=r[5],
        geom_column=r[6],
    )


def _schema_of(table_fq: str) -> str:
    """Return the schema part of a `<catalog>.<schema>.<table>` FQN."""
    parts = table_fq.split(".")
    if len(parts) != 3:
        raise LayerNotAllowed(f"table_fq must be `<catalog>.<schema>.<table>`, got: {table_fq!r}")
    return parts[1]


__all__ = ["ALLOWED_SCHEMAS", "AllowedLayer", "LayerNotAllowed", "get_allowed_layer"]
