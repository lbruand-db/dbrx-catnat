import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/styles/globals.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { createRouter, RouterProvider } from "@tanstack/react-router";
import { Provider as ReduxProvider } from "react-redux";
import { keplerStore } from "@/lib/kepler-store";
import { routeTree } from "@/types/routeTree.gen";

// Create a new query client instance
const queryClient = new QueryClient();

const router = createRouter({
    routeTree,
    context: {
        queryClient,
    },
    defaultPreload: "intent",
    // Since we're using React Query, we don't want loader calls to ever be stale
    // This will ensure that the loader is always called when the route is preloaded or visited
    defaultPreloadStaleTime: 0,
    scrollRestoration: true,
});

// Register things for typesafety
declare module "@tanstack/react-router" {
    interface Register {
        router: typeof router;
    }
}

const rootElement = document.getElementById("root")!;

if (!rootElement.innerHTML) {
    const root = createRoot(rootElement);
    root.render(
        <StrictMode>
            <ReduxProvider store={keplerStore}>
                <QueryClientProvider client={queryClient}>
                    <RouterProvider router={router} />
                </QueryClientProvider>
            </ReduxProvider>
        </StrictMode>,
    );
}
