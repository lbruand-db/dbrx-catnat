# UI design — an AI-native GIS for catnat

This document is the design language for the catnat chat-driven map. It
complements [`SPEC.md`](SPEC.md) (the *what* — data model, demo
narrative, build phases) with the *how* of the human ↔ agent ↔ map
loop. Where SPEC.md decides we use Leaflet on top of a Lakebase MVT
serving layer (§10.7), this doc decides how a user actually *talks to*
the system, what an "action" is, what undo means, and how we test all
of it.

The thesis is contrarian: building an "AI-native QGIS" doesn't mean
gluing a chatbox onto QGIS. The 2026 GIS-plugin generation
([QGPT Agent](https://plugins.qgis.org/plugins/qgpt_agent_release/),
[GeoAgent](https://plugins.qgis.org/plugins/geo_agent/),
[Q-LLM](https://github.com/osgeokr/q_llm),
[opengeos/GeoAgent](https://github.com/opengeos/GeoAgent),
[GIS Copilot](https://giscience.psu.edu/gis-copilot-towards-an-autonomous-gis-agent-for-spatial-analysis/))
demonstrates the limit: a sidecar dialog box driving a desktop GIS still
makes the user think in QGIS verbs. We want the opposite — the map and
the conversation share one state machine, and every user action (typed,
clicked, drawn) is a uniform `Command` on that state.

---

## 1. First principles

Four principles. Everything below is a consequence.

### 1.1 The map is the conversation

State lives in one place: the current Leaflet view (active layers,
selection, viewport, drawn geometries, basemap, filters). The
conversation pane is a *log* of how that state got there, not a
parallel memory the agent has to reconcile with. The agent reads from
this state and writes to it; the user reads from it and writes to it.
No second source of truth.

This matches what [agentic-interface](https://insights.theinteractive.studio/beyond-the-chat-agentic-interfaces-inside-your-product)
and [Cursor's Canvas mode](https://thinktoshare.com/blogs/cursor-canvas-feature-explained)
converged on for code: the artifact is the conversation. We're doing
that for spatial.

### 1.2 Bidirectional by default

Forward (chat → map) is mostly built — P4.3 added `add_layer`,
`remove_layer`, `zoom_to`, `style_layer`. Reverse (map → chat) is the
missing half. Every map interaction — clicking a feature, drawing a
polygon, toggling a layer, changing zoom — enriches the context the
agent reads on the *next* user message. Without this, the agent is
half-blind: the user has to type "I clicked the red zone near Lyon" to
tell the agent something it could see for itself. That's busywork.

### 1.3 Commands are atomic, named, reversible

Every action — a tool call from the agent, a layer toggle from the
user, a hotkey — is wrapped in a `Command` with `apply()` and `undo()`.
The history is a stack. Undo rewinds; redo replays. Conversation
checkpoints are named cursors into that stack. This is the same
[command pattern](https://betterprogramming.pub/utilizing-the-command-pattern-to-support-undo-redo-and-history-of-operations-b28fa9d58910)
[Liveblocks](https://liveblocks.io/blog/how-to-build-undo-redo-in-a-multiplayer-environment),
Figma, and Photoshop run on.

### 1.4 Long-running work is observable

Nothing blocks a turn. Anything that won't finish inside a normal
Claude turn (~10 s budget) emits progress events on the same SSE
channel as everything else and can be cancelled, retried, and
recovered if the connection drops.

---

## 2. The interaction triangle

```
                        ┌──────────────┐
                        │     User     │
                        └──┬─────────┬─┘
                  types    │         │   clicks, draws,
                  messages │         │   pans, toggles
                           ▼         ▼
                ┌───────────────┐  ┌───────────────┐
                │ Chat pane     │  │ Leaflet pane  │
                │  (history,    │◀▶│  (map state,  │
                │   streaming   │  │   live state) │
                │   text)       │  │               │
                └─────┬─────────┘  └───────┬───────┘
                      │ POST /api/chat     │ map_op
                      │ messages + context │ events
                      ▼                    ▲
                ┌───────────────────────────┐
                │   Agent runtime + MCP     │
                │ (commands in / out of     │
                │  the shared state)        │
                └───────────────────────────┘
```

Today (end of P4) the **chat → map** edge works. The **map → chat
context** edge is the next big unlock; it's what §3.2 is about.

---

## 3. The bidirectional model in detail

### 3.1 Forward channel (chat → map) — extending what P4.3 shipped

Existing `map_op` events from P4.3:
`add_layer`, `remove_layer`, `style_layer`, `zoom_to`.

Gaps to fill, in roughly demo-impact order:

| Op | Why | Payload sketch |
|---|---|---|
| `add_drawn_geometry` | Agent draws a polygon (e.g. "buffer 5 km") and renders it. | `{geom_geojson, label, style}` |
| `highlight_feature` | Flash a specific feature when answering "where is X?". | `{layer_id, feature_id_or_geom}` |
| `set_basemap` | "Switch to satellite". | `{name: "osm"|"ign"|"satellite"}` |
| `add_marker` | Pin an address-search result, an event epicentre, a portfolio policy. | `{lat, lon, label, icon}` |
| `set_filter` | Restyle a subset ("show only zones rouge"). | `{layer_id, where}` |
| `open_popup` | Open the side-panel for a feature programmatically. | `{layer_id, feature_id}` |
| `clear_drawing` | Sweep the temporary geometries. | `{}` |
| `reset_view` | Drop every command, back to default basemap. | `{}` |

Each is one MCP tool + one map-dispatcher case (see
[`packages/app/src/catnat_app/ui/lib/map-dispatcher.ts`](../packages/app/src/catnat_app/ui/lib/map-dispatcher.ts)
for the current shape).

### 3.2 Reverse channel (map → chat) — the missing half

The single biggest UX gap right now. Two mechanisms.

#### 3.2.1 Implicit context — every POST carries the map state

The FE attaches a `context` block to every `/api/chat` body. The agent
loop folds it into the system prompt before sending to Claude.

```json
{
  "messages": [...],
  "context": {
    "viewport": {
      "bbox": [4.5, 45.4, 5.2, 46.1],
      "zoom": 11,
      "center": [4.85, 45.75]
    },
    "active_layers": [
      {"layer_id": "hazard_ppri_communes", "row_count": 237, "style": "flood-default"},
      {"layer_id": "admin_communes"}
    ],
    "selection": {
      "layer_id": "hazard_ppri_communes",
      "feature": {
        "properties": {"code_insee": "84035", "nom": "Avignon", "zone": "rouge"},
        "geometry_summary": {"type": "Polygon", "bbox": [4.79, 43.91, 4.86, 43.97]}
      }
    },
    "drawn_geometries": [
      {"id": "draw_1", "geom_wkt": "POLYGON((...))", "created_at": "...", "label": "user_drawn"}
    ],
    "last_user_action": {
      "kind": "feature_click",
      "ts": "2026-06-03T07:45:55Z"
    }
  }
}
```

The agent doesn't need a tool call to find out "what is the user
looking at" — it's in the system context. This is the
[viewport-aware-input idea](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10151600)
applied to chat.

#### 3.2.2 Explicit references — `@selection`, `@draw_1`, `@viewport`

Users can refer to map artefacts in their typed message: "what's the
RGA exposure inside `@draw_1`?", "intersect `@selection` with PPRI".
The FE expands these tokens before posting, attaching the resolved
geometry/properties to the message payload.

#### 3.2.3 User-action turns (optional)

When a click feels like a question — e.g. user double-clicks a feature
— the FE optionally inserts a synthetic chat turn ("_User opened
feature INSEE 84035 (Avignon)._"). The agent can respond or stay
silent depending on prompt rules.

Defaults: single click → implicit context only (passive). Double-click
or "Ask the agent about this" button → synthetic turn (active).

### 3.3 Why both directions matter — demo example

Without reverse channel:
> User: "Show me PPRI in Vaucluse"
> Agent: `add_layer(...)` → polygons render
> User: (clicks a red zone)
> User: "C'est quoi ce truc rouge ?"
> Agent: "Which red zone? I can see Avignon, Cavaillon, Apt…"

With reverse channel:
> User: (clicks a red zone)
> User: "C'est quoi ce truc rouge ?"
> Agent: knows the user clicked INSEE 84035 from context, immediately
> answers about Avignon's PPR Inondation approuvé en 2017.

Half the keystrokes. Same code surface — the FE just packs the click
into context.

---

## 4. Commands and shared state

### 4.1 The `Command` type

```typescript
type CommandKind =
  // Forward map ops (agent or user-initiated):
  | "add_layer" | "remove_layer" | "style_layer" | "set_filter"
  | "zoom_to" | "set_basemap" | "highlight_feature"
  | "add_marker" | "add_drawn_geometry" | "clear_drawing"
  | "open_popup" | "close_popup"
  // Reverse / selection state:
  | "select_feature" | "deselect"
  // Meta:
  | "checkpoint" | "reset_view";

interface Command {
  id: string;                              // UUID
  ts: string;                              // ISO timestamp
  source: "user" | "agent";
  kind: CommandKind;
  payload: unknown;                        // shape per kind
  task_id?: string;                        // set if this came from a long-running task
}

interface CommandRunner<S> {
  apply(state: S, cmd: Command): S;
  undo(state: S, cmd: Command, prevState: S): S;
}
```

User UI events and agent tool invocations both produce `Command`s.
That's the entire point: one code path, one history. The FE never
calls Leaflet directly — it dispatches a Command, which the runner
turns into Leaflet calls.

### 4.2 `MapState` shape

```typescript
interface MapState {
  layers: Record<string, LayerState>;      // by layer_id
  selection: FeatureRef | null;
  viewport: { center: [number, number]; zoom: number };
  basemap: "osm" | "ign" | "satellite";
  drawn: DrawnGeometry[];
  filters: Record<string, string>;         // layer_id → where clause
  popup: { layer_id: string; feature_id: string } | null;
}

interface LayerState {
  source: { kind: "vector_tile"; tile_url: string } | { kind: "geojson"; data: GeoJSON.FeatureCollection };
  style: Record<string, unknown>;
  visibility: boolean;
  opacity: number;
  z_index: number;
  agent_added: boolean;                    // agent vs user layer
}
```

Single source of truth, held in a Zustand store on the FE. The
`context` block in §3.2.1 is a projection of this state with the heavy
fields (full geojson, dragged geometries) summarised — the agent
doesn't need the raw bytes, just the references.

### 4.3 Why Command, not Event Sourcing

Event sourcing replays from scratch on every state read — overkill for
a single-tab session. Command pattern with explicit `apply` / `undo`
gives us cheap O(1) undo + redo, matches the Figma / Photoshop / Linear
class of editors, and avoids a whole class of "rebuild from history is
slow" problems. The history is still serialisable for golden-trace
testing (§8.3) and for session persistence (§5.2).

---

## 5. Time travel

### 5.1 Undo / redo

Standard stack-with-pointer:

- Undo: `pointer--`, call `undo(state, cmd, prevState)`.
- Redo: `pointer++`, call `apply(state, cmd)`.
- New command after an undo: truncate ahead of pointer, append.

Surface:
- `Cmd+Z` / `Cmd+Shift+Z` global hotkeys.
- Top-bar arrow with hover-preview ("Undo: `add_layer hazard_ppri_communes`").
- Chat: typing "undo" / "annule" runs the same code path.
- Agent has an `undo()` MCP tool but uses it sparingly — most undo
  flows are user-driven.

Edge cases:
- An undo of `add_layer` removes the layer cleanly; if the agent then
  re-runs `add_layer` for the same `layer_id`, it's a *new* command in
  the stack, not a redo. This avoids the [collaborative undo trap](https://liveblocks.io/blog/how-to-build-undo-redo-in-a-multiplayer-environment)
  of accidentally undoing user actions when agent re-acts.
- A long-running task that hasn't completed yet can't be undone, only
  cancelled. `Command.task_id` tells the runner to refuse undo.

### 5.2 Checkpoints

Conversation-level snapshots — named cursors into the command stack.
Two flavours, both backed by the storage split in §5.4:

- **Auto-checkpoint** after each agent turn finishes (`turn_<n>`).
  Cheap, automatic, lives only in UC Delta. Used for granular
  "restore last 3 turns" undo.
- **Named snapshot** (`/checkpoint <name>` or ⌘S). Explicit user
  action. Writes a full session JSON to a per-user Git repo (§5.4)
  — durable, diffable, shareable.

Operations:

- **Restore to checkpoint** rewinds the stack to that pointer + clears
  forward. Works on either flavour.
- **Fork from here** creates a new `session_id` with a copy of the
  command stack up to the pointer (same pattern as
  [ChatGraPhT's branchable dialogues](https://arxiv.org/pdf/2512.22790)).
  If the checkpoint is a named snapshot, forks materialise as a Git
  branch in the same repo (§5.4.4).

Replay is deterministic given the same starting `MapState`.

### 5.3 Reset

Big red "Reset map" affordance. Drops every command, returns to default
basemap-only view, clears selection / drawings / filters. The agent
sees a synthetic turn "_user reset the map_" so it doesn't continue
referencing layers that no longer exist.

A reset is itself a Command in the stack, so it can be undone like
anything else. Named snapshots (§5.4) are unaffected by reset — they
live in Git, independent of the current session.

### 5.4 Sharing & versioning via Git

Two storage layers, two roles. Mixing them gives the pitch ("we
versioned the analysis like code") without bending Git to be a
runtime-state engine.

| Concern | Where it lives | Cadence |
|---|---|---|
| Active session — every command, autosaved | `catnat_silver.chat_sessions` (UC Delta) | Every command — fast write, no Git in the hot path |
| Named snapshots — "save points" the user explicitly creates | A per-user Git repo (Databricks Repos by default) | User-initiated only — ⌘S or `/checkpoint <message>` |

This is the Jupyter split: the kernel holds *runtime* state, the
notebook file in Git holds *artefact* state. Two persistence models,
clean roles, no merge nightmare. Concurrent editing of the same
session is not supported in v1, which means we never trigger a Git
merge — we never have to define what merging two divergent map
states should mean.

#### 5.4.1 What's in a snapshot

A snapshot is one JSON document committed as a single file
`sessions/<session_id>.catnat.json`:

```json
{
  "schema_version": 1,
  "session_id": "01H...",
  "saved_at": "2026-06-03T07:45:55Z",
  "saved_by": "lucas.bruand@databricks.com",
  "message": "before the storm experiment",
  "parent_snapshot": "01H...prev...",
  "map_state": { /* the §4.2 MapState shape, fully serialised */ },
  "command_log": [ /* every Command since session start */ ]
}
```

`map_state` lets a reader rebuild the live UI in one step;
`command_log` lets a reviewer replay the analysis turn-by-turn or
fork from any earlier point. The two are redundant but cheap — JSON
gzips well, sessions are small (tens of KB even for a busy 30-min
turn).

#### 5.4.2 Where the repo lives

Default: **Databricks Repos**, one repo per user
(`catnat-sessions-<sso_slug>`), created on first save. Gives us IAM,
sharing UI, and storage quotas for free.

Escape hatch: a bare repo on a UC volume
(`catnat_silver.session_repos.<user>/`) for workspaces without Repos
enabled. Same JSON file format; lose the sharing UI, keep the
versioning.

External GitHub / GitLab as the commit target is out of scope for v1
— the PAT plumbing and corporate-firewall edge cases aren't worth the
demo effort. A user can always `git clone` their Repos URL and push
to their own host manually.

#### 5.4.3 Identity rules

- **Only user-authored commits** land in Git. The agent never commits
  silently on the user's behalf — that confuses the audit trail and
  the regulator reading it later.
- Per-command attribution to `"user"` vs `"agent"` lives inside the
  `command_log` JSON (see `Command.source` in §4.1), not in commit
  metadata.
- Commit message defaults to the snapshot label the user supplied; if
  the user omits one, default to a timestamped placeholder
  (`"snapshot 14:32"`).

#### 5.4.4 Branching = forks

"Fork from this snapshot" creates a new branch (`fork/<short_id>`) in
the same repo, with a `parent_snapshot` pointer in the JSON. The user
can keep working on the new branch or switch back. Branches are cheap;
we don't try to limit how many.

Sharing a fork = sharing the branch URL. The recipient gets read-only
access by default (Databricks Repos handles the permission). No PR
review workflow in v1 — branches are for divergence, not merge.

#### 5.4.5 Diff viewer

A small in-app viewer renders `git diff` between two snapshots as a
human-readable changelog grouped by `Command.kind`:

> **Added layers**: hazard_ppri_communes (237 features)
> **Removed layers**: (none)
> **Restyled**: hazard_ppri_communes — fill color #1f77b4 → #ff0000
> **Drew**: 1 polygon (buffer of POINT(4.85 45.75) by 5 km)
> **Agent turns**: 3 (8 features added to the conversation)

The viewer reads the JSON diff and groups changes by category. Power
users can fall back to raw `git diff` in their terminal — it's a real
repo.

#### 5.4.6 What §5.4 explicitly defers

- **Real-time collaboration / concurrent editing** — one writer per
  session in v1. Two users on the same branch is undefined behaviour;
  CRDTs / merge resolution for spatial state are out of scope.
- **External Git hosts** as commit targets — Databricks Repos (or UC
  volume bare repo) only in v1.
- **Auto-commits per command** — Git is reserved for explicit user
  saves. Every command still autosaves to Delta; that's the cheap
  layer.
- **Pull-request review workflow** — branches can be shared, but
  there is no merge UI.

---

## 6. Long-running tasks

### 6.1 What "long" means

Anything that won't finish during a normal Claude turn (~10 s
end-to-end budget). Concretely:

- Full-France queries (millions of rows × multiple peril joins).
- Multi-event spatial intersect (all 6 demo events × portfolio × hazards).
- Tile pre-generation for a heavy layer.
- "Research mode" multi-step agent runs (5+ tool iterations with
  explicit chain-of-thought).

### 6.2 Wire pattern — task events on the same SSE channel

Sync (today's default): agent calls a tool, awaits result, continues.

Async: agent calls `start_task(kind, params)` → server returns a
`task_id` immediately; the task runs in a background `asyncio.TaskGroup`
([structured-concurrency pattern](https://medium.com/@2nick2patel2/fastapi-structured-concurrency-taskgroups-deadlines-and-automatic-cancellation-ed136aa8b8ca));
progress emits on the same SSE channel:

```
event: task_started   data: {task_id, kind, eta_s, started_at}
event: task_progress  data: {task_id, percent, message}
event: task_complete  data: {task_id, result_handle, duration_s}
event: task_failed    data: {task_id, error, retryable}
event: task_cancelled data: {task_id, by: "user" | "timeout"}
```

The FE renders a small **task chip** in the chat pane: collapsible,
status icon, ETA bar, ✕ to cancel. Multiple concurrent tasks each get
their own chip.

### 6.3 Survival across page refresh

Tasks persist in `catnat_silver.tasks` (one row per task, with
`session_id`, `kind`, `state`, `progress_pct`, `result_handle`,
`started_at`, `ended_at`, `error`).

On page load:
1. FE asks `GET /api/sessions/<id>/tasks?state=in_flight`.
2. For each: open a fresh SSE connection
   `GET /api/tasks/<task_id>/events` to resume progress.
3. The server reads the task's progress journal from
   `catnat_silver.task_events` and replays missed events before
   streaming live ones.

This means a 5-minute "intersect all events" query survives the user
closing the laptop and coming back.

### 6.4 Cancellation

Two surfaces:

- The task-chip ✕ button. POSTs `/api/tasks/<task_id>/cancel`.
- Typed "cancel" / "annule {task_id|kind}" in chat — same endpoint.

The server-side TaskGroup gets a `cancel()`; downstream warehouse calls
inherit the cancellation via the SDK's request context. Cleanup is
automatic; the task ends with `task_cancelled`.

### 6.5 What NOT to make async

Anything that can fit in 5 seconds. Async adds complexity (task table,
events, cancellation, recovery), so it must earn its place. The
default is sync; promote to async when measurement says we exceed
budget.

---

## 7. Failure & error model

### 7.1 Failures the user sees

| Failure | Surface | Recovery |
|---|---|---|
| Tool call fails (allowlist denial, SQL error) | tool-call card shows the error text | agent retries with a different argument, or asks the user |
| FMAPI 5xx / rate limit | error banner "agent unavailable, retrying…" | exponential backoff, max 3 retries, then a clear "agent down" message |
| SSE drops | top chip "reconnecting…" + pulsing | reconnect from last-seen `event_id`; resume the in-flight task if any |
| Tile load fails | ❌ placeholder over the affected tile | auto-retry on next pan/zoom; surface "tiles unavailable" if persistent |
| Long-running task fails | `task_failed` event, chip turns red, error text expanded | the agent gets the failure as a tool result, decides whether to retry |

### 7.2 Failures the user doesn't see

- Token refresh in transit (per-request bearer — fixed in commit `7578534`).
- Lakebase tile cache eviction.
- Allowlist denials (logged at INFO, agent retries with the suggested layer).
- The MCP server restarting (idempotent — the FE just reconnects).

### 7.3 Honesty rule (from SPEC §5.5)

The agent never silently empties results. Zero rows → say "aucune
donnée PPRI pour cette commune"; never an empty bullet list. This is
the single biggest UX cheat we refuse to take.

---

## 8. Testing strategy

Five layers, climbing in fidelity and cost. The thesis we adopt is
[deterministic-replay-as-regression-testing](https://tianpan.co/blog/2026-04-12-deterministic-replay-debugging-non-deterministic-ai-agents):
golden traces are the cheapest way to catch behavioural drift when an
LLM model upgrade or a prompt change subtly changes tool order.

### 8.1 Unit tests
Per MCP tool, per command, per store action. Already in place: 52
backend + 39 FE as of commit `7578534`. Run on every PR.

### 8.2 Integration tests
The agent loop with a scripted LLM mock + a stubbed warehouse — also
in place (`test_agent_loop.py`). Verifies the loop calls MCP correctly
and emits the right SSE events for a given LLM stream.

### 8.3 Golden trace tests
The new layer.

1. Capture a real `/api/chat` session as
   `(input_messages, input_context, recorded_llm_stream, observed_sse_events)`.
2. On every PR, replay each trace with the recorded LLM stream against
   the current code.
3. Assert *structural equivalence*: same MCP tools called in the same
   order, same `map_op` kinds emitted, same final `done` payload shape
   (text content checked via an LLM-as-judge sanity pass, not
   byte-equality).

20–30 traces covering the SPEC §6 demo script + edge cases (zero rows,
allowlist denial, long-running task, cancellation, undo). Target
budget: <5 min total per PR. Tooling: replay engine intercepts system
clock and UUID generation so timestamps don't pollute the LLM input
([cite](https://tianpan.co/blog/2026-04-12-deterministic-replay-debugging-non-deterministic-ai-agents)).

### 8.4 Real-LLM smoke
Daily, not per-PR. [`scripts/probe_agent.py`](../packages/app/scripts/probe_agent.py)
already does this for one prompt; expand to 3–5 scripted prompts
covering each Act of SPEC §6 against the live deployed app + FMAPI +
warehouse. Asserts: events arrive, tool calls succeed, `add_layer`
emits a non-empty FeatureCollection (P4.5: a valid tile URL), no token
errors. Slow (~5 min), rate-limit-sensitive — runs as a scheduled GHA
workflow.

### 8.5 Browser E2E
Playwright drives a real browser:

1. Loads the app behind a mock-FMAPI proxy.
2. Types "Affiche les communes du Rhône sur la carte."
3. Waits for the map to update.
4. Asserts: tool-call cards rendered, GeoJSON / vector-tile layer
   visible, `<canvas>` snapshot diffs within tolerance vs golden image.
5. Clicks Undo. Asserts the map reverts.
6. Tests the reverse channel: programmatic click on a feature,
   asserts the next `/api/chat` POST carries it in `context.selection`.

Three scenarios per Act. Nightly against a real workspace.

### 8.6 Chaos
- Kill the backend mid-SSE → FE shows "reconnecting".
- Drop the WebSocket → resume from last `event_id`.
- Emit malformed events → FE skips them; agent loop continues.
- Cancel a long-running task mid-flight → task chip shows cancelled,
  no orphan rows in `tasks` table.

Two scenarios per release, run on demand.

---

## 9. Rollback & migration

### 9.1 Code rollback
Standard `git revert` + `databricks bundle deploy`. The bundle pins a
stable artifact path; rollback is one redeploy.

### 9.2 Data rollback
- UC tables: Delta time-travel (`SELECT * ... VERSION AS OF <n>`).
- Lakebase mirror: `mirror_silver_to_lakebase` is idempotent — re-run
  produces the same state. For a clean restore, manual `DROP SCHEMA
  geo CASCADE` + rerun the job (~minutes for dept 069).
- Layer registry: `catnat_silver.layer_index` is rebuilt from the
  pipeline; rollback by reverting the SQL notebook.

### 9.3 Session rollback (per-user)
Three granularities, all from §5:

- **Operation-level**: undo / redo from the command stack
  (Cmd+Z / Cmd+Shift+Z, §5.1).
- **Turn-level**: restore to an auto-checkpoint
  (`catnat_silver.chat_sessions` in Delta, §5.2).
- **Snapshot-level**: `git checkout <commit>` on a per-user repo —
  durable across sessions, diffable, shareable (§5.4).

There is no "global undo" that can rewind another user — single-user
sessions only.

### 9.4 Feature flags
Risky features (autonomous research mode, async task scheduling, the
explicit `@reference` syntax) ship behind env-var flags:
`CATNAT_FEATURE_RESEARCH_MODE=1`, etc.

- Backend reads them at lifespan startup.
- FE feature-detects via `/api/version` (which already returns build
  metadata).
- Flip off → no FE change needed; the disabled features hide their UI
  affordances automatically.

This is how we de-risk demo day: anything that's not 100% reliable
ships off by default, gets flipped on for rehearsal, and stays off if
it misbehaves.

---

## 10. Non-goals and open questions

### Non-goals (v1)
- **Real-time multi-user collaboration.** Single-user sessions only.
  No cursors, no presence, no CRDTs. Defer to v2+.
- **Mobile / touch.** Desktop-only.
- **Offline mode.** Assumes connectivity. The probe runs locally as a
  diagnostic, not as a user-facing offline shell.
- **Plugin SDK.** No third-party tool surface in v1; the MCP server is
  ours.
- **Voice input / output.** Out of scope.
- **Generative cartography** (auto-styled posters, exported atlases) —
  fun but not the demo narrative.

### Open questions (decide during P4.5 / P5)
- **Should the agent ever speak unprompted?** E.g. "I notice you've
  been panning around Lyon — want me to add the PPRI layer?". Lean:
  no for v1, optional opt-in later. Surprise-talk feels Clippy-grade
  bad until proven otherwise.
  Yes, clippy bad. forget about it.
- **Drawn geometries persistence across sessions?** ✓ Decided in §5.4.
  Drawings live inside the session JSON, persisted to Delta on every
  command and to a per-user Git repo on user-requested save. Sharing
  = sharing the repo / branch.
- **Inline mini-map previews of features the agent references?**
  When the agent says "in Avignon", should the chat render a tiny
  thumbnail? Lean: yes for `feature_id` references, no for
  free-text geographical names (too easy to be wrong).
- **Undo turn vs undo command.** ✓ Decided in §5.2. Granular undo
  stays the default (Cmd+Z); turn boundaries are explicitly modelled
  as auto-checkpoints, so "restore last turn" is a separate
  affordance, not a Cmd+Z multi-press.
- **Branching conversations as a first-class UX?** ✓ Decided in
  §5.4.4. Branches are real Git branches in the per-user repo. No PR
  merge UI in v1 — branches are for divergence, sharing is a URL.
- **Pinned layers across resets** — open. A "pin layer" affordance
  marks a `LayerState.pinned = true` so reset skips it. Persistence
  follows the same model as drawings (§5.4). Defer the UI; the data
  model is ready.

---

## 11. Phasing

This doc is the design language. Implementation is rolled into the
SPEC.md §7 phase table:

| Phase | UI.md scope |
|---|---|
| P4.3 (done) | Forward channel — `add_layer` / `remove_layer` / `zoom_to` / `style_layer`. |
| P4.5 | Lakebase tile serving — `add_layer` switches to tile-URL payload (§3.1). No UI.md changes required. |
| P5 | Reverse channel implicit context (§3.2.1) + extended forward ops (§3.1 gaps). Explicit `@reference` syntax. Auto-checkpoint to Delta after every agent turn (§5.2). |
| P6 | Undo / redo from the command stack (§5.1). Named-snapshot save & restore to per-user Git repo (§5.4), incl. branch / fork UI and the diff viewer. Long-running task pattern (§6) for the multi-event intersect demo step. Golden trace test harness (§8.3). |
| Post-v1 | Real-time collaboration, external Git hosts, pull-request review workflow, voice, mobile. |

---

## References

External reading that informed this design. All accessed 2026-06-03.

### AI-native GIS landscape
- [QGPT Agent — QGIS LLM plugin](https://plugins.qgis.org/plugins/qgpt_agent_release/)
- [GeoAgent — QGIS plugin for LLM-driven analysis](https://plugins.qgis.org/plugins/geo_agent/)
- [Q-LLM — Gemma 3 / QGIS integration](https://github.com/osgeokr/q_llm)
- [opengeos/GeoAgent — multimodal AI agent for geospatial data](https://github.com/opengeos/GeoAgent)
- [GIS Copilot — autonomous GIS agent for spatial analysis](https://giscience.psu.edu/gis-copilot-towards-an-autonomous-gis-agent-for-spatial-analysis/)
- [GeoJSON Agents: Function Calling vs Code Generation (arxiv)](https://arxiv.org/pdf/2509.08863)
- [ArcGIS Maps SDK — agentic mapping intro](https://developers.arcgis.com/javascript/latest/agentic-apps/ai-introduction/)
- [ChatGraPhT — branchable LLM conversations (arxiv)](https://arxiv.org/pdf/2512.22790)

### Bidirectional chat ↔ canvas patterns
- [MCP Apps — bidirectional UI in AI clients (WorkOS, Jan 2026)](https://workos.com/blog/2026-01-27-mcp-apps)
- [Cursor Canvas feature (April 2026)](https://thinktoshare.com/blogs/cursor-canvas-feature-explained)
- [Figma's March 2026 update — AI agent design-to-dev](https://palettt.com/blog/figmas-march-2026-update-how-ai-agents-are-redefining-design-to-dev-handoff)
- [Beyond the chat — agentic interfaces inside your product](https://insights.theinteractive.studio/beyond-the-chat-agentic-interfaces-inside-your-product)
- [Closing the agentic design loop — viewport-aware screenshots](https://dev.to/ashmortar/the-eyes-have-it-closing-the-agentic-design-loop-3819)
- [Put-that-there revisited — context-aware window interactions (arxiv)](https://arxiv.org/pdf/2511.02378)

### Undo / redo / collaborative state
- [Liveblocks — undo/redo in collaborative apps](https://liveblocks.io/blog/how-to-build-undo-redo-in-a-multiplayer-environment)
- [Command pattern for undo/redo (Better Programming)](https://betterprogramming.pub/utilizing-the-command-pattern-to-support-undo-redo-and-history-of-operations-b28fa9d58910)
- [Approaches to undo and redo — Nutrient](https://www.nutrient.io/blog/approaches-to-undo-and-redo/)
- [You Don't Know Undo/Redo (DEV community)](https://dev.to/isaachagoel/you-dont-know-undoredo-4hol)
- [Designing a lightweight undo history with TypeScript](https://www.jitblox.com/blog/designing-a-lightweight-undo-history-with-typescript)

### Long-running tasks / SSE / cancellation
- [FastAPI Server-Sent Events tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/)
- [FastAPI structured concurrency, TaskGroups, deadlines, cancellation](https://medium.com/@2nick2patel2/fastapi-structured-concurrency-taskgroups-deadlines-and-automatic-cancellation-ed136aa8b8ca)
- [Managing background tasks and long-running operations in FastAPI](https://leapcell.io/blog/managing-background-tasks-and-long-running-operations-in-fastapi)
- [Python background tasks — asyncio traps, FastAPI & Celery (2026)](https://dev.to/kaushikcoderpy/python-background-tasks-asyncio-traps-fastapi-celery-2026-381i)

### Testing LLM apps
- [Deterministic replay — debugging non-deterministic agents (TianPan.co)](https://tianpan.co/blog/2026-04-12-deterministic-replay-debugging-non-deterministic-ai-agents)
- [Agent evaluation — tools, trajectories, LLM-as-judge](https://medium.com/@vinodkrane/chapter-8-agent-evaluation-for-llms-how-to-test-tools-trajectories-and-llm-as-judge-788f6f3e0d52)
- [Trustworthy AI Agents: Deterministic Replay (Sakura Sky)](https://www.sakurasky.com/blog/missing-primitives-for-trustworthy-ai-part-8/)
- [LLM agent evaluation metrics — tool calling, task completion, trace-based evals](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide)
- [Agent observability — complete guide for 2026 (Braintrust)](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026)
