import L, { type Map as LeafletMap } from "leaflet";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MapOp } from "@/types/chat";
import { applyMapOp, type ManagedLayer } from "./map-dispatcher";

/**
 * `applyMapOp` is a thin wrapper around Leaflet's vectorGrid + GeoJSON
 * APIs. Tests stub the map handle + the vectorGrid factory with
 * vitest mocks — we assert on the calls rather than on the rendered
 * DOM. `L.vectorGrid.protobuf` is a plugin and not available in
 * jsdom by default; we install a mock at import-time below.
 */

interface MockVgLayer {
    addTo: ReturnType<typeof vi.fn>;
    setStyle?: ReturnType<typeof vi.fn>;
    _catnatLayerId?: string;
}

// Install a `L.vectorGrid.protobuf` factory before any test runs. The
// real plugin is loaded via `import "leaflet.vectorgrid"` inside the
// dispatcher, but it never gets to attach to the namespace under
// vitest (no canvas), so we stand in for it.
const vgFactory = vi.fn();
beforeEach(() => {
    vgFactory.mockReset();
    vgFactory.mockImplementation(() => {
        const layer: MockVgLayer = {
            addTo: vi.fn().mockReturnThis(),
            setStyle: vi.fn(),
        };
        return layer as unknown as ManagedLayer;
    });
    (
        L as unknown as {
            vectorGrid: { protobuf: typeof vgFactory };
            canvas: { tile: () => unknown };
        }
    ).vectorGrid = { protobuf: vgFactory };
    (L as unknown as { canvas: { tile: () => unknown } }).canvas = { tile: () => ({}) };
});

function makeMap(): LeafletMap {
    const m = {
        addLayer: vi.fn(),
        removeLayer: vi.fn(),
        fitBounds: vi.fn(),
    } as unknown as LeafletMap;
    return m;
}

describe("applyMapOp", () => {
    let map: LeafletMap;
    let layers: Map<string, ManagedLayer>;

    beforeEach(() => {
        map = makeMap();
        layers = new Map();
    });

    it("add_layer wires L.vectorGrid.protobuf with the tile URL", () => {
        const op: MapOp = {
            op: "add_layer",
            layer_id: "hazard_ppri_communes",
            peril: "flood",
            tile_url: "/api/tiles/hazard_ppri_communes/{z}/{x}/{y}.pbf",
            style: { color: "#1f77b4" },
            status: "ok",
        };
        applyMapOp(op, map, layers);
        expect(vgFactory).toHaveBeenCalledTimes(1);
        const [url, options] = vgFactory.mock.calls[0];
        expect(url).toBe(op.tile_url);
        // The MVT layer name must be the layer_id (matches what
        // ST_AsMVT writes on the backend).
        expect(options.vectorTileLayerStyles[op.layer_id]).toBeDefined();
        expect(options.vectorTileLayerStyles[op.layer_id].color).toBe("#1f77b4");
        // Registered under the layer_id and tagged for cleanup.
        const reg = layers.get("hazard_ppri_communes");
        expect(reg).toBeDefined();
        expect(reg?._catnatLayerId).toBe("hazard_ppri_communes");
    });

    it("add_layer replaces an existing layer with the same id", () => {
        const op: MapOp = {
            op: "add_layer",
            layer_id: "x",
            peril: "flood",
            tile_url: "/api/tiles/x/{z}/{x}/{y}.pbf",
            style: {},
            status: "ok",
        };
        applyMapOp(op, map, layers);
        const first = layers.get("x");
        applyMapOp(op, map, layers);
        expect(map.removeLayer).toHaveBeenCalledWith(first);
        // The new layer overwrote the old one.
        expect(layers.get("x")).not.toBe(first);
        expect(vgFactory).toHaveBeenCalledTimes(2);
    });

    it("remove_layer drops the layer from the map and the registry", () => {
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "x",
                peril: "flood",
                tile_url: "/api/tiles/x/{z}/{x}/{y}.pbf",
                style: {},
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

    it("zoom_to fits map bounds to the GeoJSON without adding a layer to the map", () => {
        applyMapOp(
            {
                op: "zoom_to",
                geom_geojson: {
                    type: "Polygon",
                    coordinates: [
                        [
                            [4.85, 45.75],
                            [4.86, 45.75],
                            [4.86, 45.76],
                            [4.85, 45.75],
                        ],
                    ],
                },
                status: "ok",
            },
            map,
            layers,
        );
        expect(map.fitBounds).toHaveBeenCalledTimes(1);
        // The temporary L.geoJSON used to compute bounds is never added.
        expect(layers.size).toBe(0);
    });

    it("style_layer forwards the style payload to the layer's setStyle", () => {
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "x",
                peril: "flood",
                tile_url: "/api/tiles/x/{z}/{x}/{y}.pbf",
                style: { color: "#aaa" },
                status: "ok",
            },
            map,
            layers,
        );
        const layer = layers.get("x") as unknown as MockVgLayer;
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
        expect(layer.setStyle).toHaveBeenCalledWith({ color: "#ff0000" });
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
