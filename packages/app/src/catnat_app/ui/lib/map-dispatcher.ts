import L, { type Map as LeafletMap } from "leaflet";
import type { MapOp } from "@/types/chat";

/**
 * Apply a `MapOp` (emitted by the agent via the `map_op` SSE event) to
 * a Leaflet map handle. Maintains an external `layers` Map so
 * `remove_layer` / `style_layer` can find what `add_layer` produced.
 *
 * Kept as a plain function (not a class / hook) so tests can drive it
 * directly with a mocked map.
 */
export function applyMapOp(op: MapOp, map: LeafletMap, layers: Map<string, L.GeoJSON>): void {
    switch (op.op) {
        case "add_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
                map.removeLayer(existing);
            }
            const layer = L.geoJSON(op.geojson, { style: op.style as L.PathOptions });
            layer.addTo(map);
            layers.set(op.layer_id, layer);
            // Center on what we just added so the user sees it.
            const bounds = layer.getBounds();
            if (bounds.isValid()) {
                map.fitBounds(bounds, { padding: [20, 20], maxZoom: 12 });
            }
            return;
        }
        case "remove_layer": {
            const existing = layers.get(op.layer_id);
            if (existing) {
                map.removeLayer(existing);
                layers.delete(op.layer_id);
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
                existing.setStyle(op.style as L.PathOptions);
            }
            return;
        }
    }
}
