"""Shared fixtures for the engine, world, eval, scenario and server tests.

Unit tests use the in-memory fake rails from elix.rails.make_fake_registry().
Tests that exercise real x402 payloads start the vendor app on a free
ephemeral port, never on 8400 or 8402.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from elix.contracts import PurchaseRequest  # noqa: E402
from elix.engine import Elix  # noqa: E402
from elix.engine.clock import Clock  # noqa: E402
from elix.rails import make_fake_registry  # noqa: E402
from elix.world.config import TODAY  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolated_runtime(tmp_path_factory):
    """Keep every runtime file (rails.db, sessions.db, eval output) out of the repo."""
    root = tmp_path_factory.mktemp("elix-runtime")
    keys = ("ELIX_DATA_DIR", "ELIX_EVAL_DIR", "ELIX_RAIL_MODE", "ANTHROPIC_API_KEY")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["ELIX_DATA_DIR"] = str(root / "data")
    os.environ["ELIX_EVAL_DIR"] = str(root / "eval")
    os.environ["ELIX_RAIL_MODE"] = "offline"
    os.environ.pop("ANTHROPIC_API_KEY", None)
    yield root
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture()
def clock() -> Clock:
    return Clock(TODAY)


@pytest.fixture()
def elix(tmp_path, clock) -> Elix:
    """An empty Kestrel world (no history) on fake rails, clock at 2026-10-24 16:00 UTC."""
    engine = Elix(tmp_path / "elix.db", make_fake_registry(), clock=clock)
    yield engine
    engine.close()


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory) -> Path:
    from elix.world.seed import seed_world

    path = tmp_path_factory.mktemp("seed") / "template.db"
    result = seed_world(path, make_fake_registry())
    result.elix.close()
    return path


def copy_db(source: Path, dest: Path) -> None:
    src = sqlite3.connect(source)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


@pytest.fixture()
def seeded(seeded_template, tmp_path) -> Elix:
    """A fresh copy of the seeded Kestrel world on fake rails."""
    dest = tmp_path / "elix.db"
    copy_db(seeded_template, dest)
    engine = Elix(dest, make_fake_registry(), clock=Clock(TODAY))
    yield engine
    engine.close()


@pytest.fixture(scope="session")
def vendor_server():
    """The real x402 vendor app on a free port for tests that need signed payloads."""
    from elix import vendors

    base = vendors.ensure_running(port=0, mode="offline")
    yield base
    vendors.stop(int(base.rsplit(":", 1)[1]))


def buy(engine: Elix, agent: str, vendor_id: str, sku: str, quantity: float, **kwargs):
    defaults = {"purpose": "Imagery for the hail-v7 training set", "task_ref": "hail-v7"}
    defaults.update(kwargs)
    return engine.request_purchase(agent, PurchaseRequest(vendor_id=vendor_id, sku=sku, quantity=quantity, **defaults))
