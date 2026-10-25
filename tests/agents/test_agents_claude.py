"""The Claude code path builds valid Anthropic tool use requests.

The real anthropic SDK client is pointed at a local fake Messages endpoint that
records each request and answers with a scripted tool_use, then end_turn. No
network call to Anthropic happens in this test.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, Request
from pydantic import TypeAdapter

from .stub_api import serve_in_thread


def _fake_messages_app(script: list[dict]):
    app = FastAPI()
    app.state.requests = []

    @app.post("/v1/messages")
    async def messages(request: Request):
        body = await request.json()
        app.state.requests.append({"body": body, "headers": dict(request.headers)})
        n = len(app.state.requests) - 1
        step = script[min(n, len(script) - 1)]
        return {
            "id": f"msg_{n}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": step["content"],
            "stop_reason": step["stop_reason"],
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    return app


SCRIPT = [
    {"content": [{"type": "text", "text": "I will check my budget first."},
                 {"type": "tool_use", "id": "toolu_01", "name": "budget_status", "input": {}}],
     "stop_reason": "tool_use"},
    {"content": [{"type": "tool_use", "id": "toolu_02", "name": "request_purchase",
                  "input": {"vendor_id": "ven_lumen", "sku": "h100-spot-hr", "quantity": 400,
                            "purpose": "Spot hours to retrain hail-v7 on the new Nebraska scenes this weekend.",
                            "task_ref": "hail-v7/retrain"}}],
     "stop_reason": "tool_use"},
    {"content": [{"type": "text", "text": "WI-1001 is waiting on Dev Okafor. I am stopping here."}],
     "stop_reason": "end_turn"},
]


def test_claude_loop_with_real_sdk_against_fake_endpoint(stub_api, monkeypatch):
    import anthropic
    from anthropic.types import MessageParam, ToolParam

    from elix.agents import sessions
    from elix.agents.runner import run_blocking
    from elix.agents.tools import TOOL_NAMES
    from elix.vendors import free_port

    api_base, stub = stub_api
    fake = _fake_messages_app(SCRIPT)
    port = free_port()
    server, t = serve_in_thread(fake, port)
    try:
        monkeypatch.setenv("ELIX_AGENT_MODEL", "claude-sonnet-5-5")
        client = anthropic.Anthropic(api_key="sk-ant-test-not-real", base_url=f"http://127.0.0.1:{port}",
                                     max_retries=0)
        sid = run_blocking("forge", "Reserve 400 H100 spot hours to retrain hail-v7.", api_base, "tok_forge",
                           client=client)
    finally:
        server.should_exit = True
        t.join(timeout=5)

    reqs = [r["body"] for r in fake.state.requests]
    assert len(reqs) == 3
    first = reqs[0]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["max_tokens"] > 0
    assert "cannot approve or pay" in first["system"]
    assert [t["name"] for t in first["tools"]] == list(TOOL_NAMES)
    assert not any("approve" in t["name"] or t["name"] == "pay" for t in first["tools"])
    tool_adapter = TypeAdapter(ToolParam)
    for tool in first["tools"]:
        tool_adapter.validate_python(tool)
        assert tool["input_schema"]["type"] == "object"
    msg_adapter = TypeAdapter(list[MessageParam])
    for body in reqs:
        msg_adapter.validate_python(body["messages"])
        roles = [m["role"] for m in body["messages"]]
        assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))

    # Second request carries the tool_use and its tool_result
    second = reqs[1]["messages"]
    assert second[1]["content"][1] == {"type": "tool_use", "id": "toolu_01", "name": "budget_status", "input": {}}
    result_block = second[2]["content"][0]
    assert result_block["type"] == "tool_result" and result_block["tool_use_id"] == "toolu_01"
    assert json.loads(result_block["content"])["agent_id"] == "agent:forge"

    third = reqs[2]["messages"]
    purchase_result = json.loads(third[4]["content"][0]["content"])
    assert purchase_result["state"] == "NEEDS_APPROVAL"

    s = sessions.get_session(sid)
    assert s["mode"] == "claude" and s["model"] == "claude-sonnet-5-5" and s["status"] == "completed"
    assert s["request_ids"] == [purchase_result["item_id"]]
    roles = [e["role"] for e in s["transcript"]]
    assert roles == ["user", "assistant", "tool_call", "tool_result", "tool_call", "tool_result", "assistant"]
    assert s["transcript"][4]["tool"] == "request_purchase"
    assert ("POST", "/api/purchases") in stub.state.calls
    assert not any(path.endswith("/approve") for _m, path in stub.state.calls)


def test_live_call_only_when_key_is_set(monkeypatch, stub_api):
    from elix.agents import runner, sessions

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner.mode_for() == "scripted"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert runner.mode_for() == "claude"
    assert runner.mode_for(force_scripted=True) == "scripted"

    # With a key but no reachable API, the run falls back to scripted and says so.
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    api_base, _stub = stub_api
    sid = runner.run_blocking("scout", "", api_base, "tok_scout")
    s = sessions.get_session(sid)
    assert s["mode"] == "scripted"
    assert any("could not be reached" in (e["text"] or "") for e in s["transcript"])
    assert s["request_ids"]


def test_max_turns_is_enforced(stub_api):
    from elix.agents import sessions
    from elix.agents.claude_agent import MAX_TURNS, run_claude
    from elix.agents.tools import ElixAPI, ToolBox

    class LoopingClient:
        def __init__(self):
            self.calls = 0
            self.messages = self

        def create(self, **kw):
            self.calls += 1
            return {"content": [{"type": "tool_use", "id": f"t{self.calls}", "name": "budget_status", "input": {}}],
                    "stop_reason": "tool_use"}

    api_base, _ = stub_api
    c = LoopingClient()
    sid = sessions.create_session("agent:atlas", "loop", "claude", "test")
    status = run_claude("agent:atlas", "loop", ToolBox(ElixAPI(api_base, "tok_atlas")), sid, client=c)
    assert status == "max_turns" and c.calls == MAX_TURNS == 12
