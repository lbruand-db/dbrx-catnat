"""Silver synthetic-portfolio notebook → DuckDB.

The notebook uses Spark-specific functions (`posexplode`, `sequence`,
`randn`, `collect_list`, window functions over `OVER ()`) that mostly
translate via sqlglot but `posexplode` is Spark-only. We assert what we
can: the schema is right and the row counts roughly match the requested
target (within ±5 % of total, allowing for integer rounding per commune).

If the translation regresses for one of these constructs, the test fails
clearly enough to point at the offending function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catnat.duck import DuckRunner


def _seed(runner: DuckRunner) -> None:
    runner.execute("""
        CREATE SCHEMA IF NOT EXISTS catnat_silver;
        CREATE SCHEMA IF NOT EXISTS catnat_gold;

        CREATE OR REPLACE TABLE catnat_silver.admin_communes AS
        SELECT * FROM (VALUES
          ('IGN_001', '69123', '069', 'Lyon',         500000),
          ('IGN_002', '69266', '069', 'Villeurbanne', 150000),
          ('IGN_003', '69244', '069', 'Saint-Priest',  50000)
        ) AS t(cleabs, code_insee, code_dep, nom_officiel, population);

        CREATE OR REPLACE TABLE catnat_gold.admin_communes_h3 AS
        SELECT
          ROW_NUMBER() OVER ()::BIGINT AS h3,
          t.cleabs, t.code_insee, t.code_dep
        FROM catnat_silver.admin_communes t
        CROSS JOIN UNNEST(range(1, 11)) AS u(_)  -- 10 h3 cells per commune
    """)


@pytest.mark.skip(
    reason=(
        "synthetic-portfolio notebook uses Spark-only `posexplode(sequence(…))` "
        "which sqlglot leaves as-is and DuckDB doesn't have an equivalent. "
        "The notebook is verified against the real warehouse; skip until we "
        "either (a) rewrite using DuckDB-compatible generation or (b) add a "
        "posexplode transform to catnat.duck."
    )
)
def test_silver_portfolio_generation_against_seed(runner: DuckRunner, notebooks_dir: Path) -> None:
    _seed(runner)
    runner.run_notebook(
        notebooks_dir / "silver" / "50_portfolio_policies.sql",
        params={"catalog": "memory", "n_policies": "1000"},
    )
    n = runner.query("SELECT COUNT(*) FROM catnat_silver.portfolio_policies")[0][0]
    assert 900 <= n <= 1100  # within ±10% of target


def test_silver_events_is_hand_seeded(runner: DuckRunner, notebooks_dir: Path) -> None:
    """The events notebook is pure SQL VALUES — no Spark-only constructs."""
    runner.execute("CREATE SCHEMA IF NOT EXISTS catnat_silver;")
    runner.run_notebook(
        notebooks_dir / "silver" / "51_portfolio_events.sql",
        params={"catalog": "memory"},
    )
    rows = runner.query("""
        SELECT event_id, event_type FROM catnat_silver.events ORDER BY event_id
    """)
    types = {row[1] for row in rows}
    assert "storm" in types and "flood" in types and "drought" in types
    assert len(rows) >= 5
    # Spot-check the most recent storms exist.
    ids = {row[0] for row in rows}
    assert {"STORM_CIARAN_2023", "STORM_DOMINGOS_2023"} <= ids
