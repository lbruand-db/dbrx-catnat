import { createFileRoute } from "@tanstack/react-router";
import { ChatPane } from "@/components/catnat/chat-pane";
import { LeafletPane } from "@/components/catnat/leaflet-pane";
import { useLayers } from "@/hooks/use-layers";

export const Route = createFileRoute("/")({
    component: () => <DemoLayout />,
});

/**
 * GeoCatNat 2-pane layout. Left = operational Leaflet map; right = chat /
 * agent. The Kepler analytical pane was removed pending a clearer use case.
 */
function DemoLayout() {
    const { data: layers, error } = useLayers();

    return (
        <div className="grid h-screen w-screen grid-cols-[1fr_24rem] overflow-hidden">
            <section
                className="overflow-hidden border-b"
                aria-label="Operational map"
                data-testid="leaflet-section"
            >
                <LeafletPane layers={layers ?? []} />
            </section>
            <section
                className="border-l overflow-hidden"
                aria-label="Chat"
                data-testid="chat-section"
            >
                <ChatPane />
            </section>
            {error && (
                <div className="absolute bottom-4 left-4 rounded-md bg-destructive p-3 text-sm text-destructive-foreground">
                    Failed to load layers: {error.message}
                </div>
            )}
        </div>
    );
}
