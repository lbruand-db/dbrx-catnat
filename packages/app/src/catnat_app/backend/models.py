from pydantic import BaseModel
from .. import __version__


class VersionOut(BaseModel):
    version: str

    @classmethod
    def from_metadata(cls):
        return cls(version=__version__)


class Layer(BaseModel):
    """One row of `catnat_silver.layer_index`.

    Mirrors the table schema 1:1; the frontend types in
    `src/catnat_app/ui/types/layer.ts` track this shape.
    """

    layer_id: str
    table_fq: str
    peril: str
    medallion: str
    grain: str
    h3_column: str | None = None
    geom_column: str | None = None
    license: str
    is_displayable: bool
    description: str


class LayerListOut(BaseModel):
    layers: list[Layer]
