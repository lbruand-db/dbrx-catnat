import { createFileRoute } from "@tanstack/react-router";
import type L from "leaflet";
import type { Map as LeafletMap } from "leaflet";
import { useCallback, useRef } from "react";
import { ChatPane } from "@/components/catnat/chat-pane";
import { LeafletPane } from "@/components/catnat/leaflet-pane";
import { useLayers } from "@/hooks/use-layers";
import { applyMapOp } from "@/lib/map-dispatcher";
import type { ChatContext, MapOp } from "@/types/chat";

export const Route = createFileRoute("/")({
    component: () => <DemoLayout />,
});

/**
 * GeoCatNat 2-pane layout. Left = operational Leaflet map; right = chat /
 * agent. The chat pane streams `map_op` events from `/api/chat`; we lift
 * the map handle here so the dispatcher can mutate it on the agent's
 * behalf (add / remove / zoom / style).
 */
function DemoLayout() {
    const { data: layers, error } = useLayers();

    // The Leaflet map handle + the set of agent-added GeoJSON layers,
    // both held in refs so we can mutate them across renders without
    // forcing a re-render of either pane.
    const mapRef = useRef<LeafletMap | null>(null);
    const agentLayersRef = useRef<Map<string, L.GeoJSON>>(new Map());

    const handleMapReady = useCallback((m: LeafletMap) => {
        mapRef.current = m;
    }, []);

    const handleMapOp = useCallback((op: MapOp) => {
        const map = mapRef.current;
        if (!map) return;
        applyMapOp(op, map, agentLayersRef.current);
    }, []);

    // Read the current Leaflet view + the set of agent-added layers at
    // chat-send time. This is the reverse channel from UI.md §3.2.1 —
    // the agent learns what the user is looking at without having to
    // call a tool to find out.
    const getChatContext = useCallback((): ChatContext | null => {
        const map = mapRef.current;
        if (!map) return null;
        const bounds = map.getBounds();
        const center = map.getCenter();
        return {
            viewport: {
                bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
                zoom: map.getZoom(),
                center: [center.lng, center.lat],
            },
            active_layers: Array.from(agentLayersRef.current.keys()).map((layer_id) => ({
                layer_id,
            })),
        };
    }, []);

    return (
        <div className="grid h-screen w-screen grid-cols-[1fr_24rem] overflow-hidden">
            <section
                className="overflow-hidden border-b"
                aria-label="Operational map"
                data-testid="leaflet-section"
            >
                <LeafletPane layers={layers ?? []} onMapReady={handleMapReady} />
            </section>
            <section
                className="border-l overflow-hidden"
                aria-label="Chat"
                data-testid="chat-section"
            >
                <ChatPane onMapOp={handleMapOp} getContext={getChatContext} />
            </section>
            {error && (
                <div className="absolute bottom-4 left-4 rounded-md bg-destructive p-3 text-sm text-destructive-foreground">
                    Failed to load layers: {error.message}
                </div>
            )}
        </div>
    );
}
