"""Every API endpoint with the right tokens (scripts/smoke.py), plus SSE on a real server."""

from __future__ import annotations

import importlib.util
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from elix.rails import make_fake_registry
from elix.server import create_app

from conftest import ROOT, copy_db


def load_smoke():
    spec = importlib.util.spec_from_file_location("smoke", ROOT / "scripts" / "smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def api(seeded_template, tmp_path):
    db = tmp_path / "elix.db"
    copy_db(seeded_template, db)
    app = create_app(db, make_fake_registry(), live_clock=False)
    with TestClient(app) as client:
        yield client
    app.state.elix_state.close()


def test_smoke_every_endpoint(api):
    lines: list[str] = []
    checks = load_smoke().run(api, log=lines.append)
    assert checks >= 100


def test_error_body_shape(api):
    pending = api.get("/api/work-items", params={"inbox": "me"}, headers={"Authorization": "Bearer tok_dev"}).json()
    atlas_item = next(i["id"] for i in pending["items"] if i["requested_by"] == "agent:atlas")
    response = api.post(f"/api/work-items/{atlas_item}/approve", headers={"Authorization": "Bearer tok_atlas"}, json={})
    body = response.json()
    assert response.status_code == 403
    assert set(body["error"]) == {"code", "message", "rule_id", "audit_seq"}
    assert "—" not in body["error"]["message"]


def test_console_placeholder_when_not_built(api, monkeypatch, tmp_path):
    monkeypatch.setenv("ELIX_CONSOLE_DIST", str(tmp_path / "missing"))
    page = api.get("/")
    assert page.status_code == 200 and "console is not built yet" in page.text


def test_console_is_served_with_spa_fallback(api, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>console</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("ELIX_CONSOLE_DIST", str(dist))
    assert api.get("/").text == "<html>console</html>"
    assert api.get("/items/WI-1001").text == "<html>console</html>"
    assert api.get("/assets/app.js").text == "console.log(1)"
    assert api.get("/../../etc/passwd").status_code in (200, 404)


def test_sse_streams_engine_events(seeded_template, tmp_path):
    import uvicorn

    from elix.vendors import free_port

    db = tmp_path / "elix.db"
    copy_db(seeded_template, db)
    app = create_app(db, make_fake_registry(), live_clock=True)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    try:
        assert httpx.get(f"{base}/api/events", timeout=5).status_code == 401
        kinds: list[str] = []
        with httpx.stream("GET", f"{base}/api/events", params={"token": "tok_priya", "limit": 3}, timeout=20) as stream:
            def trigger():
                time.sleep(0.5)
                httpx.post(f"{base}/api/purchases", headers={"Authorization": "Bearer tok_scout"}, json={
                    "vendor_id": "ven_parsec", "sku": "query-1k", "quantity": 1, "task_ref": "sse-1",
                    "purpose": "Search queries for carrier contacts"}, timeout=10)

            threading.Thread(target=trigger, daemon=True).start()
            for line in stream.iter_lines():
                if line.startswith("event: "):
                    kinds.append(line.removeprefix("event: "))
                if line.startswith("data: ") and kinds and kinds[-1] in ("audit", "work_item"):
                    json.loads(line.removeprefix("data: "))
        assert kinds[0] == "ready"
        assert {"audit", "work_item"} & set(kinds[1:])
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.state.elix_state.close()
