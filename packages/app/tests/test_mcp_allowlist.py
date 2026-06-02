"""Allowlist guard tests for MCP tools.

Mocks the `Sql` handle directly — the rules are pure data: which rows
exist in `layer_index`, what `is_displayable` says, and whether the
`table_fq` schema is in the allowlist.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp.allowlist import (
    AllowedLayer,
    LayerNotAllowed,
    get_allowed_layer,
)
from databricks.sdk.service.sql import StatementState


def _sql_returning(rows: list[list[object]]) -> Sql:
    stub = MagicMock(spec=Sql)
    response = MagicMock()
    response.status.state = StatementState.SUCCEEDED
    response.status.error = None
    response.result.data_array = rows
    stub.execute_statement = MagicMock(return_value=response)
    return stub


def test_returns_metadata_for_displayable_layer() -> None:
    sql = _sql_returning(
        [
            [
                "hazard_rga_h3",
                "cat.catnat_gold.hazard_rga_h3",
                "drought",
                "gold",
                "h3_r9_cell",
                "h3",
                None,
                True,
            ]
        ]
    )
    layer = get_allowed_layer(sql, "cat", "hazard_rga_h3")
    assert isinstance(layer, AllowedLayer)
    assert layer.schema == "catnat_gold"
    assert layer.peril == "drought"
    assert layer.h3_column == "h3"
    assert layer.geom_column is None


def test_refuses_unknown_layer() -> None:
    sql = _sql_returning([])
    with pytest.raises(LayerNotAllowed, match="unknown layer"):
        get_allowed_layer(sql, "cat", "nope")


def test_refuses_non_displayable_layer() -> None:
    sql = _sql_returning(
        [
            [
                "raw_internal",
                "cat.catnat_silver.raw_internal",
                "internal",
                "silver",
                "polygon",
                None,
                "geometry",
                False,
            ]
        ]
    )
    with pytest.raises(LayerNotAllowed, match="not displayable"):
        get_allowed_layer(sql, "cat", "raw_internal")


def test_refuses_layer_outside_allowed_schemas() -> None:
    sql = _sql_returning(
        [
            [
                "shady_layer",
                "cat.catnat_bronze.raw_dump",
                "flood",
                "bronze",
                "polygon",
                None,
                "geometry",
                True,
            ]
        ]
    )
    with pytest.raises(LayerNotAllowed, match="not in the allowlist"):
        get_allowed_layer(sql, "cat", "shady_layer")


def test_refuses_malformed_table_fq() -> None:
    sql = _sql_returning(
        [
            [
                "bad_fq_layer",
                "missing_catalog_dot",
                "drought",
                "gold",
                "h3",
                "h3",
                None,
                True,
            ]
        ]
    )
    with pytest.raises(LayerNotAllowed, match="table_fq must be"):
        get_allowed_layer(sql, "cat", "bad_fq_layer")
