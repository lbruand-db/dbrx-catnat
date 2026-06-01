import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Layer } from "@/types/layer";
import { LeafletPane } from "./leaflet-pane";

const baseLayer: Layer = {
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
};

describe("<LeafletPane />", () => {
    it("renders both the layer picker and the map container", () => {
        render(<LeafletPane />);
        expect(screen.getByTestId("leaflet-layer-picker")).toBeInTheDocument();
        expect(screen.getByTestId("leaflet-map")).toBeInTheDocument();
    });

    it("shows the empty-state when no layers are provided", () => {
        render(<LeafletPane />);
        expect(
            within(screen.getByTestId("leaflet-layer-picker")).getByText(/No layers loaded yet/i),
        ).toBeInTheDocument();
    });

    it("lists only displayable layers (filters out is_displayable=false)", () => {
        const layers: Layer[] = [
            baseLayer,
            { ...baseLayer, layer_id: "hazard_rga", is_displayable: false },
            { ...baseLayer, layer_id: "admin_communes_h3", peril: "reference" },
        ];
        render(<LeafletPane layers={layers} />);
        const picker = within(screen.getByTestId("leaflet-layer-picker"));
        expect(picker.getByText("hazard_rga_h3")).toBeInTheDocument();
        expect(picker.getByText("admin_communes_h3")).toBeInTheDocument();
        expect(picker.queryByText("hazard_rga")).not.toBeInTheDocument();
    });

    it("annotates each layer with its peril via data-peril for downstream styling", () => {
        const layers: Layer[] = [
            baseLayer,
            { ...baseLayer, layer_id: "hazard_ppri_communes_h3", peril: "flood" },
        ];
        render(<LeafletPane layers={layers} />);
        const items = within(screen.getByTestId("leaflet-layer-picker")).getAllByRole("listitem");
        expect(items[0]).toHaveAttribute("data-peril", "drought");
        expect(items[1]).toHaveAttribute("data-peril", "flood");
    });

    it("invokes onMapReady with a real Leaflet Map instance after mount", () => {
        const onMapReady = vi.fn();
        render(<LeafletPane onMapReady={onMapReady} />);
        expect(onMapReady).toHaveBeenCalledTimes(1);
        const map = onMapReady.mock.calls[0]?.[0];
        // Map instance exposes setView / getCenter — we don't assert specific
        // values (jsdom + leaflet are flaky on layout), just shape.
        expect(typeof map?.setView).toBe("function");
        expect(typeof map?.getCenter).toBe("function");
    });
});
