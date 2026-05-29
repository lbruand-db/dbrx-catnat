"""Operator parameters for the catnat CLI and DAB jobs.

Resolution order (highest priority first):
  1. Real environment variables (CATNAT_PROFILE, etc.).
  2. `.env` file in the repo root, loaded once at import time via python-dotenv.
  3. Hardcoded defaults below (target the fevm-stable-po64og demo workspace).

A `.env.example` is committed as a template — copy to `.env` to customize.

`CONFIG` reads env vars on every property access so that DAB job entry points
can set `CATNAT_CATALOG=…` via task parameters and have the change take effect
without process restart.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load `.env` from the repo root. `override=False` keeps real env vars winning
# over the file — important for DAB jobs where task parameters set env vars.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class _Config:
    """Lazy view over the env so CLI / job entry points can mutate it at runtime."""

    bronze_schema: str = "catnat_bronze"
    silver_schema: str = "catnat_silver"
    gold_schema: str = "catnat_gold"
    raw_volume: str = "raw"

    @property
    def profile(self) -> str:
        return os.environ.get("CATNAT_PROFILE", "fevm-stable-po64og")

    @property
    def warehouse_id(self) -> str:
        return os.environ.get("CATNAT_WAREHOUSE_ID", "1c97ee257092c2b3")

    @property
    def catalog(self) -> str:
        return os.environ.get("CATNAT_CATALOG", "serverless_stable_po64og_catalog")

    @property
    def force_fetch(self) -> bool:
        return _bool_env("CATNAT_FORCE_FETCH", default=False)

    @property
    def raw_volume_path(self) -> str:
        return f"/Volumes/{self.catalog}/{self.bronze_schema}/{self.raw_volume}"

    @property
    def is_databricks_runtime(self) -> bool:
        """True when running inside Databricks compute (notebook or wheel task)."""
        return "DATABRICKS_RUNTIME_VERSION" in os.environ


CONFIG = _Config()
