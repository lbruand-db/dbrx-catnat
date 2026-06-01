import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import type { Layer, LayerListOut } from "@/types/layer";

const LAYERS_KEY = ["layers"] as const;

async function fetchLayers(): Promise<Layer[]> {
    const { data } = await axios.get<LayerListOut>("/api/layers");
    return data.layers;
}

/**
 * Loads every layer the demo can surface from `GET /api/layers`.
 *
 * The route reads `catnat_silver.layer_index` via the user's SQL warehouse
 * OBO token — Same RLS as any other catnat query the user runs themselves.
 */
export function useLayers() {
    return useQuery({
        queryKey: LAYERS_KEY,
        queryFn: fetchLayers,
        staleTime: 5 * 60_000, // 5 min — registry rarely changes
    });
}

export const __test__ = { LAYERS_KEY, fetchLayers };
