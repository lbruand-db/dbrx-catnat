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
(some scenario × intensity pairs don't exist server-side).

**Per-layer caching**: each layer lands in its own
`tri/tri_<scenario>_<intensity>_<suffix>.geojsonl` file in the bronze raw
volume. Re-running the fetch only re-pulls layers whose file is missing (or
when `--force` / `CATNAT_FORCE_FETCH=true`). If a particular layer fails after
all retries (Géorisques returns persistent 502s for some layers under load),
we log a warning and skip it — the other ten still land cleanly and bronze
reads the union via a glob path.

Licence: Etalab 2.0 / Licence Ouverte (Géorisques).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class LayerResult:
    """Outcome of one layer pull."""

    scenario: str
    intensity: str
    remote_path: str | None  # None when skipped
    count: int  # -1 on cache hit, -2 on skip
    cached: bool
    skipped: bool
    error: str | None = None


def _suffix(limit: int | None) -> str:
    return "sample" if limit is not None else "full"


def remote_path(scenario: str, intensity: str, suffix: str) -> str:
    return f"{CONFIG.raw_volume_path}/tri/tri_{scenario}_{intensity}_{suffix}.geojsonl"


def bronze_glob(suffix: str) -> str:
    """Glob path the bronze notebook reads — matches every per-layer file."""
    return f"{CONFIG.raw_volume_path}/tri/tri_*_*_{suffix}.geojsonl"


def fetch_layer(
    scenario: str,
    intensity: str,
    limit: int | None,
    force: bool,
) -> LayerResult:
    """Pull one ALEA_SYNT layer to its own GeoJSONSeq file in UC.

    Tags every feature with `_scenario_code` / `_intensity_code` so bronze can
    parse them as first-class columns without inspecting the file name.
    """
    layer = LAYERS[(scenario, intensity)]
    suffix = _suffix(limit)
    remote = remote_path(scenario, intensity, suffix)

    if not force and volume_exists(remote):
        logger.info("tri/%s_%s cache hit at %s", scenario, intensity, remote)
        return LayerResult(scenario, intensity, remote, -1, cached=True, skipped=False)

    try:
        gdf = read_wfs_layer(WFS_URL, layer, limit=limit)
    except Exception as e:
        # Per-layer skip on persistent failure (typically Géorisques 502 after
        # all retries). The other layers' files still land and bronze unions
        # whatever's present.
        logger.warning(
            "tri/%s_%s WFS pull failed after retries: %s — skipping",
            scenario,
            intensity,
            e,
        )
        return LayerResult(
            scenario,
            intensity,
            None,
            -2,
            cached=False,
            skipped=True,
            error=str(e),
        )

    gdf["_scenario_code"] = scenario
    gdf["_intensity_code"] = intensity
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"tri_{scenario}_{intensity}_{suffix}.geojsonl"
        pyogrio.write_dataframe(gdf, out, driver="GeoJSONSeq")
        upload_to_volume(out, remote)
    return LayerResult(scenario, intensity, remote, len(gdf), cached=False, skipped=False)


def fetch_and_upload(
    limit: int | None = 30,
    force: bool | None = None,
) -> tuple[list[LayerResult], str]:
    """Pull all eleven layers, each into its own file.

    Returns (results_per_layer, glob_path_for_bronze). The glob path is what
    the bronze notebook should consume via `read_files(:input_path, ...)`.
    """
    should_force = force if force is not None else CONFIG.force_fetch
    results: list[LayerResult] = []
    for scenario, intensity in LAYERS:
        results.append(fetch_layer(scenario, intensity, limit, should_force))
    return results, bronze_glob(_suffix(limit))
