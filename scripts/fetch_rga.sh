#!/usr/bin/env bash
# Fetch BRGM RGA susceptibility polygons from the Géorisques WFS, convert to
# line-delimited GeoJSON, and upload to the bronze raw-staging volume.
#
# Why a local script (instead of a notebook driver):
# - The target workspace has no Python compute provisioned; only a Serverless
#   SQL warehouse. ogr2ogr + Databricks CLI on the operator's laptop is the
#   simplest reproducible bridge.
# - When we move to a workspace with serverless Python or classic compute,
#   this script is replaced by a notebook task driven by the bundle.
#
# Requirements: gdal/ogr2ogr (3.5+ for GeoJSONSeq), databricks CLI with a
# valid profile.
#
# Usage:
#   ./scripts/fetch_rga.sh                       # default: 100-feature sample
#   FEATURES=full ./scripts/fetch_rga.sh         # full national dataset
#   PROFILE=other-profile ./scripts/fetch_rga.sh # override CLI profile

set -euo pipefail

PROFILE="${PROFILE:-fevm-stable-po64og}"
CATALOG="${CATALOG:-serverless_stable_po64og_catalog}"
FEATURES="${FEATURES:-sample}"   # "sample" (100 features) or "full"

WFS_URL='https://www.georisques.gouv.fr/services?service=WFS&version=2.0.0&srsName=EPSG:4326'
LAYER='ms:ALEARG_REALISE'
VOLUME_DIR="dbfs:/Volumes/${CATALOG}/catnat_bronze/raw/rga"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ "$FEATURES" == "sample" ]]; then
  LIMIT_ARGS=(-limit 100)
  OUT_NAME="rga_sample.geojsonl"
else
  LIMIT_ARGS=()
  OUT_NAME="rga_full.geojsonl"
fi

echo "[1/3] Pulling ${LAYER} from WFS (${FEATURES})..."
ogr2ogr -f GPKG "${TMP}/rga.gpkg" "WFS:${WFS_URL}" "${LAYER}" "${LIMIT_ARGS[@]}"

echo "[2/3] Converting to line-delimited GeoJSON..."
ogr2ogr -f GeoJSONSeq "${TMP}/${OUT_NAME}" "${TMP}/rga.gpkg"
echo "      $(wc -l < "${TMP}/${OUT_NAME}") features."

echo "[3/3] Uploading to ${VOLUME_DIR}/${OUT_NAME}..."
databricks fs mkdir "${VOLUME_DIR}" --profile "${PROFILE}" 2>/dev/null || true
databricks fs cp "${TMP}/${OUT_NAME}" "${VOLUME_DIR}/${OUT_NAME}" \
  --profile "${PROFILE}" --overwrite

echo "Done."
