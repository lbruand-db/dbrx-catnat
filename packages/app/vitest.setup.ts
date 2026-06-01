import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount + DOM reset between tests so leaks in one suite don't poison the next.
afterEach(() => {
    cleanup();
});
