/**
 * Analytical pane — stub for now. The full Kepler.gl integration needs a
 * Redux store + dataset injection, which is heavyweight. Phase 2 ships the
 * scaffold + placeholder; Phase 2.5 wires up the first real view (national
 * H3 hex map driven by the agent's `open_kepler_view` MCP tool).
 *
 * The `kepler.gl` package is installed so its peer-dep matrix is locked
 * with React 19, but we don't import it here yet to keep the bundle light.
 */
export function KeplerPane() {
    return (
        <section
            className="flex h-full w-full items-center justify-center bg-muted/30"
            aria-label="Analytical view"
            data-testid="kepler-pane"
        >
            <div className="max-w-md text-center space-y-2 p-6">
                <h2 className="text-lg font-semibold">Analytical view (Kepler.gl)</h2>
                <p className="text-sm text-muted-foreground">
                    Hex maps, time-animated event footprints, scenario side-by-sides land here in
                    Phase 2.5. For now this pane is a placeholder so the layout is realistic.
                </p>
            </div>
        </section>
    );
}
