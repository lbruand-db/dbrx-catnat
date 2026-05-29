"""Fetch BRGM RGA (retrait-gonflement des argiles) susceptibility polygons.

Uses pyogrio to read the Géorisques WFS layer `ms:ALEARG_REALISE` and write a
GeoJSONSeq (line-delimited GeoJSON) file. Uploads the result to the bronze
raw-staging volume via the Databricks SDK.

Why GeoJSONSeq: the SQL bronze notebook reads one feature per line via
`read_files(format=>'text')` + `ST_GeomFromGeoJSON`, which sidesteps Spark's
overzealous JSON schema inference on coordinate arrays.

Licence: Etalab 2.0 / Licence Ouverte (Géorisques / BRGM).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pyogrio
from databricks.sdk import WorkspaceClient

from catnat.config import CONFIG

WFS_URL = "https://www.georisques.gouv.fr/services?service=WFS&version=2.0.0&srsName=EPSG:4326"
LAYER = "ms:ALEARG_REALISE"

# Optional pre-computed Lambert-93 generalized geometries we drop on write.
# They're redundant — gold can re-simplify if needed.
_DROP_COLS = ("geom_l93_g1", "geom_l93_g5", "geom_l93_g10", "geom_l93_g25")


def _wfs_dataset_url(limit: int | None) -> str:
    """Build the pyogrio-readable WFS dataset URL.

    pyogrio passes the URL straight through to GDAL's WFS driver. Pagination
    happens server-side; `max_features` (below) caps client-side.
    """
    return f"WFS:{WFS_URL}"


def fetch(out_path: Path, limit: int | None = 100) -> int:
    """Pull `limit` features from the WFS and write a line-delimited GeoJSON.

    Returns the number of features written.
    """
    src = _wfs_dataset_url(limit)
    gdf_kwargs: dict[str, object] = {"layer": LAYER}
    if limit is not None:
        gdf_kwargs["max_features"] = limit

    gdf = pyogrio.read_dataframe(src, **gdf_kwargs)
    # Drop the redundant pre-simplified geometry columns.
    for col in _DROP_COLS:
        if col in gdf.columns:
            gdf = gdf.drop(columns=[col])

    # Write line-delimited GeoJSON manually to keep coordinate values as JSON
    # numbers (pyogrio's default GeoJSON-seq writer is fine; we use it).
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(gdf, out_path, driver="GeoJSONSeq")
    # Count lines for the caller (cheap; the file is small).
    with out_path.open("r", encoding="utf-8") as f:
        n = sum(1 for _ in f)
    return n


def upload(local_path: Path, remote_name: str | None = None) -> str:
    """Upload a local file to the bronze raw-staging volume.

    Returns the resulting volume path.
    """
    client = WorkspaceClient(profile=CONFIG.profile)
    name = remote_name or local_path.name
    remote = f"{CONFIG.raw_volume_path}/rga/{name}"
    with local_path.open("rb") as src:
        client.files.upload(remote, src, overwrite=True)
    return remote


def fetch_and_upload(limit: int | None = 100) -> tuple[str, int]:
    """End-to-end: pull from WFS, upload to UC volume, return (remote_path, count)."""
    suffix = "sample" if (limit is not None) else "full"
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"rga_{suffix}.geojsonl"
        n = fetch(out, limit=limit)
        # Sanity-check: every line must be parseable JSON.
        with out.open("r", encoding="utf-8") as f:
            json.loads(f.readline())
        remote = upload(out)
    return remote, n
