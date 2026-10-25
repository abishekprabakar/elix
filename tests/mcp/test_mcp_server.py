"""python -m elix.mcp_server starts over stdio and lists exactly the seven agent tools."""

from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXPECTED = ["list_vendors", "get_catalog", "budget_status", "request_purchase", "check_request",
            "answer_question", "get_resource"]


def test_mcp_server_over_stdio_lists_seven_tools():
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"ELIX_AGENT_TOKEN": "tok_scout", "ELIX_API_URL": "http://127.0.0.1:9",
           "ELIX_VENDOR_BASE_URL": "http://127.0.0.1:9", "PYTHONPATH": ROOT}

    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "elix.mcp_server"], env=env, cwd=ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                names = [t.name for t in tools]
                schema = {t.name: t.input_schema for t in tools}
                # A call works end to end and reports the unreachable API as data, not a crash.
                out = await session.call_tool("budget_status", {})
                return names, schema, out

    names, schema, out = asyncio.run(asyncio.wait_for(main(), timeout=60))
    assert sorted(names) == sorted(EXPECTED) and len(names) == 7
    assert not any("approve" in n or n == "pay" or "pay" == n.split("_")[0] for n in names)
    assert set(schema["request_purchase"]["required"]) == {"vendor_id", "sku", "quantity", "purpose", "task_ref"}
    assert "not reachable" in out.content[0].text


def test_mcp_tool_list_in_process():
    from elix.mcp_server import build_server

    tools = asyncio.run(build_server().list_tools())
    assert [t.name for t in tools] == EXPECTED


def test_mcp_purchase_end_to_end_against_real_server(tmp_path, monkeypatch):
    """An MCP client buys through Elix: stdio server, real API, real offline x402 rail."""
    import json
    import sqlite3
    import threading
    import time

    import uvicorn
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from elix import vendors
    from elix.rails import make_fake_registry, make_registry, reset_offline_state
    from elix.server.app import create_app
    from elix.world.seed import seed_world

    data = tmp_path / "data"
    monkeypatch.setenv("ELIX_DATA_DIR", str(data))
    vendor_base = vendors.ensure_running(port=0, mode="offline")
    monkeypatch.setenv("ELIX_VENDOR_BASE_URL", vendor_base)
    reset_offline_state(vendor_base)
    template, db = tmp_path / "t.db", tmp_path / "elix.db"
    seed_world(template, make_fake_registry()).elix.close()
    src, dst = sqlite3.connect(template), sqlite3.connect(db)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    port = vendors.free_port()
    app = create_app(db_path=db, registry=make_registry("offline", vendor_base), live_clock=False,
                     seed_if_missing=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)

    env = {"ELIX_AGENT_TOKEN": "tok_scout", "ELIX_API_URL": f"http://127.0.0.1:{port}",
           "ELIX_VENDOR_BASE_URL": vendor_base, "ELIX_DATA_DIR": str(data), "PYTHONPATH": ROOT}

    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "elix.mcp_server"], env=env, cwd=ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                budget = json.loads((await session.call_tool("budget_status", {})).content[0].text)
                bought = json.loads((await session.call_tool("request_purchase", {
                    "vendor_id": "ven_parsec", "sku": "query-1k", "quantity": 2,
                    "purpose": "Search queries to find Kansas crop insurers for the outreach list.",
                    "task_ref": "gtm/kansas-research"})).content[0].text)
                delivered = json.loads((await session.call_tool(
                    "get_resource", {"item_id": bought["item_id"]})).content[0].text)
                return budget, bought, delivered

    try:
        budget, bought, delivered = asyncio.run(asyncio.wait_for(main(), timeout=90))
    finally:
        server.should_exit = True
        t.join(timeout=5)
        vendors.stop(int(vendor_base.rsplit(":", 1)[1]))
    assert budget["agent_id"] == "agent:scout" and budget["per_txn_cap_cents"] == 15000
    # $10 is under Scout's $25 default, so Elix approves and pays it on the spot.
    assert bought["state"] == "POSTED", bought
    assert bought["receipt"]["tx_ref"].startswith("sim-") and bought["receipt"]["signature_verified"] is True
    assert delivered["resource"]["credits"]["queries"] == 2000
