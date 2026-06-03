import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";

function streamResponse(body: string, init: ResponseInit = {}): Response {
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
        ...init,
    });
}

function mockFetch(response: () => Response): ReturnType<typeof vi.fn> {
    const fn = vi.fn(async () => response());
    globalThis.fetch = fn as unknown as typeof fetch;
    return fn;
}

describe("useChat", () => {
    let originalFetch: typeof fetch;

    beforeEach(() => {
        originalFetch = globalThis.fetch;
    });
    afterEach(() => {
        globalThis.fetch = originalFetch;
    });

    it("starts with an empty conversation and no error", () => {
        const { result } = renderHook(() => useChat());
        expect(result.current.turns).toEqual([]);
        expect(result.current.isStreaming).toBe(false);
        expect(result.current.error).toBeNull();
    });

    it("posts to /api/chat with the user turn and streams a single delta into an assistant turn", async () => {
        const sse =
            'event: delta\ndata: {"text":"Bonjour "}\n\n' +
            'event: delta\ndata: {"text":"Lucas"}\n\n' +
            'event: done\ndata: {"final_text":"Bonjour Lucas"}\n\n';
        const fetchSpy = mockFetch(() => streamResponse(sse));

        const { result } = renderHook(() => useChat());
        await act(async () => {
            await result.current.send("hi");
        });

        // Two turns: user, then assistant.
        expect(result.current.turns).toHaveLength(2);
        expect(result.current.turns[0].role).toBe("user");
        expect(result.current.turns[0].text).toBe("hi");
        expect(result.current.turns[1].role).toBe("assistant");
        expect(result.current.turns[1].text).toBe("Bonjour Lucas");
        expect(result.current.turns[1].isStreaming).toBe(false);
        expect(result.current.isStreaming).toBe(false);

        // The request body carried the OpenAI-shape message list.
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        const call = fetchSpy.mock.calls[0];
        const body = JSON.parse(String(call[1]?.body));
        expect(body.messages).toEqual([{ role: "user", content: "hi" }]);
    });

    it("attaches tool calls + matching results to the assistant turn", async () => {
        const sse =
            'event: tool_call\ndata: {"id":"call_1","name":"list_layers","arguments":{}}\n\n' +
            'event: tool_result\ndata: {"id":"call_1","name":"list_layers","result":{"result":[{"layer_id":"rga"}]},"is_error":false}\n\n' +
            'event: delta\ndata: {"text":"Found 1 layer."}\n\n' +
            'event: done\ndata: {"final_text":"Found 1 layer."}\n\n';
        mockFetch(() => streamResponse(sse));

        const { result } = renderHook(() => useChat());
        await act(async () => {
            await result.current.send("quelles couches?");
        });

        const assistant = result.current.turns[1];
        expect(assistant.toolCalls).toHaveLength(1);
        expect(assistant.toolCalls[0].name).toBe("list_layers");
        expect(assistant.toolCalls[0].isError).toBe(false);
        // result is the structuredContent payload from FastMCP
        expect(assistant.toolCalls[0].result).toEqual({
            result: [{ layer_id: "rga" }],
        });
        expect(assistant.text).toBe("Found 1 layer.");
    });

    it("surfaces an `error` SSE event into both the turn and the hook-level error", async () => {
        const sse = 'event: error\ndata: {"message":"FMAPI timed out"}\n\n';
        mockFetch(() => streamResponse(sse));

        const { result } = renderHook(() => useChat());
        await act(async () => {
            await result.current.send("hi");
        });

        await waitFor(() => expect(result.current.error).toBe("FMAPI timed out"));
        expect(result.current.turns[1].error).toBe("FMAPI timed out");
        expect(result.current.turns[1].isStreaming).toBe(false);
    });

    it("surfaces a non-2xx HTTP response as an error", async () => {
        mockFetch(
            () =>
                new Response("nope", {
                    status: 500,
                    statusText: "Internal Server Error",
                }),
        );

        const { result } = renderHook(() => useChat());
        await act(async () => {
            await result.current.send("hi");
        });

        await waitFor(() => expect(result.current.error).toContain("500"));
        expect(result.current.turns[1].isStreaming).toBe(false);
    });

    it("fires onMapOp for each map_op SSE event with the typed payload", async () => {
        const sse =
            'event: map_op\ndata: {"op":"add_layer","layer_id":"x","peril":"flood","geojson":{"type":"FeatureCollection","features":[]},"style":{},"row_count":0,"status":"ok"}\n\n' +
            'event: tool_result\ndata: {"id":"call_1","name":"add_layer","result":{"op":"add_layer","layer_id":"x","row_count":0,"status":"ok"},"is_error":false}\n\n' +
            'event: delta\ndata: {"text":"done"}\n\n' +
            'event: done\ndata: {"final_text":"done"}\n\n';
        mockFetch(() => streamResponse(sse));

        const ops: Array<{ op: string; layer_id?: string }> = [];
        const { result } = renderHook(() =>
            useChat({
                onMapOp: (op) => {
                    ops.push(op as { op: string; layer_id?: string });
                },
            }),
        );
        await act(async () => {
            await result.current.send("show layer x");
        });

        expect(ops).toHaveLength(1);
        expect(ops[0].op).toBe("add_layer");
        expect(ops[0].layer_id).toBe("x");
    });

    it("attaches the context block from getContext() to the request body", async () => {
        const fetchSpy = mockFetch(() =>
            streamResponse('event: done\ndata: {"final_text":""}\n\n'),
        );
        const { result } = renderHook(() =>
            useChat({
                getContext: () => ({
                    viewport: {
                        bbox: [4.5, 45.4, 5.2, 46.1],
                        zoom: 11,
                        center: [4.85, 45.75],
                    },
                    active_layers: [{ layer_id: "hazard_ppri_communes" }],
                }),
            }),
        );
        await act(async () => {
            await result.current.send("hi");
        });
        const body = JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
        expect(body.context).toBeDefined();
        expect(body.context.viewport.zoom).toBe(11);
        expect(body.context.viewport.bbox).toEqual([4.5, 45.4, 5.2, 46.1]);
        expect(body.context.active_layers[0].layer_id).toBe("hazard_ppri_communes");
    });

    it("omits the context block when getContext returns null", async () => {
        const fetchSpy = mockFetch(() =>
            streamResponse('event: done\ndata: {"final_text":""}\n\n'),
        );
        const { result } = renderHook(() => useChat({ getContext: () => null }));
        await act(async () => {
            await result.current.send("hi");
        });
        const body = JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
        expect(body.context).toBeUndefined();
    });

    it("ignores empty / whitespace-only sends", async () => {
        const fetchSpy = mockFetch(() => streamResponse("event: done\ndata: {}\n\n"));
        const { result } = renderHook(() => useChat());
        await act(async () => {
            await result.current.send("   ");
        });
        expect(fetchSpy).not.toHaveBeenCalled();
        expect(result.current.turns).toEqual([]);
    });
});
