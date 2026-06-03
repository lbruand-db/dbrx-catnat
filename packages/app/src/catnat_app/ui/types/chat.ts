/** UI-side chat types. The wire types from `lib/api.ts` are generated
 * by apx from the FastAPI OpenAPI spec and target the request body
 * shape — these types are the runtime state the UI holds. */

export interface ToolCall {
    /** OpenAI-issued tool call id, used to match `tool_result`. */
    id: string;
    name: string;
    /** Best-effort JSON-decoded arguments. */
    arguments: Record<string, unknown>;
    /** Filled in when the matching `tool_result` event arrives. */
    result?: unknown;
    /** True when the tool raised / returned isError=true. */
    isError?: boolean;
}

export interface ChatTurn {
    id: string;
    role: "user" | "assistant";
    /** Accumulated text. Streams in for assistant turns. */
    text: string;
    /** Tool calls collected during this assistant turn (empty for user turns). */
    toolCalls: ToolCall[];
    /** True from creation until the `done` SSE event arrives. */
    isStreaming: boolean;
    /** Set when an `error` SSE event surfaced for this turn. */
    error?: string;
}

/** Reverse-channel context the FE attaches to every /api/chat POST.
 * Per UI.md §3.2.1 — the agent reads the user's current map view from
 * the system prompt instead of asking for it via a tool call. */
export interface ChatViewport {
    bbox?: [number, number, number, number]; // [min_lon, min_lat, max_lon, max_lat]
    zoom?: number;
    center?: [number, number]; // [lon, lat]
}

export interface ChatActiveLayer {
    layer_id: string;
    row_count?: number;
}

export interface ChatContext {
    viewport?: ChatViewport;
    active_layers?: ChatActiveLayer[];
}

/** Map-mutating operations the agent can emit via the `map_op` SSE event.
 * Mirrors the payloads from `backend/mcp/ui_tools.py`. */
export type MapOp =
    | {
          op: "add_layer";
          layer_id: string;
          peril: string;
          /** Slippy-map tile URL template the FE feeds to
           * `L.vectorGrid.protobuf`. The Lakebase mirror serves these
           * on demand via `/api/tiles/<layer>/{z}/{x}/{y}.pbf`. */
          tile_url: string;
          style: Record<string, unknown>;
          status: "ok";
      }
    | {
          op: "remove_layer";
          layer_id: string;
          status: "ok";
      }
    | {
          op: "zoom_to";
          geom_geojson: GeoJSON.Geometry;
          status: "ok";
      }
    | {
          op: "style_layer";
          layer_id: string;
          style: Record<string, unknown>;
          status: "ok";
      };
