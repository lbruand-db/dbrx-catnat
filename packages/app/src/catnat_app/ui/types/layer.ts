/**
 * Mirrors `Layer` in `src/catnat_app/backend/models.py`. Keep these in sync —
 * if the table shape changes, regenerate from the Pydantic model or update
 * both ends together.
 */
export interface Layer {
    layer_id: string;
    table_fq: string;
    peril: "flood" | "drought" | "storm" | "reference" | "portfolio";
    medallion: "silver" | "gold";
    grain: string;
    h3_column: string | null;
    geom_column: string | null;
    license: string;
    is_displayable: boolean;
    description: string;
}

export interface LayerListOut {
    layers: Layer[];
}
