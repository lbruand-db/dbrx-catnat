import L, { type Map as LeafletMap } from "leaflet";
import { useEffect, useRef } from "react";
import "leaflet/dist/leaflet.css";
import type { Layer } from "@/types/layer";

export interface LeafletPaneProps {
    /** Center map on this {lat, lon}. Defaults to Lyon. */
    center?: [number, number];
    /** Initial zoom level. */
    zoom?: number;
    /** Catnat layers to show in the side panel — populated by `useLayers()`. */
    layers?: Layer[];
    /** Optional test hook to receive the underlying Leaflet map instance. */
    onMapReady?: (map: LeafletMap) => void;
}

const DEFAULT_CENTER: [number, number] = [45.75, 4.85]; // Lyon
const DEFAULT_ZOOM = 11;

/**
 * Operational Leaflet pane — the demo's primary map.
 *
 * Imperative wrapper (not `react-leaflet`) because we need direct access to
 * the map instance for the MCP layer-ops tools later (`add_layer`,
 * `style_layer`, `zoom_to`). Wrapping in react-leaflet adds a layer of
 * indirection that would fight the agent-driven mutations of Phase 4.
 */
export function LeafletPane({
    center = DEFAULT_CENTER,
    zoom = DEFAULT_ZOOM,
    layers = [],
    onMapReady,
}: LeafletPaneProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<LeafletMap | null>(null);

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;
        const map = L.map(containerRef.current).setView(center, zoom);
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "© OpenStreetMap contributors",
            maxZoom: 19,
        }).addTo(map);
        mapRef.current = map;
        onMapReady?.(map);
        return () => {
            map.remove();
            mapRef.current = null;
        };
    }, [center, zoom, onMapReady]);

    const displayable = layers.filter((l) => l.is_displayable);

    return (
        <div className="flex h-full w-full">
            <aside
                className="w-64 border-r overflow-y-auto p-3 text-sm"
                aria-label="Layer picker"
                data-testid="leaflet-layer-picker"
            >
                <h2 className="font-medium mb-2">Layers</h2>
                {displayable.length === 0 ? (
                    <p className="text-muted-foreground italic">No layers loaded yet.</p>
                ) : (
                    <ul className="space-y-1">
                        {displayable.map((layer) => (
                            <li
                                key={layer.layer_id}
                                className="rounded px-2 py-1 hover:bg-muted"
                                data-peril={layer.peril}
                            >
                                <span className="font-mono text-xs text-muted-foreground">
                                    {layer.peril}
                                </span>{" "}
                                {layer.layer_id}
                            </li>
                        ))}
                    </ul>
                )}
            </aside>
            <section
                ref={containerRef}
                className="flex-1"
                aria-label="Map"
                data-testid="leaflet-map"
            />
        </div>
    );
}
