"""Fetch BRGM RGA (retrait-gonflement des argiles) susceptibility polygons.

Géorisques WFS layer `ms:ALEARG_REALISE` → line-delimited GeoJSON → UC volume.
Cache-first: re-runs are no-ops unless `CATNAT_FORCE_FETCH=true` or `force=True`.

Licence: Etalab 2.0 / Licence Ouverte (Géorisques / BRGM).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from catnat.config import CONFIG
from catnat.fetch.base import (
    upload_to_volume,
    volume_exists,
    wfs_to_geojsonseq,
)

logger = logging.getLogger(__name__)

WFS_URL = "https://www.georisques.gouv.fr/services?service=WFS&version=2.0.0&srsName=EPSG:4326"
LAYER = "ms:ALEARG_REALISE"

# Pre-computed Lambert-93 generalized geometries shipped by Géorisques; redundant
# for our pipeline because we re-simplify in gold if needed.
_DROP_COLS = ("geom_l93_g1", "geom_l93_g5", "geom_l93_g10", "geom_l93_g25")


def remote_path(suffix: str) -> str:
    return f"{CONFIG.raw_volume_path}/rga/rga_{suffix}.geojsonl"


def fetch_and_upload(
    limit: int | None = 100,
    force: bool | None = None,
) -> tuple[str, int, bool]:
    """End-to-end: pull WFS, upload to volume. Returns (remote_path, count, cached).

    `cached=True` means the function returned the existing volume file without
    touching the network. Force a refresh with `force=True` or env
    `CATNAT_FORCE_FETCH=true`.
    """
    suffix = "sample" if limit is not None else "full"
    remote = remote_path(suffix)
    should_force = force if force is not None else CONFIG.force_fetch
    if not should_force and volume_exists(remote):
        logger.info("rga cache hit at %s", remote)
        return remote, -1, True
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"rga_{suffix}.geojsonl"
        n = wfs_to_geojsonseq(WFS_URL, LAYER, out, limit=limit, drop_columns=_DROP_COLS)
        upload_to_volume(out, remote)
    return remote, n, False
