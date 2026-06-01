"""Backend test for `GET /api/layers`.

We override the `Sql` dependency to return a canned StatementResponse — the
real warehouse round-trip is exercised by the local CLI and the
SPECS/BENCHMARKS.md suite, not here. This test isolates the route's
response-shaping logic (row-to-Pydantic projection + boolean coercion).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from databricks.sdk.service.sql import StatementState

from catnat_app.backend.app import app
from catnat_app.backend.core.sql import Sql, _SqlDependency


def _make_sql_stub(rows: list[list[str | bool | None]]) -> Sql:
    """Build a Sql stub whose `execute_statement` returns a canned response."""
    stub = MagicMock(spec=Sql)
    response = MagicMock()
    response.status.state = StatementState.SUCCEEDED
    response.status.error = None
    response.result.data_array = rows
    stub.execute_statement = MagicMock(return_value=response)
    return stub


def _override_sql(stub: Sql):
    """Override the chained Sql dependency with a constant stub.

    Bypasses the full `_SqlDependency.__call__(request, user_ws)` chain so
    the test doesn't need a real OBO token or workspace client.
    """
    app.dependency_overrides[_SqlDependency.__call__] = lambda: stub


def _clear_overrides() -> None:
    app.dependency_overrides.pop(_SqlDependency.__call__, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_layers_endpoint_projects_rows_to_layer_models(client: TestClient) -> None:
    rows = [
        [
            "hazard_rga_h3", "cat.catnat_gold.hazard_rga_h3", "drought", "gold",
            "h3_r9_cell", "h3", None, "Etalab 2.0", True, "RGA H3 r=9 cells",
        ],
        [
            "hazard_ppri_communes", "cat.catnat_silver.hazard_ppri_communes", "flood",
            "silver", "polygon", None, "geometry", "Etalab 2.0", False,
            "PPRI commune footprints (silver, polygon).",
        ],
    ]
    stub = _make_sql_stub(rows)
    _override_sql(stub)
    try:
        resp = client.get("/api/layers")
    finally:
        _clear_overrides()

    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["layers"]) == 2
    first = payload["layers"][0]
    assert first["layer_id"] == "hazard_rga_h3"
    assert first["peril"] == "drought"
    assert first["is_displayable"] is True
    second = payload["layers"][1]
    assert second["is_displayable"] is False
    assert second["geom_column"] == "geometry"


def test_layers_endpoint_handles_empty_result(client: TestClient) -> None:
    stub = _make_sql_stub([])
    _override_sql(stub)
    try:
        resp = client.get("/api/layers")
    finally:
        _clear_overrides()

    assert resp.status_code == 200
    assert resp.json() == {"layers": []}


def test_layers_endpoint_passes_catalog_via_parameter_marker(client: TestClient) -> None:
    """The route should never inline catalog — must use parameter markers,
    *as `StatementParameterListItem` instances* (raw dicts crash inside
    `databricks.sdk.service.sql.execute_statement` with `'dict' object has
    no attribute 'as_dict'`)."""
    from databricks.sdk.service.sql import StatementParameterListItem

    stub = _make_sql_stub([])
    _override_sql(stub)
    try:
        client.get("/api/layers")
    finally:
        _clear_overrides()

    params = stub.execute_statement.call_args.kwargs["parameters"]
    assert params, "expected at least one parameter"
    for p in params:
        assert isinstance(p, StatementParameterListItem), (
            f"parameter must be StatementParameterListItem, got {type(p).__name__}"
        )
    assert any(p.name == "catalog" for p in params), (
        "catalog must be passed as a parameter, not concatenated"
    )
