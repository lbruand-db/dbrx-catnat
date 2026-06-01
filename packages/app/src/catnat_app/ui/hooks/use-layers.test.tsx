import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import axios from "axios";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LayerListOut } from "@/types/layer";
import { useLayers } from "./use-layers";

vi.mock("axios");

function wrapper({ children }: { children: ReactNode }) {
    // Fresh client per test so caches don't bleed between assertions.
    const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const sample: LayerListOut = {
    layers: [
        {
            layer_id: "hazard_rga_h3",
            table_fq: "cat.catnat_gold.hazard_rga_h3",
            peril: "drought",
            medallion: "gold",
            grain: "h3_r9_cell",
            h3_column: "h3",
            geom_column: null,
            license: "Etalab 2.0",
            is_displayable: true,
            description: "RGA H3 r=9 cells",
        },
    ],
};

describe("useLayers()", () => {
    beforeEach(() => {
        vi.mocked(axios.get).mockReset();
    });
    afterEach(() => {
        vi.restoreAllMocks();
    });

    it("calls GET /api/layers and unwraps the .layers field", async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({ data: sample });

        const { result } = renderHook(() => useLayers(), { wrapper });

        await waitFor(() => {
            expect(result.current.isSuccess).toBe(true);
        });
        expect(axios.get).toHaveBeenCalledWith("/api/layers");
        expect(result.current.data).toEqual(sample.layers);
    });

    it("surfaces network errors via the error field instead of throwing", async () => {
        vi.mocked(axios.get).mockRejectedValueOnce(new Error("ECONNREFUSED"));

        const { result } = renderHook(() => useLayers(), { wrapper });
        await waitFor(() => {
            expect(result.current.isError).toBe(true);
        });
        expect(result.current.error?.message).toBe("ECONNREFUSED");
    });
});
