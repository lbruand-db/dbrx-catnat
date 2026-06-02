import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ToolCall } from "@/types/chat";
import { ToolCallCard } from "./tool-call-card";

function tool(partial: Partial<ToolCall>): ToolCall {
    return {
        id: "call_1",
        name: "list_layers",
        arguments: {},
        ...partial,
    };
}

describe("<ToolCallCard />", () => {
    it("renders collapsed by default with the tool name visible", () => {
        render(<ToolCallCard tool={tool({ result: { result: [] } })} />);
        const card = screen.getByTestId("tool-call-card");
        expect(card).toHaveAttribute("data-tool-name", "list_layers");
        // Arguments / result not rendered while collapsed.
        expect(screen.queryByText(/arguments/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/result/i)).not.toBeInTheDocument();
    });

    it("expands to show arguments and result on click", async () => {
        const user = userEvent.setup();
        render(
            <ToolCallCard
                tool={tool({
                    arguments: { layer_id: "rga" },
                    result: { result: [{ id: 1 }] },
                })}
            />,
        );
        await user.click(screen.getByRole("button"));
        expect(screen.getByText("arguments")).toBeInTheDocument();
        expect(screen.getByText("result")).toBeInTheDocument();
        expect(screen.getByText(/layer_id/)).toBeInTheDocument();
    });

    it("status=pending when the result hasn't arrived yet", () => {
        render(<ToolCallCard tool={tool({})} />);
        expect(screen.getByTestId("tool-call-card")).toHaveAttribute("data-tool-status", "pending");
    });

    it("status=error when isError is true", () => {
        render(<ToolCallCard tool={tool({ result: "boom", isError: true })} />);
        const card = screen.getByTestId("tool-call-card");
        expect(card).toHaveAttribute("data-tool-status", "error");
        expect(screen.getByText("error")).toBeInTheDocument();
    });
});
