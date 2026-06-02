import { Loader2, Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useChat } from "@/hooks/use-chat";
import type { ChatTurn, MapOp } from "@/types/chat";
import { ToolCallCard } from "./tool-call-card";

/** Suggested first prompts shown when the chat is empty. Tuned to the
 * demo-script narrative (SPEC §6) now that map-mutating tools are wired. */
const SUGGESTED_PROMPTS = [
    "Affiche les communes du Rhône sur la carte.",
    "Montre les zones PPRI inondation autour de Lyon.",
    "Zoome sur Vaucluse (POINT(5.07 44.05)).",
];

export interface ChatPaneProps {
    /** Forwarded to `useChat` — fires once per `map_op` SSE event so the
     * surrounding layout can mutate the Leaflet map handle. */
    onMapOp?: (op: MapOp) => void;
}

/**
 * Chat / agent pane.
 *
 * Streams agent SSE events from `/api/chat` via `useChat`. Renders
 * messages as a vertical list, with collapsible tool-call cards
 * interleaved into the assistant turn at the position the agent invoked
 * them.
 */
export function ChatPane({ onMapOp }: ChatPaneProps = {}) {
    const { turns, send, isStreaming, error } = useChat({ onMapOp });
    const [draft, setDraft] = useState("");
    const listRef = useRef<HTMLUListElement>(null);

    // Auto-scroll to bottom as new content streams in. `turns` is the
    // signal — biome's exhaustive-deps rule flags it as unused (the
    // effect body only touches the ref), but we genuinely want to
    // re-fire on every turn-list mutation, not just on mount.
    // biome-ignore lint/correctness/useExhaustiveDependencies: see comment above
    useEffect(() => {
        if (listRef.current) {
            listRef.current.scrollTop = listRef.current.scrollHeight;
        }
    }, [turns]);

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const text = draft.trim();
        if (!text || isStreaming) return;
        setDraft("");
        await send(text);
    }

    async function pickPrompt(prompt: string) {
        if (isStreaming) return;
        await send(prompt);
    }

    return (
        <section className="flex h-full flex-col" aria-label="Chat" data-testid="chat-pane">
            <ul
                ref={listRef}
                className="flex-1 space-y-3 overflow-y-auto p-3"
                aria-label="Chat history"
                data-testid="chat-messages"
            >
                {turns.length === 0 ? (
                    <li data-testid="chat-empty-state">
                        <p className="mb-3 text-sm text-muted-foreground italic">
                            Ask the agent something to get started.
                        </p>
                        <div className="flex flex-col gap-1.5">
                            {SUGGESTED_PROMPTS.map((p) => (
                                <button
                                    key={p}
                                    type="button"
                                    onClick={() => pickPrompt(p)}
                                    disabled={isStreaming}
                                    className="rounded-md border bg-background px-2 py-1.5 text-left text-sm hover:bg-muted disabled:opacity-50"
                                    data-testid="suggested-prompt"
                                >
                                    {p}
                                </button>
                            ))}
                        </div>
                    </li>
                ) : (
                    turns.map((t) => <ChatTurnView key={t.id} turn={t} />)
                )}
                {error && (
                    <li
                        className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
                        data-testid="chat-error"
                    >
                        {error}
                    </li>
                )}
            </ul>
            <form className="flex gap-2 border-t p-3" onSubmit={handleSubmit}>
                <Input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="Type a question…"
                    aria-label="Message to agent"
                    data-testid="chat-input"
                    disabled={isStreaming}
                />
                <Button
                    type="submit"
                    data-testid="chat-submit"
                    disabled={isStreaming || !draft.trim()}
                >
                    {isStreaming ? (
                        <Loader2 className="size-4 animate-spin" />
                    ) : (
                        <Send className="size-4" />
                    )}
                </Button>
            </form>
        </section>
    );
}

interface ChatTurnViewProps {
    turn: ChatTurn;
}

function ChatTurnView({ turn }: ChatTurnViewProps) {
    if (turn.role === "user") {
        return (
            <li className="flex justify-end" data-role="user" data-testid="chat-turn-user">
                <div className="max-w-[85%] rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground">
                    {turn.text}
                </div>
            </li>
        );
    }

    const showCursor = turn.isStreaming && turn.text.length > 0;
    const showSpinner = turn.isStreaming && turn.text.length === 0;
    return (
        <li
            className="space-y-2"
            data-role="assistant"
            data-testid="chat-turn-assistant"
            data-streaming={turn.isStreaming}
        >
            {turn.toolCalls.map((tc) => (
                <ToolCallCard key={tc.id} tool={tc} />
            ))}
            {showSpinner ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-3 animate-spin" />
                    <span>thinking…</span>
                </div>
            ) : (
                <div className="whitespace-pre-wrap text-sm">
                    {turn.text}
                    {showCursor && (
                        <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-current align-middle" />
                    )}
                </div>
            )}
        </li>
    );
}
