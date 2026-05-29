"""Unit tests for the SQL notebook splitter — no Databricks access needed."""

from __future__ import annotations

import textwrap

from catnat.sql import iter_statements, split_notebook


def test_split_simple_two_cells() -> None:
    src = textwrap.dedent("""\
        SELECT 1;

        -- COMMAND ----------

        SELECT 2;
    """)
    cells = split_notebook(src)
    assert len(cells) == 2
    assert cells[0] == ["SELECT 1"]
    assert cells[1] == ["SELECT 2"]


def test_leading_comments_are_passed_through() -> None:
    # The `-- Databricks notebook source` header is a SQL comment; it's safe to
    # leave in the statement payload because the SQL engine ignores it.
    src = textwrap.dedent("""\
        -- Databricks notebook source

        SELECT 1;
    """)
    cells = split_notebook(src)
    assert len(cells) == 1
    assert len(cells[0]) == 1
    assert "SELECT 1" in cells[0][0]


def test_strips_widget_and_magic() -> None:
    src = textwrap.dedent("""\
        -- MAGIC %md
        -- MAGIC # Heading

        -- COMMAND ----------

        CREATE WIDGET TEXT catalog DEFAULT 'foo';

        -- COMMAND ----------

        SELECT :catalog;
    """)
    cells = split_notebook(src)
    # Cells 1 and 2 are entirely notebook-only; cell 3 has the real statement.
    assert cells[0] == []
    assert cells[1] == []
    assert cells[2] == ["SELECT :catalog"]


def test_multi_statement_cell_splits_on_semicolon() -> None:
    src = textwrap.dedent("""\
        ALTER TABLE foo ALTER COLUMN a COMMENT 'aaa';
        ALTER TABLE foo ALTER COLUMN b COMMENT 'bbb';
        ALTER TABLE foo ALTER COLUMN c COMMENT 'ccc';
    """)
    cells = split_notebook(src)
    assert len(cells) == 1
    assert len(cells[0]) == 3
    assert all(s.startswith("ALTER TABLE foo") for s in cells[0])


def test_iter_statements_yields_labels() -> None:
    src = textwrap.dedent("""\
        SELECT 1;
        SELECT 2;
        -- COMMAND ----------
        SELECT 3;
    """)
    stmts = list(iter_statements(src))
    assert [s.label for s in stmts] == ["cell 1.1", "cell 1.2", "cell 2.1"]
    assert stmts[0].text.strip() == "SELECT 1"
    assert stmts[2].text.strip() == "SELECT 3"


def test_comment_only_cell_is_skipped() -> None:
    src = textwrap.dedent("""\
        -- this is just a comment
        -- another one
        -- COMMAND ----------
        SELECT 42;
    """)
    cells = split_notebook(src)
    assert cells[0] == []
    assert cells[1] == ["SELECT 42"]
