import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for browser-level E2E. Complements the unit suite
 * (`bunx vitest run`) and the in-process agent probe
 * (`scripts/probe_agent.py`) — those exercise the FE state machine and
 * the backend agent loop respectively; this layer drives a real
 * Chromium against the built bundle and pins behaviour the unit suite
 * can't catch (network interception, Leaflet/SSE timing, click flow).
 *
 * Network model: every `/api/*` request is intercepted in the test
 * itself via `page.route()`. The "backend" here is whatever each
 * scenario stubs — no FastAPI, no warehouse, no FMAPI. The goal is to
 * catch FE bugs (rendering, click handlers, SSE parsing) without
 * needing real infrastructure.
 *
 * Before running, build the FE bundle so `__dist__/` exists:
 *   apx frontend build
 *   bunx playwright install --with-deps chromium  # one-time
 *   bunx playwright test
 */
export default defineConfig({
    testDir: "./e2e",
    fullyParallel: true,
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    workers: process.env.CI ? 2 : undefined,
    reporter: [["list"], ["html", { open: "never" }]],

    use: {
        baseURL: "http://localhost:4173",
        trace: "on-first-retry",
        screenshot: "only-on-failure",
    },

    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],

    webServer: {
        // Python's http.server is universally available (the test
        // runner already has Python via uv) and serves the built
        // SPA bundle without any extra dep. Add a fallback to
        // index.html so the SPA's client-side routing works.
        command: "python3 -m http.server 4173 --bind 127.0.0.1 --directory src/catnat_app/__dist__",
        url: "http://localhost:4173/",
        reuseExistingServer: !process.env.CI,
        stdout: "ignore",
        stderr: "pipe",
        timeout: 30_000,
    },
});
