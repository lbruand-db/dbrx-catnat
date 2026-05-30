"""Run Databricks SQL notebooks against a SQL warehouse.

Splits notebook files on the Databricks cell marker (`-- COMMAND ----------`),
strips notebook-only constructs (`CREATE WIDGET ...`, `-- MAGIC ...`), splits
multi-statement cells on `;` at end-of-line, and submits each statement via the
Statement Execution API. Parameters in `:name` form are passed through.

Why a SQL warehouse and not Python compute: the target workspace only exposes
a Serverless SQL warehouse. When we later wire a job via Databricks Asset
Bundles, this driver becomes a thin wrapper over `databricks bundle run`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import (
    StatementParameterListItem,
    StatementResponse,
    StatementState,
)

from catnat.config import CONFIG

_CELL_MARKER = re.compile(r"^-- COMMAND ----------\s*$", re.MULTILINE)
_WIDGET = re.compile(r"^\s*CREATE\s+WIDGET\b.*$", re.IGNORECASE | re.MULTILINE)
_MAGIC = re.compile(r"^\s*-- MAGIC\b.*$", re.MULTILINE)
_RE_IDENTIFIER_CALL = re.compile(r"IDENTIFIER\(\s*([^)]+?)\s*\)", re.IGNORECASE)


def _resolve_identifier_call(body: str, params: dict[str, str]) -> str:
    """Resolve `:p || '.x' || :q || '.y'` into a literal `<catalog>.x.<q>.y`.

    Used to pre-substitute `IDENTIFIER(...)` arguments before sending SQL to
    the warehouse. The Databricks SQL engine accepts parameter markers in most
    contexts but rejects them inside `CREATE VIEW … AS …` (see SQLSTATE 0A000
    `PARAMETER_MARKER_IN_UNEXPECTED_STATEMENT`). Pre-resolving keeps the
    notebooks consistent across SELECT / CREATE TABLE / CREATE VIEW.
    """
    parts: list[str] = []
    for piece in (p.strip() for p in body.split("||")):
        if piece.startswith(":"):
            parts.append(params.get(piece[1:], piece))
        elif piece.startswith("'") and piece.endswith("'"):
            parts.append(piece[1:-1])
        else:
            parts.append(piece)
    return "".join(parts)


def resolve_identifiers(sql: str, params: dict[str, str]) -> str:
    """Substitute `IDENTIFIER(...)` calls with literal qualified names.

    Other `:param` markers are left alone — the Statement Execution API
    resolves them via the `parameters` payload.
    """
    return _RE_IDENTIFIER_CALL.sub(lambda m: _resolve_identifier_call(m.group(1), params), sql)


@dataclass(frozen=True)
class Statement:
    """One submittable SQL statement, traceable back to its source cell."""

    cell: int
    index_in_cell: int
    text: str

    @property
    def label(self) -> str:
        return f"cell {self.cell}.{self.index_in_cell}"


def _has_executable_content(text: str) -> bool:
    """True if the text has at least one non-comment, non-blank line."""
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return True
    return False


def split_notebook(sql: str) -> list[list[str]]:
    """Split a notebook source into [cell][statement] lists.

    Strips `CREATE WIDGET` lines and `-- MAGIC` lines (notebook-only). Splits
    multi-statement cells on `;` at end-of-line.
    """
    cells_raw = _CELL_MARKER.split(sql)
    out: list[list[str]] = []
    for raw in cells_raw:
        cleaned = _MAGIC.sub("", _WIDGET.sub("", raw))
        if not _has_executable_content(cleaned):
            out.append([])
            continue
        # Split on `;` only when it sits at end-of-line.
        stmts: list[str] = []
        buf: list[str] = []
        for line in cleaned.splitlines():
            if line.rstrip().endswith(";"):
                buf.append(line.rstrip().removesuffix(";"))
                stmt = "\n".join(buf).strip()
                if _has_executable_content(stmt):
                    stmts.append(stmt)
                buf = []
            else:
                buf.append(line)
        tail = "\n".join(buf).strip()
        if _has_executable_content(tail):
            stmts.append(tail)
        out.append(stmts)
    return out


def iter_statements(sql: str) -> Iterable[Statement]:
    """Yield each executable statement in the notebook."""
    for cell_idx, stmts in enumerate(split_notebook(sql), start=1):
        for sub_idx, stmt in enumerate(stmts, start=1):
            yield Statement(cell=cell_idx, index_in_cell=sub_idx, text=stmt)


class WarehouseRunner:
    """Thin wrapper over `WorkspaceClient.statement_execution`."""

    def __init__(
        self,
        profile: str | None = None,
        warehouse_id: str | None = None,
        wait_timeout: str = "50s",
        poll_interval: float = 2.0,
    ) -> None:
        self.profile = profile or CONFIG.profile
        self.warehouse_id = warehouse_id or CONFIG.warehouse_id
        self.wait_timeout = wait_timeout
        self.poll_interval = poll_interval
        self._client = WorkspaceClient(profile=self.profile)

    def execute(
        self,
        statement: str,
        parameters: dict[str, str] | None = None,
    ) -> StatementResponse:
        # Pre-resolve `IDENTIFIER(:foo || '.bar')` calls to literal names. The
        # Statement Execution API accepts parameter markers for SELECT/CREATE
        # TABLE but rejects them inside `CREATE VIEW … AS …` (SQLSTATE 0A000).
        # Doing the substitution here keeps notebooks uniform across contexts.
        if parameters:
            statement = resolve_identifiers(statement, parameters)
        params: list[StatementParameterListItem] | None = None
        if parameters:
            params = [
                StatementParameterListItem(name=k, value=v, type="STRING")
                for k, v in parameters.items()
            ]
        resp = self._client.statement_execution.execute_statement(
            warehouse_id=self.warehouse_id,
            statement=statement,
            wait_timeout=self.wait_timeout,
            parameters=params,
        )
        while resp.status and resp.status.state in (
            StatementState.PENDING,
            StatementState.RUNNING,
        ):
            time.sleep(self.poll_interval)
            assert resp.statement_id is not None
            resp = self._client.statement_execution.get_statement(resp.statement_id)
        if not resp.status or resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error if resp.status else None
            raise RuntimeError(
                f"statement failed: state={resp.status.state if resp.status else '?'}, "
                f"error={err.message if err else 'unknown'}"
            )
        return resp

    def run_notebook(
        self,
        path: Path,
        parameters: dict[str, str] | None = None,
        on_statement=None,
    ) -> int:
        """Run all statements in a SQL notebook file. Returns count submitted."""
        sql = path.read_text(encoding="utf-8")
        n = 0
        for stmt in iter_statements(sql):
            if on_statement is not None:
                on_statement(stmt)
            self.execute(stmt.text, parameters)
            n += 1
        return n
