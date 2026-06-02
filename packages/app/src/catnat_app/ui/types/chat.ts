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
