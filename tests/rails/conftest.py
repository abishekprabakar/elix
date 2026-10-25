from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("ELIX_DATA_DIR", str(d))
    monkeypatch.setenv("ELIX_RAIL_MODE", "offline")
    return str(d)


@pytest.fixture(scope="session")
def vendor_base():
    """One offline vendor app on a free ephemeral port for the whole test session."""
    from elix import vendors

    base = vendors.ensure_running(port=0, mode="offline")
    yield base
    port = int(base.rsplit(":", 1)[1])
    vendors.stop(port)


@pytest.fixture()
def fresh_vendor(vendor_base, data_dir):
    from elix import vendors

    vendors.reset(vendor_base)
    return vendor_base
