import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface ChatMessage {
    /** Stable id used for keying — `crypto.randomUUID()` at submit time. */
    id: string;
    role: "user" | "agent";
    text: string;
}

export interface ChatPaneProps {
    /**
     * Test hook + Phase 4 wiring point — called when the user submits.
     * The agent backend will replace the no-op default in Phase 4.
     */
    onSubmit?: (text: string) => Promise<ChatMessage | undefined> | ChatMessage | undefined;
    initialMessages?: ChatMessage[];
}

const PLACEHOLDER_AGENT = "Agent not wired yet. Coming in Phase 4.";

/**
 * Chat / agent pane.
 *
 * Today: a typed-and-submit shell that maintains a local message list.
 * Phase 4 lights this up by wiring `onSubmit` to the MCP-backed Claude
 * runtime — same component, real responses.
 */
export function ChatPane({ onSubmit, initialMessages = [] }: ChatPaneProps) {
    const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
    const [draft, setDraft] = useState("");

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const text = draft.trim();
        if (!text) return;
        const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", text };
        setMessages((prev) => [...prev, userMsg]);
        setDraft("");

        const reply = (await onSubmit?.(text)) ?? {
            id: crypto.randomUUID(),
            role: "agent" as const,
            text: PLACEHOLDER_AGENT,
        };
        if (reply && typeof reply === "object" && "id" in reply) {
            setMessages((prev) => [...prev, reply]);
        }
    }

    return (
        <section className="flex flex-col h-full" aria-label="Chat" data-testid="chat-pane">
            <ul
                className="flex-1 overflow-y-auto p-3 space-y-2"
                aria-label="Chat history"
                data-testid="chat-messages"
            >
                {messages.length === 0 ? (
                    <li className="text-sm text-muted-foreground italic">
                        Ask the agent something — “Show me PPRI in Vaucluse”, “Quelle est
                        l'exposition RGA sur le portefeuille ?”.
                    </li>
                ) : (
                    messages.map((m) => (
                        <li
                            key={m.id}
                            className={
                                m.role === "user" ? "text-right" : "text-left text-muted-foreground"
                            }
                            data-role={m.role}
                        >
                            <span className="inline-block rounded-md bg-muted px-2 py-1 text-sm">
                                {m.text}
                            </span>
                        </li>
                    ))
                )}
            </ul>
            <form className="flex gap-2 p-3 border-t" onSubmit={handleSubmit}>
                <Input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder="Type a question…"
                    aria-label="Message to agent"
                    data-testid="chat-input"
                />
                <Button type="submit" data-testid="chat-submit">
                    Send
                </Button>
            </form>
        </section>
    );
}
