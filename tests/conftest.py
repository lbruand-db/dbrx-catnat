"""Shared pytest fixtures for the DuckDB-based notebook tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from catnat.duck import DuckRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOKS_DIR = REPO_ROOT / "notebooks"


@pytest.fixture
def runner() -> DuckRunner:
    """A fresh DuckDB session with spatial + h3 extensions loaded.

    Per-test isolation: each test gets its own in-memory database, so they can
    seed bronze tables and inspect silver/gold without polluting each other.
    """
    return DuckRunner()


@pytest.fixture
def notebooks_dir() -> Path:
    return NOTEBOOKS_DIR
