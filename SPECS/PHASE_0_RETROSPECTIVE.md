# Phase 0 — retrospective

End-state of the data foundation, the decisions that got us here, and the
sharp edges to remember for Phase 1+.

## What landed

| Layer | Source | Bronze | Silver | Gold |
|---|---|---:|---:|---:|
| **RGA** (drought) | Géorisques WFS `ms:ALEARG_REALISE` | 100 features | 99 (1 invalid geom filtered) | **511 H3 r=9 cells** |
| **PPRI** (flood, commune-level) | Géorisques WFS `ms:PPRN_COMMUNE_RISQINOND_{APPROUV,PRESCRIT}` | 400 (2×200) | 400 | **70,879 H3 r=9 cells** |
| **TRI** (flood, EU directive) | Géorisques WFS — 11 `ALEA_SYNT_<scenario>_<intensity>_FXX` layers | 300 (10 of 11; `03_03MCC` server-side 502 → skipped) | 215 (ST_IsValid drops complex polygons) | **649 H3 r=9 cells** |
| **IGN admin_communes** (dept 069 via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)) | IGN BD TOPO v3.5 | n/a (UC view, no copy) | 496 communes / 5 depts / 2.4 M pop | **59,836 H3 r=9 cells** |
| Storm footprints | (deferred out of v1 — SPEC §10.6) | — | — | — |
| Synthetic portfolio | (still TODO — SPEC §4.2) | — | — | — |

22 DuckDB tests cover silver + gold for all four layers (workspace-free, runs in CI).
4 Databricks Asset Bundle jobs (`catnat_rga`, `catnat_ppri`, `catnat_tri`, `catnat_ign`) deploy clean against `fevm-stable-po64og`.

## Decisions worth remembering

1. **H3 r=9 as the lingua franca.** Every gold table carries an `h3` column at the same resolution, so cross-layer joins are an equi-join — no `ST_Intersects` at query time. r=9 (~150 m edge) matches policy-point density without exploding cell counts beyond ~70 k per layer.
2. **`sample` mode by default.** Each fetcher caps its per-layer pull (100/200/30 features depending on peril) so the whole pipeline runs in minutes against a Small Serverless SQL Warehouse and stays within DAB / CI cost budgets. Real demos flip `data_size=full` on the bundle.
3. **Cache-first downloads.** Every fetcher checks the bronze raw volume before touching the network. `--force` / `CATNAT_FORCE_FETCH=true` overrides. Makes the demo runnable offline once warm, and CI cheap.
4. **Idempotent everywhere.** `CREATE OR REPLACE TABLE`, `CREATE SCHEMA IF NOT EXISTS`, `IDENTIFIER(:catalog || …)` parameter markers. Re-runs are no-ops.
5. **Two run paths, one notebook set.** Local CLI (`uv run catnat pipeline rga`) and DAB job (`databricks bundle run catnat_rga`) both execute the same `notebooks/{bronze,silver,gold}/*.sql` files against the same UC layout. Inner loop and production differ only at the orchestration layer.
6. **Native Databricks SQL `ST_*` + H3.** No Mosaic, no Sedona. Keeps the "vanilla Databricks" story credible and avoids a library dependency the demo audience would have to install.
7. **dbtopo-bricks integration as a UC view, not a copy.** `catnat_silver.admin_communes` projects directly off `<catalog>.ign_bdtopo.ign_bdtopo_commune_dedup`. Lineage links cleanly back, storage cost is zero, refreshes are automatic.

## Things we tripped on (and the workarounds)

Documented for Phase 1+ — most of these would have eaten an afternoon if hit fresh.

| Surface | Symptom | Fix |
|---|---|---|
| **`CREATE WIDGET TEXT …`** in SQL notebooks | Both `notebook_task.warehouse_id` and the Statement Execution API reject it with `[PARSE_SYNTAX_ERROR]` — it's notebook-UI-only syntax. | Stripped from every notebook. Parameters flow via `base_parameters` → `:name` markers. Documented in CLAUDE.md gotcha #1. |
| **`python_wheel_task` + `SystemExit(0)`** | The Databricks wheel-task runner treats *any* `SystemExit` (including success) as failure. typer's `app()` always raises `SystemExit`. | `catnat-job` entry point (`src/catnat/jobs.py`) wraps `app(standalone_mode=False)` and swallows the clean exit. |
| **`CREATE VIEW … AS …`** rejects parameter markers in the body | SQLSTATE 0A000 `UNSUPPORTED_FEATURE.PARAMETER_MARKER_IN_UNEXPECTED_STATEMENT` — even though `SELECT` and `CREATE TABLE … AS …` accept them. | `WarehouseRunner.execute` pre-resolves `IDENTIFIER(:foo \|\| '.bar')` calls to literal names before sending. Symmetric with the DuckDB shim. |
| **`ST_MakeValid` not exposed on this warehouse** | Silver notebooks can't repair invalid polygons; we *filter* instead. ~28 % of TRI polygons get dropped because of self-intersections. | Documented; acceptable for demo data. Production would need a workaround (Mosaic? cluster-side `ST_MakeValid`? raise a JIRA?). |
| **sqlglot `transform()` is pre-order** | Doesn't descend into replacement subtrees, so substituting placeholders and rewriting functions in one pass leaves `$resolution` dangling inside our new `h3_polygon_wkt_to_cells(...)` call. | Two-pass transform: placeholders first, function rewrites second. |
| **Géorisques WFS returns flaky 502s** under sequential pulls | TRI's 11-layer batch failed midway on `03_03MCC`. | Exponential-backoff retries in `catnat.fetch.base.read_wfs_layer`; per-layer skip-on-failure for TRI (one bad layer doesn't break the batch). |
| **Géorisques won't serve `application/json`** for these layers | WFS error: `'application/json' is not a permitted output format`. | pyogrio reads GML (the WFS default) directly; we write GeoJSONSeq locally for the SQL `read_files(format=>'text')` path. |
| **dbtopo-bricks doubles its `table_prefix`** | Real table is `<catalog>.ign_bdtopo.ign_bdtopo_commune_dedup` (default prefix = schema name + `_`). | Exposed `--ign-table-prefix` as a parameter; default matches dbtopo-bricks default. |
| **`cours_d_eau` OOMs the serverless executor** | Python worker exit code 128 (kernel OOM-kill) writing the layer with the default 5000-row batch. Spark's own retry loop catches `MEMORY_LIMIT` but not exit-128, so it doesn't help. | Sent `feat/per-layer-batch-size-heuristic` upstream: probes geometry blob sizes in the GPKG (cheap, header-only `MAX(LENGTH(geom))` scan) and sizes the batch against the worst single row. Replaces the hardcoded `LARGE_GEOMETRY_DEPTS` allowlist. |
| **`CREATE CATALOG` requires metastore admin** | We don't have it on `fevm-stable-po64og`. | Nest schemas under `serverless_stable_po64og_catalog.catnat_{bronze,silver,gold}`. Bundle variable lets us flip to a top-level `catnat` catalog on a workspace with the privilege. (SPEC §4.3.1.) |

## Sample-mode caveat — why the cross-layer queries are sparse today

`notebooks/queries/01_communes_in_ppri.sql`, `02_triple_peril_overlap.sql`, and `03_rga_fort_pct_per_commune.sql` return **zero rows** against the current sample data. This is *not* a bug — it's the intersection of two scoping decisions:

- **Hazards are sampled nationally.** PPRI / TRI / RGA fetchers take the first N features from the WFS regardless of geography, so the cells are scattered across all 96 départements.
- **IGN is dept-scoped.** `dbtopo-bricks` was deployed for dept 069 only (496 communes / 5 neighbouring depts).

The probability that a random national PPRI/RGA cell lands in those 5 depts is small, so the intersection is empty for sample mode. Query 5 (`05_population_under_flood_by_dept.sql`) does return one row (dept `01` Ain — a 069 neighbour that happens to be in the PPRI sample), and Query 4 confirms the TRI grid is fully populated.

**Two ways to fix when we need the demo to look populated**:

1. **`--full` mode for the hazards** (`catnat pipeline ppri --full` etc.). Each peril then covers the full territory and the cross-joins fill in. Run-time goes up by ~minutes per peril.
2. **Geographic scoping at the WFS layer.** Add a BBOX filter to `read_wfs_layer` (or a `cql_filter` for INSEE codes) so the hazards land only inside the IGN footprint. Cheaper than `--full`, more demo-realistic, and worth doing before the synthetic portfolio lands.

## What we deliberately deferred

- **Storm footprints** (SPEC §10.6) — Météo-France doesn't publish event polygons; Copernicus C3S has them but legal review wasn't worth the time before v1. Layer is dead but the peril still appears in Act 2's narrative.
- **Synthetic portfolio** (SPEC §4.2) — needed for Phase 1+ but not for the foundation; Phase 1 work can build it as Phase 0.5 once `IGN admin_communes.population` is available to drive the geographic weighting.

## Metrics

| | Lines | Notes |
|---|---:|---|
| Notebooks (SQL) | 13 | setup + bronze/silver/gold for each peril + 5 sample queries |
| Python (`src/catnat/`) | ~600 | CLI, fetchers, SQL/DuckDB runners, jobs entry point |
| Tests | 22 | 6 splitter + 8 translation + 8 silver/gold integration (RGA/PPRI/TRI/IGN) |
| Commits to `main` | 14 | from spec → end of Phase 0 |
| CI runtime | ~60 s | lint + test on every push, all workspace-free |
| Sample pipeline run (RGA) | ~2 min | Cache-hit version; cold WFS pull adds ~30 s |
| Full sample-mode run (rga + ppri + tri + ign) | ~30 min | dominated by Géorisques WFS latency for TRI and dbtopo-bricks for dept 069 (~24 min) |

## Lessons for Phase 1+

- **The H3 join pattern works.** Sample queries 1–5 are short, fast, and exactly what the MCP server's `intersect_layer` / `query_layer` tools will produce. Phase 1 can templatize them.
- **`CREATE VIEW … AS …` parameter-marker restriction** means the MCP runtime cannot just pass `:catalog` through for view DDL. We resolve to literals at the runner layer; future agents producing view-creating SQL must do the same.
- **Bundle / wheel friction is real.** Every iteration is a `uv build` + `bundle deploy` + `bundle run` (~30–60 s of plumbing per change). The local CLI is essential for the inner loop — don't drift back to "let's just deploy and see".
- **sqlglot is excellent for everything except `IDENTIFIER(...)` in DDL positions**, which its parser rejects. Pre-pass with a regex, then let sqlglot do the rest. The split between sqlglot-owned transforms and our targeted `Anonymous`/`Placeholder` AST patches is small (4 functions) and stable.
- **DuckDB + sqlglot is a real testing surface for Databricks SQL** at this scale. 22 tests, sub-second runtime, no workspace credentials. We should keep this discipline as Phase 1 adds more SQL — even the MCP tool implementations should have DuckDB-backed unit tests where possible.

---

Phase 0 closed. Ready for Phase 0.5 (synthetic portfolio) or Phase 1 (Spatial SQL views over the medallion) per SPEC §7.
