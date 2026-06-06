# CLAUDE.md — orientation for AI collaborators

This file tells future Claude sessions how to be productive in this repo fast.
Skim it before touching code; read [`SPECS/SPEC.md`](SPECS/SPEC.md) only when
you need the demo narrative or the v1 scope decisions.

## What this is

**GeoCatNat** — a Databricks geospatial demo for French P&C insurers covering
the three CatNat perils (flood, drought, storm). The demo is an "agentic
GIS": a Leaflet pane driven by an MCP-backed LLM agent over Unity Catalog
data, with PostGIS vector-tile serving for the displayable layers.

**Phases 0, 0.5, 1, 2, 3, 4, 4.5 closed.** Retrospectives:
[`SPECS/PHASE_0_RETROSPECTIVE.md`](SPECS/PHASE_0_RETROSPECTIVE.md) for the
data-foundation end-state, [`SPECS/PHASE_4_RETROSPECTIVE.md`](SPECS/PHASE_4_RETROSPECTIVE.md)
for the agent + tile-serving end-state (also covers the gotchas we hit on
OBO token scopes, mirror chunk pagination, and the 26 MB inline-result cap).
[`SPECS/BENCHMARKS.md`](SPECS/BENCHMARKS.md) holds the P1 timing numbers
(6/6 queries < 1 s on a Small Serverless SQL WH). Next phase per SPEC §7
is **P5 — Genie integration**.
We have **3 hazard layers + 1 reference layer + synthetic portfolio + a
layer registry**: RGA, PPRI, TRI, IGN BD TOPO communes (dept 069 Rhône via
[`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)), plus
`portfolio_policies` (~5k sample / 500k full), a hand-seeded `events`
table, and `catnat_silver.layer_index` cataloguing everything. Windstorms
are deferred (SPEC §10.6); `portfolio_claims` is later work.

Run `uv run catnat bench --out SPECS/BENCHMARKS.md` any time the gold
layout changes — the report regenerates with pass/fail per query against
the <1 s target.

**Phase 2 (app scaffold) ✅.** Lives under `packages/app/` as a uv
workspace member, scaffolded with [apx](https://github.com/databricks-solutions/apx).
React 18 + Vite + TypeScript (strict mode) on the frontend, FastAPI on
the backend. Lint/format: **Biome** for frontend, **ruff** for Python.
Frontend tests: **Vitest** + React Testing Library (`bunx vitest run`).
Backend tests: `pytest` (FastAPI TestClient with dependency overrides
for `_get_app_sql`).

Two panes today: Leaflet (imperative — direct map handle for the MCP
`add_layer`/`style_layer` tools in P4), chat shell (agent in P4). The
Kepler pane attempted in P2.5 was removed (react-palm React-16 reconciler
fights React 19, and the analytical view didn't earn its weight).
`/api/layers` reads `catnat_silver.layer_index` via the app SP — the
OBO `sql`-scope path never minted a working token on this workspace
despite `user_api_scopes: ['sql']`, so we fall back to SP credentials.
The app SP holds `USE CATALOG` + `USE SCHEMA` + `SELECT` on
`catnat_silver`/`catnat_gold`.

`apx frontend build` runs Vite under the hood; we keep our own
`vitest.config.ts` separate to own the test environment.

**Phase 3 (MCP server) ✅.** FastMCP server mounted at `/mcp` on the
same FastAPI app, HTTP/SSE transport (SPEC §10.3). Five tools live in
`src/catnat_app/backend/mcp/`:

- `list_layers` — every `is_displayable=true` row of `layer_index`.
- `query_layer(layer_id, bbox?, where?, limit?)` — constrained SELECT
  with bbox + AND-joined equality predicates; binary geometries projected
  to GeoJSON, H3 cells to hex strings.
- `intersect_layer(layer_id, geom_wkt)` — `ST_Intersects` join against
  the layer's `geom_column`.
- `nearest(layer_id, point_wkt, k)` — k-NN by `ST_Distance`.
- `buffer(geom_wkt, meters)` — `ST_Buffer` wrapper; returns WKT.

Allowlist (`mcp/allowlist.py`) enforces that every tool call resolves to
a layer that is both displayable AND points at a table in
`catnat_silver`/`catnat_gold`. SQL builders are in `mcp/sql_templates.py`
— pure parameterised SQL, no f-string interpolation of user data.
Tests: 14 SQL-builder unit + 5 allowlist + 9 end-to-end via the
in-memory MCP client (`mcp.shared.memory.create_connected_server_and_client_session`).

UI-mutating tools (`add_layer`, `style_layer`, `zoom_to`,
`open_kepler_view`) defer to P4 since they need a server→browser channel
that pairs with the agent loop. Spilling overflow results to
session-scoped UC tables (SPEC §5.4) is a P6 polish item — for now
queries are capped at 500 rows inline.

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
