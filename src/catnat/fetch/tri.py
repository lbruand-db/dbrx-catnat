"""Fetch Géorisques TRI flood-hazard footprints (EU Floods Directive).

TRI = Territoires à Risque Important d'inondation. The Géorisques WFS
publishes a grid of layers keyed by (return-period scenario × hazard intensity):

  scenario codes:
    01 = Fréquent  (high probability — ~10 to 30-year return)
    02 = Moyen     (medium probability — ~100 to 300-year return)
    03 = Extrême   (low probability — ~1000-year return)

  intensity codes:
    01FOR = Fort      (strong)
    02MOY = Moyen     (medium)
    03MCC = MCC       (Moyen Courant — speed/transit class, type-specific)
    04FAI = Faible    (weak)

Eleven `ALEA_SYNT_<scenario>_<intensity>_FXX` layers cover metropolitan France
(some scenario × intensity pairs don't exist — e.g. there's no `02_03MCC`).
The fetcher pulls each, tags each feature with `_scenario_code` /
`_intensity_code`, concatenates everything into one GeoDataFrame, writes one
line-delimited GeoJSON. Bronze reads that single file.

Cache-first: re-runs are no-ops unless `CATNAT_FORCE_FETCH=true` or
`force=True`.

Licence: Etalab 2.0 / Licence Ouverte (Géorisques).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pandas as pd
import pyogrio

from catnat.config import CONFIG
from catnat.fetch.base import read_wfs_layer, upload_to_volume, volume_exists

logger = logging.getLogger(__name__)

WFS_URL = "https://www.georisques.gouv.fr/services?service=WFS&version=2.0.0&srsName=EPSG:4326"

# Eleven (scenario, intensity) layers for metropolitan France. The list is
# explicit — the WFS doesn't expose every cell of the 3×4 grid (e.g. no
# 02_03MCC), and listing them by name catches missing layers loudly rather
# than via silent 404s during a glob.
LAYERS: dict[tuple[str, str], str] = {
    ("01", "01FOR"): "ms:ALEA_SYNT_01_01FOR_FXX",
    ("01", "02MOY"): "ms:ALEA_SYNT_01_02MOY_FXX",
    ("01", "03MCC"): "ms:ALEA_SYNT_01_03MCC_FXX",
    ("01", "04FAI"): "ms:ALEA_SYNT_01_04FAI_FXX",
    ("02", "01FOR"): "ms:ALEA_SYNT_02_01FOR_FXX",
    ("02", "02MOY"): "ms:ALEA_SYNT_02_02MOY_FXX",
    ("02", "04FAI"): "ms:ALEA_SYNT_02_04FAI_FXX",
    ("03", "01FOR"): "ms:ALEA_SYNT_03_01FOR_FXX",
    ("03", "02MOY"): "ms:ALEA_SYNT_03_02MOY_FXX",
    ("03", "03MCC"): "ms:ALEA_SYNT_03_03MCC_FXX",
    ("03", "04FAI"): "ms:ALEA_SYNT_03_04FAI_FXX",
}


def remote_path(suffix: str) -> str:
    return f"{CONFIG.raw_volume_path}/tri/tri_{suffix}.geojsonl"


def _pull_one(layer: str, scenario_code: str, intensity_code: str, limit: int | None):
    """Pull one ALEA_SYNT layer and tag rows with scenario/intensity codes."""
    gdf = read_wfs_layer(WFS_URL, layer, limit=limit)
    gdf["_scenario_code"] = scenario_code
    gdf["_intensity_code"] = intensity_code
    return gdf


def fetch_and_upload(
    limit: int | None = 30,
    force: bool | None = None,
) -> tuple[str, int, bool]:
    """Pull all 11 metropolitan TRI layers into one GeoJSONSeq file in UC.

    Returns (remote_path, count, cached). `count == -1` on a cache hit.
    `limit` is per-layer; default 30 gives ~330-feature samples across the
    full scenario × intensity grid.
    """
    suffix = "sample" if limit is not None else "full"
    remote = remote_path(suffix)
    should_force = force if force is not None else CONFIG.force_fetch
    if not should_force and volume_exists(remote):
        logger.info("tri cache hit at %s", remote)
        return remote, -1, True

    parts = []
    for (scenario, intensity), layer in LAYERS.items():
        logger.info("tri pulling %s (scenario=%s intensity=%s)", layer, scenario, intensity)
        parts.append(_pull_one(layer, scenario, intensity, limit))

    combined = pd.concat(parts, ignore_index=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"tri_{suffix}.geojsonl"
        pyogrio.write_dataframe(combined, out, driver="GeoJSONSeq")
        upload_to_volume(out, remote)
    return remote, len(combined), False
