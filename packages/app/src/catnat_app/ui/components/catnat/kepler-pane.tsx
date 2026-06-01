import { addDataToMap } from "@kepler.gl/actions";
import KeplerGl from "@kepler.gl/components";
import { processRowObject } from "@kepler.gl/processors";
import { useEffect, useRef, useState } from "react";
import { useDispatch } from "react-redux";
import { useKeplerPortfolio } from "@/hooks/use-kepler-portfolio";

const MAP_ID = "catnat-analytical";

/**
 * Analytical pane — Kepler.gl wired to the portfolio H3 gold rollup.
 *
 * Loads `/api/kepler/portfolio` once via TanStack Query, hands the rows to
 * `processRowObject` (Kepler's CSV-like row → field/values converter), then
 * dispatches `addDataToMap` into the Redux store wired in main.tsx.
 *
 * Kepler picks the H3 column automatically (the field is named `h3` and its
 * values look like 15-char hex strings — its H3 layer recognises both).
 * We pass a tiny `mapState` override so the camera lands on Lyon by default;
 * everything else (colour ramp, choropleth field) is left for Kepler to
 * auto-configure so the user can experiment freely.
 */
export function KeplerPane() {
    const dispatch = useDispatch();
    const { data, error } = useKeplerPortfolio();
    const loaded = useRef(false);
    const containerRef = useRef<HTMLDivElement>(null);
    const [size, setSize] = useState({ width: 0, height: 0 });

    // Resize observer — Kepler needs explicit pixel dimensions and won't
    // self-size to its container.
    useEffect(() => {
        const el = containerRef.current;
        if (!el) return;
        const observer = new ResizeObserver(([entry]) => {
            const { width, height } = entry.contentRect;
            setSize({ width: Math.floor(width), height: Math.floor(height) });
        });
        observer.observe(el);
        return () => observer.disconnect();
    }, []);

    // Inject the dataset once on first fetch; remount-on-strict-mode is
    // guarded by `loaded.current`.
    useEffect(() => {
        if (!data || loaded.current) return;
        loaded.current = true;
        const processed = processRowObject(data.rows);
        if (!processed) return;
        dispatch(
            addDataToMap({
                datasets: [{ info: { id: data.id, label: data.label }, data: processed }],
                options: { centerMap: true, readOnly: false },
                config: {
                    mapState: { latitude: 45.75, longitude: 4.85, zoom: 9 },
                    mapStyle: { styleType: "dark" },
                },
            }),
        );
    }, [data, dispatch]);

    return (
        <section
            ref={containerRef}
            className="relative h-full w-full"
            aria-label="Analytical view"
            data-testid="kepler-pane"
        >
            {size.width > 0 && size.height > 0 && (
                <KeplerGl
                    id={MAP_ID}
                    width={size.width}
                    height={size.height}
                    mapboxApiAccessToken={import.meta.env.VITE_MAPBOX_TOKEN ?? ""}
                />
            )}
            {error && (
                <div className="absolute bottom-2 left-2 rounded bg-destructive p-2 text-xs text-destructive-foreground">
                    Kepler dataset failed to load: {error.message}
                </div>
            )}
        </section>
    );
}
