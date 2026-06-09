import L, { type Map as LeafletMap } from "leaflet";
import "leaflet.vectorgrid";
import type { FeatureSelection, MapOp } from "@/types/chat";

/**
 * Apply a `MapOp` (emitted by the agent via the `map_op` SSE event) to
 * a Leaflet map handle. Maintains an external `layers` Map so
 * `remove_layer` / `style_layer` can find what `add_layer` produced.
 *
 * Per SPEC §10.7, `add_layer` payloads carry a `tile_url` template; we
 * render via `L.vectorGrid.protobuf` against the Lakebase MVT
 * endpoint (`/api/tiles/<layer>/{z}/{x}/{y}.pbf`). `zoom_to` still
 * uses an off-map `L.geoJSON` purely to compute bounds.
 *
 * The optional `onSelectionChange` callback (UI.md §3.2 reverse
 * channel) fires when the user clicks a feature on an `add_layer`
 * surface — the next chat turn then carries the click as context.
 *
 * Kept as a plain function (not a class / hook) so tests can drive it
 * directly with a mocked map.
 */
export type ManagedLayer = L.Layer & {
    /** Stash the layer_id we registered under so cleanup can match. */
    _catnatLayerId?: string;
    /** Currently highlighted feature id (per `getFeatureId`) for selection. */
    _catnatSelectedFeatureId?: string;
};

/** Callback signature for clicks on agent-added layers. */
export type SelectionChangeHandler = (selection: FeatureSelection | null) => void;

interface VectorTileClickEvent {
    latlng: { lat: number; lng: number };
    layer?: { properties?: Record<string, unknown> };
}

/**
 * Highlight style applied to the currently-clicked feature via
 * `setFeatureStyle`. Loud on purpose — the demo audience needs to see
 * which feature the agent thinks the user pointed at, regardless of
 * the underlying layer's base palette. Yellow against any peril.
 */
const SELECTION_HIGHLIGHT_STYLE = {
    color: "#ffcc00",
    weight: 3,
    fillColor: "#ffcc00",
    fillOpacity: 0.6,
    fill: true,
} as const;

/**
 * Build a stable id from a feature's properties.
 *
 * `getFeatureId` is what makes `setFeatureStyle` / `resetFeatureStyle`
 * work — without a stable id, the plugin can't find the same feature
 * across tile boundaries, so the highlight flickers or never appears.
 *
 * We try a list of known-unique fields first (the demo's hazard layers
 * all carry one of these), then fall back to a deterministic stringify
 * of the whole properties dict. The fallback is the safety net —
 * `Math.random()` (what we used to do) was the bug.
 */
function stableFeatureId(properties: Record<string, unknown> | undefined): string {
    if (!properties) return "__no_props__";
    // Ground-truthed against silver tables: communes use code_insee,
    // PPRI rows use cod_commune, RGA's per-(commune × susceptibility)
    // rows can be uniquely keyed by INSEE + susceptibility_code, TRI
    // by INSEE + scenario_code. Try the cheap candidates first.
    const stableFields = ["code_insee", "cod_commune", "id_iripprn", "id_pprn", "gid"];
    for (const k of stableFields) {
        const v = properties[k];
        if (typeof v === "string" && v.length > 0) return `${k}=${v}`;
        if (typeof v === "number") return `${k}=${v}`;
    }
    // Compound id: try INSEE + a discriminator for layers that store
    // multiple rows per commune.
    const insee = properties.insee_com ?? properties.insee_dep;
    const disc =
        properties.susceptibility_code ??
        properties.scenario_code ??
        properties.intensity_code ??
        properties.peril_kind;
    if (insee !== undefined && disc !== undefined) return `${insee}|${disc}`;
    // Last resort: deterministic property stringification. Stable
    // per-feature, even if duplicated for rows with identical
    // attributes (rare and acceptable).
    const keys = Object.keys(properties).sort();
    return keys.map((k) => `${k}=${String(properties[k])}`).join("|");
}

export function applyMapOp(
    op: MapOp,
    map: LeafletMap,
    layers: Map<string, ManagedLayer>,
    onSelectionChange?: SelectionChangeHandler,
): void {
    switch (op.op) {
        case "add_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
                map.removeLayer(existing);
            }
            const style = op.style as Record<string, unknown>;
            // L.vectorGrid.protobuf is added by the leaflet.vectorgrid
            // plugin; the @types/leaflet bundle doesn't know about it,
            // so we cast through a thin alias.
            const vgFactory = (
                L as unknown as {
                    vectorGrid: {
                        protobuf: (url: string, opts: unknown) => ManagedLayer;
                    };
                }
            ).vectorGrid.protobuf;
            const layer = vgFactory(op.tile_url, {
                rendererFactory: (L as unknown as { canvas: { tile: unknown } }).canvas.tile,
                interactive: true,
                getFeatureId: (f: { properties?: Record<string, unknown> }) =>
                    stableFeatureId(f.properties),
                vectorTileLayerStyles: {
                    // Layer name in the MVT must match the second
                    // arg to ST_AsMVT on the backend (= the layer_id).
                    [op.layer_id]: {
                        color: style.color ?? "#3388ff",
                        fillColor: style.fillColor ?? style.color ?? "#3388ff",
                        fillOpacity: style.fillOpacity ?? 0.35,
                        weight: style.weight ?? 1,
                        fill: true,
                    },
                },
            });
            layer._catnatLayerId = op.layer_id;

            // Wire the click handler. Two responsibilities:
            //  1. Highlight the clicked feature via `setFeatureStyle`
            //     (loud yellow) so the user sees the click landed.
            //  2. Fire `onSelectionChange` so the next /api/chat turn
            //     carries the clicked feature in the reverse-channel
            //     context block (UI.md §3.2.1).
            //
            // The plugin's API used here: `setFeatureStyle(id, style)`
            // re-symbolizes the feature across every tile it spans;
            // `resetFeatureStyle(id)` reverts. Both require the same
            // stable id from `getFeatureId` — see `stableFeatureId`.
            const styledLayer = layer as unknown as {
                setFeatureStyle?: (id: string, s: unknown) => void;
                resetFeatureStyle?: (id: string) => void;
                on: (kind: "click", handler: (e: VectorTileClickEvent) => void) => void;
            };
            styledLayer.on("click", (e: VectorTileClickEvent) => {
                if (!e.layer?.properties) return;
                const featureId = stableFeatureId(e.layer.properties);
                // Clear the previous selection highlight on this layer
                // (if any) before painting the new one.
                const prev = layer._catnatSelectedFeatureId;
                if (prev && prev !== featureId && styledLayer.resetFeatureStyle) {
                    styledLayer.resetFeatureStyle(prev);
                }
                if (styledLayer.setFeatureStyle) {
                    styledLayer.setFeatureStyle(featureId, SELECTION_HIGHLIGHT_STYLE);
                }
                layer._catnatSelectedFeatureId = featureId;
                onSelectionChange?.({
                    layer_id: op.layer_id,
                    properties: e.layer.properties,
                    latlng: [e.latlng.lat, e.latlng.lng],
                });
            });

            layer.addTo(map);
            layers.set(op.layer_id, layer);
            return;
        }
        case "remove_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
                // Clear the per-layer highlight tag before removal so a
                // future re-add starts clean. (The layer itself is
                // dropped, so the style reset isn't strictly needed.)
                existing._catnatSelectedFeatureId = undefined;
                map.removeLayer(existing);
                layers.delete(op.layer_id);
                // Clear any selection that pointed at the removed
                // layer so the agent doesn't see a stale reference.
                onSelectionChange?.(null);
            }
            return;
        }
        case "zoom_to": {
            // L.geoJSON wraps any GeoJSON Geometry / Feature into a layer
            // we can read bounds from. Don't add it to the map — we only
            // want the camera move.
            const tmp = L.geoJSON(op.geom_geojson as GeoJSON.GeoJsonObject);
            const bounds = tmp.getBounds();
            if (bounds.isValid()) {
                map.fitBounds(bounds, { padding: [20, 20], maxZoom: 14 });
            }
            return;
        }
        case "style_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
                const setStyle = (existing as unknown as { setStyle?: (s: unknown) => void })
                    .setStyle;
                if (typeof setStyle === "function") {
                    setStyle.call(existing, op.style);
                }
            }
            return;
        }
    }
}
