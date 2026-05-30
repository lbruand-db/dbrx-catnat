"""DuckDB-based local execution for Databricks SQL notebooks.

Translation is a thin sandwich around **sqlglot**:

1. A single regex pre-pass unwraps `IDENTIFIER(:catalog || '.schema.table')`
   to a bare `schema.table` reference. sqlglot's Databricks parser doesn't
   accept `IDENTIFIER(...)` in DDL positions (e.g. after `CREATE TABLE`), so
   we resolve it before parsing.
2. `sqlglot.parse_one(sql, dialect="databricks")` lifts the rest into an AST.
3. AST transforms handle the four function-level gaps that sqlglot doesn't
   know about by default:
   - `h3_longlatash3(lon, lat, r)` → `h3_latlng_to_cell(lat, lon, r)`
   - `h3_polyfillash3(ST_AsBinary(g), r)` → `h3_polygon_wkt_to_cells(ST_AsText(g), r)`
   - `TRY_TO_DATE(s, 'dd-MM-yyyy')` → `CAST(try_strptime(s, '%d-%m-%Y') AS DATE)`
   - `:param` markers (sqlglot `Placeholder` nodes) → SQL literals.
4. `tree.sql(dialect="duckdb")` serialises back out. This is where sqlglot
   contributes most of the value for free:
   - `LATERAL VIEW explode(arr) AS x` → `CROSS JOIN UNNEST(arr) AS _t0(x)`
   - `get_json_object(j, '$.p')` → `j ->> '$.p'`
   - `CREATE OR REPLACE TABLE … COMMENT '…' TBLPROPERTIES (…)` → comments
     and TBLPROPERTIES dropped.
   - General syntax/dialect adjustments (CAST, identifier quoting).

Statement-level skips (`OPTIMIZE … ZORDER BY`, `ALTER TABLE … COMMENT`) are
filtered before parsing because they're cosmetic / Delta-only and sqlglot
serialises them back as DuckDB-rejecting strings.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from catnat.sql import Statement, iter_statements

logger = logging.getLogger(__name__)


# --- pre-pass: IDENTIFIER(:catalog || …) → schema.table -----------------------

# Match the entire IDENTIFIER(...) call. The body is a chain of `||`-joined
# pieces — `:param` markers and/or single-quoted string literals. We resolve
# the whole expression to a plain identifier (`schema.table` or `schema`),
# dropping the catalog level since DuckDB tests live in one in-memory database.
_RE_IDENTIFIER_CALL = re.compile(r"IDENTIFIER\(\s*([^)]+?)\s*\)", re.IGNORECASE)
_RE_PARAM = re.compile(r":(\w+)")


def _eval_identifier_body(body: str, params: dict[str, str]) -> str:
    """Evaluate a `||`-joined chain of `:param` / `'literal'` into a string.

    Drops the catalog level: everything before the first `.` is discarded.
    For bare `IDENTIFIER(:catalog)` (no `||`), the param is returned verbatim
    so qualifiers like `information_schema` work.
    """
    parts: list[str] = []
    for piece in (p.strip() for p in body.split("||")):
        if piece.startswith(":"):
            name = piece[1:]
            parts.append(params.get(name, name))
        elif piece.startswith("'") and piece.endswith("'"):
            parts.append(piece[1:-1])
        else:
            parts.append(piece)
    full = "".join(parts)
    return full.split(".", 1)[1] if "." in full else full


def _unwrap_identifier(sql: str, params: dict[str, str]) -> str:
    """Resolve `IDENTIFIER(:catalog || '.foo' || ... || '.bar')` to `foo.bar`.

    Done as a regex pre-pass because sqlglot's parser rejects `IDENTIFIER(...)`
    in DDL positions (e.g. `CREATE TABLE IDENTIFIER(...)`).
    """
    return _RE_IDENTIFIER_CALL.sub(lambda m: _eval_identifier_body(m.group(1), params), sql)


# --- AST transforms -----------------------------------------------------------

_DATE_FORMAT_TOKENS = (
    ("yyyy", "%Y"),
    ("yy", "%y"),
    ("MM", "%m"),
    ("dd", "%d"),
    ("HH", "%H"),
    ("mm", "%M"),
    ("ss", "%S"),
)


def _spark_to_strptime_format(fmt: str) -> str:
    """Convert Spark date-format tokens to DuckDB strptime tokens.

    Token order matters: replace longer tokens first so `yyyy` doesn't get
    chopped by the `yy` pass.
    """
    for spark, strp in _DATE_FORMAT_TOKENS:
        fmt = fmt.replace(spark, strp)
    return fmt


def _func_name(node: exp.Anonymous) -> str:
    """Lowercase function name of an `Anonymous` node, robust to None."""
    name = node.this
    return (name or "").lower() if isinstance(name, str) else str(name).lower()


def _substitute_placeholders(params: dict[str, str]):
    """Pre-pass: replace `:name` markers with SQL literals.

    Runs before function rewrites so the rewrites can copy already-substituted
    arg subtrees without re-running into placeholders. (sqlglot's `transform`
    visits pre-order and stops descending into a replacement node.)
    """

    def visit(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Placeholder):
            raw_name = node.this
            name = raw_name if isinstance(raw_name, str) else getattr(raw_name, "this", None)
            if name and name in params:
                value = params[name]
                return exp.Literal.number(value) if value.isdigit() else exp.Literal.string(value)
        return node

    return visit


def _rewrite_functions(node: exp.Expression) -> exp.Expression:
    """Function-level Databricks→DuckDB rewrites that sqlglot doesn't do by default."""
    # h3_longlatash3(lon, lat, r) → h3_latlng_to_cell(lat, lon, r)
    if isinstance(node, exp.Anonymous) and _func_name(node) == "h3_longlatash3":
        args = node.expressions
        if len(args) == 3:
            return exp.Anonymous(
                this="h3_latlng_to_cell",
                expressions=[args[1].copy(), args[0].copy(), args[2].copy()],
            )

    # h3_polyfillash3(ST_AsBinary(g), r) → h3_polygon_wkt_to_cells(ST_AsText(g), r)
    if isinstance(node, exp.Anonymous) and _func_name(node) == "h3_polyfillash3":
        args = node.expressions
        if len(args) == 2:
            first = args[0]
            if isinstance(first, exp.Anonymous) and _func_name(first) == "st_asbinary":
                wkt = exp.Anonymous(
                    this="ST_AsText", expressions=[e.copy() for e in first.expressions]
                )
            else:
                wkt = exp.Anonymous(this="ST_AsText", expressions=[first.copy()])
            return exp.Anonymous(this="h3_polygon_wkt_to_cells", expressions=[wkt, args[1].copy()])

    # TRY_TO_DATE(s, 'fmt') → CAST(try_strptime(s, '%-fmt') AS DATE)
    if isinstance(node, exp.Anonymous) and _func_name(node) == "try_to_date":
        args = node.expressions
        if len(args) == 2 and isinstance(args[1], exp.Literal) and args[1].is_string:
            fmt = _spark_to_strptime_format(args[1].this)
            strptime = exp.Anonymous(
                this="try_strptime", expressions=[args[0].copy(), exp.Literal.string(fmt)]
            )
            return exp.Cast(this=strptime, to=exp.DataType.build("date"))

    return node


# --- statement-level skips ---------------------------------------------------


def is_skippable(stmt_text: str) -> bool:
    """Cells with no DuckDB analog that don't affect data.

    `ALTER TABLE … COMMENT '…'` and `OPTIMIZE … ZORDER BY (…)` are cosmetic /
    Delta-only. We strip leading comment-only lines before the prefix check so
    a `-- doc\\nOPTIMIZE …` cell still skips.
    """
    head = ""
    for line in stmt_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        head = stripped.upper()
        break
    return head.startswith(("ALTER TABLE", "OPTIMIZE"))


# --- public translate + runner -----------------------------------------------


def translate(sql: str, params: dict[str, str]) -> str:
    """Translate one Databricks-dialect statement to DuckDB-runnable SQL."""
    if not sql.strip():
        return sql
    pre = _unwrap_identifier(sql, params)
    try:
        tree = sqlglot.parse_one(pre, dialect="databricks")
    except sqlglot.errors.ParseError as e:
        raise RuntimeError(f"sqlglot could not parse Databricks SQL:\n{pre}\n{e}") from e
    if tree is None:
        return pre
    # Two-pass transform: placeholders first (so function-rewrite arg copies
    # carry the literal values), then function rewrites.
    tree = tree.transform(_substitute_placeholders(params))
    tree = tree.transform(_rewrite_functions)
    return tree.sql(dialect="duckdb")


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
            translated = translate(stmt.text, params).strip()
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
