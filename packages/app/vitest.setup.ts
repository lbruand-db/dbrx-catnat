import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount + DOM reset between tests so leaks in one suite don't poison the next.
afterEach(() => {
    cleanup();
});

// jsdom doesn't ship ResizeObserver; components that observe their container
// (KeplerPane especially) crash on mount without this polyfill.
if (typeof globalThis.ResizeObserver === "undefined") {
    class ResizeObserverStub {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
    }
    globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
