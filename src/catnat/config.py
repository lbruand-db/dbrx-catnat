"""Centralized defaults for the catnat CLI.

Override via env vars (CATNAT_PROFILE, CATNAT_WAREHOUSE_ID, CATNAT_CATALOG) or
CLI flags. Defaults match the dev target in `databricks.yml`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    profile: str = os.environ.get("CATNAT_PROFILE", "fevm-stable-po64og")
    warehouse_id: str = os.environ.get("CATNAT_WAREHOUSE_ID", "1c97ee257092c2b3")
    catalog: str = os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")
    bronze_schema: str = "catnat_bronze"
    silver_schema: str = "catnat_silver"
    gold_schema: str = "catnat_gold"
    raw_volume: str = "raw"

    @property
    def raw_volume_path(self) -> str:
        return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.raw_volume}"


CONFIG = Config()
