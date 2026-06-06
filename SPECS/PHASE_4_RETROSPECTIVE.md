# Phase 4 + 4.5 — retrospective

The agent loop (Claude over FMAPI ⇄ MCP) and the Lakebase tile-serving
mirror shipped together — they share enough surface (the FE's map
handle, the `add_layer` payload shape) that splitting their retros
would just duplicate context. End-state, decisions, sharp edges, lessons.

## What landed

| Surface | Path | What it does |
|---|---|---|
| **Agent loop** | `packages/app/src/catnat_app/backend/agent/loop.py` | One turn = Claude streams ⇄ MCP calls until either `done` (no more tool calls) or `MAX_ITERATIONS=8`. Emits SSE events (`tool_call`, `tool_result`, `map_op`, `delta`, `done`, `error`) the FastAPI route relays as `/api/chat`. |
| **FMAPI client** | `packages/app/src/catnat_app/backend/agent/client.py` | `AsyncOpenAI` pointed at the workspace's serving endpoint (`databricks-claude-sonnet-4-6` by default). `httpx.Auth` swap refreshes the SDK-minted bearer on every request so a long session doesn't hit the 1-hour expiry. |
| **UI-mutating MCP tools** | `packages/app/src/catnat_app/backend/mcp/ui_tools.py` | `add_layer`, `remove_layer`, `style_layer`, `zoom_to` — each returns a payload with `op` that the loop splits into a `map_op` SSE event for the FE plus a slim LLM-side summary. |
| **Reverse channel** | `loop.py::_format_context`, FE `getChatContext()` | Viewport, agent-added layers, click-selected feature folded into the system prompt every turn so "ce truc / cette zone" resolves correctly without a tool call. |
| **Lakebase mirror** | `src/catnat/{lakebase,mirror}.py` | Daily DAB job (`catnat-job mirror`) copies every displayable silver polygon layer → Lakebase Postgres `geo.<layer_id>` with GIST index. Chases `next_chunk_index`; asserts `len(rows) == manifest.total_row_count` to make partial reads fail loud. |
| **Tile endpoint** | `packages/app/src/catnat_app/backend/tiles.py` | `/api/tiles/<layer>/{z}/{x}/{y}.pbf` runs `ST_AsMVT` + `ST_AsMVTGeom` against the mirror. 5-min LRU cache; per-request `asyncpg` connect (no pool yet — P6 polish). |
| **Vector-tile rendering** | `packages/app/src/catnat_app/ui/lib/map-dispatcher.ts` | FE consumes `{tile_url, style}` from `add_layer` via `L.vectorGrid.protobuf`. Click handler emits the selected feature into the reverse-channel context. |
| **Testing** | `tests/test_agent_*.py`, `tests/test_tiles_endpoint.py`, `tests/test_golden_traces.py`, `e2e/chat.spec.ts` | 62 backend + 44 FE + 1 Playwright happy-path. Golden-trace harness pins the loop's event sequence deterministically — recorder via `scripts/probe_agent.py --record`, replay via `tests/test_golden_traces.py`. |

## Decisions worth remembering

1. **One SSE stream, not two channels.** The agent loop emits both LLM events (`delta`, `done`) and UI events (`map_op`) on the same `/api/chat` SSE pipe. The FE applies map ops as soon as they arrive — no separate WebSocket, no extra plumbing. Tool calls that don't mutate the UI (`query_layer`, `intersect_layer`) just don't emit `map_op`.
2. **MCP tool result splitting at the loop boundary.** `_split_ui_payload` recognises a payload as UI-mutating when its inner dict carries an `op` key, then strips `geojson` / `geom_geojson` before forwarding to the LLM. Saves megabytes of feature data per tool call from re-entering the next iteration's prompt.
3. **`add_layer` returns MVT URLs, not GeoJSON.** A single GeoJSON FeatureCollection for the national RGA layer would have been hundreds of MB inline. The FE renders via `L.vectorGrid.protobuf` against `/api/tiles/...`; the LLM gets `{layer_id, row_count, peril}` instead of features.
4. **Reverse-channel context goes in the system prompt, not as a tool call.** Every turn carries the FE's snapshot of viewport + active layers + click-selected feature. The model reads it for free — no `get_viewport()` tool, no latency. Saves 2-3 tool round-trips on every "show me this around here" prompt.
5. **`asyncpg` over psycopg.** Lakebase OAuth tokens are short-lived; we ask the SDK for one per request via `lakebase.connect(ws)` and pass it as the connection password. Async cleanly composes with FastAPI's async route handlers; we never block on synchronous I/O.
6. **Mirror = mirror, not source-of-truth.** Lakebase is the rendering substrate; UC remains the catalogue. Tile endpoint reads Lakebase, MCP tools read UC. Re-running the mirror is `DROP CASCADE` + `CREATE` per layer — idempotent, safe to re-run after any silver refresh.
7. **Schema cheat sheet, ground-truthed.** The system prompt carries the *exact* column names for each displayable layer (`susceptibility_code` vs `susceptibility_label`, `code_dep` "69" vs `dept` "D069"). Hand-written from `DESCRIBE TABLE` output — the model used to hallucinate columns that didn't exist and the queries silently returned zero rows.

## Things we tripped on (and the workarounds)

Documented for P5+ — most of these would have eaten an afternoon if hit fresh.

| Surface | Symptom | Fix |
|---|---|---|
| **OBO `sql`-scope tokens never minted** | Despite `user_api_scopes: ['sql']` and a SUCCEEDED deployment, the user-token reaching `/api/sql` on the workspace gateway had no `sql` scope. Affected `/api/layers` and (initially) the agent's `_sql_client`. | Switched the SQL handle to the **app SP** (`AppSqlDependency`) and granted `USE CATALOG` + `USE SCHEMA` + `SELECT` to the SP on `catnat_silver`/`catnat_gold`. Loses per-user audit on SQL but unblocks the demo. File a Databricks Apps platform ticket if per-user is ever required. |
| **FMAPI rejects empty tool arguments** | Claude emits `"arguments": ""` when a tool takes no args (e.g., `list_layers`); FMAPI's request validator rejects the next iteration's payload as invalid. | Normalise `""` → `"{}"` in `loop.py` before appending tool_calls to the history. |
| **FMAPI bearer expires after ~1 h** | Long demo sessions saw `Invalid Token` after the first tool round-trip past the hour mark. | `_DatabricksTokenAuth(httpx.Auth)` re-asks the SDK for a fresh bearer on every request, swap-cheap because the SDK caches valid tokens. |
| **SSE buffering through the Apps gateway** | First demo run: nothing arrived in the browser for 13s, then *everything* landed at once. Loop was emitting on time; gateway was buffering. | Added `Cache-Control: no-cache, no-transform` + `X-Accel-Buffering: no` headers to the SSE response. |
| **`query_layer` ⇒ 2.6 M-token prompt** | `query_layer` originally projected full GeoJSON for every row; a 5-row PPRI sample sent the whole next-iteration system prompt past the model's context. | `_projection(layer, geom_mode="bbox")` projects `ST_XMin/YMin/XMax/YMax` instead of `ST_AsGeoJSON`. 4 floats per row, the LLM still knows where each feature is for follow-up reasoning. `intersect_layer` keeps full GeoJSON — that's where the user explicitly wants shapes. |
| **Mirror silently lost 242 / 496 communes** | `admin_communes` reported 254 rows in Lakebase against a known 496. The Statement Execution API splits results into chunks; we read only the first. The FE rendered what it got, the agent confidently said "237 features", no error anywhere. | Chase `next_chunk_index` to drain every chunk; **assert** `len(rows) == manifest.total_row_count` and raise loudly on mismatch. Turns a silent geographic-data hole into a hard mirror failure. |
| **National RGA blew the 26 MB inline-result cap** | `disposition=INLINE` (default) caps result size at 26 MB. National RGA WKT is ~1 GB. | `LAYERS_NEEDING_BBOX_SCOPE = frozenset({"hazard_rga_susceptibility"})` adds a per-layer `ST_Intersects(geom, ST_GeomFromText(:bbox, 4326))` at read time. Long-term fix is `disposition=EXTERNAL_LINKS` (P6 polish). |
| **Hand-typed cheat sheet was wrong** | Prompted `susceptibility_level` (doesn't exist) vs `susceptibility_code` (does), `code_dep` "69" (admin_communes) vs `dept` "D069" (same table, different column). Queries silently returned zero rows. | Re-derived from `DESCRIBE TABLE` and pinned via the schema cheat sheet in `agent/prompts.py`. Don't trust LLM intuition for column names. |
| **React 19 + Kepler `useMemo` null crash** | Kepler.gl 3.3.0-alpha depends on `react-palm` → `react-reconciler@0.12` (React 16's reconciler). Mounting the Kepler pane threw `null.useMemo` on React 19. | Pinned React 18.3.1; removed the Kepler pane (the analytical view didn't earn its weight). `react` / `react-dom` resolutions ensure no transitive 19.x sneaks back in. |
| **Empty arguments + JSON parser** | `args_json = ""` → `json.loads("")` → JSONDecodeError. | `args = json.loads(args_json) if args_json else {}` before the tool dispatch. |

## What we deliberately deferred

- **Connection pooling for Lakebase.** Tile endpoint connects per request; fine at demo concurrency (<10 RPS). P6 polish item — `asyncpg.create_pool` + dependency-injected handle.
- **`disposition=EXTERNAL_LINKS` for the mirror.** Per-layer bbox-scope is a 30-line workaround that handles the one layer over the limit. Migrating to paginated external links is a P6 task; today's footprint doesn't need it.
- **Undo / checkpoints.** SPEC §5.4 mentioned a Git-snapshot persistence model for map state. Out of v1 — the demo runs one act at a time and a page refresh is acceptable as undo.
- **Storm footprints.** Deferred since P0 (SPEC §10.6) — Météo-France doesn't publish event polygons; legal review on Copernicus C3S wasn't worth the time before v1.
- **`portfolio_claims`.** Needs event-specific overlap logic; Act 2 of the demo narrative referenced it but works against `events` × `portfolio_policies` without it for v1.
- **Spilling overflow MCP results to session-scoped UC tables** (SPEC §5.4). 500-row inline cap is enough for the agent to iterate on filters; spill is P6.

## Metrics

| | Count | Notes |
|---|---:|---|
| Backend tests (`packages/app/tests/`) | 62 | agent loop (10), agent client (3), chat endpoint (2), layers endpoint (3), MCP allowlist (5), MCP list_layers (3), MCP SQL templates (14), MCP tools (6), MCP UI tools (9), tiles endpoint (7), golden traces (1) |
| FE tests (Vitest + RTL) | 44 | covering chat state machine, map dispatcher, tool-call cards, SSE parser |
| Browser E2E (Playwright) | 1 | happy-path: prompt → tool-call card → assistant text. Network fully stubbed at `page.route()` — no real backend |
| Commits P4 start → P4.5 close | ~20 | from `51d8f50` (P4.1 backend loop) to `a12e15d` (national RGA mirror) |
| Hazard layers tile-served | 3 | RGA (national, bbox-scoped), PPRI (national), TRI (national); plus `admin_communes` reference layer |
| Mirror runtime | ~3 min | dominated by national RGA read + insert; chunk-pagination chase + GIST index build |
| Tile cold hit | ~150 ms | PostGIS `ST_AsMVT` over GIST; warm cache <5 ms |

## Lessons for Phase 5+

- **The MCP-as-tool-router pattern scales.** Adding `ask_genie` for P5 should be a single new tool registration + a thin Genie-API call wrapper. The loop already handles streaming responses through any tool; nothing in the agent code needs to know whether a tool delegates to PostGIS, the warehouse, or Genie.
- **Schema cheat sheets need re-grounding when columns change.** If P5 introduces new tables for portfolio Q&A, ground-truth the column names from `DESCRIBE TABLE` before writing prompt text. The class of "agent confidently runs a query that returns 0 rows because a column doesn't exist" bug burns demos.
- **Golden traces > unit tests for prompt-shape changes.** Unit tests in `test_agent_loop.py` cover each piece in isolation; the golden-trace harness pins the integrated event sequence. When you change the system prompt or add a tool, re-record a trace — divergence from the recording is exactly what you want to see.
- **The reverse-channel context block is cheap and high-leverage.** Every chunk of UI state we can fold into the system prompt is a tool call we save. P5 should keep this discipline: add to the context block before adding a new tool.
- **The Lakebase mirror is the right shape, even if `EXTERNAL_LINKS` is the right transport long-term.** UC stays the catalogue; Postgres serves tiles. The bug class is "silent partial mirror" → assert manifest counts, and any future transport change preserves the invariant.

---

Phases 4 + 4.5 closed. Ready for Phase 5 (Genie integration) per SPEC §7.
