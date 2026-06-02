import type L from "leaflet";
import type { Map as LeafletMap } from "leaflet";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MapOp } from "@/types/chat";
import { applyMapOp } from "./map-dispatcher";

/**
 * `applyMapOp` is a thin wrapper around Leaflet's GeoJSON / map APIs.
 * Tests stub the map handle with vitest mocks — we assert on the calls
 * rather than on the rendered DOM.
 */

function makeMap(): LeafletMap {
    const m = {
        addLayer: vi.fn(),
        removeLayer: vi.fn(),
        fitBounds: vi.fn(),
    } as unknown as LeafletMap;
    return m;
}

const SQUARE_FC = {
    type: "FeatureCollection" as const,
    features: [
        {
            type: "Feature" as const,
            geometry: {
                type: "Polygon" as const,
                coordinates: [
                    [
                        [4.85, 45.75],
                        [4.86, 45.75],
                        [4.86, 45.76],
                        [4.85, 45.76],
                        [4.85, 45.75],
                    ],
                ],
            },
            properties: {},
        },
    ],
};

describe("applyMapOp", () => {
    let map: LeafletMap;
    let layers: Map<string, L.GeoJSON>;

    beforeEach(() => {
        map = makeMap();
        layers = new Map();
    });

    it("add_layer registers the layer and fits the bounds", () => {
        const op: MapOp = {
            op: "add_layer",
            layer_id: "x",
            peril: "flood",
            geojson: SQUARE_FC,
            style: { color: "#1f77b4" },
            row_count: 1,
            status: "ok",
        };
        applyMapOp(op, map, layers);
        expect(layers.get("x")).toBeDefined();
        expect(map.fitBounds).toHaveBeenCalledTimes(1);
        // L.geoJSON adds itself to the map via .addTo() — that maps onto
        // map.addLayer internally.
        expect(map.addLayer).toHaveBeenCalled();
    });

    it("add_layer replaces an existing layer with the same id", () => {
        const op: MapOp = {
            op: "add_layer",
            layer_id: "x",
            peril: "flood",
            geojson: SQUARE_FC,
            style: {},
            row_count: 1,
            status: "ok",
        };
        applyMapOp(op, map, layers);
        const first = layers.get("x");
        applyMapOp(op, map, layers);
        expect(map.removeLayer).toHaveBeenCalledWith(first);
        // The new layer overwrote the old one.
        expect(layers.get("x")).not.toBe(first);
    });

    it("remove_layer drops the layer from the map and the registry", () => {
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "x",
                peril: "flood",
                geojson: SQUARE_FC,
                style: {},
                row_count: 1,
                status: "ok",
            },
            map,
            layers,
        );
        const layer = layers.get("x");
        applyMapOp({ op: "remove_layer", layer_id: "x", status: "ok" }, map, layers);
        expect(map.removeLayer).toHaveBeenCalledWith(layer);
        expect(layers.has("x")).toBe(false);
    });

    it("remove_layer for an unknown id is a no-op", () => {
        applyMapOp({ op: "remove_layer", layer_id: "nope", status: "ok" }, map, layers);
        expect(map.removeLayer).not.toHaveBeenCalled();
    });

    it("zoom_to fits map bounds to the WKT geometry without adding it", () => {
        applyMapOp(
            {
                op: "zoom_to",
                geom_geojson: SQUARE_FC.features[0].geometry,
                status: "ok",
            },
            map,
            layers,
        );
        expect(map.fitBounds).toHaveBeenCalledTimes(1);
        // The temporary L.geoJSON used to compute bounds is never added.
        expect(layers.size).toBe(0);
    });

    it("style_layer mutates an existing layer's style", () => {
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "x",
                peril: "flood",
                geojson: SQUARE_FC,
                style: { color: "#aaa" },
                row_count: 1,
                status: "ok",
            },
            map,
            layers,
        );
        const layer = layers.get("x") as L.GeoJSON;
        const setStyleSpy = vi.spyOn(layer, "setStyle");
        applyMapOp(
            {
                op: "style_layer",
                layer_id: "x",
                style: { color: "#ff0000" },
                status: "ok",
            },
            map,
            layers,
        );
        expect(setStyleSpy).toHaveBeenCalledWith({ color: "#ff0000" });
    });

    it("style_layer for an unknown layer is a no-op", () => {
        applyMapOp(
            { op: "style_layer", layer_id: "nope", style: { color: "red" }, status: "ok" },
            map,
            layers,
        );
        // Nothing was added or modified.
        expect(layers.size).toBe(0);
    });
});
