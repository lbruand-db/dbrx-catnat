# dbrx-catnat — GeoCatNat

[![CI](https://github.com/lbruand-db/dbrx-catnat/actions/workflows/ci.yml/badge.svg)](https://github.com/lbruand-db/dbrx-catnat/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pytest](https://img.shields.io/badge/tested%20with-pytest-0A9EDC.svg)](https://docs.pytest.org/)
[![DuckDB](https://img.shields.io/badge/DuckDB-tested-FFF000.svg?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Databricks](https://img.shields.io/badge/Databricks-Lakehouse-FF3621.svg?logo=databricks&logoColor=white)](https://www.databricks.com/)
[![Databricks Asset Bundles](https://img.shields.io/badge/DAB-asset%20bundles-FF3621.svg?logo=databricks&logoColor=white)](https://docs.databricks.com/dev-tools/bundles/index.html)

> An agentic GIS for insurers, on the Databricks Lakehouse.

A demo aimed at French P&C insurers that collapses three tools (Excel for portfolios, QGIS/ArcGIS for hazard maps, BI for claims) into a single Lakehouse-native app. A non-GIS user — underwriter, claims manager, exec — drives the map by **chatting** with it. The LLM agent translates intent into spatial SQL and layer operations against Unity Catalog data, with results landing on **Leaflet** (operational) and **Kepler.gl** (analytical / time-animated) panes.

Covers the three perils that dominate the French CatNat loss ratio: **inondation** (flood), **sécheresse / RGA** (drought-driven clay shrinkage), and **tempête / grêle** (storm / hail).

## Status

**Phase 0 — Data foundation: in progress.** Three flood/drought layers are ingested end-to-end (bronze → silver → gold) on the `fevm-stable-po64og` workspace: BRGM RGA (clay-shrinkage), Géorisques PPRI (commune-level flood-prevention status), and Géorisques TRI (EU Floods Directive hazard maps — 11 scenario × intensity cells). The IGN BD TOPO reference layers (via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)) are next. C3S windstorms are deferred out of v1.

See [`SPECS/SPEC.md`](SPECS/SPEC.md) for the detailed build spec — narrative, architecture, data model, MCP tool surface, demo script, build phases, decisions, and the operational conventions in §8.1 (idempotency, cache-first downloads, `.env` parameters).

## Quick start — local inner loop

```bash
# 1. Configure operator parameters (CATNAT_PROFILE, _WAREHOUSE_ID, _CATALOG).
cp .env.example .env && $EDITOR .env

# 2. Install Python deps via uv (pyproject.toml).
uv sync

# 3. Smoke-test connectivity + ST_* / H3 functions on the warehouse.
uv run catnat probe

# 4. Create catalog/schemas/volume (idempotent).
uv run catnat setup

# 5. End-to-end pipelines per peril. WFS pulls are cached in the bronze
#    raw volume; re-runs are no-ops unless --force or CATNAT_FORCE_FETCH=true.
uv run catnat pipeline rga          # BRGM clay-shrinkage, 100-feature sample
uv run catnat pipeline ppri         # PPRI commune footprints (approuv + prescrit)
uv run catnat pipeline tri          # TRI hazard maps, 11 layers (scenario × intensity)
uv run catnat pipeline rga --full   # national dataset

# Run any SQL notebook standalone, passing widget params via -p.
uv run catnat run notebooks/silver/10_rga_susceptibility.sql -p catalog=foo
```

Operator parameters live in `.env` (template: `.env.example`). Real env vars
win over `.env`, so CI can override per-job without touching files. See
`src/catnat/config.py` for the resolution order.

## Deploy as Databricks Asset Bundles

The same notebooks ship as **Databricks Jobs** via DAB — fetch on
serverless Python compute, SQL stages on the warehouse:

```bash
databricks bundle validate -t dev
databricks bundle deploy -t dev         # builds the catnat wheel + uploads
databricks bundle run catnat_rga -t dev # or catnat_ppri / catnat_tri
databricks bundle deploy -t prod        # data_size=full for the prod target
```

Each job's tasks: `setup → fetch_<peril> → bronze_<peril> → silver_<peril> → gold_<peril>`. The local CLI and the DAB jobs target the **same notebooks** and the **same volume layout** — switching between them is invisible to the data.

## Architecture at a glance

- **App:** Databricks App (React + FastAPI) with three panes — Leaflet, Kepler.gl, chat.
- **Agent:** Claude via Foundation Model API, driving an HTTP/SSE **MCP server** that exposes layer ops (`add_layer`, `query_layer`, `buffer`, `intersect`, `nearest`, `zoom_to`), a Kepler view dispatcher, and a `ask_genie` delegation tool.
- **Data:** Unity Catalog `catnat.{bronze,silver,gold}` — Géorisques (PPRI / TRI / RGA), IGN ADMIN-EXPRESS, Copernicus C3S windstorms, DRIAS climate scenarios, plus a synthetic ~500k-policy portfolio. H3-indexed at r=9 (points) and r=7 (national aggregates).
- **Spatial engine:** native Databricks SQL `ST_*` + H3 on Photon. No Mosaic / Sedona dependency for v1.

## Conventions

- **Cloud / region:** AWS `eu-west-3` (Paris). All data stays on French / EU soil.
- **Language:** code, SQL, tool descriptions, and docs are English. The agent replies in the user's language (French in → French out).
- **Spatial libs:** native `ST_*` + H3 only, to keep the story "vanilla Databricks".

## Data attribution

This project relies on open public data — attributions belong in any redistributed artifact:

- **Copernicus Climate Change Service (C3S)** — windstorm reanalysis & ERA5. Generated using Copernicus Climate Change Service Information.
- **Géorisques** (PPRI, TRI, RGA susceptibility) — Etalab 2.0 / Licence Ouverte.
- **IGN ADMIN-EXPRESS** — Licence Ouverte 2.0.
- **BAN — Base Adresse Nationale** — Licence Ouverte 2.0 (geocoding).
- **Météo-France** public data — Etalab 2.0 (when used for enrichment).

## Repo layout

```
SPECS/SPEC.md            ← source of truth: spec, architecture, demo, decisions
README.md                ← this file
.env.example             ← copy to .env (gitignored) and edit operator params
pyproject.toml           ← uv-managed Python project (the `catnat` CLI + wheel)
databricks.yml           ← Databricks Asset Bundle (dev + prod targets, wheel artifact)
resources/jobs.yml       ← DAB job definitions, one per peril
src/catnat/              ← Python package powering both the CLI and the DAB jobs
  cli.py                 ←   typer entry point (local inner loop)
  jobs.py                ←   `catnat-job` entry point used by python_wheel_task
  config.py              ←   .env + env-var resolution (lazy/reactive)
  sql.py                 ←   notebook splitter + warehouse runner
  fetch/                 ←   per-source fetchers (RGA, PPRI, TRI)
    base.py              ←     shared cache-check + WFS-to-GeoJSONSeq plumbing
notebooks/
  _setup/                ←   catalog / schema / volume bootstrap
  bronze/                ←   raw → typed Delta with native GEOMETRY(4326)
  silver/                ←   geometry validity, date parsing, centroid H3
  gold/                  ←   H3 r=9 polyfill for sub-second point joins
tests/                   ←   unit tests (no Databricks access required)
.github/workflows/ci.yml ← lint (ruff) + test (pytest, workspace-free)
```

Phase 1+ (`app/`, `mcp/`) will land as the build phases in §7 of the spec are executed.
