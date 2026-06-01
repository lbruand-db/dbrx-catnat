import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet } from "@tanstack/react-router";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/components/apx/theme-provider";

export const Route = createRootRouteWithContext<{
    queryClient: QueryClient;
}>()({
    component: () => (
        <ThemeProvider defaultTheme="dark" storageKey="apx-ui-theme">
            <Outlet />
            <Toaster richColors />
        </ThemeProvider>
    ),
});
