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
┌─────────────────────────────────────────────────────────────────────────┐
│  Databricks App (Node/React + FastAPI)                                  │
│  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │
│  │  Leaflet pane    │  │  Kepler.gl pane    │  │  Chat / Agent pane  │  │
│  │  (operational)   │  │  (analytical)      │  │  (NL → actions)     │  │
│  └────────┬─────────┘  └──────────┬─────────┘  └──────────┬──────────┘  │
│           │  layer ops             │  view configs         │             │
│           └────────────┬───────────┴───────────────────────┘             │
│                        │                                                 │
│                  Agent runtime (Claude via Foundation Model API)         │
│                        │                                                 │
│                  MCP server (stdio/HTTP) exposing tools:                 │
│                    • list_layers / add_layer / remove_layer              │
│                    • query_layer (spatial SQL)                           │
│                    • buffer / intersect / nearest                        │
│                    • zoom_to / filter_attributes                         │
│                    • run_genie (portfolio Q&A)                           │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
        ┌─────▼──────┐                ┌──────▼──────┐
        │ Databricks │                │   Genie     │
        │ SQL WH     │                │   Space     │
        │ (Photon,   │                │ (curated    │
        │  ST_/H3)   │                │  semantic)  │
        └─────┬──────┘                └──────┬──────┘
              │                              │
        ┌─────▼──────────────────────────────▼──────┐
        │           Unity Catalog                   │
        │  ┌─────────────────────────────────────┐  │
        │  │  catnat.bronze  (raw ingests)       │  │
        │  │  catnat.silver  (typed, geo-tidy)   │  │
        │  │  catnat.gold    (H3-indexed marts)  │  │
        │  └─────────────────────────────────────┘  │
        └───────────────────────────────────────────┘
```

### Why these choices

- **Databricks SQL + Photon** for spatial: native `ST_*` functions and **H3** indexing land in GA; no Mosaic dependency required for the core demo. (Mosaic stays optional for raster overlays.)
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
| `hazard_ppri_flood_zones` | Géorisques (PPRI) | Polygon per zone, per commune | Officially regulated flood zones |
| `hazard_tri_flood` | Géorisques (TRI — Territoires à Risque Important) | Polygon | Modeled flood footprints, 3 return periods |
| `hazard_rga_susceptibility` | BRGM (Géorisques) | Polygon, 4 levels (faible→fort) | Clay shrinkage exposure |
| `hazard_storm_footprints` | C3S Windstorm reanalysis (`sis-european-wind-storm-reanalysis`) + ERA5 `fg10` fallback | Polygon per event | Time-stamped; covers Ciarán, Domingos, Eunice. Copernicus licence — redistributable with attribution. |
| `hazard_climate_rcp` | DRIAS / Copernicus | H3 cell × peril × scenario | RCP 4.5 / 8.5 deltas |
| `admin_communes` + reference layers (buildings, addresses, hydrography, transport) | IGN **BD TOPO v3.5** via [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks) | 60 layers across 9 INSPIRE themes | Pre-built loader. See §4.4. |

### 4.2 Portfolio (synthetic for the demo)

| Table | Grain | Notes |
|---|---|---|
| `portfolio_policies` | One row per policy | Address, geocoded lat/lon, H3 (r=9), insured value, peril coverage |
| `portfolio_claims` | One row per claim | Linked to policy + event, opened/closed dates, paid amount |
| `events` | One row per CatNat event | Type, declared date, JO publication, affected communes |

**Why synthetic:** real insurer portfolios are not available in time and not needed for the narrative. We generate ~500k policies weighted by INSEE population density and a few "hot" zones (Vaucluse / Gard for flood, Île-de-France for RGA) so the heatmaps tell a story.

### 4.3 H3 indexing convention

- **Resolution 9** (~150m edge) for policy points → fast joins to gridded hazard.
- **Resolution 7** (~1.2km edge) for national aggregates and Kepler hex layers.
- Hazard polygons pre-decomposed to H3 cells in gold for sub-second joins.

### 4.4 Upstream loaders we reuse

We don't rewrite ingestion plumbing where a sibling project already does it well.

- **[`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks)** — loads **IGN BD TOPO v3.5** (the French national topographic dataset — 60 layers across 9 INSPIRE themes: admin, addresses, buildings, hydrography, land cover, named places, public services, transport, regulated zones) into Unity Catalog Delta tables with native `GEOMETRY(4326)`. Server-side `ST_Transform` from Lambert-93 (EPSG:2154) → WGS84 (EPSG:4326), parallel per-department ingest via `for_each_task`, bilingual (FR/EN) table and column comments from the official IGN data model, deployable from a Databricks Asset Bundle. Replaces the original ADMIN-EXPRESS plan and gives us a much richer reference layer set for free — notably building footprints (useful for policy geocoding) and hydrography (useful as flood-context overlay).
  - **Integration pattern:** deploy `dbtopo-bricks` to the same workspace as a separate bundle, target its `prod` schema, then have our `catnat.silver.*` layer point at its tables via UC views (no data copy). Our own bundle adds only the CatNat-specific layers (PPRI / TRI / RGA / windstorms / synthetic portfolio).
  - **Impact on Phase 0:** P0 estimate in §7 drops by ~1 day — no need to write our own IGN ingest.

---

## 5. Functional requirements

### 5.1 Map UI (Leaflet pane)

- Base layers: OSM, IGN Plan, satellite.
- Layer panel populated from `catnat.gold.*` tables flagged `is_displayable=true`.
- Per-layer controls: visibility, opacity, color ramp (for choropleth).
- Click on feature → side panel with attributes + "Ask the agent about this".
- **Draw tools**: point, polygon, rectangle — drawn geometry becomes a queryable input the chat can reference ("show RGA in this polygon").
- Address search (geocoder: BAN — Base Adresse Nationale, free API).
- Vector tile rendering for the big polygon layers; non-tiled GeoJSON for the small/event ones.

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
| **P0 — Data foundation** | ~2 days | Bronze ingests (Géorisques PPRI/TRI/RGA, C3S windstorms, synthetic portfolio); Silver typing + geometry validity; Gold H3 marts. IGN reference layers come from [`dbtopo-bricks`](https://github.com/lbruand-db/dbtopo-bricks) (see §4.4). Notebooks in `notebooks/`. |
| **P1 — Spatial SQL layer** | ~1 day | UC views per layer; performance benchmarks (target: <1s for any single-layer point-in-polygon at portfolio scale on a Small SQL WH). |
| **P2 — Databricks App scaffold** | ~2 days | FastAPI backend + React frontend; Leaflet pane wired to UC via SQL Statement Execution API; Kepler pane with one hard-coded view. |
| **P3 — MCP server** | ~2 days | Tool implementations against UC; layer allowlist; session-scoped result tables. |
| **P4 — Agent integration** | ~2 days | Claude (Foundation Model API) wired to MCP; system prompt + suggested prompts; streaming tool calls in UI. |
| **P5 — Genie integration** | ~1 day | Genie space curated for portfolio Q&A; `ask_genie` tool. |
| **P6 — Demo polish** | ~2 days | Three act scripts rehearsed; failure-mode fallbacks; one pre-recorded backup. |

**Total:** ~12 working days for one builder; ~6 days with two builders working frontend/backend in parallel.

---

## 8. Non-functional requirements

- **Latency budget**, per chat turn: agent first token ≤ 2s; first map update ≤ 5s; full Kepler view ≤ 10s.
- **Warehouse:** Small Serverless SQL WH is the demo target — if a query needs Medium, the underlying table layout is wrong.
- **Cost target** for a 30-min demo session: < €5 (mostly SQL WH idle + FMAPI tokens).
- **Reproducibility:** the full stack is deployable from `databricks bundle deploy` against a fresh workspace.
- **Governance posture:** every chat turn that ran a query is loggable to a UC audit table — sellable as a differentiator vs. shadow-IT QGIS workflows.
- **Data residency:** all data stays on French / EU soil. Target workspace is **AWS `eu-west-3` (Paris)**; Copernicus / ERA5 sources are staged from the EU mirrors of the AWS Open Data registry to avoid cross-region egress. No data leaves the EU at any stage of the pipeline.
- **Language convention:** code, SQL, table/column names, MCP tool descriptions, agent system prompt, comments, and docs are all in **English**. The agent renders user-facing responses in the language of the question (French in / French out, English in / English out).

---

## 9. Out of scope (explicitly)

- **Real insurer portfolio data** — synthetic only.
- **Raster hazard models** (e.g. flood depth rasters from JBA / RMS) — possible Mosaic extension, but not for v1.
- **Pricing engine integration** — we *show* exposure deltas; we don't recompute premiums.
- **Mobile / tablet UX** — desktop only.
- **Authentication beyond Databricks SSO** — no per-persona role gating in v1; persona is a UI toggle, not an RBAC boundary.
- **Mosaic, Sedona, or third-party spatial libs** — native `ST_*` + H3 only, to keep the story "vanilla Databricks".

---

## 10. Decisions

1. **Cloud** — **AWS, region `eu-west-3` (Paris).** Keeps data on French soil; Copernicus / ERA5 are mirrored in-region on the AWS Open Data registry, so no cross-region egress.

2. **Kepler embed** — React **`@kepler.gl/components`** package (not the Jupyter-export style). Heavier bundle, but the agent can push live state changes (layer adds, filter updates, time-cursor moves) into Kepler's Redux store via dispatched actions — required for the chat-driven flow.

3. **MCP transport** — **HTTP/SSE.** Lets the MCP server run as a separate process inside the same Databricks App, scale independently of the frontend, and be reused later by other clients (Claude Code, a future internal CLI). stdio would have forced co-location with the frontend.

4. **Geocoder** — **Cache + proxy** the BAN (Base Adresse Nationale) API. A thin FastAPI proxy in front of `api-adresse.data.gouv.fr`, with a UC-backed cache table (`catnat.silver.geocode_cache` keyed by normalized address hash) for repeat lookups. Stays inside BAN rate limits and survives offline-demo scenarios.

5. **Language convention** — **English everywhere in code, SQL, tool descriptions, comments, docs.** Data stays on French soil (see §8 data residency). The agent itself replies in the user's language — French question → French answer — but the engineering surface is English-only.

6. **Named-storm dataset** — **Copernicus C3S Windstorm reanalysis** (`sis-european-wind-storm-reanalysis`) as primary; **ERA5 `fg10` 10m wind gust** as fallback for events not yet in the catalogue. Both are under the **Copernicus licence** (redistributable with attribution), so they ship inside a customer-runnable demo cleanly.
   - **Why not Météo-France directly:** since 1 Jan 2024 their data is Etalab 2.0 / Licence Ouverte (redistributable), but they do **not** publish a ready-made `event_name + geometry` storm-footprint product — only raw inputs (SYNOP, AROME/ARPEGE grids, vigilance bulletins). Building footprints ourselves is out of scope for this demo.
   - **Coverage check:** C3S catalogue includes Ciarán (Nov 2023), Domingos (Nov 2023), Eunice (Feb 2022) — the storms most likely to come up in Act 2 of the demo script.
   - **Skipped alternatives:** XWS (Reading) stops at 2012, no recent storms; EMS Rapid Mapping is flood/fire-oriented, not windstorm; CatDat / Risk Layer / PERILS / Verisk are proprietary and not redistributable.
   - **Legal artifacts for review:** [CDS dataset licence page](https://cds.climate.copernicus.eu/datasets/sis-european-wind-storm-reanalysis) and the [Etalab 2.0 confirmation on info.gouv.fr](https://www.info.gouv.fr/actualite/meteo-france-la-reutilisation-des-donnees-publiques-devient-gratuite) (useful if we later enrich with Météo-France vigilance bulletins).
   - **Attribution:** include the Copernicus attribution string in the notebook header and in an "About this data" panel inside the app.

---

## 11. Success criteria

The demo lands if, after 15 minutes, an insurer exec can credibly say:

> "We could replace our triage spreadsheet + the GIS team's request queue with this, and our underwriters would actually use it."

…and a technical buyer can credibly say:

> "It's all SQL on tables I already govern in Unity Catalog. The LLM didn't go anywhere it wasn't allowed to."

If either statement feels like a stretch at rehearsal, we cut scope, not corners.
