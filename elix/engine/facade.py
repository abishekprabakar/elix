"""`Elix`, the one interface the server, CLI, agents, scenarios and tests use.

Every public method takes the acting actor id. State changes go through
`workflow.transition()`, which asks `permissions.can()` and audits the attempt.
Money only moves through `gateway`. A lock serializes calls so the server can
use one instance from many threads.
"""

from __future__ import annotations

import functools
import re
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import parse_qs, urlencode, urlparse

from elix.contracts import (
    AccountBalance, AuditEvent, ChainCheck, ClosePreview, ExtractedInvoice, JournalEntry, JournalLine,
    MeterRecord, NextAction, PaymentIntent, Period, Policy, PurchaseRequest, Question, ReconciliationProposal,
    Role, State, Vendor, VendorCreditNote, VendorInvoice, WhyReport, WhyStep, World, WorkItem, WorkItemDetail,
    WorkItemKind,
)
from elix.engine import audit, corrections, credits, explain, gateway, intake, learner, periods, reconcile
from elix.engine.clock import Clock, iso, parse_ts
from elix.engine.context import POLICY, SYNC, Ctx, WorldConfig
from elix.engine.errors import Conflict, Invalid, NotFound
from elix.engine.events import EventBus
from elix.engine.fmt import cents_of, money, possessive, short_addr
from elix.engine.permissions import approver_name, can
from elix.engine.policy import evaluate_purchase, find_sku
from elix.engine.settings import vendor_base_url as default_vendor_base_url
from elix.engine.store import Store
from elix.engine.workflow import create, deny, merged_data, transition

T = TypeVar("T")

COMMITTED = ["NEW", "PROPOSED", "NEEDS_INFO", "NEEDS_APPROVAL", "APPROVED"]
TASK_REF = re.compile(
    r"(?:task(?:[\s_-]*ref(?:erence)?)?|ticket|ref)\s*[:#=]?\s*([A-Za-z0-9][A-Za-z0-9._/-]{2,})", re.IGNORECASE)
TASK_TOKEN = re.compile(r"\b([A-Za-z]+[-_][A-Za-z0-9-_]*\d[A-Za-z0-9-_]*|[A-Z]{2,}-\d+)\b")

# Fallback routes for building x402 resource URLs when elix.vendors is not importable (spec 05).
VENDOR_ROUTES = {
    "ven_terrafield": "terrafield/scenes", "ven_stormvault": "stormvault/archive", "ven_lumen": "lumen/spot",
    "ven_signalboard": "signalboard/records", "ven_parsec": "parsec/queries",
}


def locked(method: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(method)
    def wrapper(self: "Elix", *args: Any, **kwargs: Any) -> T:
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def default_config() -> WorldConfig:
    from elix.world.config import KESTREL

    return KESTREL


class Elix:
    def __init__(self, db_path: str | Path, rail_registry: Any, clock: Optional[Clock] = None,
                 config: Optional[WorldConfig] = None, bus: Optional[EventBus] = None,
                 vendor_base_url: Optional[str] = None):
        self.db_path = str(db_path)
        self.store = Store(db_path)
        self.config = config or default_config()
        if clock is None:
            clock = Clock(self.store.get_meta("clock") or self.config.company.today)
        self.clock = clock
        self.bus = bus or EventBus()
        self.registry = rail_registry
        base = vendor_base_url or getattr(rail_registry, "vendor_base_url", None) or default_vendor_base_url()
        self.ctx = Ctx(store=self.store, clock=clock, config=self.config, registry=rail_registry, bus=self.bus,
                       vendor_base_url=base)
        self._lock = threading.RLock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        if not self.store.vendors():
            with self.store.tx():
                for vendor in self.config.vendors:
                    self.store.save_vendor(vendor)
                audit.append_event(
                    self.store, at=self.clock.now(), actor=SYNC, action="load_vendor_master", subject="vendor_master",
                    allowed=True, reason=f"Sync loaded {len(self.config.vendors)} vendors into the vendor master.",
                    evidence={"vendors": [v.id for v in self.config.vendors]},
                )
        periods.ensure_period(self.ctx, self.clock.period())

    def close(self) -> None:
        self.store.close()

    @property
    def mode(self) -> str:
        return getattr(self.registry, "mode", "offline")

    # ------------------------------------------------------------------ world

    @locked
    def world(self) -> World:
        company = self.config.company.model_copy(update={"today": self.clock.today()})
        return World(company=company, actors=self.config.actors, agents=self.config.agents,
                     vendors=self.store.vendors(), accounts=self.config.accounts, periods=self.store.periods())

    @locked
    def periods(self) -> list[Period]:
        return self.store.periods()

    # ------------------------------------------------------------------ purchases

    def request_purchase(self, actor_id: str, req: PurchaseRequest) -> WorkItem:
        item, _ = self.request_purchase_ex(actor_id, req)
        return item

    @locked
    def request_purchase_ex(self, actor_id: str, req: PurchaseRequest) -> tuple[WorkItem, bool]:
        """Like request_purchase, and also says whether this was an idempotent replay."""
        ctx = self.ctx
        verdict = can(self.config, actor_id, "propose")
        if not verdict.allowed:
            raise deny(ctx, actor=actor_id, action="propose", subject="purchase", reason=verdict.reason,
                       rule_id=verdict.rule_id)
        if req.idempotency_key:
            existing = self.store.query_items(kind="purchase", agent_id=actor_id, idempotency_key=req.idempotency_key,
                                              limit=1)
            if existing:
                self._audit(actor_id, "replay", existing[0].id,
                            f"{self.config.actor_name(actor_id)} resent idempotency key {req.idempotency_key}, so Elix "
                            f"returned {existing[0].id} unchanged.", evidence={"idempotency_key": req.idempotency_key})
                return existing[0], True
        if req.quantity <= 0:
            raise Invalid("Quantity must be greater than zero.")
        vendor = ctx.vendor(req.vendor_id)
        sku = find_sku(vendor, req.sku)
        unit = req.unit_price_cents if req.unit_price_cents is not None else (sku.unit_price_cents if sku else None)
        if unit is None:
            raise Invalid(f"{req.sku} has no contract price, so the request must include unit_price_cents.")
        if unit < 0:
            raise Invalid("Unit price cannot be negative.")
        agent = self.config.agent(actor_id)
        rail = "x402" if vendor is not None and "x402" in vendor.rails else "bank"
        resource_url = req.resource_url or (self._resource_url(req.vendor_id, req.sku, req.quantity)
                                            if vendor is not None and rail == "x402" else None)
        title = explain.item_title(self.config, vendor, req.vendor_id, req.sku, req.quantity)
        amount = cents_of(req.quantity, unit)
        data: dict[str, Any] = {"agent_id": actor_id, "rail": rail, "usage_window": self._usage_window(resource_url)}
        if req.pay_to_override:
            data["pay_to_override"] = req.pay_to_override
        item = WorkItem(
            id="", kind=WorkItemKind.purchase, state=State.NEW, period=periods.current_period(self.ctx), created_at="",
            updated_at="", requested_by=actor_id, proposed_by=actor_id, vendor_id=req.vendor_id, sku=req.sku,
            quantity=req.quantity, unit_price_cents=unit, amount_cents=amount,
            cost_center=agent.cost_center if agent else None, purpose=req.purpose or None, task_ref=req.task_ref,
            idempotency_key=req.idempotency_key, resource_url=resource_url, memo=req.memo, title=title,
            simulated=self.mode != "live", data=data,
        )
        name = self.config.actor_name(actor_id)
        item = create(ctx, item, actor=actor_id, action="propose",
                      reason=f"{name} asked to buy {title[0].lower() + title[1:]} for {money(amount)}.",
                      evidence={"purpose": req.purpose, "task_ref": req.task_ref,
                                "idempotency_key": req.idempotency_key})
        challenge = None
        if rail == "x402" and vendor is not None:
            intent = PaymentIntent(work_item_id=item.id, vendor_id=item.vendor_id, resource_url=resource_url,
                                   amount_cents=amount, sku=item.sku, quantity=item.quantity,
                                   idempotency_key=item.idempotency_key, pay_to_override=req.pay_to_override)
            try:
                challenge = ctx.rail("x402").challenge(intent)
            except Exception as exc:
                error = f"Elix could not get payment terms from {vendor.name}. {str(exc) or exc.__class__.__name__}"
                return transition(ctx, item, "fail", POLICY, patch={"data": merged_data(item, error=error)},
                                  reason=error, evidence={"error": str(exc)}), False
        return self._evaluate(item, challenge), False

    def _evaluate(self, item: WorkItem, challenge=None) -> WorkItem:
        ctx = self.ctx
        if challenge is not None:
            item = item.model_copy(update={"challenge": challenge})
        ev = evaluate_purchase(ctx, item, item.requested_by)
        failed = [c for c in ev.decision.checks if not c.passed]
        kept_links = [link for link in item.links if link.rel not in ("duplicate_of", "policy")]
        links = [*(link.model_dump() for link in kept_links), *ev.links]
        patch = {
            "challenge": item.challenge.model_dump() if item.challenge else None,
            "decision": ev.decision.model_dump(), "flags": [f.value for f in ev.flags], "account": ev.account,
            "required_approver_role": ev.decision.required_approver_role, "links": links,
            "data": merged_data(item, spend_role=ev.spend_role.value if ev.spend_role else None, spend_approved=False,
                                payee_hold=ev.payee_hold, payee_verified=False,
                                policy_id=ev.policy.id if ev.policy else None),
        }
        reason = (f"Elix ran {len(ev.decision.checks)} checks and all of them passed." if not failed else
                  f"Elix ran {len(ev.decision.checks)} checks and {len(failed)} did not pass "
                  f"({', '.join(c.rule_id for c in failed)}).")
        evidence: dict[str, Any] = {"failed_rules": [c.rule_id for c in failed], "policy_version": ev.decision.policy_version}
        if item.challenge is not None:
            evidence["challenge"] = {"pay_to": item.challenge.pay_to, "amount_cents": item.challenge.amount_cents,
                                     "network": item.challenge.network, "asset": item.challenge.asset}
        item = transition(ctx, item, "evaluate", POLICY, patch=patch, reason=reason, evidence=evidence,
                          refs=[link["id"] for link in ev.links])
        summary = ev.decision.summary
        outcome = ev.decision.outcome.value
        rule_evidence = {"failed_rules": [c.rule_id for c in failed]}
        if outcome == "auto_approve":
            item = transition(ctx, item, "auto_approve", POLICY, patch={"approved_by": POLICY}, reason=summary,
                              evidence={**rule_evidence, "policy_id": ev.policy.id if ev.policy else None},
                              refs=[ev.policy.id] if ev.policy else [])
            return gateway.execute_purchase(ctx, item)
        if outcome == "needs_approval":
            return transition(ctx, item, "escalate", POLICY, reason=summary, evidence={
                **rule_evidence, "required_approver_role": ev.decision.required_approver_role,
                "payee_hold": ev.payee_hold})
        if outcome == "needs_info":
            question = Question(id=f"Q-{self.store.next_counter('question', start=1)}", asked_by=POLICY,
                                to=item.requested_by, text=ev.question or "", at=self.clock.now())
            return transition(ctx, item, "ask", POLICY,
                              patch={"questions": [*(q.model_dump() for q in item.questions), question.model_dump()]},
                              reason=f"{summary} Elix asked {self.config.actor_name(item.requested_by)} for the "
                                     f"purpose and task reference.", evidence=rule_evidence)
        return transition(ctx, item, "deny", POLICY, reason=summary, evidence=rule_evidence,
                          refs=[ev.duplicate_of] if ev.duplicate_of else [])

    @locked
    def answer(self, actor_id: str, item_id: str, question_id: str, text: str) -> WorkItem:
        item = self._get(item_id)
        question = next((q for q in item.questions if q.id == question_id), None)
        if question is None:
            raise NotFound(f"{item_id} has no question {question_id}.")
        text = (text or "").strip()
        if not text:
            raise Invalid("The answer cannot be empty.")
        now = self.clock.now()
        questions = [q.model_copy(update={"answer": text, "answered_by": actor_id, "answered_at": now})
                     if q.id == question_id else q for q in item.questions]
        patch: dict[str, Any] = {"questions": [q.model_dump() for q in questions]}
        if question.asked_by == POLICY:
            purpose = (item.purpose or "").strip()
            if len(purpose) < 12:
                patch["purpose"] = f"{purpose} {text}".strip()
            if not item.task_ref:
                found = TASK_REF.search(text) or TASK_TOKEN.search(text)
                if found:
                    patch["task_ref"] = found.group(1).rstrip(".,;")
        name = self.config.actor_name(actor_id)
        item = transition(self.ctx, item, "answer", actor_id, patch=patch,
                          reason=f"{name} answered question {question_id} with \"{text.rstrip('.')}.\"")
        if item.kind == WorkItemKind.purchase and item.state == State.PROPOSED:
            return self._evaluate(item)
        return item

    @locked
    def ask(self, actor_id: str, item_id: str, text: str) -> WorkItem:
        item = self._get(item_id)
        text = (text or "").strip()
        if not text:
            raise Invalid("The question cannot be empty.")
        to = item.data.get("agent_id") or item.requested_by
        question = Question(id=f"Q-{self.store.next_counter('question', start=1)}", asked_by=actor_id, to=to,
                            text=text, at=self.clock.now())
        return transition(self.ctx, item, "ask", actor_id,
                          patch={"questions": [*(q.model_dump() for q in item.questions), question.model_dump()]},
                          reason=f"{self.config.actor_name(actor_id)} asked {self.config.actor_name(to)} "
                                 f"\"{text.rstrip('?.')}?\"")

    # ------------------------------------------------------------------ human decisions

    @locked
    def approve(self, actor_id: str, item_id: str, note: str = "") -> WorkItem:
        item = self._get(item_id)
        if item.kind == WorkItemKind.policy:
            learner.decide_policy(self.ctx, actor_id, item.data["policy_id"], True, note)
            return self._get(item_id)
        if item.kind == WorkItemKind.purchase:
            return self._approve_purchase(actor_id, item, note)
        if intake.failed_intake(item):
            verdict = can(self.config, actor_id, "approve", item)
            if not verdict.allowed:
                raise deny(self.ctx, actor=actor_id, action="approve", subject=item.id, reason=verdict.reason,
                           rule_id=verdict.rule_id)
            raise _conflict(self.ctx, actor_id, "approve", item.id,
                            f"{item.id} failed its intake checks, so it cannot be approved or paid. Reject it and ask "
                            f"the vendor for a corrected invoice.")
        now = self.clock.now()
        actor = self.config.actor(actor_id)
        role = actor.role.value if actor else None
        kind = {"invoice": "invoice", "accrual": "accrual", "correction": "correction"}[item.kind.value]
        patch = {"approved_by": actor_id,
                 "approvals": [*item.approvals, {"actor": actor_id, "role": role, "at": now, "note": note, "kind": kind}]}
        name = self.config.actor_name(actor_id)
        what = {"invoice": "approved the invoice", "accrual": "approved the accrual",
                "correction": "approved the correction"}[kind]
        if item.kind == WorkItemKind.invoice and item.data.get("dispute_cents"):
            what = (f"approved paying {money(item.data['pay_cents'])} and disputing "
                    f"{money(item.data['dispute_cents'])}")
        item = transition(self.ctx, item, "approve", actor_id, patch=patch,
                          reason=f"{name} {what} on {item.id}" + _note(note))
        if item.kind == WorkItemKind.invoice:
            return gateway.execute_invoice(self.ctx, item)
        if item.kind == WorkItemKind.accrual:
            return gateway.post_accrual(self.ctx, item)
        return gateway.post_correction(self.ctx, item)

    def _approve_purchase(self, actor_id: str, item: WorkItem, note: str) -> WorkItem:
        ctx = self.ctx
        spend_role = item.data.get("spend_role")
        if item.state == State.NEEDS_APPROVAL and (not spend_role or item.data.get("spend_approved")):
            verdict = can(self.config, actor_id, "approve", item)
            if not verdict.allowed:
                raise deny(ctx, actor=actor_id, action="approve", subject=item.id, reason=verdict.reason,
                           rule_id=verdict.rule_id)
            raise _conflict(ctx, actor_id, "approve", item.id,
                            f"The spend on {item.id} needs no further approval. The only step left is for "
                            f"{approver_name(self.config, Role.controller, None)} to verify the payee.")
        payee_pending = bool(item.data.get("payee_hold")) and not item.data.get("payee_verified")
        target = State.NEEDS_APPROVAL if payee_pending else State.APPROVED
        now = self.clock.now()
        actor = self.config.actor(actor_id)
        approvals = [*item.approvals, {"actor": actor_id, "role": actor.role.value if actor else None, "at": now,
                                       "note": note, "kind": "spend"}]
        name = self.config.actor_name(actor_id)
        if payee_pending:
            vendor = ctx.vendor(item.vendor_id)
            reason = (f"{name} approved the spend on {item.id}{_note(note)} The payment still waits for "
                      f"{approver_name(self.config, Role.controller, None)} to verify "
                      f"{possessive(vendor.name if vendor else item.vendor_id)} new payout address.")
        else:
            reason = f"{name} approved {item.id}" + _note(note)
        item = transition(ctx, item, "approve", actor_id, to_state=target, reason=reason, patch={
            "approvals": approvals, "approved_by": actor_id, "data": merged_data(item, spend_approved=True)})
        if item.state == State.APPROVED:
            return gateway.execute_purchase(ctx, item)
        return item

    @locked
    def reject(self, actor_id: str, item_id: str, note: str = "") -> WorkItem:
        item = self._get(item_id)
        if item.kind == WorkItemKind.policy:
            learner.decide_policy(self.ctx, actor_id, item.data["policy_id"], False, note)
            return self._get(item_id)
        now = self.clock.now()
        return transition(self.ctx, item, "reject", actor_id,
                          patch={"data": merged_data(item, rejection={"actor": actor_id, "at": now, "note": note})},
                          reason=f"{self.config.actor_name(actor_id)} rejected {item.id}" + _note(note))

    @locked
    def verify_payee(self, actor_id: str, item_id: str, method: str, note: str = "") -> WorkItem:
        ctx = self.ctx
        item = self._get(item_id)
        if not (item.data.get("payee_hold") and not item.data.get("payee_verified")):
            verdict = can(self.config, actor_id, "verify_payee", item)
            if not verdict.allowed:
                raise deny(ctx, actor=actor_id, action="verify_payee", subject=item.id, reason=verdict.reason,
                           rule_id=verdict.rule_id)
            raise _conflict(ctx, actor_id, "verify_payee", item.id, f"{item.id} has no payee change to verify.")
        method = (method or "").strip() or "Other"
        spend_pending = bool(item.data.get("spend_role")) and not item.data.get("spend_approved")
        target = State.NEEDS_APPROVAL if spend_pending else State.APPROVED
        now = self.clock.now()
        vendor = ctx.vendor(item.vendor_id)
        vendor_name = vendor.name if vendor else (item.vendor_id or "the vendor")
        address = item.challenge.pay_to if item.challenge else (vendor.bank_account_last4 if vendor else None) or "bank"
        approvals = [*item.approvals, {"actor": actor_id, "role": "controller", "at": now, "note": note,
                                       "method": method, "kind": "payee"}]
        name = self.config.actor_name(actor_id)
        reason = (f"{name} verified {possessive(vendor_name)} payout address {short_addr(address)}. The method recorded "
                  f"was \"{method.rstrip('.')}\"" + (f" and the note says \"{note.rstrip('.')}.\"" if note else "."))
        if spend_pending:
            who = approver_name(self.config, Role(item.data["spend_role"]), item.requested_by)
            reason += f" The spend still needs approval from {who}."
        with self.store.tx():
            item = transition(ctx, item, "verify_payee", actor_id, to_state=target, reason=reason, patch={
                "approvals": approvals,
                "approved_by": item.approved_by or (actor_id if target == State.APPROVED else None),
                "data": merged_data(item, payee_verified=True, payee_verification={
                    "by": actor_id, "at": now, "method": method, "note": note, "address": address})},
                evidence={"method": method, "address": address})
            self.store.insert_payee_verification(vendor_id=item.vendor_id or "", address=address, verified_by=actor_id,
                                                 at=now, method=method, note=note, work_item_id=item.id)
        if item.state == State.APPROVED:
            return gateway.execute_purchase(ctx, item)
        return item

    @locked
    def recode(self, actor_id: str, je_id: str, line_index: int, new_account: str, note: str = "") -> WorkItem:
        return corrections.recode(self.ctx, actor_id, je_id, line_index, new_account, note)

    # ------------------------------------------------------------------ invoices and meters

    @locked
    def ingest_invoice(self, invoice: VendorInvoice, actor_id: str = SYNC, triggered_by: Optional[str] = None,
                       intake_record: Optional[dict[str, Any]] = None) -> WorkItem:
        ctx = self.ctx
        if not invoice.lines:
            raise Invalid("An invoice needs at least one line.")
        vendor = ctx.vendor(invoice.vendor_id)
        name = vendor.name if vendor else invoice.vendor_id
        data: dict[str, Any] = {"invoice": invoice.model_dump(mode="json"), "invoice_number": invoice.invoice_number,
                                "triggered_by": triggered_by}
        reason = f"Sync received {name} invoice {invoice.invoice_number} for {money(invoice.total_cents)}."
        evidence: dict[str, Any] = {"invoice_number": invoice.invoice_number, "triggered_by": triggered_by,
                                    "lines": [line.line_id for line in invoice.lines]}
        if intake_record is not None:
            data["intake"] = intake_record
            passed = len(intake_record.get("checks", []))
            reason += (f" It came from {intake_record.get('filename') or 'a PDF'}, which "
                       f"{self.config.actor_name(triggered_by)} uploaded. {intake.extractor_phrase(intake_record)} "
                       f"read it and all {passed} intake checks passed.")
            evidence.update({"filename": intake_record.get("filename"), "sha256": intake_record.get("sha256"),
                             "extractor": intake_record.get("extractor"), "model": intake_record.get("model")})
        item = WorkItem(
            id="", kind=WorkItemKind.invoice, state=State.NEW, period=periods.current_period(self.ctx), created_at="",
            updated_at="", requested_by=SYNC, proposed_by=POLICY, vendor_id=invoice.vendor_id,
            amount_cents=invoice.total_cents, account=vendor.default_account if vendor else "6990",
            title=f"{name} invoice {invoice.invoice_number}", simulated=True, data=data,
        )
        item = create(ctx, item, actor=actor_id, action="create", reason=reason, evidence=evidence)
        found = reconcile.match(ctx, invoice, item)
        proposal = found.proposal
        receipt_items = sorted({r["item_id"] for line in proposal.lines for r in line.evidence.get("receipts", [])
                                if line.variance_cents})
        links = [{"rel": "reconciles", "id": i} for i in receipt_items]
        if found.duplicate_of:
            links.append({"rel": "duplicate_of", "id": found.duplicate_of})
        failed = [c.rule_id for c in found.decision.checks if not c.passed]
        item = transition(
            ctx, item, "match", POLICY,
            patch={"decision": found.decision.model_dump(), "flags": [f.value for f in found.flags], "links": links,
                   "required_approver_role": found.decision.required_approver_role,
                   "data": merged_data(item, reconciliation=proposal.model_dump(mode="json"),
                                       pay_cents=proposal.pay_cents, dispute_cents=proposal.dispute_cents)},
            reason=(f"Elix matched {invoice.invoice_number} against the contract, the usage meter and x402 receipts. "
                    f"{proposal.summary}"),
            evidence={"rules": failed, "receipts": receipt_items, "pay_cents": proposal.pay_cents,
                      "dispute_cents": proposal.dispute_cents},
            refs=receipt_items,
        )
        outcome = found.decision.outcome.value
        if outcome == "auto_approve":
            item = transition(ctx, item, "auto_approve", POLICY, patch={"approved_by": POLICY},
                              reason=found.decision.summary, evidence={"rules": failed})
            return gateway.execute_invoice(ctx, item)
        if outcome == "deny":
            return transition(ctx, item, "deny", POLICY, reason=found.decision.summary, evidence={"rules": failed},
                              refs=[found.duplicate_of] if found.duplicate_of else [])
        return transition(ctx, item, "escalate", POLICY, reason=found.decision.summary,
                          evidence={"rules": failed, "required_approver_role": "accountant"})

    @locked
    def intake_invoice(self, actor_id: str, extraction: ExtractedInvoice, *, filename: str = "", sha256: str = "",
                       size: int = 0) -> tuple[WorkItem, "intake.IntakeResult"]:
        """An invoice read from a PDF. Deterministic checks decide; the extractor only proposed the fields.

        If every check passes, the invoice goes through ingest_invoice and reconciles like a structured invoice.
        If any fails, it becomes an invoice item waiting for the accountant with the failed checks, and nothing is
        reconciled, paid or posted. Accountant and controller only.
        """
        ctx = self.ctx
        verdict = can(self.config, actor_id, "intake_invoice")
        if not verdict.allowed:
            raise deny(ctx, actor=actor_id, action="intake_invoice", subject=filename or "invoice_pdf",
                       reason=verdict.reason, rule_id=verdict.rule_id)
        result = intake.validate(ctx, extraction)
        record = {
            "source": "pdf", "filename": filename, "sha256": sha256, "bytes": size,
            "extractor": extraction.extractor, "extractor_label": extraction.extractor_label,
            "model": extraction.model, "notes": extraction.notes, "pages": extraction.pages,
            "extracted": extraction.model_dump(mode="json"),
            "checks": [c.model_dump(mode="json") for c in result.checks],
            "lines": [line.view() for line in result.lines],
            "status": "accepted" if result.accepted else "failed",
            "uploaded_by": actor_id, "uploaded_at": self.clock.now(),
        }
        if result.accepted:
            item = self.ingest_invoice(result.invoice, triggered_by=actor_id, intake_record=record)
        else:
            item = intake.hold(ctx, extraction, result, record, actor_id)
        return item, result

    @locked
    def record_credit_note(self, credit: VendorCreditNote, actor_id: str = SYNC) -> WorkItem:
        """A vendor credit note against an approved invoice with an open dispute.

        Sync receives credit notes from vendors. An accountant or the controller can key in one the vendor sent by
        email. Elix checks the invoice, the vendor, the open dispute and the credit number first and audits every
        refusal. The gateway then books Dr 2010 Accounts payable, Cr 2150 Vendor disputes. Returns the invoice item.
        """
        return credits.record(self.ctx, actor_id, credit)

    @locked
    def ingest_meter(self, records: list[MeterRecord], actor_id: str = SYNC) -> int:
        verdict = can(self.config, actor_id, "ingest")
        if not verdict.allowed:
            raise deny(self.ctx, actor=actor_id, action="ingest", subject="meters", reason=verdict.reason,
                       rule_id=verdict.rule_id)
        if not records:
            return 0
        rows = [(r.vendor_id, r.sku, r.agent_id, r.quantity, r.window_start, r.window_end, r.source) for r in records]
        vendors = sorted({r.vendor_id for r in records})
        vendor_names = [v.name if (v := self.ctx.vendor(vid)) else vid for vid in vendors]
        with self.store.tx():
            self.store.insert_meters(rows)
            self._audit(actor_id, "ingest", "meters",
                        f"Sync recorded {len(records)} usage meter readings from {explain.join_words(vendor_names)}.",
                        evidence={"count": len(records), "vendors": vendors,
                                  "from": min(r.window_start for r in records), "to": max(r.window_end for r in records)})
        return len(records)

    @locked
    def update_vendor_payout(self, vendor_id: str, address: str, changed_at: Optional[str] = None,
                             actor_id: str = SYNC, source: str = "vendor portal") -> Vendor:
        """Sync records a payout address change from the vendor's portal. R03 holds payments until verified."""
        verdict = can(self.config, actor_id, "update_vendor")
        if not verdict.allowed:
            raise deny(self.ctx, actor=actor_id, action="update_vendor", subject=vendor_id, reason=verdict.reason,
                       rule_id=verdict.rule_id)
        vendor = self.ctx.vendor(vendor_id)
        if vendor is None:
            raise NotFound(f"There is no vendor called {vendor_id}.")
        changed_at = changed_at or self.clock.now()
        updated = vendor.model_copy(update={"previous_payout_address": vendor.payout_address,
                                            "payout_address": address, "payout_address_changed_at": changed_at})
        with self.store.tx():
            self.store.save_vendor(updated)
            self._audit(actor_id, "update_vendor", vendor_id,
                        f"Sync recorded that {vendor.name} changed its payout address in its {source} from "
                        f"{short_addr(vendor.payout_address)} to {short_addr(address)} at "
                        f"{parse_ts(changed_at).strftime('%H:%M')} UTC on {explain.day(changed_at)}.",
                        diff={"payout_address": [vendor.payout_address, address],
                              "payout_address_changed_at": [vendor.payout_address_changed_at, changed_at]},
                        evidence={"source": source})
        return updated

    # ------------------------------------------------------------------ policies

    @locked
    def policies(self, status: Optional[str] = None) -> list[Policy]:
        return self.store.policies(status=status)

    @locked
    def learn(self, actor_id: str = "system:learner") -> list[Policy]:
        if actor_id != "system:learner":
            verdict = can(self.config, actor_id, "learn")
            if not verdict.allowed:
                raise deny(self.ctx, actor=actor_id, action="learn", subject="policies", reason=verdict.reason,
                           rule_id=verdict.rule_id)
        proposed = learner.learn_from_history(self.ctx, triggered_by=actor_id)
        proposed += learner.learn_from_corrections(self.ctx, triggered_by=actor_id)
        self._audit("system:learner", "learn", "policies",
                    f"The learner reviewed closed periods and corrections and proposed {len(proposed)} "
                    f"{'policy' if len(proposed) == 1 else 'policies'}.",
                    evidence={"proposed": [p.id for p in proposed], "triggered_by": actor_id},
                    refs=[p.id for p in proposed])
        return proposed

    @locked
    def approve_policy(self, actor_id: str, policy_id: str, note: str = "") -> Policy:
        return learner.decide_policy(self.ctx, actor_id, policy_id, True, note)

    @locked
    def reject_policy(self, actor_id: str, policy_id: str, note: str = "") -> Policy:
        return learner.decide_policy(self.ctx, actor_id, policy_id, False, note)

    # ------------------------------------------------------------------ periods

    @locked
    def close_preview(self, actor_id: str, period: str) -> ClosePreview:
        return periods.close_preview(self.ctx, actor_id, period)

    @locked
    def close_period(self, actor_id: str, period: str) -> Period:
        return periods.close(self.ctx, actor_id, period)

    @locked
    def reopen_period(self, actor_id: str, period: str) -> None:
        periods.reopen(self.ctx, actor_id, period)

    @locked
    def open_books(self, date: str, lines: list[JournalLine], memo: str) -> JournalEntry:
        """Opening balances for a new world, posted through the gateway like everything else."""
        return gateway.post_opening_balances(self.ctx, date=date, lines=lines, memo=memo)

    # ------------------------------------------------------------------ reads

    @locked
    def work_items(self, actor_id: Optional[str] = None, **filters: Any) -> list[WorkItem]:
        items, _ = self.work_items_page(actor_id, **filters)
        return items

    @locked
    def work_items_page(self, actor_id: Optional[str] = None, *, state: Optional[str] = None,
                        kind: Optional[str] = None, agent: Optional[str] = None, vendor: Optional[str] = None,
                        flag: Optional[str] = None, period: Optional[str] = None, inbox: Optional[str] = None,
                        q: Optional[str] = None, limit: Optional[int] = None, offset: int = 0) -> tuple[list[WorkItem], int]:
        actor = self.config.actor(actor_id) if actor_id else None
        if inbox == "me" and actor_id:
            items = self.inbox(actor_id)
        else:
            states = [s.strip() for s in state.split(",")] if state else None
            kinds = [k.strip() for k in kind.split(",")] if kind else None
            agent_id = agent if not agent or agent.startswith("agent:") else f"agent:{agent}"
            items = self.store.query_items(kind=kinds, state=states, agent_id=agent_id, vendor_id=vendor, period=period)
        if actor is not None and actor.role == Role.agent:
            items = [i for i in items if (i.data.get("agent_id") or i.requested_by) == actor_id]
        if flag:
            items = [i for i in items if flag in [f.value for f in i.flags]]
        if q:
            needle = q.lower()
            items = [i for i in items if needle in " ".join(filter(None, [
                i.id, i.title, i.vendor_id, i.sku, i.purpose, i.task_ref, i.data.get("invoice_number"),
                self.config.actor_name(i.data.get("agent_id") or i.requested_by)])).lower()]
        total = len(items)
        if limit is not None:
            items = items[offset:offset + limit]
        return items, total

    @locked
    def inbox(self, actor_id: str) -> list[WorkItem]:
        actor = self.config.actor(actor_id)
        if actor is None or actor.kind.value != "human":
            return []
        found: list[WorkItem] = []
        if actor.role in (Role.budget_owner, Role.controller):
            for item in self.store.query_items(kind="purchase", state="NEEDS_APPROVAL", order="created_at ASC"):
                spend_pending = item.data.get("spend_role") and not item.data.get("spend_approved")
                payee_pending = item.data.get("payee_hold") and not item.data.get("payee_verified")
                agent = self.config.agent(item.requested_by)
                if actor.role == Role.budget_owner:
                    if spend_pending and item.data["spend_role"] == "budget_owner" and agent and agent.owner_id == actor_id:
                        found.append(item)
                elif payee_pending or (spend_pending and item.data["spend_role"] == "controller"):
                    found.append(item)
        if actor.role == Role.controller:
            found += self.store.query_items(kind=["accrual", "policy"], state="PROPOSED", order="created_at ASC")
            found += [i for i in self.store.query_items(kind="invoice", state="NEEDS_APPROVAL", order="created_at ASC")
                      if i.required_approver_role == Role.controller]
        if actor.role == Role.accountant:
            found += [i for i in self.store.query_items(kind="invoice", state="NEEDS_APPROVAL", order="created_at ASC")
                      if i.required_approver_role in (None, Role.accountant)]
            found += [i for i in self.store.query_items(kind="invoice", state="NEEDS_INFO", order="created_at ASC")
                      if intake.failed_intake(i)]
        if actor.role in (Role.accountant, Role.controller):
            found += [i for i in self.store.query_items(kind="correction", state="PROPOSED", order="created_at ASC")
                      if i.proposed_by != actor_id]
        return sorted(found, key=lambda i: (i.created_at, i.id))

    @locked
    def work_item(self, item_id: str, actor_id: Optional[str] = None) -> WorkItemDetail:
        item = self._get(item_id)
        if actor_id is not None:
            self._check_read(actor_id, item)
        events = audit.events(self.store, subjects=[item.id], ascending=True)
        entries = self.store.entries(ids=item.journal_entry_ids, newest_first=False)
        linked_ids = [link.id for link in item.links if link.id.startswith("WI-")]
        linked_ids += [i for i in self.store.items_linking_to(item.id) if i not in linked_ids]
        linked = [i for i in (self.store.get_item(x) for x in linked_ids) if i is not None]
        recon = item.data.get("reconciliation")
        return WorkItemDetail(
            item=item, events=events, journal_entries=entries, linked=linked,
            explanation=explain.item_explanation(self.ctx, item),
            reconciliation=ReconciliationProposal.model_validate(recon) if recon else None,
            next_actions=self.next_actions(actor_id, item) if actor_id else [],
        )

    def next_actions(self, actor_id: str, item: WorkItem) -> list[NextAction]:
        actor = self.config.actor(actor_id)
        if actor is None or actor.kind.value != "human":
            return []
        out: list[NextAction] = []

        def add(action: str, label: str) -> None:
            verdict = can(self.config, actor_id, action, item)
            out.append(NextAction(action=action, label=label, allowed=verdict.allowed, reason=verdict.reason))

        if item.kind == WorkItemKind.purchase and item.state == State.NEEDS_APPROVAL:
            spend_pending = bool(item.data.get("spend_role")) and not item.data.get("spend_approved")
            payee_pending = bool(item.data.get("payee_hold")) and not item.data.get("payee_verified")
            if spend_pending:
                add("approve", "Approve spend" if payee_pending else "Approve payment")
            if payee_pending:
                add("verify_payee", "Verify payee")
            add("reject", "Reject request")
            add("ask", "Ask a question")
        elif item.kind == WorkItemKind.purchase and item.state == State.NEEDS_INFO:
            add("reject", "Reject request")
        elif item.kind == WorkItemKind.invoice and item.state == State.NEEDS_APPROVAL:
            dispute = item.data.get("dispute_cents", 0)
            add("approve", f"Approve short pay of {money(item.data.get('pay_cents', 0))}" if dispute
                else f"Approve payment of {money(item.amount_cents)}")
            add("reject", "Reject invoice")
        elif item.kind == WorkItemKind.invoice and item.state == State.NEEDS_INFO and intake.failed_intake(item):
            add("reject", "Reject invoice")
        elif item.state == State.PROPOSED and item.kind in (WorkItemKind.accrual, WorkItemKind.correction,
                                                             WorkItemKind.policy):
            noun = item.kind.value
            add("approve", f"Approve {noun}")
            add("reject", f"Reject {noun}")
        return out

    @locked
    def ledger(self, period: str, account: Optional[str] = None, limit: Optional[int] = None,
               offset: int = 0) -> list[JournalEntry]:
        return self.store.entries(period=period, account=account, limit=limit, offset=offset)

    @locked
    def ledger_count(self, period: str, account: Optional[str] = None) -> int:
        return self.store.count_entries(period=period, account=account)

    @locked
    def balances(self, period: str) -> list[AccountBalance]:
        from elix.engine.ledger import balances

        return balances(self.ctx, period)

    @locked
    def trial_balance_ok(self, period: str) -> bool:
        from elix.engine.ledger import trial_balance_ok

        return trial_balance_ok(self.ctx, period, cumulative=True) and trial_balance_ok(self.ctx, period, cumulative=False)

    @locked
    def audit(self, subject: Optional[str] = None, actor: Optional[str] = None, limit: int = 200,
              action: Optional[str] = None, allowed: Optional[bool] = None,
              before_seq: Optional[int] = None) -> list[AuditEvent]:
        return audit.events(self.store, subjects=[subject] if subject else None, actor=actor, action=action,
                            allowed=allowed, before_seq=before_seq, limit=limit)

    @locked
    def verify_chain(self) -> ChainCheck:
        return audit.verify_chain(self.store)

    @locked
    def why(self, subject: str) -> WhyReport:
        subject = subject.strip()
        item = self.store.get_item(subject)
        if item is None:
            matches = self.store.query_items(kind="invoice", invoice_number=subject, order="created_at ASC")
            item = matches[0] if matches else None
        if item is None:
            item = credits.find_by_credit_number(self.ctx, subject)
        if item is None and subject.upper().startswith("WI-"):
            item = self.store.get_item(subject.upper())
        primary = item.id if item else subject
        related = {primary}
        if item is not None:
            related.update(item.journal_entry_ids)
            related.update(link.id for link in item.links)
            if item.data.get("policy_id"):
                related.add(item.data["policy_id"])
            for linked_id in [link.id for link in item.links if link.id.startswith("WI-")]:
                linked = self.store.get_item(linked_id)
                if linked:
                    related.update(linked.journal_entry_ids)
        events = audit.events(self.store, subjects=sorted(related), ascending=True)
        if not events and item is None and not self.store.get_entry(subject) and not self.store.get_policy(subject) \
                and not self.store.get_period(subject):
            raise NotFound(f"Elix has no record called {subject}.")
        steps = [WhyStep(seq=e.seq, at=e.at, actor=e.actor, actor_name=self.config.actor_name(e.actor),
                         action=e.action, sentence=explain.event_sentence(self.config, e),
                         rule_ids=explain.event_rule_ids(e), evidence=e.evidence) for e in events]
        title, answer = explain.why_answer(self.ctx, primary, item, events)
        return WhyReport(subject=primary, title=title, answer=answer, steps=steps,
                         related=sorted(r for r in related if r != primary))

    # ------------------------------------------------------------------ dashboards

    @locked
    def agents_status(self) -> list[dict[str, Any]]:
        period = self.clock.period()
        out = []
        for agent in self.config.agents:
            spent = self.store.sum_items("paid_cents", kind="purchase", agent_id=agent.actor_id, paid_period=period)
            committed = self.store.sum_items("exposure_cents", kind="purchase", agent_id=agent.actor_id, state=COMMITTED)
            latest = self.store.query_items(kind="purchase", agent_id=agent.actor_id, limit=1,
                                            order="updated_at DESC, id DESC")
            out.append({**agent.model_dump(mode="json"), "spent_cents": spent, "committed_cents": committed,
                        "last_activity_at": latest[0].updated_at if latest else None})
        return out

    @locked
    def summary(self, period: Optional[str] = None) -> dict[str, Any]:
        period = period or self.clock.period()
        store = self.store
        purchases = store.query_items(kind="purchase", period=period)
        agents = []
        for agent in self.config.agents:
            agents.append({
                "agent_id": agent.actor_id,
                "spent_cents": store.sum_items("paid_cents", kind="purchase", agent_id=agent.actor_id, paid_period=period),
                "committed_cents": store.sum_items("exposure_cents", kind="purchase", agent_id=agent.actor_id,
                                                   state=COMMITTED, period=period),
                "budget_cents": agent.monthly_budget_cents, "cap_cents": agent.per_txn_cap_cents,
            })
        paid = store.query_items(kind=["purchase", "invoice"], paid_period=period, order="paid_at ASC")
        by_rail = {"x402": [0, 0], "bank": [0, 0]}
        daily: dict[str, dict[str, int]] = {}
        for item in paid:
            if item.receipt is None:
                continue
            rail = item.receipt.rail
            by_rail[rail][0] += item.receipt.amount_cents
            by_rail[rail][1] += 1
            date = (item.data.get("paid_at") or item.receipt.settled_at)[:10]
            daily.setdefault(date, {"x402_cents": 0, "bank_cents": 0})[f"{rail}_cents"] += item.receipt.amount_cents
        by_account = []
        totals = store.account_totals(period=period)
        for account in self.config.accounts:
            if account.type != "expense" or account.code not in totals:
                continue
            debit, credit = totals[account.code]
            if debit - credit:
                by_account.append({"account": account.code, "name": account.name, "cents": debit - credit})
        decisions = {"auto_approved": 0, "human_approved": 0, "rejected": 0, "denied": 0, "pending": 0}
        for item in purchases:
            if item.state == State.REJECTED:
                decisions["rejected"] += 1
            elif item.state == State.DENIED:
                decisions["denied"] += 1
            elif item.state in (State.NEW, State.PROPOSED, State.NEEDS_INFO, State.NEEDS_APPROVAL):
                decisions["pending"] += 1
            elif item.approved_by == POLICY:
                decisions["auto_approved"] += 1
            elif item.approvals:
                decisions["human_approved"] += 1
        decided = decisions["auto_approved"] + decisions["human_approved"] + decisions["rejected"] + decisions["denied"]
        flags_recent = []
        for item in sorted((i for i in store.query_items(period=period) if i.flags), key=lambda i: i.updated_at,
                           reverse=True)[:12]:
            for flag in item.flags:
                flags_recent.append({"item_id": item.id, "flag": flag.value, "at": item.updated_at})
        start = parse_ts(period + "-01")
        last = min(parse_ts(self.clock.today()), parse_ts(explain_period_end(period)))
        days = []
        cursor = start
        while cursor <= last:
            key = cursor.strftime("%Y-%m-%d")
            days.append({"date": key, **daily.get(key, {"x402_cents": 0, "bank_cents": 0})})
            cursor += timedelta(days=1)
        inbox_counts = {a.id: len(self.inbox(a.id)) for a in self.config.actors
                        if a.kind.value == "human" and a.role != Role.auditor}
        return {
            "period": period, "today": self.clock.today(),
            "spend_cents": by_rail["x402"][0] + by_rail["bank"][0],
            "spend_by_agent": agents,
            "spend_by_rail": [{"rail": r, "cents": v[0], "count": v[1]} for r, v in by_rail.items()],
            "spend_by_account": by_account,
            "decisions": decisions,
            "automated_share": round((decisions["auto_approved"] + decisions["denied"]) / decided, 4) if decided else 0.0,
            "purchases_total": len(purchases),
            "inbox_counts": inbox_counts,
            "flags_recent": flags_recent[:20],
            "daily_spend": days,
        }

    @locked
    def session(self, actor_id: str) -> dict[str, Any]:
        actor = self.config.actor(actor_id)
        if actor is None:
            raise NotFound(f"There is no actor called {actor_id}.")
        role = actor.role
        human = actor.kind.value == "human"
        return {
            "actor": actor.model_dump(mode="json"), "role": role.value,
            "can": {
                "approve_purchases": role in (Role.budget_owner, Role.controller),
                "approve_invoices": role in (Role.accountant, Role.controller),
                "approve_policies": role == Role.controller,
                "verify_payee": role == Role.controller,
                "recode": role in (Role.accountant, Role.controller),
                "close_period": role == Role.controller,
                "record_credit_notes": role in (Role.accountant, Role.controller),
                "upload_invoices": role in (Role.accountant, Role.controller),
                "run_scenarios": human,
            },
        }

    # ------------------------------------------------------------------ helpers

    def check_read(self, actor_id: str, item: WorkItem) -> None:
        with self._lock:
            self._check_read(actor_id, item)

    def _check_read(self, actor_id: str, item: WorkItem) -> None:
        verdict = can(self.config, actor_id, "read", item)
        if not verdict.allowed:
            raise deny(self.ctx, actor=actor_id, action="read", subject=item.id, reason=verdict.reason,
                       rule_id=verdict.rule_id)

    def _get(self, item_id: str) -> WorkItem:
        item = self.store.get_item(item_id)
        if item is None:
            raise NotFound(f"There is no work item called {item_id}.")
        return item

    @locked
    def get_item(self, item_id: str) -> WorkItem:
        return self._get(item_id)

    def _audit(self, actor: str, action: str, subject: str, reason: str, *, diff: Optional[dict] = None,
               evidence: Optional[dict] = None, refs: list[str] | tuple = ()) -> AuditEvent:
        with self.store.tx():
            event = audit.append_event(self.store, at=self.clock.now(), actor=actor, action=action, subject=subject,
                                       allowed=True, reason=reason, diff=diff, evidence=evidence, refs=refs)
            payload = event.model_dump(mode="json")
            self.store.on_commit(lambda: self.bus.publish("audit", payload))
        return event

    @locked
    def record(self, actor: str, action: str, subject: str, reason: str, **kwargs: Any) -> AuditEvent:
        """Append a plain audit event (used by scenarios and the server for things like a reset)."""
        return self._audit(actor, action, subject, reason, **kwargs)

    def _resource_url(self, vendor_id: str, sku: str, quantity: float, **params: Any) -> str:
        base = self.ctx.vendor_base_url or default_vendor_base_url()
        try:
            from elix.vendors import resource_url  # provided by the rails builder (spec 10)

            return resource_url(vendor_id, sku, quantity, base_url=base, **params)
        except ImportError:
            route = VENDOR_ROUTES.get(vendor_id, vendor_id.removeprefix("ven_"))
            quantity_text = int(quantity) if float(quantity).is_integer() else quantity
            return f"{base}/{route}?{urlencode({'sku': sku, 'qty': quantity_text, **params})}"

    def resource_url(self, vendor_id: str, sku: str, quantity: float, **params: Any) -> str:
        return self._resource_url(vendor_id, sku, quantity, **params)

    def _usage_window(self, resource_url: Optional[str]) -> dict[str, str]:
        """Usage window for metered products, from the resource URL's start and end, else the next 24 hours."""
        if resource_url:
            query = parse_qs(urlparse(resource_url).query)
            start, end = query.get("start", [None])[0], query.get("end", [None])[0]
            try:
                if start and end:
                    return {"start": iso(parse_ts(start)), "end": iso(parse_ts(end))}
            except ValueError:
                pass
        now = self.clock.now_dt().replace(minute=0, second=0)
        return {"start": iso(now), "end": iso(now + timedelta(hours=24))}


def explain_period_end(period: str) -> str:
    from elix.engine.clock import period_end_date

    return period_end_date(period)


def _note(note: str) -> str:
    note = (note or "").strip()
    return f" with the note \"{note.rstrip('.')}.\"" if note else "."


def _conflict(ctx: Ctx, actor: str, action: str, subject: str, reason: str) -> Conflict:
    from elix.engine.workflow import conflict

    return conflict(ctx, actor=actor, action=action, subject=subject, reason=reason)
