import { useQuery } from "@tanstack/react-query";
import axios from "axios";

export interface KeplerPortfolioRow {
    h3: string;
    code_dep: string;
    n_policies: number | null;
    sum_insured_value_eur: number | null;
    n_flood: number | null;
    n_rga: number | null;
    n_storm: number | null;
}

export interface KeplerPortfolioDataset {
    id: string;
    label: string;
    fields: string[];
    rows: KeplerPortfolioRow[];
}

async function fetchPortfolio(): Promise<KeplerPortfolioDataset> {
    const { data } = await axios.get<KeplerPortfolioDataset>("/api/kepler/portfolio");
    return data;
}

/**
 * Loads the portfolio H3 rollup keyed for Kepler's H3 layer. Single fetch,
 * long stale-time — the underlying gold table is computed by the
 * `catnat_portfolio` DAB job, not by a user-driven action.
 */
export function useKeplerPortfolio() {
    return useQuery({
        queryKey: ["kepler", "portfolio"],
        queryFn: fetchPortfolio,
        staleTime: 10 * 60_000, // 10 min
    });
}
