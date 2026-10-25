"""Runtime paths. Everything that changes at runtime lives under ELIX_DATA_DIR."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    path = Path(os.environ.get("ELIX_DATA_DIR", "data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_db_path() -> Path:
    return data_dir() / "elix.db"


def eval_dir() -> Path:
    path = Path(os.environ.get("ELIX_EVAL_DIR", str(REPO_ROOT / "eval"))).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def console_dist() -> Path:
    return Path(os.environ.get("ELIX_CONSOLE_DIST", str(REPO_ROOT / "console" / "dist"))).resolve()


def vendor_base_url() -> str:
    return os.environ.get("ELIX_VENDOR_BASE_URL", "http://127.0.0.1:8402").rstrip("/")


def rail_mode() -> str:
    return os.environ.get("ELIX_RAIL_MODE", "offline")
