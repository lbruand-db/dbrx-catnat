"""Fetch Géorisques PPRI (Plan de Prévention des Risques Inondation) layers.

The Géorisques WFS publishes per-commune polygons indicating which communes
are covered by a PPR Inondation, with two status flavors:

- `ms:PPRN_COMMUNE_RISQINOND_APPROUV`  — approved PPR Inondation
- `ms:PPRN_COMMUNE_RISQINOND_PRESCRIT` — prescribed PPR (not yet approved)

We pull both in one fetcher; bronze stores them as separate files keyed by
status, and silver unions them with a `status` column. The detailed in-PPRI
zoning (zone rouge / zone bleue) is **not** in this WFS — it's distributed
per-PPRI as DDT shapefiles, and is post-v1 (see SPEC §4.1).

Licence: Etalab 2.0 / Licence Ouverte.
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

LAYERS: dict[str, str] = {
    "approuv": "ms:PPRN_COMMUNE_RISQINOND_APPROUV",
    "prescrit": "ms:PPRN_COMMUNE_RISQINOND_PRESCRIT",
}


def remote_path(status: str, suffix: str) -> str:
    return f"{CONFIG.raw_volume_path}/ppri/ppri_{status}_{suffix}.geojsonl"


def fetch_status(
    status: str,
    limit: int | None,
    force: bool,
) -> tuple[str, int, bool]:
    """Pull one PPRI status layer. Returns (remote_path, count, cached)."""
    layer = LAYERS[status]
    suffix = "sample" if limit is not None else "full"
    remote = remote_path(status, suffix)
    if not force and volume_exists(remote):
        logger.info("ppri/%s cache hit at %s", status, remote)
        return remote, -1, True
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"ppri_{status}_{suffix}.geojsonl"
        n = wfs_to_geojsonseq(WFS_URL, layer, out, limit=limit)
        upload_to_volume(out, remote)
    return remote, n, False


def fetch_and_upload(
    limit: int | None = 200,
    force: bool | None = None,
) -> dict[str, tuple[str, int, bool]]:
    """Pull both PPRI status layers. Returns {status: (remote, count, cached)}."""
    should_force = force if force is not None else CONFIG.force_fetch
    return {status: fetch_status(status, limit, should_force) for status in LAYERS}
