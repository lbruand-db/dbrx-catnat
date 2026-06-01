"""Backend test for `GET /api/kepler/portfolio`.

Same dependency-override trick as test_layers_endpoint — we hand the route
a canned StatementResponse and assert on the shape it produces.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState
from fastapi.testclient import TestClient

from catnat_app.backend.app import app
from catnat_app.backend.core.sql import Sql, _SqlDependency


def _stub_sql(rows: list[list[str | int | None]]) -> Sql:
    stub = MagicMock(spec=Sql)
    response = MagicMock()
    response.status.state = StatementState.SUCCEEDED
    response.status.error = None
    response.result.data_array = rows
    stub.execute_statement = MagicMock(return_value=response)
    return stub


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_kepler_portfolio_projects_rows_to_dataset(client: TestClient) -> None:
    rows = [
        # h3 (string), code_dep, n_policies, sum_insured_eur, n_flood, n_rga, n_storm
        ["892ec830003ffff", "069", "42", "12000000", "37", "40", "25"],
        ["892ec830007ffff", "069", "8",  "1500000",  "7",  "8",  "5"],
    ]
    stub = _stub_sql(rows)
    app.dependency_overrides[_SqlDependency.__call__] = lambda: stub
    try:
        resp = client.get("/api/kepler/portfolio")
    finally:
        app.dependency_overrides.pop(_SqlDependency.__call__, None)

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == "portfolio_h3"
    assert "h3" in payload["fields"]
    assert "sum_insured_value_eur" in payload["fields"]
    assert len(payload["rows"]) == 2
    first = payload["rows"][0]
    assert first["h3"] == "892ec830003ffff"
    # Integer coercion: SQL returns strings, model emits ints.
    assert first["n_policies"] == 42
    assert first["sum_insured_value_eur"] == 12_000_000


def test_kepler_portfolio_handles_empty_result(client: TestClient) -> None:
    stub = _stub_sql([])
    app.dependency_overrides[_SqlDependency.__call__] = lambda: stub
    try:
        resp = client.get("/api/kepler/portfolio")
    finally:
        app.dependency_overrides.pop(_SqlDependency.__call__, None)

    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_kepler_portfolio_passes_row_limit_parameter(client: TestClient) -> None:
    """Parameters must be `StatementParameterListItem` instances, not raw
    dicts (the SDK calls `.as_dict()` on each item)."""
    from databricks.sdk.service.sql import StatementParameterListItem

    stub = _stub_sql([])
    app.dependency_overrides[_SqlDependency.__call__] = lambda: stub
    try:
        client.get("/api/kepler/portfolio")
    finally:
        app.dependency_overrides.pop(_SqlDependency.__call__, None)

    params = stub.execute_statement.call_args.kwargs["parameters"]
    for p in params:
        assert isinstance(p, StatementParameterListItem), (
            f"parameter must be StatementParameterListItem, got {type(p).__name__}"
        )
    names = {p.name for p in params}
    assert {"catalog", "row_limit"} <= names
    row_limit = next(p for p in params if p.name == "row_limit")
    assert int(row_limit.value) <= 10_000


def test_kepler_portfolio_calls_h3_h3tostring(client: TestClient) -> None:
    """Kepler's H3 layer needs hex-string cell ids, not BIGINT."""
    stub = _stub_sql([])
    app.dependency_overrides[_SqlDependency.__call__] = lambda: stub
    try:
        client.get("/api/kepler/portfolio")
    finally:
        app.dependency_overrides.pop(_SqlDependency.__call__, None)

    statement = stub.execute_statement.call_args.kwargs["statement"]
    assert "h3_h3tostring" in statement
