import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatPane } from "./chat-pane";

function streamResponse(body: string): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
    return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
    });
}

describe("<ChatPane />", () => {
    let originalFetch: typeof fetch;

    beforeEach(() => {
        originalFetch = globalThis.fetch;
    });
    afterEach(() => {
        globalThis.fetch = originalFetch;
    });

    function mock(body: string) {
        globalThis.fetch = vi.fn(async () => streamResponse(body)) as unknown as typeof fetch;
    }

    it("shows suggested prompts when there are no messages", () => {
        render(<ChatPane />);
        expect(screen.getByTestId("chat-empty-state")).toBeInTheDocument();
        expect(screen.getAllByTestId("suggested-prompt").length).toBeGreaterThan(0);
    });

    it("hides the empty state once a message exists", async () => {
        const user = userEvent.setup();
        mock('event: delta\ndata: {"text":"ok"}\n\nevent: done\ndata: {"final_text":"ok"}\n\n');
        render(<ChatPane />);
        await user.type(screen.getByTestId("chat-input"), "hi");
        await user.click(screen.getByTestId("chat-submit"));
        expect(await screen.findByTestId("chat-turn-assistant")).toBeInTheDocument();
        expect(screen.queryByTestId("chat-empty-state")).not.toBeInTheDocument();
    });

    it("renders the user turn and the streamed assistant text", async () => {
        const user = userEvent.setup();
        mock(
            'event: delta\ndata: {"text":"Bonjour "}\n\n' +
                'event: delta\ndata: {"text":"Lucas."}\n\n' +
                'event: done\ndata: {"final_text":"Bonjour Lucas."}\n\n',
        );
        render(<ChatPane />);
        await user.type(screen.getByTestId("chat-input"), "salut");
        await user.click(screen.getByTestId("chat-submit"));

        const messages = within(screen.getByTestId("chat-messages"));
        expect(await messages.findByText("salut")).toBeInTheDocument();
        expect(await messages.findByText("Bonjour Lucas.")).toBeInTheDocument();
    });

    it("renders a collapsible tool-call card when the agent invokes a tool", async () => {
        const user = userEvent.setup();
        mock(
            'event: tool_call\ndata: {"id":"call_1","name":"list_layers","arguments":{}}\n\n' +
                'event: tool_result\ndata: {"id":"call_1","name":"list_layers","result":{"result":[]},"is_error":false}\n\n' +
                'event: delta\ndata: {"text":"no layers"}\n\n' +
                'event: done\ndata: {"final_text":"no layers"}\n\n',
        );
        render(<ChatPane />);
        await user.type(screen.getByTestId("chat-input"), "list");
        await user.click(screen.getByTestId("chat-submit"));

        const card = await screen.findByTestId("tool-call-card");
        expect(card).toHaveAttribute("data-tool-name", "list_layers");
        expect(card).toHaveAttribute("data-tool-status", "ok");
    });

    it("submitting a prompt fires fetch and clears the draft", async () => {
        const user = userEvent.setup();
        const fetchSpy = vi.fn(async () =>
            streamResponse("event: done\ndata: {}\n\n"),
        ) as unknown as typeof fetch;
        globalThis.fetch = fetchSpy;

        render(<ChatPane />);
        const input = screen.getByTestId("chat-input") as HTMLInputElement;
        await user.type(input, "anything");
        await user.click(screen.getByTestId("chat-submit"));
        expect(input.value).toBe("");
        await screen.findByTestId("chat-turn-user");
        expect(fetchSpy).toHaveBeenCalledTimes(1);
    });

    it("clicking a suggested prompt sends it directly", async () => {
        const user = userEvent.setup();
        const fetchSpy = vi.fn(async () =>
            streamResponse('event: done\ndata: {"final_text":""}\n\n'),
        ) as unknown as typeof fetch;
        globalThis.fetch = fetchSpy;

        render(<ChatPane />);
        const prompts = screen.getAllByTestId("suggested-prompt");
        await user.click(prompts[0]);

        await screen.findByTestId("chat-turn-user");
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        const body = JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
        expect(body.messages[0].content).toBe(prompts[0].textContent);
    });

    it("does not submit empty / whitespace input", async () => {
        const user = userEvent.setup();
        const fetchSpy = vi.fn() as unknown as typeof fetch;
        globalThis.fetch = fetchSpy;

        render(<ChatPane />);
        await user.click(screen.getByTestId("chat-submit"));
        await user.type(screen.getByTestId("chat-input"), "   ");
        await user.click(screen.getByTestId("chat-submit"));
        expect(fetchSpy).not.toHaveBeenCalled();
    });
});
