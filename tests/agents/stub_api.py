"""A tiny stand-in for the Elix HTTP API, used only to unit test the agent side.

It implements just the endpoints the agent tools call, with crude decisions
(auto approve under the agent's threshold, otherwise hold for the budget owner,
ask when the purpose is short). The real end to end check runs the agents
against the real server in test_agents_live_server.py.
"""

from __future__ import annotations

import itertools
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from elix.vendors import pricing

TOKENS = {"tok_atlas": "agent:atlas", "tok_forge": "agent:forge", "tok_scout": "agent:scout", "tok_dev": "human:dev"}
AGENTS = {
    "agent:atlas": {"cap": 25000, "budget": 600000, "auto": 1500},
    "agent:forge": {"cap": 250000, "budget": 4000000, "auto": 20000},
    "agent:scout": {"cap": 15000, "budget": 250000, "auto": 2500},
}


def create_stub_app() -> FastAPI:
    app = FastAPI()
    items: dict[str, dict] = {}
    seq = itertools.count(1001)
    app.state.items = items
    app.state.calls = []

    def actor(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        return TOKENS.get(auth.removeprefix("Bearer ").strip())

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    def detail(item: dict) -> dict:
        return {"item": item, "events": [], "journal_entries": [], "linked": [],
                "explanation": item["decision"]["summary"], "next_actions": []}

    @app.middleware("http")
    async def record(request: Request, call_next):
        app.state.calls.append((request.method, request.url.path))
        if actor(request) is None:
            return err(401, "unauthorized", "Unknown token")
        return await call_next(request)

    @app.get("/api/session")
    async def session(request: Request):
        a = actor(request)
        return {"actor": {"id": a, "kind": a.split(":")[0], "name": a.split(":")[1].title(), "role": "agent"},
                "role": "agent", "can": {}}

    @app.get("/api/world")
    async def world(request: Request):
        vendors = []
        for v in pricing.vendor_table().values():
            vendors.append({"id": v.id, "name": v.name, "rails": v.rails, "category": v.category,
                            "skus": [{"sku": s.sku, "description": s.description, "unit": s.unit,
                                      "unit_price_cents": s.unit_price_cents} for s in v.skus.values()]})
        return {"vendors": vendors}

    @app.get("/api/agents")
    async def agents(request: Request):
        out = []
        for aid, cfg in AGENTS.items():
            spent = sum(i["amount_cents"] for i in items.values() if i["requested_by"] == aid and i["state"] == "POSTED")
            committed = sum(i["amount_cents"] for i in items.values()
                            if i["requested_by"] == aid and i["state"] in ("NEEDS_APPROVAL", "NEEDS_INFO"))
            out.append({"actor_id": aid, "per_txn_cap_cents": cfg["cap"], "monthly_budget_cents": cfg["budget"],
                        "auto_approve_under_cents": cfg["auto"], "spent_cents": spent, "committed_cents": committed})
        return {"agents": out}

    def decide(item: dict) -> None:
        cfg = AGENTS[item["requested_by"]]
        if len(item["purpose"] or "") < 12 or not item["task_ref"]:
            qid = f"Q-{item['id']}"
            item.update(state="NEEDS_INFO", questions=[{"id": qid, "asked_by": "system:policy",
                                                        "to": item["requested_by"], "text": "What is this for?",
                                                        "answer": None, "at": "2026-10-24T16:00:00Z"}])
            item["decision"] = {"summary": "Elix needs a purpose and a task reference before it can decide.",
                                "outcome": "needs_info"}
        elif item["amount_cents"] < cfg["auto"]:
            item.update(state="POSTED", receipt={"tx_ref": "sim-stub", "simulated": True,
                                                  "amount_cents": item["amount_cents"], "signature_verified": True})
            item["decision"] = {"summary": "Auto approved under the default threshold.", "outcome": "auto_approve"}
        else:
            item.update(state="NEEDS_APPROVAL", required_approver_role="budget_owner")
            item["decision"] = {"summary": "Held for the budget owner because it is above the auto approve threshold.",
                                "outcome": "needs_approval"}

    @app.post("/api/purchases")
    async def purchases(request: Request):
        a = actor(request)
        if not a.startswith("agent:"):
            return err(403, "denied", "Only agents propose purchases")
        body = await request.json()
        for i in items.values():
            if i["idempotency_key"] == body.get("idempotency_key") and i["requested_by"] == a:
                return JSONResponse(detail(i), status_code=200)
        try:
            amount = pricing.price_cents(body["vendor_id"], body["sku"], float(body["quantity"]))
        except pricing.PricingError as exc:
            return err(400, "invalid", str(exc))
        wid = f"WI-{next(seq):04d}"
        item = {"id": wid, "kind": "purchase", "state": "NEW", "requested_by": a, "vendor_id": body["vendor_id"],
                "sku": body["sku"], "quantity": body["quantity"], "amount_cents": amount,
                "purpose": body.get("purpose"), "task_ref": body.get("task_ref"), "memo": body.get("memo"),
                "idempotency_key": body.get("idempotency_key"), "flags": [], "questions": [],
                "title": f"{body['quantity']} {body['sku']}", "required_approver_role": None, "receipt": None}
        decide(item)
        items[wid] = item
        return JSONResponse(detail(item), status_code=201)

    @app.get("/api/work-items/{wid}")
    async def get_item(wid: str, request: Request):
        i = items.get(wid)
        if i is None or i["requested_by"] != actor(request):
            return err(404, "not_found", f"No item {wid}")
        return detail(i)

    @app.post("/api/work-items/{wid}/answer")
    async def answer(wid: str, request: Request):
        i = items.get(wid)
        body = await request.json()
        for q in i["questions"]:
            if q["id"] == body["question_id"]:
                q["answer"] = body["text"]
        i["purpose"] = body["text"]
        i["task_ref"] = i["task_ref"] or "answered"
        decide(i)
        return detail(i)

    @app.post("/api/work-items/{wid}/approve")
    async def approve(wid: str, request: Request):
        return err(403, "denied", "Agents cannot approve anything.")

    @app.get("/api/work-items/{wid}/resource")
    async def resource(wid: str, request: Request):
        i = items.get(wid)
        if i is None or i["state"] != "POSTED":
            return err(404, "not_found", "Nothing delivered yet")
        return {"resource": {"synthetic": True, "sku": i["sku"]}, "sha256": "00" * 32}

    return app


def serve_in_thread(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("test server did not start")
    return server, t
