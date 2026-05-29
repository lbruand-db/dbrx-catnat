"""Operator parameters for the catnat CLI.

Resolution order (highest priority first):
  1. Real environment variables (CATNAT_PROFILE, etc.).
  2. `.env` file in the repo root, loaded once at import time via python-dotenv.
  3. Hardcoded defaults below (target the fevm-stable-po64og demo workspace).

A `.env.example` is committed as a template — copy to `.env` to customize.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load `.env` from the repo root (= parent of src/). `override=False` keeps any
# already-set environment variable winning over the file.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    profile: str = os.environ.get("CATNAT_PROFILE", "fevm-stable-po64og")
    warehouse_id: str = os.environ.get("CATNAT_WAREHOUSE_ID", "1c97ee257092c2b3")
    catalog: str = os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")
    bronze_schema: str = "catnat_bronze"
    silver_schema: str = "catnat_silver"
    gold_schema: str = "catnat_gold"
    raw_volume: str = "raw"
    force_fetch: bool = _bool_env("CATNAT_FORCE_FETCH", default=False)

    @property
    def raw_volume_path(self) -> str:
        return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.raw_volume}"


CONFIG = Config()
