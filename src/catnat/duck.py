"""DuckDB-based local execution for Databricks SQL notebooks.

Lets us unit-test the silver/gold transformation logic without burning warehouse
cycles. The translation is hybrid:

1. **Regex pre-processing** strips Databricks-only constructs that have no
   DuckDB equivalent (`TBLPROPERTIES`, `OPTIMIZE … ZORDER BY`,
   `ALTER TABLE … COMMENT`, table-level `COMMENT '…'` after `CREATE TABLE`).
2. **`IDENTIFIER(:catalog || '.schema.table')`** is unwrapped — DuckDB doesn't
   have IDENTIFIER, and we don't carry a top-level catalog in tests.
3. **`:param` markers** are substituted with literal values (quoted strings).
4. **H3 function rewrites** account for the Databricks ↔ DuckDB API gap:
   - `h3_longlatash3(lon, lat, res)` → `h3_latlng_to_cell(lat, lon, res)`
   - `h3_polyfillash3(ST_AsBinary(geom), res)` → `h3_polygon_wkt_to_cells(ST_AsText(geom), res)`
5. **`LATERAL VIEW explode(arr) AS x`** → `, UNNEST(arr) AS t(x)`.

The notebooks themselves are untouched — translation happens at run time.

The runner reuses `catnat.sql.iter_statements` (same notebook splitter as the
Databricks runner), so cell semantics stay consistent across engines.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb

from catnat.sql import Statement, iter_statements

logger = logging.getLogger(__name__)


# --- pre-processing -----------------------------------------------------------

# `TBLPROPERTIES ( … )` — Databricks-only metadata. Strip outright.
_RE_TBLPROPERTIES = re.compile(r"\bTBLPROPERTIES\s*\([^)]*\)", re.IGNORECASE | re.DOTALL)
# Table-level `COMMENT 'text'` immediately after CREATE TABLE.
# We don't enforce a strict grammar; we just remove any standalone COMMENT '…'
# that's not part of an ALTER (which we'll strip wholesale).
_RE_TABLE_COMMENT = re.compile(r"\bCOMMENT\s+'(?:[^'\\]|\\.)*'", re.IGNORECASE | re.DOTALL)
# IDENTIFIER(:catalog || '.schema.table') → schema.table
_RE_IDENTIFIER = re.compile(
    r"IDENTIFIER\(\s*:(\w+)\s*\|\|\s*'\.(?P<rest>[\w\.]+)'\s*\)",
    re.IGNORECASE,
)
# Bare IDENTIFIER(:catalog) → no-op for our test schemas (used by the setup
# notebook's information_schema lookup, which we don't run).
_RE_IDENTIFIER_BARE = re.compile(r"IDENTIFIER\(\s*:(\w+)\s*\)", re.IGNORECASE)


def _strip_databricks_only(sql: str) -> str:
    """Remove constructs DuckDB doesn't understand."""
    sql = _RE_TBLPROPERTIES.sub("", sql)
    sql = _RE_TABLE_COMMENT.sub("", sql)
    return sql


def _unwrap_identifier(sql: str, params: dict[str, str]) -> str:
    """Replace IDENTIFIER(:catalog || '.schema.table') with schema.table.

    Drops the catalog level: DuckDB tests run in a single in-memory database
    where the schema is the top-level namespace.
    """

    def repl(m: re.Match[str]) -> str:
        # Just take what's after the catalog dot (schema.table).
        return m.group("rest")

    sql = _RE_IDENTIFIER.sub(repl, sql)
    # Bare IDENTIFIER(:catalog) — substitute the catalog literally for cases
    # like `information_schema` qualifiers. Quote if needed.
    sql = _RE_IDENTIFIER_BARE.sub(lambda m: params.get(m.group(1), "memory"), sql)
    return sql


def _substitute_params(sql: str, params: dict[str, str]) -> str:
    """Replace remaining `:param` markers with quoted SQL string literals.

    Numeric-looking values get unquoted so `CAST(:resolution AS INT)` works.
    """
    for name, value in params.items():
        literal = value if value.isdigit() else f"'{value}'"
        sql = re.sub(rf":\b{name}\b", literal, sql)
    return sql


# --- function rewrites --------------------------------------------------------

_RE_H3_LONGLATASH3 = re.compile(
    r"\bh3_longlatash3\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)
_RE_H3_POLYFILL_WKB = re.compile(
    r"\bh3_polyfillash3\s*\(\s*ST_AsBinary\s*\(\s*([^)]+?)\s*\)\s*,\s*([^)]+?)\s*\)",
    re.IGNORECASE,
)
_RE_LATERAL_VIEW_HEAD = re.compile(
    r"\bLATERAL\s+VIEW\s+(?:OUTER\s+)?explode\s*\(",
    re.IGNORECASE,
)
_RE_TRY_TO_DATE = re.compile(
    r"\bTRY_TO_DATE\s*\(\s*([^,()]+(?:\([^)]*\))?)\s*,\s*'([^']+)'\s*\)",
    re.IGNORECASE,
)


def _spark_to_strptime_format(fmt: str) -> str:
    """Convert Spark date-format tokens to DuckDB strptime tokens.

    Only handles the tokens we use today (yyyy, MM, dd). Add more as we hit them.
    """
    return (
        fmt.replace("yyyy", "%Y")
        .replace("yy", "%y")
        .replace("MM", "%m")
        .replace("dd", "%d")
        .replace("HH", "%H")
        .replace("mm", "%M")
        .replace("ss", "%S")
    )


def _extract_balanced(sql: str, open_idx: int) -> int:
    """Return the index of the `)` that closes the `(` at `open_idx`.

    Handles nested parentheses; assumes the source doesn't embed unbalanced
    parens inside string literals (true for our notebooks).
    """
    depth = 0
    for i in range(open_idx, len(sql)):
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced parens starting at {open_idx}")


def _rewrite_lateral_view_explode(sql: str) -> str:
    """`LATERAL VIEW explode(<expr>) AS <name>` → `, UNNEST(<expr>) AS t(<name>)`.

    `<expr>` can contain nested parens (function calls), which a regex alone
    can't handle, so we find the matching `)` ourselves and then look for the
    `AS <name>` suffix.
    """
    out = []
    i = 0
    while True:
        m = _RE_LATERAL_VIEW_HEAD.search(sql, i)
        if not m:
            out.append(sql[i:])
            break
        out.append(sql[i : m.start()])
        # Position of the opening `(` of explode(...).
        open_paren = m.end() - 1
        close_paren = _extract_balanced(sql, open_paren)
        inner = sql[open_paren + 1 : close_paren]
        # Look ahead for AS <name>.
        tail = sql[close_paren + 1 :]
        as_match = re.match(r"\s+AS\s+(\w+)", tail, re.IGNORECASE)
        if not as_match:
            raise ValueError("LATERAL VIEW explode without `AS <name>`")
        col = as_match.group(1)
        out.append(f", UNNEST({inner.strip()}) AS t({col})")
        i = close_paren + 1 + as_match.end()
    return "".join(out)


def _rewrite_try_to_date(sql: str) -> str:
    """`TRY_TO_DATE(expr, 'dd-MM-yyyy')` → `try_strptime(expr, '%d-%m-%Y')::DATE`."""

    def repl(m: re.Match[str]) -> str:
        expr = m.group(1).strip()
        fmt = _spark_to_strptime_format(m.group(2))
        return f"try_strptime({expr}, '{fmt}')::DATE"

    return _RE_TRY_TO_DATE.sub(repl, sql)


def _rewrite_functions(sql: str) -> str:
    """Map Databricks H3 + Spark-only constructs to DuckDB equivalents."""
    # Argument-order swap: Databricks (lon, lat, res) → DuckDB (lat, lon, res).
    sql = _RE_H3_LONGLATASH3.sub(r"h3_latlng_to_cell(\2, \1, \3)", sql)
    # h3_polyfillash3(ST_AsBinary(g), r) → h3_polygon_wkt_to_cells(ST_AsText(g), r)
    sql = _RE_H3_POLYFILL_WKB.sub(r"h3_polygon_wkt_to_cells(ST_AsText(\1), \2)", sql)
    # TRY_TO_DATE(expr, 'dd-MM-yyyy') → try_strptime(expr, '%d-%m-%Y')::DATE
    sql = _rewrite_try_to_date(sql)
    # LATERAL VIEW explode(arr) AS col → , UNNEST(arr) AS t(col) — balanced.
    sql = _rewrite_lateral_view_explode(sql)
    return sql


def translate(sql: str, params: dict[str, str]) -> str:
    """Translate one statement from Databricks SQL to DuckDB-runnable SQL."""
    sql = _strip_databricks_only(sql)
    sql = _unwrap_identifier(sql, params)
    sql = _substitute_params(sql, params)
    sql = _rewrite_functions(sql)
    return sql


# --- runner -------------------------------------------------------------------


def is_skippable(stmt_text: str) -> bool:
    """Drop statements that have no DuckDB analog and don't affect results.

    Currently: standalone `ALTER TABLE … COMMENT …` and `OPTIMIZE … ZORDER BY …`.
    Comments are cosmetic; ZORDER is a Delta optimization step. Both are no-ops
    for the data shape we test against.

    Comment-only lines at the head of the cell are stripped before the prefix
    check so a `-- ZORDER for fast joins\\nOPTIMIZE …` cell still skips.
    """
    head = ""
    for line in stmt_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        head = stripped.upper()
        break
    return head.startswith(("ALTER TABLE", "OPTIMIZE"))


class DuckRunner:
    """In-memory DuckDB session with spatial + h3 extensions pre-loaded.

    Mirrors `catnat.sql.WarehouseRunner.run_notebook` but on DuckDB.
    """

    def __init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        self.con.execute("INSTALL spatial; LOAD spatial;")
        self.con.execute("INSTALL h3 FROM community; LOAD h3;")

    def execute(self, sql: str) -> None:
        self.con.execute(sql)

    def query(self, sql: str) -> list[tuple]:
        return self.con.execute(sql).fetchall()

    def run_notebook(self, path: Path, params: dict[str, str]) -> int:
        """Run the executable cells of a SQL notebook against DuckDB."""
        n = 0
        for stmt in iter_statements(path.read_text(encoding="utf-8")):
            if is_skippable(stmt.text):
                continue
            translated = translate(stmt.text, params)
            translated = translated.strip()
            if not translated:
                continue
            try:
                self.con.execute(translated)
                n += 1
            except duckdb.Error as e:
                raise RuntimeError(
                    f"DuckDB rejected translated cell {stmt.label}:\n---\n{translated}\n---\n{e}"
                ) from e
        return n

    def run_statement(self, stmt: Statement, params: dict[str, str]) -> None:
        if is_skippable(stmt.text):
            return
        self.execute(translate(stmt.text, params))
