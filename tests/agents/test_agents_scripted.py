"""Scripted agents against the stub API (agent side logic only)."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("agent", ["atlas", "forge", "scout"])
def test_scripted_default_tasks_create_requests(stub_api, vendor_base, monkeypatch, agent):
    from elix.agents import sessions
    from elix.agents.runner import DEFAULT_TASKS, run_blocking

    monkeypatch.setenv("ELIX_VENDOR_BASE_URL", vendor_base)
    api_base, stub = stub_api
    sid = run_blocking(agent, DEFAULT_TASKS[agent], api_base, f"tok_{agent}")
    s = sessions.get_session(sid)
    assert s["mode"] == "scripted" and s["mode_label"] == "Scripted agent (offline)"
    assert s["status"] == "completed", s
    assert len(s["request_ids"]) == 1
    item = stub.state.items[s["request_ids"][0]]
    assert item["requested_by"] == f"agent:{agent}"
    assert len(item["purpose"]) >= 12 and item["task_ref"]
    assert item["memo"] is None  # never copies vendor text into the request
    tools = [e.get("tool") for e in s["transcript"] if e["role"] == "tool_call"]
    assert tools[:4] == ["list_vendors", "budget_status", "get_catalog", "request_purchase"]
    assert s["transcript"][0]["role"] == "user"
    assert s["transcript"][1]["text"].startswith("Scripted agent (offline).")
    assert not any(p.endswith("/approve") for _m, p in stub.state.calls)
    if agent == "scout":
        assert any("pre-approved" in (e["text"] or "") and "ignoring" in (e["text"] or "") for e in s["transcript"])
    listed = sessions.list_sessions(agent)
    assert listed and listed[0]["id"] == sid and "transcript" not in listed[0]


def test_scripted_answers_questions_and_fetches_delivery(stub_api, vendor_base, monkeypatch):
    from elix.agents import sessions
    from elix.agents.runner import run_blocking

    monkeypatch.setenv("ELIX_VENDOR_BASE_URL", vendor_base)
    api_base, stub = stub_api
    # Two Parsec packs ($10) auto approve under Scout's $25 default in the stub.
    sid = run_blocking("scout", "Buy 2 packs of search queries for carrier research", api_base, "tok_scout")
    s = sessions.get_session(sid)
    tools = [e.get("tool") for e in s["transcript"] if e["role"] == "tool_call"]
    assert "get_resource" in tools
    assert stub.state.items[s["request_ids"][0]]["state"] == "POSTED"


def test_start_run_returns_immediately_and_finishes(stub_api, vendor_base, monkeypatch):
    import time

    from elix.agents import sessions
    from elix.agents.runner import start_run

    monkeypatch.setenv("ELIX_VENDOR_BASE_URL", vendor_base)
    api_base, _ = stub_api
    sid = start_run("atlas", "", api_base, "tok_atlas")
    assert sessions.get_session(sid)["status"] in ("running", "completed")
    deadline = time.time() + 20
    while time.time() < deadline and sessions.get_session(sid)["status"] == "running":
        time.sleep(0.05)
    assert sessions.get_session(sid)["status"] == "completed"


def test_toolbox_has_no_approve_or_pay_and_reports_errors(stub_api):
    from elix.agents.tools import TOOL_NAMES, ElixAPI, ToolBox

    assert len(TOOL_NAMES) == 7
    assert not any("approve" in n or n in ("pay", "verify_payee") for n in TOOL_NAMES)
    api_base, _ = stub_api
    tb = ToolBox(ElixAPI(api_base, "tok_atlas"))
    assert tb.call("approve", {"item_id": "WI-1"})["error"]["code"] == "unknown_tool"
    bad = tb.call("check_request", {"item_id": "WI-9999"})
    assert bad["error"]["http_status"] == 404
    assert ToolBox(ElixAPI(api_base, "tok_nobody")).call("budget_status")["error"]["http_status"] == 401
