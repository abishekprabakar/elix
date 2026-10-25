"""The state machine. `transition()` is the only function that changes a work item.

It checks the transition table, asks `can()`, and then saves the item and its
audit event in one SQLite transaction. A refused attempt is audited too, with
allowed=false, before the refusal is raised.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from elix.contracts import AuditEvent, Denied, State, WorkItem, WorkItemKind
from elix.engine import audit
from elix.engine.context import Ctx
from elix.engine.errors import Conflict
from elix.engine.permissions import can

S = State
P, I, C, A, POL = (WorkItemKind.purchase, WorkItemKind.invoice, WorkItemKind.correction,
                   WorkItemKind.accrual, WorkItemKind.policy)

# (kind, from_state, action) -> allowed target states. The first target is the default.
# from_state None means the action creates the item.
TRANSITIONS: dict[tuple[WorkItemKind, Optional[State], str], tuple[State, ...]] = {
    # purchases
    (P, None, "propose"): (S.NEW,),
    (P, S.NEW, "evaluate"): (S.PROPOSED,),
    (P, S.PROPOSED, "evaluate"): (S.PROPOSED,),     # re-evaluation after an answer
    (P, S.NEW, "fail"): (S.FAILED,),
    (P, S.PROPOSED, "auto_approve"): (S.APPROVED,),
    (P, S.PROPOSED, "escalate"): (S.NEEDS_APPROVAL,),
    (P, S.PROPOSED, "ask"): (S.NEEDS_INFO,),
    (P, S.PROPOSED, "deny"): (S.DENIED,),
    (P, S.NEEDS_INFO, "answer"): (S.PROPOSED,),
    (P, S.NEEDS_INFO, "reject"): (S.REJECTED,),
    (P, S.NEEDS_APPROVAL, "approve"): (S.APPROVED, S.NEEDS_APPROVAL),
    (P, S.NEEDS_APPROVAL, "reject"): (S.REJECTED,),
    (P, S.NEEDS_APPROVAL, "verify_payee"): (S.APPROVED, S.NEEDS_APPROVAL),
    (P, S.NEEDS_APPROVAL, "ask"): (S.NEEDS_INFO,),
    (P, S.APPROVED, "pay"): (S.PAID,),
    (P, S.APPROVED, "fail"): (S.FAILED,),
    (P, S.PAID, "post"): (S.POSTED,),
    # invoices
    (I, None, "create"): (S.NEW,),
    (I, S.NEW, "match"): (S.PROPOSED,),
    (I, S.PROPOSED, "auto_approve"): (S.APPROVED,),
    (I, S.PROPOSED, "escalate"): (S.NEEDS_APPROVAL,),
    (I, S.PROPOSED, "ask"): (S.NEEDS_INFO,),
    (I, S.PROPOSED, "deny"): (S.DENIED,),
    (I, S.NEEDS_INFO, "answer"): (S.NEEDS_APPROVAL,),
    (I, S.NEEDS_INFO, "reject"): (S.REJECTED,),        # an invoice PDF that failed intake checks
    (I, S.NEW, "intake_check"): (S.NEEDS_INFO,),       # intake checks failed, nothing reconciled or posted
    (I, S.NEEDS_APPROVAL, "approve"): (S.APPROVED,),
    (I, S.NEEDS_APPROVAL, "reject"): (S.REJECTED,),
    (I, S.APPROVED, "pay"): (S.PAID,),
    (I, S.APPROVED, "fail"): (S.FAILED,),
    (I, S.PAID, "post"): (S.POSTED,),
    (I, S.POSTED, "record_credit"): (S.POSTED,),      # a vendor credit note against the open dispute
    (I, S.POSTED, "post_credit"): (S.POSTED,),        # the gateway books it, Dr 2010 / Cr 2150
    # accruals
    (A, None, "propose"): (S.PROPOSED,),
    (A, S.PROPOSED, "approve"): (S.APPROVED,),
    (A, S.PROPOSED, "reject"): (S.REJECTED,),
    (A, S.APPROVED, "post"): (S.POSTED,),
    # corrections
    (C, None, "recode"): (S.PROPOSED,),
    (C, S.PROPOSED, "approve"): (S.APPROVED,),
    (C, S.PROPOSED, "reject"): (S.REJECTED,),
    (C, S.APPROVED, "post"): (S.POSTED,),
    # learned policy proposals
    (POL, None, "propose"): (S.PROPOSED,),
    (POL, S.PROPOSED, "approve"): (S.APPROVED,),
    (POL, S.PROPOSED, "reject"): (S.REJECTED,),
}

VERBS = {
    "approve": "approved", "reject": "rejected", "verify_payee": "verified the payee on",
    "pay": "paid", "post": "posted", "answer": "answered", "ask": "asked about",
    "record_credit": "credited", "post_credit": "credited",
}


def next_item_id(ctx: Ctx) -> str:
    return f"WI-{ctx.store.next_counter('work_item', start=1001):04d}"


def audit_denied(ctx: Ctx, *, actor: str, action: str, subject: str, reason: str, rule_id: str,
                 evidence: Optional[dict] = None, exc: Optional[Exception] = None) -> AuditEvent:
    """Write a refused attempt so that it survives the rollback of whatever was in progress."""
    holder: dict[str, AuditEvent] = {}

    def write() -> None:
        event = audit.append_event(
            ctx.store, at=ctx.clock.now(), actor=actor, action=f"denied:{action}", subject=subject,
            allowed=False, reason=reason, evidence={"rule_id": rule_id, **(evidence or {})},
        )
        holder["event"] = event
        if exc is not None and hasattr(exc, "audit_seq"):
            exc.audit_seq = event.seq  # type: ignore[attr-defined]
        ctx.store.on_commit(lambda: ctx.bus.publish("audit", event.model_dump(mode="json")))

    ctx.store.durable(write)
    return holder["event"]


def deny(ctx: Ctx, *, actor: str, action: str, subject: str, reason: str, rule_id: str = "R31",
         evidence: Optional[dict] = None) -> Denied:
    """Audit a refusal and return the exception for the caller to raise."""
    exc = Denied(reason, rule_id=rule_id)
    audit_denied(ctx, actor=actor, action=action, subject=subject, reason=reason, rule_id=rule_id,
                 evidence=evidence, exc=exc)
    return exc


def conflict(ctx: Ctx, *, actor: str, action: str, subject: str, reason: str,
             evidence: Optional[dict] = None, details: Any = None) -> Conflict:
    exc = Conflict(reason, details=details)
    audit_denied(ctx, actor=actor, action=action, subject=subject, reason=reason, rule_id="STATE",
                 evidence=evidence, exc=exc)
    return exc


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[Any]]:
    skip = {"updated_at"}
    return {k: [before.get(k), after.get(k)] for k in after if k not in skip and before.get(k) != after.get(k)}


def create(ctx: Ctx, item: WorkItem, *, actor: str, action: str, reason: str,
           evidence: Optional[dict] = None, refs: Iterable[str] = ()) -> WorkItem:
    """Create a work item through the transition table (from_state None)."""
    targets = TRANSITIONS.get((item.kind, None, action))
    if not targets:
        raise conflict(ctx, actor=actor, action=action, subject=item.id or "new",
                       reason=f"A {item.kind.value} cannot be created by {action}.")
    verdict = can(ctx.config, actor, action, item if item.requested_by else None)
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action=action, subject=item.id or item.kind.value, reason=verdict.reason,
                   rule_id=verdict.rule_id)
    now = ctx.clock.now()
    with ctx.store.tx():
        item = item.model_copy(update={
            "id": item.id or next_item_id(ctx), "state": targets[0], "created_at": item.created_at or now,
            "updated_at": now,
        })
        item = WorkItem.model_validate(item.model_dump())
        ctx.store.save_item(item)
        after = item.model_dump(mode="json")
        event = audit.append_event(
            ctx.store, at=now, actor=actor, action=action, subject=item.id, allowed=True, reason=reason,
            diff={k: [None, v] for k, v in after.items() if v not in (None, [], {}, "", False)},
            evidence=evidence or {}, refs=[*refs, *(link.id for link in item.links)],
        )
        _publish(ctx, item, event)
    return item


def transition(
    ctx: Ctx,
    item: WorkItem,
    action: str,
    actor: str,
    patch: Optional[dict[str, Any]] = None,
    reason: str = "",
    *,
    to_state: Optional[State] = None,
    evidence: Optional[dict] = None,
    refs: Iterable[str] = (),
) -> WorkItem:
    """Move an item along the state machine, audited in the same transaction."""
    # Permission first, so an actor who may never take this action learns nothing about the item's state.
    verdict = can(ctx.config, actor, action, item)
    if not verdict.allowed:
        raise deny(ctx, actor=actor, action=action, subject=item.id, reason=verdict.reason, rule_id=verdict.rule_id)
    targets = TRANSITIONS.get((item.kind, item.state, action))
    if not targets or (to_state is not None and to_state not in targets):
        raise conflict(
            ctx, actor=actor, action=action, subject=item.id,
            reason=f"{item.id} is {item.state.value.replace('_', ' ').lower()}, so it cannot be {VERBS.get(action, action)} now.",
        )

    target = to_state or targets[0]
    now = ctx.clock.now()
    before = item.model_dump(mode="json")
    updated = WorkItem.model_validate({**item.model_dump(), **(patch or {}), "state": target, "updated_at": now})
    after = updated.model_dump(mode="json")
    with ctx.store.tx():
        ctx.store.save_item(updated)
        event = audit.append_event(
            ctx.store, at=now, actor=actor, action=action, subject=item.id, allowed=True,
            reason=reason or f"{ctx.config.actor_name(actor)} {VERBS.get(action, action)} {item.id}.",
            diff=_diff(before, after), evidence=evidence or {},
            refs=[*refs, *(link.id for link in updated.links)],
        )
        _publish(ctx, updated, event)
    return updated


def _publish(ctx: Ctx, item: WorkItem, event: AuditEvent) -> None:
    item_json = item.model_dump(mode="json")
    event_json = event.model_dump(mode="json")
    ctx.store.on_commit(lambda: (ctx.bus.publish("audit", event_json), ctx.bus.publish("work_item", item_json)))


def merged_data(item: WorkItem, **updates: Any) -> dict[str, Any]:
    """item.data with keys replaced, for use in a transition patch."""
    return {**item.data, **updates}
