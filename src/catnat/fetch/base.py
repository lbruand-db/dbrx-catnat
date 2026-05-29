"""Shared plumbing for source fetchers.

Each peril-specific fetcher reuses these primitives:
- `volume_exists`: cache-check against the bronze raw volume.
- `wfs_to_geojsonseq`: pull an OGC WFS layer to line-delimited GeoJSON via
  pyogrio + GDAL. Drops requested columns (typically the pre-simplified
  Lambert-93 WKB blobs that come along for the ride from Géorisques).
- `upload_to_volume`: stream a local file into UC.

All fetches are **cache-first**: if the target file already exists in the
volume and `CATNAT_FORCE_FETCH` is unset/false, we skip the network call.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pyogrio
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

from catnat.config import CONFIG

logger = logging.getLogger(__name__)


def workspace_client() -> WorkspaceClient:
    return WorkspaceClient(profile=CONFIG.profile)


def volume_exists(remote_path: str, client: WorkspaceClient | None = None) -> bool:
    """True if `remote_path` (a /Volumes/... path) is present on UC."""
    c = client or workspace_client()
    try:
        c.files.get_metadata(remote_path)
        return True
    except NotFound:
        return False


def wfs_to_geojsonseq(
    wfs_url: str,
    layer: str,
    out_path: Path,
    limit: int | None = None,
    drop_columns: tuple[str, ...] = (),
) -> int:
    """Pull `layer` from `wfs_url` via pyogrio and write line-delimited GeoJSON.

    Returns the number of features written.
    """
    src = f"WFS:{wfs_url}"
    kwargs: dict[str, object] = {"layer": layer}
    if limit is not None:
        kwargs["max_features"] = limit
    gdf = pyogrio.read_dataframe(src, **kwargs)
    for col in drop_columns:
        if col in gdf.columns:
            gdf = gdf.drop(columns=[col])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(gdf, out_path, driver="GeoJSONSeq")
    return len(gdf)


def upload_to_volume(local_path: Path, remote_path: str) -> str:
    """Stream `local_path` to `remote_path` on UC, overwriting if present."""
    client = workspace_client()
    with local_path.open("rb") as src:
        client.files.upload(remote_path, src, overwrite=True)
    return remote_path
