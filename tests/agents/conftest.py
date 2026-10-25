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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return str(d)


@pytest.fixture()
def stub_api(data_dir):
    from elix.vendors import free_port

    from .stub_api import create_stub_app, serve_in_thread

    app = create_stub_app()
    port = free_port()
    server, t = serve_in_thread(app, port)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="session")
def vendor_base():
    from elix import vendors

    base = vendors.ensure_running(port=0, mode="offline")
    yield base
    vendors.stop(int(base.rsplit(":", 1)[1]))
