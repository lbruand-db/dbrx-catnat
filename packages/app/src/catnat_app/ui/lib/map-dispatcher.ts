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
};

/** Callback signature for clicks on agent-added layers. */
export type SelectionChangeHandler = (selection: FeatureSelection | null) => void;

interface VectorTileClickEvent {
    latlng: { lat: number; lng: number };
    layer?: { properties?: Record<string, unknown> };
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
                getFeatureId: (f: { properties?: { code_insee?: string } }) =>
                    f.properties?.code_insee ?? Math.random().toString(36),
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

            // Wire the click handler that turns a Leaflet click into a
            // selection update. `e.layer.properties` carries the
            // clicked vector-tile feature's attributes; `e.latlng` is
            // the click position. The agent will see both as system
            // context on the next /api/chat turn.
            if (onSelectionChange) {
                const eventfulLayer = layer as unknown as {
                    on: (kind: "click", handler: (e: VectorTileClickEvent) => void) => void;
                };
                eventfulLayer.on("click", (e: VectorTileClickEvent) => {
                    if (!e.layer?.properties) return;
                    onSelectionChange({
                        layer_id: op.layer_id,
                        properties: e.layer.properties,
                        latlng: [e.latlng.lat, e.latlng.lng],
                    });
                });
            }

            layer.addTo(map);
            layers.set(op.layer_id, layer);
            return;
        }
        case "remove_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
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
