import { createFileRoute } from "@tanstack/react-router";
import { ChatPane } from "@/components/catnat/chat-pane";
import { KeplerPane } from "@/components/catnat/kepler-pane";
import { LeafletPane } from "@/components/catnat/leaflet-pane";
import { useLayers } from "@/hooks/use-layers";

export const Route = createFileRoute("/")({
    component: () => <DemoLayout />,
});

/**
 * GeoCatNat 3-pane layout. Top-left = operational Leaflet map; bottom-left =
 * analytical Kepler stub; right column = chat / agent.
 *
 * Loading state is intentionally lightweight — the layer registry returns
 * ~11 rows; first paint should be near-instant once the warehouse is warm.
 */
function DemoLayout() {
    const { data: layers, error } = useLayers();

    return (
        <div className="grid h-screen w-screen grid-cols-[1fr_24rem] grid-rows-[2fr_1fr] overflow-hidden">
            <section
                className="overflow-hidden border-b"
                aria-label="Operational map"
                data-testid="leaflet-section"
            >
                <LeafletPane layers={layers ?? []} />
            </section>
            <section
                className="row-span-2 border-l overflow-hidden"
                aria-label="Chat"
                data-testid="chat-section"
            >
                <ChatPane />
            </section>
            <section
                className="overflow-hidden"
                aria-label="Analytical view"
                data-testid="kepler-section"
            >
                <KeplerPane />
            </section>
            {error && (
                <div className="absolute bottom-4 left-4 rounded-md bg-destructive p-3 text-sm text-destructive-foreground">
                    Failed to load layers: {error.message}
                </div>
            )}
        </div>
    );
}
