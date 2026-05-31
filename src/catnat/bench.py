"""Performance benchmarks for the gold-layer access patterns.

Times a fixed set of queries against the SQL warehouse and reports
min / median / p95 / max in milliseconds. Intended to validate the
SPEC §7 P1 target: any single-layer point-in-polygon at portfolio
scale runs in <1 s on a Small Serverless SQL Warehouse.

The first run of each query is a *warmup* (paid setup: warehouse spin,
catalog metadata load) and not counted in the statistics — we report
steady-state cost.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass

from catnat.config import CONFIG
from catnat.sql import WarehouseRunner


@dataclass(frozen=True)
class BenchmarkQuery:
    name: str
    description: str
    sql: str


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    description: str
    n_runs: int
    min_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float


def _queries(catalog: str) -> list[BenchmarkQuery]:
    """Define the benchmark set, parameterised on the catalog name.

    Every query is self-contained — no temp tables, no params, so timings
    are comparable across runs.
    """
    silver = f"{catalog}.catnat_silver"
    gold = f"{catalog}.catnat_gold"
    return [
        BenchmarkQuery(
            "Q1_list_layers",
            "Layer-registry lookup (MCP list_layers backend)",
            f"SELECT * FROM {silver}.layer_index",
        ),
        BenchmarkQuery(
            "Q2_single_point_h3",
            "Geocoded point → portfolio: one address worth of policies",
            f"""
            SELECT *
            FROM {silver}.portfolio_policies
            WHERE h3 = h3_longlatash3(4.85, 45.75, 9)
            """,
        ),
        BenchmarkQuery(
            "Q3_point_in_polygon",
            "Single point-in-polygon: one address × all 4 layers as a single LEFT JOIN chain "
            "(the P1 target)",
            f"""
            WITH pt AS (SELECT h3_longlatash3(4.85, 45.75, 9) AS h3)
            SELECT
              ANY_VALUE(r.susceptibility_label) AS rga,
              ANY_VALUE(p.status)               AS ppri_status,
              ANY_VALUE(t.scenario_label)       AS tri_scenario,
              ANY_VALUE(c.nom_officiel)         AS commune
            FROM pt
            LEFT JOIN {gold}.hazard_rga_h3              r ON r.h3 = pt.h3
            LEFT JOIN {gold}.hazard_ppri_communes_h3    p ON p.h3 = pt.h3
            LEFT JOIN {gold}.hazard_tri_flood_h3        t ON t.h3 = pt.h3
            LEFT JOIN {gold}.admin_communes_h3          c ON c.h3 = pt.h3
            """,
        ),
        BenchmarkQuery(
            "Q4_portfolio_x_one_hazard",
            "Full portfolio × PPRI: exposure rollup by département",
            f"""
            SELECT
              p.code_dep,
              SUM(p.n_policies)            AS n_exposed,
              SUM(p.sum_insured_value_eur) AS exposed_eur
            FROM {gold}.portfolio_policies_h3 p
            JOIN (SELECT DISTINCT h3 FROM {gold}.hazard_ppri_communes_h3) ppri
              ON p.h3 = ppri.h3
            GROUP BY p.code_dep
            """,
        ),
        BenchmarkQuery(
            "Q5_portfolio_x_three_hazards",
            "Full portfolio × triple-peril overlap: cells hit by RGA AND PPRI AND TRI",
            f"""
            WITH triple AS (
              SELECT h3 FROM (SELECT DISTINCT h3 FROM {gold}.hazard_rga_h3)
              INTERSECT
              SELECT h3 FROM (SELECT DISTINCT h3 FROM {gold}.hazard_ppri_communes_h3)
              INTERSECT
              SELECT h3 FROM (SELECT DISTINCT h3 FROM {gold}.hazard_tri_flood_h3)
            )
            SELECT
              p.code_dep,
              SUM(p.n_policies)            AS n_triple_exposed,
              SUM(p.sum_insured_value_eur) AS triple_exposed_eur
            FROM {gold}.portfolio_policies_h3 p
            JOIN triple t ON p.h3 = t.h3
            GROUP BY p.code_dep
            """,
        ),
        BenchmarkQuery(
            "Q6_bbox_filter",
            "BBox spatial filter via H3: 5×5 km tile around Lyon (k-ring at r=7 then explode)",
            f"""
            WITH centre AS (
              SELECT h3_longlatash3(4.85, 45.75, 7) AS h3
            ),
            tile AS (
              SELECT explode(h3_kring(h3, 1)) AS h3 FROM centre
            ),
            tile_r9 AS (
              SELECT explode(h3_uncompact(collect_list(h3), 9)) AS h3 FROM tile
            )
            SELECT COUNT(*) AS cells_in_tile,
                   COUNT(DISTINCT a.cleabs) AS communes_in_tile
            FROM {gold}.admin_communes_h3 a
            JOIN tile_r9 t ON a.h3 = t.h3
            """,
        ),
    ]


def _time_sql(runner: WarehouseRunner, sql: str) -> float:
    """Execute once and return elapsed wall time in seconds."""
    start = time.perf_counter()
    runner.execute(sql)
    return time.perf_counter() - start


def run(
    catalog: str | None = None,
    n_runs: int = 5,
    warmup: bool = True,
) -> list[BenchmarkResult]:
    """Run the benchmark set and return per-query statistics."""
    runner = WarehouseRunner()
    cat = catalog or CONFIG.catalog
    queries = _queries(cat)
    results: list[BenchmarkResult] = []
    for q in queries:
        if warmup:
            _time_sql(runner, q.sql)
        samples = [_time_sql(runner, q.sql) for _ in range(n_runs)]
        ms = sorted(s * 1000 for s in samples)
        results.append(
            BenchmarkResult(
                name=q.name,
                description=q.description,
                n_runs=n_runs,
                min_ms=ms[0],
                median_ms=statistics.median(ms),
                p95_ms=ms[min(int(len(ms) * 0.95), len(ms) - 1)],
                max_ms=ms[-1],
            )
        )
    return results
