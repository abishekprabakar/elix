"""Scripted agents against the real Elix server and the real offline x402 rail.

The world is seeded on fake rails (fast), then the server runs on the real
offline rail: agent purchases are signed with the x402 SDK, verified by the
local facilitator and settled on the simulated USDC ledger.
"""

from __future__ import annotations

import sqlite3
import time

import httpx
import pytest

from .stub_api import serve_in_thread


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    import os

    from elix import vendors
    from elix.rails import make_fake_registry, make_registry, reset_offline_state
    from elix.server.app import create_app
    from elix.world.seed import seed_world

    root = tmp_path_factory.mktemp("live")
    old = os.environ.get("ELIX_DATA_DIR")
    os.environ["ELIX_DATA_DIR"] = str(root / "data")
    vendor_base = vendors.ensure_running(port=0, mode="offline")
    old_vendor = os.environ.get("ELIX_VENDOR_BASE_URL")
    os.environ["ELIX_VENDOR_BASE_URL"] = vendor_base
    reset_offline_state(vendor_base)

    template = root / "template.db"
    seed_world(template, make_fake_registry()).elix.close()
    db = root / "elix.db"
    src, dst = sqlite3.connect(template), sqlite3.connect(db)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()

    port = vendors.free_port()
    api_base = f"http://127.0.0.1:{port}"
    app = create_app(db_path=db, registry=make_registry("offline", vendor_base), live_clock=False,
                     seed_if_missing=False, api_base=api_base)
    server, t = serve_in_thread(app, port)
    yield api_base, vendor_base
    server.should_exit = True
    t.join(timeout=5)
    vendors.stop(int(vendor_base.rsplit(":", 1)[1]))
    for key, value in (("ELIX_DATA_DIR", old), ("ELIX_VENDOR_BASE_URL", old_vendor)):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _get(api_base, path, token):
    return httpx.get(f"{api_base}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=30)


def _post(api_base, path, token, body=None):
    return httpx.post(f"{api_base}{path}", json=body or {}, headers={"Authorization": f"Bearer {token}"}, timeout=60)


@pytest.mark.parametrize("agent", ["atlas", "forge", "scout"])
def test_scripted_agents_complete_default_tasks_on_real_server(live_server, agent):
    from elix.agents import sessions
    from elix.agents.runner import DEFAULT_TASKS, run_blocking

    api_base, _vendor = live_server
    sid = run_blocking(agent, DEFAULT_TASKS[agent], api_base, f"tok_{agent}", force_scripted=True)
    s = sessions.get_session(sid)
    assert s["status"] == "completed", s
    assert s["mode"] == "scripted"
    assert s["request_ids"], s
    for item_id in s["request_ids"]:
        r = _get(api_base, f"/api/work-items/{item_id}", f"tok_{agent}")
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert item["requested_by"] == f"agent:{agent}"
        assert item["state"] in ("NEEDS_APPROVAL", "POSTED", "NEEDS_INFO", "DENIED")
        assert item["challenge"]["raw_b64"], "the 402 challenge came from the real vendor app"
    # The sessions API returns the same transcript.
    api = _get(api_base, f"/api/agents/sessions/{sid}", "tok_priya")
    assert api.status_code == 200
    assert len(api.json()["transcript"]) == len(s["transcript"]) > 5


def test_full_loop_human_approval_pays_over_x402_and_agent_gets_delivery(live_server):
    from elix.agents.tools import ElixAPI, ToolBox
    from elix.rails import sim_chain
    from elix.rails.keys import treasury_address

    api_base, _vendor = live_server
    forge = ToolBox(ElixAPI(api_base, "tok_forge"), agent_id="agent:forge")
    req = forge.call("request_purchase", {
        "vendor_id": "ven_lumen", "sku": "h100-spot-hr", "quantity": 120,
        "purpose": "Spot hours for a hail-v7 ablation run on the Nebraska scenes.", "task_ref": "hail-v7/ablation",
    })
    item_id = req["item_id"]
    assert req["amount_cents"] == 25200

    # The agent cannot approve its own request.
    own = _post(api_base, f"/api/work-items/{item_id}/approve", "tok_forge", {"note": "approve"})
    assert own.status_code == 403

    if req["state"] == "NEEDS_APPROVAL":
        before = sim_chain.get_chain().balance_of(treasury_address("offline"))
        ok = _post(api_base, f"/api/work-items/{item_id}/approve", "tok_dev", {"note": "Ablation is planned."})
        assert ok.status_code == 200, ok.text
        assert sim_chain.get_chain().balance_of(treasury_address("offline")) == before - 252_000_000
    final = forge.call("check_request", {"item_id": item_id})
    assert final["state"] == "POSTED", final
    assert final["receipt"]["tx_ref"].startswith("sim-") and final["receipt"]["signature_verified"] is True
    delivered = forge.call("get_resource", {"item_id": item_id})
    assert delivered["resource"]["synthetic"] is True
    assert delivered["resource"]["reservation"]["gpu_hours"] == 120
    x402 = _get(api_base, f"/api/work-items/{item_id}/x402", "tok_priya").json()
    assert x402["signature_verified"] is True and x402["simulated"] is True
    assert x402["payment_response_decoded"]["extra"]["verified_by"]


def test_agent_run_endpoint_streams_a_session(live_server):
    api_base, _vendor = live_server
    r = _post(api_base, "/api/agents/scout/run", "tok_maya", {"task": ""})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    deadline = time.time() + 60
    status = "running"
    while time.time() < deadline:
        s = _get(api_base, f"/api/agents/sessions/{sid}", "tok_maya").json()
        status = s["status"]
        if status != "running":
            break
        time.sleep(0.2)
    assert status == "completed"
    assert s["request_ids"] and s["transcript"]
    listed = _get(api_base, "/api/agents/sessions?agent=scout", "tok_maya").json()["sessions"]
    assert any(x["id"] == sid for x in listed)
    # An agent token cannot start runs.
    assert _post(api_base, "/api/agents/scout/run", "tok_scout", {"task": ""}).status_code == 403
