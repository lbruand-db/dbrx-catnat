import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { type ChatMessage, ChatPane } from "./chat-pane";

describe("<ChatPane />", () => {
    it("renders the empty-state hint when there are no messages", () => {
        render(<ChatPane />);
        expect(screen.getByText(/Ask the agent something/i)).toBeInTheDocument();
    });

    it("hides the empty-state hint once a message exists", () => {
        const seed: ChatMessage = { id: "seed", role: "agent", text: "hello world" };
        render(<ChatPane initialMessages={[seed]} />);
        expect(screen.queryByText(/Ask the agent something/i)).not.toBeInTheDocument();
        expect(screen.getByText("hello world")).toBeInTheDocument();
    });

    it("appends the user message + a default placeholder reply on submit when onSubmit is not provided", async () => {
        const user = userEvent.setup();
        render(<ChatPane />);
        await user.type(screen.getByTestId("chat-input"), "show me PPRI");
        await user.click(screen.getByTestId("chat-submit"));

        const messages = within(screen.getByTestId("chat-messages"));
        expect(messages.getByText("show me PPRI")).toBeInTheDocument();
        expect(messages.getByText(/Agent not wired yet/)).toBeInTheDocument();
    });

    it("clears the draft after submission", async () => {
        const user = userEvent.setup();
        render(<ChatPane />);
        const input = screen.getByTestId("chat-input") as HTMLInputElement;
        await user.type(input, "anything");
        await user.click(screen.getByTestId("chat-submit"));
        expect(input.value).toBe("");
    });

    it("does not submit when the draft is empty or whitespace-only", async () => {
        const user = userEvent.setup();
        const onSubmit = vi.fn();
        render(<ChatPane onSubmit={onSubmit} />);

        await user.click(screen.getByTestId("chat-submit"));
        await user.type(screen.getByTestId("chat-input"), "   ");
        await user.click(screen.getByTestId("chat-submit"));

        expect(onSubmit).not.toHaveBeenCalled();
        // Still on empty-state hint
        expect(screen.getByText(/Ask the agent something/i)).toBeInTheDocument();
    });

    it("calls onSubmit with the trimmed user text and appends its returned reply", async () => {
        const user = userEvent.setup();
        const onSubmit = vi.fn(
            async (text: string): Promise<ChatMessage> => ({
                id: "agent-1",
                role: "agent",
                text: `you said: ${text}`,
            }),
        );

        render(<ChatPane onSubmit={onSubmit} />);
        await user.type(screen.getByTestId("chat-input"), "  hello  ");
        await user.click(screen.getByTestId("chat-submit"));

        expect(onSubmit).toHaveBeenCalledTimes(1);
        expect(onSubmit).toHaveBeenCalledWith("hello");
        expect(await screen.findByText("you said: hello")).toBeInTheDocument();
    });

    it("tags user vs agent messages so styling and screen-readers can distinguish them", async () => {
        const user = userEvent.setup();
        render(<ChatPane />);
        await user.type(screen.getByTestId("chat-input"), "ping");
        await user.click(screen.getByTestId("chat-submit"));

        const messages = within(screen.getByTestId("chat-messages")).getAllByRole("listitem");
        expect(messages).toHaveLength(2);
        expect(messages[0]).toHaveAttribute("data-role", "user");
        expect(messages[1]).toHaveAttribute("data-role", "agent");
    });
});
