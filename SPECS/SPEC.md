# Spec — CatNat Geospatial Demo on Databricks

**Working title:** *GeoCatNat — an agentic GIS for insurers, on the Lakehouse*
**Audience:** Mixed exec + technical (EBC-style) for French P&C insurers
**Owner:** Lucas Bruand
**Status:** Draft

---

## 1. Context & narrative

French P&C insurers operate under the **régime CatNat** (Cat. Naturelles): premiums are capped, but exposure is concentrated geographically and is shifting fast under climate change. The three perils that dominate the loss ratio:

- **Inondation** (flood) — driven by river overflow + runoff; PPRI/TRI zoning is the legal frame.
- **Sécheresse / RGA** (drought-driven clay shrinkage damaging foundations) — now the #1 peril by claim count in many years.
- **Tempête / grêle** (storm / hail) — event-driven, with named storms (e.g. *Ciarán*, *Domingos*).

Insurers want to answer three questions, **fast and on the same map**:

1. *Where is my portfolio exposed today?* (static exposure × hazard)
2. *What just happened?* (event response — claims triage in hours, not days)
3. *Where are we heading?* (forward-looking: climate scenarios, RGA propagation, renewal pricing)

Today these questions live in **three different tools** (Excel for portfolios, QGIS/ArcGIS for hazard maps, a BI tool for claims). The demo collapses them into **one Lakehouse-native app** where a non-GIS user (underwriter, claims manager, exec) drives the map by **chatting** with it.

### The "QGIS with an LLM" pitch

QGIS is the open-source standard for spatial analysis but assumes the user knows GIS. We invert it: the user describes intent in natural language, an **MCP-based agent** translates intent into spatial SQL + layer operations against governed Lakehouse data, and the result lands as **Leaflet layers** (operational map) and **Kepler.gl views** (analytical / time-animated). All artifacts — datasets, prompts, SQL — are versioned in Unity Catalog.

---

## 2. Personas & primary jobs-to-be-done

| Persona | JTBD | Demo moment |
|---|---|---|
| **Chief Risk Officer** (exec) | "Show me board-level exposure to CatNat across the portfolio, with climate trajectory." | Opening: national heatmap + scenario slider |
| **Souscripteur / Underwriter** | "Should I quote this risk at this address? What's nearby?" | Address lookup → radius → hazard layers stack |
| **Gestionnaire sinistres / Claims manager** | "Storm Domingos hit last night — which policies are in the swath? Triage worst first." | Event-mode: ingest footprint, intersect, prioritized list |
| **Actuaire / Risk modeler** | "Compare RGA exposure under RCP 4.5 vs 8.5 by département." | Side-by-side Kepler views with scenario toggle |

The chat agent is the **shared entry point** for all four — they ask different questions, the same map answers.

---

## 3. Architecture (logical)

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Databricks App (Node/React + FastAPI)                                    │
│  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐    │
│  │  Leaflet pane    │  │  Kepler.gl pane    │  │  Chat / Agent pane  │    │
│  │  (operational)   │  │  (analytical)      │  │  (NL → actions)     │    │
│  └────────┬─────────┘  └──────────┬─────────┘  └──────────┬──────────┘    │
│           │  L.vectorGrid.protobuf │  view configs         │               │
│           │  /api/tiles/<layer>/{z}/{x}/{y}.pbf            │               │
│           └────────────┬───────────┴───────────────────────┘               │
│                        │                                                   │
│                  Agent runtime (Claude via Foundation Model API)           │
│                        │                                                   │
│                  MCP server (HTTP/SSE) exposing tools:                     │
│                    • list_layers / add_layer / remove_layer                │
│                    • query_layer (spatial SQL)                             │
│                    • buffer / intersect / nearest                          │
│                    • zoom_to / style_layer / filter_attributes             │
│                    • run_genie (portfolio Q&A)                             │
└────────┬───────────────────────┬─────────────────────────┬─────────────────┘
         │                       │                         │
         │ MVT tiles             │ ad-hoc spatial SQL      │ analytical Q&A
   ┌─────▼──────────┐      ┌─────▼──────────┐        ┌─────▼──────┐
   │ Lakebase       │      │ Databricks     │        │   Genie    │
   │ PostGIS        │      │ SQL Warehouse  │        │   Space    │
   │ (ST_AsMVT,     │      │ (Photon,       │        │ (curated   │
   │  GIST indexes) │      │  ST_*, H3)     │        │  semantic) │
   └─────▲──────────┘      └─────┬──────────┘        └─────┬──────┘
         │ daily mirror sync     │                         │
   ┌─────┴───────────────────────▼─────────────────────────▼──────┐
   │                       Unity Catalog                          │
   │   ┌────────────────────────────────────────────────────────┐ │
   │   │  catnat.bronze  (raw ingests)                          │ │
   │   │  catnat.silver  (typed, geo-tidy — source of truth)    │ │
   │   │  catnat.gold    (H3-indexed marts)                     │ │
   │   └────────────────────────────────────────────────────────┘ │
   └──────────────────────────────────────────────────────────────┘
```

### Why these choices

- **Databricks SQL + Photon** for spatial analytics: native `ST_*` functions and **H3** indexing land in GA; no Mosaic dependency required for the core demo. (Mosaic stays optional for raster overlays.)
- **Lakebase (Postgres + PostGIS)** for vector-tile serving — UC silver geometries are mirrored into a Lakebase `geo.*` schema, and `ST_AsMVT` generates Mapbox Vector Tiles on demand for the Leaflet pane (§10.7). The mirror is one-way; UC stays the source of truth.
- **Unity Catalog** as the source of truth for layers — every layer the LLM offers is a UC table or view. Governance and lineage come for free.
- **Genie** is reused (not rebuilt) as the analytical Q&A backend; the MCP server wraps it as a tool.
- **Two map panes** because they answer different questions: Leaflet is best for operational layered work (popups, draw, address lookup); Kepler is best for big-data analytical views (hex bins, time animation, side-by-side).
- **MCP** decouples the agent from the UI: the same MCP server could later power Claude Code, an Anthropic API agent, or a future internal tool.

---

## 4. Data model

All tables in Unity Catalog under `catnat.{bronze,silver,gold}`.

### 4.1 Hazard layers (silver / gold)

| Table | Source | Grain | Notes |
|---|---|---|---|
| `hazard_ppri_communes` | Géorisques WFS (`PPRN_COMMUNE_RISQINOND_APPROUV` + `_PRESCRIT`) | Polygon per commune × PPR status (`approuv` / `prescrit`) | v1: commune-level "is this commune in a PPR Inondation". The detailed in-PPRI zoning (zone rouge / bleue) lives in per-DDT shapefiles outside the WFS and is post-v1. |
| `hazard_tri_flood` | Géorisques WFS — 11 `ALEA_SYNT_<scenario>_<intensity>_FXX` layers | Polygon per TRI × scenario × intensity × flood-type | EU Floods Directive hazard maps. Three scenarios (`01` fréquent, `02` moyen, `03` extrême) × four intensities (`01FOR` fort, `02MOY` moyen, `03MCC`, `04FAI` faible) — the WFS exposes 11 cells of the 3×4 grid. Flood types: `01` débordement, `02` submersion marine, `03` ruissellement. |
| `hazard_rga_susceptibility` | BRGM (Géorisques) | Polygon, 4 levels (faible→fort) | Clay shrinkage exposure |
| ~~`hazard_storm_footprints`~~ | C3S Windstorm reanalysis (`sis-european-wind-storm-reanalysis`) + ERA5 `fg10` fallback | Polygon per event | **Deferred out of v1** — see §10.6. Storm/tempête peril stays in the narrative but its layer is post-v1. |
| `hazard_climate_rcp` | DRIAS / Copernicus | H3 cell × peril × scenario | RCP 4.5 / 8.5 deltas |
| `admin_communes` + reference layers (buildings, addresses, hydrography, transport) | IGN **BD TOPO v3.5** via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks) | 60 layers across 9 INSPIRE themes | Pre-built loader. See §4.4. |

### 4.2 Portfolio (synthetic for the demo)

| Table | Grain | Notes |
|---|---|---|
| `catnat_silver.portfolio_policies` | One row per policy | Population-weighted random H3 r=9 placement on `admin_communes`. Log-normal `insured_value_eur` (~250 k median, 50 k–5 M clip). Boolean `coverage_{flood,rga,storm}` (~85 % / 95 % / 60 %). Random `policy_start_date` in the last 5 years. |
| `catnat_silver.events` | One row per CatNat event | Hand-seeded list of 6 recent events (Ciarán, Domingos, Eunice, Alex/Vésubie, Gard 2002, RGA 2022 drought) with `event_type`, `event_date`, `jo_publication_date`, `affected_depts`. |
| `catnat_gold.portfolio_policies_h3` | One row per `(h3, code_dep)` | Per-cell rollup: `n_policies`, `sum_insured_value_eur`, per-peril counts and per-peril insured totals. ZORDER on `h3`. Joins to every hazard gold via a single equi-join. |
| `catnat_silver.portfolio_claims` | (Phase 1) | Linked to `policy_id` + `event_id` once the demo flow needs Act 2 claims-triage queries. |

**Why synthetic:** real insurer portfolios aren't available in time and not needed for the narrative. The generator is **pure SQL** — `silver/50_portfolio_policies.sql` reads `admin_communes` + `admin_communes_h3`, computes a per-commune policy quota proportional to population, expands via `posexplode(sequence(…))`, and joins each policy to a random H3 cell from its commune. Idempotent (`CREATE OR REPLACE TABLE`), runs in seconds against the Small Serverless SQL Warehouse.

**Sample vs. full**: `--n-policies 5000` (default) for inner-loop work; `--full` targets the spec's 500 k for the demo-size run. Geographic distribution comes for free from the IGN footprint — load more départements (via `dbtopo-bricks`) and the portfolio expands automatically.

### 4.3 H3 indexing convention

- **Resolution 9** (~150m edge) for policy points → fast joins to gridded hazard.
- **Resolution 7** (~1.2km edge) for national aggregates and Kepler hex layers.
- Hazard polygons pre-decomposed to H3 cells in gold for sub-second joins.

### 4.3.1 Workspace constraint — schema-prefix naming

The spec uses logical names like `catnat.bronze.foo`. On the current target workspace (`fevm-stable-po64og`) the user does **not** have `CREATE CATALOG` on the metastore, so we nest under the workspace-default catalog and prefix all schemas with `catnat_`:

| Spec name | Implementation name |
|---|---|
| `catnat.bronze.foo` | `serverless_stable_po64og_catalog.catnat_bronze.foo` |
| `catnat.silver.foo` | `serverless_stable_po64og_catalog.catnat_silver.foo` |
| `catnat.gold.foo`   | `serverless_stable_po64og_catalog.catnat_gold.foo` |

All bundle variables are parameterized so that on a workspace with metastore-admin privileges we flip the `catalog` variable to `catnat` and the schemas become `catnat.bronze` / `.silver` / `.gold` as originally specified — no code change.

### 4.4 Upstream loaders we reuse

We don't rewrite ingestion plumbing where a sibling project already does it well.

- **[`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)** — loads **IGN BD TOPO v3.5** (the French national topographic dataset — 60 layers across 9 INSPIRE themes: admin, addresses, buildings, hydrography, land cover, named places, public services, transport, regulated zones) into Unity Catalog Delta tables with native `GEOMETRY(4326)`. Server-side `ST_Transform` from Lambert-93 (EPSG:2154) → WGS84 (EPSG:4326), parallel per-department ingest via `for_each_task`, bilingual (FR/EN) table and column comments from the official IGN data model, deployable from a Databricks Asset Bundle.

  **Integration pattern**: deploy `dbtopo-bricks` to the same workspace as a sibling bundle. Our `catnat_silver.admin_communes` is a **UC view** over `<catalog>.<ign_schema>.commune_dedup` — no data copy, lineage links cleanly back. The catnat bundle adds only the CatNat-specific layers (PPRI / TRI / RGA / synthetic portfolio).

  **v1 scope**: just `commune_dedup` exposed as `catnat_silver.admin_communes` (+ a gold H3 r=9 mart for fast point-in-commune joins). Buildings, addresses, hydrography stay one hop away in the source schema and we'll layer them in as the demo narrative needs them.

  **Department scope for the demo**: **Rhône (`069`)** for v1 — Lyon + Rhône/Saône rivers (flood context) + the `FRE_TRI_LYON` TRI footprint we already ingested. The GPKG for dept 069 actually pulls in **496 communes across 5 départements** (69 + neighbours that overlap on commune borders), totalling ~2.4M people; the `_dedup` step keeps one row per `cleabs`. The gold H3 r=9 mart materialises to ~60k cells.

  **Table-prefix caveat**: dbtopo-bricks defaults its `table_prefix` to the schema name, so the actual table is `<catalog>.ign_bdtopo.ign_bdtopo_commune_dedup` (prefix doubled). Our `catnat pipeline ign` exposes `--ign-table-prefix` (default `ign_bdtopo_`) to track this without having to edit dbtopo-bricks.

  **OOM heuristic** (sent upstream as [PR draft] `feat/per-layer-batch-size-heuristic`): GPKG files are SQLite, so we probe the geometry-blob sizes via `MAX(LENGTH(geom))` *before* reading the layer (BLOB length is in row headers, not in payload — fast) and size the Spark write batch against the worst single row. Replaces a hardcoded `LARGE_GEOMETRY_DEPTS` allowlist with per-layer adaptive sizing. Without this, dept 069's `cours_d_eau` (river polygons) OOMs the serverless executor (1 GB limit) on the default 5000-row batches.

  **Deployment recipe** (one-time, from the sibling `dbtopo-bricks/` clone, on the `feat/per-layer-batch-size-heuristic` branch until merged):
  ```bash
  # Add a local-only target to dbtopo-bricks/databricks.yml:
  #   dbrx_catnat:
  #     workspace: { host: <our-workspace>, profile: <our-profile> }
  #     variables:
  #       catalog: serverless_stable_po64og_catalog
  #       schema:  ign_bdtopo
  #       departments_json: '["069"]'
  databricks bundle deploy -t dbrx_catnat
  databricks bundle run bdtopo_load -t dbrx_catnat   # ~24 min for dept 069
  # Then, back in dbrx-catnat:
  uv run catnat pipeline ign      # or `databricks bundle run catnat_ign`
  ```

  **Impact on Phase 0**: P0 estimate in §7 drops by ~1 day — no own-IGN-ingest to write.

### 4.5 Serving mirror (Lakebase PostGIS)

Geometries the Leaflet pane renders go through a **Lakebase Postgres**
instance with the **PostGIS** extension. A daily DAB job
(`mirror_silver_to_lakebase`) replicates each row of
`catnat_silver.layer_index` where `is_displayable = true` and
`geom_column` is set, into a matching `geo.<layer_id>` table — same
attribute columns, geometry stored as PostGIS `GEOMETRY(geometry, 4326)`
with a GIST index. The mirror is one-way; UC remains the source of truth
and the analytical SQL surface. Lakebase exists solely as the
tile-serving layer.

The vector-tile endpoint (`/api/tiles/<layer>/{z}/{x}/{y}.pbf`,
implemented in P4.5) runs an `ST_AsMVT` + `ST_AsMVTGeom` query against
this mirror — same shape as the
[`lakebase-vector-tile`](https://github.com/danny-db/lakebase-vector-tile)
PoC. See §10.7 for the rationale.

---

## 5. Functional requirements

### 5.1 Map UI (Leaflet pane)

- Base layers: OSM, IGN Plan, satellite.
- Layer panel populated from `catnat.gold.*` tables flagged `is_displayable=true`.
- Per-layer controls: visibility, opacity, color ramp (for choropleth).
- Click on feature → side panel with attributes + "Ask the agent about this".
- **Draw tools**: point, polygon, rectangle — drawn geometry becomes a queryable input the chat can reference ("show RGA in this polygon").
- Address search (geocoder: BAN — Base Adresse Nationale, free API).
- **Vector tile rendering** for every persistent layer via
  [`Leaflet.VectorGrid.Protobuf`](https://github.com/Leaflet/Leaflet.VectorGrid).
  The MCP `add_layer` tool returns a tile-URL template
  (`/api/tiles/<layer>/{z}/{x}/{y}.pbf`); the FE wires it to a
  `L.vectorGrid.protobuf` source with the per-peril style. Tiles are
  generated on-demand by `ST_AsMVT` against the Lakebase mirror (§4.5,
  §10.7). Small one-off geometries (e.g. an agent-drawn polygon, a
  buffered point) still ride as inline GeoJSON over the chat SSE
  channel — there's no point tiling something that lives for one turn.

### 5.2 Map UI (Kepler.gl pane)

- Triggered when the agent decides the answer is analytical/temporal (e.g. "evolution of claims over the year"), or by an explicit user toggle.
- Pre-configured views:
  - **National exposure hex map** (H3 r=7, choropleth on insured value × hazard).
  - **Event time-animation** (claims opening over the days following a named storm).
  - **Scenario comparison** (RCP 4.5 vs 8.5 side-by-side, dual-pane).
- View configs (JSON) are stored as UC volumes and selectable by the agent.

### 5.3 Chat / Agent pane

- Persistent chat tied to the map session; conversation state survives layer changes.
- The agent has access to the MCP tools below; it streams tool calls and explanations.
- "Show me what you queried" affordance: any answer is one click away from the SQL the agent ran.
- Suggested prompts depend on the active layers (cold-start scaffolding).

### 5.4 MCP server — tool surface

| Tool | Description | Returns |
|---|---|---|
| `list_layers` | Enumerate displayable layers in UC. | `[{name, peril, grain, columns}]` |
| `add_layer(name, style?)` | Add a layer to the active Leaflet pane. | layer id |
| `remove_layer(id)` | Remove. | ok |
| `style_layer(id, ramp, by_column)` | Restyle choropleth. | ok |
| `query_layer(name, where?, geom?)` | Run a constrained SQL against a layer. | result set + (optional) GeoJSON |
| `buffer(geom, meters)` | ST_Buffer wrapper. | geometry |
| `intersect(geom_a, geom_b)` / `intersect_layer(layer, geom)` | Spatial join. | features |
| `nearest(point, layer, k=5)` | k-NN. | features |
| `zoom_to(geom \| commune_insee)` | Camera. | ok |
| `open_kepler_view(view_name, params?)` | Switch/open Kepler pane. | ok |
| `ask_genie(question)` | Delegate analytical Q&A to Genie space; receive narrative + SQL. | `{answer, sql, df}` |

Design rules:
- **Tools never return more than ~1MB** to the agent; large result sets are written to a session-scoped UC table and the agent gets a handle.
- **Every spatial op is server-side SQL** (no shipping geometries through the agent's context).
- The MCP server enforces a **layer allowlist** — the LLM cannot read tables outside `catnat.gold` and a sanctioned subset of `silver`.

### 5.5 Agent behavior

- System prompt anchors persona: *"You are a CatNat geospatial analyst for a French P&C insurer. You operate on Unity Catalog data. You prefer to show on the map before answering in prose."*
- Tool routing heuristics:
  - "Show me X **on the map**" / "add layer" → Leaflet ops.
  - "Compare", "evolution", "trend", "by département" → Kepler view or Genie.
  - Quantitative portfolio question → `ask_genie` first, then visualize the result.
- Failure modes are surfaced honestly ("no PPRI data for this commune") — never silently empty.

---

## 6. Demo script (15 min, EBC-style)

> Three acts, each ~5 min. Same map, same chat — the audience never sees a tool switch.

### Act 1 — *"Where is my portfolio exposed?"* (CRO frame)

1. Open app, national view of France. Empty map.
2. Chat: **"Donne-moi une vue exécutive de mon exposition CatNat sur le portefeuille."**
3. Agent adds: H3 hex layer of insured value, then overlays RGA susceptibility. Kepler pane opens with national choropleth by département.
4. Click on Vaucluse → side panel shows €X exposed, Y% in zone PPRI rouge.

### Act 2 — *"What just happened?"* (Claims frame)

1. Chat: **"Charge l'empreinte de la tempête Domingos."**
2. Agent adds storm footprint layer, intersects with policies, opens a Kepler time-animation of claims opening over 72h.
3. Underwriter persona overlay: **"Quelles communes prioriser pour les visites d'expert ?"** → ranked list + zoom.

### Act 3 — *"Where are we heading?"* (Actuary frame)

1. Chat: **"Compare RGA actuel vs RCP 8.5 horizon 2050 sur l'Île-de-France."**
2. Agent opens Kepler side-by-side; explains the delta in prose; offers a follow-up *"voulez-vous voir l'impact sur la S/P projetée ?"* → calls Genie, returns a chart.
3. Closing slide: same three questions, same map, same chat — **vs** the three-tool status quo.

---

## 7. Build phases

| Phase | Duration | Deliverable |
|---|---|---|
| **P0 — Data foundation** ✅ | ~2 days | Bronze ingests (Géorisques PPRI/TRI/RGA); Silver typing + geometry validity; Gold H3 marts. IGN reference layers come from [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks) (see §4.4). Storm footprints deferred (§10.6); synthetic portfolio bumped to **P0.5**. **Retrospective**: [`SPECS/PHASE_0_RETROSPECTIVE.md`](PHASE_0_RETROSPECTIVE.md) — what shipped, decisions made, gotchas we tripped on, sample queries. |
| **P0.5 — Synthetic portfolio** ✅ | ~0.5 day | `portfolio_policies` (~5k sample / 500k full) weighted by `admin_communes.population`, H3-indexed at r=9 + per-cell gold rollup. Hand-seeded `events` table (6 recent CatNat events). `portfolio_claims` deferred to P1 — needs event-specific overlap logic. New sample query `06_portfolio_rga_exposure.sql` demonstrates the portfolio × hazard equi-join. |
| **P1 — Spatial SQL layer** ✅ | ~1 day | UC layer registry (`catnat_silver.layer_index`) — single row per consumable layer, drives the future MCP `list_layers`. Performance benchmark suite (`catnat bench` → [`SPECS/BENCHMARKS.md`](BENCHMARKS.md)) covering 6 reference query shapes (registry lookup, single-point geocode, single point-in-polygon × 4 layers, full portfolio × 1 hazard, triple-peril intersect, bbox via H3 k-ring). **6 / 6 queries pass the <1 s target on median latency** (best 557 ms, worst 746 ms). |
| **P2 — Databricks App scaffold** ✅ | ~2 days | Scaffolded with [apx](https://github.com/databricks-solutions/apx) under `packages/app/` (uv workspace member). React 18 + Vite + TypeScript (strict), Biome for lint/format, Vitest + RTL for tests. Two-pane layout: Leaflet (operational, imperative — direct map handle for the MCP `add_layer` / `style_layer` tools in P4), chat (input + history shell, agent wiring is P4). The Kepler.gl pane attempted in P2.5 was removed — `react-palm`'s React 16 reconciler fights every modern React, and the analytical view didn't earn its weight in the demo; revisit if a clear use case appears. `/api/layers` reads `catnat_silver.layer_index` via the app SP (the OBO `sql`-scope path never minted a working token on this workspace despite `user_api_scopes: ['sql']`; the app SP holds `USE CATALOG` + `USE SCHEMA` + `SELECT` on `catnat_silver`/`catnat_gold`). **14 FE tests** (Vitest) + **3 backend tests** (pytest+FastAPI TestClient) — all workspace-free, green in CI. |
| **P3 — MCP server** ✅ | ~2 days | FastMCP server mounted at `/mcp` on the same FastAPI app (HTTP/SSE transport, SPEC §10.3). Five tools — `list_layers`, `query_layer(bbox, where, limit)`, `intersect_layer(geom_wkt)`, `nearest(point_wkt, k)`, `buffer(geom_wkt, meters)`. Allowlist enforced via `catnat_silver.layer_index` (`is_displayable = true` + schema must be `catnat_silver`/`catnat_gold`). Inline returns capped at 500 rows with binary geometries projected to GeoJSON / H3 cells to hex strings; spilling overflow to session-scoped UC tables is tracked as a P6 polish item (the SPEC §5.4 wording — agents iterate on filters fine without it). **28 MCP tests**: 14 SQL-builder units, 5 allowlist, 9 end-to-end via the in-memory MCP client. UI-mutating tools (`add_layer`, `style_layer`, `zoom_to`, `open_kepler_view`) come with the agent in P4 since they need a server→browser channel. |
| **P4 — Agent integration** ✅ | ~3 days | FastMCP agent loop on `/api/chat` (FastAPI SSE), Claude via the Foundation Model API (`databricks-claude-sonnet-4-6`). Streaming SSE events (`tool_call`, `tool_result`, `delta`, `map_op`, `done`, `error`) drive both the chat pane and the Leaflet map: every UI-mutating tool emits a `map_op` payload the FE applies via the imperative map handle. UI-mutating tools shipped: `add_layer`, `remove_layer`, `style_layer`, `zoom_to`. Reverse-channel `context` block in the system prompt carries viewport + active layers + click-selected feature so "ce truc" / "cette zone" resolves correctly without a tool call. Bearer-rotation `httpx.Auth` fixed FMAPI's 1-hour token expiry; empty-arguments normalisation fixed the `""` → "invalid request" reject. **63 backend tests** + **44 FE tests** + **1 Playwright happy-path** + **1 golden-trace** replay test. **Retrospective**: [`SPECS/PHASE_4_RETROSPECTIVE.md`](PHASE_4_RETROSPECTIVE.md). |
| **P4.5 — Lakebase tile serving** ✅ | ~2 days | Lakebase Postgres + PostGIS instance (`catnat-tiles`, role `LAKEBASE_OAUTH_V1`); `geo.<layer_id>` mirror with GIST index per displayable silver polygon layer. Daily DAB job (`catnat-job mirror`) chases `next_chunk_index` to avoid silent row drops and asserts `len(rows) == manifest.total_row_count`. `/api/tiles/<layer>/{z}/{x}/{y}.pbf` runs `ST_AsMVT` + `ST_AsMVTGeom` against the mirror with a 5-min in-memory cache; per-request `asyncpg` connect (no pool yet — P6). The MCP `add_layer` tool returns `{op, layer_id, tile_url, style}`; the FE renders via `L.vectorGrid.protobuf` (no MapLibre, see §10.7). National RGA blew the 26 MB inline-result cap so `LAYERS_NEEDING_BBOX_SCOPE` adds a per-layer `ST_Intersects` filter at read time; long-term fix is `disposition=EXTERNAL_LINKS` (P6). **7 tile-endpoint tests** included in the P4 count above. |
| **P5 — Genie integration** | ~1 day | Genie space curated for portfolio Q&A; `ask_genie` tool. |
| **P6 — Demo polish** | ~2 days | Three act scripts rehearsed; failure-mode fallbacks; one pre-recorded backup. |

**Total:** ~12 working days for one builder; ~6 days with two builders working frontend/backend in parallel. P0 closed at end of 2026-05-30 — see [retro](PHASE_0_RETROSPECTIVE.md).

---

## 8. Non-functional requirements

- **Latency budget**, per chat turn: agent first token ≤ 2s; first map update ≤ 5s; full Kepler view ≤ 10s.
- **Warehouse:** Small Serverless SQL WH is the demo target — if a query needs Medium, the underlying table layout is wrong.
- **Cost target** for a 30-min demo session: < €5 (mostly SQL WH idle + FMAPI tokens).
- **Reproducibility:** the full stack is deployable from `databricks bundle deploy` against a fresh workspace. The `catnat` Python package builds to a wheel that DAB uploads automatically; each peril (`rga`, `ppri`, `tri`) ships as a Databricks Job whose tasks chain `setup → fetch (python_wheel_task on serverless) → bronze → silver → gold` (SQL notebooks on the warehouse). The local `catnat` CLI stays as the fast inner-loop tool; both paths target the exact same notebooks and the exact same volume layout.
- **Governance posture:** every chat turn that ran a query is loggable to a UC audit table — sellable as a differentiator vs. shadow-IT QGIS workflows.
- **Data residency:** all data stays on French / EU soil. Target workspace is **AWS `eu-west-3` (Paris)**; Copernicus / ERA5 sources are staged from the EU mirrors of the AWS Open Data registry to avoid cross-region egress. No data leaves the EU at any stage of the pipeline.
- **Language convention:** code, SQL, table/column names, MCP tool descriptions, agent system prompt, comments, and docs are all in **English**. The agent renders user-facing responses in the language of the question (French in / French out, English in / English out).

### 8.1 Testability — silver + gold notebooks run on DuckDB

Every silver and gold notebook is exercised in CI against an in-memory
**DuckDB** session (spatial + h3 community extensions). The notebooks are not
duplicated — `src/catnat/duck.py` translates them at runtime using
**[sqlglot](https://github.com/tobymao/sqlglot)** as the spine, with targeted
AST-level patches for the four function-level gaps sqlglot doesn't know about:

| Construct | Owner |
|---|---|
| `LATERAL VIEW explode(arr) AS x` → `CROSS JOIN UNNEST(arr) AS _t(x)` | sqlglot (databricks → duckdb dialect transpile) |
| `get_json_object(j, '$.p')` → `j ->> '$.p'` | sqlglot |
| `CREATE OR REPLACE TABLE … COMMENT '…' TBLPROPERTIES (…) AS …` (strips comments + properties) | sqlglot |
| `IDENTIFIER(:catalog \|\| '.schema.table')` → `schema.table` | regex pre-pass (sqlglot's parser rejects `IDENTIFIER(...)` in DDL positions) |
| `:param` → SQL literal | AST transform on `Placeholder` nodes |
| `h3_longlatash3(lon, lat, r)` → `h3_latlng_to_cell(lat, lon, r)` (arg swap) | AST transform on `Anonymous` |
| `h3_polyfillash3(ST_AsBinary(g), r)` → `h3_polygon_wkt_to_cells(ST_AsText(g), r)` | AST transform on `Anonymous` |
| `TRY_TO_DATE(s, 'dd-MM-yyyy')` → `CAST(try_strptime(s, '%d-%m-%Y') AS DATE)` | AST transform on `Anonymous` |
| `OPTIMIZE … ZORDER BY` / `ALTER TABLE … COMMENT` | cell-level skip (cosmetic / Delta-only) |

The two AST passes run in order — `Placeholder` substitution first, then
function rewrites — so the function rewrites can copy already-substituted
subtrees without colliding with sqlglot's pre-order walk semantics
(replacement subtrees aren't re-visited).

Tests live in `tests/test_*_duckdb.py`: synthetic bronze rows are seeded
directly, silver + gold notebooks run against them, assertions cover label
mapping, geometry-validity filtering, date parsing, and H3 polyfill output.
No Databricks workspace is required, so CI catches regressions for €0.

When we add a new layer (TRI, IGN, climate, portfolio), the corresponding
silver/gold notebooks ship with a matching `test_*_duckdb.py`.

### 8.2 Operational conventions for the ingest pipeline

Three rules govern every fetcher + bronze/silver/gold notebook trio:

1. **Idempotency.** Every notebook uses `CREATE OR REPLACE TABLE` for table writes, `CREATE … IF NOT EXISTS` for namespaces, and parameterized `IDENTIFIER(:catalog || …)` for object names. A re-run on the same inputs produces the same outputs, with no manual cleanup. Bronze tables are append-free — the medallion is a function of the raw volume, not a journal.
2. **Cache-first downloads.** All upstream pulls (Géorisques WFS, BRGM, IGN, Copernicus) write into the `catnat_bronze.raw` UC volume and check that path before touching the network. The cache key is `{layer}_{full|sample}.geojsonl` under a per-source folder (`raw/rga/…`, `raw/ppri/…`). To force a refresh: `--force` on the CLI or `CATNAT_FORCE_FETCH=true` in the environment. This makes the demo runnable offline once the cache is warm, and makes CI cheap.
3. **Operator parameters via `.env`.** The four operator-level knobs (`CATNAT_PROFILE`, `CATNAT_WAREHOUSE_ID`, `CATNAT_CATALOG`, `CATNAT_FORCE_FETCH`) load from a `.env` file in the repo root (template committed as `.env.example`, the real `.env` is git-ignored). Real env vars win over `.env` so CI can override per-job without touching files. `src/catnat/config.py` is the single resolution point; nothing else reads these env vars directly.

These conventions stay constant as we add PPRI, TRI, windstorms, climate, and the synthetic portfolio. Each new source ships as `src/catnat/fetch/<peril>.py` + the matching SQL notebook trio, with no plumbing changes.

---

## 9. Out of scope (explicitly)

- **Real insurer portfolio data** — synthetic only.
- **Raster hazard models** (e.g. flood depth rasters from JBA / RMS) — possible Mosaic extension, but not for v1.
- **Pricing engine integration** — we *show* exposure deltas; we don't recompute premiums.
- **Mobile / tablet UX** — desktop only.
- **Authentication beyond Databricks SSO** — no per-persona role gating in v1; persona is a UI toggle, not an RBAC boundary.
- **Mosaic, Sedona, or third-party spatial libs** — native `ST_*` + H3 only on the warehouse, to keep the analytical story "vanilla Databricks". Lakebase PostGIS is a deliberate exception for tile serving (§10.7): it's a Databricks-managed product, not a third-party library, and `ST_AsMVT` is the canonical PostGIS function for on-demand MVT — there is no native-Databricks equivalent yet.

---

## 10. Decisions

1. **Cloud** — **AWS, region `eu-west-3` (Paris).** Keeps data on French soil; Copernicus / ERA5 are mirrored in-region on the AWS Open Data registry, so no cross-region egress.

2. **Kepler embed** — React **`@kepler.gl/components`** package (not the Jupyter-export style). Heavier bundle, but the agent can push live state changes (layer adds, filter updates, time-cursor moves) into Kepler's Redux store via dispatched actions — required for the chat-driven flow.

3. **MCP transport** — **HTTP/SSE.** Lets the MCP server run as a separate process inside the same Databricks App, scale independently of the frontend, and be reused later by other clients (Claude Code, a future internal CLI). stdio would have forced co-location with the frontend.

4. **Geocoder** — **Cache + proxy** the BAN (Base Adresse Nationale) API. A thin FastAPI proxy in front of `api-adresse.data.gouv.fr`, with a UC-backed cache table (`catnat.silver.geocode_cache` keyed by normalized address hash) for repeat lookups. Stays inside BAN rate limits and survives offline-demo scenarios.

5. **Language convention** — **English everywhere in code, SQL, tool descriptions, comments, docs.** Data stays on French soil (see §8 data residency). The agent itself replies in the user's language — French question → French answer — but the engineering surface is English-only.

6. **Named-storm dataset — DEFERRED OUT OF v1.** The storm/tempête peril stays in the demo narrative (Act 2 still talks about "what just happened"), but for v1 we skip the actual `hazard_storm_footprints` layer to compress scope. When we reinstate it, the chosen source is **Copernicus C3S Windstorm reanalysis** (`sis-european-wind-storm-reanalysis`) as primary; **ERA5 `fg10` 10m wind gust** as fallback. Both are under the **Copernicus licence** (redistributable with attribution).
   - **Why not Météo-France directly:** since 1 Jan 2024 their data is Etalab 2.0 / Licence Ouverte (redistributable), but they do **not** publish a ready-made `event_name + geometry` storm-footprint product — only raw inputs (SYNOP, AROME/ARPEGE grids, vigilance bulletins). Building footprints ourselves is out of scope for this demo.
   - **Coverage check:** C3S catalogue includes Ciarán (Nov 2023), Domingos (Nov 2023), Eunice (Feb 2022) — the storms most likely to come up in Act 2 of the demo script.
   - **Skipped alternatives:** XWS (Reading) stops at 2012, no recent storms; EMS Rapid Mapping is flood/fire-oriented, not windstorm; CatDat / Risk Layer / PERILS / Verisk are proprietary and not redistributable.
   - **Legal artifacts for review:** [CDS dataset licence page](https://cds.climate.copernicus.eu/datasets/sis-european-wind-storm-reanalysis) and the [Etalab 2.0 confirmation on info.gouv.fr](https://www.info.gouv.fr/actualite/meteo-france-la-reutilisation-des-donnees-publiques-devient-gratuite) (useful if we later enrich with Météo-France vigilance bulletins).
   - **Attribution:** include the Copernicus attribution string in the notebook header and in an "About this data" panel inside the app.

7. **Vector tiles via Lakebase PostGIS — Leaflet stays.** Persistent map layers are served as **Mapbox Vector Tiles (MVT)** generated on-demand by `ST_AsMVT` against a **Lakebase Postgres + PostGIS** mirror of `catnat_silver.*`. Implementation pattern is the [`lakebase-vector-tile`](https://github.com/danny-db/lakebase-vector-tile) PoC (Postgres 17 + PostGIS 3.5, `asyncpg` pool, FastAPI route, 5-min in-memory tile cache). The Leaflet pane consumes them with **[`Leaflet.VectorGrid.Protobuf`](https://github.com/Leaflet/Leaflet.VectorGrid)** — no MapLibre / WebGL migration.
   - **Why Lakebase, not the warehouse:** the Databricks SQL warehouse has no `ST_AsMVT` equivalent. Generating MVT bytes in Python (e.g. `mapbox-vector-tile`) from warehouse queries works but adds per-tile warehouse cost and a service surface. PostGIS does it in one round-trip with a battle-tested protobuf encoder.
   - **Why Leaflet, not MapLibre:** the demo's interaction model is layered choropleth + popups + draw tools — Leaflet handles all of it. The Leaflet.VectorGrid plugin renders vector tiles on canvas without forcing a WebGL/MapLibre rewrite of the existing pane, the layer-picker UI, the `applyMapOp` dispatcher, or the chat-driven layer ops. MapLibre's edge (smooth WebGL zoom, 3D extrusions, advanced expression-based styling) doesn't pull its weight for this narrative.
   - **Why a mirror, not a write path:** UC stays the source of truth (Delta, governance, lineage). Lakebase is a read-only projection refreshed by a DAB job; if it gets out of sync we drop and rebuild without touching the analytical layer.
   - **Boundary with §10.5:** Lakebase PostGIS is a deliberate exception to "no third-party spatial libs" — Lakebase is a Databricks-managed product, not an external dependency. We do not use PostGIS for analytical SQL — only for the tile-encoding endpoint.

---

## 11. Success criteria

The demo lands if, after 15 minutes, an insurer exec can credibly say:

> "We could replace our triage spreadsheet + the GIS team's request queue with this, and our underwriters would actually use it."

…and a technical buyer can credibly say:

> "It's all SQL on tables I already govern in Unity Catalog. The LLM didn't go anywhere it wasn't allowed to."

If either statement feels like a stretch at rehearsal, we cut scope, not corners.
