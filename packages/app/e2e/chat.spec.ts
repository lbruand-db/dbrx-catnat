import { type Route, expect, test } from "@playwright/test";

/**
 * Happy-path E2E: user types a prompt → tool-call card renders →
 * assistant text streams in.
 *
 * Network model: every /api/* call is intercepted. The mock SSE
 * response is sent as one full body (Playwright doesn't natively
 * stream into a route fulfil); the FE's SSE parser handles it the
 * same way because it buffers per `\n\n`. The visible effect: events
 * appear "instantly" rather than time-spread, but the structural
 * assertions (cards render, text appears, fetch shape is right) are
 * what we care about here.
 */

const SSE_BODY = [
    'event: tool_call\ndata: {"id":"c1","name":"list_layers","arguments":{}}\n\n',
    'event: tool_result\ndata: {"id":"c1","name":"list_layers","result":{"result":[]},"is_error":false}\n\n',
    'event: delta\ndata: {"text":"Bonjour, "}\n\n',
    'event: delta\ndata: {"text":"voici la liste."}\n\n',
    'event: done\ndata: {"final_text":"Bonjour, voici la liste."}\n\n',
].join("");

async function stubApi(page: import("@playwright/test").Page): Promise<{ chatPosts: unknown[] }> {
    const chatPosts: unknown[] = [];

    await page.route("**/api/layers", async (route: Route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ layers: [] }),
        });
    });

    await page.route("**/api/chat", async (route: Route) => {
        const req = route.request();
        const body = req.postData();
        if (body) chatPosts.push(JSON.parse(body));
        await route.fulfill({
            status: 200,
            contentType: "text/event-stream",
            body: SSE_BODY,
        });
    });

    // The vector-tile endpoint isn't exercised in this scenario but the
    // FE will still request /api/version on load — stub permissive so
    // unmatched calls don't 404 in the console.
    await page.route("**/api/tiles/**", async (route: Route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/x-protobuf",
            body: Buffer.from([]),
        });
    });
    await page.route("**/api/version", async (route: Route) => {
        await route.fulfill({ status: 200, contentType: "application/json", body: '{"version":"e2e"}' });
    });
    await page.route("**/api/current-user", async (route: Route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: '{"id":"e2e-user","user_name":"e2e@local"}',
        });
    });

    return { chatPosts };
}

test("happy path: prompt → tool-call card → assistant text", async ({ page }) => {
    const { chatPosts } = await stubApi(page);

    await page.goto("/");

    // Wait for the chat empty state — proves the app booted.
    await expect(page.getByTestId("chat-empty-state")).toBeVisible();

    // Send a prompt.
    await page.getByTestId("chat-input").fill("Quelles couches sont disponibles ?");
    await page.getByTestId("chat-submit").click();

    // The user message lands first.
    await expect(page.getByTestId("chat-turn-user")).toContainText(
        "Quelles couches sont disponibles ?",
    );

    // Tool-call card materialises with the right name + ok status.
    const card = page.getByTestId("tool-call-card");
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-tool-name", "list_layers");
    await expect(card).toHaveAttribute("data-tool-status", "ok");

    // Streamed assistant text shows up concatenated.
    await expect(page.getByTestId("chat-turn-assistant")).toContainText(
        "Bonjour, voici la liste.",
    );

    // The FE posted the prompt + an empty (no layers added yet) context
    // block to /api/chat.
    expect(chatPosts).toHaveLength(1);
    const body = chatPosts[0] as { messages: Array<{ role: string; content: string }> };
    expect(body.messages[0]).toMatchObject({
        role: "user",
        content: "Quelles couches sont disponibles ?",
    });
});
