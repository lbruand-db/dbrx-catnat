/**
 * Minimal SSE (Server-Sent Events) frame parser.
 *
 * `EventSource` is the browser-native API but only supports GET — our
 * agent endpoint is POST so we drive the stream with `fetch` + this
 * parser. Format is strictly what `backend/agent/events.py::sse()`
 * emits: each frame is `event: <name>\ndata: <json>\n\n` (single line
 * per field, no multi-line data, no IDs / retry).
 */

export interface SseFrame {
    /** Event name from the `event:` line. Defaults to "message" per spec. */
    event: string;
    /** Raw `data:` payload (caller `JSON.parse`s it). */
    data: string;
}

/**
 * Parse a `ReadableStream<Uint8Array>` (typically `response.body`) into
 * a sequence of SSE frames. Holds a buffer across chunks so a frame
 * split mid-`\n\n` doesn't break.
 */
export async function* parseSseStream(
    stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
    const reader = stream.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                // Flush any trailing frame that lacks a final \n\n.
                const tail = buffer.trim();
                if (tail) {
                    const frame = parseFrame(tail);
                    if (frame) yield frame;
                }
                return;
            }
            buffer += decoder.decode(value, { stream: true });

            let boundary = buffer.indexOf("\n\n");
            while (boundary !== -1) {
                const raw = buffer.slice(0, boundary);
                buffer = buffer.slice(boundary + 2);
                const frame = parseFrame(raw);
                if (frame) yield frame;
                boundary = buffer.indexOf("\n\n");
            }
        }
    } finally {
        reader.releaseLock();
    }
}

function parseFrame(raw: string): SseFrame | null {
    let event = "message";
    let data = "";
    for (const line of raw.split("\n")) {
        if (line.startsWith(":")) continue; // SSE comment
        if (line.startsWith("event:")) {
            event = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
            // Multi-line data fields concat with `\n` per spec; our backend
            // emits single-line data, but be permissive.
            const piece = line.slice(5).replace(/^ /, "");
            data = data ? `${data}\n${piece}` : piece;
        }
    }
    if (!data) return null;
    return { event, data };
}
