import { useCallback, useRef, useState } from "react";
import { parseSseStream } from "@/lib/sse";
import type { ChatTurn, ToolCall } from "@/types/chat";

/**
 * Talks to `/api/chat` over SSE. Holds the conversation as a list of
 * `ChatTurn` records and mutates the in-flight assistant turn as
 * delta/tool_call/tool_result events arrive.
 *
 * The hook is intentionally state-machine-thin: every event maps to one
 * `setTurns` call. React batches the re-renders.
 */
export interface UseChatResult {
    turns: ChatTurn[];
    send: (text: string) => Promise<void>;
    isStreaming: boolean;
    error: string | null;
}

const CHAT_URL = "/api/chat";

export function useChat(): UseChatResult {
    const [turns, setTurns] = useState<ChatTurn[]>([]);
    const [isStreaming, setIsStreaming] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /** Keep a ref to the latest turns so `send` can build the API payload
     * from a consistent snapshot, not a stale closure capture. */
    const turnsRef = useRef<ChatTurn[]>([]);
    turnsRef.current = turns;

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

        const updateAssistant = (patch: (t: ChatTurn) => ChatTurn) =>
            setTurns((prev) => prev.map((t) => (t.id === assistantId ? patch(t) : t)));

        try {
            const resp = await fetch(CHAT_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ messages: apiMessages }),
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
