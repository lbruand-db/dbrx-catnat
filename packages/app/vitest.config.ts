import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// Vitest config — kept standalone of the apx-managed vite.config so we own
// the test environment without fighting the dev-server runtime. Path alias
// mirrors tsconfig.json (`@/*` → `src/catnat_app/ui/*`).
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": fileURLToPath(new URL("./src/catnat_app/ui", import.meta.url)),
        },
        // Force a single React copy: kepler.gl pulls react-palm@3.3.x which
        // brings its own react-dom/react-reconciler from the React 16 era.
        // Without dedupe, the Kepler pane test hits "Cannot read properties
        // of null (reading 'useMemo')" from a stale hook dispatcher.
        dedupe: ["react", "react-dom"],
    },
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./vitest.setup.ts"],
        include: ["src/catnat_app/ui/**/*.test.{ts,tsx}"],
        coverage: {
            provider: "v8",
            reporter: ["text", "html"],
            include: ["src/catnat_app/ui/**/*.{ts,tsx}"],
            exclude: [
                "src/catnat_app/ui/types/**",
                "src/catnat_app/ui/main.tsx",
                "src/catnat_app/ui/**/*.test.{ts,tsx}",
            ],
        },
    },
});
