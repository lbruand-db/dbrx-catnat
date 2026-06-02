import { describe, expect, it } from "vitest";
import { parseSseStream } from "./sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
    const encoder = new TextEncoder();
    return new ReadableStream({
        start(controller) {
            for (const c of chunks) controller.enqueue(encoder.encode(c));
            controller.close();
        },
    });
}

async function collect(
    stream: ReadableStream<Uint8Array>,
): Promise<Array<{ event: string; data: string }>> {
    const out: Array<{ event: string; data: string }> = [];
    for await (const f of parseSseStream(stream)) {
        out.push({ event: f.event, data: f.data });
    }
    return out;
}

describe("parseSseStream", () => {
    it("parses one well-formed frame", async () => {
        const frames = await collect(streamOf('event: delta\ndata: {"text":"Bonjour"}\n\n'));
        expect(frames).toEqual([{ event: "delta", data: '{"text":"Bonjour"}' }]);
    });

    it("parses multiple frames in one chunk", async () => {
        const frames = await collect(
            streamOf(
                'event: delta\ndata: {"text":"hi"}\n\n',
                'event: tool_call\ndata: {"name":"list_layers"}\n\n',
                "event: done\ndata: {}\n\n",
            ),
        );
        expect(frames.map((f) => f.event)).toEqual(["delta", "tool_call", "done"]);
        expect(frames[1].data).toBe('{"name":"list_layers"}');
    });

    it("handles a frame split across chunks", async () => {
        const frames = await collect(streamOf("event: delta\nda", 'ta: {"text":"hi"}\n\n'));
        expect(frames).toEqual([{ event: "delta", data: '{"text":"hi"}' }]);
    });

    it("ignores comment lines starting with `:`", async () => {
        const frames = await collect(streamOf(": keepalive\nevent: ping\ndata: {}\n\n"));
        expect(frames).toEqual([{ event: "ping", data: "{}" }]);
    });

    it("defaults the event name to `message` when none is supplied", async () => {
        const frames = await collect(streamOf("data: {}\n\n"));
        expect(frames).toEqual([{ event: "message", data: "{}" }]);
    });

    it("flushes a trailing frame even without final newlines", async () => {
        const frames = await collect(streamOf('event: done\ndata: {"final":true}'));
        expect(frames).toEqual([{ event: "done", data: '{"final":true}' }]);
    });

    it("drops frames that carry no data field", async () => {
        const frames = await collect(streamOf("event: only-event\n\n"));
        expect(frames).toEqual([]);
    });
});
