"""HTTP API for Elix (specs/04-api.md), plus SSE and hosting for the console.

The server is thin. It turns a bearer token into an actor, calls the facade,
and maps engine errors to the error body in spec 04. Every permission decision
is made by the engine, which also audits refusals.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from elix.contracts import Denied, PurchaseRequest, RailError, VendorCreditNote, VendorInvoice
from elix.engine import Elix
from elix.engine.errors import Conflict, Invalid, NotFound
from elix.engine.permissions import can
from elix.engine.settings import console_dist, default_db_path, eval_dir
from elix.engine.workflow import deny
from elix.server.state import ServerState
from elix.world.config import TOKENS

PING_SECONDS = 15


# ---------------------------------------------------------------- request bodies


class NoteBody(BaseModel):
    note: str = ""


class AskBody(BaseModel):
    text: str


class AnswerBody(BaseModel):
    question_id: str
    text: str


class VerifyPayeeBody(BaseModel):
    method: str = "Other"
    note: str = ""


class RecodeBody(BaseModel):
    line_index: int
    new_account: str
    note: str = ""


class CreditNoteBody(BaseModel):
    vendor_id: str
    credit_number: str
    amount_cents: int
    reason: str = ""
    issued_at: Optional[str] = None


class RunAgentBody(BaseModel):
    task: str = ""
    scripted: bool = False


class EvalRunBody(BaseModel):
    fast: bool = False
    seed: int = 11


# ---------------------------------------------------------------- errors


def error(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **{k: v for k, v in extra.items()
                                                                         if v is not None}}}, status_code=status)


class Unauthorized(Exception):
    pass


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Unauthorized)
    async def _unauthorized(request: Request, exc: Unauthorized):
        return error(401, "unauthorized", str(exc) or "A valid bearer token is required.")

    @app.exception_handler(Denied)
    async def _denied(request: Request, exc: Denied):
        return error(403, "denied", exc.message, rule_id=exc.rule_id, audit_seq=exc.audit_seq)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return error(404, "not_found", exc.message, audit_seq=getattr(exc, "audit_seq", None))

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict):
        return error(409, "conflict", exc.message, audit_seq=exc.audit_seq, details=exc.details)

    @app.exception_handler(Invalid)
    async def _invalid(request: Request, exc: Invalid):
        return error(400, "invalid", exc.message, audit_seq=getattr(exc, "audit_seq", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        message = f"The request is not valid. {where} {first.get('msg', '')}".strip()
        return error(422, "invalid", message.rstrip(".") + ".", details=json.loads(json.dumps(exc.errors(), default=str)))

    @app.exception_handler(RailError)
    async def _rail(request: Request, exc: RailError):
        return error(502, "rail_error", str(exc))


# ---------------------------------------------------------------- app


def create_app(db_path: Optional[str | Path] = None, registry: Any = None, *, live_clock: bool = True,
               seed_if_missing: bool = True, api_base: Optional[str] = None) -> FastAPI:
    if registry is None:
        from elix.rails import make_registry

        registry = make_registry()
    state = ServerState(Path(db_path or default_db_path()), registry, live_clock=live_clock,
                        seed_if_missing=seed_if_missing)
    app = FastAPI(title="Elix", version="0.1.0",
                  description="The controller for agent spend. Kestrel Labs is a fictional company. All data is synthetic.")
    app.state.elix_state = state
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)

    # ------------------------------------------------------------ auth

    def engine() -> Elix:
        return state.elix

    def actor_from(request: Request, token: Optional[str] = None) -> str:
        header = request.headers.get("authorization", "")
        value = header[7:].strip() if header.lower().startswith("bearer ") else (token or "")
        actor = TOKENS.get(value)
        if actor is None:
            raise Unauthorized("Missing or unknown bearer token." if value else "A bearer token is required.")
        return actor

    def current(request: Request) -> str:
        return actor_from(request)

    def human(request: Request) -> str:
        actor = actor_from(request)
        if not actor.startswith("human:"):
            elix = state.elix
            raise deny(elix.ctx, actor=actor, action=f"call {request.url.path}", subject=request.url.path,
                       reason=f"{elix.config.actor_name(actor)} is an agent and this endpoint is for people.")
        return actor

    def require(elix: Elix, actor: str, action: str, subject: str) -> None:
        verdict = can(elix.config, actor, action)
        if not verdict.allowed:
            raise deny(elix.ctx, actor=actor, action=action, subject=subject, reason=verdict.reason,
                       rule_id=verdict.rule_id)

    def detail(elix: Elix, item_id: str, actor: str) -> dict:
        return elix.work_item(item_id, actor).model_dump(mode="json")

    # ------------------------------------------------------------ health, session, world

    @app.get("/healthz")
    def healthz():
        elix = state.elix
        return {"ok": True, "service": "elix-api", "today": elix.clock.now(), "rail_mode": elix.mode}

    @app.get("/api/session")
    def session(actor: str = Depends(current)):
        elix = state.elix
        return {**elix.session(actor), "now": elix.clock.now()}

    @app.get("/api/world")
    def world(actor: str = Depends(current)):
        elix = state.elix
        data = elix.world().model_dump(mode="json")
        data["rail"] = {"mode": elix.mode, "kind": getattr(elix.registry, "kind", "real"),
                        "settlement_label": getattr(elix.registry, "settlement_label", "Offline simulation")}
        data["now"] = elix.clock.now()
        return data

    @app.get("/api/summary")
    def summary(period: Optional[str] = None, actor: str = Depends(current)):
        return state.elix.summary(period)

    # ------------------------------------------------------------ work items

    @app.get("/api/work-items")
    def work_items(actor: str = Depends(current), state_: Optional[str] = Query(None, alias="state"),
                   kind: Optional[str] = None, agent: Optional[str] = None, vendor: Optional[str] = None,
                   flag: Optional[str] = None, period: Optional[str] = None, inbox: Optional[str] = None,
                   q: Optional[str] = None, limit: int = 100, offset: int = 0):
        items, total = state.elix.work_items_page(actor, state=state_, kind=kind, agent=agent, vendor=vendor,
                                                  flag=flag, period=period, inbox=inbox, q=q,
                                                  limit=max(1, min(limit, 1000)), offset=max(0, offset))
        return {"items": [i.model_dump(mode="json") for i in items], "total": total}

    @app.get("/api/work-items/{item_id}")
    def work_item(item_id: str, actor: str = Depends(current)):
        return detail(state.elix, item_id, actor)

    @app.get("/api/work-items/{item_id}/resource")
    def work_item_resource(item_id: str, actor: str = Depends(current)):
        elix = state.elix
        item = elix.get_item(item_id)
        elix.check_read(actor, item)
        from elix.rails import artifacts

        view = artifacts.resource_view(_artifact_key(elix, item_id))
        if view is None:
            raise NotFound(f"{item_id} has no delivered resource. Resources are returned only after payment.")
        return {"item_id": item_id, **view}

    @app.get("/api/work-items/{item_id}/x402")
    def work_item_x402(item_id: str, actor: str = Depends(current)):
        elix = state.elix
        item = elix.get_item(item_id)
        elix.check_read(actor, item)
        from elix.rails import artifacts

        view = artifacts.x402_view(_artifact_key(elix, item_id))
        if view is None:
            if item.challenge is None:
                raise NotFound(f"{item_id} did not go through x402.")
            view = {"challenge_decoded": item.challenge.model_dump(mode="json"), "payload_decoded": None,
                    "payload_sha256": item.receipt.payload_sha256 if item.receipt else None,
                    "payment_response_decoded": None,
                    "signature_verified": item.receipt.signature_verified if item.receipt else None,
                    "simulated": item.simulated, "tx_ref": item.receipt.tx_ref if item.receipt else None,
                    "resource_url": item.resource_url, "refused": None}
        return {"item_id": item_id, **view}

    @app.post("/api/purchases", status_code=201)
    def purchase(req: PurchaseRequest, actor: str = Depends(current)):
        elix = state.elix
        item, replay = elix.request_purchase_ex(actor, req)
        body = detail(elix, item.id, actor)
        return JSONResponse(body, status_code=200 if replay else 201)

    @app.post("/api/work-items/{item_id}/approve")
    def approve(item_id: str, body: NoteBody = Body(default_factory=NoteBody), actor: str = Depends(current)):
        elix = state.elix
        item = elix.approve(actor, item_id, body.note)
        return detail(elix, item.id, actor)

    @app.post("/api/work-items/{item_id}/reject")
    def reject(item_id: str, body: NoteBody = Body(default_factory=NoteBody), actor: str = Depends(current)):
        elix = state.elix
        item = elix.reject(actor, item_id, body.note)
        return detail(elix, item.id, actor)

    @app.post("/api/work-items/{item_id}/ask")
    def ask(item_id: str, body: AskBody, actor: str = Depends(current)):
        elix = state.elix
        item = elix.ask(actor, item_id, body.text)
        return detail(elix, item.id, actor)

    @app.post("/api/work-items/{item_id}/answer")
    def answer(item_id: str, body: AnswerBody, actor: str = Depends(current)):
        elix = state.elix
        item = elix.answer(actor, item_id, body.question_id, body.text)
        return detail(elix, item.id, actor)

    @app.post("/api/work-items/{item_id}/verify-payee")
    def verify_payee(item_id: str, body: VerifyPayeeBody = Body(default_factory=VerifyPayeeBody),
                     actor: str = Depends(current)):
        elix = state.elix
        item = elix.verify_payee(actor, item_id, body.method, body.note)
        return detail(elix, item.id, actor)

    # ------------------------------------------------------------ invoices

    @app.post("/api/invoices", status_code=201)
    def invoice(body: VendorInvoice, actor: str = Depends(current)):
        elix = state.elix
        require(elix, actor, "ingest_invoice", body.invoice_number)
        item = elix.ingest_invoice(body, triggered_by=actor)
        return detail(elix, item.id, actor)

    @app.post("/api/invoices/{invoice_number}/credit-notes", status_code=201)
    def credit_note(invoice_number: str, body: CreditNoteBody, actor: str = Depends(current)):
        """Record a vendor credit note against an approved invoice. Accountant and controller tokens only.

        The engine checks the role and the invoice, audits any refusal, and the gateway books the credit.
        """
        elix = state.elix
        credit = VendorCreditNote(invoice_number=invoice_number, **body.model_dump())
        item = elix.record_credit_note(credit, actor_id=actor)
        return detail(elix, item.id, actor)

    @app.post("/api/invoices/pdf", status_code=201)
    def invoice_pdf(file: UploadFile = File(...), offline: bool = Query(False), actor: str = Depends(current)):
        """Upload an invoice PDF. Accountant and controller only.

        Claude reads it when ANTHROPIC_API_KEY is set, else the offline parser does (offline=true forces it).
        Deterministic checks then decide. A clean invoice reconciles like any other; one that fails a check waits
        for the accountant and nothing posts.
        """
        from elix import intake as pdf_intake

        elix = state.elix
        name = (file.filename or "invoice.pdf").replace("\\", "/").rsplit("/", 1)[-1][:120]
        require(elix, actor, "intake_invoice", name)
        data = file.file.read(pdf_intake.MAX_BYTES + 1)
        try:
            extraction = pdf_intake.extract(data, name, force_offline=offline)
        except pdf_intake.ParseError as exc:
            raise Invalid(str(exc))
        digest = pdf_intake.sha256(data)
        item, result = elix.intake_invoice(actor, extraction, filename=name, sha256=digest, size=len(data))
        body = {"intake": {**result.view(), "filename": name, "sha256": digest, "bytes": len(data),
                           "extraction": extraction.model_dump(mode="json")},
                "item": detail(elix, item.id, actor)}
        return JSONResponse(body, status_code=201)

    @app.get("/api/intake/samples")
    def intake_samples(actor: str = Depends(human)):
        from elix import intake as pdf_intake

        return {"samples": pdf_intake.samples(), "extractor": pdf_intake.mode()}

    @app.get("/api/intake/samples/{name}")
    def intake_sample(name: str, actor: str = Depends(human)):
        from elix import intake as pdf_intake

        path = pdf_intake.sample_path(name)
        if path is None:
            raise NotFound(f"There is no sample invoice called {name}.")
        return FileResponse(path, media_type="application/pdf", filename=name)

    @app.get("/api/reconciliations")
    def reconciliations(period: Optional[str] = None, actor: str = Depends(human)):
        elix = state.elix
        items = elix.work_items(actor, kind="invoice", period=period)
        return {"items": [{"item": i.model_dump(mode="json"), "reconciliation": i.data.get("reconciliation")}
                          for i in items if i.data.get("reconciliation")]}

    # ------------------------------------------------------------ ledger

    @app.get("/api/ledger")
    def ledger(period: Optional[str] = None, account: Optional[str] = None, limit: int = 200, offset: int = 0,
               actor: str = Depends(human)):
        elix = state.elix
        period = period or elix.clock.period()
        entries = elix.ledger(period, account=account, limit=max(1, min(limit, 2000)), offset=max(0, offset))
        return {"entries": [e.model_dump(mode="json") for e in entries], "total": elix.ledger_count(period, account)}

    @app.get("/api/balances")
    def balances(period: Optional[str] = None, actor: str = Depends(human)):
        elix = state.elix
        period = period or elix.clock.period()
        return {"period": period, "balances": [b.model_dump(mode="json") for b in elix.balances(period)],
                "trial_balance_ok": elix.trial_balance_ok(period)}

    @app.post("/api/ledger/{je_id}/recode")
    def recode(je_id: str, body: RecodeBody, actor: str = Depends(current)):
        elix = state.elix
        item = elix.recode(actor, je_id, body.line_index, body.new_account, body.note)
        return detail(elix, item.id, actor)

    # ------------------------------------------------------------ policies

    @app.get("/api/policies")
    def policies(status: Optional[str] = None, actor: str = Depends(human)):
        return {"policies": [p.model_dump(mode="json") for p in state.elix.policies(status)]}

    @app.post("/api/policies/learn")
    def learn(actor: str = Depends(current)):
        return {"proposed": [p.model_dump(mode="json") for p in state.elix.learn(actor)]}

    @app.post("/api/policies/{policy_id}/approve")
    def approve_policy(policy_id: str, body: NoteBody = Body(default_factory=NoteBody), actor: str = Depends(current)):
        return state.elix.approve_policy(actor, policy_id, body.note).model_dump(mode="json")

    @app.post("/api/policies/{policy_id}/reject")
    def reject_policy(policy_id: str, body: NoteBody = Body(default_factory=NoteBody), actor: str = Depends(current)):
        return state.elix.reject_policy(actor, policy_id, body.note).model_dump(mode="json")

    # ------------------------------------------------------------ periods

    @app.get("/api/periods")
    def periods(actor: str = Depends(current)):
        return {"periods": [p.model_dump(mode="json") for p in state.elix.periods()]}

    @app.post("/api/periods/{period}/close-preview")
    def close_preview(period: str, actor: str = Depends(current)):
        return state.elix.close_preview(actor, period).model_dump(mode="json")

    @app.post("/api/periods/{period}/close")
    def close_period(period: str, actor: str = Depends(current)):
        return state.elix.close_period(actor, period).model_dump(mode="json")

    # ------------------------------------------------------------ audit

    @app.get("/api/audit")
    def audit_log(subject: Optional[str] = None, actor_filter: Optional[str] = Query(None, alias="actor"),
                  action: Optional[str] = None, allowed: Optional[bool] = None, limit: int = 200,
                  before_seq: Optional[int] = None, actor: str = Depends(human)):
        events = state.elix.audit(subject=subject, actor=actor_filter, action=action, allowed=allowed,
                                  limit=max(1, min(limit, 2000)), before_seq=before_seq)
        return {"events": [e.model_dump(mode="json") for e in events]}

    @app.get("/api/audit/verify")
    def audit_verify(actor: str = Depends(human)):
        return state.elix.verify_chain().model_dump(mode="json")

    @app.get("/api/why/{subject}")
    def why(subject: str, actor: str = Depends(human)):
        return state.elix.why(subject).model_dump(mode="json")

    # ------------------------------------------------------------ agents

    @app.get("/api/agents")
    def agents(actor: str = Depends(current)):
        from elix.agents import runner

        mode = runner.mode_for()
        return {"agents": [{**a, "mode": mode} for a in state.elix.agents_status()]}

    @app.get("/api/agents/sessions")
    def agent_sessions(agent: Optional[str] = None, actor: str = Depends(current)):
        from elix.agents import sessions

        if actor.startswith("agent:"):
            agent = actor
        return {"sessions": sessions.list_sessions(agent)}

    @app.get("/api/agents/sessions/{session_id}")
    def agent_session(session_id: str, actor: str = Depends(current)):
        from elix.agents import sessions

        found = sessions.get_session(session_id)
        if found is None or (actor.startswith("agent:") and found.get("agent_id") != actor):
            raise NotFound(f"There is no agent session called {session_id}.")
        return found

    @app.post("/api/agents/{agent}/run")
    def run_agent(agent: str, request: Request, body: RunAgentBody = Body(default_factory=RunAgentBody),
                  actor: str = Depends(human)):
        from elix.agents import runner

        elix = state.elix
        require(elix, actor, "run_scenarios", f"agent:{agent}")
        agent_id = agent if agent.startswith("agent:") else f"agent:{agent}"
        token = runner.AGENT_TOKENS.get(agent_id)
        if token is None:
            raise NotFound(f"There is no agent called {agent}.")
        base = api_base or str(request.base_url).rstrip("/")
        session_id = runner.start_run(agent_id, body.task, base, token, force_scripted=body.scripted)
        elix.record(actor, "run_agent", agent_id, f"{elix.config.actor_name(actor)} started a run of "
                    f"{elix.config.actor_name(agent_id)}.", evidence={"session_id": session_id, "task": body.task})
        return {"session_id": session_id}

    # ------------------------------------------------------------ proof

    @app.get("/api/eval/latest")
    def eval_latest(actor: str = Depends(current)):
        path = eval_dir() / "report.json"
        if not path.exists():
            raise NotFound("No eval report yet. Run python -m elix eval to create one.")
        report = json.loads(path.read_text())
        report.pop("anomaly_details", None)
        return report

    @app.post("/api/eval/run")
    def eval_run(body: EvalRunBody = Body(default_factory=EvalRunBody), actor: str = Depends(human)):
        elix = state.elix
        require(elix, actor, "run_scenarios", "eval")
        if state.eval_thread is not None and state.eval_thread.is_alive():
            raise Conflict("The eval is already running.")
        fast = body.fast or getattr(elix.registry, "kind", "real") == "fake"
        base = getattr(elix.registry, "vendor_base_url", None)

        def progress(status: str, fraction: float) -> None:
            state.eval_status = {"status": status, "progress": round(fraction, 3)}
            state.bus.publish("eval", state.eval_status)

        def work() -> None:
            from elix.eval.run import run_eval

            try:
                progress("started", 0.0)
                run_eval(seed=body.seed, fast=fast, progress=progress, vendor_base_url=base)
            except Exception as exc:  # pragma: no cover - reported over SSE
                state.eval_status = {"status": "failed", "progress": 0.0, "error": str(exc)}
                state.bus.publish("eval", state.eval_status)

        state.eval_thread = threading.Thread(target=work, name="elix-eval", daemon=True)
        state.eval_thread.start()
        elix.record(actor, "run_eval", "eval", f"{elix.config.actor_name(actor)} started the eval.",
                    evidence={"seed": body.seed, "fast": fast})
        return {"started": True, "fast": fast}

    # ------------------------------------------------------------ scenarios and demo

    @app.get("/api/scenarios")
    def scenarios(actor: str = Depends(current)):
        from elix.scenarios import list_scenarios

        return {"scenarios": list_scenarios(state.elix)}

    @app.post("/api/scenarios/{scenario_id}/run")
    def run_scenario(scenario_id: str, body: Optional[dict] = Body(default=None), actor: str = Depends(human)):
        from elix.scenarios import run_scenario as run

        elix = state.elix
        require(elix, actor, "run_scenarios", f"scenario:{scenario_id}")
        params = body or {}
        state.bus.publish("scenario", {"id": scenario_id, "status": "running", "message": ""})
        result = run(elix, scenario_id, actor, params if scenario_id == "custom" else None)
        state.bus.publish("scenario", {"id": scenario_id, "status": "done", "message": result["message"],
                                       "result_ids": result["result_ids"]})
        return result

    @app.post("/api/demo/reset")
    def demo_reset(actor: str = Depends(human)):
        elix = state.elix
        require(elix, actor, "run_scenarios", "demo")
        state.reset()
        return {"ok": True}

    # ------------------------------------------------------------ events

    @app.get("/api/events")
    async def events(request: Request, token: Optional[str] = None, limit: Optional[int] = None):
        actor_from(request, token)
        loop = asyncio.get_running_loop()
        subscription = state.bus.subscribe(loop)

        async def stream():
            sent = 0
            try:
                yield "retry: 3000\n\n"
                yield f"event: ready\ndata: {json.dumps({'now': state.elix.clock.now()})}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        kind, payload = await asyncio.wait_for(subscription.async_queue.get(), timeout=PING_SECONDS)
                    except asyncio.TimeoutError:
                        yield f"event: ping\ndata: {json.dumps({'at': int(time.time())})}\n\n"
                        continue
                    yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
                    sent += 1
                    if limit and sent >= limit:
                        break
            finally:
                subscription.close()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------ marketing site, then console hosting

    @app.get("/site", include_in_schema=False)
    def site_page():
        page = Path(__file__).resolve().parents[2] / "site" / "dist" / "index.html"
        if not page.is_file():
            return HTMLResponse("<p>The site is not built. Run python site/build.py from the repo root.</p>", status_code=404)
        body = page.read_text(encoding="utf-8")
        shell = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                 '<style>:root{color-scheme:light}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>'
                 '</head><body>')
        return HTMLResponse(shell + body + "</body></html>", headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            raise NotFound(f"There is no API endpoint at /{path}.")
        dist = console_dist()
        index = dist / "index.html"
        if path:
            candidate = (dist / path).resolve()
            if candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(NOT_BUILT, status_code=200)

    return app


def _artifact_key(elix: Elix, item_id: str) -> str:
    namespace = getattr(getattr(elix.registry, "rail", lambda _: None)("x402"), "namespace", None)
    return f"{namespace}:{item_id}" if namespace else item_id


NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Elix</title>
<style>
:root{--paper:#F1F4F1;--ink:#141A24;--ink-2:#4D5866;--rule:#CFD8D3}
@media (prefers-color-scheme: dark){:root{--paper:#0F1416;--ink:#E9EEF0;--ink-2:#A6B2B8;--rule:#2A3538}}
body{background:var(--paper);color:var(--ink);font:16px/1.5 "Helvetica Neue",Arial,sans-serif;margin:0;padding:48px 16px}
main{max-width:640px;margin:0 auto}h1{font-size:28px;margin:0 0 12px}p{color:var(--ink-2)}
code{font-family:ui-monospace,Menlo,monospace;border:1px solid var(--rule);padding:2px 6px;border-radius:4px}
</style></head><body><main>
<h1>The Elix console is not built yet.</h1>
<p>The API is running. Build the console with <code>npm ci && npm run build</code> in the console folder, then reload this page.</p>
<p>Kestrel Labs is a fictional company. All data is synthetic.</p>
</main></body></html>"""
