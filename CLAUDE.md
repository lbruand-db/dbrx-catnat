# CLAUDE.md — orientation for AI collaborators

This file tells future Claude sessions how to be productive in this repo fast.
Skim it before touching code; read [`SPECS/SPEC.md`](SPECS/SPEC.md) only when
you need the demo narrative or the v1 scope decisions.

## What this is

**GeoCatNat** — a Databricks geospatial demo for French P&C insurers covering
the three CatNat perils (flood, drought, storm). Phase 0 (data foundation) is
in progress; the eventual demo is an "agentic GIS" with Leaflet + Kepler.gl
panes driven by an MCP-backed LLM agent over Unity Catalog data.

**Phase 0 closed** at end of 2026-05-30 — see
[`SPECS/PHASE_0_RETROSPECTIVE.md`](SPECS/PHASE_0_RETROSPECTIVE.md) for the
end-state metrics, decisions, and gotchas. We have **3 hazard layers + 1
reference layer wired**: RGA, PPRI, TRI, IGN BD TOPO communes (dept 069
Rhône, via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)).
Windstorms are deferred (SPEC §10.6); synthetic portfolio is Phase 0.5.

Runnable sample queries showing the cross-layer H3 join pattern live in
[`notebooks/queries/`](notebooks/queries/) — start there to get a feel for
the medallion before touching code.

## How to work in this repo

### Conventions that aren't obvious from the code

- **Python + `uv`, not shell.** Any non-trivial automation lives in
  `src/catnat/`. No bash scripts in `scripts/`. (We tried bash early on and
  the user explicitly course-corrected — see memory.)
- **Idempotency everywhere.** `CREATE OR REPLACE TABLE`, `CREATE SCHEMA IF
  NOT EXISTS`, `IDENTIFIER(:catalog || …)` parameter markers. Pipelines must
  be safe to re-run; never write append-only state.
- **Cache-first WFS pulls.** `catnat.fetch.base.volume_exists` is checked
  before any network call. `--force` / `CATNAT_FORCE_FETCH=true` bypasses.
  This makes the demo runnable offline once warm, and CI cheap.
- **Operator parameters via `.env`.** Real env vars win over `.env`.
  `src/catnat/config.py` is the single resolution point — read env *lazily*
  so DAB jobs can override at runtime.
- **English everywhere in code, SQL, comments, docs.** Data and user-facing
  agent responses are French. The engineering surface is English-only.
- **Native Databricks SQL `ST_*` + H3 only** — no Mosaic, no Sedona. Keeps
  the "vanilla Databricks" story credible. Use sqlglot's `databricks`
  dialect for any local translation work, not regex.

### Two ways to run the pipeline

Both paths target the **same notebooks** and the **same UC volume layout**:

```bash
# Inner loop — local CLI, Statement Execution API against the warehouse.
uv run catnat pipeline rga          # or ppri / tri
uv run catnat pipeline rga --full

# Production — Databricks Asset Bundle, Python wheel on serverless compute.
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run catnat_rga -t dev
```

When you make a change, the inner loop catches regressions in seconds. The
DAB jobs are the deployable shape (SPEC §8 NFR).

### Testing

`tests/` is workspace-free — runs in CI without Databricks credentials.

- **`tests/test_sql.py`** — notebook splitter / cell parsing.
- **`tests/test_duck_translate.py`** — pure unit tests on the
  Databricks→DuckDB translation shim (`src/catnat/duck.py`).
- **`tests/test_{silver,gold}_{rga,ppri,tri,ign}_duckdb.py`** — full silver
  + gold notebook runs against in-memory DuckDB (spatial + h3 community
  extensions). Synthetic bronze (or dbtopo-bricks-shaped input for IGN)
  seeded directly via SQL; assertions cover label mapping, date parsing,
  geometry validity, view projection, H3 polyfill output.

Adding a new layer? Mirror the pattern:
1. `src/catnat/fetch/<peril>.py` for the WFS pull.
2. Notebook trio under `notebooks/{bronze,silver,gold}/`.
3. `tests/test_{silver,gold}_<peril>_duckdb.py`.
4. CLI + DAB job wiring (`src/catnat/cli.py`, `resources/jobs.yml`).
5. SPEC §4.1 row.

```bash
uv run pytest                 # all tests
uv run ruff check src/ tests/ # lint
uv run ruff format --check    # format check
```

CI (`.github/workflows/ci.yml`) runs the lint + test jobs on push and PR.

## Repo layout

```
SPECS/SPEC.md         demo narrative, architecture, data model, decisions
README.md             quickstart, badges, attribution
CLAUDE.md             (this file)
.env.example          template; copy to .env and edit
pyproject.toml        uv-managed Python project (CLI + wheel)
databricks.yml        DAB bundle (dev + prod targets, wheel artifact)
resources/jobs.yml    DAB job definitions (catnat_rga / _ppri / _tri)
src/catnat/
  cli.py              typer entry point — local inner-loop tool
  jobs.py             `catnat-job` entry point for DAB python_wheel_task
  config.py           lazy/reactive env-var resolution + .env loader
  sql.py              SQL notebook splitter + WarehouseRunner
  duck.py             sqlglot-based Databricks→DuckDB translation + runner
  fetch/              per-source fetchers
    base.py           shared WFS retry/cache/upload primitives
    rga.py, ppri.py, tri.py
notebooks/
  _setup/             catalog/schema/volume bootstrap
  bronze/             raw JSON → native GEOMETRY(4326) Delta
  silver/             geometry validity, label mapping, centroid H3 r=7
  gold/               H3 r=9 polyfill marts (ZORDER on h3)
tests/                pytest suite, workspace-free
.github/workflows/    CI (lint + test)
```

## Gotchas you'll hit if you don't read this first

1. **No `CREATE WIDGET TEXT …` in SQL notebooks.** The warehouse parser
   rejects it — both the Statement Execution API and `notebook_task.
   warehouse_id` fail with `[PARSE_SYNTAX_ERROR]`. Parameters flow via
   `base_parameters` → `:name` markers; the local orchestrator strips
   anything that looks like a widget defn.
2. **`python_wheel_task` rejects `SystemExit(0)`.** That's what typer
   raises after every successful run. The `catnat-job` entry point
   (`src/catnat/jobs.py`) wraps `app()` and swallows it.
3. **`ST_MakeValid` is not exposed on this warehouse.** Silver notebooks
   *filter* invalid geometries instead of repairing them. Expect ~25% loss
   on complex TRI polygons; document and move on.
4. **sqlglot's `transform()` is pre-order and doesn't descend into
   replacement subtrees.** Substitute `:param` placeholders in a *separate*
   first pass before doing function rewrites — otherwise the placeholder
   gets copied into the new node and never resolved.
5. **Géorisques WFS returns flaky 502s** under sequential pulls. Layer
   pulls have exponential-backoff retries; permanent failures skip the
   layer (rather than break the batch) and bronze reads via a glob.
6. **`CREATE CATALOG` requires metastore admin** which we don't have on
   `fevm-stable-po64og`. We nest schemas under `serverless_stable_po64og_
   catalog.catnat_{bronze,silver,gold}`. On a workspace with the right
   privileges, flip the `catalog` bundle variable to `catnat` and you're
   done (SPEC §4.3.1).

## When in doubt

- SPEC §4 has the data model and source layer names.
- SPEC §8 has the NFRs (latency, residency, language, cache, DAB).
- SPEC §10 has the resolved design decisions with the *why*.
- `dbtopo-bricks` is the house-style reference for DAB layout, wheel
  packaging, and `for_each_task` patterns we'll likely need for fan-out.
