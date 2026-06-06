"""Unit tests for `src/catnat/mirror.py`.

Pins the two pieces of mirror logic that have caused silent demo bugs:

1. **Chunk pagination chase** (`_read_layer_rows`): the Statement
   Execution API splits results across chunks; the first version of
   the mirror only read the first chunk and silently lost 242 of 496
   communes (commit `d9b942a`). The manifest-vs-rows assertion turns
   that class of bug into a hard fail.
2. **Postgres identifier quoting** (`_quote_pg_ident`): layer ids
   flow into DDL. Anything outside `[A-Za-z0-9_]` must be refused.

The async `_mirror_one_layer` is exercised via `asyncio.run()` with a
mocked `asyncpg.Connection` — no real Postgres, no Databricks, runs in
CI without credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState

from catnat.mirror import (
    LAYERS_NEEDING_BBOX_SCOPE,
    MIRROR_BBOX_WKT,
    LayerToMirror,
    _enumerate_layers,
    _mirror_one_layer,
    _quote_pg_ident,
    _read_layer_rows,
)

# --- Helpers -------------------------------------------------------------


def _mock_ws_response(
    *,
    state: StatementState = StatementState.SUCCEEDED,
    error_message: str | None = None,
    columns: list[str] | None = None,
    first_chunk_rows: list[list[object]] | None = None,
    next_chunk_index: int | None = None,
    total_row_count: int | None = None,
    statement_id: str = "stmt-123",
) -> MagicMock:
    """Build a MagicMock that quacks like `execute_statement`'s response."""
    response = MagicMock()
    response.status.state = state
    if error_message is not None:
        response.status.error.message = error_message
    else:
        response.status.error = None
    response.statement_id = statement_id
    if columns is not None:
        # `MagicMock(name=...)` is the *mock's* repr name, not an attribute
        # on the mock object — assign `.name` separately after construction.
        col_mocks = [MagicMock() for _ in columns]
        for col_mock, col_name in zip(col_mocks, columns, strict=True):
            col_mock.name = col_name
        response.manifest.schema.columns = col_mocks
    else:
        response.manifest.schema.columns = []
    response.manifest.total_row_count = total_row_count
    response.result.data_array = first_chunk_rows or []
    response.result.next_chunk_index = next_chunk_index
    return response


def _mock_chunk(rows: list[list[object]], next_chunk_index: int | None) -> MagicMock:
    """Build a mock that quacks like `get_statement_result_chunk_n`'s return."""
    chunk = MagicMock()
    chunk.data_array = rows
    chunk.next_chunk_index = next_chunk_index
    return chunk


# --- _quote_pg_ident -----------------------------------------------------


def test_quote_pg_ident_accepts_alphanumeric_underscore() -> None:
    assert _quote_pg_ident("hazard_ppri_communes") == '"hazard_ppri_communes"'
    assert _quote_pg_ident("layer_99") == '"layer_99"'
    assert _quote_pg_ident("MixedCase_OK") == '"MixedCase_OK"'


def test_quote_pg_ident_rejects_empty() -> None:
    with pytest.raises(ValueError, match="unsafe identifier"):
        _quote_pg_ident("")


@pytest.mark.parametrize(
    "name",
    [
        "drop table foo",  # space
        "table; DROP--",  # semicolon + dash
        'name"with"quotes',  # quotes
        "schema.table",  # dot
        "hyphen-name",  # hyphen
        "$dollar",  # dollar
    ],
)
def test_quote_pg_ident_rejects_unsafe_characters(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe identifier"):
        _quote_pg_ident(name)


# --- _read_layer_rows: happy paths --------------------------------------


def _layer(layer_id: str = "admin_communes") -> LayerToMirror:
    return LayerToMirror(
        layer_id=layer_id,
        table_fq=f"cat.catnat_silver.{layer_id}",
        geom_column="geometry",
        peril="reference",
    )


def test_read_layer_rows_single_chunk_no_pagination() -> None:
    ws = MagicMock()
    rows = [["POLYGON(...)", "69123", "Lyon"], ["POLYGON(...)", "69056", "Vénissieux"]]
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["geom_wkt", "code_insee", "nom"],
        first_chunk_rows=rows,
        next_chunk_index=None,
        total_row_count=2,
    )

    cols, out_rows = _read_layer_rows(ws, _layer())
    assert cols == ["geom_wkt", "code_insee", "nom"]
    assert out_rows == rows
    # No chunk fetch should have happened.
    ws.statement_execution.get_statement_result_chunk_n.assert_not_called()


def test_read_layer_rows_chases_next_chunk_index() -> None:
    """Regression test for the silent 242/496-row loss (commit d9b942a).

    The Statement Execution API splits 496 polygon rows into ~256-row
    chunks. Reading only the first chunk drops half the data without
    any error signal. This test pins the chunk-chase logic.
    """
    ws = MagicMock()
    chunk0_rows = [["geom0", "v0"], ["geom1", "v1"]]
    chunk1_rows = [["geom2", "v2"]]
    chunk2_rows = [["geom3", "v3"], ["geom4", "v4"]]

    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["geom_wkt", "v"],
        first_chunk_rows=chunk0_rows,
        next_chunk_index=1,
        total_row_count=5,
        statement_id="stmt-paginated",
    )
    ws.statement_execution.get_statement_result_chunk_n.side_effect = [
        _mock_chunk(chunk1_rows, next_chunk_index=2),
        _mock_chunk(chunk2_rows, next_chunk_index=None),
    ]

    cols, out_rows = _read_layer_rows(ws, _layer())
    assert cols == ["geom_wkt", "v"]
    assert out_rows == chunk0_rows + chunk1_rows + chunk2_rows

    # Both follow-up chunk fetches used the statement_id from the initial response.
    assert ws.statement_execution.get_statement_result_chunk_n.call_count == 2
    chunk1_kwargs = ws.statement_execution.get_statement_result_chunk_n.call_args_list[0].kwargs
    assert chunk1_kwargs == {"statement_id": "stmt-paginated", "chunk_index": 1}
    chunk2_kwargs = ws.statement_execution.get_statement_result_chunk_n.call_args_list[1].kwargs
    assert chunk2_kwargs == {"statement_id": "stmt-paginated", "chunk_index": 2}


# --- _read_layer_rows: failure modes ------------------------------------


def test_read_layer_rows_raises_on_row_count_mismatch() -> None:
    """The manifest-vs-rows guard. Without this, partial reads ship to
    Lakebase and the demo silently loses features."""
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["geom_wkt", "v"],
        first_chunk_rows=[["g0", "v0"], ["g1", "v1"]],
        next_chunk_index=None,
        total_row_count=496,  # manifest claims 496, we only got 2
    )

    with pytest.raises(RuntimeError, match="partial read of admin_communes"):
        _read_layer_rows(ws, _layer())


def test_read_layer_rows_raises_when_statement_fails() -> None:
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        state=StatementState.FAILED,
        error_message="Inline byte limit exceeded.",
        columns=["geom_wkt"],
        first_chunk_rows=[],
        next_chunk_index=None,
        total_row_count=0,
    )

    with pytest.raises(RuntimeError, match="Inline byte limit exceeded"):
        _read_layer_rows(ws, _layer("hazard_rga_susceptibility"))


# --- _read_layer_rows: bbox-scope parameters ----------------------------


def test_read_layer_rows_adds_bbox_filter_for_scoped_layers() -> None:
    """National RGA needs a per-layer bbox to stay under the 26 MB cap.

    Unbounded reads would either time out or hit the inline-result
    limit, so `LAYERS_NEEDING_BBOX_SCOPE` adds `ST_Intersects(geom,
    ST_GeomFromText(:bbox, 4326))` at read time. Verify the SQL + params
    actually carry the bbox.
    """
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["geom_wkt", "susceptibility_code"],
        first_chunk_rows=[["g0", "FORT"]],
        next_chunk_index=None,
        total_row_count=1,
    )

    layer = LayerToMirror(
        layer_id="hazard_rga_susceptibility",
        table_fq="cat.catnat_silver.hazard_rga_susceptibility",
        geom_column="geometry",
        peril="drought",
    )
    # Sanity-check the constant — this is what the production code reads.
    assert "hazard_rga_susceptibility" in LAYERS_NEEDING_BBOX_SCOPE

    _read_layer_rows(ws, layer)

    call_kwargs = ws.statement_execution.execute_statement.call_args.kwargs
    assert "ST_Intersects(geometry, ST_GeomFromText(:bbox_wkt, 4326))" in call_kwargs["statement"]
    param_pairs = [(p.name, p.value) for p in call_kwargs["parameters"]]
    assert ("bbox_wkt", MIRROR_BBOX_WKT) in param_pairs
    assert ("table_fq", "cat.catnat_silver.hazard_rga_susceptibility") in param_pairs


def test_read_layer_rows_omits_bbox_filter_for_unscoped_layers() -> None:
    """Layers that already fit (PPRI, TRI, admin_communes) stay
    unbounded. Bumping the bbox-scope set unconditionally was wrong —
    it dropped TRI's Mediterranean polygons and PPRI Ain rows."""
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["geom_wkt"],
        first_chunk_rows=[["g0"]],
        next_chunk_index=None,
        total_row_count=1,
    )
    _read_layer_rows(ws, _layer("admin_communes"))

    call_kwargs = ws.statement_execution.execute_statement.call_args.kwargs
    assert "ST_Intersects" not in call_kwargs["statement"]
    assert all(p.name != "bbox_wkt" for p in call_kwargs["parameters"])


# --- _enumerate_layers ---------------------------------------------------


def test_enumerate_layers_projects_response_rows_to_dataclasses() -> None:
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        columns=["layer_id", "table_fq", "peril", "geom_column"],
        first_chunk_rows=[
            ["admin_communes", "cat.catnat_silver.admin_communes", "reference", "geometry"],
            ["hazard_ppri_communes", "cat.catnat_silver.hazard_ppri_communes", "flood", "geom"],
        ],
        next_chunk_index=None,
        total_row_count=2,
    )
    layers = _enumerate_layers(ws)
    assert layers == [
        LayerToMirror(
            layer_id="admin_communes",
            table_fq="cat.catnat_silver.admin_communes",
            peril="reference",
            geom_column="geometry",
        ),
        LayerToMirror(
            layer_id="hazard_ppri_communes",
            table_fq="cat.catnat_silver.hazard_ppri_communes",
            peril="flood",
            geom_column="geom",
        ),
    ]


def test_enumerate_layers_raises_when_query_fails() -> None:
    ws = MagicMock()
    ws.statement_execution.execute_statement.return_value = _mock_ws_response(
        state=StatementState.FAILED,
        error_message="schema not found",
    )
    with pytest.raises(RuntimeError, match="layer_index enumeration failed"):
        _enumerate_layers(ws)


# --- _mirror_one_layer ---------------------------------------------------


class _FakeAsyncpgConn:
    """Just enough of `asyncpg.Connection` for the mirror's INSERT path.

    Records every `execute` (DDL) and `executemany` (batched INSERT)
    call so tests can pin the SQL + the row count.
    """

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.executemany_calls: list[tuple[str, list[tuple]]] = []

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def executemany(self, sql: str, batch: list[tuple]) -> None:
        self.executemany_calls.append((sql, list(batch)))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_mirror_one_layer_emits_drop_create_index_in_order() -> None:
    conn = _FakeAsyncpgConn()
    layer = _layer("admin_communes")
    cols = ["geom_wkt", "code_insee", "nom"]
    rows = [["POLYGON((0 0,1 0,1 1,0 0))", "69123", "Lyon"]]

    inserted = _run(_mirror_one_layer(conn, layer, cols, rows))  # type: ignore[arg-type]
    assert inserted == 1

    assert len(conn.executed) == 3
    assert conn.executed[0].startswith("DROP TABLE IF EXISTS")
    assert '"admin_communes"' in conn.executed[0]
    assert conn.executed[1].startswith("CREATE TABLE")
    assert "geom GEOMETRY(Geometry, 4326)" in conn.executed[1]
    assert '"code_insee" TEXT' in conn.executed[1]
    assert '"nom" TEXT' in conn.executed[1]
    assert conn.executed[2].startswith("CREATE INDEX")
    assert "USING GIST (geom)" in conn.executed[2]


def test_mirror_one_layer_skips_rows_with_null_geometry() -> None:
    """Null WKT is a legal silver value (invalid geometries filtered
    upstream) — the mirror just drops the row instead of crashing on
    PostGIS' `ST_GeomFromText(null)`."""
    conn = _FakeAsyncpgConn()
    layer = _layer("hazard_tri_flood")
    rows = [
        ["POLYGON((0 0,1 0,1 1,0 0))", "scenarioA"],
        [None, "scenarioB"],  # dropped
        ["POLYGON((2 2,3 2,3 3,2 2))", "scenarioC"],
    ]

    inserted = _run(_mirror_one_layer(conn, layer, ["geom_wkt", "scenario_code"], rows))  # type: ignore[arg-type]
    assert inserted == 2
    # Both surviving rows landed in a single executemany batch.
    assert len(conn.executemany_calls) == 1
    _, batch = conn.executemany_calls[0]
    assert len(batch) == 2
    assert batch[0] == ("POLYGON((0 0,1 0,1 1,0 0))", "scenarioA")
    assert batch[1] == ("POLYGON((2 2,3 2,3 3,2 2))", "scenarioC")


def test_mirror_one_layer_batches_inserts_at_500_rows() -> None:
    """At national-RGA scale (~50K rows), one giant INSERT would blow
    asyncpg's protocol budget. The mirror batches at 500 rows."""
    conn = _FakeAsyncpgConn()
    layer = _layer("hazard_ppri_communes")
    rows = [["POLYGON((0 0,1 0,1 1,0 0))", f"v{i}"] for i in range(1100)]

    inserted = _run(_mirror_one_layer(conn, layer, ["geom_wkt", "v"], rows))  # type: ignore[arg-type]
    assert inserted == 1100
    # 500 + 500 + 100
    batch_sizes = [len(b) for _, b in conn.executemany_calls]
    assert batch_sizes == [500, 500, 100]


def test_mirror_one_layer_insert_sql_uses_st_geomfromtext_for_geometry() -> None:
    conn = _FakeAsyncpgConn()
    layer = _layer("admin_communes")
    rows = [["POLYGON((0 0,1 0,1 1,0 0))", "69123", "Lyon"]]

    _run(_mirror_one_layer(conn, layer, ["geom_wkt", "code_insee", "nom"], rows))  # type: ignore[arg-type]

    assert len(conn.executemany_calls) == 1
    insert_sql, _batch = conn.executemany_calls[0]
    assert insert_sql.startswith("INSERT INTO ")
    assert "ST_GeomFromText($1, 4326)" in insert_sql
    # One placeholder per attribute column past the geometry.
    assert "$2" in insert_sql and "$3" in insert_sql


def test_mirror_one_layer_raises_when_first_column_not_geom_wkt() -> None:
    """Defensive: `_read_layer_rows` always projects `ST_AsText(geom)
    AS geom_wkt` as the first column. If that contract ever changes
    we want a hard failure here, not silently mis-binding."""
    conn = _FakeAsyncpgConn()
    layer = _layer("admin_communes")

    with pytest.raises(RuntimeError, match="unexpected manifest"):
        _run(_mirror_one_layer(conn, layer, ["wrong_first_col"], [["x"]]))  # type: ignore[arg-type]


def test_mirror_one_layer_handles_zero_attribute_columns() -> None:
    """Edge case: a layer with only geometry, no attributes.
    The CREATE TABLE should still produce valid SQL (no trailing comma)."""
    conn = _FakeAsyncpgConn()
    layer = _layer("geom_only")
    rows = [["POLYGON((0 0,1 0,1 1,0 0))"]]

    inserted = _run(_mirror_one_layer(conn, layer, ["geom_wkt"], rows))  # type: ignore[arg-type]
    assert inserted == 1
    create_sql = conn.executed[1]
    assert create_sql == 'CREATE TABLE "geo"."geom_only" (geom GEOMETRY(Geometry, 4326))'
