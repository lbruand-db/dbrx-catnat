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
    setFeatureStyle?: ReturnType<typeof vi.fn>;
    resetFeatureStyle?: ReturnType<typeof vi.fn>;
    _catnatLayerId?: string;
    _catnatSelectedFeatureId?: string;
    on?: ReturnType<typeof vi.fn>;
    /** Captured by the mock's `.on('click', ...)` so tests can fire it. */
    _clickHandler?: (e: {
        latlng: { lat: number; lng: number };
        layer?: { properties?: Record<string, unknown> };
    }) => void;
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
            setFeatureStyle: vi.fn(),
            resetFeatureStyle: vi.fn(),
        };
        // Capture the click handler so we can invoke it from tests.
        layer.on = vi.fn((kind, handler) => {
            if (kind === "click") {
                layer._clickHandler = handler;
            }
        });
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
        // `interactive: true` at top-level is what makes Canvas-tile
        // pointer-events flow through (verified against the plugin
        // source at L.Canvas.Tile.initialize).
        expect(options.interactive).toBe(true);
        // Registered under the layer_id and tagged for cleanup.
        const reg = layers.get("hazard_ppri_communes");
        expect(reg).toBeDefined();
        expect(reg?._catnatLayerId).toBe("hazard_ppri_communes");
    });

    it("getFeatureId returns a stable id (not Math.random) for the same properties", () => {
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "hazard_rga_susceptibility",
                peril: "drought",
                tile_url: "/api/tiles/hazard_rga_susceptibility/{z}/{x}/{y}.pbf",
                style: {},
                status: "ok",
            },
            map,
            layers,
        );
        const [, options] = vgFactory.mock.calls[0];
        // RGA features carry `insee_dep` + `susceptibility_code`; the
        // bug we're regressing was returning Math.random() in that
        // case, which made setFeatureStyle never find the same feature
        // twice. Stable ids must be identical across two invocations.
        const props = { insee_dep: "69", susceptibility_code: "FORT" };
        const a = options.getFeatureId({ properties: props });
        const b = options.getFeatureId({ properties: props });
        expect(a).toBe(b);
        expect(typeof a).toBe("string");

        // Different properties → different id.
        const c = options.getFeatureId({
            properties: { insee_dep: "69", susceptibility_code: "FAIBLE" },
        });
        expect(c).not.toBe(a);
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

    it("add_layer wires a click handler that fires onSelectionChange + highlights", () => {
        const onSelectionChange = vi.fn();
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "hazard_ppri_communes",
                peril: "flood",
                tile_url: "/api/tiles/hazard_ppri_communes/{z}/{x}/{y}.pbf",
                style: {},
                status: "ok",
            },
            map,
            layers,
            onSelectionChange,
        );
        const layer = layers.get("hazard_ppri_communes") as unknown as MockVgLayer;
        expect(layer.on).toHaveBeenCalledWith("click", expect.any(Function));

        // Fire the click handler that the dispatcher registered.
        layer._clickHandler?.({
            latlng: { lat: 45.764, lng: 4.835 },
            layer: { properties: { code_insee: "69123", nom_officiel: "Lyon" } },
        });
        expect(onSelectionChange).toHaveBeenCalledWith({
            layer_id: "hazard_ppri_communes",
            properties: { code_insee: "69123", nom_officiel: "Lyon" },
            latlng: [45.764, 4.835],
        });
        // Visible feedback: the clicked feature gets re-symbolised.
        expect(layer.setFeatureStyle).toHaveBeenCalledTimes(1);
        const [featureId, highlight] = (
            layer.setFeatureStyle as unknown as { mock: { calls: [string, unknown][] } }
        ).mock.calls[0];
        expect(featureId).toBe("code_insee=69123");
        expect(highlight).toMatchObject({ color: "#ffcc00", fillColor: "#ffcc00" });
        // First click — no prior selection to reset.
        expect(layer.resetFeatureStyle).not.toHaveBeenCalled();
        expect(layer._catnatSelectedFeatureId).toBe("code_insee=69123");
    });

    it("clicking a different feature resets the previous highlight before painting the new one", () => {
        const onSelectionChange = vi.fn();
        applyMapOp(
            {
                op: "add_layer",
                layer_id: "hazard_ppri_communes",
                peril: "flood",
                tile_url: "/api/tiles/hazard_ppri_communes/{z}/{x}/{y}.pbf",
                style: {},
                status: "ok",
            },
            map,
            layers,
            onSelectionChange,
        );
        const layer = layers.get("hazard_ppri_communes") as unknown as MockVgLayer;
        // First click on Lyon.
        layer._clickHandler?.({
            latlng: { lat: 45.764, lng: 4.835 },
            layer: { properties: { code_insee: "69123" } },
        });
        // Second click on Vénissieux.
        layer._clickHandler?.({
            latlng: { lat: 45.697, lng: 4.886 },
            layer: { properties: { code_insee: "69256" } },
        });
        // Lyon's highlight must be reverted before Vénissieux's is
        // painted, otherwise both stay yellow and the user sees a
        // misleading multi-selection.
        expect(layer.resetFeatureStyle).toHaveBeenCalledWith("code_insee=69123");
        expect(layer.setFeatureStyle).toHaveBeenCalledTimes(2);
        const calls = (layer.setFeatureStyle as unknown as { mock: { calls: [string, unknown][] } })
            .mock.calls;
        expect(calls[1][0]).toBe("code_insee=69256");
        expect(layer._catnatSelectedFeatureId).toBe("code_insee=69256");
    });

    it("clicking the same feature twice is a no-op for resetFeatureStyle", () => {
        const onSelectionChange = vi.fn();
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
            onSelectionChange,
        );
        const layer = layers.get("x") as unknown as MockVgLayer;
        layer._clickHandler?.({
            latlng: { lat: 0, lng: 0 },
            layer: { properties: { code_insee: "69123" } },
        });
        layer._clickHandler?.({
            latlng: { lat: 0, lng: 0 },
            layer: { properties: { code_insee: "69123" } },
        });
        // Same id both times → never reset.
        expect(layer.resetFeatureStyle).not.toHaveBeenCalled();
        // setFeatureStyle still fires both times — the plugin de-dupes.
        expect(layer.setFeatureStyle).toHaveBeenCalledTimes(2);
    });

    it("click on a feature with no properties does not fire the callback", () => {
        const onSelectionChange = vi.fn();
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
            onSelectionChange,
        );
        const layer = layers.get("x") as unknown as MockVgLayer;
        layer._clickHandler?.({ latlng: { lat: 0, lng: 0 } });
        expect(onSelectionChange).not.toHaveBeenCalled();
    });

    it("remove_layer clears the selection callback with null", () => {
        const onSelectionChange = vi.fn();
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
            onSelectionChange,
        );
        applyMapOp(
            { op: "remove_layer", layer_id: "x", status: "ok" },
            map,
            layers,
            onSelectionChange,
        );
        expect(onSelectionChange).toHaveBeenCalledWith(null);
    });
});
