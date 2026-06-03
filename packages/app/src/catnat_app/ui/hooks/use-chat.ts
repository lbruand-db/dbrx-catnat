import { useCallback, useRef, useState } from "react";
import { parseSseStream } from "@/lib/sse";
import type { ChatContext, ChatTurn, MapOp, ToolCall } from "@/types/chat";

/**
 * Talks to `/api/chat` over SSE. Holds the conversation as a list of
 * `ChatTurn` records and mutates the in-flight assistant turn as
 * delta/tool_call/tool_result/map_op events arrive.
 *
 * The hook is intentionally state-machine-thin: every event maps to one
 * `setTurns` call. React batches the re-renders. `map_op` events fire
 * the optional `onMapOp` callback so a parent can mutate the Leaflet
 * map handle without coupling the hook to a map instance.
 */
export interface UseChatOptions {
    /** Invoked for every `map_op` SSE event. Receives the fully-typed payload. */
    onMapOp?: (op: MapOp) => void;
    /** Called at send time to read the current map view. Returns the
     * reverse-channel context the agent will fold into its system
     * prompt (UI.md §3.2.1). Null/undefined → no context attached. */
    getContext?: () => ChatContext | null | undefined;
}

export interface UseChatResult {
    turns: ChatTurn[];
    send: (text: string) => Promise<void>;
    isStreaming: boolean;
    error: string | null;
}

const CHAT_URL = "/api/chat";

export function useChat(options?: UseChatOptions): UseChatResult {
    const [turns, setTurns] = useState<ChatTurn[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /** Keep a ref to the latest turns so `send` can build the API payload
     * from a consistent snapshot, not a stale closure capture. */
    const turnsRef = useRef<ChatTurn[]>([]);
    turnsRef.current = turns;
    /** Hold the latest callbacks in refs so `send` doesn't need to depend
     * on them (which would invalidate the memoised function on each render). */
    const onMapOpRef = useRef<UseChatOptions["onMapOp"]>(options?.onMapOp);
    onMapOpRef.current = options?.onMapOp;
    const getContextRef = useRef<UseChatOptions["getContext"]>(options?.getContext);
    getContextRef.current = options?.getContext;

    const send = useCallback(async (text: string) => {
        const trimmed = text.trim();
        if (!trimmed) return;
        setError(null);

        const userTurn: ChatTurn = {
            id: crypto.randomUUID(),
            role: "user",
            text: trimmed,
            toolCalls: [],
            isStreaming: false,
        };
        const assistantId = crypto.randomUUID();
        const assistantTurn: ChatTurn = {
            id: assistantId,
            role: "assistant",
            text: "",
            toolCalls: [],
            isStreaming: true,
        };
        setTurns((prev) => [...prev, userTurn, assistantTurn]);
        setIsStreaming(true);

        const apiMessages = [...turnsRef.current, userTurn].map((t) => ({
            role: t.role,
            content: t.text,
        }));
        const context = getContextRef.current?.() ?? undefined;

        const updateAssistant = (patch: (t: ChatTurn) => ChatTurn) =>
            setTurns((prev) => prev.map((t) => (t.id === assistantId ? patch(t) : t)));

        try {
            const body: { messages: typeof apiMessages; context?: ChatContext } = {
                messages: apiMessages,
            };
            if (context) body.context = context;
            const resp = await fetch(CHAT_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            if (!resp.ok || !resp.body) {
                throw new Error(`chat request failed: ${resp.status} ${resp.statusText}`);
            }

            for await (const frame of parseSseStream(resp.body)) {
                let payload: Record<string, unknown> = {};
                try {
                    payload = JSON.parse(frame.data);
                } catch {
                    // Bad frame — skip, the loop is best-effort.
                    continue;
                }
                switch (frame.event) {
                    case "delta": {
                        const text = String(payload.text ?? "");
                        updateAssistant((t) => ({ ...t, text: t.text + text }));
                        break;
                    }
                    case "tool_call": {
                        const tc: ToolCall = {
                            id: String(payload.id ?? ""),
                            name: String(payload.name ?? ""),
                            arguments: (payload.arguments as Record<string, unknown>) ?? {},
                        };
                        updateAssistant((t) => ({
                            ...t,
                            toolCalls: [...t.toolCalls, tc],
                        }));
                        break;
                    }
                    case "map_op": {
                        onMapOpRef.current?.(payload as unknown as MapOp);
                        break;
                    }
                    case "tool_result": {
                        const id = String(payload.id ?? "");
                        updateAssistant((t) => ({
                            ...t,
                            toolCalls: t.toolCalls.map((tc) =>
                                tc.id === id
                                    ? {
                                          ...tc,
                                          result: payload.result,
                                          isError: Boolean(payload.is_error),
                                      }
                                    : tc,
                            ),
                        }));
                        break;
                    }
                    case "done": {
                        updateAssistant((t) => ({ ...t, isStreaming: false }));
                        break;
                    }
                    case "error": {
                        const message = String(payload.message ?? "unknown error");
                        setError(message);
                        updateAssistant((t) => ({
                            ...t,
                            isStreaming: false,
                            error: message,
                        }));
                        break;
                    }
                }
            }
        } catch (e) {
            const message = e instanceof Error ? e.message : String(e);
            setError(message);
            updateAssistant((t) => ({ ...t, isStreaming: false, error: message }));
        } finally {
            setIsStreaming(false);
        }
    }, []);

    return { turns, send, isStreaming, error };
}
