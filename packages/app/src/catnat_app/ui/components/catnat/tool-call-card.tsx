import { ChevronDown, ChevronRight, Loader2, Wrench } from "lucide-react";
import { useState } from "react";
import type { ToolCall } from "@/types/chat";

interface ToolCallCardProps {
    tool: ToolCall;
}

/**
 * Collapsible "the agent used <tool>" card. Closed by default; expand to
 * see arguments + result. Stays compact in the chat stream so a busy
 * turn doesn't push the assistant text off-screen.
 */
export function ToolCallCard({ tool }: ToolCallCardProps) {
    const [open, setOpen] = useState(false);
    const isPending = tool.result === undefined && !tool.isError;
    const status = tool.isError ? "error" : isPending ? "pending" : "ok";

    return (
        <div
            className="rounded-md border bg-muted/40 text-sm"
            data-testid="tool-call-card"
            data-tool-name={tool.name}
            data-tool-status={status}
        >
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-muted/70"
                aria-expanded={open}
            >
                {open ? (
                    <ChevronDown className="size-3 shrink-0" />
                ) : (
                    <ChevronRight className="size-3 shrink-0" />
                )}
                <Wrench
                    className={
                        tool.isError
                            ? "size-3 text-destructive shrink-0"
                            : "size-3 text-muted-foreground shrink-0"
                    }
                />
                <span className="font-mono text-xs">{tool.name}</span>
                {isPending && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
                {tool.isError && <span className="text-xs text-destructive">error</span>}
            </button>
            {open && (
                <div className="border-t bg-background/40 px-2 py-1.5 text-xs">
                    {Object.keys(tool.arguments).length > 0 && (
                        <div className="mb-1.5">
                            <div className="text-muted-foreground">arguments</div>
                            <pre className="overflow-x-auto whitespace-pre-wrap font-mono">
                                {JSON.stringify(tool.arguments, null, 2)}
                            </pre>
                        </div>
                    )}
                    {tool.result !== undefined && (
                        <div>
                            <div className="text-muted-foreground">
                                {tool.isError ? "error" : "result"}
                            </div>
                            <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono">
                                {typeof tool.result === "string"
                                    ? tool.result
                                    : JSON.stringify(tool.result, null, 2)}
                            </pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
