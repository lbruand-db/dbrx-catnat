"""Tests for `/api/tiles/<layer>/{z}/{x}/{y}.pbf`.

The Lakebase connection is mocked — these tests verify the route's
allowlist, cache, parameter validation, and response shape. The
actual `ST_AsMVT` query is exercised end-to-end against the live
Lakebase by `scripts/probe_agent.py` and the deploy smoke.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from catnat_app.backend import tiles as tiles_mod
from catnat_app.backend.app import app
from catnat_app.backend.app_sql import _get_app_sql
from catnat_app.backend.core.sql import Sql
from catnat_app.backend.mcp.allowlist import AllowedLayer, LayerNotAllowed
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _allowed(layer_id: str = "hazard_ppri_communes") -> AllowedLayer:
    return AllowedLayer(
        layer_id=layer_id,
        table_fq=f"cat.catnat_silver.{layer_id}",
        schema="catnat_silver",
        peril="flood",
        medallion="silver",
        grain="polygon",
        geom_column="geometry",
        h3_column=None,
    )


def _override_allowlist(monkeypatch: pytest.MonkeyPatch, result):
    """Patch `get_allowed_layer` in the tiles module. If `result` is an
    exception, it's raised; otherwise it's returned."""

    def fake(_sql, _catalog, layer_id):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(tiles_mod, "get_allowed_layer", fake)


def _override_sql_stub(stub: Sql) -> None:
    """Stub the SQL dependency so the route doesn't reach the warehouse."""
    app.dependency_overrides[_get_app_sql] = lambda: stub


def _make_sql_stub() -> Sql:
    stub = MagicMock(spec=Sql)
    stub.execute_statement = MagicMock()
    return stub


def _override_conn(monkeypatch: pytest.MonkeyPatch, fetchval_return: bytes) -> AsyncMock:
    """Patch `_open_lakebase_conn` to return a fake asyncpg connection
    whose `fetchval` returns `fetchval_return`."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.close = AsyncMock()

    async def fake_open():
        return conn

    monkeypatch.setattr(tiles_mod, "_open_lakebase_conn", fake_open)
    return conn


def _clear_cache():
    tiles_mod._tile_cache.clear()


# --- Allowlist behaviour ---------------------------------------------


def test_tile_404s_for_unknown_layer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_cache()
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, LayerNotAllowed("unknown layer: nope"))
    try:
        resp = client.get("/api/tiles/nope/10/520/375.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 404
    assert "unknown layer" in resp.json()["detail"]


def test_tile_400s_for_invalid_tile_coords(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cache()
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, _allowed())
    try:
        # x out of range for z=10 (max_tile = 1024)
        resp = client.get("/api/tiles/hazard_ppri_communes/10/9999/375.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 400


def test_tile_400s_for_zoom_out_of_range(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cache()
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, _allowed())
    try:
        resp = client.get("/api/tiles/hazard_ppri_communes/99/0/0.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 400


def test_tile_rejects_unsafe_layer_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_cache()
    _override_sql_stub(_make_sql_stub())
    # The allowlist is bypassed here because path-level validation
    # catches the bad ident first; still set the override so the SQL
    # dependency resolves.
    _override_allowlist(monkeypatch, _allowed())
    try:
        resp = client.get("/api/tiles/hazard%3B%20drop/10/520/375.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 400


# --- Happy path: protobuf body + cache --------------------------------


def test_tile_returns_protobuf_body(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_cache()
    tile_bytes = b"\x1a\x2b\x3c\x4d\x5e"
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, _allowed())
    _override_conn(monkeypatch, tile_bytes)
    try:
        resp = client.get("/api/tiles/hazard_ppri_communes/10/520/375.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 200
    assert resp.content == tile_bytes
    assert resp.headers["content-type"].startswith("application/x-protobuf")
    assert resp.headers.get("x-cache") == "miss"


def test_tile_cache_hits_skip_lakebase(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_cache()
    tile_bytes = b"\xff" * 32
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, _allowed())
    conn = _override_conn(monkeypatch, tile_bytes)
    try:
        r1 = client.get("/api/tiles/hazard_ppri_communes/10/520/375.pbf")
        r2 = client.get("/api/tiles/hazard_ppri_communes/10/520/375.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.headers.get("x-cache") == "hit"
    # Lakebase was only contacted once.
    assert conn.fetchval.call_count == 1


def test_tile_returns_empty_body_for_null_lakebase_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty tile (no features intersecting) is a legitimate 200
    with zero bytes — Leaflet.VectorGrid handles it cleanly."""
    _clear_cache()
    _override_sql_stub(_make_sql_stub())
    _override_allowlist(monkeypatch, _allowed())
    _override_conn(monkeypatch, None)  # asyncpg returns None for ST_AsMVT-no-rows
    try:
        resp = client.get("/api/tiles/hazard_ppri_communes/0/0/0.pbf")
    finally:
        app.dependency_overrides.pop(_get_app_sql, None)
    assert resp.status_code == 200
    assert resp.content == b""
