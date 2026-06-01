import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import axios from "axios";
import type { ReactNode } from "react";
import { Provider as ReduxProvider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock the @kepler.gl/* surfaces upfront. The real packages drag in Mapbox /
// WebGL / Worker plumbing that doesn't make sense to run inside jsdom, so we
// render a deterministic stub and assert against it.
vi.mock("@kepler.gl/components", () => ({
    default: ({ id, width, height }: { id: string; width: number; height: number }) => (
        <div data-testid="kepler-gl-mock" data-id={id} data-width={width} data-height={height} />
    ),
}));
vi.mock("@kepler.gl/processors", () => ({
    processRowObject: (rows: unknown[]) => ({ rows, fields: [], cols: [] }),
}));
vi.mock("@kepler.gl/actions", () => ({
    addDataToMap: (payload: unknown) => ({ type: "ADD_DATA_TO_MAP", payload }),
}));
vi.mock("axios");

import { KeplerPane } from "./kepler-pane";

// Tiny Redux stub — captures dispatched actions for assertion. We don't import
// the real keplerStore because it pulls @kepler.gl/reducers which has Worker
// imports that jsdom doesn't tolerate.
type Action = { type: string; payload?: unknown };

function makeStore() {
    const dispatched: Action[] = [];
    return {
        dispatched,
        getState: () => ({}),
        subscribe: () => () => {},
        dispatch: (action: Action) => {
            dispatched.push(action);
            return action;
        },
        // biome-ignore lint/suspicious/noExplicitAny: redux store typing is intricate; this stub only needs the surface we use
        [Symbol.observable as any]: () => ({ subscribe: () => ({ unsubscribe: () => {} }) }),
    };
}

function wrapper({
    children,
    store,
}: {
    children: ReactNode;
    store: ReturnType<typeof makeStore>;
}) {
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return (
        // biome-ignore lint/suspicious/noExplicitAny: ReduxProvider typing is opinionated about the store; our stub matches the surface area used
        <ReduxProvider store={store as any}>
            <QueryClientProvider client={client}>{children}</QueryClientProvider>
        </ReduxProvider>
    );
}

describe("<KeplerPane />", () => {
    let store: ReturnType<typeof makeStore>;

    beforeEach(() => {
        store = makeStore();
        vi.mocked(axios.get).mockReset();
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("renders the analytical-view region with the testid", () => {
        vi.mocked(axios.get).mockResolvedValueOnce({
            data: { id: "x", label: "x", fields: [], rows: [] },
        });
        render(<KeplerPane />, { wrapper: (p) => wrapper({ ...p, store }) });
        expect(screen.getByTestId("kepler-pane")).toBeInTheDocument();
    });

    it("dispatches addDataToMap exactly once after the dataset arrives", async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({
            data: {
                id: "portfolio_h3",
                label: "Portfolio exposure (H3 r=9)",
                fields: ["h3", "code_dep"],
                rows: [
                    { h3: "892ec...01", code_dep: "069", n_policies: 12 },
                    { h3: "892ec...02", code_dep: "069", n_policies: 8 },
                ],
            },
        });

        render(<KeplerPane />, { wrapper: (p) => wrapper({ ...p, store }) });

        await waitFor(() => {
            expect(store.dispatched.some((a) => a.type === "ADD_DATA_TO_MAP")).toBe(true);
        });
        // Action should fire exactly once even on rerender.
        const addCalls = store.dispatched.filter((a) => a.type === "ADD_DATA_TO_MAP");
        expect(addCalls).toHaveLength(1);
    });

    it("renders the KeplerGl child only after the container has measured non-zero size", () => {
        vi.mocked(axios.get).mockResolvedValueOnce({
            data: { id: "x", label: "x", fields: [], rows: [] },
        });
        render(<KeplerPane />, { wrapper: (p) => wrapper({ ...p, store }) });
        // jsdom doesn't fire ResizeObserver, so the KeplerGl child remains
        // unmounted. That's the intended behaviour — Kepler refuses to render
        // at 0x0 anyway, and this keeps the SSR-style first paint cheap.
        expect(screen.queryByTestId("kepler-gl-mock")).not.toBeInTheDocument();
    });

    it("surfaces a fetch error with a visible message", async () => {
        vi.mocked(axios.get).mockRejectedValueOnce(new Error("503 warehouse_id missing"));
        render(<KeplerPane />, { wrapper: (p) => wrapper({ ...p, store }) });
        await waitFor(() => {
            expect(screen.getByText(/Kepler dataset failed to load/i)).toBeInTheDocument();
        });
        expect(screen.getByText(/503 warehouse_id missing/)).toBeInTheDocument();
    });
});
