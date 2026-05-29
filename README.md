# dbrx-catnat — GeoCatNat

> An agentic GIS for insurers, on the Databricks Lakehouse.

A demo aimed at French P&C insurers that collapses three tools (Excel for portfolios, QGIS/ArcGIS for hazard maps, BI for claims) into a single Lakehouse-native app. A non-GIS user — underwriter, claims manager, exec — drives the map by **chatting** with it. The LLM agent translates intent into spatial SQL and layer operations against Unity Catalog data, with results landing on **Leaflet** (operational) and **Kepler.gl** (analytical / time-animated) panes.

Covers the three perils that dominate the French CatNat loss ratio: **inondation** (flood), **sécheresse / RGA** (drought-driven clay shrinkage), and **tempête / grêle** (storm / hail).

## Status

**Phase 0 — Data foundation: in progress.** The BRGM RGA (clay-shrinkage) layer is ingested end-to-end (bronze → silver → gold) on the `fevm-stable-po64og` workspace. PPRI / TRI flood layers and the IGN BD TOPO reference layers (via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)) are the next bronze drops. C3S windstorms are deferred out of v1.

See [`SPECS/SPEC.md`](SPECS/SPEC.md) for the detailed build spec — narrative, architecture, data model, MCP tool surface, demo script, build phases, and decisions.

## Quick start

```bash
# Install Python deps via uv (pyproject.toml)
uv sync

# Smoke-test connectivity + ST_* / H3 functions on the warehouse
uv run catnat probe

# Create catalog/schemas/volume (idempotent)
uv run catnat setup

# End-to-end: WFS pull → bronze → silver → gold for BRGM RGA
uv run catnat pipeline rga          # 100-feature sample
uv run catnat pipeline rga --full   # national dataset

# Run any SQL notebook standalone, passing widget params via -p
uv run catnat run notebooks/silver/10_rga_susceptibility.sql -p catalog=foo
```

Auth uses the `fevm-stable-po64og` Databricks CLI profile by default; override with `CATNAT_PROFILE`, `CATNAT_WAREHOUSE_ID`, or `CATNAT_CATALOG` env vars (see `src/catnat/config.py`).

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
pyproject.toml           ← uv-managed Python project (the `catnat` CLI)
databricks.yml           ← Databricks Asset Bundle (dev + prod targets)
src/catnat/              ← Python package powering the `catnat` CLI
  cli.py                 ←   typer entry point
  sql.py                 ←   notebook splitter + warehouse runner
  fetch/                 ←   per-source fetchers (RGA today, more coming)
notebooks/
  _setup/                ←   catalog / schema / volume bootstrap
  bronze/                ←   raw → typed Delta with native GEOMETRY(4326)
  silver/                ←   geometry repair, labels, centroid H3
  gold/                  ←   H3 r=9 polyfill for sub-second point joins
tests/                   ←   unit tests (no Databricks access required)
```

Phase 1+ (`app/`, `mcp/`) will land as the build phases in §7 of the spec are executed.
